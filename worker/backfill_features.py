#!/usr/bin/env python3
"""
Backfill deepdanbooru/eva02/pixai feature vectors into the HDF5 feature
store (feature_store.py) for existing MongoDB images that don't have them
yet.

This machine runs a GPU-exclusive background job (see
../HakataMatrix-app-controller-claude) that must be stopped before running
this script -- it refuses to start if VRAM doesn't look free. Confirm with
the user before running it for real; use --dry-run first to see what would
be processed.

Usage:
    cd worker
    venv/bin/python backfill_features.py --ids-file /path/to/ids.txt [options]
    venv/bin/python backfill_features.py --date-from 2026-04-10 --date-to 2026-08-03 [options]

Options:
    --ids-file PATH     File with one MongoDB _id (hex string) per line.
                         Takes priority over --date-from/--date-to if given.
    --date-from DATE    Filter by date >= (YYYY-MM-DD)
    --date-to DATE      Filter by date <= (YYYY-MM-DD)
    --batch-size N      GPU batch size (default: 64)
    --limit N           Stop after processing N images (default: unlimited)
    --skip-existing     Skip images whose images.features.stored is already
                         true (default: True)
    --no-skip-existing  Re-extract and re-write even if already stored
    --status STATUS     MongoDB status filter, comma-separated (default: inferred)
    --min-free-vram-mb N  VRAM free threshold to proceed (default: 4000)
    --skip-vram-check   Skip the rocm-smi VRAM safety check (for non-ROCm hosts)
    --dry-run           Print what would be processed without doing it
"""

import argparse
import json
import logging
import os
import subprocess
import sys
from pathlib import Path

import numpy as np
import torch
import torch.nn as nn
from bson import ObjectId
from PIL import Image, UnidentifiedImageError
from pymongo import MongoClient, UpdateOne
from torchvision import models, transforms

WORKER_DIR = Path(__file__).parent
sys.path.insert(0, str(WORKER_DIR))

from feature_store import FEATURE_DIMS, write_features  # noqa: E402

MONGODB_URI = os.environ.get("MONGODB_URI", "mongodb://localhost:27017")
MONGODB_DB  = os.environ.get("MONGODB_DB", "danbooru-ml-classifier")

PIXAI_MODEL_DIR   = Path.home() / ".cache" / "pixai-tagger"
PIXAI_MODEL_REPO  = "pixai-labs/pixai-tagger-v0.9"
PIXAI_MODEL_FILES = ["model_v0.9.pth", "tags_v0.9_13k.json", "char_ip_map.json"]

DEEPDANBOORU_DIM        = FEATURE_DIMS["deepdanbooru"]
EVA02_DIM               = FEATURE_DIMS["eva02"]
PIXAI_DIM               = FEATURE_DIMS["pixai"]
DEEPDANBOORU_IMAGE_SIZE = 360
PIXAI_IMAGE_SIZE        = 448

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)


# ── GPU safety check ─────────────────────────────────────────────────────────
# This machine runs GPU-exclusive apps managed by
# ../HakataMatrix-app-controller-claude; running a heavy extraction job while
# one of those holds VRAM would OOM (or silently starve) both jobs.

def _check_gpu_free(min_free_mb: int) -> None:
    try:
        result = subprocess.run(
            ["rocm-smi", "--showmeminfo", "vram", "--json"],
            capture_output=True, text=True, timeout=10, check=True,
        )
        data = json.loads(result.stdout)
        card = next(iter(data.values()))
        total = int(card["VRAM Total Memory (B)"])
        used  = int(card["VRAM Total Used Memory (B)"])
        free_mb = (total - used) / (1024 * 1024)
    except Exception as exc:
        log.warning(
            "Could not query GPU VRAM via rocm-smi (%s); proceeding without the safety check.",
            exc,
        )
        return

    if free_mb < min_free_mb:
        log.error(
            "Only %.0f MB VRAM free (need >= %d MB free). A GPU-exclusive "
            "background job is likely still running -- stop it first (see "
            "HakataMatrix-app-controller-claude) and re-run this script.",
            free_mb, min_free_mb,
        )
        sys.exit(1)
    log.info("VRAM check OK: %.0f MB free.", free_mb)


# ── Image helpers (duplicated from main.py, same convention as backfill_qdrant.py) ──

def _to_rgb(image: Image.Image) -> Image.Image:
    if image.mode == "RGBA":
        bg = Image.new("RGB", image.size, (255, 255, 255))
        bg.paste(image, mask=image.split()[3])
        return bg
    if image.mode != "RGB":
        return image.convert("RGB")
    return image


class DeepDanbooruExtractor:
    NORMALIZE_MEAN = [0.7137, 0.6628, 0.6519]
    NORMALIZE_STD  = [0.2970, 0.3017, 0.2979]

    def __init__(self, device: str):
        from danbooru_resnet import _resnet

        self.device = device
        log.info("[DeepDanbooru] Loading model ...")
        model = _resnet(models.resnet50, DEEPDANBOORU_DIM)
        state = torch.hub.load_state_dict_from_url(
            "https://github.com/RF5/danbooru-pretrained/releases/download"
            "/v0.1/resnet50-13306192.pth",
            map_location="cpu",
            progress=True,
        )
        model.load_state_dict(state)
        model.eval()
        self.model = model.to(device)
        log.info("[DeepDanbooru] Ready on %s", device)

        self.transform = transforms.Compose([
            transforms.Resize(DEEPDANBOORU_IMAGE_SIZE),
            transforms.CenterCrop(DEEPDANBOORU_IMAGE_SIZE),
            transforms.ToTensor(),
            transforms.Normalize(self.NORMALIZE_MEAN, self.NORMALIZE_STD),
        ])

    @torch.inference_mode()
    def extract_batch(self, images: list[Image.Image]) -> np.ndarray:
        tensors = torch.stack([self.transform(_to_rgb(img)) for img in images])
        tensors = tensors.to(self.device)
        probs = torch.sigmoid(self.model(tensors))
        return probs.cpu().numpy()


class PixAIExtractor:
    def __init__(self, device: str):
        import timm
        from pixai_tagger import TaggingHead

        self.device = device
        PIXAI_MODEL_DIR.mkdir(parents=True, exist_ok=True)

        for fname in PIXAI_MODEL_FILES:
            local = PIXAI_MODEL_DIR / fname
            if not local.exists():
                from huggingface_hub import hf_hub_download
                log.info("[PixAI] Downloading %s ...", fname)
                hf_hub_download(repo_id=PIXAI_MODEL_REPO, filename=fname, local_dir=str(PIXAI_MODEL_DIR))

        log.info("[PixAI] Loading model ...")
        weights_file = PIXAI_MODEL_DIR / "model_v0.9.pth"
        encoder = timm.create_model("hf_hub:SmilingWolf/wd-eva02-large-tagger-v3", pretrained=False)
        encoder.reset_classifier(0)
        decoder = TaggingHead(EVA02_DIM, PIXAI_DIM)
        full_model = nn.Sequential(encoder, decoder)
        states = torch.load(str(weights_file), map_location="cpu", weights_only=True)
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
    def extract_batch(self, images: list[Image.Image]) -> tuple[np.ndarray, np.ndarray]:
        tensors = torch.stack([self.transform(_to_rgb(img)) for img in images])
        tensors = tensors.to(self.device)
        emb  = self.encoder(tensors)
        tags = self.decoder(emb)
        return emb.cpu().numpy(), tags.cpu().numpy()


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--ids-file", type=str, default=None)
    parser.add_argument("--date-from", type=str, default=None)
    parser.add_argument("--date-to", type=str, default=None)
    parser.add_argument("--batch-size", type=int, default=64)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--skip-existing", dest="skip_existing", action="store_true", default=True)
    parser.add_argument("--no-skip-existing", dest="skip_existing", action="store_false")
    parser.add_argument("--status", type=str, default="inferred")
    parser.add_argument("--min-free-vram-mb", type=int, default=4000)
    parser.add_argument("--skip-vram-check", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    statuses = [s.strip() for s in args.status.split(",")]

    mongo_filter: dict = {"status": {"$in": statuses}, "localPath": {"$exists": True}}
    if args.skip_existing:
        mongo_filter["features.stored"] = {"$ne": True}

    if args.ids_file:
        ids = [line.strip() for line in Path(args.ids_file).read_text().splitlines() if line.strip()]
        object_ids = []
        for id_str in ids:
            try:
                object_ids.append(ObjectId(id_str))
            except Exception:
                log.warning("Skipping malformed id in --ids-file: %r", id_str)
        mongo_filter["_id"] = {"$in": object_ids}
        log.info("Filtering to %d ids from %s", len(object_ids), args.ids_file)
    else:
        date_range: dict = {}
        if args.date_from:
            date_range["$gte"] = args.date_from
        if args.date_to:
            date_range["$lte"] = args.date_to
        if date_range:
            mongo_filter["date"] = date_range

    client = MongoClient(MONGODB_URI)
    col = client[MONGODB_DB]["images"]

    all_docs = list(col.find(mongo_filter, {"_id": 1, "localPath": 1, "date": 1}))
    processable = [doc for doc in all_docs if doc.get("localPath") and Path(doc["localPath"]).exists()]
    log.info(
        "Matching documents: %d  |  files on disk: %d  (skipped %d missing)",
        len(all_docs), len(processable), len(all_docs) - len(processable),
    )

    if args.limit:
        processable = processable[:args.limit]
        log.info("Limiting to %d images.", args.limit)

    if args.dry_run:
        log.info("[dry-run] Would process %d images. Exiting.", len(processable))
        return

    if not processable:
        log.info("Nothing to do.")
        return

    if not args.skip_vram_check:
        _check_gpu_free(args.min_free_vram_mb)

    device = "cuda" if torch.cuda.is_available() else "cpu"
    log.info("Device: %s", device)

    dd_extractor   = DeepDanbooruExtractor(device)
    pxai_extractor = PixAIExtractor(device)

    n_total = len(processable)
    n_written_total = {"deepdanbooru": 0, "eva02": 0, "pixai": 0}
    n_error = 0

    for batch_start in range(0, n_total, args.batch_size):
        batch_docs = processable[batch_start:batch_start + args.batch_size]
        batch_end  = min(batch_start + args.batch_size, n_total)
        log.info("Batch [%d-%d / %d] ...", batch_start + 1, batch_end, n_total)

        batch_imgs:  list[Image.Image] = []
        loaded_docs: list[dict]        = []
        for doc in batch_docs:
            try:
                img = Image.open(doc["localPath"])
                img.load()
                batch_imgs.append(img)
                loaded_docs.append(doc)
            except (UnidentifiedImageError, OSError, Exception) as exc:
                log.error("Cannot open %s: %s", doc["localPath"], exc)
                n_error += 1

        if not batch_imgs:
            continue

        try:
            X_dd          = dd_extractor.extract_batch(batch_imgs)
            X_eva, X_pxai = pxai_extractor.extract_batch(batch_imgs)
        except Exception as exc:
            log.error("Feature extraction failed: %s", exc)
            n_error += len(loaded_docs)
            continue
        finally:
            batch_imgs.clear()

        result = write_features(
            loaded_docs,
            {"deepdanbooru": X_dd, "eva02": X_eva, "pixai": X_pxai},
        )
        for name, n in result["written"].items():
            n_written_total[name] += n

        if result["complete"]:
            pointer_ops = [
                UpdateOne({"_id": doc_id}, {"$set": {"features": {"stored": True, "shard": month}}})
                for doc_id, month in result["complete"].items()
            ]
            col.bulk_write(pointer_ops, ordered=False)

        log.info("  Written this batch: %s  (total: %s  error=%d)", result["written"], n_written_total, n_error)

    log.info("Done. written=%s  error=%d", n_written_total, n_error)


if __name__ == "__main__":
    main()
