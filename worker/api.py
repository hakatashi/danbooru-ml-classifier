#!/usr/bin/env python3
"""
Thin API server for danbooru-ml-classifier.

Exposes MongoDB image data sorted by importantTagProbs or inferences values,
filtered by date. Intended for use by the public website.

Usage:
    venv/bin/uvicorn api:app --host 0.0.0.0 --port 8766
"""

import base64
import os
import re
import time
import urllib.parse
import uuid
from datetime import datetime
from pathlib import Path
from typing import Literal, Optional

import requests as http_requests
from dotenv import load_dotenv

load_dotenv()
from bson import ObjectId
from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field, field_validator
from pymongo import MongoClient, ASCENDING, DESCENDING, UpdateOne

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

# Firebase Hosting preview deploy URLs (e.g. https://danbooru-ml-classifier--pr196-feature-qdrant-simil-bfwdlmhi.web.app)
ALLOWED_ORIGIN_REGEX = r"https://danbooru-ml-classifier--[\w-]+\.web\.app"

PAGE_SIZE_DEFAULT = 50
PAGE_SIZE_MAX     = 200

# Firebase auth (used by /favorites/* write & read endpoints, which are
# private -- everything else in this API remains public and unauthenticated)
ALLOWED_EMAIL = os.environ.get("ALLOWED_EMAIL", "hakatasiloving@gmail.com")
FIREBASE_CRED_PATH = os.environ.get(
    "FIREBASE_CRED_PATH",
    str(Path(__file__).resolve().parent.parent / "danbooru-ml-classifier-firebase-adminsdk-uivsj-3a07a63db5.json"),
)

DANBOORU_API_USER = os.environ.get("DANBOORU_API_USER", "")
DANBOORU_API_KEY  = os.environ.get("DANBOORU_API_KEY", "")
GELBOORU_API_USER = os.environ.get("GELBOORU_API_USER", "")
GELBOORU_API_KEY  = os.environ.get("GELBOORU_API_KEY", "")

QDRANT_HOST       = os.environ.get("QDRANT_HOST", "localhost")
QDRANT_PORT       = int(os.environ.get("QDRANT_PORT", "6333"))
QDRANT_COLLECTION           = "image_embeddings"
QDRANT_COLLECTION_MULTIAXIS = "image_embeddings_multiaxis"
VALID_AXES = {"eva02", "character", "situation", "style"}

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


# ── Qdrant client (lazy singleton) ───────────────────────────────────────────

_qdrant_client = None

def _get_qdrant_client():
    global _qdrant_client
    if _qdrant_client is None:
        from qdrant_client import QdrantClient
        _qdrant_client = QdrantClient(host=QDRANT_HOST, port=QDRANT_PORT, timeout=10)
    return _qdrant_client


def _mongo_id_to_qdrant_uuid(mongo_id: str) -> str:
    """Deterministically convert a MongoDB ObjectId hex string to a UUID string."""
    return str(uuid.uuid5(uuid.NAMESPACE_OID, mongo_id))


# ── Firebase auth (lazy singleton) ───────────────────────────────────────────
# Protects /favorites/* only. Initialising firebase_admin eagerly at import
# time would make every local import (tests, other scripts) touch Firebase,
# so this mirrors the _get_qdrant_client() lazy-init idiom above.

_firebase_app = None


def _get_firebase_app():
    global _firebase_app
    if _firebase_app is None:
        import firebase_admin
        from firebase_admin import credentials

        if firebase_admin._apps:
            _firebase_app = firebase_admin.get_app()
        else:
            _firebase_app = firebase_admin.initialize_app(credentials.Certificate(FIREBASE_CRED_PATH))
    return _firebase_app


_bearer = HTTPBearer(auto_error=False)


def require_admin(
    cred: Optional[HTTPAuthorizationCredentials] = Depends(_bearer),
) -> dict:
    """FastAPI dependency: require a valid Firebase ID token for ALLOWED_EMAIL.

    Applied to every /favorites/* route (reads included -- the favorites list
    is private taste data, not a public resource like /images).
    """
    if cred is None or not cred.credentials:
        raise HTTPException(
            status_code=401,
            detail="Missing bearer token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    from firebase_admin import auth as fb_auth

    _get_firebase_app()
    try:
        decoded = fb_auth.verify_id_token(cred.credentials)
    except fb_auth.ExpiredIdTokenError:
        raise HTTPException(status_code=401, detail="Token expired")
    except Exception:
        raise HTTPException(status_code=401, detail="Invalid token")

    if not decoded.get("email_verified") or decoded.get("email") != ALLOWED_EMAIL:
        raise HTTPException(status_code=403, detail="Forbidden")

    return decoded


# ── Rank cache ────────────────────────────────────────────────────────────────
# Maps (date, sort_field) -> list of image_id strings sorted descending by score.
# This lets O(1) rank lookup after the first request for a given date+field.

_rank_cache: dict = {}       # (date, sort_field) -> [image_id, ...]
_rank_cache_ts: dict = {}    # (date, sort_field) -> float (unix timestamp)
RANK_CACHE_TTL = 3600        # seconds


def _get_sorted_ids(date: str, sort_field: str) -> list:
    """
    Return all image IDs for the given date sorted descending by sort_field.
    Results are cached for RANK_CACHE_TTL seconds.
    """
    key = (date, sort_field)
    now = time.time()
    if key in _rank_cache and now - _rank_cache_ts.get(key, 0) < RANK_CACHE_TTL:
        return _rank_cache[key]

    db = get_db()
    col = db["images"]
    cursor = col.find(
        {"status": "inferred", "date": date, sort_field: {"$exists": True}},
        {"_id": 1},
    ).sort(sort_field, DESCENDING)

    ids = [str(doc["_id"]) for doc in cursor]
    _rank_cache[key] = ids
    _rank_cache_ts[key] = now
    return ids


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


# ── Image id resolution (favorites-only helpers) ─────────────────────────────
# `images._id` is an ObjectId for cron-ingested docs and a plain string (the
# original Firestore document id) for legacy Firestore imports -- see
# publisher/scripts/import-firestore.ts. No 24-hex id is known to also exist
# as a string `_id`, so trying ObjectId first is unambiguous.

_OBJECT_ID_HEX_RE = re.compile(r"^[0-9a-f]{24}$")


def _resolve_image_id(col, image_id: str):
    """Return the actual `_id` value (ObjectId or str) present in `images`, or None."""
    if _OBJECT_ID_HEX_RE.match(image_id):
        oid = ObjectId(image_id)
        if col.count_documents({"_id": oid}, limit=1):
            return oid
    if col.count_documents({"_id": image_id}, limit=1):
        return image_id
    return None


def _resolve_image_ids(col, image_ids: list) -> tuple:
    """Bulk-resolve a list of id strings.

    Returns (mapping, not_found) where mapping maps each resolvable original
    id string to the actual `_id` value stored in Mongo (ObjectId or str).
    """
    hex_ids = [i for i in image_ids if _OBJECT_ID_HEX_RE.match(i)]
    other_ids = [i for i in image_ids if not _OBJECT_ID_HEX_RE.match(i)]

    mapping: dict = {}
    if hex_ids:
        oid_by_orig = {i: ObjectId(i) for i in hex_ids}
        found = col.find({"_id": {"$in": list(oid_by_orig.values())}}, {"_id": 1})
        matched_oids = {doc["_id"] for doc in found}
        for orig, oid in oid_by_orig.items():
            if oid in matched_oids:
                mapping[orig] = oid
            else:
                # Not found as ObjectId -- fall back to a string `_id` match.
                other_ids.append(orig)

    if other_ids:
        found = col.find({"_id": {"$in": other_ids}}, {"_id": 1})
        matched_strs = {doc["_id"] for doc in found}
        for orig in other_ids:
            if orig in matched_strs:
                mapping[orig] = orig

    not_found = [i for i in image_ids if i not in mapping]
    return mapping, not_found


def _get_path(doc: dict, path: str):
    """Resolve a dotted field path against a (possibly nested) dict."""
    cur = doc
    for part in path.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return None
        cur = cur[part]
    return cur


# ── Favorites sort field validation & projections ────────────────────────────

_FAVORITES_EXTRA_SORT_FIELDS = {"favorites.favoritedAt", "favorites.updatedAt", "date"}


def _validate_favorites_sort_field(field: str) -> str:
    """Extend (never loosen) _validate_sort_field with favorites-only fields."""
    if field in _FAVORITES_EXTRA_SORT_FIELDS:
        return field
    return _validate_sort_field(field)


# Lean projections: favorites queries scan the whole (small) favorited set
# in-process, so keep documents small by dropping large fields (raw tag
# scores, captions, etc.) that the favorites/gallery UI never renders.
_FAVORITES_PROJECTION = {
    "key": 1,
    "type": 1,
    "status": 1,
    "date": 1,
    "width": 1,
    "height": 1,
    "postId": 1,
    "favorites": 1,
    "inferences": 1,
    "importantTagProbs": 1,
    "moderations": 1,
    "ageEstimations": 1,
    "source": 1,
}
_FAVORITES_POOL_PROJECTION = {"key": 1, "type": 1, "width": 1, "height": 1}


# ── App ───────────────────────────────────────────────────────────────────────

app = FastAPI(
    title="Danbooru ML Classifier API",
    description="Query images sorted by ML inference scores and tag probabilities",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_origin_regex=ALLOWED_ORIGIN_REGEX,
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS"],
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
    Response includes ``total`` (full count) in addition to ``count`` (items on this page).
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

    total = col.count_documents(mongo_filter)

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
        "total": total,
    }


@app.get("/images/{image_id}")
def get_image(image_id: str):
    """
    Return a single image document by its MongoDB ``_id``.

    Also returns ``scoreRanks``: for each inference field that has a score and
    the image has a ``date``, provides the 1-based rank among all images on that
    date sorted descending by that field, plus the total count.
    Ranks are derived from a cache (TTL = 1 hour) to avoid repeated DB scans.
    """
    db  = get_db()
    col = db["images"]

    resolved_id = _resolve_image_id(col, image_id)
    if resolved_id is None:
        raise HTTPException(status_code=404, detail="Image not found")

    doc = col.find_one({"_id": resolved_id}, _EXCLUDED_FIELDS)
    if doc is None:
        raise HTTPException(status_code=404, detail="Image not found")

    result = _doc_to_dict(doc)

    # Compute score ranks for all inference fields
    date = doc.get("date")
    score_ranks: dict = {}
    if date and doc.get("inferences"):
        image_id_str = str(doc["_id"])
        for model_key, model_data in doc["inferences"].items():
            if not isinstance(model_data, dict):
                continue
            for field_name, value in model_data.items():
                if not isinstance(value, (int, float)):
                    continue
                sort_field = f"inferences.{model_key}.{field_name}"
                sorted_ids = _get_sorted_ids(date, sort_field)
                try:
                    rank = sorted_ids.index(image_id_str) + 1
                except ValueError:
                    rank = None
                score_ranks[sort_field] = {
                    "rank": rank,
                    "total": len(sorted_ids),
                }

    result["scoreRanks"] = score_ranks
    return result


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
    mongo_filter: dict = {"date": {"$regex": f"^{month}"}, "status": "inferred"}
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
    Samples the document with the most recent ``date`` value.
    """
    db  = get_db()
    col = db["images"]

    # Use the most recently dated inferred document
    sample = col.find_one(
        {"status": "inferred", "inferences": {"$exists": True}},
        {"inferences": 1},
        sort=[("date", DESCENDING)],
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
    Samples the document with the most recent ``date`` value.
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
        sort=[("date", DESCENDING)],
    )
    if not sample or not sample.get("importantTagProbs"):
        return {"tags": {}}

    tags = {
        feature: list(tag_map.keys())
        for feature, tag_map in sample["importantTagProbs"].items()
        if isinstance(tag_map, dict)
    }
    return {"tags": tags}


@app.get("/images/{image_id}/similar")
def get_similar_images(
    image_id: str,
    limit: int = Query(20, ge=1, le=100, description="Number of similar images to return"),
    status: Optional[str] = Query(
        "inferred",
        description="Comma-separated list of status values to filter by (e.g. 'inferred' or 'inferred,pending')",
    ),
    date: Optional[str] = Query(None, description="Filter results to a specific date (YYYY-MM-DD)"),
    image_type: Optional[str] = Query(
        None,
        alias="type",
        description="Filter results by image source type (e.g. 'pixiv', 'danbooru')",
        pattern="^[a-z]+$",
    ),
    axis: Optional[str] = Query(
        None,
        description=(
            "Similarity axis: 'eva02' (default, general visual), "
            "'character' (hair/body/clothing), "
            "'situation' (pose/composition/scenario), "
            "'style' (art style/medium/shading)"
        ),
        pattern="^[a-z0-9_]+$",
    ),
):
    """
    Return images similar to the given image along a chosen similarity axis.

    - (default / axis=eva02) General visual similarity via EVA02 1024-dim embedding.
    - axis=character  Similar character appearance (hair, eyes, body, clothing).
    - axis=situation  Similar pose, composition, or scenario.
    - axis=style      Similar art style, medium, or shading.

    character/situation/style axes use the multiaxis Qdrant collection populated by
    backfill_multiaxis_qdrant.py; the query image must have been indexed there first.
    """
    try:
        oid = ObjectId(image_id)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid image id")

    # Validate axis
    resolved_axis = axis or "eva02"
    if resolved_axis not in VALID_AXES:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid axis '{resolved_axis}'. Valid values: {sorted(VALID_AXES)}",
        )

    use_multiaxis = resolved_axis != "eva02"

    # Build Qdrant filter
    try:
        from qdrant_client.models import Filter, FieldCondition, MatchValue, MatchAny

        must_conditions = []

        if status:
            status_list = [s.strip() for s in status.split(",") if s.strip()]
            if len(status_list) == 1:
                must_conditions.append(
                    FieldCondition(key="status", match=MatchValue(value=status_list[0]))
                )
            elif len(status_list) > 1:
                must_conditions.append(
                    FieldCondition(key="status", match=MatchAny(any=status_list))
                )

        if date:
            _validate_date(date)
            must_conditions.append(
                FieldCondition(key="date", match=MatchValue(value=date))
            )

        if image_type:
            must_conditions.append(
                FieldCondition(key="type", match=MatchValue(value=image_type))
            )

        query_filter = Filter(must=must_conditions) if must_conditions else None

        qdrant = _get_qdrant_client()
        qdrant_uuid = _mongo_id_to_qdrant_uuid(image_id)

        collection = QDRANT_COLLECTION_MULTIAXIS if use_multiaxis else QDRANT_COLLECTION
        query_kwargs: dict = {
            "collection_name": collection,
            "query": qdrant_uuid,
            "query_filter": query_filter,
            "limit": limit + 1,
            "with_payload": True,
            "with_vectors": False,
        }
        if use_multiaxis:
            query_kwargs["using"] = resolved_axis

        response = qdrant.query_points(**query_kwargs)
        results = response.points

    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Qdrant unavailable: {exc}")

    # Extract MongoDB IDs, excluding the query image itself
    similar: list[dict] = []
    for r in results:
        payload   = r.payload or {}
        result_id = payload.get("image_id", "")
        if result_id == image_id:
            continue
        similar.append({"image_id": result_id, "similarity": r.score})
        if len(similar) >= limit:
            break

    if not similar:
        return {"similar": [], "total": 0}

    # Fetch MongoDB documents for the similar images
    db  = get_db()
    col = db["images"]
    similar_oids = [ObjectId(s["image_id"]) for s in similar if s["image_id"]]
    docs_by_id = {
        str(doc["_id"]): _doc_to_dict(doc)
        for doc in col.find({"_id": {"$in": similar_oids}}, _EXCLUDED_FIELDS)
    }

    # Merge similarity score into each document, preserving rank order
    result_docs = []
    for s in similar:
        doc = docs_by_id.get(s["image_id"])
        if doc:
            doc["similarity"] = round(s["similarity"], 6)
            result_docs.append(doc)

    return {"similar": result_docs, "total": len(result_docs)}


# ── Favorites ─────────────────────────────────────────────────────────────────
# All /favorites* routes require Depends(require_admin) -- see the startup
# assertion at the bottom of this file, which fails fast if any route here
# is missing it.


@app.get("/favorites")
def list_favorites(
    _admin: dict = Depends(require_admin),
    sort_field: str = Query("favorites.favoritedAt"),
    sort_dir: str = Query("desc", pattern="^(asc|desc)$"),
    category: Optional[str] = Query(None, max_length=100, description="Category name, or '__none__' for uncategorized"),
    image_type: Optional[str] = Query(None, alias="type", pattern="^[a-z]+$"),
    date_from: Optional[str] = Query(None, description="Filter by date >= (YYYY-MM-DD)"),
    date_to: Optional[str] = Query(None, description="Filter by date <= (YYYY-MM-DD)"),
    include_undated: bool = Query(False, description="Include favorites with no `date` field when a date range is set"),
    page: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=500),
):
    """
    Return the caller's favorited images.

    Unlike GET /images, this endpoint does NOT force status == "inferred" and
    does NOT require the sort field to exist on every document -- doing so
    would silently drop legacy Twitter favorites (status="liked", no `date`
    or `inferences`). Sorting happens in-process over the (small) favorited
    set rather than via a MongoDB blocking sort, since documents can be up to
    ~45KB each and there are 100+ candidate sort fields (inference model
    scores, tag probabilities) that are impractical to all index.
    """
    validated_field = _validate_favorites_sort_field(sort_field)

    mongo_filter: dict = {"favorites.isFavorited": True}
    if image_type:
        mongo_filter["type"] = image_type
    if category == "__none__":
        mongo_filter["favorites.categories"] = {"$size": 0}
    elif category:
        mongo_filter["favorites.categories"] = category

    date_range: dict = {}
    if date_from:
        date_range["$gte"] = _validate_date(date_from)
    if date_to:
        date_range["$lte"] = _validate_date(date_to)
    if date_range:
        if include_undated:
            mongo_filter["$or"] = [{"date": date_range}, {"date": {"$exists": False}}]
        else:
            mongo_filter["date"] = date_range

    db  = get_db()
    col = db["images"]

    docs = list(col.find(mongo_filter, _FAVORITES_PROJECTION))

    has_field = [d for d in docs if _get_path(d, validated_field) is not None]
    missing_field = [d for d in docs if _get_path(d, validated_field) is None]
    has_field.sort(key=lambda d: _get_path(d, validated_field), reverse=(sort_dir == "desc"))
    docs = has_field + missing_field

    total = len(docs)
    paged = docs[page * limit : page * limit + limit]
    images = [_doc_to_dict(doc) for doc in paged]

    return {
        "images": images,
        "page": page,
        "limit": limit,
        "count": len(images),
        "total": total,
    }


@app.get("/favorites/categories")
def list_favorite_categories(_admin: dict = Depends(require_admin)):
    """Return category counts and source-type counts among favorited images."""
    db  = get_db()
    col = db["images"]

    category_rows = list(
        col.aggregate(
            [
                {"$match": {"favorites.isFavorited": True}},
                {"$unwind": "$favorites.categories"},
                {"$sortByCount": "$favorites.categories"},
            ]
        )
    )
    categories = [{"name": row["_id"], "count": row["count"]} for row in category_rows]

    type_rows = list(
        col.aggregate(
            [
                {"$match": {"favorites.isFavorited": True}},
                {"$group": {"_id": "$type", "count": {"$sum": 1}}},
                {"$sort": {"count": -1}},
            ]
        )
    )
    types = [{"name": row["_id"], "count": row["count"]} for row in type_rows if row["_id"]]

    total = col.count_documents({"favorites.isFavorited": True})
    uncategorized_count = col.count_documents({"favorites.isFavorited": True, "favorites.categories": {"$size": 0}})

    return {
        "categories": categories,
        "uncategorizedCount": uncategorized_count,
        "types": types,
        "total": total,
    }


@app.get("/favorites/pool")
def get_favorites_pool(
    _admin: dict = Depends(require_admin),
    image_type: Optional[str] = Query(None, alias="type", pattern="^[a-z]+$"),
    category: Optional[str] = Query(None, max_length=100),
):
    """
    Return the full favorited-image pool (lean projection: id/key/type/width/height).

    Intended to be fetched once per session by the random Gallery viewer,
    which shuffles it client-side for no-repeat, low-latency navigation.
    """
    mongo_filter: dict = {"favorites.isFavorited": True}
    if image_type:
        mongo_filter["type"] = image_type
    if category:
        mongo_filter["favorites.categories"] = category

    db  = get_db()
    col = db["images"]
    docs = list(col.find(mongo_filter, _FAVORITES_POOL_PROJECTION))
    images = [_doc_to_dict(doc) for doc in docs]
    return {"images": images, "total": len(images)}


@app.get("/favorites/random")
def get_random_favorites(
    _admin: dict = Depends(require_admin),
    limit: int = Query(10, ge=1, le=100),
    exclude: str = Query("", description="Comma-separated ids to exclude (max 5000)"),
    image_type: Optional[str] = Query(None, alias="type", pattern="^[a-z]+$"),
    category: Optional[str] = Query(None, max_length=100),
):
    """
    Return `limit` random favorited images via $sample, excluding `exclude`.

    Documented fallback for when the favorited pool outgrows a single
    GET /favorites/pool response; the frontend's primary path is the pool.
    """
    mongo_filter: dict = {"favorites.isFavorited": True}
    if image_type:
        mongo_filter["type"] = image_type
    if category:
        mongo_filter["favorites.categories"] = category

    db  = get_db()
    col = db["images"]

    exclude_ids = [i for i in exclude.split(",") if i][:5000]
    if exclude_ids:
        mapping, _ = _resolve_image_ids(col, exclude_ids)
        resolved_ids = list(mapping.values())
        if resolved_ids:
            mongo_filter["_id"] = {"$nin": resolved_ids}

    pipeline = [
        {"$match": mongo_filter},
        {"$sample": {"size": limit}},
        {"$project": _FAVORITES_POOL_PROJECTION},
    ]
    docs = list(col.aggregate(pipeline))
    images = [_doc_to_dict(doc) for doc in docs]
    return {"images": images, "total": len(images)}


class FavoritesLookupRequest(BaseModel):
    ids: list[str] = Field(min_length=1, max_length=500)


@app.post("/favorites/lookup")
def lookup_favorites(body: FavoritesLookupRequest, _admin: dict = Depends(require_admin)):
    """
    Return the favorites state for arbitrary image ids.

    POST (not GET) because several legacy favorites have percent-encoded
    string ids (e.g. "twitter%2Fabc.jpg") that make long query strings
    unwieldy. Used by the Firestore-backed views to paint heart buttons.
    """
    db  = get_db()
    col = db["images"]

    mapping, not_found = _resolve_image_ids(col, body.ids)
    orig_by_resolved = {v: k for k, v in mapping.items()}

    favorites: dict = {}
    if mapping:
        docs = col.find({"_id": {"$in": list(mapping.values())}}, {"favorites": 1})
        for doc in docs:
            orig_id = orig_by_resolved.get(doc["_id"])
            if orig_id is None:
                continue
            fav = doc.get("favorites")
            if fav:
                favorites[orig_id] = _doc_to_dict(fav)

    return {"favorites": favorites, "notFound": not_found}


class FavoritesUpdateRequest(BaseModel):
    ids: list[str] = Field(min_length=1, max_length=500)
    op: Literal["toggle", "add", "remove", "set"]
    categories: list[str] = Field(default_factory=list, max_length=50)

    @field_validator("categories")
    @classmethod
    def _validate_categories(cls, v: list[str]) -> list[str]:
        seen = set()
        for c in v:
            if not c or len(c) > 100 or c != c.strip():
                raise ValueError("each category must be 1..100 chars with no leading/trailing whitespace")
            if c in seen:
                raise ValueError(f"duplicate category: {c!r}")
            seen.add(c)
        return v

    @field_validator("ids")
    @classmethod
    def _validate_ids(cls, v: list[str]) -> list[str]:
        if len(set(v)) != len(v):
            raise ValueError("duplicate ids")
        return v


@app.post("/favorites/update")
def update_favorites(body: FavoritesUpdateRequest, _admin: dict = Depends(require_admin)):
    """
    Add/remove/set/toggle categories on one or many images in a single call.

    Ids travel in the JSON body rather than as path parameters: several
    legacy favorites have ids like "twitter%2Fabc.jpg", and Starlette
    percent-decodes path parameters before routing, which would turn a path
    like /favorites/twitter%2Fabc.jpg into the two-segment (and 404ing)
    path /favorites/twitter/abc.jpg.

    `favoritedAt` is preserved across category edits and only set on the
    0 -> 1 "isFavorited" transition, so sorting by it means "first favorited".
    Unfavoriting is expressed as `op: "set", categories: []`.
    """
    if body.op == "toggle" and (len(body.ids) != 1 or len(body.categories) != 1):
        raise HTTPException(status_code=400, detail="'toggle' requires exactly 1 id and exactly 1 category")

    db  = get_db()
    col = db["images"]

    mapping, not_found = _resolve_image_ids(col, body.ids)
    if not mapping:
        return {"updated": [], "notFound": not_found, "count": 0}

    resolved_ids = list(mapping.values())
    existing_by_resolved = {
        doc["_id"]: (doc.get("favorites") or {}) for doc in col.find({"_id": {"$in": resolved_ids}}, {"favorites": 1})
    }

    now = datetime.utcnow()
    orig_by_resolved = {v: k for k, v in mapping.items()}
    ops = []
    updated_by_orig: dict = {}

    for resolved_id in resolved_ids:
        existing = existing_by_resolved.get(resolved_id, {})
        existing_categories = existing.get("categories") or []
        existing_favorited_at = existing.get("favoritedAt")

        if body.op == "toggle":
            category = body.categories[0]
            if category in existing_categories:
                new_categories = [c for c in existing_categories if c != category]
            else:
                new_categories = [*existing_categories, category]
        elif body.op == "add":
            new_categories = list(existing_categories)
            for c in body.categories:
                if c not in new_categories:
                    new_categories.append(c)
        elif body.op == "remove":
            remove_set = set(body.categories)
            new_categories = [c for c in existing_categories if c not in remove_set]
        else:  # "set"
            new_categories = list(body.categories)

        is_favorited = len(new_categories) > 0
        favorites = {
            "isFavorited": is_favorited,
            "categories": new_categories,
            "favoritedAt": (existing_favorited_at or now) if is_favorited else None,
            "updatedAt": now,
        }

        ops.append(UpdateOne({"_id": resolved_id}, {"$set": {"favorites": favorites}}))

        orig_id = orig_by_resolved[resolved_id]
        updated_by_orig[orig_id] = {
            "id": orig_id,
            "isFavorited": favorites["isFavorited"],
            "categories": favorites["categories"],
            "favoritedAt": favorites["favoritedAt"].isoformat() if favorites["favoritedAt"] else None,
            "updatedAt": favorites["updatedAt"].isoformat(),
        }

    if ops:
        col.bulk_write(ops, ordered=False)

    updated = [updated_by_orig[i] for i in body.ids if i in updated_by_orig]

    return {"updated": updated, "notFound": not_found, "count": len(updated)}


@app.get("/health")
def health():
    """Simple health check."""
    return {"status": "ok"}


def _pixiv_image_url_to_artwork(source: Optional[str]) -> Optional[str]:
    """Convert a Pixiv image URL to its artwork page URL if applicable."""
    if not source:
        return source
    m = re.search(r"pximg\.net/.*/(\d+)_p\d+", source)
    if m:
        return f"https://www.pixiv.net/artworks/{m.group(1)}"
    return source


@app.get("/post-source")
def get_post_source(
    provider: str = Query(..., description="Image provider: 'danbooru' or 'gelbooru'", pattern="^(danbooru|gelbooru)$"),
    id: str = Query(..., description="Post ID (numeric string)", pattern=r"^\d+$"),
):
    """
    Fetch the 'source' field from the Danbooru or Gelbooru API for the given post.

    Returns the source URL (which may point to Pixiv artwork or another site),
    or null if no source is available.
    """
    headers = {"User-Agent": "danbooru-ml-classifier/1.0"}

    if provider == "danbooru":
        url = f"https://danbooru.donmai.us/posts/{id}.json"
        if DANBOORU_API_USER and DANBOORU_API_KEY:
            creds = base64.b64encode(f"{DANBOORU_API_USER}:{DANBOORU_API_KEY}".encode()).decode()
            headers["Authorization"] = f"Basic {creds}"
        try:
            resp = http_requests.get(url, headers=headers, timeout=10)
            resp.raise_for_status()
            data = resp.json()
        except http_requests.RequestException as e:
            raise HTTPException(status_code=502, detail=f"Failed to fetch from Danbooru: {e}")
        source = data.get("source") or None
        return {"source": _pixiv_image_url_to_artwork(source)}

    if provider == "gelbooru":
        params: dict = {"page": "dapi", "s": "post", "q": "index", "json": "1", "id": id}
        if GELBOORU_API_USER and GELBOORU_API_KEY:
            params["api_key"] = GELBOORU_API_KEY
            params["user_id"] = GELBOORU_API_USER
        url = "https://gelbooru.com/index.php?" + urllib.parse.urlencode(params)
        try:
            resp = http_requests.get(url, headers=headers, timeout=10)
            resp.raise_for_status()
            data = resp.json()
        except http_requests.RequestException as e:
            raise HTTPException(status_code=502, detail=f"Failed to fetch from Gelbooru: {e}")
        posts = data.get("post", [])
        if not posts:
            return {"source": None}
        source = posts[0].get("source") or None
        return {"source": _pixiv_image_url_to_artwork(source)}


# ── Startup safety check ─────────────────────────────────────────────────────
# Runs at import time (module load), before uvicorn starts accepting
# connections. Fails fast if a /favorites route is ever added without
# Depends(require_admin) -- one missed dependency would make the favorites
# list world-readable.


def _assert_favorites_routes_protected() -> None:
    for route in app.routes:
        path = getattr(route, "path", "")
        if not path.startswith("/favorites"):
            continue
        dependant = getattr(route, "dependant", None)
        deps = getattr(dependant, "dependencies", []) if dependant else []
        if not any(getattr(d, "call", None) is require_admin for d in deps):
            raise RuntimeError(f"Route {path} is missing Depends(require_admin)")


_assert_favorites_routes_protected()
