"""Alternatives for the main edit: N comparable forks of a named region.

Each test names a defect the alternatives mechanism closes: a fork that
differs outside its declared dimension (which would make the comparison
dishonest), a fork that changes the argument (which "without changing
the argument" forbids), a dimension nothing declares, and a count below
two (which is not a comparison). The graph is derived, so every test
builds a small edit's `pipeline_data.json` state and asserts the
alternatives' answer - never a registry, enumeration or schema entry,
which would pass no matter what the mechanism actually does.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from library.tools import composer, edit_graph
from library.tools.versions import edit_variants
from library.tools.versions.edit_variants import AlternativeSpec

REPO = Path(__file__).resolve().parents[2]


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
    """A small edit's recorded state: three blocks, three passages, and
    the decisions that hang off them.

    Blocks 1-3 are speech cut from clip_001; a B-roll cover sits on
    block 3. The three passages are the "opening" the captain asks for
    alternatives of.
    """
    return {
        "capability_outputs": {
            "spine.mesh": {"audio_spine": {"structure": [
                _block(1, source=(5.0, 10.0), timeline=(0.0, 5.0)),
                _block(2, source=(15.0, 20.0), timeline=(5.0, 10.0)),
                _block(3, source=(25.0, 30.0), timeline=(10.0, 15.0)),
            ]}},
            "speech.enrich": {"speech_sequence": {"body_sequence": [
                {"position": 1, "clip_id": "clip_001",
                 "source_start": 5.0, "source_end": 10.0,
                 "start_time": 0.0, "end_time": 5.0, "text": "hello"},
                {"position": 2, "clip_id": "clip_001",
                 "source_start": 15.0, "source_end": 20.0,
                 "start_time": 5.0, "end_time": 10.0, "text": "world"},
                {"position": 3, "clip_id": "clip_001",
                 "source_start": 25.0, "source_end": 30.0,
                 "start_time": 10.0, "end_time": 15.0, "text": "again"},
            ]}},
            "aroll.assign": {"a_roll_assignments": {"a_roll_assignments": [
                {"spine_block_position": 1, "clip_id": "clip_001",
                 "source_start": 5.0, "source_end": 10.0,
                 "timeline_start": 0.0, "timeline_end": 5.0},
                {"spine_block_position": 2, "clip_id": "clip_001",
                 "source_start": 15.0, "source_end": 20.0,
                 "timeline_start": 5.0, "timeline_end": 10.0},
                {"spine_block_position": 3, "clip_id": "clip_001",
                 "source_start": 25.0, "source_end": 30.0,
                 "timeline_start": 10.0, "timeline_end": 15.0},
            ]}},
            "broll.resolve": {"b_roll_assignments": {"b_roll_assignments": [
                {"spine_block_position": 3, "clip_id": "clip_002",
                 "video_in": 0.0, "video_out": 2.0,
                 "timeline_start": 10.0, "timeline_end": 12.0},
            ]}},
        }
    }


def _intent_model() -> dict:
    """A small intent model: three beats, one claim, one key moment."""
    return {
        "beats": [
            {"beat_id": "beat:1", "purpose": "setup", "span": {
                "clip_id": "clip_001", "source_start": 5.0,
                "source_end": 10.0}},
            {"beat_id": "beat:2", "purpose": "development", "span": {
                "clip_id": "clip_001", "source_start": 15.0,
                "source_end": 20.0}},
            {"beat_id": "beat:3", "purpose": "payoff", "span": {
                "clip_id": "clip_001", "source_start": 25.0,
                "source_end": 30.0}},
        ],
        "claims": [
            {"claim_id": "claim:1", "statement": "the opening works",
             "supports": []},
        ],
        "key_moments": [],
        "pacing_curve": [],
    }


@pytest.fixture
def graph() -> dict:
    return edit_graph.build_graph(_small_edit_state())


# ── The alternatives differ only in the declared dimension ──────────


def test_passage_order_alternatives_differ_only_in_the_ordering(graph):
    """The defect: a fork that changes something it did not declare
    makes the comparison dishonest - the captain chooses between
    alternatives on the strength of the declaration, so a fork that
    also moved a block or re-wired an edge would be a different edit
    wearing the alternatives' name. The fix makes `validate_alternatives`
    refuse any payload difference outside the declared dimension.
    """
    specs = [
        AlternativeSpec(
            region=("passage:1", "passage:2", "passage:3"),
            dimension=edit_variants.PASSAGE_ORDER,
            value=(1, 2, 3),
            trade_off="the approved order",
        ),
        AlternativeSpec(
            region=("passage:1", "passage:2", "passage:3"),
            dimension=edit_variants.PASSAGE_ORDER,
            value=(2, 1, 3),
            trade_off="opens with the second passage",
        ),
        AlternativeSpec(
            region=("passage:1", "passage:2", "passage:3"),
            dimension=edit_variants.PASSAGE_ORDER,
            value=(3, 1, 2),
            trade_off="opens with the strongest passage",
        ),
    ]
    alternatives = edit_variants.produce_alternatives(
        graph, _intent_model(), specs)
    problems = edit_variants.validate_alternatives(
        alternatives, graph, edit_variants.PASSAGE_ORDER)
    assert problems == []

    # The orderings actually differ: the original passage positions in
    # the new order (the node_id carries the original position).
    orderings = []
    for alternative in alternatives:
        g = alternative["graph"]
        passages = sorted(
            (n for n in g["nodes"]
             if n["node_type"] == edit_graph.PASSAGE_SELECTION),
            key=lambda n: n["payload"]["position"])
        orderings.append([int(n["node_id"].split(":")[1])
                          for n in passages])
    assert orderings[0] == [1, 2, 3]
    assert orderings[1] == [2, 1, 3]
    assert orderings[2] == [3, 1, 2]


def test_broll_cover_alternatives_differ_only_in_the_clip(graph):
    """The defect: a B-roll cover alternative that also changed the
    block it covers or the span it plays would be a different edit, not
    a different cover. The fix makes `validate_alternatives` refuse
    any payload difference outside `clip_id`.
    """
    specs = [
        AlternativeSpec(
            region=("broll:3",),
            dimension=edit_variants.BROLL_COVER,
            value="clip_002",
            trade_off="the approved cover",
        ),
        AlternativeSpec(
            region=("broll:3",),
            dimension=edit_variants.BROLL_COVER,
            value="clip_003",
            trade_off="a tighter cover",
        ),
    ]
    alternatives = edit_variants.produce_alternatives(
        graph, _intent_model(), specs)
    problems = edit_variants.validate_alternatives(
        alternatives, graph, edit_variants.BROLL_COVER)
    assert problems == []

    clips = []
    for alternative in alternatives:
        g = alternative["graph"]
        for node in g["nodes"]:
            if node["node_type"] == edit_graph.BROLL_COVER:
                clips.append(node["payload"]["clip_id"])
    assert clips == ["clip_002", "clip_003"]


def test_a_fork_that_changes_the_argument_is_refused(graph):
    """The defect: "without changing the argument" is the whole
    request - a fork that re-pointed a passage at a different beat
    would edit the story, not the telling. The fix makes
    `validate_alternatives` refuse any intent-ref difference.
    """
    specs = [
        AlternativeSpec(
            region=("passage:1", "passage:2"),
            dimension=edit_variants.PASSAGE_ORDER,
            value=(2, 1),
            trade_off="reordered",
        ),
    ]
    alternatives = edit_variants.produce_alternatives(
        graph, _intent_model(), specs)
    # Simulate a fork that changed the argument: re-point a passage's
    # intent refs at a different beat.
    alternatives[0]["graph"]["nodes"][0]["intent_refs"] = ["beat:9"]
    problems = edit_variants.validate_alternatives(
        alternatives, graph, edit_variants.PASSAGE_ORDER)
    assert any("intent refs differ" in p for p in problems)


def test_a_fork_that_differs_outside_the_dimension_is_refused(graph):
    """The defect: a fork that moved a block's timeline while reordering
    passages would be a different edit, not a different ordering. The
    fix makes `validate_alternatives` refuse any payload difference
    outside the declared dimension's field.
    """
    specs = [
        AlternativeSpec(
            region=("passage:1", "passage:2"),
            dimension=edit_variants.PASSAGE_ORDER,
            value=(2, 1),
            trade_off="reordered",
        ),
    ]
    alternatives = edit_variants.produce_alternatives(
        graph, _intent_model(), specs)
    # Simulate a fork that also moved a block's timeline.
    for node in alternatives[0]["graph"]["nodes"]:
        if node["node_id"] == "spine_block:1":
            node["payload"]["timeline_start"] = 99.0
    problems = edit_variants.validate_alternatives(
        alternatives, graph, edit_variants.PASSAGE_ORDER)
    assert any("differs outside the declared dimension" in p
               for p in problems)


# ── The composer's alternatives goal ────────────────────────────────


def test_the_composer_plans_the_alternatives_goal():
    """The defect: "give me three alternatives" has no composer goal,
    so the request cannot be resolved to the capabilities that serve it.
    The fix adds `compose_alternatives`, which plans the graph build and
    the alternatives operation.
    """
    comp = composer.compose_alternatives(
        region=("passage:1", "passage:2"),
        dimension=edit_variants.PASSAGE_ORDER,
        count=3)
    assert comp.completed
    assert comp.goal == composer.ALTERNATIVES_GOAL
    assert "edit_graph.build" in comp.operations
    assert composer.EDIT_ALTERNATIVES_PRODUCE in comp.operations


def test_the_composer_refuses_an_unknown_dimension():
    """The defect: a dimension nothing declares would plan a fork that
    differs in nothing - or worse, in something the declaration does not
    name. The fix refuses the dimension by name.
    """
    comp = composer.compose_alternatives(
        region=("passage:1",),
        dimension="camera_angle",
        count=3)
    assert comp.refused
    assert "camera_angle" in comp.refusal_reason()


def test_the_composer_refuses_a_single_alternative():
    """The defect: one alternative is not a comparison - the captain
    asked for three. The fix refuses counts below two.
    """
    comp = composer.compose_alternatives(
        region=("passage:1",),
        dimension=edit_variants.PASSAGE_ORDER,
        count=1)
    assert comp.refused
    assert "comparison" in comp.refusal_reason()
