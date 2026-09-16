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
from pathlib import Path

import pytest

from library.tools import heard_speech

REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURE = (REPO_ROOT / "tests" / "fixtures" / "reel_hearing"
           / "reel26.heard.json")

#: The one module allowed to name the vendor, plus the two places that
#: describe the capability in prose. A module added here is a module the
#: one-file change no longer covers.
CONTAINMENT = {
    "library/tools/heard_speech.py",
    "tests/test_heard_speech.py",
}


def _payload():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


# ── 1. The containment ───────────────────────────────────────────────

def test_only_one_module_names_the_vendor_json_keys():
    """A key name is the seam. Two spellings of it is two files to change."""
    vendor_only = ("durationSec", "processingSec", "loadSec")
    for path in sorted((REPO_ROOT / "library").rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        relative = str(path.relative_to(REPO_ROOT))
        if relative in CONTAINMENT:
            continue
        body = path.read_text(encoding="utf-8")
        for key in vendor_only:
            assert key not in body, (
                f"{relative} names the transcriber's JSON key {key!r}. "
                f"Every fact about the transcriber lives in "
                f"library/tools/heard_speech.py so that replacing it is a "
                f"one-file change.")


def test_only_one_module_invokes_the_binary():
    """`da voz` appears in exactly one place, and it is behind a function."""
    invocations = []
    for path in sorted(REPO_ROOT.rglob("*.py")):
        if "__pycache__" in path.parts or ".venv" in path.parts:
            continue
        relative = str(path.relative_to(REPO_ROOT))
        if relative in CONTAINMENT:
            continue
        body = path.read_text(encoding="utf-8")
        if f'"{heard_speech.SUBCOMMAND}"' in body or \
                f"'{heard_speech.SUBCOMMAND}'" in body:
            invocations.append(relative)
    assert invocations == [], (
        f"{invocations} name the transcriber's subcommand. It is invoked "
        f"in one function, `heard_speech.transcribe`.")


def test_the_whole_vendor_surface_is_declared_in_one_block():
    """Every name the vendor owns is a module constant, not a literal."""
    for name in ("BINARY", "SUBCOMMAND", "FLAGS", "WORDS_KEY",
                 "WORD_TEXT_KEY", "WORD_START_KEY", "WORD_END_KEY",
                 "TEXT_KEY", "DURATION_KEY"):
        assert getattr(heard_speech, name), name


# ── 2. What it returns ───────────────────────────────────────────────

def test_a_recorded_payload_maps_onto_words_in_the_file_s_own_timebase():
    spoken = heard_speech.read_payload(_payload(), "reel26.mp4")
    assert len(spoken.words) == 142
    assert spoken.words[0].word == "What"
    assert spoken.words == sorted(spoken.words, key=lambda w: (w.start,
                                                               w.end))
    assert spoken.engine["transcriber"] == heard_speech.BINARY
    assert spoken.media_path == "reel26.mp4"


def test_a_word_with_no_usable_timing_is_dropped_rather_than_guessed():
    payload = {"words": [{"text": "kept", "start": 0.0, "end": 0.2},
                         {"text": "no start", "end": 0.5},
                         {"text": "  ", "start": 1.0, "end": 1.2}]}
    spoken = heard_speech.read_payload(payload)
    assert [w.word for w in spoken.words] == ["kept"]


def test_the_transcriber_s_own_structural_failures_are_named():
    """Reported BESIDE the findings, never as findings.

    It stretches a sentence-final word across a silence - 13.44 seconds,
    measured on this project's own audio. A divergence next to one of
    those is the transcriber's, not the edit's, and a reader who cannot
    tell them apart is being cried wolf at.
    """
    payload = {"words": [
        {"text": "something.", "start": 10.0, "end": 23.44},
        {"text": "backwards", "start": 30.0, "end": 30.0},
        {"text": "tick", "start": 40.0, "end": 40.01},
        {"text": "ordinary", "start": 50.0, "end": 50.4},
    ]}
    kinds = [row["kind"] for row in heard_speech.read_payload(payload).anomalies]
    assert kinds == ["word_spans_a_silence", "degenerate_interval",
                     "sub_frame_word"]


def test_the_recorded_reel_has_the_one_anomaly_it_was_measured_to_have():
    spoken = heard_speech.read_payload(_payload())
    assert len(spoken.anomalies) == 1


# ── 3. What it refuses ───────────────────────────────────────────────

def test_an_absent_file_refuses_rather_than_returning_silence(tmp_path):
    """An empty hearing and a silent render must not be the same object."""
    if not heard_speech.executable():
        pytest.skip(
            "the transcriber is not installed on this machine; the refusal "
            "it raises for an absent file needs it on PATH to reach. Runs "
            "wherever `da` is installed - see docs/ML_ENVIRONMENT.md.")
    with pytest.raises(heard_speech.TranscriberUnavailable) as refused:
        heard_speech.transcribe(str(tmp_path / "nothing.mp4"))
    assert "no file" in str(refused.value)


def test_availability_names_what_is_missing(monkeypatch):
    monkeypatch.setattr(heard_speech, "executable", lambda: None)
    installed, detail = heard_speech.available()
    assert installed is False
    assert heard_speech.BINARY in detail
    with pytest.raises(heard_speech.TranscriberUnavailable):
        heard_speech.transcribe("anything.mp4")
