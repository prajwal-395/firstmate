"""Dialogue cleanup travels from plan to timeline, or refuses by name.

Fidelity rung R5d: step 5.02 measures per-source noise floors into
`cleanup_context`, the model requests cleanup in `cleanup_plan`, the
post-bridge validates each entry strictly, `assemble` carries the plan
on `audio_mix_spec.dialogue_cleanup`, and the OTIO route swaps staged
stems onto the clips that play them. These tests drive the real
functions on synthetic fixtures (stdlib `wave` WAVs under `tmp_path`,
synthetic OTIO dicts - no model, no Resolve, no DeepFilterNet):

- `cleanup_context` joins spine word timings to played ranges through
  the segment clip_id and records a floor per source, stating what it
  cannot measure;
- `resolve_cleanup` accepts a valid plan, warns-and-empties a
  non-list answer (cleanup is opt-in), and refuses unknown keys/tools,
  missing amounts and missing whys through the retry path;
- `otio_mix.stem_swaps`/`apply_stem_swaps`/`verify_stems` swap the
  stem onto the matched clip, report the miss, and read the swap back;
- `stage_deepfilternet` intersects spans with played ranges, refuses
  an empty intersection and an unplayed source, and stages one stem
  per surviving range (DeepFilterNet itself mocked - the model run is
  measured in the module docstring, not here).
"""
import math
import os
import struct
import sys
import wave
from unittest.mock import patch
import pytest
import shutil
from library.steps.step_1_02_catalog_footage.step import (
    MEASURE_MARGIN_DB,
    ProgramStreamRefused,
    catalog_footage,
    describe_audio_streams,
    measure_program_selection,
    measure_stream_levels,
    select_program_stream,
)
from library.steps.step_6_01_render.resolve_build_timeline import (
    SpeechChannelRefused,
    mapping_carries_program,
    resolve_speech_channel,
)
import subprocess
import numpy as np
from library.tools.render_qa import measure_silence_under_picture
from pathlib import Path


PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from library.steps.step_5_02_audio_mix.mix import assemble, cleanup_context
from library.steps.step_5_02_audio_mix.post_bridge import resolve_cleanup
from library.tools import otio_mix
from library.tools.dialogue_cleanup import (
    DialogueCleanupRefused,
    stage_deepfilternet,
)
from library.tools.plan_keys import UnreadPlanKey

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


def _speech_wav(path, word=(0.5, 1.5), total=4.0):
    samples = []
    for i in range(int(total * SR)):
        t = i / SR
        if word[0] <= t < word[1]:
            samples.append(0.6 * math.sin(2 * math.pi * 440.0 * t))
        else:
            samples.append(0.004 * math.sin(2 * math.pi * 120.0 * t))
    _write_wav(path, samples)


def _spine(clip_id="clip_1"):
    return {"structure": [{
        "position": "hook", "block_type": "hook",
        "clip_id": clip_id,
        "content": {
            "clip_id": clip_id,
            "word_timestamps": [
                {"word": "hello", "source_start": 0.5,
                 "source_end": 1.5},
            ],
        },
    }]}


def _assignments(source, clip_id="clip_1"):
    return [{
        "spine_block_position": "hook",
        "video_segments": [{
            "clip_id": clip_id,
            "source_file": source,
            "video_in": 0.0, "video_out": 4.0,
        }],
    }]


# ── the plan context ─────────────────────────────────────────────────

def test_cleanup_context_records_a_floor_per_source(tmp_path):
    source = str(tmp_path / "speech.wav")
    _speech_wav(source)
    context = cleanup_context(_spine(), _assignments(source))
    assert len(context["sources"]) == 1
    row = context["sources"][0]
    assert row["source_file"] == source
    assert row["floor"]["level_dbfs"] < -30
    assert row["floor_unmeasured_reason"] == ""
    assert row["speech"]["measured"] is True
    assert set(context["tools"]) == {
        "deepfilternet", "voice_isolation", "audio_ops"}


def test_cleanup_context_states_an_unmeasurable_floor(tmp_path):
    source = str(tmp_path / "dense.wav")
    _speech_wav(source)
    spine = {"structure": [{
        "position": "hook", "block_type": "hook", "clip_id": "clip_1",
        "content": {"clip_id": "clip_1", "word_timestamps": [
            {"word": "all", "source_start": 0.0, "source_end": 4.0}]},
    }]}
    context = cleanup_context(spine, _assignments(source))
    row = context["sources"][0]
    assert row["floor"] == {}
    assert "no speech-free stretch" in row["floor_unmeasured_reason"]


def test_cleanup_context_skips_blocks_with_no_words(tmp_path):
    source = str(tmp_path / "speech.wav")
    _speech_wav(source)
    spine = {"structure": [{
        "position": "card", "block_type": "intro_card",
        "content": {"clip_id": "clip_9"},
    }, {
        # A block whose content is not an object must not break the
        # join - it simply carries no word timings.
        "position": "other", "block_type": "speech",
        "clip_id": "clip_1", "content": "unparsed",
        "word_timestamps": "unparsed",
    }]}
    context = cleanup_context(spine, _assignments(source))
    # No word timings means no exclusions: the whole played range is
    # the gap, so the floor reads the whole file rather than refusing.
    row = context["sources"][0]
    assert row["floor_unmeasured_reason"] == ""
    assert row["floor"]["level_dbfs"] is not None


# ── the post-bridge ──────────────────────────────────────────────────

def test_resolve_cleanup_accepts_a_valid_plan_and_an_absent_one():
    out = resolve_cleanup({
        "cleanup_context": {"tools": {"deepfilternet": {}}},
        "cleanup_plan": [
            {"source": "IMG_1816.MOV", "tool": "voice_isolation",
             "amount": 60, "why": "floor -29.7 dBFS"},
            {"source": "IMG_1822.MOV", "tool": "deepfilternet",
             "why": "floor -54.1 dBFS, light room"},
        ],
    })
    assert [r["tool"] for r in out["requests"]] == [
        "voice_isolation", "deepfilternet"]
    assert out["requests"][0]["amount"] == 60
    # Cleanup is opt-in: no plan, an empty one, or a non-list answer
    # means no cleanup, never an error.
    assert resolve_cleanup({})["requests"] == []
    assert resolve_cleanup({"cleanup_plan": []})["requests"] == []
    out = resolve_cleanup({"cleanup_plan": "loud please"})
    assert out["requests"] == []


def test_resolve_cleanup_refuses_what_nothing_reads():
    with pytest.raises(UnreadPlanKey):
        resolve_cleanup({"cleanup_plan": [
            {"source": "a.wav", "tool": "deepfilternet",
             "strength": "lots", "why": "noisy"}]})
    with pytest.raises(DialogueCleanupRefused):
        resolve_cleanup({"cleanup_plan": [
            {"source": "a.wav", "tool": "noisereduce",
             "why": "noisy"}]})
    with pytest.raises(DialogueCleanupRefused):
        resolve_cleanup({"cleanup_plan": [
            {"source": "a.wav", "tool": "voice_isolation",
             "why": "no amount named"}]})
    with pytest.raises(DialogueCleanupRefused):
        resolve_cleanup({"cleanup_plan": [
            {"source": "a.wav", "tool": "deepfilternet"}]})


def test_assemble_carries_the_cleanup_plan():
    spec = assemble({"bed_measurements": {}}, [], [], [],
                    cleanup={"requests": [{"tool": "deepfilternet"}],
                             "tools": {}})["audio_mix_spec"]
    assert spec["dialogue_cleanup"]["requests"] == [{"tool": "deepfilternet"}]
    bare = assemble({"bed_measurements": {}}, [], [], [])["audio_mix_spec"]
    assert bare["dialogue_cleanup"] == {"requests": [], "tools": {},
                                        "sources": []}


# ── the OTIO swap ────────────────────────────────────────────────────

def _otio(clip_path, frames=100):
    return {"tracks": {"children": [{
        "kind": "Audio", "name": "A1",
        "children": [{
            "OTIO_SCHEMA": "Clip.1",
            "source_range": {
                "start_time": {"value": 480, "rate": 24.0},
                "duration": {"value": frames, "rate": 24.0}},
            "active_media_reference_key": "m1",
            "media_references": {"m1": {"target_url": "file://" + clip_path}},
            "effects": [{"metadata": {"Resolve_OTIO": {
                "Effect Name": "Fairlight Clip Volume and Fades"},
                "Parameters": []}}],
        }],
    }]}}


def test_stem_swap_lands_on_the_matched_clip(tmp_path):
    clip_path = str(tmp_path / "src.MOV")
    stem_path = str(tmp_path / "src_clean0.wav")
    open(stem_path, "wb").write(b"RIFF")
    manifest = {"audio": {"dialogue_cleanup": {"stems": [{
        "source_file": clip_path, "stem_file": stem_path,
        "timeline_in_frame": 0, "label": "hook_clean0"}]}}}
    swaps = otio_mix.stem_swaps(manifest)
    assert len(swaps) == 1
    otio = _otio(clip_path)
    report = otio_mix.apply_stem_swaps(otio, swaps)
    assert len(report["applied"]) == 1 and not report["unmatched"]
    clip = next(otio_mix.audio_clips(otio))[1]
    assert clip["media_references"]["m1"]["target_url"].endswith(
        "src_clean0.wav")
    assert clip["source_range"]["start_time"]["value"] == 0
    assert clip["source_range"]["duration"]["value"] == 100
    assert not otio_mix.verify_stems(otio, report["applied"])


def test_stem_swap_reports_a_miss_or_a_missing_stem(tmp_path):
    """Neither is swapped; each is reported unmatched with the reason."""
    manifest = {"audio": {"dialogue_cleanup": {"stems": [{
        "source_file": "/src/other.MOV",
        "stem_file": str(tmp_path / "x.wav"),
        "timeline_in_frame": 500, "label": "miss"}]}}}
    report = otio_mix.apply_stem_swaps(
        _otio("/src/other.MOV"), otio_mix.stem_swaps(manifest))
    assert not report["applied"] and len(report["unmatched"]) == 1
    assert "matched no clip" in report["unmatched"][0]["reason"]

    report = otio_mix.apply_stem_swaps(_otio("/src/a.MOV"), [{
        "source_file": "/src/a.MOV", "stem_file": "/gone/clean.wav",
        "start_frame": 0, "label": "gone"}])
    assert not report["applied"]
    assert "not on disk" in report["unmatched"][0]["reason"]


# ── compile-time staging ─────────────────────────────────────────────

def _clip(source, start=0.0, end=4.0, label="hook"):
    return {"source_file": source, "source_in": start, "source_out": end,
            "timeline_in_frame": 0, "timeline_out_frame": 96,
            "label": label}


def test_stage_refuses_an_unplayed_source_or_span(tmp_path):
    with pytest.raises(DialogueCleanupRefused, match="no played clip"):
        stage_deepfilternet(
            {"source": "gone.MOV", "tool": "deepfilternet",
             "why": "noisy"},
            [], [], str(tmp_path))
    source = str(tmp_path / "speech.wav")
    _speech_wav(source)
    with pytest.raises(DialogueCleanupRefused, match="intersects nothing"):
        stage_deepfilternet(
            {"source": "speech.wav", "tool": "deepfilternet",
             "span_start": 10.0, "span_end": 12.0, "why": "noisy"},
            [_clip(source)], [(0.5, 1.5)], str(tmp_path))


def test_stage_cleans_each_surviving_range(tmp_path):
    source = str(tmp_path / "speech.wav")
    _speech_wav(source)
    staged = []
    with patch("library.tools.dialogue_cleanup._extract_range") as ext, \
         patch("library.tools.dialogue_cleanup.run_deepfilternet") as run:
        def _fake(src, dst, speech_spans=None):
            open(dst, "wb").write(b"RIFF")
            staged.append((src, dst, speech_spans))
            return {"stem_file": dst, "method": "python",
                    "wall_seconds": 0.3, "floor_before_dbfs": -29.7,
                    "floor_after_dbfs": -34.5,
                    "speech_before_lufs": -27.8,
                    "speech_after_lufs": -29.9}
        run.side_effect = _fake
        stems = stage_deepfilternet(
            {"source": "speech.wav", "tool": "deepfilternet",
             "span_start": 1.0, "span_end": 3.0, "why": "room in the tail"},
            [_clip(source, 0.0, 2.0, label="a"),
             _clip(source, 5.0, 6.0, label="b")],
            [(0.5, 1.5)], str(tmp_path))
    # The 5-6 s clip sits outside the 1-3 s span: one stem, clipped.
    assert len(stems) == 1
    assert (stems[0]["source_in"], stems[0]["source_out"]) == (1.0, 2.0)
    assert stems[0]["floor_before_dbfs"] == -29.7
    assert ext.call_count == 1 and len(staged) == 1


# --------------------------------------------------------------------------
# From test_dialogue_cleanup.py
#
# Dialogue cleanup is plan-requested, measured, and refused by name.
#
# Fidelity rung R5d: DeepFilterNet plus Resolve Voice Isolation, measured
# locally first (see `library/tools/dialogue_cleanup.py` for the table).
# These tests drive the real module on synthetic WAV fixtures (stdlib
# `wave`, decoded the same way - no ffmpeg, no model, no Resolve):
#
# - a plan entry with an unknown tool, a missing/out-of-range amount, a
#   half or backwards span, or no `why`/`source` refuses by name;
# - Voice Isolation claims nothing without the re-read (False, mismatch
#   and missing track all refuse; the fake timeline judges the
#   discipline);
# - the OTIO rewrite refuses a missing stem or reference (the swap itself
#   is `tests/unit/audio/test_audio_mix.py`'s);
# - the availability probe resolves package, shared location, then PATH,
#   and states its reason instead of raising.

if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from library.tools.dialogue_cleanup import (
    apply_voice_isolation,
    deepfilternet_probe,
    measure_source,
    rewrite_clip_media_to_stem,
    validate_cleanup_request,
)


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

def _clip_2(tmp_path):
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
    clip, stem = _clip_2(tmp_path)
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


# --------------------------------------------------------------------------
# From test_program_stream_measurement.py
#
# The program mix is a decision: declared, or measured - never defaulted.
#
# The catalog records every audio stream with what tells them apart. The
# captain's field-test MXF (LC4930.MXF, 2026-09-09) carries four identical
# mono pcm_s24le streams (one mix, two ISOs, one empty) with no layout,
# language, title or disposition, and the catalog once took the FIRST
# audio stream and discarded the rest - a non-program stream leaked onto
# the timeline. So `select_program_stream` refuses without a declaration,
# even where metadata could tell streams apart; a project may instead
# measure the mix off the footage (`source.measure_program_stream`). A
# declaration always wins; a measurement that is not decisive refuses
# exactly like an undeclared one.
#
# Media fixtures are generated with ffmpeg (skipped with a named
# environment where it is absent): four sine streams 6 dB apart read as
# four program candidates with a clear winner; two streams 1 dB apart
# read as ambiguous.

NEEDS_FFMPEG = pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="needs ffmpeg+ffprobe (CI installs both; local: brew install ffmpeg)",
)

FFPROBE = [
    "ffprobe", "-v", "quiet", "-print_format", "json",
    "-show_format", "-show_streams",
]


def _sine_gains(path, gains, duration=3):
    """One MKV - a video stream plus a mono sine per gain."""
    import subprocess

    inputs = ["-f", "lavfi", "-i",
              f"color=size=320x240:rate=24:duration={duration}:color=black"]
    for _gain in gains:
        inputs += ["-f", "lavfi", "-i",
                   f"sine=frequency=440:duration={duration}"]
    filters = "".join(
        f"[{i + 1}:a]volume={gain}[a{i}];"
        for i, gain in enumerate(gains))
    maps = ["-map", "0:v"]
    for i in range(len(gains)):
        maps += ["-map", f"[a{i}]"]
    cmd = (["ffmpeg", "-y"] + inputs + ["-filter_complex", filters]
           + maps + ["-c:v", "libx264", "-pix_fmt", "yuv420p",
                     "-c:a", "pcm_s16le", "-shortest", str(path)])
    subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                   check=True, timeout=120)


def _probe_streams(path):
    import json
    import subprocess

    result = subprocess.run(
        FFPROBE + [str(path)], capture_output=True, text=True,
        encoding="utf-8", check=True, timeout=60)
    return describe_audio_streams(json.loads(result.stdout))


def _mxf_like_probe():
    """ffprobe output shaped like the captain's MXF: one video stream,
    four IDENTICAL mono audio streams, one data stream."""
    streams = [{
        "index": 0, "codec_name": "h264", "codec_type": "video",
        "width": 3840, "height": 2160, "r_frame_rate": "24000/1001",
        "pix_fmt": "yuv420p", "tags": {},
    }]
    for i in (1, 2, 3, 4):
        streams.append({
            "index": i, "codec_name": "pcm_s24le", "codec_type": "audio",
            "sample_rate": "48000", "channels": 1,
            "bits_per_sample": 24, "tags": {},
            "disposition": {"default": 0, "dub": 0, "original": 0,
                            "comment": 0, "lyrics": 0, "karaoke": 0,
                            "forced": 0, "hearing_impaired": 0,
                            "visual_impaired": 0, "clean_effects": 0,
                            "attached_pic": 0, "timed_thumbnails": 0},
        })
    streams.append({"index": 5, "codec_name": "smpte_436m_anc",
                    "codec_type": "data"})
    return {"streams": streams, "format": {"duration": "10.0", "tags": {}}}


def _video():
    return {"index": 0, "codec_name": "h264", "codec_type": "video",
            "width": 1920, "height": 1080, "r_frame_rate": "30/1",
            "pix_fmt": "yuv420p"}


def test_every_audio_stream_is_recorded_with_what_tells_them_apart():
    streams = describe_audio_streams(_mxf_like_probe())
    assert len(streams) == 4
    first = streams[0]
    assert first["index"] == 1
    assert first["channel"] == 1
    assert first["codec"] == "pcm_s24le"
    assert first["channels"] == 1
    assert first["sample_rate"] == 48000
    for key in ("channel_layout", "language", "title", "handler"):
        assert key in first
    assert [s["channel"] for s in streams] == [1, 2, 3, 4]


def test_the_program_stream_is_declared_single_or_refused():
    mxf = describe_audio_streams(_mxf_like_probe())
    # Indistinguishable streams refuse rather than default to stream 0,
    # naming the source and what was seen.
    with pytest.raises(ProgramStreamRefused) as exc:
        select_program_stream(mxf, declaration=None, source="LC4930.MXF")
    assert "LC4930.MXF" in str(exc.value)
    assert "4 audio streams" in str(exc.value)
    # The project declares, and the engine obeys instead of choosing.
    chosen = select_program_stream(mxf, declaration=1, source="LC4930.MXF")
    assert (chosen["channel"], chosen["index"], chosen["basis"]) == (
        1, 1, "declared")

    # A single stream needs no declaration.
    phone = describe_audio_streams({"streams": [_video(), {
        "index": 1, "codec_name": "aac", "codec_type": "audio",
        "sample_rate": "44100", "channels": 2, "channel_layout": "stereo"}],
        "format": {"duration": "5.0"}})
    chosen = select_program_stream(phone, declaration=None, source="phone.MOV")
    assert (chosen["channel"], chosen["basis"]) == (1, "single")

    # Even distinguishable metadata is not a heuristic the engine may use.
    tagged = describe_audio_streams({"streams": [_video(), {
        "index": 1, "codec_name": "aac", "codec_type": "audio",
        "sample_rate": "48000", "channels": 2, "channel_layout": "stereo",
        "tags": {"language": "eng", "title": "Program Mix"}}, {
        "index": 2, "codec_name": "aac", "codec_type": "audio",
        "sample_rate": "48000", "channels": 8, "channel_layout": "7.1",
        "tags": {"language": "eng", "title": "ISO feeds"}}],
        "format": {"duration": "5.0"}})
    with pytest.raises(ProgramStreamRefused):
        select_program_stream(tagged, declaration=None, source="cam.MXF")
    chosen = select_program_stream(tagged, declaration=1, source="cam.MXF")
    assert chosen["title"] == "Program Mix"

    # A declaration beats a measurement.
    chosen = select_program_stream(
        [{"index": 1, "channel": 1}, {"index": 3, "channel": 2}],
        declaration=2, source="cam.MXF",
        measured_selection={"channel": 1, "basis": "measured-loudest",
                            "levels": {1: -10.0, 2: -20.0}})
    assert (chosen["channel"], chosen["basis"]) == (2, "declared")


@NEEDS_FFMPEG
def test_levels_follow_the_gains_in_stream_order(tmp_path):
    fixture = tmp_path / "four.mkv"
    _sine_gains(fixture, [1.0, 0.5, 0.25, 0.125])
    streams = _probe_streams(fixture)
    assert len(streams) == 4
    levels = measure_stream_levels(str(fixture), streams)
    ordered = [levels[c] for c in (1, 2, 3, 4)]
    assert ordered[0] > ordered[1] > ordered[2] > ordered[3]
    gaps = [a - b for a, b in zip(ordered, ordered[1:])]
    assert all(abs(gap - 6.02) < 0.3 for gap in gaps)


@NEEDS_FFMPEG
def test_an_ambiguous_measurement_refuses_like_an_undeclared_one(tmp_path):
    fixture = tmp_path / "close.mkv"
    _sine_gains(fixture, [1.0, 0.9, 0.25, 0.125])
    streams = _probe_streams(fixture)
    with pytest.raises(ProgramStreamRefused, match="within "
                       f"{MEASURE_MARGIN_DB} dB"):
        measure_program_selection(str(fixture), streams, source="close.mkv")


@NEEDS_FFMPEG
def test_source_block_measure_flag_records_a_measured_selection(tmp_path):
    """A decisive measurement selects the loudest stream, and the
    evidence travels with the decision, per file."""
    fixture = tmp_path / "four.mkv"
    _sine_gains(fixture, [1.0, 0.5, 0.25, 0.125])
    (tmp_path / "project.yaml").write_text(
        "source:\n  measure_program_stream: true\n", encoding="utf-8")
    result = catalog_footage(
        [{"path": str(fixture), "filename": "four.mkv",
          "extension": ".mkv",
          "size_bytes": fixture.stat().st_size,
          "clip_id": "clip_001"}],
        project_folder=str(tmp_path))
    entry = result["clip_catalog"][0]
    assert entry["program_stream"]["channel"] == 1
    assert entry["program_stream"]["basis"] == "measured-loudest"
    assert entry["program_stream_refusal"] is None
    # The evidence travels with the decision, per file.
    assert set(entry["program_stream"]["measured_levels_db"]) == {
        "CH1", "CH2", "CH3", "CH4"}


# --------------------------------------------------------------------------
# From test_speech_channel_resolution.py
#
# The build resolves the speech channel or refuses it loudly.
#
# The catalog records which stream is the program mix (declared or
# measured); the build must honour that record rather than falling back
# to a silent channel 1. These tests cover the resolution both builders
# share - the 6.01 master path resolves per angle, the reel path reads
# the project's declaration first - and the refusal an undeclared,
# unmeasurable source earns instead of a quiet default.

# (angle_sources, catalog_channels, catalog_refusals, manifest_decl,
#  project_decl, single_stream, expected_channel, basis_fragment)
RESOLVED = [
    # The manifest's own angle declaration wins over everything.
    ({"a.MXF"}, {"/x/a.MXF": 1, "a.MXF": 1}, {}, 2, 1, {"a.MXF"},
     2, "manifest angle declaration"),
    # The project's declaration beats a stale catalog recording.
    ({"a.MXF"}, {"a.MXF": 2}, {}, None, 1, set(), 1, "declaration"),
    # A catalog recording is honoured when nothing is declared.
    ({"a.MXF"}, {"a.MXF": 3}, {}, None, None, set(), 3, "catalog"),
    # A single-stream source is its own answer.
    ({"phone.MOV"}, {}, {}, None, None, {"phone.MOV"},
     1, "single-stream source"),
    # Every source on an angle agrees.
    ({"a.MXF", "b.MXF"}, {"a.MXF": 1, "b.MXF": 1}, {}, None, None, set(),
     1, "catalog"),
]

REFUSED = [
    # Sources on one angle recorded on different program channels.
    ({"a.MXF", "b.MXF"}, {"a.MXF": 1, "b.MXF": 2}, {}, None,
     ["different program"]),
    # An undeclared multi-stream source refuses loudly, naming the fix.
    ({"cam.MXF"}, {}, {}, None,
     ["REFUSING to place speech", "cam.MXF", "source.program_stream"]),
    # The catalog's own refusal is carried into the build refusal.
    ({"cam.MXF"}, {}, {"cam.MXF": "Refusal: cam.MXF carries 4 audio streams"},
     None, ["declared or recorded"]),
    # A garbage manifest declaration refuses rather than tracebacks.
    ({"a.MXF"}, {"a.MXF": 1}, {}, "CH1", ["not a channel ordinal"]),
]


def test_the_speech_channel_resolves_by_precedence():
    for (sources, recorded, refusals, manifest_decl, project_decl, single,
         expected, basis_fragment) in RESOLVED:
        channel, basis = resolve_speech_channel(
            "a", "SpeakerOne", manifest_decl, sources, recorded, refusals,
            project_decl, single)
        assert channel == expected, (sources, recorded, basis)
        assert basis_fragment in basis, (sources, basis)


def test_an_unresolvable_speech_channel_refuses_by_name():
    for sources, recorded, refusals, manifest_decl, fragments in REFUSED:
        with pytest.raises(SpeechChannelRefused) as exc:
            resolve_speech_channel(
                "a", "SpeakerOne", manifest_decl, sources, recorded, refusals,
                None, set())
        for fragment in fragments:
            assert fragment in str(exc.value), (fragment, str(exc.value))


def test_a_mapping_carries_the_program_stream_when_it_includes_it():
    """Finding 4: iPhone stereo speech maps CH[1, 2] and carries
    program CH1 in it. Exact-equality (`channels == [1]`) deleted
    every such item as "non-program audio" - twelve deletions, an
    export at -91 dB over the spoken hook, the step green."""
    assert mapping_carries_program([1, 2], 1) is True
    assert mapping_carries_program([1], 1) is True
    assert mapping_carries_program([2], 1) is False
    assert mapping_carries_program([3, 4], 1) is False
    assert mapping_carries_program([], 1) is False
    assert mapping_carries_program(None, 1) is False


# --------------------------------------------------------------------------
# From test_silence_under_picture.py
#
# Picture on screen over digital silence fails the render; the ladder above
# zero is reported and gates nothing (`craft-silence-under-picture` is the
# captain's). Measurements behind it: docs/evidence/music_tests.md#silence-under-picture.

FPS = 30
SR_2 = 48000
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
        handle.setframerate(SR_2)
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
    t = np.arange(int(SR_2 * seconds)) / SR_2
    return np.round(amplitude * 32767 * np.sin(2 * np.pi * 220 * t))


def _zeros(seconds: float):
    return np.zeros(int(SR_2 * seconds), dtype="<i2")


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


# --------------------------------------------------------------------------
# From test_speed_carries_linked_audio.py
#
# Finding 17: a retimed talking shot without its dialogue loses sync.
#
# Scout RT2.1 (B2): SetSpeed 50% verified (GetSpeed re-reads) while the
# picture played source 25-61 in the same 72 record frames and audio1
# still played 25-97 at full speed - half the line's picture gone, lip
# sync broken, build green.
#
# The fix: the linked dialogue rides the same write. In the build's
# native applicator every dialogue-row audio item sharing the video
# item's record span is retimed to the same Percentage and judged by its
# own re-read; an audio refusal fails the op (restoring the video item)
# rather than shipping the pair split. The resolve-axi `edit speed`
# verb selects the same way (same span, same source file - the bed
# shares spans, never sources) and names what it will touch in the dry
# run.

REPO = Path(__file__).resolve().parents[3]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

FPS_2 = 30.0


class _FakePool:
    def __init__(self, filename):
        self._filename = filename

    def GetClipProperty(self, key):
        assert key == "File Name"
        return self._filename


class _FakeItem:
    def __init__(self, name, start, duration, pool="hook.mov",
                 speed=100.0, refusing=False):
        self._name = name
        self._start = start
        self._duration = duration
        self._pool = _FakePool(pool)
        self._speed = float(speed)
        self._refusing = refusing
        self.writes = []

    def GetName(self): return self._name
    def GetStart(self): return self._start
    def GetEnd(self): return self._start + self._duration
    def GetDuration(self): return self._duration
    def GetSourceStartFrame(self): return 0
    def GetSourceEndFrame(self): return self._duration
    def GetUniqueId(self): return f"uid-{self._name}"
    def GetMediaPoolItem(self): return self._pool

    def GetSpeed(self):
        return {"Percentage": self._speed}

    def SetSpeed(self, opts):
        self.writes.append(dict(opts))
        if self._refusing:
            return False
        self._speed = float(opts["Percentage"])
        return True


class _FakeTimeline:
    def __init__(self, video=(), audio=()):
        self._video = list(video)
        self._audio = list(audio)

    def GetTrackCount(self, track_type):
        if track_type == "audio":
            return 1 if self._audio else 0
        return 1

    def GetItemListInTrack(self, track_type, index):
        if track_type == "video":
            return list(self._video) if index == 1 else []
        return list(self._audio) if index == 1 else []

    def GetSetting(self, key):
        assert key == "timelineFrameRate"
        return FPS_2

    def GetName(self): return "fake"


def _speed_op(op_id="speed_001", start=0.0, end=2.4, percent=50.0):
    return {"op_id": op_id, "effect_type": "speed_ramp",
            "timeline_start": start, "timeline_end": end,
            "segments": [{"percent": percent, "timeline_start": start,
                          "timeline_end": end}]}


# ── the build applicator ─────────────────────────────────────────

def test_retime_carries_same_span_dialogue_and_never_the_bed():
    """The B2 shape: 50% on the picture retimes the same-span dialogue
    to 50% too, each judged by its own re-read. Span match alone is not
    linkage: the bed plays the same span on a music row and stays at
    100% (track scoping is the caller's: dialogue row 1 only)."""
    from library.tools import native_ops_apply as apply
    video = _FakeItem("hook", 0, 72)
    dialogue = _FakeItem("hook-audio", 0, 72)
    bed = _FakeItem("bed", 0, 72, pool="infected.wav")

    class _TwoRow(_FakeTimeline):
        def GetItemListInTrack(self, track_type, index):
            if track_type == "audio":
                return {1: [dialogue], 2: [bed]}.get(index, [])
            return super().GetItemListInTrack(track_type, index)

    report = apply.apply_native_speed_ops(
        _TwoRow(video=[video]), [_speed_op()], fps=FPS_2, dialogue_tracks=[1])
    assert len(report["applied"]) == 1
    row = report["applied"][0]
    assert dialogue.GetSpeed()["Percentage"] == 50.0
    assert row["audio"][0]["item"] == "hook-audio"
    assert row["audio"][0]["percent"] == 50.0
    assert bed.GetSpeed()["Percentage"] == 100.0
    assert report["failed"] == []


def test_video_only_clip_reports_no_linked_audio():
    """B-roll/video_only has no dialogue: the op applies with the
    audio half said plainly, not silently."""
    from library.tools import native_ops_apply as apply
    video = _FakeItem("broll", 0, 72, pool="street.mov")
    timeline = _FakeTimeline(video=[video], audio=[])
    report = apply.apply_native_speed_ops(
        timeline, [_speed_op()], fps=FPS_2, dialogue_tracks=[1])
    assert len(report["applied"]) == 1
    assert "no linked dialogue audio" in report["applied"][0]["audio"]
    assert report["failed"] == []


def test_audio_refusal_fails_the_op_and_restores_the_video():
    """An audio item that will not retime fails the op BY NAME - and
    the video item goes back to its pre-op speed rather than leaving
    the pair split across two speeds."""
    from library.tools import native_ops_apply as apply
    video = _FakeItem("hook", 0, 72)
    dialogue = _FakeItem("hook-audio", 0, 72, refusing=True)
    timeline = _FakeTimeline(video=[video], audio=[dialogue])
    report = apply.apply_native_speed_ops(
        timeline, [_speed_op()], fps=FPS_2, dialogue_tracks=[1])
    assert report["applied"] == []
    (failed,) = report["failed"]
    assert failed["op_id"] == "speed_001"
    assert "hook-audio" in failed["what"]
    assert video.GetSpeed()["Percentage"] == 100.0


def test_freeze_carries_linked_audio():
    """A freeze is a retime to 0.0 for the pair, judged the same way."""
    from library.tools import native_ops_apply as apply
    video = _FakeItem("hook", 0, 72)
    dialogue = _FakeItem("hook-audio", 0, 72)
    timeline = _FakeTimeline(video=[video], audio=[dialogue])
    report = apply.apply_native_speed_ops(
        timeline, [{"op_id": "speed_001", "effect_type": "freeze_frame",
                    "timeline_start": 0.0, "timeline_end": 2.4}],
        fps=FPS_2, dialogue_tracks=[1])
    assert len(report["applied"]) == 1
    assert dialogue.GetSpeed()["Percentage"] == 0.0


# ── the resolve-axi verb selection ───────────────────────────────

def _span_of(item):
    from library.tools import resolve_axi as axi
    return axi._item_span(item)


def test_verb_links_same_source_audio_and_refuses_to_guess():
    """Same span + same file is linked; same span + another file is
    named and left alone; no audio selects nothing; a same-span item
    whose source will not read refuses rather than guesses - the bed
    could be hiding behind the blank."""
    from library.tools import resolve_axi as axi
    video = _FakeItem("hook", 0, 72, pool="hook.mov")
    dialogue = _FakeItem("hook-audio", 0, 72, pool="hook.mov")
    bed = _FakeItem("bed", 0, 72, pool="infected.wav")
    linked, skipped, refused, unchecked = axi._linked_audio_for_speed(
        _FakeTimeline(video=[video], audio=[dialogue, bed]), video,
        _span_of(video))
    assert [a.GetName() for a in linked] == ["hook-audio"]
    assert skipped == ["bed"]
    assert refused == "" and unchecked == ""

    assert axi._linked_audio_for_speed(
        _FakeTimeline(video=[video], audio=[]), video,
        _span_of(video)) == ([], [], "", "")

    class _NoSource(_FakeItem):
        def GetMediaPoolItem(self): return None

    linked, skipped, refused, unchecked = axi._linked_audio_for_speed(
        _FakeTimeline(video=[video], audio=[_NoSource("ghost", 0, 72)]),
        video, _span_of(video))
    assert linked == [] and skipped == []
    assert "ghost" in refused and unchecked == ""


def test_ledger_retime_sets_picture_and_dialogue_and_refuses_a_reshape():
    """Punch list 10: a ledger retime reaches the timeline as a speed on
    the placed picture AND its dialogue, each re-read; a retimed piece a
    later placement pass reshaped refuses rather than play the wrong
    source."""
    from types import SimpleNamespace

    import pytest

    from library.tools.reel_build import (
        ReelBuildError, _apply_ledger_retimes, placements)
    from library.tools.reel_clock import rated_range

    ranges = [rated_range(0.0, 10.0, [(2.0, 4.2, 1.1)])]
    video = SimpleNamespace(timeline_start=0.0, timeline_end=10.0,
                            source_in=0.0, track_index=1, speaker="A",
                            track_type="video", source_file="/hook.mov")
    audio = SimpleNamespace(**{**vars(video), "track_type": "audio"})
    placed = placements(ranges, [video, audio], FPS_2)
    retimed = [p for p in placed if "rate" in p]
    assert len(retimed) == 2
    start, frames = retimed[0]["snapped_record"], retimed[0]["record_frames"]
    assert (start, frames) == (60, 60)  # 66 master frames at 110%

    picture = _FakeItem("pic", start, frames)
    speech = _FakeItem("speech", start, frames)
    applied = _apply_ledger_retimes(
        _FakeTimeline(video=[picture], audio=[speech]), placed, FPS_2,
        [1], [1], "Reel 01")
    assert len(applied) == 1
    assert picture.GetSpeed()["Percentage"] == pytest.approx(110.0)
    assert speech.GetSpeed()["Percentage"] == pytest.approx(110.0)
    assert picture.writes[0]["RippleTimeline"] is False

    reshaped = [dict(p) for p in placed]
    for p in reshaped:
        if "rate" in p and p["clip"] is video:
            p["source_in"] += 0.5
    with pytest.raises(ReelBuildError, match="reshaped that piece"):
        _apply_ledger_retimes(
            _FakeTimeline(video=[_FakeItem("pic", start, frames)],
                          audio=[_FakeItem("speech", start, frames)]),
            reshaped, FPS_2, [1], [1], "Reel 01")
