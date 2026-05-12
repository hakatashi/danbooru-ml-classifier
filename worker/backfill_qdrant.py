#!/usr/bin/env python3
"""
Backfill EVA02 embeddings into Qdrant for existing MongoDB images.

This script is intended to be run once after Qdrant is set up to populate
the vector database with all images that have already been processed by
main.py (status='inferred') but do not yet have a Qdrant entry.

Usage:
    cd worker
    venv/bin/python backfill_qdrant.py [options]

Options:
    --batch-size N     GPU batch size for EVA02 extraction (default: 64)
    --limit N          Stop after processing N images (default: unlimited)
    --skip-existing    Skip images already present in Qdrant (default: True)
    --no-skip-existing Re-upsert all images even if already in Qdrant
    --status STATUS    MongoDB status filter, comma-separated (default: inferred)
    --dry-run          Print what would be processed without doing it
"""

import argparse
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
from torchvision import models, transforms

# ── Paths ──────────────────────────────────────────────────────────────────────
WORKER_DIR = Path(__file__).parent
REPO_ROOT  = WORKER_DIR.parent

IMAGE_CACHE_DIR   = Path(os.environ.get("IMAGE_CACHE_DIR", "/mnt/cache2/danbooru-ml-classifier/images"))
MONGODB_URI       = os.environ.get("MONGODB_URI", "mongodb://localhost:27017")
MONGODB_DB        = os.environ.get("MONGODB_DB", "danbooru-ml-classifier")
QDRANT_HOST       = os.environ.get("QDRANT_HOST", "localhost")
QDRANT_PORT       = int(os.environ.get("QDRANT_PORT", "6333"))
QDRANT_COLLECTION = "image_embeddings"

PIXAI_MODEL_DIR   = Path.home() / ".cache" / "pixai-tagger"
PIXAI_MODEL_REPO  = "pixai-labs/pixai-tagger-v0.9"
PIXAI_MODEL_FILES = ["model_v0.9.pth", "tags_v0.9_13k.json", "char_ip_map.json"]

EVA02_DIM        = 1024
PIXAI_IMAGE_SIZE = 448

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

sys.path.insert(0, str(WORKER_DIR))


# ── Helpers ────────────────────────────────────────────────────────────────────

def _mongo_id_to_qdrant_uuid(mongo_id: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_OID, mongo_id))


def _to_rgb(image: Image.Image) -> Image.Image:
    if image.mode == "RGBA":
        bg = Image.new("RGB", image.size, (255, 255, 255))
        bg.paste(image, mask=image.split()[3])
        return bg
    if image.mode != "RGB":
        return image.convert("RGB")
    return image


# ── EVA02 extractor (encoder only) ────────────────────────────────────────────

class EVA02Extractor:
    def __init__(self, device: str):
        import timm
        from pixai_tagger import TaggingHead

        self.device = device
        PIXAI_MODEL_DIR.mkdir(parents=True, exist_ok=True)

        for fname in PIXAI_MODEL_FILES:
            local = PIXAI_MODEL_DIR / fname
            if not local.exists():
                from huggingface_hub import hf_hub_download
                log.info("[EVA02] Downloading %s ...", fname)
                hf_hub_download(
                    repo_id=PIXAI_MODEL_REPO,
                    filename=fname,
                    local_dir=str(PIXAI_MODEL_DIR),
                )

        log.info("[EVA02] Loading encoder ...")
        weights_file = PIXAI_MODEL_DIR / "model_v0.9.pth"
        encoder = timm.create_model(
            "hf_hub:SmilingWolf/wd-eva02-large-tagger-v3",
            pretrained=False,
        )
        encoder.reset_classifier(0)

        # Load the full model weights then discard the decoder
        decoder = TaggingHead(EVA02_DIM, 13461)
        full_model = nn.Sequential(encoder, decoder)
        states = torch.load(str(weights_file), map_location="cpu", weights_only=True)
        full_model.load_state_dict(states)
        full_model.eval()

        self.encoder = full_model[0].to(device)
        log.info("[EVA02] Ready on %s", device)

        self.transform = transforms.Compose([
            transforms.Resize((PIXAI_IMAGE_SIZE, PIXAI_IMAGE_SIZE)),
            transforms.ToTensor(),
            transforms.Normalize(mean=[0.5, 0.5, 0.5], std=[0.5, 0.5, 0.5]),
        ])

    @torch.inference_mode()
    def extract_batch(self, images: list[Image.Image]) -> np.ndarray:
        """Returns float32 array of shape (B, 1024)."""
        tensors = torch.stack([self.transform(_to_rgb(img)) for img in images])
        tensors = tensors.to(self.device)
        emb = self.encoder(tensors)
        return emb.cpu().numpy()


# ── Qdrant helpers ─────────────────────────────────────────────────────────────

def get_qdrant_client():
    from qdrant_client import QdrantClient
    from qdrant_client.models import Distance, VectorParams, PayloadSchemaType

    client = QdrantClient(host=QDRANT_HOST, port=QDRANT_PORT, timeout=10)

    if not client.collection_exists(QDRANT_COLLECTION):
        log.info("[Qdrant] Creating collection '%s' ...", QDRANT_COLLECTION)
        client.create_collection(
            collection_name=QDRANT_COLLECTION,
            vectors_config=VectorParams(size=EVA02_DIM, distance=Distance.COSINE),
        )
        client.create_payload_index(QDRANT_COLLECTION, "date",   PayloadSchemaType.KEYWORD)
        client.create_payload_index(QDRANT_COLLECTION, "type",   PayloadSchemaType.KEYWORD)
        client.create_payload_index(QDRANT_COLLECTION, "status", PayloadSchemaType.KEYWORD)
        log.info("[Qdrant] Collection ready.")

    return client


def qdrant_existing_ids(client, batch_ids: list[str]) -> set[str]:
    """Return the subset of UUIDs that already exist in Qdrant."""
    uuids = [_mongo_id_to_qdrant_uuid(mid) for mid in batch_ids]
    results = client.retrieve(
        collection_name=QDRANT_COLLECTION,
        ids=uuids,
        with_payload=False,
        with_vectors=False,
    )
    found_uuids = {str(r.id) for r in results}
    return {
        mid for mid, uid in zip(batch_ids, uuids) if uid in found_uuids
    }


# ── Main ───────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--batch-size", type=int, default=64)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument(
        "--skip-existing", dest="skip_existing",
        action="store_true", default=True,
        help="Skip images already present in Qdrant (default: True)",
    )
    parser.add_argument(
        "--no-skip-existing", dest="skip_existing",
        action="store_false",
        help="Re-upsert all images even if already in Qdrant",
    )
    parser.add_argument(
        "--status", type=str, default="inferred",
        help="Comma-separated list of MongoDB status values to include (default: inferred)",
    )
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    statuses = [s.strip() for s in args.status.split(",")]
    device = "cuda" if torch.cuda.is_available() else "cpu"
    log.info("Device: %s", device)

    # MongoDB
    client_mongo = MongoClient(MONGODB_URI)
    col = client_mongo[MONGODB_DB]["images"]

    mongo_filter: dict = {
        "status": {"$in": statuses},
        "localPath": {"$exists": True},
    }
    total_matching = col.count_documents(mongo_filter)
    log.info(
        "Images matching filter (status in %s, has localPath): %d",
        statuses, total_matching,
    )

    all_docs = list(col.find(mongo_filter, {"_id": 1, "localPath": 1, "date": 1, "type": 1, "status": 1}))

    # Filter to files that exist on disk
    processable = [
        doc for doc in all_docs
        if doc.get("localPath") and Path(doc["localPath"]).exists()
    ]
    log.info(
        "Files on disk: %d  (skipped %d missing)",
        len(processable), len(all_docs) - len(processable),
    )

    if args.limit:
        processable = processable[:args.limit]
        log.info("Limiting to %d images.", args.limit)

    if args.dry_run:
        log.info("[dry-run] Would process %d images. Exiting.", len(processable))
        return

    # Qdrant
    qdrant = get_qdrant_client()

    # EVA02 extractor
    extractor = EVA02Extractor(device)

    n_total   = len(processable)
    n_upserted = 0
    n_skipped  = 0
    n_error    = 0

    for batch_start in range(0, n_total, args.batch_size):
        batch_docs = processable[batch_start:batch_start + args.batch_size]
        batch_end  = min(batch_start + args.batch_size, n_total)
        log.info("Batch [%d–%d / %d]", batch_start + 1, batch_end, n_total)

        # Skip images already in Qdrant
        if args.skip_existing:
            batch_mongo_ids = [str(d["_id"]) for d in batch_docs]
            already_in_qdrant = qdrant_existing_ids(qdrant, batch_mongo_ids)
            if already_in_qdrant:
                log.info("  Skipping %d already-indexed images.", len(already_in_qdrant))
                n_skipped += len(already_in_qdrant)
            batch_docs = [d for d in batch_docs if str(d["_id"]) not in already_in_qdrant]
            if not batch_docs:
                continue

        # Load images
        batch_imgs:  list[Image.Image] = []
        loaded_docs: list[dict]        = []
        for doc in batch_docs:
            try:
                img = Image.open(doc["localPath"])
                img.load()
                batch_imgs.append(img)
                loaded_docs.append(doc)
            except Exception as exc:
                log.error("Cannot open %s: %s", doc["localPath"], exc)
                n_error += 1

        if not batch_imgs:
            continue

        # Extract EVA02 embeddings
        try:
            embeddings = extractor.extract_batch(batch_imgs)   # (B, 1024)
        except Exception as exc:
            log.error("EVA02 extraction failed: %s", exc)
            n_error += len(loaded_docs)
            continue
        finally:
            batch_imgs.clear()

        # Upsert to Qdrant
        try:
            from qdrant_client.models import PointStruct
            points = [
                PointStruct(
                    id=_mongo_id_to_qdrant_uuid(str(doc["_id"])),
                    vector=emb.tolist(),
                    payload={
                        "image_id": str(doc["_id"]),
                        "date":     doc.get("date", ""),
                        "type":     doc.get("type", ""),
                        "status":   doc.get("status", ""),
                    },
                )
                for doc, emb in zip(loaded_docs, embeddings)
            ]
            qdrant.upsert(collection_name=QDRANT_COLLECTION, points=points)
            n_upserted += len(points)
            log.info("  Upserted %d  (total=%d  skipped=%d  error=%d)",
                     len(points), n_upserted, n_skipped, n_error)
        except Exception as exc:
            log.error("Qdrant upsert failed: %s", exc)
            n_error += len(loaded_docs)

    log.info("Done. upserted=%d  skipped=%d  error=%d", n_upserted, n_skipped, n_error)


if __name__ == "__main__":
    main()
