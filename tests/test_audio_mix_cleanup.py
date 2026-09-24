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

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
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
    assert set(context["tools"]) == {"deepfilternet", "voice_isolation"}


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

def test_resolve_cleanup_accepts_a_valid_plan():
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


def test_resolve_cleanup_empty_means_no_cleanup():
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

def _otio(clip_path, start_frame=0, frames=100):
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


def _start(clip, frames=100):
    position = 0
    return position


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
    assert not otio_mix.verify_stems(otio, report["applied"])


def test_stem_swap_reports_the_miss(tmp_path):
    manifest = {"audio": {"dialogue_cleanup": {"stems": [{
        "source_file": "/src/other.MOV",
        "stem_file": str(tmp_path / "x.wav"),
        "timeline_in_frame": 500, "label": "miss"}]}}}
    otio = _otio("/src/other.MOV", start_frame=0)
    report = otio_mix.apply_stem_swaps(
        otio, otio_mix.stem_swaps(manifest))
    assert not report["applied"] and len(report["unmatched"]) == 1
    assert "matched no clip" in report["unmatched"][0]["reason"]


def test_stem_swap_refuses_a_missing_stem_file():
    otio = _otio("/src/a.MOV")
    report = otio_mix.apply_stem_swaps(otio, [{
        "source_file": "/src/a.MOV", "stem_file": "/gone/clean.wav",
        "start_frame": 0, "label": "gone"}])
    assert not report["applied"]
    assert "not on disk" in report["unmatched"][0]["reason"]


# ── compile-time staging ─────────────────────────────────────────────

def _clip(source, start=0.0, end=4.0, label="hook"):
    return {"source_file": source, "source_in": start, "source_out": end,
            "timeline_in_frame": 0, "timeline_out_frame": 96,
            "label": label}


def test_stage_refuses_an_unplayed_source(tmp_path):
    with pytest.raises(DialogueCleanupRefused, match="no played clip"):
        stage_deepfilternet(
            {"source": "gone.MOV", "tool": "deepfilternet",
             "why": "noisy"},
            [], [], str(tmp_path))


def test_stage_refuses_a_span_outside_the_played_range(tmp_path):
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
