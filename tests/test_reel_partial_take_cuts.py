"""A repeated take is removed WHOLE or not at all.

The defect, measured on the pipeline's rebuild of the captain's reel 03
(master 301.241-341.270s, approved 2026-09-05):

Akshita says one sentence three times.  The transcript segments the
first take as three consecutive lines.  Two of them paired with the
second take at containment 1.000 and Jaccard 1.000 and were CUT; the
third paired just as confidently and was refused, because 2.851s against
0.600s is a duration ratio of 4.75 and `DURATION_RATIO` is 2.0.  So two
thirds of a take were removed and its TAIL was left - and because the
take was at the head of the span, that orphaned tail became the reel's
first line.  The rebuild opened on "And whoever AI best understands,
gets the answer", the answer before the question, and the model's own
written hook did not arrive until 3.41 seconds in.  2.348s of repetition
really did go, and the reel was worse at the open than the timeline it
replaced.

The segments below are the captain's real ones, copied verbatim from
`pipeline_output/scratch/timeline_transcript/transcript.json` on the
field-test project.  No test here reads that project (AGENTS.md 8): the
measurement travels as data so the case can be run anywhere.

`library/tools/reel_build.py` - `redundant_runs`, `refused_take_groups`,
`assert_takes_are_whole`.
"""

from __future__ import annotations

import pytest

from library.tools.reel_build import (
    Cut,
    DURATION_RATIO,
    ReelBuildError,
    assert_takes_are_whole,
    keep_ranges,
    redundant_runs,
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

def test_reel_03s_first_take_is_not_partly_cut():
    """Neither of take one's first two lines goes on its own.

    Before the rule this returned three cuts, two of them here.  The
    third line could not be paired safely, so none of the run may go.
    """
    cuts = redundant_takes(REEL_03_START, REEL_03_END, REEL_03)
    dropped = sorted(round(cut.dropped_start, 3) for cut in cuts)
    assert 301.241 not in dropped, cuts
    assert 302.626 not in dropped, cuts


def test_reel_03_opens_on_the_hook_and_not_on_an_orphaned_tail():
    """The reel's first played second is the span's own first second.

    The rebuilt reel opened on "And whoever AI best understands, gets
    the answer" - the third line of a take whose first two had been cut
    from in front of it - and reached the model's written hook 3.41s in.
    """
    from library.tools.reel_opening import opening_words

    cuts = redundant_takes(REEL_03_START, REEL_03_END, REEL_03)
    ranges = keep_ranges(REEL_03_START, REEL_03_END, cuts)

    assert ranges[0][0] == REEL_03_START, ranges
    opening = " ".join(word["word"] for word in opening_words(ranges, REEL_03))
    assert opening.startswith("Yeah, so search didn't change."), opening
    assert not opening.startswith("And whoever"), opening


def test_a_repeated_run_that_can_go_whole_still_goes():
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


def test_the_cut_the_rule_produces_passes_its_own_gate():
    """The other direction: a coherent list is not refused.

    A gate that refused correct output would be no more coverage than
    one that cannot fail (AGENTS.md 10.4), so both verdicts are pinned
    on the same real span.
    """
    cuts = redundant_takes(REEL_03_START, REEL_03_END, REEL_03)
    assert cuts, "the span really does contain a removable repetition"
    assert_takes_are_whole(cuts, REEL_03_START, REEL_03_END, REEL_03)


def test_no_run_a_cut_touches_is_left_partly_standing():
    """The property the rule guarantees, checked directly.

    A reel opens on the first second its keep ranges retain.  Every run
    a cut touches is removed entirely, so the leading segment is either
    the span's own first segment or the first segment after a wholly
    removed run - never a tail whose opening was cut from in front of
    it.
    """
    cuts = redundant_takes(REEL_03_START, REEL_03_END, REEL_03)
    for run in redundant_runs(REEL_03_START, REEL_03_END, REEL_03):
        touched = [segment for segment in run.segments
                   if any(cut.dropped_start < segment["timeline_end"]
                          and cut.dropped_end > segment["timeline_start"]
                          for cut in cuts)]
        assert not touched or len(touched) == len(run.segments), run


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


def test_a_lone_pair_the_duration_test_refuses_is_not_a_withheld_cut():
    """Only a run where a cut was actually taken away is reported.

    Reels 07, 17 and 18 each have a pair the duration test refuses on
    its own, with no cut anywhere in the run.  Nothing is being withheld
    there and nothing changed for them, so calling those refusals would
    make the report describe a difference the rule does not make.
    """
    long_line = ("it is going to start hallucinating because it is confused "
                 "about what you actually do")
    transcript = {"segments": [
        _seg("Akshita", long_line, 10.0, 14.3, "a"),
        _seg("Akshita", "confused about what you actually do hallucinating",
             15.0, 15.5, "b"),
    ]}
    assert redundant_takes(0.0, 60.0, transcript) == []
    assert refused_take_groups(0.0, 60.0, transcript) == []


# ── The same defect away from the head of a reel ─────────────────────

REEL_16_SEGMENTS = [
    _seg("Akshita", "and that's a very specific query.", 2246.070, 2247.249,
         "r16"),
    _seg("Akshita", "Those queries don't work for Google,", 2247.351,
         2248.509, "r16"),
    _seg("Akshita", "but they work for AI.", 2248.570, 2249.344, "r16"),
    _seg("Akshita", "And because I typed that in,", 2249.971, 2251.188,
         "r16"),
    _seg("Akshita", "I got some,", 2251.833, 2252.366, "r16"),
    _seg("Akshita", "you know,", 2252.653, 2252.904, "r16"),
    _seg("Akshita", "I'm going to get companies recommended and I chose the "
                    "first one I saw.", 2253.191, 2255.809, "r16"),
    _seg("Akshita", "So I am using AI a lot.", 2256.051, 2258.350, "r16"),
    _seg("Akshita", "And I do think a lot of our audience will be as well.",
         2258.450, 2260.989, "r16"),
    _seg("Akshita", "And they're typing in very specific queries that don't "
                    "work for Google,", 2261.471, 2264.329, "r16"),
    _seg("Akshita", "but work for AI.", 2264.511, 2265.144, "r16"),
    _seg("Akshita", "So make sure that you're,", 2265.692, 2267.329, "r16"),
]

REEL_16 = {"segments": REEL_16_SEGMENTS}


def test_reel_16s_half_cut_take_is_the_same_defect_mid_reel():
    """Leading a reel is where the fragment is loudest, not the rule.

    Reel 16 says "Those queries don't work for Google, but they work for
    AI" twice.  The second half paired safely and the first half was
    refused at a ratio of 2.47, so the cut removed "but they work for
    AI" and left "Those queries don't work for Google," hanging in the
    middle of the reel.  The rule is about the CUT, not the position, so
    this one is withheld too.
    """
    cuts = redundant_takes(2217.710, 2287.089, REEL_16)
    dropped = sorted(round(cut.dropped_start, 3) for cut in cuts)
    assert 2248.570 not in dropped, cuts
    assert_takes_are_whole(cuts, 2217.710, 2287.089, REEL_16)

    groups = refused_take_groups(2217.710, 2287.089, REEL_16)
    assert len(groups) == 1, groups
    assert groups[0]["lines"] == ["Those queries don't work for Google,",
                                  "but they work for AI."]
