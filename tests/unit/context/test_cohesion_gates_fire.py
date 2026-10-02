"""The cohesion review's gates can fire, measure the timeline, and judge nothing.

The duration gate measures the spine against the project's own declaration;
the engagement gate compares the play order with the model's own ranking;
the pacing thresholds and the score are gone (AGENTS.md 10.5). History:
docs/evidence/creative_cohesion.md.
"""
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from library.steps.step_5_03_creative_cohesion.step import (
    review_creative_cohesion,
)
from library.tools.timeline_duration import (
    measure_timeline_duration,
)


def _engagement(rank):
    """The shape step 2.02's handoff asks for: a place in the sequence's
    own ordering and one sentence for itself. No number: the 0-100
    composite is withdrawn (captain, 2026-09-02)."""
    return {"rank": rank,
            "basis": "a judgement of this passage against the others"}


def _passage(clip_id, start, rank):
    """A passage the gate can locate, so a finding can name where it is."""
    return {"clip_id": clip_id, "source_start": start,
            "source_end": start + 3.0,
            "engagement": _engagement(rank)}


# ── The duration gate measures the timeline ───────────────────────────

# 001's numbers: a 54.77 s timeline whose last speech passage ends at
# 40.1 s inside its own source clip.
SPINE_001 = {"structure": [
    {"block_type": "hook", "position": 0,
     "timeline_start": 0.0, "timeline_end": 3.4},
    {"block_type": "speech", "position": 1,
     "timeline_start": 3.4, "timeline_end": 54.77},
]}
SPEECH_001 = {"body_sequence": [
    {"start_time": 33.0, "end_time": 40.1,
     "engagement": _engagement(1)},
]}


class TestMeasureTimelineDuration:

    def test_a_roll_is_the_fallback(self):
        assert measure_timeline_duration({
            "a_roll_assignments": [
                {"timeline_start": 0.0, "timeline_end": 20.0},
                {"timeline_start": 20.0, "timeline_end": 54.77},
            ]}) == pytest.approx(54.77)


def _inputs(**kw):
    base = {
        "creative_direction": {"target_energy": "moderate"},
        # 001's own project.yaml declaration, which is what the gate now
        # measures against. It used to be handed 54/60/66 by
        # `duration_targets` whatever any project declared, and nothing
        # ever put a `project_config` in state at all - so the captain's
        # number governed nothing and the gate ran on a made-up minute.
        "project_config": {"target_duration_seconds": 60},
        "transition_spec": [],
        "sfx_spec": [],
        "speech_sequence": SPEECH_001,
        "audio_spine": SPINE_001,
    }
    base.update(kw)
    return base


def test_the_gate_is_the_projects_own_declaration():
    """54-66s is 001's `target_duration_seconds: 60` plus or minus 10%."""
    from library.tools.duration_targets import get_target_duration_zone
    assert get_target_duration_zone(_inputs()) == (54.0, 60.0, 66.0)


def test_with_no_declared_target_the_gate_says_so_rather_than_firing():
    """A gate with nothing to judge against is not coverage."""
    inputs = _inputs()
    inputs.pop("project_config")
    review = review_creative_cohesion(inputs)
    assert any(w.startswith("Duration not checked:")
               for w in review["warnings"])
    assert not any("target zone" in w for w in review["warnings"])


def test_the_duration_gate_passes_001_and_still_fires_on_a_short_cut():
    """The concrete false warning (40.1s reported about a 54.77s cut) is
    gone, and the gate must still be able to fire."""
    review = review_creative_cohesion(_inputs())
    assert review["timeline_duration_seconds"] == pytest.approx(54.77)
    assert not any("Duration warning" in w for w in review["warnings"]), (
        f"a timeline inside the 54-66s zone was flagged: "
        f"{review['warnings']}")

    short = {"structure": [{"block_type": "speech", "position": 0,
                            "timeline_start": 0.0, "timeline_end": 30.0}]}
    review = review_creative_cohesion(_inputs(audio_spine=short))
    assert any("below the minimum target zone" in w
               for w in review["warnings"])
    assert "30.0s" in " ".join(review["warnings"])


# ── The engagement gate can fire ──────────────────────────────────────

def test_a_buried_peak_is_detected_and_an_opening_peak_is_not():
    """The gate's whole purpose: the strongest passage is not the opener.

    It reads TWO ORDERINGS and nothing else - where the passage sits in
    `body_sequence`, which is the order it plays in, against the rank the
    model gave it. The `hook_segment` this used to compare against was
    withdrawn with the closed role vocabulary; the ordering is the thing
    that survived, so the ordering is what the finding is made of.
    """
    speech = {"body_sequence": [
        _passage("clip_002", 1.0, rank=3),
        _passage("clip_007", 20.0, rank=2),
        _passage("clip_016", 48.065, rank=1),
    ]}
    review = review_creative_cohesion(_inputs(speech_sequence=speech))
    joined = " ".join(review["warnings"])
    assert "ranked strongest" in joined, (
        f"the engagement gate still cannot fire: {review['warnings']}")
    assert "clip_016" in joined and "48.065" in joined, joined

    # No number is reported, because none is written any more.
    assert "composite" not in joined

    # It is an OBSERVATION, not an adjustment. Re-ordering the
    # narrative is step 2.02's decision and eleven steps of timing
    # rest on it, so `compile_manifest` refuses it - and a
    # recommendation nobody can apply, reported in an array named
    # `adjustments`, reads as a change that was made (#238).
    assert not any(a["target_step"] == "speech_sequence"
                   for a in review["adjustments"])
    ordering = [o for o in review["observations"]
                if o["state_key"] == "speech_sequence"]
    assert len(ordering) == 1, review["observations"]
    assert ordering[0]["owner_step"] == "step_2_02_speech_sequence"
    assert ordering[0]["finding"] == [w for w in review["warnings"]
                                      if "ranked strongest" in w][0]
    # No `suggested_value`: "front_loaded" was a word this step
    # invented about an ordering it never computed.
    assert "suggested_value" not in ordering[0]

    speech = {"body_sequence": [
        _passage("clip_016", 48.065, rank=1),
        _passage("clip_007", 20.0, rank=3),
        _passage("clip_009", 30.0, rank=2),
    ]}
    review = review_creative_cohesion(_inputs(speech_sequence=speech))
    assert not any("ranked strongest" in w for w in review["warnings"])


# ── The review reports counts and judges none of them ────────────────

# The timeline the duration gate measures. It used to read
# `body_sequence[-1]["end_time"]` - a SOURCE timestamp - so these fixtures
# expressed the length as a speech passage ending at 60s. The spine is the
# timeline; see library/tools/timeline_duration.py.
SPINE_60S_HOOKED = {
    "structure": [
        {"block_type": "hook", "position": 0,
         "timeline_start": 0.0, "timeline_end": 3.0},
        {"block_type": "speech", "position": 1,
         "timeline_start": 3.0, "timeline_end": 60.0},
    ]
}


def test_counts_are_reported_and_nothing_is_judged():
    """The four thresholds are gone; the counts are what is reported.

    This used to assert "High energy but found slow transition (1000.0ms)"
    and an adjustment rewriting `duration_frames` to 10.  Both the 500 ms
    line and the 10 frames were numbers this step picked, and
    `duration_frames` is the ONE field `compile_manifest` rewrites, so the
    picked number reached the picture.
    """
    inputs = {
        "creative_direction": {"target_energy": "high"},
        "transition_spec": [
            {"transition_type": "defocus", "duration_frames": 30}
        ],
        "sfx_spec": [],  # sparse
        "project_config": {"target_duration_seconds": 60},
        "speech_sequence": {"body_sequence": [{"start_time": 0, "end_time": 60}]},
        "audio_spine": SPINE_60S_HOOKED,
    }

    review = review_creative_cohesion(inputs)

    assert not any("High energy" in w for w in review["warnings"])
    assert review["adjustments"] == []
    assert review["observations"] == []

    assert review["measurements"] == {
        "declared_target_energy": "high",
        "transitions_planned": 1,
        "transitions_drawn": 1,
        "sfx_events": 0,
        "sfx_per_minute": 0.0,
    }

    transitions = [dict(t) for t in TRANSITIONS_001]
    review = review_creative_cohesion({
        "creative_direction": {"target_energy": "high"},
        "transition_spec": transitions,
        "sfx_spec": [],
        "project_config": {"target_duration_seconds": 60},
        "audio_spine": SPINE_60S_HOOKED,
    })

    assert review["adjustments"] == []
    assert review["measurements"]["transitions_planned"] == 15
    assert review["measurements"]["transitions_drawn"] == 2
    assert [t["duration_frames"] for t in transitions
            if t["transition_id"] in ("trans_008", "trans_013")] == [15, 15]


# Project 001's REAL `transition_spec`, as step 4.02 emitted it on the
# 2026-08-26 run: fifteen entries, eleven `hard_cut` and two `jump_cut` at
# zero frames, and two `defocus` at 15 frames - 500 ms exactly, which is
# the high-energy trigger. The rows are the real ones, keys and all, so
# this covers the one actionable finding against the shape the planner
# really produces rather than against `{"type": "cross_dissolve"}`, which
# is a withdrawn type under a key alias and is what the removed test used.
#
# 001 itself declares `target_energy: "building"`, which reads as
# `moderate` (AGENTS.md 10.1), so the check is inert on 001 as it stands.
# The energy is the ONE value this fixture supplies.
TRANSITIONS_001 = (
    [{"transition_id": f"trans_{i:03d}", "transition_type": "hard_cut",
      "duration_frames": 0, "cut_point_timeline": 2.398 + i} for i in range(1, 8)]
    + [{"transition_id": "trans_008", "transition_type": "defocus",
        "duration_frames": 15, "cut_point_timeline": 34.394}]
    + [{"transition_id": f"trans_{i:03d}", "transition_type": "hard_cut",
        "duration_frames": 0, "cut_point_timeline": 2.398 + i} for i in range(9, 13)]
    + [{"transition_id": "trans_013", "transition_type": "defocus",
        "duration_frames": 15, "cut_point_timeline": 46.147}]
    + [{"transition_id": f"trans_{i:03d}", "transition_type": "jump_cut",
        "duration_frames": 0, "cut_point_timeline": 2.398 + i} for i in (14, 15)]
)
