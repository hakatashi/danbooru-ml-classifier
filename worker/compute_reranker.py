#!/usr/bin/env python3
"""
Score a date's stage-1 candidate pool (Libra ensemble top-K) with the
trained stage-2 re-ranker (reranker.py / pu-learning/scripts/train_reranker.py)
and write `inferences.reranker_v1.score` for just those images.

Mirrors compute_ensembles.py's shape, but writes scores only for the day's
stage-1 pool (CANDIDATE_K images), not every inferred image that date --
this is the whole point of the two-stage design (plan section 1.5.3): the
model was trained to only ever see this restricted population, and scoring
it on the full day would reproduce the collapse that finding describes.
Images outside the pool simply have no `inferences.reranker_v1` field, same
as any other model that hasn't scored them (worker/api.py's sort already
requires `{$exists: True}`).

Usage:
    cd worker
    venv/bin/python compute_reranker.py --date-from 2026-07-01 --date-to 2026-08-04
    venv/bin/python compute_reranker.py --all
    venv/bin/python compute_reranker.py --date-from 2026-08-01 --date-to 2026-08-04 --dry-run

Options:
    --date-from DATE    Start date, inclusive (YYYY-MM-DD)
    --date-to DATE      End date, inclusive (YYYY-MM-DD)
    --all               Compute for every distinct date with inferred documents
                         (ignores --date-from/--date-to)
    --dry-run           Print counts without writing to MongoDB
"""

import argparse
import logging
import os
import sys
from pathlib import Path

import joblib
from pymongo import MongoClient

WORKER_DIR = Path(__file__).parent
sys.path.insert(0, str(WORKER_DIR))

import reranker as rr  # noqa: E402
from ensembles import ENSEMBLES  # noqa: E402

PU_DIR = WORKER_DIR.parent / "pu-learning"
MODELS_DIR = PU_DIR / "data" / "models"
MODEL_PATH = MODELS_DIR / f"{rr.MODEL_KEY}.joblib"
SPEC_PATH = MODELS_DIR / f"{rr.MODEL_KEY}_feature_spec.json"

MONGODB_URI = os.environ.get("MONGODB_URI", "mongodb://localhost:27017")
MONGODB_DB  = os.environ.get("MONGODB_DB", "danbooru-ml-classifier")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger(__name__)


def _dates_in_range(col, date_from: str | None, date_to: str | None) -> list[str]:
    mongo_filter: dict = {"status": "inferred"}
    date_range: dict = {}
    if date_from:
        date_range["$gte"] = date_from
    if date_to:
        date_range["$lte"] = date_to
    if date_range:
        mongo_filter["date"] = date_range
    return sorted(col.distinct("date", mongo_filter))


def compute_for_date(db, model, spec: dict, date: str, dry_run: bool = False) -> int:
    pool_ids = rr.stage1_pool_ids(db, date, ENSEMBLES, spec["candidate_ensemble"], spec["candidate_k"])
    if not pool_ids:
        return 0
    docs_by_id = rr.fetch_pool_docs(db, pool_ids)
    X = rr.build_feature_matrix(pool_ids, docs_by_id, date, spec)
    scores = model.predict_proba(X)[:, 1]

    if dry_run:
        return len(pool_ids)

    from bson import ObjectId
    from pymongo import UpdateOne

    ops = [
        UpdateOne({"_id": ObjectId(mid)}, {"$set": {f"inferences.{rr.MODEL_KEY}.score": float(score)}})
        for mid, score in zip(pool_ids, scores)
    ]
    if ops:
        db["images"].bulk_write(ops, ordered=False)
    return len(pool_ids)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--date-from", type=str, default=None)
    parser.add_argument("--date-to", type=str, default=None)
    parser.add_argument("--all", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if not args.all and not args.date_from and not args.date_to:
        parser.error("specify --date-from/--date-to or --all")

    if not MODEL_PATH.exists() or not SPEC_PATH.exists():
        log.error("No trained reranker at %s / %s -- run pu-learning/scripts/train_reranker.py first.", MODEL_PATH, SPEC_PATH)
        sys.exit(1)

    model = joblib.load(MODEL_PATH)
    spec = rr.load_feature_spec(SPEC_PATH)

    client = MongoClient(MONGODB_URI)
    db = client[MONGODB_DB]
    col = db["images"]

    if args.all:
        dates = sorted(col.distinct("date", {"status": "inferred"}))
    else:
        dates = _dates_in_range(col, args.date_from, args.date_to)

    log.info("Scoring %d date(s) with %s%s", len(dates), rr.MODEL_KEY, " (dry-run)" if args.dry_run else "")
    if not dates:
        log.info("Nothing to do.")
        return

    total = 0
    for i, date in enumerate(dates):
        n = compute_for_date(db, model, spec, date, dry_run=args.dry_run)
        total += n
        if (i + 1) % 20 == 0:
            log.info(" ... %d/%d dates processed", i + 1, len(dates))

    log.info("Done. Scored %d images total across %d date(s).", total, len(dates))


if __name__ == "__main__":
    main()
