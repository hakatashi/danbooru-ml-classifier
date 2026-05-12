#!/usr/bin/env python3
"""
Build the evaluation dataset from manually labeled images.

Steps
-----
1. Load pu-learning/data/labels/manual_labels.json
2. Compute SHA-256 hashes for all eval images
3. Hash-based deduplication:
   a. Within eval: keep the first occurrence when duplicate content found
   b. Against training set (splits.parquet): remove eval images whose hash
      matches any training image
4. Build manifest DataFrame:
     image_id, label, file_path, source, artwork_id, artwork_group_size
   where artwork_group_size is set for pixiv images (# images of same artwork
   in the *final* eval set, for use as sampling weight during evaluation).
5. Extract features (deepdanbooru / eva02 / pixai) into separate HDF5 files
   under data/features/:
     eval_deepdanbooru.h5
     eval_eva02.h5
     eval_pixai.h5
6. Save manifest to data/metadata/eval_manifest.parquet

Usage
-----
  python scripts/build_eval_dataset.py [--features {deepdanbooru,eva02_pixai,all}]
                                       [--batch-size 64] [--device auto]
                                       [--skip-extraction]
"""

import argparse
import hashlib
import json
import logging
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Optional

import h5py
import numpy as np
import pandas as pd
from PIL import Image, UnidentifiedImageError
from tqdm import tqdm

SCRIPTS_DIR = Path(__file__).parent
sys.path.insert(0, str(SCRIPTS_DIR))
sys.path.insert(0, str(SCRIPTS_DIR.parent.parent / "worker"))

from config import (
    DATA_DIR, FEATURES_DIR, METADATA_DIR,
    PIXAI_MODEL_DIR, PIXAI_MODEL_REPO, PIXAI_MODEL_FILES,
    DEEPDANBOORU_DIM, EVA02_DIM, PIXAI_DIM,
    DEEPDANBOORU_IMAGE_SIZE, PIXAI_IMAGE_SIZE,
    DEFAULT_BATCH_SIZE, IMAGE_EXTENSIONS,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

# ── Constants ─────────────────────────────────────────────────────────────────
LABELS_FILE    = DATA_DIR / "labels" / "manual_labels.json"
SPLITS_FILE    = METADATA_DIR / "splits.parquet"
EVAL_MANIFEST  = METADATA_DIR / "eval_manifest.parquet"
VALID_LABELS   = {"pixiv_public", "pixiv_private", "not_bookmarked"}

DMC_IMAGES_DIR = Path("/mnt/cache2/danbooru-ml-classifier/images")

HASH_CACHE_FILE = DATA_DIR / "metadata" / "file_hashes.json"  # shared with train set


# ── File hashing ──────────────────────────────────────────────────────────────

def sha256_file(path: str) -> str:
    """Return lowercase hex SHA-256 of a file's contents."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def compute_hashes(
    paths: list[str],
    desc: str = "Hashing",
    cache: dict[str, str] | None = None,
) -> dict[str, str]:
    """
    Return {path: sha256} for every path.
    If *cache* is provided, skip paths already present in it and update it in-place.
    """
    result: dict[str, str] = {}
    to_hash = []
    for p in paths:
        if cache is not None and p in cache:
            result[p] = cache[p]
        else:
            to_hash.append(p)

    if to_hash:
        for p in tqdm(to_hash, desc=desc, unit="file"):
            try:
                h = sha256_file(p)
            except OSError as e:
                log.warning("  hash error %s: %s", p, e)
                h = ""
            result[p] = h
            if cache is not None:
                cache[p] = h

    return result


def load_hash_cache() -> dict[str, str]:
    if HASH_CACHE_FILE.exists():
        with open(HASH_CACHE_FILE, encoding="utf-8") as f:
            data = json.load(f)
        log.info("Loaded %d cached hashes from %s", len(data), HASH_CACHE_FILE)
        return data
    return {}


def save_hash_cache(cache: dict[str, str]) -> None:
    HASH_CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(HASH_CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(cache, f)
    log.info("Saved %d hashes to cache", len(cache))


# ── image_id derivation (mirrors extract_features.py convention) ──────────────

def path_to_image_id(file_path: str) -> str:
    """
    /mnt/cache2/danbooru-ml-classifier/images/<source>/<stem><ext>
    → dmc_<source>/<stem>
    """
    p = Path(file_path)
    # source subdirectory name (danbooru / gelbooru / pixiv / sankaku)
    try:
        rel = p.relative_to(DMC_IMAGES_DIR)
    except ValueError:
        # fallback: just use the stem
        return p.stem
    parts = rel.parts  # e.g. ('danbooru', '10998522.jpg')
    if len(parts) >= 2:
        return f"dmc_{parts[0]}/{p.stem}"
    return p.stem


def path_to_source(file_path: str) -> str:
    """Return 'danbooru' | 'gelbooru' | 'pixiv' | 'sankaku' | 'unknown'."""
    p = Path(file_path)
    try:
        rel = p.relative_to(DMC_IMAGES_DIR)
        return rel.parts[0] if rel.parts else "unknown"
    except ValueError:
        return "unknown"


def pixiv_artwork_id(stem: str) -> str | None:
    """142579323_p0 → '142579323';  non-pixiv stems → None."""
    m = re.match(r"^(\d+)_p\d+$", stem)
    return m.group(1) if m else None


# ── Main ──────────────────────────────────────────────────────────────────────

def build_manifest() -> pd.DataFrame:
    """
    Load labels, deduplicate by hash, remove training overlaps,
    and return the final eval manifest.
    """
    # ── Load labels ───────────────────────────────────────────────────────────
    log.info("Loading labels from %s", LABELS_FILE)
    with open(LABELS_FILE, encoding="utf-8") as f:
        raw: dict[str, object] = json.load(f)

    # Normalise to {path: {"label": str, "rating": int|None}}
    labeled: dict[str, dict] = {}
    for path, val in raw.items():
        if isinstance(val, dict):
            label  = val.get("label")
            rating = val.get("rating")   # int 1-3 or None
        else:
            label  = val
            rating = None
        if label in VALID_LABELS:
            labeled[path] = {"label": label, "rating": rating}

    log.info("  valid labeled images: %d  (skipped %d with unknown label)",
             len(labeled), len(raw) - len(labeled))
    n_with_rating = sum(1 for v in labeled.values() if v["rating"] is not None)
    log.info("  entries with rating  : %d", n_with_rating)

    eval_paths = sorted(labeled.keys())

    # ── Hash all eval images ──────────────────────────────────────────────────
    hash_cache = load_hash_cache()
    log.info("Computing SHA-256 hashes for eval images …")
    eval_hashes = compute_hashes(eval_paths, desc="Hashing eval", cache=hash_cache)

    # ── Within-eval deduplication ─────────────────────────────────────────────
    log.info("Deduplicating within eval set …")
    seen_hashes: dict[str, str] = {}   # hash → first path that claimed it
    eval_paths_deduped: list[str] = []
    n_dup_eval = 0
    for p in eval_paths:
        h = eval_hashes[p]
        if not h:  # hash error → drop
            log.warning("  dropping (hash error): %s", p)
            continue
        if h in seen_hashes:
            log.warning("  eval duplicate: %s  == %s  (both hash %s)", p, seen_hashes[h], h[:12])
            n_dup_eval += 1
        else:
            seen_hashes[h] = p
            eval_paths_deduped.append(p)

    log.info("  %d duplicates removed within eval set", n_dup_eval)

    # ── Hash training images and remove overlaps ───────────────────────────────
    log.info("Loading training splits from %s", SPLITS_FILE)
    splits_df = pd.read_parquet(SPLITS_FILE)
    train_paths = splits_df["file_path"].tolist()

    log.info("Computing SHA-256 hashes for %d training images …", len(train_paths))
    train_hashes = compute_hashes(train_paths, desc="Hashing train", cache=hash_cache)

    train_hash_set: set[str] = {h for h in train_hashes.values() if h}
    log.info("  unique training hashes: %d", len(train_hash_set))

    n_overlap = 0
    final_paths: list[str] = []
    for p in eval_paths_deduped:
        h = eval_hashes[p]
        if h in train_hash_set:
            log.warning("  removing (matches training image): %s  (hash %s)", p, h[:12])
            n_overlap += 1
        else:
            final_paths.append(p)

    log.info("  %d eval images removed (content matches training set)", n_overlap)
    log.info("  final eval set size: %d images", len(final_paths))

    # Save updated cache
    save_hash_cache(hash_cache)

    # ── Build DataFrame ───────────────────────────────────────────────────────
    records = []
    for p in final_paths:
        source = path_to_source(p)
        stem   = Path(p).stem
        aid    = pixiv_artwork_id(stem) if source == "pixiv" else None
        records.append({
            "image_id":    path_to_image_id(p),
            "label":       labeled[p]["label"],
            "rating":      labeled[p]["rating"],  # int 1-3 or None
            "file_path":   p,
            "source":      source,
            "artwork_id":  aid,   # None for non-pixiv
        })

    df = pd.DataFrame(records)

    # ── Pixiv artwork group size ──────────────────────────────────────────────
    # Count how many images from each artwork_id survive in the final eval set
    pixiv_mask  = df["source"] == "pixiv"
    group_sizes = (
        df.loc[pixiv_mask, "artwork_id"]
        .value_counts()
        .rename("artwork_group_size")
    )
    df["artwork_group_size"] = (
        df["artwork_id"]
        .map(group_sizes)
        .where(pixiv_mask)          # NaN for non-pixiv rows
        .astype("Int64")            # nullable int
    )

    # ── Summary ───────────────────────────────────────────────────────────────
    log.info("=" * 60)
    log.info("Eval manifest summary")
    log.info("=" * 60)
    label_counts = df["label"].value_counts()
    for lbl, cnt in label_counts.items():
        log.info("  %-20s : %d", lbl, cnt)
    log.info("  %-20s : %d", "total", len(df))
    log.info("-" * 60)
    source_counts = df["source"].value_counts()
    for src, cnt in source_counts.items():
        log.info("  source %-14s : %d", src, cnt)
    log.info("-" * 60)
    pixiv_df = df[pixiv_mask]
    if len(pixiv_df) > 0:
        unique_artworks = pixiv_df["artwork_id"].nunique()
        multi = (pixiv_df["artwork_group_size"] > 1).sum()
        log.info("  pixiv unique artwork IDs : %d", unique_artworks)
        log.info("  pixiv multi-page images  : %d", multi)
        log.info("  max pages per artwork    : %d", int(pixiv_df["artwork_group_size"].max()))
    log.info("-" * 60)
    rated = df[df["rating"].notna()]
    log.info("  images with rating       : %d", len(rated))
    if len(rated) > 0:
        rating_counts = rated["rating"].value_counts().sort_index()
        for r, c in rating_counts.items():
            log.info("    rating=%d : %d", int(r), c)
    log.info("=" * 60)

    return df


# ── Feature extraction (reuses extract_features.py infrastructure) ────────────

class H5FeatureStore:
    """Append-friendly HDF5 writer for (image_id, feature_vector) pairs."""

    def __init__(self, path: Path, dim: int):
        self.path = path
        self.dim  = dim
        path.parent.mkdir(parents=True, exist_ok=True)

    def existing_ids(self) -> set[str]:
        if not self.path.exists():
            return set()
        with h5py.File(self.path, "r") as f:
            if "image_ids" not in f:
                return set()
            return set(f["image_ids"].asstr()[:])

    def append(self, image_ids: list[str], features: np.ndarray) -> None:
        if not image_ids:
            return
        features = features.astype(np.float16)
        with h5py.File(self.path, "a") as f:
            if "features" not in f:
                f.create_dataset(
                    "features",
                    shape=(0, self.dim),
                    maxshape=(None, self.dim),
                    dtype="float16",
                    chunks=(min(256, len(image_ids)), self.dim),
                    compression="gzip", compression_opts=4,
                )
                f.create_dataset(
                    "image_ids",
                    shape=(0,),
                    maxshape=(None,),
                    dtype=h5py.special_dtype(vlen=str),
                )
            n = f["features"].shape[0]
            m = len(image_ids)
            f["features"].resize(n + m, axis=0)
            f["image_ids"].resize(n + m, axis=0)
            f["features"][n:] = features
            f["image_ids"][n:] = image_ids


def _to_rgb(image: Image.Image) -> Image.Image:
    if image.mode == "RGBA":
        bg = Image.new("RGB", image.size, (255, 255, 255))
        bg.paste(image, mask=image.split()[3])
        return bg
    if image.mode != "RGB":
        return image.convert("RGB")
    return image


def _load_image(path: str) -> Optional[Image.Image]:
    try:
        img = Image.open(path)
        img.load()
        return _to_rgb(img)
    except Exception as exc:
        log.warning("  skip %s: %s", path, exc)
        return None


def _extract_loop(extract_fn, rows: list[dict], batch_size: int, stores: dict) -> None:
    batch_ids:  list[str] = []
    batch_imgs: list      = []

    def _flush():
        if not batch_ids:
            return
        try:
            result = extract_fn(batch_imgs)
        except Exception as e:
            log.error("  extract error (batch of %d): %s", len(batch_ids), e)
            batch_ids.clear(); batch_imgs.clear()
            return

        if isinstance(result, np.ndarray):
            result = {list(stores.keys())[0]: result}

        for key, store in stores.items():
            store.append(batch_ids, result[key])

        batch_ids.clear(); batch_imgs.clear()

    for row in tqdm(rows, desc="Extracting", unit="img"):
        img = _load_image(row["file_path"])
        if img is None:
            continue
        batch_ids.append(row["image_id"])
        batch_imgs.append(img)
        if len(batch_ids) >= batch_size:
            _flush()

    _flush()


def run_extraction(manifest: pd.DataFrame, feature_set: str, batch_size: int, device: str) -> None:
    import torch
    import torch.nn as nn
    from torchvision import models, transforms

    do_dd = feature_set in ("deepdanbooru", "all")
    do_ep = feature_set in ("eva02_pixai",  "all")

    store_dd  = H5FeatureStore(FEATURES_DIR / "eval_deepdanbooru.h5", DEEPDANBOORU_DIM)
    store_eva = H5FeatureStore(FEATURES_DIR / "eval_eva02.h5",        EVA02_DIM)
    store_pai = H5FeatureStore(FEATURES_DIR / "eval_pixai.h5",        PIXAI_DIM)

    rows = manifest.to_dict("records")

    # ── DeepDanbooru ─────────────────────────────────────────────────────────
    if do_dd:
        done = store_dd.existing_ids()
        remaining = [r for r in rows if r["image_id"] not in done]
        log.info("[DeepDanbooru] %d done, %d remaining", len(done), len(remaining))

        if remaining:
            from danbooru_resnet import _resnet

            model = _resnet(models.resnet50, 6000)
            state = torch.hub.load_state_dict_from_url(
                "https://github.com/RF5/danbooru-pretrained/releases/download"
                "/v0.1/resnet50-13306192.pth",
                map_location="cpu", progress=True,
            )
            model.load_state_dict(state)
            model.eval().to(device)

            tf = transforms.Compose([
                transforms.Resize(DEEPDANBOORU_IMAGE_SIZE),
                transforms.CenterCrop(DEEPDANBOORU_IMAGE_SIZE),
                transforms.ToTensor(),
                transforms.Normalize([0.7137, 0.6628, 0.6519], [0.2970, 0.3017, 0.2979]),
            ])

            @torch.inference_mode()
            def _dd_extract(imgs):
                t = torch.stack([tf(i) for i in imgs]).to(device)
                return torch.sigmoid(model(t)).cpu().numpy()

            _extract_loop(_dd_extract, remaining, batch_size, {"deepdanbooru": store_dd})
            del model
            if "cuda" in device or "hip" in device:
                torch.cuda.empty_cache()

    # ── EVA02 + PixAI ─────────────────────────────────────────────────────────
    if do_ep:
        done = store_eva.existing_ids() & store_pai.existing_ids()
        remaining = [r for r in rows if r["image_id"] not in done]
        log.info("[EVA02/PixAI] %d done, %d remaining", len(done), len(remaining))

        if remaining:
            import timm
            from pixai_tagger import TaggingHead, load_model
            from huggingface_hub import hf_hub_download

            PIXAI_MODEL_DIR.mkdir(parents=True, exist_ok=True)
            for fname in PIXAI_MODEL_FILES:
                local = PIXAI_MODEL_DIR / fname
                if not local.exists():
                    log.info("[PixAI] Downloading %s …", fname)
                    hf_hub_download(repo_id=PIXAI_MODEL_REPO, filename=fname,
                                    local_dir=str(PIXAI_MODEL_DIR))

            encoder = timm.create_model(
                "hf_hub:SmilingWolf/wd-eva02-large-tagger-v3", pretrained=False)
            encoder.reset_classifier(0)
            decoder = TaggingHead(EVA02_DIM, PIXAI_DIM)
            full_model = nn.Sequential(encoder, decoder)
            states = torch.load(str(PIXAI_MODEL_DIR / "model_v0.9.pth"),
                                map_location="cpu", weights_only=True)
            full_model.load_state_dict(states)
            full_model.eval().to(device)
            enc = full_model[0]
            dec = full_model[1]

            tf_ep = transforms.Compose([
                transforms.Resize((PIXAI_IMAGE_SIZE, PIXAI_IMAGE_SIZE)),
                transforms.ToTensor(),
                transforms.Normalize([0.5, 0.5, 0.5], [0.5, 0.5, 0.5]),
            ])

            @torch.inference_mode()
            def _ep_extract(imgs):
                t   = torch.stack([tf_ep(_to_rgb(i)) for i in imgs]).to(device)
                emb = enc(t)
                pai = dec(emb)
                return {"eva02": emb.cpu().numpy(), "pixai": pai.cpu().numpy()}

            _extract_loop(_ep_extract, remaining, batch_size,
                          {"eva02": store_eva, "pixai": store_pai})
            del full_model
            if "cuda" in device or "hip" in device:
                torch.cuda.empty_cache()


# ── Entry point ───────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--features", choices=["deepdanbooru", "eva02_pixai", "all"], default="all",
        help="Which feature types to extract (default: all)",
    )
    parser.add_argument("--batch-size", type=int, default=DEFAULT_BATCH_SIZE)
    parser.add_argument(
        "--device", default="auto",
        help="PyTorch device string, or 'auto' to detect (default: auto)",
    )
    parser.add_argument(
        "--skip-extraction", action="store_true",
        help="Only build the manifest parquet; skip feature extraction",
    )
    args = parser.parse_args()

    # ── Build manifest ────────────────────────────────────────────────────────
    manifest = build_manifest()

    METADATA_DIR.mkdir(parents=True, exist_ok=True)
    manifest.to_parquet(EVAL_MANIFEST, index=False)
    log.info("Saved eval manifest → %s  (%d images)", EVAL_MANIFEST, len(manifest))

    if args.skip_extraction:
        log.info("--skip-extraction set; done.")
        return

    # ── Resolve device ────────────────────────────────────────────────────────
    if args.device == "auto":
        import torch
        if torch.cuda.is_available():
            device = "cuda"
        else:
            device = "cpu"
        log.info("Auto-detected device: %s", device)
    else:
        device = args.device

    # ── Extract features ──────────────────────────────────────────────────────
    run_extraction(manifest, args.features, args.batch_size, device)

    log.info("Done. Eval feature files:")
    for name in ("eval_deepdanbooru.h5", "eval_eva02.h5", "eval_pixai.h5"):
        p = FEATURES_DIR / name
        if p.exists():
            size_mb = p.stat().st_size / 1e6
            log.info("  %s  (%.1f MB)", p, size_mb)


if __name__ == "__main__":
    main()
