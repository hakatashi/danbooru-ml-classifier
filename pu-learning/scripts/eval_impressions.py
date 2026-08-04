#!/usr/bin/env python3
"""
Evaluate every existing inference model (and the Virgo/Libra ensembles) on
the impression dataset built by build_impressions.py.

Reads MongoDB `inferences.*` values directly -- no feature extraction, no
joblib loading, no GPU. Virgo/Libra (and any --ensemble given) are computed
on the fly from their component model scores (worker/ensembles.py is the
source of truth for component lists and the percentile-rank-average
formula), so this always reflects the current ensemble definitions even if
worker/compute_ensembles.py hasn't been (re)run for every date yet.

Metrics (see reports/recommendation_improvement_plan.md sections 1.5, 1.5.1,
1.5.2 for why AUC/Recall@50 need care here):
  page_auc_*   -- PRIMARY. Unweighted, pooled Mann-Whitney AUC within each
                  (date, sort_field, page) group, reported overall and by
                  page-depth band (p0-1 / p0-4 / p5-19 / p20+). This is the
                  only metric immune to range-restriction bias from the
                  incumbent sort ranking most of the impression population.
  weighted_auc, weighted_ap
               -- Global AUC/AP using each row's confidence weight
                  (build_impressions.py's --weight-profile ladder) as
                  sample_weight.
  wndcg_at_50  -- Per-date weighted NDCG@50 (weighted_ndcg_at_k, copied from
                  eval_models.py), averaged over the test dates.
  recall_at_50 -- Per-date, averaged. REFERENCE ONLY -- flagged
                  `biased_by_incumbent=True` for any model whose scores
                  correlate with the incumbent sort field used to build the
                  impressions, since that field's own Recall@50 is
                  structurally inflated (the labels were discovered by
                  browsing it). See section 1.5.2.

Protocol: time-series holdout. Metrics are computed only on the most recent
--test-days dates; a random split would leak through preference drift and
page-level correlation. This script does not train anything -- the holdout
exists so this evaluation protocol matches what Phase B's learned re-ranker
must also use.

Usage:
    cd pu-learning
    venv/bin/python scripts/eval_impressions.py
    venv/bin/python scripts/eval_impressions.py --test-days 14
    venv/bin/python scripts/eval_impressions.py --ensemble my_combo:eva02_pixiv_private_nnpu_joblib,pixai_pixiv_private_elkan_noto_joblib
"""

import argparse
import logging
import re
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from pymongo import MongoClient
from scipy.stats import rankdata
from sklearn.metrics import average_precision_score, roc_auc_score

SCRIPTS_DIR = Path(__file__).parent
sys.path.insert(0, str(SCRIPTS_DIR))
from config import METADATA_DIR, MONGODB_DB, MONGODB_URI, RESULTS_DIR  # noqa: E402

# worker/ensembles.py is the source of truth for Virgo/Libra's component
# lists and the percentile-rank-average formula (same convention as
# build_eval_dataset.py reusing worker/danbooru_resnet.py etc).
sys.path.insert(0, str(SCRIPTS_DIR.parent.parent / "worker"))
from ensembles import ENSEMBLES, compute_ensemble_scores  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

IMPRESSIONS_PARQUET = METADATA_DIR / "impressions.parquet"
METRICS_CSV         = RESULTS_DIR / "impression_metrics.csv"

CLASS_NAMES = ["not_bookmarked", "bookmarked_public", "bookmarked_private"]
PAGE_BANDS  = [("p0_1", 0, 1), ("p0_4", 0, 4), ("p5_19", 5, 19), ("p20plus", 20, 10**9), ("all", 0, 10**9)]

# A model is treated as potentially biased toward the incumbent sort field
# used to build the impressions if it *is* that field, or shares the same
# model key (e.g. the field is inferences.X.score and the model is X.score).
_INFERENCE_FIELD_RE = re.compile(r"^inferences\.([a-z0-9_-]+)\.(score|not_bookmarked|bookmarked_public|bookmarked_private)$")


# ── Weighted NDCG@K (copied from eval_models.py) ─────────────────────────────

def weighted_ndcg_at_k(relevances: np.ndarray, weights: np.ndarray, scores: np.ndarray, k: int) -> float:
    n = len(scores)
    k_eff = min(k, n)
    if k_eff == 0:
        return 0.0

    rank_order = np.argsort(scores)[::-1][:k_eff]
    gains      = (2.0 ** relevances[rank_order] - 1.0) * weights[rank_order]
    discounts  = np.log2(np.arange(1, k_eff + 1, dtype=np.float64) + 1.0)
    wdcg       = float(np.sum(gains / discounts))

    all_gains   = (2.0 ** relevances - 1.0) * weights
    ideal_order = np.argsort(all_gains)[::-1][:k_eff]
    ideal_gains = all_gains[ideal_order]
    widcg       = float(np.sum(ideal_gains / np.log2(np.arange(1, len(ideal_gains) + 1, dtype=np.float64) + 1.0)))

    return 0.0 if widcg == 0.0 else wdcg / widcg


# ── Page-internal (within date/sort_field/page) pooled AUC ──────────────────

def pooled_page_auc(df: pd.DataFrame, score_col: str, page_lo: int, page_hi: int) -> float | None:
    """
    Unweighted, pooled Mann-Whitney AUC across every (date, sort_field, page)
    group whose page falls in [page_lo, page_hi]. This is the "page-internal
    AUC" from recommendation_improvement_plan.md sections 1.5-1.5.1: it never
    compares a positive from one page against a negative from another, so it
    is immune to the range-restriction bias that makes plain global AUC
    unfairly favor whichever model's ranking the impressions were sourced
    from.
    """
    subset = df[(df["page"] >= page_lo) & (df["page"] <= page_hi)]
    if subset.empty:
        return None

    num = 0.0
    den = 0.0
    for _, g in subset.groupby(["date", "sort_field", "page"], sort=False):
        y = g["y"].to_numpy()
        n1 = int(y.sum())
        n0 = len(y) - n1
        if n1 == 0 or n0 == 0:
            continue
        s = g[score_col].to_numpy(dtype=float)
        ranks = rankdata(s)
        u = ranks[y == 1].sum() - n1 * (n1 + 1) / 2
        num += u
        den += n1 * n0

    return None if den == 0 else num / den


def recall_at_k_by_date(df: pd.DataFrame, score_col: str, k: int) -> float | None:
    """Per-date Recall@k (top-k by score_col / total positives that date), averaged."""
    values = []
    for _, g in df.groupby("date", sort=False):
        n_pos = int(g["y"].sum())
        if n_pos == 0:
            continue
        top = g.nlargest(k, score_col)
        values.append(top["y"].sum() / n_pos)
    return None if not values else float(np.mean(values))


def wndcg_at_k_by_date(df: pd.DataFrame, score_col: str, k: int) -> float | None:
    values = []
    for _, g in df.groupby("date", sort=False):
        if g["y"].sum() == 0:
            continue
        values.append(weighted_ndcg_at_k(
            g["y"].to_numpy(dtype=float),
            g["weight"].to_numpy(dtype=float),
            g[score_col].to_numpy(dtype=float),
            k,
        ))
    return None if not values else float(np.mean(values))


def evaluate_model(df: pd.DataFrame, score_col: str, k: int) -> dict:
    y = df["y"].to_numpy()
    s = df[score_col].to_numpy(dtype=float)
    w = df["weight"].to_numpy(dtype=float)

    row = {}
    for band_name, lo, hi in PAGE_BANDS:
        row[f"page_auc_{band_name}"] = pooled_page_auc(df, score_col, lo, hi)

    try:
        row["weighted_auc"] = float(roc_auc_score(y, s, sample_weight=w))
    except ValueError:
        row["weighted_auc"] = None
    try:
        row["weighted_ap"] = float(average_precision_score(y, s, sample_weight=w))
    except ValueError:
        row["weighted_ap"] = None

    row[f"wndcg_at_{k}"] = wndcg_at_k_by_date(df, score_col, k)
    row[f"recall_at_{k}"] = recall_at_k_by_date(df, score_col, k)
    row["n_rows"] = len(df)
    row["n_pos"] = int(y.sum())
    return row


# ── Model/ensemble score discovery ───────────────────────────────────────────

def fetch_scores(db, mongo_ids: list[str]) -> dict[str, dict]:
    """Return {mongo_id: inferences_dict} for the given ids."""
    from bson import ObjectId

    oids = [ObjectId(i) for i in mongo_ids]
    docs = db["images"].find({"_id": {"$in": oids}}, {"inferences": 1})
    return {str(doc["_id"]): (doc.get("inferences") or {}) for doc in docs}


def discover_score_columns(df: pd.DataFrame, inferences_by_id: dict) -> pd.DataFrame:
    """
    Add one column per (model, field) found across `inferences_by_id`, named
    `inferences.<model>.<field>` to match the sort_field convention used
    elsewhere in the app.
    """
    all_fields: set[str] = set()
    for inferences in inferences_by_id.values():
        for model_key, value in inferences.items():
            if not isinstance(value, dict):
                continue
            for field in value:
                all_fields.add(f"inferences.{model_key}.{field}")

    out = df.copy()
    for col in sorted(all_fields):
        _, model_key, field = col.split(".", 2)
        out[col] = [
            (inferences_by_id.get(mid, {}).get(model_key) or {}).get(field)
            for mid in df["mongo_id"]
        ]
        # Non-numeric / missing -> NaN, so pandas keeps a float column.
        out[col] = pd.to_numeric(out[col], errors="coerce")
    return out


def add_ensemble_columns(df: pd.DataFrame, ensembles: dict[str, list[str]]) -> pd.DataFrame:
    """
    Compute each ensemble's rank-average score per (date, sort_field) group,
    using worker/ensembles.py's exact formula, and add it as a column named
    `inferences.<ensemble_name>.score`.

    Uses the DataFrame's own positional index as the doc "_id" passed to
    compute_ensemble_scores() -- safe and unique per row, and lets each
    score be written back with a single label-based .at[] assignment rather
    than an O(n) boolean-mask scan per row.
    """
    out = df.reset_index(drop=True).copy()
    for name, components in ensembles.items():
        col_name = f"inferences.{name}.score"
        out[col_name] = np.nan
        component_cols = [f"inferences.{m}.score" for m in components]
        if not any(c in out.columns for c in component_cols):
            continue

        for _, group in out.groupby(["date", "sort_field"], sort=False):
            docs = [
                {
                    "_id": idx,
                    "inferences": {
                        m: {"score": row[f"inferences.{m}.score"]}
                        for m in components
                        if f"inferences.{m}.score" in out.columns
                        and pd.notna(row[f"inferences.{m}.score"])
                    },
                }
                for idx, row in group.iterrows()
            ]
            scores = compute_ensemble_scores(docs, components)
            for idx, score in scores.items():
                out.at[idx, col_name] = score
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--impressions", type=str, default=str(IMPRESSIONS_PARQUET))
    parser.add_argument("--test-days", type=int, default=30, help="Most recent N dates used for evaluation")
    parser.add_argument("--k", type=int, default=50, help="K for Recall@K / NDCG@K")
    parser.add_argument(
        "--ensemble", action="append", default=[],
        help="Custom ensemble as name:model1,model2,... (repeatable)",
    )
    parser.add_argument("--min-rows", type=int, default=50, help="Skip models with fewer than this many non-null scored rows")
    args = parser.parse_args()

    impressions_path = Path(args.impressions)
    if not impressions_path.exists():
        log.error("%s not found -- run build_impressions.py first.", impressions_path)
        sys.exit(1)

    df = pd.read_parquet(impressions_path)
    log.info("Loaded %d impression rows across %d dates", len(df), df["date"].nunique())

    dates = sorted(df["date"].unique())
    test_dates = set(dates[-args.test_days:])
    train_dates = set(dates) - test_dates
    test_df = df[df["date"].isin(test_dates)].reset_index(drop=True)
    log.info(
        "Time-series holdout: %d train dates, %d test dates (%s .. %s) -> %d test rows (%d positive)",
        len(train_dates), len(test_dates), min(test_dates), max(test_dates),
        len(test_df), int(test_df["y"].sum()),
    )
    if test_df.empty or test_df["y"].sum() == 0:
        log.error("Test period has no positives -- widen --test-days.")
        sys.exit(1)

    client = MongoClient(MONGODB_URI)
    db = client[MONGODB_DB]
    inferences_by_id = fetch_scores(db, test_df["mongo_id"].unique().tolist())

    scored_df = discover_score_columns(test_df, inferences_by_id)

    ensembles_to_eval = dict(ENSEMBLES)
    for spec in args.ensemble:
        name, _, components_str = spec.partition(":")
        if not components_str:
            log.error("Invalid --ensemble spec %r (expected name:model1,model2,...)", spec)
            sys.exit(1)
        ensembles_to_eval[name] = components_str.split(",")
    scored_df = add_ensemble_columns(scored_df, ensembles_to_eval)

    score_cols = [c for c in scored_df.columns if c.startswith("inferences.")]
    incumbent_fields = set(scored_df["sort_field"].unique())

    log.info("Evaluating %d model/ensemble score columns ...", len(score_cols))
    records = []
    for col in sorted(score_cols):
        non_null = scored_df[col].notna().sum()
        if non_null < args.min_rows:
            continue
        eval_df = scored_df[scored_df[col].notna()]
        row = evaluate_model(eval_df, col, args.k)
        row["model"] = col
        row["biased_by_incumbent"] = col in incumbent_fields
        row["test_days"] = args.test_days
        records.append(row)

    if not records:
        log.error("No model had >= %d non-null scored rows in the test period.", args.min_rows)
        sys.exit(1)

    result_df = pd.DataFrame(records)
    ordered_cols = ["model", "biased_by_incumbent", "n_rows", "n_pos", "test_days"] + [
        c for c in result_df.columns if c not in ("model", "biased_by_incumbent", "n_rows", "n_pos", "test_days")
    ]
    result_df = result_df[ordered_cols].sort_values("page_auc_all", ascending=False)

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    if METRICS_CSV.exists():
        existing = pd.read_csv(METRICS_CSV)
        existing = existing[
            ~(existing["model"].isin(result_df["model"]) & (existing["test_days"] == args.test_days))
        ]
        combined = pd.concat([existing, result_df], ignore_index=True)
    else:
        combined = result_df
    combined.to_csv(METRICS_CSV, index=False)
    log.info("Saved -> %s", METRICS_CSV)

    log.info(
        "\n%s",
        result_df[["model", "biased_by_incumbent", "page_auc_p0_1", "page_auc_p0_4", "page_auc_p5_19", "page_auc_p20plus", "page_auc_all", f"recall_at_{args.k}"]]
        .to_string(index=False, float_format="%.4f"),
    )
    log.info(
        "NOTE: recall_at_%d is a reference metric only -- rows marked biased_by_incumbent=True "
        "were used (directly or via an ensemble containing it) to build the impressions in the "
        "first place, so their Recall is structurally inflated (see script docstring / plan section 1.5.2).",
        args.k,
    )


if __name__ == "__main__":
    main()
