#!/usr/bin/env python3
"""
Delete on-disk image files for old, low-ranked images to reclaim disk space,
keeping MongoDB metadata (inferences, favorites, source info, ...) intact.

For each date older than the cutoff, keeps ~10% of that day's images and
deletes the local file (only the file -- the MongoDB doc is kept, with
`localPath` cleared and `imageDeleted`/`imageDeletedAt` set) for the rest.
Kept images are chosen by:

  1. Favorited images (favorites.isFavorited) -- always kept.
  2. Images without images.features.stored=true -- always kept. Deleting the
     file before its deepdanbooru/eva02/pixai vectors are persisted to the
     HDF5 feature store (worker/feature_store.py) would permanently drop
     that image from the recommendation-improvement plan's feature-store
     backfill (see pu-learning/reports/recommendation_improvement_plan.md
     section 3.2) -- this is the rule that keeps this script from
     conflicting with that plan.
  3. From the remaining quota, a weighted rank-union across the seven named
     sort tabs (public/src/config/namedSorts.ts), favoring the tabs actually
     browsed day to day (Gemini x5, Libra x3, the rest x1 each).

Only images with status='inferred' and a non-null localPath are eligible for
deletion; everything else (not yet inferred, already deduped/pruned) is left
untouched. Safe to re-run: already-pruned images (localPath=null) are simply
skipped, and the keep/delete split is recomputed fresh from current state
each time (monotonic -- a file is never "un-deleted").

Usage:
    cd worker
    venv/bin/python prune_old_images.py --dry-run
    venv/bin/python prune_old_images.py
    venv/bin/python prune_old_images.py --older-than-days 60
    venv/bin/python prune_old_images.py --date-from 2026-01-01 --date-to 2026-03-31

Options:
    --older-than-days N   Process dates older than N days ago (default: 30).
                           Ignored if --date-from/--date-to is given.
    --date-from DATE       Start date, inclusive (YYYY-MM-DD)
    --date-to DATE         End date, inclusive (YYYY-MM-DD)
    --dry-run              Print per-date counts without deleting anything

Before the first real run, make sure ensemble_virgo_v1/ensemble_libra_v1 are
computed for old dates too (compute_ensembles.py --all) -- dates missing
those scores just get a smaller effective rank-union (fewer of the seven
tabs contribute), not an error.
"""

import argparse
import logging
import math
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from pymongo import MongoClient, UpdateOne

MONGODB_URI = os.environ.get("MONGODB_URI", "mongodb://localhost:27017")
MONGODB_DB  = os.environ.get("MONGODB_DB", "danbooru-ml-classifier")

RETAIN_RATIO           = 0.10
DEFAULT_OLDER_THAN_DAYS = 30

# Weighted rank-union across public/src/config/namedSorts.ts's seven tabs.
# Gemini and Libra are the tabs actually browsed day to day (see CLAUDE.md /
# recommendation_improvement_plan.md); the rest get equal, lower weight.
TAB_WEIGHTS: dict[str, int] = {
    "eva02_pixiv_private_nnpu_joblib":            5,  # Gemini
    "ensemble_libra_v1":                          3,  # Libra
    "eva02_twitter_elkan_noto_joblib":            1,  # Aries
    "deepdanbooru_twitter_biased_svm_joblib":     1,  # Taurus
    "pixai_pixiv_private_elkan_noto_joblib":      1,  # Cancer
    "deepdanbooru_pixiv_private_elkan_noto_joblib": 1,  # Leo
    "ensemble_virgo_v1":                          1,  # Virgo
}
TOTAL_WEIGHT = sum(TAB_WEIGHTS.values())

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)


def process_date(col, date: str, dry_run: bool) -> dict | None:
    docs = list(col.find(
        {"date": date, "status": {"$ne": "deduped"}},
        {
            "_id": 1, "status": 1, "localPath": 1,
            "favorites.isFavorited": 1, "features.stored": 1,
            "inferences": 1,
        },
    ))
    n = len(docs)
    if n == 0:
        log.info("%s: no documents, skipping", date)
        return None

    forced_keep = 0
    eligible: list[dict] = []
    for doc in docs:
        if doc.get("localPath") is None:
            continue  # already pruned (or never had a file) -- not a candidate
        is_favorited    = bool((doc.get("favorites") or {}).get("isFavorited"))
        features_stored = bool((doc.get("features") or {}).get("stored"))
        if is_favorited or not features_stored or doc.get("status") != "inferred":
            forced_keep += 1
            continue
        eligible.append(doc)

    quota           = math.ceil(n * RETAIN_RATIO)
    remaining_quota = max(0, quota - forced_keep)

    keep_ids: set = set()
    for model_key, weight in TAB_WEIGHTS.items():
        target_k = round(remaining_quota * weight / TOTAL_WEIGHT)
        if target_k <= 0:
            continue
        scored = [
            (doc["_id"], (doc.get("inferences", {}).get(model_key) or {}).get("score"))
            for doc in eligible
        ]
        scored = [(id_, score) for id_, score in scored if isinstance(score, (int, float))]
        scored.sort(key=lambda t: t[1], reverse=True)
        keep_ids.update(id_ for id_, _ in scored[:target_k])

    eligible_by_id = {doc["_id"]: doc for doc in eligible}
    delete_ids = [doc_id for doc_id in eligible_by_id if doc_id not in keep_ids]

    stats = {
        "n": n,
        "forced_keep": forced_keep,
        "quota": quota,
        "eligible": len(eligible),
        "kept_by_rank": len(keep_ids),
        "deleted": len(delete_ids),
    }

    if dry_run:
        return stats

    now = datetime.now(timezone.utc)
    ops = []
    n_unlink_errors = 0
    for doc_id in delete_ids:
        path = Path(eligible_by_id[doc_id]["localPath"])
        try:
            path.unlink()
        except FileNotFoundError:
            pass
        except OSError as exc:
            log.error("Failed to delete %s: %s", path, exc)
            n_unlink_errors += 1
            continue
        ops.append(UpdateOne(
            {"_id": doc_id},
            {"$set": {"localPath": None, "imageDeleted": True, "imageDeletedAt": now}},
        ))

    if ops:
        col.bulk_write(ops, ordered=False)
    stats["unlink_errors"] = n_unlink_errors
    return stats


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--older-than-days", type=int, default=DEFAULT_OLDER_THAN_DAYS)
    parser.add_argument("--date-from", type=str, default=None, help="Start date, inclusive (YYYY-MM-DD)")
    parser.add_argument("--date-to", type=str, default=None, help="End date, inclusive (YYYY-MM-DD)")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    client = MongoClient(MONGODB_URI)
    col    = client[MONGODB_DB]["images"]

    if args.date_from or args.date_to:
        date_range: dict = {}
        if args.date_from:
            date_range["$gte"] = args.date_from
        if args.date_to:
            date_range["$lte"] = args.date_to
        dates = sorted(col.distinct("date", {"date": date_range}))
    else:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=args.older_than_days)).strftime("%Y-%m-%d")
        log.info("Cutoff date: %s (older-than-days=%d)", cutoff, args.older_than_days)
        dates = sorted(col.distinct("date", {"date": {"$lt": cutoff}}))

    log.info("Processing %d date(s)%s", len(dates), " (dry-run)" if args.dry_run else "")
    if not dates:
        log.info("Nothing to do.")
        return

    totals: dict[str, int] = {}
    for date in dates:
        stats = process_date(col, date, args.dry_run)
        if stats is None:
            continue
        log.info(
            "%s: n=%d forced_keep=%d quota=%d eligible=%d kept_by_rank=%d deleted=%d%s",
            date, stats["n"], stats["forced_keep"], stats["quota"], stats["eligible"],
            stats["kept_by_rank"], stats["deleted"],
            f" unlink_errors={stats['unlink_errors']}" if stats.get("unlink_errors") else "",
        )
        for k, v in stats.items():
            totals[k] = totals.get(k, 0) + v

    log.info("Done. Totals across %d date(s): %s", len(dates), totals)


if __name__ == "__main__":
    main()
