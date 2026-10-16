"""Unit tests for library.tools.video_features (TMP-033..041 adapters).

No models, no footage, no network: cache behavior, metric helpers and
structured unavailability. Benchmark measurements themselves are frozen
in docs/benchmarks, not recomputed here.
"""

import json

import numpy as np
import pytest

from library.tools.video_features import cache, metrics
from library.tools.video_features import pe as pe_adapter
from library.tools.video_features import vjepa as vjepa_adapter


def test_cache_key_stable_and_scoped(tmp_path) -> None:
    kwargs = {"source_path": "/v/IMG_1807.MOV", "byte_size": 10,
              "mtime_ns": 5, "start": 0.0, "end": 4.0,
              "model_id": "vjepa2.1-vitb-384", "precision": "fp32"}
    first = cache.cache_key(**kwargs)
    assert first == cache.cache_key(**kwargs)
    assert len(first) == 64
    altered = dict(kwargs, precision="fp16")
    assert cache.cache_key(**altered) != first
    shifted = dict(kwargs, start=0.001)
    assert cache.cache_key(**shifted) != first


def test_cache_store_load_round_trip(tmp_path) -> None:
    key = cache.cache_key(source_path="/v/a.MOV", byte_size=3, mtime_ns=1,
                          start=1.0, end=5.0, model_id="pe-core-s16-384",
                          precision="fp32")
    arrays = {"embedding": np.arange(8, dtype=np.float32)}
    provenance = {"model_id": "pe-core-s16-384", "precision": "fp32"}
    path = cache.store(tmp_path, key, arrays, provenance)
    assert path.is_file()
    loaded_arrays, loaded_provenance = cache.load(tmp_path, key)
    np.testing.assert_array_equal(loaded_arrays["embedding"], arrays["embedding"])
    assert loaded_provenance == provenance


def test_cache_missing_and_corrupt_raise_cache_miss(tmp_path) -> None:
    with pytest.raises(cache.CacheMiss):
        cache.load(tmp_path, "0" * 64)
    bad = tmp_path / ("1" * 64 + ".npz")
    bad.write_bytes(b"not a numpy archive")
    with pytest.raises(cache.CacheMiss):
        cache.load(tmp_path, "1" * 64)


def test_missing_keys_reports_only_absent(tmp_path) -> None:
    present = cache.cache_key(source_path="/v/a.MOV", byte_size=1,
                              mtime_ns=1, start=0.0, end=1.0,
                              model_id="m", precision="fp32")
    cache.store(tmp_path, present, {"x": np.zeros(2)}, {})
    absent = cache.cache_key(source_path="/v/a.MOV", byte_size=1,
                             mtime_ns=1, start=1.0, end=2.0,
                             model_id="m", precision="fp32")
    assert cache.missing_keys(tmp_path, [present, absent]) == [absent]


def test_rank_auc_perfect_partial_and_single_class() -> None:
    assert metrics.rank_auc([1, 1, 0, 0], [0.9, 0.8, 0.2, 0.1]) == 1.0
    assert metrics.rank_auc([1, 0], [0.1, 0.9]) == 0.0
    assert metrics.rank_auc([1, 0], [0.5, 0.5]) == 0.5
    assert np.isnan(metrics.rank_auc([1, 1], [0.5, 0.6]))


def test_average_precision_and_precision_at_k() -> None:
    labels = [1, 0, 1, 0]
    scores = [0.9, 0.8, 0.7, 0.1]
    assert metrics.average_precision(labels, scores) == pytest.approx(
        (1.0 + 2.0 / 3.0) / 2.0)
    assert metrics.precision_at_k(labels, scores, 1) == 1.0
    assert metrics.precision_at_k(labels, scores, 2) == 0.5
    with pytest.raises(ValueError):
        metrics.precision_at_k(labels, scores, 0)


def test_spearman_monotone_inverse_and_constant() -> None:
    assert metrics.spearman([1, 2, 3, 4], [1, 2, 3, 4]) == pytest.approx(1.0)
    assert metrics.spearman([1, 2, 3, 4], [4, 3, 2, 1]) == pytest.approx(-1.0)
    assert metrics.spearman([1, 1, 1], [1, 2, 3]) == 0.0
    with pytest.raises(ValueError):
        metrics.spearman([1, 2], [1])


def test_cosine_orthogonal_identical() -> None:
    assert metrics.cosine([1, 0], [0, 1]) == pytest.approx(0.0)
    assert metrics.cosine([1, 1], [1, 1]) == pytest.approx(1.0)


def test_vjepa_loader_refuses_without_repo_or_weights(tmp_path) -> None:
    with pytest.raises(vjepa_adapter.ModelUnavailable):
        vjepa_adapter.load_encoder(tmp_path / "no-repo", tmp_path / "no.pt")
    repo = tmp_path / "repo"
    (repo / "app" / "vjepa_2_1" / "models").mkdir(parents=True)
    (repo / "app" / "vjepa_2_1" / "models" / "vision_transformer.py").write_text(
        "# publisher stub without vit_base\n")
    with pytest.raises(vjepa_adapter.ModelUnavailable):
        vjepa_adapter.load_encoder(repo, tmp_path / "no.pt")


def test_pe_loader_refuses_without_repo_or_weights(tmp_path) -> None:
    with pytest.raises(pe_adapter.ModelUnavailable):
        pe_adapter.load_tower(tmp_path / "no-repo", tmp_path / "no.pt")


def test_adapter_model_ids_documented() -> None:
    from library.tools.video_features import MODEL_IDS

    assert set(MODEL_IDS) == {"vjepa2.1-vitb-384", "pe-core-s16-384"}
    for entry in MODEL_IDS.values():
        assert entry["publisher"] == "Meta FAIR"
        assert json.dumps(entry)
