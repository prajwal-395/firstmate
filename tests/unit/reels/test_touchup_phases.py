"""The touch path's staging cost is three measured phases.

The defect this names (scout, 2026-10-21): a touch-up's staging cost
was ONE unlabelled `resolve_hold` span - 55.7-57.4 s measured on the
Ren-owned scratch copy - so a profile could say what a touch cost and
could not say WHICH of the duplicate, the conform/compose/verify and
the promotion to attack.  The largest single lever in the touch path
was completely invisible.

The three phases are recorded as their own ledger rows, INSIDE the
lease they came out of, so the split moves no seconds: the phases plus
the hold's own remainder is the hold the profile showed before.
"""

from __future__ import annotations

import functools
import json
import sys
import threading
import time
from contextlib import contextmanager
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from library.tools import (
    perf_ledger,  # noqa: E402
    touchup_phases,  # noqa: E402
)
from tests.composed_edit_harness import build_reel  # noqa: E402

MASTER = "Main Edit"
REEL = "Reel 01 - topic"

#: How deep the fake lease is: the real one is reentrant.
_LEASE_DEPTH = threading.local()


# ── The lease, as `resolve_lock` records it ─────────────────────────


def _ledger_lease(purpose, **_kwargs):
    """The instance lease, with the `resolve_hold` span it records.

    The real lease opens a `resolve_hold` span around the whole
    critical section and reports how long it held (`held_s`).  It is
    REENTRANT: a section the touch opens inside its own hold takes no
    second hold, so the touch's staging cost is ONE hold's seconds -
    which is what the split has to account for.  The fake does the
    same, so a test can ask the question the fix lane asks: do the
    three phases still account for the hold, or did the split invent
    seconds?
    """

    def decorator(function):
        @functools.wraps(function)
        def guarded(*args, **kwargs):
            depth = getattr(_LEASE_DEPTH, "depth", 0)
            _LEASE_DEPTH.depth = depth + 1
            try:
                if depth:
                    return function(*args, **kwargs)
                with perf_ledger.span(perf_ledger.RESOLVE_HOLD,
                                      purpose=purpose) as held:
                    granted = time.time()
                    try:
                        return function(*args, **kwargs)
                    finally:
                        held["held_s"] = round(time.time() - granted, 3)
            finally:
                _LEASE_DEPTH.depth = depth

        return guarded

    return decorator


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
            if operation["op"] == "clip.delete":
                timeline.DeleteClips([item])
            elif operation["op"] == "clip.set_property":
                item.SetProperty(operation["key"], operation["value"])
            else:
                raise AssertionError(operation)
        return {"status": "committed", "generation": 1}

    monkeypatch.setattr(edit_patch, "apply_live_patch", apply_live_patch)


def _mock_shadow(monkeypatch):
    from library.tools import timeline_shadow

    class _Head:
        def summary(self):
            return {"generation": 1}

    monkeypatch.setattr(timeline_shadow, "observe",
                        lambda *a, **k: _Head())


def _rows(ledger_path):
    return [json.loads(line)
            for line in ledger_path.read_text(encoding="utf-8").splitlines()
            if line.strip()]


def _phase_rows(rows, target):
    return {row["touchup_phase"]: row for row in rows
            if row.get("layer", "").startswith("touchup_")
            and row.get("touchup_target") == target}


# ── The phases themselves ───────────────────────────────────────────


def test_a_phase_that_never_happened_reports_no_seconds():
    """A phase that never ran has no row: the ledger says what was
    measured, and a phase that did not happen was not measured."""
    clock = touchup_phases.PhaseClock("test")

    clock.open("duplicate")
    clock.close("duplicate")

    assert [row["phase"] for row in clock.rows] == ["duplicate"]


def test_a_phase_reports_its_own_seconds():
    """The seconds land in the phase that spent them."""
    clock = touchup_phases.PhaseClock("test")

    clock.open("duplicate")
    time.sleep(0.05)
    clock.close("duplicate")

    assert clock.rows[0]["wall_s"] >= 0.05


def test_opening_a_phase_closes_the_one_before_it():
    """The three phases are DISJOINT intervals of one lease: opening the
    promotion closes the conform/compose/verify, so no second is
    charged to two phases."""
    clock = touchup_phases.PhaseClock("test")

    clock.open("conform_compose_verify")
    clock.open("promote")
    clock.close("promote")

    phases = [row["phase"] for row in clock.rows]
    assert phases == ["conform_compose_verify", "promote"]
    first, second = clock.rows
    assert first["started_at"] + first["wall_s"] <= second["started_at"] + 1e-6


# ── The master touch, end to end against the fake Resolve ───────────


def _master_project(tmp_path, monkeypatch):
    from library.tools import resolve_lock

    timeline, _pool, _media = build_reel(tmp_path)
    timeline.SetName(MASTER)
    project = timeline._project
    project.SetCurrentTimeline(timeline)
    monkeypatch.setattr(resolve_lock, "under_lease", _ledger_lease)
    monkeypatch.setattr(resolve_lock, "cursor_fence", _pass_fence)
    monkeypatch.setattr(resolve_lock, "cursor_excursion", _pass_excursion)
    _mock_edit_patch(monkeypatch)
    _mock_shadow(monkeypatch)

    folder = tmp_path / "project"
    folder.mkdir()
    (folder / "project.yaml").write_text(
        "resolve:\n"
        f"  project_name: {project.GetName()}\n"
        f"  timeline_name: {MASTER}\n",
        encoding="utf-8")
    return project, folder


@contextmanager
def _sole_writer():
    """The fake Resolve has no instance to contend for.

    The real lease is REENTRANT: the sections the touch opens inside
    its own hold (the cursor fence, the marker reads, the inventory)
    take no second hold, so the touch's staging cost is ONE hold's
    seconds - which is what the split has to account for.
    """
    from library.tools.resolve_lock import assume_sole_writer

    with assume_sole_writer("test: fake Resolve has no instance"):
        yield


def test_the_master_touch_measures_three_phases_that_sum_to_its_hold(
        tmp_path, monkeypatch):
    """The staging cost is three numbers, and they are the hold's own.

    The defect this names: the touch's staging cost was one
    unlabelled `resolve_hold` span, so the profile could not say
    whether the duplicate, the conform/compose/verify or the promotion
    was the thing to attack - the largest single lever in the touch
    path, and invisible.

    The three phases are recorded INSIDE the lease, so the hold's own
    remainder plus the three phases is the hold: the split moves no
    seconds and double-charges none.
    """
    from library.tools import master_touchup, reel_build

    project, folder = _master_project(tmp_path, monkeypatch)
    monkeypatch.setattr(reel_build, "_connect_resolve_project",
                        lambda _name: project)

    # The fake Resolve is instant, so the duplicate is the one phase
    # given a measurable cost: the seconds must land in THAT phase.
    timeline = next(t for t in project.timelines if t.GetName() == MASTER)
    duplicate = timeline.DuplicateTimeline

    def slow_duplicate(name):
        time.sleep(0.05)
        return duplicate(name)

    monkeypatch.setattr(timeline, "DuplicateTimeline", slow_duplicate)

    ledger_path = tmp_path / "perf_ledger.jsonl"
    spec = {"edits": [
        {"op": "set_properties", "row": "V3", "item": 0,
         "properties": {"Opacity": 50}}]}
    with (perf_ledger.recording(str(ledger_path), "run-touch-phases",
                                 "reel.touchup", required=True),
          _sole_writer()):
        receipt = master_touchup.apply_master_touchup(str(folder), spec)

    assert receipt["master"] == MASTER

    rows = _rows(ledger_path)
    phases = _phase_rows(rows, "master")
    assert set(phases) == set(touchup_phases.PHASES), (
        "every phase of the touch is measured, and named")
    assert phases["duplicate"]["wall_s"] >= 0.05, (
        "the seconds the duplicate spent are the duplicate's own")
    assert phases["conform_compose_verify"]["wall_s"] < 0.05, (
        "and they are not charged to the phase that follows it")

    holds = [row for row in rows
             if row.get("layer") == perf_ledger.RESOLVE_HOLD]
    assert len(holds) == 1
    hold = holds[0]
    accounted = hold["wall_s"] + sum(row["wall_s"] for row in phases.values())
    assert accounted == pytest.approx(hold["held_s"], abs=0.5), (
        "the three phases and the hold's own remainder are the hold: "
        "the split moved no seconds and counted none twice")


# ── The reel touch, end to end against the fake Resolve ─────────────


def _reel_project(tmp_path, monkeypatch):
    """A project holding one built reel, with the proposal that names it."""
    from library.tools import reel_proposal, resolve_lock

    timeline, _pool, _media = build_reel(tmp_path)
    timeline.SetName(REEL)
    project = timeline._project
    project.SetCurrentTimeline(timeline)
    monkeypatch.setattr(resolve_lock, "under_lease", _ledger_lease)
    monkeypatch.setattr(resolve_lock, "cursor_fence", _pass_fence)
    monkeypatch.setattr(resolve_lock, "cursor_excursion", _pass_excursion)
    _mock_edit_patch(monkeypatch)
    _mock_shadow(monkeypatch)

    folder = tmp_path / "project"
    folder.mkdir()
    (folder / "project.yaml").write_text(
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


def test_the_reel_touch_measures_three_phases_that_sum_to_its_hold(
        tmp_path, monkeypatch):
    """The reel path measures the same three phases as the master path.

    The defect this names: the phases were measured on one path only,
    so the touch the captain actually runs - a reel touch - still
    reported one unlabelled number.
    """
    from library.tools import composed_edit, reel_build, reel_touchup

    project, folder = _reel_project(tmp_path, monkeypatch)
    monkeypatch.setattr(reel_build, "_connect_resolve_project",
                        lambda _name: project)

    # The fake Resolve is instant, so the staging copy is the one phase
    # given a measurable cost: the seconds must land in THAT phase.
    prepare = composed_edit.prepare_candidate

    def slow_prepare(source, staging):
        time.sleep(0.05)
        return prepare(source, staging)

    monkeypatch.setattr(composed_edit, "prepare_candidate", slow_prepare)

    ledger_path = tmp_path / "perf_ledger.jsonl"
    spec = {"reel": 1, "edits": [
        {"op": "set_properties", "row": "V3", "item": 0,
         "properties": {"Opacity": 50}}]}
    with (perf_ledger.recording(str(ledger_path), "run-touch-phases",
                                 "reel.touchup", required=True),
          _sole_writer()):
        receipt = reel_touchup.apply_touchup(
            str(folder), spec, connect=lambda _name: project)

    assert receipt["promoted"], receipt

    rows = _rows(ledger_path)
    phases = _phase_rows(rows, "reel")
    assert set(phases) == set(touchup_phases.PHASES), (
        "every phase of the touch is measured, and named")
    assert phases["duplicate"]["wall_s"] >= 0.05, (
        "the seconds the staging copy spent are the duplicate's own")
    assert phases["conform_compose_verify"]["wall_s"] < 0.05, (
        "and they are not charged to the phase that follows it")

    holds = [row for row in rows
             if row.get("layer") == perf_ledger.RESOLVE_HOLD]
    assert len(holds) == 1
    hold = holds[0]
    accounted = hold["wall_s"] + sum(row["wall_s"] for row in phases.values())
    assert accounted == pytest.approx(hold["held_s"], abs=0.5), (
        "the three phases and the hold's own remainder are the hold: "
        "the split moved no seconds and counted none twice")
