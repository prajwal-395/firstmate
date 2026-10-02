"""How a creative value gets decided, and what the decision has to say.

The captain removed nine constants that decide creative outcomes with one
ruling rather than nine answers (2026-09-16): a stated preference wins,
then the project's creative direction, then the model reasoning over
measured signal, and only then a documented fallback that is visible AS
one.  `library/tools/decided_value.py` is the only implementation of that
precedence, and these tests are what stop a tenth constant being written
instead of a tenth slot.

What is pinned here:

* every rung, in order, including the ones that CANNOT answer - a rung
  that was skipped says why, so a run short of a preference reads
  differently from one short of a measurement;
* the solver - the model answers in units an ear can judge and the engine
  computes what the renderer reads, from measurements, and produces
  NOTHING rather than a stand-in when a term was never measured;
* the fallback - reached last, recorded as itself, owned by somebody who
  is not the engine;
* the trace - merged never replaced, and a value with no record behind it
  is refused.

The route to the model (schema appender, split-out answer, the
post-bridge hand-off) is exercised by the scenarios below; creative
quantity ownership is declared separately on the capability registry.
"""
from __future__ import annotations
import pytest
from library.tools import decided_value as dv
import os
import sys
from pathlib import Path
from unittest.mock import patch
from library.steps.step_5_04_compile_manifest.step import compile_manifest
from library.steps.step_4_03_plan_vfx.post_bridge import (
    STABILIZE_EFFECT,
    resolve_vfx,
)
import unittest
from library.tools.neural_engine import (  # noqa: E402
    apply_super_scale,
)
import numpy as np
from scipy.io import wavfile
from library.steps.step_5_02_audio_mix.mix import apply_word_gap_ducking
from library.steps.step_5_02_audio_mix.post_bridge import (
    resolve_audio_delivery_plan,
    resolve_music_ducking_plan,
)
from library.steps.step_6_01_render import step as render_step
from library.tools import audio_effects, dialogue_cleanup, otio_mix
from library.tools import master_loudness
from library.tools.master_loudness import (
    MasteringResult,
    master_render_report,
)
from library.tools.project_layout import Area, ProjectLayout


SLOT = "mix.speech_above_bed_db"

# Project 001's own numbers: a bed mastered about 8.5 dB hotter than the
# iPhone speech, which is the material that proved a single constant
# could not generalise.
MEASURED = {"bed_integrated_lufs": -13.9, "speech_lufs": -22.4}


@pytest.fixture
def _isolate_user_taste_profile(tmp_path, monkeypatch):
    """A local user's profile must not change the ladder's unit tests."""
    config = tmp_path / "user-config" / "ren" / "config.env"
    monkeypatch.setenv("REN_CONFIG", str(config))


def _decide(**kwargs):
    kwargs.setdefault("scope", "background")
    kwargs.setdefault("measurements", MEASURED)
    return dv.decide(SLOT, **kwargs)


# ─── The registry is a contract ───────────────────────────────────────

@pytest.mark.usefixtures("_isolate_user_taste_profile")
class TestTheRegistry:

    def test_an_unregistered_slot_raises_rather_than_resolving(self):
        with pytest.raises(dv.UnknownSlot):
            dv.slot("mix.something_nobody_registered")


# ─── The ladder, rung by rung ─────────────────────────────────────────

@pytest.mark.usefixtures("_isolate_user_taste_profile")
class TestTheLadder:

    def test_a_stated_preference_wins(self, tmp_path):
        (tmp_path / "project.yaml").write_text(
            "pipeline:\n"
            "  creative_preferences:\n"
            "    mix:\n"
            "      speech_above_bed_db:\n"
            "        background: 14\n", encoding="utf-8")
        decision = _decide(
            project_folder=str(tmp_path),
            model_answer={"value": 6.0, "why": "I would rather it were 6"})
        assert decision.basis == dv.STATED
        assert decision.answer == 14
        assert "creative_preferences" in decision.source

    def test_the_model_decides_when_the_project_states_nothing(self):
        decision = _decide(
            model_answer={"value": 9.0, "why": "the bed is hot and busy"})
        assert decision.basis == dv.REASONED
        assert decision.why == "the bed is hot and busy"
        assert decision.answer == 9.0

    def test_an_answer_with_no_why_or_no_number_is_not_a_decision(self):
        """5.01's rule: a value nobody can review is what the constant
        this replaces was; a value that is not a number is not solved."""
        for answer in ({"value": 9.0, "why": "   "},
                       {"value": "quite a lot", "why": "x"}):
            assert _decide(model_answer=answer).basis == dv.FALLBACK, answer

    def test_the_fallback_is_reached_last_and_says_it_is_one(self):
        decision = _decide()
        assert decision.basis == dv.FALLBACK
        assert decision.value == -18
        assert "Lucie" in decision.source
        assert "Superseded by" in decision.source

    def test_a_slot_with_no_fallback_yields_no_value_at_all(self):
        """Never 0, never a stand-in: the consumer drops or refuses."""
        import dataclasses

        row = dataclasses.replace(dv.slot(SLOT), fallback=None,
                                  undetermined_means="the window gets no gain")
        original = dict(dv.SLOTS)
        try:
            dv.SLOTS[SLOT] = row
            decision = _decide()
        finally:
            dv.SLOTS.clear()
            dv.SLOTS.update(original)
        assert decision.basis == dv.UNDETERMINED
        assert decision.value is None
        assert decision.why == "the window gets no gain"
        assert decision.decided is False

    def test_every_rung_that_could_not_answer_says_why(self):
        """A run short of a preference must read differently from one
        short of a measurement, or the record measures nothing."""
        decision = _decide()
        skipped = {r["basis"]: r["why"] for r in decision.rungs_skipped}
        assert set(skipped) == {dv.STATED, dv.DIRECTED, dv.REASONED}
        assert all(why.strip() for why in skipped.values())
        assert "creative_preferences" in skipped[dv.STATED]
        assert "DIRECTION_KEYS" in skipped[dv.DIRECTED]


# ─── The formula, and what it refuses to compute ──────────────────────

@pytest.mark.usefixtures("_isolate_user_taste_profile")
class TestTheSolver:

    def test_the_gain_is_arithmetic_over_the_judgement_and_two_measures(self):
        decision = _decide(model_answer={"value": 9.0, "why": "hot bed"})
        # (speech - separation) - bed
        assert decision.value == pytest.approx((-22.4 - 9.0) - -13.9)
        # Nothing is clamped in either direction - the captain: "it could
        # very well be possible that we need to use values outside of
        # these bounds".
        for separation in (-40.0, 0.0, 120.0):
            decision = _decide(
                model_answer={"value": separation, "why": "this piece"})
            assert decision.value == pytest.approx(
                (-22.4 - separation) - -13.9)

    def test_an_unmeasured_term_produces_no_value_rather_than_a_stand_in(
            self):
        for missing in ("bed_integrated_lufs", "speech_lufs"):
            measurements = dict(MEASURED)
            measurements[missing] = None
            decision = _decide(
                measurements=measurements,
                model_answer={"value": 9.0, "why": "hot bed"})
            assert decision.basis == dv.FALLBACK, (
                f"{missing}: reasoning over a measurement nobody took is the "
                "defect this module exists to remove, one level up")
            reasoned = next(r for r in decision.rungs_skipped
                            if r["basis"] == dv.REASONED)
            assert "not measured" in reasoned["why"]


# ─── The route to the model ───────────────────────────────────────────

@pytest.mark.usefixtures("_isolate_user_taste_profile")
class TestTheAsk:

    def test_the_answer_is_split_out_of_the_step_s_own_output(self):
        answer = {"audio_mix_spec": {"x": 1},
                  dv.FIELD: [{"slot": SLOT, "scope": "background",
                              "value": 9.0, "why": "hot bed"}]}
        remaining, taken = dv.take("audio_mix", answer)
        assert dv.FIELD not in remaining
        assert remaining == {"audio_mix_spec": {"x": 1}}
        assert taken[(SLOT, "background")]["value"] == 9.0

    def test_a_foreign_slot_or_an_entry_with_no_why_is_dropped(self):
        for entry in ({"slot": "mix.something_else", "value": 1,
                       "why": "because"},
                      {"slot": SLOT, "scope": "background", "value": 9.0}):
            _, taken = dv.take("audio_mix", {dv.FIELD: [entry]})
            assert taken == {}, entry

    def test_the_answer_reaches_a_post_bridge_through_one_key(self):
        """A post-bridge is a subprocess and cannot read the collector."""
        dv.reset()
        _, taken = dv.take("audio_mix", {dv.FIELD: [
            {"slot": SLOT, "scope": "background", "value": 9.0, "why": "w"}]})
        dv.stash("audio_mix", taken)
        merge_data = {dv.MERGE_KEY: dv.stashed("audio_mix")}
        assert dv.answers_from(merge_data, SLOT, "background")["value"] == 9.0
        assert dv.answers_from(merge_data, SLOT, "prominent") is None
        dv.reset()
        assert dv.stashed("audio_mix") == []


# ─── The trace ────────────────────────────────────────────────────────

@pytest.mark.usefixtures("_isolate_user_taste_profile")
class TestTheTrace:

    def test_a_value_is_refused_without_a_record_and_passes_with_one(self):
        with pytest.raises(dv.UndecidedValue):
            dv.assert_decided(SLOT, -18, [], scope="background")
        record = _decide().as_record()
        dv.assert_decided(SLOT, record["value"], [record], scope="background")

    def test_records_are_merged_not_replaced(self):
        """A `--rerun audio_mix` answers one step; replacing the key would
        erase every other step's decisions."""
        existing = [{"slot": "other.value", "scope": "", "step": "color_grade",
                     "basis": dv.REASONED, "value": 1},
                    {"slot": SLOT, "scope": "background", "step": "audio_mix",
                     "basis": dv.FALLBACK, "value": -18}]
        fresh = [{"slot": SLOT, "scope": "background", "step": "audio_mix",
                  "basis": dv.REASONED, "value": -19.5}]
        merged = dv.merge_records(existing, fresh)
        assert len(merged) == 2
        carried = next(r for r in merged if r["step"] == "color_grade")
        assert carried["from_a_previous_run"] is True
        answered = next(r for r in merged if r["step"] == "audio_mix")
        assert answered["value"] == -19.5
        assert "from_a_previous_run" not in answered


# --------------------------------------------------------------------------
# From test_downbeat_provenance.py
#
# Rung-1 finding 3: guessed downbeats must never travel as downbeats.
#
# Ren's downbeat grid was every 4th librosa beat from beat 0 and nothing
# said so - 4.04 snapped SFX to it, and on Sickick - Infected
# (Instrumental) it sat one beat off the bar (downbeat F 0.065 against
# beat_this, bass 177.7 on Ren's bar position against 567.7 one beat
# away). The producer now carries `downbeat_source` ("detected" vs
# "estimated"), the estimate is labelled everywhere it travels, and these
# tests fail if a grid ever ships without its provenance again.

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from library.tools.analysis.music_pipeline import _finalize_tempo


def test_finalize_labels_the_grid_detected_or_estimated():
    beats = [round(0.5 + i * 0.68, 3) for i in range(16)]
    downbeats = beats[::4]
    got = _finalize_tempo("beat-this-final0", beats, downbeats, "detected")
    assert got["downbeat_source"] == "detected"
    assert got["beats"] == beats
    assert got["downbeats"] == downbeats
    # Grid-derived BPM from the median interval, not the tracker's own
    # estimate (librosa's octave-errors).
    assert got["bpm"] == round(60.0 / 0.68, 1)

    # The every-4th-beat guess is labelled estimated.
    downbeats = [beats[i] for i in range(0, len(beats), 4)]
    got = _finalize_tempo("librosa-beat-track", beats, downbeats,
                          "estimated", "every 4th beat")
    assert got["downbeat_source"] == "estimated"
    assert "every 4th" in got["note"]


def _rhythm_with(source):
    import library.tools.analysis.music_pipeline as mp
    import library.tools.music_measurement as mm

    real_tempo, real_key = mp.analyze_tempo_beats, mp.analyze_key
    beats = [round(0.5 + i * 0.68, 3) for i in range(16)]
    mp.analyze_tempo_beats = lambda _path: {
        "method": "librosa-beat-track", "bpm": 88.2,
        "beats": beats, "downbeats": beats[::4],
        "downbeat_source": source, "tempo_stable": True, "note": "",
    }
    mp.analyze_key = lambda _path: {"method": None, "key": None,
                                    "scale": None,
                                    "note": "essentia not installed"}
    try:
        return mm.measure_rhythm_track("does-not-need-to-exist.wav")
    finally:
        mp.analyze_tempo_beats, mp.analyze_key = real_tempo, real_key


def test_the_estimate_reaches_the_model_labelled():
    """`measure_rhythm_track` surfaces the source and warns in
    `tempo_note` - the column the model reads beside the count - and a
    detected grid carries no such warning."""
    got = _rhythm_with("estimated")
    assert got["tempo_downbeat_source"] == "estimated"
    assert got["tempo_downbeat_count"] == 4
    assert "estimated" in got["tempo_note"], (
        "an estimated grid reaches the model with a bare count and no "
        "warning in tempo_note")
    got = _rhythm_with("detected")
    assert got["tempo_downbeat_source"] == "detected"
    assert got["tempo_note"] == ""


# ─────────────────────────────────────────────────────────
# `ren doctor` reports the beat_this checkpoint
# ─────────────────────────────────────────────────────────

def _beat_this_check(monkeypatch, present: bool):
    """The doctor's beat_this line with the checkpoint present/absent."""
    from pathlib import Path

    from ren import doctor

    if present:
        return [c for c in doctor.model_checks() if "beat_this" in c.name]
    monkeypatch.setattr(doctor, "torch_checkpoints",
                        lambda: Path("/nonexistent-torch-hub"))
    try:
        return [c for c in doctor.model_checks() if "beat_this" in c.name]
    finally:
        monkeypatch.undo()


def test_doctor_names_the_fetch_when_the_checkpoint_is_missing(monkeypatch):
    """Without the beat_this line a machine silently runs estimated
    downbeats; missing, the line degrades music.analyse and names the
    fetch, without failing the doctor."""
    found = _beat_this_check(monkeypatch, present=False)
    assert len(found) == 1
    check = found[0]
    from ren import doctor
    assert not check.ok and doctor.required_failures([check]) == [], (
        "a missing checkpoint must not FAIL the doctor - the run still "
        "completes on the labelled estimate - it only degrades music.analyse")
    assert "model.beat_this" in doctor.capability_report(
        [check])["music.analyse"][2]
    assert "estimate" in check.detail
    assert "load_model" in check.fix and "final0" in check.fix, (
        "the fix must say how to fetch the checkpoint, not just that it "
        "is missing")


# --------------------------------------------------------------------------
# From test_ken_burns_direction.py
#
# Ken Burns is the drift move under the captain's name - an EXTENSION.
#
# PR 777 landed reasoned kinetic motion: `slow_zoom_in` / `slow_zoom_out`
# refuse to apply without a stated per-shot rationale.  The captain then
# asked for "a lot of ken burns to emphasize points" (2026-09-09), naming
# the same move.  So `ken_burns` is accepted as a spelling, with the
# direction DERIVED from the entry's own `zoom_start` / `zoom_end` and the
# same rationale bar: this extends PR 777 rather than adding a second
# motion system beside it.  Two overlapping motion systems would be worse
# than none, so the resolved effect IS the drift effect - the renderer
# reads it unchanged.
#
# A `ken_burns` entry whose params state no direction is dropped as
# `ken_burns_without_direction`; one with no rationale is dropped as
# `no_stated_reason`, like every other drift entry.

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO))

from library.steps.step_4_03_plan_vfx.post_bridge import (
    _derive_ken_burns_direction,
)


def _spine(*positions):
    return {"structure": [
        {"position": p, "block_type": "speech",
         "timeline_start": float(i * 5), "timeline_end": float(i * 5 + 5)}
        for i, p in enumerate(positions)
    ]}


def _ken_burns(position=1, **extra):
    entry = {"target_block_position": position,
             "effect_type": "ken_burns",
             "params": {"zoom_start": 1.0, "zoom_end": 1.03},
             "rationale": "a locked hold that goes dead under the point"}
    entry.update(extra)
    return entry


def test_direction_is_derived_from_the_params():
    assert _derive_ken_burns_direction(
        {"zoom_start": 1.0, "zoom_end": 1.03}) == "slow_zoom_in"
    assert _derive_ken_burns_direction(
        {"zoom_start": 1.03, "zoom_end": 1.0}) == "slow_zoom_out"
    assert _derive_ken_burns_direction(
        {"zoom_start": 1.0, "zoom_end": 1.0}) is None
    assert _derive_ken_burns_direction({"zoom_start": 1.0}) is None
    assert _derive_ken_burns_direction({}) is None
    assert _derive_ken_burns_direction("zoom") is None


def test_push_in_resolves_as_the_drift_effect():
    """EXTENSION, not a second system: the resolved entry IS a
    `slow_zoom_in`, which is what the renderer dispatches on."""
    dropped = []
    resolved = resolve_vfx([_ken_burns()], _spine(1, 2), dropped=dropped)
    assert len(resolved) == 1
    assert resolved[0]["effect_type"] == "slow_zoom_in"
    assert resolved[0]["params"] == {"zoom_start": 1.0, "zoom_end": 1.03}
    assert dropped == []


def test_directionless_params_get_no_motion():
    """Equal zooms name neither way, and an unstated reason is no reason:
    both dropped by name, never defaulted."""
    dropped = []
    resolved = resolve_vfx(
        [_ken_burns(params={"zoom_start": 1.02, "zoom_end": 1.02})],
        _spine(1, 2), dropped=dropped)
    assert resolved == []
    assert [d.reason for d in dropped] == ["ken_burns_without_direction"]

    # The same bar as PR 777: emphasis is the reason, and it must be
    # stated - a move every shot gets is what the captain rejected.
    entry = _ken_burns()
    del entry["rationale"]
    dropped = []
    resolved = resolve_vfx([entry], _spine(1, 2), dropped=dropped)
    assert resolved == []
    assert [d.reason for d in dropped] == ["no_stated_reason"]


# --------------------------------------------------------------------------
# From test_neural_directives.py
#
# Stabilization is plan-requested, never keyword-decided.
#
# compile_manifest used to set `neural_engine_directives[*].stabilize`
# off a keyword match (`_UNSTABLE_CAMERA_WORDS`) against vision prose -
# the stability summary, camera prose, scene text and assessment
# keywords - so Ren stabilized clips nobody asked it to (captain,
# 2026-09-24). Now a shaky-worded clip compiles to NO stabilize
# directive unless an `enhancement_spec.visual_effects` entry with
# `effect_type == "stabilize"` (step 4.03 plan_vfx) covers it, and a
# requested one still reaches the build's neural applicator.

SOURCE = os.path.abspath(__file__)

SHAKY_V3_DOC = {
    "clip_id": "clip_001",
    "scene": [{"start": 0, "end": 10, "location": "street",
               "type": "exterior"}],
    "camera": [{"start": 0, "end": 10, "framing": "medium",
                "mode": "handheld", "stability": "shaky",
                "movement": "walking"}],
    "actions": [], "objects": [],
    "assessment": {"content_type": "person_talking_to_camera",
                   "camera_stability": "shaky",
                   "keywords": ["handheld", "speaker"]},
}


def _inputs(semantic, enhancement_spec=None):
    return {
        "semantic_analysis": semantic,
        "audio_spine": {
            "structure": [{
                "block_type": "speech", "position": 1, "clip_id": "clip_001",
                "source_start": 2.417, "source_end": 12.417,
                "timeline_start": 0.0, "timeline_end": 10.0,
                "content": {"clip_id": "clip_001"},
            }],
            "frame_rate": 30.0,
        },
        # The catalog keys clips as clip_XXX while step_1_03 keys its
        # documents by file stem - the join semantic_index performs.
        "clip_catalog": [{"clip_id": "clip_001", "path": SOURCE,
                          "width": 1080, "height": 1920}],
        "a_roll_assignments": [{
            "spine_block_position": 1, "clip_id": "clip_001",
            "source_file": SOURCE, "video_in": 2.417, "video_out": 12.417,
            "timeline_start": 0.0, "timeline_end": 10.0,
        }],
        "b_roll_assignments": [], "transition_spec": [],
        "enhancement_spec": enhancement_spec
        if enhancement_spec is not None else [],
        "sfx_spec": [],
        "color_grade_spec": {}, "audio_mix_spec": {},
    }


def _directives(semantic, enhancement_spec=None):
    inputs = _inputs(semantic, enhancement_spec)
    with patch("library.steps.step_5_04_compile_manifest.step.load",
               side_effect=lambda out_dir, filename: inputs):
        manifest = compile_manifest("dummy")
    return manifest["neural_engine_directives"]


def _stabilize_request(position=1, start=0.0, end=10.0):
    return {"visual_effects": [{
        "target_block_position": position,
        "timeline_start": start, "timeline_end": end,
        "effect_type": "stabilize", "params": {},
        "rationale": "handheld walk, visibly shaky",
    }]}


def test_a_shaky_worded_clip_is_stabilized_only_when_the_plan_requests_it():
    """Every word that used to trigger the keyword match is in this
    document: no directive follows on its own, and one plan entry
    stabilizes the placed clip through the build's neural path."""
    directives = _directives(
        {"semantic_analysis_documents": [dict(SHAKY_V3_DOC)]})
    assert not any(d.get("stabilize") for d in directives.values())

    directives = _directives(
        {"semantic_analysis_documents": [dict(SHAKY_V3_DOC)]},
        _stabilize_request())
    assert directives.get("speech_1", {}).get("stabilize") is True


def test_plan_vfx_resolves_a_stabilize_entry_to_the_neural_route():
    """The plan vocabulary the compile reads: `stabilize` resolves with
    `route == "neural_engine"`, never as a Fusion comp."""
    spine = {"structure": [{
        "position": 1, "timeline_start": 0.0, "timeline_end": 10.0,
    }]}
    resolved = resolve_vfx([{
        "target_block_position": 1, "effect_type": "stabilize",
        "params": {}, "rationale": "handheld walk, visibly shaky",
    }], spine)
    assert len(resolved) == 1
    entry = resolved[0]
    assert entry["effect_type"] == STABILIZE_EFFECT
    assert entry["route"] == "neural_engine"
    assert (entry["timeline_start"], entry["timeline_end"]) == (0.0, 10.0)


def test_documents_that_join_to_nothing_fail_loudly():
    """A key-name mismatch has to raise, not compile to an empty dict."""
    with pytest.raises(ValueError, match="none of them joined"):
        _directives({"semantic_analysis_documents": [
            {"clip_id": "a_clip_from_another_project",
             "analysis": {"motion": "handheld"}},
        ]})


def test_no_semantic_analysis_at_all_is_not_an_error():
    assert _directives({}) == {}


# --------------------------------------------------------------------------
# From test_neural_engine.py

class MockMediaPoolItem:
    """Super Scale lives on the MEDIA POOL item, not the timeline item."""

    def __init__(self):
        self.properties = {}

    def SetClipProperty(self, key, value):
        if key == "Super Scale":
            # Resolve accepts an int and rejects anything else, including
            # the string "2".
            if value != 2:
                return False
        self.properties[key] = value
        return True


class MockClip:
    def __init__(self, name="TestClip"):
        self.name = name
        self.properties = {}
        self.stabilize_result = True
        self.media_pool_item = MockMediaPoolItem()

    def GetName(self):
        return self.name

    def GetMediaPoolItem(self):
        return self.media_pool_item

    def SetClipProperty(self, key, value):
        self.properties[key] = value
        return True

    def Stabilize(self):
        return self.stabilize_result


class TestNeuralEngine(unittest.TestCase):
    def test_apply_super_scale_targets_the_media_pool_item(self):
        clip = MockClip()
        self.assertTrue(apply_super_scale(clip, 2))
        # Set on the media pool item, and NOT on the timeline item, which
        # is where it used to go and be silently refused.
        self.assertEqual(clip.media_pool_item.properties["Super Scale"], 2)
        self.assertNotIn("Super Scale", clip.properties)

        # The real property names: "SuperScale Sharpness", not
        # "Super Scale Sharpness".
        clip = MockClip()
        apply_super_scale(clip, 2, sharpness="Medium", noise_reduction="Medium")
        self.assertIn("SuperScale Sharpness", clip.media_pool_item.properties)
        self.assertIn("SuperScale Noise Reduction", clip.media_pool_item.properties)


# --------------------------------------------------------------------------
# From test_audio_chain_declared_ops.py
#
# Audio-chain plans must validate, render, and report the values they name.

def test_an_audio_ops_request_the_renderer_cannot_honour_refuses():
    """The plan schema catches effect controls the renderer never reads,
    and an empty operation list refuses instead of claiming delivery."""
    request = {
        "source": "speaker.wav",
        "tool": "audio_ops",
        "operations": [{
            "type": "equalizer", "frequency_hz": 300,
            "gain_db": -3, "q": 1, "why": "boxiness",
            "unread_control": 2,
        }],
        "why": "measured room floor and voice spectrum",
    }
    with pytest.raises(dialogue_cleanup.DialogueCleanupRefused) as raised:
        dialogue_cleanup.validate_cleanup_request(request)
    assert "unread keys" in raised.value.why
    assert "unread_control" in raised.value.why

    with pytest.raises(dialogue_cleanup.DialogueCleanupRefused,
                       match="operations.*empty"):
        dialogue_cleanup.validate_cleanup_request(
            dict(request, operations=[], why="clean up the room"))


def test_block_boundary_ramp_does_not_pull_down_a_recovered_gap():
    """Word keys own a word-keyed block; the block ramp stays out of it.

    The ramp between two block levels sat a quarter-block inside each
    block, so a trailing gap recovered after the last word and was then
    ramped back down to the ducked level before the block ended.
    """
    def block(start, end, words, level):
        return {"spine_block_position": start, "timeline_start": start,
                "timeline_end": end, "music_behavior": "background",
                "target_level_db": level, "word_intervals": words,
                "word_gap_level_db": level + 10.0,
                "word_gap_release_ms": 300.0}

    automation = [block(0.0, 8.0, [[0.0, 2.0]], -30.0),
                  block(8.0, 16.0, [[8.0, 14.0]], -24.0)]
    curve = otio_mix.music_curve(
        automation, fps=10, clip_start_frame=0, clip_frame_count=160,
        fade_seconds=2.0)

    keys = sorted(curve.items())

    def level_at(frame):
        for (left, low), (right, high) in zip(keys, keys[1:]):
            if left <= frame <= right:
                return low + (high - low) * (frame - left) / (right - left)
        raise AssertionError(frame)

    # Block 1 recovers at 2.3 s and holds -20 dB until its last frame;
    # block 2 starts on a word, at its own ducked level.
    assert [level_at(f) for f in (23, 50, 70, 79)] == [-20.0] * 4
    assert level_at(80) == -24.0


def test_audio_ops_chain_stages_the_played_clip_range(tmp_path):
    """A declared EQ chain becomes a stem that OTIO can actually place."""
    source = tmp_path / "speaker.wav"
    source.write_bytes(b"source")
    request = {
        "source": "speaker.wav", "tool": "audio_ops",
        "operations": [{"type": "high_pass", "frequency_hz": 80,
                        "why": "remove measured handling rumble"}],
        "why": "measured low frequency rumble",
    }
    clip = {
        "source_file": str(source), "source_in": 2.0, "source_out": 4.0,
        "timeline_in_frame": 48, "timeline_out_frame": 96,
        "label": "speech_1",
    }

    def extract(_source, _start, _end, output):
        Path(output).write_bytes(b"range")

    def process(input_path, output_path, operations):
        Path(output_path).write_bytes(b"processed")
        return {"input_path": input_path, "output_path": output_path,
                "operations": operations, "delivered": True}

    with patch.object(dialogue_cleanup, "_extract_range", extract), \
            patch.object(dialogue_cleanup, "_match_source_channels",
                         return_value=1), \
            patch.object(audio_effects, "apply_operations", process):
        stems = dialogue_cleanup.stage_audio_chain(
            request, [clip], [], str(tmp_path / "build"))

    assert len(stems) == 1
    assert stems[0]["source_in"] == 2.0
    assert stems[0]["timeline_in_frame"] == 48
    assert stems[0]["tool"] == "audio_ops"
    assert stems[0]["operations"][0]["type"] == "high_pass"
    assert Path(stems[0]["stem_file"]).read_bytes() == b"processed"
    swaps = otio_mix.stem_swaps({"audio": {
        "dialogue_cleanup": {"stems": stems}}})
    assert swaps[0]["stem_file"] == stems[0]["stem_file"]
    assert swaps[0]["start_frame"] == 48


def test_audio_ops_stem_is_measured_and_plays_in_both_channels(tmp_path):
    """A stem is measured, then delivered in the source's channel layout.

    The WAV reader refused the chain's 24-bit stems and the refusal was
    swallowed, so every audio_ops stem on the K2 evals reported speech
    after `None`.  And a mono stem swapped onto a stereo source's clip
    played in the left channel only: the K2 exports carried cleaned
    dialogue about 15 dB down in the right ear.
    """
    sample_rate = 48000
    seconds = np.arange(sample_rate * 3) / sample_rate
    speech = 0.2 * np.sin(2 * np.pi * 220 * seconds)
    source = tmp_path / "speaker.wav"
    stereo = np.stack([speech, speech], axis=1)
    wavfile.write(source, sample_rate,
                  (stereo * np.iinfo(np.int16).max).astype(np.int16))
    request = {
        "source": "speaker.wav", "tool": "audio_ops",
        "operations": [{"type": "high_pass", "frequency_hz": 80,
                        "why": "remove measured handling rumble"}],
        "why": "measured low frequency rumble",
    }
    clip = {
        "source_file": str(source), "source_in": 0.0, "source_out": 3.0,
        "timeline_in_frame": 0, "timeline_out_frame": 72,
        "label": "speech_1",
    }

    stems = dialogue_cleanup.stage_audio_chain(
        request, [clip], [], str(tmp_path / "build"))

    assert isinstance(stems[0]["speech_before_lufs"], float)
    assert stems[0]["speech_after_lufs"] == pytest.approx(
        stems[0]["speech_before_lufs"], abs=1.0)
    _, delivered = wavfile.read(stems[0]["stem_file"])
    assert delivered.ndim == 2 and delivered.shape[1] == 2
    left, right = (np.sqrt(np.mean(delivered[:, c].astype(np.float64) ** 2))
                   for c in (0, 1))
    assert left > 0 and right == pytest.approx(left, rel=1e-3)
    assert stems[0]["channels"] == 2


def test_declared_effects_compile_to_filters_and_wpe_changes_a_spectrum():
    """Each named operation has an execution path, including dereverb."""
    operations = [
        {"type": "high_pass", "frequency_hz": 80, "why": "rumble"},
        {"type": "equalizer", "frequency_hz": 300,
         "gain_db": -3, "q": 1, "why": "boxiness"},
        {"type": "de_ess", "frequency_hz": 6000,
         "reduction_db": 3, "threshold_dbfs": -30, "q": 2,
         "attack_ms": 5, "release_ms": 80, "why": "sibilance"},
        {"type": "dereverb", "strength": 0.5, "why": "room tail"},
    ]

    filters = [audio_effects.ffmpeg_filter(op) for op in operations[:3]]
    assert filters[0] == "highpass=f=80"
    assert filters[1].startswith("equalizer=f=300")
    assert filters[2].startswith("adynamicequalizer=")
    assert "mode=cutabove" in filters[2]

    rng = np.random.default_rng(7)
    spectrum = np.zeros((2, 200), dtype=np.complex128)
    spectrum[:, 8] = 1.0
    for frame in range(9, spectrum.shape[1]):
        spectrum[:, frame] = 0.75 * spectrum[:, frame - 1]
    spectrum += (rng.standard_normal(spectrum.shape)
                 + 1j * rng.standard_normal(spectrum.shape)) * 1e-5
    processed = audio_effects.dereverberate_spectrum(
        spectrum, strength=0.5, taps=3, delay=2, iterations=2)

    assert processed.shape == spectrum.shape
    assert np.isfinite(processed).all()
    assert not np.allclose(processed, spectrum)


def test_declared_high_pass_attenuates_below_its_cutoff(tmp_path):
    """The plan's high-pass reaches samples, not only a filter string."""
    sample_rate = 48000
    seconds = np.arange(sample_rate * 2) / sample_rate
    source_signal = (0.4 * np.sin(2 * np.pi * 40 * seconds)
                     + 0.1 * np.sin(2 * np.pi * 600 * seconds))
    source = tmp_path / "source.wav"
    output = tmp_path / "high_pass.wav"
    wavfile.write(source, sample_rate, source_signal.astype(np.float32))

    audio_effects.apply_operations(str(source), str(output), [{
        "type": "high_pass", "frequency_hz": 80,
        "why": "remove measured low-frequency rumble",
    }])

    out_rate, processed = wavfile.read(output)
    assert out_rate == sample_rate
    spectrum_before = abs(np.fft.rfft(source_signal))
    spectrum_after = abs(np.fft.rfft(processed.astype(np.float64)))
    low_bin, high_bin = 40 * 2, 600 * 2
    assert spectrum_after[low_bin] / spectrum_after[high_bin] \
        < (spectrum_before[low_bin] / spectrum_before[high_bin]) * 0.5


def test_word_gap_curve_recovers_after_declared_release():
    """The bed stays ducked on words, then reaches the declared gap level."""
    automation = [{
        "spine_block_position": 1,
        "timeline_start": 0.0, "timeline_end": 8.0,
        "music_behavior": "background",
        "target_level_db": -30.0,
        "speech_lufs": -16.0,
    }]
    spine = {"structure": [{
        "position": 1, "block_type": "speech", "clip_id": "clip-1",
        "source_start": 10.0, "source_end": 18.0,
        "timeline_start": 0.0, "timeline_end": 8.0,
        "word_timestamps": [
            {"word": "one", "source_start": 11.0, "source_end": 12.0},
            {"word": "two", "source_start": 14.0, "source_end": 15.0},
        ],
    }]}
    plan = {"enabled": True, "duck_db": 10.0,
            "release_ms": 300.0, "why": "10 dB duck, 300 ms recovery"}

    apply_word_gap_ducking(automation, spine, plan)
    curve = otio_mix.music_curve(
        automation, fps=10, clip_start_frame=0, clip_frame_count=80,
        fade_seconds=0.0)

    assert automation[0]["word_intervals"] == [[1.0, 2.0], [4.0, 5.0]]
    assert automation[0]["word_gap_level_db"] == -20.0
    assert curve[0] == -20.0
    assert curve[10] == -30.0
    assert curve[20] == -30.0
    assert curve[23] == -20.0
    assert curve[40] == -30.0
    assert curve[50] == -30.0
    assert curve[53] == -20.0


def test_word_gap_recovery_is_held_until_the_next_word():
    """Resolve interpolates linearly between keys, so a gap needs a hold key.

    With only the recovery key after one word and the ducked key on the
    next, every gap was a ramp back down that started the moment it
    recovered: the K2 exports delivered 2-5 dB of a planned 10 dB.
    """
    automation = [{
        "spine_block_position": 1,
        "timeline_start": 0.0, "timeline_end": 8.0,
        "music_behavior": "background",
        "target_level_db": -30.0,
        "speech_lufs": -16.0,
        "word_intervals": [[1.0, 2.0], [4.0, 5.0]],
        "word_gap_level_db": -20.0,
        "word_gap_release_ms": 300.0,
    }]
    curve = otio_mix.music_curve(
        automation, fps=10, clip_start_frame=0, clip_frame_count=80,
        fade_seconds=0.0)

    def level_at(frame):
        keys = sorted(curve.items())
        for (left, low), (right, high) in zip(keys, keys[1:]):
            if left <= frame <= right:
                return low + (high - low) * (frame - left) / (right - left)
        raise AssertionError(frame)

    # Leading gap and the gap between the words stay at the recovered level
    # until one frame before the next word, which starts ducked.
    assert [level_at(f) for f in (0, 5, 9)] == [-20.0, -20.0, -20.0]
    assert level_at(10) == -30.0
    assert [level_at(f) for f in (23, 31, 39)] == [-20.0, -20.0, -20.0]
    assert level_at(40) == -30.0


def test_numeric_mix_values_the_request_states_are_preserved_exactly():
    """MX3.1's 10 dB / 300 ms duck and -16 LUFS / -1 dBTP delivery are
    not rounded into engine defaults by planning."""
    plan = resolve_music_ducking_plan({
        "music_ducking_plan": {
            "enabled": True, "duck_db": 10, "release_ms": 300,
            "why": "request states 10 dB and 300 ms",
        },
    })
    assert plan["duck_db"] == 10.0
    assert plan["release_ms"] == 300.0

    plan = resolve_audio_delivery_plan({
        "audio_delivery_plan": {
            "dialogue_target_lufs": -16,
            "true_peak_ceiling_dbtp": -1,
            "why": "request states both delivery targets",
        },
    })
    assert plan["dialogue_target_lufs"] == -16.0
    assert plan["true_peak_ceiling_dbtp"] == -1.0


def test_master_report_points_only_to_the_measured_delivery_file(tmp_path):
    """The export report cannot keep pointing at Resolve's unmastered file."""
    raw = tmp_path / "reel_pre_master.mp4"
    final = tmp_path / "reel.mp4"
    raw.write_bytes(b"raw")
    final.write_bytes(b"mastered")
    result = MasteringResult(
        input_path=str(raw), output_path=str(final), already_compliant=False,
        input_i=-21.02, input_tp=0.24, output_i=-14.1, output_tp=-1.4)

    with patch("library.tools.master_loudness.normalize_to_delivery",
               return_value=result) as normalize:
        report = master_render_report({"output_path": str(raw)}, str(final))

    normalize.assert_called_once_with(
        str(raw), str(final), target_lufs=-14.0,
        true_peak_ceiling=-1.0)
    assert report["raw_output_path"] == str(raw)
    assert report["output_path"] == str(final)
    assert report["mastering"]["input"]["lufs"] == -21.02
    assert report["mastering"]["output"]["true_peak_dbtp"] == -1.4
    assert report["size_bytes"] == len(b"mastered")


def test_mastering_measures_and_delivers_the_lufs_and_true_peak_target(tmp_path):
    """The output gets re-measured against -14 LUFS and -1 dBTP."""
    sample_rate = 48000
    seconds = np.arange(sample_rate * 5) / sample_rate
    waveform = 0.08 * np.sin(2 * np.pi * 1000 * seconds)
    source = tmp_path / "quiet.wav"
    output = tmp_path / "delivery.mp4"
    wavfile.write(source, sample_rate,
                  (waveform * np.iinfo(np.int16).max).astype(np.int16))

    from library.tools.master_loudness import normalize_to_delivery

    result = normalize_to_delivery(str(source), str(output))

    assert result.output_i == pytest.approx(-14.0, abs=0.5)
    assert result.output_tp <= -1.0

    explicit_output = tmp_path / "dialogue_target.mp4"
    explicit = normalize_to_delivery(
        str(source), str(explicit_output), target_lufs=-16.0)
    assert explicit.output_i == pytest.approx(-16.0, abs=0.5)
    assert explicit.output_tp <= -1.0


def test_mastering_keeps_the_mix_level_changes_it_was_handed(tmp_path):
    """Mastering is one static gain; it never rides the mix's levels.

    loudnorm's `linear=true` falls back to dynamic mode whenever the gain
    would push the true peak over target - every K2 eval master - and that
    AGC pulled a bed's planned level changes back together.
    """
    sample_rate = 48000
    seconds = np.arange(sample_rate * 12) / sample_rate
    carrier = np.sin(2 * np.pi * 1000 * seconds)
    # 8 s ducked, 4 s recovered 10 dB louder; a peaky click every half
    # second makes the linear gain infeasible under the true-peak ceiling.
    waveform = np.where(seconds < 8.0, 0.02, 0.02 * 10 ** (10 / 20)) * carrier
    waveform[::sample_rate // 2] = 0.95
    source = tmp_path / "mix.wav"
    output = tmp_path / "delivery.mp4"
    wavfile.write(source, sample_rate,
                  (waveform * np.iinfo(np.int16).max).astype(np.int16))

    master_loudness.normalize_to_delivery(str(source), str(output))

    from library.tools.render_qa import _decode_mono
    delivered = _decode_mono(str(output))

    def rms_db(left, right):
        part = delivered[int(left * sample_rate):int(right * sample_rate)]
        return 20 * np.log10(np.sqrt(np.mean(part ** 2)))

    assert rms_db(9.0, 11.5) - rms_db(2.0, 7.5) == pytest.approx(10.0, abs=0.5)


def test_master_retries_when_aac_reencode_overshoots_true_peak(tmp_path):
    """AAC re-encoding can overshoot the limiter's requested true-peak ceiling."""
    source = tmp_path / "resolve_render.mp4"
    output = tmp_path / "delivery.mp4"
    source.write_bytes(b"raw resolve export")
    readings = iter([
        {"input_i": -21.0, "input_tp": 0.2, "input_lra": 8.0,
         "input_thresh": -32.0, "target_offset": 0.0},
        {"input_i": -14.73, "input_tp": 0.13, "input_lra": 7.0,
         "input_thresh": -25.0, "target_offset": 0.0},
        {"input_i": -14.0, "input_tp": -1.1, "input_lra": 7.0,
         "input_thresh": -25.0, "target_offset": 0.0},
    ])
    filters = []

    def encode(argv, **_kwargs):
        filters.append(argv[argv.index("-af") + 1])
        Path(argv[-1]).write_bytes(b"encoded delivery")

        class Completed:
            returncode = 0
            stderr = ""

        return Completed()

    with patch.object(master_loudness, "_loudnorm_measure",
                      side_effect=lambda _path: next(readings)), \
            patch.object(master_loudness, "_audio_sample_rate",
                         return_value=48000), \
            patch.object(master_loudness.subprocess, "run",
                         side_effect=encode) as run:
        result = master_loudness.normalize_to_delivery(
            str(source), str(output))

    assert run.call_count == 2
    # First pass: the measured gain under a -1.5 dBTP limiter.  Second:
    # the gain corrected by the measured 0.73 LU shortfall, the limiter
    # lowered by the measured 1.13 dB overshoot plus the retry margin.
    assert filters[0] == master_loudness.static_gain_filter(7.0, -1.5, 48000)
    assert filters[1] == master_loudness.static_gain_filter(
        7.73, -3.13, 48000)
    assert result.normalization_attempts == 2
    assert result.output_i == -14.0
    assert result.output_tp == -1.1
    assert output.read_bytes() == b"encoded delivery"


def test_pipeline_export_masters_the_scratch_render_before_reporting(tmp_path):
    """Step 6.01 exports the mastered path while preserving the raw render."""
    project = tmp_path / "project"
    project.mkdir()
    layout = ProjectLayout(str(project))
    scratch = str(layout.write_dir(Area.SCRATCH, step="render"))
    exports = str(layout.write_dir(Area.EXPORTS, step="render"))
    raw = os.path.join(scratch, "Timeline_1_pre_master.mp4")
    mastered = os.path.join(exports, "Timeline_1.mp4")

    class Completed:
        returncode = 0
        stderr = ""
        stdout = '{"output_path": "' + raw + '", "size_bytes": 3}'

    with patch.object(render_step.subprocess, "run", return_value=Completed()) \
            as run, patch(
                "library.tools.master_loudness.master_render_report",
                return_value={"output_path": mastered,
                              "raw_output_path": raw}) as master:
        report = render_step._export_timeline(
            "Timeline_1", {"project_folder": str(project)},
            {"project": {"name": "Project"},
             "audio_mix": {"delivery_lufs_target": -16.0,
                           "delivery_true_peak_ceiling_dbtp": -1.0}})

    command = run.call_args.args[0]
    assert command[command.index("--output-dir") + 1] == scratch
    assert command[command.index("--name") + 1] == "Timeline_1_pre_master"
    master.assert_called_once_with(
        {"output_path": raw, "size_bytes": 3}, mastered,
        target_lufs=-16.0, true_peak_ceiling=-1.0)
    assert report["output_path"] == mastered
