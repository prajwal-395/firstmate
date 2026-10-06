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

Concurrency note: `fabric_scheduler`/`task_graph` have landed since
this slice, but the fan-out stays SERIAL here - each variant holds its
own branch-scoped lease while it records, and releases before the
next starts. The per-variant lease declarations already describe what
may run together, so dispatching this loop through the scheduler is a
follow-up slice on the scheduler lane's API, not a change to what is
recorded.
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

    Returns the exploration record: its id, the base, and one entry
    per variant with the recorded branch. A request whose content
    hashes to an exploration already on disk returns that record -
    a re-sent request never forks history.
    """
    from library.tools import event_log, undo_journal
    from library.tools.resource_leases import (
        ResourceLeaseBusy, resource_lease)
    from library.tools.versions import store

    entries = list(variants or ())
    if not entries:
        raise VariantRefused(
            "no variant directions arrived",
            "an exploration with no directions would plan nothing, and "
            "a plan with no direction is taste this module must not invent",
            "pass one --direction per variant - the project or user "
            "text each variant tries")
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
    # earlier planning: recording over it would silently discard that
    # work, so the exploration refuses naming the holder instead.
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

    recorded = []
    for entry in cleaned:
        branch_name = VARIANT_BRANCH_PREFIX + entry["name"]
        branch_timeline = branch_name
        declaration = variant_declaration(branch_timeline)
        try:
            # Serial by design (see module docstring): the lease is
            # still taken - each variant holds its own branch
            # exclusively while it records - so concurrent planners
            # arriving later already have declarations that describe
            # what may run together.
            # `wait=False`: a stuck holder refuses by name instead of
            # hanging the run.
            with resource_lease(
                    declaration, purpose=f"variant {entry['name']}",
                    wait=False, heartbeat_thread=False,
                    project=str(project_folder)):
                branch = undo_journal.record_branch(
                    project_folder, name=branch_name,
                    base=base_commit["id"], timeline=branch_timeline,
                    note=entry["direction"])
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
