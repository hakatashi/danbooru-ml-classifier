#!/usr/bin/env python3
"""
Batch inference job for danbooru-ml-classifier.

Finds MongoDB images with status='pending' and a localPath that exists on disk,
runs all ML models from pu-learning/data/models/, and saves results to MongoDB.

Saved fields:
  status          → "inferred" (or "error" on failure)
  inferences      → {model_filename: {score: float} or {not_bookmarked, bookmarked_public, bookmarked_private}}
  importantTagProbs → {deepdanbooru: {tag: prob, ...}, pixai: {tag: prob, ...}}
"""

import logging
import os
import re
import sys
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
import torch
import torch.nn as nn
from PIL import Image, UnidentifiedImageError
from pymongo import MongoClient
from torchvision import models, transforms

# ── Paths ─────────────────────────────────────────────────────────────────────
WORKER_DIR = Path(__file__).parent
REPO_ROOT  = WORKER_DIR.parent
PU_DIR     = REPO_ROOT / "pu-learning"
MODELS_DIR = PU_DIR / "data" / "models"
RESULTS_DIR = PU_DIR / "data" / "results"

DEEPDANBOORU_IMPORTANCE_CSV = RESULTS_DIR / "feature_importance_deepdanbooru_pixiv_private_elkan_noto_positive.csv"
PIXAI_IMPORTANCE_CSV        = RESULTS_DIR / "feature_importance_pixai_pixiv_private_nnpu_positive.csv"

IMAGE_CACHE_DIR = Path(os.environ.get("IMAGE_CACHE_DIR", "/mnt/cache/danbooru-ml-classifier/images"))
MONGODB_URI     = os.environ.get("MONGODB_URI", "mongodb://localhost:27017")
MONGODB_DB      = os.environ.get("MONGODB_DB", "danbooru-ml-classifier")

PIXAI_MODEL_DIR   = Path.home() / ".cache" / "pixai-tagger"
PIXAI_MODEL_REPO  = "pixai-labs/pixai-tagger-v0.9"
PIXAI_MODEL_FILES = ["model_v0.9.pth", "tags_v0.9_13k.json", "char_ip_map.json"]

DEEPDANBOORU_DIM        = 6000
EVA02_DIM               = 1024
PIXAI_DIM               = 13461
DEEPDANBOORU_IMAGE_SIZE = 360
PIXAI_IMAGE_SIZE        = 448

CLASS_NAMES  = ["not_bookmarked", "bookmarked_public", "bookmarked_private"]
ALL_FEATURES = ["deepdanbooru", "eva02", "pixai"]
ALL_LABELS   = ["pixiv_public", "pixiv_private", "twitter"]
ALL_METHODS  = ["elkan_noto", "biased_svm", "nnpu"]

_MODEL_RE = re.compile(
    r"^(" + "|".join(ALL_FEATURES) + r")"
    r"_(" + "|".join(ALL_LABELS) + r")"
    r"_(" + "|".join(ALL_METHODS) + r")$"
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

# Worker dir must be on sys.path for danbooru_resnet and pixai_tagger imports
sys.path.insert(0, str(WORKER_DIR))


# ── Unpickling support (matches score_unlabeled.py) ───────────────────────────

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
        if self.scaler is not None:
            X = self.scaler.transform(X)
        self.model.eval()
        probs = []
        with torch.no_grad():
            for i in range(0, len(X), 2048):
                batch  = torch.from_numpy(X[i:i + 2048]).float().to(self.device)
                logits = self.model(batch).squeeze(1)
                probs.append(torch.sigmoid(logits).cpu().numpy())
        p1 = np.concatenate(probs)
        return np.stack([1.0 - p1, p1], axis=1)


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


# ── Image helpers ─────────────────────────────────────────────────────────────

def _to_rgb(image: Image.Image) -> Image.Image:
    if image.mode == "RGBA":
        bg = Image.new("RGB", image.size, (255, 255, 255))
        bg.paste(image, mask=image.split()[3])
        return bg
    if image.mode != "RGB":
        return image.convert("RGB")
    return image


# ── DeepDanbooru feature extractor ───────────────────────────────────────────

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
    def extract(self, image: Image.Image) -> np.ndarray:
        """Returns float32 array of shape (6000,)."""
        t = self.transform(_to_rgb(image)).unsqueeze(0).to(self.device)
        probs = torch.sigmoid(self.model(t))
        return probs.cpu().numpy()[0]


# ── PixAI / EVA02 feature extractor ──────────────────────────────────────────

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
                hf_hub_download(
                    repo_id=PIXAI_MODEL_REPO,
                    filename=fname,
                    local_dir=str(PIXAI_MODEL_DIR),
                )

        log.info("[PixAI] Loading model ...")
        weights_file = PIXAI_MODEL_DIR / "model_v0.9.pth"
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
        log.info("[PixAI] Ready on %s", device)

        self.transform = transforms.Compose([
            transforms.Resize((PIXAI_IMAGE_SIZE, PIXAI_IMAGE_SIZE)),
            transforms.ToTensor(),
            transforms.Normalize(mean=[0.5, 0.5, 0.5], std=[0.5, 0.5, 0.5]),
        ])

    @torch.inference_mode()
    def extract(self, image: Image.Image) -> tuple[np.ndarray, np.ndarray]:
        """Returns (eva02_emb: float32 (1024,), pixai_tags: float32 (13461,))."""
        t = self.transform(_to_rgb(image)).unsqueeze(0).to(self.device)
        emb  = self.encoder(t)     # (1, 1024)
        tags = self.decoder(emb)   # (1, 13461)
        return emb.cpu().numpy()[0], tags.cpu().numpy()[0]


# ── Legacy PyTorch shallow network ────────────────────────────────────────────

class _ShallowNetwork(nn.Module):
    def __init__(self):
        super().__init__()
        self.middle1_layer = nn.Linear(6000, 512)
        self.middle2_layer = nn.Linear(512, 128)
        self.middle3_layer = nn.Linear(128, 128)
        self.out_layer     = nn.Linear(128, 3)

    def forward(self, x):
        import torch.nn.functional as F
        x = F.relu(self.middle1_layer(x))
        x = F.relu(self.middle2_layer(x))
        x = F.relu(self.middle3_layer(x))
        return self.out_layer(x)


# ── Model loading ─────────────────────────────────────────────────────────────

def load_all_models(device: str) -> dict:
    """
    Loads all models from MODELS_DIR.

    Returns a dict: model_filename → ("legacy_sklearn", clf)
                                   | ("legacy_torch",  net, device)
                                   | ("pu", clf, feature_name)
    """
    loaded = {}

    # 1. Legacy sklearn multiclass models
    for model_path in sorted(MODELS_DIR.glob("sklearn-multiclass-*.joblib")):
        log.info("Loading %s ...", model_path.name)
        clf = joblib.load(model_path)
        _fix_sklearn_compat(clf)
        loaded[model_path.name] = ("legacy_sklearn", clf)

    # 2. Legacy PyTorch shallow network
    torch_model_path = MODELS_DIR / "torch-multiclass-onehot-shallow-network-multilayer"
    if torch_model_path.exists():
        log.info("Loading %s ...", torch_model_path.name)
        net = _ShallowNetwork()
        state = torch.load(str(torch_model_path), map_location=device)
        net.load_state_dict(state)
        net.to(device)
        net.eval()
        loaded[torch_model_path.name] = ("legacy_torch", net, device)

    # 3. PU Learning models
    for model_path in sorted(MODELS_DIR.glob("*.joblib")):
        m = _MODEL_RE.match(model_path.stem)
        if not m:
            continue
        feature, label, method = m.group(1), m.group(2), m.group(3)
        log.info("Loading %s ...", model_path.name)
        clf = joblib.load(model_path)
        _fix_sklearn_compat(clf)
        if isinstance(clf, NNPUClassifier):
            clf.model  = clf.model.to(device)
            clf.device = device
        loaded[model_path.name] = ("pu", clf, feature)

    log.info("Loaded %d models total", len(loaded))
    return loaded


# ── Per-image inference ───────────────────────────────────────────────────────

def run_inference(
    features: dict[str, np.ndarray],
    all_models: dict,
) -> dict:
    """
    Score a single image with all loaded models.

    features: {"deepdanbooru": arr(6000,), "eva02": arr(1024,), "pixai": arr(13461,)}
    Returns inferences dict keyed by model filename.
    """
    inferences = {}

    for model_name, model_tuple in all_models.items():
        model_type = model_tuple[0]

        if model_type == "legacy_sklearn":
            clf = model_tuple[1]
            x = features["deepdanbooru"].reshape(1, -1)
            if hasattr(clf, "decision_function"):
                scores = clf.decision_function(x)[0].tolist()
            else:
                scores = clf.predict_proba(x)[0].tolist()
            inferences[model_name] = dict(zip(CLASS_NAMES, scores))

        elif model_type == "legacy_torch":
            net    = model_tuple[1]
            dev    = model_tuple[2]
            x = torch.from_numpy(features["deepdanbooru"]).float().unsqueeze(0).to(dev)
            with torch.no_grad():
                logits = net(x)[0].cpu().tolist()
            inferences[model_name] = dict(zip(CLASS_NAMES, logits))

        elif model_type == "pu":
            clf          = model_tuple[1]
            feature_name = model_tuple[2]
            x = features[feature_name].reshape(1, -1)
            if hasattr(clf, "predict_proba"):
                score = float(clf.predict_proba(x)[0, 1])
            else:
                score = float(clf.decision_function(x)[0])
            inferences[model_name] = {"score": score}

    return inferences


# ── Important tag loading ─────────────────────────────────────────────────────

def load_important_tag_indices(csv_path: Path, n: int = 50) -> list[tuple[str, int]]:
    """Returns [(tag_name, feat_idx), ...] for the top-n rows."""
    df = pd.read_csv(csv_path).head(n)
    return list(zip(df["tag"].tolist(), df["feat_idx"].astype(int).tolist()))


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    device = "cuda" if torch.cuda.is_available() else "cpu"
    log.info("Device: %s", device)

    # MongoDB connection
    client = MongoClient(MONGODB_URI)
    db     = client[MONGODB_DB]
    col    = db["images"]

    # Load important tag index lists (top-50 each)
    log.info("Loading important tag indices ...")
    dd_tags   = load_important_tag_indices(DEEPDANBOORU_IMPORTANCE_CSV, 50)
    pxai_tags = load_important_tag_indices(PIXAI_IMPORTANCE_CSV, 50)
    log.info("  DeepDanbooru: %d tags, PixAI: %d tags", len(dd_tags), len(pxai_tags))

    # Load all ML models
    log.info("Loading models from %s ...", MODELS_DIR)
    all_models = load_all_models(device)

    # Load feature extractors
    log.info("Initialising DeepDanbooru extractor ...")
    dd_extractor = DeepDanbooruExtractor(device)

    log.info("Initialising PixAI extractor ...")
    pxai_extractor = PixAIExtractor(device)

    # Find pending images that have a local file
    pending_docs = list(col.find({
        "status":    "pending",
        "localPath": {"$exists": True},
    }))
    log.info("Pending documents with localPath: %d", len(pending_docs))

    processable = [
        doc for doc in pending_docs
        if doc.get("localPath") and Path(doc["localPath"]).exists()
    ]
    skipped = len(pending_docs) - len(processable)
    log.info("Files found on disk: %d  (skipped %d missing)", len(processable), skipped)

    for i, doc in enumerate(processable):
        doc_id     = doc["_id"]
        local_path = doc["localPath"]
        log.info("[%d/%d] %s", i + 1, len(processable), local_path)

        # Load image
        try:
            image = Image.open(local_path)
            image.load()
        except (UnidentifiedImageError, OSError, Exception) as exc:
            log.error("  Cannot open image: %s", exc)
            col.update_one({"_id": doc_id}, {"$set": {"status": "error"}})
            continue

        # Extract features
        try:
            dd_feats                 = dd_extractor.extract(image)       # (6000,)
            eva02_feats, pxai_feats  = pxai_extractor.extract(image)     # (1024,), (13461,)
        except Exception as exc:
            log.error("  Feature extraction failed: %s", exc)
            col.update_one({"_id": doc_id}, {"$set": {"status": "error"}})
            continue

        features = {
            "deepdanbooru": dd_feats,
            "eva02":        eva02_feats,
            "pixai":        pxai_feats,
        }

        # Run all models
        try:
            inferences = run_inference(features, all_models)
        except Exception as exc:
            log.error("  Inference failed: %s", exc)
            col.update_one({"_id": doc_id}, {"$set": {"status": "error"}})
            continue

        # Build importantTagProbs
        important_tag_probs = {
            "deepdanbooru": {tag: float(dd_feats[idx])   for tag, idx in dd_tags},
            "pixai":        {tag: float(pxai_feats[idx]) for tag, idx in pxai_tags},
        }

        # Save to MongoDB
        col.update_one(
            {"_id": doc_id},
            {"$set": {
                "status":            "inferred",
                "inferences":        inferences,
                "importantTagProbs": important_tag_probs,
            }},
        )
        log.info("  Saved → %s", doc_id)

    log.info("Done. Processed %d images.", len(processable))


if __name__ == "__main__":
    main()
