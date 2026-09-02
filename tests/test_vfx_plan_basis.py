"""An empty VFX plan says WHY it is empty.

`{"visual_effects": []}` meant two opposite things and looked identical
in both.  On 001's run of record it was a decision - the planner wrote
`docs/run-001-reasoning/plan_vfx.md` before answering, applied the
handoff's own "long AND static" criterion to eleven blocks, and held the
empty answer across three `semantically empty` rejections.  It is the
same bytes when the planner names four effects and the post-bridge
discards every one of them, and the drop reasons went only to stderr.

These tests drive the REAL post-bridge as the runner drives it - one
subprocess, JSON on stdin, JSON on stdout - and assert the two cases come
out different.  They also assert the accepting half is untouched: an
empty plan is still accepted, still never padded, and still never
refused.
"""

from __future__ import annotations

import json
import pathlib
import subprocess
import sys

import pytest

from library.tools.vfx_plan_basis import (
    BASIS_LEGEND,
    DROP_REASONS,
    PLAN_BASES,
    THE_REFUSAL_QUESTION,
    DroppedEntry,
    PlanBasis,
    assert_vocabulary_is_well_formed,
    basis_summary,
    plan_basis,
)

REPO = pathlib.Path(__file__).resolve().parent.parent
POST_BRIDGE = "library.steps.step_4_03_plan_vfx.post_bridge"


def _spine():
    """Four blocks, of the shape `mesh_spine` really emits."""
    return {"structure": [
        {"position": "hook", "block_type": "hook", "clip_id": "clip_1",
         "timeline_start": 0.0, "timeline_end": 2.4},
        {"position": 7, "block_type": "speech", "clip_id": "clip_2",
         "timeline_start": 2.4, "timeline_end": 18.4},
        {"position": 14, "block_type": "speech", "clip_id": "clip_3",
         "timeline_start": 18.4, "timeline_end": 27.1},
        {"position": 15, "block_type": "speech", "clip_id": "clip_4",
         "timeline_start": 27.1, "timeline_end": 29.8},
    ]}


def _run(plan):
    """The post-bridge, run the way `run_pipeline` runs it."""
    payload = {
        "a_roll_assignments": [
            {"spine_block_position": 7,
             "video_segments": [{"clip_id": "clip_2",
                                 "source_file": "/tmp/IMG_1.mov"}]},
        ],
        "timed_spine": _spine(),
        "vfx_creative": plan,
    }
    proc = subprocess.run(
        [sys.executable, "-m", POST_BRIDGE],
        input=json.dumps(payload), capture_output=True,
        encoding="utf-8", cwd=str(REPO),
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)["enhancement_spec"], proc.stderr


# ── The vocabulary ────────────────────────────────────────────────────

def test_the_vocabulary_is_well_formed():
    assert_vocabulary_is_well_formed()


def test_the_two_empty_bases_are_spelled_differently():
    """A decision and the absence of one may not share a name.

    Same rule `cutaway_window.BASES` follows, where `single_span` is
    recorded apart from `moment_match`.
    """
    assert "no_effects_planned" in PLAN_BASES
    assert "every_entry_dropped" in PLAN_BASES
    assert "empty" not in PLAN_BASES


def test_the_basis_is_read_off_the_counts():
    assert plan_basis(0, 0) == "no_effects_planned"
    assert plan_basis(4, 0) == "every_entry_dropped"
    assert plan_basis(4, 2) == "planned"
    assert plan_basis(2, 2) == "planned"


def test_a_plan_cannot_grow():
    with pytest.raises(ValueError, match="cannot grow"):
        plan_basis(1, 2)


def test_an_unknown_drop_reason_is_refused_by_name():
    with pytest.raises(ValueError, match="unknown VFX drop reason"):
        DroppedEntry(target_block_position=1, effect_type="x",
                     reason="because_i_felt_like_it")


def test_an_unknown_basis_is_refused_by_name():
    with pytest.raises(ValueError, match="unknown VFX plan basis"):
        basis_summary({"basis": "fine", "proposed": 0, "resolved": 0})


def test_the_refusal_question_is_recorded_not_answered():
    """Whether a dropped entry should FAIL the step is the captain's.

    AGENTS.md 10.5 refuses an unplayable sound in 4.04 and 13 refuses an
    invented bookend, so the precedent exists - and this module records
    the question rather than settling it (the brief's "propose, do not
    settle").  The proof that it did not settle it is that a plan whose
    every entry is dropped still exits 0.
    """
    assert "captain" in THE_REFUSAL_QUESTION or "Not settled" in THE_REFUSAL_QUESTION
    spec, _ = _run([{"target_block_position": 7, "effect_type": "glitch",
                     "params": {"zoom_start": 1.0, "zoom_end": 1.03},
                     "rationale": "x"}])
    assert spec["visual_effects"] == []
    assert spec["planning_basis"]["basis"] == "every_entry_dropped"


def test_every_legend_key_is_a_key_of_the_record():
    spec, _ = _run([])
    assert set(BASIS_LEGEND) == set(spec["planning_basis"])


# ── The distinction, driven through the real post-bridge ──────────────

def test_a_plan_the_model_left_empty_says_so():
    spec, stderr = _run([])
    assert spec["visual_effects"] == []
    assert spec["planning_basis"] == {
        "basis": "no_effects_planned",
        "proposed": 0, "resolved": 0, "dropped": [],
    }
    assert "no_effects_planned" in stderr


def test_a_plan_whose_every_entry_was_dropped_says_that_instead():
    spec, stderr = _run([
        {"target_block_position": 14, "effect_type": "slow_zoom",
         "params": {}, "rationale": "a"},
        {"target_block_position": 7, "effect_type": "glitch",
         "params": {}, "rationale": "b"},
        {"target_block_position": 99, "effect_type": "slow_zoom_in",
         "params": {}, "rationale": "c"},
    ])
    assert spec["visual_effects"] == []
    basis = spec["planning_basis"]
    assert basis["basis"] == "every_entry_dropped"
    assert basis["proposed"] == 3 and basis["resolved"] == 0
    assert [d["reason"] for d in basis["dropped"]] == [
        "withdrawn_alias", "unknown_effect_type",
        "not_a_spine_block",
    ]
    for drop in basis["dropped"]:
        assert drop["detail"], "a drop states its own sentence"
    assert "every_entry_dropped" in stderr


def test_the_two_empty_plans_are_not_the_same_bytes():
    """The whole point, stated as the comparison that used to fail."""
    chosen, _ = _run([])
    dropped, _ = _run([{"target_block_position": 7,
                        "effect_type": "glitch",
                        "params": {"zoom_start": 1.0},
                        "rationale": "x"}])
    assert chosen["visual_effects"] == dropped["visual_effects"] == []
    assert json.dumps(chosen, sort_keys=True) != json.dumps(dropped, sort_keys=True)


def test_a_partly_dropped_plan_is_planned_and_names_its_casualties():
    spec, _ = _run([
        {"target_block_position": 14, "effect_type": "slow_zoom_in",
         "params": {"zoom_start": 1.0, "zoom_end": 1.03},
         "rationale": "the one long static hold"},
        {"target_block_position": 7, "effect_type": "glitch",
         "params": {"zoom_start": 1.0, "zoom_end": 1.04}, "rationale": "b"},
    ])
    basis = spec["planning_basis"]
    assert basis["basis"] == "planned"
    assert basis["proposed"] == 2 and basis["resolved"] == 1
    assert [d["reason"] for d in basis["dropped"]] == ["unknown_effect_type"]
    assert len(spec["visual_effects"]) == 1


def test_a_fully_resolved_plan_drops_nothing():
    spec, _ = _run([
        {"target_block_position": 14, "effect_type": "slow_zoom_in",
         "params": {"zoom_start": 1.0, "zoom_end": 1.03}, "rationale": "a"},
        {"target_block_position": 7, "effect_type": "zoom_emphasis",
         "params": {"zoom_start": 1.0, "zoom_end": 1.03}, "rationale": "b"},
    ])
    basis = spec["planning_basis"]
    assert basis == {"basis": "planned", "proposed": 2, "resolved": 2,
                     "dropped": []}
    assert len(spec["visual_effects"]) == 2


# ── What the record must not do ───────────────────────────────────────

def test_recording_the_basis_never_pads_the_plan():
    """AGENTS.md 10.5: there are no creative floors, in code or prompt.

    The record says what happened; it may never make something happen.
    """
    spec, _ = _run([])
    assert spec["visual_effects"] == []
    assert "generator_overlays" not in spec


def test_the_record_carries_no_creative_value():
    """No intensity, no effect type, no count is invented by the record.

    Every value in it is either a count of what the planner said or a
    verbatim echo of what the planner asked for.
    """
    spec, _ = _run([{"target_block_position": 7, "effect_type": "glitch",
                     "params": {"zoom_start": 1.0, "zoom_end": 1.04},
                     "rationale": "x"}])
    drop = spec["planning_basis"]["dropped"][0]
    assert drop["effect_type"] == "glitch"
    assert drop["target_block_position"] == 7


def test_the_dataclass_and_the_json_agree():
    record = PlanBasis(
        proposed=2, resolved=1,
        dropped=[DroppedEntry(7, "glitch", "unknown_effect_type", "d")],
    ).as_dict()
    assert record["basis"] == "planned"
    assert record["dropped"][0]["reason"] in DROP_REASONS
    assert basis_summary(record).startswith("VFX plan: planned")
