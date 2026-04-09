#!/usr/bin/env python3
"""
Thin API server for danbooru-ml-classifier.

Exposes MongoDB image data sorted by importantTagProbs or inferences values,
filtered by date. Intended for use by the public website.

Usage:
    venv/bin/uvicorn api:app --host 0.0.0.0 --port 8766
"""

import os
import re
from datetime import datetime
from typing import Optional

from bson import ObjectId
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pymongo import MongoClient, ASCENDING, DESCENDING

# ── Config ────────────────────────────────────────────────────────────────────

MONGODB_URI = os.environ.get("MONGODB_URI", "mongodb://localhost:27017")
MONGODB_DB  = os.environ.get("MONGODB_DB", "danbooru-ml-classifier")

ALLOWED_ORIGINS = [
    "https://danbooru-ml-classifier.web.app",
    "https://danbooru-ml-classifier.firebaseapp.com",
    # Allow localhost for local development
    "http://localhost:5173",
    "http://localhost:4173",
]

PAGE_SIZE_DEFAULT = 50
PAGE_SIZE_MAX     = 200

# ── Validation patterns ───────────────────────────────────────────────────────

# Allowed sort field patterns:
#   importantTagProbs.(deepdanbooru|pixai).<tag>
#   inferences.<model_key>.(score|not_bookmarked|bookmarked_public|bookmarked_private)
_INFERENCE_FIELD_RE = re.compile(
    r"^inferences\.[a-z0-9_-]+\."
    r"(score|not_bookmarked|bookmarked_public|bookmarked_private)$"
)
_IMPORTANT_TAG_FIELD_RE = re.compile(
    r"^importantTagProbs\.(deepdanbooru|pixai)\.[a-z0-9_()\[\] ]+$"
)
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

# Fields excluded from API responses (sensitive / large / internal)
_EXCLUDED_FIELDS = {
    "localPath": 0,
    "topTagProbs": 0,
}

# ── MongoDB client (lazy singleton) ──────────────────────────────────────────

_client: Optional[MongoClient] = None

def get_db():
    global _client
    if _client is None:
        _client = MongoClient(MONGODB_URI)
    return _client[MONGODB_DB]


# ── Helpers ───────────────────────────────────────────────────────────────────

def _doc_to_dict(doc: dict) -> dict:
    """Convert a MongoDB document to a JSON-serialisable dict."""
    result = {}
    for k, v in doc.items():
        if k == "_id":
            result["id"] = str(v)
        elif isinstance(v, ObjectId):
            result[k] = str(v)
        elif isinstance(v, datetime):
            result[k] = v.isoformat()
        elif isinstance(v, dict):
            result[k] = _doc_to_dict(v)
        elif isinstance(v, list):
            result[k] = [
                _doc_to_dict(i) if isinstance(i, dict) else i for i in v
            ]
        else:
            result[k] = v
    return result


def _validate_sort_field(field: str) -> str:
    """Raise HTTPException if sort_field is not on the allowlist."""
    if _INFERENCE_FIELD_RE.match(field):
        return field
    if _IMPORTANT_TAG_FIELD_RE.match(field):
        return field
    raise HTTPException(
        status_code=400,
        detail=(
            "sort_field must match one of: "
            "inferences.<model_key>.(score|not_bookmarked|bookmarked_public|bookmarked_private), "
            "importantTagProbs.(deepdanbooru|pixai).<tag>"
        ),
    )


def _validate_date(value: str) -> str:
    if not _DATE_RE.match(value):
        raise HTTPException(status_code=400, detail="Date must be YYYY-MM-DD")
    return value


# ── App ───────────────────────────────────────────────────────────────────────

app = FastAPI(
    title="Danbooru ML Classifier API",
    description="Query images sorted by ML inference scores and tag probabilities",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=False,
    allow_methods=["GET"],
    allow_headers=["*"],
)


# ── Endpoints ─────────────────────────────────────────────────────────────────

@app.get("/images")
def list_images(
    sort_field: str = Query(
        ...,
        description=(
            "MongoDB field path to sort by. Examples: "
            "'inferences.deepdanbooru_pixiv_private_nnpu_joblib.score', "
            "'importantTagProbs.deepdanbooru.bdsm'"
        ),
    ),
    sort_dir: str = Query(
        "desc",
        description="Sort direction: 'asc' or 'desc'",
        pattern="^(asc|desc)$",
    ),
    date: Optional[str] = Query(
        None,
        description="Filter by exact date (YYYY-MM-DD)",
    ),
    date_from: Optional[str] = Query(
        None,
        description="Filter by date >= (YYYY-MM-DD)",
    ),
    date_to: Optional[str] = Query(
        None,
        description="Filter by date <= (YYYY-MM-DD)",
    ),
    image_type: Optional[str] = Query(
        None,
        alias="type",
        description="Filter by image source type (e.g. 'pixiv', 'danbooru', 'gelbooru')",
        pattern="^[a-z]+$",
    ),
    page: int = Query(0, ge=0, description="Zero-based page index"),
    limit: int = Query(
        PAGE_SIZE_DEFAULT,
        ge=1,
        le=PAGE_SIZE_MAX,
        description=f"Items per page (max {PAGE_SIZE_MAX})",
    ),
):
    """
    Return a paginated list of inferred images sorted by an ML score or tag probability.

    Only images with ``status='inferred'`` are returned.
    """
    validated_field = _validate_sort_field(sort_field)

    # Build MongoDB filter
    mongo_filter: dict = {"status": "inferred"}

    if date:
        mongo_filter["date"] = _validate_date(date)
    else:
        date_range: dict = {}
        if date_from:
            date_range["$gte"] = _validate_date(date_from)
        if date_to:
            date_range["$lte"] = _validate_date(date_to)
        if date_range:
            mongo_filter["date"] = date_range

    if image_type:
        mongo_filter["type"] = image_type

    # Sort direction
    direction = ASCENDING if sort_dir == "asc" else DESCENDING

    db  = get_db()
    col = db["images"]

    # Only return documents that actually have the sort field populated
    mongo_filter[validated_field] = {"$exists": True}

    cursor = (
        col.find(mongo_filter, _EXCLUDED_FIELDS)
        .sort(validated_field, direction)
        .skip(page * limit)
        .limit(limit)
    )

    images = [_doc_to_dict(doc) for doc in cursor]

    return {
        "images": images,
        "page": page,
        "limit": limit,
        "count": len(images),
    }


@app.get("/images/{image_id}")
def get_image(image_id: str):
    """Return a single image document by its MongoDB ``_id``."""
    try:
        oid = ObjectId(image_id)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid image id")

    db  = get_db()
    col = db["images"]
    doc = col.find_one({"_id": oid}, _EXCLUDED_FIELDS)
    if doc is None:
        raise HTTPException(status_code=404, detail="Image not found")
    return _doc_to_dict(doc)


@app.get("/daily-counts")
def daily_counts(
    month: str = Query(
        ...,
        description="Month to aggregate (YYYY-MM)",
        pattern=r"^\d{4}-\d{2}$",
    ),
    image_type: Optional[str] = Query(
        None,
        alias="type",
        description="Filter by image source type (e.g. 'pixiv', 'danbooru', 'gelbooru')",
        pattern="^[a-z]+$",
    ),
):
    """
    Return the number of images stored per day for the given month.

    The ``date`` field in MongoDB is a ``YYYY-MM-DD`` string, so the query
    uses a prefix match (``^YYYY-MM``).  All statuses are counted.
    """
    mongo_filter: dict = {"date": {"$regex": f"^{month}"}}
    if image_type:
        mongo_filter["type"] = image_type

    db  = get_db()
    col = db["images"]

    pipeline = [
        {"$match": mongo_filter},
        {"$group": {"_id": "$date", "count": {"$sum": 1}}},
        {"$sort": {"_id": 1}},
    ]
    rows = list(col.aggregate(pipeline))

    days = {row["_id"]: row["count"] for row in rows if row["_id"]}
    return {"month": month, "days": days}


@app.get("/inference-models")
def list_inference_models():
    """
    Return the list of inference model keys available in the database,
    along with the value type (PU score or multiclass probabilities).
    """
    db  = get_db()
    col = db["images"]

    # Sample one fully-inferred document to discover available model keys
    sample = col.find_one(
        {"status": "inferred", "inferences": {"$exists": True}},
        {"inferences": 1},
    )
    if not sample or not sample.get("inferences"):
        return {"models": []}

    models = []
    for key, value in sample["inferences"].items():
        if isinstance(value, dict):
            if "score" in value:
                model_type = "pu"
                fields = ["score"]
            else:
                model_type = "legacy_multiclass"
                fields = list(value.keys())
        else:
            model_type = "unknown"
            fields = []
        models.append({"key": key, "type": model_type, "fields": fields})

    return {"models": sorted(models, key=lambda m: m["key"])}


@app.get("/important-tags")
def list_important_tags():
    """
    Return the list of important tag names available per feature type
    (deepdanbooru, pixai).
    """
    db  = get_db()
    col = db["images"]

    sample = col.find_one(
        {
            "status": "inferred",
            "importantTagProbs": {"$exists": True},
            "importantTagProbs.deepdanbooru": {"$exists": True, "$ne": {}},
        },
        {"importantTagProbs": 1},
    )
    if not sample or not sample.get("importantTagProbs"):
        return {"tags": {}}

    tags = {
        feature: list(tag_map.keys())
        for feature, tag_map in sample["importantTagProbs"].items()
        if isinstance(tag_map, dict)
    }
    return {"tags": tags}


@app.get("/health")
def health():
    """Simple health check."""
    return {"status": "ok"}
