#!/usr/bin/env python3
"""
Per-image attribution visualization for trained classifiers.

Two visualization modes:

  1. tag_contribution  (deepdanbooru / pixai models)
     For each top-scored image, compute per-tag contribution:
       contribution_i = tag_probability_i × model_coefficient_i
     Renders image + horizontal bar chart of top contributing tags.
     Output: data/results/tag_contrib_{model}_{rank:02d}_{image_id}.png

  2. gradcam  (EVA02 models)
     Loads the full EVA02 encoder + trained linear classifier.
     Registers a hook on the last ViT block to capture token activations and
     their gradients w.r.t. the classifier output score.
     GradCAM heatmap = ReLU(mean_over_channels(grad × activation)) for each
     spatial token, reshaped to 32×32 and upsampled over the original image.
     Output: data/results/gradcam_{model}_{rank:02d}_{image_id}.png

Usage:
  # Per-image tag contribution for top-10 images
  python visualize_attribution.py --mode tag_contribution --features deepdanbooru --top-k 10

  # GradCAM heatmaps for EVA02 biased_svm model
  python visualize_attribution.py --mode gradcam --labels pixiv_public --methods biased_svm --top-k 10

  # Combine both for deepdanbooru and eva02
  python visualize_attribution.py --mode all --top-k 5
"""

import argparse
import json
import logging
import re
import sys
import warnings
from pathlib import Path

import h5py
import joblib
import numpy as np
import pandas as pd
from PIL import Image

SCRIPTS_DIR = Path(__file__).parent
sys.path.insert(0, str(SCRIPTS_DIR))
from config import (
    DATA_DIR, FEATURES_DIR, METADATA_DIR,
    PIXAI_MODEL_DIR, PIXAI_MODEL_REPO, PIXAI_MODEL_FILES,
    EVA02_DIM, PIXAI_DIM, PIXAI_IMAGE_SIZE,
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

ALL_FEATURES_TAG  = ["deepdanbooru", "pixai"]       # have tag names
ALL_FEATURES_GRAD = ["eva02"]                        # require encoder reload
ALL_LABELS   = ["pixiv_public", "pixiv_private", "twitter"]
ALL_METHODS  = ["elkan_noto", "biased_svm", "nnpu"]

_MODEL_RE = re.compile(
    r"^(deepdanbooru|eva02|pixai)"
    r"_(pixiv_public|pixiv_private|twitter)"
    r"_(elkan_noto|biased_svm|nnpu)$"
)

# ── Unpickling stubs ──────────────────────────────────────────────────────────

class _ScaledClassifier:
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
    def __init__(self, model, scaler, device):
        self.model  = model
        self.scaler = scaler
        self.device = device


# ── Tag name loading ──────────────────────────────────────────────────────────

def load_deepdanbooru_tags() -> list[str]:
    path = METADATA_DIR / "deepdanbooru_tags.json"
    if not path.exists():
        raise FileNotFoundError(f"DeepDanbooru tag list not found: {path}")
    with path.open() as f:
        return json.load(f)


def load_pixai_tags() -> list[str]:
    path = PIXAI_MODEL_DIR / "tags_v0.9_13k.json"
    if not path.exists():
        raise FileNotFoundError(f"PixAI tag list not found: {path}")
    with path.open() as f:
        d = json.load(f)
    tag_map = d["tag_map"]
    idx_to_tag = [""] * len(tag_map)
    for name, idx in tag_map.items():
        idx_to_tag[idx] = name
    return idx_to_tag


TAG_LOADERS = {
    "deepdanbooru": load_deepdanbooru_tags,
    "pixai":        load_pixai_tags,
}

# ── Coefficient extraction (same as feature_importance.py) ───────────────────

def get_linear_coef(clf, method: str) -> np.ndarray | None:
    try:
        if method == "elkan_noto":
            return clf.clf.estimator.coef_.ravel()
        elif method == "biased_svm":
            coefs = [c.estimator.coef_.ravel()
                     for c in clf.clf.calibrated_classifiers_]
            return np.mean(coefs, axis=0)
    except Exception as e:
        log.warning("Could not extract linear coef: %s", e)
    return None


# ── Feature loading ───────────────────────────────────────────────────────────

class FeatureStore:
    def __init__(self, feature_name: str):
        self.path = FEATURES_DIR / f"{feature_name}.h5"
        with h5py.File(self.path, "r") as f:
            ids = f["image_ids"].asstr()[:]
        self._id_to_row = {iid: i for i, iid in enumerate(ids)}

    def load_rows(self, image_ids: list[str]) -> np.ndarray:
        rows  = [self._id_to_row.get(iid) for iid in image_ids]
        valid = [i for i in range(len(rows)) if rows[i] is not None]
        with h5py.File(self.path, "r") as f:
            hdf_rows = sorted(set(rows[i] for i in valid))
            data = f["features"][hdf_rows, :].astype(np.float32)
            row_to_local = {r: j for j, r in enumerate(hdf_rows)}
        out = np.zeros((len(image_ids), data.shape[1]), dtype=np.float32)
        for i in valid:
            out[i] = data[row_to_local[rows[i]]]
        return out


# ── Score top-K / bottom-K images ────────────────────────────────────────────

def score_all(
    clf,
    feature_name: str,
    image_ids: list[str],
) -> np.ndarray:
    """Score all images and return a 1-D float array aligned with image_ids."""
    store = FeatureStore(feature_name)
    X     = store.load_rows(image_ids)

    if hasattr(clf, "predict_proba"):
        return clf.predict_proba(X)[:, 1]
    elif hasattr(clf, "decision_function"):
        return clf.decision_function(X)
    else:
        raise ValueError("Classifier has neither predict_proba nor decision_function")


def pick_top_bottom(
    image_ids: list[str],
    id_to_path: dict[str, str],
    scores: np.ndarray,
    top_k: int,
    bottom_k: int,
) -> tuple[list[tuple[str, str, float]], list[tuple[str, str, float]]]:
    """
    Return (top_images, bottom_images) each as [(image_id, file_path, score)].
    top_images    : highest-scoring images, rank 1 = best
    bottom_images : lowest-scoring images,  rank 1 = worst
    """
    sorted_desc = np.argsort(scores)[::-1]
    sorted_asc  = np.argsort(scores)

    top_images = [
        (image_ids[i], id_to_path[image_ids[i]], float(scores[i]))
        for i in sorted_desc[:top_k]
    ]
    bottom_images = [
        (image_ids[i], id_to_path[image_ids[i]], float(scores[i]))
        for i in sorted_asc[:bottom_k]
    ]
    return top_images, bottom_images


# ── Colormap helper ───────────────────────────────────────────────────────────

def heatmap_overlay(image: Image.Image, heatmap: np.ndarray, alpha: float = 0.5) -> Image.Image:
    """Overlay a [0,1] float heatmap on a PIL image using a blue→red colormap."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.cm as cm

    h, w = heatmap.shape
    heatmap_norm = (heatmap - heatmap.min()) / (heatmap.max() - heatmap.min() + 1e-8)
    colored = (cm.jet(heatmap_norm)[:, :, :3] * 255).astype(np.uint8)
    colored_img = Image.fromarray(colored).resize(image.size, Image.BILINEAR)

    base = image.convert("RGB")
    return Image.blend(base, colored_img, alpha)


# ═══════════════════════════════════════════════════════════════════════════════
# Mode 1: Per-image tag contribution (deepdanbooru / pixai)
# ═══════════════════════════════════════════════════════════════════════════════

def visualize_tag_contributions(
    clf,
    feature_name: str,
    method: str,
    model_name: str,
    images: list[tuple[str, str, float]],
    tag_names: list[str],
    top_tags: int,
    direction: str = "top",   # "top" | "bottom"
) -> None:
    """
    For each image, draw:
      Left  : thumbnail of the image
      Right : horizontal bar chart of top-contributing tags
               bar length = tag_prob × coef  (signed)

    direction : "top" for highest-scored images, "bottom" for lowest-scored.
    """
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    coef = get_linear_coef(clf, method)
    if coef is None:
        log.warning("  [tag_contribution] Cannot extract coef for method=%s; skipping", method)
        return

    store = FeatureStore(feature_name)

    for rank, (image_id, file_path, score) in enumerate(images, 1):
        feats = store.load_rows([image_id])[0]      # (dim,)
        contrib = feats * coef                      # element-wise: tag_prob × coef

        # Top positive and negative contributions
        pos_idx = np.argsort(contrib)[::-1][:top_tags]
        neg_idx = np.argsort(contrib)[:top_tags]

        selected = list(pos_idx) + list(neg_idx)
        selected_unique = list(dict.fromkeys(selected))   # preserve order, deduplicate

        contrib_vals = [contrib[i] for i in selected_unique]
        tag_labels   = [tag_names[i] for i in selected_unique]

        colors = ["#d62728" if v > 0 else "#1f77b4" for v in contrib_vals]

        # ── Draw figure ───────────────────────────────────────────────────────
        fig, (ax_img, ax_bar) = plt.subplots(1, 2, figsize=(16, 8))

        try:
            img = Image.open(file_path).convert("RGB")
            img.thumbnail((512, 512))
            ax_img.imshow(img)
        except Exception:
            ax_img.text(0.5, 0.5, "Image unavailable", ha="center", va="center")
        ax_img.axis("off")
        dir_label = "▲ top" if direction == "top" else "▼ bottom"
        ax_img.set_title(f"{dir_label} #{rank}  score={score:.4f}\n{image_id}", fontsize=9)

        ax_bar.barh(range(len(contrib_vals)), contrib_vals, color=colors)
        ax_bar.set_yticks(range(len(tag_labels)))
        ax_bar.set_yticklabels(tag_labels, fontsize=8)
        ax_bar.invert_yaxis()
        ax_bar.axvline(0, color="black", linewidth=0.8)
        ax_bar.set_xlabel("tag_prob × coef  (contribution to score)")
        ax_bar.set_title(f"Tag contributions  [{model_name}]  [{direction}]", fontsize=9)

        plt.tight_layout()
        safe_id = re.sub(r"[/\\:]", "_", image_id)
        out_path = RESULTS_DIR / f"tag_contrib_{model_name}_{direction}_{rank:02d}_{safe_id}.png"
        plt.savefig(out_path, dpi=100, bbox_inches="tight")
        plt.close(fig)
        log.info("  Saved → %s", out_path)


# ═══════════════════════════════════════════════════════════════════════════════
# Mode 2: GradCAM for EVA02
# ═══════════════════════════════════════════════════════════════════════════════

def _ensure_pixai_model_files() -> None:
    from huggingface_hub import hf_hub_download
    PIXAI_MODEL_DIR.mkdir(parents=True, exist_ok=True)
    for fname in PIXAI_MODEL_FILES:
        local = PIXAI_MODEL_DIR / fname
        if not local.exists():
            log.info("Downloading %s …", fname)
            hf_hub_download(
                repo_id=PIXAI_MODEL_REPO,
                filename=fname,
                local_dir=str(PIXAI_MODEL_DIR),
            )


def build_eva02_pipeline(clf, device: str):
    """
    Load the EVA02 encoder + attach the trained linear classifier on top.
    Returns (encoder, score_fn) where score_fn(embedding) -> scalar score.
    """
    import timm
    import torch
    import torch.nn as nn
    from torchvision import transforms
    sys.path.insert(0, str(SCRIPTS_DIR.parent.parent / "worker"))
    from pixai_tagger import TaggingHead

    _ensure_pixai_model_files()

    encoder = timm.create_model(
        "hf_hub:SmilingWolf/wd-eva02-large-tagger-v3",
        pretrained=False,
    )
    encoder.reset_classifier(0)

    # Load PixAI weights (encoder portion only)
    decoder   = TaggingHead(EVA02_DIM, PIXAI_DIM)
    full_model = nn.Sequential(encoder, decoder)
    states = torch.load(
        str(PIXAI_MODEL_DIR / "model_v0.9.pth"),
        map_location="cpu",
        weights_only=True,
    )
    full_model.load_state_dict(states)
    encoder = full_model[0]
    encoder.eval().to(device)
    log.info("  EVA02 encoder loaded on %s", device)

    # Build a differentiable score function from the trained classifier
    if isinstance(clf, _ScaledClassifier):
        inner_clf = clf.clf
        scaler    = clf.scaler
    else:
        inner_clf = clf
        scaler    = None

    # Build coef / intercept tensors for linear scoring
    method_name = type(inner_clf).__name__
    if "ElkanotoPuClassifier" in method_name:
        lr = inner_clf.estimator
        coef_np = lr.coef_.ravel().astype(np.float32)
        bias_np = lr.intercept_.ravel().astype(np.float32)
    elif "CalibratedClassifierCV" in method_name:
        coefs = [c.estimator.coef_.ravel() for c in inner_clf.calibrated_classifiers_]
        coef_np = np.mean(coefs, axis=0).astype(np.float32)
        biases  = [c.estimator.intercept_.ravel() for c in inner_clf.calibrated_classifiers_]
        bias_np = np.mean(biases, axis=0).astype(np.float32)
    else:
        raise ValueError(f"Unsupported inner classifier type: {method_name}")

    import torch
    coef_t = torch.from_numpy(coef_np).to(device)     # (1024,)
    bias_t = torch.from_numpy(bias_np).to(device)     # (1,) or scalar

    if scaler is not None:
        mean_t  = torch.from_numpy(scaler.mean_.astype(np.float32)).to(device)
        scale_t = torch.from_numpy(scaler.scale_.astype(np.float32)).to(device)
    else:
        mean_t  = None
        scale_t = None

    def score_fn(embedding: "torch.Tensor") -> "torch.Tensor":
        """embedding: (1, 1024) — returns scalar logit."""
        e = embedding.squeeze(0)          # (1024,)
        if mean_t is not None:
            e = (e - mean_t) / scale_t
        return (e @ coef_t + bias_t[0])  # scalar

    transform = transforms.Compose([
        transforms.Resize((PIXAI_IMAGE_SIZE, PIXAI_IMAGE_SIZE)),
        transforms.ToTensor(),
        transforms.Normalize(mean=[0.5, 0.5, 0.5], std=[0.5, 0.5, 0.5]),
    ])

    return encoder, score_fn, transform


def gradcam_eva02(
    encoder,
    score_fn,
    transform,
    model_name: str,
    images: list[tuple[str, str, float]],
    device: str,
    alpha: float = 0.6,
    direction: str = "top",   # "top" | "bottom"
) -> None:
    """
    For each image:
      1. Forward pass through EVA02 encoder with grad tracking.
      2. Hook the last ViT block's output tokens (1+1024 tokens, dim=1024).
      3. Compute score via score_fn.
      4. Backpropagate to get grad w.r.t. last block tokens.
      5. GradCAM = ReLU(mean_channels(grad × activation)) for spatial tokens.
      6. Reshape to 32×32, upsample, overlay on original image.

    direction : "top" for highest-scored images, "bottom" for lowest-scored.
    """
    import torch
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    last_block = encoder.blocks[-1]

    for rank, (image_id, file_path, score) in enumerate(images, 1):
        try:
            img_orig = Image.open(file_path).convert("RGB")
        except Exception as e:
            log.warning("  Could not load %s: %s", file_path, e)
            continue

        x = transform(img_orig).unsqueeze(0).to(device)   # (1, 3, 448, 448)

        # ── Register hooks ────────────────────────────────────────────────────
        activation_store: dict[str, "torch.Tensor"] = {}
        grad_store:       dict[str, "torch.Tensor"] = {}

        def forward_hook(module, inp, out):
            activation_store["tokens"] = out           # (1, 1025, 1024)

        def backward_hook(module, grad_in, grad_out):
            grad_store["tokens"] = grad_out[0]         # (1, 1025, 1024)

        fh = last_block.register_forward_hook(forward_hook)
        bh = last_block.register_full_backward_hook(backward_hook)

        try:
            encoder.zero_grad()
            embedding = encoder(x)                     # (1, 1024)
            logit     = score_fn(embedding)
            logit.backward()

            acts  = activation_store["tokens"]         # (1, 1025, 1024)
            grads = grad_store["tokens"]               # (1, 1025, 1024)

            # Spatial tokens only (skip CLS at position 0)
            acts_spatial  = acts[0, 1:, :]             # (1024, 1024)
            grads_spatial = grads[0, 1:, :]            # (1024, 1024)

            # GradCAM: average over channel dim, take ReLU
            cam = (grads_spatial * acts_spatial).mean(dim=-1)   # (1024,)
            cam = torch.relu(cam).cpu().detach().numpy()        # (1024,)

            # Reshape to 32×32 grid (32 = sqrt(1024))
            n_patches = cam.shape[0]
            side = int(np.sqrt(n_patches))
            cam_2d = cam.reshape(side, side)

        finally:
            fh.remove()
            bh.remove()

        # ── Overlay on image ──────────────────────────────────────────────────
        vis = heatmap_overlay(img_orig, cam_2d, alpha=alpha)

        dir_label = "▲ top" if direction == "top" else "▼ bottom"
        fig, axes = plt.subplots(1, 2, figsize=(14, 7))
        axes[0].imshow(img_orig)
        axes[0].axis("off")
        axes[0].set_title(f"Original  {dir_label} #{rank}  score={score:.4f}", fontsize=9)
        axes[1].imshow(vis)
        axes[1].axis("off")
        axes[1].set_title(f"GradCAM  [{model_name}]  [{direction}]\n{image_id}", fontsize=9)

        plt.tight_layout()
        safe_id = re.sub(r"[/\\:]", "_", image_id)
        out_path = RESULTS_DIR / f"gradcam_{model_name}_{direction}_{rank:02d}_{safe_id}.png"
        plt.savefig(out_path, dpi=100, bbox_inches="tight")
        plt.close(fig)
        log.info("  Saved → %s", out_path)


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--mode",
        choices=["tag_contribution", "gradcam", "all"],
        default="all",
        help="Visualization mode (default: all)",
    )
    parser.add_argument(
        "--top-k", type=int, default=5,
        help="Number of highest-scored images to visualize per model (default: 5)",
    )
    parser.add_argument(
        "--bottom-k", type=int, default=None,
        help="Number of lowest-scored images to visualize per model "
             "(default: same as --top-k; 0 to disable)",
    )
    parser.add_argument(
        "--top-tags", type=int, default=20,
        help="Number of top/bottom contributing tags to show in bar chart (default: 20)",
    )
    parser.add_argument(
        "--features", nargs="+",
        choices=["deepdanbooru", "eva02", "pixai", "all"], default=["all"],
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
        "--split", choices=["train", "val", "test", "all"], default="test",
        help="Dataset split for scoring unlabeled images (default: test)",
    )
    parser.add_argument(
        "--gpu-device", type=str, default=None,
        help="PyTorch device (default: auto)",
    )
    parser.add_argument(
        "--gradcam-alpha", type=float, default=0.6,
        help="Overlay opacity for GradCAM heatmap (default: 0.6)",
    )
    args = parser.parse_args()

    bottom_k = args.bottom_k if args.bottom_k is not None else args.top_k

    filter_features = ["deepdanbooru", "eva02", "pixai"] if "all" in args.features else args.features
    filter_labels   = ALL_LABELS if "all" in args.labels   else args.labels
    filter_methods  = ALL_METHODS if "all" in args.methods else args.methods

    do_tag_contrib = args.mode in ("tag_contribution", "all")
    do_gradcam     = args.mode in ("gradcam", "all")

    import torch
    gpu_device = args.gpu_device or ("cuda" if torch.cuda.is_available() else "cpu")
    log.info("PyTorch device: %s", gpu_device)

    # ── Load splits ───────────────────────────────────────────────────────────
    splits_path = METADATA_DIR / "splits.parquet"
    if not splits_path.exists():
        log.error("splits.parquet not found. Run build_dataset.py first.")
        sys.exit(1)
    splits = pd.read_parquet(splits_path)

    if args.split == "all":
        split_mask = pd.Series([True] * len(splits), index=splits.index)
    else:
        split_mask = splits["split"] == args.split

    unl_df = splits[split_mask & (splits["label"] == UNLABELED_LABEL)].reset_index(drop=True)
    if unl_df.empty:
        log.error("No unlabeled images in split=%s", args.split)
        sys.exit(1)

    image_ids = unl_df["image_id"].tolist()
    id_to_path = dict(zip(unl_df["image_id"], unl_df["file_path"]))
    log.info("Unlabeled images in split=%s: %d", args.split, len(image_ids))

    # ── Discover models ───────────────────────────────────────────────────────
    models = []
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
        models.append((feature, label, method, p))

    if not models:
        log.error("No matching models found in %s", MODELS_DIR)
        sys.exit(1)

    log.info("Models to visualize: %d", len(models))

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)

    # Cache tag loaders
    tag_cache: dict[str, list[str]] = {}

    for feature, label, method, model_path in models:
        model_name = f"{feature}_{label}_{method}"
        log.info("=" * 60)
        log.info("Model: %s", model_name)
        log.info("=" * 60)

        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            clf = joblib.load(model_path)

        if isinstance(clf, NNPUClassifier):
            clf.model  = clf.model.to(gpu_device)
            clf.device = gpu_device

        # ── Score all unlabeled images ────────────────────────────────────────
        log.info("  Scoring %d unlabeled images …", len(image_ids))
        scores = score_all(clf, feature, image_ids)
        top_images, bottom_images = pick_top_bottom(
            image_ids, id_to_path, scores, args.top_k, bottom_k,
        )

        log.info("  Top-%d:", args.top_k)
        for rank, (iid, fpath, sc) in enumerate(top_images, 1):
            log.info("    #%d  score=%.4f  %s", rank, sc, iid)
        if bottom_k > 0:
            log.info("  Bottom-%d:", bottom_k)
            for rank, (iid, fpath, sc) in enumerate(bottom_images, 1):
                log.info("    #%d  score=%.4f  %s", rank, sc, iid)

        # Pairs of (images_list, direction_label) to process
        batches: list[tuple[list[tuple[str, str, float]], str]] = []
        if args.top_k > 0:
            batches.append((top_images, "top"))
        if bottom_k > 0:
            batches.append((bottom_images, "bottom"))

        # ── Tag contribution ──────────────────────────────────────────────────
        if do_tag_contrib and feature in ALL_FEATURES_TAG:
            if method == "nnpu":
                log.info("  [tag_contribution] nnpu has no linear coef; skipping")
            else:
                if feature not in tag_cache:
                    tag_cache[feature] = TAG_LOADERS[feature]()
                for imgs, direction in batches:
                    log.info("  Rendering tag contribution charts  [%s] …", direction)
                    visualize_tag_contributions(
                        clf, feature, method, model_name,
                        imgs, tag_cache[feature], args.top_tags,
                        direction=direction,
                    )

        # ── GradCAM ───────────────────────────────────────────────────────────
        if do_gradcam and feature in ALL_FEATURES_GRAD:
            if method == "nnpu":
                log.info("  [gradcam] nnpu uses MLP on top of EVA02 embedding — GradCAM still applicable")
            log.info("  Building EVA02 pipeline …")
            try:
                encoder, score_fn, transform = build_eva02_pipeline(clf, gpu_device)
                for imgs, direction in batches:
                    log.info("  Rendering GradCAM heatmaps  [%s] …", direction)
                    gradcam_eva02(
                        encoder, score_fn, transform,
                        model_name, imgs, gpu_device,
                        alpha=args.gradcam_alpha,
                        direction=direction,
                    )
            except Exception as e:
                log.error("  GradCAM failed: %s", e)
                import traceback
                traceback.print_exc()

    log.info("Done. Results saved to %s", RESULTS_DIR)


if __name__ == "__main__":
    main()
