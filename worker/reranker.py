"""
Stage-2 supervised re-ranker (recommendation_improvement_plan.md Phase B /
roadmap item S4): a LightGBM model that only ever scores images inside a
day's stage-1 candidate pool (the existing Libra ensemble's top-K).

Single source of truth for feature construction, imported by BOTH:
  - pu-learning/scripts/train_reranker.py  (training, time-series holdout)
  - worker/compute_reranker.py             (daily serving)
so train and serve can never drift out of sync. This matters concretely
here: section 1.5.3 of the plan found that a learned re-ranker applied
outside the population it was trained on collapses (Recall@50 -> 0). The
two-stage design avoids ever exposing it to out-of-pool images, but only if
training computes features against the exact same reference population
serving will use -- see build_feature_matrix()'s docstring.

Feature groups (Qdrant kNN preference similarity, plan section 3.3 Q1, is
deliberately NOT included -- as of S4 implementation, Qdrant multiaxis
coverage over viewed/candidate images was measured at only ~25% (vs. ~90%
for the HDF5 feature store), so the feature would mostly be NaN-imputed to
a constant. Add it once that backfill catches up):
  model_scores -- every inferences.<model>.<field> in FEATURE_SPEC,
                  rank-percentiled within the day's stage-1 POOL (NOT just
                  whatever subset happens to be labeled -- see below)
  tags         -- importantTagProbs.{deepdanbooru,pixai}.<tag>, fixed
                  vocab frozen into FEATURE_SPEC at training time
  meta         -- type (one-hot), log aspect ratio, day-of-week (one-hot)

Why percentiles must be computed within the stage-1 pool, not a labeled
subset: an earlier probe (pu-learning/scripts/eval_reranker_baseline.py)
ranked model scores within each date's *viewed* impressions -- a small,
incumbent-biased sample (only pages the existing ranking already surfaced
near the top). At serving time there is no "viewed subset" for today's
images, only the day's stage-1 pool. Training against the wrong reference
population would make the trained model see a systematically different
score distribution live than during training -- exactly the mechanism
behind the 1.5.3 failure mode. So both train and serve always compute
percentiles over the full CANDIDATE_K-sized pool for that date.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import numpy as np
import pandas as pd

log = logging.getLogger(__name__)

MODEL_KEY = "reranker_v1"

# Stage-1 candidate model: worker/ensembles.py's Libra ensemble, chosen
# because section 1.5.1 found it strongest on deeper pages (broader net)
# while Virgo is only marginally better on p0-1 -- see plan section 4.
CANDIDATE_ENSEMBLE = "ensemble_libra_v1"
CANDIDATE_K = 500

IMAGE_TYPES = ["pixiv", "danbooru", "gelbooru", "sankaku", "twitter"]
DAYS_OF_WEEK = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]

FEATURE_SPEC_VERSION = 1


# ── Stage-1 pool ──────────────────────────────────────────────────────────────

def stage1_pool_ids(db, date: str, ensembles: dict, candidate_ensemble: str = CANDIDATE_ENSEMBLE, k: int = CANDIDATE_K) -> list[str]:
    """Top-`k` mongo _ids for `date`, ranked by `candidate_ensemble`'s rank-average score."""
    from ensembles import compute_ensemble_scores

    components = ensembles[candidate_ensemble]
    proj = {f"inferences.{m}.score": 1 for m in components}
    proj["_id"] = 1
    docs = list(db["images"].find({"status": "inferred", "date": date}, proj))
    scored = compute_ensemble_scores(docs, components)
    ranked = sorted(scored.items(), key=lambda kv: kv[1], reverse=True)[:k]
    return [str(_id) for _id, _ in ranked]


def fetch_pool_docs(db, mongo_ids: list[str]) -> dict[str, dict]:
    """{mongo_id: doc} with the fields build_feature_matrix() needs."""
    from bson import ObjectId

    oids = [ObjectId(i) for i in mongo_ids]
    proj = {"inferences": 1, "importantTagProbs": 1, "type": 1, "width": 1, "height": 1}
    out: dict[str, dict] = {}
    CH = 1000
    for i in range(0, len(oids), CH):
        chunk = oids[i : i + CH]
        for doc in db["images"].find({"_id": {"$in": chunk}}, proj):
            out[str(doc["_id"])] = doc
    return out


# ── Feature construction ─────────────────────────────────────────────────────

def observe_model_score_fields(docs_by_id: dict[str, dict]) -> list[str]:
    fields: set[str] = set()
    for doc in docs_by_id.values():
        for model_key, value in (doc.get("inferences") or {}).items():
            if not isinstance(value, dict):
                continue
            for subfield in value:
                fields.add(f"inferences.{model_key}.{subfield}")
    return sorted(fields)


def observe_tag_vocab(docs_by_id: dict[str, dict]) -> dict[str, list[str]]:
    vocab: dict[str, set[str]] = {"deepdanbooru": set(), "pixai": set()}
    for doc in docs_by_id.values():
        tags = doc.get("importantTagProbs") or {}
        for feature_type in vocab:
            vocab[feature_type].update((tags.get(feature_type) or {}).keys())
    return {k: sorted(v) for k, v in vocab.items()}


def build_feature_names(model_score_fields: list[str], tag_vocab: dict[str, list[str]]) -> list[str]:
    names = [f"pct.{f}" for f in model_score_fields]
    for feature_type, vocab in tag_vocab.items():
        names += [f"tag.{feature_type}.{t}" for t in vocab]
    names += [f"meta.type.{t}" for t in IMAGE_TYPES]
    names.append("meta.log_aspect_ratio")
    names += [f"meta.dow.{d}" for d in DAYS_OF_WEEK]
    return names


def build_feature_matrix(pool_ids: list[str], docs_by_id: dict[str, dict], date: str, spec: dict) -> pd.DataFrame:
    """
    Build the feature matrix for `pool_ids`, indexed by mongo_id.

    IMPORTANT: `pool_ids` must be that date's FULL stage-1 pool (or at least
    the same population used at training time) -- model-score percentiles
    are computed by rank *within this exact list*. Passing a smaller/biased
    subset silently changes what the percentile features mean. Callers that
    need features for a handful of already-labeled rows (e.g. training)
    should still pass the full pool here and select rows afterward.

    Columns always match `spec["feature_names"]` (missing tags -> 0.0,
    missing model scores -> NaN, which the trained sklearn Pipeline's
    imputer handles).
    """
    n = len(pool_ids)
    cols: dict[str, np.ndarray] = {}

    for field in spec["model_score_fields"]:
        _, model_key, subfield = field.split(".", 2)
        raw = np.full(n, np.nan)
        for i, mid in enumerate(pool_ids):
            v = ((docs_by_id.get(mid, {}).get("inferences") or {}).get(model_key) or {}).get(subfield)
            if isinstance(v, (int, float)):
                raw[i] = v
        cols[f"pct.{field}"] = pd.Series(raw).rank(pct=True, na_option="keep").to_numpy()

    for feature_type, vocab in spec["tag_vocab"].items():
        tag_dicts = [
            (docs_by_id.get(mid, {}).get("importantTagProbs") or {}).get(feature_type) or {}
            for mid in pool_ids
        ]
        for tag in vocab:
            cols[f"tag.{feature_type}.{tag}"] = np.array([d.get(tag, 0.0) for d in tag_dicts])

    types = [docs_by_id.get(mid, {}).get("type") or "unknown" for mid in pool_ids]
    for t in IMAGE_TYPES:
        cols[f"meta.type.{t}"] = np.array([1.0 if x == t else 0.0 for x in types])

    log_aspect = []
    for mid in pool_ids:
        doc = docs_by_id.get(mid, {})
        w, h = doc.get("width"), doc.get("height")
        log_aspect.append(np.log((w or 1) / (h or 1)) if w and h else np.nan)
    cols["meta.log_aspect_ratio"] = np.array(log_aspect)

    dow = pd.Timestamp(date).dayofweek
    for d in range(7):
        cols[f"meta.dow.{DAYS_OF_WEEK[d]}"] = np.full(n, 1.0 if d == dow else 0.0)

    df = pd.DataFrame(cols, index=pool_ids)
    return df[spec["feature_names"]]


# ── Feature spec persistence ─────────────────────────────────────────────────

def save_feature_spec(spec: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        json.dump(spec, f, indent=2)


def load_feature_spec(path: Path) -> dict:
    with open(path) as f:
        return json.load(f)
