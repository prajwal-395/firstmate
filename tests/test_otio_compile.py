"""The measured laws of `library/tools/otio_compile.py`, each one a defect
that would import a wrong timeline with no error from Resolve."""

import shutil
import subprocess

import pytest

from library.tools import otio_compile as C

RATE = 24000 / 1001


def _clip(document, track=0, item=0):
    clips = [c for c in document["tracks"]["children"][track]["children"]
             if c["OTIO_SCHEMA"].startswith("Clip")]
    return clips[item]


def _params(clip):
    effect = clip["effects"][0]["metadata"]["Resolve_OTIO"]
    return {p["Parameter ID"]: p["Parameter Value"] for p in effect["Parameters"]}


@pytest.fixture
def media(tmp_path):
    path = tmp_path / "LC4932.MXF"
    path.write_bytes(b"")
    return str(path)


def test_source_counts_from_the_start_timecode_and_transforms_from_the_frame(media):
    # Reel 09, Akshita at record 132: API startFrame 34305 on a file whose
    # timecode starts at frame 12296 is OTIO source 46601; API Pan -35 and
    # Tilt -1836 on a 1080x1920 frame are -0.032407 and -0.95625.
    track = C.Track("video", "Akshita", [C.Placement(
        media, source_in=34305, frames=392, record_in=132, media_start=12296,
        media_frames=118468,
        transform={"ZoomX": 2.307, "ZoomY": 2.307, "Pan": -35.0,
                   "Tilt": -1836.0})])
    clip = _clip(C.compile_timeline("R", [track], RATE, 1080, 1920))
    assert clip["source_range"]["start_time"]["value"] == 46601
    params = _params(clip)
    assert params["transformationPan"] == pytest.approx(-0.0324074, abs=1e-6)
    assert params["transformationTilt"] == pytest.approx(-0.95625)
    assert params["transformationZoomX"] == pytest.approx(2.307)


def test_a_record_offset_is_a_gap_and_an_overlap_refuses(media):
    first = C.Placement(media, 0, 10, 5, 0, 100)
    document = C.compile_timeline(
        "R", [C.Track("video", "V", [first])], RATE, 1080, 1920)
    gap = document["tracks"]["children"][0]["children"][0]
    assert gap["OTIO_SCHEMA"] == "Gap.1"
    assert gap["source_range"]["duration"]["value"] == 5
    with pytest.raises(C.OtioCompileError, match="overlaps"):
        C.compile_timeline("R", [C.Track("video", "V", [
            first, C.Placement(media, 0, 10, 10, 0, 100)])], RATE, 1080, 1920)


def test_channel_one_is_source_channel_zero_and_another_refuses(media):
    def audio(channel):
        return [C.Track("audio", "Akshita CH1", [C.Placement(
            media, 0, 10, 0, 0, 100, link_group=1, channel=channel)], "Mono")]
    clip = _clip(C.compile_timeline("R", audio(1), RATE, 1080, 1920))
    assert clip["metadata"]["Resolve_OTIO"]["Channels"] == [
        {"Source Channel ID": 0, "Source Track ID": 0}]
    with pytest.raises(C.OtioCompileError, match="channel 2"):
        C.compile_timeline("R", audio(2), RATE, 1080, 1920)


def test_a_missing_file_refuses_by_name_instead_of_a_silent_none(tmp_path):
    gone = str(tmp_path / "sub_gone.mov")
    with pytest.raises(C.OtioCompileError, match="sub_gone.mov"):
        C.compile_timeline("R", [C.Track("video", "Subtitles", [
            C.Placement(gone, 12, 30, 0, 0, 97)])], RATE, 1080, 1920)


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="needs ffmpeg")
def test_media_start_is_the_container_timecode_at_the_nominal_rate(tmp_path):
    path = tmp_path / "tc.mov"
    subprocess.run(
        ["ffmpeg", "-v", "error", "-f", "lavfi", "-i",
         "testsrc2=size=64x64:rate=24000/1001:duration=0.2",
         "-timecode", "00:08:32:08", str(path)],
        check=True, capture_output=True, encoding="utf-8")
    assert C.media_start_frame(str(path), RATE) == 12296
    plain = tmp_path / "plain.mov"
    subprocess.run(
        ["ffmpeg", "-v", "error", "-f", "lavfi", "-i",
         "testsrc2=size=64x64:rate=24:duration=0.2", str(plain)],
        check=True, capture_output=True, encoding="utf-8")
    assert C.media_start_frame(str(plain), 24) == 0
