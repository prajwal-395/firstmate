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


# ── Scheduled dispatch ──────────────────────────────────────────────
#
# Each test names a defect the scheduler path closes:
#
# - a plan that records branches (plan must write the record and
#   nothing else - the branches are the dispatch's job);
# - variant tasks sharing a resource (disjoint declarations are what
#   let the scheduler run them together);
# - a dispatch that silently re-records landed branches (recorded
#   plans refuse; a foreign base under a planned name refuses);
# - a task failure the dispatch swallows (anything not done refuses
#   naming its tasks, and the plan stays unrecorded);
# - a foreign-held lease the dispatch spins on (pre-flight refuses by
#   name before the scheduler starts).


def _planned(tmp_path, monkeypatch, names=("faster-intro",
                                           "emotional-intro")):
    locks = tmp_path / "locks"
    locks.mkdir(exist_ok=True)
    monkeypatch.setenv("PIPELINE_RESOLVE_LOCK_DIR", str(locks))
    project = _project(tmp_path)
    root = _root(project)
    record = vx.plan_variants(
        project, base=root["id"],
        variants=_variants(*names),
        agent="ren-worker-07", timeline="Reel 01 - lab")
    return project, root["id"], record


def test_plan_writes_the_record_and_no_branch(tmp_path, monkeypatch):
    """The plan validates like the fan-out and writes the record with
    `"recorded": False` - and no branch, lease or branch event: the
    branches are the dispatch's job, not the plan's."""
    project, root_id, record = _planned(tmp_path, monkeypatch)

    assert record["recorded"] is False
    assert record["base"] == root_id
    assert uj.list_branches(project) == []
    kinds = [e["type"] for e in event_log.read_events(project)]
    assert "variant_branch_recorded" not in kinds
    assert kinds.count("variant_exploration_planned") == 1
    assert vx.read_exploration(project, record["id"])["recorded"] is False


def test_variant_tasks_are_disjoint_and_name_their_command(
        tmp_path, monkeypatch):
    """One task per variant, pairwise compatible declarations, each
    commanding back into this module - the scheduler can place them
    together, and whatever runs is this plan's own record."""
    project, _base, record = _planned(tmp_path, monkeypatch)

    graph = vx.variant_task_graph(project, record["id"])

    assert sorted(graph.tasks) == ["variant-emotional-intro",
                                   "variant-faster-intro"]
    tasks = list(graph.tasks.values())
    assert tasks[0].resources.conflicts_with(tasks[1].resources) is None
    for task in tasks:
        assert task.command[-4:-1] == ("record-variant", project,
                                       record["id"])
    assert tasks[0].capabilities == ()


def test_dispatch_records_every_branch_and_flips_the_plan(
        tmp_path, monkeypatch):
    """The scheduler path lands every branch, fills the record and
    emits the same branch events the serial path emits - one plan, one
    recording, whichever path ran."""
    project, base, record = _planned(tmp_path, monkeypatch)

    def _run(task):
        name = task.id[len("variant-"):]
        direction = next(
            v["direction"] for v in record["variants"]
            if v["name"] == name)
        vx._record_variant_branch(
            project, base_id=base,
            entry={"name": name, "direction": direction})
        return 0

    result = vx.dispatch_variants(project, record["id"], runner=_run)

    assert result["states"] == {"variant-emotional-intro": "done",
                                "variant-faster-intro": "done"}
    branches = {b["name"]: b for b in uj.list_branches(project)}
    assert branches["variant faster-intro"]["base"] == base
    assert branches["variant faster-intro"]["note"] == "try faster-intro"
    done = vx.read_exploration(project, record["id"])
    assert done["recorded"] is True
    assert [v["branch"] for v in done["variants"]] == [
        "variant faster-intro", "variant emotional-intro"]
    kinds = [e["type"] for e in event_log.read_events(project)]
    assert kinds.count("variant_branch_recorded") == 2
    assert kinds.count("variant_exploration_recorded") == 1
    assert kinds.count("variant_dispatch_finished") == 1


def test_dispatch_refuses_a_recorded_plan(tmp_path, monkeypatch):
    """Dispatching a recorded exploration would only re-record landed
    branches - it refuses instead of forking a second recording."""
    locks = tmp_path / "locks"
    locks.mkdir(exist_ok=True)
    monkeypatch.setenv("PIPELINE_RESOLVE_LOCK_DIR", str(locks))
    project = _project(tmp_path)
    root = _root(project)
    recorded = vx.explore_variants(
        project, base=root["id"], variants=_variants("faster-intro"),
        agent="ren-worker-07", timeline="Reel 01 - lab")
    assert recorded["recorded"] is True
    with pytest.raises(VariantRefused, match="already recorded"):
        vx.dispatch_variants(project, recorded["id"], runner=lambda t: 0)
    with pytest.raises(VariantRefused, match="already recorded"):
        vx.variant_task_graph(project, recorded["id"])


def test_dispatch_names_unfinished_tasks_and_keeps_the_plan(
        tmp_path, monkeypatch):
    """A task the runner fails is named, its siblings still land, and
    the plan stays unrecorded - a half-recorded plan would read as a
    comparison of branches that are not all there."""
    project, _base, record = _planned(tmp_path, monkeypatch)

    def _run(task):
        if task.id == "variant-emotional-intro":
            return 1
        name = task.id[len("variant-"):]
        direction = next(
            v["direction"] for v in record["variants"]
            if v["name"] == name)
        base = vx.read_exploration(
            project, record["id"])["base"]
        vx._record_variant_branch(
            project, base_id=base,
            entry={"name": name, "direction": direction})
        return 0

    with pytest.raises(VariantRefused, match="variant-emotional-intro"):
        vx.dispatch_variants(project, record["id"], runner=_run)
    assert uj.read_branch(project, "variant faster-intro")
    assert vx.read_exploration(project, record["id"])["recorded"] is False


def test_dispatch_preflights_a_held_lease(tmp_path, monkeypatch):
    """A foreign holder standing at dispatch time refuses by name
    before the scheduler starts - no branch, no checkpoint spin."""
    import threading

    project, _base, record = _planned(tmp_path, monkeypatch)
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
        with pytest.raises(VariantRefused, match="variant-faster-intro"):
            vx.dispatch_variants(project, record["id"], runner=lambda t: 0)
    finally:
        release.set()
        holder.join(timeout=30)
    assert uj.list_branches(project) == []
    assert vx.read_exploration(project, record["id"])["recorded"] is False


def test_record_variant_entry_point(tmp_path, monkeypatch, capsys):
    """The scheduler task's command records its branch and prints its
    name; unknown explorations and variants exit 4 with a refusal, and
    record nothing."""
    project, base, record = _planned(tmp_path, monkeypatch)

    assert vx._main(["record-variant", project, record["id"],
                     "faster-intro"]) == 0
    out = capsys.readouterr().out.strip()
    assert out == "variant faster-intro"
    assert uj.read_branch(project, "variant faster-intro")["base"] == base

    assert vx._main(["record-variant", project, "no-such-id",
                     "faster-intro"]) == 4
    assert vx._main(["record-variant", project, record["id"],
                     "no-such-variant"]) == 4


def test_retry_over_a_foreign_base_is_refused(tmp_path, monkeypatch):
    """A retry re-records its own base; a branch name that landed off
    another base belongs to other planning and refuses naming it."""
    project, base, record = _planned(tmp_path, monkeypatch)
    foreign = uj.record_commit(
        project, parent=None, intent="another line", changes=[],
        agent="ren-worker-08", timeline="Reel 02 - lab")["id"]
    uj.record_branch(project, name="variant faster-intro",
                     base=foreign, timeline="variant faster-intro",
                     note="someone else's")

    with pytest.raises(uj.BranchRefused, match="another"):
        vx._record_variant_branch(
            project, base_id=base,
            entry={"name": "faster-intro", "direction": "try faster-intro"})
    assert uj.read_branch(
        project, "variant faster-intro")["note"] == "someone else's"

    uj.record_branch(project, name="variant emotional-intro",
                     base=base, timeline="variant emotional-intro",
                     note="first attempt")
    again = vx._record_variant_branch(
        project, base_id=base,
        entry={"name": "emotional-intro",
               "direction": "try emotional-intro"})
    assert again["base"] == base


def test_exploration_ids_resolve_from_unique_prefixes(tmp_path, monkeypatch):
    """The CLI prints short ids, so it accepts them back: a unique
    prefix resolves, an unknown ref refuses, and a prefix naming two
    plans refuses instead of guessing."""
    project, _base, record = _planned(tmp_path, monkeypatch)

    assert vx.resolve_exploration(
        project, record["id"][:12])["id"] == record["id"]
    assert vx.resolve_exploration(
        project, record["id"])["id"] == record["id"]
    with pytest.raises(VariantRefused, match="not recorded"):
        vx.resolve_exploration(project, "no-such-plan")
    with pytest.raises(VariantRefused, match="no exploration named"):
        vx.resolve_exploration(project, "  ")
