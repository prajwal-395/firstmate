"""A missing measurement must never resolve to a value that reads as a

With the engagement judgement absent, the rank is ABSENT (None) - never a
plausible number - and every reader says it has no basis.
History: `docs/evidence/passage_engagement.md`.
"""
import re
import sys
from pathlib import Path


REPO = Path(__file__).resolve().parents[3]
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


# ── No scorer exists to read a missing measurement ────────────────────

class TestNoScorerSubstitutesANumber:
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
        assert "wind noise" in engagement_basis(UNJUDGED)

    def test_an_absent_or_malformed_rank_is_none_never_zero_or_last(self):
        """N is a rank; None is the absence of one. A reader that cannot
        tell them apart put `Hook engagement (0)` into project 001's
        cohesion review."""
        for passage in ({}, {"engagement": "high"}, None, UNJUDGED):
            assert engagement_rank(passage) is None, passage
        for bad in ("1", 1.5, True, 0, -3, None):
            assert engagement_rank({"engagement": {"rank": bad}}) is None, bad


