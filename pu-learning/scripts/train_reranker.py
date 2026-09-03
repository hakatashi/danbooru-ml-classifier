#!/usr/bin/env python3
"""
Train the Phase B / S4 stage-2 re-ranker (worker/reranker.py is the source
of truth for feature construction -- this script only orchestrates data
collection, fitting, evaluation, and saving).

Unlike the earlier go/no-go probe (eval_reranker_baseline.py), this script
computes model-score percentiles within each date's FULL stage-1 candidate
pool (Libra ensemble top-500), not just the labeled/viewed subset -- see
worker/reranker.py's module docstring for why that distinction matters for
avoiding train/serve skew. Training rows are every impressions.parquet row
that also falls inside its date's stage-1 pool (~93% of positives per the
probe run); rows outside the pool are dropped, since there is no serving-
time analogue for scoring an image the stage-1 filter would never surface.

Fits both LightGBM (primary) and a LogisticRegression baseline (Phase B's
plan section 4 requirement: "線形ロジスティック回帰もベースラインとして必ず
併記する"), evaluated identically against the stage-1 candidate ensemble's
own raw score.

Protocol: time-series holdout, last --test-days dates = test (matches
eval_impressions.py / eval_reranker_baseline.py).

Usage:
    cd pu-learning
    venv/bin/python scripts/train_reranker.py
    venv/bin/python scripts/train_reranker.py --test-days 14
    venv/bin/python scripts/train_reranker.py --dry-run   # skip saving the model
"""

import argparse
import logging
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from pymongo import MongoClient
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import average_precision_score, roc_auc_score
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

SCRIPTS_DIR = Path(__file__).parent
sys.path.insert(0, str(SCRIPTS_DIR))
from config import METADATA_DIR, MODELS_DIR, MONGODB_DB, MONGODB_URI  # noqa: E402

WORKER_DIR = SCRIPTS_DIR.parent.parent / "worker"
sys.path.insert(0, str(WORKER_DIR))
from ensembles import ENSEMBLES  # noqa: E402
import reranker as rr  # noqa: E402

from eval_impressions import recall_at_k_by_date  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger(__name__)

IMPRESSIONS_PARQUET = METADATA_DIR / "impressions.parquet"
MODEL_PATH = MODELS_DIR / f"{rr.MODEL_KEY}.joblib"
BASELINE_MODEL_PATH = MODELS_DIR / f"{rr.MODEL_KEY}_lr_baseline.joblib"
SPEC_PATH = MODELS_DIR / f"{rr.MODEL_KEY}_feature_spec.json"


def flat_auc_ap(y: np.ndarray, s: np.ndarray) -> tuple[float | None, float | None]:
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
    parser.add_argument("--dry-run", action="store_true", help="Evaluate but don't save the model/spec")
    args = parser.parse_args()

    impressions = pd.read_parquet(args.impressions)
    log.info("Loaded %d impression rows across %d dates", len(impressions), impressions["date"].nunique())

    # A single image can appear as more than one impression row on the same
    # date (e.g. shown on both a Gemini-reconstructed page and a Libra
    # explicit mark) -- collapse to one label per (date, image) before this
    # is joined against the (also one-row-per-image) stage-1 pool, taking
    # the max weight (an explicit mark's weight=1.0 should win over a lower
    # reconstructed-page confidence for the same image).
    dedup = impressions.groupby(["date", "mongo_id"], as_index=False).agg(y=("y", "max"), weight=("weight", "max"))
    log.info("Deduplicated to %d (date, image) label rows (was %d impression rows)", len(dedup), len(impressions))
    labels_by_date: dict[str, pd.DataFrame] = {d: g.set_index("mongo_id") for d, g in dedup.groupby("date")}

    dates = sorted(impressions["date"].unique())
    test_dates = set(dates[-args.test_days :])

    client = MongoClient(MONGODB_URI)
    db = client[MONGODB_DB]

    all_docs_by_id: dict[str, dict] = {}
    rows: list[pd.DataFrame] = []
    meta_rows: list[dict] = []

    log.info("Building stage-1 pools + features for %d dates ...", len(dates))
    for i, date in enumerate(dates):
        pool_ids = rr.stage1_pool_ids(db, date, ENSEMBLES)
        if not pool_ids:
            continue
        pool_docs = rr.fetch_pool_docs(db, pool_ids)
        all_docs_by_id.update(pool_docs)

        labels = labels_by_date.get(date)
        if labels is None:
            continue
        in_pool = [mid for mid in pool_ids if mid in labels.index]
        if not in_pool:
            continue
        meta_rows.append({"date": date, "n_pool": len(pool_ids), "n_labeled_in_pool": len(in_pool)})
        rows.append(pd.DataFrame({"mongo_id": pool_ids, "date": date}))

        if (i + 1) % 20 == 0:
            log.info(" ... %d/%d dates processed", i + 1, len(dates))

    pools_df = pd.concat(rows, ignore_index=True)
    log.info("Fetched %d total pool docs across %d dates", len(all_docs_by_id), len(dates))

    # Freeze the feature spec from everything actually observed.
    model_score_fields = rr.observe_model_score_fields(all_docs_by_id)
    tag_vocab = rr.observe_tag_vocab(all_docs_by_id)
    feature_names = rr.build_feature_names(model_score_fields, tag_vocab)
    spec = {
        "version": rr.FEATURE_SPEC_VERSION,
        "model_key": rr.MODEL_KEY,
        "candidate_ensemble": rr.CANDIDATE_ENSEMBLE,
        "candidate_k": rr.CANDIDATE_K,
        "model_score_fields": model_score_fields,
        "tag_vocab": tag_vocab,
        "feature_names": feature_names,
    }
    log.info("Feature spec: %d model-score fields, %d+%d tags -> %d total feature columns",
              len(model_score_fields), len(tag_vocab.get("deepdanbooru", [])), len(tag_vocab.get("pixai", [])), len(feature_names))

    log.info("Building feature matrix for each date's full pool, keeping labeled rows only ...")
    X_parts, y_parts, w_parts, date_parts, id_parts = [], [], [], [], []
    for date, group in pools_df.groupby("date", sort=False):
        pool_ids = group["mongo_id"].tolist()
        X_pool = rr.build_feature_matrix(pool_ids, all_docs_by_id, date, spec)
        labels = labels_by_date[date]
        in_pool_labeled = [mid for mid in pool_ids if mid in labels.index]
        if not in_pool_labeled:
            continue
        X_parts.append(X_pool.loc[in_pool_labeled])
        y_parts.append(labels.loc[in_pool_labeled, "y"].to_numpy())
        w_parts.append(labels.loc[in_pool_labeled, "weight"].to_numpy(dtype=float))
        date_parts.extend([date] * len(in_pool_labeled))
        id_parts.extend(in_pool_labeled)

    X = pd.concat(X_parts, axis=0)
    y = np.concatenate(y_parts)
    w = np.concatenate(w_parts)
    dates_arr = np.array(date_parts)
    n_pos_total_labeled = int(dedup["y"].sum())
    log.info(
        "Training frame: %d rows (%d positive) -- %.1f%% of all %d labeled positives survive the stage-1 pool filter",
        len(X), int(y.sum()), 100 * y.sum() / n_pos_total_labeled, n_pos_total_labeled,
    )

    is_test = np.isin(dates_arr, list(test_dates))
    is_train = ~is_test
    log.info("Time-series holdout: %d train rows (%d pos), %d test rows (%d pos)",
              is_train.sum(), int(y[is_train].sum()), is_test.sum(), int(y[is_test].sum()))

    # ── LogisticRegression baseline ──────────────────────────────────────────
    lr = Pipeline([
        ("impute", SimpleImputer(strategy="median")),
        ("scale", StandardScaler()),
        ("clf", LogisticRegression(max_iter=2000, C=1.0)),
    ])
    lr.fit(X[is_train], y[is_train], clf__sample_weight=w[is_train])
    lr_test_scores = lr.predict_proba(X[is_test])[:, 1]

    # ── LightGBM ──────────────────────────────────────────────────────────────
    import lightgbm as lgb

    gbm = lgb.LGBMClassifier(
        objective="binary",
        n_estimators=300,
        num_leaves=15,
        max_depth=4,
        learning_rate=0.05,
        min_child_samples=20,
        subsample=0.8,
        colsample_bytree=0.7,
        reg_lambda=1.0,
        is_unbalance=True,
        random_state=42,
        verbosity=-1,
    )
    gbm.fit(X[is_train], y[is_train], sample_weight=w[is_train])
    gbm_test_scores = gbm.predict_proba(X[is_test])[:, 1]

    # ── Candidate ensemble's own raw score, same test population ────────────
    cand_field = f"pct.inferences.{rr.CANDIDATE_ENSEMBLE}.score"
    cand_test_scores = X.loc[is_test, cand_field].to_numpy() if cand_field in X.columns else np.full(is_test.sum(), np.nan)

    test_df = pd.DataFrame({
        "mongo_id": np.array(id_parts)[is_test],
        "date": dates_arr[is_test],
        "y": y[is_test],
        "weight": w[is_test],
        "lr_score": lr_test_scores,
        "gbm_score": gbm_test_scores,
        "candidate_score": cand_test_scores,
    })

    records = []
    for name, col in [("candidate_raw (Libra)", "candidate_score"), ("lr_baseline", "lr_score"), ("lightgbm (S4)", "gbm_score")]:
        auc, ap = flat_auc_ap(test_df["y"].to_numpy(), test_df[col].to_numpy())
        recall = recall_at_k_by_date(test_df, col, args.k)
        records.append({"model": name, "auc": auc, "ap": ap, f"recall_at_{args.k}": recall})

    result = pd.DataFrame(records)
    log.info("\n%s", result.to_string(index=False, float_format="%.4f"))

    if args.dry_run:
        log.info("--dry-run: not saving model/spec.")
        return

    import joblib
    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    joblib.dump(gbm, MODEL_PATH)
    joblib.dump(lr, BASELINE_MODEL_PATH)
    rr.save_feature_spec(spec, SPEC_PATH)
    log.info("Saved -> %s, %s, %s", MODEL_PATH, BASELINE_MODEL_PATH, SPEC_PATH)


if __name__ == "__main__":
    main()
