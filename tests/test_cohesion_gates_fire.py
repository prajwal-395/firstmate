"""Two gates in `creative_cohesion`, one that could not fire and one
that measured the wrong quantity.

**The engagement check could not fire.** It tested
`isinstance(hook["engagement"], (int, float))`, and `speech_sequence`
wrote `engagement` as a DICT - `{hook, flow, value, composite,
rationale}`. So `hook_eng` was permanently 0 and `eng_values` permanently
`[]` on every real project. A gate that cannot fail reads as coverage;
this one now compares composites where they exist.

The scorer that produced those composites is itself withdrawn (#236): it
gave nine of project 001's eleven passages an identical 49, and the
composite it fed went the same way on the captain's ruling of 2026-09-02
- only the rank ordering was ever consumed. What writes a judgement now
is step 2.02's own model, asked in the handoff to place the passages it
selected in ONE ordering, and the gate compares nothing else: two
orderings the model itself wrote, the order the passages PLAY in against
the order it RANKED them in. Where a sequence carries no judgement at all
the gate still STATES that it has no basis; see
tests/test_passage_engagement.py.

**The duration check measured the wrong quantity.** It used
`body_sequence[-1]["end_time"]`, a SOURCE timestamp - where the last
passage ends inside its own clip - as the timeline length. For project
001 it reported "actual duration (40.1s) is below the minimum target zone
(54.0s)" about a 54.77 s timeline that `review_rough_cut` had already
measured as inside the zone, one step earlier, from the spine. Both now
call `library/tools/timeline_duration.measure_timeline_duration`.
"""
import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from library.steps.step_5_03_creative_cohesion.step import (
    review_creative_cohesion,
)
from library.tools.timeline_duration import (
    measure_timeline_duration,
)


STEP_DIR = REPO / "library" / "steps" / "step_5_03_creative_cohesion"


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


def test_the_001_timeline_is_inside_the_zone():
    """The concrete false warning: 40.1s reported about a 54.77s cut."""
    review = review_creative_cohesion(_inputs())
    assert review["timeline_duration_seconds"] == pytest.approx(54.77)
    assert not any("Duration warning" in w for w in review["warnings"]), (
        f"a timeline inside the 54-66s zone was flagged: "
        f"{review['warnings']}")


def test_a_genuinely_short_timeline_is_still_flagged():
    """The gate must still be able to fire."""
    short = {"structure": [{"block_type": "speech", "position": 0,
                            "timeline_start": 0.0, "timeline_end": 30.0}]}
    review = review_creative_cohesion(_inputs(audio_spine=short))
    assert any("below the minimum target zone" in w
               for w in review["warnings"])
    assert "30.0s" in " ".join(review["warnings"])




def test_the_step_declares_the_inputs_it_now_measures():
    """Without the declaration the runner injects nothing and the gate is
    back to measuring whatever it can reach."""
    manifest = json.loads(
        (STEP_DIR / "manifest.json").read_text(encoding="utf-8"))
    names = {i["name"] for i in manifest["interface"]["inputs"]}
    assert {"audio_spine", "a_roll_assignments"} <= names




# ── The engagement gate can fire ──────────────────────────────────────

def test_a_buried_peak_is_detected():
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


def test_an_edit_that_opens_on_its_strongest_passage_is_not_flagged():
    """It must not fire on a correct edit either."""
    speech = {"body_sequence": [
        _passage("clip_016", 48.065, rank=1),
        _passage("clip_007", 20.0, rank=3),
        _passage("clip_009", 30.0, rank=2),
    ]}
    review = review_creative_cohesion(_inputs(speech_sequence=speech))
    assert not any("ranked strongest" in w for w in review["warnings"])



