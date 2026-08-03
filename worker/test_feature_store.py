"""Tests for feature_store.py's HDF5 write path (no MongoDB, no GPU)."""

import numpy as np
import pytest
from bson import ObjectId

import feature_store as fs


@pytest.fixture(autouse=True)
def _isolated_features_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(fs, "FEATURES_DIR", tmp_path)


def _docs_and_features(n: int, date: str):
    docs = [{"_id": ObjectId(), "date": date} for _ in range(n)]
    features = {
        name: np.random.rand(n, dim).astype(np.float32)
        for name, dim in fs.FEATURE_DIMS.items()
    }
    return docs, features


class TestWriteFeatures:
    def test_writes_all_features_for_new_docs(self):
        docs, features = _docs_and_features(3, "2026-08-01")
        result = fs.write_features(docs, features)
        assert result["written"] == {"deepdanbooru": 3, "eva02": 3, "pixai": 3}
        assert len(result["complete"]) == 3
        assert all(month == "2026-08" for month in result["complete"].values())

    def test_idempotent_rerun_writes_nothing(self):
        docs, features = _docs_and_features(2, "2026-08-01")
        fs.write_features(docs, features)
        result = fs.write_features(docs, features)
        assert result["written"] == {"deepdanbooru": 0, "eva02": 0, "pixai": 0}
        assert len(result["complete"]) == 2

    def test_docs_without_date_are_skipped(self):
        docs = [{"_id": ObjectId(), "date": None}, {"_id": ObjectId()}]
        features = {name: np.random.rand(2, dim).astype(np.float32) for name, dim in fs.FEATURE_DIMS.items()}
        result = fs.write_features(docs, features)
        assert result["written"] == {"deepdanbooru": 0, "eva02": 0, "pixai": 0}
        assert result["complete"] == {}

    def test_docs_grouped_into_separate_month_shards(self):
        doc_aug = {"_id": ObjectId(), "date": "2026-08-15"}
        doc_jul = {"_id": ObjectId(), "date": "2026-07-15"}
        features = {name: np.random.rand(2, dim).astype(np.float32) for name, dim in fs.FEATURE_DIMS.items()}
        result = fs.write_features([doc_aug, doc_jul], features)
        assert result["complete"][doc_aug["_id"]] == "2026-08"
        assert result["complete"][doc_jul["_id"]] == "2026-07"
        assert fs._shard_path("eva02", "2026-08").exists()
        assert fs._shard_path("eva02", "2026-07").exists()

    def test_only_new_ids_within_a_shard_get_appended(self):
        docs1, features1 = _docs_and_features(2, "2026-08-01")
        fs.write_features(docs1, features1)

        doc3 = {"_id": ObjectId(), "date": "2026-08-02"}
        features2 = {name: np.random.rand(1, dim).astype(np.float32) for name, dim in fs.FEATURE_DIMS.items()}
        result = fs.write_features(docs1 + [doc3], {
            name: np.concatenate([features1[name], features2[name]])
            for name in features1
        })
        assert result["written"] == {"deepdanbooru": 1, "eva02": 1, "pixai": 1}
        # "complete" covers every doc whose features are now fully present in
        # the shard, new or pre-existing -- not just the newly written one.
        assert set(result["complete"]) == {docs1[0]["_id"], docs1[1]["_id"], doc3["_id"]}


class TestH5FeatureStore:
    def test_stored_vectors_round_trip(self, tmp_path):
        store = fs.H5FeatureStore(tmp_path / "eva02" / "2026-08.h5", dim=4)
        vecs = np.array([[1.0, 2.0, 3.0, 4.0], [5.0, 6.0, 7.0, 8.0]], dtype=np.float32)
        store.append(["a", "b"], vecs)

        assert store.existing_ids() == {"a", "b"}

        import h5py
        with h5py.File(store.path, "r") as f:
            assert f["features"].shape == (2, 4)
            assert f["features"].dtype == np.float16
            np.testing.assert_allclose(f["features"][0], vecs[0], atol=1e-2)

    def test_missing_file_has_no_existing_ids(self, tmp_path):
        store = fs.H5FeatureStore(tmp_path / "does-not-exist.h5", dim=4)
        assert store.existing_ids() == set()

    def test_append_empty_is_a_noop(self, tmp_path):
        store = fs.H5FeatureStore(tmp_path / "empty.h5", dim=4)
        store.append([], np.zeros((0, 4), dtype=np.float32))
        assert not store.path.exists() or store.existing_ids() == set()
