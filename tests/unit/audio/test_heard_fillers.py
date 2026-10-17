"""Unit tests for the filler detector behind `heard_speech.detect_fillers`.

Nothing here runs the detector: payloads replay through the mapper, and
the subprocess is faked. The vendor subcommand is referenced only through
`heard_speech.UHM_SUBCOMMAND` - spelling it as a literal anywhere but
`library/tools/heard_speech.py` and the containment contract fails
`tests/contracts/test_heard_speech.py`.
"""
import json
import subprocess

import pytest

from library.tools import heard_speech


def _payload():
    return {
        "durationSec": 60,
        "fillers": [
            {"confidence": 0.80, "durationSec": 0.70,
             "end": 19.08, "start": 18.38, "type": "um"},
            {"confidence": 0.84, "durationSec": 1.28,
             "end": 47.68, "start": 46.40, "type": "um"},
        ],
        "input": "clip.wav",
    }


def test_filler_payload_keeps_timed_events_verbatim():
    heard = heard_speech.read_filler_payload(_payload(), "clip.wav")
    assert [(e.filler_type, e.start, e.end) for e in heard.events] == [
        ("um", 18.38, 19.08), ("um", 46.40, 47.68)]
    assert heard.events[0].confidence == pytest.approx(0.80)
    assert heard.duration_seconds == pytest.approx(60)
    assert heard.engine["subcommand"] == heard_speech.UHM_SUBCOMMAND
    assert heard.as_dict()["event_count"] == 2


def test_filler_payload_drops_untimed_degenerate_and_blank_rows():
    payload = {"fillers": [
        {"type": "uh", "start": 3.0, "end": 3.4},
        {"type": "um", "start": 5.0},  # no end: dropped, not guessed
        {"type": "  ", "start": 6.0, "end": 6.5},  # blank type: dropped
        {"type": "hmm", "start": 7.0, "end": 7.0},  # degenerate: dropped
        {"type": "uh", "start": 1.0, "end": 1.2},  # sorts first
        "not-a-row",
    ]}
    heard = heard_speech.read_filler_payload(payload)
    assert [(e.filler_type, e.start) for e in heard.events] == [
        ("uh", 1.0), ("uh", 3.0)]
    assert heard.events[1].confidence is None


def test_filler_payload_without_events_is_a_measured_absence():
    heard = heard_speech.read_filler_payload({"durationSec": 46})
    assert heard.events == []
    assert heard.as_dict()["event_count"] == 0


def test_filler_payload_that_is_not_an_object_refuses():
    with pytest.raises(heard_speech.TranscriberUnavailable):
        heard_speech.read_filler_payload(["fillers"])


def test_detect_fillers_reaches_the_detector_subcommand(
        monkeypatch, tmp_path):
    media = tmp_path / "clip.wav"
    media.write_bytes(b"RIFF" + b"\0" * 44)
    monkeypatch.setattr(heard_speech, "executable", lambda: "/bin/da")
    observed = {}

    def _run(label, command, **kwargs):
        observed["command"] = command
        return subprocess.CompletedProcess(
            command, 0, stdout=json.dumps(_payload()), stderr="")

    monkeypatch.setattr(heard_speech.perf_ledger, "run", _run)
    heard = heard_speech.detect_fillers(str(media))
    assert observed["command"][:3] == [
        "/bin/da", heard_speech.UHM_SUBCOMMAND, str(media)]
    assert len(heard.events) == 2


def test_detect_fillers_without_a_binary_refuses(monkeypatch, tmp_path):
    monkeypatch.setattr(heard_speech, "executable", lambda: None)
    with pytest.raises(heard_speech.TranscriberUnavailable):
        heard_speech.detect_fillers(str(tmp_path / "clip.wav"))
