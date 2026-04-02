#!/usr/bin/env python3
"""
Feature extraction for PU Learning dataset.

Extracts three types of features from every image in the dataset:

  1. deepdanbooru  – 6000-dim tag probability vector (ResNet50)
  2. eva02         – 1024-dim visual embedding (EVA02-Large encoder)
  3. pixai         – 13461-dim tag probability vector (PixAI Tagger v0.9)

EVA02 embeddings and PixAI tags share a single forward pass, so choosing
"--features eva02" or "--features pixai" will always produce both outputs.

Results are stored in HDF5 files under data/features/:
  deepdanbooru.h5 – datasets: image_ids (N,), features (N, 6000) float16
  eva02.h5        – datasets: image_ids (N,), features (N, 1024) float16
  pixai.h5        – datasets: image_ids (N,), features (N, 13461) float16

The script is fully resumable: on re-run it skips image IDs that are
already stored in the target HDF5 file.

Usage:
  python extract_features.py [--features {deepdanbooru,eva02_pixai,all}]
                             [--batch-size 64]
"""

import argparse
import json
import logging
import subprocess
import sys
from pathlib import Path
from typing import Optional

import h5py
import numpy as np
import pandas as pd
import torch
import torch.nn as nn
from PIL import Image, UnidentifiedImageError
from torchvision import models, transforms
from tqdm import tqdm

# ── Bootstrap paths ───────────────────────────────────────────────────────────
SCRIPTS_DIR = Path(__file__).parent
sys.path.insert(0, str(SCRIPTS_DIR))
sys.path.insert(0, str(SCRIPTS_DIR.parent.parent / "worker"))

from config import (
    DATA_DIR, FEATURES_DIR, METADATA_DIR,
    HAKATAARCHIVE_PIXIV_DIR, HAKATAARCHIVE_TWITTER_DIR, DMC_IMAGES_DIR,
    PIXIV_INDEX_S3_URI,
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


# ── HDF5 helper ───────────────────────────────────────────────────────────────

class H5FeatureStore:
    """
    Append-friendly HDF5 writer/reader for (image_id, feature_vector) pairs.

    The file contains two resizable datasets:
      image_ids : variable-length UTF-8 strings
      features  : float16 matrix, shape (N, dim)
    """

    def __init__(self, path: Path, dim: int):
        self.path = path
        self.dim  = dim
        path.parent.mkdir(parents=True, exist_ok=True)

    def existing_ids(self) -> set[str]:
        """Return the set of image_ids already stored."""
        if not self.path.exists():
            return set()
        with h5py.File(self.path, "r") as f:
            if "image_ids" not in f:
                return set()
            return set(f["image_ids"].asstr()[:])

    def append(self, image_ids: list[str], features: np.ndarray) -> None:
        """Append a batch of (image_id, feature) pairs."""
        assert len(image_ids) == len(features), "Length mismatch"
        if len(image_ids) == 0:
            return
        features = features.astype(np.float16)

        with h5py.File(self.path, "a") as f:
            # Create datasets on first write
            if "features" not in f:
                f.create_dataset(
                    "features",
                    shape=(0, self.dim),
                    maxshape=(None, self.dim),
                    dtype="float16",
                    chunks=(min(256, len(image_ids)), self.dim),
                    compression="gzip",
                    compression_opts=4,
                )
                str_dt = h5py.special_dtype(vlen=str)
                f.create_dataset(
                    "image_ids",
                    shape=(0,),
                    maxshape=(None,),
                    dtype=str_dt,
                )

            n_old = f["features"].shape[0]
            n_new = len(image_ids)
            f["features"].resize(n_old + n_new, axis=0)
            f["image_ids"].resize(n_old + n_new, axis=0)
            f["features"][n_old:] = features
            f["image_ids"][n_old:] = image_ids


# ── Manifest collection ───────────────────────────────────────────────────────

def _is_image(path: Path) -> bool:
    """True for files with a recognised image extension OR no extension."""
    return path.suffix.lower() in IMAGE_EXTENSIONS or path.suffix == ""


def collect_manifest() -> pd.DataFrame:
    """
    Walk all source directories and build a DataFrame with columns:
      image_id   – unique string key used across all HDF5 stores
      label      – pixiv_public | pixiv_private | twitter | unlabeled
      file_path  – absolute path to the image file
    """
    records: list[dict] = []

    # ── Load pixiv bookmark index from S3 ────────────────────────────────────
    log.info("Fetching pixiv index from S3 …")
    result = subprocess.run(
        ["aws", "s3", "cp", PIXIV_INDEX_S3_URI, "-"],
        capture_output=True, check=True,
    )
    pixiv_index = json.loads(result.stdout)
    public_ids  = set(pixiv_index["public"])
    private_ids = set(pixiv_index["private"])
    positive_artwork_ids = public_ids | private_ids
    log.info(
        "  pixiv_public=%d  pixiv_private=%d  total artworks=%d",
        len(public_ids), len(private_ids), len(positive_artwork_ids),
    )

    # ── Positive: pixiv bookmarks ─────────────────────────────────────────────
    log.info("Scanning %s …", HAKATAARCHIVE_PIXIV_DIR)
    for path in sorted(HAKATAARCHIVE_PIXIV_DIR.iterdir()):
        if not path.is_file() or not _is_image(path):
            continue
        artwork_id = path.stem.split("_p")[0]
        if artwork_id in private_ids:
            label = "pixiv_private"
        elif artwork_id in public_ids:
            label = "pixiv_public"
        else:
            continue
        records.append({
            "image_id":  f"pixiv/{path.stem}",
            "label":     label,
            "file_path": str(path),
        })
    log.info("  found %d positive pixiv images", sum(1 for r in records if "pixiv" in r["label"]))

    # ── Positive: twitter ─────────────────────────────────────────────────────
    log.info("Scanning %s …", HAKATAARCHIVE_TWITTER_DIR)
    twitter_start = len(records)
    for path in sorted(HAKATAARCHIVE_TWITTER_DIR.iterdir()):
        if not path.is_file() or not _is_image(path):
            continue
        # Extension-less files (tweet IDs) use the full filename as stem
        stem = path.stem if path.suffix else path.name
        records.append({
            "image_id":  f"twitter/{stem}",
            "label":     "twitter",
            "file_path": str(path),
        })
    log.info("  found %d positive twitter images", len(records) - twitter_start)

    # ── Unlabeled: danbooru-ml-classifier cache ───────────────────────────────
    log.info("Scanning %s …", DMC_IMAGES_DIR)
    unlabeled_start = len(records)
    for subdir_name in ("danbooru", "gelbooru", "pixiv"):
        subdir = DMC_IMAGES_DIR / subdir_name
        if not subdir.exists():
            continue
        for path in sorted(subdir.iterdir()):
            if not path.is_file() or path.suffix.lower() not in IMAGE_EXTENSIONS:
                continue
            # For dmc/pixiv: skip artworks already in the positive set
            if subdir_name == "pixiv":
                artwork_id = path.stem.split("_p")[0]
                if artwork_id in positive_artwork_ids:
                    continue
            records.append({
                "image_id":  f"dmc_{subdir_name}/{path.stem}",
                "label":     "unlabeled",
                "file_path": str(path),
            })
    log.info("  found %d unlabeled images", len(records) - unlabeled_start)

    df = pd.DataFrame(records)
    # Deduplicate: if the same image_id appears twice keep the first (positive wins)
    df = df.drop_duplicates(subset="image_id", keep="first").reset_index(drop=True)
    log.info(
        "Manifest total: %d images  (%d positive, %d unlabeled)",
        len(df),
        (df["label"] != "unlabeled").sum(),
        (df["label"] == "unlabeled").sum(),
    )
    return df


# ── DeepDanbooru feature extractor ───────────────────────────────────────────

class DeepDanbooruExtractor:
    """Extracts 6000-dim tag probability vectors via DeepDanbooru ResNet50."""

    NORMALIZE_MEAN = [0.7137, 0.6628, 0.6519]
    NORMALIZE_STD  = [0.2970, 0.3017, 0.2979]

    def __init__(self, device: str):
        from danbooru_resnet import _resnet

        self.device = device
        log.info("[DeepDanbooru] Loading model …")
        model = _resnet(models.resnet50, 6000)
        state = torch.hub.load_state_dict_from_url(
            "https://github.com/RF5/danbooru-pretrained/releases/download"
            "/v0.1/resnet50-13306192.pth",
            map_location="cpu",
            progress=True,
        )
        model.load_state_dict(state)
        model.eval()
        self.model = model.to(device)
        log.info("[DeepDanbooru] Model ready on %s", device)

        self.transform = transforms.Compose([
            transforms.Resize(DEEPDANBOORU_IMAGE_SIZE),
            transforms.CenterCrop(DEEPDANBOORU_IMAGE_SIZE),
            transforms.ToTensor(),
            transforms.Normalize(self.NORMALIZE_MEAN, self.NORMALIZE_STD),
        ])

    @torch.inference_mode()
    def extract_batch(self, images: list[Image.Image]) -> np.ndarray:
        """Returns float32 array of shape (B, 6000)."""
        tensors = torch.stack([self.transform(img) for img in images])
        tensors = tensors.to(self.device)
        probs = torch.sigmoid(self.model(tensors))
        return probs.cpu().numpy()


# ── PixAI / EVA02 feature extractor ──────────────────────────────────────────

class PixAIExtractor:
    """
    Wraps PixAI Tagger v0.9 to extract two feature types in one forward pass:
      • EVA02 embedding : 1024-dim output of the EVA02-Large encoder
      • PixAI tags      : 13461-dim sigmoid probabilities from the tagging head
    """

    def __init__(self, device: str):
        import timm
        from pixai_tagger import TaggingHead, load_model

        self.device = device

        # Ensure model files are present
        self._ensure_model_files()

        log.info("[PixAI] Loading model …")
        weights_file = PIXAI_MODEL_DIR / "model_v0.9.pth"

        # Build encoder (reset_classifier removes the timm head → 1024-dim output)
        encoder = timm.create_model(
            "hf_hub:SmilingWolf/wd-eva02-large-tagger-v3",
            pretrained=False,
        )
        encoder.reset_classifier(0)

        decoder = TaggingHead(EVA02_DIM, PIXAI_DIM)
        full_model = nn.Sequential(encoder, decoder)

        states = torch.load(str(weights_file), map_location="cpu", weights_only=True)
        full_model.load_state_dict(states)
        full_model.eval()
        self.full_model = full_model.to(device)
        self.encoder    = full_model[0]
        self.decoder    = full_model[1]

        log.info("[PixAI] Model ready on %s", device)

        self.transform = transforms.Compose([
            transforms.Resize((PIXAI_IMAGE_SIZE, PIXAI_IMAGE_SIZE)),
            transforms.ToTensor(),
            transforms.Normalize(mean=[0.5, 0.5, 0.5], std=[0.5, 0.5, 0.5]),
        ])

    def _ensure_model_files(self) -> None:
        from huggingface_hub import hf_hub_download
        PIXAI_MODEL_DIR.mkdir(parents=True, exist_ok=True)
        for fname in PIXAI_MODEL_FILES:
            local = PIXAI_MODEL_DIR / fname
            if not local.exists():
                log.info("[PixAI] Downloading %s …", fname)
                hf_hub_download(
                    repo_id=PIXAI_MODEL_REPO,
                    filename=fname,
                    local_dir=str(PIXAI_MODEL_DIR),
                )

    @torch.inference_mode()
    def extract_batch(
        self, images: list[Image.Image]
    ) -> tuple[np.ndarray, np.ndarray]:
        """
        Returns:
          eva02_emb  : float32 array (B, 1024)
          pixai_tags : float32 array (B, 13461)
        """
        tensors = torch.stack([self.transform(_to_rgb(img)) for img in images])
        tensors = tensors.to(self.device)

        embeddings = self.encoder(tensors)          # (B, 1024)
        tag_probs  = self.decoder(embeddings)       # (B, 13461)

        return embeddings.cpu().numpy(), tag_probs.cpu().numpy()


# ── Image loading helpers ─────────────────────────────────────────────────────

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
        img.load()          # Force decode to catch truncated files early
        return _to_rgb(img)
    except (UnidentifiedImageError, OSError, Exception) as exc:
        log.warning("  skip %s : %s", path, exc)
        return None


# ── Main extraction loop ──────────────────────────────────────────────────────

def run_extraction(
    manifest: pd.DataFrame,
    feature_set: str,
    batch_size: int,
    device: str,
) -> None:
    """
    Extract features for all images in *manifest* that are not yet stored.

    feature_set : "deepdanbooru" | "eva02_pixai" | "all"
    """
    do_dd      = feature_set in ("deepdanbooru", "all")
    do_ep      = feature_set in ("eva02_pixai",  "all")

    # Stores
    store_dd   = H5FeatureStore(FEATURES_DIR / "deepdanbooru.h5", DEEPDANBOORU_DIM)
    store_eva  = H5FeatureStore(FEATURES_DIR / "eva02.h5",        EVA02_DIM)
    store_pai  = H5FeatureStore(FEATURES_DIR / "pixai.h5",        PIXAI_DIM)

    # Determine which IDs still need processing
    remaining = manifest.copy()
    if do_dd:
        done = store_dd.existing_ids()
        remaining_dd = remaining[~remaining["image_id"].isin(done)]
        log.info("[DeepDanbooru] %d already done, %d remaining",
                 len(done), len(remaining_dd))
    if do_ep:
        done_eva = store_eva.existing_ids()
        done_pai = store_pai.existing_ids()
        done_ep  = done_eva & done_pai
        remaining_ep = remaining[~remaining["image_id"].isin(done_ep)]
        log.info("[EVA02/PixAI] %d already done, %d remaining",
                 len(done_ep), len(remaining_ep))

    # ── DeepDanbooru pass ─────────────────────────────────────────────────────
    if do_dd and len(remaining_dd) > 0:
        extractor = DeepDanbooruExtractor(device)
        _extract_loop(
            extractor.extract_batch,
            remaining_dd,
            batch_size,
            stores={"deepdanbooru": store_dd},
            output_keys=["deepdanbooru"],
        )
        del extractor
        _clear_gpu_cache(device)

    # ── EVA02 + PixAI pass ────────────────────────────────────────────────────
    if do_ep and len(remaining_ep) > 0:
        extractor = PixAIExtractor(device)

        def _ep_extract(images):
            eva, pai = extractor.extract_batch(images)
            return {"eva02": eva, "pixai": pai}

        _extract_loop(
            _ep_extract,
            remaining_ep,
            batch_size,
            stores={"eva02": store_eva, "pixai": store_pai},
            output_keys=["eva02", "pixai"],
        )
        del extractor
        _clear_gpu_cache(device)


def _extract_loop(
    extract_fn,
    manifest: pd.DataFrame,
    batch_size: int,
    stores: dict,
    output_keys: list[str],
) -> None:
    """
    Generic batched extraction loop.

    extract_fn must accept a list[Image] and return either:
      - a np.ndarray                           (single-output)
      - a dict[str, np.ndarray]                (multi-output)
    """
    rows       = manifest.to_dict("records")
    error_log  = METADATA_DIR / "extraction_errors.txt"
    METADATA_DIR.mkdir(parents=True, exist_ok=True)

    batch_ids:   list[str]   = []
    batch_imgs:  list        = []

    def _flush():
        if not batch_ids:
            return
        try:
            result = extract_fn(batch_imgs)
        except Exception as exc:
            log.error("Batch extraction failed: %s", exc)
            with open(error_log, "a") as fh:
                for bid in batch_ids:
                    fh.write(f"batch_error\t{bid}\t{exc}\n")
            batch_ids.clear()
            batch_imgs.clear()
            return

        if isinstance(result, np.ndarray):
            result = {output_keys[0]: result}

        for key in output_keys:
            stores[key].append(batch_ids, result[key])

        batch_ids.clear()
        batch_imgs.clear()

    with tqdm(total=len(rows), unit="img") as pbar:
        for row in rows:
            img = _load_image(row["file_path"])
            if img is None:
                with open(error_log, "a") as fh:
                    fh.write(f"load_error\t{row['image_id']}\t{row['file_path']}\n")
                pbar.update(1)
                continue

            batch_ids.append(row["image_id"])
            batch_imgs.append(img)

            if len(batch_ids) >= batch_size:
                _flush()

            pbar.update(1)

    _flush()   # Remaining partial batch


def _clear_gpu_cache(device: str) -> None:
    if "cuda" in device:
        torch.cuda.empty_cache()


# ── Entry point ───────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--features",
        choices=["deepdanbooru", "eva02_pixai", "all"],
        default="all",
        help="Which feature types to extract (default: all)",
    )
    parser.add_argument(
        "--batch-size", type=int, default=DEFAULT_BATCH_SIZE,
        help=f"GPU batch size (default: {DEFAULT_BATCH_SIZE})",
    )
    parser.add_argument(
        "--device", default=None,
        help="PyTorch device string (default: auto-detect)",
    )
    parser.add_argument(
        "--rebuild-manifest", action="store_true",
        help="Re-collect the manifest even if manifest.parquet already exists",
    )
    args = parser.parse_args()

    device = args.device or ("cuda" if torch.cuda.is_available() else "cpu")
    log.info("Using device: %s", device)

    # ── Manifest ──────────────────────────────────────────────────────────────
    manifest_path = METADATA_DIR / "manifest.parquet"
    METADATA_DIR.mkdir(parents=True, exist_ok=True)
    FEATURES_DIR.mkdir(parents=True, exist_ok=True)

    if manifest_path.exists() and not args.rebuild_manifest:
        log.info("Loading existing manifest from %s", manifest_path)
        manifest = pd.read_parquet(manifest_path)
    else:
        manifest = collect_manifest()
        manifest.to_parquet(manifest_path, index=False)
        log.info("Saved manifest → %s  (%d rows)", manifest_path, len(manifest))

    # ── Feature extraction ────────────────────────────────────────────────────
    run_extraction(manifest, args.features, args.batch_size, device)

    log.info("Feature extraction complete.")


if __name__ == "__main__":
    main()
