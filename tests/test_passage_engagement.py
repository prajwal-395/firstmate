"""A missing measurement must never resolve to a value that reads as a
real one.

The `engagement_scorer` this replaces gave nine of project 001's eleven
speech passages an identical composite of 49, and a `hook` component of 30
to ten of eleven, because `prosody_data.get("energy_rms", 0)` returned 0
for every line the captain has ever said and `0 < 0.3` subtracted twenty
points from a base of fifty.  The scorers are withdrawn - see
library/tools/passage_engagement.py for why each one was not a
measurement.

These tests hold the property, not the implementation: with the
measurement absent, the score must be ABSENT, never a plausible-looking
number, and every reader must say it has no basis.
"""
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from library.tools.passage_engagement import (
    NO_ENGAGEMENT_BASIS,
    WITHDRAWN_SCORERS,
    engagement_basis,
    engagement_of,
    engagement_rank,
    is_unjudged,
    unjudged_summary,
)
from library.steps.step_5_03_creative_cohesion.step import (
    review_creative_cohesion,
)


# ── The non-negotiable: absent means absent ───────────────────────────

class TestAnAbsentMeasurementStaysAbsent:
    def test_a_passage_with_no_engagement_scores_nothing(self):
        assert engagement_of({"text": "and i have an announcement to make."}) is None

    def test_it_is_none_and_not_zero(self):
        """0 is a score. None is the absence of one. They are not the
        same fact and a reader that cannot tell them apart is what put
        `Hook engagement (0)` into project 001's cohesion review."""
        value = engagement_of({})
        assert value is None
        assert value != 0
        assert not isinstance(value, (int, float))

    @pytest.mark.parametrize("passage", [
        {},
        {"engagement": None},
        {"engagement": {}},
        {"engagement": {"rationale": "Hook:30, Flow:60, Value:60"}},
        {"engagement": "high"},
        {"engagement": True},
        None,
        "not a passage",
    ])
    def test_nothing_that_is_not_a_score_becomes_one(self, passage):
        assert engagement_of(passage) is None

    def test_a_real_score_is_still_read(self):
        """The reader is not the thing withdrawn. A step that measures or
        judges engagement and writes it on the passage needs no change."""
        assert engagement_of({"engagement": {"composite": 71}}) == 71.0
        assert engagement_of({"engagement": 71}) == 71.0


# ── No scorer exists to read a missing measurement ────────────────────

class TestNoScorerSubstitutesANumber:
    def test_the_withdrawn_scorers_are_gone(self):
        import library.tools.passage_engagement as mod
        for name in ("score_hook", "score_flow", "score_value",
                     "compute_engagement"):
            assert not hasattr(mod, name), (
                f"{name} is back; it scored passages off measurements this "
                f"pipeline does not produce")
        assert not (REPO / "library" / "tools" / "engagement_scorer.py").exists()

    def test_every_withdrawal_carries_its_reason(self):
        for name, reason in WITHDRAWN_SCORERS.items():
            assert len(reason) > 80, f"{name} is withdrawn without a reason"

    def test_energy_rms_is_read_nowhere(self):
        """It is emitted nowhere either: `analyze_prosody` produces
        pitch_stats, pitch_contour_10ms, voice_quality, speaking_rate and
        intensity_contour_50ms, and none of them has a key by that name.

        Prose may name it - a withdrawal has to say what it withdrew, so
        comments are stripped before matching and passage_engagement.py,
        which is the record itself, is exempt. What must not come back is
        a READ: a subscript or a `.get`, which is the shape that turned an
        absent dependency into a number."""
        record = REPO / "library" / "tools" / "passage_engagement.py"
        read = re.compile(
            r"""\.get\(\s*["']energy_rms["']|\[\s*["']energy_rms["']\s*\]""")
        hits = [
            f"{p.relative_to(REPO)}:{i}"
            for p in (REPO / "library").rglob("*.py") if p != record
            for i, line in enumerate(p.read_text(errors="ignore").splitlines(), 1)
            if read.search(line.split("#", 1)[0])
        ]
        assert not hits, f"energy_rms is read again at {hits}"

    def test_the_dummy_flow_scaffold_is_gone(self):
        record = REPO / "library" / "tools" / "passage_engagement.py"
        hits = [
            str(p.relative_to(REPO))
            for p in (REPO / "library").rglob("*.py") if p != record
            if "Dummy flow scoring" in p.read_text(errors="ignore")
        ]
        assert not hits, f"the flow scaffold is back in {hits}"


# ── Step 2.02 attaches no score ───────────────────────────────────────

def test_the_speech_sequence_bridge_writes_no_engagement(tmp_path):
    """Drive the real post-bridge and assert on its output.

    A floor - or a fabricated score - that lives in a bridge is still a
    fabrication; that is where the last one hid (AGENTS.md 10.5).
    """
    sys.path.insert(0, str(REPO / "library" / "steps" / "step_2_02_speech_sequence"))
    from library.steps.step_2_02_speech_sequence.post_bridge import (
        enrich_speech_sequence,
    )

    index_dir = tmp_path / "temporal_index"
    index_dir.mkdir()
    words = [
        {"word": "it", "start": 1.00, "end": 1.10},
        {"word": "just", "start": 1.10, "end": 1.35},
        {"word": "matters", "start": 1.35, "end": 1.80},
        {"word": "that", "start": 1.80, "end": 1.95},
        {"word": "it", "start": 1.95, "end": 2.05},
        {"word": "gets", "start": 2.05, "end": 2.30},
        {"word": "posted", "start": 2.30, "end": 2.80},
    ]
    (index_dir / "clip_012.json").write_text(json.dumps({
        "clip_id": "clip_012",
        "speech_regions": [{"start": 1.0, "end": 2.8,
                            "text": "it just matters that it gets posted",
                            "words": words}],
    }))

    sequence = {
        "hook_segment": {
            "clip_id": "clip_012", "text": "it just matters that it gets posted",
            "source_start": 1.0, "source_end": 2.8,
        },
        "body_sequence": [],
    }
    result = enrich_speech_sequence(sequence, str(index_dir))
    hook = result["hook_segment"]

    assert hook["word_timestamps"], "the bridge did not align the passage"
    assert "engagement" not in hook, (
        f"the bridge attached an engagement score: {hook.get('engagement')}")


# ── Step 5.03 says it has no basis ────────────────────────────────────

def _inputs(speech_sequence):
    return {
        "creative_direction": {"target_energy": "high"},
        "transition_spec": [],
        "sfx_spec": [],
        "color_grade_spec": {},
        "speech_sequence": speech_sequence,
        "audio_spine": {"structure": [
            {"block_type": "speech", "position": 0,
             "timeline_start": 0.0, "timeline_end": 55.0},
        ]},
    }


def test_the_cohesion_review_states_the_absence():
    """Project 001's real shape after this change: eleven passages, none
    carrying a score. The review must say so, and must not report a
    comparison of zeros."""
    speech = {
        "hook_segment": {"text": "i can feel the silent judgment"},
        "body_sequence": [{"text": f"passage {i}"} for i in range(10)],
    }
    review = review_creative_cohesion(_inputs(speech))
    joined = " ".join(review["warnings"])

    assert NO_ENGAGEMENT_BASIS in joined, (
        f"the review did not state that it has no ranking basis: "
        f"{review['warnings']}")
    assert not re.search(r"Hook engagement \(", joined), (
        f"the review reported a comparison it could not make: {joined}")
    assert not any(a["target_step"] == "speech_sequence"
                   for a in review["adjustments"]), (
        "the review recommended a re-order off scores that do not exist")


def test_stating_the_absence_costs_no_cohesion_score():
    """An unmeasured signal is not a defect in the edit."""
    unscored = review_creative_cohesion(_inputs(
        {"hook_segment": {}, "body_sequence": [{}, {}]}))
    scored = review_creative_cohesion(_inputs({
        "hook_segment": {"engagement": {"composite": 92}},
        "body_sequence": [{"engagement": {"composite": 60}},
                          {"engagement": {"composite": 70}}],
    }))
    assert unscored["cohesion_score"] == scored["cohesion_score"]


def test_a_half_scored_sequence_is_not_compared_either():
    """A hook with no score against bodies that have one is not a
    comparison, and coercing the missing half to 0 is what made every
    hook look buried."""
    speech = {
        "hook_segment": {},
        "body_sequence": [{"engagement": {"composite": 92}}],
    }
    review = review_creative_cohesion(_inputs(speech))
    joined = " ".join(review["warnings"])
    assert NO_ENGAGEMENT_BASIS in joined
    assert "Hook engagement" not in joined


# ── An unjudgeable passage reads as unjudged, not as a low score ──────

# The shape step 2.02's handoff asks for when the model cannot place a
# passage: rank and composite null, and the reason in `basis`.
UNJUDGED = {
    "clip_id": "clip_004",
    "text": "uh so anyway",
    "engagement": {
        "rank": None,
        "composite": None,
        "basis": "half of this passage is wind noise over the mic, so I "
                 "cannot tell whether it holds a viewer",
    },
}

JUDGED = {
    "clip_id": "clip_012",
    "text": "it just matters that it gets posted",
    "engagement": {"rank": 1, "composite": 88,
                   "basis": "the line the whole piece is built on"},
}


class TestAnUnjudgedPassageIsNotALowScore:
    """The failure this guards is the one that produced the 49s: a
    passage nobody could judge being read as a passage that judged badly.
    An absent judgement must stay absent through every reader, and the
    REASON must survive so the absence can be stated rather than blanked.
    """

    def test_the_declined_judgement_carries_no_number(self):
        assert engagement_of(UNJUDGED) is None
        assert engagement_rank(UNJUDGED) is None

    def test_it_does_not_become_zero_or_last(self):
        """0 is a score and N is a rank. Neither is the absence of one."""
        assert engagement_of(UNJUDGED) != 0
        assert engagement_rank(UNJUDGED) != 999
        assert not isinstance(engagement_of(UNJUDGED), (int, float))
        assert not isinstance(engagement_rank(UNJUDGED), int)

    def test_the_reason_survives_so_the_absence_can_be_stated(self):
        assert "wind noise" in engagement_basis(UNJUDGED)

    def test_declining_to_judge_is_not_the_same_as_never_being_asked(self):
        """Both read as absent to `engagement_of`, and only one has
        something to report. A passage with no key was never asked."""
        assert is_unjudged(UNJUDGED) is True
        assert is_unjudged({"text": "never asked"}) is False
        assert is_unjudged(JUDGED) is False

    def test_the_summary_names_how_many_and_why(self):
        line = unjudged_summary([JUDGED, UNJUDGED, JUDGED])
        assert line.startswith("1 of 3 passage(s) were not judged")
        assert "wind noise" in line

    def test_a_fully_judged_sequence_has_nothing_to_report(self):
        assert unjudged_summary([JUDGED, JUDGED]) is None

    def test_a_malformed_rank_is_absent_and_not_guessed(self):
        for bad in ("1", 1.5, True, 0, -3, None):
            assert engagement_rank({"engagement": {"rank": bad}}) is None, bad


class TestTheCohesionReviewReadsTheJudgement:
    """The review's finding must come off the ORDERING the model wrote,
    and must never be produced by an unjudged passage."""

    def test_an_unjudged_passage_does_not_bury_the_hook(self):
        """The hook is ranked strongest; one body passage could not be
        judged. Reading that absence as a low score would be harmless
        here - but reading it as a HIGH one, or sorting it anywhere at
        all, is how a passage nobody judged comes to outrank the hook."""
        review = review_creative_cohesion(_inputs({
            "hook_segment": {**JUDGED, "engagement": {
                "rank": 1, "composite": 90, "basis": "the strongest line"}},
            "body_sequence": [
                UNJUDGED,
                {"clip_id": "clip_007", "source_start": 3.0, "source_end": 6.0,
                 "engagement": {"rank": 2, "composite": 70, "basis": "setup"}},
            ],
        }))
        joined = " ".join(review["warnings"])
        assert "ranked strongest" not in joined, joined
        assert not any(a["target_step"] == "speech_sequence"
                       for a in review["adjustments"])

    def test_the_unjudged_passage_is_named_rather_than_blanked(self):
        review = review_creative_cohesion(_inputs({
            "hook_segment": {"engagement": {"rank": 1, "composite": 90,
                                            "basis": "strongest"}},
            "body_sequence": [UNJUDGED],
        }))
        joined = " ".join(review["warnings"])
        assert "were not judged for engagement" in joined, joined
        assert "wind noise" in joined, joined

    def test_a_buried_peak_is_reported_off_the_ranking(self):
        """#246's own case: the strongest moment is a different moment
        from the hook, and it sits late in the body."""
        review = review_creative_cohesion(_inputs({
            "hook_segment": {
                "clip_id": "clip_002", "source_start": 1.0, "source_end": 3.0,
                "engagement": {"rank": 4, "composite": 61, "basis": "a tease"}},
            "body_sequence": [
                {"clip_id": "clip_002", "source_start": 1.0, "source_end": 3.0,
                 "engagement": {"rank": 3, "composite": 65, "basis": "opens"}},
                {"clip_id": "clip_016", "source_start": 48.065,
                 "source_end": 52.0,
                 "engagement": {"rank": 1, "composite": 92,
                                "basis": "the emotional floor of the piece"}},
            ],
        }))
        joined = " ".join(review["warnings"])
        assert "ranked strongest" in joined, joined
        assert "clip_016" in joined and "48.065" in joined, joined
        assert "composite 92 against the hook's 61" in joined, joined
        assert any(a["target_step"] == "speech_sequence"
                   for a in review["adjustments"])

    def test_a_hook_teasing_the_strongest_passage_is_not_a_finding(self):
        """The hook is CUT FROM the top-ranked passage. That is the
        shortform technique the handoff describes, so a review that
        reported it would be firing on a correct edit."""
        review = review_creative_cohesion(_inputs({
            "hook_segment": {
                "clip_id": "clip_016", "source_start": 48.5,
                "source_end": 50.0,
                "engagement": {"rank": 2, "composite": 88,
                               "basis": "a tease of the strongest line"}},
            "body_sequence": [
                {"clip_id": "clip_016", "source_start": 48.065,
                 "source_end": 52.0,
                 "engagement": {"rank": 1, "composite": 92,
                                "basis": "the emotional floor"}},
            ],
        }))
        joined = " ".join(review["warnings"])
        assert "ranked strongest" not in joined, joined

    def test_the_finding_is_not_fired_by_the_composite_alone(self):
        """Composites drift run to run; the ordering is what held across
        three answers to the identical prompt. A sequence whose numbers
        disagree with its own ranking must follow the RANKING."""
        review = review_creative_cohesion(_inputs({
            "hook_segment": {
                "clip_id": "clip_002", "source_start": 1.0, "source_end": 3.0,
                "engagement": {"rank": 1, "composite": 55,
                               "basis": "strongest despite a low number"}},
            "body_sequence": [
                {"clip_id": "clip_009", "source_start": 20.0,
                 "source_end": 24.0,
                 "engagement": {"rank": 2, "composite": 95,
                                "basis": "a high number, ranked second"}},
            ],
        }))
        joined = " ".join(review["warnings"])
        assert "ranked strongest" not in joined, joined
