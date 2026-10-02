"""Dense motion measurement: direction, peaks, zoom, and the fallback chain.

Step 1.04 measures motion per shot with dense Farneback flow on a
160x90 proxy at 5 Hz: dominant direction, magnitude over time, motion
peaks (action onsets and apexes), camera-versus-subject separation,
and a MEASURED zoom signal (mean radial divergence) - the replacement
for the always-1.0 `zoom_factor` that `decompose_camera_motion` no
longer reports (`tests/unit/picture/test_no_constant_zoom_factor.py`).

These tests drive the pure pieces on synthetic inputs: peak detection
on a synthetic magnitude curve, direction labels, Farneback pair
stats on synthetic translation / expansion / still fields, the
clip-level classifier on synthetic frame sequences (with extraction
stubbed), and the cache-backfill predicate. The real-footage proof -
peaks on a clip with a clear action, checked against frames - lives
in the PR body, not here: a unit test cannot look at footage.
"""
import os
import sys

import pytest

numpy = pytest.importorskip(
    "numpy", reason="measurement math needs numpy; CI installs it",
)
cv2 = pytest.importorskip(
    "cv2", reason="dense flow needs OpenCV; CI installs it",
)

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from library.steps.step_1_04_temporal_index.step import (  # noqa: E402
    _block_match_shift,
    _compass,
    _direction_of,
    _farneback_pair_stats,
    backfill_motion_measurement,
    compute_optical_flow_direction,
    detect_motion_peaks,
    motion_backfill_needed,
)

HZ = 5


def _noise(seed=0):
    rng = numpy.random.default_rng(seed)
    return (rng.random((90, 160)) * 255).astype(numpy.uint8)


def _shifted(frame, dx):
    out = numpy.zeros_like(frame)
    if dx >= 0:
        out[:, dx:] = frame[:, :-dx] if dx else frame
    else:
        out[:, :dx] = frame[:, -dx:]
    return out


def _expanded(frame, scale=1.1):
    big = cv2.resize(frame, (int(160 * scale), int(90 * scale)))
    zh, zw = big.shape
    top, left = (zh - 90) // 2, (zw - 160) // 2
    return big[top:top + 90, left:left + 160]


# ── Peak detection on a synthetic curve ──────────────────────────────

def test_peaks_find_two_events_with_onsets_before_apexes():
    curve = ([0.03] * 10 + [0.1, 0.3, 0.6, 0.4, 0.15] + [0.04] * 10
             + [0.05, 0.5, 0.7, 0.2] + [0.03] * 10)
    peaks, threshold = detect_motion_peaks(curve, HZ)
    assert threshold == pytest.approx(0.2)
    kinds = [(p["time"], p["kind"]) for p in peaks]
    assert kinds == [(2.4, "onset"), (2.6, "apex"),
                     (5.4, "onset"), (5.6, "apex")]
    assert peaks[1]["magnitude"] == pytest.approx(0.6)


def test_a_curve_with_no_action_peaks_nothing():
    peaks, threshold = detect_motion_peaks([], HZ)
    assert peaks == []
    assert threshold == 0.0
    assert detect_motion_peaks([0.02] * 20, HZ)[0] == []
    # Decaying from the first sample: no guessed start, and the endpoint
    # maximum is not an apex.
    assert detect_motion_peaks(
        [0.8, 0.7, 0.5, 0.2, 0.05, 0.03, 0.03], HZ)[0] == []
    # A prominent bump that never reaches the onset threshold is ripple.
    peaks, threshold = detect_motion_peaks(
        [0.02] * 5 + [0.08, 0.19, 0.08] + [0.02] * 5, HZ)
    assert threshold == pytest.approx(0.2)
    assert peaks == []


def test_a_curve_hovering_on_the_threshold_is_one_onset():
    # Without re-arm hysteresis every wobble across the threshold
    # would read as a new action starting. The curve crosses 0.2
    # three times but never dips back under the re-arm level, so it
    # reads as one sustained action: one onset.
    curve = ([0.05] * 10 + [0.18, 0.25, 0.17, 0.26, 0.19, 0.24, 0.18]
             + [0.05] * 8)
    peaks, threshold = detect_motion_peaks(curve, HZ)
    onsets = [p for p in peaks if p["kind"] == "onset"]
    assert threshold == pytest.approx(0.2)
    assert len(onsets) == 1
    assert onsets[0]["time"] == pytest.approx(2.4)


# ── Direction labels ─────────────────────────────────────────────────

def test_compass_points_and_stillness_floor():
    assert _compass(1.0, 0.0) == "right"
    assert _compass(0.0, -1.0) == "up"
    assert _compass(0.0, 1.0) == "down"
    assert _compass(-1.0, 0.0) == "left"
    assert _direction_of(0.001, 0.0, 0.001) == "static"
    # A zoom's median is ~0 while its mean is not - compassing that
    # noise would present a direction nothing decided.
    assert _direction_of(0.006, -0.005, 0.404) == "mixed"
    assert _direction_of(0.5, 0.0, 0.502) == "right"


# ── Farneback pair stats on synthetic fields ─────────────────────────

def test_translation_reads_as_camera_with_no_residual():
    base = _noise()
    stats = _farneback_pair_stats(base, _shifted(base, 4))
    assert stats["method"] == "farneback"
    assert stats["dx"] == pytest.approx(0.5, abs=0.1)
    assert abs(stats["dy"]) < 0.1
    assert stats["direction"] == "right"
    assert stats["subject_energy"] < 0.05
    assert abs(stats["divergence"]) < 0.05


def test_expansion_reads_as_divergence_with_no_translation():
    base = _noise()
    stats = _farneback_pair_stats(base, _expanded(base))
    assert stats["direction"] == "mixed"
    assert stats["divergence"] > 0.1
    assert abs(stats["dx"]) < 0.1
    assert abs(stats["dy"]) < 0.1
    # A pure zoom leaves nothing for translation to explain.
    assert stats["subject_energy"] == pytest.approx(
        stats["magnitude"], rel=0.2)


def test_block_match_answers_a_translation_where_dense_cannot():
    base = _noise()
    shift = _block_match_shift(base, _shifted(base, 4))
    assert shift is not None
    # Content-motion sign: rightward movement reads positive dx, the
    # same sign the Farneback median carries (the raw search shift
    # points the other way and is negated inside).
    assert shift[0] > 0
    assert shift == (4, 0)


# ── Clip-level classifier on synthetic sequences ─────────────────────

def _frames(kind, n=15):
    base = _noise()
    if kind == "static":
        return [base.copy() for _ in range(n)]
    if kind == "pan":
        return [_shifted(base, i) for i in range(n)]
    if kind == "zoom":
        out = [base.copy()]
        for i in range(1, n):
            out.append(_expanded(out[-1], scale=1.02))
        return out
    raise AssertionError(kind)


def _flow_of(kind, monkeypatch):
    import library.steps.step_1_04_temporal_index.step as step_1_04

    frames = _frames(kind)
    stack = numpy.stack(frames)
    monkeypatch.setattr(
        step_1_04, "_extract_gray_proxy", lambda *_a, **_k: stack)
    return compute_optical_flow_direction("clip.mp4")


def test_static_pan_and_zoom_sequences_classify(monkeypatch):
    flow = _flow_of("static", monkeypatch)
    assert flow["dominant_motion"] == "static"
    assert flow["dominant_direction"] == "static"
    assert flow["method"] == "farneback"
    assert flow["motion_peaks"] == []
    flow = _flow_of("pan", monkeypatch)
    assert flow["dominant_motion"] == "pan_right"
    assert flow["dominant_direction"] == "right"
    # The zoom is measured, not the old constant.
    flow = _flow_of("zoom", monkeypatch)
    assert flow["dominant_motion"] == "zoom_in"
    divergences = [s["divergence"] for s in flow["values"]
                   if "divergence" in s]
    assert divergences and sum(divergences) / len(divergences) > 0


def test_burst_sequence_peaks_where_the_burst_is(monkeypatch):
    import library.steps.step_1_04_temporal_index.step as step_1_04

    base = _noise()
    frames = [base.copy()] * 5
    frames += [_shifted(base, i * 2) for i in range(5)]
    frames += [_shifted(base, 8)] * 5
    stack = numpy.stack(frames)
    monkeypatch.setattr(
        step_1_04, "_extract_gray_proxy", lambda *_a, **_k: stack)
    flow = compute_optical_flow_direction("clip.mp4")
    apexes = [p for p in flow["motion_peaks"] if p["kind"] == "apex"]
    assert apexes, flow["motion_peaks"]
    assert 1.0 <= apexes[0]["time"] <= 2.5
    assert flow["onset_threshold"] > 0


# ── Cache backfill ───────────────────────────────────────────────────

def test_backfill_predicate():
    assert motion_backfill_needed({}) is True
    assert motion_backfill_needed({"optical_flow_direction": None}) is True
    # The block-match era wrote no method.
    assert motion_backfill_needed(
        {"optical_flow_direction": {"values": [], "motion_peaks": []}}
    ) is True
    assert motion_backfill_needed(
        {"optical_flow_direction": {"method": "block_match",
                                    "motion_peaks": []}}) is True
    assert motion_backfill_needed(
        {"optical_flow_direction": {"method": "farneback"}}) is True
    assert motion_backfill_needed(
        {"optical_flow_direction": {"method": "farneback",
                                    "motion_peaks": []}}) is False


def test_backfill_writes_nothing_when_nothing_is_measured(
        monkeypatch, tmp_path):
    import library.steps.step_1_04_temporal_index.step as step_1_04

    clip = tmp_path / "clip.mp4"
    clip.write_bytes(b"0")
    monkeypatch.setattr(
        step_1_04, "compute_optical_flow_direction",
        lambda _p: {"values": [], "dominant_motion": "unknown"})
    index = {"clip_id": "clip_001", "source_file": str(clip)}
    assert backfill_motion_measurement(index) is False
    assert "optical_flow_direction" not in index
    # Without footage the stale document is served untouched.
    index = {"clip_id": "clip_001",
             "source_file": "/nowhere/gone.mp4",
             "optical_flow_direction": {"values": []}}
    assert backfill_motion_measurement(index) is False
    assert index["optical_flow_direction"] == {"values": []}

    # A real measurement upgrades the document in place.
    fresh = {"method": "farneback", "values": [{"dx": 0.1}],
             "motion_peaks": []}
    monkeypatch.setattr(
        step_1_04, "compute_optical_flow_direction", lambda _p: fresh)
    index = {"clip_id": "clip_001", "source_file": str(clip),
             "optical_flow_direction": {"values": []}}
    assert backfill_motion_measurement(index) is True
    assert index["optical_flow_direction"] is fresh

