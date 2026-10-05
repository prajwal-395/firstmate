"""step_1_04 must report WHERE the subject is, not only whether it is there.

The Haar cascade already returned `(x, y, w, h)` per face and the step kept
only `max(w*h)` as a scalar presence score. The position was measured and
discarded, and nothing else in the pipeline measures it: the v3 vision pass
emits shot size, identity and time ranges, and the two steps that do produce
boxes (`object_segmentation`, matte-triggered, and `ocr_extraction`, wired
and deselected by default) do not feed framing.

Also covered here: an OpenCV without Haar cascades. OpenCV 5 removed them,
`requirements.txt` allowed `>=4.8`, and the resulting AttributeError was
swallowed by the function's broad `except Exception` - so `face_presence`
returned EMPTY values rather than falling back to the variance heuristic.
Empty silently disabled the subject-absence usable-range rule in the vision
pass as well as subject-aware framing.
"""
import ast
import os
import sys
import pytest
import shutil
import subprocess
import json
import stat
from library.steps.step_1_04_temporal_index.step import (
    decompose_camera_motion,
    write_sound_memory,
)
from library.tools.camera_stability import (
    HANDHELD_BELOW,
    STABLE_BELOW,
    read_camera_stability,
    residual_samples,
)
from library.tools.context_views import build_view


PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

STEP_PATH = os.path.join(
    PROJECT_ROOT, "library", "steps", "step_1_04_temporal_index", "step.py")


def _face_presence_returns():
    """Every `return {...}` inside compute_face_presence, as key lists."""
    with open(STEP_PATH, encoding="utf-8") as f:
        tree = ast.parse(f.read())
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "compute_face_presence":
            out = []
            for sub in ast.walk(node):
                if isinstance(sub, ast.Return) and isinstance(sub.value, ast.Dict):
                    out.append([k.value for k in sub.value.keys
                                if isinstance(k, ast.Constant)])
            return out
    pytest.fail("compute_face_presence not found")


def test_every_return_path_carries_face_center_x():
    """Including the error paths.

    A consumer that indexes `face_center_x` must not have to guess whether
    the key is there. Five return sites exist - the happy path, two early
    ffmpeg failures and two exception handlers - and a key present on only
    some of them is the key-name mismatch this codebase is prone to.
    """
    returns = _face_presence_returns()
    assert returns, "no dict returns found"
    missing = [ks for ks in returns if "face_center_x" not in ks]
    assert not missing, f"return sites without face_center_x: {missing}"


class TestCascadeAvailability:
    """`_load_face_cascade` must answer honestly on any OpenCV."""

    def test_returns_none_without_a_usable_cascade(self, monkeypatch, tmp_path):
        """OpenCV 5: the attribute is simply gone."""
        from library.steps.step_1_04_temporal_index import step as s

        class FakeCv2:
            data = None

        monkeypatch.setitem(sys.modules, "cv2", FakeCv2())
        assert s._load_face_cascade() is None

        # An empty CascadeClassifier detects nothing on every frame, which
        # is indistinguishable from "no face in this video" - the worst
        # possible way for this to fail.
        xml = tmp_path / "haarcascade_frontalface_default.xml"
        xml.write_text("<opencv_storage/>")

        class Empty:
            def empty(self):
                return True

        class FakeData:
            haarcascades = str(tmp_path) + os.sep

        class EmptyCv2:
            data = FakeData()

            @staticmethod
            def CascadeClassifier(path):
                return Empty()

        monkeypatch.setitem(sys.modules, "cv2", EmptyCv2())
        assert s._load_face_cascade() is None

    def test_returns_the_classifier_when_everything_is_present(self, monkeypatch, tmp_path):
        from library.steps.step_1_04_temporal_index import step as s

        xml = tmp_path / "haarcascade_frontalface_default.xml"
        xml.write_text("<opencv_storage/>")
        sentinel = type("Loaded", (), {"empty": lambda self: False})()

        class FakeData:
            haarcascades = str(tmp_path) + os.sep

        class FakeCv2:
            data = FakeData()

            @staticmethod
            def CascadeClassifier(path):
                return sentinel

        monkeypatch.setitem(sys.modules, "cv2", FakeCv2())
        assert s._load_face_cascade() is sentinel


# --------------------------------------------------------------------------
# From test_face_sample_aspect.py
#
# Face sampling must not squash the clip before the cascade sees it.
#
# `compute_face_presence` extracted frames with `-vf "fps=5,scale=320:180"`.
# That literal is a landscape shape, applied to every clip regardless of its
# own. A rotated iPhone clip is 1080x1920 after ffmpeg's autorotate, so it
# reached the Haar frontal cascade squashed ~5.3x horizontally - and the
# cascade is trained on undistorted faces. Measured on project 001, the
# stored index split exactly along the `rotation` field: 1,886 detections
# over 3,578 samples on the ten landscape clips, 1 over 461 samples on the
# seven rotated ones. `subject_framing.subject_center_x` therefore returned
# None for every portrait clip, and `compile_manifest._conform_fields`
# computed no pan for them - subject-aware framing was silently unavailable
# on exactly the footage a vertical channel shoots most.
#
# What these tests pin is the PROPERTY, not a detection count. A count is a
# fact about one OpenCV build and one cascade XML and would rot; "the frames
# handed to the detector have the source's aspect ratio" is the thing the
# defect broke and the thing the fix restores.

if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from library.steps.step_1_04_temporal_index import step as s  # noqa: E402

_SECTION_1_MARK = pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="face sampling is an ffmpeg pipeline; without it there is "
           "nothing to measure")


def _synth(path, size, rotation=None, seconds=1):
    """A real video file, because the defect lived in the ffmpeg filter.

    `rotation` writes the display-rotation side data an iPhone carries, so
    the stored frame is landscape and the DISPLAYED frame is portrait -
    the case that produced 461 samples and one detection.
    """
    def _encode(codec):
        args = ["ffmpeg", "-y", "-f", "lavfi",
                "-i", f"testsrc=size={size}:rate=5:duration={seconds}",
                "-c:v", codec, "-pix_fmt", "yuv420p"]
        if codec == "libx264":
            args += ["-preset", "ultrafast"]
        if rotation is not None:
            args += ["-metadata:s:v:0", f"rotate={rotation}"]
        args += ["-v", "error", str(path)]
        return subprocess.run(args, capture_output=True, check=False)

    result = _encode("libx264")
    if result.returncode != 0:
        # A CI image whose ffmpeg was built without libx264 still has mpeg4.
        result = _encode("mpeg4")
    if result.returncode != 0:
        pytest.skip(f"ffmpeg cannot encode a fixture here: "
                    f"{result.stderr.decode('utf-8', 'replace')[:200]}")
    if rotation is not None:
        # -metadata rotate= is ignored by some muxers; set the side data
        # the way ffmpeg 6+ spells it and verify below.
        rotated = str(path) + ".rot.mp4"
        done = subprocess.run(
            ["ffmpeg", "-y", "-display_rotation", str(rotation),
             "-i", str(path), "-c", "copy", "-v", "error", rotated],
            capture_output=True, check=False)
        if done.returncode == 0:
            os.replace(rotated, str(path))
    return str(path)


def _rotated_or_skip(path):
    """The rotation fixture, or a skip if this ffmpeg could not write one.

    Older builds honour neither `-display_rotation` nor a `rotate` tag on
    every muxer. Without the side data the file is simply a landscape clip
    and would prove nothing either way.
    """
    probe = subprocess.run(
        ["ffprobe", "-v", "quiet", "-print_format", "json",
         "-show_streams", "-select_streams", "v:0", path],
        capture_output=True, text=True, check=False)
    if probe.returncode != 0 or "rotation" not in probe.stdout:
        pytest.skip("this ffmpeg cannot write display-rotation side data")
    return path


def _aspect(w, h):
    return w / float(h)


@_SECTION_1_MARK
class TestSampleDimensions:
    """`face_sample_dimensions` is the whole of the fix's decision."""

    def test_portrait_source_is_sampled_portrait(self, tmp_path):
        path = _synth(tmp_path / "portrait.mp4", "270x480")
        w, h = s.face_sample_dimensions(path)
        assert h > w, (
            f"a 270x480 clip was sampled {w}x{h} - a portrait source "
            "sampled landscape is the squash this fix removes")
        assert _aspect(w, h) == pytest.approx(270 / 480.0, rel=0.02)

    def test_rotation_side_data_decides_the_shape(self, tmp_path):
        """The iPhone case: stored landscape, displayed portrait.

        ffmpeg autorotates before the user filter chain, so the frame the
        scale filter receives is the DISPLAY frame. Reading the stored
        width and height and ignoring the rotation would sample this clip
        landscape and reproduce the bug on the exact footage that has it.
        """
        path = _rotated_or_skip(
            _synth(tmp_path / "iphone.mp4", "480x270", rotation=-90))

        w, h = s.face_sample_dimensions(path)
        assert h > w, (
            f"a clip stored 480x270 with rotation -90 displays 270x480 and "
            f"was sampled {w}x{h}")

    def test_it_raises_rather_than_defaulting_to_a_shape(self, tmp_path):
        """A silent fixed shape is the bug. Refusing is the fix."""
        bad = tmp_path / "not_a_video.mp4"
        bad.write_bytes(b"not media")
        with pytest.raises(ValueError):
            s.face_sample_dimensions(str(bad))


@_SECTION_1_MARK
class TestFramesReachingTheCascade:
    """End to end through the real ffmpeg call, which is where it broke.

    `face_sample_dimensions` could be right and `compute_face_presence`
    could still pass a hardcoded `scale=320:180` - it did, for both. So
    this measures the array the cascade is actually handed.
    """

    def _shapes_seen(self, path, monkeypatch):
        seen = []

        class SpyCascade:
            def detectMultiScale(self, gray, **kwargs):
                seen.append(gray.shape)
                return []

        monkeypatch.setattr(s, "_load_face_cascade", lambda: SpyCascade())
        s.compute_face_presence(path)
        return seen

    def test_rotated_frames_are_not_squashed(self, tmp_path, monkeypatch):
        pytest.importorskip("cv2")
        path = _rotated_or_skip(
            _synth(tmp_path / "iphone.mp4", "480x270", rotation=-90,
                   seconds=2))
        seen = self._shapes_seen(path, monkeypatch)
        assert seen, "the cascade was handed no frames at all"
        for h, w in seen:
            assert _aspect(w, h) == pytest.approx(270 / 480.0, rel=0.02), (
                f"cascade was handed {w}x{h} for a clip that displays "
                f"270x480")


@_SECTION_1_MARK
def test_face_center_x_is_normalised_against_the_sampled_width(tmp_path,
                                                               monkeypatch):
    """The width divides out, so the value must not name a constant.

    `face_center_x` was `(fx + fw/2) / 320.0`. With the sample width now
    per clip, a leftover 320 would report a face at the right-hand edge of
    an 854-wide frame as 1.33 - outside `subject_framing`'s plausibility
    bounds, so the pan would be dropped exactly where the subject is
    furthest off centre.
    """
    pytest.importorskip("cv2")
    path = _synth(tmp_path / "landscape.mp4", "480x270", seconds=2)
    sample_w, _sample_h = s.face_sample_dimensions(path)

    class RightEdgeCascade:
        """One "face" flush against the right edge of whatever it is given."""

        def detectMultiScale(self, gray, **kwargs):
            _h, w = gray.shape
            box_w = max(2, w // 10)
            return [(w - box_w, 0, box_w, box_w)]

    monkeypatch.setattr(s, "_load_face_cascade", lambda: RightEdgeCascade())
    out = s.compute_face_presence(path)

    centers = [c for c in out["face_center_x"] if c is not None]
    assert centers, "no centres recorded"
    expected = 1.0 - (max(2, sample_w // 10) / 2.0) / sample_w
    for c in centers:
        assert c == pytest.approx(expected, abs=0.01), (
            f"centre {c} for a face at the right edge of a {sample_w}-wide "
            "frame - normalised against the wrong width")
        assert 0.0 <= c <= 1.0


@_SECTION_1_MARK
def test_face_width_is_recorded_and_parallel_to_the_centre(tmp_path,
                                                           monkeypatch):
    """The size the cascade measures is kept, not thrown away.

    `compute_face_presence` had (x, y, w, h) in hand and recorded only the
    centre, so the conform could aim a crop at the subject but never knew
    whether the crop was wide enough for them. On project 001 it was not,
    and the speaker's face was cut by the frame edge with every gate
    passing. See `library/tools/subject_framing.subject_box`.
    """
    pytest.importorskip("cv2")
    path = _synth(tmp_path / "landscape.mp4", "480x270", seconds=2)
    sample_w, _sample_h = s.face_sample_dimensions(path)
    box_w = max(2, sample_w // 4)

    class QuarterWidthCascade:
        def detectMultiScale(self, gray, **kwargs):
            _h, w = gray.shape
            return [(w // 4, 0, max(2, w // 4), max(2, w // 4))]

    monkeypatch.setattr(s, "_load_face_cascade", lambda: QuarterWidthCascade())
    out = s.compute_face_presence(path)

    assert len(out["face_width"]) == len(out["face_center_x"]) == \
        len(out["values"]), "the three tracks describe the same samples"
    widths = [w for w in out["face_width"] if w is not None]
    assert widths, "no widths recorded"
    for w in widths:
        assert w == pytest.approx(box_w / sample_w, abs=0.01)


# --------------------------------------------------------------------------
# From test_motion_measurement.py
#
# Dense motion measurement: direction, peaks, zoom, and the fallback chain.
#
# Step 1.04 measures motion per shot with dense Farneback flow on a
# 160x90 proxy at 5 Hz: dominant direction, magnitude over time, motion
# peaks (action onsets and apexes), camera-versus-subject separation,
# and a MEASURED zoom signal (mean radial divergence) - the replacement
# for the always-1.0 `zoom_factor` that `decompose_camera_motion` no
# longer reports (`tests/unit/picture/test_temporal_index.py`).
#
# These tests drive the pure pieces on synthetic inputs: peak detection
# on a synthetic magnitude curve, direction labels, Farneback pair
# stats on synthetic translation / expansion / still fields, the
# clip-level classifier on synthetic frame sequences (with extraction
# stubbed), and the cache-backfill predicate. The real-footage proof -
# peaks on a clip with a clear action, checked against frames - lives
# in the PR body, not here: a unit test cannot look at footage.

numpy = pytest.importorskip(
    "numpy", reason="measurement math needs numpy; CI installs it",
)
cv2 = pytest.importorskip(
    "cv2", reason="dense flow needs OpenCV; CI installs it",
)

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


# --------------------------------------------------------------------------
# From test_vision_measure.py
#
# Apple Vision measurement in step 1.04: fallback and phantom filter.
#
# Covers `library/steps/step_1_04_temporal_index/vision_measure.py` - the
# wrapper, not the Swift helper (which the eval measured directly). Each
# test names the defect it would catch; there are no count, existence or
# snapshot tests here.

if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from library.steps.step_1_04_temporal_index import vision_measure


def _vision_only_env(monkeypatch, tmp_path):
    """A machine with no swiftc and an empty helper cache.

    `ensure_helper` consults the temp cache first: without redirecting
    it, a developer machine that once compiled the helper would never
    take the fallback path under test.
    """
    monkeypatch.setattr(shutil, "which", lambda *_, **__: None)
    monkeypatch.setattr(vision_measure.tempfile, "gettempdir",
                        lambda: str(tmp_path))


def test_failed_helper_is_a_fallback_with_all_keys_not_an_exception(
        monkeypatch, tmp_path):
    """A Vision outage must not break the step or its document shape.

    Would catch: `measure_clip_vision` raising (or returning a partial
    dict) when swiftc is missing, so a whole clip fails - or worse,
    downstream `KeyError` on `vision_faces` - on any machine without
    the helper. The fallback carries every owned key empty with the
    reason stamped, the same contract the face_presence tests pin.
    """
    _vision_only_env(monkeypatch, tmp_path)
    doc = vision_measure.measure_clip_vision("no-such-file.mp4", 854, 480)
    for key in vision_measure.VISION_KEYS:
        assert key in doc, f"fallback missing {key}"
    method = doc["vision_method"]
    assert method["engine"] == "none"
    assert str(method["fallback"]).startswith("unavailable:")


def test_working_helper_returns_measurements_not_fallback(tmp_path):
    """A runnable helper must be measured, never fallback-stamped.

    Would catch: the availability/parse path claiming `unavailable`
    despite a working helper (bad `ensure_helper` check, wrong argv,
    JSON shape drift) - the silent form of the fallback defect, where
    the step serves empty Vision documents on a capable machine. The
    fake helper below speaks the real `--list` protocol and returns one
    faced doc per input line; landmarks must survive pairing.
    """
    helper = tmp_path / "vision_helper"
    helper.write_text(
        "#!/usr/bin/env python3\n"
        "import json, sys\n"
        "paths = [l for l in open(sys.argv[sys.argv.index('--list') + 1])"
        ".read().splitlines() if l]\n"
        "docs = [{\"faces\": [{\"box\": [0.4, 0.2, 0.6, 0.5], "
        "\"confidence\": 0.9}], "
        "\"landmarks\": [{\"box\": [0.4, 0.2, 0.6, 0.5], "
        "\"outerLips\": [[0.48, 0.4], [0.52, 0.4]]}], "
        "\"bodies\": [], \"hands\": [], \"segmentation\": {}} "
        "for _ in paths]\n"
        "sys.stdout.write(json.dumps(docs) + \"\\n\")\n"
    )
    helper.chmod(helper.stat().st_mode | stat.S_IEXEC)
    docs = vision_measure.measure_frames(["a.jpg", "b.jpg"], str(helper))
    assert len(docs) == 2
    doc = vision_measure._assemble(docs, 5, "testsha", "854x480")
    assert doc["vision_method"]["engine"] == vision_measure.VISION_METHOD
    assert doc["vision_method"]["fallback"] is None
    assert len(doc["vision_faces"]["samples"]) == 2
    assert (doc["vision_faces"]["samples"][0][0]["landmarks"]
            ["outerLips"] == [[0.48, 0.4], [0.52, 0.4]])


def test_persistence_keeps_tracks_drops_only_isolated():
    """The phantom filter must not eat sequence ends or short tracks.

    Would catch: an off-by-one that consults only the previous sample
    (a face visible on the last two samples dies on the last one), or a
    threshold applied to confidence instead of temporal support (the
    eval proved confidence cannot separate phantoms). Synthetic boxes:
    track A spans samples 0-1, track B is samples 2-3 adjacent to A but
    disjoint, sample 4 holds an isolated phantom.
    """
    A = [0.10, 0.10, 0.30, 0.40]
    A2 = [0.11, 0.10, 0.31, 0.40]
    B = [0.60, 0.60, 0.80, 0.90]
    B2 = [0.61, 0.60, 0.81, 0.90]
    phantom = [0.40, 0.70, 0.50, 0.85]
    kept, removed = vision_measure.apply_persistence(
        [[A], [A2], [B], [B2], [phantom]])
    assert kept == [[True], [True], [True], [True], [False]]
    assert removed == [0, 0, 0, 0, 1]
    # A lone first sample with support only ahead still survives.
    kept, _ = vision_measure.apply_persistence([[A], [A2]])
    assert kept == [[True], [True]]


# --------------------------------------------------------------------------
# From test_no_constant_zoom_factor.py
#
# No constant zoom factor out of step 1.04's camera-motion decomposition.
#
# `decompose_camera_motion` used to emit `zoom_factor =
# 1.0 + max(0, mag - (|dx| + |dy|)) * 0.3` on every sample. Its inputs are
# a single global translation vector per sample with magnitude =
# sqrt(dx^2 + dy^2), which never exceeds |dx| + |dy| - so the max() term
# was 0.0 on every sample and the factor read 1.0 always: a constant
# presented as a measurement, reaching the temporal index as though zoom
# had been estimated. True zoom needs a center-weighted dense field the
# block matcher does not compute, so the key is gone rather than fixed.
#
# This test names that defect: it fails on any decomposition output that
# carries a `zoom_factor` key again.

def test_decomposition_emits_no_zoom_factor():
    flow = {
        "sample_rate_hz": 5,
        "values": [
            {"dx": 0.5, "dy": 0.0, "magnitude": 0.5},
            {"dx": 0.3, "dy": 0.4, "magnitude": 0.5},
            {"dx": 0.0, "dy": 0.0, "magnitude": 0.0},
        ],
    }
    out = decompose_camera_motion(flow)
    assert len(out["values"]) == 3
    for sample in out["values"]:
        assert "zoom_factor" not in sample, (
            f"zoom_factor is back in the decomposition output: {sample} - "
            "it always evaluated to 1.0 and must not be reported"
        )
    assert set(out["values"][0]) == {
        "translation_x", "translation_y", "residual",
    }


# --------------------------------------------------------------------------
# From test_camera_stability.py
#
# The residual is read from the key step 1.04 writes, and the two
# stability signals say when they disagree.
#
# `compute_deterministic_assessment` indexed
# ``temporal_index["camera_motion"]["residual"]``. Step 1.04 writes
# ``camera_motion_decomposition``, with the residual per sample inside
# ``values``. On project 001 ``ti.get("camera_motion")`` was ``None`` on
# 17 of 17 clips, so the optical-flow residual had never been read on any
# clip of any run, every label came from frame differencing, and the label
# called the steadiest clip in the edit ``unstable``.
#
# The test that covered this passed, because its fixture was written in
# the same wrong shape as the reader.

def _index(residuals):
    return {"camera_motion_decomposition": {
        "sample_rate_hz": 5,
        "values": [{"translation_x": 0.0, "translation_y": 0.0,
                    "zoom_factor": 1.0, "residual": r} for r in residuals],
    }}


def test_the_residual_is_read_under_the_key_step_1_04_writes():
    index = _index([0.01] * 20)
    assert len(residual_samples(index)) == 20
    label, method, mean = read_camera_stability(index)
    assert method == "optical_flow_residual"
    assert label == "stable"
    assert mean == pytest.approx(0.01)
    # The shape the reader used to expect answers with no measurement.
    label, method, _ = read_camera_stability(
        {"camera_motion": {"residual": [0.01] * 20}})
    assert (label, method) == ("unknown", "unmeasured")


def test_the_tiers_are_the_search_grid():
    """Half a grid step and one grid step of mean global displacement."""
    assert read_camera_stability(_index([STABLE_BELOW - 0.001] * 20))[0] == "stable"
    assert read_camera_stability(_index([STABLE_BELOW] * 20))[0] == "handheld"
    assert read_camera_stability(_index([HANDHELD_BELOW - 0.001] * 20))[0] == "handheld"
    assert read_camera_stability(_index([HANDHELD_BELOW] * 20))[0] == "unstable"


# ── The disagreement is DATA ────────────────────────────────────────────


def test_the_view_carries_both_signals_a_legend_and_the_disagreement():
    view = build_view("stability", {"semantic_analysis_documents": [
        {"clip_id": "clip_017",
         "assessment": {"camera_stability": "unstable",
                        "camera_stability_method": "optical_flow_residual"},
         "camera": [{"stability": "stable"}]},
        {"clip_id": "clip_002",
         "assessment": {"camera_stability": "unstable",
                        "camera_stability_method": "optical_flow_residual"},
         "camera": [{"stability": "shaky"}]},
    ]})["stability"]

    assert set(view["legend"]) == {
        "deterministic_stability", "deterministic_method",
        "vlm_stability", "signals_agree"}
    rows = {r["clip_id"]: r for r in view["clips"]}
    assert rows["clip_017"]["signals_agree"] == "disagree"
    assert rows["clip_017"]["vlm_stability"] == "stable"
    assert rows["clip_017"]["deterministic_method"] == "optical_flow_residual"
    assert rows["clip_002"]["signals_agree"] == "agree"
    assert "1 of 2" in view["disagreements"]
    assert "clip_017" in view["disagreements"]

    # A label whose signal was not recorded is not a label of nothing.
    view = build_view("stability", {"semantic_analysis_documents": [
        {"clip_id": "clip_001",
         "assessment": {"camera_stability": "handheld"},
         "camera": [{"stability": "stable"}]},
    ]})["stability"]
    assert view["clips"][0]["deterministic_method"] == "unrecorded"


def test_write_sound_memory_serializes_the_measured_panns_events(
        tmp_path, monkeypatch):
    """The defect: the temporal index measured PANNs sound events per
    clip, but the M5 slot reserved for them stayed empty - a reader of
    `sound.json` got nothing. The already-measured events must reach the
    slot, with the method that says whether anything was measured."""
    from library.tools import footage_identity, source_memory

    memory_root = tmp_path / "memory"
    monkeypatch.setenv(source_memory.MEMORY_ROOT_ENV, str(memory_root))

    media = tmp_path / "A.MOV"
    media.write_bytes(b"sound-bytes" * (3 * 1024 * 1024 // 11 + 1))
    digest = footage_identity.fingerprint(str(media))["content_digest"]

    index = {
        "clip_id": "clip_001",
        "source_file": str(media),
        "sound_events": [{"label": "Laughter", "start": 1.0, "end": 2.0,
                          "confidence": 0.9}],
        "sound_event_method": "panns-cnn14",
    }

    write_sound_memory(index, str(media))

    doc = source_memory.read_sound(digest)
    assert doc is not None, "the M5 slot stayed empty"
    assert doc["content_digest"] == digest
    assert doc["sound_events"] == index["sound_events"]
    assert doc["method"] == "panns-cnn14"
    assert doc["status"] == "measured"
