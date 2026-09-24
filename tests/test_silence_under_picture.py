"""Picture on screen with nothing at all on any track.

Nothing in this repository looked for it.  "Every frame of the timeline
must show a clip" (AGENTS.md section 10.2) has no audio twin:
`detect_black_frames` asks whether the picture went away,
`verify_audio_streams` asks only whether an audio stream exists, and
`measure_lufs` asks whether the whole master is deliverable, which an
11% hole barely moves.

Measured on project 001's shipped master: **6.312s of its 56.639s is at
digital zero, 11.1%** - 41.643-43.943s (2.301s) and 52.627-56.639s
(4.011s, the last four seconds of the video), of which 6.274s has a
picture on screen.  Measured on the captain's craft reference, twenty
minutes of finished documentary: **0.783s, 0.06%**, at 1219.379s, and
every frame of it is black.  That is the difference this check is for.

The mechanism on 001 is traceable and no part of it is a bug - a
`transition_slot` declaring `music_behavior: silent` and an `outro`
declaring `fade_out`, both covered by V2 cutaways placed `video_only`,
with no A-roll under them.  Silencing the MUSIC is not silencing the
FILM, and the plan has no vocabulary for the second.

The gate is DIGITAL ZERO alone.  How quiet a declared quiet moment may be
is an open captain decision (`craft-silence-under-picture`), so the
ladder above zero is reported at every rung and gates at none.
"""

import shutil
import subprocess
import wave

import numpy as np
import pytest

from library.tools import render_qa
from library.tools.render_qa import (
    DIGITAL_ZERO_DBFS,
    MIN_SILENCE_FRAMES,
    NEAR_SILENCE_LADDER_DBFS,
    detect_black_frames,
    measure_lufs,
    measure_silence_under_picture,
    verify_audio_streams,
)

FPS = 30
SR = 48000
needs_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None,
                                  reason="ffmpeg/ffprobe not available")


def _master(tmp_path, name: str, seconds: float, audio,
            black_from: float = None):
    """A lossless master: a lit picture, optionally going black, plus `audio`.

    pcm_s16le in Matroska rather than AAC in mp4, because a lossy encode
    smears exact zeros into the tens and the question here is whether the
    delivered sample IS zero.
    """
    wav = tmp_path / f"{name}.wav"
    with wave.open(str(wav), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(SR)
        handle.writeframes(np.asarray(audio, dtype="<i2").tobytes())

    out = tmp_path / f"{name}.mkv"
    chain = []
    if black_from is not None:
        chain = ["-vf", f"drawbox=x=0:y=0:w=320:h=320:color=black@1:t=fill:"
                        f"enable='gte(t,{black_from})'"]
    subprocess.run(
        ["ffmpeg", "-nostdin", "-v", "error", "-y",
         "-f", "lavfi", "-i", f"color=c=0x8080a0:s=320x320:r={FPS}:d={seconds}",
         "-i", str(wav)] + chain +
        ["-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "pcm_s16le",
         "-shortest", str(out)],
        check=True, capture_output=True)
    return str(out)


def _tone(seconds: float, amplitude: float = 0.3):
    t = np.arange(int(SR * seconds)) / SR
    return np.round(amplitude * 32767 * np.sin(2 * np.pi * 220 * t))


def _zeros(seconds: float):
    return np.zeros(int(SR * seconds), dtype="<i2")


# ── the gate ──


@needs_ffmpeg
def test_picture_over_digital_silence_fails_and_says_where(tmp_path):
    """001's own shape: the last stretch of picture plays over nothing."""
    audio = np.concatenate([_tone(3.0), _zeros(1.0)])
    path = _master(tmp_path, "hole", 4.0, audio)

    result = measure_silence_under_picture(path)
    assert not result.passed
    assert result.severity == "error"
    zero = result.value["by_level"]["digital_zero"]
    assert zero["runs"] == 1
    run = zero["where"][0]
    assert 2.9 < run["start"] < 3.1
    assert run["seconds_under_picture"] > 0.9
    assert "digital silence at" in result.detail


@needs_ffmpeg
def test_nothing_else_in_the_module_sees_it(tmp_path):
    """The other checks that could plausibly have caught it, and do not.

    This is the whole reason the check exists: the same master passes
    black frames, the audio-stream count and the loudness gate.
    """
    audio = np.concatenate([_tone(3.0), _zeros(1.0)])
    path = _master(tmp_path, "blind", 4.0, audio)

    assert detect_black_frames(path).passed
    assert verify_audio_streams(path).passed
    assert measure_lufs(path, target_lufs=-14.0, tolerance=30.0).passed
    assert not measure_silence_under_picture(path).passed


@needs_ffmpeg
def test_silence_over_black_is_not_a_defect(tmp_path):
    """The reference's shape: the tail is silent and the picture is gone."""
    audio = np.concatenate([_tone(3.0), _zeros(1.0)])
    path = _master(tmp_path, "tail", 4.0, audio, black_from=2.95)

    result = measure_silence_under_picture(path)
    assert result.passed, result.detail
    zero = result.value["by_level"]["digital_zero"]
    assert zero["seconds"] > 0.9, "the silence must be real, or this proves nothing"
    assert zero["seconds_under_picture"] == 0.0


# ── the ladder reports and judges nothing ──

@needs_ffmpeg
def test_a_quiet_tail_is_reported_at_the_ladder_and_fails_nothing(tmp_path):
    """-70 dBFS under a picture is a number, not a verdict.

    Where the line sits between digital zero and quiet is the captain's
    (`craft-silence-under-picture`); this check measures it and stops.
    """
    quiet = _tone(1.0, amplitude=10 ** (-75.0 / 20.0))
    audio = np.concatenate([_tone(3.0), quiet])
    path = _master(tmp_path, "quiet", 4.0, audio)

    result = measure_silence_under_picture(path)
    assert result.passed, result.detail
    levels = result.value["by_level"]
    assert levels["digital_zero"]["seconds_under_picture"] == 0.0
    assert levels["-70.00"]["seconds_under_picture"] > 0.9
    assert levels["-70.00"]["gates"] is False
    assert result.threshold["ladder_gates"] is False


