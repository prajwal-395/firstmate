"""Offline proof for assignment review, scoped variants and recovery."""
from __future__ import annotations

import copy

import pytest

from library.tools import assignment_revision as revision


def _patch(project, generation, patch_id):
    return {
        "id": patch_id, "project": "Fixture Resolve", "timeline": "Draft",
        "base_generation": generation, "capability": "timeline.apply_patch",
        "affected_spans": [[0, 24]], "conflict_domains": ["timeline_structure"],
        "operations": [{"op": "clip.move", "unique_id": "clip-a",
                        "to_frame": 0}],
    }


def _review(tmp_path):
    return revision.capture_review(
        str(tmp_path), "assignment-1", "revision-1",
        note="The opening feels slow; try a tighter start.",
        current={"generation": 7, "label": "Current cut"},
        result={"generation": 7, "label": "Proposed cut"},
        creative_difference={"summary": "Shorter opening; argument unchanged",
                            "changed_categories": ["sequence"]},
        base_generation=7,
        variants=[
            {"name": "Tighter opening", "summary": "Remove the pause before the first line",
             "patch": _patch(str(tmp_path), 7, "tight")},
            {"name": "Keep the pause", "summary": "Retain the reflective beat",
             "patch": _patch(str(tmp_path), 7, "keep")},
        ])


def test_approved_variant_applies_only_selected_scoped_patch(tmp_path):
    review = _review(tmp_path)
    applied = []

    def writer(patch):
        applied.append(copy.deepcopy(patch))
        return {"status": "applied", "generation": 8, "patch_id": patch["id"]}

    result = revision.decide(str(tmp_path), "assignment-1", "revision-1",
                             action="approved", variant_name="Tighter opening",
                             current_generation=7, apply_patch=writer)
    assert review["note"].startswith("The opening feels slow")
    assert result["comparison"]["creative_difference"]["summary"] == (
        "Shorter opening; argument unchanged")
    assert result["status"] == "applied"
    assert result["selected_variant"] == "Tighter opening"
    assert [patch["id"] for patch in applied] == ["tight"]
    assert result["variants"]["Keep the pause"]["patch"]["id"] == "keep"
    assert result["recovery"]["action"] == "submit_inverse_edit_patch"
    rolled_back = revision.rollback(
        str(tmp_path), "assignment-1", "revision-1",
        inverse_patch=_patch(str(tmp_path), 8, "undo-tight"),
        current_generation=8, apply_patch=writer)
    assert rolled_back["status"] == "rolled_back"
    assert [patch["id"] for patch in applied] == ["tight", "undo-tight"]


def test_stale_revision_recovery_never_calls_patch_writer(tmp_path):
    _review(tmp_path)
    called = []
    result = revision.decide(str(tmp_path), "assignment-1", "revision-1",
                             action="approved", variant_name="Tighter opening",
                             current_generation=8,
                             apply_patch=lambda patch: called.append(patch))
    assert result["status"] == "stale"
    assert result["recovery"]["action"] == "rebase_or_replan"
    assert called == []
    assert revision.load_review(str(tmp_path), "assignment-1", "revision-1")["status"] == "stale"


def test_rejected_revision_is_durable_and_never_applied(tmp_path, monkeypatch):
    _review(tmp_path)
    monkeypatch.setattr(revision.review_gate, "save_gate_feedback",
                        lambda *args, **kwargs: tmp_path / "feedback.json")
    result = revision.decide(str(tmp_path), "assignment-1", "revision-1",
                             action="rejected")
    assert result["status"] == "rejected"
    assert result["recovery"]["action"] == "revise_from_feedback"
    assert revision.load_review(str(tmp_path), "assignment-1", "revision-1")["status"] == "rejected"


def test_revision_id_cannot_be_reused_for_a_different_note(tmp_path):
    _review(tmp_path)
    with pytest.raises(revision.RevisionRefused, match="different review content"):
        revision.capture_review(
            str(tmp_path), "assignment-1", "revision-1", note="Different note",
            current={}, result={}, creative_difference={}, base_generation=7,
            variants=[{"name": "One", "summary": "One", "patch": _patch(str(tmp_path), 7, "one")}])
