"""Reel 13's repetitive Craig line, removed in the keep ranges.

The captain, 2026-09-12, on frame 189 of
`Reel 13 - the-accounting-firm-ai-called-healthcare`: *"craig's line at
this point is a bit repetitive with the line he says just before this,
can you fix this?"*

The pair, off the master transcript (real words, real seconds):

    889.92-893.02  Craig   "and that's what happened with that company
                            why they came back as a healthcare company"
    893.46-895.12  Akshita "And not an accounting software company, yes."
    895.47-899.40  Craig   "and that's one of the reasons why that company
                            got called a healthcare company"

This is a cutter MISS by design, not by accident: the two readings are
paraphrase across an interjection, marginal on every bar the cut lane
holds (containment 0.667/1.0 against 0.75, Jaccard 0.286/0.50 against
0.55, durations 1.625s against 0.8s at ratio 2.03 over 2.0). Loosening
any of those bars to catch it would also catch deliberate restatement -
Reel 30's green marker records the cutter dropping a repeated closing
phrase that was never a retake, and the fix there was WITHDRAWING the
cut. So the cutter still refuses this pair, the suspect lane still
flags it for the captain, and the captain's verdict travels as a
recorded keep exclusion: Craig's SECOND line goes
(895.471-899.400, the one AT the marker), the first stays.

And the Reel 30 seam rule holds here: an exclusion edge through a word
plays half a word and then jumps (`exclusion_midword_edges` refuses the
build). Both edges of this one sit exactly on timed word edges, so the
seam carries no partial word and no clipped breath - the cut starts on
"and"'s first frame (grown back over the wordless lead-in to "yes."'s
last) and ends on "company"'s last.
"""

from types import SimpleNamespace

from library.tools import reel_build
from library.tools import transcript_corrections as tc

START = 889.92
END = 900.0

#: The recorded strike: Craig's second line, word edge to word edge.
EXCLUDED = (895.471, 899.400)


def _words(text, start, ends):
    parts = text.split(" ")
    return [{"word": word, "start": start if i == 0 else ends[i - 1],
             "end": ends[i], "timed": True}
            for i, word in enumerate(parts)]


def _transcript():
    segments = [
        {"speaker": "Craig", "resolve_item_id": "clip-craig-1", "text":
         "and that's what happened with that company why",
         "timeline_start": 889.92, "timeline_end": 891.26,
         "words": _words(
             "and that's what happened with that company why", 889.92,
             [889.99, 890.20, 890.40, 890.60, 890.80, 891.00, 891.10,
              891.26])},
        {"speaker": "Craig", "resolve_item_id": "clip-craig-1", "text":
         "they came back as a healthcare company",
         "timeline_start": 891.40, "timeline_end": 893.025,
         "words": [{"word": "they", "start": 891.40, "end": 891.48,
                    "timed": True},
                   {"word": "came", "start": 891.50, "end": 891.66,
                    "timed": True},
                   {"word": "back", "start": 891.70, "end": 891.88,
                    "timed": True},
                   {"word": "as", "start": 891.94, "end": 892.04,
                    "timed": True},
                   {"word": "a", "start": 892.08, "end": 892.10,
                    "timed": True},
                   {"word": "healthcare", "start": 892.20, "end": 892.64,
                    "timed": True},
                   {"word": "company", "start": 892.66, "end": 893.025,
                    "timed": True}]},
        {"speaker": "Akshita", "resolve_item_id": "clip-akshita-1", "text":
         "And not an accounting software company, yes.",
         "timeline_start": 893.455, "timeline_end": 895.12,
         "words": [{"word": "And", "start": 893.455, "end": 893.515,
                    "timed": True},
                   {"word": "not", "start": 893.556, "end": 893.696,
                    "timed": True},
                   {"word": "an", "start": 893.736, "end": 893.796,
                    "timed": True},
                   {"word": "accounting", "start": 893.836, "end": 894.157,
                    "timed": True},
                   {"word": "software", "start": 894.197, "end": 894.558,
                    "timed": True},
                   {"word": "company,", "start": 894.598, "end": 894.939,
                    "timed": True},
                   {"word": "yes.", "start": 894.979, "end": 895.12,
                    "timed": True}]},
        {"speaker": "Craig", "resolve_item_id": "clip-craig-2", "text":
         "and that's one of the reasons why that company got called a",
         "timeline_start": 895.471, "timeline_end": 898.199,
         "words": [{"word": "and", "start": 895.471, "end": 895.552,
                    "timed": True},
                   {"word": "that's", "start": 895.572, "end": 895.792,
                    "timed": True},
                   {"word": "one", "start": 895.852, "end": 895.933,
                    "timed": True},
                   {"word": "of", "start": 895.953, "end": 895.993,
                    "timed": True},
                   {"word": "the", "start": 896.033, "end": 896.093,
                    "timed": True},
                   {"word": "reasons", "start": 896.153, "end": 896.454,
                    "timed": True},
                   {"word": "why", "start": 896.534, "end": 896.775,
                    "timed": True},
                   {"word": "that", "start": 896.955, "end": 897.196,
                    "timed": True},
                   {"word": "company", "start": 897.276, "end": 897.657,
                    "timed": True},
                   {"word": "got", "start": 897.677, "end": 897.858,
                    "timed": True},
                   {"word": "called", "start": 897.898, "end": 898.159,
                    "timed": True},
                   {"word": "a", "start": 898.179, "end": 898.199,
                    "timed": True}]},
        {"speaker": "Craig", "resolve_item_id": "clip-craig-2", "text": "healthcare company",
         "timeline_start": 898.60, "timeline_end": 899.40,
         "words": [{"word": "healthcare", "start": 898.60, "end": 899.00,
                    "timed": True},
                   {"word": "company", "start": 899.04, "end": 899.40,
                    "timed": True}]},
        {"speaker": "Akshita", "resolve_item_id": "clip-akshita-2", "text": "Yeah, so they had",
         "timeline_start": 899.57, "timeline_end": 900.40,
         "words": [{"word": "Yeah,", "start": 899.57, "end": 899.83,
                    "timed": True},
                   {"word": "so", "start": 899.85, "end": 899.97,
                    "timed": True},
                   {"word": "they", "start": 899.99, "end": 900.13,
                    "timed": True},
                   {"word": "had", "start": 900.17, "end": 900.37,
                    "timed": True}]},
    ]
    return {"segments": segments}


def test_the_cutter_refuses_the_pair_and_the_suspect_lane_flags_it():
    """The known-unknown, answered: a deliberate keep that reads badly.

    Neither bar the cut lane holds may take this pair, so no take cut
    fires here on any reel - and the near miss is a suspect, which is
    what put it in front of the captain rather than removing it.
    """
    transcript = _transcript()
    assert reel_build.redundant_takes(START, END, transcript) == []
    suspects = reel_build.suspected_takes(START, END, transcript)
    pairs = {(round(s.dropped_start, 2), round(s.kept_start, 2))
             for s in suspects}
    assert (889.92, 895.47) in pairs
    assert (891.40, 898.60) in pairs


def test_the_recorded_strike_cuts_exactly_the_second_line():
    """`exclusion_cuts_for_span` clips the strike to the span, and the
    wordless lead-in growth pulls its start back to "yes."'s last frame
    - so no sub-floor nub of room tone is stranded for F7 to refuse."""
    cuts = tc.exclusion_cuts_for_span(
        START, END, [{"start": EXCLUDED[0], "end": EXCLUDED[1],
                      "id": "reel13-craig-repeat"}])
    assert cuts == [(895.471, 899.40, "reel13-craig-repeat")]
    grown = tc.grow_cuts_over_wordless_leadin(cuts, _transcript())
    assert [(round(s, 3), round(e, 3)) for s, e, _ in grown] == [
        (895.120, 899.400)]


def test_the_reel_keeps_one_line_and_plays_no_half_word():
    """One reel in, one reel out, fewer seconds: the first line stays,
    the second goes, and every interior edge sits on a timed word edge.
    """
    transcript = _transcript()
    grown = tc.grow_cuts_over_wordless_leadin(
        tc.exclusion_cuts_for_span(
            START, END, [{"start": EXCLUDED[0], "end": EXCLUDED[1],
                          "id": "reel13-craig-repeat"}]),
        transcript)
    moment = SimpleNamespace(timeline_start=START, timeline_end=END)
    ranges = reel_build.reel_ranges(
        moment, transcript,
        extra_cuts=[(s, e) for s, e, _ in grown])
    assert ranges == [(START, 895.120), (899.400, END)]
    played = " ".join(
        s["text"] for s in transcript["segments"]
        if any(a < s["timeline_end"] and s["timeline_start"] < b
               for a, b in ranges))
    assert "they came back as a healthcare company" in played
    assert "one of the reasons why that company got called" not in played
    assert reel_build.exclusion_midword_edges(
        START, END, grown, transcript) == []
