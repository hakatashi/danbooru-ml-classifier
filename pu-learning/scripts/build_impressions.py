#!/usr/bin/env python3
"""
Build a labeled impression dataset from explicit "page viewed" marks and
favorites-based page reconstruction.

Two label sources are merged:
  explicit      -- MongoDB `pageViews` collection: {date, sortField, page,
                    imageIds}. The user explicitly marked this page as
                    viewed (worker/api.py POST /page-views/mark), so every
                    image on it is a real impression. Negatives get weight
                    1.0 (confirmed, not estimated).
  reconstructed -- Inferred from favorites: for each date, images are
                    ranked descending by --sort-field; any page containing
                    >=1 favorite is treated as viewed. Negatives on these
                    pages get a confidence weight (see --weight-profile)
                    reflecting how likely the page was actually browsed.
                    See reports/recommendation_improvement_plan.md sections
                    1.2, 1.3, 2.3 for how this ladder was derived.
Where the same (date, sort_field, page) triple appears in both sources, the
explicit mark wins (it has an authoritative imageIds snapshot).

Cleaning (skip with --no-clean):
  - Negatives sharing `artworkId` with a favorite are dropped (same artwork,
    different page -- evidence of "already collected it", not "disliked
    it"; see plan section 1.4).
  - Any image overlapping data/metadata/eval_manifest.parquet is dropped
    entirely (positive or negative), to keep the hand-labeled eval set
    unpolluted by training data.

Output: data/metadata/impressions.parquet, columns:
  mongo_id, image_id, date, sort_field, page, rank, y, weight, source, type, artwork_id
`image_id` is the dmc_<source>/<stem> id used by pu-learning's training
manifest (splits.parquet, feature h5 stores); None where the source type
isn't covered by extract_features.py's DMC scan yet (currently: sankaku).

Usage:
    cd pu-learning
    venv/bin/python scripts/build_impressions.py
    venv/bin/python scripts/build_impressions.py --weight-profile flat
    venv/bin/python scripts/build_impressions.py --weight-profile explicit-only
    venv/bin/python scripts/build_impressions.py --sort-field inferences.ensemble_virgo_v1.score
    venv/bin/python scripts/build_impressions.py --dump-ids /tmp/viewed_ids.txt
    venv/bin/python scripts/build_impressions.py --no-clean
"""

import argparse
import logging
import sys
from pathlib import Path

import pandas as pd
from pymongo import DESCENDING, MongoClient

SCRIPTS_DIR = Path(__file__).parent
sys.path.insert(0, str(SCRIPTS_DIR))
from config import DAILY_PAGE_SIZE, METADATA_DIR, MONGODB_DB, MONGODB_URI  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

IMPRESSIONS_PARQUET = METADATA_DIR / "impressions.parquet"
EVAL_MANIFEST       = METADATA_DIR / "eval_manifest.parquet"

DEFAULT_SORT_FIELD = "inferences.eva02_pixiv_private_nnpu_joblib.score"

# dmc_<source>/<stem> id prefixes covered by extract_features.py's DMC scan.
# sankaku is deliberately excluded -- extract_features.py doesn't walk it yet
# (recommendation_improvement_plan.md section 1.6).
DMC_COVERED_TYPES = {"danbooru", "gelbooru", "pixiv"}


def _dmc_image_id(doc_type: str, key: str | None) -> str | None:
    """Mirror pu-learning/scripts/extract_features.py's `dmc_<source>/<stem>` id."""
    if not key or doc_type not in DMC_COVERED_TYPES:
        return None
    stem = Path(key).stem
    return f"dmc_{doc_type}/{stem}"


def _confidence_weight(
    page: int,
    contiguous_max_page: int,
    page_fav_count: int,
    lag_days: float | None,
) -> float:
    """Ladder from recommendation_improvement_plan.md section 2.3."""
    if page <= contiguous_max_page:
        return 1.0
    if page <= 4:
        return 0.9
    if page_fav_count >= 2:
        return 0.7
    if lag_days is not None and lag_days <= 3:
        return 0.5
    return 0.15


def load_explicit_marks(db) -> pd.DataFrame:
    """Rows from pageViews snapshots. Every image gets weight 1.0."""
    marks = list(db["pageViews"].find({}))
    if not marks:
        log.info("[explicit] No pageViews marks found.")
        return pd.DataFrame(columns=[
            "mongo_id", "image_id", "date", "sort_field", "page", "rank",
            "y", "weight", "source", "type", "artwork_id",
        ])

    all_ids = {oid for mark in marks for oid in mark["imageIds"]}
    docs_by_id = {
        doc["_id"]: doc
        for doc in db["images"].find(
            {"_id": {"$in": list(all_ids)}},
            {"favorites.isFavorited": 1, "type": 1, "key": 1, "artworkId": 1},
        )
    }

    rows = []
    for mark in marks:
        for i, oid in enumerate(mark["imageIds"]):
            doc = docs_by_id.get(oid)
            if doc is None:
                continue  # image deleted since the mark was made
            y = 1 if (doc.get("favorites") or {}).get("isFavorited") else 0
            rows.append({
                "mongo_id": str(oid),
                "image_id": _dmc_image_id(doc.get("type"), doc.get("key")),
                "date": mark["date"],
                "sort_field": mark["sortField"],
                "page": mark["page"],
                "rank": mark["page"] * DAILY_PAGE_SIZE + i,
                "y": y,
                "weight": 1.0,
                "source": "explicit",
                "type": doc.get("type"),
                "artwork_id": doc.get("artworkId"),
            })

    df = pd.DataFrame(rows)
    log.info(
        "[explicit] %d marks -> %d image rows (%d positive)",
        len(marks), len(df), int(df["y"].sum()) if len(df) else 0,
    )
    return df


def load_reconstructed_impressions(
    db, sort_field: str, page_size: int, weight_profile: str,
) -> pd.DataFrame:
    col = db["images"]

    favorites = list(col.find(
        {"favorites.isFavorited": True, "date": {"$exists": True}},
        {"_id": 1, "date": 1, "favorites.favoritedAt": 1},
    ))
    fav_by_date: dict[str, list[dict]] = {}
    for f in favorites:
        fav_by_date.setdefault(f["date"], []).append(f)

    rows = []
    for date, favs_on_date in sorted(fav_by_date.items()):
        fav_ids = {f["_id"] for f in favs_on_date}
        fav_at  = {f["_id"]: f["favorites"]["favoritedAt"] for f in favs_on_date}

        docs = list(col.find(
            {"status": "inferred", "date": date, sort_field: {"$exists": True}},
            {"_id": 1, "type": 1, "key": 1, "artworkId": 1},
        ).sort(sort_field, DESCENDING))
        if not docs:
            continue

        fav_ranks = sorted(rank for rank, doc in enumerate(docs) if doc["_id"] in fav_ids)
        if not fav_ranks:
            continue
        fav_pages = sorted({rank // page_size for rank in fav_ranks})

        contiguous_max_page = -1
        for p in range(fav_pages[-1] + 1):
            if p in fav_pages:
                contiguous_max_page = p
            else:
                break

        for page in fav_pages:
            lo, hi = page * page_size, min((page + 1) * page_size, len(docs))
            page_docs  = docs[lo:hi]
            page_favs  = [d for d in page_docs if d["_id"] in fav_ids]
            page_fav_count = len(page_favs)
            lag_days = None
            if page_favs:
                lags = [
                    (fav_at[d["_id"]].date() - pd.Timestamp(date).date()).days
                    for d in page_favs
                ]
                lag_days = min(lags)

            if weight_profile == "flat":
                weight = 1.0
            else:
                weight = _confidence_weight(page, contiguous_max_page, page_fav_count, lag_days)

            for rank, doc in enumerate(page_docs, start=lo):
                y = 1 if doc["_id"] in fav_ids else 0
                rows.append({
                    "mongo_id": str(doc["_id"]),
                    "image_id": _dmc_image_id(doc.get("type"), doc.get("key")),
                    "date": date,
                    "sort_field": sort_field,
                    "page": page,
                    "rank": rank,
                    "y": y,
                    "weight": 1.0 if y == 1 else weight,
                    "source": "reconstructed",
                    "type": doc.get("type"),
                    "artwork_id": doc.get("artworkId"),
                })

    df = pd.DataFrame(rows)
    log.info(
        "[reconstructed] %d date(s) with favorites -> %d image rows (%d positive)",
        len(fav_by_date), len(df), int(df["y"].sum()) if len(df) else 0,
    )
    return df


def merge_sources(explicit_df: pd.DataFrame, reconstructed_df: pd.DataFrame) -> pd.DataFrame:
    """Explicit wins over reconstructed for the same (date, sort_field, page)."""
    if explicit_df.empty:
        return reconstructed_df
    if reconstructed_df.empty:
        return explicit_df

    explicit_keys = set(
        explicit_df[["date", "sort_field", "page"]].itertuples(index=False, name=None)
    )
    keep_mask = ~reconstructed_df[["date", "sort_field", "page"]].apply(tuple, axis=1).isin(explicit_keys)
    dropped = (~keep_mask).sum()
    if dropped:
        log.info("[merge] %d reconstructed rows superseded by explicit marks", dropped)

    return pd.concat([explicit_df, reconstructed_df[keep_mask]], ignore_index=True)


def clean_negatives(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df

    favorite_artwork_ids = set(
        df.loc[df["y"] == 1, "artwork_id"].dropna().unique()
    )
    eval_ids: set[str] = set()
    if EVAL_MANIFEST.exists():
        eval_ids = set(pd.read_parquet(EVAL_MANIFEST)["image_id"])

    same_artwork_negative = (
        (df["y"] == 0)
        & df["artwork_id"].notna()
        & df["artwork_id"].isin(favorite_artwork_ids)
    )
    in_eval_set = df["image_id"].notna() & df["image_id"].isin(eval_ids)

    log.info(
        "[clean] dropping %d same-artwork negatives, %d rows overlapping eval_manifest.parquet",
        int(same_artwork_negative.sum()), int(in_eval_set.sum()),
    )
    return df[~same_artwork_negative & ~in_eval_set].reset_index(drop=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--sort-field", type=str, default=DEFAULT_SORT_FIELD)
    parser.add_argument("--page-size", type=int, default=DAILY_PAGE_SIZE)
    parser.add_argument(
        "--weight-profile", type=str, default="ladder",
        choices=["ladder", "flat", "explicit-only"],
    )
    parser.add_argument("--no-clean", action="store_true")
    parser.add_argument("--dump-ids", type=str, default=None, help="Write one mongo_id per line to this path")
    args = parser.parse_args()

    client = MongoClient(MONGODB_URI)
    db = client[MONGODB_DB]

    explicit_df = load_explicit_marks(db)
    if args.weight_profile == "explicit-only":
        df = explicit_df
    else:
        reconstructed_df = load_reconstructed_impressions(
            db, args.sort_field, args.page_size, args.weight_profile,
        )
        df = merge_sources(explicit_df, reconstructed_df)

    if df.empty:
        log.error("No impressions found. Nothing to write.")
        sys.exit(1)

    if not args.no_clean:
        df = clean_negatives(df)

    log.info(
        "Final: %d rows (%d positive, %d negative), %d unique dates",
        len(df), int((df["y"] == 1).sum()), int((df["y"] == 0).sum()), df["date"].nunique(),
    )

    METADATA_DIR.mkdir(parents=True, exist_ok=True)
    df.to_parquet(IMPRESSIONS_PARQUET, index=False)
    log.info("Saved impressions -> %s", IMPRESSIONS_PARQUET)

    if args.dump_ids:
        Path(args.dump_ids).write_text("\n".join(df["mongo_id"].unique()) + "\n")
        log.info("Dumped %d unique mongo_ids -> %s", df["mongo_id"].nunique(), args.dump_ids)


if __name__ == "__main__":
    main()
