#!/usr/bin/env python3
"""
Go/no-go baseline for Phase B's supervised stage-2 re-ranker: fit a plain
LogisticRegression on cheap-to-build features and check whether it beats the
existing best single model / ensemble, using the exact two-stage protocol
section 1.5.3 of recommendation_improvement_plan.md found to be mandatory
here (a learned re-ranker applied to a day's *full* population collapses;
restricted to an existing ranker's top-K candidate pool it can win).

This intentionally stops short of building the full S4 pipeline (LightGBM,
Qdrant kNN preference features, model registry/promotion gate). The point is
to answer one question first: do the feature groups that are already free to
build (model-score percentiles + importantTagProbs tags + basic meta) carry
enough signal to be worth the LightGBM/Qdrant investment? If this baseline
underperforms the best single model even inside the restricted pool, adding
model complexity first is the wrong move -- the missing signal (most likely
Qdrant kNN preference similarity, section 3.3 Q1) should be added before
reaching for a bigger model.

Feature groups:
  model_scores -- every inferences.<model>.<field>, rank-percentiled within
                  date (so day-to-day score-distribution shifts don't leak)
  tags         -- importantTagProbs.{deepdanbooru,pixai}.<tag>; fixed ~50-tag
                  vocab per feature type (same tags written for every image)
  meta         -- type (one-hot), log aspect ratio, day-of-week (one-hot)
  qdrant_knn   -- section 3.3 Q1: for each of the 4 axes in the
                  `image_embeddings_multiaxis` Qdrant collection (eva02,
                  character, situation, style), the candidate's cosine
                  similarity to the set of favorites *already favorited
                  before that row's date* (max / mean / top-10 mean) -- 12
                  dims. Time-gated on purpose: using favorites added after a
                  row's date would leak future preference into the past and
                  inflate every date except the very latest. The candidate's
                  own image is always excluded from its own neighbor set.
                  Disable with --no-qdrant-knn to reproduce the earlier
                  (weaker) 3-group baseline.

Protocol:
  1. Same time-series holdout as eval_impressions.py: last --test-days dates
     held out, everything earlier is train.
  2. Stage-1 candidate pool: for every date, --candidate-model's top
     --candidate-k images among that date's FULL population (not just the
     viewed/labeled subset) -- fetched fresh from MongoDB per date.
  3. Fit LogisticRegression on all train-date impression rows (unrestricted,
     matching how 1.5.3's original probe was trained).
  4. Evaluate on test rows twice:
       unrestricted  -- reproduces the 1.5.3 collapse as a sanity check
       stage1_pool   -- test rows that are BOTH labeled (viewed) and inside
                        that date's stage-1 pool; this is the only valid
                        estimate of serving-time performance
     and compare against --candidate-model's own raw score on the identical
     stage1_pool subset, so "did learning help" is judged on equal footing.
  5. Reports the stage-1 pool's recall ceiling too (what fraction of test
     positives even survive stage-1) -- caps what stage-2 can ever recover.

Usage:
    cd pu-learning
    venv/bin/python scripts/eval_reranker_baseline.py
    venv/bin/python scripts/eval_reranker_baseline.py --candidate-model inferences.pixai_pixiv_private_elkan_noto_joblib.score --candidate-k 300
    venv/bin/python scripts/eval_reranker_baseline.py --test-days 14
"""

import argparse
import logging
import sys
import uuid
from pathlib import Path

import numpy as np
import pandas as pd
from bson import ObjectId
from pymongo import DESCENDING, MongoClient
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import average_precision_score, roc_auc_score
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

SCRIPTS_DIR = Path(__file__).parent
sys.path.insert(0, str(SCRIPTS_DIR))
from config import METADATA_DIR, MONGODB_DB, MONGODB_URI, RESULTS_DIR  # noqa: E402

sys.path.insert(0, str(SCRIPTS_DIR.parent.parent / "worker"))
from ensembles import ENSEMBLES, compute_ensemble_scores  # noqa: E402

# Reuse eval_impressions.py's exact metric/holdout logic so numbers are
# directly comparable to what it reports for the existing models.
from eval_impressions import (  # noqa: E402
    discover_score_columns,
    fetch_scores,
    recall_at_k_by_date,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger(__name__)

IMPRESSIONS_PARQUET = METADATA_DIR / "impressions.parquet"
BASELINE_CSV = RESULTS_DIR / "reranker_baseline_metrics.csv"

DAYS_OF_WEEK = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
IMAGE_TYPES = ["pixiv", "danbooru", "gelbooru", "sankaku", "twitter"]

# Must match worker/main.py's QDRANT_HOST/PORT/COLLECTION_MULTIAXIS and
# _mongo_id_to_qdrant_uuid exactly -- these are the point ids main.py wrote.
QDRANT_HOST = "localhost"
QDRANT_PORT = 6333
QDRANT_COLLECTION_MULTIAXIS = "image_embeddings_multiaxis"
QDRANT_AXES = ["eva02", "character", "situation", "style"]
QDRANT_STATS = ["max", "mean", "top10mean"]


def _mongo_id_to_qdrant_uuid(mongo_id: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_OID, mongo_id))


# ── Stage-1 candidate pools (full day population, from MongoDB) ─────────────

def fetch_stage1_pool(db, date: str, candidate_field: str, candidate_k: int, ensembles: dict) -> set[str]:
    """Top-`candidate_k` mongo _ids for `date`, ranked by `candidate_field`."""
    model_key = None
    if candidate_field.startswith("inferences.") and candidate_field.endswith(".score"):
        model_key = candidate_field[len("inferences."):-len(".score")]

    if model_key in ensembles:
        components = ensembles[model_key]
        proj = {f"inferences.{m}.score": 1 for m in components}
        proj["_id"] = 1
        docs = list(db["images"].find({"status": "inferred", "date": date}, proj))
        scored = compute_ensemble_scores(docs, components)
        ranked = sorted(scored.items(), key=lambda kv: kv[1], reverse=True)[:candidate_k]
        return {str(_id) for _id, _ in ranked}

    docs = (
        db["images"]
        .find({"status": "inferred", "date": date, candidate_field: {"$exists": True}}, {"_id": 1})
        .sort(candidate_field, DESCENDING)
        .limit(candidate_k)
    )
    return {str(d["_id"]) for d in docs}


# ── Feature construction ─────────────────────────────────────────────────────

def fetch_feature_docs(db, mongo_ids: list[str]) -> dict[str, dict]:
    oids = [ObjectId(i) for i in mongo_ids]
    proj = {"inferences": 1, "importantTagProbs": 1, "type": 1, "width": 1, "height": 1}
    out = {}
    CH = 5000
    for i in range(0, len(oids), CH):
        chunk = oids[i : i + CH]
        for doc in db["images"].find({"_id": {"$in": chunk}}, proj):
            out[str(doc["_id"])] = doc
    return out


def build_tag_matrix(df: pd.DataFrame, docs_by_id: dict, feature_type: str) -> pd.DataFrame:
    vocab: set[str] = set()
    for mid in df["mongo_id"]:
        tags = (docs_by_id.get(mid, {}).get("importantTagProbs") or {}).get(feature_type) or {}
        vocab.update(tags.keys())
    vocab = sorted(vocab)
    cols = [f"tag.{feature_type}.{t}" for t in vocab]
    rows = []
    for mid in df["mongo_id"]:
        tags = (docs_by_id.get(mid, {}).get("importantTagProbs") or {}).get(feature_type) or {}
        rows.append([tags.get(t, 0.0) for t in vocab])
    return pd.DataFrame(rows, columns=cols, index=df.index)


def build_meta_matrix(df: pd.DataFrame, docs_by_id: dict) -> pd.DataFrame:
    types, log_aspect, dows = [], [], []
    for mid, date in zip(df["mongo_id"], df["date"]):
        doc = docs_by_id.get(mid, {})
        types.append(doc.get("type") or "unknown")
        w, h = doc.get("width"), doc.get("height")
        log_aspect.append(np.log((w or 1) / (h or 1)) if w and h else np.nan)
        dows.append(pd.Timestamp(date).dayofweek)

    out = pd.DataFrame(index=df.index)
    for t in IMAGE_TYPES:
        out[f"meta.type.{t}"] = [1.0 if x == t else 0.0 for x in types]
    out["meta.log_aspect_ratio"] = log_aspect
    for d in range(7):
        out[f"meta.dow.{DAYS_OF_WEEK[d]}"] = [1.0 if x == d else 0.0 for x in dows]
    return out


def build_model_score_matrix(df: pd.DataFrame, inferences_by_id: dict, ensembles: dict) -> pd.DataFrame:
    scored = discover_score_columns(df, inferences_by_id)
    score_cols = [c for c in scored.columns if c.startswith("inferences.")]

    for name, components in ensembles.items():
        col = f"inferences.{name}.score"
        if col in score_cols:
            continue
        scored[col] = np.nan
        component_cols = [f"inferences.{m}.score" for m in components]
        if not any(c in scored.columns for c in component_cols):
            continue
        for _, group in scored.groupby("date", sort=False):
            docs = [
                {
                    "_id": idx,
                    "inferences": {
                        m: {"score": row[f"inferences.{m}.score"]}
                        for m in components
                        if f"inferences.{m}.score" in scored.columns and pd.notna(row[f"inferences.{m}.score"])
                    },
                }
                for idx, row in group.iterrows()
            ]
            for idx, score in compute_ensemble_scores(docs, components).items():
                scored.at[idx, col] = score
        score_cols.append(col)

    # Rank-percentile within date so absolute score-distribution shifts
    # across days don't leak into the learned model.
    out = pd.DataFrame(index=df.index)
    for col in score_cols:
        pct = scored.groupby("date")[col].rank(pct=True, na_option="keep")
        out[f"pct.{col}"] = pct
    return out


# ── Qdrant kNN preference-similarity features (section 3.3 Q1) ──────────────

def fetch_favorites(db) -> pd.DataFrame:
    """{mongo_id, favorited_at} for every current favorite."""
    docs = db["images"].find(
        {"favorites.isFavorited": True, "favorites.favoritedAt": {"$exists": True, "$ne": None}},
        {"favorites.favoritedAt": 1},
    )
    rows = [{"mongo_id": str(d["_id"]), "favorited_at": d["favorites"]["favoritedAt"]} for d in docs]
    return pd.DataFrame(rows)


def _l2_normalize(mat: np.ndarray) -> np.ndarray:
    norms = np.linalg.norm(mat, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return mat / norms


def fetch_qdrant_all_axes(qdrant_client, mongo_ids: list[str]) -> dict[str, tuple[np.ndarray, np.ndarray]]:
    """
    One retrieve pass (all named vectors at once) -> {axis: (matrix, present)}
    for `mongo_ids` in row order. `matrix` rows are L2-normalized float32;
    `present` flags which rows were actually found (missing -> zero vector).
    """
    id_to_row = {mid: i for i, mid in enumerate(mongo_ids)}
    uuids = [_mongo_id_to_qdrant_uuid(mid) for mid in mongo_ids]
    uuid_to_mid = dict(zip(uuids, mongo_ids))

    dims = {axis: p.size for axis, p in qdrant_client.get_collection(QDRANT_COLLECTION_MULTIAXIS).config.params.vectors.items()}
    mats = {axis: np.zeros((len(mongo_ids), dims[axis]), dtype=np.float32) for axis in QDRANT_AXES}
    present = {axis: np.zeros(len(mongo_ids), dtype=bool) for axis in QDRANT_AXES}

    CH = 500
    for i in range(0, len(uuids), CH):
        chunk = uuids[i : i + CH]
        records = qdrant_client.retrieve(collection_name=QDRANT_COLLECTION_MULTIAXIS, ids=chunk, with_vectors=True)
        for rec in records:
            mid = uuid_to_mid[str(rec.id)]
            row = id_to_row[mid]
            if not isinstance(rec.vector, dict):
                continue
            for axis in QDRANT_AXES:
                vec = rec.vector.get(axis)
                if vec is not None:
                    mats[axis][row] = vec
                    present[axis][row] = True

    return {axis: (_l2_normalize(mats[axis]), present[axis]) for axis in QDRANT_AXES}


def build_qdrant_knn_matrix(df: pd.DataFrame, db, candidate_ids: list[str]) -> pd.DataFrame:
    """
    12-dim kNN preference-similarity block: for each of QDRANT_AXES, each
    row's cosine similarity (max/mean/top10mean) to favorites strictly
    favorited before that row's `date` -- see module docstring for why the
    time gate and self-exclusion matter.
    """
    from qdrant_client import QdrantClient

    log.info("Fetching favorites list for Qdrant kNN features ...")
    favorites = fetch_favorites(db)
    log.info("%d favorites with favoritedAt", len(favorites))
    fav_ids = favorites["mongo_id"].tolist()
    fav_at = pd.to_datetime(favorites["favorited_at"]).to_numpy()
    order = np.argsort(fav_at)
    fav_ids_sorted = [fav_ids[i] for i in order]
    fav_at_sorted = fav_at[order]
    fav_id_to_sorted_pos = {mid: i for i, mid in enumerate(fav_ids_sorted)}

    qdrant_client = QdrantClient(host=QDRANT_HOST, port=QDRANT_PORT, timeout=30)

    out = pd.DataFrame(
        {f"knn.{axis}.{stat}": np.full(len(df), np.nan, dtype=np.float64) for axis in QDRANT_AXES for stat in QDRANT_STATS},
        index=df.index,
    )
    cand_id_to_row = {mid: i for i, mid in enumerate(candidate_ids)}
    cand_row_for_df = np.array([cand_id_to_row[mid] for mid in df["mongo_id"]])
    fav_row_for_df = np.array([fav_id_to_sorted_pos.get(mid, -1) for mid in df["mongo_id"]])

    log.info("[Qdrant kNN] fetching %d candidate vectors (all axes) ...", len(candidate_ids))
    cand_by_axis = fetch_qdrant_all_axes(qdrant_client, candidate_ids)
    log.info("[Qdrant kNN] fetching %d favorite vectors (all axes) ...", len(fav_ids_sorted))
    fav_by_axis = fetch_qdrant_all_axes(qdrant_client, fav_ids_sorted)

    for axis in QDRANT_AXES:
        cand_mat, cand_present = cand_by_axis[axis]
        fav_mat, fav_present = fav_by_axis[axis]

        for date, group in df.groupby("date", sort=False):
            cutoff = np.datetime64(pd.Timestamp(date))
            n_known = int(np.searchsorted(fav_at_sorted, cutoff, side="left"))
            if n_known == 0:
                continue
            known_idx = np.where(fav_present[:n_known])[0]
            if len(known_idx) == 0:
                continue
            K = fav_mat[known_idx]  # (n_known, dim)

            row_positions = group.index.to_numpy()
            cand_rows = cand_row_for_df[row_positions]
            c_present = cand_present[cand_rows]
            if not c_present.any():
                continue
            C = cand_mat[cand_rows[c_present]]  # (n_present, dim)
            sims = C @ K.T  # (n_present, n_known)

            # Self-exclusion: mask a candidate's own favorite record if it's
            # among the known favorites for this axis.
            self_fav_pos = fav_row_for_df[row_positions[c_present]]
            for local_i, spos in enumerate(self_fav_pos):
                if spos < 0:
                    continue
                match = np.where(known_idx == spos)[0]
                if len(match):
                    sims[local_i, match[0]] = np.nan

            valid = np.isfinite(sims)
            n_valid = valid.sum(axis=1)
            sims_masked = np.where(valid, sims, np.nan)
            max_sim = np.nanmax(np.where(valid, sims, -np.inf), axis=1)
            max_sim[n_valid == 0] = np.nan
            mean_sim = np.nanmean(sims_masked, axis=1)

            k = sims.shape[1]
            topk = min(10, k)
            top_vals = np.partition(np.where(valid, sims, -np.inf), -topk, axis=1)[:, -topk:]
            top_vals = np.where(np.isfinite(top_vals), top_vals, np.nan)
            top10mean = np.nanmean(top_vals, axis=1)

            present_positions = row_positions[c_present]
            out.loc[present_positions, f"knn.{axis}.max"] = max_sim
            out.loc[present_positions, f"knn.{axis}.mean"] = mean_sim
            out.loc[present_positions, f"knn.{axis}.top10mean"] = top10mean

    return out


# ── Evaluation ────────────────────────────────────────────────────────────────

def flat_auc_ap(df: pd.DataFrame, score_col: str) -> tuple[float | None, float | None]:
    y = df["y"].to_numpy()
    s = df[score_col].to_numpy(dtype=float)
    if y.sum() == 0 or y.sum() == len(y):
        return None, None
    try:
        return float(roc_auc_score(y, s)), float(average_precision_score(y, s))
    except ValueError:
        return None, None


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--impressions", type=str, default=str(IMPRESSIONS_PARQUET))
    parser.add_argument("--test-days", type=int, default=30)
    parser.add_argument("--k", type=int, default=50)
    parser.add_argument("--candidate-model", type=str, default="inferences.ensemble_libra_v1.score")
    parser.add_argument("--candidate-k", type=int, default=500)
    parser.add_argument("--qdrant-knn", dest="qdrant_knn", action="store_true", default=True)
    parser.add_argument("--no-qdrant-knn", dest="qdrant_knn", action="store_false")
    args = parser.parse_args()

    df = pd.read_parquet(args.impressions)
    log.info("Loaded %d impression rows across %d dates", len(df), df["date"].nunique())

    dates = sorted(df["date"].unique())
    test_dates = sorted(dates[-args.test_days :])
    train_dates = set(dates) - set(test_dates)
    log.info("Time-series holdout: %d train dates, %d test dates (%s .. %s)",
              len(train_dates), len(test_dates), test_dates[0], test_dates[-1])

    client = MongoClient(MONGODB_URI)
    db = client[MONGODB_DB]

    all_ids = df["mongo_id"].unique().tolist()
    log.info("Fetching inferences/tags/meta for %d unique images ...", len(all_ids))
    inferences_by_id = fetch_scores(db, all_ids)
    docs_by_id = fetch_feature_docs(db, all_ids)

    log.info("Building feature matrix (model-score percentiles + tags + meta) ...")
    X_scores = build_model_score_matrix(df, inferences_by_id, ENSEMBLES)
    X_tags_dd = build_tag_matrix(df, docs_by_id, "deepdanbooru")
    X_tags_px = build_tag_matrix(df, docs_by_id, "pixai")
    X_meta = build_meta_matrix(df, docs_by_id)
    blocks = [X_scores, X_tags_dd, X_tags_px, X_meta]
    if args.qdrant_knn:
        log.info("Building Qdrant kNN preference-similarity block (section 3.3 Q1) ...")
        X_knn = build_qdrant_knn_matrix(df, db, all_ids)
        blocks.append(X_knn)
    X = pd.concat(blocks, axis=1)
    log.info("Feature matrix: %d rows x %d cols", *X.shape)

    y = df["y"].to_numpy()
    w = df["weight"].to_numpy(dtype=float)
    is_train = df["date"].isin(train_dates).to_numpy()
    is_test = ~is_train

    pipe = Pipeline([
        ("impute", SimpleImputer(strategy="median")),
        ("scale", StandardScaler()),
        ("clf", LogisticRegression(max_iter=2000, C=1.0)),
    ])
    log.info("Fitting LogisticRegression on %d train rows (%d positive) ...", is_train.sum(), int(y[is_train].sum()))
    pipe.fit(X[is_train], y[is_train], clf__sample_weight=w[is_train])

    test_df = df[is_test].reset_index(drop=True)
    test_X = X[is_test].reset_index(drop=True)
    test_df["reranker_score"] = pipe.predict_proba(test_X)[:, 1]

    log.info("--- Sanity check: unrestricted (expect collapse per plan section 1.5.3) ---")
    unrestricted_recall = recall_at_k_by_date(test_df, "reranker_score", args.k)
    unrestricted_auc, unrestricted_ap = flat_auc_ap(test_df, "reranker_score")
    log.info("reranker, full population: recall@%d=%s auc=%s ap=%s",
              args.k, unrestricted_recall, unrestricted_auc, unrestricted_ap)

    log.info("--- Stage-1 pools: fetching %s top-%d per test date ---", args.candidate_model, args.candidate_k)
    pool_by_date = {d: fetch_stage1_pool(db, d, args.candidate_model, args.candidate_k, ENSEMBLES) for d in test_dates}

    n_pos_total = int(test_df["y"].sum())
    in_pool_mask = [mid in pool_by_date.get(date, set()) for mid, date in zip(test_df["mongo_id"], test_df["date"])]
    pooled_df = test_df[in_pool_mask].reset_index(drop=True)
    n_pos_pooled = int(pooled_df["y"].sum())
    log.info(
        "Stage-1 recall ceiling: %d/%d test positives (%.1f%%) survive the %s top-%d filter",
        n_pos_pooled, n_pos_total, 100 * n_pos_pooled / max(n_pos_total, 1), args.candidate_model, args.candidate_k,
    )

    if n_pos_pooled == 0:
        log.error("No positives survive the stage-1 pool -- widen --candidate-k or pick a different --candidate-model.")
        sys.exit(1)

    reranker_recall = recall_at_k_by_date(pooled_df, "reranker_score", args.k)
    reranker_auc, reranker_ap = flat_auc_ap(pooled_df, "reranker_score")

    candidate_col = args.candidate_model
    cand_scored = discover_score_columns(pooled_df, inferences_by_id)
    if candidate_col in cand_scored.columns and cand_scored[candidate_col].notna().any():
        pooled_df[candidate_col] = cand_scored[candidate_col]
    else:
        # Not already materialized in `inferences` for (all of) this subset --
        # recompute the ensemble per date (percentile ranks must not mix dates).
        model_key = candidate_col.removeprefix("inferences.").removesuffix(".score")
        if model_key not in ENSEMBLES:
            log.error("%s has no stored scores in the stage-1 pool and isn't a known ensemble.", candidate_col)
            sys.exit(1)
        pooled_df[candidate_col] = np.nan
        for date, group in pooled_df.groupby("date", sort=False):
            docs = [
                {"_id": idx, "inferences": inferences_by_id.get(row["mongo_id"], {})}
                for idx, row in group.iterrows()
            ]
            for idx, score in compute_ensemble_scores(docs, ENSEMBLES[model_key]).items():
                pooled_df.at[idx, candidate_col] = score

    candidate_recall = recall_at_k_by_date(pooled_df, candidate_col, args.k)
    candidate_auc, candidate_ap = flat_auc_ap(pooled_df, candidate_col)

    result = pd.DataFrame([
        {"variant": "reranker_lr_unrestricted", "n_rows": len(test_df), "n_pos": n_pos_total,
         "recall_at_k": unrestricted_recall, "auc": unrestricted_auc, "ap": unrestricted_ap},
        {"variant": "reranker_lr_stage1_pool", "n_rows": len(pooled_df), "n_pos": n_pos_pooled,
         "recall_at_k": reranker_recall, "auc": reranker_auc, "ap": reranker_ap},
        {"variant": f"candidate_raw_score_stage1_pool ({candidate_col})", "n_rows": len(pooled_df), "n_pos": n_pos_pooled,
         "recall_at_k": candidate_recall, "auc": candidate_auc, "ap": candidate_ap},
    ])
    result["test_days"] = args.test_days
    result["candidate_k"] = args.candidate_k
    result["stage1_recall_ceiling"] = n_pos_pooled / max(n_pos_total, 1)

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    result.to_csv(BASELINE_CSV, index=False)
    log.info("Saved -> %s", BASELINE_CSV)
    log.info("\n%s", result.to_string(index=False, float_format="%.4f"))
    if args.qdrant_knn:
        log.info("Qdrant kNN preference features (Q1) ARE included in this run (time-gated per row's date).")
    else:
        log.info(
            "NOTE: Qdrant kNN preference features (Q1) are NOT included in this run (--no-qdrant-knn) -- "
            "if reranker_lr_stage1_pool doesn't clearly beat candidate_raw_score, that's the next "
            "feature group to add before reaching for LightGBM (section 3.3 Q1 / section 4 feature table)."
        )


if __name__ == "__main__":
    main()
