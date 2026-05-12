#!/usr/bin/env python3
"""
Backfill multi-axis similarity vectors into Qdrant for existing MongoDB images.

For each image with a localPath that exists on disk, runs the PixAI model to
obtain both the EVA02 embedding (1024-dim) and the full 13461-dim probability
vector, then splits the probability vector into semantic axes using the
pre-computed tag category file:

  character  – PixAI feature tags classified as 'character'
               (hair, eyes, body, clothing, accessories, etc.)
  situation  – PixAI feature tags classified as 'situation'
               (pose, action, expression, composition, scenario, etc.)
  style      – PixAI feature tags classified as 'style'
               (art style, medium, shading, era, etc.)
  eva02      – Full EVA02 encoder output (general visual similarity)

Results are upserted into a new Qdrant collection with named vectors.

Usage:
    cd worker
    venv/bin/python backfill_multiaxis_qdrant.py [options]

Options:
    --batch-size N     GPU batch size (default: 32)
    --limit N          Stop after N images (default: unlimited)
    --no-skip-existing Re-upsert images already in Qdrant (default: skip)
    --status STATUS    Comma-separated MongoDB status filter (default: inferred)
    --dry-run          Print stats without doing anything
"""

import argparse
import json
import logging
import os
import sys
import uuid
from pathlib import Path

import numpy as np
import torch
import torch.nn as nn
from PIL import Image, UnidentifiedImageError
from pymongo import MongoClient
from torchvision import transforms

WORKER_DIR = Path(__file__).parent
REPO_ROOT  = WORKER_DIR.parent
PU_DIR     = REPO_ROOT / "pu-learning"

PIXAI_TAG_CATEGORIES_JSON = PU_DIR / "data" / "metadata" / "pixai_tag_categories.json"

IMAGE_CACHE_DIR = Path(os.environ.get("IMAGE_CACHE_DIR", "/mnt/cache2/danbooru-ml-classifier/images"))
MONGODB_URI     = os.environ.get("MONGODB_URI", "mongodb://localhost:27017")
MONGODB_DB      = os.environ.get("MONGODB_DB", "danbooru-ml-classifier")
QDRANT_HOST     = os.environ.get("QDRANT_HOST", "localhost")
QDRANT_PORT     = int(os.environ.get("QDRANT_PORT", "6333"))
QDRANT_COLLECTION_MULTIAXIS = "image_embeddings_multiaxis"

PIXAI_MODEL_DIR   = Path.home() / ".cache" / "pixai-tagger"
PIXAI_MODEL_REPO  = "pixai-labs/pixai-tagger-v0.9"
PIXAI_MODEL_FILES = ["model_v0.9.pth", "tags_v0.9_13k.json", "char_ip_map.json"]

EVA02_DIM        = 1024
PIXAI_DIM        = 13461
PIXAI_IMAGE_SIZE = 448

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

sys.path.insert(0, str(WORKER_DIR))


# ── Tag category index mapping ─────────────────────────────────────────────────

def build_axis_indices() -> dict[str, np.ndarray]:
    """
    Load pixai_tag_categories.json and tags_v0.9_13k.json and return a dict
    mapping axis name → int array of global PixAI indices (into the 13461-dim
    probability vector) that belong to that axis.

    Only feature tags (index < gen_tag_count) are included; character name tags
    (index >= gen_tag_count) are excluded.
    """
    with open(PIXAI_TAG_CATEGORIES_JSON) as f:
        tag_categories: dict[str, str] = json.load(f)

    tags_json = PIXAI_MODEL_DIR / "tags_v0.9_13k.json"
    with open(tags_json) as f:
        tag_data = json.load(f)
    tag_map: dict[str, int] = tag_data["tag_map"]
    gen_tag_count: int = tag_data["tag_split"]["gen_tag_count"]

    axes: dict[str, list[int]] = {"character": [], "situation": [], "style": []}
    for tag, global_idx in tag_map.items():
        if global_idx >= gen_tag_count:
            # Character name tags (indices gen_tag_count..end) all go to character axis
            axes["character"].append(global_idx)
            continue
        cat = tag_categories.get(tag)
        if cat in axes:
            axes[cat].append(global_idx)

    result = {k: np.array(sorted(v), dtype=np.int32) for k, v in axes.items()}
    for k, v in result.items():
        log.info("Axis '%s': %d dims", k, len(v))
    return result


# ── PixAI full model extractor ────────────────────────────────────────────────

from pixai_tagger import TaggingHead


class PixAIFullExtractor:
    """Runs the full PixAI model (encoder + head) and returns both EVA02 and tag probs."""

    def __init__(self, device: str):
        import timm

        self.device = device
        PIXAI_MODEL_DIR.mkdir(parents=True, exist_ok=True)

        for fname in PIXAI_MODEL_FILES:
            local = PIXAI_MODEL_DIR / fname
            if not local.exists():
                from huggingface_hub import hf_hub_download
                log.info("[PixAI] Downloading %s ...", fname)
                hf_hub_download(repo_id=PIXAI_MODEL_REPO, filename=fname,
                                local_dir=str(PIXAI_MODEL_DIR))

        log.info("[PixAI] Loading model ...")
        encoder = timm.create_model("hf_hub:SmilingWolf/wd-eva02-large-tagger-v3", pretrained=False)
        encoder.reset_classifier(0)
        decoder = TaggingHead(EVA02_DIM, PIXAI_DIM)
        full_model = nn.Sequential(encoder, decoder)
        states = torch.load(str(PIXAI_MODEL_DIR / "model_v0.9.pth"),
                            map_location="cpu", weights_only=True)
        full_model.load_state_dict(states)
        full_model.eval()

        self.encoder = full_model[0].to(device)
        self.decoder = full_model[1].to(device)
        log.info("[PixAI] Ready on %s", device)

        self.transform = transforms.Compose([
            transforms.Resize((PIXAI_IMAGE_SIZE, PIXAI_IMAGE_SIZE)),
            transforms.ToTensor(),
            transforms.Normalize(mean=[0.5, 0.5, 0.5], std=[0.5, 0.5, 0.5]),
        ])

    @torch.inference_mode()
    def extract_batch(
        self, images: list[Image.Image]
    ) -> tuple[np.ndarray, np.ndarray]:
        """Returns (eva02: float32 (B,1024), pixai_probs: float32 (B,13461))."""
        def to_rgb(img: Image.Image) -> Image.Image:
            if img.mode == "RGBA":
                bg = Image.new("RGB", img.size, (255, 255, 255))
                bg.paste(img, mask=img.split()[3])
                return bg
            return img.convert("RGB") if img.mode != "RGB" else img

        tensors = torch.stack([self.transform(to_rgb(img)) for img in images]).to(self.device)
        emb  = self.encoder(tensors)
        probs = self.decoder(emb)
        return emb.cpu().numpy(), probs.cpu().numpy()


# ── Qdrant helpers ─────────────────────────────────────────────────────────────

def _mongo_id_to_qdrant_uuid(mongo_id: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_OID, mongo_id))


def get_qdrant_client(axis_dims: dict[str, int]):
    from qdrant_client import QdrantClient
    from qdrant_client.models import Distance, VectorParams, PayloadSchemaType

    client = QdrantClient(host=QDRANT_HOST, port=QDRANT_PORT, timeout=30)

    if not client.collection_exists(QDRANT_COLLECTION_MULTIAXIS):
        log.info("[Qdrant] Creating collection '%s' ...", QDRANT_COLLECTION_MULTIAXIS)
        vectors_config = {
            "eva02": VectorParams(size=EVA02_DIM, distance=Distance.COSINE),
            **{
                axis: VectorParams(size=dim, distance=Distance.COSINE)
                for axis, dim in axis_dims.items()
            },
        }
        client.create_collection(
            collection_name=QDRANT_COLLECTION_MULTIAXIS,
            vectors_config=vectors_config,
        )
        for field in ("date", "type", "status"):
            client.create_payload_index(
                QDRANT_COLLECTION_MULTIAXIS, field, PayloadSchemaType.KEYWORD
            )
        log.info("[Qdrant] Collection ready.")

    return client


def qdrant_existing_ids(client, mongo_ids: list[str]) -> set[str]:
    uuids = [_mongo_id_to_qdrant_uuid(mid) for mid in mongo_ids]
    results = client.retrieve(
        collection_name=QDRANT_COLLECTION_MULTIAXIS,
        ids=uuids,
        with_payload=False,
        with_vectors=False,
    )
    found = {str(r.id) for r in results}
    return {mid for mid, uid in zip(mongo_ids, uuids) if uid in found}


# ── Main ───────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--batch-size", type=int, default=32)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument(
        "--no-skip-existing", dest="skip_existing",
        action="store_false", default=True,
        help="Re-upsert all images even if already in Qdrant",
    )
    parser.add_argument(
        "--status", type=str, default="inferred",
        help="Comma-separated MongoDB status values (default: inferred)",
    )
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    statuses = [s.strip() for s in args.status.split(",")]
    device = "cuda" if torch.cuda.is_available() else "cpu"
    log.info("Device: %s", device)

    # Build axis index arrays
    axis_indices = build_axis_indices()
    axis_dims = {k: len(v) for k, v in axis_indices.items()}

    # MongoDB
    mongo_client = MongoClient(MONGODB_URI)
    col = mongo_client[MONGODB_DB]["images"]

    mongo_filter = {
        "status": {"$in": statuses},
        "localPath": {"$exists": True},
    }
    all_docs = list(col.find(mongo_filter, {"_id": 1, "localPath": 1, "date": 1, "type": 1, "status": 1}))
    log.info("MongoDB docs matching filter: %d", len(all_docs))

    processable = [
        doc for doc in all_docs
        if doc.get("localPath") and Path(doc["localPath"]).exists()
    ]
    log.info("Files on disk: %d  (skipped %d missing)", len(processable), len(all_docs) - len(processable))

    if args.limit:
        processable = processable[:args.limit]
        log.info("Limiting to %d images.", args.limit)

    if args.dry_run:
        log.info("[dry-run] Would process %d images. Exiting.", len(processable))
        return

    # Qdrant
    qdrant = get_qdrant_client(axis_dims)

    # Model
    extractor = PixAIFullExtractor(device)

    n_total    = len(processable)
    n_upserted = 0
    n_skipped  = 0
    n_error    = 0
    batch_size = args.batch_size

    for batch_start in range(0, n_total, batch_size):
        batch_docs = processable[batch_start : batch_start + batch_size]
        mongo_ids  = [str(doc["_id"]) for doc in batch_docs]

        # Skip already-indexed if requested
        if args.skip_existing:
            already_done = qdrant_existing_ids(qdrant, mongo_ids)
            new_docs  = [doc for doc, mid in zip(batch_docs, mongo_ids) if mid not in already_done]
            n_skipped += len(batch_docs) - len(new_docs)
            if not new_docs:
                log.info(
                    "[%d/%d] all %d in batch already indexed — skip",
                    batch_start + len(batch_docs), n_total, len(batch_docs),
                )
                continue
            batch_docs = new_docs
            mongo_ids  = [str(doc["_id"]) for doc in batch_docs]

        # Load images
        batch_imgs: list[Image.Image] = []
        valid_docs: list[dict] = []
        for doc in batch_docs:
            try:
                img = Image.open(doc["localPath"])
                img.load()
                batch_imgs.append(img)
                valid_docs.append(doc)
            except (UnidentifiedImageError, OSError) as exc:
                log.warning("Cannot open %s: %s", doc.get("localPath"), exc)
                n_error += 1

        if not batch_imgs:
            continue

        # GPU extraction
        try:
            X_eva, X_pxai = extractor.extract_batch(batch_imgs)
        except Exception as exc:
            log.error("Extraction failed for batch at %d: %s", batch_start, exc)
            n_error += len(batch_imgs)
            continue
        finally:
            batch_imgs.clear()

        # Build Qdrant points with named vectors
        from qdrant_client.models import PointStruct

        points = []
        for doc, eva_emb, pxai_probs in zip(valid_docs, X_eva, X_pxai):
            mongo_id = str(doc["_id"])
            named_vectors = {
                "eva02": eva_emb.tolist(),
                **{
                    axis: pxai_probs[indices].tolist()
                    for axis, indices in axis_indices.items()
                },
            }
            points.append(PointStruct(
                id=_mongo_id_to_qdrant_uuid(mongo_id),
                vector=named_vectors,
                payload={
                    "image_id": mongo_id,
                    "date":     doc.get("date", ""),
                    "type":     doc.get("type", ""),
                    "status":   doc.get("status", ""),
                },
            ))

        try:
            qdrant.upsert(collection_name=QDRANT_COLLECTION_MULTIAXIS, points=points)
            n_upserted += len(points)
        except Exception as exc:
            log.error("Qdrant upsert failed: %s", exc)
            n_error += len(points)
            continue

        log.info(
            "[%d/%d] upserted %d  |  total: done=%d skip=%d err=%d",
            batch_start + len(batch_docs), n_total,
            len(points), n_upserted, n_skipped, n_error,
        )

    log.info(
        "Done. upserted=%d  skipped=%d  error=%d",
        n_upserted, n_skipped, n_error,
    )


if __name__ == "__main__":
    main()
