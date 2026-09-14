"""The widened default-as-measured sweep (WP1, principle 3).

`tests/test_assessment_reports_no_default_as_measured.py` sweeps exactly
one producer - `compute_deterministic_assessment`. The defect family it
names spans the codebase ("assume another exists until the sweep says
otherwise"), and the 177-site `audit_p3b.py` pass plus its classification
(`docs/WP1_P3B_CLASSIFICATION.md`) proved it: 16 sites where a swallowed
item let an unmeasured value publish as measured.

This file is the widened gate. For every tranche-1 fix it pins the
admitted absence directly against the fixed function, beiden directions
bound (AGENTS.md 10.4):

- it FAILS on the defect: revert any one fix and the matching test goes
  red (a gate that cannot fail is worse than no gate);
- it does NOT fail on legitimate optional swallows: the last two tests
  pin swallows the classification cleared, so a future "fix" that turns
  an admitted absence into a refusal - or a gate that flags every
  `except: continue` - fails here first (a gate that fails correct
  output is no more coverage than one that cannot fail).
"""

import subprocess
import sys
import types

import pytest

numpy = pytest.importorskip(
    "numpy", reason="measurement math needs numpy; CI installs it",
)

from library.steps.step_1_04_temporal_index import step as step_1_04
from library.tools import beat_grid
from library.tools.analysis import music_pipeline
from library.tools.render_check import check_captions
from library.tools.subject_framing import subject_center_reading

FRAME_SIZE = 160 * 90


def _ffmpeg_result(n_frames, fill=0):
    return subprocess.CompletedProcess(
        args=["ffmpeg"], returncode=0,
        stdout=bytes([fill]) * FRAME_SIZE * n_frames, stderr=b"",
    )


# ── Anchor: step_1_04 optical-flow shift search ───────────────────────


def test_flow_where_no_candidate_survives_is_unknown_not_static(monkeypatch):
    """Every (dx, dy) raising must leave the pair unmeasured.

    Pre-fix this published magnitude 0.0 per pair and classified the
    clip "static" - a default presented as a measurement.

    Only the candidate evaluations fail here: the means the
    classifier reads afterwards keep working, so a fix that merely
    moves the crash (the function's outer handler already converts a
    *later* crash into unknown) does not pass - the zero vectors
    themselves must never publish.
    """
    real_mean = numpy.mean
    calls = {"n": 0}
    # One frame pair (two frames) x 81 shift candidates.
    FAIL_FIRST = 81

    def flaky_mean(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] <= FAIL_FIRST:
            raise RuntimeError("boom")
        return real_mean(*args, **kwargs)

    monkeypatch.setattr(
        subprocess, "run", lambda *a, **k: _ffmpeg_result(2))
    monkeypatch.setattr(numpy, "mean", flaky_mean)
    result = step_1_04.compute_optical_flow_direction("clip.mp4")
    assert result["values"] == []
    assert result["dominant_motion"] == "unknown"


def test_genuine_stillness_still_reads_static(monkeypatch):
    """The mirror: identical frames really are still, and still say so.

    Noise, not a flat fill: on constant frames every shift ties at
    zero and the estimator keeps its first candidate, while on
    identical noise only (0, 0) compares equal and wins outright.
    """
    rng = numpy.random.RandomState(7)
    frame = rng.randint(0, 255, size=(90, 160)).astype(numpy.uint8)
    still = numpy.tile(frame, (3, 1, 1))
    monkeypatch.setattr(
        subprocess, "run",
        lambda *a, **k: subprocess.CompletedProcess(
            args=["ffmpeg"], returncode=0, stdout=still.tobytes(),
            stderr=b""),
    )
    result = step_1_04.compute_optical_flow_direction("clip.mp4")
    assert result["dominant_motion"] == "static"
    assert all(v["magnitude"] == 0.0 for v in result["values"])


# ── music_pipeline windowed key / chords ─────────────────────────────


def _stub_essentia(monkeypatch, seconds, sample_rate=44100):
    """An essentia that loads short or long audio and always answers."""
    fake_standard = types.ModuleType("essentia.standard")

    class _Loader:
        def __init__(self, filename, sampleRate=44100):
            pass

        def __call__(self):
            return [0.0] * int(seconds * sample_rate)

    class _Key:
        def __call__(self, chunk):
            return ("C", "major", 0.9)

    fake_standard.MonoLoader = _Loader
    fake_standard.KeyExtractor = _Key
    fake_package = types.ModuleType("essentia")
    fake_package.standard = fake_standard
    monkeypatch.setitem(sys.modules, "essentia", fake_package)
    monkeypatch.setitem(sys.modules, "essentia.standard", fake_standard)


def test_key_consistency_with_no_surviving_windows_is_none(monkeypatch):
    """Zero windowed estimates is not perfect stability.

    Pre-fix this published consistency 1.0 with the extractor's method
    beside it. A 3-second track is shorter than one 5-second window, so
    short audio alone reaches the defect without any extractor failure.
    """
    _stub_essentia(monkeypatch, seconds=3)
    result = music_pipeline.analyze_key("track.wav")
    assert result["key"] == "C"
    assert result["consistency"] is None
    assert result["key_changes"] == []
    assert result["note"] == "no windowed key estimates survived"


def test_key_consistency_measured_stable_still_reads_one(monkeypatch):
    """The mirror: windows that all agree still read 1.0."""
    _stub_essentia(monkeypatch, seconds=12)
    result = music_pipeline.analyze_key("track.wav")
    assert result["consistency"] == 1.0
    assert len(result["key_changes"]) > 0


def test_chord_count_with_no_surviving_windows_is_none(monkeypatch):
    """Zero windowed estimates is not "no chord changes".

    Pre-fix this published chord_count 0 with the windowed method
    beside it.
    """
    _stub_essentia(monkeypatch, seconds=1)
    result = music_pipeline.analyze_chord_progression("track.wav")
    assert result["chord_count"] is None
    assert result["chord_progression"] == []
    assert result["note"] == "no windowed chord estimates survived"


def test_chord_count_measured_still_counts(monkeypatch):
    """The mirror: windows that answer still produce a count."""
    _stub_essentia(monkeypatch, seconds=12)
    result = music_pipeline.analyze_chord_progression("track.wav")
    assert result["chord_count"] is not None
    assert result["chord_count"] > 0


# ── render_check caption probe geometry ──────────────────────────────


def _caption_plan(**overlay):
    base = {"fps": 30.0}
    base.update(overlay)
    return {"subtitle_overlay": {
        "fps": base.pop("fps"),
        "segments": [{
            "overlay_path": "/nonexistent/caption.mov",
            "timeline_start": 0.0,
            "timeline_end": 1.0,
            "source_in_frame": 0,
            **base,
        }],
    }}


def test_unreadable_overlay_fps_is_a_finding_not_thirty():
    """A declared-but-garbled fps must fail closed, not probe at 30."""
    findings = check_captions("render.mp4", _caption_plan(fps="fast"))
    assert any(not f.passed and "fps" in f.message for f in findings)


def test_unreadable_source_offset_is_a_finding_not_zero():
    """A garbled source offset must fail closed, not probe the head."""
    findings = check_captions(
        "render.mp4", _caption_plan(source_in_frame="somewhere"))
    assert any(not f.passed and "offset" in f.message for f in findings)


def test_missing_overlay_file_with_clean_metadata_reads_unreadable():
    """The mirror: valid plans never hit the new findings.

    Requires ffmpeg on PATH (CI installs it): without a decoder the
    probe cannot run at all, and skipping would make this a gate that
    cannot fail in that environment.
    """
    if __import__("shutil").which("ffmpeg") is None:
        pytest.skip("ffmpeg not on PATH; CI installs it")
    findings = check_captions("render.mp4", _caption_plan())
    assert findings, "a missing overlay must still be reported"
    assert all(not f.passed for f in findings)
    assert all("unreadable" not in f.message or "could not be read" in f.message
               for f in findings), (
        "clean metadata must not route into the corrupt-geometry findings: "
        f"{[f.message for f in findings]}"
    )


# ── Legitimate swallows the gate must not punish ─────────────────────


def test_garbage_beats_are_dropped_and_valid_ones_kept():
    """beat_grid skips non-numeric beats: the grid narrows, nothing is
    invented. A sweep that flagged every `except: continue` would fail
    this correct behaviour."""
    beats = [float(t) for t in range(1, 11)] + ["bogus", None, "1.5x"]
    analysis = {"tempo": {"beats": beats}}
    out = beat_grid.beat_positions(analysis, None, None, None)
    assert out == [float(t) for t in range(1, 11)]


def test_garbage_face_samples_read_as_unmeasurable_not_centred():
    """subject_framing drops bad samples toward unmeasurable-None, which
    the module distinguishes from measured-centred-None. Clearing this
    swallow would collapse that distinction."""
    reading = subject_center_reading(
        {"face_center_x": ["bogus", None, "xx", 0.5, ""],
         "sample_rate_hz": 5},
        0.0, 1.0,
    )
    assert reading.position is None
    assert reading.status == "unmeasurable"
