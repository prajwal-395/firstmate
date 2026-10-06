"""Room tone: the measured quiet of each source, and the dropouts in it.

`room_tone.find_dropouts` is the defect this file pins: a source that
carries audible room can still hold digital-silence spans inside the
ranges the edit plays - the stretches where the room drops out after a
dialogue edit. Before it, nothing detected those spans; the mix shipped
with them as holes of digital silence. The fixtures are written with
stdlib `wave` and decoded the same way, so they never need ffmpeg.
"""
import os
import sys
import math
import struct
import wave

import pytest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from library.tools.room_tone import (
    MIN_DROPOUT_SECONDS,
    find_dropouts,
    measure_room_tone,
    stage_fill,
)

SR = 48000


def _write_wav(path, samples, rate=SR):
    packed = struct.pack(f"<{len(samples)}h",
                         *(int(max(-1.0, min(1.0, v)) * 32767.0)
                           for v in samples))
    with wave.open(path, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(packed)


def _room_with_gap(gap_start=2.0, gap_end=2.5, total=5.0, room=0.004):
    """Room tone throughout, with one digital-silence gap."""
    samples = []
    for i in range(int(total * SR)):
        t = i / SR
        if gap_start <= t < gap_end:
            samples.append(0.0)
        else:
            samples.append(room * math.sin(2 * math.pi * 120.0 * t))
    return samples


def test_dropsout_inside_played_range_is_detected(tmp_path):
    """The defect: a digital-silence span inside the played range is a
    dropout, and it is measured - not shipped as a silent hole."""
    path = str(tmp_path / "source.wav")
    _write_wav(path, _room_with_gap())
    dropouts = find_dropouts(path, [(0.0, 5.0)])
    assert len(dropouts) == 1
    assert dropouts[0]["start"] == pytest.approx(2.0, abs=0.01)
    assert dropouts[0]["end"] == pytest.approx(2.5, abs=0.01)
    assert dropouts[0]["duration"] == pytest.approx(0.5, abs=0.01)


def test_no_digital_silence_no_dropout(tmp_path):
    """A source whose played range carries only room tone has no
    dropout - the measurement does not invent one."""
    path = str(tmp_path / "source.wav")
    samples = [0.004 * math.sin(2 * math.pi * 120.0 * i / SR)
               for i in range(int(5.0 * SR))]
    _write_wav(path, samples)
    assert find_dropouts(path, [(0.0, 5.0)]) == []


def test_gap_outside_played_range_is_not_a_dropout(tmp_path):
    """A digital-silence span the edit does not play is not a dropout -
    the fill would land where nothing plays."""
    path = str(tmp_path / "source.wav")
    _write_wav(path, _room_with_gap(gap_start=2.0, gap_end=2.5))
    assert find_dropouts(path, [(0.0, 1.0)]) == []


def test_dropout_shorter_than_the_floor_is_not_reported(tmp_path):
    """A zero span below the measurement floor is a zero crossing, not
    a hole the ear hears - the same floor render_qa measures silence at."""
    path = str(tmp_path / "source.wav")
    samples = [0.004 * math.sin(2 * math.pi * 120.0 * i / SR)
               for i in range(int(5.0 * SR))]
    for i in range(int(0.005 * SR)):  # 5 ms - below MIN_DROPOUT_SECONDS
        samples[int(2.0 * SR) + i] = 0.0
    _write_wav(path, samples)
    assert find_dropouts(path, [(0.0, 5.0)]) == []


def test_room_tone_and_dropouts_agree_on_one_source(tmp_path):
    """The room tone comes from the audible gaps; the dropout is the
    zero span. Both are measured on the same decode, and a source whose
    only pauses are digital silence refuses the room while the dropout
    is still found - the two readings do not contradict."""
    path = str(tmp_path / "source.wav")
    _write_wav(path, _room_with_gap())
    record = measure_room_tone(path, speech_spans=[])
    assert record["level_dbfs"] > float("-inf")
    dropouts = find_dropouts(path, [(0.0, 5.0)])
    assert len(dropouts) == 1


def test_stage_fill_loops_the_measured_room_over_the_dropout(tmp_path):
    """The fill is the measured room, looped to the dropout's length -
    never a default level reported as measured. The dropout is longer
    than the room gap, so the loop is exercised."""
    path = str(tmp_path / "source.wav")
    _write_wav(path, _room_with_gap(gap_start=1.0, gap_end=2.8, total=3.0))
    record = measure_room_tone(path, speech_spans=[(1.0, 2.8)])
    out = str(tmp_path / "fill.wav")
    staged = stage_fill(path, record, 1.8, out)
    assert os.path.isfile(out)
    assert staged["fill_seconds"] == pytest.approx(1.8, abs=0.01)
    assert staged["loops"] >= 1
