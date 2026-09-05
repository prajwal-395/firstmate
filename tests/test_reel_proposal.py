"""The pipeline proposes reel moments; the captain approves.

Captain's ruling, 2026-09-04 (Q4): "Pipeline proposes, captain approves
... Do not build timelines for unapproved moments."

An instruction not to build something is worth nothing if the build path
cannot tell an approved moment from a proposed one, so the tests that
matter most here are the GATE tests - and specifically that the DEFAULT
state fails it. A gate whose default is "allowed" is ornamental
(AGENTS.md 10.4).
"""

from __future__ import annotations

import json
from dataclasses import replace

import pytest

from library.tools.reel_proposal import (
    Approval,
    MIN_REEL_SECONDS,
    NotApproved,
    ProposalError,
    ReelMoment,
    approved_only,
    assert_approved,
    enrich,
    held_back,
    read_proposal,
    reel_timeline_name,
    render_for_review,
    slugify,
    validate_proposal,
    write_proposal,
    bound_segments,
    straddling_segments,
    snap_to_speech,
    straddling_within,
    duplicate_takes,
)


def _transcript(**overrides):
    doc = {
        "derived_from": {"timeline": "GEO Podcast - Synced",
                         "duration_seconds": 2656.6},
        "segments": [
            {"speaker": "Craig", "text": "SEO is about convincing a crawler.",
             "timeline_start": 10.0, "timeline_end": 18.0,
             "source_file": "/m/LCATL0011.MXF", "resolve_item_id": "uid-1",
             "source_start": 100.0, "source_end": 108.0},
            {"speaker": "Akshita", "text": "GEO is about comprehension.",
             "timeline_start": 18.5, "timeline_end": 26.0,
             "source_file": "/m/LC4930.MXF", "resolve_item_id": "uid-2",
             "source_start": 200.0, "source_end": 207.5},
            {"speaker": "Craig", "text": "Totally different system.",
             "timeline_start": 400.0, "timeline_end": 409.0,
             "source_file": "/m/LCATL0012.MXF", "resolve_item_id": "uid-3",
             "source_start": 50.0, "source_end": 59.0},
        ],
    }
    doc.update(overrides)
    return doc


def _moment(**overrides):
    base = dict(number=1, slug="seo-vs-geo",
                reason="The clearest one-line contrast in the episode.",
                timeline_start=10.0, timeline_end=26.0)
    base.update(overrides)
    return ReelMoment(**base)


# ── The gate ─────────────────────────────────────────────────────────

def test_a_proposed_moment_is_not_buildable():
    """The default state. This is the test that makes the gate real."""
    with pytest.raises(NotApproved) as excinfo:
        assert_approved(_moment())
    assert "still PROPOSED" in str(excinfo.value)


def test_the_default_approval_is_proposed():
    assert _moment().approval is Approval.PROPOSED


def test_a_rejected_moment_is_not_buildable():
    moment = _moment(approval=Approval.REJECTED,
                     approval_note="covered better later")
    with pytest.raises(NotApproved) as excinfo:
        assert_approved(moment)
    assert "REJECTED" in str(excinfo.value)
    assert "covered better later" in str(excinfo.value)


def test_an_approved_moment_passes_and_is_returned():
    moment = _moment(approval=Approval.APPROVED)
    assert assert_approved(moment) is moment


def test_approved_only_selects_and_held_back_explains():
    moments = [
        _moment(number=1, approval=Approval.APPROVED),
        _moment(number=2, slug="b"),
        _moment(number=3, slug="c", approval=Approval.REJECTED),
    ]
    assert [m.number for m in approved_only(moments)] == [1]
    back = held_back(moments)
    assert [m.number for m in back["proposed"]] == [2]
    assert [m.number for m in back["rejected"]] == [3]


# ── The captain's naming ─────────────────────────────────────────────

def test_the_reel_name_is_the_captains_format():
    assert reel_timeline_name(1, "seo vs geo") == "Reel 01 - seo-vs-geo"
    assert reel_timeline_name(12, "Why AI Reads Copy") == "Reel 12 - why-ai-reads-copy"


def test_the_number_is_zero_padded_to_two_digits():
    assert reel_timeline_name(3, "x").startswith("Reel 03 - ")


def test_an_empty_slug_is_visibly_untitled_not_blank():
    assert slugify("") == "untitled"
    assert reel_timeline_name(1, "!!!") == "Reel 01 - untitled"


# ── A proposal must be real ──────────────────────────────────────────

def test_a_valid_proposal_passes():
    validate_proposal([_moment()], _transcript(), 2656.6)


def test_a_moment_outside_the_timeline_is_refused():
    with pytest.raises(ProposalError) as excinfo:
        validate_proposal([_moment(timeline_start=9000.0, timeline_end=9020.0)],
                          _transcript(), 2656.6)
    assert "outside the timeline" in str(excinfo.value)


def test_a_moment_where_nobody_speaks_is_refused():
    """The check that stops a model inventing a timecode."""
    with pytest.raises(ProposalError) as excinfo:
        validate_proposal([_moment(timeline_start=1000.0, timeline_end=1030.0)],
                          _transcript(), 2656.6)
    assert "invented timecode" in str(excinfo.value)


def test_a_moment_with_no_reason_is_refused():
    with pytest.raises(ProposalError) as excinfo:
        validate_proposal([_moment(reason="   ")], _transcript(), 2656.6)
    assert "no reason" in str(excinfo.value)


def test_an_empty_range_is_refused():
    with pytest.raises(ProposalError):
        validate_proposal([_moment(timeline_end=10.0)], _transcript(), 2656.6)


def test_a_span_under_the_measurement_floor_is_refused():
    with pytest.raises(ProposalError) as excinfo:
        validate_proposal(
            [_moment(timeline_start=10.0,
                     timeline_end=10.0 + MIN_REEL_SECONDS - 0.1)],
            _transcript(), 2656.6)
    assert "floor" in str(excinfo.value)


def test_there_is_no_maximum_length():
    """How long a reel should be is the captain's call, not a floor here."""
    validate_proposal([_moment(timeline_start=10.0, timeline_end=2000.0)],
                      _transcript(), 2656.6)


def test_a_repeated_reel_number_is_refused():
    with pytest.raises(ProposalError) as excinfo:
        validate_proposal([_moment(number=1), _moment(number=1, slug="other")],
                          _transcript(), 2656.6)
    assert "used twice" in str(excinfo.value)


def test_an_empty_proposal_is_refused():
    with pytest.raises(ProposalError) as excinfo:
        validate_proposal([], _transcript(), 2656.6)
    assert "no moments proposed" in str(excinfo.value)


# ── Measured fields are attached, not asked for ──────────────────────

def test_enrich_attaches_speakers_and_ground_truth():
    enriched = enrich(_moment(), _transcript())
    assert enriched.speakers == ("Craig", "Akshita")
    assert "SEO is about convincing" in enriched.transcript_preview
    assert [s["source_file"] for s in enriched.source_spans] == [
        "/m/LCATL0011.MXF", "/m/LC4930.MXF"]


def test_enrich_leaves_the_span_alone():
    """Measured fields are attached; the model's choice is not edited."""
    moment = _moment()
    enriched = enrich(moment, _transcript())
    assert (enriched.timeline_start, enriched.timeline_end) == (
        moment.timeline_start, moment.timeline_end)


# ── The review surface ───────────────────────────────────────────────

def test_a_proposal_round_trips_with_the_captains_decision(tmp_path):
    path = tmp_path / "reel_proposals.json"
    write_proposal(path, [enrich(_moment(), _transcript())], _transcript())

    data = json.loads(path.read_text())
    assert data["moments"][0]["approval"] == "proposed"
    assert data["moments"][0]["timeline_name"] == "Reel 01 - seo-vs-geo"

    # the captain edits the file
    data["moments"][0]["approval"] = "approved"
    data["moments"][0]["approval_note"] = "yes, lead with this"
    path.write_text(json.dumps(data))

    loaded = read_proposal(path)
    assert loaded[0].approval is Approval.APPROVED
    assert assert_approved(loaded[0]) is loaded[0]


def test_the_document_says_how_to_approve(tmp_path):
    path = tmp_path / "p.json"
    write_proposal(path, [_moment()], _transcript())
    data = json.loads(path.read_text())
    assert "approved" in data["instruction"]
    assert "Nothing is built" in data["instruction"]


def test_a_foreign_document_is_refused(tmp_path):
    path = tmp_path / "p.json"
    path.write_text(json.dumps({"format": "something/else", "moments": []}))
    with pytest.raises(ProposalError):
        read_proposal(path)


def test_the_review_rendering_shows_state_timecode_and_reason():
    text = render_for_review([
        enrich(_moment(), _transcript()),
        _moment(number=2, slug="later", timeline_start=400.0,
                timeline_end=409.0, approval=Approval.APPROVED),
    ])
    assert "[ ] Reel 01 - seo-vs-geo" in text
    assert "[x] Reel 02 - later" in text
    assert "00:10-00:26" in text
    assert "The clearest one-line contrast" in text


# ── A reel never opens or closes mid-sentence ────────────────────────
#
# 63 of the field test's 906 segments straddle a cut. A boundary landing
# on one takes half a sentence, and in practice a straddling segment is
# often WhisperX bridging a silent gap - Craig's 22.0-47.2s runs 25
# seconds across a stretch where his track has no clip at all.

def _bound(**kw):
    base = dict(speaker="Craig", text="a line", timeline_start=10.0,
                timeline_end=18.0, source_file="/m/a.MXF",
                source_start=100.0, source_end=108.0,
                resolve_item_id="uid-1")
    base.update(kw)
    return base


def _tx(*segments):
    return {"derived_from": {}, "segments": list(segments)}


def test_a_boundary_that_cuts_a_segment_is_refused():
    tx = _tx(_bound(timeline_start=10.0, timeline_end=18.0))
    moment = _moment(timeline_start=14.0, timeline_end=30.0)
    with pytest.raises(ProposalError) as excinfo:
        validate_proposal([moment], tx, 2656.6)
    assert "mid-sentence" in str(excinfo.value)


def test_a_boundary_on_whole_segments_passes():
    tx = _tx(_bound(timeline_start=10.0, timeline_end=18.0),
             _bound(timeline_start=18.5, timeline_end=26.0, resolve_item_id="uid-2"))
    validate_proposal([_moment(timeline_start=10.0, timeline_end=26.0)],
                      tx, 2656.6)


def test_snap_moves_boundaries_outward_not_inward():
    """Trimming inward silently drops words the proposer meant to keep."""
    tx = _tx(_bound(timeline_start=10.0, timeline_end=18.0),
             _bound(timeline_start=18.5, timeline_end=26.0, resolve_item_id="uid-2"))
    assert snap_to_speech(14.0, 20.0, tx) == (10.0, 26.0)


def test_snapping_makes_a_refused_moment_pass():
    tx = _tx(_bound(timeline_start=10.0, timeline_end=18.0),
             _bound(timeline_start=18.5, timeline_end=26.0, resolve_item_id="uid-2"))
    start, end = snap_to_speech(14.0, 20.0, tx)
    validate_proposal([_moment(timeline_start=start, timeline_end=end)],
                      tx, 2656.6)


def test_a_straddling_segment_is_never_a_boundary_anchor():
    """It has no single ground truth, and is often a bridged silence."""
    tx = _tx(_bound(timeline_start=10.0, timeline_end=18.0),
             _bound(timeline_start=22.0, timeline_end=47.2,
                    resolve_item_id=None))          # straddles a cut
    assert len(bound_segments(tx)) == 1
    assert len(straddling_segments(tx)) == 1
    # snapping ignores the straddler rather than extending to 47.2s
    assert snap_to_speech(11.0, 17.0, tx) == (10.0, 18.0)


def test_a_straddler_the_span_touches_still_does_not_widen_it():
    """The case the test above could not reach, and the one that bit.

    The straddler OVERLAPS the proposed span here, so a `snap_to_speech`
    reading `transcript["segments"]` whole widens the end to 47.2s -
    which on the field test is seconds of bridged silence and then the
    next topic, cut mid-sentence.  The reel closed on that and the
    review surface showed the clean line instead, because `enrich`
    reads bound segments only.
    """
    tx = _tx(_bound(timeline_start=10.0, timeline_end=18.0),
             _bound(timeline_start=16.0, timeline_end=47.2,
                    resolve_item_id=None))          # straddles a cut
    assert snap_to_speech(11.0, 17.0, tx) == (10.0, 18.0)


def test_straddling_speech_inside_a_span_is_reported():
    """The reel PLAYS it, so a review surface has to show it."""
    tx = _tx(_bound(timeline_start=10.0, timeline_end=18.0),
             _bound(timeline_start=16.0, timeline_end=47.2,
                    resolve_item_id=None, speaker="Craig",
                    text="well that's",
                    words=[{"word": "well", "start": 16.0, "end": 40.0},
                           {"word": "that's", "start": 40.1, "end": 47.2}]))
    start, end = snap_to_speech(11.0, 17.0, tx)
    reported = straddling_within(start, end, tx)
    assert len(reported) == 1
    assert reported[0]["speaker"] == "Craig"
    assert reported[0]["text"] == "well"
    # ONE word over a 24-second segment: a bridge, and the count is
    # what says so.  Summed word durations would read 24 voiced seconds.
    assert reported[0]["word_count"] == 1


def test_a_straddler_outside_the_span_is_not_reported():
    tx = _tx(_bound(timeline_start=10.0, timeline_end=18.0),
             _bound(timeline_start=22.0, timeline_end=47.2,
                    resolve_item_id=None,
                    words=[{"word": "later", "start": 22.0, "end": 22.4}]))
    assert straddling_within(10.0, 18.0, tx) == []


def test_enrich_carries_the_straddling_report_and_it_round_trips():
    tx = _tx(_bound(timeline_start=10.0, timeline_end=18.0),
             _bound(timeline_start=16.0, timeline_end=47.2,
                    resolve_item_id=None, speaker="Craig",
                    words=[{"word": "well", "start": 16.5, "end": 17.0}]))
    moment = enrich(_moment(timeline_start=10.0, timeline_end=18.0), tx)
    assert moment.straddling_within[0]["text"] == "well"
    assert "well" not in moment.transcript_preview
    body = moment.as_dict()
    assert body["straddling_within"][0]["word_count"] == 1
    assert ReelMoment.from_dict(body).straddling_within == moment.straddling_within


def test_a_straddling_segment_does_not_make_a_boundary_illegal():
    """It is excluded from the check, not treated as a cut segment.

    The straddler here OVERLAPS the span and is cut by it, which is the
    case that matters: `snap_to_speech` will not move a boundary onto a
    straddler's edges, so refusing the boundary for cutting one refuses
    a span nothing can repair.  `straddling_within` reports it instead.
    """
    tx = _tx(_bound(timeline_start=10.0, timeline_end=18.0),
             _bound(timeline_start=16.0, timeline_end=47.2,
                    resolve_item_id=None,
                    words=[{"word": "bridged", "start": 16.5, "end": 17.0}]))
    validate_proposal([_moment(timeline_start=10.0, timeline_end=18.0)],
                      tx, 2656.6)
    assert straddling_within(10.0, 18.0, tx)[0]["text"] == "bridged"


def test_enrich_ignores_straddling_segments_too():
    """Otherwise a preview shows words the reel does not contain, and
    names a speaker who is not in it."""
    tx = _tx(_bound(speaker="Akshita", text="in the reel",
                    timeline_start=10.0, timeline_end=18.0),
             _bound(speaker="Craig", text="bridged across a silent gap",
                    timeline_start=5.0, timeline_end=40.0,
                    resolve_item_id=None))
    enriched = enrich(_moment(timeline_start=10.0, timeline_end=18.0), tx)
    assert enriched.speakers == ("Akshita",)
    assert "bridged" not in enriched.transcript_preview


# ── Repeated takes ───────────────────────────────────────────────────
#
# The captain warned the rough cut still holds several takes of the same
# lines. It turned out to be four of the first ten proposals, which is
# why this is reported per moment rather than left as a footnote.

def _words(text, start, step=0.4):
    return [{"word": w, "start": start + i * step, "end": start + (i + 1) * step}
            for i, w in enumerate(text.split())]


def _spoken(text, start, end, uid="uid-1", speaker="Akshita"):
    return {"speaker": speaker, "text": text, "timeline_start": start,
            "timeline_end": end, "source_file": "/m/a.MXF",
            "source_start": start, "source_end": end,
            "resolve_item_id": uid, "words": _words(text, start)}


def test_a_reworded_retake_is_found():
    """Take two of reel 02 shares no long n-gram with take one and is
    plainly the same sentence. Content-word overlap survives reordering;
    exact phrases do not."""
    tx = _tx(_spoken("we ran an audit on a client and their SEO team had "
                     "stuffed all their keywords with H1 tags", 10.0, 20.0),
             _spoken("so we ran an audit last week on a client where an SEO "
                     "team stuffed all the H1 tags with keywords",
                     20.5, 30.0, uid="uid-2"))
    found = duplicate_takes(10.0, 30.0, tx)
    assert found, "a reworded retake must still be found"
    assert found[0]["band"] == "repeat"
    assert found[0]["first_start"] < found[0]["second_start"]


def test_a_finding_names_both_timeline_ranges():
    """So the captain can see which of the two to drop without hunting."""
    tx = _tx(_spoken("google only ranks pages and cares nothing about "
                     "information", 10.0, 20.0),
             _spoken("google only ranks pages caring nothing about "
                     "information", 21.0, 30.0, uid="uid-2"))
    found = duplicate_takes(10.0, 30.0, tx)
    assert found
    entry = found[0]
    for key in ("first_start", "first_end", "second_start", "second_end",
                "first_text", "second_text", "similarity", "band"):
        assert key in entry, key
    assert entry["second_start"] > entry["first_end"]


def test_distinct_speech_is_not_a_retake():
    tx = _tx(_spoken("seo is about convincing an algorithm to rank your "
                     "page higher", 10.0, 20.0),
             _spoken("geo is about whether artificial intelligence "
                     "comprehends your company", 21.0, 30.0, uid="uid-2"))
    assert duplicate_takes(10.0, 30.0, tx) == []


def test_the_two_bands_are_reported_separately():
    """No single window is both complete and clean, so the boundary is
    REPORTED rather than decided - the shape footage_search already uses
    for its dense floor and weak band."""
    from library.tools.reel_proposal import (
        TAKE_WEAK_WINDOW, TAKE_WINDOW_WORDS)
    assert TAKE_WEAK_WINDOW < TAKE_WINDOW_WORDS
    tx = _tx(_spoken("machines actually comprehend the company", 10.0, 15.0),
             _spoken("whether machines actually comprehend anyone",
                     20.0, 25.0, uid="uid-2"))
    found = duplicate_takes(10.0, 25.0, tx)
    assert found and found[0]["band"] == "possible"


def test_a_straddling_segment_is_not_scanned_for_takes():
    """It is often a bridged silence, and its text is not reliably in
    the reel at all."""
    tx = _tx(_spoken("we ran an audit on a client for their seo team",
                     10.0, 20.0),
             dict(_spoken("we ran an audit on a client for their seo team",
                          21.0, 30.0, uid="uid-2"), resolve_item_id=None))
    assert duplicate_takes(10.0, 30.0, tx) == []


def test_enrich_attaches_the_findings_to_the_moment():
    tx = _tx(_spoken("we ran an audit on a client and their seo team "
                     "stuffed keywords into h1 tags", 10.0, 20.0),
             _spoken("we ran an audit last week where an seo team stuffed "
                     "keywords into h1 tags", 21.0, 30.0, uid="uid-2"))
    enriched = enrich(_moment(timeline_start=10.0, timeline_end=30.0), tx)
    assert enriched.duplicate_takes
    assert enriched.as_dict()["has_duplicate_take"] is True


def test_a_moment_with_no_repeat_says_so():
    tx = _tx(_spoken("seo convinces an algorithm to rank pages higher",
                     10.0, 18.0))
    enriched = enrich(_moment(timeline_start=10.0, timeline_end=18.0), tx)
    assert enriched.duplicate_takes == ()
    assert enriched.as_dict()["has_duplicate_take"] is False


def test_a_repeat_never_removes_anything():
    """It is a MEASUREMENT. Which take to keep is the captain's call on
    their own edit, and nothing here trims or reorders."""
    tx = _tx(_spoken("we ran an audit on a client and their seo team "
                     "stuffed keywords into h1 tags", 10.0, 20.0),
             _spoken("we ran an audit last week where an seo team stuffed "
                     "keywords into h1 tags", 21.0, 30.0, uid="uid-2"))
    moment = _moment(timeline_start=10.0, timeline_end=30.0)
    enriched = enrich(moment, tx)
    assert (enriched.timeline_start, enriched.timeline_end) == (
        moment.timeline_start, moment.timeline_end)
    validate_proposal([enriched], tx, 2656.6)


def test_snapping_reaches_a_fixed_point():
    """Extending the span pulls in segments that were outside it, and
    those can themselves be partially covered. One pass leaves a boundary
    mid-sentence; found that way on reel 12 of the second batch."""
    tx = _tx(_bound(timeline_start=10.0, timeline_end=18.0),
             _bound(timeline_start=17.5, timeline_end=26.0, resolve_item_id="u2"),
             _bound(timeline_start=25.5, timeline_end=34.0, resolve_item_id="u3"))
    start, end = snap_to_speech(12.0, 16.0, tx)
    assert (start, end) == (10.0, 34.0)
    validate_proposal([_moment(timeline_start=start, timeline_end=end)],
                      tx, 2656.6)


# ── A reel must be a conversation ────────────────────────────────────
#
# The captain, rejecting the first ten reels: "none of what you proposed
# is good - its mostly just a single person yapping and not really a
# convo".  They classified 'both speakers with real turns' as the
# CHECKABLE half, explicitly not taste.
#
# A single-speaker moment is a bad PICK, not an integrity failure.
# It is DROPPED with a reason, not raised - raising would discard the
# other nineteen good reels because one was a monologue.

def test_a_two_speaker_moment_is_a_conversation():
    """Both speakers with real turns - is_conversation returns None."""
    from library.tools.reel_proposal import is_conversation
    assert is_conversation(_moment(), _transcript()) is None


def test_a_single_speaker_moment_is_not_a_conversation():
    """The captain: 'its mostly just a single person yapping and not
    really a convo'.  is_conversation returns a reason string."""
    from library.tools.reel_proposal import is_conversation
    tx = _tx(_bound(speaker="Craig", timeline_start=10.0, timeline_end=26.0))
    reason = is_conversation(
        _moment(timeline_start=10.0, timeline_end=26.0), tx)
    assert reason is not None
    assert "single person yapping" in reason


def test_a_stray_word_does_not_count_as_a_turn():
    """A speaker with less than MIN_TURN_SECONDS of speech is a stray
    word from mic bleed, not a real conversational turn."""
    from library.tools.reel_proposal import is_conversation
    tx = _tx(
        _bound(speaker="Craig", timeline_start=10.0, timeline_end=26.0),
        # Akshita has only 1 second of speech - below MIN_TURN_SECONDS
        _bound(speaker="Akshita", timeline_start=20.0, timeline_end=21.0,
               resolve_item_id="uid-2"),
    )
    reason = is_conversation(
        _moment(timeline_start=10.0, timeline_end=26.0), tx)
    assert reason is not None
    assert "single person yapping" in reason


def test_post_bridge_drops_a_monologue_and_keeps_a_conversation():
    """post_bridge drops single-speaker moments with a reason rather
    than raising and discarding the whole batch."""
    from library.steps.step_3_04_select_reels.post_bridge import resolve
    tx = _transcript()
    llm_output = {
        "moments": [
            # This one covers both Craig (10-18) and Akshita (18.5-26) -
            # a real conversation.
            {"start": 10.0, "end": 26.0,
             "slug": "seo-vs-geo",
             "reason": "The clearest contrast."},
            # This one is Craig only (400-409) - a monologue.
            {"start": 400.0, "end": 409.0,
             "slug": "different-system",
             "reason": "A good aside."},
        ],
    }
    result = resolve(llm_output, {"timeline_transcript": tx})
    sel = result["reel_selection"]
    # The conversation survives
    assert len(sel["moments"]) == 1
    assert sel["moments"][0]["slug"] == "seo-vs-geo"
    # The monologue is dropped with the captain's words
    assert len(sel["dropped"]) == 1
    assert "single person yapping" in sel["dropped"][0]["reason"]


# ── Picture holes are drops, not raises ──────────────────────────────
#
# A hole in the captain's master is invisible to the model. Raising
# kills the whole batch for a defect the model had no way to avoid.

def test_a_moment_over_a_picture_hole_is_dropped():
    """overlaps_picture_hole returns a reason naming the hole's position."""
    from library.tools.reel_proposal import overlaps_picture_hole
    tx = _transcript()
    tx["derived_from"]["picture_holes"] = [(12.0, 13.5)]
    reason = overlaps_picture_hole(_moment(), tx)
    assert reason is not None
    assert "picture hole" in reason
    assert "play black" in reason


def test_a_moment_avoiding_holes_passes():
    from library.tools.reel_proposal import overlaps_picture_hole
    tx = _transcript()
    tx["derived_from"]["picture_holes"] = [(50.0, 52.0)]
    assert overlaps_picture_hole(_moment(), tx) is None


def test_post_bridge_drops_a_moment_over_a_hole():
    """post_bridge drops the moment over a hole and keeps the clean one."""
    from library.steps.step_3_04_select_reels.post_bridge import resolve
    tx = _transcript()
    tx["derived_from"]["picture_holes"] = [(12.0, 13.5)]
    llm_output = {
        "moments": [
            # This one overlaps the hole at 12-13.5
            {"start": 10.0, "end": 26.0,
             "slug": "seo-vs-geo",
             "reason": "The clearest contrast."},
            # This one is clear of the hole (400-409)
            {"start": 400.0, "end": 409.0,
             "slug": "different-system",
             "reason": "A good aside."},
        ],
    }
    result = resolve(llm_output, {"timeline_transcript": tx})
    sel = result["reel_selection"]
    # The moment over the hole is dropped
    hole_drops = [d for d in sel["dropped"] if "picture hole" in d["reason"]]
    assert len(hole_drops) == 1
    assert "play black" in hole_drops[0]["reason"]


def test_overlapping_moments_are_surfaced_rather_than_refused():
    """Overlap is REPORTED, not a rejection.

    Two moments drawing on one stretch is an editorial question - the
    same exchange can be worth two shorts - so 6f3ee03 stopped dropping
    the shorter one and started writing the overlap into both reasons
    where the captain can see it and decide.  These two tests still
    asserted the old refusal and had failed ever since; they now pin the
    behaviour the code actually has.
    """
    from library.tools.reel_proposal import validate_proposal
    tx = _transcript()
    # Add a fourth segment so both ranges are two-speaker and match whole segments
    tx["segments"].append({
        "speaker": "Akshita", "text": "Yes exactly.",
        "timeline_start": 409.5, "timeline_end": 420.0,
        "source_file": "/m/LC4930.MXF", "resolve_item_id": "uid-4",
        "source_start": 210.0, "source_end": 220.5
    })
    # m1: 10.0-26.0, m2: 18.5-420.0 -> overlap by 7.5s (18.5 to 26.0)
    m1 = _moment(number=1, slug="first", timeline_start=10.0, timeline_end=26.0)
    m2 = _moment(number=2, slug="second", timeline_start=18.5, timeline_end=420.0)
    validate_proposal([m1, m2], tx, 500.0)   # does not raise


def test_post_bridge_keeps_both_overlapping_moments_and_says_so():
    from library.steps.step_3_04_select_reels.post_bridge import resolve
    tx = _transcript()
    tx["segments"].append({
        "speaker": "Akshita", "text": "Yes exactly.",
        "timeline_start": 409.5, "timeline_end": 420.0,
        "source_file": "/m/LC4930.MXF", "resolve_item_id": "uid-4",
        "source_start": 210.0, "source_end": 220.5
    })
    # Both spans are two-speaker conversations, and the second contains
    # the first entirely.
    llm_output = {
        "moments": [
            {"start": 10.0, "end": 26.0, "slug": "shorter", "reason": "Shorter clip"},
            {"start": 10.0, "end": 420.0, "slug": "longer", "reason": "Longer clip"},
        ]
    }
    result = resolve(llm_output, {"timeline_transcript": tx})
    sel = result["reel_selection"]
    assert [m["slug"] for m in sel["moments"]] == ["shorter", "longer"]
    assert sel["dropped"] == []
    # Each one names the other, with the shared span, in its reason.
    assert "OVERLAP: shares 16.0s (10.0-26.0s) with longer" in sel["moments"][0]["reason"]
    assert "OVERLAP: shares 16.0s (10.0-26.0s) with shorter" in sel["moments"][1]["reason"]


# ── The closing CTA, from anywhere in the episode ────────────────────
#
# A moment is a BODY window plus, optionally, one CTA range taken from
# anywhere else in the episode.  The captain's format ends on a spoken
# call to action and this episode says about six of them, so while a
# moment was one contiguous window the two requirements could not both
# be met.  What is checked here is that the closer is REAL - real
# seconds, real speech - and never that it is a GOOD one.


def _cta_transcript():
    """A transcript with a body exchange and, far away, a spoken CTA."""
    return {
        "derived_from": {"duration_seconds": 1200.0},
        "segments": [
            {"speaker": "Craig", "text": "So what actually changes for them?",
             "timeline_start": 600.0, "timeline_end": 612.0,
             "source_file": "/m/LCATL0013.MXF", "resolve_item_id": "uid-b1",
             "source_start": 10.0, "source_end": 22.0},
            {"speaker": "Akshita", "text": "The whole retrieval path changes.",
             "timeline_start": 612.5, "timeline_end": 660.0,
             "source_file": "/m/LC4932.MXF", "resolve_item_id": "uid-b2",
             "source_start": 30.0, "source_end": 77.5},
            {"speaker": "Craig",
             "text": "jump on lucycontent.com take it dm us "
                     "it's also in the link below",
             "timeline_start": 468.06, "timeline_end": 476.5,
             "source_file": "/m/LCATL0013.MXF", "resolve_item_id": "uid-cta",
             "source_start": 300.0, "source_end": 308.44},
        ],
    }


def _with_cta(number=1, start=600.0, end=660.0,
              cta=(468.06, 476.5), slug="retrieval"):
    from library.tools.reel_proposal import CallToAction
    return ReelMoment(
        number=number, slug=slug, reason="a complete exchange",
        timeline_start=start, timeline_end=end,
        call_to_action=CallToAction(timeline_start=cta[0],
                                    timeline_end=cta[1]) if cta else None)


def test_a_moment_carries_a_cta_from_anywhere_in_the_episode():
    """The closer is EARLIER on the master than the body it closes."""
    moment = _with_cta()
    validate_proposal([moment], _cta_transcript(), 1200.0)
    assert moment.call_to_action.timeline_start < moment.timeline_start


def test_a_moment_with_no_cta_is_still_valid():
    """The field is additive - a reel with no declared closer ends where
    its body ends, exactly as every reel did before."""
    validate_proposal([_with_cta(cta=None)], _cta_transcript(), 1200.0)


def test_two_reels_may_close_on_the_same_cta():
    """Six spoken CTAs closing sixteen reels is the point of the
    mechanism. Two reels may not share a BODY; they may share a CLOSER."""
    shared = (468.06, 476.5)
    first = _with_cta(number=1, start=600.0, end=612.0, cta=shared)
    second = _with_cta(number=2, start=612.5, end=660.0, cta=shared,
                       slug="other")
    validate_proposal([first, second], _cta_transcript(), 1200.0)
    assert (first.call_to_action.master_range
            == second.call_to_action.master_range)


def test_a_cta_where_nobody_speaks_is_refused():
    """The rule that stops a CTA being authored, templated or padded."""
    with pytest.raises(ProposalError, match="genuinely SPOKEN"):
        validate_proposal([_with_cta(cta=(900.0, 910.0))],
                          _cta_transcript(), 1200.0)


def test_a_cta_outside_the_timeline_is_refused():
    with pytest.raises(ProposalError, match="outside the timeline"):
        validate_proposal([_with_cta(cta=(1300.0, 1310.0))],
                          _cta_transcript(), 1200.0)


def test_a_cta_that_is_not_a_range_is_refused():
    with pytest.raises(ProposalError, match="not a range"):
        validate_proposal([_with_cta(cta=(476.5, 468.06))],
                          _cta_transcript(), 1200.0)


def test_a_cta_inside_its_own_body_is_refused():
    """The reel would play those seconds twice."""
    with pytest.raises(ProposalError, match="play those seconds twice"):
        validate_proposal([_with_cta(cta=(612.5, 660.0))],
                          _cta_transcript(), 1200.0)


def test_a_cta_that_opens_mid_sentence_is_refused():
    with pytest.raises(ProposalError, match="close\\s+mid-sentence"):
        validate_proposal([_with_cta(cta=(470.0, 476.5))],
                          _cta_transcript(), 1200.0)


def test_nothing_scores_or_ranks_a_cta():
    """A passage that says nothing CTA-like passes, because whether a
    closer is a good one is taste (AGENTS.md 10.5) and this module has
    no opinion. There is no keyword list, no pitch floor, no cutoff."""
    validate_proposal([_with_cta(start=468.06, end=476.5,
                                 cta=(600.0, 612.0))],
                      _cta_transcript(), 1200.0)


def test_a_cta_reads_its_words_back_off_the_transcript():
    """MEASURED, never asked of the model - a model that had to restate
    the words could restate them wrongly, and the whole guarantee is that
    the closer is speech the episode really contains."""
    enriched = enrich(_with_cta(), _cta_transcript())
    assert "lucycontent.com" in enriched.call_to_action.text
    assert enriched.call_to_action.speaker == "Craig"


def test_a_cta_survives_being_written_and_read_back(tmp_path):
    """`from_dict` drops any key it does not name, so a closer that does
    not round-trip never reaches the build path at all."""
    path = tmp_path / "reel_proposals_v2.json"
    write_proposal(path, [enrich(_with_cta(), _cta_transcript())],
                   _cta_transcript())
    back = read_proposal(path)[0]
    assert back.call_to_action is not None
    assert back.call_to_action.timeline_start == pytest.approx(468.06)
    assert back.call_to_action.timeline_end == pytest.approx(476.5)
    assert "lucycontent.com" in back.call_to_action.text


def test_a_reel_with_no_cta_round_trips_as_none(tmp_path):
    path = tmp_path / "reel_proposals_v2.json"
    write_proposal(path, [_with_cta(cta=None)], _cta_transcript())
    assert read_proposal(path)[0].call_to_action is None


def test_total_duration_counts_the_closer_and_duration_does_not():
    moment = _with_cta()
    assert moment.duration == pytest.approx(60.0)
    assert moment.total_duration == pytest.approx(60.0 + 8.44)


def test_the_review_surface_names_the_closer():
    """The captain reads this. A reel that plays eight seconds nobody can
    see in the timecodes is a surprise in Resolve."""
    rendered = render_for_review([enrich(_with_cta(), _cta_transcript())])
    assert "closes on" in rendered
    assert "lucycontent.com" in rendered


def test_a_cta_over_a_picture_hole_is_a_bad_pick_not_a_raise():
    """A closer plays black over a hole exactly as the body would."""
    from library.tools.reel_proposal import overlaps_picture_hole
    transcript = _cta_transcript()
    transcript["derived_from"]["picture_holes"] = [[470.0, 474.0]]
    reason = overlaps_picture_hole(_with_cta(), transcript)
    assert reason and "call to action" in reason


# ── Publishing the plan the builder reads ────────────────────────────
#
# `build-reels` read `pipeline_output/review/reel_proposals_v2.json` and
# NOTHING in this repository wrote it: every batch of proposals reached
# that path by hand, which is why a round where the selector was never
# re-run looked exactly like one where it was.

def _project_with_step_output(tmp_path, moments, transcript=None):
    root = tmp_path / "project"
    step = root / "pipeline_output" / "steps" / "3_04_select_reels"
    step.mkdir(parents=True)
    (step / "output.json").write_text(json.dumps(
        {"reel_selection": {"moments": moments}}))
    scratch = root / "pipeline_output" / "scratch" / "timeline_transcript"
    scratch.mkdir(parents=True)
    (scratch / "transcript.json").write_text(
        json.dumps(transcript if transcript is not None else _transcript()))
    return root


def _emitted(**kw):
    base = dict(number=1, slug="seo-vs-geo", reason="The clearest contrast.",
                timeline_start=10.0, timeline_end=26.0)
    base.update(kw)
    return base


def test_the_step_output_becomes_the_file_the_builder_reads(tmp_path):
    from library.tools.reel_proposal import (proposal_path,
                                             write_from_step_output)
    root = _project_with_step_output(tmp_path, [_emitted()])
    path = write_from_step_output(root)
    assert path == proposal_path(root)
    written = read_proposal(path)
    assert [m.slug for m in written] == ["seo-vs-geo"]
    assert written[0].approval is Approval.PROPOSED


def test_a_published_moment_carries_its_borrowed_closer(tmp_path):
    """The CTA range survives the trip through the review file, because
    that file is what `build-reels` reads back."""
    from library.tools.reel_proposal import write_from_step_output

    root = _project_with_step_output(
        tmp_path, [_emitted(call_to_action={"timeline_start": 400.0,
                                            "timeline_end": 409.0,
                                            "note": "the closer"})])
    written = read_proposal(write_from_step_output(root))
    assert written[0].call_to_action is not None
    assert written[0].call_to_action.timeline_start == 400.0
    assert written[0].call_to_action.timeline_end == 409.0


def test_publishing_refuses_to_discard_the_captains_answer(tmp_path):
    from library.tools.reel_proposal import write_from_step_output
    root = _project_with_step_output(tmp_path, [_emitted()])
    path = write_from_step_output(root)
    ruled = read_proposal(path)
    write_proposal(path, [replace(ruled[0], approval=Approval.REJECTED)],
                   _transcript())

    with pytest.raises(ProposalError) as excinfo:
        write_from_step_output(root)
    assert "ruled on" in str(excinfo.value)
    assert "seo-vs-geo" in str(excinfo.value)
    # and the answer is still there
    assert read_proposal(path)[0].approval is Approval.REJECTED

    write_from_step_output(root, force=True)
    assert read_proposal(path)[0].approval is Approval.PROPOSED


def test_publishing_before_the_step_has_run_says_so(tmp_path):
    from library.tools.reel_proposal import write_from_step_output
    root = tmp_path / "project"
    (root / "pipeline_output").mkdir(parents=True)
    with pytest.raises(ProposalError) as excinfo:
        write_from_step_output(root)
    assert "step 3.4 has not run" in str(excinfo.value)


def test_a_borrowed_closer_reports_the_speech_its_text_omits():
    """The CTA has the body's old blind spot, and it matters more here.

    `enrich_call_to_action` measures `text` from bound segments, so a
    straddling segment inside the closer is not in it - and the reel
    plays it. The claim being made about a CTA is that it is a COMPLETE
    invitation, so a closer whose finishing words are only in a straddler
    is not atomic however clean its text reads.
    """
    from library.tools.reel_proposal import (CallToAction,
                                             enrich_call_to_action)
    tx = _tx(_bound(speaker="Craig", text="we'd love for you to",
                    timeline_start=400.0, timeline_end=404.0),
             _bound(speaker="Craig", timeline_start=404.1, timeline_end=417.0,
                    resolve_item_id=None,
                    text="jump on lucycontent.com it's also in the link below",
                    words=[{"word": "jump", "start": 404.1, "end": 404.4},
                           {"word": "on", "start": 404.5, "end": 404.7}]))
    enriched = enrich_call_to_action(
        CallToAction(timeline_start=400.0, timeline_end=417.0), tx)

    assert enriched.text == "we'd love for you to"
    assert enriched.straddling_within[0]["text"] == "jump on"
    assert enriched.straddling_within[0]["word_count"] == 2


def test_a_closer_with_no_straddling_speech_reports_none():
    from library.tools.reel_proposal import (CallToAction,
                                             enrich_call_to_action)
    tx = _tx(_bound(speaker="Craig", text="go check it out",
                    timeline_start=400.0, timeline_end=404.0))
    enriched = enrich_call_to_action(
        CallToAction(timeline_start=400.0, timeline_end=404.0), tx)
    assert enriched.text == "go check it out"
    assert enriched.straddling_within == ()
