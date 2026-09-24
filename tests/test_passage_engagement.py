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
    engagement_basis,
    engagement_rank,
    is_unjudged,
    unjudged_summary,
)
from library.steps.step_5_03_creative_cohesion.step import (
    review_creative_cohesion,
)


# ── The non-negotiable: absent means absent ───────────────────────────

class TestAnAbsentMeasurementStaysAbsent:
    """The property is unchanged; what carries it is now the ORDERING.

    The 0-100 composite was withdrawn with the closed role vocabulary on
    the captain's ruling of 2026-09-02 - only the rank was ever consumed
    - so `engagement_rank` is the reader these assertions hold, and there
    is no magnitude reader left to hold them on.
    """

    def test_it_is_none_and_not_last(self):
        """N is a rank. None is the absence of one. They are not the
        same fact and a reader that cannot tell them apart is what put
        `Hook engagement (0)` into project 001's cohesion review."""
        value = engagement_rank({})
        assert value is None
        assert value != 0
        assert not isinstance(value, int)

    @pytest.mark.parametrize("passage", [
        {},
        {"engagement": "high"},
        None,
    ])
    def test_nothing_that_is_not_a_judgement_becomes_one(self, passage):
        assert engagement_rank(passage) is None


# ── No scorer exists to read a missing measurement ────────────────────

class TestNoScorerSubstitutesANumber:
    def test_the_withdrawn_scorers_are_gone(self):
        import library.tools.passage_engagement as mod
        for name in ("score_hook", "score_flow", "score_value",
                     "compute_engagement"):
            assert not hasattr(mod, name), (
                f"{name} is back; it scored passages off measurements this "
                f"pipeline does not produce")

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


def test_a_sequence_with_no_ranks_states_the_absence_and_finds_nothing():
    """No judgement is not a bad judgement, and the review says which."""
    speech = {"body_sequence": [{"text": "a"}, {"text": "b"}]}
    review = review_creative_cohesion(_inputs(speech))
    joined = " ".join(review["warnings"])
    assert NO_ENGAGEMENT_BASIS in joined
    assert "ranked strongest" not in joined
    assert review["adjustments"] == []


# ── An unjudgeable passage reads as unjudged, not as a low score ──────

# The shape step 2.02's handoff asks for when the model cannot place a
# passage: rank null, and the reason in `basis`.
UNJUDGED = {
    "clip_id": "clip_004",
    "text": "uh so anyway",
    "engagement": {
        "rank": None,
        "basis": "half of this passage is wind noise over the mic, so I "
                 "cannot tell whether it holds a viewer",
    },
}

JUDGED = {
    "clip_id": "clip_012",
    "text": "it just matters that it gets posted",
    "engagement": {"rank": 1,
                   "basis": "the line the whole piece is built on"},
}


class TestAnUnjudgedPassageIsNotALowScore:
    """The failure this guards is the one that produced the 49s: a
    passage nobody could judge being read as a passage that judged badly.
    An absent judgement must stay absent through every reader, and the
    REASON must survive so the absence can be stated rather than blanked.
    """

    def test_it_does_not_become_zero_or_last(self):
        """0 and N are both ranks. Neither is the absence of one."""
        assert engagement_rank(UNJUDGED) != 0
        assert engagement_rank(UNJUDGED) != 999
        assert not isinstance(engagement_rank(UNJUDGED), int)

    def test_the_reason_survives_so_the_absence_can_be_stated(self):
        assert "wind noise" in engagement_basis(UNJUDGED)

    def test_declining_to_judge_is_not_the_same_as_never_being_asked(self):
        """Both read as absent to `engagement_rank`, and only one has
        something to report. A passage with no key was never asked."""
        assert is_unjudged(UNJUDGED) is True
        assert is_unjudged({"text": "never asked"}) is False
        assert is_unjudged(JUDGED) is False

    def test_the_summary_names_how_many_and_why(self):
        line = unjudged_summary([JUDGED, UNJUDGED, JUDGED])
        assert line.startswith("1 of 3 passage(s) were not judged")
        assert "wind noise" in line

    def test_a_malformed_rank_is_absent_and_not_guessed(self):
        for bad in ("1", 1.5, True, 0, -3, None):
            assert engagement_rank({"engagement": {"rank": bad}}) is None, bad


