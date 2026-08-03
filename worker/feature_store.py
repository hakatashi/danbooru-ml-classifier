"""
Monthly-sharded HDF5 feature store for eva02 / deepdanbooru / pixai vectors.

Reuses the H5FeatureStore layout from pu-learning/scripts/extract_features.py
(image_ids as variable-length UTF-8 strings + features as a float16 matrix,
gzip-4 compressed, resizable, chunked) so pu-learning's build_dataset.py /
train_pu.py can read these shards without modification once merged into a
training manifest.

Storage layout: FEATURES_DIR/{feature}/{YYYY-MM}.h5
Keys are MongoDB `_id` hex strings -- NOT the `dmc_<source>/<stem>` ids used
by pu-learning's training manifest. The `images.features` pointer written
by main.py (`{stored: true, shard: "YYYY-MM"}`) is what lets a future join
step find a doc's row without scanning every shard.

Written at inference time in main.py, right where eva02/deepdanbooru/pixai
feature matrices already coexist in memory for the Qdrant upsert -- so this
adds disk I/O but zero additional GPU cost. See
pu-learning/reports/recommendation_improvement_plan.md section 3.2.
"""

import logging
import os
from pathlib import Path

import h5py
import numpy as np

log = logging.getLogger(__name__)

FEATURES_DIR = Path(os.environ.get(
    "FEATURE_STORE_DIR",
    "/mnt/cache2/danbooru-ml-classifier/features",
))

FEATURE_DIMS = {"deepdanbooru": 6000, "eva02": 1024, "pixai": 13461}


class H5FeatureStore:
    """
    Append-friendly HDF5 writer/reader for (image_id, feature_vector) pairs.

    Identical layout to pu-learning/scripts/extract_features.py's class of
    the same name (duplicated rather than imported -- worker/ and
    pu-learning/ are deliberately independent venvs/deployables).
    """

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
        assert len(image_ids) == len(features), "Length mismatch"
        if len(image_ids) == 0:
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


def _shard_path(feature: str, month: str) -> Path:
    return FEATURES_DIR / feature / f"{month}.h5"


def write_features(docs: list[dict], features: dict[str, np.ndarray]) -> dict:
    """
    Persist feature vectors for a batch of docs into the appropriate month
    shard for each requested feature type.

    `docs[i]` must correspond to `features[name][i]` for every `name` in
    `features` (same row order, same length as `docs`). Docs without a
    `date` field are skipped -- shards are keyed by month, and every
    `status='inferred'` doc has a `date` (see CLAUDE.md), so this only
    matters for defensive robustness against unexpected input.

    Idempotent: re-running with ids already present in a shard is a no-op
    for those ids (checked via existing_ids() per shard, same convention as
    backfill_qdrant.py's per-batch existence check).

    Returns:
        {
            "written": {feature: n_newly_written, ...},
            "complete": {doc_id: "YYYY-MM", ...},  # docs with ALL requested
                                                     # features now present
                                                     # (new or pre-existing)
        }
    """
    feature_names = list(features)
    written  = {name: 0 for name in feature_names}
    complete: dict = {}

    by_month: dict[str, list[int]] = {}
    for i, doc in enumerate(docs):
        date = doc.get("date")
        if not date:
            continue
        by_month.setdefault(date[:7], []).append(i)

    for month, indices in by_month.items():
        ids = [str(docs[i]["_id"]) for i in indices]
        present_per_feature: dict[str, set[str]] = {}

        for name in feature_names:
            store = H5FeatureStore(_shard_path(name, month), FEATURE_DIMS[name])
            existing = store.existing_ids()
            new_positions = [k for k, id_ in enumerate(ids) if id_ not in existing]
            if new_positions:
                store.append(
                    [ids[k] for k in new_positions],
                    features[name][[indices[k] for k in new_positions]],
                )
                written[name] += len(new_positions)
            present_per_feature[name] = existing | {ids[k] for k in new_positions}

        for k, idx in enumerate(indices):
            if all(ids[k] in present_per_feature[name] for name in feature_names):
                complete[docs[idx]["_id"]] = month

    return {"written": written, "complete": complete}
