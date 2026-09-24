"""Room tone is measured from the quietest speech-free stretch, never defaulted.

Fidelity rung R5a (P3: doc J-cuts with room tone): the mix has no
room-tone measurement, so a J/L cut's gap would drop to digital
silence. These tests drive the real measurement on synthetic WAV
fixtures (written with stdlib `wave`, decoded the same way - no ffmpeg
needed): speech spans exclude the loud words, the quietest remaining
gap wins, its level and spectrum are recorded, and staging loops it to
the fill length. The refusal half: a source with no measurable gap
raises `RoomToneRefused` instead of returning a default level.
"""
import math
import os
import struct
import sys
import wave

import pytest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from library.tools.room_tone import (
    MIN_GAP_SECONDS,
    RoomToneRefused,
    decode_mono,
    measure_room_tone,
    rms_dbfs,
    spectrum_db,
    speech_free_gaps,
    stage_fill,
)

SR = 8000


def _write_wav(path, samples, rate=SR):
    packed = struct.pack(f"<{len(samples)}h",
                         *(int(max(-1.0, min(1.0, v)) * 32767.0)
                           for v in samples))
    with wave.open(path, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(packed)


def _fixture(path):
    """4 s: loud word 0.5-1.0 s, quiet room 1.0-3.0 s, loud word 3.0-3.5 s.

    The room is low-level noise plus a 120 Hz hum; the words are a loud
    440 Hz tone. Speech spans cover the words.
    """
    samples = []
    for i in range(4 * SR):
        t = i / SR
        if 0.5 <= t < 1.0 or 3.0 <= t < 3.5:
            samples.append(0.7 * math.sin(2 * math.pi * 440.0 * t))
        else:
            noise = 0.004 * math.sin(2 * math.pi * 120.0 * t)
            noise += 0.001 * math.sin(2 * math.pi * 3000.0 * t + 1.0)
            samples.append(noise)
    _write_wav(path, samples)
    return [(0.5, 1.0), (3.0, 3.5)]


def test_gaps_exclude_speech_and_short_breaths():
    gaps = speech_free_gaps(4.0, [(0.5, 1.0), (3.0, 3.5)])
    assert gaps[0] == pytest.approx((1.0, 3.0))
    # The 0.0-0.5 s lead and 3.5-4.0 s tail survive (both >= MIN_GAP).
    assert (0.0, 0.5) in gaps
    assert (3.5, 4.0) in gaps
    # A 0.1 s breath between words is not room tone.
    assert speech_free_gaps(4.0, [(0.0, 1.95), (2.05, 4.0)]) == []


def test_quietest_gap_wins_and_level_is_measured(tmp_path):
    src = str(tmp_path / "take.wav")
    speech = _fixture(src)
    record = measure_room_tone(src, speech)
    assert record["segment_start"] == pytest.approx(1.0, abs=0.01)
    assert record["segment_end"] == pytest.approx(3.0, abs=0.01)
    # The room hum at 0.004 amplitude is about -48 dBFS: measured, and
    # far from both speech (~-6 dBFS) and digital silence.
    assert -60.0 < record["level_dbfs"] < -35.0
    assert record["peak_dbfs"] < -20.0
    assert record["gaps_considered"] == 3
    assert len(record["spectrum_db"]) == 8
    # The hum lives in the low bands: the 80-250 Hz band must read
    # far hotter than the empty 10-15 kHz band above the fixture's own
    # Nyquist.
    assert record["spectrum_db"][1] > record["spectrum_db"][6] + 20.0


def test_staged_fill_matches_the_measurement(tmp_path):
    src = str(tmp_path / "take.wav")
    speech = _fixture(src)
    record = measure_room_tone(src, speech)
    out = str(tmp_path / "room" / "fill.wav")
    staged = stage_fill(src, record, 0.5, out)
    assert staged["loops"] == 0
    assert staged["fill_seconds"] == pytest.approx(0.5, abs=0.01)
    assert abs(staged["level_dbfs"] - record["level_dbfs"]) < 3.0


def test_short_gap_loops_to_the_fill_length(tmp_path):
    # Speech everywhere except a 0.4 s pause: the fill is longer than
    # the room, so it loops - and stays room, not silence.
    samples = [0.5 * math.sin(2 * math.pi * 440.0 * i / SR)
               if (i / SR < 1.0 or i / SR >= 1.4) else 0.003
               for i in range(4 * SR)]
    src = str(tmp_path / "tight.wav")
    _write_wav(src, samples)
    record = measure_room_tone(src, [(0.0, 1.0), (1.4, 4.0)])
    assert record["segment_end"] - record["segment_start"] == pytest.approx(
        0.4, abs=0.02)
    staged = stage_fill(src, record, 1.0, str(tmp_path / "fill.wav"))
    assert staged["loops"] >= 1
    assert staged["fill_seconds"] == pytest.approx(1.0, abs=0.01)
    assert staged["level_dbfs"] < -30.0


def test_wall_to_wall_speech_refuses_by_name(tmp_path):
    samples = [0.5 * math.sin(2 * math.pi * 440.0 * i / SR)
               for i in range(2 * SR)]
    src = str(tmp_path / "wall.wav")
    _write_wav(src, samples)
    with pytest.raises(RoomToneRefused, match="no speech-free stretch"):
        measure_room_tone(src, [(0.0, 2.0)])


def test_missing_source_refuses_with_the_fix(tmp_path):
    with pytest.raises(RoomToneRefused) as exc:
        measure_room_tone(str(tmp_path / "absent.wav"), [])
    assert "not on disk" in str(exc.value)


def test_silence_reads_as_silence_not_zero():
    assert rms_dbfs([0.0] * 100) == float("-inf")
    assert rms_dbfs([]) == float("-inf")
    assert all(b == float("-inf") for b in spectrum_db([], SR))


def test_decode_mono_reads_the_fixture_exactly(tmp_path):
    src = str(tmp_path / "exact.wav")
    _write_wav(src, [0.5, -0.25, 0.0, 0.125])
    samples, rate = decode_mono(src, sample_rate=SR)
    assert rate == SR
    assert samples[0] == pytest.approx(0.5, abs=1e-4)
    assert samples[1] == pytest.approx(-0.25, abs=1e-4)
    assert samples[3] == pytest.approx(0.125, abs=1e-4)


def test_digital_silence_is_not_room_tone(tmp_path):
    # Pauses of pure digital silence read -inf: they must not win as
    # "the quietest gap", or the staged fill would be silence.
    samples = [0.0] * (2 * SR)
    for i in range(int(0.2 * SR), int(0.8 * SR)):
        samples[i] = 0.5 * math.sin(2 * math.pi * 440.0 * i / SR)
    src = str(tmp_path / "silent.wav")
    _write_wav(src, samples)
    with pytest.raises(RoomToneRefused, match="digital silence"):
        measure_room_tone(src, [(0.2, 0.8)])


def test_records_are_json_safe(tmp_path):
    import json
    src = str(tmp_path / "take.wav")
    speech = _fixture(src)
    record = measure_room_tone(src, speech)
    staged = stage_fill(src, record, 0.5, str(tmp_path / "fill.wav"))
    json.dumps({"room_tone": record, "staged": staged})


def test_min_gap_is_a_measurement_bound_not_taste():
    # 0.30 s at 48 kHz is 14,400 samples: enough for an 8-band
    # spectrum, which is what the bound exists to protect.
    assert MIN_GAP_SECONDS * 48000 >= 4096
