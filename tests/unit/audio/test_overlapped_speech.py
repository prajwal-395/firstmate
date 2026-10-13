"""Overlapped speech (MU-20): two voices at once, found and carried.

The defect: diarization gives one label per window and the per-ISO path
hears each microphone alone, so nothing said WHEN two people talked over
each other - and every microphone in a room hears every voice, so the
naive "both tracks are loud" reading calls all bleed crosstalk. These
tests build ISO pairs whose crosstalk is known by construction, with
bleed in both directions and unequal gains, and check the detector, the
transcript record and the three readers (captions, the reel's speech
boundaries, the project model).
"""
from __future__ import annotations

import json
import wave
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from library.tools import overlapped_speech as ov
from library.tools import reel_project_model as rpm
from library.tools import single_track_diarization as std
from library.tools import timeline_transcript as tt
from library.tools.reel_proposal import snap_to_speech
from library.tools.reel_spine import spine_for_reel

SR = std.SAMPLE_RATE


def _write(path, samples):
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(SR)
        handle.writeframes(
            (np.clip(samples, -1, 1) * 32767).astype(np.int16).tobytes())
    return path


def _iso_pair(tmp_path, seed, overlap_rate=0.35, seconds=180):
    """Two ISO tracks of alternating turns, some ending in crosstalk.

    Each voice is a harmonic tone with a syllable-rate envelope; each
    microphone carries the other voice as bleed (-12 dB onto A, -8 dB
    onto B) and the two mics sit at different gains, so neither a
    symmetric threshold nor a fixed bleed constant can pass by luck.
    Returns the paths and the true crosstalk spans.
    """
    rng = np.random.default_rng(seed)
    n = seconds * SR
    t = np.arange(n) / SR

    def voice(f0):
        return sum(np.sin(2 * np.pi * f0 * k * t) / k for k in range(1, 6))

    gate_a, gate_b = np.zeros(n), np.zeros(n)
    truth = []
    cursor, speaker = 0.5, 0
    while cursor < seconds - 5:
        length = rng.uniform(1.0, 4.0)
        first, last = int(cursor * SR), int((cursor + length) * SR)
        (gate_a if speaker == 0 else gate_b)[first:last] = 1
        if rng.random() < overlap_rate:
            # The other voice comes in before this turn ends.
            lap = rng.uniform(0.6, 1.5)
            (gate_b if speaker == 0 else gate_a)[
                int((cursor + length - lap) * SR):last] = 1
            truth.append((cursor + length - lap, cursor + length))
        cursor += length + rng.uniform(0.1, 0.6)
        speaker ^= 1
    syllables = np.sqrt(0.5 + 0.5 * np.sin(2 * np.pi * 4.0 * t
                                           + rng.uniform(0, 6.3)))
    a, b = voice(110) * gate_a * syllables, voice(190) * gate_b * syllables
    noise = 0.001
    mic_a = 0.08 * (a + 10 ** (-12 / 20) * b) + noise * rng.standard_normal(n)
    mic_b = 0.03 * (b + 10 ** (-8 / 20) * a) + noise * rng.standard_normal(n)
    return (_write(tmp_path / "a.wav", mic_a),
            _write(tmp_path / "b.wav", mic_b), truth, seconds)


@pytest.mark.parametrize("seed", [1, 2, 3])
def test_crosstalk_on_two_iso_mics_is_found_and_bleed_is_not(tmp_path, seed):
    """Precision and recall against crosstalk known by construction."""
    path_a, path_b, truth, seconds = _iso_pair(tmp_path, seed)
    measured = ov.iso_overlap({"Akshita": path_a, "Craig": path_b})
    scored = ov.score(measured.spans, truth, seconds)
    assert scored["precision"] >= 0.9, scored
    assert scored["recall"] >= 0.9, scored
    assert all(set(s.voices) == {"Akshita", "Craig"} for s in measured.spans)
    # The bleed modes are the file's own, not constants.
    modes = measured.basis["bleed_modes_db"]["Akshita|Craig"]
    assert modes["bleed_of_Craig"] < 0 < modes["own_Akshita"]


def test_bleed_alone_is_never_crosstalk(tmp_path):
    """Turns that never overlap, every one heard on both mics: no span.

    "Both tracks voiced" is true for every frame of this file, which
    is exactly why it cannot be the rule."""
    path_a, path_b, truth, _ = _iso_pair(tmp_path, seed=7, overlap_rate=0.0)
    assert truth == []
    measured = ov.iso_overlap({"Akshita": path_a, "Craig": path_b})
    assert measured.seconds <= 0.5, measured.spans


def test_one_track_is_not_measured_rather_than_measured_empty(tmp_path):
    path_a, _, _, _ = _iso_pair(tmp_path, seed=1, seconds=20)
    with pytest.raises(ov.OverlapUnavailable, match="1 speaker track"):
        ov.iso_overlap({"Akshita": path_a})
    record = ov.not_measured("why")
    assert ov.spans_of({ov.RECORD_KEY: record}) is None
    assert ov.spans_of({}) is None, "an old transcript is unknown, not none"
    assert ov.spans_of({ov.RECORD_KEY: {"measured": True, "spans": []}}) == []


def _memory(tmp_path, path_a, path_b, lag):
    """Two cameras' program audio in a source-memory root, with the M6
    clock that says camera B started `lag` seconds after camera A."""
    root = tmp_path / "memory"
    digests = {"a.mxf": "a" * 64, "b.mxf": "b" * 64}
    samples_b = ov._read_track(path_b)[round(lag * SR):]
    for name, digest, offset, samples in (
            ("a.mxf", digests["a.mxf"], 0.0, ov._read_track(path_a)),
            ("b.mxf", digests["b.mxf"], lag, samples_b)):
        folder = root / digest
        folder.mkdir(parents=True)
        _write(folder / "program.16k.wav", samples)
        (folder / "clock.json").write_text(json.dumps({
            "content_digest": digest, "source_file": name,
            "group_members": sorted(digests.values()),
            "offset_to_reference_seconds": offset}))
    return root, (lambda path: digests[Path(path).name])


def _cut(seconds, lag, every=3.0):
    """A cut that alternates cameras every few seconds, whoever talks."""
    slots, start, speaker = [], 0.0, 0
    while start < seconds - 1:
        end = min(seconds - 1, start + every)
        slots.append(SimpleNamespace(
            speaker=("Akshita", "Craig")[speaker],
            source_file=("a.mxf", "b.mxf")[speaker],
            source_in=start - (0.0, lag)[speaker],
            timeline_start=start, timeline_end=end))
        start, speaker = end, speaker ^ 1
    return slots


def test_each_camera_is_laid_under_the_whole_cut_through_the_clock(tmp_path):
    """On a cut, each speaker's own clips hold their camera only while
    their picture plays - two such tracks are never voiced at once. The
    crosstalk is found by laying each camera under every slot through
    the M6 clock; with no clock it is not measured, never "none"."""
    path_a, path_b, truth, seconds = _iso_pair(tmp_path, seed=4)
    root, digest_of = _memory(tmp_path, path_a, path_b, lag=2.0)
    slots = _cut(seconds, lag=2.0)
    record = ov.timeline_record(slots, seconds - 1, root, digest_of)
    assert record["measured"], record
    reached = [(s["start"], s["end"]) for s in record["spans"]]
    scored = ov.score(reached, truth, seconds - 1)
    assert scored["precision"] >= 0.9 and scored["recall"] >= 0.9, scored

    for name in ("a" * 64, "b" * 64):
        (root / name / "clock.json").unlink()
    unclocked = ov.timeline_record(slots, seconds - 1, root, digest_of)
    assert unclocked["measured"] is False
    assert "never voiced while both are recording" in unclocked["reason"]


def test_the_clock_reads_the_same_instant_conversation_clock_does(tmp_path):
    """`_same_instant` restates `conversation_clock.map_time` to keep the
    clock's Resolve imports out of the preflight code identity; the two
    must never disagree about where a moment sits on another camera."""
    from library.tools import conversation_clock

    path_a, path_b, _, _ = _iso_pair(tmp_path, seed=1, seconds=20)
    root, _ = _memory(tmp_path, path_a, path_b, lag=2.0)
    for digest in ("a" * 64, "b" * 64):
        assert ov._same_instant(digest, 7.25, root) == (
            conversation_clock.map_time(digest, 7.25, root))
    assert ov._same_instant("c" * 64, 7.25, root) == {}


# ── the transcript carries it ───────────────────────────────────────

class _Snap:
    project_name, timeline_name, fps, duration = "p", "t", 24.0, 30.0
    clips = ()


def _measured(*spans):
    return {"measured": True, "arm": ov.ARM_ISO,
            "spans": [{"start": s, "end": e, "voices": list(v)}
                      for s, e, v in spans]}


def test_the_record_rides_the_transcript_and_survives_a_rebind():
    record = _measured((11.0, 12.0, ("host", "guest")))
    doc = tt.transcript_document(_Snap(), [], overlapped_speech=record)
    assert doc[ov.RECORD_KEY] == record
    assert ov.RECORD_KEY not in tt.transcript_document(_Snap(), [])

    class _NoClips(_Snap):
        def picture_clips(self):
            return []

    rebound = tt.rebind_document(doc, _NoClips())
    assert rebound[ov.RECORD_KEY] == record, (
        "a rebind re-hears nothing; the audio measurement travels")


# ── readers ─────────────────────────────────────────────────────────

@dataclass
class _Moment:
    timeline_start: float
    timeline_end: float
    cta_start: float = None
    cta_end: float = None
    number: int = 1


def _row(speaker, text, start, source_file, source_start, per_word=0.4):
    words = [{"word": w, "start": start + i * per_word,
              "end": start + (i + 1) * per_word}
             for i, w in enumerate(text.split())]
    end = start + len(words) * per_word
    return {"speaker": speaker, "text": text, "timeline_start": start,
            "timeline_end": end, "source_file": source_file,
            "resolve_item_id": source_file, "source_start": source_start,
            "source_end": source_start + (end - start), "words": words}


def test_captions_keep_both_voices_of_measured_unison_and_drop_bleed():
    """Same words on both mics: one mic's bleed - unless both voices
    were MEASURED sounding there, in which case two people said them."""
    rows = [_row("host", "yeah exactly right", 10.0, "cam_a.mov", 100.0),
            _row("guest", "yeah exactly right", 10.05, "cam_b.mov", 200.0)]
    unmeasured = spine_for_reel(_Moment(10.0, 12.0), {"segments": rows})
    assert len(unmeasured["structure"]) == 1
    assert unmeasured["unison_blocks_kept"] is None
    assert "overlapped_speech" not in unmeasured["structure"][0]

    measured = spine_for_reel(_Moment(10.0, 12.0), {
        "segments": rows,
        ov.RECORD_KEY: _measured((10.0, 11.3, ("host", "guest")))})
    assert {b["speaker"] for b in measured["structure"]} == {"host", "guest"}
    assert measured["bleed_blocks_dropped"] == 0
    assert measured["unison_blocks_kept"] == 1
    for block in measured["structure"]:
        assert len(block["word_timestamps"]) == 3, (
            "the cross-speaker edge cut must not take the unison words")
        assert block["overlapped_speech"][0][2] == ["host", "guest"]

    # Crosstalk measured ELSEWHERE does not rescue a bleed pair.
    elsewhere = spine_for_reel(_Moment(10.0, 12.0), {
        "segments": rows,
        ov.RECORD_KEY: _measured((30.0, 31.0, ("host", "guest")))})
    assert len(elsewhere["structure"]) == 1
    assert elsewhere["unison_blocks_kept"] == 0


def test_a_reel_boundary_never_opens_inside_measured_crosstalk():
    """A start in a gap no word covers, while the other voice is talking
    over this one, moves out of the crosstalk; unmeasured, it stays."""
    guest = _row("guest", "no wait", 11.0, "cam_b.mov", 201.0)
    guest["words"] = [{"word": "no", "start": 11.0, "end": 11.5},
                      {"word": "wait", "start": 11.9, "end": 12.3}]
    guest["resolve_item_id"] = None  # a straddling row binds nothing
    rows = [_row("host", "we tried that and", 10.0, "cam_a.mov", 100.0),
            _row("host", "it worked", 12.0, "cam_a.mov", 102.0), guest]
    plain = {"segments": rows}
    assert snap_to_speech(11.7, 12.8, plain)[0] == pytest.approx(11.7), (
        "11.7 s is in silence on both rows: the word pass cannot see it")
    measured = dict(plain, **{ov.RECORD_KEY: _measured(
        (11.0, 12.3, ("host", "guest")))})
    start, _ = snap_to_speech(11.7, 12.8, measured)
    assert start <= 11.0


def test_the_project_model_gets_an_overlap_observation_per_voice():
    transcript = {
        "segments": [_row("host", "so the thing is that", 10.0,
                          "cam_a.mov", 100.0)],
        ov.RECORD_KEY: _measured((10.8, 11.6, ("host", "guest")))}
    model = rpm.blank_model("m")
    model["clocks"].append({
        "id": "clock:a", "domain": {"kind": "source",
                                      "asset_id": "asset:a"},
        "ticks_per_second": rpm.TICKS_PER_SECOND, "frame_rate": None})
    minted = rpm.overlap_observations(model, transcript, "host",
                                      "cam_a.mov", "asset:a", "clock:a",
                                      "person:host")
    assert len(minted) == 1
    obs = next(o for o in model["observations"] if o["id"] == minted[0])
    assert obs["modality"] == "overlap"
    assert obs["interval"] == {"clock_id": "clock:a",
                               "start": 12_096_000, "end": 12_192_000}
    assert obs["payload"]["voices"] == ["host", "guest"]
    assert any(link["observation_id"] == minted[0]
               for link in model["identity_links"])
    unmeasured = rpm.blank_model("m")
    assert rpm.overlap_observations(
        unmeasured, {"segments": transcript["segments"]}, "host",
        "cam_a.mov", "asset:a", "clock:a", "person:host") == []
