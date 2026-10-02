"""The program mix is a decision: declared, or measured - never defaulted.

The catalog records every audio stream with what tells them apart. The
captain's field-test MXF (LC4930.MXF, 2026-09-09) carries four identical
mono pcm_s24le streams (one mix, two ISOs, one empty) with no layout,
language, title or disposition, and the catalog once took the FIRST
audio stream and discarded the rest - a non-program stream leaked onto
the timeline. So `select_program_stream` refuses without a declaration,
even where metadata could tell streams apart; a project may instead
measure the mix off the footage (`source.measure_program_stream`). A
declaration always wins; a measurement that is not decisive refuses
exactly like an undeclared one.

Media fixtures are generated with ffmpeg (skipped with a named
environment where it is absent): four sine streams 6 dB apart read as
four program candidates with a clear winner; two streams 1 dB apart
read as ambiguous.
"""

import shutil

import pytest

from library.steps.step_1_02_catalog_footage.step import (
    MEASURE_MARGIN_DB,
    ProgramStreamRefused,
    catalog_footage,
    describe_audio_streams,
    measure_program_selection,
    measure_stream_levels,
    select_program_stream,
)

NEEDS_FFMPEG = pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="needs ffmpeg+ffprobe (CI installs both; local: brew install ffmpeg)",
)

FFPROBE = [
    "ffprobe", "-v", "quiet", "-print_format", "json",
    "-show_format", "-show_streams",
]


def _sine_gains(path, gains, duration=3):
    """One MKV - a video stream plus a mono sine per gain."""
    import subprocess

    inputs = ["-f", "lavfi", "-i",
              f"color=size=320x240:rate=24:duration={duration}:color=black"]
    for _gain in gains:
        inputs += ["-f", "lavfi", "-i",
                   f"sine=frequency=440:duration={duration}"]
    filters = "".join(
        f"[{i + 1}:a]volume={gain}[a{i}];"
        for i, gain in enumerate(gains))
    maps = ["-map", "0:v"]
    for i in range(len(gains)):
        maps += ["-map", f"[a{i}]"]
    cmd = (["ffmpeg", "-y"] + inputs + ["-filter_complex", filters]
           + maps + ["-c:v", "libx264", "-pix_fmt", "yuv420p",
                     "-c:a", "pcm_s16le", "-shortest", str(path)])
    subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                   check=True, timeout=120)


def _probe_streams(path):
    import json
    import subprocess

    result = subprocess.run(
        FFPROBE + [str(path)], capture_output=True, text=True,
        encoding="utf-8", check=True, timeout=60)
    return describe_audio_streams(json.loads(result.stdout))


def _mxf_like_probe():
    """ffprobe output shaped like the captain's MXF: one video stream,
    four IDENTICAL mono audio streams, one data stream."""
    streams = [{
        "index": 0, "codec_name": "h264", "codec_type": "video",
        "width": 3840, "height": 2160, "r_frame_rate": "24000/1001",
        "pix_fmt": "yuv420p", "tags": {},
    }]
    for i in (1, 2, 3, 4):
        streams.append({
            "index": i, "codec_name": "pcm_s24le", "codec_type": "audio",
            "sample_rate": "48000", "channels": 1,
            "bits_per_sample": 24, "tags": {},
            "disposition": {"default": 0, "dub": 0, "original": 0,
                            "comment": 0, "lyrics": 0, "karaoke": 0,
                            "forced": 0, "hearing_impaired": 0,
                            "visual_impaired": 0, "clean_effects": 0,
                            "attached_pic": 0, "timed_thumbnails": 0},
        })
    streams.append({"index": 5, "codec_name": "smpte_436m_anc",
                    "codec_type": "data"})
    return {"streams": streams, "format": {"duration": "10.0", "tags": {}}}


def _video():
    return {"index": 0, "codec_name": "h264", "codec_type": "video",
            "width": 1920, "height": 1080, "r_frame_rate": "30/1",
            "pix_fmt": "yuv420p"}


def test_every_audio_stream_is_recorded_with_what_tells_them_apart():
    streams = describe_audio_streams(_mxf_like_probe())
    assert len(streams) == 4
    first = streams[0]
    assert first["index"] == 1
    assert first["channel"] == 1
    assert first["codec"] == "pcm_s24le"
    assert first["channels"] == 1
    assert first["sample_rate"] == 48000
    for key in ("channel_layout", "language", "title", "handler"):
        assert key in first
    assert [s["channel"] for s in streams] == [1, 2, 3, 4]


def test_the_program_stream_is_declared_single_or_refused():
    mxf = describe_audio_streams(_mxf_like_probe())
    # Indistinguishable streams refuse rather than default to stream 0,
    # naming the source and what was seen.
    with pytest.raises(ProgramStreamRefused) as exc:
        select_program_stream(mxf, declaration=None, source="LC4930.MXF")
    assert "LC4930.MXF" in str(exc.value)
    assert "4 audio streams" in str(exc.value)
    # The project declares, and the engine obeys instead of choosing.
    chosen = select_program_stream(mxf, declaration=1, source="LC4930.MXF")
    assert (chosen["channel"], chosen["index"], chosen["basis"]) == (
        1, 1, "declared")

    # A single stream needs no declaration.
    phone = describe_audio_streams({"streams": [_video(), {
        "index": 1, "codec_name": "aac", "codec_type": "audio",
        "sample_rate": "44100", "channels": 2, "channel_layout": "stereo"}],
        "format": {"duration": "5.0"}})
    chosen = select_program_stream(phone, declaration=None, source="phone.MOV")
    assert (chosen["channel"], chosen["basis"]) == (1, "single")

    # Even distinguishable metadata is not a heuristic the engine may use.
    tagged = describe_audio_streams({"streams": [_video(), {
        "index": 1, "codec_name": "aac", "codec_type": "audio",
        "sample_rate": "48000", "channels": 2, "channel_layout": "stereo",
        "tags": {"language": "eng", "title": "Program Mix"}}, {
        "index": 2, "codec_name": "aac", "codec_type": "audio",
        "sample_rate": "48000", "channels": 8, "channel_layout": "7.1",
        "tags": {"language": "eng", "title": "ISO feeds"}}],
        "format": {"duration": "5.0"}})
    with pytest.raises(ProgramStreamRefused):
        select_program_stream(tagged, declaration=None, source="cam.MXF")
    chosen = select_program_stream(tagged, declaration=1, source="cam.MXF")
    assert chosen["title"] == "Program Mix"

    # A declaration beats a measurement.
    chosen = select_program_stream(
        [{"index": 1, "channel": 1}, {"index": 3, "channel": 2}],
        declaration=2, source="cam.MXF",
        measured_selection={"channel": 1, "basis": "measured-loudest",
                            "levels": {1: -10.0, 2: -20.0}})
    assert (chosen["channel"], chosen["basis"]) == (2, "declared")


@NEEDS_FFMPEG
def test_levels_follow_the_gains_in_stream_order(tmp_path):
    fixture = tmp_path / "four.mkv"
    _sine_gains(fixture, [1.0, 0.5, 0.25, 0.125])
    streams = _probe_streams(fixture)
    assert len(streams) == 4
    levels = measure_stream_levels(str(fixture), streams)
    ordered = [levels[c] for c in (1, 2, 3, 4)]
    assert ordered[0] > ordered[1] > ordered[2] > ordered[3]
    gaps = [a - b for a, b in zip(ordered, ordered[1:])]
    assert all(abs(gap - 6.02) < 0.3 for gap in gaps)


@NEEDS_FFMPEG
def test_an_ambiguous_measurement_refuses_like_an_undeclared_one(tmp_path):
    fixture = tmp_path / "close.mkv"
    _sine_gains(fixture, [1.0, 0.9, 0.25, 0.125])
    streams = _probe_streams(fixture)
    with pytest.raises(ProgramStreamRefused, match="within "
                       f"{MEASURE_MARGIN_DB} dB"):
        measure_program_selection(str(fixture), streams, source="close.mkv")


@NEEDS_FFMPEG
def test_source_block_measure_flag_records_a_measured_selection(tmp_path):
    """A decisive measurement selects the loudest stream, and the
    evidence travels with the decision, per file."""
    fixture = tmp_path / "four.mkv"
    _sine_gains(fixture, [1.0, 0.5, 0.25, 0.125])
    (tmp_path / "project.yaml").write_text(
        "source:\n  measure_program_stream: true\n", encoding="utf-8")
    result = catalog_footage(
        [{"path": str(fixture), "filename": "four.mkv",
          "extension": ".mkv",
          "size_bytes": fixture.stat().st_size,
          "clip_id": "clip_001"}],
        project_folder=str(tmp_path))
    entry = result["clip_catalog"][0]
    assert entry["program_stream"]["channel"] == 1
    assert entry["program_stream"]["basis"] == "measured-loudest"
    assert entry["program_stream_refusal"] is None
    # The evidence travels with the decision, per file.
    assert set(entry["program_stream"]["measured_levels_db"]) == {
        "CH1", "CH2", "CH3", "CH4"}

