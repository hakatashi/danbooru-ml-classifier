#!/usr/bin/env python3
"""
Score test-split unlabeled images using trained multiclass classifiers and save montage images.

Handles two model types (all using 6000-dim DeepDanbooru tag_probs as input):

  sklearn-multiclass-*.joblib
    sklearn multiclass classifiers; scored via decision_function (shape N×3).

  torch-multiclass-onehot-shallow-network-multilayer
    PyTorch Network (6000→512→128→128→3); scored via raw logits (shape N×3).

Classes:
  0: not_bookmarked
  1: bookmarked_public
  2: bookmarked_private

Output per (model × class):
  data/results/top{N}_{model_name}_{class_name}_{split}.png

Usage:
  python score_unlabeled.py
  python score_unlabeled.py --top-k 20
  python score_unlabeled.py --split val
  python score_unlabeled.py --classes bookmarked_private bookmarked_public
"""

import argparse
import logging
import sys
from pathlib import Path

import h5py
import joblib
import numpy as np
import pandas as pd

SCRIPTS_DIR = Path(__file__).parent
sys.path.insert(0, str(SCRIPTS_DIR))
from config import (
    DATA_DIR, FEATURES_DIR, METADATA_DIR,
    UNLABELED_LABEL,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

MODELS_DIR  = DATA_DIR / "models"
RESULTS_DIR = DATA_DIR / "results"

CLASS_NAMES = ["not_bookmarked", "bookmarked_public", "bookmarked_private"]


# ── sklearn version compatibility ────────────────────────────────────────────

def _fix_sklearn_compat(clf) -> None:
    """
    Patch models pickled with sklearn <1.4 so they run under sklearn >=1.4.

    sklearn 1.4 added `monotonic_cst` to DecisionTreeClassifier.
    Models pickled with 1.3.x lack this attribute and raise AttributeError
    on prediction.
    """
    from sklearn.tree import DecisionTreeClassifier

    estimators = []
    if hasattr(clf, "estimators_"):
        estimators.extend(clf.estimators_)
    if hasattr(clf, "estimator"):
        estimators.append(clf.estimator)

    for est in estimators:
        if isinstance(est, DecisionTreeClassifier) and not hasattr(est, "monotonic_cst"):
            est.monotonic_cst = None


# ── Feature loading ───────────────────────────────────────────────────────────

class FeatureStore:
    """Random-access feature loader backed by an HDF5 file."""

    def __init__(self, feature_name: str):
        self.path = FEATURES_DIR / f"{feature_name}.h5"
        if not self.path.exists():
            raise FileNotFoundError(f"HDF5 not found: {self.path}")
        with h5py.File(self.path, "r") as f:
            ids = f["image_ids"].asstr()[:]
        self._id_to_row = {image_id: i for i, image_id in enumerate(ids)}
        log.info("  Loaded index for %s (%d entries)", feature_name, len(ids))

    def load_rows(self, image_ids: list[str]) -> np.ndarray:
        rows  = [self._id_to_row.get(iid) for iid in image_ids]
        miss  = sum(1 for r in rows if r is None)
        if miss:
            log.warning("  %d / %d image_ids not found in feature store", miss, len(image_ids))

        order     = sorted(range(len(rows)), key=lambda i: rows[i] if rows[i] is not None else -1)
        valid_pos = [i for i in order if rows[i] is not None]

        with h5py.File(self.path, "r") as f:
            hdf_rows = sorted(set(rows[i] for i in valid_pos))
            data = f["features"][hdf_rows, :].astype(np.float32)
            row_to_local = {r: j for j, r in enumerate(hdf_rows)}

        out = np.zeros((len(image_ids), data.shape[1]), dtype=np.float32)
        for list_i in valid_pos:
            out[list_i] = data[row_to_local[rows[list_i]]]
        return out


# ── Montage ───────────────────────────────────────────────────────────────────

def _save_montage(
    paths: list[str],
    scores: list[float],
    out_path: Path,
    thumb_size: int = 224,
) -> None:
    """Create a grid of thumbnails with score labels and save as PNG."""
    from PIL import Image, ImageDraw, ImageFont

    n    = len(paths)
    cols = min(5, n)
    rows = (n + cols - 1) // cols
    gap  = 4
    label_h = 18

    canvas_w = cols * (thumb_size + gap) + gap
    canvas_h = rows * (thumb_size + label_h + gap) + gap
    canvas   = Image.new("RGB", (canvas_w, canvas_h), (30, 30, 30))

    try:
        font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 13)
    except OSError:
        font = ImageFont.load_default()

    for i, (fpath, score) in enumerate(zip(paths, scores)):
        row, col = divmod(i, cols)
        x = gap + col * (thumb_size + gap)
        y = gap + row * (thumb_size + label_h + gap)

        try:
            img = Image.open(fpath).convert("RGB")
            img.thumbnail((thumb_size, thumb_size))
            bg = Image.new("RGB", (thumb_size, thumb_size), (50, 50, 50))
            off_x = (thumb_size - img.width)  // 2
            off_y = (thumb_size - img.height) // 2
            bg.paste(img, (off_x, off_y))
            canvas.paste(bg, (x, y))
        except Exception as exc:
            log.warning("  Could not load thumbnail %s: %s", fpath, exc)

        draw  = ImageDraw.Draw(canvas)
        label = f"#{i+1}  {score:.4f}"
        draw.rectangle([x, y + thumb_size, x + thumb_size, y + thumb_size + label_h], fill=(20, 20, 20))
        draw.text((x + 4, y + thumb_size + 2), label, fill=(220, 220, 100), font=font)

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    canvas.save(out_path)
    log.info("  Montage saved → %s", out_path)


# ── PyTorch model ────────────────────────────────────────────────────────────

TORCH_MODEL_PATH = MODELS_DIR / "torch-multiclass-onehot-shallow-network-multilayer"


def _load_torch_network(device: str):
    """
    Instantiate the shallow Network and load state_dict from TORCH_MODEL_PATH.

    Architecture matches worker/torch_network.py:
      Linear(6000→512) ReLU → Linear(512→128) ReLU → Linear(128→128) ReLU → Linear(128→3)
    """
    import torch
    import torch.nn as nn
    import torch.nn.functional as F

    class Network(nn.Module):
        def __init__(self):
            super().__init__()
            self.middle1_layer = nn.Linear(6000, 512)
            self.middle2_layer = nn.Linear(512, 128)
            self.middle3_layer = nn.Linear(128, 128)
            self.out_layer     = nn.Linear(128, 3)

        def forward(self, x):
            x = F.relu(self.middle1_layer(x))
            x = F.relu(self.middle2_layer(x))
            x = F.relu(self.middle3_layer(x))
            return self.out_layer(x)

    model = Network()
    state = torch.load(TORCH_MODEL_PATH, map_location=device)
    model.load_state_dict(state)
    model.to(device)
    model.eval()
    log.info("  Torch network loaded (device=%s)", device)
    return model


class TorchNetworkWrapper:
    """
    Wraps the PyTorch Network so it can be passed to score_and_save.

    decision_function returns raw logits (shape N×3) as a numpy array,
    matching the contract expected by score_and_save.
    """

    def __init__(self, model, device: str, batch_size: int = 2048):
        self.model      = model
        self.device     = device
        self.batch_size = batch_size

    def decision_function(self, X: np.ndarray) -> np.ndarray:
        import torch
        results = []
        with torch.no_grad():
            for i in range(0, len(X), self.batch_size):
                batch  = torch.from_numpy(X[i : i + self.batch_size]).float().to(self.device)
                logits = self.model(batch)
                results.append(logits.cpu().numpy())
        return np.concatenate(results, axis=0)   # shape (N, 3)


# ── AUC-ROC ───────────────────────────────────────────────────────────────────

# Maps PU-learning split labels → model class index (0/1/2).
# unlabeled is treated as class 0 (not_bookmarked) — the same proxy
# assumption as in train_pu.py, where unlabeled is the negative set.
LABEL_TO_CLASS = {
    "pixiv_public":  1,   # bookmarked_public
    "pixiv_private": 2,   # bookmarked_private
    "twitter":       1,   # bookmarked_public (tweets are public bookmarks)
    "unlabeled":     0,   # not_bookmarked (proxy)
}


def calc_auc(
    scores_all: np.ndarray,       # shape (N_unl + N_labeled, 3)
    image_ids_unl: list[str],
    image_ids_labeled: list[str],
    labels_labeled: list[str],
    target_classes: list[int],
    model_name: str,
    split: str,
) -> list[dict]:
    """
    For each target class, build binary (y_true, scores) and compute AUC-ROC.

    Positive  (y=1): labeled images whose LABEL_TO_CLASS maps to class_idx
    Negative  (y=0): unlabeled images (same proxy as train_pu.py)

    scores_all rows: first len(image_ids_unl) rows are unlabeled,
                     then len(image_ids_labeled) rows are labeled.
    """
    from sklearn.metrics import average_precision_score, roc_auc_score

    n_unl     = len(image_ids_unl)
    s_unl     = scores_all[:n_unl]
    s_labeled = scores_all[n_unl:]

    records = []
    for class_idx in target_classes:
        class_name = CLASS_NAMES[class_idx]

        # y=1 for labeled images that map to this class
        pos_mask  = np.array([LABEL_TO_CLASS.get(lbl, -1) == class_idx
                               for lbl in labels_labeled])
        # exclude labeled images that map to a *different* positive class
        other_pos = np.array([LABEL_TO_CLASS.get(lbl, -1) not in (-1, 0, class_idx)
                               for lbl in labels_labeled])

        s_pos   = s_labeled[pos_mask, class_idx]
        s_other = s_labeled[other_pos, class_idx]   # excluded from scoring
        s_neg   = s_unl[:, class_idx]
        # also include labeled images that are NOT a positive for any class
        # (LABEL_TO_CLASS == 0) together with unlabeled negatives
        neg_from_labeled_mask = np.array([LABEL_TO_CLASS.get(lbl, -1) == 0
                                           for lbl in labels_labeled])
        s_neg_labeled = s_labeled[neg_from_labeled_mask, class_idx]

        scores_bin = np.concatenate([s_pos,
                                     s_neg,
                                     s_neg_labeled])
        y_true     = np.concatenate([np.ones(len(s_pos)),
                                     np.zeros(len(s_neg) + len(s_neg_labeled))])

        if y_true.sum() == 0 or (y_true == 0).sum() == 0:
            log.warning("  Skipping AUC for %s/%s: no positives or no negatives",
                        model_name, class_name)
            continue

        auc = float(roc_auc_score(y_true, scores_bin))
        ap  = float(average_precision_score(y_true, scores_bin))
        log.info(
            "  AUC-ROC=%.4f  AP=%.4f  (pos=%d, neg=%d)  [%s / %s]",
            auc, ap, int(y_true.sum()), int((y_true == 0).sum()),
            model_name, class_name,
        )
        records.append({
            "model":      model_name,
            "split":      split,
            "class":      class_name,
            "n_pos":      int(y_true.sum()),
            "n_neg":      int((y_true == 0).sum()),
            "auc_roc":    round(auc, 5),
            "avg_precision": round(ap, 5),
        })

    return records


# ── Scoring ───────────────────────────────────────────────────────────────────

def score_and_save(
    clf,
    model_name: str,
    X_unl: np.ndarray,
    image_ids_unl: list[str],
    id_to_path: dict[str, str],
    X_labeled: np.ndarray,
    image_ids_labeled: list[str],
    labels_labeled: list[str],
    target_classes: list[int],
    n: int,
    split: str,
) -> list[dict]:
    """
    Score unlabeled images, save top-N montages, and compute AUC-ROC.

    Returns a list of metric dicts (one per target class).
    """
    if hasattr(clf, "decision_function"):
        scores_unl     = clf.decision_function(X_unl)       # (N_unl, 3)
        scores_labeled = clf.decision_function(X_labeled)   # (N_labeled, 3)
    elif hasattr(clf, "predict_proba"):
        scores_unl     = clf.predict_proba(X_unl)
        scores_labeled = clf.predict_proba(X_labeled)
    else:
        raise ValueError(f"Model {model_name} has neither decision_function nor predict_proba")

    # ── Top-N montage per class (unlabeled only) ──────────────────────────────
    for class_idx in target_classes:
        class_name = CLASS_NAMES[class_idx]
        scores     = scores_unl[:, class_idx]
        top_idx    = np.argsort(scores)[::-1][:n]

        log.info(
            "  ── Top %d unlabeled by %s score (%s / %s) ──",
            n, class_name, model_name, split,
        )
        top_paths  = []
        top_scores = []
        for rank, idx in enumerate(top_idx, 1):
            iid   = image_ids_unl[idx]
            fpath = id_to_path[iid]
            score = float(scores[idx])
            log.info("  %2d. score=%.4f  %s  [%s]", rank, score, fpath, iid)
            top_paths.append(fpath)
            top_scores.append(score)

        out_path = RESULTS_DIR / f"top{n}_{model_name}_{class_name}_{split}.png"
        _save_montage(top_paths, top_scores, out_path)

    # ── AUC-ROC ───────────────────────────────────────────────────────────────
    scores_all = np.concatenate([scores_unl, scores_labeled], axis=0)
    return calc_auc(
        scores_all,
        image_ids_unl, image_ids_labeled, labels_labeled,
        target_classes, model_name, split,
    )


# ── Main ─────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--split", choices=["train", "val", "test"], default="test",
        help="Which split to score unlabeled images from (default: test)",
    )
    parser.add_argument(
        "--top-k", type=int, default=30,
        help="Number of top images per class per model (default: 30)",
    )
    parser.add_argument(
        "--classes", nargs="+",
        choices=CLASS_NAMES + ["all"], default=["all"],
        help="Which class scores to rank by (default: all)",
    )
    parser.add_argument(
        "--gpu-device", type=str, default=None,
        help="PyTorch device for torch network (default: auto-detect cuda/cpu)",
    )
    args = parser.parse_args()

    target_classes = (
        list(range(len(CLASS_NAMES)))
        if "all" in args.classes
        else [CLASS_NAMES.index(c) for c in args.classes]
    )

    import torch
    gpu_device = args.gpu_device or ("cuda" if torch.cuda.is_available() else "cpu")
    log.info("PyTorch device: %s", gpu_device)

    # ── Load splits ───────────────────────────────────────────────────────────
    splits_path = METADATA_DIR / "splits.parquet"
    if not splits_path.exists():
        log.error("splits.parquet not found. Run build_dataset.py first.")
        sys.exit(1)
    splits = pd.read_parquet(splits_path)

    unl_df = splits[
        (splits["split"] == args.split) & (splits["label"] == UNLABELED_LABEL)
    ].reset_index(drop=True)

    if unl_df.empty:
        log.error("No unlabeled images found in split=%s", args.split)
        sys.exit(1)

    log.info(
        "Unlabeled images in split=%s: %d",
        args.split, len(unl_df),
    )

    image_ids_unl = unl_df["image_id"].tolist()
    id_to_path    = dict(zip(unl_df["image_id"], unl_df["file_path"]))

    # labeled rows in the same split (for AUC-ROC)
    labeled_df        = splits[
        (splits["split"] == args.split) & (splits["label"] != UNLABELED_LABEL)
    ].reset_index(drop=True)
    image_ids_labeled = labeled_df["image_id"].tolist()
    labels_labeled    = labeled_df["label"].tolist()
    log.info("Labeled images in split=%s: %d", args.split, len(labeled_df))

    # ── Load deepdanbooru features (used by all GCS models) ──────────────────
    log.info("Loading deepdanbooru features …")
    store     = FeatureStore("deepdanbooru")
    X_unl     = store.load_rows(image_ids_unl)
    X_labeled = store.load_rows(image_ids_labeled)
    log.info("Unlabeled feature matrix : %s", X_unl.shape)
    log.info("Labeled   feature matrix : %s", X_labeled.shape)

    # ── Collect models ────────────────────────────────────────────────────────
    sklearn_paths = sorted(MODELS_DIR.glob("sklearn-multiclass-*.joblib"))
    has_torch     = TORCH_MODEL_PATH.exists()

    if not sklearn_paths and not has_torch:
        log.error(
            "No models found in %s\n"
            "  Expected: sklearn-multiclass-*.joblib  or  %s",
            MODELS_DIR, TORCH_MODEL_PATH.name,
        )
        sys.exit(1)

    log.info("sklearn models : %d", len(sklearn_paths))
    for p in sklearn_paths:
        log.info("  %s", p.name)
    log.info("torch model    : %s", TORCH_MODEL_PATH.name if has_torch else "(not found)")

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    all_records: list[dict] = []

    # ── Score sklearn models ──────────────────────────────────────────────────
    for model_path in sklearn_paths:
        model_name = model_path.stem
        log.info("=" * 60)
        log.info("Model: %s", model_name)
        log.info("=" * 60)

        log.info("  Loading model …")
        clf = joblib.load(model_path)
        _fix_sklearn_compat(clf)

        records = score_and_save(
            clf, model_name,
            X_unl, image_ids_unl, id_to_path,
            X_labeled, image_ids_labeled, labels_labeled,
            target_classes=target_classes,
            n=args.top_k,
            split=args.split,
        )
        all_records.extend(records)

    # ── Score PyTorch model ───────────────────────────────────────────────────
    if has_torch:
        model_name = "torch-multiclass-onehot-shallow-network"
        log.info("=" * 60)
        log.info("Model: %s", model_name)
        log.info("=" * 60)

        log.info("  Loading model …")
        net = _load_torch_network(gpu_device)
        clf = TorchNetworkWrapper(net, device=gpu_device)

        records = score_and_save(
            clf, model_name,
            X_unl, image_ids_unl, id_to_path,
            X_labeled, image_ids_labeled, labels_labeled,
            target_classes=target_classes,
            n=args.top_k,
            split=args.split,
        )
        all_records.extend(records)

    # ── Save metrics ──────────────────────────────────────────────────────────
    if all_records:
        metrics_path = RESULTS_DIR / "metrics_score_unlabeled.csv"
        df_new = pd.DataFrame(all_records)
        if metrics_path.exists():
            df_new = pd.concat([pd.read_csv(metrics_path), df_new], ignore_index=True)
        df_new.to_csv(metrics_path, index=False)

        log.info("\n── AUC-ROC summary ──")
        log.info("\n%s", pd.DataFrame(all_records)[
            ["model", "class", "auc_roc", "avg_precision"]
        ].to_string(index=False, float_format="%.4f"))
        log.info("Metrics saved → %s", metrics_path)

    log.info("Done. Results saved to %s", RESULTS_DIR)


if __name__ == "__main__":
    main()
