#!/usr/bin/env python3
"""
Train PU Learning classifiers for illustration preference prediction.

Three labels × three methods × three feature types = 27 models total.

Each positive label (pixiv_public / pixiv_private / twitter) is trained
independently against the unlabeled set, yielding a separate preference
score per label per image.

  Methods:
    elkan_noto  – Elkan & Noto (2008): EM-based probability correction
                  Base estimator: LogisticRegression (solver=saga)
    biased_svm  – Biased SVM: LinearSVC with asymmetric sample weights
                  Unlabeled weight = pi_p (class prior estimate)
    nnpu        – nnPU (Kiryo et al. 2017): non-negative PU risk with MLP
                  GPU-accelerated; early stopping on val AUC-ROC

  Feature types:
    deepdanbooru – 6 000-dim tag probability vector (ResNet50)
    eva02        – 1 024-dim visual embedding (EVA02-Large)
    pixai        – 13 461-dim tag probability vector (PixAI v0.9)

Evaluation (val set — positive held-out vs unlabeled proxy):
  AUC-ROC, Average Precision, Precision@K  (K = |val positives|)

Outputs:
  data/models/{feature}_{label}_{method}.joblib  – serialised classifier
  data/results/metrics.csv                       – per-run metrics (appended)

Usage:
  python train_pu.py
  python train_pu.py --features eva02 --methods biased_svm
  python train_pu.py --labels pixiv_public twitter --features all --methods all
  python train_pu.py --grid-search --features deepdanbooru
  python train_pu.py --features eva02 --methods nnpu --epochs 100
"""

import argparse
import concurrent.futures
import itertools
import json
import logging
import sys
import time
from datetime import datetime
from functools import partial
from pathlib import Path

import h5py
import joblib
import numpy as np
import pandas as pd
from sklearn.calibration import CalibratedClassifierCV
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import average_precision_score, roc_auc_score
from sklearn.preprocessing import StandardScaler
from sklearn.svm import LinearSVC

SCRIPTS_DIR = Path(__file__).parent
sys.path.insert(0, str(SCRIPTS_DIR))
from config import (
    DATA_DIR, FEATURES_DIR, METADATA_DIR,
    UNLABELED_LABEL, RANDOM_SEED,
    DEEPDANBOORU_DIM, EVA02_DIM, PIXAI_DIM,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

MODELS_DIR  = DATA_DIR / "models"
RESULTS_DIR = DATA_DIR / "results"

FEATURE_DIM = {
    "deepdanbooru": DEEPDANBOORU_DIM,
    "eva02":        EVA02_DIM,
    "pixai":        PIXAI_DIM,
}

ALL_FEATURES = ["deepdanbooru", "eva02", "pixai"]
ALL_METHODS  = ["elkan_noto", "biased_svm", "nnpu"]
ALL_LABELS   = ["pixiv_public", "pixiv_private", "twitter"]

# MLP hidden layer sizes per feature type (nnPU)
MLP_HIDDEN = {
    "deepdanbooru": [1024, 512, 128],
    "eva02":        [512,  256, 128],
    "pixai":        [2048, 512, 128],
}


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
        """
        Return float32 array of shape (N, dim) for the requested image_ids.
        Rows are returned in the same order as *image_ids*.
        Missing IDs are filled with zeros and a warning is logged.
        """
        rows   = [self._id_to_row.get(iid) for iid in image_ids]
        valid  = [r for r in rows if r is not None]
        miss   = sum(1 for r in rows if r is None)
        if miss:
            log.warning("  %d / %d image_ids not found in feature store", miss, len(image_ids))

        # Sort row indices for efficient sequential HDF5 access, then reorder
        order     = sorted(range(len(rows)), key=lambda i: rows[i] if rows[i] is not None else -1)
        valid_pos = [i for i in order if rows[i] is not None]

        with h5py.File(self.path, "r") as f:
            hdf_rows = sorted(set(rows[i] for i in valid_pos))
            # Load a contiguous-ish block; fancy indexing on sorted list is fast
            data = f["features"][hdf_rows, :].astype(np.float32)
            row_to_local = {r: j for j, r in enumerate(hdf_rows)}

        out = np.zeros((len(image_ids), data.shape[1]), dtype=np.float32)
        for list_i in valid_pos:
            out[list_i] = data[row_to_local[rows[list_i]]]
        return out


def build_xy(
    splits: pd.DataFrame,
    feature_name: str,
    split: str,
    positive_label: str,
    max_unlabeled: int | None,
    rng: np.random.Generator,
) -> tuple[np.ndarray, np.ndarray, list[str]]:
    """
    Build (X, y, image_ids) arrays for *split* (train / val / test).

    y = 1  for rows whose label == positive_label
    y = 0  for unlabeled rows
    Other positive labels are excluded (kept pure for their own models).
    """
    subset = splits[splits["split"] == split]
    pos    = subset[subset["label"] == positive_label]
    unl    = subset[subset["label"] == UNLABELED_LABEL]

    if max_unlabeled is not None and len(unl) > max_unlabeled:
        idx = rng.choice(len(unl), size=max_unlabeled, replace=False)
        unl = unl.iloc[idx]
        log.info("  Subsampled unlabeled to %d", max_unlabeled)

    combined   = pd.concat([pos, unl])
    image_ids  = combined["image_id"].tolist()
    y          = np.where(combined["label"] != UNLABELED_LABEL, 1, 0).astype(np.int32)

    store = FeatureStore(feature_name)
    X     = store.load_rows(image_ids)

    log.info(
        "  %s %s: X=%s  pos=%d  unl=%d",
        feature_name, split, X.shape, y.sum(), (y == 0).sum(),
    )
    return X, y, image_ids


# ── Metrics ───────────────────────────────────────────────────────────────────

def precision_at_k(y_true: np.ndarray, scores: np.ndarray) -> float:
    """Precision@K where K = number of true positives."""
    k = int(y_true.sum())
    if k == 0:
        return 0.0
    top_k = np.argsort(scores)[::-1][:k]
    return float(y_true[top_k].mean())


def evaluate(clf, X_val: np.ndarray, y_val: np.ndarray) -> dict[str, float]:
    if hasattr(clf, "predict_proba"):
        scores = clf.predict_proba(X_val)[:, 1]
    elif hasattr(clf, "decision_function"):
        scores = clf.decision_function(X_val)
    else:
        scores = clf.predict(X_val).astype(float)

    return {
        "auc_roc": float(roc_auc_score(y_val, scores)),
        "avg_precision": float(average_precision_score(y_val, scores)),
        "precision_at_k": precision_at_k(y_val, scores),
    }


# ── Elkan & Noto ──────────────────────────────────────────────────────────────

def train_elkan_noto(
    X_train: np.ndarray,
    y_train: np.ndarray,
    C: float = 1.0,
    scale: bool = True,
) -> object:
    """
    Elkan & Noto (2008) PU classifier.

    Uses LogisticRegression as the base estimator, wrapped by
    pulearn.ElkanotoPuClassifier which estimates c = P(labeled|positive)
    from a held-out fraction of positives and adjusts probabilities.
    """
    from pulearn import ElkanotoPuClassifier

    base = LogisticRegression(
        solver="saga",
        max_iter=2000,
        C=C,
        n_jobs=-1,
        random_state=RANDOM_SEED,
    )

    if scale:
        log.info("    Fitting StandardScaler …")
        scaler  = StandardScaler()
        X_train = scaler.fit_transform(X_train)   # returns a copy
    else:
        scaler = None

    clf = ElkanotoPuClassifier(estimator=base, hold_out_ratio=0.1, random_state=RANDOM_SEED)
    log.info("    Fitting ElkanotoPuClassifier (C=%.3g) …", C)
    t0  = time.time()
    clf.fit(X_train, y_train)
    log.info("    Done in %.1f s", time.time() - t0)

    # Wrap scaler + PU classifier into a single Pipeline-like object
    # so callers can call predict_proba without re-scaling
    if scaler is not None:
        from sklearn.pipeline import Pipeline
        pipeline = Pipeline([("scaler", scaler), ("clf", clf)])
        # Manually attach the fitted objects since we already transformed
        # (we need to return something with predict_proba)
        return _ScaledClassifier(scaler, clf)

    return clf


def train_biased_svm(
    X_train: np.ndarray,
    y_train: np.ndarray,
    pi_p: float,
    C: float = 1.0,
    scale: bool = True,
) -> object:
    """
    Biased SVM (Liu et al. 2003).

    LinearSVC with asymmetric sample weights:
      positive  samples → weight = 1.0
      unlabeled samples → weight = pi_p  (class prior estimate)

    Higher pi_p → unlabeled samples are penalised more heavily,
    reflecting a higher chance they contain true positives.

    The final model is probability-calibrated with Platt scaling so that
    consistent AUC / AP scores can be computed across methods.
    """
    sample_weight = np.where(y_train == 1, 1.0, pi_p)

    if scale:
        log.info("    Fitting StandardScaler …")
        scaler  = StandardScaler()
        X_train = scaler.fit_transform(X_train)   # returns a copy
    else:
        scaler = None

    base = LinearSVC(C=C, max_iter=3000, random_state=RANDOM_SEED)
    log.info(
        "    Fitting LinearSVC (C=%.3g, pi_p=%.3f) …", C, pi_p
    )
    t0 = time.time()
    base.fit(X_train, y_train, sample_weight=sample_weight)
    log.info("    Raw SVC done in %.1f s – calibrating probabilities …", time.time() - t0)

    # Platt scaling calibration (sigmoid) using the training data
    # (small cv=3 fold to keep runtime manageable)
    calibrated = CalibratedClassifierCV(base, cv=3, method="sigmoid")
    calibrated.fit(X_train, y_train, sample_weight=sample_weight)
    log.info("    Calibration done in %.1f s", time.time() - t0)

    if scaler is not None:
        return _ScaledClassifier(scaler, calibrated)
    return calibrated


# ── nnPU (Kiryo et al. 2017) ──────────────────────────────────────────────────

def _build_mlp(input_dim: int, hidden_dims: list[int]):
    """Build a BN-ReLU-Dropout MLP with a single scalar output (logit)."""
    import torch.nn as nn
    layers = []
    prev = input_dim
    for h in hidden_dims:
        layers += [nn.Linear(prev, h), nn.BatchNorm1d(h), nn.ReLU(), nn.Dropout(0.3)]
        prev = h
    layers.append(nn.Linear(prev, 1))
    return nn.Sequential(*layers)


def _nnpu_loss(outputs, y, pi_p: float):
    """
    Non-negative PU risk (Kiryo et al. 2017, eq. 9).

    R_nnPU = π_p · R_p^+(f) + max(0, R_u^-(f) − π_p · R_p^-(f))

    Surrogate: softplus  l_+(z) = log(1+exp(−z))
                         l_-(z) = log(1+exp( z))
    """
    import torch
    import torch.nn.functional as F

    pos = y == 1
    unl = y == 0

    if pos.sum() == 0:
        return F.softplus(outputs[unl]).mean()
    if unl.sum() == 0:
        return pi_p * F.softplus(-outputs[pos]).mean()

    R_p_plus  = F.softplus(-outputs[pos]).mean()   # l_+(f(x)), x ~ P
    R_p_minus = F.softplus( outputs[pos]).mean()   # l_-(f(x)), x ~ P
    R_u_minus = F.softplus( outputs[unl]).mean()   # l_-(f(x)), x ~ U

    return pi_p * R_p_plus + torch.clamp(R_u_minus - pi_p * R_p_minus, min=0.0)


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


def train_nnpu(
    X_train: np.ndarray,
    y_train: np.ndarray,
    X_val: np.ndarray,
    y_val: np.ndarray,
    feature_name: str,
    pi_p: float,
    device: str,
    epochs: int = 50,
    lr: float = 1e-3,
    batch_size: int = 512,
    patience: int = 10,
    scale: bool = True,
) -> NNPUClassifier:
    """
    Train an MLP with nnPU loss (Kiryo et al. 2017).

    Early stopping is based on val AUC-ROC, evaluated every epoch.
    The model state with the best val AUC is restored at the end.
    """
    import torch
    from torch.utils.data import DataLoader, TensorDataset

    if scale:
        log.info("    Fitting StandardScaler …")
        scaler  = StandardScaler()
        X_train = scaler.fit_transform(X_train)
        X_val_s = scaler.transform(X_val)
    else:
        scaler  = None
        X_val_s = X_val

    hidden_dims = MLP_HIDDEN.get(feature_name, [512, 128])
    model = _build_mlp(X_train.shape[1], hidden_dims).to(device)
    log.info(
        "    MLP architecture: %d → %s → 1  (params=%d)",
        X_train.shape[1], hidden_dims,
        sum(p.numel() for p in model.parameters()),
    )

    X_t   = torch.from_numpy(X_train).float()
    y_t   = torch.from_numpy(y_train.astype(np.float32))
    loader = DataLoader(TensorDataset(X_t, y_t), batch_size=batch_size, shuffle=True)

    optimizer = torch.optim.Adam(model.parameters(), lr=lr, weight_decay=1e-4)
    scheduler = torch.optim.lr_scheduler.CosineAnnealingLR(optimizer, T_max=epochs, eta_min=1e-5)

    best_auc   = -1.0
    best_state = None
    no_improve = 0

    for epoch in range(1, epochs + 1):
        # ── train ────────────────────────────────────────────────────────────
        model.train()
        total_loss = 0.0
        for X_b, y_b in loader:
            X_b, y_b = X_b.to(device), y_b.to(device)
            optimizer.zero_grad()
            loss = _nnpu_loss(model(X_b).squeeze(1), y_b, pi_p)
            loss.backward()
            optimizer.step()
            total_loss += loss.item()
        scheduler.step()

        # ── validate ─────────────────────────────────────────────────────────
        tmp_clf = NNPUClassifier(model, scaler=None, device=device)
        val_auc = float(roc_auc_score(y_val, tmp_clf.predict_proba(X_val_s)[:, 1]))

        if val_auc > best_auc:
            best_auc   = val_auc
            best_state = {k: v.cpu().clone() for k, v in model.state_dict().items()}
            no_improve = 0
        else:
            no_improve += 1

        if epoch % 5 == 0 or no_improve == 0:
            log.info(
                "    Epoch %3d/%d  loss=%.4f  val_auc=%.4f%s",
                epoch, epochs, total_loss / len(loader), val_auc,
                "  ✓ best" if no_improve == 0 else "",
            )

        if no_improve >= patience:
            log.info("    Early stop at epoch %d (patience=%d)", epoch, patience)
            break

    model.load_state_dict(best_state)
    log.info("    Restored best model  val_auc=%.4f", best_auc)
    return NNPUClassifier(model, scaler, device)


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


# ── Grid search helper ────────────────────────────────────────────────────────

def grid_search_C(
    method: str,
    X_train: np.ndarray,
    y_train: np.ndarray,
    X_val: np.ndarray,
    y_val: np.ndarray,
    pi_p: float,
    C_values: list[float],
    scale: bool,
) -> tuple[float, float]:
    """
    Try each C value, return (best_C, best_auc_roc).
    Scaling is applied once outside the loop.
    """
    if scale:
        scaler  = StandardScaler()
        X_train = scaler.fit_transform(X_train)
        X_val   = scaler.transform(X_val)

    best_C, best_auc = C_values[0], -1.0
    for C in C_values:
        if method == "elkan_noto":
            from pulearn import ElkanotoPuClassifier
            base = LogisticRegression(
                solver="saga", max_iter=2000, C=C,
                n_jobs=-1, random_state=RANDOM_SEED,
            )
            clf = ElkanotoPuClassifier(estimator=base, hold_out_ratio=0.1, random_state=RANDOM_SEED)
            clf.fit(X_train, y_train)
            scores = clf.predict_proba(X_val)[:, 1]
        else:  # biased_svm
            sw    = np.where(y_train == 1, 1.0, pi_p)
            base  = LinearSVC(C=C, max_iter=3000, random_state=RANDOM_SEED)
            base.fit(X_train, y_train, sample_weight=sw)
            cal   = CalibratedClassifierCV(base, cv=3, method="sigmoid")
            cal.fit(X_train, y_train, sample_weight=sw)
            scores = cal.predict_proba(X_val)[:, 1]

        auc = float(roc_auc_score(y_val, scores))
        log.info("      C=%.4g → AUC-ROC=%.4f", C, auc)
        if auc > best_auc:
            best_auc, best_C = auc, C

    log.info("    Best C=%.4g  AUC-ROC=%.4f", best_C, best_auc)
    return best_C, best_auc


# ── Results persistence ───────────────────────────────────────────────────────

def save_metrics(records: list[dict]) -> None:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    path = RESULTS_DIR / "metrics.csv"
    df   = pd.DataFrame(records)
    if path.exists():
        df = pd.concat([pd.read_csv(path), df], ignore_index=True)
    df.to_csv(path, index=False)
    log.info("Metrics saved → %s", path)


def print_summary(records: list[dict]) -> None:
    df = pd.DataFrame(records)[["feature", "label", "method", "auc_roc", "avg_precision", "precision_at_k"]]
    log.info("\n" + df.to_string(index=False, float_format="%.4f"))


# ── Top-K unlabeled display ───────────────────────────────────────────────────

def show_top_unlabeled(
    clf,
    feature_name: str,
    method: str,
    splits: pd.DataFrame,
    n: int = 10,
    split: str = "test",
    positive_label: str = "positive",
) -> None:
    """
    Score all unlabeled images in *split*, log the top *n* by predicted
    positive probability, and save a thumbnail montage as a PNG.

    Montage is written to:
      data/results/top{n}_{feature}_{positive_label}_{method}_{split}.png
    """
    unl_df = splits[
        (splits["split"] == split) & (splits["label"] == UNLABELED_LABEL)
    ].reset_index(drop=True)

    if unl_df.empty:
        log.warning("  No unlabeled images in split=%s — skipping top-K display", split)
        return

    log.info("  Scoring %d unlabeled images (%s set) …", len(unl_df), split)
    image_ids  = unl_df["image_id"].tolist()
    id_to_path = dict(zip(unl_df["image_id"], unl_df["file_path"]))

    store  = FeatureStore(feature_name)
    X      = store.load_rows(image_ids)

    if hasattr(clf, "predict_proba"):
        scores = clf.predict_proba(X)[:, 1]
    elif hasattr(clf, "decision_function"):
        scores = clf.decision_function(X)
    else:
        scores = clf.predict(X).astype(float)

    top_idx = np.argsort(scores)[::-1][:n]

    log.info("  ── Top %d unlabeled images (%s / %s / %s / %s) ──", n, feature_name, positive_label, method, split)
    top_paths  = []
    top_scores = []
    for rank, idx in enumerate(top_idx, 1):
        iid   = image_ids[idx]
        fpath = id_to_path[iid]
        score = float(scores[idx])
        log.info("  %2d. score=%.4f  %s  [%s]", rank, score, fpath, iid)
        top_paths.append(fpath)
        top_scores.append(score)

    # ── Thumbnail montage ─────────────────────────────────────────────────────
    _save_montage(
        top_paths, top_scores,
        RESULTS_DIR / f"top{n}_{feature_name}_{positive_label}_{method}_{split}.png",
        thumb_size=224,
    )


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
            # Centre on thumb background
            bg = Image.new("RGB", (thumb_size, thumb_size), (50, 50, 50))
            off_x = (thumb_size - img.width)  // 2
            off_y = (thumb_size - img.height) // 2
            bg.paste(img, (off_x, off_y))
            canvas.paste(bg, (x, y))
        except Exception as exc:
            log.warning("  Could not load thumbnail %s: %s", fpath, exc)

        # Score label below the thumbnail
        draw = ImageDraw.Draw(canvas)
        label = f"#{i+1}  {score:.4f}"
        draw.rectangle([x, y + thumb_size, x + thumb_size, y + thumb_size + label_h], fill=(20, 20, 20))
        draw.text((x + 4, y + thumb_size + 2), label, fill=(220, 220, 100), font=font)

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    canvas.save(out_path)
    log.info("  Montage saved → %s", out_path)


# ── Per-combination worker ────────────────────────────────────────────────────

def _train_combination(
    combo: tuple[str, str, str],
    args,
    splits_path: Path,
) -> dict:
    """
    Train and evaluate one (feature_name, positive_label, method) combination.
    Designed to run inside a subprocess via ProcessPoolExecutor.
    """
    feature_name, positive_label, method = combo

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
    )

    splits      = pd.read_parquet(splits_path)
    train_df    = splits[splits["split"] == "train"]
    n_unl_train = (train_df["label"] == UNLABELED_LABEL).sum()
    rng         = np.random.default_rng(RANDOM_SEED)
    scale       = not args.no_scale

    if args.pi_p is not None:
        pi_p = args.pi_p
    else:
        n_pos_train = (train_df["label"] == positive_label).sum()
        pi_p        = n_pos_train / (n_pos_train + n_unl_train)

    prefix = f"[{feature_name}/{positive_label}/{method}]"
    log.info("%s π_p=%.4f — loading data …", prefix, pi_p)
    X_train, y_train, _ = build_xy(splits, feature_name, "train", positive_label, args.max_unlabeled, rng)
    X_val,   y_val,   _ = build_xy(splits, feature_name, "val",   positive_label, None, rng)

    C = args.C
    if args.grid_search and method != "nnpu":
        C_grid = [0.01, 0.1, 1.0, 10.0]
        log.info("%s Grid search over C=%s …", prefix, C_grid)
        C, _ = grid_search_C(method, X_train.copy(), y_train, X_val.copy(), y_val, pi_p, C_grid, scale)
    elif args.grid_search and method == "nnpu":
        log.info("%s Grid search skipped for nnpu", prefix)

    gpu_device = args.gpu_device or ("cuda" if __import__("torch").cuda.is_available() else "cpu")
    log.info("%s Training …", prefix)
    run_ts  = datetime.now().isoformat(timespec="seconds")
    t_start = time.time()

    if method == "elkan_noto":
        clf = train_elkan_noto(X_train, y_train, C=C, scale=scale)
    elif method == "biased_svm":
        clf = train_biased_svm(X_train, y_train, pi_p=pi_p, C=C, scale=scale)
    else:  # nnpu
        log.info("%s GPU device: %s", prefix, gpu_device)
        clf = train_nnpu(
            X_train, y_train, X_val, y_val,
            feature_name=feature_name, pi_p=pi_p, device=gpu_device,
            epochs=args.epochs, lr=args.lr,
            batch_size=args.nn_batch_size, patience=args.nn_patience,
            scale=scale,
        )

    train_sec = time.time() - t_start
    log.info("%s Training done in %.1f s", prefix, train_sec)

    metrics = evaluate(clf, X_val, y_val)
    log.info(
        "%s AUC-ROC=%.4f  AP=%.4f  P@K=%.4f",
        prefix, metrics["auc_roc"], metrics["avg_precision"], metrics["precision_at_k"],
    )

    record = {
        "timestamp":   run_ts,
        "feature":     feature_name,
        "label":       positive_label,
        "method":      method,
        "C":           C,
        "pi_p":        round(pi_p, 4),
        "n_train_pos": int(y_train.sum()),
        "n_train_unl": int((y_train == 0).sum()),
        "n_val_pos":   int(y_val.sum()),
        "n_val_unl":   int((y_val == 0).sum()),
        "train_sec":   round(train_sec, 1),
        **{k: round(v, 5) for k, v in metrics.items()},
    }

    if not args.no_save:
        MODELS_DIR.mkdir(parents=True, exist_ok=True)
        model_path = MODELS_DIR / f"{feature_name}_{positive_label}_{method}.joblib"
        joblib.dump(clf, model_path)
        log.info("%s Model saved → %s", prefix, model_path)

    if args.top_k > 0:
        show_top_unlabeled(
            clf, feature_name, method, splits,
            n=args.top_k, split=args.top_split, positive_label=positive_label,
        )

    return record


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--features", nargs="+",
        choices=ALL_FEATURES + ["all"], default=["all"],
        help="Feature type(s) to use",
    )
    parser.add_argument(
        "--methods", nargs="+",
        choices=ALL_METHODS + ["all"], default=["all"],
        help="PU Learning method(s) to train",
    )
    parser.add_argument(
        "--labels", nargs="+",
        choices=ALL_LABELS + ["all"], default=["all"],
        help="Positive label(s) to train (default: all)",
    )
    parser.add_argument(
        "--max-unlabeled", type=int, default=None,
        help="Cap unlabeled samples in training set (default: use all)",
    )
    parser.add_argument(
        "--pi-p", type=float, default=None,
        help="Class prior P(y=1). Auto-estimated from split ratios if omitted.",
    )
    parser.add_argument(
        "--C", type=float, default=1.0,
        help="Regularisation strength C (default: 1.0)",
    )
    parser.add_argument(
        "--grid-search", action="store_true",
        help="Run a small grid search over C values before final training",
    )
    parser.add_argument(
        "--no-scale", action="store_true",
        help="Skip StandardScaler (not recommended for SVM)",
    )
    parser.add_argument(
        "--no-save", action="store_true",
        help="Do not save trained models to disk",
    )
    parser.add_argument(
        "--top-k", type=int, default=30,
        help="Show top-K unlabeled images after each model (0 to disable, default: 30)",
    )
    parser.add_argument(
        "--top-split", choices=["train", "val", "test"], default="test",
        help="Which split to score for top-K display (default: test)",
    )
    # ── nnPU-specific ─────────────────────────────────────────────────────────
    parser.add_argument(
        "--epochs", type=int, default=50,
        help="Max training epochs for nnPU (default: 50)",
    )
    parser.add_argument(
        "--lr", type=float, default=1e-3,
        help="Adam learning rate for nnPU (default: 1e-3)",
    )
    parser.add_argument(
        "--nn-batch-size", type=int, default=512,
        help="Mini-batch size for nnPU (default: 512)",
    )
    parser.add_argument(
        "--nn-patience", type=int, default=10,
        help="Early-stopping patience (val AUC) for nnPU (default: 10)",
    )
    parser.add_argument(
        "--gpu-device", type=str, default=None,
        help="PyTorch device for nnPU (default: auto-detect cuda/cpu)",
    )
    parser.add_argument(
        "--workers", type=int, default=1,
        help="Number of parallel worker processes (default: 1). "
             "Note: nnPU (GPU) jobs may contend when workers > 1.",
    )
    args = parser.parse_args()

    features = ALL_FEATURES if "all" in args.features else args.features
    methods  = ALL_METHODS  if "all" in args.methods  else args.methods
    labels   = ALL_LABELS   if "all" in args.labels   else args.labels

    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)

    splits_path = METADATA_DIR / "splits.parquet"
    if not splits_path.exists():
        log.error("splits.parquet not found. Run build_dataset.py first.")
        sys.exit(1)

    combos = list(itertools.product(features, labels, methods))
    log.info("%d combination(s) to train  (workers=%d)", len(combos), args.workers)

    worker_fn = partial(_train_combination, args=args, splits_path=splits_path)

    if args.workers == 1:
        records = [worker_fn(combo) for combo in combos]
    else:
        with concurrent.futures.ProcessPoolExecutor(max_workers=args.workers) as executor:
            records = list(executor.map(worker_fn, combos))

    # ── Final summary ─────────────────────────────────────────────────────────
    if records:
        print_summary(records)
        save_metrics(records)


if __name__ == "__main__":
    main()
