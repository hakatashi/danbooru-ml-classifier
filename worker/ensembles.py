"""
Rank-average ensembles materialized into `inferences.<key>.score`.

Both ensembles are pure re-aggregations of scores `main.py` has already
computed and written -- no GPU, no new features. Within each day, every
component model's score is converted to a percentile rank (to make scores
from different models/feature spaces comparable) and averaged.

See pu-learning/reports/recommendation_improvement_plan.md (sections 1.5,
1.5.1, 4 Phase A) for how these two component sets were chosen: offline
metrics don't agree on a single winner across page depths, so both are
exposed as separate named sorts (Virgo / Libra) rather than picking one.

  ensemble_virgo_v1 -- rank-average of the 9 pixiv_private models
                       (3 features x 3 methods). Best within-page AUC on the
                       pages actually browsed day to day (pages 0-1).
  ensemble_libra_v1 -- rank-average of 5 hand-picked top models across
                       feature types (incl. one twitter-trained model).
                       Best within-page AUC on deeper pages.
"""

import logging

log = logging.getLogger(__name__)

ALL_FEATURES = ("deepdanbooru", "eva02", "pixai")
ALL_METHODS  = ("biased_svm", "elkan_noto", "nnpu")

ENSEMBLES: dict[str, list[str]] = {
    "ensemble_virgo_v1": [
        f"{feature}_pixiv_private_{method}_joblib"
        for feature in ALL_FEATURES
        for method in ALL_METHODS
    ],
    "ensemble_libra_v1": [
        "pixai_pixiv_private_elkan_noto_joblib",
        "eva02_pixiv_private_nnpu_joblib",
        "eva02_pixiv_private_elkan_noto_joblib",
        "deepdanbooru_pixiv_private_nnpu_joblib",
        "eva02_twitter_nnpu_joblib",
    ],
}


def percentile_ranks(values: list[float]) -> list[float]:
    """
    Convert raw scores to percentile ranks in (0, 1], highest score -> 1.0.

    Ties get the average rank of their block (matches scipy.stats.rankdata's
    default 'average' method), so this doesn't require adding a scipy
    dependency to the worker.
    """
    n = len(values)
    if n == 0:
        return []
    order = sorted(range(n), key=lambda i: values[i])
    ranks = [0.0] * n
    i = 0
    while i < n:
        j = i
        while j + 1 < n and values[order[j + 1]] == values[order[i]]:
            j += 1
        avg_rank = (i + j) / 2 + 1  # 1-based average rank across the tie block
        for k in range(i, j + 1):
            ranks[order[k]] = avg_rank / n
        i = j + 1
    return ranks


def _min_required_components(n_components: int) -> int:
    """A doc needs scores from at least half (rounded up) of an ensemble's
    component models to get an ensemble score; otherwise the average would
    be based on too thin a sample to be meaningful."""
    return (n_components + 1) // 2


def compute_ensemble_scores(
    docs: list[dict],
    component_models: list[str],
) -> dict:
    """
    Compute one ensemble's rank-average score for each doc in `docs`.

    `docs` must all share the same `date` (percentile ranks are computed
    within that population) and must include `inferences.<model>.score` for
    each entry in `component_models` where available.

    Returns {doc_id: score}, omitting docs with fewer than half of the
    component models' scores present.
    """
    n_docs = len(docs)
    if n_docs == 0:
        return {}

    # component_name -> percentile rank per doc index (None if missing)
    per_component_ranks: dict[str, list[float | None]] = {}
    for model in component_models:
        raw: list[float] = []
        present_idx: list[int] = []
        for i, doc in enumerate(docs):
            score = (doc.get("inferences", {}).get(model) or {}).get("score")
            if isinstance(score, (int, float)):
                raw.append(score)
                present_idx.append(i)
        ranks_for_present = percentile_ranks(raw)
        ranks: list[float | None] = [None] * n_docs
        for idx, rank in zip(present_idx, ranks_for_present):
            ranks[idx] = rank
        per_component_ranks[model] = ranks

    min_required = _min_required_components(len(component_models))
    scores: dict = {}
    for i, doc in enumerate(docs):
        values = [
            per_component_ranks[model][i]
            for model in component_models
            if per_component_ranks[model][i] is not None
        ]
        if len(values) < min_required:
            continue
        scores[doc["_id"]] = sum(values) / len(values)

    return scores


def compute_and_write_ensembles(
    db,
    dates: list[str],
    ensemble_names: list[str] | None = None,
    dry_run: bool = False,
) -> dict[str, dict[str, int]]:
    """
    Recompute and write ensemble scores for every date in `dates`.

    Always recomputes (rather than skip-if-exists): each date's percentile
    ranks depend on that day's full population, which can still grow later
    in the day as pending images get inferred, so a stale ensemble score
    would silently drift from what percentile-of-day it actually reflects.

    Returns {date: {ensemble_name: n_written}}.
    """
    from pymongo import UpdateOne

    names = ensemble_names or list(ENSEMBLES.keys())
    for name in names:
        if name not in ENSEMBLES:
            raise ValueError(f"Unknown ensemble: {name!r} (known: {list(ENSEMBLES)})")

    col = db["images"]
    results: dict[str, dict[str, int]] = {}

    for date in dates:
        docs = list(col.find(
            {"status": "inferred", "date": date},
            {"inferences": 1},
        ))
        results[date] = {}
        if not docs:
            log.info("[ensembles] %s: no inferred documents, skipping", date)
            continue

        ops = []
        for name in names:
            component_models = ENSEMBLES[name]
            scores = compute_ensemble_scores(docs, component_models)
            results[date][name] = len(scores)
            for doc_id, score in scores.items():
                ops.append(UpdateOne(
                    {"_id": doc_id},
                    {"$set": {f"inferences.{name}.score": score}},
                ))
            log.info(
                "[ensembles] %s: %s -> %d/%d docs scored",
                date, name, len(scores), len(docs),
            )

        if ops and not dry_run:
            col.bulk_write(ops, ordered=False)

    return results
