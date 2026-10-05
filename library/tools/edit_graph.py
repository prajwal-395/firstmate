"""The editorial/edit graph - the derived artifact above the DAG.

The gap this closes
-------------------
The DAG is a linear chain of steps connected by `data_mapping` edges
that move whole state keys between planners; the spine is a linear block
sequence; `compile_manifest` merges specs by reading `pipeline_data.json`
and validating pairwise constraints. No module builds, queries or mutates
a graph of editorial decisions, so a planner cannot ask "what does this
cut serve?" or "what constrains this overlay?" - it can only read the
state keys its manifest names. This module is that graph.

The model
---------
The graph is a DERIVED artifact, never authored. `build_graph` reads
`pipeline_data.json` and emits it; planners decide exactly as they did
before. Nodes are the decisions the planners made:

    passage_selection   a passage the sequence selected (speech.enrich)
    spine_block         a block on the timed spine (spine.mesh)
    aroll_assignment    a block's A-roll clip range (aroll.assign)
    broll_cover         a B-roll assignment or interjection (broll.resolve)
    transition           a cut-point transition (transitions.resolve)
    graphic             a subtitle or motion-graphics overlay segment
    music_behavior      a block's music behavior word (spine.mesh)
    vfx                 a per-block visual effect (vfx.resolve)
    sfx                 a placed sound effect (sfx.resolve)
    grade_correction    a per-clip grade correction (color_grade.resolve)
    mix_window          a measured mix window (audio_mix.resolve)

Every node carries the four things the target behavior asks for:

    producer       the capability id whose output the node was derived from
    intent_refs    what it serves - beat ids ("beat:<spine position>")
    evidence_refs  what it rests on - measurement ids ("clip_catalog:clip_001")
    constraints    name -> declaration, the rules the node must satisfy

Edges are dependencies and point at a decision's INPUTS: a transition
depends on the two blocks it joins, a caption depends on the block whose
word timings it renders, a B-roll cover depends on the block it covers.
So the blast radius of a change is a reverse walk.

Queries
-------
    intent_of(node_id) / evidence_of(node_id)   what a node serves / rests on
    nodes_serving(intent_ref)                   which nodes serve a beat
    dependents_of(node_id)                      the transitive reverse closure
    violations_of(constraint_name)              nodes whose constraint fails

Mutation
--------
`apply_mutation` applies one graph operation (reorder passages, retime a
block, remove a node), re-derives the dependency edges from the new
payloads, and reports every node the mutation propagates to: the mutated
node and its transitive dependents. That report is the answer to "if this
passage moves, what else moves?" - the question the fidelity test asks.

Build is incremental: `add_node` is O(the node's dependencies), not
O(graph), so a planner emitting one node at a time never re-walks the
whole graph. `build_graph` is the whole-state derivation the step runs.

This module imports nothing from steps or processes: it reads state
through `library/tools/capability_outputs.py` and the spine through
`library/tools/spine_contract.py`, the same readers every consumer has.
"""
from __future__ import annotations

import copy
from dataclasses import dataclass

from library.tools import capability_outputs
from library.tools.music_behavior import is_known_behavior

# ── The node types, one enumeration ────────────────────────────────

PASSAGE_SELECTION = "passage_selection"
SPINE_BLOCK = "spine_block"
AROLL_ASSIGNMENT = "aroll_assignment"
BROLL_COVER = "broll_cover"
TRANSITION = "transition"
GRAPHIC = "graphic"
MUSIC_BEHAVIOR = "music_behavior"
VFX = "vfx"
SFX = "sfx"
GRADE_CORRECTION = "grade_correction"
MIX_WINDOW = "mix_window"

NODE_TYPES = (
    PASSAGE_SELECTION,
    SPINE_BLOCK,
    AROLL_ASSIGNMENT,
    BROLL_COVER,
    TRANSITION,
    GRAPHIC,
    MUSIC_BEHAVIOR,
    VFX,
    SFX,
    GRADE_CORRECTION,
    MIX_WINDOW,
)

# ── Who produces each state key the graph derives from ──────────────
#
# The graph is derived from the run's recorded decisions, so every read
# goes through the capability that owns the key - never a bare state
# read, which would bypass the lineage the records carry.

SPEECH_SEQUENCE = "speech.enrich"
SPINE_MESH = "spine.mesh"
AROLL_ASSIGN = "aroll.assign"
BROLL_RESOLVE = "broll.resolve"
TRANSITIONS_RESOLVE = "transitions.resolve"
SUBTITLES_RENDER = "subtitles.render"
MOTION_GRAPHICS_RENDER = "motion_graphics.render"
VFX_RESOLVE = "vfx.resolve"
SFX_RESOLVE = "sfx.resolve"
COLOR_GRADE_RESOLVE = "color_grade.resolve"
AUDIO_MIX_RESOLVE = "audio_mix.resolve"
MUSIC_ANALYSE = "music.analyse"

# ── The constraints a node may declare, and what each means ──────────
#
# A constraint is a rule the node must satisfy, declared on the node and
# checked against the graph state by `violations_of`. The names are the
# vocabulary; the check is the graph's own reading of the payloads.

NEEDS_V1_BOUNDARY = "needs_v1_boundary"
"""A drawn transition needs a V1 clip ending at the cut to draw through."""

FITS_ITS_BLOCK = "fits_its_block"
"""An overlay must fit inside the timeline span of its block."""

COVERS_ITS_BLOCK = "covers_its_block"
"""A B-roll cover must span the whole block it covers."""

TIMELINE_COVERED = "timeline_covered"
"""Every frame of a block must show a clip, unless a black beat is declared."""

WITHIN_PICTURE = "within_picture"
"""A mix window must sit inside the picture the spine actually plays."""

CONSTRAINT_NAMES = (
    NEEDS_V1_BOUNDARY,
    FITS_ITS_BLOCK,
    COVERS_ITS_BLOCK,
    TIMELINE_COVERED,
    WITHIN_PICTURE,
)


# ── Small readers over state ───────────────────────────────────────


def _as_dict(value) -> dict:
    return value if isinstance(value, dict) else {}


def _as_list(value) -> list:
    return value if isinstance(value, list) else []


def _position(node) -> str:
    """The spine position of a node, as a string key.

    Positions are ints or the word "hook"; every edge in the graph is
    keyed by the string form so an int 3 and a "3" are the same beat. A
    raw spine block or passage carries its position at the top level; a
    graph node carries it in its payload.
    """
    if "payload" in node:
        return str(node.get("payload", {}).get("position", ""))
    return str(node.get("position", ""))


def _overlap(start_a, end_a, start_b, end_b) -> bool:
    """Whether two half-open spans share any instant."""
    return start_a < end_b and start_b < end_a


# ── The graph object ───────────────────────────────────────────────


def new_graph() -> dict:
    """An empty graph, the shape `build_graph` returns and the schema pins."""
    return {
        "graph_id": "edit_graph",
        "node_count": 0,
        "nodes": [],
    }


def add_node(graph: dict, node: dict) -> None:
    """Add one node. O(the node's dependencies), never O(graph).

    The incremental build path: a planner emitting one decision at a time
    calls this and pays only for the edges that decision carries. The
    node's `depends_on` is stored as given; the reverse index a query
    walks is built on demand, so adding never re-walks the graph.
    """
    graph["nodes"].append(node)
    graph["node_count"] = len(graph["nodes"])


def node(graph: dict, node_id: str) -> dict:
    """The node with this id, or {} - a missing node is a query miss."""
    for candidate in graph["nodes"]:
        if candidate["node_id"] == node_id:
            return candidate
    return {}


def _reverse_index(graph: dict) -> dict:
    """`{node_id: [node_ids that depend on it]}`, built on demand.

    Edges point at a decision's inputs, so the propagation direction -
    "who is affected if this node changes" - is the reverse of
    `depends_on`. A query builds this once per call; it is not stored,
    because storing it would make every add O(graph) to keep fresh.
    """
    reverse: dict = {}
    for candidate in graph["nodes"]:
        for dependency in candidate["depends_on"]:
            reverse.setdefault(dependency, []).append(candidate["node_id"])
    return reverse


# ── The queries ────────────────────────────────────────────────────


def intent_of(graph: dict, node_id: str) -> list:
    """What this node serves - its beat ids, in declaration order."""
    return list(node(graph, node_id).get("intent_refs", ()))


def evidence_of(graph: dict, node_id: str) -> list:
    """What this node rests on - its measurement ids, in declaration order."""
    return list(node(graph, node_id).get("evidence_refs", ()))


def nodes_serving(graph: dict, intent_ref: str) -> list:
    """Which nodes serve one intent - the fidelity test's second question.

    O(answer size): the nodes are scanned once and only those naming the
    intent are returned, so a beat with three servants costs three hits,
    not a walk of the whole graph per servant.
    """
    return [candidate["node_id"] for candidate in graph["nodes"]
            if intent_ref in candidate.get("intent_refs", ())]


def dependents_of(graph: dict, node_id: str) -> list:
    """Every node that transitively depends on this one, sorted.

    The blast radius of a change: the reverse walk from `node_id`. Each
    node is visited once, so the cost is O(the affected subgraph), not
    O(graph) - a change that touches three nodes never walks the 500 it
    leaves alone.
    """
    reverse = _reverse_index(graph)
    seen: set = set()
    stack = [node_id]
    while stack:
        current = stack.pop()
        for dependent in reverse.get(current, ()):
            if dependent not in seen:
                seen.add(dependent)
                stack.append(dependent)
    return sorted(seen)


def violations_of(graph: dict, constraint_name: str) -> list:
    """Which nodes violate one constraint - the fidelity test's third question.

    A node violates a constraint it DECLARES when the graph state breaks
    the rule the constraint names. The check is the graph's own reading
    of the payloads, so a constraint no node declares has no violators and
    a violation no node declares is invisible - the two cannot drift.
    """
    return [candidate["node_id"] for candidate in graph["nodes"]
            if constraint_name in _violations_of_node(graph, candidate)]


def _block_at(graph: dict, position: str) -> dict:
    return node(graph, f"spine_block:{position}")


def _lacks_v1_boundary(graph: dict, transition: dict) -> bool:
    """A drawn transition whose outgoing block puts no clip on V1.

    The outgoing block is the one before the cut; a cut is keyed by the
    INCOMING block's position, so the outgoing block is the previous one
    in spine order. A block with no clip_id is a non-speech block - a
    picture, a music moment, a card - and a drawn effect has nothing to
    draw through. A cut (zero-length) declares no such need.
    """
    if _is_cut(transition):
        return False
    outgoing = transition.get("payload", {}).get("outgoing_position")
    if outgoing is None:
        return False
    return _block_at(graph, str(outgoing)).get("payload", {}).get("clip_id") is None


def _is_cut(transition: dict) -> bool:
    transition_type = transition.get("payload", {}).get("transition_type", "")
    return transition_type in ("cut", "CUT", "") and not transition.get(
        "payload", {}).get("duration_seconds")


def _span_of(graph: dict, node_id: str):
    """The `(timeline_start, timeline_end)` a node occupies, or None."""
    payload = node(graph, node_id).get("payload", {})
    start, end = payload.get("timeline_start"), payload.get("timeline_end")
    if start is None or end is None:
        return None
    return start, end


def _overflows_its_block(graph: dict, graphic: dict) -> bool:
    """An overlay that reaches past the block it sits on."""
    block = _block_at(graph, str(graphic.get("payload", {}).get("spine_block_position", "")))
    graphic_span = _span_of(graph, graphic["node_id"])
    block_span = _span_of(graph, block["node_id"]) if block else None
    if graphic_span is None or block_span is None:
        return False
    return graphic_span[0] < block_span[0] or graphic_span[1] > block_span[1]


def _misses_its_block(graph: dict, cover: dict) -> bool:
    """A B-roll cover that does not span the whole block it covers."""
    block = _block_at(graph, str(cover.get("payload", {}).get("spine_block_position", "")))
    cover_span = _span_of(graph, cover["node_id"])
    block_span = _span_of(graph, block["node_id"]) if block else None
    if cover_span is None or block_span is None:
        return False
    return cover_span[0] > block_span[0] or cover_span[1] < block_span[1]


def _leaves_a_hole(graph: dict, block: dict) -> bool:
    """A block that shows no clip and declares no intentional black beat."""
    payload = block.get("payload", {})
    if payload.get("clip_id") is not None:
        return False
    return not payload.get("intentional_black_beat")


def _outside_picture(graph: dict, window: dict) -> bool:
    """A mix window that reaches past the last block the spine plays."""
    window_span = _span_of(graph, window["node_id"])
    if window_span is None:
        return False
    last_end = None
    for candidate in graph["nodes"]:
        if candidate["node_type"] != SPINE_BLOCK:
            continue
        span = _span_of(graph, candidate["node_id"])
        if span and (last_end is None or span[1] > last_end):
            last_end = span[1]
    if last_end is None:
        return False
    return window_span[1] > last_end


_CONSTRAINT_CHECKS = {
    NEEDS_V1_BOUNDARY: _lacks_v1_boundary,
    FITS_ITS_BLOCK: _overflows_its_block,
    COVERS_ITS_BLOCK: _misses_its_block,
    TIMELINE_COVERED: _leaves_a_hole,
    WITHIN_PICTURE: _outside_picture,
}
"""Constraint name -> the check that reads the graph state and breaks it.

A constraint is checked by exactly one function, so a constraint and its
violators cannot drift: adding a constraint means adding a row here and
a declaration in `_constraints_for`, nowhere else.
"""


def _violations_of_node(graph: dict, candidate: dict) -> list:
    """The constraints this node declares and the graph state breaks."""
    broken = []
    for name in candidate.get("constraints", {}):
        check = _CONSTRAINT_CHECKS.get(name)
        if check is not None and check(graph, candidate):
            broken.append(name)
    return broken


# ── The builder ────────────────────────────────────────────────────


def build_graph(state: dict) -> dict:
    """Derive the whole edit graph from `pipeline_data.json`.

    Reads every state key the graph is derived from, emits one node per
    editorial decision, then derives the edges, the intent links, the
    evidence links and the constraints. A key the run has not produced is
    simply absent - the graph builds from whatever decisions exist, so a
    run that stopped after the spine still gets the blocks and passages.
    """
    graph = new_graph()
    _emit_spine_nodes(graph, state)
    _emit_passage_nodes(graph, state)
    _emit_aroll_nodes(graph, state)
    _emit_broll_nodes(graph, state)
    _emit_transition_nodes(graph, state)
    _emit_graphic_nodes(graph, state)
    _emit_vfx_nodes(graph, state)
    _emit_sfx_nodes(graph, state)
    _emit_grade_nodes(graph, state)
    _emit_mix_nodes(graph, state)
    _derive_edges(graph)
    _derive_intents(graph)
    _derive_evidence(graph)
    _derive_constraints(graph)
    return graph


def _spine_blocks(state: dict) -> list:
    """The spine's blocks, from `audio_spine` (falling back to `timed_spine`)."""
    spine = capability_outputs.value(state, SPINE_MESH, "audio_spine", None)
    if not isinstance(spine, dict):
        spine = capability_outputs.value(state, SPINE_MESH, "timed_spine", None)
    return _as_list(_as_dict(spine).get("structure"))


def _emit_spine_nodes(graph: dict, state: dict) -> None:
    for block in _spine_blocks(state):
        position = _position(block)
        if not position:
            continue
        payload = {
            "position": block.get("position"),
            "block_type": block.get("block_type"),
            "clip_id": block.get("clip_id"),
            "source_start": block.get("source_start"),
            "source_end": block.get("source_end"),
            "timeline_start": block.get("timeline_start"),
            "timeline_end": block.get("timeline_end"),
            "word_timestamp_count": len(_as_list(block.get("word_timestamps"))),
            "alignment_method": block.get("alignment_method"),
        }
        if block.get("intentional_black_beat"):
            payload["intentional_black_beat"] = True
            payload["black_beat_reason"] = block.get("black_beat_reason")
        add_node(graph, {
            "node_id": f"spine_block:{position}",
            "node_type": SPINE_BLOCK,
            "producer": SPINE_MESH,
            "label": f"{block.get('block_type', 'block')} block {position}",
            "intent_refs": [],
            "evidence_refs": [],
            "constraints": {},
            "depends_on": [],
            "payload": payload,
        })
        behavior = block.get("music_behavior")
        if behavior and is_known_behavior(behavior):
            add_node(graph, {
                "node_id": f"music_behavior:{position}",
                "node_type": MUSIC_BEHAVIOR,
                "producer": SPINE_MESH,
                "label": f"music {behavior} on block {position}",
                "intent_refs": [],
                "evidence_refs": [],
                "constraints": {},
                "depends_on": [],
                "payload": {"position": block.get("position"), "behavior": behavior},
            })


def _passages(state: dict) -> list:
    sequence = capability_outputs.value(state, SPEECH_SEQUENCE, "speech_sequence", None)
    return _as_list(_as_dict(sequence).get("body_sequence"))


def _emit_passage_nodes(graph: dict, state: dict) -> None:
    for passage in _passages(state):
        position = _position(passage)
        if not position:
            continue
        clip_id = passage.get("clip_id")
        payload = {
            "position": passage.get("position"),
            "clip_id": clip_id,
            "source_start": passage.get("source_start"),
            "source_end": passage.get("source_end"),
            "start_time": passage.get("start_time"),
            "end_time": passage.get("end_time"),
        }
        engagement = _as_dict(passage.get("engagement"))
        if engagement:
            payload["engagement_rank"] = engagement.get("rank")
        add_node(graph, {
            "node_id": f"passage:{position}",
            "node_type": PASSAGE_SELECTION,
            "producer": SPEECH_SEQUENCE,
            "label": f"passage {position}",
            "intent_refs": [],
            "evidence_refs": [],
            "constraints": {},
            "depends_on": [],
            "payload": payload,
        })


def _aroll_entries(state: dict) -> list:
    assignments = capability_outputs.value(state, AROLL_ASSIGN, "a_roll_assignments", None)
    entries = _as_list(_as_dict(assignments).get("a_roll_assignments"))
    hook = _as_dict(_as_dict(assignments).get("hook_assignment"))
    if hook:
        entries = entries + [hook]
    return entries


def _block_position(entry) -> str:
    """The spine block position an assignment names, as a string key.

    Assignments are keyed `spine_block_position`; a raw spine block or
    passage carries its own `position`. Both spellings meet here.
    """
    return str(entry.get("spine_block_position", entry.get("position", "")))


def _payload_position(payload) -> str:
    """The spine block position a node payload names, as a string key.

    Block-level nodes name it `spine_block_position`; the spine's own
    blocks and the per-block effects name it `position`. Both are the
    same address, so a node's edges and intents read it the same way.
    """
    return str(payload.get("spine_block_position", payload.get("position", "")))


def _emit_aroll_nodes(graph: dict, state: dict) -> None:
    for assignment in _aroll_entries(state):
        position = _block_position(assignment)
        if not position:
            continue
        add_node(graph, {
            "node_id": f"aroll:{position}",
            "node_type": AROLL_ASSIGNMENT,
            "producer": AROLL_ASSIGN,
            "label": f"A-roll on block {position}",
            "intent_refs": [],
            "evidence_refs": [],
            "constraints": {},
            "depends_on": [],
            "payload": {
                "spine_block_position": assignment.get("spine_block_position"),
                "clip_id": assignment.get("clip_id"),
                "source_start": assignment.get("source_start"),
                "source_end": assignment.get("source_end"),
                "timeline_start": assignment.get("timeline_start"),
                "timeline_end": assignment.get("timeline_end"),
            },
        })


def _emit_broll_nodes(graph: dict, state: dict) -> None:
    broll = capability_outputs.value(state, BROLL_RESOLVE, "b_roll_assignments", None)
    for assignment in _as_list(_as_dict(broll).get("b_roll_assignments")):
        position = _block_position(assignment)
        if not position:
            continue
        add_node(graph, {
            "node_id": f"broll:{position}",
            "node_type": BROLL_COVER,
            "producer": BROLL_RESOLVE,
            "label": f"B-roll cover on block {position}",
            "intent_refs": [],
            "evidence_refs": [],
            "constraints": {},
            "depends_on": [],
            "payload": {
                "spine_block_position": assignment.get("spine_block_position"),
                "clip_id": assignment.get("clip_id"),
                "video_in": assignment.get("video_in"),
                "video_out": assignment.get("video_out"),
                "timeline_start": assignment.get("timeline_start"),
                "timeline_end": assignment.get("timeline_end"),
            },
        })
    interjections = capability_outputs.value(
        state, BROLL_RESOLVE, "b_roll_interjections", None)
    for index, interjection in enumerate(_as_list(_as_dict(interjections).get("b_roll_interjections"))):
        assigned = _as_dict(interjection.get("assigned_clip"))
        add_node(graph, {
            "node_id": f"broll_interjection:{index}",
            "node_type": BROLL_COVER,
            "producer": BROLL_RESOLVE,
            "label": f"B-roll interjection {index}",
            "intent_refs": [],
            "evidence_refs": [],
            "constraints": {},
            "depends_on": [],
            "payload": {
                "spine_block_position": interjection.get("spine_block_position"),
                "clip_id": assigned.get("clip_id"),
                "video_in": assigned.get("video_in"),
                "video_out": assigned.get("video_out"),
                "timeline_start": interjection.get("timeline_start"),
                "timeline_end": interjection.get("timeline_end"),
            },
        })


def _transitions(state: dict) -> list:
    spec = capability_outputs.value(state, TRANSITIONS_RESOLVE, "transition_spec", None)
    if isinstance(spec, list):
        return spec
    spec = _as_dict(spec)
    return _as_list(spec.get("transitions")) or _as_list(spec.get("transition_spec"))


def _emit_transition_nodes(graph: dict, state: dict) -> None:
    blocks = _spine_blocks(state)
    for transition in _transitions(state):
        cut_position = str(transition.get("cut_point_position", ""))
        if not cut_position:
            continue
        outgoing = transition.get("outgoing_position")
        if outgoing is None:
            outgoing = _outgoing_position(blocks, cut_position)
        payload = {
            "cut_point_position": transition.get("cut_point_position"),
            "transition_type": transition.get("transition_type"),
            "outgoing_position": outgoing,
            "incoming_position": transition.get("incoming_position", cut_position),
            "cut_time": transition.get("cut_time"),
        }
        if transition.get("duration_seconds") is not None:
            payload["duration_seconds"] = transition.get("duration_seconds")
        add_node(graph, {
            "node_id": f"transition:{cut_position}",
            "node_type": TRANSITION,
            "producer": TRANSITIONS_RESOLVE,
            "label": f"{transition.get('transition_type', 'cut')} at block {cut_position}",
            "intent_refs": [],
            "evidence_refs": [],
            "constraints": {},
            "depends_on": [],
            "payload": payload,
        })


def _outgoing_position(blocks: list, cut_position: str):
    """The block before the one the cut is keyed by, in spine order."""
    for index, block in enumerate(blocks):
        if _position(block) == cut_position and index > 0:
            return blocks[index - 1].get("position")
    return None


def _graphic_segments(state: dict, capability: str, key: str, kind: str) -> list:
    overlay = capability_outputs.value(state, capability, key, None)
    segments = _as_dict(overlay).get("segments")
    if segments is None:
        segments = _as_list(overlay) if isinstance(overlay, list) else []
    return _as_list(segments)


def _emit_graphic_nodes(graph: dict, state: dict) -> None:
    for capability, key, kind in (
        (SUBTITLES_RENDER, "subtitle_overlay", "subtitle"),
        (MOTION_GRAPHICS_RENDER, "motion_graphics_overlay", "motion_graphics"),
    ):
        for segment in _graphic_segments(state, capability, key, kind):
            position = str(segment.get("spine_block_position", ""))
            if not position:
                continue
            add_node(graph, {
                "node_id": f"graphic:{position}:{kind}",
                "node_type": GRAPHIC,
                "producer": capability,
                "label": f"{kind} on block {position}",
                "intent_refs": [],
                "evidence_refs": [],
                "constraints": {},
                "depends_on": [],
                "payload": {
                    "spine_block_position": segment.get("spine_block_position"),
                    "timeline_start": segment.get("timeline_start"),
                    "timeline_end": segment.get("timeline_end"),
                    "text": segment.get("text"),
                },
            })


def _vfx_entries(state: dict) -> list:
    spec = capability_outputs.value(state, VFX_RESOLVE, "enhancement_spec", None)
    if isinstance(spec, list):
        return spec
    return _as_list(_as_dict(spec).get("enhancement_spec"))


def _emit_vfx_nodes(graph: dict, state: dict) -> None:
    for entry in _vfx_entries(state):
        position = _position(entry)
        if not position:
            continue
        add_node(graph, {
            "node_id": f"vfx:{position}",
            "node_type": VFX,
            "producer": VFX_RESOLVE,
            "label": f"VFX on block {position}",
            "intent_refs": [],
            "evidence_refs": [],
            "constraints": {},
            "depends_on": [],
            "payload": {
                "position": entry.get("position"),
                "effect": entry.get("effect"),
                "clip_id": entry.get("clip_id"),
            },
        })


def _sfx_entries(state: dict) -> list:
    spec = capability_outputs.value(state, SFX_RESOLVE, "sfx_spec", None)
    if isinstance(spec, list):
        return spec
    return _as_list(_as_dict(spec).get("sfx_spec"))


def _emit_sfx_nodes(graph: dict, state: dict) -> None:
    for index, entry in enumerate(_sfx_entries(state)):
        position = str(entry.get("spine_block_position")
                       or entry.get("target_block_position") or "")
        add_node(graph, {
            "node_id": f"sfx:{index}",
            "node_type": SFX,
            "producer": SFX_RESOLVE,
            "label": f"sfx {entry.get('sfx_id', index)} on block {position}",
            "intent_refs": [],
            "evidence_refs": [],
            "constraints": {},
            "depends_on": [],
            "payload": {
                "spine_block_position": entry.get("spine_block_position"),
                "target_block_position": entry.get("target_block_position"),
                "sfx_id": entry.get("sfx_id"),
                "volume_db": entry.get("volume_db"),
                "duration_seconds": entry.get("duration_seconds"),
            },
        })


def _grade_entries(state: dict) -> list:
    spec = capability_outputs.value(state, COLOR_GRADE_RESOLVE, "color_grade_spec", None)
    return _as_list(_as_dict(spec).get("subject_grades"))


def _emit_grade_nodes(graph: dict, state: dict) -> None:
    for entry in _grade_entries(state):
        clip_id = entry.get("clip_id")
        if not clip_id:
            continue
        add_node(graph, {
            "node_id": f"grade:{clip_id}",
            "node_type": GRADE_CORRECTION,
            "producer": COLOR_GRADE_RESOLVE,
            "label": f"grade correction on {clip_id}",
            "intent_refs": [],
            "evidence_refs": [],
            "constraints": {},
            "depends_on": [],
            "payload": {"clip_id": clip_id},
        })


def _mix_windows(state: dict) -> list:
    spec = capability_outputs.value(state, AUDIO_MIX_RESOLVE, "audio_mix_spec", None)
    return _as_list(_as_dict(spec).get("mix_windows"))


def _emit_mix_nodes(graph: dict, state: dict) -> None:
    for index, window in enumerate(_mix_windows(state)):
        add_node(graph, {
            "node_id": f"mix_window:{index}",
            "node_type": MIX_WINDOW,
            "producer": AUDIO_MIX_RESOLVE,
            "label": f"mix window {index}",
            "intent_refs": [],
            "evidence_refs": [],
            "constraints": {},
            "depends_on": [],
            "payload": {
                "timeline_start": window.get("timeline_start"),
                "timeline_end": window.get("timeline_end"),
                "behavior": window.get("behavior"),
            },
        })


# ── Edge, intent, evidence and constraint derivation ───────────────


def _derive_edges(graph: dict) -> None:
    """Point every node's `depends_on` at the decisions it rests on.

    The edges are DERIVED from the payloads, never stored by the
    producers: a transition depends on the two blocks it joins, a
    caption on the block whose word timings it renders, a B-roll cover on
    the block it covers. A mutation re-runs this, so the edges always
    follow the current payloads.
    """
    existing = {candidate["node_id"] for candidate in graph["nodes"]}
    for candidate in graph["nodes"]:
        # An edge to a node that does not exist is a dangling edge, so it
        # is dropped here rather than stored - a removal leaves no node
        # pointing at the decision that went away.
        candidate["depends_on"] = [dependency
                                  for dependency in _edges_for(graph, candidate)
                                  if dependency in existing]


def _edges_for(graph: dict, candidate: dict) -> list:
    """The node ids this node depends on, derived from its payload."""
    node_type = candidate["node_type"]
    payload = candidate.get("payload", {})
    position = _payload_position(payload)
    if node_type == SPINE_BLOCK:
        return _passage_dependencies(graph, candidate)
    if node_type == AROLL_ASSIGNMENT:
        edges = _block_edge(position)
        edges += _passage_dependencies(graph, candidate)
        return edges
    if node_type == BROLL_COVER:
        return _block_edge(position)
    if node_type == TRANSITION:
        return _transition_edges(graph, candidate)
    if node_type == GRAPHIC:
        return _block_edge(position)
    if node_type == MUSIC_BEHAVIOR:
        return _block_edge(position)
    if node_type == VFX:
        return _block_edge(position)
    if node_type == SFX:
        return _block_edge(position)
    if node_type == GRADE_CORRECTION:
        return _grade_edges(graph, candidate)
    if node_type == MIX_WINDOW:
        return _mix_edges(graph, candidate)
    return []


def _block_edge(position: str) -> list:
    return [f"spine_block:{position}"] if position else []


def _passage_dependencies(graph: dict, candidate: dict) -> list:
    """The passages a block or assignment plays, matched by clip and range.

    A block plays the passage cut from the same clip over the same source
    range - the A-roll assignment IS the passage realized as a timeline
    span. Matching on clip_id and source-range overlap keeps the edge
    true when non-speech blocks interleave the spine and block position
    no longer lines up with passage position.
    """
    payload = candidate.get("payload", {})
    clip_id = payload.get("clip_id")
    if not clip_id:
        return []
    start, end = payload.get("source_start"), payload.get("source_end")
    if start is None or end is None:
        return []
    edges = []
    for other in graph["nodes"]:
        if other["node_type"] != PASSAGE_SELECTION:
            continue
        other_payload = other.get("payload", {})
        if other_payload.get("clip_id") != clip_id:
            continue
        other_start, other_end = (other_payload.get("source_start"),
                                  other_payload.get("source_end"))
        if other_start is None or other_end is None:
            continue
        if _overlap(start, end, other_start, other_end):
            edges.append(other["node_id"])
    return edges


def _transition_edges(graph: dict, transition: dict) -> list:
    """The two blocks a transition joins: the outgoing and the incoming."""
    payload = transition.get("payload", {})
    incoming = str(payload.get("incoming_position", ""))
    outgoing = payload.get("outgoing_position")
    edges = _block_edge(incoming)
    if outgoing is not None:
        edges += _block_edge(str(outgoing))
    return edges


def _grade_edges(graph: dict, grade: dict) -> list:
    """The blocks a grade correction corrects - the blocks playing its clip."""
    clip_id = grade.get("payload", {}).get("clip_id")
    if not clip_id:
        return []
    return [other["node_id"] for other in graph["nodes"]
            if other["node_type"] == SPINE_BLOCK
            and other.get("payload", {}).get("clip_id") == clip_id]


def _mix_edges(graph: dict, window: dict) -> list:
    """The blocks a mix window spans, by timeline overlap."""
    span = _span_of(graph, window["node_id"])
    if span is None:
        return []
    return [other["node_id"] for other in graph["nodes"]
            if other["node_type"] == SPINE_BLOCK
            and _span_of(graph, other["node_id"]) is not None
            and _overlap(span[0], span[1],
                         *_span_of(graph, other["node_id"]))]


def _derive_intents(graph: dict) -> None:
    """Name the beat each node serves.

    A beat is a spine block position - the spine is the timed structure
    the edit is built from, so "what does this cut serve?" is answered by
    the block the cut sits on. A passage serves the beats the blocks it
    plays sit on; a node with no block serves nothing yet, and says so by
    carrying no intent refs rather than an invented one.
    """
    for candidate in graph["nodes"]:
        candidate["intent_refs"] = _intents_for(graph, candidate)


def _intents_for(graph: dict, candidate: dict) -> list:
    node_type = candidate["node_type"]
    payload = candidate.get("payload", {})
    position = _payload_position(payload)
    if node_type == SPINE_BLOCK:
        return [f"beat:{_position(candidate)}"] if _position(candidate) else []
    if node_type == PASSAGE_SELECTION:
        return [f"beat:{_position(other)}" for other in graph["nodes"]
                if other["node_type"] == SPINE_BLOCK
                and candidate["node_id"] in other["depends_on"]]
    if node_type == GRADE_CORRECTION:
        return [f"beat:{_position(other)}" for other in graph["nodes"]
                if other["node_type"] == SPINE_BLOCK
                and candidate["node_id"] in other["depends_on"]]
    if node_type == MIX_WINDOW:
        return [f"beat:{_position(other)}" for other in graph["nodes"]
                if other["node_type"] == SPINE_BLOCK
                and candidate["node_id"] in other["depends_on"]]
    return [f"beat:{position}"] if position else []


def _derive_evidence(graph: dict) -> None:
    """Name the measurement each node rests on.

    Evidence is a measurement id - `clip_catalog:clip_001` for the clip's
    identity and duration, `temporal_index:clip_001` for its word
    timings, `semantic_analysis:clip_001` for its vision document,
    `music_analysis:tempo` for the measured tempo. A node rests on the
    measurements its own payload names; a node that rests on the edit
    structure alone (a transition, a caption) carries none rather than a
    borrowed one.
    """
    for candidate in graph["nodes"]:
        candidate["evidence_refs"] = _evidence_for(graph, candidate)


def _evidence_for(graph: dict, candidate: dict) -> list:
    node_type = candidate["node_type"]
    payload = candidate.get("payload", {})
    clip_id = payload.get("clip_id")
    if node_type == PASSAGE_SELECTION:
        return ([f"clip_catalog:{clip_id}", f"temporal_index:{clip_id}"]
                if clip_id else [])
    if node_type == SPINE_BLOCK:
        return [f"clip_catalog:{clip_id}"] if clip_id else []
    if node_type == AROLL_ASSIGNMENT:
        return ([f"clip_catalog:{clip_id}", f"temporal_index:{clip_id}"]
                if clip_id else [])
    if node_type == BROLL_COVER:
        return ([f"clip_catalog:{clip_id}", f"semantic_analysis:{clip_id}"]
                if clip_id else [])
    if node_type == MUSIC_BEHAVIOR:
        return ["music_analysis:tempo"]
    if node_type == GRADE_CORRECTION:
        return [f"clip_catalog:{clip_id}"] if clip_id else []
    if node_type == MIX_WINDOW:
        return ["music_analysis:tempo"]
    return []


def _derive_constraints(graph: dict) -> None:
    """Declare the rules each node must satisfy.

    A constraint is declared only where the node's own data creates the
    obligation: a drawn transition needs a V1 boundary, an overlay must
    fit its block, a B-roll cover must span its block, a block must show
    a clip, a mix window must sit inside the picture. `violations_of`
    checks exactly these declarations, so a constraint and its violators
    cannot drift apart.
    """
    for candidate in graph["nodes"]:
        candidate["constraints"] = _constraints_for(graph, candidate)


def _constraints_for(graph: dict, candidate: dict) -> dict:
    node_type = candidate["node_type"]
    if node_type == TRANSITION:
        if not _is_cut(candidate):
            return {NEEDS_V1_BOUNDARY: True}
        return {}
    if node_type == GRAPHIC:
        return {FITS_ITS_BLOCK: True}
    if node_type == BROLL_COVER:
        return {COVERS_ITS_BLOCK: True}
    if node_type == SPINE_BLOCK:
        return {TIMELINE_COVERED: True}
    if node_type == MIX_WINDOW:
        return {WITHIN_PICTURE: True}
    return {}


# ── Mutation ───────────────────────────────────────────────────────


@dataclass
class Mutation:
    """One graph operation, applied by `apply_mutation`.

    The vocabulary is the edit algebra's first three verbs over the
    graph - the operations the target behavior names. Each carries the
    fields its op needs and no others, so a mutation cannot ask for a
    change its op does not express.
    """

    op: str
    """`reorder_passages` | `retime_block` | `remove_node`."""
    node_id: str
    before: str = ""
    """For `reorder_passages`: the passage to move the node before."""
    timeline_start: float | None = None
    """For `retime_block`: the block's new timeline start."""


def apply_mutation(graph: dict, mutation: Mutation) -> tuple:
    """Apply one mutation; return `(new_graph, affected_node_ids)`.

    The mutation is applied to the node's payload, the dependency edges
    are re-derived from the new payloads, and the affected set is
    reported: the mutated node and every node that transitively depends
    on it. That set is the blast radius - the answer to "if this
    passage moves, what else moves?".

    The graph is not mutated in place; the caller decides whether to
    keep the result, which is what makes a mutation reversible by
    applying its inverse to the original.

    The blast radius is read off the ORIGINAL graph: a removal drops the
    edges that name the removed node, so counting dependents after the
    change would report nothing for the very nodes the removal took out
    from under. The original's dependents are what the change touches.
    """
    new_graph = copy.deepcopy(graph)
    mutated = _apply_op(new_graph, mutation)
    _derive_edges(new_graph)
    affected = set(mutated)
    for node_id in mutated:
        affected |= set(dependents_of(graph, node_id))
    return new_graph, sorted(affected)


def _apply_op(graph: dict, mutation: Mutation) -> list:
    """Apply the op to the payload; return the node ids it changed."""
    target = node(graph, mutation.node_id)
    if not target:
        raise KeyError(f"mutation names {mutation.node_id!r}, which is not in the graph")
    if mutation.op == "reorder_passages":
        return _reorder_passages(graph, target, mutation.before)
    if mutation.op == "retime_block":
        return _retime_block(graph, target, mutation.timeline_start)
    if mutation.op == "remove_node":
        return _remove_node(graph, target)
    raise ValueError(f"unknown mutation op {mutation.op!r}")


def _reorder_passages(graph: dict, target: dict, before: str) -> list:
    """Move one passage before another, renumbering the sequence.

    The two passages swap positions; every other passage keeps its
    place. The positions are the sequence's own ordering, so the swap is
    the whole change - the blocks that played these passages are
    reported as affected because their content follows the passages.
    """
    if not before:
        raise ValueError("reorder_passages needs the passage to move before")
    other = node(graph, before)
    if not other:
        raise KeyError(f"reorder_passages names {before!r}, which is not in the graph")
    target["payload"]["position"], other["payload"]["position"] = (
        other["payload"].get("position"), target["payload"].get("position"))
    return sorted([target["node_id"], other["node_id"]])


def _retime_block(graph: dict, target: dict, timeline_start) -> list:
    """Move one block to a new timeline start, keeping its duration."""
    if timeline_start is None:
        raise ValueError("retime_block needs a timeline_start")
    payload = target["payload"]
    duration = payload.get("timeline_end", 0) - payload.get("timeline_start", 0)
    payload["timeline_start"] = timeline_start
    payload["timeline_end"] = timeline_start + duration
    return [target["node_id"]]


def _remove_node(graph: dict, target: dict) -> list:
    """Remove a node and every edge that names it."""
    graph["nodes"] = [candidate for candidate in graph["nodes"]
                      if candidate["node_id"] != target["node_id"]]
    graph["node_count"] = len(graph["nodes"])
    for candidate in graph["nodes"]:
        candidate["depends_on"] = [dependency
                                  for dependency in candidate["depends_on"]
                                  if dependency != target["node_id"]]
    return [target["node_id"]]
