"""The transcriber is CONTAINED, so ripping it out is a one-file change.

`desert-ant-cli` was version 0.1.1 and two weeks old when this landed.
The captain's condition for using it at all was that it sit behind one
function the way ffmpeg does, and these tests are what hold that: no
other module in the repository names the binary, its subcommand or its
JSON keys, so replacing it means rewriting `transcribe` to return the
same `HeardSpeech` and changing nothing else.

Nothing here runs the transcriber. The payload is the recorded one from
`tests/fixtures/reel_hearing/`, replayed through the mapper.
"""
import json
import struct
import subprocess
import wave
from pathlib import Path

import pytest

from library.tools import heard_speech

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURE = (REPO_ROOT / "tests" / "fixtures" / "reel_hearing"
           / "reel26.heard.json")

#: The one module allowed to name the vendor, plus the two places that
#: describe the capability in prose. A module added here is a module the
#: one-file change no longer covers.
CONTAINMENT = {
    "library/tools/heard_speech.py",
    "tests/contracts/test_heard_speech.py",
}


def _payload():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


# ── 1. The containment ───────────────────────────────────────────────

def test_only_one_module_names_the_vendor():
    """A key name or a subcommand is the seam; two spellings of it is two
    files to change. BOTH subcommands: `voz` writes the words and `ear`
    names the language the hybrid pass needs."""
    vendor_only = ("durationSec", "processingSec", "loadSec")
    offenders = []
    for path in sorted(REPO_ROOT.rglob("*.py")):
        if "__pycache__" in path.parts or ".venv" in path.parts:
            continue
        relative = str(path.relative_to(REPO_ROOT))
        if relative in CONTAINMENT:
            continue
        body = path.read_text(encoding="utf-8")
        if relative.startswith("library/"):
            offenders += [(relative, key) for key in vendor_only
                          if key in body]
        for subcommand in (heard_speech.SUBCOMMAND,
                           heard_speech.EAR_SUBCOMMAND):
            if f'"{subcommand}"' in body or f"'{subcommand}'" in body:
                offenders.append((relative, subcommand))
    assert offenders == [], (
        f"{offenders} name the transcriber. Every fact about it lives in "
        f"library/tools/heard_speech.py so replacing it is a one-file "
        f"change.")


# ── 1b. The second half the hybrid needs ─────────────────────────────


def test_a_language_payload_that_names_nothing_refuses():
    """An unanswered language question and 'this is English' must never
    arrive as the same object: the caller decides whether the hybrid may
    transcribe at all from this answer."""
    with pytest.raises(heard_speech.TranscriberUnavailable):
        heard_speech.read_language_payload({"confidence": 0.99})


def test_language_identifier_can_read_a_word_timed_wav_sample(
        monkeypatch, tmp_path):
    """Language ID receives speech selected by Voz, not a file's lead-in."""
    media_path = tmp_path / "clip.wav"
    with wave.open(str(media_path), "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(16000)
        audio.writeframes(b"".join(
            struct.pack("<h", value) * 16000
            for value in (100, 200, 300, 400)))

    monkeypatch.setattr(heard_speech, "executable",
                        lambda: "/usr/local/bin/da")
    observed = {}

    def _run(command, **kwargs):
        observed["command"] = command
        sample_path = command[2]
        with wave.open(sample_path, "rb") as sample:
            observed["duration"] = (
                sample.getnframes() / sample.getframerate())
            observed["rate"] = sample.getframerate()
            observed["first_frame"] = sample.readframes(1)
        return subprocess.CompletedProcess(
            command, 0,
            stdout=json.dumps({"language": "en", "confidence": 0.99}),
            stderr="")

    monkeypatch.setattr(heard_speech.subprocess, "run", _run)
    language = heard_speech.identify_language(
        str(media_path), sample_start_seconds=1.25,
        sample_duration_seconds=1.5)

    assert language == heard_speech.HeardLanguage("en", 0.99)
    assert observed["command"][1:2] == [heard_speech.EAR_SUBCOMMAND]
    assert observed["command"][2] != str(media_path)
    assert observed["duration"] == pytest.approx(1.5)
    assert observed["rate"] == 16000
    assert struct.unpack("<h", observed["first_frame"])[0] == 200


# ── 2. What it returns ───────────────────────────────────────────────


def test_the_payload_keeps_only_timed_words_and_names_its_own_failures():
    """A word with no usable timing is dropped rather than guessed. The
    transcriber's own structural failures (it stretches a sentence-final
    word across a silence - 13.44 s, measured on this project's audio)
    are reported BESIDE the findings, never as findings."""
    payload = {"words": [{"text": "kept", "start": 0.0, "end": 0.2},
                         {"text": "no start", "end": 0.5},
                         {"text": "  ", "start": 1.0, "end": 1.2}]}
    assert [w.word for w in heard_speech.read_payload(payload).words] == [
        "kept"]
    payload = {"words": [
        {"text": "something.", "start": 10.0, "end": 23.44},
        {"text": "backwards", "start": 30.0, "end": 30.0},
        {"text": "tick", "start": 40.0, "end": 40.01},
        {"text": "ordinary", "start": 50.0, "end": 50.4},
    ]}
    kinds = [row["kind"] for row in heard_speech.read_payload(payload).anomalies]
    assert kinds == ["word_spans_a_silence", "degenerate_interval",
                     "sub_frame_word"]


# ── 3. What it refuses ───────────────────────────────────────────────


def test_availability_names_what_is_missing(monkeypatch):
    monkeypatch.setattr(heard_speech, "executable", lambda: None)
    installed, detail = heard_speech.available()
    assert installed is False
    assert heard_speech.BINARY in detail
    with pytest.raises(heard_speech.TranscriberUnavailable):
        heard_speech.transcribe("anything.mp4")
