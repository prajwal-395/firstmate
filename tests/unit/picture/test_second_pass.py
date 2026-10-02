"""The envelope is measured for the sections the model NAMES.

Captain's choice, verbatim: *"Two-pass: summaries first, envelope for the
section the model names."*

`music_measurement.measure_track` buckets the envelope of seconds 0 to the
length of the edit and NOTHING else, so the richest evidence in step 2.04
described only the head of every track - which is the answer the section
feature exists to let the model move away from.

These run the REAL step 2.04 post-bridge as a subprocess against real
audio, so what is asserted is the exchange the runner would carry.
"""

import json
import math
import os
import shutil
import struct
import subprocess
import sys
import wave

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
sys.path.insert(0, REPO)

from library.tools import second_pass  # noqa: E402
from library.tools.music_measurement import (  # noqa: E402
    section_envelopes,
)

POST_BRIDGE = os.path.join(
    REPO, "library", "steps", "step_2_04_music_selection", "post_bridge.py")

# The measurement is an ffmpeg pipeline, and ffmpeg is a SYSTEM binary the
# CI workflow never installs - not one of the pip packages its
# `torch|sam2|whisperx|parselmouth` filter drops. So on the runner
# `rms_windows` raises FileNotFoundError, `section_envelopes` catches it
# and every row comes back `measured: False` with the reason.
#
# That degradation is the honest one and it is asserted UNCONDITIONALLY
# below, together with the whole two-pass EXCHANGE, which needs no audio
# analysis at all. Only the NUMBERS - the shape of a named section - need
# a real ffmpeg, and only those carry this guard. It is the same
# condition `tests/test_music_measurement.py` already declares.
HAS_FFMPEG = (shutil.which("ffmpeg") is not None
              and shutil.which("ffprobe") is not None)
needs_ffmpeg = pytest.mark.skipif(
    not HAS_FFMPEG, reason="ffmpeg/ffprobe not available")


def _write_track(path, shape):
    """A wav whose level follows `shape` - one amplitude per second."""
    with wave.open(path, "w") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(8000)
        frames = bytearray()
        for amplitude in shape:
            for n in range(8000):
                value = int(amplitude * 20000 * math.sin(2 * math.pi * 220
                                                         * n / 8000))
                frames += struct.pack("<h", value)
        out.writeframes(bytes(frames))
    return path


@pytest.fixture(scope="module")
def track(tmp_path_factory):
    """A track whose head is flat and whose 60-120s section RISES."""
    directory = tmp_path_factory.mktemp("music")
    shape = ([0.3] * 60          # 0-60: flat
             + [0.05 + i * 0.012 for i in range(60)]   # 60-120: rising
             + [0.4] * 40)
    return _write_track(str(directory / "bed.wav"), shape)


# ── The measurement itself ───────────────────────────────────────────

@needs_ffmpeg
def test_a_named_section_gets_the_shape_the_head_of_the_track_had(track):
    rows = section_envelopes(track, [
        {"source_in": 0.0, "source_out": 60.0},
        {"source_in": 60.0, "source_out": 120.0},
    ])
    assert len(rows) == 2
    for row in rows:
        assert row["measured"] is True
        assert len(row["envelope_dbfs"]) == 12
    flat, rising = rows
    assert max(flat["envelope_dbfs"]) - min(flat["envelope_dbfs"]) < 1.0
    assert rising["envelope_dbfs"][-1] > rising["envelope_dbfs"][0] + 6.0, (
        "the shape of a section is exactly what a mean and a spread "
        "cannot carry")


def test_an_unmeasurable_section_never_becomes_a_curve_of_zeroes(monkeypatch,
                                                                 tmp_path):
    """The degradation CI runs on, asserted where CI can see it.

    ffmpeg is a system binary and the runner has none, so this is the
    path that really executes there. An absent measurement is stated -
    never a flat envelope, which would read as a section with no shape
    (AGENTS.md 10.3).
    """
    silent = _write_track(str(tmp_path / "flat.wav"), [0.3] * 30)
    monkeypatch.setattr(shutil, "which", lambda name: None)
    monkeypatch.setenv("PATH", str(tmp_path))
    rows = section_envelopes(silent, [{"source_in": 0.0, "source_out": 30.0}])
    assert len(rows) == 1
    assert rows[0]["measured"] is False
    assert rows[0]["measurement_note"]
    for absent in ("envelope_dbfs", "mean_dbfs", "spread_db"):
        assert absent not in rows[0]
    # And the span it could not measure is still named, so a reader can
    # tell WHICH section went unmeasured.
    assert (rows[0]["source_in"], rows[0]["source_out"]) == (0.0, 30.0)
    # A track that does not exist answers the same way.
    rows = section_envelopes("/nowhere/at/all.wav",
                             [{"source_in": 0.0, "source_out": 30.0}])
    assert rows[0]["measured"] is False
    assert "envelope_dbfs" not in rows[0]
    assert rows[0]["measurement_note"]


# ── The exchange ─────────────────────────────────────────────────────

def _run(payload):
    return subprocess.run(
        [sys.executable, POST_BRIDGE], input=json.dumps(payload),
        capture_output=True, text=True, encoding="utf-8", timeout=300)


def _payload(track, shortlist, pass_number=1):
    return {
        "__pass": pass_number,
        "project_folder": os.path.dirname(track),
        "music_candidates": {
            "target_duration_seconds": 60.0,
            "candidates": [{"title": "Bed", "audio_path": track,
                            "duration_seconds": 160.0, "duration_ok": True}],
        },
        "music_selection": {
            "title": "Bed", "source": "library", "audio_path": track,
            "duration_seconds": 160.0,
            "section": {"source_in": 60.0, "why": "the rising middle"},
            "direction_justification": {
                "direction_mood": "measured, unhurried",
                "why_it_fits": "it settles rather than pushes",
                "forbidden_registers": ["triumphant"],
                "why_not_forbidden": {"triumphant": "no brass, no lift"},
            },
            "candidates_evaluated": [
                {"title": "Bed", "source": "library", "verdict": "chosen",
                 "reason": "the only candidate measured"}],
            "section_shortlist": shortlist,
        },
    }


def test_pass_one_answers_with_the_measurements_and_asks_again(track, capsys):
    shortlist = [
        {"source_in": 0.0, "source_out": 60.0, "why": "the flat head"},
        {"source_in": 60.0, "source_out": 120.0, "why": "the rising middle"},
        {"source_in": 100.0, "source_out": 160.0, "why": "the settled tail"},
    ]
    proc = _run(_payload(track, shortlist))
    assert proc.returncode == 0, proc.stdout + proc.stderr
    answer = json.loads(proc.stdout)
    assert second_pass.REQUEST_KEY in answer, (
        "a shortlist asks for another pass rather than resolving")
    rest, request = second_pass.take(answer)
    assert rest == {}, "the request is not one of the step's outputs"
    body = json.loads(request["context"])
    rows = body["section_measurements"]
    assert len(rows) == 3, (
        "the shortlist is SEVERAL sections and every one of them is "
        "accounted for, measured or not")
    assert [(r["source_in"], r["source_out"]) for r in rows] == [
        (0.0, 60.0), (60.0, 120.0), (100.0, 160.0)], (
        "each row is the section the model named, in the order it named "
        "them")
    # The definitions are in 2.04's prompt, already read by the time
    # this block arrives; they do not travel beside the numbers.
    assert "section_measurements_legend" not in body
    if HAS_FFMPEG:
        assert all(len(r["envelope_dbfs"]) == 12 for r in rows)
    else:
        # No ffmpeg: the shape cannot be measured, and the rows say so
        # rather than carrying a curve of zeroes.
        assert all(r["measured"] is False and r["measurement_note"]
                   for r in rows)
        assert all("envelope_dbfs" not in r for r in rows)
    with capsys.disabled():
        print("\n── pass one asked for a second pass ──")
        print(second_pass.block(request, 2)[:2400])


def test_pass_two_resolves_and_carries_the_evidence(track):
    shortlist = [{"source_in": 0.0, "source_out": 60.0},
                 {"source_in": 60.0, "source_out": 120.0}]
    proc = _run(_payload(track, shortlist, pass_number=2))
    assert proc.returncode == 0, proc.stdout + proc.stderr
    answer = json.loads(proc.stdout)
    assert second_pass.REQUEST_KEY not in answer, (
        "at the bound the answer stands rather than asking forever")
    selection = answer["music_selection"]
    assert selection["section"]["source_in"] == 60.0
    assert len(selection["section_measurements"]) == 2, (
        "the evidence the choice was made from travels with the choice")


# ── The mechanism's own rules ────────────────────────────────────────

def test_a_request_carrying_nothing_new_is_refused():
    with pytest.raises(second_pass.SecondPassError, match="resample"):
        second_pass.take({second_pass.REQUEST_KEY: {"context": "  "}})


def test_a_malformed_shortlist_raises_rather_than_measuring_fewer():
    for bad in ({"section_shortlist": "later"},
                {"section_shortlist": [{"source_in": 10}]},
                {"section_shortlist": [{"source_in": 10, "source_out": 5}]}):
        with pytest.raises(second_pass.SecondPassError):
            second_pass.read_shortlist(bad)
