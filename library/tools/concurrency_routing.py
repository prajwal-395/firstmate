"""Which work runs in parallel and which serialises. One table.

The captain's ruling of 2026-09-12 that this exists for: *"get the
async locking and gating and declaration keys and routing all figured
and implemented ... to account for the ability to horizontally scale
across me and the LLM(s) working on video projects using a single
davinci instance on my local computer"* - and, said plainly, *"today
that judgement is mine and I have got it wrong twice today"*.

So this is not advice. It is a lookup a supervisor performs without
judgement: name the operation, read the exclusion, dispatch or queue.

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

`DECLARATION`      Writes a keyed file under `external/` or
                   `pipeline_output/review/`. No Resolve. Serialises
                   only against another writer of the SAME FILE, and
                   conflicts only against the same ENTRY KEY -
                   `library/tools/declaration_keys.py`.

`FREE`             Touches neither. Vision, ffmpeg, Remotion, planning,
                   the LLM calls, every test whose Resolve is a mock.
                   Unbounded parallelism, and it is most of a build.

The whole point of the split is the last row. A reel build is minutes
of `FREE` around seconds of `RESOLVE_CURSOR`. Routing the build as one
unit would serialise the minutes to protect the seconds and delete the
parallelism this exists to provide.

What is NOT in this table
-------------------------
A second Resolve instance, or any isolation approach. The scripting
bridge is a single fixed port with no instance selector, so a second
headless Resolve is unaddressable by our own code. That is a filed
captain decision (`vep-resolve-statefulness-hazards`), and it is what
makes cooperation the only available route rather than one option
among several.

`tests/test_concurrency_routing.py`.
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


#: Every operation in this engine that reaches Resolve or a shared
#: file, with the entry point a supervisor dispatches.
#:
#: An operation NOT in this table is `FREE` by omission and that is
#: deliberate: the table names what constrains, so adding a Resolve
#: caller means adding a row, and `tests/test_resolve_guard_wiring.py`
#: fails when a module connects to Resolve without one.
OPERATIONS: Tuple[Operation, ...] = (
    Operation(
        name="build reels",
        entry_point="library.tools.reel_build.rebuild_reels_in_project",
        exclusion=RESOLVE_CURSOR,
        why="Creates and fills timelines. The cursor moves per reel and "
            "every caption, comp and overlay is appended through it."),
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
        name="render the edit timeline",
        entry_point="library.steps.step_6_01_render.resolve_build_timeline",
        exclusion=RESOLVE_CURSOR,
        why="Builds the whole edit timeline: pool imports, track "
            "layout, placement, stabilisation, render queue."),
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
        name="render out",
        entry_point="library.tools.execution.resolve_render",
        exclusion=RESOLVE_CURSOR,
        why="The render queue is one queue for the whole instance."),
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
        name="record a variant build",
        entry_point="library.tools.variant_choice.record_build",
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
        why="Writes supplied state into `external/`, whole-file."),
    Operation(
        name="analyse footage",
        entry_point="library.steps.step_1_03_semantic_analysis",
        exclusion=FREE,
        why="Vision on files. Never opens Resolve. The example the "
            "table exists to keep parallel."),
    Operation(
        name="render subtitles",
        entry_point="library.steps.step_4_05_render_subtitles",
        exclusion=FREE,
        why="Remotion and ffmpeg on files."),
)

BY_ENTRY_POINT: Dict[str, Operation] = {op.entry_point: op
                                        for op in OPERATIONS}


def route(entry_point: str) -> Operation:
    """The exclusion for one entry point.

    An unknown entry point is `FREE`, and the caller is told so by the
    returned row rather than by a guess: a supervisor that dispatches
    an unlisted Resolve caller in parallel is the failure this table
    prevents, and `tests/test_resolve_guard_wiring.py` is what stops an
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
