"""Two steps decided from memory, and now they decide from context.

`mesh_spine` (2.05) sees the creative direction's energy arc through its
own projection, and `plan_sfx` (4.04) sees which transitions draw. The
legacy DAG edges that route them are not pinned here (AGENTS.md 3).
History (F7, F13): docs/evidence/sfx.md.
"""
import json
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

from library.steps.step_4_04_plan_sfx.bridge import (  # noqa: E402
    build_transition_rows,
)
from library.tools.context_projector import project_fields  # noqa: E402


def _manifest(step_dir: str) -> dict:
    return json.loads(
        (REPO / "library" / "steps" / step_dir / "manifest.json")
        .read_text(encoding="utf-8")
    )


# ── The direction reaches the step that sets the pace ────────────────


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
    # The boundary: 2.01's own reasoning and a second copy of the
    # passages 2.02 already chose do not travel.
    assert "key_moments" not in kept and "rationale" not in kept


# ── The sound step can see the transitions ────────────────────────────


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
