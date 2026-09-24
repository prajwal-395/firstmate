"""Dialogue cleanup is plan-requested, measured, and refused by name.

Fidelity rung R5d: DeepFilterNet plus Resolve Voice Isolation, measured
locally first (see `library/tools/dialogue_cleanup.py` for the table).
These tests drive the real module on synthetic WAV fixtures (stdlib
`wave`, decoded the same way - no ffmpeg, no model, no Resolve):

- the vocabulary holds exactly the two measured tools;
- a plan entry with an unknown tool, a missing/out-of-range amount, a
  half or backwards span, or no `why`/`source` refuses by name;
- Voice Isolation claims nothing without the re-read (False, mismatch
  and missing track all refuse; the fake timeline judges the
  discipline);
- the OTIO rewrite points the clip at the stem, zeroes the source
  start, keeps the duration, and refuses a missing stem;
- the availability probes state their reason instead of raising.
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
    CLEANUP_ENTRY_KEYS,
    TOOLS,
    DialogueCleanupRefused,
    apply_voice_isolation,
    deepfilternet_probe,
    measure_source,
    rewrite_clip_media_to_stem,
    validate_cleanup_request,
    voice_isolation_note,
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


# ── vocabulary ───────────────────────────────────────────────────────

def test_tools_are_exactly_the_two_measured():
    assert tuple(TOOLS) == ("voice_isolation", "deepfilternet")


def test_probes_state_their_reason():
    probe = deepfilternet_probe()
    assert set(probe) == {"available", "method", "reason"}
    assert probe["reason"].strip()
    if probe["available"]:
        assert probe["method"] in ("python", "binary")
    note = voice_isolation_note()
    assert "Studio" in note["reason"]


# ── plan validation ──────────────────────────────────────────────────

def test_valid_entries_normalise():
    row = validate_cleanup_request(_good())
    assert row["tool"] == "deepfilternet" and row["amount"] is None
    assert row["span_start"] is None and row["span_end"] is None
    row = validate_cleanup_request(
        _good("voice_isolation", span_start=1.0, span_end=2.0))
    assert (row["amount"], row["span_start"], row["span_end"]) == (60, 1.0, 2.0)


@pytest.mark.parametrize("entry", [
    _good(tool="noisereduce"),
    _good(tool="eq"),
    _good(tool=None),
])
def test_unknown_tool_refuses_by_name(entry):
    with pytest.raises(DialogueCleanupRefused, match="not a cleanup tool"):
        validate_cleanup_request(entry)


def test_voice_isolation_needs_an_amount():
    with pytest.raises(DialogueCleanupRefused, match="names no amount"):
        validate_cleanup_request(_good("voice_isolation", amount=None))


@pytest.mark.parametrize("amount", [-1, 101, "loud", 55.5 + 45.6])
def test_voice_isolation_amount_is_resolves_own_scale(amount):
    if amount == 101.1:
        with pytest.raises(DialogueCleanupRefused):
            validate_cleanup_request(_good("voice_isolation", amount=amount))
    elif isinstance(amount, str):
        with pytest.raises(DialogueCleanupRefused, match="not a number"):
            validate_cleanup_request(_good("voice_isolation", amount=amount))
    else:
        with pytest.raises(DialogueCleanupRefused, match="0\\.\\.100"):
            validate_cleanup_request(_good("voice_isolation", amount=amount))


def test_deepfilternet_carries_no_amount():
    with pytest.raises(DialogueCleanupRefused, match="carries an amount"):
        validate_cleanup_request(_good(amount=60))


def test_half_span_and_backwards_span_refuse():
    with pytest.raises(DialogueCleanupRefused, match="half a span"):
        validate_cleanup_request(_good("voice_isolation", span_start=1.0))
    with pytest.raises(DialogueCleanupRefused, match="runs backwards"):
        validate_cleanup_request(
            _good("voice_isolation", span_start=2.0, span_end=1.0))


def test_no_why_or_no_source_refuses():
    with pytest.raises(DialogueCleanupRefused, match="no why"):
        validate_cleanup_request(_good(why="  "))
    with pytest.raises(DialogueCleanupRefused, match="no source"):
        validate_cleanup_request(_good(source=""))
    with pytest.raises(DialogueCleanupRefused, match="not an object"):
        validate_cleanup_request("voice_isolation")


def test_entry_keys_are_exactly_what_the_step_reads():
    assert set(CLEANUP_ENTRY_KEYS) == {
        "source", "tool", "amount", "span_start", "span_end", "why"}


# ── source measurement ───────────────────────────────────────────────

def test_measure_source_records_floor_and_speech(tmp_path):
    path = str(tmp_path / "speech.wav")
    _fixture(path)
    record = measure_source(path, [(0.0, 4.0)], [(0.5, 1.5)])
    assert record["floor"]["level_dbfs"] < -30
    assert record["floor_unmeasured_reason"] == ""
    assert record["speech"]["measured"] is True


def test_measure_source_states_an_unmeasurable_floor(tmp_path):
    path = str(tmp_path / "dense.wav")
    _fixture(path)
    record = measure_source(path, [(0.0, 4.0)], [(0.0, 4.0)])
    assert record["floor"] == {}
    assert "no speech-free stretch" in record["floor_unmeasured_reason"]
    assert record["speech"]["measured"] is True


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


def test_voice_isolation_false_or_mismatch_refuses():
    with pytest.raises(DialogueCleanupRefused, match="answered False"):
        apply_voice_isolation(_Timeline(write_answer=False), 1, 60)
    with pytest.raises(DialogueCleanupRefused, match="re-reads"):
        apply_voice_isolation(
            _Timeline(read_back={"isEnabled": True, "amount": 30}), 1, 60)
    with pytest.raises(DialogueCleanupRefused, match="re-reads"):
        apply_voice_isolation(
            _Timeline(read_back={"isEnabled": False}), 1, 60)


def test_voice_isolation_missing_track_refuses():
    with pytest.raises(DialogueCleanupRefused, match="names nothing"):
        apply_voice_isolation(_Timeline(tracks=1), 2, 60)
    with pytest.raises(DialogueCleanupRefused, match="0\\.\\.100"):
        apply_voice_isolation(_Timeline(), 1, 101)


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


def test_stem_rewrite_points_at_the_stem_and_zeroes_the_start(tmp_path):
    clip, stem = _clip(tmp_path)
    rewrite_clip_media_to_stem(clip, stem)
    assert clip["media_references"]["m1"]["target_url"] == "file://" + stem
    assert clip["source_range"]["start_time"]["value"] == 0
    assert clip["source_range"]["duration"]["value"] == 768000


def test_stem_rewrite_refuses_a_missing_stem(tmp_path):
    clip, _ = _clip(tmp_path)
    with pytest.raises(DialogueCleanupRefused, match="not on disk"):
        rewrite_clip_media_to_stem(clip, str(tmp_path / "gone.wav"))


def test_stem_rewrite_refuses_a_clip_with_no_reference(tmp_path):
    stem = tmp_path / "clean.wav"
    stem.write_bytes(b"RIFF")
    with pytest.raises(DialogueCleanupRefused, match="no active media"):
        rewrite_clip_media_to_stem({"OTIO_SCHEMA": "Clip.1"}, str(stem))
