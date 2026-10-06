"""Variant exploration: N isolated branches off one base, offline.

Each test names a defect the fan-out closes:

- a variant with no direction, so its planning would invent the taste
  the direction was supposed to carry;
- two variants on one name, so the second silently discards the first;
- a variant name that escapes the branch root, so the record lands
  outside the project;
- an unknown base that leaves half an exploration behind (branches and
  events with no base);
- a re-plan over a landed branch name, so earlier planning is silently
  discarded;
- a re-sent identical request that forks history instead of returning
  the record;
- a variant whose branch lease is held elsewhere, so two planners
  write one branch and the refusal names nobody.

Branches, explorations and events are recorded offline under
`pipeline_output/` in a `tmp_path` project; leases are redirected to
a `tmp_path` lock dir. No Resolve is touched.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from library.tools import event_log  # noqa: E402
from library.tools import undo_journal as uj  # noqa: E402
from library.tools import variant_exploration as vx  # noqa: E402
from library.tools.resource_leases import (  # noqa: E402
    resource_lease,
)
from library.tools.variant_exploration import VariantRefused  # noqa: E402


@pytest.fixture
def lock_dir(tmp_path, monkeypatch):
    """Leases of our own, never the machine's.

    The redirect `resolve_lock` itself uses, copied from the resource
    lease tests - the fan-out takes real leases, so the test isolates
    them.
    """
    directory = tmp_path / "locks"
    directory.mkdir()
    monkeypatch.setenv("PIPELINE_RESOLVE_LOCK_DIR", str(directory))
    return directory


def _project(tmp_path) -> str:
    folder = Path(tmp_path) / "project"
    folder.mkdir(parents=True, exist_ok=True)
    return str(folder)


def _root(project) -> dict:
    return uj.record_commit(
        project, parent=None, intent="first touch", changes=[],
        agent="reel_touchup", timeline="Reel 01 - lab")


def _variants(*names):
    return [{"name": name, "direction": f"try {name}"} for name in names]


def test_three_directions_become_three_isolated_branches(tmp_path, lock_dir):
    """The fan-out: one branch per direction, each on the base.

    The defect: N directions planned onto one timeline or onto
    branches that share a base with nothing - the critic would compare
    variants that were never isolated. Each recorded branch must point
    at the base commit and carry its direction as the note, and the
    exploration record must read back whole.
    """
    project = _project(tmp_path)
    root = _root(project)
    record = vx.explore_variants(
        project, base=root["id"],
        variants=_variants("faster-intro", "emotional-intro",
                           "music-driven-intro"),
        agent="ren-worker-07", timeline="Reel 01 - lab")

    assert [v["name"] for v in record["variants"]] == [
        "faster-intro", "emotional-intro", "music-driven-intro"]
    branches = {b["name"]: b for b in uj.list_branches(project)}
    for variant in record["variants"]:
        branch = branches[variant["branch"]]
        assert branch["base"] == root["id"]
        assert branch["note"] == variant["direction"]
        assert branch["timeline"] == variant["timeline"]
    assert vx.read_exploration(project, record["id"])["id"] == record["id"]

    kinds = [e["type"] for e in event_log.read_events(project)]
    assert kinds.count("variant_branch_recorded") == 3
    assert kinds.count("variant_exploration_recorded") == 1


def test_variant_declarations_never_share_a_resource(lock_dir):
    """Two variants' lease declarations are compatible by construction.

    The defect: a fan-out whose variants declare the same resource
    serialises (or collides) where the spec promises isolation - each
    variant must hold its own branch exclusively, so concurrent
    planning never writes the same resource.
    """
    first = vx.variant_declaration("variant faster-intro")
    second = vx.variant_declaration("variant music-driven-intro")
    assert first.conflicts_with(second) is None
    assert first.conflicts_with(first) is not None


def test_blank_direction_is_refused(tmp_path, lock_dir):
    """A variant with no direction is refused, not planned.

    The defect: a directionless variant planned from an invented
    direction - the engine substituting taste where the project or
    user said nothing. Nothing is recorded: no branch, no event.
    """
    project = _project(tmp_path)
    root = _root(project)
    with pytest.raises(VariantRefused):
        vx.explore_variants(
            project, base=root["id"],
            variants=[{"name": "faster-intro", "direction": "   "}],
            agent="ren-worker-07")
    assert uj.list_branches(project) == []
    assert vx.list_explorations(project) == []
    assert event_log.read_events(project) == []


def test_no_directions_is_refused(tmp_path, lock_dir):
    """An exploration with nothing to explore refuses.

    The defect: an empty exploration record that later reads as a
    comparison of nothing - the critic would score zero variants and
    call it a result.
    """
    project = _project(tmp_path)
    root = _root(project)
    with pytest.raises(VariantRefused):
        vx.explore_variants(
            project, base=root["id"], variants=[], agent="ren-worker-07")
    assert vx.list_explorations(project) == []


def test_duplicate_names_are_refused(tmp_path, lock_dir):
    """Two variants on one name refuse before anything is written.

    The defect: the second variant silently discarding the first -
    one branch, two directions, and no record that one was lost.
    """
    project = _project(tmp_path)
    root = _root(project)
    with pytest.raises(VariantRefused, match="named twice"):
        vx.explore_variants(
            project, base=root["id"],
            variants=[{"name": "faster-intro", "direction": "go fast"},
                      {"name": "faster-intro", "direction": "go faster"}],
            agent="ren-worker-07")
    assert uj.list_branches(project) == []
    assert vx.list_explorations(project) == []


@pytest.mark.parametrize("bad", ["", "   ", "../evil", "a/b", "a\\b",
                                 "..", "."])
def test_unsafe_names_are_refused(tmp_path, lock_dir, bad):
    """A name that escapes the branch root never reaches it.

    The defect: `record_branch` joins the name onto its root without
    validation, so a separator in a caller-supplied name writes
    outside the project. The fan-out is the only writer that takes
    names from outside, so the check lives here.
    """
    project = _project(tmp_path)
    root = _root(project)
    with pytest.raises(VariantRefused):
        vx.explore_variants(
            project, base=root["id"],
            variants=[{"name": bad, "direction": "go fast"}],
            agent="ren-worker-07")
    assert uj.list_branches(project) == []
    assert vx.list_explorations(project) == []
    assert not (Path(project) / "evil").exists()


def test_unknown_base_refuses_before_anything_is_written(tmp_path, lock_dir):
    """A typo'd base leaves no half exploration behind.

    The defect: branches and events recorded against a base that does
    not exist - variants isolated from nothing, pointing nowhere.
    """
    project = _project(tmp_path)
    with pytest.raises(uj.UndoRefused):
        vx.explore_variants(
            project, base="no-such-commit",
            variants=_variants("faster-intro"), agent="ren-worker-07")
    assert uj.list_branches(project) == []
    assert vx.list_explorations(project) == []
    assert event_log.read_events(project) == []


def test_taken_branch_name_refuses_naming_the_holder(tmp_path, lock_dir):
    """A re-plan over a landed branch refuses instead of discarding it.

    The defect: `record_branch` overwrites silently, so exploring again
    with the same names would discard the landed variant's planning
    with no record. The refusal names the branch that owns the name.
    """
    project = _project(tmp_path)
    root = _root(project)
    uj.record_branch(project, name="variant faster-intro",
                     base=root["id"], timeline="variant faster-intro",
                     note="landed earlier")
    with pytest.raises(VariantRefused, match="variant faster-intro"):
        vx.explore_variants(
            project, base=root["id"],
            variants=_variants("faster-intro", "emotional-intro"),
            agent="ren-worker-07")
    assert vx.list_explorations(project) == []
    assert uj.read_branch(
        project, "variant faster-intro")["note"] == "landed earlier"


def test_identical_request_returns_the_record(tmp_path, lock_dir):
    """A re-sent request is a no-op, not a fork.

    The defect: every retry forking a parallel history - N identical
    explorations, N identical branch sets, and a critic comparing a
    variant with itself. Like `record_commit`: same content is the
    same exploration.
    """
    project = _project(tmp_path)
    root = _root(project)
    first = vx.explore_variants(
        project, base=root["id"], variants=_variants("faster-intro"),
        agent="ren-worker-07", timeline="Reel 01 - lab")
    second = vx.explore_variants(
        project, base=root["id"], variants=_variants("faster-intro"),
        agent="ren-worker-07", timeline="Reel 01 - lab")
    assert second["id"] == first["id"]
    assert len(vx.list_explorations(project)) == 1
    assert len(uj.list_branches(project)) == 1
    assert [e["type"] for e in event_log.read_events(project)].count(
        "variant_exploration_recorded") == 1


def test_held_branch_lease_refuses_naming_the_holder(tmp_path, lock_dir):
    """A variant whose branch is held elsewhere refuses by name.

    The defect: two planners writing one branch - the lease layer's
    whole reason to exist. The holder runs in a SECOND thread: same-
    thread reentrancy (PR 1881) grants a nested declaration its own
    outer lease covers, so holding and requesting in one thread no
    longer contends - only a different holder does. The fan-out's
    `wait=False` acquisition meets the live holder and the refusal
    carries it.
    """
    import threading

    project = _project(tmp_path)
    root = _root(project)
    declaration = vx.variant_declaration("variant faster-intro")
    entered = threading.Event()
    release = threading.Event()

    def _hold():
        with resource_lease(declaration, purpose="other planner",
                            heartbeat_thread=False, project=project):
            entered.set()
            release.wait(timeout=30)

    holder = threading.Thread(target=_hold, name="other-planner")
    holder.start()
    try:
        assert entered.wait(timeout=30)
        with pytest.raises(VariantRefused, match="faster-intro"):
            vx.explore_variants(
                project, base=root["id"],
                variants=_variants("faster-intro"), agent="ren-worker-07")
    finally:
        release.set()
        holder.join(timeout=30)
    assert uj.list_branches(project) == []
    assert vx.list_explorations(project) == []
