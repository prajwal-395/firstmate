"""Defect: the Project Model could not say what a timeline held.

The model's `timeline_states` row carried a generation number and no
composition - `clip_use_ids` and `edit_operation_ids` were empty - and
the model's `nle_bindings` collection was declared in the schema and
never committed.  So there was nothing to reconcile the live Resolve
timeline against, and no join between a model clip use and the Resolve
item that plays it.

`library/tools/timeline_state_reconcile.py` compares the two and commits
what the timeline holds.
"""

from __future__ import annotations

import pathlib

import pytest

from library.tools import project_model_store as store
from library.tools import reel_project_model as rpm
from library.tools import timeline_state_reconcile as reconcile
from library.tools.project_model import validate_project_model


def _model():
    model = rpm.blank_model("project-x")
    rpm.clip_uses_from_placements(
        model, "reel-01", "Reel 01", [{
            "source_file": "/m/a.MXF", "source_in": 10.0, "source_out": 15.0,
            "record_start": 0.0, "record_frame": 0, "record_frames": 120,
            "track_type": "video", "track_index": 1, "track_name": "A-Roll",
        }], {("video", 1): "a_roll"})
    return model


def _snapshot(unique_id="uid-live-1", source_file="/m/a.MXF",
              record_in=0, record_out=120):
    return {"tracks": [{"type": "video", "index": 1, "name": "V1", "clips": [
        {"unique_id": unique_id, "name": "Shot", "source_file": source_file,
         "record_in": record_in, "record_out": record_out,
         "track_type": "video", "track_index": 1}]}]}


@pytest.fixture()
def project(tmp_path):
    folder = tmp_path / "proj"
    folder.mkdir()
    revision = store.commit_project_model(
        folder, _model(), expected_revision=0, author="test", reason="seed")
    return folder, revision


# ── The comparison ──────────────────────────────────────────────────


def test_a_live_clip_joins_the_model_clip_use_that_plays_it():
    model = _model()
    joined = reconcile.join_live_clips(model, _snapshot(), "reel-01")
    assert joined["unbound"] == []
    assert list(joined["bound"].values()) == [
        model["clip_uses"][0]["id"]], (
        "the live clip plays the model's clip use, and the join is on the "
        "material and the extent")


def test_a_clip_the_model_does_not_describe_is_unbound():
    model = _model()
    joined = reconcile.join_live_clips(
        model, _snapshot(source_file="/m/other.MXF"), "reel-01")
    assert joined["bound"] == {}
    assert len(joined["unbound"]) == 1
    assert "asset" in joined["unbound"][0]["reason"]


def test_a_timeline_the_model_says_nothing_about_is_unbound():
    model = _model()
    report = reconcile.reconcile(model, snapshot=_snapshot(),
                                 timeline_id="reel-01")
    assert report["status"] == reconcile.UNBOUND, (
        "the model holds no composition for this timeline, so there is "
        "nothing to be in sync with")
    assert report["bound"], (
        "the live clip still joins the model's clip use; what the model "
        "does not have is the composition")


def test_a_live_clip_the_model_holds_no_use_for_is_drift():
    model = _model()
    model["clip_uses"] = []
    report = reconcile.reconcile(model, snapshot=_snapshot(),
                                 timeline_id="reel-01")
    assert report["status"] == reconcile.DRIFT
    assert report["unbound"], "the drift must name what differs"


def test_a_model_binding_to_a_clip_that_is_gone_is_drift():
    model = _model()
    model["nle_bindings"] = [{
        "id": "nle:old", "timeline_id": "reel-01", "system": "resolve",
        "project_identity": "Podcast", "timeline_identity": "Reel 01",
        "generation": 1,
        "clip_bindings": {model["clip_uses"][0]["id"]: "uid-deleted"},
        "external_bindings": {},
    }]
    report = reconcile.reconcile(model, snapshot=_snapshot(),
                                 timeline_id="reel-01")
    assert report["status"] == reconcile.DRIFT
    assert report["missing"], (
        "the model describes a clip the live timeline no longer holds")


def test_a_clock_with_no_frame_rate_is_unavailable_not_drift():
    """A comparison that cannot be made is not a disagreement."""
    model = _model()
    for clock in model["clocks"]:
        clock.pop("frame_rate", None)
    report = reconcile.reconcile(model, snapshot=_snapshot(),
                                 timeline_id="reel-01")
    assert report["status"] != reconcile.DRIFT, (
        "the model cannot convert the live frames to its own ticks, and "
        "that is not a disagreement")


# ── The commit ──────────────────────────────────────────────────────


def test_the_commit_records_what_the_timeline_holds(project):
    folder, revision = project
    result = reconcile.commit_reconciled_state(
        folder, snapshot=_snapshot(), timeline_id="reel-01", generation=1,
        project_identity="Podcast", timeline_identity="Reel 01",
        expected_revision=revision)
    assert result["status"] == "committed"
    _revision, model = store.load_project_model(folder)
    state = reconcile.timeline_state(model, "reel-01")
    assert state, "the timeline must have a state row"
    assert state["clip_use_ids"], (
        "a timeline state with no clip use ids says nothing about the "
        "timeline")
    assert state["generation"] == 1
    validate_project_model(model)


def test_the_commit_binds_a_clip_use_to_the_live_resolve_item(project):
    folder, revision = project
    reconcile.commit_reconciled_state(
        folder, snapshot=_snapshot(), timeline_id="reel-01", generation=1,
        project_identity="Podcast", timeline_identity="Reel 01",
        expected_revision=revision)
    _revision, model = store.load_project_model(folder)
    bindings = reconcile.bindings_for(model, "reel-01")
    assert len(bindings) == 1, (
        "the model's nle_bindings collection was declared and never "
        "committed; it is what joins a clip use to the Resolve item")
    binding = bindings[0]
    use_id = _model()["clip_uses"][0]["id"]
    assert binding["clip_bindings"] == {use_id: "uid-live-1"}
    assert binding["system"] == "resolve"
    assert binding["generation"] == 1


def test_the_reconcile_is_in_sync_once_the_composition_is_committed(project):
    folder, revision = project
    reconcile.commit_reconciled_state(
        folder, snapshot=_snapshot(), timeline_id="reel-01", generation=1,
        project_identity="Podcast", timeline_identity="Reel 01",
        expected_revision=revision)
    _revision, model = store.load_project_model(folder)
    report = reconcile.reconcile(model, snapshot=_snapshot(),
                                 timeline_id="reel-01")
    assert report["status"] == reconcile.IN_SYNC
    assert report["bound"], "the live clip is bound to its clip use"
    assert report["unbound"] == []
    assert report["missing"] == []


def test_the_commit_refuses_a_stale_revision(project):
    folder, revision = project
    with pytest.raises(reconcile.ReconcileRefused):
        reconcile.commit_reconciled_state(
            folder, snapshot=_snapshot(), timeline_id="reel-01", generation=1,
            project_identity="Podcast", timeline_identity="Reel 01",
            expected_revision=revision + 5)


def test_the_commit_leaves_another_timelines_rows_alone(project):
    folder, revision = project
    _revision, model = store.load_project_model(folder)
    model["timeline_states"] = [{
        "id": "timeline_state:other", "timeline_id": "reel-02",
        "generation": 4, "clip_use_ids": [], "edit_operation_ids": []}]
    revision = store.commit_project_model(
        folder, model, expected_revision=revision, author="test",
        reason="seed another reel")
    reconcile.commit_reconciled_state(
        folder, snapshot=_snapshot(), timeline_id="reel-01", generation=1,
        project_identity="Podcast", timeline_identity="Reel 01",
        expected_revision=revision)
    _revision, model = store.load_project_model(folder)
    other = reconcile.timeline_state(model, "reel-02")
    assert other is not None
    assert other["generation"] == 4
    validate_project_model(model)


def test_a_timeline_with_no_live_clip_records_no_binding(project):
    """An empty binding binds nothing, and the model says so."""
    folder, revision = project
    reconcile.commit_reconciled_state(
        folder, snapshot={"tracks": []}, timeline_id="reel-01",
        generation=1, project_identity="Podcast",
        timeline_identity="Reel 01", expected_revision=revision)
    _revision, model = store.load_project_model(folder)
    assert reconcile.bindings_for(model, "reel-01") == []
    state = reconcile.timeline_state(model, "reel-01")
    assert state is not None
    assert state["clip_use_ids"] == []
