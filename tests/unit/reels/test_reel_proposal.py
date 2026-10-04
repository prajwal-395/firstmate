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
    NotApproved,
    ProposalError,
    ReelMoment,
    approved_only,
    assert_approved,
    enrich,
    held_back,
    read_proposal,
    reel_timeline_name,
    slugify,
    validate_proposal,
    write_proposal,
    snap_to_speech,
    straddling_within,
    duplicate_takes,
)
from library.tools.reel_proposal import (
    CallToAction,
    snap_moment_to_speech,
)
from library.tools.reel_proposal import (
    decision_lines,
    preview_snap,
    render_snap_preview,
)
from types import SimpleNamespace
from library.tools import reel_look, subtitle_coverage
from library.tools.reel_build import placements
from library.tools.reel_proposal import (
    partial_overlaps,
)
from library.tools.sub_block_anchor import resolve_anchor
from dataclasses import dataclass
from library.tools.manifest_validator import MIN_CAPTION_DISPLAY_SECONDS
from library.tools.reel_spine import spine_for_reel
from pathlib import Path
from library.steps.step_4_05_render_subtitles import generate_remotion_props
from library.tools import (reel_build, timeline_transcript, transcript_corrections)
from library.tools import reel_conformance_verifier as verifier


def _transcript(**overrides):
    doc = {
        "derived_from": {"timeline": "GEO Podcast - Synced",
                         "duration_seconds": 2656.6},
        "segments": [
            {"speaker": "SpeakerTwo", "text": "SEO is about convincing a crawler.",
             "timeline_start": 10.0, "timeline_end": 18.0,
             "source_file": "/m/LCATL0011.MXF", "resolve_item_id": "uid-1",
             "source_start": 100.0, "source_end": 108.0},
            {"speaker": "SpeakerOne", "text": "GEO is about comprehension.",
             "timeline_start": 18.5, "timeline_end": 26.0,
             "source_file": "/m/LC4930.MXF", "resolve_item_id": "uid-2",
             "source_start": 200.0, "source_end": 207.5},
            {"speaker": "SpeakerTwo", "text": "Totally different system.",
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

def test_only_an_approved_moment_passes_the_gate():
    """The DEFAULT (proposed) state fails, as does rejected; approved is
    returned unchanged. This is the test that makes the gate real."""
    with pytest.raises(NotApproved, match="still PROPOSED"):
        assert_approved(_moment())
    rejected = _moment(approval=Approval.REJECTED,
                       approval_note="covered better later")
    with pytest.raises(NotApproved) as excinfo:
        assert_approved(rejected)
    assert "REJECTED" in str(excinfo.value)
    assert "covered better later" in str(excinfo.value)
    approved = _moment(approval=Approval.APPROVED)
    assert assert_approved(approved) is approved
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
    # an empty slug is visibly untitled, not blank
    assert slugify("") == "untitled"
    assert reel_timeline_name(1, "!!!") == "Reel 01 - untitled"


# ── A proposal must be real ──────────────────────────────────────────

def test_an_unreal_proposal_is_refused_by_name():
    table = [
        ([_moment(timeline_start=9000.0, timeline_end=9020.0)],
         _transcript(), 2656.6, "outside the timeline"),
        # the check that stops a model inventing a timecode
        ([_moment(timeline_start=1000.0, timeline_end=1030.0)],
         _transcript(), 2656.6, "invented timecode"),
        ([_moment(timeline_start=14.0, timeline_end=30.0)],
         _tx(_bound(timeline_start=10.0, timeline_end=18.0)),
         2656.6, "mid-sentence"),
        # a CTA inside its own body would play those seconds twice
        ([_with_cta(cta=(612.5, 660.0))], _cta_transcript(), 1200.0,
         "play those seconds twice"),
        ([_with_cta(cta=(470.0, 476.5))], _cta_transcript(), 1200.0,
         "close\\s+mid-sentence"),
    ]
    for moments, transcript, duration, says in table:
        with pytest.raises(ProposalError, match=says):
            validate_proposal(moments, transcript, duration)


def test_enrich_attaches_speakers_and_ground_truth():
    enriched = enrich(_moment(), _transcript())
    assert enriched.speakers == ("SpeakerTwo", "SpeakerOne")
    assert "SEO is about convincing" in enriched.transcript_preview
    assert [s["source_file"] for s in enriched.source_spans] == [
        "/m/LCATL0011.MXF", "/m/LC4930.MXF"]


# ── The review surface ───────────────────────────────────────────────

def test_a_proposal_round_trips_with_the_captains_decision(tmp_path):
    path = tmp_path / "reel_proposals.json"
    write_proposal(path, [enrich(_moment(), _transcript())], _transcript())

    data = json.loads(path.read_text())
    assert data["moments"][0]["approval"] == "proposed"
    assert "timeline_name" not in data["moments"][0]  # derived, not stored

    # the captain edits the file
    data["moments"][0]["approval"] = "approved"
    data["moments"][0]["approval_note"] = "yes, lead with this"
    path.write_text(json.dumps(data))

    loaded = read_proposal(path)
    assert loaded[0].approval is Approval.APPROVED
    assert assert_approved(loaded[0]) is loaded[0]
    # a foreign document is refused
    path = tmp_path / "p.json"
    path.write_text(json.dumps({"format": "something/else", "moments": []}))
    with pytest.raises(ProposalError):
        read_proposal(path)


# ── A reel never opens or closes mid-sentence ────────────────────────
#
# 63 of the field test's 906 segments straddle a cut. A boundary landing
# on one takes half a sentence, and in practice a straddling segment is
# often WhisperX bridging a silent gap - SpeakerTwo's 22.0-47.2s runs 25
# seconds across a stretch where his track has no clip at all.

def _bound(**kw):
    base = dict(speaker="SpeakerTwo", text="a line", timeline_start=10.0,
                timeline_end=18.0, source_file="/m/a.MXF",
                source_start=100.0, source_end=108.0,
                resolve_item_id="uid-1")
    base.update(kw)
    return base


def _tx(*segments):
    return {"derived_from": {}, "segments": list(segments)}


def test_snap_keeps_an_end_in_clean_silence():
    """A tail breath is not a cut segment: pulling it back to the last
    word deletes the room an end animation needs, and no segment is cut
    by keeping it. Measured 2026-09-11: a closer end at 342.03s, in the
    silence after "The link's in our bio." and before the next speech,
    was pulled back to 341.27s, playing the TV switch-off over her last
    words instead of after them."""
    tx = _tx(_bound(timeline_start=10.0, timeline_end=18.0),
             _bound(timeline_start=18.5, timeline_end=26.0, resolve_item_id="uid-2"))
    assert snap_to_speech(10.0, 30.0, tx) == (10.0, 30.0)


def test_a_straddler_the_span_touches_still_does_not_widen_it():
    """A straddler that OVERLAPS the span, the case that bit.

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


def test_enrich_carries_the_straddling_report_and_it_round_trips():
    tx = _tx(_bound(timeline_start=10.0, timeline_end=18.0),
             _bound(timeline_start=16.0, timeline_end=47.2,
                    resolve_item_id=None, speaker="SpeakerTwo",
                    words=[{"word": "well", "start": 16.5, "end": 17.0}]))
    moment = enrich(_moment(timeline_start=10.0, timeline_end=18.0), tx)
    assert moment.straddling_within[0]["text"] == "well"
    assert "well" not in moment.transcript_preview
    body = moment.as_dict()
    assert body["straddling_within"][0]["word_count"] == 1
    assert ReelMoment.from_dict(body).straddling_within == moment.straddling_within
    # Otherwise a preview shows words the reel does not contain, and
    # names a speaker who is not in it.
    tx = _tx(_bound(speaker="SpeakerOne", text="in the reel",
                    timeline_start=10.0, timeline_end=18.0),
             _bound(speaker="SpeakerTwo", text="bridged across a silent gap",
                    timeline_start=5.0, timeline_end=40.0,
                    resolve_item_id=None))
    enriched = enrich(_moment(timeline_start=10.0, timeline_end=18.0), tx)
    assert enriched.speakers == ("SpeakerOne",)
    assert "bridged" not in enriched.transcript_preview


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


def test_snap_moves_an_end_out_of_a_straddling_word():
    """Reel 5 of the rebuild (2026-09-08) ended at 413.851s - the end of
    SpeakerOne's bound row - and inside SpeakerTwo's straddling word 'about'
    (412.77-414.03s). Segment snapping cannot see words, and straddlers
    are excluded from its arithmetic, so the raw end survived and the
    build failed F8 on it. The drawer snaps OUT of the word, to its end.
    """
    tx = _tx(
        _bound(speaker="SpeakerTwo", text="the setup", timeline_start=342.038,
               timeline_end=350.0, resolve_item_id="uid-0",
               words=[{"word": "setup", "start": 342.1, "end": 342.5}]),
        _bound(speaker="SpeakerOne", text="recommend you or your brand.",
               timeline_start=412.63, timeline_end=413.85,
               resolve_item_id="uid-a",
               words=[{"word": "recommend", "start": 412.63, "end": 412.99},
                      {"word": "you", "start": 413.01, "end": 413.17},
                      {"word": "or", "start": 413.37, "end": 413.43},
                      {"word": "your", "start": 413.45, "end": 413.57},
                      {"word": "brand.", "start": 413.59, "end": 413.85}]),
        _bound(speaker="SpeakerTwo", text="about",
               timeline_start=412.77, timeline_end=414.03,
               resolve_item_id=None,
               words=[{"word": "about", "start": 412.77, "end": 414.03}]),
        _bound(speaker="SpeakerTwo", text="a lot of times",
               timeline_start=414.05, timeline_end=420.0,
               resolve_item_id="uid-c",
               words=[{"word": "times", "start": 414.23, "end": 414.33}]),
    )
    assert snap_to_speech(342.038, 413.851, tx) == (342.038, 414.03)


# ── Repeated takes ───────────────────────────────────────────────────
#
# The captain warned the rough cut still holds several takes of the same
# lines. It turned out to be four of the first ten proposals, which is
# why this is reported per moment rather than left as a footnote.

def _words(text, start, step=0.4):
    return [{"word": w, "start": start + i * step, "end": start + (i + 1) * step}
            for i, w in enumerate(text.split())]


def _spoken(text, start, end, uid="uid-1", speaker="SpeakerOne"):
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


def test_distinct_speech_is_not_a_retake():
    tx = _tx(_spoken("seo is about convincing an algorithm to rank your "
                     "page higher", 10.0, 20.0),
             _spoken("geo is about whether artificial intelligence "
                     "comprehends your company", 21.0, 30.0, uid="uid-2"))
    assert duplicate_takes(10.0, 30.0, tx) == []
    # It is often a bridged silence, and its text is not reliably in
    # the reel at all.
    tx = _tx(_spoken("we ran an audit on a client for their seo team",
                     10.0, 20.0),
             dict(_spoken("we ran an audit on a client for their seo team",
                          21.0, 30.0, uid="uid-2"), resolve_item_id=None))
    assert duplicate_takes(10.0, 30.0, tx) == []


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


def test_a_stray_word_does_not_count_as_a_turn():
    """A speaker with less than MIN_TURN_SECONDS of speech is a stray
    word from mic bleed, not a real conversational turn."""
    from library.tools.reel_proposal import is_conversation
    tx = _tx(
        _bound(speaker="SpeakerTwo", timeline_start=10.0, timeline_end=26.0),
        # SpeakerOne has only 1 second of speech - below MIN_TURN_SECONDS
        _bound(speaker="SpeakerOne", timeline_start=20.0, timeline_end=21.0,
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
            # This one covers both SpeakerTwo (10-18) and SpeakerOne (18.5-26) -
            # a real conversation.
            {"start": 10.0, "end": 26.0,
             "slug": "seo-vs-geo",
             "reason": "The clearest contrast."},
            # This one is SpeakerTwo only (400-409) - a monologue.
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


def test_post_bridge_keeps_both_overlapping_moments_and_says_so():
    from library.steps.step_3_04_select_reels.post_bridge import resolve
    tx = _transcript()
    tx["segments"].append({
        "speaker": "SpeakerOne", "text": "Yes exactly.",
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
            {"speaker": "SpeakerTwo", "text": "So what actually changes for them?",
             "timeline_start": 600.0, "timeline_end": 612.0,
             "source_file": "/m/LCATL0013.MXF", "resolve_item_id": "uid-b1",
             "source_start": 10.0, "source_end": 22.0},
            {"speaker": "SpeakerOne", "text": "The whole retrieval path changes.",
             "timeline_start": 612.5, "timeline_end": 660.0,
             "source_file": "/m/LC4932.MXF", "resolve_item_id": "uid-b2",
             "source_start": 30.0, "source_end": 77.5},
            {"speaker": "SpeakerTwo",
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


def test_a_cta_reads_its_words_back_off_the_transcript():
    """MEASURED, never asked of the model - a model that had to restate
    the words could restate them wrongly, and the whole guarantee is that
    the closer is speech the episode really contains."""
    enriched = enrich(_with_cta(), _cta_transcript())
    assert "lucycontent.com" in enriched.call_to_action.text
    assert enriched.call_to_action.speaker == "SpeakerTwo"


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
    tx = _tx(_bound(speaker="SpeakerTwo", text="we'd love for you to",
                    timeline_start=400.0, timeline_end=404.0),
             _bound(speaker="SpeakerTwo", timeline_start=404.1, timeline_end=417.0,
                    resolve_item_id=None,
                    text="jump on lucycontent.com it's also in the link below",
                    words=[{"word": "jump", "start": 404.1, "end": 404.4},
                           {"word": "on", "start": 404.5, "end": 404.7}]))
    enriched = enrich_call_to_action(
        CallToAction(timeline_start=400.0, timeline_end=417.0), tx)

    assert enriched.text == "we'd love for you to"
    assert enriched.straddling_within[0]["text"] == "jump on"
    assert enriched.straddling_within[0]["word_count"] == 2


# --------------------------------------------------------------------------
# From test_reel_proposal_build_time_snap.py
#
# Stored proposals are snapped out of word interiors at BUILD time
# (`snap_moment_to_speech`), body and CTA alike, without reselecting.
#
# History: docs/evidence/reel_boundary_snap.md.

def _tx_2(*segments):
    return {"segments": list(segments)}


def _reel5_transcript():
    """The reel-5 shape, verbatim from the #671 drawer test: SpeakerOne's
    bound row ends at 413.85s and SpeakerTwo's straddling word 'about'
    (412.77-414.03s) overlaps it, invisible to segment arithmetic."""
    return _tx_2(
        _bound(speaker="SpeakerTwo", text="the setup", timeline_start=342.038,
               timeline_end=350.0, resolve_item_id="uid-0",
               words=[{"word": "setup", "start": 342.1, "end": 342.5}]),
        _bound(speaker="SpeakerOne", text="recommend you or your brand.",
               timeline_start=412.63, timeline_end=413.85,
               resolve_item_id="uid-a",
               words=[{"word": "recommend", "start": 412.63, "end": 412.99},
                      {"word": "you", "start": 413.01, "end": 413.17},
                      {"word": "or", "start": 413.37, "end": 413.43},
                      {"word": "your", "start": 413.45, "end": 413.57},
                      {"word": "brand.", "start": 413.59, "end": 413.85}]),
        _bound(speaker="SpeakerTwo", text="about",
               timeline_start=412.77, timeline_end=414.03,
               resolve_item_id=None,
               words=[{"word": "about", "start": 412.77, "end": 414.03}]),
        _bound(speaker="SpeakerTwo", text="a lot of times",
               timeline_start=414.05, timeline_end=420.0,
               resolve_item_id="uid-c",
               words=[{"word": "times", "start": 414.23, "end": 414.33}]),
    )


def _moment_2(start, end, cta=None):
    return ReelMoment(
        number=5, slug="schema-and-knowledge-graphs",
        reason="a complete exchange",
        timeline_start=start, timeline_end=end,
        approval=Approval.APPROVED,
        call_to_action=(CallToAction(timeline_start=cta[0],
                                     timeline_end=cta[1])
                        if cta else None))


def test_build_time_repair_moves_a_stored_end_out_of_a_word():
    """The defect, exactly: the stored end 413.851s sits inside SpeakerTwo's
    straddling 'about', and the repair moves it to the word's end -
    the same span a fresh proposal would have stored."""
    repaired, moves = snap_moment_to_speech(
        _moment_2(342.038, 413.851), _reel5_transcript())
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
    tx = _tx_2(
        _bound(speaker="SpeakerTwo", text="a borrowed close",
               timeline_start=468.0, timeline_end=476.5,
               resolve_item_id="uid-x",
               words=[{"word": "go", "start": 476.2, "end": 477.1}]),
    )
    moment = _moment_2(342.038, 350.0, cta=(468.06, 476.5))
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

    tx = _tx_2(
        _bound(speaker="SpeakerOne", text="The link's bio.",
               timeline_start=340.54, timeline_end=341.38,
               resolve_item_id="uid-cta",
               words=[{"word": "The", "start": 340.54, "end": 340.66},
                      {"word": "link's", "start": 340.68, "end": 341.02},
                      {"word": "bio.", "start": 341.05, "end": 341.38}]),
        _bound(speaker="SpeakerTwo", text="So what we're hearing",
               timeline_start=341.98, timeline_end=349.54,
               resolve_item_id="uid-next",
               words=[{"word": "So", "start": 341.98, "end": 342.18},
                      {"word": "what", "start": 342.20, "end": 342.48},
                      {"word": "we're", "start": 342.50, "end": 342.78},
                      {"word": "hearing", "start": 342.80, "end": 343.24}]),
    )
    moment = _moment_2(10.0, 18.0, cta=(328.608, 342.03))

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


# --------------------------------------------------------------------------
# From test_snap_preview.py
#
# The snap preview reports, per moment, how far each boundary would move
# and what words that pulls in, flagging moves over the decision bar.
#
# History: docs/evidence/reel_boundary_snap.md.

def _w(word, start, end):
    return {"word": word, "start": start, "end": end, "timed": True}


def _seg(text, start, end, words, bound=True):
    return {"speaker": "Host", "text": text,
            "timeline_start": start, "timeline_end": end,
            "source_file": "/m/a.MXF",
            "source_start": 100.0, "source_end": 100.0 + (end - start),
            "resolve_item_id": ("uid" if bound else None),
            "words": words}


def _cascade_transcript():
    """Three bound segments overlapping by ASR jitter, like the
    canary's: each starts just before the previous one ends."""
    return {"segments": [
        _seg("alpha beta", 10.0, 20.0,
             [_w("alpha", 10.5, 11.0), _w("beta", 12.0, 12.5)]),
        _seg("gamma delta", 19.5, 30.0,
             [_w("gamma", 20.0, 20.5), _w("delta", 22.0, 22.5)]),
        _seg("epsilon zeta", 29.5, 40.0,
             [_w("epsilon", 31.0, 31.5), _w("zeta", 33.0, 33.5)]),
    ]}


def _moment_3(number, start, end, cta=None):
    return ReelMoment(
        number=number, slug=f"reel-{number:02d}",
        reason="a complete exchange",
        timeline_start=start, timeline_end=end,
        approval=Approval.APPROVED,
        call_to_action=(CallToAction(timeline_start=cta[0],
                                     timeline_end=cta[1])
                        if cta else None))


# ------------------------------------------------- the cascade, flagged


def test_a_snap_cascade_is_flagged_with_its_pulled_in_words():
    """The M16 shape: a body start at 19.7s walks two jittered joints
    and lands at 10.0s (-9.7s), pulling an unrelated preamble in."""
    report = preview_snap([_moment_3(16, 19.7, 25.0)],
                          _cascade_transcript())
    assert report["moved"] == 2
    assert report["flagged"] == 2
    by_boundary = {m["boundary"]: m
                   for m in report["moments"][0]["moves"]}
    start = by_boundary["body_start"]
    assert (start["was"], start["now"]) == (19.7, 10.0)
    assert start["delta"] == pytest.approx(-9.7)
    assert start["needs_decision"]
    assert [w["word"] for w in start["pulled_in"]] == ["alpha", "beta"]
    assert start["dropped"] == []
    end = by_boundary["body_end"]
    assert (end["was"], end["now"]) == (25.0, 40.0)
    assert end["needs_decision"]
    assert [w["word"] for w in end["pulled_in"]] == [
        "epsilon", "zeta"]
    # The bar is tunable: the same moves under a 20s bar flag nothing.
    relaxed = preview_snap([_moment_3(16, 19.7, 25.0)],
                           _cascade_transcript(), threshold=20.0)
    assert (relaxed["moved"], relaxed["flagged"]) == (2, 0)


# --------------------------------------- small moves stay quiet


def test_a_word_edge_nudge_is_reported_never_flagged():
    """The reel-5 shape: the stored end sits inside one word and the
    repair moves 0.179s onto its edge - a line of output, no
    decision."""
    tx = {"segments": [
        _seg("recommend you", 412.63, 413.85,
             [_w("recommend", 412.63, 412.99),
              _w("you", 413.01, 413.17),
              _w("yours", 413.59, 413.85)]),
        _seg("about", 412.77, 414.03, [_w("about", 412.77, 414.03)],
             bound=False),
    ]}
    report = preview_snap([_moment_3(5, 412.63, 413.851)], tx)
    assert report["moved"] == 1
    assert report["flagged"] == 0
    move = report["moments"][0]["moves"][0]
    assert move["boundary"] == "body_end"
    assert move["now"] == 414.03
    assert not move["needs_decision"]
    assert [w["word"] for w in move["pulled_in"]] == ["about"]
    text = render_snap_preview(report)
    assert "NEEDS DECISION" not in text
    assert "0 need(s) a decision" in text
    _decision_lines_derive_from_a_raw_build_loop_move()


# --------------------------------- the build loop reads one spelling


def _decision_lines_derive_from_a_raw_build_loop_move():
    """The build loop hands `decision_lines` the raw move (no word
    lists, no phrases): a flagged cascade still reads loud, a nudge
    still reads as nothing - so the beforehand report and the build
    agree."""
    tx = _cascade_transcript()
    loud = decision_lines(16, {"boundary": "body_start",
                               "was": 19.7, "now": 10.0,
                               "through": None}, tx)
    assert any("NEEDS DECISION" in line for line in loud)
    assert any("alpha" in line for line in loud)
    assert any("proposal" in line for line in loud)
    quiet = decision_lines(5, {"boundary": "body_end",
                               "was": 413.851, "now": 414.03,
                               "through": "about"}, tx)
    assert quiet == []


# ----------------- the pin, recorded with provenance, applied


def _pin_transcript():
    return {"segments": [
        _seg("the setup mattered", 50.0, 80.0,
             [_w("the", 51.0, 51.3), _w("setup", 51.4, 51.8),
              _w("mattered", 52.0, 52.5)]),
        _seg("here is the thing", 90.0, 101.0,
             [_w("here", 90.1, 90.4), _w("is", 90.5, 90.7),
              _w("the", 90.8, 91.0), _w("thing", 91.1, 91.5)]),
        _seg("welcome back everyone today we have words", 100.0, 120.0,
             [_w("welcome", 100.5, 100.9), _w("back", 101.0, 101.4),
              _w("everyone", 101.5, 102.0), _w("today", 103.0, 103.5),
              _w("we", 104.0, 104.3), _w("have", 104.4, 104.8),
              _w("words", 105.0, 105.4)]),
    ]}


def test_a_decided_pin_is_recorded_with_provenance_and_applied(tmp_path):
    """The canary's M6 loop, closed: a closer start the snap would drag
    is flagged with the record-closer command and both phrases (not a
    proposal pointer); those phrases become a `redraw_closer`
    declaration in the project store (with its `source`), and the
    build's pin pass redraws the snapped closer onto the ruled opening."""
    from library.tools import captain_edits
    from library.tools.project_layout import ProjectLayout

    project = tmp_path / "project"
    project.mkdir()
    ProjectLayout(str(project)).ensure()
    tx = _pin_transcript()
    transcript_file = (project / "pipeline_output" / "scratch"
                       / "timeline_transcript" / "transcript.json")
    transcript_file.parent.mkdir(parents=True, exist_ok=True)
    transcript_file.write_text(json.dumps(tx), encoding="utf-8")

    moment = _moment_3(6, 50.0, 80.0, cta=(100.0, 120.0))
    report = preview_snap([moment], tx)
    moves = {m["boundary"]: m for m in report["moments"][0]["moves"]}
    assert set(moves) == {"cta_start"}
    closer = moves["cta_start"]
    assert (closer["was"], closer["now"]) == (100.0, 90.0)
    assert closer["needs_decision"]
    assert closer["anchor_phrase"].startswith("welcome back")
    assert closer["snapped_phrase"].startswith("here is the thing")
    lines = decision_lines(6, closer, tx, "/projects/demo")
    assert any("record-closer" in line for line in lines)
    assert any(repr(closer["anchor_phrase"]) in line for line in lines)
    assert any(repr(closer["snapped_phrase"]) in line for line in lines)

    edit, action = captain_edits.record_edit(
        str(project),
        {"kind": "redraw_closer",
         "anchor_phrase": closer["anchor_phrase"],
         "from_phrase": closer["snapped_phrase"],
         "reason": "snap preview: the closer would open 10s from "
                   "the ruled opening; pin it back"},
        "snap preview")
    assert action == "recorded"
    stored = json.loads(
        (project / "external" / "declarations" / "captain_edits.json")
        .read_text(encoding="utf-8"))
    assert stored["source"] == "snap preview"
    assert captain_edits.load_edits(str(project)) == [edit]

    # The build snaps first, then applies pins: the snapped closer
    # opens on the from-phrase, and the pin redraws it onto the
    # anchor's words with the end fixed.
    from library.tools.reel_proposal import snap_moment_to_speech
    snapped, _ = snap_moment_to_speech(moment, tx)
    assert snapped.call_to_action.timeline_start == 90.0
    redrawn, applied, held, stale = (
        captain_edits.apply_closer_redraws([snapped], tx, [edit]))
    assert stale == [] and held == []
    assert [r["reel"] for r in applied] == [6]
    assert redrawn[0].call_to_action.timeline_start == 100.5
    assert redrawn[0].call_to_action.timeline_end == 120.0


# --------------------------------------------------------------------------
# From test_reel03_opening_boundary_guard.py
#
# A shared speech edge must open on the whole first word.

SOURCE = "/media/LCATL0013.MXF"
ITEM = "same-resolve-item"
FPS = 24000 / 1001
EDGE = 126.02154166666666


def _transcript_2(previous_item=ITEM, current_item=ITEM,
                previous_end=212.9, previous_source_end=EDGE,
                current_source_start=EDGE):
    previous_start = 211.55
    previous_source_start = 124.67154166666667
    current_start = 212.9
    current_end = 218.26
    return {"derived_from": {"duration_seconds": 500.0}, "segments": [
        {
            "speaker": "SpeakerTwo",
            "text": "So we are hearing about that.",
            "timeline_start": previous_start,
            "timeline_end": previous_end,
            "source_file": SOURCE,
            "resolve_item_id": previous_item,
            "source_start": previous_source_start,
            "source_end": previous_source_end,
            "words": [
                {"word": "about", "start": 212.55, "end": 212.87},
                {"word": "that.", "start": 212.87, "end": 212.9},
            ],
        },
        {
            "speaker": "SpeakerTwo",
            "text": "If you're attorney,",
            "timeline_start": current_start,
            "timeline_end": current_end,
            "source_file": SOURCE,
            "resolve_item_id": current_item,
            "source_start": current_source_start,
            "source_end": current_source_start + (current_end - current_start),
            "words": [
                {"word": "If", "start": 212.9, "end": 213.28},
                {"word": "you're", "start": 213.28, "end": 213.39},
                {"word": "attorney,", "start": 213.39, "end": 213.82},
            ],
        },
    ]}


def _clip():
    # The source and timeline offsets match both transcript rows exactly.
    return SimpleNamespace(
        resolve_item_id=ITEM,
        track_type="video",
        track_index=1,
        track_name="SpeakerTwo",
        speaker="SpeakerTwo",
        source_file=SOURCE,
        source_in=124.67154166666667,
        source_out=131.38154166666664,
        timeline_start=211.55,
        timeline_end=218.26,
    )


def _captioned(played):
    entries = []
    norms = set()
    for word in played:
        norm = subtitle_coverage.normalize_word(word["word"])
        norms.add(norm)
        entries.append({
            "word": word["word"],
            "norm": norm,
            "reel_start": word["reel_start"],
            "reel_end": word["reel_end"],
            "card": "opening.mov",
        })
    cards = [{"card": "opening.mov", "reel_start": 0.0,
              "reel_end": 0.92, "text_norms": norms}]
    return entries, cards


def test_reel03_shared_edge_excludes_prior_sentence_and_resolves_if_anchor():
    transcript = _transcript_2()
    start, end = snap_to_speech(213.26, 218.26, transcript)

    # "that." ends exactly where "If" begins. Keep that measured edge;
    # moving 50ms into If loses its onset after placement rounding.
    assert start == pytest.approx(212.9)
    assert end == pytest.approx(218.26)
    assert partial_overlaps(start, end, transcript) == []
    validate_proposal(
        [ReelMoment(number=3, slug="people-stopped-searching",
                    reason="opening boundary regression",
                    timeline_start=start, timeline_end=end,
                    approval=Approval.APPROVED)],
        transcript, 500.0,
    )

    placed = placements([(start, end)], [_clip()], FPS)
    assert len(placed) == 1
    assert placed[0]["source_in"] == pytest.approx(EDGE, abs=1e-9)

    played = subtitle_coverage.played_words_from_transcript(
        transcript["segments"], [{
            "source_file": SOURCE,
            "source_start": placed[0]["source_in"],
            "source_end": placed[0]["source_out"],
            "reel_start": placed[0]["record"],
        }])["words"]
    assert [word["word"] for word in played] == [
        "If", "you're", "attorney,"
    ]
    assert played[0]["reel_start"] == pytest.approx(0.0)
    assert played[0]["reel_end"] == pytest.approx(0.38)
    captioned, cards = _captioned(played)
    gate = subtitle_coverage.check_word_coverage(played, captioned, cards)
    assert [finding for finding in gate["findings"]
            if finding["severity"] == "error"] == []

    spine = reel_look.motion_spine(placed, FPS, transcript["segments"])
    block = spine["structure"][0]
    assert block["source_start"] == pytest.approx(EDGE, abs=1e-9)
    assert [word["word"] for word in block["word_timestamps"]] == [
        "If", "you're", "attorney,"
    ]
    anchor = resolve_anchor(
        {"word": "If"}, block=block, frame_rate=FPS,
        step="plan_vfx", plan="reel_motion", index=0,
    )
    assert anchor["timeline_seconds"] == pytest.approx(0.0)
    assert 0 <= anchor["timeline_seconds"] < block["timeline_end"]


def test_opening_still_snaps_to_the_whole_first_word_when_rows_differ():
    for changes in ({"previous_end": 212.89},
                    {"previous_item": "different-resolve-item"},
                    {"current_item": None},
                    {"current_source_start": EDGE + 0.001}):
        transcript = _transcript_2(**changes)
        start, end = snap_to_speech(213.26, 218.26, transcript)
        assert (start, end) == pytest.approx((212.9, 218.26)), changes


def test_boundary_guard_does_not_leave_a_cut_inside_the_first_word():
    transcript = _transcript_2()
    assert partial_overlaps(212.95, 218.26, transcript) == [
        transcript["segments"][1]
    ]


def test_build_reel_timeline_places_audio_at_or_after_the_measured_edge(
        tmp_path):
    """The Resolve append path must not round a repaired word edge back.

    The stored proposal starts at 213.26, so the build-time repair first
    moves it to the shared edge at 212.9. That edge maps to source
    126.0215417s, between source frames. The master audio item is one
    frame behind the picture-bound transcript, so its independent map
    makes the old append conversion choose frame 3020. That readback is
    62ms before the edge and plays the ends of "about that." although
    the proposal and subtitle plan both start on "If".
    """
    from types import SimpleNamespace
    from unittest.mock import MagicMock

    from library.tools.reel_build import build_reel_timeline

    transcript = _transcript_2()
    stored = ReelMoment(
        number=3, slug="people-stopped-searching",
        reason="opening boundary regression",
        timeline_start=213.26, timeline_end=218.26,
        approval=Approval.APPROVED,
    )
    moment, moves = snap_moment_to_speech(stored, transcript)
    assert moment.timeline_start == pytest.approx(212.9)
    assert moves[0]["boundary"] == "body_start"

    base = _clip()
    video_attrs = {**vars(base), "track_type": "video"}
    audio_attrs = {**vars(base), "track_type": "audio"}
    # The master audio item's own in-point is one source frame behind the
    # picture-bound transcript. The offline PR 1454 fixture used only the
    # picture clip, so it could not expose the build's separate audio map.
    audio_attrs["source_in"] -= 1 / float(FPS)
    audio_attrs["source_out"] -= 1 / float(FPS)
    video = SimpleNamespace(**video_attrs)
    audio = SimpleNamespace(**audio_attrs)
    clips = [video, audio]

    project = MagicMock()
    pool = MagicMock()
    project.GetMediaPool.return_value = pool
    timeline = MagicMock()
    timeline.GetUniqueId.return_value = "test-reel03"
    timeline.GetSetting.side_effect = lambda key: {
        "useCustomSettings": "1",
        "timelineResolutionWidth": "1080",
        "timelineResolutionHeight": "1920",
    }.get(key, "")
    timeline.GetTrackCount.side_effect = lambda kind: (
        1 if kind in ("video", "audio") else 0)
    timeline.GetItemListInTrack.return_value = []
    pool.CreateEmptyTimeline.return_value = timeline
    project.GetCurrentTimeline.return_value = timeline

    root = MagicMock()
    pool.GetRootFolder.return_value = root
    root.GetSubFolderList.return_value = []
    source_item = MagicMock()
    source_item.GetClipProperty.side_effect = lambda prop: (
        SOURCE if prop == "File Path" else str(FPS) if prop == "FPS" else "")
    root.GetClipList.return_value = [source_item]

    build_reel_timeline(
        project, moment, clips, [], FPS, 1080, 1920,
        str(tmp_path), transcript, program_channels={"1": 1},
    )

    audio_append = next(
        call.args[0][0] for call in pool.AppendToTimeline.call_args_list
        if call.args[0][0]["mediaType"] == 2)
    assert audio_append["startFrame"] == 3022
    assert audio_append["endFrame"] - audio_append["startFrame"] == 129

    # F25 reads the source frames Resolve was actually asked to place.
    source_start = audio_append["startFrame"] / float(FPS)
    source_end = audio_append["endFrame"] / float(FPS)
    played = subtitle_coverage.played_words_from_transcript(
        transcript["segments"], [{
            "source_file": SOURCE,
            "source_start": source_start,
            "source_end": source_end,
            "reel_start": 0.0,
        }])["words"]
    assert [word["word"] for word in played] == [
        "If", "you're", "attorney,"
    ]


# --------------------------------------------------------------------------
# From test_reel_fragment_blocks.py
#
# A mid-sentence transcript row is given back to its sentence.
#
# Both directions: it fires on a fragment whose sentence continues into a
# neighbour, and does NOT fire across a cut, a clip or a speaker.
#
# History: `docs/evidence/reel_spine.md` (test_reel_fragment_blocks.py).

@dataclass
class Moment:
    timeline_start: float
    timeline_end: float
    cta_start: float = None
    cta_end: float = None
    number: int = 1


def row(speaker, text, tl_start, tl_end, clip, source_start):
    """One transcript row, its words tiling its span evenly."""
    parts = text.split()
    step = (tl_end - tl_start) / len(parts)
    words = [{"word": w,
              "start": round(tl_start + i * step, 4),
              "end": round(tl_start + (i + 1) * step, 4)}
             for i, w in enumerate(parts)]
    return {"speaker": speaker, "text": text,
            "timeline_start": tl_start, "timeline_end": tl_end,
            "source_file": clip, "resolve_item_id": clip,
            "source_start": source_start,
            "source_end": source_start + (tl_end - tl_start),
            "words": words}


def texts(spine):
    return [b["content"]["text"] for b in spine["structure"]]


# ── It fires, and the fragment lands on the right side ──────────────

def test_a_tail_fragment_is_given_back_to_the_sentence_it_ends():
    """Reel 23's own case: "It was definitely going to help" / "them."."""
    transcript = {"segments": [
        row("SpeakerOne", "It was definitely going to help",
            135.616, 136.460, "cam_a", 197.970),
        row("SpeakerOne", "them.", 136.540, 136.721, "cam_a", 198.894),
    ]}
    spine = spine_for_reel(Moment(135.0, 137.5), transcript)

    assert texts(spine) == ["It was definitely going to help them."]
    assert spine["fragment_blocks_merged"] == 1
    assert spine["fragment_blocks_unmerged"] == []
    block = spine["structure"][0]
    assert block["timeline_end"] - block["timeline_start"] \
        >= MIN_CAPTION_DISPLAY_SECONDS
    assert [w["word"] for w in block["word_timestamps"]] == [
        "It", "was", "definitely", "going", "to", "help", "them."]


def test_a_head_fragment_is_given_back_to_the_sentence_it_opens():
    """Reel 23's third card: "that's" heads SpeakerTwo's next sentence.

    The previous block ENDS a sentence, so the fragment cannot be its
    tail - the transcriber's own full stop is what says so.
    """
    transcript = {"segments": [
        row("SpeakerTwo", "and that is the whole point.",
            176.0, 179.4, "cam_b", 238.0),
        row("SpeakerTwo", "that's", 179.830, 179.970, "cam_b", 241.892),
        row("SpeakerTwo", "why i'm super excited about this",
            180.151, 185.210, "cam_b", 242.213),
    ]}
    spine = spine_for_reel(Moment(175.0, 186.0), transcript)

    assert texts(spine) == ["and that is the whole point.",
                            "that's why i'm super excited about this"]
    assert spine["fragment_blocks_merged"] == 1


def test_the_fragment_joins_the_sentence_the_punctuation_names():
    """Same fragment, both neighbours contiguous - the full stop decides.

    Merging always-backwards would put "For" onto the end of the
    previous sentence, where it stays the block's last card and stays
    short.  The transcriber wrote the full stop; this reads it.
    """
    transcript = {"segments": [
        row("SpeakerOne", "i ran google and chat gpt side by side.",
            10.0, 13.0, "cam_a", 100.0),
        row("SpeakerOne", "For", 13.1, 13.2, "cam_a", 103.1),
        row("SpeakerOne", "google it gave a list from 2023",
            13.3, 16.0, "cam_a", 103.3),
    ]}
    spine = spine_for_reel(Moment(9.0, 17.0), transcript)

    assert texts(spine) == ["i ran google and chat gpt side by side.",
                            "For google it gave a list from 2023"]


def test_a_head_fragment_falls_back_to_the_previous_sentence():
    """Reel 31's case: "Yeah." after "Authority and trust.".

    The previous block ends a sentence, so the punctuation reading says
    the fragment heads the next one - but the next block is the closer
    off another clip, so forward is impossible. Leaving it alone authors
    a 2-frame flash card F7 fails. The previous side plays straight on
    (same speaker, same clip, 101ms), so the fragment closes the
    sentence it follows instead. Forward keeps priority: this only fires
    when the punctuated side cannot take it.
    """
    transcript = {"segments": [
        row("SpeakerOne", "Authority and trust.",
            81.901, 83.42, "cam_a", 4830.045),
        row("SpeakerOne", "Yeah.", 83.521, 83.621, "cam_a", 4831.665),
        row("SpeakerOne", "Let us do the work for you.",
            83.621, 84.7, "cam_b", 3673.531),
    ]}
    spine = spine_for_reel(Moment(81.0, 85.0), transcript)

    assert texts(spine) == ["Authority and trust. Yeah.",
                            "Let us do the work for you."]
    assert spine["fragment_blocks_merged"] == 1
    assert spine["fragment_blocks_unmerged"] == []
    block = spine["structure"][0]
    assert block["timeline_end"] - block["timeline_start"] \
        >= MIN_CAPTION_DISPLAY_SECONDS


# ── It does NOT fire, and says which ones it left ───────────────────

def test_a_fragment_is_left_alone_across_a_cut_and_is_named():
    """The rows abut on the reel but not in the source: a take was cut.

    Merging them would claim one continuous piece of footage that was
    never continuous.
    """
    transcript = {"segments": [
        row("SpeakerOne", "so the thing you have to know is",
            10.0, 12.0, "cam_a", 100.0),
        # 0.08s later on the reel, 3.08s later in the SOURCE.
        row("SpeakerOne", "this.", 12.08, 12.26, "cam_a", 105.08),
    ]}
    spine = spine_for_reel(Moment(9.0, 13.0), transcript)

    assert texts(spine) == ["so the thing you have to know is", "this."]
    assert spine["fragment_blocks_merged"] == 0
    assert spine["fragment_blocks_unmerged"] == ["this."]

    # A head fragment whose previous side is cut too, and whose next block
    # is another clip: nothing continuous can take it, so it stays, named.
    transcript = {"segments": [
        row("SpeakerOne", "Authority and trust.",
            81.901, 83.42, "cam_a", 4830.045),
        # Same reel seconds, but 3 seconds later in the SOURCE: a cut.
        row("SpeakerOne", "Yeah.", 83.521, 83.621, "cam_a", 4834.665),
        row("SpeakerOne", "Let us do the work for you.",
            83.621, 84.7, "cam_b", 3673.531),
    ]}
    spine = spine_for_reel(Moment(81.0, 85.0), transcript)
    assert texts(spine) == ["Authority and trust.", "Yeah.",
                            "Let us do the work for you."]
    assert spine["fragment_blocks_merged"] == 0
    assert spine["fragment_blocks_unmerged"] == ["Yeah."]


# --------------------------------------------------------------------------
# From test_reel_f25_planning_regressions.py
#
# The reel build's subtitle planner must produce words F25 can pair.
#
# These regressions use `reel_build.reel_subtitle_segments`, the same
# planning entry point as a staged reel build, then compare its plan with
# the offline played-word coverage used by F25. Remotion rendering is
# stubbed; no Resolve or project output is involved.

SOURCE_2 = "fixture.mxf"


def _moment_4(start, end, source_start=100.0):
    return ReelMoment(
        number=12,
        slug="caption-planning-regression",
        reason="regression fixture",
        timeline_start=start,
        timeline_end=end,
        source_spans=({
            "source_file": SOURCE_2,
            "source_start": source_start,
            "source_end": source_start + (end - start),
        },),
    )


def _segment(text, words, start, end, source_start, speaker="SpeakerTwo"):
    return {
        "speaker": speaker,
        "text": text,
        "timeline_start": start,
        "timeline_end": end,
        "source_file": SOURCE_2,
        "source_start": source_start,
        "source_end": source_start + (end - start),
        "resolve_item_id": "fixture-clip",
        "words": words,
    }


def _plan(transcript, ranges, project_folder):
    start = min(row[0] for row in ranges)
    end = max(row[1] for row in ranges)
    source_start = min(
        segment["source_start"] for segment in transcript["segments"])
    moment = _moment_4(start, end, source_start)
    return reel_build.reel_subtitle_segments(
        moment, transcript, ranges, str(project_folder), fps=FPS,
        width=1080, height=1920)


def _f25_errors(plan, transcript, ranges, project_folder):
    audio_spans = [{
        "source_file": segment["source_file"],
        "source_start": segment["source_start"],
        "source_end": segment["source_end"],
        "reel_start": reel_build.reel_time(
            segment["timeline_start"], ranges),
    } for segment in transcript["segments"]]
    played_result = subtitle_coverage.played_words_from_transcript(
        transcript["segments"], audio_spans)
    played = subtitle_coverage.read_words_for_comparison(
        played_result["words"],
        transcript_corrections.spelling_corrections(str(project_folder)))
    suppressed = verifier._suppressed_played_words(
        played, str(project_folder))
    captioned = []
    cards = []
    for entry in plan.caption_entries:
        cards.append({
            "card": entry["id"],
            "reel_start": entry["timeline_start"],
            "reel_end": entry["timeline_end"],
            "text_norms": sorted({
                subtitle_coverage.normalize_word(token)
                for token in entry["text"].split()
                if subtitle_coverage.normalize_word(token)
            }),
        })
        for word in entry.get("words") or []:
            norm = subtitle_coverage.normalize_word(word["word"])
            if norm:
                captioned.append({
                    "word": word["word"],
                    "norm": norm,
                    "card": entry["id"],
                    "reel_start": word["start"],
                    "reel_end": word["end"],
                })
    result = subtitle_coverage.check_word_coverage(
        played, captioned, cards, suppressed=suppressed)
    return [finding for finding in result["findings"]
            if finding["severity"] == "error"]


def _skip_render(monkeypatch):
    monkeypatch.setattr(
        generate_remotion_props, "generate_subtitle_props_per_block",
        lambda *args, **kwargs: [],
    )


def test_subframe_word_is_not_planned_and_f25_passes(tmp_path, monkeypatch):
    _skip_render(monkeypatch)
    words = [
        {"word": "there", "start": 1.0, "end": 1.3, "timed": True},
        {"word": "a", "start": 1.31, "end": 1.32, "timed": True},
        {"word": "reason", "start": 1.4, "end": 1.9, "timed": True},
    ]
    transcript = {"segments": [
        _segment("there a reason", words, 1.0, 2.0, 101.0),
    ]}
    ranges = [(1.0, 2.0)]

    plan = _plan(transcript, ranges, tmp_path)

    assert "a" not in " ".join(entry["text"]
                                for entry in plan.caption_entries).split()
    assert _f25_errors(plan, transcript, ranges, tmp_path) == []


def test_word_at_float_noisy_range_head_is_planned_and_f25_passes(
        tmp_path, monkeypatch):
    _skip_render(monkeypatch)
    words = [
        {"word": "and", "start": 1.0, "end": 1.11, "timed": True},
        {"word": "if", "start": 1.11, "end": 1.3, "timed": True},
        {"word": "you", "start": 1.3, "end": 1.5, "timed": True},
    ]
    transcript = {"segments": [
        _segment("and if you", words, 1.0, 2.0, 101.0),
    ]}
    # The transcript and the derived range head differ by floating-point
    # representation noise only. Placement rounds them to the same edge.
    ranges = [(1.0000000000001, 2.0)]

    plan = _plan(transcript, ranges, tmp_path)

    assert "and" in " ".join(entry["text"]
                              for entry in plan.caption_entries).split()
    assert _f25_errors(plan, transcript, ranges, tmp_path) == []


def test_reel15_cached_iso_merge_makes_caption_plan_pass_f25(
        tmp_path, monkeypatch):
    """A pre-1465 transcript is resolved from its cached ISO WAVs."""
    _skip_render(monkeypatch)
    words = ["Yeah", "so", "AI", "is", "actually", "better", "for",
             "small", "businesses"]

    def segment(speaker, start):
        duration = 2.37
        step = duration / len(words)
        timed = tuple({"word": word, "start": start + index * step,
                       "end": start + (index + 1) * step, "timed": True}
                      for index, word in enumerate(words))
        text = " ".join(words)
        return timeline_transcript.SpokenSegment(
            speaker=speaker, text=text, timeline_start=start,
            timeline_end=start + duration, source_file=SOURCE_2,
            source_start=100.0, source_end=102.37,
            resolve_item_id=f"{speaker}-clip", words=timed)

    audio_dir = (tmp_path / "pipeline_output" / "scratch"
                 / "timeline_transcript")
    audio_dir.mkdir(parents=True)
    (audio_dir / "speakertwo.wav").write_bytes(b"cached SpeakerTwo ISO")
    (audio_dir / "speakerone.wav").write_bytes(b"cached SpeakerOne ISO")
    monkeypatch.setattr(
        timeline_transcript, "_track_rms_dbfs",
        lambda path, _start, _end: (
            (-42.62 if Path(path).name == "speakertwo.wav" else -27.12), None),
    )
    stale_document = {"segments": [row.as_dict() for row in
                                    (segment("SpeakerTwo", 1.0),
                                     segment("SpeakerOne", 1.05))]}
    transcript = timeline_transcript.resolve_document_mic_bleed(
        stale_document, str(tmp_path))
    merged = transcript["segments"]
    decisions = transcript["mic_bleed_resolution"]
    assert [row["speaker"] for row in merged] == ["SpeakerOne"]
    assert decisions[0]["level_difference_db"] == 15.5

    ranges = [(1.0, 3.42)]
    moment = ReelMoment(
        number=15, slug="the-3d-nail-art-salon-beats-the-chains",
        reason="duplicate ISO transcript regression",
        timeline_start=ranges[0][0], timeline_end=ranges[0][1],
        source_spans=({"source_file": SOURCE_2, "source_start": 100.0,
                       "source_end": 102.42},),
    )
    plan = reel_build.reel_subtitle_segments(
        moment, transcript, ranges, str(tmp_path), fps=FPS,
        width=1080, height=1920)

    assert " ".join(entry["text"] for entry in plan.caption_entries).lower() == (
        "yeah so ai is actually better for small businesses")
    assert _f25_errors(plan, transcript, ranges, tmp_path) == []


def test_reel15_build_snap_keeps_full_edge_words_and_passes_f25(
        tmp_path, monkeypatch):
    """The build repairs both approved starts before placing and captioning.

    The approved body and CTA starts sit 111ms and 108ms into their first
    words. The build's normal boundary repair widens them to the measured
    word starts, so the reel plays complete words and 4.01 captions them.
    """
    _skip_render(monkeypatch)
    body_words = [
        {"word": "If", "start": 1186.83, "end": 1187.10, "timed": True},
        {"word": "you're", "start": 1187.10, "end": 1187.35, "timed": True},
        {"word": "a", "start": 1187.35, "end": 1187.44, "timed": True},
        {"word": "salon", "start": 1187.44, "end": 1187.80, "timed": True},
        {"word": "owner", "start": 1187.80, "end": 1188.15, "timed": True},
        {"word": "or", "start": 1188.15, "end": 1188.55, "timed": True},
    ]
    cta_words = [
        {"word": "And", "start": 333.69, "end": 333.95, "timed": True},
        {"word": "that's", "start": 333.95, "end": 334.11, "timed": True},
        {"word": "why", "start": 334.11, "end": 334.21, "timed": True},
        {"word": "we've", "start": 334.21, "end": 334.39, "timed": True},
        {"word": "been", "start": 334.39, "end": 334.56, "timed": True},
        {"word": "building", "start": 334.56, "end": 335.08, "timed": True},
    ]
    transcript = {"segments": [
        _segment("If you're a salon owner or", body_words,
                 1186.83, 1188.55, 100.0),
        _segment("And that's why we've been building", cta_words,
                 333.69, 335.08, 200.0, speaker="SpeakerOne"),
    ]}
    moment = ReelMoment(
        number=15,
        slug="the-3d-nail-art-salon-beats-the-chains",
        reason="edge-word regression",
        timeline_start=1186.941,
        timeline_end=1188.55,
        source_spans=(
            {"source_file": SOURCE_2, "source_start": 100.0,
             "source_end": 101.72},
            {"source_file": SOURCE_2, "source_start": 200.0,
             "source_end": 201.39},
        ),
        call_to_action=CallToAction(
            timeline_start=333.798,
            timeline_end=335.08,
            text="And that's why we've been building",
            speaker="SpeakerOne",
        ),
    )

    repaired, moves = snap_moment_to_speech(moment, transcript)
    assert [(move["boundary"], move["was"], move["now"])
            for move in moves if move["boundary"] in
            ("body_start", "cta_start")] == [
        ("body_start", 1186.941, 1186.83),
        ("cta_start", 333.798, 333.69),
    ]
    ranges = reel_build.reel_ranges(repaired, transcript)
    assert ranges == [(1186.83, 1188.55), (333.69, 335.08)]

    plan = reel_build.reel_subtitle_segments(
        repaired, transcript, ranges, str(tmp_path), fps=FPS,
        width=1080, height=1920)

    planned = " ".join(entry["text"] for entry in plan.caption_entries).lower()
    assert planned.startswith("if you're")
    assert "and that's why" in planned
    assert _f25_errors(plan, transcript, ranges, tmp_path) == []


def test_legacy_suppressed_i_does_not_steal_ive_alignment(
        tmp_path, monkeypatch):
    _skip_render(monkeypatch)
    transcript_corrections.record_display_suppression(
        str(tmp_path), "I", "the anchored second I is a false start",
        scope={"speaker": "SpeakerTwo", "surface": "I",
               "prev": "I've", "next": "thought"})
    words = [
        {"word": "I've", "start": 1.0, "end": 1.45, "timed": True},
        {"word": "I", "start": 1.46, "end": 1.58, "timed": True,
         "display": False},
        {"word": "thought", "start": 1.6, "end": 2.0, "timed": True},
    ]
    transcript = {"segments": [
        # This is the cached text left by the old apostrophe-boundary bug.
        _segment("'ve I thought", words, 1.0, 2.0, 101.0),
    ]}
    ranges = [(1.0, 2.0)]

    plan = _plan(transcript, ranges, tmp_path)

    planned = " ".join(entry["text"]
                       for entry in plan.caption_entries).lower()
    assert "i've" in planned
    assert _f25_errors(plan, transcript, ranges, tmp_path) == []


def test_project_phrase_spelling_reaches_reel_alignment_and_f25(
        tmp_path, monkeypatch):
    _skip_render(monkeypatch)
    transcript_corrections.record_spelling(
        str(tmp_path), "Jim and I", "Gemini",
        reason="captain-confirmed transcript correction")
    words = [
        {"word": "Jim", "start": 1.0, "end": 1.2, "timed": True},
        {"word": "and", "start": 1.2, "end": 1.35, "timed": True},
        {"word": "I", "start": 1.35, "end": 1.55, "timed": True},
        {"word": "on", "start": 1.6, "end": 1.8, "timed": True},
        {"word": "Google", "start": 1.8, "end": 2.2, "timed": True},
    ]
    transcript = {"segments": [
        _segment("Jim and I on Google", words, 1.0, 2.3, 101.0),
    ]}
    ranges = [(1.0, 2.3)]

    plan = _plan(transcript, ranges, tmp_path)

    planned = " ".join(entry["text"]
                       for entry in plan.caption_entries).lower()
    assert "gemini" in planned
    assert _f25_errors(plan, transcript, ranges, tmp_path) == []


def test_overlapped_same_speaker_word_moves_to_covering_card(
        tmp_path, monkeypatch):
    _skip_render(monkeypatch)
    first_words = [
        {"word": "what", "start": 30.82, "end": 31.02, "timed": True},
        {"word": "you", "start": 31.02, "end": 31.2, "timed": True},
        {"word": "can", "start": 31.2, "end": 31.38, "timed": True},
        {"word": "say", "start": 31.38, "end": 31.52, "timed": True},
        {"word": "about", "start": 31.52, "end": 31.76, "timed": True},
        {"word": "it.", "start": 31.782, "end": 31.92, "timed": True},
    ]
    second_words = [
        {"word": "and", "start": 31.751, "end": 31.791, "timed": True},
        {"word": "AI", "start": 31.92, "end": 32.15, "timed": True},
        {"word": "really", "start": 32.15, "end": 32.38, "timed": True},
        {"word": "likes", "start": 32.38, "end": 32.7, "timed": True},
        {"word": "that.", "start": 32.7, "end": 33.08, "timed": True},
    ]
    transcript = {"segments": [
        _segment("what you can say about it.", first_words,
                 30.82, 31.92, 130.82),
        _segment("and AI really likes that.", second_words,
                 31.751, 33.08, 131.751),
    ]}
    ranges = [(30.82, 33.08)]

    plan = _plan(transcript, ranges, tmp_path)

    cards = plan.caption_entries
    it_words = [
        (entry, word) for entry in cards
        for word in entry.get("words") or []
        if subtitle_coverage.normalize_word(word["word"]) == "it"
    ]
    assert len(it_words) == 1, cards
    entry, word = it_words[0]
    assert entry["timeline_start"] <= word["start"]
    assert entry["timeline_end"] >= word["end"], cards
    assert _f25_errors(plan, transcript, ranges, tmp_path) == []


def test_shared_boundary_word_onset_keeps_transcript_order_and_f25_passes(
        tmp_path, monkeypatch):
    _skip_render(monkeypatch)
    first_words = [
        {"word": "what", "start": 1.0, "end": 1.2, "timed": True},
        {"word": "your", "start": 1.2, "end": 1.4, "timed": True},
        {"word": "differentiator", "start": 1.4, "end": 1.7,
         "timed": True},
        {"word": "is.", "start": 1.8, "end": 1.9, "timed": True},
    ]
    second_words = [
        {"word": "If", "start": 1.8, "end": 1.89, "timed": True},
        {"word": "you", "start": 2.0, "end": 2.2, "timed": True},
        {"word": "think", "start": 2.2, "end": 2.5, "timed": True},
        {"word": "your", "start": 2.5, "end": 2.7, "timed": True},
    ]
    transcript = {"segments": [
        _segment("what your differentiator is.", first_words,
                 1.0, 2.0, 101.0),
        _segment("If you think your", second_words,
                 1.8, 2.7, 101.8),
    ]}
    ranges = [(1.0, 2.7)]

    plan = _plan(transcript, ranges, tmp_path)

    is_cards = [
        entry for entry in plan.caption_entries
        if any(word["word"].casefold() == "is."
               for word in entry.get("words") or [])
    ]
    if_cards = [
        entry for entry in plan.caption_entries
        if any(word["word"].casefold() == "if"
               for word in entry.get("words") or [])
    ]
    assert len(is_cards) == len(if_cards) == 1
    assert is_cards[0] is not if_cards[0]
    assert plan.caption_entries.index(is_cards[0]) < plan.caption_entries.index(
        if_cards[0])
    assert _f25_errors(plan, transcript, ranges, tmp_path) == []
