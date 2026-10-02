"""A repeated take is removed WHOLE or not at all (`redundant_runs`,
`refused_take_groups`, `assert_takes_are_whole`); a withheld cut is
reported, never silent. Reel 03's real segments travel as data.

History: docs/evidence/reel_take_cuts.md.
"""

from __future__ import annotations

import pytest

from library.tools.reel_build import (
    Cut,
    DURATION_RATIO,
    ReelBuildError,
    assert_takes_are_whole,
    redundant_takes,
    refused_take_groups,
)

MXF = "/m/a.MXF"


def _seg(speaker, text, start, end, uid, words=None):
    segment = {"speaker": speaker, "text": text, "timeline_start": start,
               "timeline_end": end, "source_file": MXF,
               "source_start": start, "source_end": end,
               "resolve_item_id": uid}
    if words is not None:
        segment["words"] = [
            {"word": word, "start": at, "end": until, "timed": True}
            for word, at, until in words]
    return segment


TAKE_ONE = "3d0a-take-one"
TAKE_TWO = "55c6-take-two"

# Reel 03, master 301.241-341.270s. Take one is the first four lines;
# take two is the next four; the 309.92/310.12 pair is one utterance
# WhisperX emitted twice, 0.200s and 0.260s long, carrying the whole
# sentence as its text both times.
REEL_03_SEGMENTS = [
    _seg("Akshita", "Yeah, so search didn't change.", 301.241, 302.566,
         TAKE_ONE,
         [("Yeah,", 301.241, 301.441), ("so", 301.522, 301.642),
          ("search", 301.662, 301.883), ("didn't", 301.903, 302.164),
          ("change.", 302.204, 302.566)]),
    _seg("Akshita", "The question changed.", 302.626, 303.449, TAKE_ONE,
         [("The", 302.626, 302.706), ("question", 302.746, 303.068),
          ("changed.", 303.108, 303.449)]),
    _seg("Akshita", "And whoever AI best understands, gets the answer.",
         303.549, 306.400, TAKE_ONE,
         [("And", 303.549, 303.670), ("whoever", 303.971, 304.332),
          ("AI", 304.453, 304.633), ("best", 304.674, 304.894),
          ("understands,", 304.935, 305.577), ("gets", 305.938, 306.119),
          ("the", 306.139, 306.219), ("answer.", 306.259, 306.400)]),
    _seg("Akshita", "yeah", 306.400, 306.527, TAKE_ONE,
         [("yeah", 306.400, 306.527)]),
    _seg("Akshita", "So,yeah, so search didn't change", 306.801, 307.596,
         TAKE_TWO),
    _seg("Akshita", "The question changed.", 307.840, 308.840, TAKE_TWO),
    _seg("Akshita", "So,yeah", 308.840, 309.111, TAKE_TWO),
    _seg("Akshita", "and whoever AI understands best, gets the answer.",
         309.320, 309.920, TAKE_TWO),
    _seg("Akshita", "Yeah, so search didn't change. The question changed. "
                    "And whoever AI understands best, gets the answer.",
         309.920, 310.120, TAKE_TWO),
    _seg("Akshita", "Yeah, so search didn't change. The question changed. "
                    "And whoever AI understands best, gets the answer.",
         310.120, 310.380, TAKE_TWO),
    _seg("Akshita", "best will have the answers.", 310.380, 312.041, TAKE_TWO),
    _seg("Craig", "the questions change so that's why this is so important "
                  "that's who ai is going to recommend", 313.690, 317.950,
         "craig-1"),
    _seg("Craig", "to be the answer it's exactly why we've been building "
                  "this platform", 318.091, 321.530, "craig-2"),
]

REEL_03 = {"segments": REEL_03_SEGMENTS}
REEL_03_START, REEL_03_END = 301.241, 341.270

# What `redundant_takes` returned on this span before the rule landed:
# two of take one's four lines, and nothing for the other two.
THE_PARTIAL_CUT = [
    Cut(dropped_start=301.241, dropped_end=302.566,
        dropped_text="Yeah, so search didn't change.",
        kept_start=306.801, kept_end=307.596,
        kept_text="So,yeah, so search didn't change",
        speaker="Akshita", containment=1.0, jaccard=1.0),
    Cut(dropped_start=302.626, dropped_end=303.449,
        dropped_text="The question changed.",
        kept_start=307.840, kept_end=308.840,
        kept_text="The question changed.",
        speaker="Akshita", containment=1.0, jaccard=1.0),
]


# ── The coherent outcome happens ─────────────────────────────────────

def test_a_take_is_cut_whole_or_not_at_all():
    """Neither of take one's first two lines goes on its own.

    Before the rule this returned three cuts, two of them here.  The
    third line could not be paired safely, so none of the run may go.
    """
    cuts = redundant_takes(REEL_03_START, REEL_03_END, REEL_03)
    dropped = sorted(round(cut.dropped_start, 3) for cut in cuts)
    assert 301.241 not in dropped, cuts
    assert 302.626 not in dropped, cuts
    _a_repeated_run_that_can_go_whole_still_goes()


def _a_repeated_run_that_can_go_whole_still_goes():
    """The rule withholds partial cuts, not cutting.

    Both lines of this take pair safely, so both are removed - the same
    two-line shape reel 03's take one has, without the third line the
    duration test refuses.
    """
    line_one = "so last week we ran an audit on a client for their website"
    line_two = "and their SEO team had stuffed the H1 tags with keywords"
    transcript = {"segments": [
        _seg("Akshita", line_one, 10.0, 14.0, "a"),
        _seg("Akshita", line_two, 14.1, 18.0, "a"),
        _seg("Akshita", line_one, 20.0, 24.5, "b"),
        _seg("Akshita", line_two, 24.6, 28.0, "b"),
    ]}
    cuts = redundant_takes(0.0, 60.0, transcript)
    assert sorted(round(cut.dropped_start, 3) for cut in cuts) == [10.0, 14.1]


# ── The orphan outcome is refused ────────────────────────────────────

def test_a_partly_cut_take_is_refused():
    """The gate fires on the exact cut list the rebuild used.

    `assert_takes_are_whole` is asked of the list ABOUT TO BE APPLIED,
    whatever produced it, so a second producer cannot strand a fragment
    by going round `redundant_takes`.
    """
    with pytest.raises(ReelBuildError) as refusal:
        assert_takes_are_whole(THE_PARTIAL_CUT, REEL_03_START, REEL_03_END,
                               REEL_03)
    said = str(refusal.value)
    assert "WHOLE or not at all" in said
    assert "And whoever AI best understands, gets the answer." in said


# ── What the refusal SAYS ────────────────────────────────────────────

def test_the_refused_run_names_what_stopped_it_and_what_it_kept_in():
    """A withheld cut is reported, never silent.

    The report is what reaches the model at selection time
    (`reel_proposal.enrich`) and the operator at build time, because the
    repetition is STILL IN THE REEL and somebody has to know that.
    """
    groups = refused_take_groups(REEL_03_START, REEL_03_END, REEL_03)
    assert len(groups) == 1, groups
    group = groups[0]

    assert group["speaker"] == "Akshita"
    assert group["lines"][0] == "Yeah, so search didn't change."
    assert len(group["would_have_cut"]) == 2
    stopper = group["could_not_cut"][0]
    assert stopper["duration_ratio"] == 4.75
    assert f"DURATION_RATIO={DURATION_RATIO}" in stopper["refused_by"]
    assert "still in the reel" in group["why_nothing_was_cut"]
    # Only a run where a cut was actually taken away is reported: a lone
    # pair the duration test refuses on its own (Reels 07, 17, 18) has
    # nothing withheld, and calling it a refusal would describe a
    # difference the rule does not make.
    long_line = ("it is going to start hallucinating because it is confused "
                 "about what you actually do")
    lone = {"segments": [
        _seg("Akshita", long_line, 10.0, 14.3, "a"),
        _seg("Akshita", "confused about what you actually do hallucinating",
             15.0, 15.5, "b"),
    ]}
    assert redundant_takes(0.0, 60.0, lone) == []
    assert refused_take_groups(0.0, 60.0, lone) == []
