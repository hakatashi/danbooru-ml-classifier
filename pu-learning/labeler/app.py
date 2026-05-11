#!/usr/bin/env python3
"""
Manual labeling tool for evaluation dataset construction.

Finds images in /mnt/cache2/danbooru-ml-classifier/images/ that are NOT in
the training/val/test splits (splits.parquet), and serves a web UI to label
each image as: pixiv_public | pixiv_private | not_bookmarked

Labels are saved to pu-learning/data/labels/manual_labels.json incrementally.

Usage:
    python app.py [--port 8765]
"""

import argparse
import datetime
import http.server
import io
import json
import mimetypes
import os
import subprocess
import sys
import threading
import urllib.parse
import urllib.request
from pathlib import Path

# ── Paths ─────────────────────────────────────────────────────────────────────
SCRIPT_DIR  = Path(__file__).parent
PROJECT_DIR = SCRIPT_DIR.parent          # pu-learning/
REPO_DIR    = PROJECT_DIR.parent

SPLITS_PARQUET  = PROJECT_DIR / "data" / "metadata" / "splits.parquet"
LABELS_DIR      = PROJECT_DIR / "data" / "labels"
LABELS_FILE     = LABELS_DIR / "manual_labels.json"
DMC_IMAGES_DIR  = Path("/mnt/cache2/danbooru-ml-classifier/images")
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".gif"}

FRONTEND_DIR   = SCRIPT_DIR / "frontend"


def _load_dotenv(dotenv_path: Path) -> None:
    """Load KEY=value lines from a .env file into os.environ (skip if already set)."""
    if not dotenv_path.is_file():
        return
    with open(dotenv_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            os.environ.setdefault(key, value)


# Load .env from the publisher directory (sibling of pu-learning/)
_load_dotenv(REPO_DIR / "publisher" / ".env")

# ── External API credentials (optional) ───────────────────────────────────────
DANBOORU_API_USER = os.environ.get("DANBOORU_API_USER", "")
DANBOORU_API_KEY  = os.environ.get("DANBOORU_API_KEY", "")
GELBOORU_API_USER = os.environ.get("GELBOORU_API_USER", "")
GELBOORU_API_KEY  = os.environ.get("GELBOORU_API_KEY", "")

VALID_LABELS   = ["pixiv_public", "pixiv_private", "not_bookmarked"]
RATABLE_LABELS = {"pixiv_public", "pixiv_private"}  # labels that support a 1-3 rating


def get_label(path: str) -> str | None:
    val = _labels.get(path)
    if val is None:
        return None
    if isinstance(val, dict):
        return val.get("label")
    return val  # legacy string (should not appear after migration)


def get_rating(path: str) -> int | None:
    val = _labels.get(path)
    if isinstance(val, dict):
        return val.get("rating")
    return None

# ── Global state ──────────────────────────────────────────────────────────────
_images_to_label: list[str] = []      # sorted list of absolute paths
_index_map: dict[str, int] = {}       # path → index in _images_to_label (O(1) lookup)
_labels: dict[str, str] = {}          # path → label
_labels_lock = threading.Lock()
_thumb_cache: dict[str, bytes] = {}   # path → resized JPEG bytes
_thumb_lock = threading.Lock()
THUMB_SIZE = 80                        # thumbnail size in pixels


def build_image_list() -> list[str]:
    """
    Collect DMC images NOT already in splits.parquet.
    Files with mtime >= CUTOFF_TIME are excluded, except sankaku files with
    mtime in [SANKAKU_WINDOW_START, SANKAKU_WINDOW_END] (JST) which are included.
    Returns sorted list of absolute path strings.
    """
    # 2026-04-05 09:00 JST = 2026-04-05 00:00 UTC
    CUTOFF = datetime.datetime(2026, 4, 5, 0, 0, 0, tzinfo=datetime.timezone.utc)
    cutoff_ts = CUTOFF.timestamp()

    # Sankaku extra window: 2026-04-09 10:00–12:00 JST (inclusive)
    JST = datetime.timezone(datetime.timedelta(hours=9))
    SANKAKU_START = datetime.datetime(2026, 4, 9, 10, 0, 0, tzinfo=JST)
    SANKAKU_END   = datetime.datetime(2026, 4, 9, 19, 0, 0, tzinfo=JST)
    sankaku_start_ts = SANKAKU_START.timestamp()
    sankaku_end_ts   = SANKAKU_END.timestamp()
    SANKAKU_DIR = DMC_IMAGES_DIR / "sankaku"

    try:
        import pandas as pd
        df = pd.read_parquet(SPLITS_PARQUET)
        dataset_files = set(df["file_path"].tolist())
    except Exception as e:
        print(f"[warn] Could not load splits.parquet: {e}. Using empty exclusion set.")
        dataset_files = set()

    all_images: list[str] = []
    n_skipped_ts = 0
    for f in DMC_IMAGES_DIR.rglob("*"):
        if not (f.is_file() and f.suffix.lower() in IMAGE_EXTENSIONS):
            continue
        mtime = f.stat().st_mtime
        if mtime >= cutoff_ts:
            # Allow sankaku files within the extra time window
            try:
                f.relative_to(SANKAKU_DIR)
                in_sankaku = True
            except ValueError:
                in_sankaku = False
            if in_sankaku and sankaku_start_ts <= mtime <= sankaku_end_ts:
                pass  # include this file
            else:
                n_skipped_ts += 1
                continue
        all_images.append(str(f))

    to_label = sorted(p for p in all_images if p not in dataset_files)
    print(f"[init] DMC total: {len(all_images)}, in dataset: {len(dataset_files & set(all_images))}, to label: {len(to_label)} (cutoff excluded: {n_skipped_ts})")
    return to_label


def make_thumbnail(image_path: Path) -> bytes:
    """Return JPEG bytes of an 80×80 thumbnail, using in-memory cache."""
    key = str(image_path)
    with _thumb_lock:
        if key in _thumb_cache:
            return _thumb_cache[key]

    from PIL import Image
    try:
        with Image.open(image_path) as img:
            img.thumbnail((THUMB_SIZE, THUMB_SIZE), Image.LANCZOS)
            # Convert to RGB so JPEG save works for all modes (e.g. RGBA, P)
            if img.mode not in ("RGB", "L"):
                img = img.convert("RGB")
            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=70, optimize=True)
            data = buf.getvalue()
    except Exception:
        # Return 1-pixel grey JPEG on error
        buf = io.BytesIO()
        Image.new("RGB", (1, 1), (80, 80, 80)).save(buf, format="JPEG")
        data = buf.getvalue()

    with _thumb_lock:
        _thumb_cache[key] = data
    return data


def _fetch_post_source(provider: str, stem: str) -> str | None:
    """Fetch the 'source' field from the Danbooru or Gelbooru API.

    Returns the source string (may be empty or a URL), or None if not supported.
    Raises urllib.error.URLError / ValueError on network/parse errors.
    """
    if provider == "danbooru":
        post_id = stem  # stem is the numeric ID directly
        url = f"https://danbooru.donmai.us/posts/{post_id}.json"
        req = urllib.request.Request(url, headers={"User-Agent": "danbooru-ml-classifier-labeler/1.0"})
        if DANBOORU_API_USER and DANBOORU_API_KEY:
            import base64
            creds = base64.b64encode(f"{DANBOORU_API_USER}:{DANBOORU_API_KEY}".encode()).decode()
            req.add_header("Authorization", f"Basic {creds}")
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
        return _pixiv_image_url_to_artwork(data.get("source") or None)

    if provider == "gelbooru":
        post_id = stem  # stem is the numeric ID directly
        q = urllib.parse.urlencode({
            "page": "dapi", "s": "post", "q": "index", "json": "1", "id": post_id,
            **({"api_key": GELBOORU_API_KEY, "user_id": GELBOORU_API_USER}
               if GELBOORU_API_USER and GELBOORU_API_KEY else {}),
        })
        url = f"https://gelbooru.com/index.php?{q}"
        req = urllib.request.Request(url, headers={"User-Agent": "danbooru-ml-classifier-labeler/1.0"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
        # Response: {"post": [...]} or {"@attributes": ..., "post": [...]}
        posts = data.get("post", [])
        if not posts:
            return None
        return _pixiv_image_url_to_artwork(posts[0].get("source") or None)

    return None  # provider not supported (e.g. pixiv)


def _pixiv_image_url_to_artwork(source: str | None) -> str | None:
    """Convert a Pixiv image URL to its artwork page URL if applicable."""
    if not source:
        return source
    import re
    m = re.search(r"pximg\.net/.*/(\d+)_p\d+", source)
    if m:
        return f"https://www.pixiv.net/artworks/{m.group(1)}"
    return source


def load_labels() -> dict:
    LABELS_DIR.mkdir(parents=True, exist_ok=True)
    if not LABELS_FILE.exists():
        return {}

    with open(LABELS_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)

    # ── Migrate old string-value format to dict format ────────────────────
    n_migrated = 0
    for path, val in list(data.items()):
        if isinstance(val, str):
            entry: dict = {"label": val}
            if val in RATABLE_LABELS:
                entry["rating"] = 1
            data[path] = entry
            n_migrated += 1

    if n_migrated:
        print(f"[init] Migrated {n_migrated} entries to new format (rating field added)")
        with open(LABELS_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    print(f"[init] Loaded {len(data)} existing labels from {LABELS_FILE}")
    return data


def save_labels() -> None:
    LABELS_DIR.mkdir(parents=True, exist_ok=True)
    with open(LABELS_FILE, "w", encoding="utf-8") as f:
        json.dump(_labels, f, ensure_ascii=False, indent=2)


# ── Request handler ───────────────────────────────────────────────────────────

class LabelHandler(http.server.BaseHTTPRequestHandler):

    def log_message(self, format, *args):  # quieter logging
        pass

    # ── Routing ───────────────────────────────────────────────────────────────

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        params = urllib.parse.parse_qs(parsed.query)

        if path == "/api/status":
            self._handle_status()
        elif path == "/api/images":
            self._handle_images(params)
        elif path == "/api/image":
            self._handle_image(params)
        elif path == "/api/thumbnail":
            self._handle_thumbnail(params)
        elif path == "/api/source":
            self._handle_source(params)
        else:
            # Serve static assets (Vite build output); fall back to index.html for SPA routing
            clean = path.lstrip("/") or "index.html"
            static_path = SCRIPT_DIR / "static" / clean
            if static_path.is_file():
                self._serve_static(clean)
            else:
                self._serve_static("index.html")

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path

        if path == "/api/label":
            self._handle_label_post()
        elif path == "/api/unlabel":
            self._handle_unlabel_post()
        elif path == "/api/rating":
            self._handle_rating_post()
        else:
            self._send_404()

    def do_OPTIONS(self):
        self.send_response(204)
        self._cors_headers()
        self.end_headers()

    # ── API handlers ──────────────────────────────────────────────────────────

    def _handle_status(self):
        total   = len(_images_to_label)
        labeled = sum(1 for p in _images_to_label if p in _labels and get_label(p) != "__skip__")
        skipped = sum(1 for p in _images_to_label if get_label(p) == "__skip__")
        data = {
            "total": total,
            "labeled": labeled,
            "skipped": skipped,
            "remaining": total - labeled - skipped,
        }
        self._send_json(data)

    def _handle_images(self, params: dict):
        """
        Returns a window of images with their label state.
        Query params:
          offset  (int, default 0)
          limit   (int, default 50)
          filter  "all" | "unlabeled" | "labeled" | "skipped"
          sort    "asc" (default) | "desc"
          label   "all" | "pixiv_public" | "pixiv_private" | "not_bookmarked"
                  (only applied when filter="labeled")
        """
        offset = int(params.get("offset", ["0"])[0])
        limit  = int(params.get("limit",  ["50"])[0])
        filt   = params.get("filter", ["all"])[0]
        sort   = params.get("sort",   ["asc"])[0]
        label_filter = params.get("label", ["all"])[0]

        if filt == "unlabeled":
            items = [p for p in _images_to_label if p not in _labels]
        elif filt == "labeled":
            items = [p for p in _images_to_label if p in _labels and get_label(p) != "__skip__"]
            if label_filter != "all" and label_filter in VALID_LABELS:
                items = [p for p in items if get_label(p) == label_filter]
        elif filt == "skipped":
            items = [p for p in _images_to_label if get_label(p) == "__skip__"]
        else:
            items = _images_to_label

        if sort == "desc":
            items = list(reversed(items))

        page = items[offset: offset + limit]
        result = []
        for p in page:
            result.append({
                "path":   p,
                "label":  get_label(p),
                "rating": get_rating(p),
                "index":  _index_map.get(p, -1),
            })

        self._send_json({
            "total": len(items),
            "offset": offset,
            "limit": limit,
            "items": result,
        })

    def _handle_image(self, params: dict):
        """Serve image binary. Query param: path=<absolute path>"""
        path_param = params.get("path", [None])[0]
        if not path_param:
            self._send_error(400, "Missing 'path' parameter")
            return

        # Security: only serve files inside DMC_IMAGES_DIR
        try:
            image_path = Path(path_param).resolve()
            image_path.relative_to(DMC_IMAGES_DIR.resolve())
        except (ValueError, Exception):
            self._send_error(403, "Forbidden path")
            return

        if not image_path.is_file():
            self._send_error(404, "Image not found")
            return

        mime = mimetypes.guess_type(str(image_path))[0] or "image/jpeg"
        try:
            data = image_path.read_bytes()
        except OSError as e:
            self._send_error(500, str(e))
            return

        self.send_response(200)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "public, max-age=3600")
        self._cors_headers()
        self.end_headers()
        self.wfile.write(data)

    def _handle_thumbnail(self, params: dict):
        """Serve an 80×80 JPEG thumbnail. Query param: path=<absolute path>"""
        path_param = params.get("path", [None])[0]
        if not path_param:
            self._send_error(400, "Missing 'path' parameter")
            return

        try:
            image_path = Path(path_param).resolve()
            image_path.relative_to(DMC_IMAGES_DIR.resolve())
        except (ValueError, Exception):
            self._send_error(403, "Forbidden path")
            return

        if not image_path.is_file():
            self._send_error(404, "Image not found")
            return

        data = make_thumbnail(image_path)
        self.send_response(200)
        self.send_header("Content-Type", "image/jpeg")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "public, max-age=86400")
        self._cors_headers()
        self.end_headers()
        self.wfile.write(data)

    def _handle_source(self, params: dict):
        """GET /api/source?path=<absolute path>
        Fetches the 'source' field from the Danbooru or Gelbooru API for the given image.
        Returns: {"source": "<url or empty string>"}
        """
        path_param = params.get("path", [None])[0]
        if not path_param:
            self._send_error(400, "Missing 'path' parameter")
            return

        # Determine provider and post ID from the relative path
        try:
            image_path = Path(path_param).resolve()
            rel = image_path.relative_to(DMC_IMAGES_DIR.resolve())
        except (ValueError, Exception):
            self._send_error(403, "Forbidden path")
            return

        parts = rel.parts  # e.g. ("danbooru", "12345.jpg")
        if len(parts) < 2:
            self._send_json({"source": None})
            return

        provider = parts[0]
        stem = Path(parts[1]).stem  # filename without extension

        try:
            source = _fetch_post_source(provider, stem)
        except Exception as e:
            self._send_error(502, f"Failed to fetch source: {e}")
            return

        self._send_json({"source": source})

    def _handle_label_post(self):
        """POST /api/label  body: {"path": "...", "label": "...", "rating": 1}"""
        body = self._read_json_body()
        if body is None:
            return

        path   = body.get("path")
        label  = body.get("label")
        rating = body.get("rating", 1)

        if path not in _index_map:
            self._send_error(400, "Unknown image path")
            return
        if label not in VALID_LABELS and label != "__skip__":
            self._send_error(400, f"Invalid label: {label!r}")
            return

        entry: dict = {"label": label}
        if label in RATABLE_LABELS:
            entry["rating"] = rating if isinstance(rating, int) and rating in (1, 2, 3) else 1

        with _labels_lock:
            _labels[path] = entry
            save_labels()

        total   = len(_images_to_label)
        labeled = sum(1 for p in _images_to_label if p in _labels and get_label(p) != "__skip__")
        self._send_json({"ok": True, "labeled": labeled, "total": total})

    def _handle_rating_post(self):
        """POST /api/rating  body: {"path": "...", "rating": 1|2|3}"""
        body = self._read_json_body()
        if body is None:
            return

        path   = body.get("path")
        rating = body.get("rating")

        if path not in _index_map:
            self._send_error(400, "Unknown image path")
            return
        if not isinstance(rating, int) or rating not in (1, 2, 3):
            self._send_error(400, "rating must be 1, 2, or 3")
            return

        entry = _labels.get(path)
        if not isinstance(entry, dict) or entry.get("label") not in RATABLE_LABELS:
            self._send_error(400, "Image must be labeled pixiv_public or pixiv_private first")
            return

        with _labels_lock:
            entry["rating"] = rating
            save_labels()

        self._send_json({"ok": True, "rating": rating})

    def _handle_unlabel_post(self):
        """POST /api/unlabel  body: {"path": "..."}  — removes label"""
        body = self._read_json_body()
        if body is None:
            return

        path = body.get("path")
        if path not in _images_to_label:
            self._send_error(400, "Unknown image path")
            return

        with _labels_lock:
            _labels.pop(path, None)
            save_labels()

        self._send_json({"ok": True})

    # ── Static file serving ───────────────────────────────────────────────────

    def _serve_static(self, filename: str):
        static_path = SCRIPT_DIR / "static" / filename
        if not static_path.is_file():
            self._send_error(404, f"Static file not found: {filename}")
            return
        data = static_path.read_bytes()
        mime = mimetypes.guess_type(filename)[0] or "text/html"
        content_type = mime if mime.startswith("image/") else mime + "; charset=utf-8"
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self._cors_headers()
        self.end_headers()
        self.wfile.write(data)

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _read_json_body(self) -> dict | None:
        length = int(self.headers.get("Content-Length", 0))
        if length == 0:
            self._send_error(400, "Empty body")
            return None
        raw = self.rfile.read(length)
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            self._send_error(400, "Invalid JSON")
            return None

    def _send_json(self, data):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self._cors_headers()
        self.end_headers()
        self.wfile.write(body)

    def _send_error(self, code: int, message: str):
        body = json.dumps({"error": message}).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self._cors_headers()
        self.end_headers()
        self.wfile.write(body)

    def _send_404(self):
        self._send_error(404, "Not found")

    def _cors_headers(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")


# ── Entry point ───────────────────────────────────────────────────────────────

def build_frontend() -> None:
    """Run `npm install` (if needed) then `npm run build` in the frontend directory."""
    if not FRONTEND_DIR.is_dir():
        print("[warn] frontend/ directory not found, skipping build.")
        return

    node_modules = FRONTEND_DIR / "node_modules"
    if not node_modules.is_dir():
        print("[build] Running npm install...")
        subprocess.run(["npm", "install"], cwd=FRONTEND_DIR, check=True)

    print("[build] Building frontend...")
    subprocess.run(["npm", "run", "build"], cwd=FRONTEND_DIR, check=True)
    print("[build] Frontend build complete.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--no-build", action="store_true", help="Skip frontend build on startup")
    args = parser.parse_args()

    if not args.no_build:
        build_frontend()

    global _images_to_label, _index_map, _labels
    _images_to_label = build_image_list()
    _index_map = {p: i for i, p in enumerate(_images_to_label)}
    _labels = load_labels()

    # Use ThreadingHTTPServer so image requests don't block the API
    server = http.server.ThreadingHTTPServer(("", args.port), LabelHandler)
    url = f"http://localhost:{args.port}"
    print(f"\n  Labeling tool running at {url}")
    print(f"  Images to label : {len(_images_to_label)}")
    print(f"  Already labeled : {sum(1 for p in _images_to_label if p in _labels and get_label(p) != '__skip__')}")
    print(f"  Labels file     : {LABELS_FILE}")
    print(f"  Press Ctrl+C to stop.\n")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[info] Shutting down. Labels saved.")


if __name__ == "__main__":
    main()
