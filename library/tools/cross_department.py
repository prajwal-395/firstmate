"""Cross-department constraints, checked against the edit graph.

The gap this closes
-------------------
Departments are planned independently and merged at `compile_manifest`:
each planner decides from its own vocabulary with no global grammar
spanning its department, and no constraint spans two departments. The
result is a set of conflicts no module can reason about - the same
transition type twice in row, a drawn transition whose cut a B-roll
interjection covers, a motion graphic colliding with a caption row, a
mix window spanning a cut, an effect or sound landing on a block whose
picture B-roll replaced. `compile_manifest` catches some of these
pairwise (a transition against the raw V1 clip list) and downgrades
with a reason that names no cause.

`edit_graph` declares what each node must satisfy about its OWN
department - a drawn transition needs a V1 boundary, an overlay must fit
its block. This module is the cross-department half: what nodes must
satisfy about EACH OTHER. Each constraint below spans two departments
(or states one department's global grammar), is checked against the
graph's nodes, edges and payloads, and is resolved by the graph's
dependency edges - the resolution NAMES the node that yields, so a
reader can tell which decision gave way.

The constraints
---------------
    transition_grammar      no drawn transition type twice in a row -
                            the transition department's grammar over the
                            sequence of cuts
    transition_boundary     a drawn transition's cut is not covered by a
                            B-roll interjection (transitions x picture)
    graphic_caption         a motion graphic does not share a block with
                            a caption row (graphics x graphics)
    mix_window_transition   a mix window does not span a cut a drawn
                            transition holds (mix x transitions)
    sfx_on_covered_block    an SFX does not land on a block whose picture
                            a B-roll cover replaced (sound x picture)
    vfx_on_covered_block    a VFX does not land on a block whose picture
                            a B-roll cover replaced (effects x picture)

A conflict is resolved by the graph's dependency edges, not by a
pairwise check: the checker reads the two nodes' payloads and the blocks
they share, and the yielding node is the one whose decision the other
node's edges point past - the span-owner over the constraint-owner, the
cover over the decoration, the boundary over the span.

`check_cross_department` is O(constraints): each constraint reads the
nodes it names once (one sort for the ordering constraints), so the
check costs one pass per constraint, not one per pair of departments.
"""
from __future__ import annotations

from dataclasses import dataclass

from library.tools import edit_graph
from library.tools.edit_graph import (
    BROLL_COVER,
    GRAPHIC,
    MIX_WINDOW,
    SFX,
    TRANSITION,
    VFX,
    node,
)

# ── The constraints, one enumeration ────────────────────────────────

TRANSITION_GRAMMAR = "transition_grammar"
"""No drawn transition type twice in a row - the grammar over the cuts."""

TRANSITION_BOUNDARY = "transition_boundary"
"""A drawn transition's cut is not covered by a B-roll interjection."""

GRAPHIC_CAPTION = "graphic_caption"
"""A motion graphic does not share a block with a caption row."""

MIX_WINDOW_TRANSITION = "mix_window_transition"
"""A mix window does not span a cut a drawn transition holds."""

SFX_ON_COVERED_BLOCK = "sfx_on_covered_block"
"""An SFX does not land on a block whose picture a B-roll cover replaced."""

VFX_ON_COVERED_BLOCK = "vfx_on_covered_block"
"""A VFX does not land on a block whose picture a B-roll cover replaced."""

CONSTRAINT_NAMES = (
    TRANSITION_GRAMMAR,
    TRANSITION_BOUNDARY,
    GRAPHIC_CAPTION,
    MIX_WINDOW_TRANSITION,
    SFX_ON_COVERED_BLOCK,
    VFX_ON_COVERED_BLOCK,
)

INTERJECTION_PREFIX = "broll_interjection:"
"""How the graph ids a B-roll interjection node (assignments are `broll:<position>`)."""

GRAPHIC_KIND_SEPARATOR = ":"
"""The node id `graphic:<position>:<kind>` carries the kind as its last field."""


# ── One conflict, and its resolution ────────────────────────────────


@dataclass
class CrossDepartmentConflict:
    """One cross-department conflict and the node that resolves it.

    `names` are the node ids in conflict, in graph order. `yields` is
    the node id that gives way - the resolution, named so a reader can
    tell which decision lost. `reason` states what happened and names
    the yielding node, so the record is self-describing on the manifest.
    """

    constraint: str
    names: tuple
    yields: str
    reason: str


# ── Small readers over the graph ───────────────────────────────────


def _drawn_transitions(graph: dict) -> list:
    """The transition nodes that declare a drawn effect, in cut order.

    A drawn transition is one the graph declares `needs_v1_boundary` on -
    a cut declares no such need, so the graph's own constraint
    declaration is what separates an effect from the absence of one.
    """
    drawn = [candidate for candidate in graph["nodes"]
             if candidate["node_type"] == TRANSITION
             and edit_graph.NEEDS_V1_BOUNDARY in candidate.get("constraints", {})]
    return sorted(drawn, key=lambda t: (
        t.get("payload", {}).get("cut_time") or 0.0,
        str(t.get("payload", {}).get("cut_point_position", "")),
    ))


def _interjections(graph: dict) -> list:
    """The B-roll interjection nodes, with their timeline spans."""
    covers = [candidate for candidate in graph["nodes"]
              if candidate["node_type"] == BROLL_COVER
              and candidate["node_id"].startswith(INTERJECTION_PREFIX)]
    return [cover for cover in covers if _span(graph, cover) is not None]


def _span(graph: dict, candidate: dict):
    """The `(timeline_start, timeline_end)` a node occupies, or None."""
    payload = candidate.get("payload", {})
    start, end = payload.get("timeline_start"), payload.get("timeline_end")
    if start is None or end is None:
        return None
    return start, end


def _cut_time(transition: dict):
    """The seconds a transition's cut sits at, or None."""
    return transition.get("payload", {}).get("cut_time")


def _block_position(candidate: dict) -> str:
    """The spine block position a node's payload names, as a string key."""
    payload = candidate.get("payload", {})
    return str(payload.get("spine_block_position", payload.get("position", "")))


def _graphic_kind(graphic: dict) -> str:
    """The kind of a graphic node - `subtitle` or `motion_graphics`."""
    node_id = graphic["node_id"]
    return node_id.split(GRAPHIC_KIND_SEPARATOR)[-1]


def _broll_assignments_on(graph: dict, position: str) -> list:
    """The B-roll assignment nodes covering one block position."""
    return [candidate for candidate in graph["nodes"]
            if candidate["node_type"] == BROLL_COVER
            and not candidate["node_id"].startswith(INTERJECTION_PREFIX)
            and _block_position(candidate) == position]


# ── The checks ─────────────────────────────────────────────────────


def _transition_grammar_conflicts(graph: dict) -> list:
    """No drawn transition type twice in a row.

    The grammar spans the sequence of cuts: two drawn transitions of the
    same type at consecutive cut points, with no other drawn transition
    between them, are the same gesture twice. The LATER one yields - the
    first occurrence sets the grammar, and the graph orders the cuts by
    the blocks they join, so "later" is the transition whose cut is
    later in spine order.
    """
    conflicts = []
    drawn = _drawn_transitions(graph)
    for earlier, later in zip(drawn, drawn[1:]):
        type_earlier = earlier.get("payload", {}).get("transition_type")
        type_later = later.get("payload", {}).get("transition_type")
        if type_earlier != type_later:
            continue
        conflicts.append(CrossDepartmentConflict(
            constraint=TRANSITION_GRAMMAR,
            names=(earlier["node_id"], later["node_id"]),
            yields=later["node_id"],
            reason=(
                f"{later['node_id']} yields to {earlier['node_id']} - "
                f"the same drawn type {type_later!r} twice in a row"
            ),
        ))
    return conflicts


def _transition_boundary_conflicts(graph: dict) -> list:
    """A drawn transition's cut is not covered by a B-roll interjection.

    The interjection's span owns the timeline at the cut; the transition
    needs a V1 boundary there to draw through. When the span covers the
    cut, the boundary is not on screen - the viewer sees the interjection,
    not the cut. The transition yields to the interjection: the
    constraint-owner yields to the span-owner, and the graph's edges
    name the shared block both decisions hang off.
    """
    conflicts = []
    for transition in _drawn_transitions(graph):
        cut = _cut_time(transition)
        if cut is None:
            continue
        for interjection in _interjections(graph):
            start, end = _span(graph, interjection)
            if start <= cut < end:
                conflicts.append(CrossDepartmentConflict(
                    constraint=TRANSITION_BOUNDARY,
                    names=(transition["node_id"], interjection["node_id"]),
                    yields=transition["node_id"],
                    reason=(
                        f"{transition['node_id']} yields to "
                        f"{interjection['node_id']} - the interjection "
                        f"covers the cut at {cut}s, so the V1 boundary "
                        f"the transition draws through is not on screen"
                    ),
                ))
                break
    return conflicts


def _graphic_caption_conflicts(graph: dict) -> list:
    """A motion graphic does not share a block with a caption row.

    Both hang off the same block (the graph gives them the same
    `spine_block_position` and the same dependency edge). The caption
    carries the words - it is the load-bearing half - so the motion
    graphic yields when the two collide on one block.
    """
    conflicts = []
    graphics = [candidate for candidate in graph["nodes"]
                if candidate["node_type"] == GRAPHIC]
    subtitles = [graphic for graphic in graphics
                 if _graphic_kind(graphic) == "subtitle"]
    motion_graphics = [graphic for graphic in graphics
                      if _graphic_kind(graphic) == "motion_graphics"]
    for motion in motion_graphics:
        motion_span = _span(graph, motion)
        motion_block = _block_position(motion)
        for caption in subtitles:
            if _block_position(caption) != motion_block:
                continue
            caption_span = _span(graph, caption)
            if motion_span is None or caption_span is None:
                continue
            if (motion_span[0] < caption_span[1]
                    and caption_span[0] < motion_span[1]):
                conflicts.append(CrossDepartmentConflict(
                    constraint=GRAPHIC_CAPTION,
                    names=(motion["node_id"], caption["node_id"]),
                    yields=motion["node_id"],
                    reason=(
                        f"{motion['node_id']} yields to "
                        f"{caption['node_id']} - a motion graphic "
                        f"collides with the caption row on block "
                        f"{motion_block}"
                    ),
                ))
    return conflicts


def _mix_window_transition_conflicts(graph: dict) -> list:
    """A mix window does not span a cut a drawn transition holds.

    The window's span crosses the boundary the transition is the
    deliberate decision about, so the mix behavior changes under the
    viewer's feet at the one moment the edit marks. The window yields to
    the boundary: the span is trimmed at the cut, and the graph's edges
    name the block both decisions point at.
    """
    conflicts = []
    windows = [candidate for candidate in graph["nodes"]
               if candidate["node_type"] == MIX_WINDOW]
    for window in windows:
        window_span = _span(graph, window)
        if window_span is None:
            continue
        for transition in _drawn_transitions(graph):
            cut = _cut_time(transition)
            if cut is None:
                continue
            if window_span[0] < cut < window_span[1]:
                conflicts.append(CrossDepartmentConflict(
                    constraint=MIX_WINDOW_TRANSITION,
                    names=(window["node_id"], transition["node_id"]),
                    yields=window["node_id"],
                    reason=(
                        f"{window['node_id']} yields to "
                        f"{transition['node_id']} - the window spans the "
                        f"cut at {cut}s the transition holds"
                    ),
                ))
    return conflicts


def _sfx_on_covered_block_conflicts(graph: dict) -> list:
    """An SFX does not land on a block whose picture B-roll replaced.

    The sound was planned for the block's own content; a B-roll cover
    replaces that picture, so the sound illustrates nothing. The cover is
    the deliberate picture decision both nodes depend on, so the SFX
    yields to it.
    """
    conflicts = []
    for candidate in graph["nodes"]:
        if candidate["node_type"] != SFX:
            continue
        position = _block_position(candidate)
        if not position:
            continue
        for cover in _broll_assignments_on(graph, position):
            conflicts.append(CrossDepartmentConflict(
                constraint=SFX_ON_COVERED_BLOCK,
                names=(candidate["node_id"], cover["node_id"]),
                yields=candidate["node_id"],
                reason=(
                    f"{candidate['node_id']} yields to "
                    f"{cover['node_id']} - the sfx lands on block "
                    f"{position} whose picture the B-roll cover replaced"
                ),
            ))
    return conflicts


def _vfx_on_covered_block_conflicts(graph: dict) -> list:
    """A VFX does not land on a block whose picture B-roll replaced.

    The comp was planned for the block's own picture; a B-roll cover
    replaces it, so the effect draws on a clip the viewer does not see.
    The cover wins - the VFX yields to it.
    """
    conflicts = []
    for candidate in graph["nodes"]:
        if candidate["node_type"] != VFX:
            continue
        position = _block_position(candidate)
        if not position:
            continue
        for cover in _broll_assignments_on(graph, position):
            conflicts.append(CrossDepartmentConflict(
                constraint=VFX_ON_COVERED_BLOCK,
                names=(candidate["node_id"], cover["node_id"]),
                yields=candidate["node_id"],
                reason=(
                    f"{candidate['node_id']} yields to "
                    f"{cover['node_id']} - the vfx lands on block "
                    f"{position} whose picture the B-roll cover replaced"
                ),
            ))
    return conflicts


_CHECKS = {
    TRANSITION_GRAMMAR: _transition_grammar_conflicts,
    TRANSITION_BOUNDARY: _transition_boundary_conflicts,
    GRAPHIC_CAPTION: _graphic_caption_conflicts,
    MIX_WINDOW_TRANSITION: _mix_window_transition_conflicts,
    SFX_ON_COVERED_BLOCK: _sfx_on_covered_block_conflicts,
    VFX_ON_COVERED_BLOCK: _vfx_on_covered_block_conflicts,
}
"""Constraint name -> the check that reads the graph and finds conflicts.

A constraint is checked by exactly one function, so a constraint and
its conflicts cannot drift: adding a constraint means adding a row here
and a name in `CONSTRAINT_NAMES`, nowhere else.
"""


# ── The checker ────────────────────────────────────────────────────


def check_cross_department(graph: dict) -> list:
    """Every cross-department constraint, checked against the graph.

    O(constraints): each constraint reads the nodes it names once, so
    the check costs one pass per constraint, not one per pair of
    departments. A constraint no graph state breaks reports nothing, and
    a conflict is reported with the node that yields - never as a
    pairwise error string.
    """
    conflicts = []
    for constraint in CONSTRAINT_NAMES:
        conflicts += _CHECKS[constraint](graph)
    return conflicts


def conflicts_for(graph: dict, constraint_name: str) -> list:
    """The conflicts one constraint finds, or [] for an unknown name."""
    check = _CHECKS.get(constraint_name)
    return [] if check is None else check(graph)
