#!/usr/bin/env python3
"""
Cluster favorited images using UMAP + HDBSCAN on EVA02 embeddings from Qdrant,
then write cluster assignments to Firestore favorites/ collection and
cluster metadata to favorite_categories/ collection.

Cluster IDs follow the format:  auto:cluster_v{version}_{cluster_index}
  e.g. auto:cluster_v1_0, auto:cluster_v1_1, ...

Noise points (HDBSCAN label = -1) are by default assigned to the nearest cluster
centroid in UMAP-reduced space. Use --no-assign-noise to leave them uncategorized.

After clustering, a thumbnail grid (5×5) is saved for each cluster under
--output-dir (default: /tmp/cluster_previews/).

Usage:
    cd worker
    GOOGLE_APPLICATION_CREDENTIALS=/path/to/sa.json \
    venv/bin/python cluster_favorites.py [options]

Options:
    --version STR           Version string, e.g. "1" → IDs become auto:cluster_v1_* (default: 1)
    --umap-components N     UMAP output dimensions (default: 30)
    --umap-neighbors N      UMAP n_neighbors (default: 15)
    --min-cluster-size N    HDBSCAN min_cluster_size (default: 15)
    --top-tags N            Number of top discriminative tags per cluster name (default: 3)
    --output-dir PATH       Directory to save cluster preview images (default: /tmp/cluster_previews)
    --thumb-size N          Thumbnail size in pixels (default: 512)
    --no-assign-noise       Leave noise points uncategorized instead of assigning to nearest cluster
    --dry-run               Print results and save previews without writing to Firestore
"""

import argparse
import logging
import os
import random
import sys
import uuid
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
from bson import ObjectId
from pymongo import MongoClient

WORKER_DIR = Path(__file__).parent

MONGODB_URI       = os.environ.get("MONGODB_URI", "mongodb://localhost:27017")
MONGODB_DB        = os.environ.get("MONGODB_DB", "danbooru-ml-classifier")
QDRANT_HOST       = os.environ.get("QDRANT_HOST", "localhost")
QDRANT_PORT       = int(os.environ.get("QDRANT_PORT", "6333"))
QDRANT_COLLECTION = "image_embeddings"
SA_KEY            = os.environ.get(
    "GOOGLE_APPLICATION_CREDENTIALS",
    str(WORKER_DIR.parent / "danbooru-ml-classifier-firebase-adminsdk-uivsj-3a07a63db5.json"),
)

QDRANT_RETRIEVE_BATCH = 200
GRID_COLS             = 5
GRID_ROWS             = 5
GRID_SAMPLES          = GRID_COLS * GRID_ROWS  # 25
DEFAULT_THUMB_SIZE    = 512
DEFAULT_MIN_CLUSTER   = 15

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _mongo_id_to_qdrant_uuid(mongo_id: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_OID, mongo_id))


def cluster_id(version: str, cluster_index: int) -> str:
    return f"auto:cluster_v{version}_{cluster_index}"


def is_auto_cluster_category(cat: str) -> bool:
    return cat.startswith("auto:cluster_v")


def _to_objectid(mid: str):
    try:
        return ObjectId(mid)
    except Exception:
        return mid


# ── Qdrant vector fetch ────────────────────────────────────────────────────────

def fetch_vectors(qdrant_client, image_ids: list[str]) -> dict[str, np.ndarray]:
    """
    Batch-fetch EVA02 vectors from Qdrant.
    Returns dict of image_id → float32 array (1024,).
    IDs not found in Qdrant are omitted.
    """
    id_to_uuid = {mid: _mongo_id_to_qdrant_uuid(mid) for mid in image_ids}
    uuid_to_id = {v: k for k, v in id_to_uuid.items()}

    result: dict[str, np.ndarray] = {}
    uuids = list(id_to_uuid.values())

    for start in range(0, len(uuids), QDRANT_RETRIEVE_BATCH):
        batch_uuids = uuids[start:start + QDRANT_RETRIEVE_BATCH]
        points = qdrant_client.retrieve(
            collection_name=QDRANT_COLLECTION,
            ids=batch_uuids,
            with_vectors=True,
            with_payload=False,
        )
        for pt in points:
            mid = uuid_to_id.get(str(pt.id))
            if mid and pt.vector is not None:
                result[mid] = np.array(pt.vector, dtype=np.float32)

    return result


# ── Tag-based cluster naming ───────────────────────────────────────────────────

def compute_cluster_names(
    cluster_labels: np.ndarray,
    image_ids: list[str],
    all_tag_probs: dict[str, dict[str, float]],
    top_n: int = 3,
) -> dict[int, tuple[str, list[str]]]:
    """
    For each cluster label (excluding -1), compute a display name and top tags
    using a TF-IDF-like discriminativeness score:
        cluster_mean_prob / (global_mean_prob + 0.01)

    Tags with cluster_mean_prob < 0.1 are excluded.
    Falls back to "cluster_{index}" when no tags qualify.
    """
    unique_labels = sorted(set(cluster_labels) - {-1})
    global_count = sum(1 for mid in image_ids if mid in all_tag_probs)

    global_sums: dict[str, float] = defaultdict(float)
    for mid in image_ids:
        if mid in all_tag_probs:
            for tag, prob in all_tag_probs[mid].items():
                global_sums[tag] += prob

    names: dict[int, tuple[str, list[str]]] = {}

    for label in unique_labels:
        cluster_ids = [image_ids[i] for i, lbl in enumerate(cluster_labels) if lbl == label]
        cluster_size = len(cluster_ids)

        cluster_sums: dict[str, float] = defaultdict(float)
        cluster_count = 0
        for mid in cluster_ids:
            if mid in all_tag_probs:
                for tag, prob in all_tag_probs[mid].items():
                    cluster_sums[tag] += prob
                cluster_count += 1

        if cluster_count == 0:
            names[label] = (f"cluster_{label}", [])
            continue

        scores: dict[str, float] = {}
        for tag, csum in cluster_sums.items():
            c_mean = csum / cluster_count
            if c_mean < 0.1:
                continue
            g_mean = global_sums[tag] / global_count if global_count > 0 else 0.0
            scores[tag] = c_mean / (g_mean + 0.01)

        top_tags = sorted(scores, key=lambda t: scores[t], reverse=True)[:top_n]
        display_name = "・".join(top_tags) if top_tags else f"cluster_{label}"
        names[label] = (display_name, top_tags)

    return names


# ── Thumbnail grid ─────────────────────────────────────────────────────────────

def save_cluster_preview(
    label: int,
    version: str,
    image_ids_in_cluster: list[str],
    id_to_localpath: dict[str, str],
    display_name: str,
    output_dir: Path,
    thumb_size: int = 128,
) -> Path | None:
    """
    Save a 5×5 thumbnail grid for a cluster.
    Randomly samples up to GRID_SAMPLES images that have a local file.
    Returns the output path, or None if no images could be loaded.
    """
    from PIL import Image, ImageDraw, ImageFont

    available = [mid for mid in image_ids_in_cluster if id_to_localpath.get(mid) and Path(id_to_localpath[mid]).exists()]
    if not available:
        log.warning("Cluster %d: no local files available for preview.", label)
        return None

    sampled = random.sample(available, min(GRID_SAMPLES, len(available)))

    thumbs = []
    for mid in sampled:
        try:
            img = Image.open(id_to_localpath[mid]).convert("RGB")
            img.thumbnail((thumb_size, thumb_size), Image.LANCZOS)
            # Pad to exact square
            padded = Image.new("RGB", (thumb_size, thumb_size), (30, 30, 30))
            x = (thumb_size - img.width) // 2
            y = (thumb_size - img.height) // 2
            padded.paste(img, (x, y))
            thumbs.append(padded)
        except Exception as exc:
            log.debug("Cannot open %s: %s", id_to_localpath[mid], exc)

    if not thumbs:
        return None

    # Fill remaining cells with grey placeholders
    placeholder = Image.new("RGB", (thumb_size, thumb_size), (50, 50, 50))
    while len(thumbs) < GRID_SAMPLES:
        thumbs.append(placeholder.copy())

    # Label bar height
    label_h = 24
    grid_w = GRID_COLS * thumb_size
    grid_h = GRID_ROWS * thumb_size + label_h

    canvas = Image.new("RGB", (grid_w, grid_h), (20, 20, 20))
    draw = ImageDraw.Draw(canvas)

    for idx, thumb in enumerate(thumbs):
        row, col = divmod(idx, GRID_COLS)
        canvas.paste(thumb, (col * thumb_size, label_h + row * thumb_size))

    cid = cluster_id(version, label)
    header = f"{cid}  |  {display_name}  (n={len(available)})"
    try:
        font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 14)
    except Exception:
        font = ImageFont.load_default()
    draw.text((4, 4), header, fill=(220, 220, 220), font=font)

    out_path = output_dir / f"cluster_v{version}_{label:03d}.png"
    canvas.save(str(out_path))
    return out_path


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--version", default="1",
                        help="Version string for cluster IDs (default: 1)")
    parser.add_argument("--umap-components", type=int, default=30)
    parser.add_argument("--umap-neighbors", type=int, default=15)
    parser.add_argument("--min-cluster-size", type=int, default=DEFAULT_MIN_CLUSTER)
    parser.add_argument("--top-tags", type=int, default=3)
    parser.add_argument("--output-dir", default="/tmp/cluster_previews",
                        help="Directory for cluster preview images (default: /tmp/cluster_previews)")
    parser.add_argument("--thumb-size", type=int, default=DEFAULT_THUMB_SIZE,
                        help=f"Thumbnail size in pixels (default: {DEFAULT_THUMB_SIZE})")
    parser.add_argument("--no-assign-noise", action="store_true",
                        help="Leave noise points uncategorized instead of assigning to nearest cluster")
    parser.add_argument("--dry-run", action="store_true",
                        help="Save previews but skip Firestore writes")
    args = parser.parse_args()

    version = args.version
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    log.info("Cluster version: v%s  |  preview dir: %s", version, output_dir)

    # ── Firebase / Firestore ──────────────────────────────────────────────────
    import firebase_admin
    from firebase_admin import credentials, firestore as fs
    from google.cloud.firestore_v1.base_query import FieldFilter

    if not firebase_admin._apps:
        cred = credentials.Certificate(SA_KEY)
        firebase_admin.initialize_app(cred)
    db = fs.client()

    # ── Fetch favorited image IDs ─────────────────────────────────────────────
    log.info("Fetching favorited image IDs from Firestore ...")
    fav_docs = list(
        db.collection("favorites")
        .where(filter=FieldFilter("isFavorited", "==", True))
        .stream()
    )
    image_ids: list[str] = [doc.id for doc in fav_docs]
    fav_categories: dict[str, list[str]] = {
        doc.id: doc.to_dict().get("categories", []) for doc in fav_docs
    }
    log.info("Favorited images: %d", len(image_ids))

    # ── Fetch EVA02 vectors from Qdrant ───────────────────────────────────────
    from qdrant_client import QdrantClient

    qdrant = QdrantClient(host=QDRANT_HOST, port=QDRANT_PORT, timeout=30)
    log.info("Fetching EVA02 vectors from Qdrant ...")
    id_to_vec = fetch_vectors(qdrant, image_ids)
    log.info(
        "Vectors found: %d / %d  (missing: %d)",
        len(id_to_vec), len(image_ids), len(image_ids) - len(id_to_vec),
    )

    if len(id_to_vec) < 2:
        log.error("Too few vectors to cluster. Aborting.")
        sys.exit(1)

    valid_ids = [mid for mid in image_ids if mid in id_to_vec]
    X = np.stack([id_to_vec[mid] for mid in valid_ids])  # (N, 1024)
    log.info("Feature matrix shape: %s", X.shape)

    # ── Fetch tag probabilities + local paths from MongoDB ────────────────────
    log.info("Fetching tag probabilities and local paths from MongoDB ...")
    mongo = MongoClient(MONGODB_URI)
    col = mongo[MONGODB_DB]["images"]

    oids = [_to_objectid(mid) for mid in valid_ids]
    cursor = col.find(
        {"_id": {"$in": oids}},
        {"importantTagProbs.pixai": 1, "localPath": 1},
    )
    all_tag_probs: dict[str, dict[str, float]] = {}
    id_to_localpath: dict[str, str] = {}
    for doc in cursor:
        mid = str(doc["_id"])
        pixai = doc.get("importantTagProbs", {}).get("pixai")
        if pixai:
            all_tag_probs[mid] = pixai
        lp = doc.get("localPath")
        if lp:
            id_to_localpath[mid] = lp

    log.info(
        "Images with pixai tag probs: %d / %d  |  with local path: %d",
        len(all_tag_probs), len(valid_ids), len(id_to_localpath),
    )

    # ── UMAP dimensionality reduction ─────────────────────────────────────────
    log.info(
        "Running UMAP (n_components=%d, n_neighbors=%d) ...",
        args.umap_components, args.umap_neighbors,
    )
    import umap

    reducer = umap.UMAP(
        n_components=args.umap_components,
        n_neighbors=args.umap_neighbors,
        min_dist=0.0,
        metric="cosine",
        random_state=42,
    )
    X_reduced = reducer.fit_transform(X)
    log.info("UMAP output shape: %s", X_reduced.shape)

    # ── HDBSCAN clustering ────────────────────────────────────────────────────
    log.info("Running HDBSCAN (min_cluster_size=%d) ...", args.min_cluster_size)
    import hdbscan

    clusterer = hdbscan.HDBSCAN(
        min_cluster_size=args.min_cluster_size,
        metric="euclidean",
        prediction_data=True,
    )
    labels: np.ndarray = clusterer.fit_predict(X_reduced)

    unique_labels, counts = np.unique(labels, return_counts=True)
    noise_count = int(counts[unique_labels == -1][0]) if -1 in unique_labels else 0
    cluster_label_vals = unique_labels[unique_labels >= 0]
    log.info(
        "Clusters: %d  |  noise points: %d  |  total clustered: %d",
        len(cluster_label_vals),
        noise_count,
        int(np.sum(labels >= 0)),
    )

    # ── Assign noise points to nearest cluster centroid ───────────────────────
    if not args.no_assign_noise and noise_count > 0:
        centroids = np.stack([
            X_reduced[labels == lbl].mean(axis=0) for lbl in cluster_label_vals
        ])  # (n_clusters, n_components)
        noise_mask = labels == -1
        noise_vecs = X_reduced[noise_mask]  # (n_noise, n_components)
        # Squared Euclidean distance to each centroid
        dists = np.sum((noise_vecs[:, None, :] - centroids[None, :, :]) ** 2, axis=2)
        nearest = cluster_label_vals[np.argmin(dists, axis=1)]
        labels = labels.copy()
        labels[noise_mask] = nearest
        log.info(
            "Noise points assigned to nearest centroid: %d  →  new noise count: 0",
            noise_count,
        )
        noise_count = 0

    # ── Compute cluster display names ─────────────────────────────────────────
    log.info("Computing cluster names from tag probabilities ...")
    cluster_names = compute_cluster_names(labels, valid_ids, all_tag_probs, top_n=args.top_tags)

    for cidx, (dname, tags) in sorted(cluster_names.items()):
        cid = cluster_id(version, cidx)
        n = int(np.sum(labels == cidx))
        log.info("  %s  →  '%s'  (n=%d)", cid, dname, n)

    # ── Save thumbnail previews ───────────────────────────────────────────────
    log.info("Saving cluster preview images to %s ...", output_dir)
    saved_previews = 0
    for cidx, (display_name, _) in sorted(cluster_names.items()):
        cluster_member_ids = [valid_ids[i] for i, lbl in enumerate(labels) if lbl == cidx]
        out_path = save_cluster_preview(
            label=cidx,
            version=version,
            image_ids_in_cluster=cluster_member_ids,
            id_to_localpath=id_to_localpath,
            display_name=display_name,
            output_dir=output_dir,
            thumb_size=args.thumb_size,
        )
        if out_path:
            log.info("  Saved: %s", out_path)
            saved_previews += 1

    log.info("Previews saved: %d / %d clusters", saved_previews, len(cluster_names))

    if args.dry_run:
        log.info("[dry-run] Skipping Firestore writes.")
        return

    # ── Write favorite_categories collection ──────────────────────────────────
    log.info("Writing favorite_categories to Firestore ...")
    now = datetime.now(timezone.utc)
    BATCH_LIMIT = 500
    batch = db.batch()
    batch_count = 0

    for cidx, (display_name, top_tags) in cluster_names.items():
        cid = cluster_id(version, cidx)
        n = int(np.sum(labels == cidx))
        doc_ref = db.collection("favorite_categories").document(cid)
        batch.set(doc_ref, {
            "id": cid,
            "displayName": display_name,
            "version": version,
            "clusterIndex": cidx,
            "topTags": top_tags,
            "imageCount": n,
            "isAutoGenerated": True,
            "createdAt": now,
        })
        batch_count += 1
        if batch_count >= BATCH_LIMIT:
            batch.commit()
            batch = db.batch()
            batch_count = 0

    if batch_count > 0:
        batch.commit()
    log.info("Wrote %d category documents.", len(cluster_names))

    # ── Update favorites/ documents ───────────────────────────────────────────
    log.info("Updating favorites/ documents ...")
    batch = db.batch()
    batch_count = 0
    updated = 0
    skipped_noise = 0

    for i, mid in enumerate(valid_ids):
        label = int(labels[i])
        existing = fav_categories.get(mid, [])
        kept = [c for c in existing if not is_auto_cluster_category(c)]

        if label == -1:
            new_cats = kept
            skipped_noise += 1
        else:
            new_cats = kept + [cluster_id(version, label)]

        if new_cats == existing:
            continue

        doc_ref = db.collection("favorites").document(mid)
        batch.set(doc_ref, {"isFavorited": True, "categories": new_cats}, merge=False)
        batch_count += 1
        updated += 1

        if batch_count >= BATCH_LIMIT:
            batch.commit()
            batch = db.batch()
            batch_count = 0

    if batch_count > 0:
        batch.commit()

    log.info(
        "Done. favorites updated: %d  |  noise (no cluster assigned): %d",
        updated, skipped_noise,
    )


if __name__ == "__main__":
    main()
