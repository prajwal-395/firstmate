"""Two steps decided from memory, and now they decide from context.

Both defects were invisible on the run of record because one agent
answered every step, and both are recorded in the creative-decision
degradation report of 2026-08-28 (F7 and F13):

  * `mesh_spine` (2.05) places every non-speech gap, sets its length,
    and sets the `music_behavior` of every block - including the
    `silent` climax, the strongest creative decision in the finished
    video. Its own handoff names the creative direction as one of three
    core reads and scores it on "the spine follows the creative
    direction's energy arc". No DAG edge carried it. The 2.05 reasoning
    trace justified the silence by quoting 2.01's `energy_arc` - "the
    piece resolves by getting quieter and more certain, not louder" -
    and that sentence appeared nowhere in 2.05's context.

  * `plan_sfx` (4.04) is told, first thing, to pair sounds with
    transitions, and its second evaluation criterion is "every creative
    transition has at most one SFX". No DAG edge carried
    `transition_spec`. Both sounds that shipped sit on the two drawn
    transitions, placed by an agent that had planned those transitions
    itself minutes earlier.

Replace the answering agent between steps and neither justification has
a source. These tests fail if either edge is removed again, and they
also hold the BOUNDARY: what was deliberately left out stays out, so the
fix cannot quietly grow into routing everything available.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
DAG = json.loads(
    (REPO / "library" / "processes" / "edit_video" / "dag.json")
    .read_text(encoding="utf-8")
)
SFX = REPO / "library" / "steps" / "step_4_04_plan_sfx"

from library.steps.step_4_04_plan_sfx.bridge import (  # noqa: E402
    build_transition_rows,
)
from library.tools.context_projector import project_fields  # noqa: E402


def _manifest(step_dir: str) -> dict:
    return json.loads(
        (REPO / "library" / "steps" / step_dir / "manifest.json")
        .read_text(encoding="utf-8")
    )


def _carries(source: str, target: str, state_key: str) -> bool:
    return any(
        e["from"] == source and e["to"] == target
        and state_key in (e.get("data_mapping") or {})
        for e in DAG["edges"]
    )


# ── Edge 1: the direction reaches the step that sets the pace ─────────


def test_the_dag_carries_the_direction_into_the_spine():
    assert _carries("creative_direction", "mesh_spine",
                    "creative_direction"), (
        "mesh_spine sets every gap and every music_behavior with no "
        "sight of the creative direction again"
    )


def test_the_spine_declares_the_direction_and_the_runner_refuses_without_it():
    declared = {i["name"]: i
                for i in _manifest("step_2_05_mesh_spine")
                ["interface"]["inputs"]}
    assert "creative_direction" in declared
    assert declared["creative_direction"]["required"] is True, (
        "an optional creative direction is one nothing refuses on, and "
        "a run missing it would look exactly like the defect this "
        "edge removes"
    )


def test_the_energy_arc_survives_the_spines_projection():
    """The allow-list is what decides whether the prompt sees it.

    Routing without selecting would delete the direction again inside
    `project_step_context`, and the step would read exactly as it did
    on the run of record.
    """
    fields = _manifest("step_2_05_mesh_spine")["context_fields"]
    inputs = {"creative_direction": {
        "narrative_theme": "theme",
        "target_mood": "self-deprecating and quietly resolved",
        "target_energy": "building",
        "energy_arc": (
            "No triumphant lift at the end; the piece resolves by "
            "getting quieter and more certain, not louder."
        ),
        "emotional_landscape": "music should feel like company",
        "audience_emotion": "recognition, then permission",
        "key_moments": [{"moment": "the admission", "why": "..."}],
        "rationale": "why 2.01 chose this thread over three others",
    }}
    kept = project_fields(inputs, fields)["creative_direction"]

    assert "not louder" in kept["energy_arc"], (
        "the sentence 2.05 had to quote from memory is projected away "
        "again"
    )
    for name in ("target_mood", "target_energy", "emotional_landscape",
                 "narrative_theme", "audience_emotion"):
        assert name in kept, f"{name} no longer reaches the spine prompt"


def test_the_spine_is_not_handed_the_whole_direction():
    """The boundary, held deliberately.

    `key_moments` names clips and source ranges that 2.02 has already
    turned into the passages this step arranges, and `rationale` is
    2.01's account of how it reached the direction - addressed to
    somebody auditing 2.01, not to somebody placing a gap. Together
    they are 4,744 of the direction's 6,854 bytes on project 001.
    """
    fields = _manifest("step_2_05_mesh_spine")["context_fields"]
    selected = [f for f in fields if f.startswith("creative_direction")]
    assert selected, "nothing selects the direction"
    assert not any("key_moments" in f or "rationale" in f
                   for f in selected), (
        "the spine now carries 2.01's own reasoning and a second copy "
        "of the passages speech_sequence already chose"
    )


# ── Edge 2: the sound step can see the transitions ────────────────────


def test_the_dag_carries_the_transition_plan_into_plan_sfx():
    assert _carries("plan_transitions", "plan_sfx", "transition_spec"), (
        "plan_sfx is told to pair sounds with transitions it cannot see "
        "again"
    )


def _spine():
    """Four blocks, in project 001's own shape."""
    return {"structure": [
        {"position": "hook", "block_type": "hook",
         "timeline_start": 0.0, "timeline_end": 2.398},
        {"position": 1, "block_type": "transition_slot",
         "timeline_start": 2.398, "timeline_end": 6.06},
        {"position": 2, "block_type": "speech",
         "timeline_start": 6.06, "timeline_end": 8.742},
        {"position": 3, "block_type": "speech",
         "timeline_start": 8.742, "timeline_end": 11.444},
    ]}


def _spec():
    """One drawn transition, one hard cut, one jump cut.

    Shaped as step 4.02's post-bridge writes it: `cut_point_original` is
    the incoming block's own `timeline_start`, and the per-cut
    `rationale` is the prose that must not travel.
    """
    return [
        {"transition_id": "trans_001", "cut_point_original": 2.398,
         "cut_point_timeline": 2.398, "transition_type": "hard_cut",
         "duration_frames": 0,
         "rationale": "Nothing to draw. " + "x" * 400},
        {"transition_id": "trans_002", "cut_point_original": 6.06,
         "cut_point_timeline": 6.06, "transition_type": "defocus",
         "duration_frames": 15,
         "rationale": "A blur into the breath. " + "y" * 400},
        {"transition_id": "trans_003", "cut_point_original": 8.742,
         "cut_point_timeline": 8.742, "transition_type": "jump_cut",
         "duration_frames": 0,
         "rationale": "Labelled, not decorated. " + "z" * 400},
    ]


def _inputs(**overrides):
    payload = {"timed_spine": _spine(), "transition_spec": _spec(),
               "project_fps": 30.0}
    payload.update(overrides)
    return payload






def test_a_drawn_transition_is_told_apart_from_one_that_draws_nothing():
    """The handoff's second criterion is about a CREATIVE transition.

    A hard cut and a jump cut are real editorial labels that put
    nothing on screen (`transition_vocabulary.CUT_TYPES`, AGENTS.md
    10.5), so a column that only named the type would leave the model
    unable to apply the criterion at all.
    """
    rows = build_transition_rows(_inputs())
    by_position = {r["spine_block_position"]: r for r in rows}
    assert by_position[1]["draws_on_screen"] == "no"
    assert by_position[2]["draws_on_screen"] == "yes"
    assert by_position[2]["duration_frames"] == 15
    assert by_position[3]["draws_on_screen"] == "no", (
        "a jump_cut draws nothing - it is a label on a cut, not an "
        "effect on the picture"
    )


def test_a_cut_that_names_no_boundary_is_unresolved_not_the_nearest_block():
    """Never attach a cut to the block that happens to be closest.

    A wrong position is worse than an absent one here: it invites a
    sound onto a boundary the plan never decorated.
    """
    spec = _spec() + [{"transition_id": "trans_004",
                       "cut_point_original": 41.183,
                       "transition_type": "defocus",
                       "duration_frames": 15, "rationale": ""}]
    rows = build_transition_rows(_inputs(transition_spec=spec))
    assert rows[-1]["spine_block_position"] == "unresolved"
    assert rows[-1]["cut_point_seconds"] == 41.183, (
        "an unresolved cut still says where it is, or it is invisible"
    )




def test_the_table_is_empty_rather_than_wrong_without_the_plan():
    """An absent plan is zero rows, never a table of invented cuts.

    `empty_table_guard` reports a zero-row table on the run that sends
    it (AGENTS.md 10.1), which is the honest signal that the edge is
    gone.
    """
    payload = _inputs()
    del payload["transition_spec"]
    assert build_transition_rows(payload) == []




def test_the_table_is_declared_as_an_output():
    """A bridge key the manifest does not declare warns on every run."""
    names = {o["name"] for o in _manifest("step_4_04_plan_sfx")
             ["interface"]["outputs"]}
    assert "transitions_toon" in names


