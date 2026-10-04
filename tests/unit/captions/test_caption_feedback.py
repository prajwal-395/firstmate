"""Caption notes are grounded in the same timed speech as the plan."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from library.tools import caption_feedback


def _fixture(spoken="the link's bio", caption="the link's bio",
             note_text=None):
    words = spoken.split()
    timed = [{"word": word, "source_start": 10.0 + index * 0.25,
              "source_end": 10.2 + index * 0.25}
             for index, word in enumerate(words)]
    spine = {"structure": [{
        "position": 7,
        "block_type": "speech",
        "clip_id": "clip_001",
        "source_start": 10.0,
        "source_end": 10.0 + len(words) * 0.25,
        "word_timestamps": timed,
    }]}
    plan = {"subtitle_entries": [{
        "entry_id": "caption-7-1",
        "text": caption,
        "spine_block_position": 7,
        "timeline_start": 1.0,
        "timeline_end": 2.0,
    }], "total_subtitles": 1}
    note = {
        "note_id": "timeline:Reel 01:marker:1247",
        "typed": (note_text or
                  'subtitle says "the link\'s bio" instead of '
                  '"the link\'s in our bio"'),
        "timeline": "Reel 01",
        "at_timecode": "00:00:41:17",
        "typed_operations": [],
    }
    notes = {"notes": [note]}
    context = caption_feedback.build_context(notes, plan, spine)
    return spine, plan, context


def test_context_pairs_quoted_note_phrases_with_the_matching_timed_block():
    _spine, _plan, context = _fixture()

    [note] = context["notes"]
    assert note["note_id"] == "timeline:Reel 01:marker:1247"
    assert len(note["matches"]) == 1
    [match] = note["matches"]
    assert match["spoken_text"] == "the link's bio"
    assert match["word_timestamps"][1]["source_start"] == 10.25
    assert match["caption_entries"][0]["text"] == "the link's bio"


def test_missing_words_are_reported_for_upstream_transcript_reindex(tmp_path):
    _spine, plan, context = _fixture()
    result = caption_feedback.resolve_feedback({
        "caption_feedback_context": context,
        "subtitle_plan": plan,
        "caption_feedback": [{
            "note_id": "timeline:Reel 01:marker:1247",
            "action": "upstream_transcript_needed",
            "requested_phrase": "the link's in our bio",
            "reason": "The requested words are absent from the timed speech.",
        }],
        "project_folder": str(tmp_path),
    })

    assert result["caption_feedback_report"]["notes"][0]["outcome"] == \
        "upstream_transcript_needed"
    assert result["subtitle_plan"] == plan
    from library.tools.edit_ledger import load_rows
    assert load_rows(str(tmp_path)) == []


def test_supported_caption_fix_is_recorded_and_applied(tmp_path):
    from library.tools.edit_ledger import load_rows
    from library.tools.project_layout import ProjectLayout

    project = tmp_path / "project"
    ProjectLayout(str(project)).ensure()
    _spine, plan, context = _fixture(
        "seo and geo", "seo and geo", "capitalize SEO and GEO")
    result = caption_feedback.resolve_feedback({
        "caption_feedback_context": context,
        "subtitle_plan": plan,
        "caption_feedback": [{
            "note_id": "timeline:Reel 01:marker:1247",
            "action": "caption_fix",
            "anchor_phrase": "seo and geo",
            "replacement": "SEO and GEO",
            "reason": "The caption should preserve the spoken acronym casing.",
        }],
        "project_folder": str(project),
    })

    assert result["subtitle_plan"]["subtitle_entries"][0]["text"] == \
        "SEO and GEO"
    assert result["caption_feedback_report"]["notes"][0]["outcome"] == \
        "applied"
    [row] = load_rows(str(project))
    assert row["op"] == "caption_fix"
    assert row["source_note_id"] == "timeline:Reel 01:marker:1247"


def test_already_correct_requires_the_phrase_in_speech_and_caption():
    _spine, plan, context = _fixture()
    with pytest.raises(caption_feedback.CaptionFeedbackError,
                       match="both the timed speech and current caption"):
        caption_feedback.resolve_feedback({
            "caption_feedback_context": context,
            "subtitle_plan": plan,
            "caption_feedback": [{
                "note_id": "timeline:Reel 01:marker:1247",
                "action": "already_correct",
                "requested_phrase": "the link's in our bio",
                "reason": "It looks correct.",
            }],
        })


def test_every_routed_note_must_receive_exactly_one_answer():
    _spine, plan, context = _fixture()
    with pytest.raises(caption_feedback.CaptionFeedbackError,
                       match="coverage differs"):
        caption_feedback.resolve_feedback({
            "caption_feedback_context": context,
            "subtitle_plan": plan,
            "caption_feedback": [],
        })


def test_subtitle_feedback_is_a_hybrid_prompt_step_with_declared_notes():
    from library.processes.edit_video.run_pipeline import get_step_implementation

    step_dir = Path(__file__).resolve().parents[3] / "library" / "steps" / \
        "step_4_01_plan_subtitles"
    manifest = json.loads((step_dir / "manifest.json").read_text(
        encoding="utf-8"))
    assert any(item["name"] == "timeline_notes"
               for item in manifest["interface"]["inputs"])
    assert any(item["name"] == "caption_feedback"
               for item in manifest["interface"]["llm_outputs"])
    assert get_step_implementation(step_dir)["type"] == "hybrid"


def test_no_caption_notes_stand_down_the_model_call(monkeypatch):
    from library.steps.step_4_01_plan_subtitles import bridge

    plan = {"subtitle_entries": [], "total_subtitles": 0}
    monkeypatch.setattr(
        bridge, "generate_subtitles", lambda *args, **kwargs: {
            "subtitle_plan": plan})
    result = bridge.build_context({
        "audio_spine": {"structure": []},
        "timeline_notes": {"notes": []},
    })

    assert result["subtitle_plan"] is plan
    assert result["caption_feedback_context"]["notes"] == []
    assert "no timeline notes" in result["nothing_to_decide"]
