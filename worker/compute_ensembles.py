#!/usr/bin/env python3
"""
Compute/backfill the Virgo and Libra rank-average ensembles.

Materializes ensembles.ENSEMBLES into `inferences.<ensemble_key>.score` for
the requested dates, purely by re-aggregating scores main.py has already
written -- no GPU, no feature extraction. Safe to re-run; each date's
ensemble scores are fully recomputed (not skip-if-exists), since the day's
population can still grow later as pending images finish inferring.

Usage:
    cd worker
    venv/bin/python compute_ensembles.py --date-from 2026-07-01 --date-to 2026-08-04
    venv/bin/python compute_ensembles.py --all
    venv/bin/python compute_ensembles.py --date-from 2026-08-01 --date-to 2026-08-04 --dry-run
    venv/bin/python compute_ensembles.py --date-from 2026-08-01 --date-to 2026-08-04 --ensembles ensemble_virgo_v1

Options:
    --date-from DATE    Start date, inclusive (YYYY-MM-DD)
    --date-to DATE      End date, inclusive (YYYY-MM-DD)
    --all               Compute for every distinct date with inferred documents
                         (ignores --date-from/--date-to)
    --ensembles NAMES   Comma-separated ensemble keys (default: all in ensembles.ENSEMBLES)
    --dry-run           Print counts without writing to MongoDB
"""

import argparse
import logging
import os
import sys
from pathlib import Path

from pymongo import MongoClient

WORKER_DIR = Path(__file__).parent
sys.path.insert(0, str(WORKER_DIR))

from ensembles import ENSEMBLES, compute_and_write_ensembles  # noqa: E402

MONGODB_URI = os.environ.get("MONGODB_URI", "mongodb://localhost:27017")
MONGODB_DB  = os.environ.get("MONGODB_DB", "danbooru-ml-classifier")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
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


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--date-from", type=str, default=None, help="Start date, inclusive (YYYY-MM-DD)")
    parser.add_argument("--date-to", type=str, default=None, help="End date, inclusive (YYYY-MM-DD)")
    parser.add_argument("--all", action="store_true", help="Compute for every distinct date with inferred documents")
    parser.add_argument(
        "--ensembles", type=str, default=None,
        help=f"Comma-separated ensemble keys (default: all of {list(ENSEMBLES)})",
    )
    parser.add_argument("--dry-run", action="store_true", help="Print counts without writing")
    args = parser.parse_args()

    if not args.all and not args.date_from and not args.date_to:
        parser.error("specify --date-from/--date-to or --all")

    ensemble_names = args.ensembles.split(",") if args.ensembles else None
    if ensemble_names:
        unknown = [n for n in ensemble_names if n not in ENSEMBLES]
        if unknown:
            parser.error(f"unknown ensemble(s): {unknown} (known: {list(ENSEMBLES)})")

    client = MongoClient(MONGODB_URI)
    db     = client[MONGODB_DB]
    col    = db["images"]

    if args.all:
        dates = sorted(col.distinct("date", {"status": "inferred"}))
    else:
        dates = _dates_in_range(col, args.date_from, args.date_to)

    log.info(
        "Computing %s for %d date(s)%s",
        ensemble_names or list(ENSEMBLES),
        len(dates),
        " (dry-run)" if args.dry_run else "",
    )
    if not dates:
        log.info("Nothing to do.")
        return

    results = compute_and_write_ensembles(db, dates, ensemble_names=ensemble_names, dry_run=args.dry_run)

    totals: dict[str, int] = {}
    for per_date in results.values():
        for name, n in per_date.items():
            totals[name] = totals.get(name, 0) + n
    log.info("Done. Totals across %d date(s): %s", len(dates), totals)


if __name__ == "__main__":
    main()
