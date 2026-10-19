"""Violation tests for the editing invariants in the VC upgrade plan.

These exercise the public edit boundary with the canonical Resolve double.
Each test names the failure it is intended to prevent.
"""

from __future__ import annotations

import pytest

from library.tools import edit_patch
from library.tools import timeline_shadow as shadow
from library.tools.resolve_lock import assume_sole_writer
from tests.resolve_double import (
    FakeResolve,
    FakeTimeline,
    make_pool_clip,
    make_project,
    place_clip,
)


@pytest.fixture
def edit_world(tmp_path):
    project = make_project("VC Invariant Project")
    timeline = project.adopt(FakeTimeline("VC Reel", project=project))
    item = place_clip(timeline, make_pool_clip("shot.mov"), 0, 47)
    project.SetCurrentTimeline(timeline)
    store = shadow.ShadowStore(tmp_path / "timeline-shadow.sqlite3")
    with assume_sole_writer("VC invariant Resolve double"):
        base = shadow.observe(project, timeline, store)
        yield project, timeline, item, store, base


def _patch(base, operations, *, project="VC Invariant Project",
           timeline="VC Reel", patch_id="invariant-patch"):
    return {
        "id": patch_id,
        "project": project,
        "timeline": timeline,
        "base_generation": base.generation,
        "capability": "reel.touchup",
        "affected_spans": [[0, 48]],
        "conflict_domains": ["markers"],
        "operations": operations,
    }


def _apply(patch, project, timeline, store, tmp_path):
    return edit_patch.apply_patch(
        patch, resolve=FakeResolve(project), project=project,
        timeline=timeline, store=store, project_folder=str(tmp_path))


def test_failed_replacement_keeps_the_approved_timeline(edit_world):
    """Defect: a failed candidate swap removes the only approved cut."""
    from library.tools import reel_build

    project, timeline, item, _store, _base = edit_world
    timeline.SetName("Approved Reel")
    approved_id = timeline.GetUniqueId()
    original_enabled = item.GetClipEnabled()

    # A missing candidate is a failed replacement request. The live
    # approved timeline must remain identifiable and playable.
    with pytest.raises(reel_build.ReelBuildError,
                       match="not a staging container"):
        reel_build.promote_staged_reels(
            str(edit_world[3].path.parent), "VC Invariant Project",
            "VC Master", {"Approved Reel": "Missing Candidate"},
            resolve=FakeResolve(project),
        )

    assert timeline.GetUniqueId() == approved_id
    assert item.GetClipEnabled() == original_enabled
    assert timeline.GetName() == "Approved Reel"


def test_qa01_reel09_unattributed_human_edit_is_not_silently_overwritten(
        edit_world, tmp_path):
    """Defect: a patch planned before a hand edit overwrites that edit."""
    from library.tools import reel_edit_history
    from library.tools.reel_read import rows_of
    from library.tools.versions.rounds import digest_rows

    project, timeline, _item, store, base = edit_world
    review_dir = tmp_path / "pipeline_output" / "review"
    baseline_rows_digest = digest_rows(rows_of(store.snapshot(base)))
    reel_edit_history.record_entry(
        str(review_dir), "VC Reel", actor=reel_edit_history.ACTOR_REN,
        act="build", summary="recorded base",
        refs={"rows_digest_after": baseline_rows_digest})
    timeline.AddMarker(30, "Red", "captain's edit", "keep", 1)
    patch = _patch(base, [{
        "op": "marker.add", "frame": 5, "color": "Blue", "name": "Ren",
    }])

    with pytest.raises(edit_patch.StalePatch) as refused:
        _apply(patch, project, timeline, store, tmp_path)

    assert "unrecorded Resolve edit" in str(refused.value)
    assert refused.value.manual_edit_recorded
    assert "intent is unknown" in refused.value.user_message
    assert 30 in timeline.GetMarkers()
    assert 5 not in timeline.GetMarkers()
    assert store.head("VC Invariant Project", base.timeline_id).source == shadow.OBSERVED
    edits = [entry for entry in reel_edit_history.recorded_history(
        str(tmp_path / "pipeline_output" / "review"), "VC Reel")
             if entry["act"] == reel_edit_history.ACT_MANUAL_EDIT]
    assert len(edits) == 1
    assert "intent unknown" in edits[0]["summary"]
    assert store.head("VC Invariant Project", base.timeline_id).hash in \
        edits[0]["summary"]


def test_edit_context_names_exact_target_generation_and_effect(
        edit_world, tmp_path):
    """Defect: an edit has no auditable target, base, scope, or effect."""
    project, timeline, _item, store, base = edit_world
    wrong_target = _patch(
        base, [{"op": "marker.add", "frame": 5, "color": "Blue",
                "name": "Ren"}], timeline="VC Reel (staging)")

    with pytest.raises(edit_patch.PatchRefused, match="EXACT name"):
        _apply(wrong_target, project, timeline, store, tmp_path)

    assert not timeline.GetMarkers()
    assert store.head("VC Invariant Project", base.timeline_id).generation == base.generation


def test_failed_edit_records_observed_partial_state_and_stopped_operation(
        edit_world, tmp_path, monkeypatch):
    """Defect: a failed multi-op edit disappears or reports full success."""
    project, timeline, item, store, base = edit_world
    original = item.SetProperty

    def fail_pan(key, value):
        if key == "Pan":
            raise RuntimeError("injected Resolve write failure")
        return original(key, value)

    item.SetProperty = fail_pan
    patch = {
        "id": "partial-state",
        "project": "VC Invariant Project",
        "timeline": "VC Reel",
        "base_generation": base.generation,
        "capability": "reel.touchup",
        "affected_spans": [[0, 48]],
        "conflict_domains": ["markers", "picture_transform"],
        "operations": [
            {"op": "marker.add", "frame": 8, "color": "Blue", "name": "kept"},
            {"op": "clip.set_property", "unique_id": item.GetUniqueId(),
             "key": "Pan", "value": 12},
        ],
    }

    receipt = _apply(patch, project, timeline, store, tmp_path)

    assert receipt["status"] == "verification_failed"
    assert receipt["stopped"]["index"] == 1
    assert receipt["stopped"]["op"] == "clip.set_property"
    assert 8 in timeline.GetMarkers()
    head = store.head("VC Invariant Project", base.timeline_id)
    assert head.source == shadow.PATCH
    assert head.receipt["status"] == "verification_failed"


def test_version_identity_survives_rename_and_duplicate_names(
        edit_world, tmp_path):
    """Defect: rename or duplicate display names merge distinct histories."""
    project, timeline, _item, store, base = edit_world
    timeline.SetName("VC Reel (renamed)")
    renamed = shadow.observe(project, timeline, store)
    duplicate = FakeTimeline("VC Reel (renamed)", project=project)
    project.adopt(duplicate)
    project.SetCurrentTimeline(duplicate)
    second = shadow.observe(project, duplicate, store)

    assert renamed.timeline_id == base.timeline_id
    assert second.timeline_id != renamed.timeline_id
    assert store.head("VC Invariant Project", renamed.timeline_id).timeline_name == "VC Reel (renamed)"
    assert store.head("VC Invariant Project", second.timeline_id).timeline_name == "VC Reel (renamed)"


def test_promotion_accounts_for_native_markers_it_cannot_carry(edit_world):
    """Defect: replacing a reel silently discards a marker with no anchor."""
    from library.tools import marker_carry

    _project, _timeline, _item, _store, _base = edit_world
    replacement = FakeTimeline("VC replacement")
    carried, uncarried = marker_carry.plan_carry(
        [{"frame": 80, "color": "Blue", "name": "captain note",
          "note": "preserve this", "duration": 1, "custom_data": "",
          "anchor": None}],
        replacement, "VC replacement")

    assert carried == []
    assert uncarried[0]["name"] == "captain note"
    assert "nothing was playing" in uncarried[0]["why"]


def test_receipt_does_not_call_a_partial_patch_fully_applied(
        edit_world, tmp_path):
    """Defect: generic receipt standardization hides a stopped operation."""
    from library.tools import transaction_receipt

    project, timeline, item, store, base = edit_world
    original = item.SetProperty

    def fail_pan(key, value):
        if key == "Pan":
            raise RuntimeError("injected Resolve write failure")
        return original(key, value)

    item.SetProperty = fail_pan
    patch = {
        "id": "partial-standardized",
        "project": "VC Invariant Project",
        "timeline": "VC Reel",
        "base_generation": base.generation,
        "capability": "reel.touchup",
        "affected_spans": [[0, 48]],
        "conflict_domains": ["markers", "picture_transform"],
        "operations": [
            {"op": "marker.add", "frame": 8, "color": "Blue", "name": "kept"},
            {"op": "clip.set_property", "unique_id": item.GetUniqueId(),
             "key": "Pan", "value": 12},
        ],
    }
    actual = _apply(patch, project, timeline, store, tmp_path)
    standardized = transaction_receipt.standardize(actual)

    assert standardized["status"] == "verification_failed"
    assert standardized["applied"]["status"] == "partial"
    assert standardized["applied"]["operations"] == 1
