"""Dialogue cleanup is plan-requested, measured, and refused by name.

Fidelity rung R5d: DeepFilterNet plus Resolve Voice Isolation, measured
locally first (see `library/tools/dialogue_cleanup.py` for the table).
These tests drive the real module on synthetic WAV fixtures (stdlib
`wave`, decoded the same way - no ffmpeg, no model, no Resolve):

- a plan entry with an unknown tool, a missing/out-of-range amount, a
  half or backwards span, or no `why`/`source` refuses by name;
- Voice Isolation claims nothing without the re-read (False, mismatch
  and missing track all refuse; the fake timeline judges the
  discipline);
- the OTIO rewrite refuses a missing stem or reference (the swap itself
  is `tests/test_audio_mix_cleanup.py`'s);
- the availability probe resolves package, shared location, then PATH,
  and states its reason instead of raising.
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

from library.tools.dialogue_cleanup import (
    DialogueCleanupRefused,
    apply_voice_isolation,
    deepfilternet_probe,
    measure_source,
    rewrite_clip_media_to_stem,
    validate_cleanup_request,
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
    """4 s: loud word 0.5-1.5 s, quiet room elsewhere."""
    samples = []
    for i in range(4 * SR):
        t = i / SR
        if 0.5 <= t < 1.5:
            samples.append(0.6 * math.sin(2 * math.pi * 440.0 * t))
        else:
            samples.append(0.004 * math.sin(2 * math.pi * 120.0 * t))
    _write_wav(path, samples)


def _good(tool="deepfilternet", **over):
    entry = {"source": "IMG_1816.MOV", "tool": tool,
             "why": "floor -29.7 dBFS, the noisiest played source"}
    if tool == "voice_isolation":
        entry["amount"] = 60
    entry.update(over)
    return entry


# ── plan validation ──────────────────────────────────────────────────

def test_valid_entries_normalise():
    row = validate_cleanup_request(_good())
    assert row["tool"] == "deepfilternet" and row["amount"] is None
    assert row["span_start"] is None and row["span_end"] is None
    row = validate_cleanup_request(
        _good("voice_isolation", span_start=1.0, span_end=2.0))
    assert (row["amount"], row["span_start"], row["span_end"]) == (60, 1.0, 2.0)


def test_an_invalid_entry_refuses_by_name():
    """Each row is one malformed plan entry; each refuses naming why."""
    rows = [
        (_good(tool="noisereduce"), "not a cleanup tool"),
        (_good(tool="eq"), "not a cleanup tool"),
        (_good(tool=None), "not a cleanup tool"),
        (_good("voice_isolation", amount=None), "names no amount"),
        (_good("voice_isolation", amount=-1), "0\\.\\.100"),
        (_good("voice_isolation", amount=101), "0\\.\\.100"),
        (_good("voice_isolation", amount=101.1), "0\\.\\.100"),
        (_good("voice_isolation", amount="loud"), "not a number"),
        (_good(amount=60), "carries an amount"),
        (_good("voice_isolation", span_start=1.0), "half a span"),
        (_good("voice_isolation", span_start=2.0, span_end=1.0),
         "runs backwards"),
        (_good(why="  "), "no why"),
        (_good(source=""), "no source"),
        ("voice_isolation", "not an object"),
    ]
    for entry, said in rows:
        with pytest.raises(DialogueCleanupRefused, match=said):
            validate_cleanup_request(entry)


# ── source measurement ───────────────────────────────────────────────

def test_measure_source_states_a_missing_file(tmp_path):
    record = measure_source(str(tmp_path / "gone.wav"), [(0.0, 1.0)], [])
    assert record["floor"] == {}
    assert record["floor_unmeasured_reason"].strip()
    assert record["speech"]["measured"] is False


# ── voice isolation discipline ───────────────────────────────────────

class _Timeline:
    def __init__(self, tracks=2, read_back=None, write_answer=True):
        self._tracks = tracks
        self._read_back = read_back
        self._write_answer = write_answer
        self.writes = []

    def GetTrackCount(self, kind):
        assert kind == "audio"
        return self._tracks

    def SetVoiceIsolationState(self, track, state):
        self.writes.append((track, dict(state)))
        return self._write_answer

    def GetVoiceIsolationState(self, track):
        if self._read_back is not None:
            return dict(self._read_back)
        track, state = self.writes[-1]
        return {"isEnabled": state["isEnabled"],
                "amount": state.get("amount")}


def test_voice_isolation_claims_only_what_rereads():
    timeline = _Timeline()
    report = apply_voice_isolation(timeline, 1, 60)
    assert report == {"track": 1, "amount": 60,
                      "verified": "re-reads equal"}
    assert timeline.writes == [(1, {"isEnabled": True, "amount": 60})]


def test_voice_isolation_refuses_what_it_cannot_reread():
    """A False answer, a re-read that disagrees, a missing track, or an
    amount off Resolve's scale - none is claimed as applied."""
    rows = [
        (_Timeline(write_answer=False), 1, 60, "answered False"),
        (_Timeline(read_back={"isEnabled": True, "amount": 30}), 1, 60,
         "re-reads"),
        (_Timeline(read_back={"isEnabled": False}), 1, 60, "re-reads"),
        (_Timeline(tracks=1), 2, 60, "names nothing"),
        (_Timeline(), 1, 101, "0\\.\\.100"),
    ]
    for timeline, track, amount, said in rows:
        with pytest.raises(DialogueCleanupRefused, match=said):
            apply_voice_isolation(timeline, track, amount)


# ── OTIO stem rewrite ────────────────────────────────────────────────

def _clip(tmp_path):
    stem = tmp_path / "clean.wav"
    stem.write_bytes(b"RIFF")
    return {
        "OTIO_SCHEMA": "Clip.1",
        "source_range": {"start_time": {"value": 480,
                                        "rate": 48000.0},
                         "duration": {"value": 768000,
                                      "rate": 48000.0}},
        "active_media_reference_key": "m1",
        "media_references": {
            "m1": {"target_url": "file:///src/IMG_1816.MOV"},
        },
    }, str(stem)


def test_stem_rewrite_refuses_a_missing_stem_or_reference(tmp_path):
    clip, stem = _clip(tmp_path)
    with pytest.raises(DialogueCleanupRefused, match="not on disk"):
        rewrite_clip_media_to_stem(clip, str(tmp_path / "gone.wav"))
    with pytest.raises(DialogueCleanupRefused, match="no active media"):
        rewrite_clip_media_to_stem({"OTIO_SCHEMA": "Clip.1"}, stem)


# ── binary resolution: one location, then PATH ────────────────────────
#
# The shared ML venv cannot carry deepfilternet (numpy<2 pin, no cp312
# wheel), so the binary method reads the shared-environment location
# first and PATH second. The fake binaries below are shell scripts -
# they prove the RESOLUTION, not the suppression; the real binary is
# measured on real dialogue outside the suite.

def _no_df_package(monkeypatch):
    import importlib.util
    monkeypatch.setattr(importlib.util, "find_spec",
                        lambda name: None)


def _fake_binary(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/bin/sh\ncp \"$1\" \"$3/$(basename \"$1\")\"\n",
                    encoding="utf-8")
    path.chmod(0o755)
    return str(path)


def test_probe_resolves_shared_location_then_path_then_says_how(
        tmp_path, monkeypatch):
    from library.tools.dialogue_cleanup import _deepfilter_binary_path
    _no_df_package(monkeypatch)
    monkeypatch.delenv("PIPELINE_DEEPFILTER_BINARY", raising=False)
    monkeypatch.setenv("PIPELINE_VEP_HOME", str(tmp_path / "vep"))

    # PATH alone answers when the shared location holds nothing.
    monkeypatch.setattr("shutil.which",
                        lambda name: "/usr/local/bin/deep-filter"
                        if name == "deep-filter" else None)
    probe = deepfilternet_probe()
    assert probe["available"] and probe["method"] == "binary"
    assert "/usr/local/bin/deep-filter" in probe["reason"]

    # Nothing anywhere: unavailable, naming the install route.
    monkeypatch.setattr("shutil.which", lambda name: None)
    probe = deepfilternet_probe()
    assert probe["available"] is False and probe["method"] == ""
    assert "scripts/install_deepfilternet.sh" in probe["reason"]
    assert "PIPELINE_DEEPFILTER_BINARY" in probe["reason"]

    # The shared-environment location wins over PATH.
    expected = _fake_binary(tmp_path / "vep" / "bin" / "deep-filter")
    monkeypatch.setattr("shutil.which", lambda name: "/elsewhere/deep-filter")
    assert _deepfilter_binary_path() == expected
    assert deepfilternet_probe() == {
        "available": True, "method": "binary",
        "reason": f"the deep-filter binary answers at {expected}"}


def test_enhance_binary_moves_the_produced_stem_into_place(
        tmp_path, monkeypatch):
    from library.tools.dialogue_cleanup import _enhance_binary
    _no_df_package(monkeypatch)
    monkeypatch.delenv("PIPELINE_DEEPFILTER_BINARY", raising=False)
    monkeypatch.setenv("PIPELINE_VEP_HOME", str(tmp_path / "vep"))
    _fake_binary(tmp_path / "vep" / "bin" / "deep-filter")
    monkeypatch.setattr("shutil.which", lambda name: None)
    source = str(tmp_path / "take_range.wav")
    _fixture(source)
    out = str(tmp_path / "stems" / "take.wav")
    import os
    os.makedirs(os.path.dirname(out), exist_ok=True)
    _enhance_binary(source, out)
    assert os.path.isfile(out)
    assert open(out, "rb").read() == open(source, "rb").read()
    assert os.listdir(os.path.dirname(out)) == ["take.wav"]
