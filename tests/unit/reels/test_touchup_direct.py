"""The touch path's direct route, and the staging it replaces.

The defect this names (PERF-002, 2026-10-21): a small touch-up - one
property on one item - spent 53.6 s of its 58.5 s wall clock on the
PROMOTION: the whole-reel bookkeeping a staged replacement runs
(two full timeline snapshots, the project-wide timeline inventory, the
marker fleet over every timeline, the rename, the journal close, the
retirement of the replaced generation).  The staging itself was ~1 s
of 58 - the staging was never the lever, the bookkeeping was.

So the touch path now asks the router whether the request is a
REVERSIBLE MUTATION with a proven direct executor, and takes the direct
route when it is: the write lands on the approved timeline through the
EditPatch, is read back, and is recorded as a generation whose inverse
restores the accepted generation.

The tests here are the DEFECTS this change could introduce, each named
for what it would break:

1. a property write the router proves direct must NOT stage;
2. a change the router cannot prove direct must still stage;
3. a change with an unattributed editor edit on the approved timeline
   must still stage - a direct write has no candidate to carry the
   editor's hand edit on, so it must refuse the whole request;
4. a caller may force the staged route, and forcing it must stage;
5. the direct route must leave a recovery: the journal that reverses it
   and the EditPatch generation that reverses it.
"""

from __future__ import annotations

import json
import sys
from contextlib import contextmanager
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from library.tools import (
    perf_ledger,
    touchup_phases,
)
from tests.composed_edit_harness import build_reel, ledger_lease

REEL = "Reel 01 - topic"

#: The runtime state the recorded `clip.set_property` qualification was
#: earned under.  The router compares the LIVE state against it, so a
#: test that does not measure one is refused - which is the point.
QUALIFIED_STATE = {
    "inspected": True,
    "state": {
        "resolve_version": "21.1.0.14",
        "media_type": "video",
        "clip_composition": "linked_video_clip",
        "linked_audio_configuration": "linked_audio_present",
        "track_layout": "video+audio",
        "track_occupancy": "all_populated",
        "timeline_density": "dense",
        "timeline_structure": "multi_item_timeline",
        "project_settings": "portrait_1080x1920",
        "frame_rate": "23.976",
        "existing_effects": "transforms+retiming",
    },
}


@contextmanager
def _sole_writer():
    from library.tools.resolve_lock import assume_sole_writer

    with assume_sole_writer("test: fake Resolve has no instance"):
        yield


def _rows(ledger_path):
    return [json.loads(line)
            for line in ledger_path.read_text(encoding="utf-8").splitlines()
            if line.strip()]


def _phase_rows(rows, target):
    return {row["touchup_phase"]: row for row in rows
            if row.get("layer", "").startswith("touchup_")
            and row.get("touchup_target") == target}


def _project(tmp_path, monkeypatch):
    """A project holding one built reel, with the proposal that names it."""
    from library.tools import reel_proposal, resolve_lock

    timeline, _pool, _media = build_reel(tmp_path)
    timeline.SetName(REEL)
    project = timeline._project
    project.SetCurrentTimeline(timeline)
    monkeypatch.setattr(resolve_lock, "under_lease", ledger_lease)
    monkeypatch.setattr(resolve_lock, "cursor_fence", _pass_fence)
    monkeypatch.setattr(resolve_lock, "cursor_excursion", _pass_excursion)
    _mock_edit_patch(monkeypatch)
    _mock_shadow(monkeypatch)

    folder = tmp_path / "project"
    folder.mkdir()
    (folder / "project.yaml").write_text(
        "name: Test Project\n"
        "slug: test-project\n"
        "resolve:\n"
        f"  project_name: {project.GetName()}\n",
        encoding="utf-8")
    proposal = reel_proposal.proposal_path(folder)
    proposal.parent.mkdir(parents=True, exist_ok=True)
    proposal.write_text(json.dumps({
        "format": reel_proposal.PROPOSAL_FORMAT,
        "moments": [{"number": 1, "slug": "topic", "reason": "the answer",
                     "timeline_start": 0.0, "timeline_end": 10.0,
                     "timeline_name": REEL, "approval": "approved"}],
    }), encoding="utf-8")
    return project, folder


class _Fence:
    def __init__(self):
        self.drift_seen = []


@contextmanager
def _pass_fence(project, timeline, purpose):
    project.SetCurrentTimeline(timeline)
    yield _Fence()


@contextmanager
def _pass_excursion(project, timeline, purpose):
    previous = project.GetCurrentTimeline()
    project.SetCurrentTimeline(timeline)
    try:
        yield
    finally:
        project.SetCurrentTimeline(previous)


def _mock_edit_patch(monkeypatch):
    from library.tools import edit_patch, reel_read

    def apply_live_patch(*, project, timeline, operations, **_kwargs):
        for operation in operations:
            matches = [item for row in reel_read.live_items(timeline)
                       for item in row["items"]
                       if item.GetUniqueId() == operation["unique_id"]]
            assert len(matches) == 1
            item = matches[0]
            if operation["op"] == "clip.set_property":
                item.SetProperty(operation["key"], operation["value"])
            else:
                raise AssertionError(operation)
        return {"status": "committed", "generation": 1,
                "patch_id": "patch_test"}

    monkeypatch.setattr(edit_patch, "apply_live_patch", apply_live_patch)


def _mock_shadow(monkeypatch):
    from library.tools import timeline_shadow

    class _Head:
        def summary(self):
            return {"generation": 1}

    monkeypatch.setattr(timeline_shadow, "observe",
                        lambda *a, **k: _Head())


def _mock_resolve_app(monkeypatch, project):
    """The Resolve app handle, for the one measurement a routing
    decision needs: the runtime state the qualification evidence was
    earned under.  Without it the router refuses, which is the
    fail-closed answer - never a live Resolve in a test."""
    from library.tools import reel_build
    from tests.resolve_double import FakeResolve

    monkeypatch.setattr(reel_build, "_connect_resolve",
                        lambda: FakeResolve(project))


def _mock_qualification(monkeypatch, state=None):
    """The measured runtime state the router checks the evidence against."""
    from library.tools import axi_qualification_state

    measured = state if state is not None else QUALIFIED_STATE
    monkeypatch.setattr(axi_qualification_state, "inspect",
                        lambda **_kwargs: measured)
    monkeypatch.setattr(axi_qualification_state, "inspect_handles",
                        lambda **_kwargs: measured)


def _touch(folder, project, spec, **kwargs):
    from library.tools import reel_touchup

    return reel_touchup.apply_touchup(str(folder), spec,
                                      connect=lambda _name: project,
                                      **kwargs)


def _property_spec(**overrides):
    spec = {"reel": 1, "edits": [
        {"op": "set_properties", "row": "V1", "item": 0,
         "properties": {"Tilt": -684.041}}]}
    spec.update(overrides)
    return spec


# ── 1. The direct route ─────────────────────────────────────────────


def test_a_property_write_the_router_proves_direct_does_not_stage(
        tmp_path, monkeypatch):
    """The defect: a one-property touch-up spent 53.6 s of 58.5 s on the
    promotion's whole-reel bookkeeping, because every touch-up staged a
    candidate and promoted it over the approved reel - even when the
    change was a reversible mutation the router can prove direct.

    The direct route writes the property on the APPROVED timeline, is
    read back, and leaves no staging behind.
    """

    project, folder = _project(tmp_path, monkeypatch)
    _mock_resolve_app(monkeypatch, project)
    _mock_qualification(monkeypatch)
    ledger_path = tmp_path / "perf_ledger.jsonl"

    with (perf_ledger.recording(str(ledger_path), "run-direct",
                                 "reel.touchup", required=True),
          _sole_writer()):
        receipt = _touch(folder, project, _property_spec())

    assert receipt["status"] == "applied_direct", receipt
    assert receipt["staging_created"] is False
    assert receipt["promoted"] is None
    assert receipt["direct"]["backend"] == "edit_patch.property"
    assert receipt["direct"]["mode"] == "direct"

    # The write landed on the APPROVED timeline, and the approved
    # timeline is still the only one: nothing was duplicated.
    rows = _phase_rows(_rows(ledger_path), "reel")
    assert set(rows) == {touchup_phases.DIRECT_PHASE}, (
        "a direct touch stages nothing, so it opens no staging phase: "
        f"{sorted(rows)}")


def test_the_direct_route_writes_the_property_on_the_approved_timeline(
        tmp_path, monkeypatch):
    """The direct route is not a cheaper promotion - it is a write on
    the approved timeline, and the property must be there when it ends."""
    from library.tools import reel_read

    project, folder = _project(tmp_path, monkeypatch)
    _mock_resolve_app(monkeypatch, project)
    _mock_qualification(monkeypatch)

    with _sole_writer():
        receipt = _touch(folder, project, _property_spec())

    approved = [t for t in project.timelines if t.GetName() == REEL]
    assert len(approved) == 1
    items = [item for row in reel_read.live_items(approved[0])
             for item in row["items"]]
    written = [item for item in items
               if abs(float(item.GetProperty("Tilt")) + 684.041) < 0.001]
    assert len(written) == 1, (
        "the property the request named is on the approved timeline")
    assert receipt["direct"]["targets"][0]["row"] == "V1"


# ── 2. What may NOT go direct ────────────────────────────────────────


def test_a_change_the_router_cannot_prove_direct_still_stages(
        tmp_path, monkeypatch):
    """The direct route is the router's decision, not the touch path's
    preference: an unmeasured runtime state is a refusal, and the
    request stages exactly as it always did."""
    project, folder = _project(tmp_path, monkeypatch)
    _mock_resolve_app(monkeypatch, project)
    _mock_qualification(monkeypatch, state={
        "inspected": False,
        "reason": "the runtime was not inspected"})

    with _sole_writer():
        receipt = _touch(folder, project, _property_spec())

    assert receipt.get("status") != "applied_direct"
    assert receipt["promoted"], receipt
    assert "staged" in receipt["direct_route"]


def test_a_structural_change_never_goes_direct(tmp_path, monkeypatch):
    """A change that adds, removes or moves an item is not a property
    write: the staged route protects against a lost row and a wrong
    target, and the direct route is not asked."""
    from library.tools import reel_touchup

    project, folder = _project(tmp_path, monkeypatch)
    _mock_resolve_app(monkeypatch, project)
    _mock_qualification(monkeypatch)

    spec = {"reel": 1, "edits": [
        {"op": "move", "row": "V1", "item": 0, "to_row": "V1",
         "to_index": 1}]}
    with _sole_writer():
        try:
            receipt = _touch(folder, project, spec)
        except reel_touchup.TouchupRefused:
            return
    assert receipt.get("status") != "applied_direct", (
        "a structural change staged, and the direct route must never "
        "serve one")


def test_an_unattributed_editor_change_still_stages(tmp_path, monkeypatch):
    """The accepted edits are the reason the staging exists.  A direct
    write has no candidate to carry the editor's hand edit on, so it
    must decline the whole request and let the staged route decide -
    with the accepted-edit vocabulary it already has."""
    from library.tools import reel_replace_guard

    project, folder = _project(tmp_path, monkeypatch)
    _mock_resolve_app(monkeypatch, project)
    _mock_qualification(monkeypatch)

    real = reel_replace_guard.detect_editor_changes

    def with_pending(project_folder, final, live, staged_initial):
        detection = real(project_folder, final, live, staged_initial)
        detection["pending"] = [{"id": "pending-1", "status": "pending",
                                 "before_snapshot": live,
                                 "after_snapshot": live,
                                 "changes": []}]
        return detection

    monkeypatch.setattr(reel_replace_guard, "detect_editor_changes",
                        with_pending)

    with _sole_writer():
        receipt = _touch(folder, project, _property_spec())

    assert receipt.get("status") != "applied_direct", (
        "the direct route wrote over a timeline that carries the "
        "editor's own edit")
    assert "editor change" in receipt["direct_route"]
    assert receipt["promoted"], receipt


# ── 3. The caller may force the staged route ─────────────────────────


def test_the_caller_can_force_the_staged_route(tmp_path, monkeypatch):
    """The staged route is the safe one, so a caller may ask for it
    even when the direct route is proven.  The flag can only escalate
    to staged - it can never widen a request to a direct write."""
    project, folder = _project(tmp_path, monkeypatch)
    _mock_resolve_app(monkeypatch, project)
    _mock_qualification(monkeypatch)

    with _sole_writer():
        receipt = _touch(folder, project, _property_spec(),
                         staged_only=True)

    assert receipt.get("status") != "applied_direct"
    assert receipt["promoted"], receipt


# ── 4. The recovery ─────────────────────────────────────────────────


def test_the_direct_route_leaves_a_way_back(tmp_path, monkeypatch):
    """A direct write is still undoable: the touch's journal holds the
    state before and after it, the version it produced is on the undo
    stack, and the EditPatch's generation is the second way back."""
    from library.tools import undo_journal

    project, folder = _project(tmp_path, monkeypatch)
    _mock_resolve_app(monkeypatch, project)
    _mock_qualification(monkeypatch)

    with _sole_writer():
        receipt = _touch(folder, project, _property_spec())

    assert receipt["journal"], receipt
    entry = undo_journal.read_entry(str(folder), receipt["journal"])
    assert entry["status"] == undo_journal.STATUS_APPLIED
    assert entry["before"]["tracks"], "the journal holds the state before"
    assert entry["after"]["tracks"], "and the state after"
    assert receipt["version"], "the touch produced a version"
    assert receipt["recovery"]["act"].startswith("patch:")


def test_the_undo_stack_offers_the_direct_touch(tmp_path, monkeypatch):
    """`ren undo` reads the acts off the stack.  A direct touch that
    recorded no undoable act would be a write nobody can reverse."""
    from library.tools import undo_journal

    project, folder = _project(tmp_path, monkeypatch)
    _mock_resolve_app(monkeypatch, project)
    _mock_qualification(monkeypatch)

    with _sole_writer():
        _touch(folder, project, _property_spec())

    stack = undo_journal.undo_stack(str(folder))
    assert stack, (
        "the direct touch left no act on the undo stack: it wrote on "
        "the approved timeline and nobody can reverse it")


@pytest.mark.parametrize("phase", ["duplicate", "conform_compose_verify",
                                   "promote"])
def test_the_staged_phases_still_exist_for_a_staged_touch(
        tmp_path, monkeypatch, phase):
    """The staging is not removed: a change the router cannot prove
    direct still stages, and every phase of the staged route is still
    measured."""
    project, folder = _project(tmp_path, monkeypatch)
    _mock_resolve_app(monkeypatch, project)
    _mock_qualification(monkeypatch, state={
        "inspected": False, "reason": "not inspected"})
    ledger_path = tmp_path / "perf_ledger.jsonl"

    with (perf_ledger.recording(str(ledger_path), "run-staged",
                                 "reel.touchup", required=True),
          _sole_writer()):
        receipt = _touch(folder, project, _property_spec())

    assert receipt["promoted"], receipt
    rows = _phase_rows(_rows(ledger_path), "reel")
    assert phase in rows, (
        f"the {phase} phase is still measured: {sorted(rows)}")
