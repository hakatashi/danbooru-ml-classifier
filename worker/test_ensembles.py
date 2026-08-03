"""Tests for the pure ranking/aggregation logic in ensembles.py (no MongoDB)."""

from ensembles import ENSEMBLES, compute_ensemble_scores, percentile_ranks


class TestPercentileRanks:
    def test_empty(self):
        assert percentile_ranks([]) == []

    def test_single_value(self):
        assert percentile_ranks([0.5]) == [1.0]

    def test_strictly_increasing(self):
        # Highest raw value gets the highest percentile rank (1.0).
        assert percentile_ranks([1.0, 2.0, 3.0]) == [1 / 3, 2 / 3, 1.0]

    def test_strictly_decreasing(self):
        assert percentile_ranks([3.0, 2.0, 1.0]) == [1.0, 2 / 3, 1 / 3]

    def test_ties_get_average_rank(self):
        # Two tied lowest values share rank (1+2)/2=1.5 -> 1.5/4; highest is 4/4.
        result = percentile_ranks([1.0, 1.0, 2.0, 3.0])
        assert result[0] == result[1] == 1.5 / 4
        assert result[2] == 3 / 4
        assert result[3] == 1.0

    def test_all_tied(self):
        result = percentile_ranks([5.0, 5.0, 5.0])
        assert result == [2 / 3, 2 / 3, 2 / 3]


class TestComputeEnsembleScores:
    def _doc(self, doc_id, **model_scores):
        return {
            "_id": doc_id,
            "inferences": {model: {"score": score} for model, score in model_scores.items()},
        }

    def test_averages_percentile_ranks_across_components(self):
        # 2 components, both present on both docs, agreeing on ranking.
        docs = [
            self._doc(1, m1=0.1, m2=0.2),
            self._doc(2, m1=0.9, m2=0.8),
        ]
        scores = compute_ensemble_scores(docs, ["m1", "m2"])
        assert scores[1] == 0.5  # both components rank doc 1 lowest
        assert scores[2] == 1.0  # both components rank doc 2 highest

    def test_missing_component_is_excluded_from_average(self):
        docs = [
            self._doc(1, m1=0.1, m2=0.9),  # m2 missing on doc 2 below
            {"_id": 2, "inferences": {"m1": {"score": 0.9}}},
        ]
        scores = compute_ensemble_scores(docs, ["m1", "m2"])
        # doc 1: m1 rank 0.5 (lowest of 2), m2 rank 1.0 (only value) -> avg 0.75
        assert scores[1] == 0.75
        # doc 2: only m1 present, rank 1.0 (highest of 2) -> avg 1.0
        assert scores[2] == 1.0

    def test_doc_below_half_threshold_is_omitted(self):
        # 3 components; a doc with only 1 present (< ceil(3/2)=2) is dropped.
        docs = [
            self._doc(1, m1=0.5, m2=0.5, m3=0.5),
            {"_id": 2, "inferences": {"m1": {"score": 0.9}}},
        ]
        scores = compute_ensemble_scores(docs, ["m1", "m2", "m3"])
        assert 1 in scores
        assert 2 not in scores

    def test_no_docs_returns_empty(self):
        assert compute_ensemble_scores([], ["m1"]) == {}

    def test_no_docs_have_any_component_returns_empty(self):
        docs = [{"_id": 1, "inferences": {}}, {"_id": 2, "inferences": {}}]
        assert compute_ensemble_scores(docs, ["m1"]) == {}

    def test_non_numeric_score_is_treated_as_missing(self):
        docs = [
            {"_id": 1, "inferences": {"m1": {"score": None}}},
            {"_id": 2, "inferences": {"m1": {"score": 0.5}}},
        ]
        scores = compute_ensemble_scores(docs, ["m1"])
        assert 1 not in scores
        assert scores[2] == 1.0


class TestEnsembleDefinitions:
    def test_virgo_has_nine_pixiv_private_components(self):
        components = ENSEMBLES["ensemble_virgo_v1"]
        assert len(components) == 9
        assert all("pixiv_private" in c for c in components)
        assert len(set(components)) == 9  # no duplicates

    def test_libra_has_five_components(self):
        components = ENSEMBLES["ensemble_libra_v1"]
        assert len(components) == 5
        assert len(set(components)) == 5
