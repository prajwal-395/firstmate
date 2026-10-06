"""Branch-parallel variant exploration: N isolated directions off one base.

The spec's "Branch parallelism" (Ren Fabric §"Branch parallelism"):
instead of several agents fighting over one timeline, Ren forks one
base commit into N isolated creative branches - one per variant
direction - plans each offline, and a critic step compares them.
Direction text is project or user data, never hardcoded: every variant
arrives with its direction, and a variant with none is refused rather
than planned from an invented one.

This module composes three layers it does not duplicate: branches are
recorded through `undo_journal.record_branch` (the commit graph owns
branch identity), isolation is declared through
`resource_leases.resource_lease` (the lease layer owns exclusion), and
the run is recorded through `event_log.emit` (the log owns resume).
No existing module fans out N branches at once, so the fan-out lives
here, with its source named beside every value it derives.

Offline first: recording a branch needs no live Resolve - the proof
that the materialized double is identical to its source
(`record_branch`'s track comparison) runs when a timeline is actually
duplicated for the variant, not here. What this records is the plan:
which base, which directions, which branch each one owns, and the
lease each planning step must hold before it writes.

Concurrency note: `fabric_scheduler`/`task_graph` carry the
concurrent path (`dispatch_variants` below builds one task per
variant and runs the graph through `Scheduler`, whose lease check is
what lets disjoint variants run together). The serial path stays for
a caller that wants N branches with no scheduler in the loop - both
record through the same leaseless core, so the two paths cannot
diverge in what a branch means. Known scheduler-lane gap, not worked
around here: `Scheduler.run` has no timeout, so a task whose lease a
foreign holder never releases spins until it does. `dispatch_variants`
pre-flights every variant lease first (immediate, advisory) and
refuses by name where one is held; a lease taken between the check
and the start is the scheduler's hang to fix.
"""

from __future__ import annotations

import hashlib
import json
import os
import time

from library.tools.ren_refusal import RenRefusal

EXPLORATION_FORMAT = "variant_exploration/1"
"""What an exploration record claims to be. Read back on read, like
every versioned record under `pipeline_output/review/`."""

EXPLORATION_FILENAME = "exploration.json"
"""One file per exploration, beside the commits and branches."""

VARIANT_BRANCH_PREFIX = "variant "
"""Variant branch names start here, so a variant branch never collides
with a touchup staging branch (which is named for its staging
timeline) and `list_branches` readers can tell them apart."""


class VariantRefused(RenRefusal):
    """An exploration that cannot start: nothing ran, nothing is written."""


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime())


def exploration_root(project_folder) -> str:
    """Where exploration records live, beside commits and branches."""
    return os.path.join(str(project_folder), "pipeline_output", "review",
                        "variant_explorations")


def variant_declaration(branch_timeline: str):
    """The lease declaration one variant's planning holds.

    Exclusive on its own branch timeline, nothing else: two variants
    hold disjoint declarations, so their planning may run concurrently
    without ever writing the same resource. Exposed (rather than built
    inline) so the test pins the isolation and a future scheduler can
    read what may run together without re-deriving it.
    """
    from library.tools.resource_leases import LeaseDeclaration, ResourceRef

    return LeaseDeclaration(exclusive=frozenset(
        {ResourceRef("timeline", str(branch_timeline))}))


def _exploration_id(content: dict) -> str:
    """The exploration's identity: SHA-256 of canonical JSON.

    The `plan_provenance` identity pattern `record_commit` copies: the
    same base, timeline, agent and variant set is the SAME exploration,
    so a re-sent request returns the record instead of forking
    parallel histories.
    """
    payload = json.dumps(content, sort_keys=True,
                         separators=(",", ":"), ensure_ascii=False,
                         default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _exploration_path(project_folder, exploration_id: str) -> str:
    return os.path.join(exploration_root(project_folder), exploration_id,
                        EXPLORATION_FILENAME)


def _check_name(name: str) -> str:
    """A branch name that stays inside the branch root.

    `record_branch` joins the name onto its root without validation, so
    a name carrying a separator would write outside it. The check lives
    here, at the fan-out, because this is the only writer that takes
    names from outside (directions arrive from the project or user).
    """
    cleaned = str(name or "").strip()
    if not cleaned:
        raise VariantRefused(
            "a variant needs a name",
            "one entry arrived with an empty name, and an unnamed "
            "branch matches nothing and excludes nobody",
            "name every variant - e.g. 'faster-intro' - and re-run")
    if ("/" in cleaned or "\\" in cleaned or cleaned in (".", "..")
            or ".." in cleaned.split()):
        raise VariantRefused(
            f"variant name {cleaned!r} would leave the branch root",
            "branch records are files under the branch root, so a "
            "separator in the name writes outside it",
            "use a flat slug - letters, digits, dashes - and re-run")
    return cleaned


def read_exploration(project_folder, exploration_id: str) -> dict:
    """One exploration record back, format-checked."""
    path = _exploration_path(project_folder, exploration_id)
    try:
        with open(path, encoding="utf-8") as handle:
            record = json.load(handle)
    except (OSError, ValueError) as unreadable:
        raise VariantRefused(
            f"variant exploration {exploration_id!r} could not be read",
            f"({unreadable})",
            "list what is recorded with "
            "`manage_project.py explore-variants <project> list") from unreadable
    if record.get("format") != EXPLORATION_FORMAT:
        raise VariantRefused(
            f"{path} is {record.get('format')!r}, not "
            f"{EXPLORATION_FORMAT!r}",
            "an exploration in a foreign format cannot be trusted",
            "recover the record from the project's git history")
    return record


def list_explorations(project_folder) -> list:
    """Every exploration record, oldest first."""
    root = exploration_root(project_folder)
    if not os.path.isdir(root):
        return []
    records = []
    for name in sorted(os.listdir(root)):
        path = os.path.join(root, name, EXPLORATION_FILENAME)
        if os.path.isfile(path):
            records.append(read_exploration(project_folder, name))
    return sorted(records, key=lambda entry: entry.get("created_at") or "")


def _clean_directions(variants) -> list:
    """Validated variant entries: unique flat names, non-blank directions.

    Shared by the plan and the serial fan-out, so the two entry points
    cannot disagree on what a variant is. Every refusal carries its
    fix; all fire before anything is written.
    """
    entries = list(variants or ())
    if not entries:
        raise VariantRefused(
            "no variant directions arrived",
            "an exploration with no directions would plan nothing, and "
            "a plan with no direction is taste this module must not invent",
            "pass one --direction per variant - the project or user "
            "text each variant tries")
    cleaned = []
    seen = set()
    for entry in entries:
        name = _check_name((entry or {}).get("name"))
        direction = str((entry or {}).get("direction") or "").strip()
        if not direction:
            raise VariantRefused(
                f"variant {name!r} arrived with no direction",
                "direction text is project or user data, and a variant "
                "with none would be planned from an invented one",
                f"say what {name!r} tries - one line of project or "
                "user text - and re-run")
        if name in seen:
            raise VariantRefused(
                f"variant {name!r} is named twice",
                "two variants on one name would plan over each other, "
                "and the second would silently discard the first",
                "give each variant its own name and re-run")
        seen.add(name)
        cleaned.append({"name": name, "direction": direction})
    return cleaned


def _refuse_taken_branches(project_folder, cleaned) -> None:
    """Refuse where a planned branch name already landed.

    A branch that already landed belongs to earlier planning:
    recording over it would silently discard that work. The serial
    fan-out and the plan share this check, so neither can take a
    name the other verified - the check runs at write time in both.
    """
    from library.tools import undo_journal

    for entry in cleaned:
        branch_name = VARIANT_BRANCH_PREFIX + entry["name"]
        try:
            undo_journal.read_branch(project_folder, branch_name)
        except undo_journal.BranchRefused:
            continue
        raise VariantRefused(
            f"branch {branch_name!r} is already recorded",
            "re-planning over it would silently discard the variant "
            "that owns it",
            "pick fresh variant names, or read the landed plan with "
            "`manage_project.py explore-variants <project> list`")


def plan_variants(project_folder, *, base: str, variants,
                  agent: str, timeline: str = "") -> dict:
    """Plan N variants off one base without recording any branch.

    Validates everything the serial fan-out validates (base readable,
    names, directions, taken names) and writes the exploration record
    with `"recorded": False` - the plan the scheduler dispatches.
    `dispatch_variants` fills the branches in and flips the flag; the
    graph derives nodes only for recorded explorations, so a plan
    never reads as a comparison of branches that do not exist yet. A
    request whose content hashes to a record already on disk returns
    it, like the fan-out.
    """
    from library.tools import event_log, undo_journal
    from library.tools.versions import store

    if not str(agent or "").strip():
        raise VariantRefused(
            "no planning agent named",
            "the exploration record says who planned each variant, and "
            "an anonymous plan cannot be questioned later",
            "pass the worker's own name as the agent and re-run")
    base_commit = undo_journal.read_commit(project_folder, base)
    cleaned = _clean_directions(variants)
    _refuse_taken_branches(project_folder, cleaned)
    content = {
        "base": base_commit["id"],
        "timeline": str(timeline or ""),
        "agent": str(agent).strip(),
        "variants": cleaned,
    }
    exploration_id = _exploration_id(content)
    existing_path = _exploration_path(project_folder, exploration_id)
    if os.path.isfile(existing_path):
        return read_exploration(project_folder, exploration_id)
    record = {
        "format": EXPLORATION_FORMAT,
        "id": exploration_id,
        "base": base_commit["id"],
        "timeline": str(timeline or ""),
        "agent": str(agent).strip(),
        "variants": [{"name": entry["name"],
                      "direction": entry["direction"]} for entry in cleaned],
        "recorded": False,
        "created_at": _now(),
    }
    store.write_record(
        _exploration_path(project_folder, exploration_id), record,
        prefix=".exploration-")
    event_log.emit(
        str(project_folder), "variant_exploration_planned",
        task=str(agent).strip(), exploration=exploration_id,
        base=base_commit["id"],
        branches=[VARIANT_BRANCH_PREFIX + entry["name"]
                  for entry in cleaned])
    return record


def explore_variants(project_folder, *, base: str, variants,
                     agent: str, timeline: str = "") -> dict:
    """Fork one base commit into one isolated branch per direction.

    `base` is the commit every variant starts from (read through the
    commit graph - an unknown base refuses before anything is written).
    `variants` is the direction set: each entry carries `name` (the
    caller's slug for the branch) and `direction` (the project/user
    text saying what this variant tries - never a value this module
    invents). `agent` names the worker planning the variants, and
    `timeline` the timeline the base is for (part of the identity, as
    it is for commits: an exploration on Reel 03 is a different
    exploration from one on Reel 10).

    The serial path: each variant's branch is recorded here, under
    its own lease, before the next starts. The scheduled path
    (`plan_variants` + `dispatch_variants`) records through the task
    graph instead. Returns the exploration record: its id, the base,
    and one entry per variant with the recorded branch. A request
    whose content hashes to an exploration already on disk returns
    that record - a re-sent request never forks history.
    """
    from library.tools import event_log, undo_journal
    from library.tools.resource_leases import (
        ResourceLeaseBusy, resource_lease)
    from library.tools.versions import store

    if not str(agent or "").strip():
        raise VariantRefused(
            "no planning agent named",
            "the exploration record says who planned each variant, and "
            "an anonymous plan cannot be questioned later",
            "pass the worker's own name as the agent and re-run")

    # The base is read FIRST: an unknown base refuses before any
    # branch, lease or event exists, so a typo never leaves half an
    # exploration behind.
    base_commit = undo_journal.read_commit(project_folder, base)
    cleaned = _clean_directions(variants)

    content = {
        "base": base_commit["id"],
        "timeline": str(timeline or ""),
        "agent": str(agent).strip(),
        "variants": cleaned,
    }
    exploration_id = _exploration_id(content)
    existing_path = _exploration_path(project_folder, exploration_id)
    if os.path.isfile(existing_path):
        # The same exploration is already recorded: return it as it
        # stands, like `record_commit` - a re-sent request never forks
        # history and never re-records branches.
        return read_exploration(project_folder, exploration_id)

    # A branch that already landed under one of these names belongs to
    # earlier planning - the shared check, so the serial path takes no
    # name the plan would refuse.
    _refuse_taken_branches(project_folder, cleaned)

    recorded = []
    for entry in cleaned:
        branch_name = VARIANT_BRANCH_PREFIX + entry["name"]
        branch_timeline = branch_name
        declaration = variant_declaration(branch_timeline)
        try:
            # Serial by design for this path (see module docstring):
            # the lease is still taken - each variant holds its own
            # branch exclusively while it records - so concurrent
            # planners arriving later already have declarations that
            # describe what may run together.
            # `wait=False`: a stuck holder refuses by name instead of
            # hanging the run.
            with resource_lease(
                    declaration, purpose=f"variant {entry['name']}",
                    wait=False, heartbeat_thread=False,
                    project=str(project_folder)):
                branch = _record_variant_branch(
                    project_folder, base_id=base_commit["id"],
                    entry=entry)
        except ResourceLeaseBusy as held:
            raise VariantRefused(
                f"variant {entry['name']!r} could not take its branch lease",
                f"{held}",
                "wait for the holder to finish, or plan this variant "
                "under a fresh name") from held
        event_log.emit(
            str(project_folder), "variant_branch_recorded",
            task=str(agent).strip(), branch=branch_name,
            base=base_commit["id"], direction=entry["direction"])
        recorded.append({
            "name": entry["name"],
            "direction": entry["direction"],
            "branch": branch["name"],
            "timeline": branch["timeline"],
            "base": branch["base"],
        })

    record = {
        "format": EXPLORATION_FORMAT,
        "id": exploration_id,
        "base": base_commit["id"],
        "timeline": str(timeline or ""),
        "agent": str(agent).strip(),
        "variants": recorded,
        "recorded": True,
        "created_at": _now(),
    }
    store.write_record(
        _exploration_path(project_folder, exploration_id), record,
        prefix=".exploration-")
    event_log.emit(
        str(project_folder), "variant_exploration_recorded",
        task=str(agent).strip(), exploration=exploration_id,
        base=base_commit["id"],
        branches=[entry["branch"] for entry in recorded])
    return record


def _record_variant_branch(project_folder, *, base_id: str,
                           entry: Mapping) -> dict:
    """Record one variant's branch: the leaseless core both paths share.

    The serial path takes the lease itself; the scheduled path runs
    under the lease the scheduler holds for the task. Neither takes it
    twice, and neither records anything else - one function, so the
    two paths cannot diverge in what a branch means. Callers hold the
    lease; this function only writes.

    A branch name that landed since the plan - with a DIFFERENT base -
    belongs to other planning and is refused naming it, so a retry
    never discards someone else's variant. The same base re-records:
    that is a retry of this plan, and the record is identical.
    """
    from library.tools import undo_journal

    branch_name = VARIANT_BRANCH_PREFIX + entry["name"]
    try:
        landed = undo_journal.read_branch(project_folder, branch_name)
    except undo_journal.BranchRefused:
        landed = None
    if landed is not None and landed.get("base") != base_id:
        raise undo_journal.BranchRefused(
            f"branch {branch_name!r} is already recorded off another "
            f"base - a retry records the same base, never someone "
            f"else's variant")
    return undo_journal.record_branch(
        project_folder, name=branch_name, base=base_id,
        timeline=branch_name, note=entry["direction"])


def resolve_exploration(project_folder, ref: str) -> dict:
    """One exploration record from a full id or a unique prefix.

    The CLI prints short ids, so it accepts them back: an unknown ref
    refuses, and a prefix matching more than one exploration refuses
    naming the matches instead of guessing.
    """
    wanted = str(ref or "").strip()
    if not wanted:
        raise VariantRefused(
            "no exploration named",
            "dispatch records one planned exploration, and none was named",
            "pass --exploration <id> from "
            "`explore-variants <project> list`")
    hits = [record for record in list_explorations(project_folder)
            if record.get("id") == wanted
            or str(record.get("id") or "").startswith(wanted)]
    if len(hits) == 1:
        return hits[0]
    if not hits:
        raise VariantRefused(
            f"exploration {wanted!r} is not recorded",
            "dispatch records a planned exploration, and no record "
            "answers that id",
            "list what is recorded with "
            "`manage_project.py explore-variants <project> list`")
    raise VariantRefused(
        f"exploration prefix {wanted!r} matches {len(hits)} explorations",
        "a prefix that names more than one plan names none",
        "pass the full id from "
        "`manage_project.py explore-variants <project> list`")


def variant_task_graph(project_folder, exploration_id: str):
    """One scheduler task per variant of a planned exploration.

    No dependencies between variants - every task is ready at once,
    and the scheduler's lease check is what serializes two tasks that
    declare the same resource (which disjoint variants never do) while
    letting the rest run together. Capabilities are empty: recording a
    branch needs the project store and nothing else - no Resolve, no
    GPU - so any worker may take it, and the matcher gates on nothing.
    The command re-enters this module (`record-variant`), which
    records the branch under the lease the scheduler already holds.
    An already-recorded exploration refuses here too: dispatching it
    would only re-record landed branches.
    """
    import sys

    from library.tools.task_graph import Task, TaskGraph

    record = resolve_exploration(project_folder, exploration_id)
    exploration_id = record["id"]
    if record.get("recorded"):
        raise VariantRefused(
            f"exploration {exploration_id[:12]} is already recorded",
            "its branches landed - building tasks for them would only "
            "re-record what stands",
            "dispatch a planned exploration, or read this one with "
            "`explore-variants <project> list`")
    tasks = {}
    for variant in record.get("variants") or ():
        name = variant.get("name") or ""
        task = Task(
            id=f"variant-{name}",
            name=f"record variant branch {name}",
            resources=variant_declaration(
                VARIANT_BRANCH_PREFIX + name),
            command=(sys.executable, "-m",
                     "library.tools.variant_exploration",
                     "record-variant", str(project_folder),
                     exploration_id, name),
        )
        if task.id in tasks:
            raise VariantRefused(
                f"variant {name!r} is recorded twice",
                "two tasks with one id are one task the scheduler "
                "cannot place twice",
                "re-propose the exploration with distinct names")
        tasks[task.id] = task
    return TaskGraph(tasks=tasks)


def _dispatch_state_path(project_folder, exploration_id: str) -> str:
    """The scheduler checkpoint: scratch, never the record.

    Task states are resume bookkeeping, not creative history - they
    live under `pipeline_output/scratch/`, safe to throw away, while
    the exploration record stays versioned text under `review/`.
    """
    return os.path.join(str(project_folder), "pipeline_output", "scratch",
                        "variant_dispatch", f"{exploration_id}.state.json")


def dispatch_variants(project_folder, exploration_id: str, *,
                      runner=None, manifest=None) -> dict:
    """Run a planned exploration's variants through the scheduler.

    Takes a `plan_variants` record (`"recorded": False`) and records
    every variant's branch through one scheduler task each: disjoint
    variants proceed together under their own branch leases, and a
    task whose lease a foreign holder keeps never starts. An already-
    recorded exploration refuses - there is nothing to dispatch, and
    re-running would only re-record landed branches. Every variant
    lease is pre-flighted first (immediate, advisory - no threshold
    to invent): a lease held at dispatch time refuses by name before
    the scheduler starts, because a blocked task would otherwise spin
    the scheduler until the holder lets go. A lease taken between the
    check and the start is the scheduler lane's hang to fix, and is
    said so in the module note rather than worked around here. On
    success the record is filled in and flipped to recorded, with the
    same branch events the serial path emits; anything not done
    refuses naming its tasks, and the record stays unrecorded.
    """
    from library.tools import event_log, undo_journal
    from library.tools.fabric_scheduler import Scheduler
    from library.tools.resource_leases import (
        ResourceLeaseBusy, resource_lease)
    from library.tools.versions import store

    record = resolve_exploration(project_folder, exploration_id)
    exploration_id = record["id"]
    if record.get("recorded"):
        raise VariantRefused(
            f"exploration {exploration_id[:12]} is already recorded",
            "dispatch records a plan's branches - a recorded plan has "
            "no branches left to record",
            "read it with `manage_project.py explore-variants <project> "
            "list`, or plan fresh variants")
    graph = variant_task_graph(project_folder, exploration_id)
    for task in graph.tasks.values():
        try:
            with resource_lease(task.resources,
                                purpose=f"variant dispatch preflight "
                                        f"{task.id}",
                                wait=False, heartbeat_thread=False):
                pass
        except ResourceLeaseBusy as held:
            raise VariantRefused(
                f"variant task {task.id!r} is blocked at dispatch",
                f"{held}",
                "wait for the holder to finish, then dispatch again - "
                "`manage_project.py explore-variants <project> dispatch "
                "--exploration <id>`") from held
    scheduler = Scheduler(
        # Variant tasks declare no capabilities, so the matcher gates
        # on nothing and any worker may take them - the default empty
        # manifest is enough. A future variant task that needs a
        # capability declares it on the Task, and the caller passes
        # the live manifest through `manifest`.
        graph, manifest if manifest is not None else {},
        _dispatch_state_path(project_folder, exploration_id),
        runner=runner)
    states = scheduler.run()
    pending = sorted(
        task_id for task_id, state in states.items() if state != "done")
    event_log.emit(
        str(project_folder), "variant_dispatch_finished",
        task=record.get("agent") or "", exploration=exploration_id,
        states={task_id: states[task_id] for task_id in sorted(states)})
    if pending:
        raise VariantRefused(
            f"variant dispatch left {len(pending)} task(s) unfinished: "
            f"{', '.join(pending)}",
            "a task that did not finish recorded nothing - its branch "
            "is absent, not half-written, and the plan stays unrecorded",
            "read the scheduler checkpoint at "
            f"{_dispatch_state_path(project_folder, exploration_id)} "
            "for per-task states, clear the cause, and dispatch again")
    filled = []
    for variant in record.get("variants") or ():
        branch = undo_journal.read_branch(
            project_folder, VARIANT_BRANCH_PREFIX + variant["name"])
        filled.append({
            "name": variant["name"],
            "direction": variant["direction"],
            "branch": branch["name"],
            "timeline": branch["timeline"],
            "base": branch["base"],
        })
        event_log.emit(
            str(project_folder), "variant_branch_recorded",
            task=record.get("agent") or "",
            branch=branch["name"], base=branch["base"],
            direction=variant["direction"])
    record["variants"] = filled
    record["recorded"] = True
    store.write_record(
        _exploration_path(project_folder, exploration_id), record,
        prefix=".exploration-")
    event_log.emit(
        str(project_folder), "variant_exploration_recorded",
        task=record.get("agent") or "", exploration=exploration_id,
        base=record["base"],
        branches=[entry["branch"] for entry in filled])
    return {"exploration": exploration_id, "states": states}


def _main(argv=None) -> int:
    """`python -m library.tools.variant_exploration record-variant ...`.

    The scheduler task's command: record one variant's branch under
    the lease the scheduler holds. Never takes a lease itself - taking
    one here would contend with the scheduler's own hold on the same
    resource from another process and refuse work that is already
    guarded.
    """
    import argparse
    import sys

    from library.tools.ren_refusal import REFUSAL_EXIT_CODE

    parser = argparse.ArgumentParser(
        prog="python -m library.tools.variant_exploration",
        description="Record one variant's branch (a scheduler task "
                    "command - the scheduler holds the lease).")
    parser.add_argument("command", choices=["record-variant"])
    parser.add_argument("project_folder")
    parser.add_argument("exploration_id")
    parser.add_argument("name")
    args = parser.parse_args(argv)
    try:
        record = read_exploration(args.project_folder,
                                  args.exploration_id)
    except VariantRefused as refused:
        print(refused.render(), file=sys.stderr)
        return REFUSAL_EXIT_CODE
    match = [v for v in record.get("variants") or ()
             if v.get("name") == args.name]
    if not match:
        print(f"ren: refused - exploration {args.exploration_id[:12]} "
              f"names no variant {args.name!r}\n"
              f"  why: a task for an unrecorded variant would invent "
              f"its direction\n"
              f"  fix: dispatch the recorded exploration instead",
              file=sys.stderr)
        return REFUSAL_EXIT_CODE
    try:
        from library.tools import undo_journal

        branch = _record_variant_branch(
            args.project_folder, base_id=record["base"],
            entry={"name": args.name,
                   "direction": match[0].get("direction") or ""})
    except (VariantRefused, undo_journal.BranchRefused,
            undo_journal.UndoRefused) as refused:
        message = (refused.render() if isinstance(refused, VariantRefused)
                   else f"ren: refused - {refused}")
        print(message, file=sys.stderr)
        return REFUSAL_EXIT_CODE
    print(branch["name"])
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
