"""Face sampling must not squash the clip before the cascade sees it.

`compute_face_presence` extracted frames with `-vf "fps=5,scale=320:180"`.
That literal is a landscape shape, applied to every clip regardless of its
own. A rotated iPhone clip is 1080x1920 after ffmpeg's autorotate, so it
reached the Haar frontal cascade squashed ~5.3x horizontally - and the
cascade is trained on undistorted faces. Measured on project 001, the
stored index split exactly along the `rotation` field: 1,886 detections
over 3,578 samples on the ten landscape clips, 1 over 461 samples on the
seven rotated ones. `subject_framing.subject_center_x` therefore returned
None for every portrait clip, and `compile_manifest._conform_fields`
computed no pan for them - subject-aware framing was silently unavailable
on exactly the footage a vertical channel shoots most.

What these tests pin is the PROPERTY, not a detection count. A count is a
fact about one OpenCV build and one cascade XML and would rot; "the frames
handed to the detector have the source's aspect ratio" is the thing the
defect broke and the thing the fix restores.
"""
import os
import shutil
import subprocess
import sys

import pytest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from library.steps.step_1_04_temporal_index import step as s  # noqa: E402

pytestmark = pytest.mark.skipif(
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
