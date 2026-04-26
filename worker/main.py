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

import json
import logging
import os
import re
import sys
import uuid
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
import torch
import torch.nn as nn
from PIL import Image, UnidentifiedImageError
from pymongo import MongoClient, UpdateOne
from torchvision import models, transforms

# ── Paths ─────────────────────────────────────────────────────────────────────
WORKER_DIR = Path(__file__).parent
REPO_ROOT  = WORKER_DIR.parent
PU_DIR     = REPO_ROOT / "pu-learning"
MODELS_DIR = PU_DIR / "data" / "models"
RESULTS_DIR = PU_DIR / "data" / "results"

DEEPDANBOORU_IMPORTANCE_CSV = RESULTS_DIR / "feature_importance_deepdanbooru_pixiv_private_elkan_noto_positive.csv"
PIXAI_IMPORTANCE_CSV        = RESULTS_DIR / "feature_importance_pixai_pixiv_private_elkan_noto_positive.csv"

IMAGE_CACHE_DIR = Path(os.environ.get("IMAGE_CACHE_DIR", "/mnt/cache/danbooru-ml-classifier/images"))
MONGODB_URI     = os.environ.get("MONGODB_URI", "mongodb://localhost:27017")
MONGODB_DB      = os.environ.get("MONGODB_DB", "danbooru-ml-classifier")
QDRANT_HOST     = os.environ.get("QDRANT_HOST", "localhost")
QDRANT_PORT     = int(os.environ.get("QDRANT_PORT", "6333"))
QDRANT_COLLECTION           = "image_embeddings"
QDRANT_COLLECTION_MULTIAXIS = "image_embeddings_multiaxis"

PIXAI_TAG_CATEGORIES_JSON = PU_DIR / "data" / "metadata" / "pixai_tag_categories.json"

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
    def extract_batch(self, images: list[Image.Image]) -> np.ndarray:
        """Returns float32 array of shape (B, 6000)."""
        tensors = torch.stack([self.transform(_to_rgb(img)) for img in images])
        tensors = tensors.to(self.device)
        probs = torch.sigmoid(self.model(tensors))
        return probs.cpu().numpy()


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
    def extract_batch(
        self, images: list[Image.Image]
    ) -> tuple[np.ndarray, np.ndarray]:
        """Returns (eva02_emb: float32 (B, 1024), pixai_tags: float32 (B, 13461))."""
        tensors = torch.stack([self.transform(_to_rgb(img)) for img in images])
        tensors = tensors.to(self.device)
        emb  = self.encoder(tensors)   # (B, 1024)
        tags = self.decoder(emb)       # (B, 13461)
        return emb.cpu().numpy(), tags.cpu().numpy()


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


# ── Helpers ───────────────────────────────────────────────────────────────────

def _mongo_key(name: str) -> str:
    """Replace characters invalid in MongoDB field names ('.' and '$')."""
    return name.replace(".", "_").replace("$", "_")


def _mongo_id_to_qdrant_uuid(mongo_id: str) -> str:
    """Deterministically convert a MongoDB ObjectId hex string to a UUID string."""
    return str(uuid.uuid5(uuid.NAMESPACE_OID, mongo_id))


# ── Qdrant client (lazy singleton) ───────────────────────────────────────────

_qdrant_client = None

def _get_qdrant_client():
    """Return a Qdrant client, initialising the collection if needed."""
    global _qdrant_client
    if _qdrant_client is not None:
        return _qdrant_client

    from qdrant_client import QdrantClient
    from qdrant_client.models import Distance, VectorParams, PayloadSchemaType

    client = QdrantClient(host=QDRANT_HOST, port=QDRANT_PORT, timeout=10)

    if not client.collection_exists(QDRANT_COLLECTION):
        log.info("[Qdrant] Creating collection '%s' (dim=1024, Cosine) ...", QDRANT_COLLECTION)
        client.create_collection(
            collection_name=QDRANT_COLLECTION,
            vectors_config=VectorParams(size=EVA02_DIM, distance=Distance.COSINE),
        )
        # Create payload indexes for efficient filtering
        client.create_payload_index(QDRANT_COLLECTION, "date",   PayloadSchemaType.KEYWORD)
        client.create_payload_index(QDRANT_COLLECTION, "type",   PayloadSchemaType.KEYWORD)
        client.create_payload_index(QDRANT_COLLECTION, "status", PayloadSchemaType.KEYWORD)
        log.info("[Qdrant] Collection ready.")

    _qdrant_client = client
    return client


def _load_axis_indices() -> dict[str, np.ndarray] | None:
    """
    Load per-axis feature-tag index arrays from the tag category JSON.
    Returns None (with a warning) if the file does not exist yet.
    """
    if not PIXAI_TAG_CATEGORIES_JSON.exists():
        log.warning(
            "[Multiaxis] %s not found — run classify_pixai_tags.py first. "
            "Skipping multiaxis Qdrant upsert.",
            PIXAI_TAG_CATEGORIES_JSON,
        )
        return None

    with open(PIXAI_TAG_CATEGORIES_JSON) as f:
        tag_categories: dict[str, str] = json.load(f)

    tags_json = PIXAI_MODEL_DIR / "tags_v0.9_13k.json"
    with open(tags_json) as f:
        tag_data = json.load(f)
    tag_map: dict[str, int] = tag_data["tag_map"]
    gen_tag_count: int = tag_data["tag_split"]["gen_tag_count"]

    axes: dict[str, list[int]] = {"character": [], "situation": [], "style": []}
    for tag, global_idx in tag_map.items():
        if global_idx >= gen_tag_count:
            # Character name tags (indices gen_tag_count..end) all go to character axis
            axes["character"].append(global_idx)
            continue
        cat = tag_categories.get(tag)
        if cat in axes:
            axes[cat].append(global_idx)

    return {k: np.array(sorted(v), dtype=np.int32) for k, v in axes.items()}


# Lazily loaded axis indices and Qdrant multiaxis client
_axis_indices: dict[str, np.ndarray] | None = None
_axis_indices_loaded = False
_qdrant_multiaxis_client = None


def _get_axis_indices() -> dict[str, np.ndarray] | None:
    global _axis_indices, _axis_indices_loaded
    if not _axis_indices_loaded:
        _axis_indices = _load_axis_indices()
        _axis_indices_loaded = True
    return _axis_indices


def _get_qdrant_multiaxis_client(axis_dims: dict[str, int]):
    global _qdrant_multiaxis_client
    if _qdrant_multiaxis_client is not None:
        return _qdrant_multiaxis_client

    from qdrant_client import QdrantClient
    from qdrant_client.models import Distance, VectorParams, PayloadSchemaType

    client = QdrantClient(host=QDRANT_HOST, port=QDRANT_PORT, timeout=10)

    if not client.collection_exists(QDRANT_COLLECTION_MULTIAXIS):
        log.info("[Qdrant] Creating multiaxis collection '%s' ...", QDRANT_COLLECTION_MULTIAXIS)
        vectors_config = {
            "eva02": VectorParams(size=EVA02_DIM, distance=Distance.COSINE),
            **{
                axis: VectorParams(size=dim, distance=Distance.COSINE)
                for axis, dim in axis_dims.items()
            },
        }
        client.create_collection(
            collection_name=QDRANT_COLLECTION_MULTIAXIS,
            vectors_config=vectors_config,
        )
        for field in ("date", "type", "status"):
            client.create_payload_index(
                QDRANT_COLLECTION_MULTIAXIS, field, PayloadSchemaType.KEYWORD
            )
        log.info("[Qdrant] Multiaxis collection ready.")

    _qdrant_multiaxis_client = client
    return client


def upsert_multiaxis_to_qdrant(
    docs: list[dict],
    eva02_embeddings: np.ndarray,
    pixai_probs: np.ndarray,
    status: str = "inferred",
) -> None:
    """
    Upsert EVA02 + per-axis PixAI sub-vectors to the multiaxis Qdrant collection.
    Errors are logged but do not propagate.
    """
    try:
        axis_indices = _get_axis_indices()
        if axis_indices is None:
            return

        axis_dims = {k: len(v) for k, v in axis_indices.items()}
        client = _get_qdrant_multiaxis_client(axis_dims)

        from qdrant_client.models import PointStruct

        points = []
        for doc, eva_emb, pxai in zip(docs, eva02_embeddings, pixai_probs):
            mongo_id = str(doc["_id"])
            named_vectors = {
                "eva02": eva_emb.tolist(),
                **{
                    axis: pxai[indices].tolist()
                    for axis, indices in axis_indices.items()
                },
            }
            points.append(PointStruct(
                id=_mongo_id_to_qdrant_uuid(mongo_id),
                vector=named_vectors,
                payload={
                    "image_id": mongo_id,
                    "date":     doc.get("date", ""),
                    "type":     doc.get("type", ""),
                    "status":   status,
                },
            ))

        client.upsert(collection_name=QDRANT_COLLECTION_MULTIAXIS, points=points)
        log.info("[Qdrant/multiaxis] Upserted %d points.", len(points))
    except Exception as exc:
        log.warning("[Qdrant/multiaxis] Upsert failed (non-fatal): %s", exc)


def upsert_eva02_to_qdrant(
    docs: list[dict],
    embeddings: np.ndarray,
    status: str = "inferred",
) -> None:
    """
    Upsert EVA02 embeddings to Qdrant.
    Errors are logged but do not propagate — Qdrant unavailability must not
    block the main inference pipeline.

    Args:
        docs:       MongoDB documents (must have _id, date, type fields).
        embeddings: float32 array of shape (N, 1024).
        status:     Value to store in the 'status' payload field.
    """
    try:
        from qdrant_client.models import PointStruct

        client = _get_qdrant_client()
        points = []
        for doc, emb in zip(docs, embeddings):
            mongo_id = str(doc["_id"])
            points.append(PointStruct(
                id=_mongo_id_to_qdrant_uuid(mongo_id),
                vector=emb.tolist(),
                payload={
                    "image_id": mongo_id,
                    "date":     doc.get("date", ""),
                    "type":     doc.get("type", ""),
                    "status":   status,
                },
            ))
        client.upsert(collection_name=QDRANT_COLLECTION, points=points)
        log.info("[Qdrant] Upserted %d points.", len(points))
    except Exception as exc:
        log.warning("[Qdrant] Upsert failed (non-fatal): %s", exc)


# ── Batched inference across all images ───────────────────────────────────────

def run_inference_batched(
    all_features: dict[str, np.ndarray],
    all_models: dict,
    device: str,
    torch_batch_size: int = 512,
) -> list[dict]:
    """
    Score N images with all loaded models in vectorised passes.

    all_features: {"deepdanbooru": (N, 6000), "eva02": (N, 1024), "pixai": (N, 13461)}
    Returns list of N inference dicts, each keyed by model filename.
    """
    n = len(next(iter(all_features.values())))
    inferences_list: list[dict] = [{} for _ in range(n)]

    for model_name, model_tuple in all_models.items():
        model_type = model_tuple[0]
        key = _mongo_key(model_name)

        if model_type == "legacy_sklearn":
            clf = model_tuple[1]
            X = all_features["deepdanbooru"]   # (N, 6000)
            if hasattr(clf, "decision_function"):
                scores = clf.decision_function(X)   # (N, 3)
            else:
                scores = clf.predict_proba(X)       # (N, 3)
            for i in range(n):
                inferences_list[i][key] = dict(zip(CLASS_NAMES, scores[i].tolist()))

        elif model_type == "legacy_torch":
            net = model_tuple[1]
            dev = model_tuple[2]
            X   = all_features["deepdanbooru"]  # (N, 6000)
            chunks = []
            with torch.no_grad():
                for start in range(0, n, torch_batch_size):
                    t = torch.from_numpy(X[start:start + torch_batch_size]).float().to(dev)
                    chunks.append(net(t).cpu().numpy())
            logits = np.concatenate(chunks, axis=0)   # (N, 3)
            for i in range(n):
                inferences_list[i][key] = dict(zip(CLASS_NAMES, logits[i].tolist()))

        elif model_type == "pu":
            clf          = model_tuple[1]
            feature_name = model_tuple[2]
            X = all_features[feature_name]   # (N, dim)
            if hasattr(clf, "predict_proba"):
                scores = clf.predict_proba(X)[:, 1]   # (N,)
            else:
                scores = clf.decision_function(X)      # (N,)
            for i in range(n):
                inferences_list[i][key] = {"score": float(scores[i])}

    return inferences_list


# ── Important tag loading ─────────────────────────────────────────────────────

def load_important_tag_indices(csv_path: Path, n: int = 50) -> list[tuple[str, int]]:
    """Returns [(tag_name, feat_idx), ...] for the top-n rows."""
    df = pd.read_csv(csv_path).head(n)
    return list(zip(df["tag"].tolist(), df["feat_idx"].astype(int).tolist()))


# ── Main ──────────────────────────────────────────────────────────────────────

DEFAULT_GPU_BATCH_SIZE = 64

def main():
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--batch-size", type=int, default=DEFAULT_GPU_BATCH_SIZE,
        help=f"Images per GPU forward pass (default: {DEFAULT_GPU_BATCH_SIZE})",
    )
    args = parser.parse_args()

    device = "cuda" if torch.cuda.is_available() else "cpu"
    log.info("Device: %s  |  GPU batch size: %d", device, args.batch_size)

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

    # ── Step 1: collect processable images ────────────────────────────────────
    pending_docs = list(col.find({
        "status":    {"$in": ["pending", "error"]},
        "localPath": {"$exists": True},
    }))
    log.info("Pending documents with localPath: %d", len(pending_docs))

    processable = [
        doc for doc in pending_docs
        if doc.get("localPath") and Path(doc["localPath"]).exists()
    ]
    log.info(
        "Files found on disk: %d  (skipped %d missing)",
        len(processable), len(pending_docs) - len(processable),
    )
    if not processable:
        log.info("Nothing to do.")
        return

    # ── Step 2-5: per-batch: load → extract → infer → write ──────────────────
    dd_tag_names   = [tag for tag, _ in dd_tags]
    pxai_tag_names = [tag for tag, _ in pxai_tags]
    dd_indices     = [idx for _, idx in dd_tags]
    pxai_indices   = [idx for _, idx in pxai_tags]

    n_total    = len(processable)
    n_done     = 0
    n_error    = 0

    for batch_start in range(0, n_total, args.batch_size):
        batch_docs = processable[batch_start:batch_start + args.batch_size]
        batch_end  = min(batch_start + args.batch_size, n_total)
        log.info("Batch [%d-%d / %d] ...", batch_start + 1, batch_end, n_total)

        # 1. Load images (skip unreadable ones)
        batch_imgs:  list[Image.Image] = []
        loaded_docs: list[dict]        = []
        for doc in batch_docs:
            try:
                img = Image.open(doc["localPath"])
                img.load()
                batch_imgs.append(img)
                loaded_docs.append(doc)
            except (UnidentifiedImageError, OSError, Exception) as exc:
                log.error("Cannot open %s: %s", doc["localPath"], exc)
                col.update_one({"_id": doc["_id"]}, {"$set": {"status": "error"}})
                n_error += 1

        if not batch_imgs:
            continue

        # 2. GPU feature extraction
        try:
            X_dd              = dd_extractor.extract_batch(batch_imgs)       # (B, 6000)
            X_eva, X_pxai     = pxai_extractor.extract_batch(batch_imgs)     # (B, 1024/13461)
        except Exception as exc:
            log.error("Feature extraction failed: %s", exc)
            for doc in loaded_docs:
                col.update_one({"_id": doc["_id"]}, {"$set": {"status": "error"}})
            n_error += len(loaded_docs)
            continue
        finally:
            batch_imgs.clear()

        # 3. Run all models (vectorised over the batch)
        try:
            inferences_list = run_inference_batched(
                {"deepdanbooru": X_dd, "eva02": X_eva, "pixai": X_pxai},
                all_models,
                device,
            )
        except Exception as exc:
            log.error("Inference failed: %s", exc)
            for doc in loaded_docs:
                col.update_one({"_id": doc["_id"]}, {"$set": {"status": "error"}})
            n_error += len(loaded_docs)
            continue

        # 4. Build importantTagProbs and bulk-write to MongoDB
        bulk_ops = []
        for i, (doc, inferences) in enumerate(zip(loaded_docs, inferences_list)):
            important_tag_probs = {
                "deepdanbooru": dict(zip(dd_tag_names,   X_dd[i][dd_indices].tolist())),
                "pixai":        dict(zip(pxai_tag_names, X_pxai[i][pxai_indices].tolist())),
            }
            bulk_ops.append(UpdateOne(
                {"_id": doc["_id"]},
                {"$set": {
                    "status":            "inferred",
                    "inferences":        inferences,
                    "importantTagProbs": important_tag_probs,
                }},
            ))

        result = col.bulk_write(bulk_ops, ordered=False)
        n_done += result.modified_count
        log.info(
            "  Saved %d  (total done=%d  error=%d)",
            result.modified_count, n_done, n_error,
        )

        # Upsert EVA02 embeddings to Qdrant (non-fatal if Qdrant is unavailable)
        upsert_eva02_to_qdrant(loaded_docs, X_eva, status="inferred")
        upsert_multiaxis_to_qdrant(loaded_docs, X_eva, X_pxai, status="inferred")

    log.info("Done. processed=%d  error=%d", n_done, n_error)


if __name__ == "__main__":
    main()
