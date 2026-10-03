"""A keep insistence (seconds the captain says STAY IN) withdraws the take
cut that drops them, at the build, and round-trips through the store.

History: docs/evidence/reel_take_cuts.md.
"""
from __future__ import annotations
from types import SimpleNamespace
import pytest
from library.tools import reel_build, transcript_corrections
from library.tools.learned_context import LearnedContextError
from library.tools import transcript_corrections as tc
import sys
from pathlib import Path
import json
from library.tools.reel_build import ReelBuildError, reel_ranges
from library.tools.reel_proposal import (
    Approval,
    ProposalError,
    ReelMoment,
    duplicate_takes,
    validate_proposal,
)


#: SpeakerTwo's two lines, worded and timed as the field test's own
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
SPEAKERTWO_WORDS = [
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
            "start": first[1], "end": last[2], "speaker": "SpeakerTwo",
            "bound": True,
            # BOUND: `reel_proposal.bound_segments` reads this, and a
            # segment without one is never used for boundary arithmetic.
            "resolve_item_id": item,
            "text": " ".join(w for w, _s, _e in words),
            "words": [{"word": w, "start": s, "end": e, "timed": True}
                      for w, s, e in words]}


def _transcript():
    return {"segments": [_segment(LEAD_IN), _segment(SPEAKERTWO_WORDS)]}


def _moment(start=9.0, end=16.0):
    return SimpleNamespace(timeline_start=start, timeline_end=end,
                           number=1, slug="x", timeline_name="Reel 01 - x",
                           call_to_action=None)


def _cut(a, b):
    return reel_build.Cut(
        dropped_start=a, dropped_end=b, dropped_text="got to get into",
        kept_start=12.29, kept_end=13.15, kept_text="geo geo geo",
        speaker="SpeakerTwo", containment=0.667, jaccard=0.667)


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
    return [w for w, s, _e in LEAD_IN + SPEAKERTWO_WORDS
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
        "captain, Reel 01 frame 270: the cut on SpeakerTwo is jarring")
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


# --------------------------------------------------------------------------
# From test_keep_exclusion_reel13_repeat.py
#
# Reel 13's repeated SpeakerTwo line goes as a recorded keep exclusion: the
# cutter refuses the pair, the strike grows over wordless edges past the
# angle switch, and no edge lands mid-word.
#
# History: docs/evidence/reel_take_cuts.md.

START = 889.92
END = 900.0

#: The recorded strike: SpeakerTwo's second line, word edge to word edge.
EXCLUDED = (895.471, 899.400)


def _words(text, start, ends):
    parts = text.split(" ")
    return [{"word": word, "start": start if i == 0 else ends[i - 1],
             "end": ends[i], "timed": True}
            for i, word in enumerate(parts)]


def _transcript_2():
    segments = [
        {"speaker": "SpeakerTwo", "resolve_item_id": "clip-speakertwo-1", "text":
         "and that's what happened with that company why",
         "timeline_start": 889.92, "timeline_end": 891.26,
         "words": _words(
             "and that's what happened with that company why", 889.92,
             [889.99, 890.20, 890.40, 890.60, 890.80, 891.00, 891.10,
              891.26])},
        {"speaker": "SpeakerTwo", "resolve_item_id": "clip-speakertwo-1", "text":
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
        {"speaker": "SpeakerOne", "resolve_item_id": "clip-speakerone-1", "text":
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
        {"speaker": "SpeakerTwo", "resolve_item_id": "clip-speakertwo-2", "text":
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
        {"speaker": "SpeakerTwo", "resolve_item_id": "clip-speakertwo-2", "text": "healthcare company",
         "timeline_start": 898.60, "timeline_end": 899.40,
         "words": [{"word": "healthcare", "start": 898.60, "end": 899.00,
                    "timed": True},
                   {"word": "company", "start": 899.04, "end": 899.40,
                    "timed": True}]},
        {"speaker": "SpeakerOne", "resolve_item_id": "clip-speakerone-2", "text": "Yeah, so they had",
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
    transcript = _transcript_2()
    assert reel_build.redundant_takes(START, END, transcript) == []
    suspects = reel_build.suspected_takes(START, END, transcript)
    pairs = {(round(s.dropped_start, 2), round(s.kept_start, 2))
             for s in suspects}
    assert (889.92, 895.47) in pairs
    assert (891.40, 898.60) in pairs


# ── The tail edge: what the first beside build refused ────────────────
#
# The strike is recorded on a real word edge and STILL stranded a nub,
# because the nub forms at whichever edge the master's angle switch
# falls on the wrong side of. Measured 2026-09-12 building Reel 13
# beside the captain's, against the live `Podcast (field test)`.

#: Where `GEO Podcast - Synced` switches back to SpeakerOne's camera after
#: SpeakerTwo's second telling. Read off the master: V2 SpeakerTwo runs
#: 895.311..899.482 and V1 SpeakerOne resumes at 899.482.
ANGLE_SWITCH_BACK = 899.482


def _grown_cuts(transcript):
    """The strike as the build applies it: both edges grown."""
    cuts = tc.exclusion_cuts_for_span(
        START, END, [{"start": EXCLUDED[0], "end": EXCLUDED[1],
                      "id": "reel13-speakertwo-repeat"}])
    head = tc.grow_cuts_over_wordless_leadin(cuts, transcript)
    return tc.grow_cuts_over_wordless_tail(head, transcript)


def test_the_strike_grows_over_wordless_edges_and_clears_the_switch():
    """The recorded strike is clipped to the span and grown both ways:
    back to "yes."'s last frame (no sub-floor room-tone nub for F7), and
    forward over the 170ms between SpeakerTwo's "company" (899.400) and
    SpeakerOne's "Yeah," (899.570), stopping at her word.

    THE FAILING INPUT: resuming at the recorded 899.400 admitted 82ms of
    SpeakerTwo's camera (the master switches back at 899.482), which the build
    gate refused as a 2-frame item under the readability floor. Grown,
    the cut covers the switch and no sliver of SpeakerTwo's is placed."""
    transcript = _transcript_2()
    head_only = tc.grow_cuts_over_wordless_leadin(
        tc.exclusion_cuts_for_span(
            START, END, [{"start": EXCLUDED[0], "end": EXCLUDED[1],
                          "id": "reel13-speakertwo-repeat"}]),
        transcript)
    assert head_only[0][1] < ANGLE_SWITCH_BACK, (
        "the head-only growth is what stranded the flash")
    grown, held = _grown_cuts(transcript)
    assert [(round(s, 3), round(e, 3)) for s, e, _ in grown] == [
        (895.120, 899.570)]
    assert held == []
    fps = 24000 / 1001
    stranded = round((ANGLE_SWITCH_BACK - grown[0][1]) * fps)
    assert stranded <= 0, f"{stranded} frame(s) of the wrong camera left"


def test_the_reel_still_keeps_one_line_after_the_tail_grows():
    """The repeat is still gone and no edge sits mid-word - the tail
    growth moves silence only."""
    transcript = _transcript_2()
    # The fixture stops at 900.0; the real moment runs to 956.64, so the
    # tail here is extended to give the second range something to be
    # other than a sub-floor fragment of its own.
    transcript["segments"].append(
        {"speaker": "SpeakerOne", "resolve_item_id": "clip-speakerone-2",
         "text": "a case study on their site",
         "timeline_start": 900.49, "timeline_end": 903.00,
         "words": [{"word": "a", "start": 900.49, "end": 900.55,
                    "timed": True},
                   {"word": "case", "start": 900.63, "end": 900.82,
                    "timed": True},
                   {"word": "study", "start": 900.84, "end": 901.30,
                    "timed": True},
                   {"word": "on", "start": 901.90, "end": 901.96,
                    "timed": True},
                   {"word": "their", "start": 901.98, "end": 902.12,
                    "timed": True},
                   {"word": "site", "start": 902.30, "end": 903.00,
                    "timed": True}]})
    moment_end = 903.00
    grown, _held = _grown_cuts(transcript)
    moment = SimpleNamespace(timeline_start=START, timeline_end=moment_end)
    ranges = reel_build.reel_ranges(
        moment, transcript, extra_cuts=[(s, e) for s, e, _ in grown])
    assert ranges == [(START, 895.120), (899.570, moment_end)]
    played = " ".join(
        s["text"] for s in transcript["segments"]
        if any(a < s["timeline_end"] and s["timeline_start"] < b
               for a, b in ranges))
    assert "they came back as a healthcare company" in played
    assert "one of the reasons why that company got called" not in played
    assert "Yeah, so they had" in played
    assert reel_build.exclusion_midword_edges(
        START, moment_end, grown, transcript) == []


# --------------------------------------------------------------------------
# From test_exclusions_reach_approved_builds.py
#
# A recorded keep exclusion cuts an APPROVED moment's build ranges - one
# reel in, one reel out, fewer seconds.
#
# History: docs/evidence/reel_take_cuts.md.

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT))


def _seg(speaker, text, start, end, uid="u", words=()):
    seg = {"speaker": speaker, "text": text, "timeline_start": start,
           "timeline_end": end, "source_file": "/m/a.MXF",
           "source_start": start, "source_end": end,
           "resolve_item_id": uid}
    if words:
        seg["words"] = [
            {"word": w, "start": s, "end": e, "timed": True}
            for w, s, e in words]
    return seg


def _tx(*segments):
    return {"segments": list(segments)}


def _moment_2(start, end, number=9):
    from library.tools.reel_proposal import ReelMoment
    return ReelMoment(
        number=number, slug="your-website-is-only-20-percent",
        reason="a complete exchange",
        timeline_start=start, timeline_end=end)


def _false_start_tx():
    """SpeakerTwo's 653.421-656.760s segment in miniature: the false start
    'so what do they' (10.0-10.9s here) the captain struck, then the
    kept 'so what else ...' from 11.8s on."""
    return _tx(
        _seg("SpeakerOne", "very important to be aware.", 5.0, 9.0, "u0"),
        _seg("SpeakerTwo", "so what do they so what else do they need",
             10.0, 15.0, "u1",
             words=[("so", 10.0, 10.2), ("what", 10.25, 10.45),
                    ("do", 10.5, 10.65), ("they", 10.7, 10.9),
                    ("so", 11.8, 12.0), ("what", 12.05, 12.25),
                    ("else", 12.3, 12.5)]),
        _seg("SpeakerTwo", "where is ai pulling all this from", 16.0, 19.0,
             "u2"),
    )


# ── The store half: overlaps clipped to the span ─────────────────────


# ── The build half: one reel in, one reel out, fewer seconds ─────────

def test_a_mid_moment_strike_cuts_the_builds_ranges_not_the_reel():
    """The bug, by demonstration: an interior strike used to be
    unreachable at build time (selection drops such moments, and the
    build never read the store), so approved Reel 09 kept playing the
    struck false start. Now it cuts the ranges - the reel stays ONE
    reel with a hole where the struck seconds were - and the built
    timeline's OWN speech (`played_speech` over the cut ranges) carries
    the kept 'so what else' and none of the struck 'so what do they'."""
    from library.tools.reel_build import reel_ranges
    from library.tools.reel_quality_bar import played_speech
    tx = _false_start_tx()
    moment = _moment_2(0.0, 40.0)
    strike = [(10.0, 10.9, "lc-0002")]
    assert reel_ranges(moment, tx, extra_cuts=strike) == [
        (0.0, 10.0), (10.9, 40.0)]
    before = [w["word"] for line in played_speech(moment, tx, with_words=True)
              for w in line.get("words", [])]
    assert before.count("so") == 2  # false start AND kept opening
    after = [w["word"] for line in played_speech(
        moment, tx, with_words=True, extra_cuts=strike)
        for w in line.get("words", [])]
    assert "else" in after  # the kept continuation still plays
    assert after.count("so") == 1  # only the kept one


# --------------------------------------------------------------------------
# From test_tail_extend_authorization.py
#
# The captain's standing yes to one reported tail extension.
#
# The carve-out (`reel_build._analyze_tail_edge` returning `report`
# where a tail runs longer than the closing thought) is deliberate:
# silently lengthening an accepted reel was the failure mode to avoid.
# This file tests the narrow answer the captain has since given for one
# reel - the ruling travels as data, applies to its own reel only, still
# reports everywhere else, and refuses where the seconds moved since the
# ruling was made.

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from library.tools.tail_extend_authorization import (
    AuthorizationError,
    authorized_for,
    check_applied,
    load_authorizations,
)


def _write(path: Path, payload) -> str:
    project = path / "project"
    (project / "external").mkdir(parents=True)
    (project / "external" / "tail_extend_authorizations.json").write_text(
        json.dumps(payload), encoding="utf-8")
    return str(project)


def _entry(**overrides):
    base = {"reel": 28,
            "reason": "captain 2026-09-21: extend it",
            "measured_edge": 2265.6,
            "measured_tail_end": 2287.14,
            "measured_gap": 0.02}
    base.update(overrides)
    return base


def test_a_reel_answers_only_its_own_entry(tmp_path):
    assert load_authorizations(str(tmp_path / "absent")) == {}
    project = _write(tmp_path, {"authorizations": [_entry()]})
    auths = load_authorizations(project)
    assert authorized_for(auths, 28)["reason"].startswith("captain")
    assert authorized_for(auths, 10) == {}
    assert authorized_for(auths, "28")["reel"] == 28


def test_a_malformed_ruling_file_refuses(tmp_path):
    """No reason, no measured seconds, a double ruling on one reel, and
    a file that is not JSON each refuse."""
    no_seconds = _entry()
    del no_seconds["measured_tail_end"]
    payloads = [
        {"authorizations": [_entry(reason=" ")]},
        {"authorizations": [no_seconds]},
        {"authorizations": [_entry(), _entry()]},
        "{not json",
    ]
    for i, payload in enumerate(payloads):
        project = tmp_path / f"p{i}" / "project"
        (project / "external").mkdir(parents=True)
        (project / "external" / "tail_extend_authorizations.json").write_text(
            payload if isinstance(payload, str) else json.dumps(payload),
            encoding="utf-8")
        with pytest.raises(AuthorizationError):
            load_authorizations(str(project))


def test_the_applied_seconds_are_weighed_against_the_ruling():
    entry = _entry()
    check_applied(28, entry, 2287.14, 0.02)
    check_applied(28, entry, 2287.5, 0.02)
    with pytest.raises(AuthorizationError):
        check_applied(28, entry, 2300.0, 0.02)
    with pytest.raises(AuthorizationError):
        check_applied(28, entry, 2200.0, 0.02)


# --------------------------------------------------------------------------
# From test_stranded_tail.py
#
# A kept range that stops before its thought finishes is extended, held,
# reported or refused - never silently shipped (`repair_moment_tail`,
# `stranded_tail_keep_edges`). Field-test numbers travel as data.
#
# History: docs/evidence/reel_boundary_snap.md.

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from library.tools.reel_build import (
    record_tail_repairs,
    repair_moment_tail,
    stranded_tail_keep_edges,
)
from library.tools.reel_proposal import (
    decision_lines,
    snap_moment_to_speech,
)

FPS = 24000 / 1001  # 23.976 exact - the reels' own frame rate


def _w(word, start, end):
    return {"word": word, "start": start, "end": end, "timed": True}


def _seg_2(speaker, text, start, end, uid="u", words=()):
    return {"speaker": speaker, "text": text, "timeline_start": start,
            "timeline_end": end, "source_file": "/m/a.MXF",
            "source_start": start, "source_end": end,
            "resolve_item_id": uid, "words": list(words)}


def _moment_3(number, start, end):
    return ReelMoment(number=number, slug=f"reel-{number:02d}",
                      reason="a complete exchange",
                      timeline_start=start, timeline_end=end,
                      approval=Approval.APPROVED)


# ------------------------------------------------- Reel 09, verbatim
#
# SpeakerOne's closing segment, 682.19-693.38s, and SpeakerTwo's following
# turn opening at 694.1s. The approved body end is 693.3s - inside
# the final word "misrecommended." (692.50-693.38s) with 0.08s of it
# uncovered, and 0.8s before SpeakerTwo's next speech.


def _reel9_transcript():
    return _tx(
        _seg_2("SpeakerOne",
             "And so when AI reads all of these sources, that's how it's "
             "building a big picture of your company and your brand and "
             "you have to be aware of what it's saying or you can be "
             "completely re like misrecommended.",
             682.19, 693.38, "u-speakerone",
             words=[
                 _w("And", 682.19, 682.71),
                 _w("so", 682.71, 683.08),
                 _w("when", 683.14, 683.28),
                 _w("AI", 683.28, 683.46),
                 _w("reads", 683.46, 683.74),
                 _w("all", 683.74, 683.93),
                 _w("of", 683.93, 684.04),
                 _w("these", 684.04, 684.21),
                 _w("sources,", 684.21, 684.78),
                 _w("that's", 685.04, 685.29),
                 _w("how", 685.29, 685.43),
                 _w("it's", 685.43, 685.54),
                 _w("building", 685.54, 685.87),
                 _w("a", 685.87, 685.92),
                 _w("big", 685.92, 686.19),
                 _w("picture", 686.19, 686.71),
                 _w("of", 687.15, 687.3),
                 _w("your", 687.3, 687.42),
                 _w("company", 687.42, 687.85),
                 _w("and", 687.85, 688.02),
                 _w("your", 688.02, 688.16),
                 _w("brand", 688.16, 688.57),
                 _w("and", 688.63, 689.04),
                 _w("you", 689.04, 689.28),
                 _w("have", 689.38, 689.63),
                 _w("to", 689.63, 689.72),
                 _w("be", 689.72, 689.87),
                 _w("aware", 689.87, 690.21),
                 _w("of", 690.21, 690.34),
                 _w("what", 690.34, 690.45),
                 _w("it's", 690.45, 690.61),
                 _w("saying", 690.61, 690.98),
                 _w("or", 691.04, 691.11),
                 _w("you", 691.11, 691.21),
                 _w("can", 691.21, 691.36),
                 _w("be", 691.36, 691.46),
                 _w("completely", 691.46, 692.0),
                 _w("re", 692.0, 692.18),
                 _w("like", 692.26, 692.5),
                 _w("misrecommended.", 692.5, 693.38),
             ]),
        _seg_2("SpeakerTwo", "Well, I kind of tell people when I talk.",
             694.1, 698.0, "u-speakertwo",
             words=[
                 _w("Well,", 694.1, 694.29),
                 _w("I", 694.29, 694.38),
                 _w("kind", 694.38, 694.59),
                 _w("of", 694.59, 694.67),
                 _w("tell", 694.67, 694.87),
                 _w("people", 694.87, 695.09),
             ]),
    )


def _an_approved_bound_covering_all_but_breath_holds():
    """Reel 09's shape: the approved end sits 0.08s inside the final
    word - inside the speaker's 0.18s pace - with the next speech
    0.8s away. The repair holds it: drift since approval does not
    move approved spans."""
    new_end, finding = repair_moment_tail(
        693.3, 631.115, _reel9_transcript())
    assert new_end == 693.3
    assert finding["verdict"] == "hold"
    assert finding["word"] == "misrecommended."
    assert finding["remainder"] == pytest.approx(0.08)
    assert finding["pace"] == pytest.approx(0.18)


def test_an_edge_before_the_final_word_extends_to_it():
    """The defect's shape: an end 0.02s before "misrecommended."
    starts strands the whole word, so the repair extends to the
    sentence end - which is the word's end here."""
    new_end, finding = repair_moment_tail(
        692.48, 682.0, _reel9_transcript())
    assert new_end == 693.38
    assert finding["verdict"] == "extend"
    assert finding["kind"] == "word-tail"
    assert finding["tail_words"] == ["misrecommended."]


def test_the_held_bound_places_36510():
    """The acceptance, in frames: the held body end maps to source
    frame 36510 on the transcript's own clip alignment - not the
    live defect's 36490, and one frame under the snapped word end. The
    only body_end move is the tail-hold, never a snap rewrite."""
    repaired, moves = snap_moment_to_speech(
        _moment_3(9, 631.115, 693.3), _reel9_transcript())
    assert repaired.timeline_end == 693.3
    body_ends = [m for m in moves if m["boundary"] == "body_end"]
    assert [(m["attribution"], m["now"]) for m in body_ends] == [
        ("tail-hold", 693.3)]
    ranges = reel_ranges(repaired, _reel9_transcript())
    assert ranges == [(repaired.timeline_start, 693.3)]
    clip = SimpleNamespace(
        timeline_start=682.19, timeline_end=693.38,
        source_in=1511.64, source_file="LC4932.MXF",
        track_index=0, speaker="SpeakerOne", track_type="video")
    placed = reel_build.placements(ranges, [clip], FPS)
    assert round(placed[-1]["source_out"] * FPS) == 36510
    _an_approved_bound_covering_all_but_breath_holds()


# ------------------------------------------------- Reel 04, verbatim
#
# The body ends at 285.42s, exactly on "why." - and the sentence that
# states the reel's point starts 0.20s later, inside the 0.29s pace.


def _reel4_transcript():
    return _tx(
        _seg_2("SpeakerOne",
             "For Google search, it gave out a list from 2023, and for "
             "ChatGPT, it gave three recommendations with specific "
             "reasons why.",
             278.12, 285.42, "u-a",
             words=[
                 _w("For", 278.12, 278.41),
                 _w("Google", 278.41, 278.68),
                 _w("search,", 278.68, 279.16),
                 _w("it", 279.21, 279.29),
                 _w("gave", 279.29, 279.56),
                 _w("out", 279.56, 279.82),
                 _w("a", 279.82, 279.88),
                 _w("list", 279.88, 280.15),
                 _w("from", 280.15, 280.27),
                 _w("2023,", 280.27, 281.14),
                 _w("and", 281.14, 281.33),
                 _w("for", 281.33, 281.44),
                 _w("ChatGPT,", 281.44, 282.31),
                 _w("it", 282.48, 282.62),
                 _w("gave", 282.62, 282.99),
                 _w("three", 282.99, 283.13),
                 _w("recommendations", 283.13, 284.02),
                 _w("with", 284.02, 284.12),
                 _w("specific", 284.12, 284.55),
                 _w("reasons", 284.55, 285.03),
                 _w("why.", 285.03, 285.42),
             ]),
        _seg_2("SpeakerOne",
             "So one, which is Google, is a search engine, and the "
             "other, ChatGPT, is a decision engine.",
             285.62, 290.23, "u-b",
             words=[
                 _w("So", 285.62, 285.96),
                 _w("one,", 285.96, 286.49),
                 _w("which", 286.55, 286.73),
                 _w("is", 286.73, 286.83),
                 _w("Google,", 286.83, 287.18),
                 _w("is", 287.18, 287.33),
                 _w("a", 287.33, 287.39),
                 _w("search", 287.39, 287.72),
                 _w("engine,", 287.72, 288.07),
                 _w("and", 288.07, 288.28),
                 _w("the", 288.28, 288.38),
                 _w("other,", 288.38, 288.63),
                 _w("ChatGPT,", 288.63, 289.29),
                 _w("is", 289.29, 289.37),
                 _w("a", 289.37, 289.42),
                 _w("decision", 289.42, 289.85),
                 _w("engine.", 289.85, 290.23),
             ]),
    )


def test_a_body_that_ends_before_its_point_extends_to_the_sentence():
    """Reel 04's shape: the 0.20s gap to "So" sits inside the 0.29s
    pace, so the body extends to the sentence end at 290.23s, carrying
    the "decision engine" punchline as one clean keep range. Over the
    two-second bar the extension shouts NEEDS DECISION and points at
    the recorded WHY."""
    new_end, finding = repair_moment_tail(
        285.42, 267.358, _reel4_transcript())
    assert new_end == 290.23
    assert finding["verdict"] == "extend"
    assert finding["kind"] == "sentence-tail"
    assert finding["tail_words"][-2:] == ["decision", "engine."]
    assert finding["gap"] == pytest.approx(0.20)
    assert finding["pace"] == pytest.approx(0.27)
    repaired, moves = snap_moment_to_speech(
        _moment_3(4, 267.358, 285.42), _reel4_transcript())
    assert repaired.timeline_end == 290.23
    assert moves[0]["attribution"] == "tail-extend"
    assert reel_ranges(repaired, _reel4_transcript()) == [
        (267.358, 290.23)]
    assert reel_build.midword_keep_edges(
        repaired.timeline_start, repaired.timeline_end,
        _reel4_transcript()) == []
    lines = decision_lines(4, moves[0], _reel4_transcript(), "")
    assert any("NEEDS DECISION" in line for line in lines)
    assert any("decision" in line and "engine" in line for line in lines)
    assert any("moment_boundary_repairs" in line for line in lines)
    assert not any("no pin kind" in line for line in lines)


# ------------------------------------------------- the family rules


def test_a_turn_boundary_or_straddling_word_is_left_alone():
    """Turn boundaries are legitimate ends: the same words that
    extend a same-speaker thought stay untouched when another
    speaker takes over - reaching into their turn is selection's
    decision (redraw the span), never an automatic extension."""
    tx = _tx(
        _seg_2("SpeakerOne", "the setup is done.", 10.0, 20.0, "u1",
             words=[_w("the", 10.0, 10.3),
                    _w("setup", 10.4, 10.7),
                    _w("is", 10.8, 10.9),
                    _w("done.", 11.0, 11.5)]),
        _seg_2("SpeakerTwo", "and here is why it matters.", 11.6, 20.0, "u2",
             words=[_w("and", 11.6, 11.9),
                    _w("here", 12.0, 12.3),
                    _w("is", 12.4, 12.5),
                    _w("why", 12.6, 12.9),
                    _w("it", 13.0, 13.1),
                    _w("matters.", 13.2, 13.7)]),
    )
    new_end, finding = repair_moment_tail(11.5, 10.0, tx)
    assert (new_end, finding) == (11.5, None)
    _a_straddling_word_interior_is_never_held()


def test_take_cuts_are_not_stranded_tails():
    """The cutter's own edges are clean: words a take cut drops are
    removed on purpose, so the same plan that refuses a mid-word
    edge passes a take boundary on word edges untouched."""
    tx = _tx(
        _seg_2("SpeakerOne", "Absolutely.", 100.0, 101.0, "u1",
             words=[_w("Absolutely.", 100.0, 100.70)]),
        _seg_2("SpeakerOne", "is your resume, hiring managers check both",
             104.42, 109.0, "u2",
             words=[_w("is", 104.50, 104.65),
                    _w("both", 108.60, 109.00)]),
        _seg_2("SpeakerOne", "is your resume, hiring managers check both places",
             112.0, 117.0, "u3",
             words=[_w("places", 116.60, 117.00)]),
        _seg_2("SpeakerOne", "and that is the whole point", 118.0, 121.0, "u4",
             words=[_w("point", 120.6, 121.0)]),
    )
    assert stranded_tail_keep_edges(100.0, 121.0, tx) == []


def test_a_tail_across_a_dropped_take_abstains_loudly():
    """Extending over a take the cutter removed would reinstate it:
    the predicate reports the crossing and applies nothing, so a
    human redraws the span."""
    tx = _tx(
        _seg_2("Host", "first telling of the line here.", 10.0, 20.0,
             "u1", words=[_w("first", 10.0, 10.3),
                          _w("telling", 10.4, 10.7),
                          _w("of", 10.8, 10.9),
                          _w("the", 11.0, 11.1),
                          _w("line", 11.2, 11.5),
                          _w("here.", 11.6, 12.0)]),
    )
    cuts = [reel_build.Cut(
        dropped_start=10.35, dropped_end=10.75, dropped_text="telling of",
        kept_start=30.0, kept_end=30.4, kept_text="telling of",
        speaker="Host", containment=1.0, jaccard=1.0)]
    new_end, finding = repair_moment_tail(
        10.3, 10.0, tx, take_cuts=cuts)
    assert new_end == 10.3
    assert finding["verdict"] == "abstain"
    assert "crosses a dropped take" in finding["reason"]


def _a_straddling_word_interior_is_never_held():
    """No boundary is ever placed on a straddling row: an end inside
    one belongs to the snap as before, however small the remainder."""
    tx = _tx(
        _seg_2("SpeakerOne", "recommend you.", 412.63, 413.85, "u-a",
             words=[_w("recommend", 412.63, 412.99),
                    _w("you", 413.01, 413.17),
                    _w("yours", 413.59, 413.85)]),
        _seg_2("SpeakerTwo", "about", 412.77, 414.03, None,
             words=[_w("about", 412.77, 414.03)]),
    )
    new_end, finding = repair_moment_tail(413.851, 412.63, tx)
    assert (new_end, finding) == (413.851, None)


def test_a_correct_reel_comes_out_byte_identical():
    """The neighbour proof: a moment that ends on a segment edge with
    the next speech a proper distance away passes through untouched -
    the same object, no moves, so twenty-eight correct reels keep
    exactly what they had. (An end mid-segment still widens to the
    segment edge exactly as before - the tail pass finds nothing to
    judge there and the snap owns it.)"""
    tx = _tx(
        _seg_2("Host", "the point is made.", 10.0, 20.0, "u1",
             words=[_w("the", 10.0, 10.3),
                    _w("point", 10.4, 10.7),
                    _w("is", 10.8, 10.9),
                    _w("made.", 11.0, 11.5)]),
        _seg_2("Host", "a later thought.", 60.0, 70.0, "u2",
             words=[_w("later", 60.0, 60.4),
                    _w("thought.", 60.5, 61.0)]),
    )
    moment = _moment_3(1, 10.0, 20.0)
    repaired, moves = snap_moment_to_speech(moment, tx)
    assert repaired is moment
    assert moves == []
    widened, widen_moves = snap_moment_to_speech(_moment_3(1, 10.0, 11.5),
                                                 tx)
    assert (widened.timeline_start, widened.timeline_end) == (10.0, 20.0)
    assert [m["boundary"] for m in widen_moves] == ["body_end"]
    assert "attribution" not in widen_moves[0]


# ------------------------------------------------- the ledger


def test_tail_repairs_land_on_the_ledger_idempotently(tmp_path):
    """Repairs are recorded and reversible: each lands on
    moment_boundary_repairs.json in that file's own shape, a rebuild
    rewrites the same entry instead of duplicating it, and entries
    the repair did not write are never touched."""
    project = tmp_path / "project"
    review = project / "pipeline_output" / "review"
    review.mkdir(parents=True)
    ledger_file = review / "moment_boundary_repairs.json"
    ledger_file.write_text(json.dumps({
        "key": "moment_boundary_repairs",
        "source": "firstmate 2026-09-18",
        "value": [{"kind": "moment_boundary_repair", "reel": 2,
                   "boundary": "body_end", "was": 1.0, "now": 2.0,
                   "attribution": "snap", "reason": "older repair",
                   "source": "firstmate 2026-09-18"}],
    }), encoding="utf-8")
    repairs = [
        (9, {"boundary": "body_end", "was": 693.3, "now": 693.3,
             "attribution": "tail-hold", "why": "approved bound stands"}),
        (4, {"boundary": "body_end", "was": 285.42, "now": 290.23,
             "attribution": "tail-extend", "why": "finishes the tale"}),
    ]
    first = record_tail_repairs(str(project), repairs)
    assert first == str(ledger_file)
    written = json.loads(ledger_file.read_text(encoding="utf-8"))
    assert len(written["value"]) == 3
    by_reel = {entry["reel"]: entry for entry in written["value"]}
    assert by_reel[2]["reason"] == "older repair"
    assert by_reel[9]["attribution"] == "tail-hold"
    assert by_reel[4]["now"] == 290.23
    record_tail_repairs(str(project), repairs)
    again = json.loads(ledger_file.read_text(encoding="utf-8"))
    assert len(again["value"]) == 3
    _an_authorised_extension_answers_the_recorded_report(tmp_path / "second")


# ------------------------------------------------- the caption side


def test_a_held_bound_clips_its_caption_block_and_says_so():
    """The neighbour the hold would otherwise break: a range end
    inside the final word must not silently uncaption the block -
    the spine clips it to the range and reports the cut."""
    from library.tools.reel_spine import spine_for_reel

    tx = _reel9_transcript()
    moment = _moment_3(9, 631.115, 693.3)
    spine = spine_for_reel(moment, tx, [(631.115, 693.3)])
    assert len(spine["structure"]) == 1
    assert spine["structure"][-1]["timeline_end"] == pytest.approx(
        693.3 - 631.115)
    clipped = spine["range_clipped_blocks"]
    assert len(clipped) == 1
    assert clipped[0]["master_end"] == pytest.approx(693.38)
    assert clipped[0]["clipped_to"] == pytest.approx(693.3)
    words = [entry["word"] for entry in
             spine["structure"][-1]["word_timestamps"]]
    assert words[-1] == "misrecommended."


def test_an_abstention_is_loud_in_the_preview_and_the_build():
    """Where no bound segment reaches the edge, the transcript-wide
    fallback judges distance but never content: far speech is clean,
    nearby speech abstains, and the abstention is held for a human on
    both surfaces - HELD FOR DECISION in the preview (flagged, never
    moved) and NEEDS DECISION in the build's lines."""
    from library.tools.reel_proposal import preview_snap, render_snap_preview

    tx = _tx(
        _seg_2("Host", "a later thought.", 60.0, 70.0, "u1",
             words=[_w("later", 60.0, 60.4),
                    _w("thought.", 60.5, 61.0)]),
    )
    assert repair_moment_tail(50.0, 40.0, tx) == (50.0, None)
    near_end, near_finding = repair_moment_tail(59.9, 40.0, tx)
    assert near_end == 59.9
    assert near_finding["verdict"] == "abstain"
    assert near_finding["inferred"] is False
    report = preview_snap([_moment_3(1, 40.0, 59.9)], tx)
    assert report["moved"] == 0
    assert report["flagged"] == 1
    text = render_snap_preview(report)
    assert "HELD FOR DECISION" in text
    move = report["moments"][0]["moves"][0]
    assert move["abstained"] is True
    lines = decision_lines(1, move, tx, "")
    assert any("NEEDS DECISION" in line for line in lines)
    assert any("redraw" in line for line in lines)


def _reel28_transcript():
    """Reel 28's shape, load-bearing numbers verbatim: the closing
    thought runs 2256.08-2265.42s (9.34s, pace 0.21s) and the
    same-voice sentence after the approved bound runs
    2265.62-2287.14s (21.54s added). The middle run's interior words
    are abbreviated - only its span enters the verdict, and the span
    (text and times) is verbatim."""
    return _tx(
        _seg_2("SpeakerOne",
             "So I am using AI a lot and I do think a lot of our "
             "audience will be as well and they're typing in very "
             "specific queries that don't work for Google but work "
             "for AI.",
             2256.08, 2265.42, "u-a",
             words=[
                 _w("So", 2256.08, 2256.53),
                 _w("I", 2257.1, 2257.27),
                 _w("am", 2257.27, 2257.49),
                 _w("using", 2257.49, 2257.81),
                 _w("AI", 2257.81, 2258.0),
                 _w("a", 2258.0, 2258.09),
                 _w("lot", 2258.09, 2258.4),
                 _w("and", 2258.4, 2258.53),
                 _w("I", 2258.53, 2258.6),
                 _w("do", 2258.6, 2258.83),
                 _w("think", 2258.83, 2259.14),
                 _w("a", 2259.26, 2259.32),
                 _w("lot", 2259.32, 2259.64),
                 _w("of", 2259.64, 2259.75),
                 _w("our", 2259.75, 2259.9),
                 _w("audience", 2259.9, 2260.3),
                 _w("will", 2260.3, 2260.4),
                 _w("be", 2260.4, 2260.61),
                 _w("as", 2260.61, 2260.8),
                 _w("well", 2260.8, 2261.14),
                 _w("and", 2261.4, 2261.64),
                 _w("they're", 2261.64, 2261.73),
                 _w("typing", 2261.73, 2262.1),
                 _w("in", 2262.1, 2262.26),
                 _w("very", 2262.26, 2262.5),
                 _w("specific", 2262.5, 2263.06),
                 _w("queries", 2263.06, 2263.43),
                 _w("that", 2263.43, 2263.59),
                 _w("don't", 2263.59, 2263.81),
                 _w("work", 2263.81, 2263.97),
                 _w("for", 2263.97, 2264.11),
                 _w("Google", 2264.11, 2264.45),
                 _w("but", 2264.45, 2264.6),
                 _w("work", 2264.6, 2264.81),
                 _w("for", 2264.81, 2264.98),
                 _w("AI.", 2265.01, 2265.42),
             ]),
        _seg_2("SpeakerOne",
             "So make sure that your when you're trying to optimize "
             "your content, your website, your LinkedIn, etcetera, "
             "Try to see what you have as a differentiator, your "
             "niche, and try to see how humans would give queries to "
             "Chat GPT that are more um that are more like niche and "
             "have a lot more words in it because that's not usually",
             2265.62, 2284.59, "u-b",
             words=[
                 _w("So", 2265.62, 2265.81),
                 _w("make", 2265.81, 2266.09),
                 _w("sure", 2266.09, 2266.48),
                 _w("that", 2266.48, 2266.91),
                 _w("your", 2266.91, 2267.44),
                 _w("when", 2267.51, 2267.66),
                 _w("etcetera,", 2270.98, 2271.51),
                 _w("usually", 2284.27, 2284.59),
             ]),
        _seg_2("SpeakerOne",
             "how they would search on Google and try to optimize "
             "for that.",
             2284.55, 2287.14, "u-c",
             words=[
                 _w("how", 2284.55, 2284.67),
                 _w("they", 2284.67, 2284.81),
                 _w("would", 2284.81, 2284.96),
                 _w("search", 2284.96, 2285.19),
                 _w("on", 2285.19, 2285.32),
                 _w("Google", 2285.32, 2285.76),
                 _w("and", 2286.09, 2286.19),
                 _w("try", 2286.19, 2286.47),
                 _w("to", 2286.47, 2286.61),
                 _w("optimize", 2286.61, 2286.8),
                 _w("for", 2286.8, 2286.92),
                 _w("that.", 2286.92, 2287.14),
             ]),
    )


def test_a_reported_reel_places_as_approved_but_stays_loud():
    """The Reel 28 carve-out, as a general predicate: the tail adds
    21.54s past a 9.34s closing thought - a further passage, not a
    severed tail - so the bound keeps its approved value, a tail-report
    move is recorded, and the preview flags it without counting it."""
    from library.tools.reel_proposal import preview_snap

    new_end, finding = repair_moment_tail(
        2265.6, 2217.71, _reel28_transcript())
    assert new_end == 2265.6
    assert finding["verdict"] == "report"
    assert finding["tail_end"] == 2287.14
    assert finding["kind"] == "sentence-tail"
    assert finding["inferred"] is True

    repaired, moves = snap_moment_to_speech(
        _moment_3(28, 2217.71, 2265.6), _reel28_transcript())
    assert repaired.timeline_end == 2265.6
    reports = [m for m in moves
               if m.get("attribution") == "tail-report"]
    assert len(reports) == 1
    assert reports[0]["reported"] is True
    report = preview_snap([_moment_3(28, 2217.71, 2265.6)],
                          _reel28_transcript())
    assert report["moved"] == 0
    assert report["flagged"] == 1
    lines = decision_lines(28, report["moments"][0]["moves"][0],
                           _reel28_transcript(), "")
    assert any("NEEDS DECISION" in line for line in lines)
    assert any("moment_boundary_repairs" in line for line in lines)


# ------------------------------------------------- the authorised reel
#
# The captain, 2026-09-21, answering the board: "Extend it" - Reel 28
# lengthens by design, and every other accepted reel keeps reporting.
# The ruling travels as data
# (`library/tools/tail_extend_authorization.py`); the predicate,
# its thresholds and its report verdict are untouched.


def _reel28_authorizations():
    return {28: {"reel": 28,
                 "reason": "captain 2026-09-21: extend it",
                 "measured_edge": 2265.6,
                 "measured_tail_end": 2287.14,
                 "measured_gap": 0.02}}


def test_an_authorised_report_applies_as_an_extension():
    """Reel 28 with its recorded ruling extends exactly where the
    predicate would have: the body runs to the sentence end at
    2287.14s, the WHY and the move name whose decision that was, the
    lengthening is loud, and the ruling is per reel: Reel 10's identical
    shape with no entry of its own keeps reporting."""
    from library.tools.reel_proposal import preview_snap

    repaired, moves = snap_moment_to_speech(
        _moment_3(28, 2217.71, 2265.6), _reel28_transcript(),
        tail_extend_authorizations=_reel28_authorizations())
    assert repaired.timeline_end == 2287.14
    extends = [m for m in moves
               if m.get("attribution") == "tail-extend"]
    assert len(extends) == 1
    assert extends[0]["now"] == 2287.14
    assert (extends[0]["authorized_by"]
            == "captain 2026-09-21: extend it")
    assert "captain 2026-09-21" in extends[0]["why"]
    assert reel_ranges(repaired, _reel28_transcript()) == [
        (repaired.timeline_start, 2287.14)]
    assert reel_build.midword_keep_edges(
        repaired.timeline_start, repaired.timeline_end,
        _reel28_transcript()) == []
    report = preview_snap([_moment_3(28, 2217.71, 2265.6)],
                          _reel28_transcript(),
                          tail_extend_authorizations=(
                              _reel28_authorizations()))
    assert (report["moved"], report["flagged"]) == (1, 1)
    lines = decision_lines(28, report["moments"][0]["moves"][0],
                           _reel28_transcript(), "")
    assert any("NEEDS DECISION" in line for line in lines)
    sibling, sibling_moves = snap_moment_to_speech(
        _moment_3(10, 2217.71, 2265.6), _reel28_transcript(),
        tail_extend_authorizations=_reel28_authorizations())
    assert sibling.timeline_end == 2265.6
    assert [m for m in sibling_moves
            if m.get("attribution") == "tail-report"] != []


def _an_authorised_extension_answers_the_recorded_report(tmp_path):
    """The ledger stops asking once answered: recording an
    authorised tail-extend supersedes the same reel and boundary's
    tail-report entry, and says so - while an UNauthorised extend
    would leave both standing."""
    tmp_path.mkdir()
    project = tmp_path / "project"
    review = project / "pipeline_output" / "review"
    review.mkdir(parents=True)
    ledger_file = review / "moment_boundary_repairs.json"
    ledger_file.write_text(json.dumps({
        "key": "moment_boundary_repairs",
        "source": "firstmate 2026-09-19",
        "value": [{"kind": "moment_boundary_repair", "reel": 28,
                   "boundary": "body_end", "was": 2265.6, "now": 2265.6,
                   "attribution": "tail-report",
                   "reason": "reported, never applied",
                   "source": "firstmate 2026-09-19"}],
    }), encoding="utf-8")
    record_tail_repairs(
        str(project),
        [(28, {"boundary": "body_end", "was": 2265.6, "now": 2287.14,
               "attribution": "tail-extend",
               "why": "applied under captain 2026-09-21: extend it",
               "authorized_by": "captain 2026-09-21: extend it"})])
    written = json.loads(ledger_file.read_text(encoding="utf-8"))
    attributions = [entry["attribution"]
                    for entry in written["value"]]
    assert attributions == ["tail-extend"]
    assert (written["value"][0]["authorized_by"]
            == "captain 2026-09-21: extend it")


# --------------------------------------------------------------------------
# From test_reel_midword_keep_edges.py
#
# Keep-range edges the cutter draws must not land inside a word.
#
# From `vep-approved-reels-still-have-visible-defects/report.md`: placed
# cards match the plan within 2 frames on all 306 cards, so the defects
# the captain watched were authored in the PLAN - and the interior keep
# edges are drawn by the cutter (`redundant_takes` + `keep_ranges`), which
# the plan gate never checks. `validate_proposal` refuses an OUTER
# boundary cutting a segment (`partial_overlaps`), but nothing asks what
# an INTERIOR edge cuts through:
#
# - R07: the cut lands 0.02s into "Your" - the viewer hears "Your we-".
# - R02 head: a 3-frame "their keywords" sliver, 125ms, less than the
#   words themselves - the edge sits inside the words.
# - R02/R06/R10 blips: 5-13f slivers playing the attack/onset of a word
#   ("that's", "i kind", "Yeah") between cards.
#
# A mid-word edge is measurable against word times TODAY
# (`_word_intervals`, the same stream `_snap_out_of_words` reads), so it
# is a MECHANICAL refusal, not taste (AGENTS.md 10.5): the check asks the
# same strict-interior question the snap asks, of the edges the snap never
# touches.
#
# What this deliberately does NOT refuse: a keep edge on a word boundary
# mid-sentence (R04's three pieces, R10's 25s jump, R08/R11 cold opens).
# "Does not parse as a thought" is a judgement and belongs to the MODEL,
# not to a rule in the engine - those stay reported
# (`refused_take_groups`, `opening_observations`), never refused here.

def _seg_3(speaker, text, start, end, uid="u", words=None):
    seg = {"speaker": speaker, "text": text, "timeline_start": start,
           "timeline_end": end, "source_file": "/m/a.MXF",
           "source_start": start, "source_end": end,
           "resolve_item_id": uid}
    if words is not None:
        seg["words"] = words
    return seg


def _w_2(word, start, end):
    return {"word": word, "start": start, "end": end}


def _r07_transcript():
    """R07's shape, report numbers kept: the dropped take starts at
    104.42s, which is 0.02s inside "Your" (104.40-104.90s)."""
    return _tx(
        _seg_3("SpeakerOne", "Absolutely. Your website", 100.0, 104.42, "u1",
             words=[_w_2("Absolutely.", 100.0, 100.70),
                    _w_2("Your", 104.40, 104.90),
                    _w_2("website", 104.95, 105.60)]),
        _seg_3("SpeakerOne", "is your resume, hiring managers check both",
             104.42, 109.0, "u2",
             words=[_w_2("is", 104.95, 105.10),
                    _w_2("both", 108.60, 109.00)]),
        _seg_3("SpeakerOne", "is your resume, hiring managers check both places",
             112.0, 117.0, "u3",
             words=[_w_2("is", 112.10, 112.25),
                    _w_2("places", 116.60, 117.00)]),
        _seg_3("SpeakerOne", "and that is the whole point", 118.0, 121.0, "u4",
             words=[_w_2("and", 118.0, 118.2),
                    _w_2("point", 120.6, 121.0)]),
    )


def _moment_4(start=100.0, end=121.0):
    return ReelMoment(number=7, slug="website-is-resume",
                      reason="a complete exchange",
                      timeline_start=start, timeline_end=end,
                      approval=Approval.APPROVED)


def test_a_keep_edge_inside_a_word_is_found():
    from library.tools.reel_build import midword_keep_edges
    found = midword_keep_edges(100.0, 121.0, _r07_transcript())
    assert len(found) == 1
    assert found[0]["edge"] == 104.42
    assert found[0]["word"] == "Your"


def test_the_build_refuses_a_midword_keep_edge():
    with pytest.raises(ReelBuildError):
        reel_ranges(_moment_4(), _r07_transcript())


def test_a_keep_edge_on_word_edges_passes():
    """The same takes, but the segment edge lands exactly where "Your"
    starts: the viewer hears whole words, so the plan stands."""
    tx = _tx(
        _seg_3("SpeakerOne", "Absolutely.", 100.0, 101.0, "u1",
             words=[_w_2("Absolutely.", 100.0, 100.70)]),
        _seg_3("SpeakerOne", "is your resume, hiring managers check both",
             104.42, 109.0, "u2",
             words=[_w_2("is", 104.50, 104.65),
                    _w_2("both", 108.60, 109.00)]),
        _seg_3("SpeakerOne", "is your resume, hiring managers check both places",
             112.0, 117.0, "u3",
             words=[_w_2("places", 116.60, 117.00)]),
        _seg_3("SpeakerOne", "and that is the whole point", 118.0, 121.0, "u4",
             words=[_w_2("point", 120.6, 121.0)]),
    )
    from library.tools.reel_build import midword_keep_edges
    assert midword_keep_edges(100.0, 121.0, tx) == []
    assert reel_ranges(_moment_4(), tx) == [(100.0, 104.42), (109.0, 121.0)]


def test_faithful_disfluency_is_not_a_take_and_not_a_midword_edge():
    """R12's "different different" is the speaker stuttering, not the
    editor keeping two takes: one adjacent repeat inside ONE segment is
    below every detector window (4-word `repeat`, 3-word `possible`),
    and with no cut there is no interior edge to refuse. A rule that
    could see this would mangle honest speech; the line is structural -
    cross-segment content repetition is editorial, intra-segment
    adjacency is faithful - so this plan must pass untouched."""
    tx = _tx(
        _seg_3("SpeakerOne", "it was a different different problem", 10.0, 16.0,
             "u1", words=[_w_2("different", 12.0, 12.35),
                          _w_2("different", 12.35, 12.70),
                          _w_2("problem", 13.0, 13.5)]),
        _seg_3("SpeakerTwo", "right, and then what happened", 17.0, 22.0, "u2",
             words=[_w_2("right,", 17.0, 17.3)]),
    )
    assert duplicate_takes(10.0, 22.0, tx) == []
    from library.tools.reel_build import midword_keep_edges, redundant_takes
    assert redundant_takes(10.0, 22.0, tx) == []
    assert midword_keep_edges(10.0, 22.0, tx) == []


def test_float_dust_on_a_word_edge_is_not_a_midword_cut():
    """Reel 14 (2026-09-20): a take-cut edge at 1158.3199999999997s
    against the word start 1158.32s - 2.8e-13s of JSON float dust on
    the same instant - refused the quality bar's playable_ranges and
    emptied the reel text, failing a correct reel. Edges canonicalise
    to 6dp keys; words now canonicalise the same way, so dust is not
    a cut word while a genuine interior edge still fires."""
    from library.tools.reel_build import Cut, midword_keep_edges
    tx = _tx(
        _seg_3("SpeakerOne", "setup words here", 1138.0, 1140.0, "u1",
             words=[_w_2("here", 1139.0, 1139.50)]),
        _seg_3("SpeakerOne", "If you run ads", 1158.32, 1160.0, "u2",
             words=[_w_2("If", 1158.3199999999997, 1158.48),
                    _w_2("you", 1158.48, 1158.60)]),
        _seg_3("SpeakerOne", "take two kept", 1168.0, 1170.0, "u3",
             words=[_w_2("kept", 1169.0, 1169.50)]),
    )
    cuts = [Cut(
        dropped_start=1158.3199999999997, dropped_end=1164.0,
        dropped_text="If you run ads", kept_start=1168.0,
        kept_end=1170.0, kept_text="take two kept", speaker="SpeakerOne",
        containment=1.0, jaccard=1.0, basis="test dust")]
    assert midword_keep_edges(1138.0, 1170.0, tx, cuts) == []


def test_a_genuine_interior_edge_still_fires():
    """The dust canonicalisation must not swallow a real cut: an edge
    a full frame inside the word still refuses."""
    from library.tools.reel_build import Cut, midword_keep_edges
    tx = _tx(
        _seg_3("SpeakerOne", "setup words here", 1138.0, 1140.0, "u1",
             words=[_w_2("here", 1139.0, 1139.50)]),
        _seg_3("SpeakerOne", "If you run ads", 1158.32, 1160.0, "u2",
             words=[_w_2("If", 1158.32, 1158.48),
                    _w_2("you", 1158.48, 1158.60)]),
        _seg_3("SpeakerOne", "take two kept", 1168.0, 1170.0, "u3",
             words=[_w_2("kept", 1169.0, 1169.50)]),
    )
    cuts = [__import__("library.tools.reel_build", fromlist=["x"]).Cut(
        dropped_start=1158.36, dropped_end=1164.0,
        dropped_text="If you run ads", kept_start=1168.0,
        kept_end=1170.0, kept_text="take two kept", speaker="SpeakerOne",
        containment=1.0, jaccard=1.0, basis="test interior")]
    found = midword_keep_edges(1138.0, 1170.0, tx, cuts)
    assert len(found) == 1
    assert found[0]["word"] == "If"


# --------------------------------------------------------------------------
# From test_reel_spine_straddle_split.py
#
# A keep exclusion cutting the middle out of one transcript row must split it.
#
# Reel 21, 2026-09-20: lc-0095 strikes the abandoned run-up tail out of the
# middle of one transcript segment. Narrowing the row to its kept words
# still spanned the hole - 5.36s of source on 2.03s of reel - and step
# 4.01 refused the block, because no 1x word mapping can cross a hole the
# picture jump-cuts over. The spine now splits the row into one block per
# contiguous play span, so every block plays straight through.

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from dataclasses import dataclass

from library.steps.step_4_01_plan_subtitles.step import generate_subtitles
from library.tools.reel_spine import spine_for_reel


@dataclass
class Moment:
    timeline_start: float
    timeline_end: float
    number: int = 1


def _row(text, tl_start, per_word=0.4):
    parts = text.split()
    return {
        "speaker": "guest", "text": text,
        "timeline_start": tl_start,
        "timeline_end": tl_start + len(parts) * per_word,
        "source_file": "cam_a.mov", "resolve_item_id": "cam_a.mov",
        "source_start": 100.0,
        "source_end": 100.0 + len(parts) * per_word,
        "words": [
            {"word": w, "start": tl_start + i * per_word,
             "end": tl_start + (i + 1) * per_word}
            for i, w in enumerate(parts)
        ],
    }


def test_row_straddling_a_keep_exclusion_splits_in_two():
    """The Reel 21 shape: one row, a 1s hole cut from its middle."""
    row = _row("alpha beta gamma delta epsilon zeta eta theta", 10.0)
    ranges = [(10.0, 11.0), (12.0, 13.2)]
    spine = spine_for_reel(Moment(10.0, 13.2), {"segments": [row]},
                           ranges=ranges)
    blocks = spine["structure"]
    assert len(blocks) == 2
    for block in blocks:
        source_span = block["source_end"] - block["source_start"]
        reel_span = block["timeline_end"] - block["timeline_start"]
        assert source_span <= reel_span * 1.5, (
            f"block {block['position']} still spans a hole: "
            f"{source_span:.2f}s of source on {reel_span:.2f}s of reel")
    words = [w["word"] for b in blocks for w in b["word_timestamps"]]
    assert words == ["alpha", "beta", "gamma",
                     "zeta", "eta", "theta"], (
        "both sides survive, the struck middle does not")
    # the refusal point itself: step 4.01 maps every block 1x
    plan = generate_subtitles(spine, caption_case="lowercase",
                              project_folder="")
    entries = plan["subtitle_plan"]["subtitle_entries"]
    assert entries, "both surviving sides must still be captioned"
    assert " ".join(e["text"] for e in entries) == \
        "alpha beta gamma zeta eta theta"


def test_overlapping_words_do_not_split():
    """The aligner's own slop is not a cut: an overlapping pair stays put.

    Same-file transcript overlaps are sequential speech, and the caption
    gate already reads them that way. Splitting on them would trade one
    refusal for overlapping blocks.
    """
    row = _row("alpha beta gamma delta", 10.0)
    row["words"][1]["start"] = 10.3  # beta opens inside alpha's span
    spine = spine_for_reel(Moment(10.0, 12.0), {"segments": [row]},
                           ranges=[(10.0, 12.0)])
    assert len(spine["structure"]) == 1
