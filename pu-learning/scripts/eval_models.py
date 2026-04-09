#!/usr/bin/env python3
"""
Evaluate trained models on the manually-labeled evaluation dataset.

Evaluates two families of models:

1. Legacy multiclass models (deepdanbooru 6000-dim):
     sklearn-multiclass-ada-boost.joblib     — 3-class (N×3)
     sklearn-multiclass-linear-svc.joblib    — 3-class (N×3) decision_function only
     torch-multiclass-onehot-shallow-network-multilayer
   Classes: 0=not_bookmarked  1=bookmarked_public  2=bookmarked_private
   → column 1 evaluated against pixiv_public, column 2 against pixiv_private

2. PU Learning models:
     {feature}_{label}_{method}.joblib — binary, one per (feature, label, method)
   Feature types: deepdanbooru, eva02, pixai
   Labels:        pixiv_public, pixiv_private, twitter (→ treated as pixiv_public)
   Methods:       elkan_noto, biased_svm, nnpu

Metrics (all three use per-image effective weights):
  wNDCG@K  — Weighted NDCG with graded relevance
  wAUC     — Weighted AUC-ROC
  wAP      — Weighted Average Precision

Relevance grades (wNDCG):
  not_bookmarked        → 0
  positive, no rating   → 2
  positive, rating = 1  → 1
  positive, rating = 2  → 2
  positive, rating = 3  → 4

Effective weight per image:
  rating_w  = {unrated:2, 1:1, 2:2, 3:4}   (positives); 1 (negatives)
  artwork_w = 1 / artwork_group_size        (pixiv); 1.0 (others)
  weight    = rating_w × artwork_w          (applied to BOTH positives and negatives)

Output:
  data/results/eval_metrics.csv          — per-model metrics (overwritten for same model+k)
  data/results/montages/{model}_{label}.png
    — 4-section image per model: top/bottom 10 for positives and negatives

Usage:
  python eval_models.py
  python eval_models.py --k 50
  python eval_models.py --features eva02 --methods nnpu
  python eval_models.py --labels pixiv_public
  python eval_models.py --no-legacy       # skip legacy multiclass models
  python eval_models.py --no-montage      # skip montage generation
  python eval_models.py --thumb-size 200  # thumbnail size in pixels (default: 160)
  python eval_models.py --gpu-device cuda
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
from sklearn.metrics import average_precision_score, roc_auc_score
from sklearn.tree import DecisionTreeClassifier

SCRIPTS_DIR = Path(__file__).parent
sys.path.insert(0, str(SCRIPTS_DIR))
from config import DATA_DIR, FEATURES_DIR, METADATA_DIR

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

MODELS_DIR    = DATA_DIR / "models"
RESULTS_DIR   = DATA_DIR / "results"
EVAL_MANIFEST = METADATA_DIR / "eval_manifest.parquet"

# ── Model discovery constants ─────────────────────────────────────────────────
ALL_FEATURES = ["deepdanbooru", "eva02", "pixai"]
ALL_LABELS   = ["pixiv_public", "pixiv_private", "twitter"]
ALL_METHODS  = ["elkan_noto", "biased_svm", "nnpu"]

_MODEL_RE = re.compile(
    r"^(" + "|".join(ALL_FEATURES) + r")"
    r"_(" + "|".join(ALL_LABELS)   + r")"
    r"_(" + "|".join(ALL_METHODS)  + r")$"
)

# Legacy multiclass: class index → eval_label to evaluate against
LEGACY_CLASS_MAP = {
    1: "pixiv_public",   # bookmarked_public
    2: "pixiv_private",  # bookmarked_private
}

# twitter model evaluated as pixiv_public (public bookmark proxy)
TWITTER_AS_LABEL = "pixiv_public"

# ── Relevance / weight tables ─────────────────────────────────────────────────
RATING_TO_REL    = {1: 1,  2: 2,  3: 4}
DEFAULT_POS_REL  = 2      # positive with no rating
RATING_TO_WEIGHT = {None: 2, 1: 1, 2: 2, 3: 4}


# ── Classes required for joblib deserialisation ───────────────────────────────
# (models were pickled from train_pu.py running as __main__)

class _ScaledClassifier:
    """StandardScaler → base classifier wrapper."""

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
                batch  = torch.from_numpy(X[i : i + 2048]).float().to(self.device)
                logits = self.model(batch).squeeze(1)
                probs.append(torch.sigmoid(logits).cpu().numpy())
        p1 = np.concatenate(probs)
        return np.stack([1.0 - p1, p1], axis=1)


def _fix_sklearn_compat(clf) -> None:
    """Patch models pickled with sklearn <1.4 (missing monotonic_cst on trees)."""
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


# ── Eval feature store ────────────────────────────────────────────────────────

class EvalFeatureStore:
    """Random-access loader for eval_{feature}.h5 files."""

    def __init__(self, feature_name: str):
        self.path = FEATURES_DIR / f"eval_{feature_name}.h5"
        if not self.path.exists():
            raise FileNotFoundError(
                f"Eval feature file not found: {self.path}\n"
                "Run build_eval_dataset.py first."
            )
        with h5py.File(self.path, "r") as f:
            ids = f["image_ids"].asstr()[:]
        self._id_to_row = {iid: i for i, iid in enumerate(ids)}
        log.info("  Loaded eval features '%s': %d images", feature_name, len(ids))

    def load(self, image_ids: list[str]) -> np.ndarray:
        rows = [self._id_to_row.get(iid) for iid in image_ids]
        miss = sum(1 for r in rows if r is None)
        if miss:
            log.warning("  %d / %d ids missing from feature store", miss, len(image_ids))

        valid    = [(i, r) for i, r in enumerate(rows) if r is not None]
        hdf_rows = sorted({r for _, r in valid})

        with h5py.File(self.path, "r") as f:
            data = f["features"][hdf_rows, :].astype(np.float32)
        row_to_local = {r: j for j, r in enumerate(hdf_rows)}

        out = np.zeros((len(image_ids), data.shape[1]), dtype=np.float32)
        for list_i, hdf_r in valid:
            out[list_i] = data[row_to_local[hdf_r]]
        return out


# ── Weight / relevance helpers ────────────────────────────────────────────────

def _artwork_weight(row) -> float:
    gs = row.get("artwork_group_size")
    if pd.isna(gs) or gs <= 1:
        return 1.0
    return 1.0 / float(gs)


WEIGHTING_MODES = ["weighted", "unweighted", "r2plus"]
"""
weighting modes:
  weighted   — rating-based rel/weight × artwork_w  (default)
  unweighted — all positives rel=1, weight=artwork_w (no rating differentiation)
  r2plus     — only rating >= 2 positives; keeps rating weights
"""


def build_eval_subset(
    eval_df: pd.DataFrame,
    eval_label: str,
    weighting: str = "weighted",
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """
    Return (pos_df, neg_df) filtered to the target label pair.

    pos_df : eval images with label == eval_label  (filtered by weighting)
    neg_df : eval images with label == "not_bookmarked"
    Other positive labels are excluded.
    Each DataFrame gets two extra columns: relevance, weight.

    weighting : "weighted" | "unweighted" | "r2plus"
    """
    pos_df = eval_df[eval_df["label"] == eval_label].copy()
    neg_df = eval_df[eval_df["label"] == "not_bookmarked"].copy()

    # ── r2plus: exclude rating=1 positives ────────────────────────────────────
    if weighting == "r2plus":
        is_r1 = pos_df["rating"].notna() & (pos_df["rating"].astype(float).astype(int) == 1)
        pos_df = pos_df[~is_r1].copy()

    # ── Positive relevance and weight ─────────────────────────────────────────
    if weighting == "unweighted":
        pos_df["relevance"] = 1.0
        pos_df["weight"]    = 1.0
    else:
        def _pos_rel(row) -> float:
            r = row["rating"]
            return float(RATING_TO_REL.get(int(r), DEFAULT_POS_REL) if not pd.isna(r) else DEFAULT_POS_REL)

        def _pos_rw(row) -> float:
            r = row["rating"]
            return float(RATING_TO_WEIGHT.get(int(r), RATING_TO_WEIGHT[None]) if not pd.isna(r) else RATING_TO_WEIGHT[None])

        pos_df["relevance"] = pos_df.apply(_pos_rel, axis=1)
        pos_df["weight"]    = pos_df.apply(_pos_rw,  axis=1)

    # ── Negative: rel=0, unit rating weight ──────────────────────────────────
    neg_df["relevance"] = 0.0
    neg_df["weight"]    = 1.0

    # ── Artwork debiasing: applied to both ───────────────────────────────────
    pos_df["weight"] *= pos_df.apply(_artwork_weight, axis=1)
    neg_df["weight"] *= neg_df.apply(_artwork_weight, axis=1)

    return pos_df.reset_index(drop=True), neg_df.reset_index(drop=True)


# ── Weighted NDCG@K ───────────────────────────────────────────────────────────

def weighted_ndcg_at_k(
    relevances: np.ndarray,
    weights: np.ndarray,
    scores: np.ndarray,
    k: int,
) -> float:
    """
    Weighted NDCG@K.

    Each item at rank r contributes:  w_r * (2^rel_r - 1) / log2(r+1)

    The ideal ordering (IDCG) sorts items by  w * (2^rel - 1)  descending.
    """
    n = len(scores)
    k_eff = min(k, n)

    # ── wDCG@K ────────────────────────────────────────────────────────────────
    rank_order = np.argsort(scores)[::-1][:k_eff]
    gains      = (2.0 ** relevances[rank_order] - 1.0) * weights[rank_order]
    discounts  = np.log2(np.arange(1, k_eff + 1, dtype=np.float64) + 1.0)
    wdcg       = float(np.sum(gains / discounts))

    # ── wIDCG@K ───────────────────────────────────────────────────────────────
    all_gains   = (2.0 ** relevances - 1.0) * weights
    ideal_order = np.argsort(all_gains)[::-1][:k_eff]
    ideal_gains = all_gains[ideal_order]
    widcg       = float(np.sum(ideal_gains / np.log2(np.arange(1, len(ideal_gains) + 1, dtype=np.float64) + 1.0)))

    return 0.0 if widcg == 0.0 else wdcg / widcg


# ── Scoring helpers ───────────────────────────────────────────────────────────

def get_binary_scores(clf, X: np.ndarray) -> np.ndarray:
    """Return 1-D positive-class scores from a binary PU model."""
    if hasattr(clf, "predict_proba"):
        return clf.predict_proba(X)[:, 1]
    out = clf.decision_function(X)
    return out if out.ndim == 1 else out[:, 1]


def get_multiclass_scores(clf, X: np.ndarray, class_idx: int) -> np.ndarray:
    """
    Return 1-D scores for one class from a 3-class model.
    Prefer predict_proba column; fall back to decision_function column.
    LinearSVC has no predict_proba.
    """
    if hasattr(clf, "predict_proba"):
        return clf.predict_proba(X)[:, class_idx]
    out = clf.decision_function(X)         # (N, 3)
    return out[:, class_idx]


# ── Compute metrics for one (pos_df, neg_df, scores) triplet ─────────────────

def weighted_precision_at_k(
    w_pos: np.ndarray,
    scores_pos: np.ndarray,
    scores_neg: np.ndarray,
    k: int,
) -> float:
    """
    Weighted Precision@K.

    wPrecision@K = Σ_{i ∈ top-K, positive} w_i  /  K

    The numerator accumulates the effective weight of positives that appear
    in the top-K pool (positives ∪ negatives sorted by score descending).
    Negatives have weight 0 in the numerator.
    Dividing by K gives the precision normalised to [0, max_weight].

    When all weights are 1 this reduces to standard Precision@K.
    """
    n_pos = len(scores_pos)
    n_neg = len(scores_neg)
    n     = n_pos + n_neg
    k_eff = min(k, n)

    all_scores  = np.concatenate([scores_pos, scores_neg])
    all_w_num   = np.concatenate([w_pos, np.zeros(n_neg)])  # negatives contribute 0

    top_idx = np.argsort(all_scores)[::-1][:k_eff]
    return float(all_w_num[top_idx].sum() / k_eff)


def fbeta_at_k(
    w_pos: np.ndarray,
    scores_pos: np.ndarray,
    scores_neg: np.ndarray,
    k: int,
    beta: float,
) -> float:
    """
    Weighted F-beta@K.

    Precision@K and Recall@K are both computed with the same effective weights:

      wPrecision@K = Σ_{i ∈ top-K, positive} w_i  /  K
      wRecall@K    = Σ_{i ∈ top-K, positive} w_i  /  Σ_{all positives} w_i

      F-beta@K = (1 + β²) × P × R  /  (β² × P + R)

    When β < 1 precision is weighted more; β = 1 gives the harmonic mean (F1).
    Returns 0.0 if both precision and recall are 0.
    """
    n_pos = len(scores_pos)
    n_neg = len(scores_neg)
    n     = n_pos + n_neg
    k_eff = min(k, n)

    all_scores = np.concatenate([scores_pos, scores_neg])
    all_w_num  = np.concatenate([w_pos, np.zeros(n_neg)])

    top_idx     = np.argsort(all_scores)[::-1][:k_eff]
    tp_weight   = float(all_w_num[top_idx].sum())
    total_pos_w = float(w_pos.sum())

    prec = tp_weight / k_eff       if k_eff > 0           else 0.0
    rec  = tp_weight / total_pos_w if total_pos_w > 0.0   else 0.0

    denom = beta ** 2 * prec + rec
    if denom == 0.0:
        return 0.0
    return float((1.0 + beta ** 2) * prec * rec / denom)


def compute_metrics(
    pos_df: pd.DataFrame,
    neg_df: pd.DataFrame,
    scores_pos: np.ndarray,
    scores_neg: np.ndarray,
    k: int,
    model_name: str,
    eval_label: str,
    beta_values: tuple[float, ...] = (0.5, 1.0),
) -> dict:
    rel_all  = np.concatenate([pos_df["relevance"].values, neg_df["relevance"].values])
    w_all    = np.concatenate([pos_df["weight"].values,    neg_df["weight"].values])
    s_all    = np.concatenate([scores_pos, scores_neg])
    y_binary = np.concatenate([np.ones(len(pos_df)), np.zeros(len(neg_df))])
    w_pos    = pos_df["weight"].values

    wndcg  = weighted_ndcg_at_k(rel_all, w_all, s_all, k)
    wauc   = float(roc_auc_score(y_binary, s_all, sample_weight=w_all))
    wap    = float(average_precision_score(y_binary, s_all, sample_weight=w_all))
    wprec  = weighted_precision_at_k(w_pos, scores_pos, scores_neg, k)
    fbetas = {
        beta: fbeta_at_k(w_pos, scores_pos, scores_neg, k, beta)
        for beta in beta_values
    }

    # Per-rating median score (informational)
    rating_info = {}
    for r in [1, 2, 3]:
        mask = (pos_df["rating"].fillna(-1).astype(int) == r).values
        if mask.any():
            rating_info[f"n_r{r}"] = int(mask.sum())
            rating_info[f"med_score_r{r}"] = round(float(np.median(scores_pos[mask])), 4)

    fbeta_str = "  ".join(f"wF{b}@{k}={fbetas[b]:.4f}" for b in beta_values)
    log.info(
        "  [%s | %s] wNDCG@%d=%.4f  wAUC=%.4f  wAP=%.4f  wPrec@%d=%.4f  %s  (pos=%d, neg=%d)",
        model_name, eval_label, k, wndcg, wauc, wap, k, wprec, fbeta_str, len(pos_df), len(neg_df),
    )

    record = {
        "model":      model_name,
        "eval_label": eval_label,
        "k":          k,
        "n_pos":      len(pos_df),
        "n_neg":      len(neg_df),
        f"wndcg_at_{k}":  round(wndcg, 5),
        "wauc_roc":        round(wauc,  5),
        "wap":             round(wap,   5),
        f"wprec_at_{k}":  round(wprec, 5),
        **{f"wf{b}_at_{k}": round(fbetas[b], 5) for b in beta_values},
    }
    record.update(rating_info)
    return record


# ── Montage generation ───────────────────────────────────────────────────────

MONTAGE_DIR = RESULTS_DIR / "montages"

# Section colours (R, G, B) — used for section header background
_SEC_COLORS = {
    "pos_high": (20,  70,  20),   # dark green
    "pos_low":  (50,  30,  10),   # dark amber
    "neg_high": (70,  20,  20),   # dark red
    "neg_low":  (20,  20,  60),   # dark blue
}
_SEC_TITLES = {
    "pos_high": "Positive — High score (top 10)",
    "pos_low":  "Positive — Low score (bottom 10)",
    "neg_high": "Negative — High score (top 10)",
    "neg_low":  "Negative — Low score (bottom 10)",
}


def _load_thumb(path: str, size: int) -> "Image.Image":
    from PIL import Image
    try:
        img = Image.open(path).convert("RGB")
        img.thumbnail((size, size), Image.LANCZOS)
    except Exception:
        img = Image.new("RGB", (size, size), (60, 60, 60))
    # centre-pad to size×size
    bg = Image.new("RGB", (size, size), (30, 30, 30))
    bg.paste(img, ((size - img.width) // 2, (size - img.height) // 2))
    return bg


def save_eval_montage(
    pos_df: pd.DataFrame,
    neg_df: pd.DataFrame,
    scores_pos: np.ndarray,
    scores_neg: np.ndarray,
    model_name: str,
    eval_label: str,
    out_path: Path,
    n: int = 10,
    thumb_size: int = 160,
) -> None:
    """
    Save a 4-section montage image for one (model, eval_label) combination.

    Layout (each section = n thumbnails in a single row):
      ┌─────────────────────────────────────────┐
      │  Positive — High score (top n)          │
      ├─────────────────────────────────────────┤
      │  Positive — Low score  (bottom n)       │
      ├─────────────────────────────────────────┤
      │  Negative — High score (top n)          │
      ├─────────────────────────────────────────┤
      │  Negative — Low score  (bottom n)       │
      └─────────────────────────────────────────┘

    Each cell shows the image thumbnail with a footer label:
      rank · score · rating (for positives)
    """
    from PIL import Image, ImageDraw, ImageFont

    try:
        font_sm = ImageFont.truetype(
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 11)
        font_hd = ImageFont.truetype(
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 13)
    except OSError:
        font_sm = font_hd = ImageFont.load_default()

    HEADER_H  = 26   # section header strip height
    FOOTER_H  = 18   # per-cell score footer height
    GAP       = 3    # gap between cells
    SIDE_PAD  = 6    # left/right padding inside section

    cell_h    = thumb_size + FOOTER_H
    n_cols    = n
    sec_w     = SIDE_PAD * 2 + n_cols * thumb_size + (n_cols - 1) * GAP
    sec_h     = HEADER_H + cell_h
    total_h   = 4 * sec_h + 3 * GAP     # 4 sections + 3 inter-section gaps

    canvas = Image.new("RGB", (sec_w, total_h), (15, 15, 15))
    draw   = ImageDraw.Draw(canvas)

    def _draw_section(
        section_key: str,
        file_paths: list[str],
        section_scores: list[float],
        ratings: list[int | None],   # None for negatives
        y_offset: int,
    ) -> None:
        color = _SEC_COLORS[section_key]
        title = _SEC_TITLES[section_key]

        # Header bar
        draw.rectangle([0, y_offset, sec_w, y_offset + HEADER_H], fill=color)
        draw.text((SIDE_PAD, y_offset + 5), title, fill=(230, 230, 230), font=font_hd)

        y_img = y_offset + HEADER_H
        for col, (fpath, score, rating) in enumerate(zip(file_paths, section_scores, ratings)):
            x = SIDE_PAD + col * (thumb_size + GAP)
            thumb = _load_thumb(fpath, thumb_size)
            canvas.paste(thumb, (x, y_img))

            # Footer bar
            draw.rectangle(
                [x, y_img + thumb_size, x + thumb_size, y_img + thumb_size + FOOTER_H],
                fill=(20, 20, 20),
            )
            if rating is not None:
                label_text = f"#{col+1} {score:.3f} r{rating}"
                txt_color  = (100, 255, 100) if rating == 3 else (200, 200, 100)
            else:
                label_text = f"#{col+1} {score:.3f}"
                txt_color  = (200, 200, 200)
            draw.text(
                (x + 3, y_img + thumb_size + 2),
                label_text,
                fill=txt_color,
                font=font_sm,
            )

    def _top_n_info(df: pd.DataFrame, scores: np.ndarray, ascending: bool) -> tuple:
        idx    = np.argsort(scores) if ascending else np.argsort(scores)[::-1]
        idx    = idx[:n]
        paths  = df.iloc[idx]["file_path"].tolist()
        sc     = scores[idx].tolist()
        if "rating" in df.columns:
            rat = [
                (None if pd.isna(r) else int(r))
                for r in df.iloc[idx]["rating"]
            ]
        else:
            rat = [None] * len(idx)
        return paths, sc, rat

    sections = [
        ("pos_high", pos_df, scores_pos, False),
        ("pos_low",  pos_df, scores_pos, True),
        ("neg_high", neg_df, scores_neg, False),
        ("neg_low",  neg_df, scores_neg, True),
    ]

    for s_idx, (sec_key, df, sc, asc) in enumerate(sections):
        paths, s_vals, rats = _top_n_info(df, sc, ascending=asc)
        y = s_idx * (sec_h + GAP)
        _draw_section(sec_key, paths, s_vals, rats, y)

    MONTAGE_DIR.mkdir(parents=True, exist_ok=True)
    canvas.save(str(out_path))
    log.info("  Montage saved → %s", out_path)


# ── Legacy PyTorch network ────────────────────────────────────────────────────

def _load_torch_network(device: str):
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

    torch_path = MODELS_DIR / "torch-multiclass-onehot-shallow-network-multilayer"
    if not torch_path.exists():
        return None
    model = Network()
    state = torch.load(str(torch_path), map_location=device, weights_only=False)
    model.load_state_dict(state)
    model.to(device).eval()
    return model


class TorchNetworkWrapper:
    """decision_function returns raw logits (N×3)."""

    def __init__(self, model, device: str, batch_size: int = 2048):
        self.model      = model
        self.device     = device
        self.batch_size = batch_size

    def decision_function(self, X: np.ndarray) -> np.ndarray:
        import torch
        chunks = []
        with torch.no_grad():
            for i in range(0, len(X), self.batch_size):
                t = torch.from_numpy(X[i : i + self.batch_size]).float().to(self.device)
                chunks.append(self.model(t).cpu().numpy())
        return np.concatenate(chunks, axis=0)   # (N, 3)


# ── Model discovery ───────────────────────────────────────────────────────────

def discover_pu_models(
    filter_features: list[str],
    filter_labels: list[str],
    filter_methods: list[str],
) -> list[tuple[str, str, str, Path]]:
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


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--k", type=int, default=100,
                        help="K for wNDCG@K (default: 100)")
    parser.add_argument("--features", nargs="+",
                        choices=ALL_FEATURES + ["all"], default=["all"])
    parser.add_argument("--labels", nargs="+",
                        choices=ALL_LABELS + ["all"], default=["all"])
    parser.add_argument("--methods", nargs="+",
                        choices=ALL_METHODS + ["all"], default=["all"])
    parser.add_argument("--no-legacy", action="store_true",
                        help="Skip legacy multiclass models")
    parser.add_argument("--no-montage", action="store_true",
                        help="Skip montage image generation")
    parser.add_argument("--thumb-size", type=int, default=160,
                        help="Thumbnail size in pixels for montage (default: 160)")
    parser.add_argument("--gpu-device", type=str, default=None,
                        help="PyTorch device for nnPU / torch models (default: auto)")
    parser.add_argument("--weighting", nargs="+",
                        choices=WEIGHTING_MODES + ["all"], default=["all"],
                        help="Weighting mode(s) to evaluate (default: all)")
    args = parser.parse_args()

    filter_features  = ALL_FEATURES    if "all" in args.features  else args.features
    filter_labels    = ALL_LABELS      if "all" in args.labels    else args.labels
    filter_methods   = ALL_METHODS     if "all" in args.methods   else args.methods
    weighting_modes  = WEIGHTING_MODES if "all" in args.weighting else args.weighting

    # ── Load eval manifest ────────────────────────────────────────────────────
    if not EVAL_MANIFEST.exists():
        log.error("eval_manifest.parquet not found: %s\nRun build_eval_dataset.py first.",
                  EVAL_MANIFEST)
        sys.exit(1)

    eval_df = pd.read_parquet(EVAL_MANIFEST)
    log.info("Eval manifest: %d images", len(eval_df))
    for lbl, cnt in eval_df["label"].value_counts().items():
        log.info("  %-20s : %d", lbl, cnt)
    pos_df_info = eval_df[eval_df["label"] != "not_bookmarked"]
    log.info("Positive rating distribution: %s",
             pos_df_info["rating"].value_counts(dropna=False).to_dict())

    # ── GPU device ────────────────────────────────────────────────────────────
    pu_models = discover_pu_models(filter_features, filter_labels, filter_methods)
    need_gpu  = (not args.no_legacy) or any(m == "nnpu" for _, _, m, _ in pu_models)
    if need_gpu:
        import torch
        gpu_device = args.gpu_device or ("cuda" if torch.cuda.is_available() else "cpu")
        log.info("PyTorch device: %s", gpu_device)
    else:
        gpu_device = "cpu"

    # ── Feature store cache ───────────────────────────────────────────────────
    _store_cache: dict[str, EvalFeatureStore] = {}

    def get_store(name: str) -> EvalFeatureStore:
        if name not in _store_cache:
            _store_cache[name] = EvalFeatureStore(name)
        return _store_cache[name]

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    all_records: list[dict] = []

    # ── Pre-load legacy models (outside weighting loop to avoid re-loading) ────
    legacy_models: list[tuple[str, object]] = []
    legacy_scores_cache: dict[str, tuple[np.ndarray, list[str]]] = {}

    if not args.no_legacy:
        store_dd = get_store("deepdanbooru")
        legacy_sklearn = sorted(MODELS_DIR.glob("sklearn-multiclass-*.joblib"))
        torch_path     = MODELS_DIR / "torch-multiclass-onehot-shallow-network-multilayer"

        for p in legacy_sklearn:
            clf = joblib.load(p)
            _fix_sklearn_compat(clf)
            legacy_models.append((p.stem, clf))

        if torch_path.exists():
            torch_net = _load_torch_network(gpu_device)
            if torch_net is not None:
                legacy_models.append((
                    "torch-multiclass-onehot-shallow-network",
                    TorchNetworkWrapper(torch_net, device=gpu_device),
                ))

        # Score the entire eval set once per model (reused across weighting modes)
        all_ids = eval_df["image_id"].tolist()
        X_all   = store_dd.load(all_ids)
        id_to_score_idx = {iid: i for i, iid in enumerate(all_ids)}

        for model_name, clf in legacy_models:
            log.info("Pre-scoring legacy model: %s", model_name)
            scores_matrix = (
                clf.predict_proba(X_all)
                if hasattr(clf, "predict_proba")
                else clf.decision_function(X_all)
            )     # (N, 3)
            legacy_scores_cache[model_name] = (scores_matrix, all_ids)

    # ── Pre-load PU models (outside weighting loop) ────────────────────────────
    pu_models_loaded: list[tuple[str, str, str, str, object]] = []
    pu_scores_cache: dict[str, tuple[np.ndarray, np.ndarray, str, pd.DataFrame, pd.DataFrame]] = {}

    log.info("PU models to evaluate: %d", len(pu_models))
    for feature, label, method, path in pu_models:
        log.info("Pre-loading PU model: %s", path.stem)
        clf = joblib.load(path)
        _fix_sklearn_compat(clf)
        if isinstance(clf, NNPUClassifier) and gpu_device is not None:
            clf.model  = clf.model.to(gpu_device)
            clf.device = gpu_device
        eval_label = TWITTER_AS_LABEL if label == "twitter" else label
        pu_models_loaded.append((feature, label, method, path.stem, clf))

        # Cache raw scores per image_id (reused across weighting modes)
        # We score the full positive+negative pool for the eval_label
        pos_df_all, neg_df_all = build_eval_subset(eval_df, eval_label, weighting="weighted")
        store = get_store(feature)
        X_pos = store.load(pos_df_all["image_id"].tolist())
        X_neg = store.load(neg_df_all["image_id"].tolist())
        sp = get_binary_scores(clf, X_pos)
        sn = get_binary_scores(clf, X_neg)
        pu_scores_cache[path.stem] = (sp, sn, eval_label, pos_df_all, neg_df_all)

    # ═══════════════════════════════════════════════════════════════════════════
    # Main evaluation loop — once per weighting mode
    # ═══════════════════════════════════════════════════════════════════════════
    for weighting in weighting_modes:
        log.info("\n" + "=" * 70)
        log.info("  WEIGHTING MODE: %s", weighting.upper())
        log.info("=" * 70)

        # ── 1. Legacy multiclass models ───────────────────────────────────────
        if not args.no_legacy:
            for model_name, clf in legacy_models:
                log.info("=" * 60)
                log.info("Legacy model: %s  [%s]", model_name, weighting)
                log.info("=" * 60)

                scores_matrix, all_ids = legacy_scores_cache[model_name]

                for class_idx, eval_label in LEGACY_CLASS_MAP.items():
                    pos_df, neg_df = build_eval_subset(eval_df, eval_label, weighting=weighting)
                    if pos_df.empty:
                        continue

                    scores_pos = scores_matrix[
                        [id_to_score_idx[i] for i in pos_df["image_id"]], class_idx
                    ]
                    scores_neg = scores_matrix[
                        [id_to_score_idx[i] for i in neg_df["image_id"]], class_idx
                    ]

                    record = compute_metrics(
                        pos_df, neg_df, scores_pos, scores_neg,
                        args.k, model_name, eval_label,
                    )
                    record["feature"]     = "deepdanbooru"
                    record["model_label"] = eval_label
                    record["method"]      = "legacy"
                    record["weighting"]   = weighting
                    all_records.append(record)

                    if not args.no_montage and weighting == "weighted":
                        out_png = MONTAGE_DIR / f"{model_name}_{eval_label}.png"
                        save_eval_montage(
                            pos_df, neg_df, scores_pos, scores_neg,
                            model_name, eval_label, out_png,
                            thumb_size=args.thumb_size,
                        )

        # ── 2. PU Learning models ─────────────────────────────────────────────
        for feature, label, method, model_stem, clf in pu_models_loaded:
            log.info("=" * 60)
            log.info("PU model: %s  [%s]", model_stem, weighting)
            log.info("=" * 60)

            eval_label = TWITTER_AS_LABEL if label == "twitter" else label

            # Re-build subset with this weighting mode
            pos_df, neg_df = build_eval_subset(eval_df, eval_label, weighting=weighting)
            if pos_df.empty:
                log.warning("  No positives for %s / %s, skipping", eval_label, weighting)
                continue

            # Reuse cached raw scores, aligned to this weighting's subset
            sp_all, sn_all, _, pos_df_all, neg_df_all = pu_scores_cache[model_stem]
            pos_id_to_score = dict(zip(pos_df_all["image_id"], sp_all))
            neg_id_to_score = dict(zip(neg_df_all["image_id"], sn_all))

            scores_pos = np.array([pos_id_to_score[i] for i in pos_df["image_id"]])
            scores_neg = np.array([neg_id_to_score[i] for i in neg_df["image_id"]])

            record = compute_metrics(
                pos_df, neg_df, scores_pos, scores_neg,
                args.k, model_stem, eval_label,
            )
            record["feature"]     = feature
            record["model_label"] = label
            record["method"]      = method
            record["weighting"]   = weighting
            all_records.append(record)

            if not args.no_montage and weighting == "weighted":
                out_png = MONTAGE_DIR / f"{model_stem}_{eval_label}.png"
                save_eval_montage(
                    pos_df, neg_df, scores_pos, scores_neg,
                    model_stem, eval_label, out_png,
                    thumb_size=args.thumb_size,
                )

    # ── Save results ──────────────────────────────────────────────────────────
    if not all_records:
        log.warning("No results produced.")
        return

    df_new = pd.DataFrame(all_records)
    metrics_path = RESULTS_DIR / "eval_metrics.csv"

    if metrics_path.exists():
        df_existing = pd.read_csv(metrics_path)
        # Remove stale rows for same (model, k, weighting) combinations
        # Handle old CSVs that may lack 'weighting' column
        if "weighting" not in df_existing.columns:
            df_existing["weighting"] = "weighted"
        new_keys = set(zip(df_new["model"], df_new["k"], df_new["weighting"]))
        mask_stale = pd.Series(
            list(zip(df_existing["model"], df_existing["k"], df_existing["weighting"]))
        ).isin(new_keys).values
        df_keep = df_existing[~mask_stale]
        df_out = pd.concat([df_keep, df_new], ignore_index=True)
    else:
        df_out = df_new

    df_out.to_csv(metrics_path, index=False)
    log.info("Metrics saved → %s", metrics_path)

    # ── Summary tables ────────────────────────────────────────────────────────
    ndcg_col  = f"wndcg_at_{args.k}"
    prec_col  = f"wprec_at_{args.k}"
    f05_col   = f"wf0.5_at_{args.k}"
    f1_col    = f"wf1.0_at_{args.k}"

    log.info("\n%s", "=" * 80)
    log.info("  EVALUATION SUMMARY  (sorted by wNDCG@%d, weighting=weighted)", args.k)
    log.info("%s", "=" * 80)

    df_w = df_new[df_new["weighting"] == "weighted"] if "weighted" in weighting_modes else df_new
    display_cols = [
        c for c in ["model", "eval_label", ndcg_col, "wauc_roc", "wap",
                    prec_col, f05_col, f1_col, "n_pos"]
        if c in df_w.columns
    ]
    summary = df_w[display_cols].sort_values(ndcg_col, ascending=False)
    log.info("\n%s", summary.to_string(index=False, float_format="%.4f"))

    # ── Cross-weighting comparison table ─────────────────────────────────────
    if len(weighting_modes) > 1:
        log.info("\n%s", "=" * 80)
        log.info("  CROSS-WEIGHTING COMPARISON  (wNDCG@%d / wPrec@%d / wF1@%d)", args.k, args.k, args.k)
        log.info("%s", "=" * 80)

        for eval_lbl in sorted(df_new["eval_label"].unique()):
            subset = df_new[df_new["eval_label"] == eval_lbl]
            for metric_col, metric_label in [
                (ndcg_col, f"wNDCG@{args.k}"),
                (prec_col, f"wPrec@{args.k}"),
                (f1_col,   f"wF1@{args.k}"),
            ]:
                if metric_col not in subset.columns:
                    continue
                pivot = subset.pivot_table(
                    index="model",
                    columns="weighting",
                    values=metric_col,
                    aggfunc="first",
                )
                ordered_cols = [c for c in WEIGHTING_MODES if c in pivot.columns]
                pivot = pivot[ordered_cols]
                sort_col = "weighted" if "weighted" in pivot.columns else pivot.columns[0]
                pivot = pivot.sort_values(sort_col, ascending=False)
                log.info("\n── %s  %s (by weighting mode) ──", eval_lbl, metric_label)
                log.info("\n%s", pivot.to_string(float_format="%.4f"))

    log.info("\nDone. Results → %s", metrics_path)


if __name__ == "__main__":
    main()
