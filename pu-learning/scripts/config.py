"""
Shared configuration for PU Learning project.
"""

from pathlib import Path

# ── Directory layout ─────────────────────────────────────────────────────────
SCRIPTS_DIR  = Path(__file__).parent
PROJECT_ROOT = SCRIPTS_DIR.parent
REPO_ROOT    = PROJECT_ROOT.parent

DATA_DIR     = PROJECT_ROOT / "data"
FEATURES_DIR = DATA_DIR / "features"
METADATA_DIR = DATA_DIR / "metadata"
MODELS_DIR   = DATA_DIR / "models"

# ── Source image directories ──────────────────────────────────────────────────
HAKATAARCHIVE_PIXIV_DIR    = Path("/mnt/cache/hakataarchive/pixiv")
HAKATAARCHIVE_TWITTER_DIR  = Path("/mnt/cache/hakataarchive/twitter")
DMC_IMAGES_DIR             = Path("/mnt/cache/danbooru-ml-classifier/images")

# ── External data ─────────────────────────────────────────────────────────────
PIXIV_INDEX_S3_URI = "s3://hakataarchive/index/pixiv.json"

# ── Worker module path (for shared model code) ────────────────────────────────
WORKER_DIR = REPO_ROOT / "worker"

# ── Feature dimensions ────────────────────────────────────────────────────────
DEEPDANBOORU_DIM = 6000
EVA02_DIM        = 1024
PIXAI_DIM        = 13461

# ── Model configuration ───────────────────────────────────────────────────────
PIXAI_MODEL_DIR    = Path.home() / ".cache" / "pixai-tagger"
PIXAI_MODEL_REPO   = "pixai-labs/pixai-tagger-v0.9"
PIXAI_MODEL_FILES  = ["model_v0.9.pth", "tags_v0.9_13k.json", "char_ip_map.json"]

DEEPDANBOORU_URL = (
    "https://github.com/RF5/danbooru-pretrained/releases/download/v0.1"
    "/resnet50-13306192.pth"
)

# Input sizes
DEEPDANBOORU_IMAGE_SIZE = 360   # Resize before ResNet50 (AdaptivePool handles output)
PIXAI_IMAGE_SIZE        = 448   # Fixed by PixAI preprocessing

# ── Dataset split ratios ──────────────────────────────────────────────────────
POSITIVE_SPLIT  = {"train": 0.70, "val": 0.15, "test": 0.15}
UNLABELED_SPLIT = {"train": 0.90, "val": 0.05, "test": 0.05}

RANDOM_SEED = 42

# ── Processing ────────────────────────────────────────────────────────────────
DEFAULT_BATCH_SIZE = 64

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".gif"}

# Labels used in the dataset
POSITIVE_LABELS  = {"pixiv_public", "pixiv_private", "twitter"}
UNLABELED_LABEL  = "unlabeled"
