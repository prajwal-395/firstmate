"""PERF-008: a cached analysis is reusable only under the key it was made with.

The defects these name:

* "Cache successful results by source identity, model, configuration, and
  analysis version." - before, a cached measurement was found by the
  window it covered, and nothing said WHICH model, configuration or
  analysis version produced it, so a pass made by another producer was
  served for this one.
* "Reuse previous evidence without treating stale analysis as current." -
  before, there was no key to compare, so "stale" was not a state a
  reader could be told about.

Each test names one defect; the "before" column is what the reader did
before the key existed.
"""

import json

from library.tools import analysis_cache


def _key(**overrides):
    values = {"source_identity": "digest-1", "model": "owlv2_named",
              "configuration": "cfg-1", "analysis_version": "v1"}
    values.update(overrides)
    return analysis_cache.AnalysisKey(**values)


def test_the_key_names_the_four_things_a_result_is_reusable_under():
    """Before: a cached pass was found by the WINDOW it covered, and the
    reader could not tell which model, configuration or analysis version
    had measured it."""
    key = _key()
    assert set(key.to_dict()) == {"source_identity", "model",
                                   "configuration", "analysis_version"}


def test_two_keys_that_differ_in_one_component_have_different_signatures():
    """Before: one cache slot held every pass over a window, so a second
    model could overwrite the first one's answer."""
    assert _key().signature() != _key(model="rf_detr_nano").signature()
    assert _key().signature() != _key(analysis_version="v2").signature()
    assert _key().signature() != _key(configuration="cfg-2").signature()
    assert _key().signature() != _key(source_identity="digest-2").signature()


def test_a_stale_entry_names_the_component_that_moved():
    """Before: a reader learned only that an entry was not used, never
    WHY - so a stale answer looked like a missing one."""
    cached = _key(model="rf_detr_nano").to_dict()
    differing = analysis_cache.differing_components(cached, _key())
    assert differing == ["model"]


def test_a_document_with_no_key_is_not_a_key():
    """Before: a file with no key could be read as one, and an entry
    with no key cannot say what it measured."""
    assert analysis_cache.AnalysisKey.from_dict({"model": "owlv2"}) is None
    assert analysis_cache.AnalysisKey.from_dict({"model": "", }) is None
    assert analysis_cache.AnalysisKey.from_dict("not a document") is None


def test_a_frame_is_served_only_under_the_key_it_was_measured_with(tmp_path):
    """Before: the store was one list per window, so a pass measured by
    the cheap detector was served for a request the accurate one
    answered."""
    root = tmp_path / "memory"
    key = _key()
    frames = {30: {"detections": [{"phrase": "cell phone", "score": 0.4}]}}
    analysis_cache.store_frames("digest-1", key, producer={"id": "owlv2_named"},
                                source_fps=30.0, sample_rate_hz=1.0,
                                frames=frames, root=root)
    assert analysis_cache.lookup_frames(
        "digest-1", key, [30], root=root) == frames
    assert analysis_cache.lookup_frames(
        "digest-1", _key(model="rf_detr_nano"), [30], root=root) == {}
    assert analysis_cache.lookup_frames(
        "digest-1", _key(analysis_version="v2"), [30], root=root) == {}


def test_the_store_lives_beside_the_source_measurements(tmp_path):
    """Before: a cached pass had no address at all - it was a row in one
    project's model, so another project could not find it."""
    root = tmp_path / "memory"
    path = analysis_cache.cache_path("digest-1", root)
    assert path.parent.name == "digest-1"
    assert path.name == analysis_cache.CACHE_FILENAME


def test_a_frame_measured_twice_is_one_measurement(tmp_path):
    """Before: a second pass over the same frame added a second row for
    it, so the count of measurements stopped meaning anything."""
    root = tmp_path / "memory"
    key = _key()
    for score in (0.4, 0.9):
        analysis_cache.store_frames(
            "digest-1", key, producer={"id": "owlv2_named"},
            source_fps=30.0, sample_rate_hz=1.0,
            frames={30: {"detections": [{"phrase": "cell phone",
                                         "score": score}]}}, root=root)
    entries = analysis_cache.read_store("digest-1", root)
    assert len(entries) == 1
    assert len(entries[key.signature()]["frames"]) == 1


def test_a_store_that_is_not_a_store_is_not_read(tmp_path):
    """Before: a half-written or foreign file was read as a cache, so a
    reader could serve an entry that says nothing about what it
    measured."""
    root = tmp_path / "memory"
    path = analysis_cache.cache_path("digest-1", root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"format": "something-else/9"}),
                     encoding="utf-8")
    assert analysis_cache.read_store("digest-1", root) == {}


def test_the_cache_can_be_turned_off(monkeypatch):
    """Before: there was no cache to turn off, so a benchmark had no way
    to measure the independent arm."""
    monkeypatch.delenv("REN_ANALYSIS_CACHE", raising=False)
    assert analysis_cache.cache_enabled() is True
    monkeypatch.setenv("REN_ANALYSIS_CACHE", "off")
    assert analysis_cache.cache_enabled() is False
