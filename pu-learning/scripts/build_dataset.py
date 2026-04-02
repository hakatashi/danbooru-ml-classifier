#!/usr/bin/env python3
"""
Build the final dataset by assigning train/val/test splits and verifying
that every image in the manifest has features extracted.

Outputs
-------
data/metadata/manifest.parquet  (updated with a 'split' column)
data/metadata/splits.parquet    (image_id, label, split, file_path)

The split is stratified by label so each label is represented in every
partition at the configured ratios:
  Positive (pixiv_public / pixiv_private / twitter) : 70 / 15 / 15 %
  Unlabeled                                         : 90 /  5 /  5 %

Usage:
  python build_dataset.py [--check-features]
"""

import argparse
import logging
import sys
from pathlib import Path

import h5py
import numpy as np
import pandas as pd
from sklearn.model_selection import train_test_split

SCRIPTS_DIR = Path(__file__).parent
sys.path.insert(0, str(SCRIPTS_DIR))

from config import (
    DATA_DIR, FEATURES_DIR, METADATA_DIR,
    POSITIVE_LABELS, UNLABELED_LABEL,
    POSITIVE_SPLIT, UNLABELED_SPLIT,
    RANDOM_SEED,
    DEEPDANBOORU_DIM, EVA02_DIM, PIXAI_DIM,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)


# ── Feature coverage check ────────────────────────────────────────────────────

def check_feature_coverage(manifest: pd.DataFrame) -> dict[str, set[str]]:
    """
    For each feature type, return the set of image_ids that are missing.
    """
    feature_files = {
        "deepdanbooru": FEATURES_DIR / "deepdanbooru.h5",
        "eva02":        FEATURES_DIR / "eva02.h5",
        "pixai":        FEATURES_DIR / "pixai.h5",
    }
    all_ids = set(manifest["image_id"])
    missing: dict[str, set[str]] = {}

    for name, path in feature_files.items():
        if not path.exists():
            log.warning("[%s] HDF5 file not found: %s", name, path)
            missing[name] = all_ids
            continue
        with h5py.File(path, "r") as f:
            if "image_ids" not in f:
                missing[name] = all_ids
                continue
            stored = set(f["image_ids"].asstr()[:])
        absent = all_ids - stored
        missing[name] = absent
        if absent:
            log.warning("[%s] Missing features for %d / %d images",
                        name, len(absent), len(all_ids))
        else:
            log.info("[%s] All %d images have features ✓", name, len(all_ids))

    return missing


# ── Split assignment ──────────────────────────────────────────────────────────

def assign_splits(manifest: pd.DataFrame) -> pd.DataFrame:
    """
    Assign a 'split' column (train / val / test) to every row.

    Positive images are split 70/15/15.
    Unlabeled images are split 90/5/5.
    Splitting is stratified within each label class for balance.
    """
    rng = np.random.default_rng(RANDOM_SEED)
    split_col = pd.Series("train", index=manifest.index, name="split")

    def _split_group(df_group: pd.DataFrame, ratios: dict[str, float]) -> pd.Series:
        idx   = df_group.index.to_numpy()
        rng.shuffle(idx)
        n     = len(idx)
        n_val  = max(1, round(n * ratios["val"]))
        n_test = max(1, round(n * ratios["test"]))

        result = pd.Series("train", index=idx)
        result.iloc[: n_val]             = "val"
        result.iloc[n_val: n_val + n_test] = "test"
        return result

    # Process each label separately so every label appears in all splits
    for label, group_df in manifest.groupby("label"):
        if label in POSITIVE_LABELS:
            ratios = POSITIVE_SPLIT
        else:
            ratios = UNLABELED_SPLIT

        label_splits = _split_group(group_df, ratios)
        split_col.update(label_splits)

    return manifest.assign(split=split_col)


# ── Summary statistics ────────────────────────────────────────────────────────

def print_summary(df: pd.DataFrame) -> None:
    log.info("=" * 60)
    log.info("Dataset summary")
    log.info("=" * 60)

    pivot = df.groupby(["label", "split"]).size().unstack(fill_value=0)
    pivot["total"] = pivot.sum(axis=1)
    log.info("\n%s", pivot.to_string())

    log.info("-" * 60)
    log.info("Total: %d  (positive: %d, unlabeled: %d)",
             len(df),
             (df["label"] != UNLABELED_LABEL).sum(),
             (df["label"] == UNLABELED_LABEL).sum())

    for split in ("train", "val", "test"):
        sub = df[df["split"] == split]
        log.info(
            "  %-5s : %6d  (pos=%d, unl=%d)",
            split, len(sub),
            (sub["label"] != UNLABELED_LABEL).sum(),
            (sub["label"] == UNLABELED_LABEL).sum(),
        )
    log.info("=" * 60)


# ── Entry point ───────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check-features", action="store_true",
        help="Report images that are missing extracted features and exit",
    )
    parser.add_argument(
        "--allow-missing-features", action="store_true",
        help="Build the dataset even if some features are missing",
    )
    args = parser.parse_args()

    manifest_path = METADATA_DIR / "manifest.parquet"
    if not manifest_path.exists():
        log.error("manifest.parquet not found. Run extract_features.py first.")
        sys.exit(1)

    manifest = pd.read_parquet(manifest_path)
    log.info("Loaded manifest: %d images", len(manifest))

    # ── Feature coverage ──────────────────────────────────────────────────────
    missing = check_feature_coverage(manifest)
    total_missing = sum(len(v) for v in missing.values())

    if args.check_features:
        if total_missing:
            log.warning(
                "Run extract_features.py to fill %d missing feature entries.",
                total_missing,
            )
            sys.exit(1)
        else:
            log.info("All features present.")
            sys.exit(0)

    if total_missing and not args.allow_missing_features:
        log.warning(
            "%d feature entries are missing. "
            "Run extract_features.py first, or pass --allow-missing-features.",
            total_missing,
        )
        # Continue anyway but restrict manifest to images that have ALL features
        have_all = (
            set(manifest["image_id"])
            - missing.get("deepdanbooru", set())
            - missing.get("eva02", set())
            - missing.get("pixai", set())
        )
        before = len(manifest)
        manifest = manifest[manifest["image_id"].isin(have_all)].reset_index(drop=True)
        log.info(
            "Restricted manifest to %d images with complete features (dropped %d)",
            len(manifest), before - len(manifest),
        )

    # ── Assign splits ─────────────────────────────────────────────────────────
    manifest = assign_splits(manifest)
    print_summary(manifest)

    # ── Save ──────────────────────────────────────────────────────────────────
    METADATA_DIR.mkdir(parents=True, exist_ok=True)

    # Overwrite manifest with split column
    manifest.to_parquet(manifest_path, index=False)
    log.info("Updated manifest → %s", manifest_path)

    # Also save a standalone splits file for quick loading
    splits_path = METADATA_DIR / "splits.parquet"
    manifest[["image_id", "label", "split", "file_path"]].to_parquet(
        splits_path, index=False
    )
    log.info("Saved splits → %s", splits_path)


if __name__ == "__main__":
    main()
