"""A keep insistence (seconds the captain says STAY IN) withdraws the take
cut that drops them, at the build, and round-trips through the store.

History: docs/evidence/reel_take_cuts.md.
"""

from types import SimpleNamespace

import pytest

from library.tools import reel_build, transcript_corrections
from library.tools.learned_context import LearnedContextError

#: Craig's two lines, worded and timed as the field test's own
#: transcript words and times them.  Enough of the run-up is here for
#: the word-stream scan to see what it saw on the real episode: with
#: less context it finds nothing and the test would pass on a defect
#: it never reproduced.
LEAD_IN = [
    ("their", 9.154, 9.414), ("cmo", 9.515, 10.077),
    ("or", 10.137, 10.237), ("their", 10.257, 10.378),
    ("head", 10.398, 10.518), ("of", 10.558, 10.638),
    ("marketing", 10.699, 11.040), ("hey", 11.060, 11.120),
]
CRAIG_WORDS = [
    ("we've", 11.241, 11.401), ("got", 11.421, 11.522),
    ("to", 11.542, 11.582), ("get", 11.622, 11.723),
    ("into", 11.763, 11.924), ("geo", 11.984, 12.245),
    ("geo", 12.285, 12.526), ("geo", 12.586, 12.888),
    ("i", 13.008, 13.109), ("get", 13.149, 13.350),
    ("it", 13.390, 13.450), ("it's", 13.912, 14.033),
    ("something", 14.053, 14.314), ("that's", 14.334, 14.495),
    ("going", 14.515, 14.656), ("to", 14.676, 14.736),
    ("continually", 14.796, 15.298), ("eat", 15.459, 15.580),
    ("into", 15.600, 15.680),
]


def _segment(words, item="clip-1"):
    first, last = words[0], words[-1]
    return {"timeline_start": first[1], "timeline_end": last[2],
            "start": first[1], "end": last[2], "speaker": "Craig",
            "bound": True,
            # BOUND: `reel_proposal.bound_segments` reads this, and a
            # segment without one is never used for boundary arithmetic.
            "resolve_item_id": item,
            "text": " ".join(w for w, _s, _e in words),
            "words": [{"word": w, "start": s, "end": e, "timed": True}
                      for w, s, e in words]}


def _transcript():
    return {"segments": [_segment(LEAD_IN), _segment(CRAIG_WORDS)]}


def _moment(start=9.0, end=16.0):
    return SimpleNamespace(timeline_start=start, timeline_end=end,
                           number=1, slug="x", timeline_name="Reel 01 - x",
                           call_to_action=None)


def _cut(a, b):
    return reel_build.Cut(
        dropped_start=a, dropped_end=b, dropped_text="got to get into",
        kept_start=12.29, kept_end=13.15, kept_text="geo geo geo",
        speaker="Craig", containment=0.667, jaccard=0.667)


# ── The withdrawal ─────────────────────────────────────────────────

def _an_insistence_withdraws_the_cut_that_drops_those_seconds():
    cuts = [_cut(11.42, 11.98)]
    kept, withdrawn = reel_build.withdraw_insisted_cuts(
        cuts, [(11.42, 11.98, "keep-1")])
    assert kept == []
    assert [ident for _c, ident in withdrawn] == ["keep-1"]


# ── Through the build's own ranges ─────────────────────────────────

def _spoken(ranges):
    """The words a set of keep ranges actually plays."""
    return [w for w, s, _e in LEAD_IN + CRAIG_WORDS
            if any(a - 1e-6 <= s < b for a, b in ranges)]


def test_the_reel_plays_the_words_again():
    """The defect and its close, measured in the words the reel says."""
    transcript, moment = _transcript(), _moment()
    # Reproduced: the scan calls the run before "geo geo geo" a retake
    # of the repetition itself at 11.42-11.98s, out of the middle of one
    # sentence.
    cut = reel_build.redundant_takes(9.0, 16.0, transcript)
    assert len(cut) == 1
    assert cut[0].dropped_start == pytest.approx(11.42)
    assert cut[0].jaccard == pytest.approx(0.667, abs=0.001)

    # The take judge withdraws that candidate before any captain record
    # is read: a mid-utterance excision is not a telling removed, so
    # the build plays the sentence whole with no insistence on file.
    judged = reel_build.reel_ranges(moment, transcript)
    assert judged == [(9.0, 16.0)]

    # With the insistence the sentence is whole again: ONE range, and
    # the cut is not made rather than made and patched. The insistence
    # still runs first - the judge never hides the captain's word
    # behind its own reason.
    whole = reel_build.reel_ranges(
        moment, transcript, insisted_spans=[(11.42, 11.98, "keep-1")])
    assert whole == [(9.0, 16.0)]
    restored = _spoken(whole)
    window = restored[restored.index("we've"):restored.index("geo")]
    assert window == ["we've", "got", "to", "get", "into"]
    _an_insistence_withdraws_the_cut_that_drops_those_seconds()


# ── The store ──────────────────────────────────────────────────────

def test_an_insistence_round_trips_through_the_store(tmp_path):
    transcript_corrections.record_keep_insistence(
        str(tmp_path), 11.42, 11.98,
        "captain, Reel 01 frame 270: the cut on Craig is jarring")
    read = transcript_corrections.keep_insistences(str(tmp_path))
    assert len(read) == 1
    assert read[0]["start"] == pytest.approx(11.42)
    assert read[0]["end"] == pytest.approx(11.98)
    assert "jarring" in read[0]["reason"]
    # It is a correction in the same store the strikes live in, and it
    # routes to the step that makes the cut.
    assert transcript_corrections.INSIST_READERS == ["build_reels"]


def test_a_malformed_insistence_refuses(tmp_path):
    for args, match in (((11.98, 11.42, "why"), "not a range"),
                        ((11.42, 11.98, "   "), "no reason")):
        with pytest.raises(LearnedContextError, match=match):
            transcript_corrections.record_keep_insistence(
                str(tmp_path), *args)
