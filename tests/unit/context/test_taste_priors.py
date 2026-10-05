"""A stated editorial preference reaches the decision record.

The mechanism (`library/tools/decided_value.py`) shipped with one slot -
the mix separation - and no slot for pacing, transition density, B-roll
density, caption aggression, music prominence or SFX density.  A captain
who said "I prefer faster cuts" or "keep captions minimal" had nowhere to
record it, so the preference could not reach any decision.

What is pinned here is the defect that fix prevents, per slot:

* a stated preference (the user's taste profile, or the project's own
  `creative_preferences`) is the STATED rung - the top of the ladder -
  and the decision record names the profile path and the `stated_by`;
* the project's `creative_preferences` outranks the user's profile, the
  way `decided_value.stated_preference` reads them;
* where the decided value shapes the output, it does: a stated word
  ceiling becomes the caption grouping's ceiling, and a stated shot
  length becomes the target a feel-word pacing window is measured
  against.

No test here asserts a registry, a schema or a count.  Each names the
defect - a preference that could not be recorded, or a recorded
preference that did not reach the value it was stated for.
"""
from __future__ import annotations

import json
import stat

import pytest

from library.tools import decided_value as dv
from library.tools import taste_profile as tp


def _user_profile_path(tmp_path, monkeypatch):
    """Isolate the per-user taste profile so a test never touches a real one."""
    config = tmp_path / "user-home" / ".config" / "ren" / "config.env"
    monkeypatch.setenv("REN_CONFIG", str(config))
    return config.parent / tp.PROFILE_FILENAME


def _record(profile, key, value, *, stated_by="Prajwal",
            reason="a preference I stated"):
    return tp.set_preference(key, "", value, stated_by=stated_by,
                             reason=reason, path=profile)


# ── The caption slot, end to end (the fidelity test) ──────────────────

def test_a_stated_caption_preference_reaches_the_plan_and_the_record(
        tmp_path, monkeypatch):
    """The fidelity test: "I prefer captions under 8 words" becomes the
    plan's word ceiling, and the decision record names the profile and
    the `stated_by`."""
    from library.steps.step_4_01_plan_subtitles.bridge import build_context

    profile = _user_profile_path(tmp_path, monkeypatch)
    _record(profile, "subtitle.max_words_per_card", 8,
            reason="I prefer captions under 8 words")
    project = tmp_path / "project"
    project.mkdir()

    words = [{"word": f"w{i}", "source_start": i * 0.25,
              "source_end": i * 0.25 + 0.2} for i in range(8)]
    data = {
        "audio_spine": {"structure": [{
            "position": 1, "block_type": "speech", "clip_id": "clip_001",
            "source_start": 0.0, "source_end": 2.0,
            "timeline_start": 0.0, "timeline_end": 2.0,
            "word_timestamps": words,
            "content": {"text": " ".join(w["word"] for w in words)},
        }]},
        "project_folder": str(project),
        "timeline_notes": {},
    }
    output = build_context(data)
    plan = output["subtitle_plan"]

    assert plan["max_words"] == 8
    record = plan["value_decisions"][0]
    assert record["slot"] == "subtitle.max_words_per_card"
    assert record["basis"] == dv.STATED
    assert record["value"] == 8
    assert str(profile) in record["source"]
    assert "stated by Prajwal" in record["source"]
    assert "under 8 words" in record["source"]


def test_the_project_s_own_preference_outranks_the_user_profile(
        tmp_path, monkeypatch):
    """The project's `creative_preferences` is the more specific
    declaration; it wins over the user's profile, and the record says so."""
    from library.steps.step_4_01_plan_subtitles.bridge import build_context

    profile = _user_profile_path(tmp_path, monkeypatch)
    _record(profile, "subtitle.max_words_per_card", 8)
    project = tmp_path / "project"
    project.mkdir()
    (project / "project.yaml").write_text(
        "pipeline:\n"
        "  creative_preferences:\n"
        "    subtitle:\n"
        "      max_words_per_card: 5\n", encoding="utf-8")

    words = [{"word": f"w{i}", "source_start": i * 0.25,
              "source_end": i * 0.25 + 0.2} for i in range(8)]
    data = {
        "audio_spine": {"structure": [{
            "position": 1, "block_type": "speech", "clip_id": "clip_001",
            "source_start": 0.0, "source_end": 2.0,
            "timeline_start": 0.0, "timeline_end": 2.0,
            "word_timestamps": words,
            "content": {"text": " ".join(w["word"] for w in words)},
        }]},
        "project_folder": str(project),
        "timeline_notes": {},
    }
    plan = build_context(data)["subtitle_plan"]

    assert plan["max_words"] == 5
    record = plan["value_decisions"][0]
    assert record["basis"] == dv.STATED
    assert "creative_preferences" in record["source"]
    assert str(profile) not in record["source"]


# ── Each slot's stated preference reaches the decision record ──────────

@pytest.mark.parametrize("key,value", [
    ("pacing.target_asl_seconds", 2.5),
    ("transition.density_per_minute", 1.5),
    ("broll.density_per_minute", 20.0),
    ("music.bed_level_lufs", -16.0),
    ("sfx.density_per_minute", 4.0),
])
def test_a_stated_preference_reaches_the_decision_record(
        tmp_path, monkeypatch, key, value):
    """Every editorial slot carries a stated preference as the STATED
    rung, and the record names the profile path and the `stated_by`."""
    profile = _user_profile_path(tmp_path, monkeypatch)
    _record(profile, key, value, reason="I stated this across projects")
    project = tmp_path / "project"
    project.mkdir()

    decision = dv.decide(key, project_folder=str(project))

    assert decision.basis == dv.STATED
    assert decision.value == value
    assert str(profile) in decision.source
    assert "stated by Prajwal" in decision.source
    assert "across projects" in decision.source
    # The record a reader sees carries the same source.
    record = decision.as_record()
    assert str(profile) in record["source"]


# ── Where the decided value shapes the output ─────────────────────────

def test_a_stated_shot_length_becomes_the_feel_windows_target(
        tmp_path, monkeypatch):
    """A feel-word pacing window ("faster cuts as it builds") is measured
    against the shot length the user stated, so the preference reaches
    the windows the model expressed as a feel."""
    from library.steps.step_2_05_mesh_spine.post_bridge import _resolve_pacing

    profile = _user_profile_path(tmp_path, monkeypatch)
    _record(profile, "pacing.target_asl_seconds", 2.5,
            reason="I prefer faster cuts")
    project = tmp_path / "project"
    project.mkdir()

    decision = dv.decide("pacing.target_asl_seconds",
                         project_folder=str(project))
    blocks = [{"position": 1}, {"position": 2}]
    windows = _resolve_pacing(
        [{"start_block": 1, "end_block": 2, "feel": "accelerating"}],
        blocks, default_asl=decision.value)

    assert windows[0]["asl_seconds"] == 2.5
    assert windows[0]["feel"] == "accelerating"


def test_a_stated_preference_the_model_also_named_still_records_stated(
        tmp_path, monkeypatch):
    """The STATED rung is the top of the ladder: even when the model
    answered the same slot, the preference is what decided it.  The
    model's answer is not read at all - the ladder returns at STATED -
    so the record carries no REASONED row to mistake for the decision."""
    profile = _user_profile_path(tmp_path, monkeypatch)
    _record(profile, "sfx.density_per_minute", 4.0)
    project = tmp_path / "project"
    project.mkdir()

    decision = dv.decide(
        "sfx.density_per_minute", project_folder=str(project),
        model_answer={"value": 9.0, "why": "the piece is busy"})

    assert decision.basis == dv.STATED
    assert decision.value == 4.0
    assert decision.answer == 4.0
    assert [r["basis"] for r in decision.rungs_skipped] == []


def test_a_slot_with_no_stated_preference_falls_to_the_model_then_fallback(
        tmp_path, monkeypatch):
    """With no preference stated, the model's answer decides; with
    neither, the registered fallback does - and each says which it is."""
    profile = _user_profile_path(tmp_path, monkeypatch)
    project = tmp_path / "project"
    project.mkdir()

    reasoned = dv.decide(
        "transition.density_per_minute", project_folder=str(project),
        model_answer={"value": 3.0, "why": "the cuts are the story"})
    assert reasoned.basis == dv.REASONED
    assert reasoned.value == 3.0

    fallback = dv.decide("transition.density_per_minute",
                         project_folder=str(project))
    assert fallback.basis == dv.FALLBACK
    assert fallback.value == 2.0
    assert "daily videos" in fallback.source
