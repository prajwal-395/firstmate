"""Stored proposals are snapped out of word interiors at BUILD time
(`snap_moment_to_speech`), body and CTA alike, without reselecting.

History: docs/evidence/reel_boundary_snap.md.
"""

from library.tools.reel_proposal import (
    Approval,
    CallToAction,
    ReelMoment,
    snap_moment_to_speech,
)


def _bound(**kw):
    base = dict(speaker="Craig", text="a line", timeline_start=10.0,
                timeline_end=18.0, source_file="/m/a.MXF",
                source_start=100.0, source_end=108.0,
                resolve_item_id="uid-1")
    base.update(kw)
    return base


def _tx(*segments):
    return {"segments": list(segments)}


def _reel5_transcript():
    """The reel-5 shape, verbatim from the #671 drawer test: Akshita's
    bound row ends at 413.85s and Craig's straddling word 'about'
    (412.77-414.03s) overlaps it, invisible to segment arithmetic."""
    return _tx(
        _bound(speaker="Craig", text="the setup", timeline_start=342.038,
               timeline_end=350.0, resolve_item_id="uid-0",
               words=[{"word": "setup", "start": 342.1, "end": 342.5}]),
        _bound(speaker="Akshita", text="recommend you or your brand.",
               timeline_start=412.63, timeline_end=413.85,
               resolve_item_id="uid-a",
               words=[{"word": "recommend", "start": 412.63, "end": 412.99},
                      {"word": "you", "start": 413.01, "end": 413.17},
                      {"word": "or", "start": 413.37, "end": 413.43},
                      {"word": "your", "start": 413.45, "end": 413.57},
                      {"word": "brand.", "start": 413.59, "end": 413.85}]),
        _bound(speaker="Craig", text="about",
               timeline_start=412.77, timeline_end=414.03,
               resolve_item_id=None,
               words=[{"word": "about", "start": 412.77, "end": 414.03}]),
        _bound(speaker="Craig", text="a lot of times",
               timeline_start=414.05, timeline_end=420.0,
               resolve_item_id="uid-c",
               words=[{"word": "times", "start": 414.23, "end": 414.33}]),
    )


def _moment(start, end, cta=None):
    return ReelMoment(
        number=5, slug="schema-and-knowledge-graphs",
        reason="a complete exchange",
        timeline_start=start, timeline_end=end,
        approval=Approval.APPROVED,
        call_to_action=(CallToAction(timeline_start=cta[0],
                                     timeline_end=cta[1])
                        if cta else None))


def test_build_time_repair_moves_a_stored_end_out_of_a_word():
    """The defect, exactly: the stored end 413.851s sits inside Craig's
    straddling 'about', and the repair moves it to the word's end -
    the same span a fresh proposal would have stored."""
    repaired, moves = snap_moment_to_speech(
        _moment(342.038, 413.851), _reel5_transcript())
    assert (repaired.timeline_start, repaired.timeline_end) == (
        342.038, 414.03)
    assert moves == [{"boundary": "body_end", "was": 413.851,
                      "now": 414.03, "through": "about"}]
    # Idempotent: the second pass finds no boundary inside any word.
    again, moves = snap_moment_to_speech(repaired, _reel5_transcript())
    assert again is repaired
    assert moves == []


def test_repair_snaps_a_stored_cta_without_reselecting():
    """The closer is repaired the same way the drawer draws one - its
    range moves off the word edge, and nothing else about the moment
    (which take, which CTA, whose approval) changes."""
    tx = _tx(
        _bound(speaker="Craig", text="a borrowed close",
               timeline_start=468.0, timeline_end=476.5,
               resolve_item_id="uid-x",
               words=[{"word": "go", "start": 476.2, "end": 477.1}]),
    )
    moment = _moment(342.038, 350.0, cta=(468.06, 476.5))
    repaired, moves = snap_moment_to_speech(moment, tx)
    assert (repaired.timeline_start, repaired.timeline_end) == (
        342.038, 350.0)
    assert (repaired.call_to_action.timeline_start,
            repaired.call_to_action.timeline_end) == (468.0, 477.1)
    assert moves == [{"boundary": "cta_start", "was": 468.06,
                      "now": 468.0, "through": None},
                     {"boundary": "cta_end", "was": 476.5,
                      "now": 477.1, "through": "go"}]
    assert repaired.number == moment.number
    assert repaired.slug == moment.slug
    assert repaired.approval is Approval.APPROVED


def test_large_cta_end_cascade_is_reported_but_held():
    """A 50ms overlap with the next speaker cannot add their whole
    sentence to an approved CTA when the cascade exceeds the decision
    threshold."""
    from library.tools.reel_proposal import (
        decision_lines, preview_snap, render_snap_preview,
    )

    tx = _tx(
        _bound(speaker="Akshita", text="The link's bio.",
               timeline_start=340.54, timeline_end=341.38,
               resolve_item_id="uid-cta",
               words=[{"word": "The", "start": 340.54, "end": 340.66},
                      {"word": "link's", "start": 340.68, "end": 341.02},
                      {"word": "bio.", "start": 341.05, "end": 341.38}]),
        _bound(speaker="Craig", text="So what we're hearing",
               timeline_start=341.98, timeline_end=349.54,
               resolve_item_id="uid-next",
               words=[{"word": "So", "start": 341.98, "end": 342.18},
                      {"word": "what", "start": 342.20, "end": 342.48},
                      {"word": "we're", "start": 342.50, "end": 342.78},
                      {"word": "hearing", "start": 342.80, "end": 343.24}]),
    )
    moment = _moment(10.0, 18.0, cta=(328.608, 342.03))

    repaired, moves = snap_moment_to_speech(moment, tx)

    assert repaired.call_to_action.timeline_end == 342.03
    assert len(moves) == 1
    candidate = moves[0]
    assert candidate["boundary"] == "cta_end"
    assert (candidate["was"], candidate["now"]) == (342.03, 349.54)
    assert candidate["held_for_decision"] is True
    assert "approved edge stays in force" in candidate["why"]

    report = preview_snap([moment], tx)
    assert report["moved"] == 0
    assert report["flagged"] == 1
    reported = report["moments"][0]["moves"][0]
    assert reported["held_for_decision"] is True
    assert reported["needs_decision"] is True
    assert "HELD FOR DECISION" in render_snap_preview(report)
    lines = decision_lines(5, candidate, tx)
    assert "candidate 342.030s -> 349.540s (+7.510s)" in lines[0]
    assert "approved CTA end remains at 342.030s" in lines[1]
