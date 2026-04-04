#!/usr/bin/env python3
"""
Score unlabeled images using trained classifiers and save montage images.

Evaluates two model families in order:

1. Legacy multiclass models (6000-dim DeepDanbooru features):
     sklearn-multiclass-*.joblib      — sklearn classifiers; decision_function (N×3)
     torch-multiclass-onehot-shallow-network-multilayer
                                      — PyTorch network (6000→512→128→128→3)
   Classes: 0=not_bookmarked  1=bookmarked_public  2=bookmarked_private

2. PU Learning models (train_pu.py):
     {feature}_{label}_{method}.joblib — binary PU classifiers (one per label)
   Feature types: deepdanbooru, eva02, pixai
   Labels:        pixiv_public, pixiv_private, twitter
   Methods:       elkan_noto, biased_svm, nnpu

Output per model:
  data/results/top{N}_{model_name}_{class_or_label}_{split}.png

Usage:
  python score_unlabeled.py
  python score_unlabeled.py --top-k 20
  python score_unlabeled.py --split val
  python score_unlabeled.py --classes bookmarked_private bookmarked_public
  python score_unlabeled.py --labels pixiv_public pixiv_private
  python score_unlabeled.py --features eva02 --methods biased_svm
"""

import argparse
import logging
import re
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

# ── Legacy multiclass constants ───────────────────────────────────────────────
CLASS_NAMES      = ["not_bookmarked", "bookmarked_public", "bookmarked_private"]
TORCH_MODEL_PATH = MODELS_DIR / "torch-multiclass-onehot-shallow-network-multilayer"

LABEL_TO_CLASS = {
    "pixiv_public":  1,   # bookmarked_public
    "pixiv_private": 2,   # bookmarked_private
    "twitter":       1,   # bookmarked_public (tweets are public bookmarks)
    "unlabeled":     0,   # not_bookmarked (proxy)
}

# ── PU Learning constants ─────────────────────────────────────────────────────
ALL_FEATURES = ["deepdanbooru", "eva02", "pixai"]
ALL_LABELS   = ["pixiv_public", "pixiv_private", "twitter"]
ALL_METHODS  = ["elkan_noto", "biased_svm", "nnpu"]

_MODEL_RE = re.compile(
    r"^(" + "|".join(ALL_FEATURES) + r")"
    r"_(" + "|".join(ALL_LABELS) + r")"
    r"_(" + "|".join(ALL_METHODS) + r")$"
)


# ── Unpickling support ────────────────────────────────────────────────────────
# train_pu.py is run as __main__, so these classes are pickled under __main__.
# Define them here (also __main__) so joblib.load can resolve them.

class _ScaledClassifier:
    """Thin wrapper: applies StandardScaler before delegating to clf."""

    def __init__(self, scaler, clf):
        self.scaler = scaler
        self.clf    = clf

    def predict_proba(self, X: np.ndarray) -> np.ndarray:
        return self.clf.predict_proba(self.scaler.transform(X))

    def decision_function(self, X: np.ndarray) -> np.ndarray:
        return self.clf.decision_function(self.scaler.transform(X))

    def predict(self, X: np.ndarray) -> np.ndarray:
        return self.clf.predict(self.scaler.transform(X))


class NNPUClassifier:
    """sklearn-compatible wrapper for a trained nnPU neural network."""

    def __init__(self, model, scaler, device: str):
        self.model  = model
        self.scaler = scaler
        self.device = device

    def predict_proba(self, X: np.ndarray) -> np.ndarray:
        import torch
        if self.scaler is not None:
            X = self.scaler.transform(X)
        self.model.eval()
        probs = []
        with torch.no_grad():
            for i in range(0, len(X), 2048):
                batch  = torch.from_numpy(X[i:i+2048]).float().to(self.device)
                logits = self.model(batch).squeeze(1)
                probs.append(torch.sigmoid(logits).cpu().numpy())
        p1 = np.concatenate(probs)
        return np.stack([1.0 - p1, p1], axis=1)


# ── sklearn version compatibility ─────────────────────────────────────────────

def _fix_sklearn_compat(clf) -> None:
    """Patch models pickled with sklearn <1.4 (missing monotonic_cst)."""
    from sklearn.tree import DecisionTreeClassifier

    estimators = []
    if hasattr(clf, "estimators_"):
        estimators.extend(clf.estimators_)
    if hasattr(clf, "estimator"):
        estimators.append(clf.estimator)
    if hasattr(clf, "clf"):
        estimators.append(clf.clf)

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
    thumb_size: int = 640,
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


# ── Legacy PyTorch model ─────────────────────────────────────────────────────

def _load_torch_network(device: str):
    """
    Instantiate the shallow Network and load state_dict from TORCH_MODEL_PATH.

    Architecture: Linear(6000→512) ReLU → Linear(512→128) ReLU → Linear(128→128) ReLU → Linear(128→3)
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
    Wraps the legacy PyTorch Network.
    decision_function returns raw logits (shape N×3) as numpy array.
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


# ── Legacy AUC-ROC (multiclass) ───────────────────────────────────────────────

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
    Negative  (y=0): unlabeled images (proxy)

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

        pos_mask  = np.array([LABEL_TO_CLASS.get(lbl, -1) == class_idx
                               for lbl in labels_labeled])
        other_pos = np.array([LABEL_TO_CLASS.get(lbl, -1) not in (-1, 0, class_idx)
                               for lbl in labels_labeled])

        s_pos   = s_labeled[pos_mask, class_idx]
        neg_from_labeled_mask = np.array([LABEL_TO_CLASS.get(lbl, -1) == 0
                                           for lbl in labels_labeled])
        s_neg_labeled = s_labeled[neg_from_labeled_mask, class_idx]
        s_neg   = s_unl[:, class_idx]

        scores_bin = np.concatenate([s_pos, s_neg, s_neg_labeled])
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
            "model":         model_name,
            "split":         split,
            "class":         class_name,
            "n_pos":         int(y_true.sum()),
            "n_neg":         int((y_true == 0).sum()),
            "auc_roc":       round(auc, 5),
            "avg_precision": round(ap, 5),
        })

    return records


# ── Legacy scoring (multiclass) ───────────────────────────────────────────────

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
    Score unlabeled images with a multiclass model, save top-N montages per class,
    and compute AUC-ROC.  Returns a list of metric dicts (one per target class).
    """
    if hasattr(clf, "decision_function"):
        scores_unl     = clf.decision_function(X_unl)
        scores_labeled = clf.decision_function(X_labeled)
    elif hasattr(clf, "predict_proba"):
        scores_unl     = clf.predict_proba(X_unl)
        scores_labeled = clf.predict_proba(X_labeled)
    else:
        raise ValueError(f"Model {model_name} has neither decision_function nor predict_proba")

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

    scores_all = np.concatenate([scores_unl, scores_labeled], axis=0)
    return calc_auc(
        scores_all,
        image_ids_unl, image_ids_labeled, labels_labeled,
        target_classes, model_name, split,
    )


# ── AUC-ROC (binary PU) ───────────────────────────────────────────────────────

def calc_auc_binary(
    scores_unl: np.ndarray,      # shape (N_unl,)  — unlabeled images
    scores_labeled: np.ndarray,  # shape (N_labeled,)
    labels_labeled: list[str],
    positive_label: str,
    model_name: str,
    split: str,
) -> dict | None:
    """
    Binary AUC-ROC for one PU model.

    Positive (y=1): labeled images whose label == positive_label
    Negative (y=0): unlabeled images
    Excluded:       labeled images with other positive labels
    """
    from sklearn.metrics import average_precision_score, roc_auc_score

    pos_mask = np.array([lbl == positive_label for lbl in labels_labeled])
    s_pos    = scores_labeled[pos_mask]
    s_neg    = scores_unl

    if len(s_pos) == 0 or len(s_neg) == 0:
        log.warning("  Skipping AUC for %s: no positives or no negatives", model_name)
        return None

    scores_bin = np.concatenate([s_pos, s_neg])
    y_true     = np.concatenate([np.ones(len(s_pos)), np.zeros(len(s_neg))])

    auc = float(roc_auc_score(y_true, scores_bin))
    ap  = float(average_precision_score(y_true, scores_bin))
    log.info(
        "  AUC-ROC=%.4f  AP=%.4f  (pos=%d, neg=%d)  [%s]",
        auc, ap, len(s_pos), len(s_neg), model_name,
    )
    return {
        "model":         model_name,
        "split":         split,
        "label":         positive_label,
        "n_pos":         len(s_pos),
        "n_neg":         len(s_neg),
        "auc_roc":       round(auc, 5),
        "avg_precision": round(ap, 5),
    }


# ── Scoring ───────────────────────────────────────────────────────────────────

def score_model(
    clf,
    feature_name: str,
    positive_label: str,
    method: str,
    image_ids_unl: list[str],
    id_to_path: dict[str, str],
    image_ids_labeled: list[str],
    labels_labeled: list[str],
    X_unl: np.ndarray,
    X_labeled: np.ndarray,
    n: int,
    split: str,
) -> dict | None:
    """Score one PU model, save top-N montage, and return AUC-ROC metrics."""
    model_name = f"{feature_name}_{positive_label}_{method}"

    if hasattr(clf, "predict_proba"):
        scores_unl     = clf.predict_proba(X_unl)[:, 1]
        scores_labeled = clf.predict_proba(X_labeled)[:, 1]
    elif hasattr(clf, "decision_function"):
        scores_unl     = clf.decision_function(X_unl)
        scores_labeled = clf.decision_function(X_labeled)
    else:
        raise ValueError(f"Model {model_name} has neither predict_proba nor decision_function")

    # ── Top-N montage ─────────────────────────────────────────────────────────
    top_idx = np.argsort(scores_unl)[::-1][:n]
    log.info(
        "  ── Top %d unlabeled  [%s / %s / %s] ──",
        n, feature_name, positive_label, method,
    )
    top_paths  = []
    top_scores = []
    for rank, idx in enumerate(top_idx, 1):
        iid   = image_ids_unl[idx]
        fpath = id_to_path[iid]
        score = float(scores_unl[idx])
        log.info("  %2d. score=%.4f  %s  [%s]", rank, score, fpath, iid)
        top_paths.append(fpath)
        top_scores.append(score)

    out_path = RESULTS_DIR / f"top{n}_{model_name}_{split}.png"
    _save_montage(top_paths, top_scores, out_path)

    # ── AUC-ROC ───────────────────────────────────────────────────────────────
    return calc_auc_binary(
        scores_unl, scores_labeled, labels_labeled,
        positive_label, model_name, split,
    )


# ── Model discovery ───────────────────────────────────────────────────────────

def discover_models(
    filter_features: list[str],
    filter_labels: list[str],
    filter_methods: list[str],
) -> list[tuple[str, str, str, Path]]:
    """
    Return [(feature, label, method, path), ...] for all matching .joblib files.
    """
    found = []
    for p in sorted(MODELS_DIR.glob("*.joblib")):
        m = _MODEL_RE.match(p.stem)
        if not m:
            continue
        feature, label, method = m.group(1), m.group(2), m.group(3)
        if feature not in filter_features:
            continue
        if label not in filter_labels:
            continue
        if method not in filter_methods:
            continue
        found.append((feature, label, method, p))
    return found


# ── Main ─────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--split", choices=["train", "val", "test", "all"], default="test",
        help="Which split to score unlabeled images from (default: test)",
    )
    parser.add_argument(
        "--top-k", type=int, default=30,
        help="Number of top images per model (default: 30)",
    )
    parser.add_argument(
        "--classes", nargs="+",
        choices=CLASS_NAMES + ["all"], default=["all"],
        help="Which classes to rank for legacy multiclass models (default: all)",
    )
    parser.add_argument(
        "--features", nargs="+",
        choices=ALL_FEATURES + ["all"], default=["all"],
        help="Feature type(s) to evaluate for PU models (default: all)",
    )
    parser.add_argument(
        "--labels", nargs="+",
        choices=ALL_LABELS + ["all"], default=["all"],
        help="Positive label(s) to evaluate for PU models (default: all)",
    )
    parser.add_argument(
        "--methods", nargs="+",
        choices=ALL_METHODS + ["all"], default=["all"],
        help="PU method(s) to evaluate (default: all)",
    )
    parser.add_argument(
        "--gpu-device", type=str, default=None,
        help="PyTorch device for torch/nnpu models (default: auto-detect cuda/cpu)",
    )
    args = parser.parse_args()

    target_classes  = (
        list(range(len(CLASS_NAMES)))
        if "all" in args.classes
        else [CLASS_NAMES.index(c) for c in args.classes]
    )
    filter_features = ALL_FEATURES if "all" in args.features else args.features
    filter_labels   = ALL_LABELS   if "all" in args.labels   else args.labels
    filter_methods  = ALL_METHODS  if "all" in args.methods  else args.methods

    # ── Load splits ───────────────────────────────────────────────────────────
    splits_path = METADATA_DIR / "splits.parquet"
    if not splits_path.exists():
        log.error("splits.parquet not found. Run build_dataset.py first.")
        sys.exit(1)
    splits = pd.read_parquet(splits_path)

    split_mask = (
        pd.Series([True] * len(splits), index=splits.index)
        if args.split == "all"
        else (splits["split"] == args.split)
    )

    unl_df = splits[
        split_mask & (splits["label"] == UNLABELED_LABEL)
    ].reset_index(drop=True)
    if unl_df.empty:
        log.error("No unlabeled images found in split=%s", args.split)
        sys.exit(1)
    log.info("Unlabeled images in split=%s: %d", args.split, len(unl_df))

    image_ids_unl = unl_df["image_id"].tolist()
    id_to_path    = dict(zip(unl_df["image_id"], unl_df["file_path"]))

    labeled_df        = splits[
        split_mask & (splits["label"] != UNLABELED_LABEL)
    ].reset_index(drop=True)
    image_ids_labeled = labeled_df["image_id"].tolist()
    labels_labeled    = labeled_df["label"].tolist()
    log.info("Labeled images in split=%s: %d", args.split, len(labeled_df))

    # ── Discover legacy models ────────────────────────────────────────────────
    sklearn_paths = sorted(MODELS_DIR.glob("sklearn-multiclass-*.joblib"))
    has_torch     = TORCH_MODEL_PATH.exists()

    # ── Discover PU models ────────────────────────────────────────────────────
    pu_models = discover_models(filter_features, filter_labels, filter_methods)

    if not sklearn_paths and not has_torch and not pu_models:
        log.error(
            "No models found in %s\n"
            "  Legacy: sklearn-multiclass-*.joblib  or  %s\n"
            "  PU:     {feature}_{label}_{method}.joblib  (run train_pu.py first)",
            MODELS_DIR, TORCH_MODEL_PATH.name,
        )
        sys.exit(1)

    log.info("Legacy sklearn models : %d", len(sklearn_paths))
    for p in sklearn_paths:
        log.info("  %s", p.name)
    log.info("Legacy torch model    : %s", TORCH_MODEL_PATH.name if has_torch else "(not found)")
    log.info("PU models             : %d", len(pu_models))
    for feature, label, method, path in pu_models:
        log.info("  %s", path.name)

    # ── PyTorch device ────────────────────────────────────────────────────────
    need_torch = has_torch or any(method == "nnpu" for _, _, method, _ in pu_models)
    if need_torch:
        import torch
        gpu_device = args.gpu_device or ("cuda" if torch.cuda.is_available() else "cpu")
        log.info("PyTorch device: %s", gpu_device)
    else:
        gpu_device = None

    # ── Feature cache: feature_name → (X_unl, X_labeled) ─────────────────────
    feature_cache: dict[str, tuple[np.ndarray, np.ndarray]] = {}

    def get_features(feature_name: str) -> tuple[np.ndarray, np.ndarray]:
        if feature_name not in feature_cache:
            log.info("Loading features: %s …", feature_name)
            store = FeatureStore(feature_name)
            X_u   = store.load_rows(image_ids_unl)
            X_l   = store.load_rows(image_ids_labeled)
            log.info("  unl=%s  labeled=%s", X_u.shape, X_l.shape)
            feature_cache[feature_name] = (X_u, X_l)
        return feature_cache[feature_name]

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    all_records: list[dict] = []

    # ── 1. Legacy sklearn multiclass models ───────────────────────────────────
    for model_path in sklearn_paths:
        model_name = model_path.stem
        log.info("=" * 60)
        log.info("Legacy Model: %s", model_name)
        log.info("=" * 60)

        log.info("  Loading model …")
        clf = joblib.load(model_path)
        _fix_sklearn_compat(clf)

        X_unl_feat, X_labeled_feat = get_features("deepdanbooru")

        records = score_and_save(
            clf, model_name,
            X_unl_feat, image_ids_unl, id_to_path,
            X_labeled_feat, image_ids_labeled, labels_labeled,
            target_classes=target_classes,
            n=args.top_k,
            split=args.split,
        )
        all_records.extend(records)

    # ── 2. Legacy PyTorch multiclass model ────────────────────────────────────
    if has_torch:
        model_name = "torch-multiclass-onehot-shallow-network"
        log.info("=" * 60)
        log.info("Legacy Model: %s", model_name)
        log.info("=" * 60)

        log.info("  Loading model …")
        net = _load_torch_network(gpu_device)
        clf = TorchNetworkWrapper(net, device=gpu_device)

        X_unl_feat, X_labeled_feat = get_features("deepdanbooru")

        records = score_and_save(
            clf, model_name,
            X_unl_feat, image_ids_unl, id_to_path,
            X_labeled_feat, image_ids_labeled, labels_labeled,
            target_classes=target_classes,
            n=args.top_k,
            split=args.split,
        )
        all_records.extend(records)

    # ── 3. PU Learning models ─────────────────────────────────────────────────
    for feature, label, method, model_path in pu_models:
        log.info("=" * 60)
        log.info("PU Model: %s_%s_%s", feature, label, method)
        log.info("=" * 60)

        log.info("  Loading model …")
        clf = joblib.load(model_path)
        _fix_sklearn_compat(clf)

        if isinstance(clf, NNPUClassifier) and gpu_device is not None:
            clf.model  = clf.model.to(gpu_device)
            clf.device = gpu_device

        X_unl_feat, X_labeled_feat = get_features(feature)

        record = score_model(
            clf,
            feature_name      = feature,
            positive_label    = label,
            method            = method,
            image_ids_unl     = image_ids_unl,
            id_to_path        = id_to_path,
            image_ids_labeled = image_ids_labeled,
            labels_labeled    = labels_labeled,
            X_unl             = X_unl_feat,
            X_labeled         = X_labeled_feat,
            n                 = args.top_k,
            split             = args.split,
        )
        if record is not None:
            all_records.append(record)

    # ── Save metrics ──────────────────────────────────────────────────────────
    if all_records:
        metrics_path = RESULTS_DIR / "metrics_score_unlabeled.csv"
        df_new = pd.DataFrame(all_records)
        if metrics_path.exists():
            df_new = pd.concat([pd.read_csv(metrics_path), df_new], ignore_index=True)
        df_new.to_csv(metrics_path, index=False)

        df_summary = pd.DataFrame(all_records)
        # Unify "class" (legacy) and "label" (PU) into one column for display
        if "class" in df_summary.columns and "label" in df_summary.columns:
            df_summary["label"] = df_summary["label"].fillna(df_summary["class"])
        elif "class" in df_summary.columns:
            df_summary = df_summary.rename(columns={"class": "label"})
        log.info("\n── AUC-ROC summary ──")
        log.info(
            "\n%s",
            df_summary[["model", "label", "auc_roc", "avg_precision"]]
            .to_string(index=False, float_format="%.4f"),
        )
        log.info("Metrics saved → %s", metrics_path)

    log.info("Done. Results saved to %s", RESULTS_DIR)


if __name__ == "__main__":
    main()
