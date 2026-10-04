"""Which work runs in parallel and which serialises.

The captain's ruling of 2026-09-12 that this exists for: *"get the
async locking and gating and declaration keys and routing all figured
and implemented ... to account for the ability to horizontally scale
across me and the LLM(s) working on video projects using a single
davinci instance on my local computer"* - and, said plainly, *"today
that judgement is mine and I have got it wrong twice today"*.

So this is not advice. Capability routes are derived from each
`Operation.execution` phase. The remaining rows cover direct tool
surfaces that are not capabilities; a supervisor names the operation,
reads its exclusion, and dispatches or queues it.

The four classes
----------------
`RESOLVE_CURSOR`   Establishes the Resolve cursor (current project or
                   current timeline) and writes through it. Needs the
                   EXCLUSIVE instance lease. One at a time, machine-wide.

`RESOLVE_READ`     Reads Resolve through a handle it names, never
                   through `GetCurrentTimeline`. Needs the SHARED lease:
                   several run together, none runs while a cursor
                   operation holds the instance. A read is fenced too,
                   because a foreign writer can delete the timeline the
                   handle points at halfway through.

`DECLARATION`      Writes a keyed file under `external/declarations/` or
                   `pipeline_output/review/`. No Resolve. Serialises
                   only against another writer of the SAME FILE, and
                   conflicts only against the same ENTRY KEY -
                   `library/tools/declaration_keys.py`.

`FREE`             Touches neither. Vision, ffmpeg, Remotion, planning,
                   the LLM calls, every test whose Resolve is a mock.
                   Unbounded parallelism, and it is most of a build.

The whole point of the split is the last row. A reel build is minutes
of `FREE` around seconds of `RESOLVE_CURSOR`, so the build row itself
is `FREE`: the cursor sections take their own holds inside the body
(one exclusive hold per placed reel, shared holds for the gate and
the surveys - see `rebuild_reels_in_project`'s THE LEASE section),
and two builds meet only there. Routing the build as one unit would
serialise the minutes to protect the seconds and delete the
parallelism this exists to provide.

What is NOT in this table
-------------------------
A second Resolve instance, or any isolation approach. The scripting
bridge is a single fixed port with no instance selector, so a second
headless Resolve is unaddressable by our own code. That is a filed
captain decision (`vep-resolve-statefulness-hazards`), and it is what
makes cooperation the only available route rather than one option
among several.

`tests/unit/context/test_concurrency.py`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Optional, Tuple

RESOLVE_CURSOR = "resolve_cursor"
RESOLVE_READ = "resolve_read"
DECLARATION = "declaration"
FREE = "free"

CLASSES = (RESOLVE_CURSOR, RESOLVE_READ, DECLARATION, FREE)

#: What holding each class costs another worker.
#: `exclusive` - may one hold at a time, machine-wide?
#: `lease`     - does it take the Resolve instance lease at all?
EXCLUSION: Dict[str, Tuple[bool, bool]] = {
    RESOLVE_CURSOR: (True, True),
    RESOLVE_READ: (False, True),
    DECLARATION: (False, False),
    FREE: (False, False),
}


@dataclass(frozen=True)
class Operation:
    """One named piece of work, and what it may run beside."""

    name: str
    entry_point: str
    exclusion: str
    why: str

    #: The declaration file it writes, where `exclusion` is DECLARATION.
    declaration: Optional[str] = None

    #: A PERSON presses this one. It PREFERS the instance rather than
    #: taking it: free, it holds; held, it goes ahead anyway and the
    #: agent finds out through its own cursor fence. The captain
    #: writing by hand is the requirement, so a button of theirs that
    #: queued behind a fifteen-minute build would be the workflow
    #: designed out rather than served.
    human_initiated: bool = False

    def is_exclusive(self) -> bool:
        return EXCLUSION[self.exclusion][0]

    def takes_lease(self) -> bool:
        return EXCLUSION[self.exclusion][1]


#: Direct tool entry points which do not have a capability owner. The
#: route policy for capability-backed entry points lives on the
#: capability's execution phase and is derived below.
AUXILIARY_OPERATIONS: Tuple[Operation, ...] = (
    Operation(
        name="build reel variants",
        entry_point="library.tools.reel_build.build_reel_variants",
        exclusion=RESOLVE_CURSOR,
        why="Two versions of one reel, both built into the same project."),
    Operation(
        name="promote staged reels",
        entry_point="library.tools.reel_build.promote_staged_reels",
        exclusion=RESOLVE_CURSOR,
        why="The ONLY place an approved timeline is deleted. A cursor "
            "that moved mid-promotion deletes the wrong name."),
    Operation(
        name="apply fusion comps",
        entry_point="library.tools.execution.apply_fusion_comps",
        exclusion=RESOLVE_CURSOR,
        why="ImportFusionComp acts on the current timeline's items, and "
            "must run in its own process (AGENTS.md 5)."),
    Operation(
        name="render segments",
        entry_point="library.tools.segment_renderer",
        exclusion=RESOLVE_CURSOR,
        why="Adds to the render queue, which is global, and renders "
            "what is current."),
    Operation(
        name="capture a frame for firstmate",
        entry_point="library.tools.marker_capture",
        exclusion=RESOLVE_CURSOR,
        human_initiated=True,
        why="GrabStill writes to the gallery, which is global, and "
            "reads the playhead, which is the cursor - but the captain "
            "presses it, so it prefers the instance rather than "
            "queueing for it."),
    Operation(
        name="read the captain's markers",
        entry_point="library.tools.marker_feedback.pull",
        exclusion=RESOLVE_READ,
        why="Reads markers off named timelines. Names its handles; "
            "takes no cursor."),
    Operation(
        name="snapshot a timeline",
        entry_point="library.tools.timeline_ingest.snapshot_timeline",
        exclusion=RESOLVE_READ,
        why="Reads items off a named timeline handle."),
    Operation(
        name="verify conformance",
        entry_point="library.tools.timeline_conformance.verify_timeline",
        exclusion=RESOLVE_READ,
        why="Reads a built timeline back and compares against the plan."),
    Operation(
        name="verify a timeline against the SOP",
        entry_point="library.skills.verify_timeline.skill.run",
        exclusion=RESOLVE_READ,
        why="The skill entry point over the row above. A skill is invoked "
            "by import OR by shell, so it is dispatchable on its own and "
            "must contend as the read it is - routing only the tool it "
            "wraps would leave the shell form free."),
    Operation(
        name="read a reel",
        entry_point="library.tools.reel_read.read_reel",
        exclusion=RESOLVE_READ,
        why="The one sanctioned probe (AGENTS.md 15)."),
    Operation(
        name="record a captain edit",
        entry_point="library.tools.captain_edits.record_edit",
        exclusion=DECLARATION,
        declaration="captain_edits",
        why="Read-modify-write of one project-wide store. Two agents "
            "recording different edits lose one of them without a key."),
    Operation(
        name="record an edit-ledger row",
        entry_point="library.tools.edit_ledger.record_row",
        exclusion=DECLARATION,
        declaration="edit_ledger",
        why="Read-modify-write of one project-wide store, keyed by "
            "the row identity. Two agents recording different rows "
            "lose one of them without a key."),
    Operation(
        name="record a variant build",
        entry_point="library.tools.versions.variants.record_build",
        exclusion=DECLARATION,
        declaration="reel_variant_builds",
        why="Read-modify-write keyed by reel."),
    Operation(
        name="sign a reel off",
        entry_point="library.tools.reel_signoff",
        exclusion=DECLARATION,
        declaration="reel_signoff",
        why="Read-modify-write keyed by reel."),
    Operation(
        name="write external state from a live timeline",
        entry_point="library.tools.timeline_ingest.write_external",
        exclusion=DECLARATION,
        declaration="timeline_ingest",
        why="Writes supplied state into `external/state/`, whole-file."),
    Operation(
        name="reset the qualification project",
        entry_point="library.tools.qualification_project.reset",
        exclusion=RESOLVE_CURSOR,
        why="Switches the OPEN PROJECT away from the captain's and back, "
            "deletes and recreates Ren Qualification: the whole body is "
            "the cursor, held once."),
    Operation(
        name="qualify ren-resolved live",
        entry_point="library.tools.qualification_project.qualify",
        exclusion=RESOLVE_CURSOR,
        why="The reset above plus a broker it starts as a child, which "
            "inherits this lease; every check runs inside the one hold."),
)


def _capability_operations(registry=None) -> Tuple[Operation, ...]:
    """Routing rows derived from capability-owned execution phases."""
    from library.tools import operations

    source = operations.all() if registry is None else tuple(registry)
    by_mode = {FREE: "none", RESOLVE_READ: "shared",
               RESOLVE_CURSOR: "exclusive"}
    rows = []
    for operation in source:
        if not hasattr(operation, "execution"):
            continue
        for phase in getattr(operation.execution, "phases", ()):
            exclusion = next((name for name, mode in by_mode.items()
                              if mode == phase.resolve_mode), None)
            if exclusion is None:
                continue
            for entry_point in phase.entry_points:
                rows.append(Operation(
                    name=f"{operation.name}:{phase.name}",
                    entry_point=entry_point, exclusion=exclusion,
                    why=phase.why or "Derived from the capability execution phase."))
    return tuple(rows)


#: Computed view for readers and tests. The authored routing for a
#: capability exists only once, in its `ExecutionPhase`.
OPERATIONS: Tuple[Operation, ...] = (
    *_capability_operations(), *AUXILIARY_OPERATIONS)
BY_ENTRY_POINT: Dict[str, Operation] = {op.entry_point: op
                                        for op in OPERATIONS}


def problems(registry=None) -> list[str]:
    """Check that derived and auxiliary route declarations do not disagree."""
    rows = (*_capability_operations(registry), *AUXILIARY_OPERATIONS)
    seen = {}
    out = []
    for row in rows:
        previous = seen.get(row.entry_point)
        if previous is not None:
            out.append(f"route {row.entry_point} is declared by both "
                       f"{previous.name} and {row.name}")
        else:
            seen[row.entry_point] = row
    return out


def route(entry_point: str) -> Operation:
    """The exclusion for one entry point.

    An unknown entry point is `FREE`, and the caller is told so by the
    returned row rather than by a guess: a supervisor that dispatches
    an unlisted Resolve caller in parallel is the failure this table
    prevents, and `tests/contracts/test_resolve_guard_wiring.py` is what stops an
    unlisted Resolve caller existing in the first place.
    """
    found = BY_ENTRY_POINT.get(entry_point)
    if found is not None:
        return found
    return Operation(name=entry_point, entry_point=entry_point,
                     exclusion=FREE,
                     why="Not in OPERATIONS: reaches neither Resolve nor "
                         "a shared file, so nothing constrains it.")


def may_run_together(first: str, second: str) -> bool:
    """May a supervisor dispatch these two at the same moment?

    The whole routing rule, in one predicate. Two exclusives never;
    an exclusive beside anything that takes the lease never; two
    declaration writes always (they contend per KEY, in
    `declaration_keys`, not per dispatch); anything beside `FREE`
    always.
    """
    a, b = route(first), route(second)
    if a.is_exclusive() and b.takes_lease():
        return False
    if b.is_exclusive() and a.takes_lease():
        return False
    return True


def describe() -> str:
    """The table, for a supervisor or a report to read back."""
    lines = []
    for op in OPERATIONS:
        lines.append(f"{op.exclusion:<15} {op.name:<42} {op.entry_point}")
    return "\n".join(lines)


if __name__ == "__main__":  # pragma: no cover - a reading, not a gate
    print(describe())
