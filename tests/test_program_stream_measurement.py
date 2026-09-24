"""The program mix is declared or measured - and measurement must earn it.

The captain's field-test MXF carries four mono streams (one mix, two
ISOs, one empty) with nothing in the metadata telling them apart, so
`select_program_stream` refuses without a declaration. This file covers
the second route the pipeline offers: measuring the mix off the footage
itself (`source.measure_program_stream`), and the precedence between
the two. A declaration always wins; a measurement that is not decisive
refuses exactly like an undeclared one.

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
def test_a_decisive_measurement_selects_the_loudest_with_evidence(tmp_path):
    fixture = tmp_path / "four.mkv"
    _sine_gains(fixture, [1.0, 0.5, 0.25, 0.125])
    streams = _probe_streams(fixture)
    chosen = measure_program_selection(
        str(fixture), streams, source="four.mkv")
    assert chosen["channel"] == 1
    assert chosen["basis"] == "measured-loudest"
    assert set(chosen["measured_levels_db"]) == {
        "CH1", "CH2", "CH3", "CH4"}


@NEEDS_FFMPEG
def test_an_ambiguous_measurement_refuses_like_an_undeclared_one(tmp_path):
    fixture = tmp_path / "close.mkv"
    _sine_gains(fixture, [1.0, 0.9, 0.25, 0.125])
    streams = _probe_streams(fixture)
    with pytest.raises(ProgramStreamRefused, match="within "
                       f"{MEASURE_MARGIN_DB} dB"):
        measure_program_selection(str(fixture), streams, source="close.mkv")


def test_a_declaration_beats_a_measurement():
    streams = [
        {"index": 1, "channel": 1},
        {"index": 3, "channel": 2},
    ]
    measured = {"channel": 1, "basis": "measured-loudest",
                "levels": {1: -10.0, 2: -20.0}}
    chosen = select_program_stream(
        streams, declaration=2, source="cam.MXF",
        measured_selection=measured)
    assert chosen["channel"] == 2
    assert chosen["basis"] == "declared"


@NEEDS_FFMPEG
def test_source_block_measure_flag_records_a_measured_selection(tmp_path):
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

