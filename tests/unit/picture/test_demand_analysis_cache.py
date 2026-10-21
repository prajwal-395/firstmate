"""PERF-008: a request measures the frames it needs, and reuses the rest.

The defects these name:

* "Avoid running every expensive model on every source interval." -
  before, a request for a window WIDER than an earlier pass covered
  re-measured every frame of the window, including the frames the
  earlier pass had already measured.
* "Reuse previous evidence without treating stale analysis as current." -
  before, a stored pass was served by the window it covered, so a pass
  made by another producer, another configuration or another analysis
  version was served as the current answer.
* "record what was skipped" - before, a run that reused a measurement
  said nothing about it, so the saving had to be inferred from a total.

Each test names one defect; the "before" column is what the pass did
before the analysis cache existed.
"""

from pathlib import Path

import pytest

from library.tools import analysis_cache
from library.tools.analysis import object_grounding as og

PHRASES = ["cell phone"]


@pytest.fixture
def source(monkeypatch):
    """A source of known geometry, with the detector stubbed out.

    The measurement this lane does is expensive ON PURPOSE, so the test
    measures WHICH FRAMES were DETECTED, not how long a model took.  The
    frames are still EXTRACTED in one pass over the source - the
    extraction cost is in the source, not in the number of frames asked
    for - so the two are counted separately.
    """
    measured: list[int] = []
    extracted: list[int] = []

    monkeypatch.setattr(og, "probe_source", lambda path: {
        "fps": 30.0, "frame_count": 1000, "width": 1920, "height": 1080,
        "duration_seconds": 1000 / 30.0})

    def fake_extract(source_file, indices, out_dir):
        extracted.extend(indices)
        return [Path(f"{number + 1:06d}.jpg")
                for number in range(len(indices))]

    def fake_detect(images, phrases, *, producer, score_threshold=None):
        detections = []
        for image in images:
            measured.append(image.name)
            detections.append({
                "phrase": phrases[0], "label": phrases[0],
                "box": [0.1, 0.2, 0.3, 0.4], "score": 0.5,
                "detector": producer.detector, "image": image.name})
        return detections, []

    monkeypatch.setattr(og, "extract_frames", fake_extract)
    monkeypatch.setattr(og, "detect_frames", fake_detect)
    return {"detected": measured, "extracted": extracted}


def _cache(root, *, phrases=PHRASES, producer=None, version=None):
    producer = producer or og.PRODUCERS["owlv2_named"]
    key = og.analysis_key(source_digest="digest-1", producer=producer,
                          phrases=phrases)
    if version:
        key = analysis_cache.AnalysisKey(
            source_identity=key.source_identity, model=key.model,
            configuration=key.configuration, analysis_version=version)
    return analysis_cache.FrameCache("digest-1", key, root=root)


def _pass(root, start, end, *, cache=None, phrases=PHRASES,
          producer=None, version=None):
    producer = producer or og.PRODUCERS["owlv2_named"]
    if cache is None:
        cache = _cache(root, phrases=phrases, producer=producer,
                       version=version)
    return og.detect_window(
        "clip.mxf", phrases=phrases, start_s=start, end_s=end,
        producer=producer, cache=cache, source_digest="digest-1")


def test_a_wider_request_measures_only_the_frames_it_missed(tmp_path, source):
    """Before: a request for [0,20] after a pass over [0,10] measured
    all twenty frames again - the ten the first pass had already
    measured included."""
    cache = _cache(tmp_path)
    _pass(tmp_path, 0.0, 10.0, cache=cache)
    assert len(source["detected"]) == 10
    second = _pass(tmp_path, 0.0, 20.0, cache=cache)
    assert len(source["detected"]) == 20, "the wider request re-measured the ten frames"
    assert second["cache"]["hits"] == 10
    assert second["cache"]["misses"] == 10


def test_the_reused_frames_answer_the_same_question(tmp_path, source):
    """Before: the answer's frames were the frames the LAST pass
    measured, so a reused frame was indistinguishable from a missing
    one."""
    cache = _cache(tmp_path)
    _pass(tmp_path, 0.0, 10.0, cache=cache)
    reused = _pass(tmp_path, 0.0, 20.0, cache=cache)
    measured_only = _pass(tmp_path, 0.0, 20.0,
                          cache=_cache(tmp_path / "other"))
    assert reused["detections"] == measured_only["detections"]
    assert reused["coverage"] == measured_only["coverage"]


def test_a_pass_measured_by_another_producer_is_not_reused(tmp_path, source):
    """Before: the stored rows were found by the window they covered, so
    the cheap detector's pass was served for a request the accurate
    producer was asked for."""
    cache = _cache(tmp_path, producer=og.PRODUCERS["rf_detr_nano"])
    _pass(tmp_path, 0.0, 10.0, cache=cache)
    other = _pass(tmp_path, 0.0, 20.0,
                  cache=_cache(tmp_path, producer=og.PRODUCERS["owlv2_named"]))
    assert other["cache"]["hits"] == 0
    assert len(source["detected"]) == 30, "the wider request measured every frame again"


def test_a_pass_measured_by_another_analysis_version_is_not_reused(
        tmp_path, source):
    """Before: nothing recorded which version of the analysis measured a
    pass, so an older measurement was served as the current one."""
    cache = _cache(tmp_path, version="object-request-v0")
    _pass(tmp_path, 0.0, 10.0, cache=cache)
    current = _pass(tmp_path, 0.0, 20.0, cache=_cache(tmp_path))
    assert current["cache"]["hits"] == 0


def test_a_request_for_another_object_is_not_reused(tmp_path, source):
    """Before: a pass that looked for one object was served for a
    request about another - the pass never looked for it."""
    cache = _cache(tmp_path)
    _pass(tmp_path, 0.0, 10.0, cache=cache)
    other = _pass(tmp_path, 0.0, 20.0, phrases=["handheld device"])
    assert other["cache"]["hits"] == 0


def test_the_frames_are_still_extracted_for_the_identity_half(tmp_path, source):
    """Before: the frames were extracted for the detection only.  Now
    the detection is skipped for a reused frame, and the frame still has
    to be on disk beside it - the identity half reads the frame of every
    detection, measured or reused."""
    cache = _cache(tmp_path)
    _pass(tmp_path, 0.0, 10.0, cache=cache)
    _pass(tmp_path, 0.0, 20.0, cache=cache)
    assert len(source["extracted"]) == 30
    assert len(source["detected"]) == 20


def test_the_run_records_the_frames_it_did_not_measure(tmp_path, source):
    """Before: a run that reused a measurement said nothing about it, so
    the saving could not be read off the run."""
    cache = _cache(tmp_path)
    _pass(tmp_path, 0.0, 10.0, cache=cache)
    second = _pass(tmp_path, 0.0, 20.0, cache=cache)
    assert second["cache"]["reused_frames"]
    assert second["cache"]["measured_frames"]
    assert not set(second["cache"]["reused_frames"]) & set(
        second["cache"]["measured_frames"])


def test_the_cache_can_be_turned_off(tmp_path, source, monkeypatch):
    """Before: there was no cache, so the independent arm of a
    measurement could not be taken from the same code."""
    monkeypatch.setenv("REN_ANALYSIS_CACHE", "off")
    cache = _cache(tmp_path)
    cache.enabled = analysis_cache.cache_enabled()
    _pass(tmp_path, 0.0, 10.0, cache=cache)
    second = _pass(tmp_path, 0.0, 20.0, cache=cache)
    assert second["cache"]["hits"] == 0
    assert len(source["detected"]) == 30, "the wider request measured every frame again"


# ── the row the project keeps ────────────────────────────────────────────

def _coverage_row(result):
    """The Project Model row one pass files."""
    model = {"id": "model-1", "observations": [], "entities": [],
             "provenance": [], "object_classes": [], "tracks": []}
    observations, _provenance = og.build_observations(
        model=model, digest="digest-1", asset_id="asset-1",
        clock_id="clock-1", phrases=PHRASES, result=result)
    return observations


def _result_with_key(key, *, start=0.0, end=10.0):
    return {
        "detections": [],
        "coverage": {"scope": {"start_s": start, "end_s": end},
                     "sample_rate_hz": 1.0, "frames_inspected": 10,
                     "frame_indices": [0, 30, 60, 90, 120, 150, 180, 210,
                                       240, 270],
                     "inspected": [{"start_s": start, "end_s": end}],
                     "unexamined": [], "source_fps": 30.0,
                     "source_frame_count": 1000},
        "producer": {"id": "owlv2_named", "detector": "owlv2",
                     "tier": "standard", "measured_fidelity": 0.5,
                     "note": "measured"},
        "producer_version": og.PRODUCER_VERSION,
        "analysis": og.analysis_key_block(key),
        "cost_seconds": 1.0, "notes": [],
    }


def test_a_stored_row_from_another_key_is_not_served_as_current():
    """Before: a stored row was found by the WINDOW it covered, so a row
    another producer wrote was served as this request's answer."""
    from library.tools import project_model_acquisition as pma

    wanted = og.analysis_key(source_digest="digest-1",
                              producer=og.PRODUCERS["owlv2_named"],
                              phrases=PHRASES)
    stored = og.analysis_key(source_digest="digest-1",
                              producer=og.PRODUCERS["rf_detr_nano"],
                              phrases=PHRASES)
    model = {"id": "model-1",
             "observations": _coverage_row(_result_with_key(stored))}
    hit = pma._cached_object_pass(model, phrases=PHRASES, start_s=0.0,
                                  end_s=10.0, key=wanted)
    assert hit is not None
    assert hit.get("sufficient") is not True
    assert hit["stale"]["differing"] == ["model", "configuration"]


def test_a_stored_row_under_the_same_key_is_served():
    """Before: nothing said which key a row was measured under, so the
    row could not be served on its own terms."""
    from library.tools import project_model_acquisition as pma

    wanted = og.analysis_key(source_digest="digest-1",
                              producer=og.PRODUCERS["owlv2_named"],
                              phrases=PHRASES)
    model = {"id": "model-1",
             "observations": _coverage_row(_result_with_key(wanted))}
    hit = pma._cached_object_pass(model, phrases=PHRASES, start_s=0.0,
                                  end_s=10.0, key=wanted)
    assert hit["sufficient"] is True
