"""Patch-in-place vs rebuild for a routed feedback note.

Evidence: Reel 13's rebuild wave (2026-10-06) - a caption-wording fix was
applied by rebuilding the whole reel from scratch and carrying the
captain's hand edits back on; the carry kept refusing (an unmappable
6-frame hand extension among others) with the guards correctly refusing
to overwrite his edits. A wording fix stays inside existing caption
cards - it is a patch, and the classifier must say so.
"""

from __future__ import annotations

import os
import sys
from dataclasses import asdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))

from library.tools import (
    feedback_patch,
    marker_routing,
)


def _raw(text: str, frame=712, source: str = "timeline_marker") -> dict:
    return {
        "source": source,
        "name": "feedback",
        "note": text,
        "text": f"feedback\n\n{text}",
        "frame": frame,
        "timecode": "00:00:00:00",
        "frame_in_timeline_space": frame,
        "unplaced_reason": "",
        "color": "Blue",
        "duration_frames": 1,
        "custom_data": {},
        "custom_data_raw": "",
        "clips": [],
        "attachments": [],
        "read_at": "",
    }


def _routed(text: str, **kw) -> dict:
    return asdict(marker_routing.route_note(_raw(text, **kw)))


# ── The first real case: a wording fix is a patch ────────────────────
#
# The captain's Reel 10 note ("2021 is a year, this needs to be fixed in
# the subtitles") routes to plan_subtitles. Today that reaches a planning
# prompt and a full rebuild-and-promote (`gather_step_inputs` hands
# `prompt_block` to every step declaring `timeline_notes`; nothing
# consults a patch verdict). The classifier must call it a patch.

def test_caption_wording_note_is_patch_not_rebuild():
    routed = _routed("2021 is a year, this needs to be fixed in the subtitles")
    assert routed["outcome"] == marker_routing.OUTCOME_ROUTED
    assert routed["steps"] == ["plan_subtitles"]
    verdict = feedback_patch.decide(routed)
    assert verdict["verdict"] == feedback_patch.VERDICT_PATCH
    assert "plan_subtitles" in verdict["reason"]


def test_caption_wording_reason_is_recorded():
    verdict = feedback_patch.decide(
        _routed("the subtitles are too small on this reel"))
    assert verdict["verdict"] == feedback_patch.VERDICT_PATCH
    assert verdict["reason"]  # the reason is the record - never empty


# ── A change to the plan's selection or order is a rebuild ───────────

def test_passage_order_note_is_rebuild():
    routed = _routed("cut this passage and move it earlier in the order")
    assert routed["outcome"] == marker_routing.OUTCOME_ROUTED
    assert routed["steps"] == ["speech_sequence"]
    verdict = feedback_patch.decide(routed)
    assert verdict == {
        "verdict": feedback_patch.VERDICT_REBUILD,
        "reason": verdict["reason"],
        "steps": ["speech_sequence"],
    }
    assert "selection or order" in verdict["reason"]


def test_broll_selection_note_is_rebuild():
    routed = _routed("this b-roll cover does not fit, use another cutaway")
    assert routed["steps"] == ["select_broll"]
    assert (feedback_patch.decide(routed)["verdict"]
            == feedback_patch.VERDICT_REBUILD)


# ── Anything unplaced or unrouted rebuilds (today's behaviour) ───────

def test_ambiguous_note_is_rebuild():
    routed = _routed("this zoom looks wrong here")
    assert routed["outcome"] == marker_routing.OUTCOME_AMBIGUOUS
    verdict = feedback_patch.decide(routed)
    assert verdict["verdict"] == feedback_patch.VERDICT_REBUILD
    assert "ambiguous" in verdict["reason"]


def test_unrouted_note_is_rebuild():
    routed = _routed("hello, just thinking aloud about lunch")
    assert routed["outcome"] == marker_routing.OUTCOME_UNROUTED
    assert (feedback_patch.decide(routed)["verdict"]
            == feedback_patch.VERDICT_REBUILD)


def test_unplaced_note_is_rebuild():
    routed = _routed("the subtitles are too small on this reel", frame=None)
    assert routed["frame"] is None
    verdict = feedback_patch.decide(routed)
    assert verdict["verdict"] == feedback_patch.VERDICT_REBUILD
    assert "no placed frame" in verdict["reason"]


# ── Typed edit specs decide by what their operations change ──────────

def _edit_spec_note(owners: list) -> dict:
    routed = _routed("step: plan_subtitles\nfix the caption cards")
    assert routed["basis"] == marker_routing.BASIS_DECLARED
    routed["basis"] = marker_routing.BASIS_EDIT_SPEC
    routed["evidence"] = {"edit_spec_ops": [
        {"op": "caption.fix", "owner": owner} for owner in owners]}
    routed["steps"] = list(dict.fromkeys(owners))
    routed["outcome"] = marker_routing.OUTCOME_ROUTED
    return routed


def test_edit_spec_inside_existing_items_is_patch():
    verdict = feedback_patch.decide(
        _edit_spec_note(["plan_subtitles", "audio_mix"]))
    assert verdict["verdict"] == feedback_patch.VERDICT_PATCH


def test_edit_spec_touching_selection_is_rebuild():
    verdict = feedback_patch.decide(
        _edit_spec_note(["plan_subtitles", "speech_sequence"]))
    assert verdict["verdict"] == feedback_patch.VERDICT_REBUILD
    assert "speech_sequence" in verdict["reason"]


# ── The tables cover every routable step, exactly once ───────────────

def test_every_routable_step_is_in_exactly_one_table():
    routable = set(marker_routing.BY_NODE_ID)
    assert (feedback_patch.PATCH_STEPS | feedback_patch.REBUILD_STEPS
            == routable)
    assert not (feedback_patch.PATCH_STEPS & feedback_patch.REBUILD_STEPS)


# ── A typed spec places itself; a program is a patch or refused ──────

def test_a_typed_chat_note_needs_no_marker_frame():
    """Catches: a note typed in chat (no marker, so no frame) always
    rebuilding, though its typed operations stay inside existing items."""
    routed = _edit_spec_note(["plan_subtitles"])
    routed["frame"] = None
    assert (feedback_patch.decide(routed)["verdict"]
            == feedback_patch.VERDICT_PATCH)


def _program_note(ops: list) -> dict:
    parameters = {
        "clip.set_property": {"unique_id": "clip-1", "key": "Pan",
                              "value": 1.0},
        "clip.set_enabled": {"unique_id": "clip-1", "enabled": False},
        "clip.delete": {"unique_id": "clip-1"},
    }
    return {"basis": marker_routing.BASIS_EDIT_SPEC,
            "outcome": marker_routing.OUTCOME_UNROUTED, "steps": [],
            "frame": None, "evidence": {"edit_spec_ops": [
                {"op": op, "owner": None, "ledger_op": "edit_program",
                 **parameters.get(op, {})}
                for op in ops]}}


def test_unqualified_program_is_refused_even_when_feedback_patch_can_restore():
    verdict = feedback_patch.decide(
        _program_note(["clip.set_property", "clip.set_enabled"]))
    assert verdict["verdict"] == feedback_patch.VERDICT_REFUSED
    assert "clip.set_enabled" in verdict["reason"]


def test_program_routing_requires_a_qualified_executor():
    """No compile-only backend can authorize a normal revise operation."""
    verdict = feedback_patch.decide(
        _program_note(["clip.set_property", "clip.delete"]))
    assert verdict["verdict"] == feedback_patch.VERDICT_REFUSED
    assert "clip.delete" in verdict["reason"]


def test_program_accepts_any_router_selected_backend(monkeypatch):
    """The old feedback-patch operation list no longer selects outcomes."""
    from types import SimpleNamespace

    from library.tools import execution_router

    routed = {}

    def route(compiled, *, context=None):
        routed["operations"] = compiled["operations"]
        routed["context"] = context
        return SimpleNamespace(as_dict=lambda: {"backend": "qualified_backend"})

    monkeypatch.setattr(execution_router, "route_compiled_edit", route)
    verdict = feedback_patch.decide(
        _program_note(["clip.delete", "clip.set_enabled"]))

    assert verdict["verdict"] == feedback_patch.VERDICT_PATCH
    assert verdict["execution"]["backend"] == "qualified_backend"
    assert routed["context"] == {"require_qualified": True}
    assert [op["op"] for op in routed["operations"]] == [
        "clip.delete", "clip.set_enabled"]
