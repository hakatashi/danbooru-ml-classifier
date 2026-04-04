#!/usr/bin/env python3
"""
Analyse feature importance / tag contributions for trained classifiers.

PU Learning models (biased_svm, elkan_noto, nnpu):
  deepdanbooru / pixai features → tag-name mapped coefficients or input gradients.
  eva02 features are 1024-dim latent embeddings with no tag labels → skipped.

Legacy sklearn multiclass models:
  sklearn-multiclass-linear-svc  → coef_ (3 classes × 6000 tags)
  sklearn-multiclass-ada-boost   → feature_importances_ (6000 tags)

Legacy PyTorch multiclass model:
  torch-multiclass-onehot-shallow-network-multilayer
  → mean signed input gradient over a sample of unlabeled deepdanbooru features.

Outputs:
  data/results/feature_importance_{model}_{positive/negative}.csv
  Console table with top-K positive and negative tags

Usage:
  python feature_importance.py
  python feature_importance.py --top-k 30
  python feature_importance.py --features deepdanbooru --methods biased_svm
  python feature_importance.py --labels pixiv_public --top-k 50
  python feature_importance.py --legacy          # only legacy models
  python feature_importance.py --legacy --top-k 30
"""

import argparse
import json
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
    PIXAI_MODEL_DIR,
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

ALL_FEATURES = ["deepdanbooru", "pixai"]  # eva02 excluded (latent embeddings)
ALL_LABELS   = ["pixiv_public", "pixiv_private", "twitter"]
ALL_METHODS  = ["elkan_noto", "biased_svm", "nnpu"]

_MODEL_RE = re.compile(
    r"^(deepdanbooru|eva02|pixai)"
    r"_(pixiv_public|pixiv_private|twitter)"
    r"_(elkan_noto|biased_svm|nnpu)$"
)

# ── Unpickling stubs (models were pickled as __main__ classes) ────────────────

class _ScaledClassifier:
    def __init__(self, scaler, clf):
        self.scaler = scaler
        self.clf    = clf

    def predict_proba(self, X):
        return self.clf.predict_proba(self.scaler.transform(X))

    def decision_function(self, X):
        return self.clf.decision_function(self.scaler.transform(X))

    def predict(self, X):
        return self.clf.predict(self.scaler.transform(X))


class NNPUClassifier:
    def __init__(self, model, scaler, device):
        self.model  = model
        self.scaler = scaler
        self.device = device

    def predict_proba(self, X):
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


# ── Tag name loading ──────────────────────────────────────────────────────────

def load_deepdanbooru_tags() -> list[str]:
    """Load DeepDanbooru tag list (6000 names, index = feature dimension)."""
    path = METADATA_DIR / "deepdanbooru_tags.json"
    if not path.exists():
        raise FileNotFoundError(
            f"DeepDanbooru tag list not found: {path}\n"
            "  Download from: https://raw.githubusercontent.com/RF5/danbooru-pretrained/"
            "master/config/class_names_6000.json"
        )
    with path.open() as f:
        tags = json.load(f)
    log.info("  DeepDanbooru tags: %d", len(tags))
    return tags


def load_pixai_tags() -> list[str]:
    """Load PixAI tag list (13461 names, index = feature dimension)."""
    path = PIXAI_MODEL_DIR / "tags_v0.9_13k.json"
    if not path.exists():
        raise FileNotFoundError(
            f"PixAI tag list not found: {path}\n"
            "  Run worker/vlm_captioner.py --models pixai to download the model."
        )
    with path.open() as f:
        d = json.load(f)
    tag_map = d["tag_map"]  # {name: index}
    idx_to_tag = [""] * len(tag_map)
    for name, idx in tag_map.items():
        idx_to_tag[idx] = name
    log.info("  PixAI tags: %d", len(idx_to_tag))
    return idx_to_tag


def get_tag_names(feature: str) -> list[str]:
    if feature == "deepdanbooru":
        return load_deepdanbooru_tags()
    if feature == "pixai":
        return load_pixai_tags()
    raise ValueError(f"No tag names available for feature type '{feature}'")


# ── Coefficient extraction ────────────────────────────────────────────────────

def extract_coef_elkan_noto(clf: _ScaledClassifier) -> np.ndarray:
    """Return 1-D coefficient array from the inner LogisticRegression."""
    inner = clf.clf  # ElkanotoPuClassifier
    # pulearn stores the fitted estimator as .estimator (not .estimator_)
    lr = inner.estimator
    return lr.coef_.ravel()


def extract_coef_biased_svm(clf: _ScaledClassifier) -> np.ndarray:
    """
    Return 1-D coefficient array from the inner LinearSVC.
    CalibratedClassifierCV may have multiple CV folds; average them.
    """
    cal = clf.clf  # CalibratedClassifierCV
    coefs = []
    for cal_clf in cal.calibrated_classifiers_:
        svc = cal_clf.estimator  # LinearSVC
        coefs.append(svc.coef_.ravel())
    return np.mean(coefs, axis=0)


def extract_importance_nnpu(
    clf: NNPUClassifier,
    X_sample: np.ndarray,
    device: str,
) -> np.ndarray:
    """
    Compute mean absolute input gradient over X_sample.

    grad_i = ∂σ(f(x)) / ∂x_i  averaged over all sample images.
    High |grad_i| means the model is sensitive to feature i.
    Returns signed mean gradient (positive = increases score).
    """
    import torch

    model  = clf.model.to(device)
    scaler = clf.scaler

    if scaler is not None:
        X_scaled = scaler.transform(X_sample).astype(np.float32)
    else:
        X_scaled = X_sample.astype(np.float32)

    model.eval()
    grads = []
    bs    = 256
    for i in range(0, len(X_scaled), bs):
        batch = torch.from_numpy(X_scaled[i:i+bs]).float().to(device)
        batch.requires_grad_(True)
        logits = model(batch).squeeze(1)
        probs  = torch.sigmoid(logits)
        probs.sum().backward()
        grads.append(batch.grad.cpu().numpy())

    grads_all = np.concatenate(grads, axis=0)  # (N, dim)
    return grads_all.mean(axis=0)              # signed mean gradient


# ── Feature loading (for nnpu sample) ────────────────────────────────────────

def load_unlabeled_sample(feature_name: str, n: int = 2000) -> np.ndarray:
    """Load up to n unlabeled training images for gradient attribution."""
    splits_path = METADATA_DIR / "splits.parquet"
    if not splits_path.exists():
        raise FileNotFoundError("splits.parquet not found. Run build_dataset.py first.")
    splits = pd.read_parquet(splits_path)
    unl    = splits[
        (splits["split"] == "train") & (splits["label"] == UNLABELED_LABEL)
    ].head(n)
    image_ids = unl["image_id"].tolist()

    h5_path = FEATURES_DIR / f"{feature_name}.h5"
    with h5py.File(h5_path, "r") as f:
        ids_all = f["image_ids"].asstr()[:]
        id_to_row = {iid: i for i, iid in enumerate(ids_all)}
        rows = [id_to_row[iid] for iid in image_ids if iid in id_to_row]
        data = f["features"][sorted(rows), :].astype(np.float32)
    log.info("  Loaded %d unlabeled samples for gradient attribution", len(rows))
    return data


# ── Legacy model helpers ──────────────────────────────────────────────────────

LEGACY_MULTICLASS_CLASSES = ["not_bookmarked", "bookmarked_public", "bookmarked_private"]


def extract_coef_legacy_linear_svc(clf) -> dict[str, np.ndarray]:
    """
    Return {class_name: coef_1d} from a legacy sklearn LinearSVC (3 classes × 6000 tags).
    """
    # coef_ shape: (n_classes, n_features)  — OvR for multiclass
    return {
        cls: clf.coef_[i].ravel()
        for i, cls in enumerate(LEGACY_MULTICLASS_CLASSES)
    }


def extract_importance_legacy_adaboost(clf) -> np.ndarray:
    """
    Return aggregated feature_importances_ from AdaBoostClassifier.
    This is a single 1-D array; high values = tag used frequently in splits.
    """
    return clf.feature_importances_.ravel()


def extract_importance_torch_network(
    model_path: Path,
    X_sample: np.ndarray,
    device: str,
) -> dict[str, np.ndarray]:
    """
    Load the legacy PyTorch shallow network and compute mean signed input gradient
    for each of the 3 output classes.

    Architecture: Linear(6000→512) ReLU → Linear(512→128) ReLU →
                  Linear(128→128) ReLU → Linear(128→3)
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
    state = torch.load(str(model_path), map_location=device)
    model.load_state_dict(state)
    model.to(device).eval()

    X_t = torch.from_numpy(X_sample.astype(np.float32))
    grads_per_class: dict[str, np.ndarray] = {}

    for class_idx, class_name in enumerate(LEGACY_MULTICLASS_CLASSES):
        grads = []
        bs    = 256
        for i in range(0, len(X_t), bs):
            batch = X_t[i:i+bs].to(device).requires_grad_(True)
            logits = model(batch)          # (B, 3)
            logits[:, class_idx].sum().backward()
            grads.append(batch.grad.detach().cpu().numpy())
        grads_per_class[class_name] = np.concatenate(grads, axis=0).mean(axis=0)

    return grads_per_class


# ── Formatting helpers ────────────────────────────────────────────────────────

def top_k_table(coef: np.ndarray, tag_names: list[str], k: int) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Return (positive_df, negative_df) DataFrames with top-k tags by coefficient."""
    pos_idx = np.argsort(coef)[::-1][:k]
    neg_idx = np.argsort(coef)[:k]

    pos_df = pd.DataFrame({
        "rank":      range(1, k + 1),
        "tag":       [tag_names[i] for i in pos_idx],
        "coef":      [round(float(coef[i]), 6) for i in pos_idx],
        "feat_idx":  pos_idx,
    })
    neg_df = pd.DataFrame({
        "rank":      range(1, k + 1),
        "tag":       [tag_names[i] for i in neg_idx],
        "coef":      [round(float(coef[i]), 6) for i in neg_idx],
        "feat_idx":  neg_idx,
    })
    return pos_df, neg_df


def print_table(df: pd.DataFrame, title: str) -> None:
    log.info("")
    log.info("  ── %s ──", title)
    log.info("  %s", df[["rank", "tag", "coef"]].to_string(index=False))


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--top-k", type=int, default=20,
        help="Number of top/bottom tags to report per model (default: 20)",
    )
    parser.add_argument(
        "--features", nargs="+",
        choices=ALL_FEATURES + ["all"], default=["all"],
        help="Feature type(s) to analyse (default: all; eva02 excluded as it has no tag labels)",
    )
    parser.add_argument(
        "--labels", nargs="+",
        choices=ALL_LABELS + ["all"], default=["all"],
    )
    parser.add_argument(
        "--methods", nargs="+",
        choices=ALL_METHODS + ["all"], default=["all"],
    )
    parser.add_argument(
        "--nnpu-samples", type=int, default=2000,
        help="Number of unlabeled training images to use for nnpu/torch gradient attribution (default: 2000)",
    )
    parser.add_argument(
        "--gpu-device", type=str, default=None,
        help="PyTorch device for nnpu/torch gradient computation (default: auto)",
    )
    parser.add_argument(
        "--legacy", action="store_true",
        help="Also analyse legacy sklearn and PyTorch multiclass models",
    )
    parser.add_argument(
        "--legacy-only", action="store_true",
        help="Analyse only legacy models (skip PU learning models)",
    )
    args = parser.parse_args()

    filter_features = ALL_FEATURES if "all" in args.features else args.features
    filter_labels   = ALL_LABELS   if "all" in args.labels   else args.labels
    filter_methods  = ALL_METHODS  if "all" in args.methods  else args.methods

    run_legacy = args.legacy or args.legacy_only
    run_pu     = not args.legacy_only

    # Detect GPU
    need_torch = ("nnpu" in filter_methods and run_pu) or run_legacy
    if need_torch:
        import torch
        gpu_device = args.gpu_device or ("cuda" if torch.cuda.is_available() else "cpu")
        log.info("PyTorch device: %s", gpu_device)
    else:
        gpu_device = None

    # ── Tag name cache ────────────────────────────────────────────────────────
    tag_cache: dict[str, list[str]] = {}
    def get_tags(feature: str) -> list[str]:
        if feature not in tag_cache:
            tag_cache[feature] = get_tag_names(feature)
        return tag_cache[feature]

    # ── Feature sample cache (for nnpu / torch gradient) ─────────────────────
    sample_cache: dict[str, np.ndarray] = {}
    def get_sample(feature: str) -> np.ndarray:
        if feature not in sample_cache:
            sample_cache[feature] = load_unlabeled_sample(feature, args.nnpu_samples)
        return sample_cache[feature]

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    all_rows: list[dict] = []

    def _save_coef(coef: np.ndarray, tag_names: list[str], model_name: str,
                   extra: dict) -> None:
        """Build top-k tables, print, and save CSVs. Mutates all_rows."""
        pos_df, neg_df = top_k_table(coef, tag_names, args.top_k)
        print_table(pos_df, f"Top {args.top_k} POSITIVE tags  [{model_name}]")
        print_table(neg_df, f"Top {args.top_k} NEGATIVE tags  [{model_name}]")
        for direction, df in [("positive", pos_df), ("negative", neg_df)]:
            for k, v in extra.items():
                df[k] = v
            df["direction"] = direction
            out = RESULTS_DIR / f"feature_importance_{model_name}_{direction}.csv"
            df.to_csv(out, index=False)
            log.info("  Saved → %s", out)
            all_rows.append(df)

    # ═══════════════════════════════════════════════════════════════════════════
    # 1. PU Learning models
    # ═══════════════════════════════════════════════════════════════════════════
    if run_pu:
        models_to_run = []
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
            models_to_run.append((feature, label, method, p))

        log.info("PU models to analyse: %d", len(models_to_run))
        for feature, label, method, p in models_to_run:
            log.info("  %s", p.name)

        for feature, label, method, model_path in models_to_run:
            model_name = f"{feature}_{label}_{method}"
            log.info("=" * 60)
            log.info("PU Model: %s", model_name)
            log.info("=" * 60)

            clf = joblib.load(model_path)
            tag_names = get_tags(feature)

            coef: np.ndarray | None = None

            if method == "elkan_noto":
                try:
                    coef = extract_coef_elkan_noto(clf)
                    log.info("  Method: LogisticRegression coef (shape=%s)", coef.shape)
                except Exception as e:
                    log.warning("  Could not extract elkan_noto coef: %s", e)

            elif method == "biased_svm":
                try:
                    coef = extract_coef_biased_svm(clf)
                    log.info("  Method: LinearSVC coef, avg over %d CV folds (shape=%s)",
                             len(clf.clf.calibrated_classifiers_), coef.shape)
                except Exception as e:
                    log.warning("  Could not extract biased_svm coef: %s", e)

            elif method == "nnpu":
                try:
                    X_sample = get_sample(feature)
                    nnpu_device = gpu_device or "cpu"
                    if isinstance(clf, NNPUClassifier):
                        clf.model = clf.model.to(nnpu_device)
                        clf.device = nnpu_device
                    coef = extract_importance_nnpu(clf, X_sample, nnpu_device)
                    log.info("  Method: mean signed input gradient over %d samples (shape=%s)",
                             len(X_sample), coef.shape)
                except Exception as e:
                    log.warning("  Could not compute nnpu gradient attribution: %s", e)

            if coef is None:
                log.warning("  Skipping %s (no coef extracted)", model_name)
                continue

            assert len(coef) == len(tag_names), (
                f"coef length {len(coef)} != tag count {len(tag_names)}"
            )
            _save_coef(coef, tag_names, model_name,
                       {"model": model_name, "feature": feature,
                        "label": label, "method": method})

    # ═══════════════════════════════════════════════════════════════════════════
    # 2. Legacy models
    # ═══════════════════════════════════════════════════════════════════════════
    if run_legacy:
        dd_tags = get_tags("deepdanbooru")

        # ── sklearn LinearSVC (multiclass, 3 classes × 6000) ─────────────────
        lsvc_path = MODELS_DIR / "sklearn-multiclass-linear-svc.joblib"
        if lsvc_path.exists():
            log.info("=" * 60)
            log.info("Legacy Model: %s", lsvc_path.stem)
            log.info("=" * 60)
            import warnings
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                clf = joblib.load(lsvc_path)
            class_coefs = extract_coef_legacy_linear_svc(clf)
            for class_name, coef in class_coefs.items():
                model_name = f"legacy-linear-svc_{class_name}"
                log.info("  Class: %s  coef shape=%s", class_name, coef.shape)
                _save_coef(coef, dd_tags, model_name,
                           {"model": model_name, "feature": "deepdanbooru",
                            "label": class_name, "method": "linear_svc"})
        else:
            log.warning("Legacy LinearSVC not found: %s", lsvc_path)

        # ── AdaBoost (multiclass, feature_importances_) ───────────────────────
        ada_path = MODELS_DIR / "sklearn-multiclass-ada-boost.joblib"
        if ada_path.exists():
            log.info("=" * 60)
            log.info("Legacy Model: %s", ada_path.stem)
            log.info("=" * 60)
            import warnings
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                clf = joblib.load(ada_path)
            fi = extract_importance_legacy_adaboost(clf)
            model_name = "legacy-ada-boost"
            log.info("  feature_importances_ shape=%s", fi.shape)
            # AdaBoost importance is non-negative; use it as "positive" importance
            _save_coef(fi, dd_tags, model_name,
                       {"model": model_name, "feature": "deepdanbooru",
                        "label": "all_classes", "method": "ada_boost"})
        else:
            log.warning("Legacy AdaBoost not found: %s", ada_path)

        # ── PyTorch shallow network (multiclass, 3 classes) ───────────────────
        torch_path = MODELS_DIR / "torch-multiclass-onehot-shallow-network-multilayer"
        if torch_path.exists():
            log.info("=" * 60)
            log.info("Legacy Model: torch-multiclass-onehot-shallow-network")
            log.info("=" * 60)
            torch_device = gpu_device or "cpu"
            X_sample = get_sample("deepdanbooru")
            log.info("  Computing mean input gradients over %d samples …", len(X_sample))
            class_grads = extract_importance_torch_network(torch_path, X_sample, torch_device)
            for class_name, coef in class_grads.items():
                model_name = f"legacy-torch-shallow_{class_name}"
                log.info("  Class: %s  grad shape=%s", class_name, coef.shape)
                _save_coef(coef, dd_tags, model_name,
                           {"model": model_name, "feature": "deepdanbooru",
                            "label": class_name, "method": "torch_gradient"})
        else:
            log.warning("Legacy torch model not found: %s", torch_path)

    # ── Combined CSV ──────────────────────────────────────────────────────────
    if all_rows:
        combined = pd.concat(all_rows, ignore_index=True)
        out_path = RESULTS_DIR / "feature_importance_all.csv"
        combined.to_csv(out_path, index=False)
        log.info("\nCombined results saved → %s", out_path)
    else:
        log.warning("No results produced.")


if __name__ == "__main__":
    main()
