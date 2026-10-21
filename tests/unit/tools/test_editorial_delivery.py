"""The evidence taken off the DELIVERED file.

The defect: a timeline can be right and the file wrong. The render can
carry the wrong frame, lose its audio, clip, or hold a black frame
nobody declared, and a harness that reads the timeline calls the run a
success. So the delivery evidence is read off the FILE, and every check
that could not run is NAMED rather than passed.
"""

from __future__ import annotations

import shutil
import subprocess
import wave
from pathlib import Path

import pytest

from library.tools.editorial_acceptance import delivery

FFMPEG = shutil.which("ffmpeg")
FFPROBE = shutil.which("ffprobe")


def _skip_without_ffmpeg() -> None:
    if FFMPEG is None or FFPROBE is None:
        pytest.skip("ffmpeg/ffprobe not on PATH")


def _master_with_silent_tail(tmp_path: Path, *, seconds: float = 4.0,
                             tone_seconds: float = 3.0) -> Path:
    """A lit picture whose last second plays over digital silence.

    `pcm_s16le` in Matroska, not AAC in mp4: a lossy encode smears
    exact zeros into the tens, and the question here is whether the
    delivered sample IS zero.
    """
    import numpy as np

    rate = 48000
    tone = np.round(0.3 * 32767 * np.sin(
        2 * np.pi * 220 * np.arange(int(rate * tone_seconds)) / rate))
    zeros = np.zeros(int(rate * (seconds - tone_seconds)), dtype="<i2")
    wav = tmp_path / "tail.wav"
    with wave.open(str(wav), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(
            np.concatenate([tone, zeros]).astype("<i2").tobytes())
    out = tmp_path / "tail.mkv"
    subprocess.run(
        ["ffmpeg", "-nostdin", "-v", "error", "-y",
         "-f", "lavfi", "-i",
         f"color=c=0x8080a0:s=320x180:r=30:d={seconds}",
         "-i", str(wav),
         "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "pcm_s16le",
         "-shortest", str(out)],
        check=True, capture_output=True)
    return out


def _make_video(path: Path, *, seconds: int = 2, colour: str = "0x345678",
                with_audio: bool = True) -> Path:
    args = [FFMPEG, "-v", "error", "-y", "-f", "lavfi", "-i",
            f"color=c={colour}:s=320x180:r=24:d={seconds}"]
    if with_audio:
        args += ["-f", "lavfi", "-i",
                 f"sine=frequency=440:sample_rate=48000:d={seconds}"]
    args += ["-map", "0:v:0"]
    if with_audio:
        args += ["-map", "1:a:0", "-c:a", "aac", "-shortest"]
    args += ["-c:v", "libx264", "-preset", "ultrafast", "-crf", "40",
             "-pix_fmt", "yuv420p", str(path)]
    subprocess.run(args, capture_output=True, text=True, encoding="utf-8",
                   check=False, timeout=120)
    return path


@pytest.fixture(scope="module")
def video(tmp_path_factory) -> Path:
    """One delivered file, made once: the checks are what is under test,
    not the encoder."""
    if FFMPEG is None or FFPROBE is None:
        pytest.skip("ffmpeg/ffprobe not on PATH")
    root = tmp_path_factory.mktemp("delivery")
    return _make_video(root / "out.mp4")


@pytest.fixture(scope="module")
def silent_video(tmp_path_factory) -> Path:
    if FFMPEG is None or FFPROBE is None:
        pytest.skip("ffmpeg/ffprobe not on PATH")
    root = tmp_path_factory.mktemp("delivery-silent")
    return _make_video(root / "silent.mp4", with_audio=False)


def test_a_delivered_file_carries_its_digest_and_its_own_length(tmp_path,
                                                               video):
    """The defect: a score that names no file, so two runs of the same
    brief are indistinguishable and a report cannot be checked."""
    render = delivery.build_render_evidence(video, workdir=tmp_path / "ev")
    assert len(render["sha256"]) == 64
    assert render["duration_seconds"] == pytest.approx(2.0, abs=0.5)
    assert render["bytes"] > 0
    assert render["path"] == str(video)


def test_the_evidence_is_read_off_the_file_and_can_be_looked_at(tmp_path,
                                                                 video):
    """The defect: the judge is handed a description of the file instead
    of the file, and reads the description."""
    render = delivery.build_render_evidence(video, workdir=tmp_path / "ev")
    kinds = {item["kind"] for item in render["evidence"]}
    assert "frame" in kinds
    assert "audio" in kinds
    for item in render["evidence"]:
        assert item["artifact"]
        assert item["observation"]
    frames = [item for item in render["evidence"] if item["kind"] == "frame"]
    assert frames
    for frame in frames:
        assert Path(frame["artifact"]).exists()


def test_a_check_nobody_declared_is_named_rather_than_passed(tmp_path,
                                                               video):
    """The defect: the render is graded against a guessed frame and a
    guessed rate, and the guesses read as measurements."""
    render = delivery.build_render_evidence(video, workdir=tmp_path / "ev")
    named = {row["name"] for row in render["technical_not_checked"]}
    assert "resolution" in named
    assert "framerate" in named
    assert "duration" in named
    assert "resolution" not in render["technical_verification"]
    assert "audio_streams" in render["technical_verification"]


def test_a_declared_frame_and_rate_are_graded(tmp_path, video):
    """The defect: the delivery format the project declared is ignored,
    so a render in the wrong shape passes."""
    render = delivery.build_render_evidence(
        video, workdir=tmp_path / "ev",
        expected_resolution=(320, 180), expected_fps=24.0,
        expected_duration=2.0)
    checks = render["technical_verification"]
    assert checks["resolution"] is True
    assert checks["framerate"] is True
    assert checks["duration"] is True
    assert render["technical_not_checked"] == []


def test_a_render_in_the_wrong_shape_fails_the_check(tmp_path, video):
    """The defect: the check exists and never fails, which is worse than
    no check because it reads as coverage."""
    render = delivery.build_render_evidence(
        video, workdir=tmp_path / "ev", expected_resolution=(1920, 1080))
    assert render["technical_verification"]["resolution"] is False


def test_a_file_with_no_audio_fails_the_audio_stream_check(tmp_path,
                                                             silent_video):
    """The defect: a silent render is delivered and the gate does not
    look, because the check only ever passed."""
    video = silent_video
    render = delivery.build_render_evidence(video, workdir=tmp_path / "ev")
    assert render["technical_verification"]["audio_streams"] is False


def test_a_file_that_is_not_there_is_refused_rather_than_scored(tmp_path):
    """The defect: an absent render is scored as a delivered one."""
    with pytest.raises(delivery.DeliveryError):
        delivery.build_render_evidence(tmp_path / "nothing.mp4",
                                       workdir=tmp_path / "ev")


def test_the_evidence_directory_survives_until_the_caller_cleans_it(
        tmp_path, video):
    """The defect: the frames are removed before the judge can look at
    them, and the judge is asked to judge a file it cannot see."""
    render = delivery.build_render_evidence(video)
    try:
        directory = Path(render["evidence_directory"])
        assert directory.is_dir()
        assert render["evidence_directory_is_temporary"] is True
        assert delivery.cleanup_evidence(render) is True
        assert not directory.exists()
    finally:
        delivery.cleanup_evidence(render)


def test_a_caller_owned_evidence_directory_is_not_removed(tmp_path, video):
    """The defect: the harness deletes a directory the run owns, and the
    run's own evidence goes with it."""
    owned = tmp_path / "ev"
    render = delivery.build_render_evidence(video, workdir=owned)
    assert render["evidence_directory_is_temporary"] is False
    assert delivery.cleanup_evidence(render) is False
    assert owned.is_dir()


def test_a_reel_that_ends_on_a_silent_end_card_can_never_pass(tmp_path):
    """The defect: the harness has no way to DECLARE an ending, while
    the product's own gate has had one all along - so the same file is
    excused by one gate and condemned by the other."""
    if FFMPEG is None or FFPROBE is None:
        pytest.skip("ffmpeg/ffprobe not on PATH")
    master = _master_with_silent_tail(tmp_path)
    undeclared = delivery.technical_checks(master)
    assert undeclared["checks"]["silence_under_picture"] is False
    declared = delivery.technical_checks(
        master, declared_silence_spans=[(2.5, 4.0)])
    assert declared["checks"]["silence_under_picture"] is True


def test_the_declared_ending_is_read_from_the_manifest_that_declared_it():
    """The defect: the harness holds its own idea of what a declared
    ending is, and the two drift - so a reel's silent end card is
    excused by one gate and not by the other."""
    from library.tools import render_qa

    manifest = {
        "project": {"frame_rate": 25.0},
        "tracks": {"V1": {"clips": [
            {"label": "body", "timeline_in": 0.0, "timeline_out": 4.0},
            {"label": "card", "bookend": "end_card", "video_only": True,
             "timeline_in": 4.0, "timeline_out": 6.0},
        ]}},
    }
    spans = delivery.declared_ending_spans(manifest)
    assert spans == render_qa.declared_ending_spans(manifest)
    assert [tuple(span) for span in spans["silence"]] == [(4.0, 6.0)]


def test_the_delivery_section_is_produced_from_the_file(tmp_path, video):
    """The defect: the record's delivery section was typed by hand, so it
    carried three checks nobody ran and a verdict nobody measured."""
    from library.tools.delivery_spec import resolve_delivery_spec

    section = delivery.build_delivery_section(
        video,
        spec=resolve_delivery_spec("tiktok"),
        timeline_fps=24.0, timeline_frames=48)
    assert section["sha256"] == delivery.sha256_of(video)
    assert section["path"] == str(video)
    assert section["bytes"] > 0
    assert section["duration_seconds"] == pytest.approx(2.0, abs=0.5)
    assert section["technical_not_checked"] == []
    assert section["technical_verification"]["framerate"] is True
    assert section["technical_verification"]["duration"] is True


def test_a_delivery_section_measures_what_nothing_declared_as_unmeasured(
        tmp_path, video):
    """The defect: the frame, the rate and the length were never passed,
    so three checks could not run and the report said nothing about
    them - which reads as coverage."""
    section = delivery.build_delivery_section(video)
    assert {row["name"] for row in section["technical_not_checked"]} == {
        "resolution", "framerate", "duration"}


def test_a_delivery_section_refuses_a_file_that_is_not_there(tmp_path):
    """The defect: an absent render is scored as a delivered one."""
    with pytest.raises(delivery.DeliveryError):
        delivery.build_delivery_section(tmp_path / "nothing.mp4")
