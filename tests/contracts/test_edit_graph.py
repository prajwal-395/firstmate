"""The editorial/edit graph answers the questions the fidelity test asks.

Each test names a defect the graph closes: a mutation that does not
propagate to the nodes that depend on it, a beat no node can be asked
about, a decision whose evidence is untraceable, a constraint violation
that is invisible. The graph is derived, so every test builds a small
edit's `pipeline_data.json` state and asserts the graph's answer - never
a registry, enumeration or schema entry, which would pass no matter what
the graph actually does.
"""
from __future__ import annotations

import json
from pathlib import Path

import jsonschema
import pytest

from library.tools import edit_graph
from library.tools.edit_graph import Mutation

REPO = Path(__file__).resolve().parents[2]
SCHEMA = json.loads(
    (REPO / "library" / "schema" / "edit_graph.schema.json").read_text(
        encoding="utf-8"))


def _block(position, block_type="speech", clip_id="clip_001",
           source=(0.0, 5.0), timeline=(0.0, 5.0), behavior="background"):
    return {
        "position": position,
        "block_type": block_type,
        "clip_id": clip_id,
        "source_start": source[0],
        "source_end": source[1],
        "timeline_start": timeline[0],
        "timeline_end": timeline[1],
        "word_timestamps": [{"word": "a", "source_start": source[0],
                             "source_end": source[1]}],
        "alignment_method": "search",
        "music_behavior": behavior,
    }


def _small_edit_state() -> dict:
    """A small edit's recorded state: three blocks, two passages, and the
    decisions that hang off them.

    Block 1 and 2 are speech cut from clip_001; block 3 is a picture block
    with no clip. A drawn transition sits at block 3, whose outgoing block
    (block 2) carries a clip - and a second drawn transition sits past the
    picture block, whose outgoing block has no clip, so it is the one the
    constraint check must catch.
    """
    return {
        "capability_outputs": {
            "spine.mesh": {"audio_spine": {"structure": [
                _block(1, source=(5.0, 10.0), timeline=(0.0, 5.0)),
                _block(2, source=(15.0, 20.0), timeline=(5.0, 10.0)),
                _block(3, block_type="picture", clip_id=None,
                       source=(0.0, 0.0), timeline=(10.0, 12.0),
                       behavior="prominent"),
            ]}},
            "speech.enrich": {"speech_sequence": {"body_sequence": [
                {"position": 1, "clip_id": "clip_001",
                 "source_start": 5.0, "source_end": 10.0,
                 "start_time": 0.0, "end_time": 5.0, "text": "hello"},
                {"position": 2, "clip_id": "clip_001",
                 "source_start": 15.0, "source_end": 20.0,
                 "start_time": 5.0, "end_time": 10.0, "text": "world"},
            ]}},
            "aroll.assign": {"a_roll_assignments": {"a_roll_assignments": [
                {"spine_block_position": 1, "clip_id": "clip_001",
                 "source_start": 5.0, "source_end": 10.0,
                 "timeline_start": 0.0, "timeline_end": 5.0},
                {"spine_block_position": 2, "clip_id": "clip_001",
                 "source_start": 15.0, "source_end": 20.0,
                 "timeline_start": 5.0, "timeline_end": 10.0},
            ]}},
            "broll.resolve": {"b_roll_assignments": {"b_roll_assignments": [
                {"spine_block_position": 3, "clip_id": "clip_002",
                 "video_in": 0.0, "video_out": 2.0,
                 "timeline_start": 10.0, "timeline_end": 12.0},
            ]}},
            "transitions.resolve": {"transition_spec": {"transitions": [
                {"cut_point_position": 2, "transition_type": "cut",
                 "outgoing_position": 1, "incoming_position": 2,
                 "cut_time": 5.0},
                {"cut_point_position": 3, "transition_type": "crash_zoom",
                 "outgoing_position": 2, "incoming_position": 3,
                 "cut_time": 10.0, "duration_seconds": 0.5},
                {"cut_point_position": "end", "transition_type": "glow",
                 "outgoing_position": 3, "incoming_position": "end",
                 "cut_time": 12.0, "duration_seconds": 0.5},
            ]}},
            "subtitles.render": {"subtitle_overlay": {"segments": [
                {"spine_block_position": 1, "timeline_start": 0.5,
                 "timeline_end": 4.5, "text": "hello"},
            ]}},
            "sfx.resolve": {"sfx_spec": [
                {"spine_block_position": 2, "sfx_id": "whoosh.wav",
                 "volume_db": -12.0, "duration_seconds": 0.3},
            ]},
            "color_grade.resolve": {"color_grade_spec": {"subject_grades": [
                {"clip_id": "clip_001"},
            ]}},
            "audio_mix.resolve": {"audio_mix_spec": {"mix_windows": [
                {"timeline_start": 0.0, "timeline_end": 10.0,
                 "behavior": "bed"},
            ]}},
        }
    }


@pytest.fixture
def graph() -> dict:
    return edit_graph.build_graph(_small_edit_state())


# ── The mutation propagates ────────────────────────────────────────


def test_reordering_two_passages_reports_every_downstream_node_affected(
        graph):
    """The defect: a reorder of two passages leaves every node that plays
    them stale - the graph reports nothing affected, so a reader cannot
    tell what else moves. The fix makes the mutation report the mutated
    passages and their transitive dependents.
    """
    _, affected = edit_graph.apply_mutation(
        graph, Mutation(op="reorder_passages", node_id="passage:2",
                        before="passage:1"))

    # The two passages are mutated.
    assert "passage:1" in affected
    assert "passage:2" in affected
    # Every node that plays them is affected: the blocks, their A-rolls,
    # the transition between them, the caption, the sfx, the music
    # behaviors, the grade correction and the mix window.
    for downstream in (
        "spine_block:1", "spine_block:2",
        "aroll:1", "aroll:2",
        "transition:2",
        "graphic:1:subtitle",
        "sfx:0",
        "music_behavior:1", "music_behavior:2",
        "grade:clip_001",
        "mix_window:0",
    ):
        assert downstream in affected, (
            f"{downstream} plays the reordered passages but is not reported "
            f"affected")
    # The picture block plays neither passage and is untouched.
    assert "spine_block:3" not in affected


def test_a_mutation_does_not_mutate_the_original_graph(graph):
    """The defect: applying a mutation in place destroys the graph the
    reader was querying, so the mutation cannot be reversed or compared.
    The fix returns a new graph and leaves the original untouched.
    """
    original_positions = {
        n["node_id"]: n["payload"].get("position")
        for n in graph["nodes"] if n["node_type"] == "passage_selection"
    }
    edit_graph.apply_mutation(
        graph, Mutation(op="reorder_passages", node_id="passage:2",
                        before="passage:1"))
    assert {
        n["node_id"]: n["payload"].get("position")
        for n in graph["nodes"] if n["node_type"] == "passage_selection"
    } == original_positions


def test_removing_a_node_reports_its_dependents_affected(graph):
    """The defect: removing a block leaves the nodes that depended on it
    pointing at a decision that no longer exists. The fix reports the
    removal's blast radius and drops the dangling edges.
    """
    new_graph, affected = edit_graph.apply_mutation(
        graph, Mutation(op="remove_node", node_id="spine_block:2"))
    assert "spine_block:2" in affected
    # The nodes that depended on block 2 are affected; block 1, which
    # depended on nothing removed, is not.
    assert "aroll:2" in affected
    assert "transition:2" in affected
    assert "spine_block:1" not in affected
    # The removal drops the dangling edges, so no node points at the
    # removed block.
    assert all("spine_block:2" not in n["depends_on"]
               for n in new_graph["nodes"])


# ── The queries the fidelity test asks ─────────────────────────────


def test_a_node_serves_the_beat_its_block_is(graph):
    """The defect: a planner cannot ask what serves a beat - the beat is a
    block position and nothing links a decision to it. The fix derives
    the intent links, so `nodes_serving` answers the question.
    """
    serving = edit_graph.nodes_serving(graph, "beat:1")
    assert "spine_block:1" in serving
    assert "aroll:1" in serving
    assert "graphic:1:subtitle" in serving
    assert "music_behavior:1" in serving
    # Block 3 serves beat 3, not beat 1.
    assert "spine_block:3" not in serving
    assert edit_graph.intent_of(graph, "spine_block:1") == ["beat:1"]


def test_a_node_rests_on_the_measurements_its_clip_came_from(graph):
    """The defect: a decision's evidence is untraceable - a reader cannot
    tell what measurement a passage rests on. The fix derives the
    evidence links from the payloads.
    """
    evidence = edit_graph.evidence_of(graph, "passage:1")
    assert "clip_catalog:clip_001" in evidence
    assert "temporal_index:clip_001" in evidence
    # A transition rests on the edit structure, not a measurement, and
    # says so by carrying none rather than a borrowed one.
    assert edit_graph.evidence_of(graph, "transition:2") == []


def test_a_drawn_transition_whose_outgoing_block_has_no_clip_violates_the_boundary(
        graph):
    """The defect: a drawn transition that needs a V1 boundary, sitting
    after a block that puts no clip on V1, is invisible - nothing checks
    the constraint the node declares. The fix checks exactly the declared
    constraints and reports the violator.
    """
    # The glow transition past the picture block: its outgoing block (the
    # picture block) has no clip, so it cannot draw through.
    assert "transition:end" in edit_graph.violations_of(
        graph, edit_graph.NEEDS_V1_BOUNDARY)
    # The crash zoom at block 3 draws out of block 2, which carries a
    # clip, so it does not violate.
    assert "transition:3" not in edit_graph.violations_of(
        graph, edit_graph.NEEDS_V1_BOUNDARY)


def test_a_block_with_no_clip_and_no_declared_black_beat_leaves_a_hole(graph):
    """The defect: a block that shows no clip and declares no intentional
    black beat is an uncovered stretch nobody reports. The fix declares
    the coverage constraint on every block and reports the violator.
    """
    assert "spine_block:3" in edit_graph.violations_of(
        graph, edit_graph.TIMELINE_COVERED)
    assert "spine_block:1" not in edit_graph.violations_of(
        graph, edit_graph.TIMELINE_COVERED)


# ── The artifact conforms to its schema ────────────────────────────


def test_the_built_graph_validates_against_its_schema(graph):
    """The defect: the graph artifact drifts from the schema that pins
    it, so a reader cannot trust the shape. The fix builds the graph to
    the schema, and this test refuses a graph that does not conform.
    """
    jsonschema.validate(graph, SCHEMA)


def test_a_graph_built_from_an_empty_state_is_empty_not_absent(graph):
    """The defect: a run that has produced nothing yet gets no graph at
    all, so a reader cannot distinguish 'no decisions' from 'no graph'.
    The fix always emits the artifact - empty when the run is empty.
    """
    empty = edit_graph.build_graph({})
    assert empty["node_count"] == 0
    assert empty["nodes"] == []
    jsonschema.validate(empty, SCHEMA)
