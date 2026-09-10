"""The shared CTA opens on a sentence start, by a pin that survives rebuilds.

The captain, 2026-09-10: the shared closer [321.61, 328.23] opens
mid-sentence ("we're calling the lucy visibility system ..."), and the
redraw starts it from "it's exactly why we've been building this
platform we're calling ...". Firstmate measured `"it's"` at 319.358,
after a 0.478s pause following `"answer"` (ends 318.875) - so the new
start is 319.358, the end stays 328.231, and each of the four reels
sharing the span (2, 9, 20, 26) grows by exactly 2.252s.

PR 880 established that keep exclusions can only REMOVE seconds, so no
existing mechanism can extend a closer backwards. The pin lives in
`captain_edits` as a `redraw_closer` delta - word-anchored like every
other edit there, refusing timecode pins by construction - naming both
ends in words: `anchor_phrase` (what the closer must open on) and
`from_phrase` (what it opens on now, identifying WHICH closer moves so
no other reel's closer is touched).

Fail-before: `validate_edits` knows no `redraw_closer` kind and
`captain_edits` has no `apply_closer_redraws` - every test here errors
on the kind or the attribute, not on an assertion.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from library.tools import captain_edits
from library.tools.project_layout import ProjectLayout

OLD_START = 321.610
NEW_START = 319.358
CTA_END = 328.231
GROWTH = 2.252

ANCHOR = "it's exactly why we've been building this platform we're calling"
FROM = "we're calling the lucy visibility system"

CLOSER_TEXT = (
    "we're calling the lucy visibility system we'd love for you to go "
    "check it out see how ai sees you the links in the bio")


# ── The fixture: four reels, one shared closer, measured timings ──────

def _seg(speaker, text, start, end, uid, words=()):
    seg = {"speaker": speaker, "text": text, "timeline_start": start,
           "timeline_end": end, "resolve_item_id": uid}
    if words:
        seg["words"] = [
            {"word": w, "start": s, "end": e, "timed": True}
            for w, s, e in words]
    return seg


def _tx():
    """The CTA geometry in miniature, at the brief's own numbers.

    seg1 ends WITH a period ("... to be the answer."): the pause before
    "it's" is a sentence break, which is what lets the redrawn closer
    open clean. seg2 carries no terminal punctuation, so the CURRENT
    start opens mid-sentence and the bar fires - the defect.
    """
    answer_words = [
        ("we", 312.75, 313.1), ("kept", 313.15, 313.6),
        ("asking", 313.65, 314.2), ("what", 314.25, 314.6),
        ("the", 314.65, 314.9), ("old", 314.95, 315.4),
        ("playbook", 315.45, 316.1), ("claimed", 316.15, 316.8),
        ("to", 316.85, 317.1), ("be", 317.15, 317.5),
        ("the", 317.55, 317.9), ("answer.", 318.2, 318.875),
    ]
    head_words = [
        ("it's", 319.358, 319.6), ("exactly", 319.65, 320.1),
        ("why", 320.15, 320.4), ("we've", 320.45, 320.7),
        ("been", 320.75, 320.95), ("building", 321.0, 321.2),
        ("this", 321.25, 321.4), ("platform", 321.4, 321.61),
    ]
    closer_tokens = CLOSER_TEXT.split()
    cursor, closer_words = OLD_START, []
    step = (CTA_END - OLD_START) / len(closer_tokens)
    for token in closer_tokens:
        closer_words.append((token, round(cursor, 3),
                             round(cursor + step - 0.02, 3)))
        cursor += step
    closer_words[-1] = (closer_words[-1][0], closer_words[-1][1], CTA_END)
    assert closer_words[0][1] == OLD_START
    return {
        "segments": [
            _seg("Craig", "the old ranking game stops paying", 10.0, 25.0,
                 "b2a"),
            _seg("Akshita", "people ask full sentences now", 25.0, 40.0,
                 "b2b"),
            _seg("Craig", "client site earns zero clicks daily", 50.0, 80.0,
                 "b9a"),
            _seg("Akshita", "buyers trust visible proof fast", 80.0, 110.0,
                 "b9b"),
            _seg("Craig", "competitor publishes answers weekly", 120.0,
                 150.0, "b20a"),
            _seg("Akshita", "search summary quotes them instead", 150.0,
                 180.0, "b20b"),
            _seg("Craig", "owners write helpful guides nightly", 200.0,
                 230.0, "b26a"),
            _seg("Akshita", "callers mention those pages often", 230.0,
                 260.0, "b26b"),
            _seg("Craig",
                 "we kept asking what the old playbook claimed to be "
                 "the answer.", 312.75, 318.875, "lead", words=answer_words),
            _seg("Craig",
                 "it's exactly why we've been building this platform",
                 319.358, 321.61, "head", words=head_words),
            _seg("Craig", CLOSER_TEXT, 321.61, 328.231, "closer",
                 words=closer_words),
        ],
        "derived_from": {"duration_seconds": 1000.0,
                         "fps": 24000 / 1001},
    }


def _moment(number, start, end):
    from library.tools.reel_proposal import CallToAction, ReelMoment
    return ReelMoment(
        number=number, slug=f"reel-{number:02d}", reason="a full exchange",
        timeline_start=start, timeline_end=end,
        call_to_action=CallToAction(
            timeline_start=OLD_START, timeline_end=CTA_END,
            text=CLOSER_TEXT, speaker="Craig"))


def _moments():
    return [_moment(2, 10.0, 40.0), _moment(9, 50.0, 110.0),
            _moment(20, 120.0, 180.0), _moment(26, 200.0, 260.0)]


def _pin(**over):
    edit = {"kind": "redraw_closer", "anchor_phrase": ANCHOR,
            "from_phrase": FROM,
            "reason": "captain 2026-09-10: start the CTA from "
                      "\"it's exactly why\", not mid-clause"}
    edit.update(over)
    return edit


def _project(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    ProjectLayout(str(project)).ensure()
    return project


def _write_edits_file(project, edits):
    directory = project / "external"
    directory.mkdir(exist_ok=True)
    (directory / "captain_edits.json").write_text(
        json.dumps({"key": "captain_edits",
                    "source": "captain, 2026-09-10", "value": edits}),
        encoding="utf-8")


# ── 1. The pin validates, word-anchored ────────────────────────────────

def test_a_redraw_closer_pin_validates():
    assert len(captain_edits.validate_edits([_pin()])) == 1


def test_a_pin_without_a_from_phrase_is_refused():
    """A pin that does not name which closer it moves would redraw EVERY
    closer in the batch - including ones the captain never heard. The
    bars forbid touching beyond the shared span, so the pin must say
    which span in words."""
    edit = _pin()
    del edit["from_phrase"]
    with pytest.raises(captain_edits.CaptainEditError) as exc:
        captain_edits.validate_edits([edit])
    assert "from_phrase" in str(exc.value)


def test_a_timecode_pinned_closer_is_refused():
    """PR 857 refuses frame-anchored edits by construction; a closer
    pinned to 319.358 breaks the moment anything upstream re-times, so
    the numbers are refused at write time and only words travel."""
    with pytest.raises(captain_edits.CaptainEditError) as exc:
        captain_edits.validate_edits([_pin(new_start=NEW_START)])
    assert "319.358" in str(exc.value) or "start" in str(exc.value).lower()


# ── 2. The redraw moves all four, end fixed ────────────────────────────

def test_the_pin_opens_the_shared_closer_on_its_exactly_why():
    moments, applied, held, stale = captain_edits.apply_closer_redraws(
        _moments(), _tx(), [_pin()])
    assert stale == []
    assert held == []
    assert sorted(r["reel"] for r in applied) == [2, 9, 20, 26]
    for moment in moments:
        assert moment.call_to_action.timeline_start == pytest.approx(
            NEW_START)
        assert moment.call_to_action.timeline_end == pytest.approx(CTA_END)
    for record in applied:
        assert record["was"][0] == pytest.approx(OLD_START)
        assert record["now"][0] == pytest.approx(NEW_START)
        assert record["now"][1] == pytest.approx(CTA_END)


def test_each_reel_grows_by_exactly_2_252s():
    from library.tools.reel_build import reel_ranges
    from library.tools.reel_quality_bar import duration_reading
    from library.tools.reel_quality_bar import declared_closers
    tx = _tx()

    def played(moment):
        return sum(end - start for start, end in reel_ranges(moment, tx))

    before = {m.number: played(m) for m in _moments()}
    moments, _, _, _ = captain_edits.apply_closer_redraws(
        _moments(), tx, [_pin()])
    for moment in moments:
        assert played(moment) - before[moment.number] == pytest.approx(
            GROWTH)
        # The reported durations move with it, at report resolution.
        assert duration_reading(moment, tx)["delivered_seconds"] == (
            pytest.approx(before[moment.number] + GROWTH, abs=0.06))
    closers = declared_closers(moments)
    assert list(closers) == [(round(NEW_START, 2), round(CTA_END, 2))]
    assert sorted(closers[(round(NEW_START, 2), round(CTA_END, 2))]) == [
        2, 9, 20, 26]


def test_the_built_reel_opens_its_closer_on_its_exactly_why():
    """From the built timeline's OWN speech (`played_speech` over the
    ranges the builder lays down), not from the plan's declared text."""
    from library.tools.reel_quality_bar import played_speech
    from library.tools.reel_build import reel_ranges
    tx = _tx()
    moments, _, _, _ = captain_edits.apply_closer_redraws(
        _moments(), tx, [_pin()])
    for moment in moments:
        ranges = reel_ranges(moment, tx)
        assert ranges[-1][0] == pytest.approx(NEW_START)
        lines = played_speech(moment, tx, with_words=True)
        closer_lines = [line for line in lines
                        if line["range"] == len(ranges) - 1]
        opening = " ".join(line["text"] for line in closer_lines)
        assert opening.startswith("it's exactly why"), opening
        first_word = closer_lines[0]["words"][0]
        assert first_word["word"] == "it's"
        # The closer is laid down LAST, so the reel second of "it's" is
        # exactly the body's played length: the word opens the closer.
        assert first_word["at"] == pytest.approx(
            moment.timeline_end - moment.timeline_start)


# ── 3. The warning fires before, quiet after ───────────────────────────

def test_mid_sentence_warning_fires_on_all_four_before_and_none_after():
    from library.tools.reel_quality_bar import (
        QB_CTA_OPENS_MID_SENTENCE, judge)
    tx = _tx()
    before = judge(_moments(), tx)
    firing_before = sorted(
        v.number for v in before.verdicts
        if any(f.code == QB_CTA_OPENS_MID_SENTENCE for f in v.findings))
    assert firing_before == [2, 9, 20, 26]
    moments, _, _, _ = captain_edits.apply_closer_redraws(
        _moments(), tx, [_pin()])
    after = judge(moments, tx)
    firing_after = [v.number for v in after.verdicts
                    if any(f.code == QB_CTA_OPENS_MID_SENTENCE
                           for f in v.findings)]
    assert firing_after == []


# ── 4. Durability: the store, twice ────────────────────────────────────

def test_a_recorded_pin_survives_a_second_rebuild(tmp_path):
    """'Persisted through iterations': the pin lives in the project's
    external file, and two successive reads derive the same spans - the
    second reports HELD, not stale and not re-applied."""
    from library.tools.reel_quality_bar import played_speech
    from library.tools.reel_build import reel_ranges
    project = _project(tmp_path)
    _write_edits_file(project, [_pin()])
    tx = _tx()

    def rebuild(moments):
        fresh = json.loads(json.dumps([m.as_dict() for m in moments]))
        from library.tools.reel_proposal import ReelMoment
        moments = [ReelMoment.from_dict(d) for d in fresh]
        return captain_edits.apply_closer_redraws(
            moments, tx, captain_edits.load_edits(str(project)))

    moments_a, applied_a, held_a, stale_a = rebuild(_moments())
    assert sorted(r["reel"] for r in applied_a) == [2, 9, 20, 26]
    assert held_a == [] and stale_a == []
    moments_b, applied_b, held_b, stale_b = rebuild(moments_a)
    assert applied_b == [] and stale_b == []
    assert sorted(h["reel"] for h in held_b) == [2, 9, 20, 26]
    for first, second in zip(moments_a, moments_b):
        assert (first.call_to_action.timeline_start
                == second.call_to_action.timeline_start == pytest.approx(
                    NEW_START))
    for moment in moments_b:
        ranges = reel_ranges(moment, tx)
        closer_lines = [line for line in played_speech(moment, tx)
                        if line["range"] == len(ranges) - 1]
        assert " ".join(
            line["text"] for line in closer_lines).startswith(
                "it's exactly why")


# ── 5. Honest failures ─────────────────────────────────────────────────

def test_a_retranscribed_anchor_reports_stale_not_silent(tmp_path):
    """The passage reworded so the anchor matches nothing: the pin
    reports STALE loudly and no span moves."""
    tx = _tx()
    for segment in tx["segments"]:
        segment["text"] = segment["text"].replace(
            "it's exactly why we've been building", "motivation here runs")
        for word in segment.get("words") or []:
            word["word"] = word["word"].replace("it's", "motivation")
    moments, applied, held, stale = captain_edits.apply_closer_redraws(
        _moments(), tx, [_pin()])
    assert applied == [] and held == []
    assert len(stale) == 1 and ANCHOR in stale[0]["reason"]
    for moment in moments:
        assert moment.call_to_action.timeline_start == pytest.approx(
            OLD_START)


def test_an_extension_into_its_own_body_is_refused_loudly():
    """Extending the closer backwards must not double-play: a reel
    whose body already contains the anchor keeps its span, with the
    reason naming the overlap."""
    tx = _tx()
    reel = _moment(2, 10.0, 40.0)
    body_covering_anchor = _moment(7, 300.0, 322.0)
    moments, applied, held, stale = captain_edits.apply_closer_redraws(
        [reel, body_covering_anchor], tx, [_pin()])
    assert [r["reel"] for r in applied] == [2]
    assert len(stale) == 1 and stale[0].get("reel") == 7
    assert "overlap" in stale[0]["reason"].lower()
    assert body_covering_anchor.call_to_action.timeline_start == pytest.approx(
        OLD_START) or moments[1].call_to_action.timeline_start == pytest.approx(
            OLD_START)


def test_a_closer_opening_on_neither_phrase_is_left_alone():
    """A reel closing on a DIFFERENT invitation is not this pin's
    business: untouched, and the pin is not reported stale for it."""
    from library.tools.reel_proposal import CallToAction
    from dataclasses import replace
    tx = _tx()
    other = replace(
        _moment(5, 400.0, 440.0),
        call_to_action=CallToAction(
            timeline_start=400.0, timeline_end=406.0, text="",
            speaker="Craig"))
    moments, applied, held, stale = captain_edits.apply_closer_redraws(
        [other], tx, [_pin()])
    assert applied == [] and stale == []
    assert moments[0].call_to_action.timeline_start == pytest.approx(400.0)


# ── 6. Selection draws the pinned start on regeneration ────────────────

def test_selection_redraws_a_regenerated_proposal(tmp_path):
    """The model still names the old start; the proposal that lands
    carries the pinned one - so regeneration cannot re-emit the defect."""
    from library.steps.step_3_04_select_reels.post_bridge import resolve
    project = _project(tmp_path)
    _write_edits_file(project, [_pin()])
    tx = _tx()
    bodies = [(2, 10.0, 40.0), (9, 50.0, 110.0), (20, 120.0, 180.0),
              (26, 200.0, 260.0)]
    llm_output = {"moments": [
        {"start": start, "end": end, "slug": f"reel-{number:02d}",
         "reason": "a full exchange",
         "cta": {"start": OLD_START, "end": CTA_END,
                 "note": "the shared invitation"}}
        for number, start, end in bodies]}
    result = resolve(llm_output, {"timeline_transcript": tx,
                                  "project_folder": str(project)})
    selection = result["reel_selection"]
    assert len(selection["moments"]) == 4
    for stored in selection["moments"]:
        cta = stored["call_to_action"]
        assert cta["timeline_start"] == pytest.approx(NEW_START)
        assert cta["timeline_end"] == pytest.approx(CTA_END)
    assert sorted(r["reel"] for r in
                  selection["captain_closer_redraws"]["applied"]) == [
                      1, 2, 3, 4]  # selection renumbers sequentially


# ── 7. Both ends of the pin must still be spoken ───────────────────────

def test_a_pin_checks_both_phrases_against_measured_speech(tmp_path):
    """The external correspondence check covers `from_phrase` too: a
    pin whose old opening is gone from the speech is drift, refused by
    name rather than applied to the wrong closer."""
    from library.tools import external_inputs
    from library.tools.external_inputs import ExternalStateError
    project = _project(tmp_path)
    _write_edits_file(project, [_pin()])
    speech = ("it's exactly why we've been building this platform we're "
              "calling the lucy visibility system from start to finish")
    state = {"step_outputs": {"speech_sequence": {"body_sequence": [
        {"text": speech}]}}}
    supplied = external_inputs.load(str(project), state)
    assert "1 edit(s)" in supplied["captain_edits"].checked
    _write_edits_file(project, [_pin(from_phrase="zebras on mars")])
    with pytest.raises(ExternalStateError) as exc:
        external_inputs.load(str(project), state)
    assert "zebras on mars" in str(exc.value)


# ── 8. The real geometry: mid-row on a word edge ─────────────────────
#
# The fixture above rows the head ("it's exactly why ... platform")
# as its own segment, so the pin lands on a segment edge. The real
# transcript rows it INSIDE the previous row ([318.091, 321.530]:
# "to be the answer it's exactly why we've been building this
# platform") - WhisperX's chunking, not a sentence. The pin refused
# that opening as "no longer on a clean segment edge" and the rebuild
# faithfully reproduced the wrong CTA forever. A mid-row opening on a
# clean timed-word edge is the captain's ruling standing over the
# rowing, and applies; a mid-WORD opening still refuses.

def _tx_merged():
    """The real geometry: answer and head share one bound row."""
    tx = _tx()
    lead = [s for s in tx["segments"] if s["resolve_item_id"] == "lead"][0]
    head = [s for s in tx["segments"] if s["resolve_item_id"] == "head"][0]
    merged = dict(lead)
    merged["text"] = lead["text"] + " " + head["text"]
    merged["timeline_end"] = head["timeline_end"]
    merged["words"] = lead["words"] + head["words"]
    tx["segments"] = [
        merged if s["resolve_item_id"] == "lead" else s
        for s in tx["segments"]
        if s["resolve_item_id"] != "head"]
    return tx


def test_a_sentence_start_mid_row_applies():
    moments, applied, held, stale = captain_edits.apply_closer_redraws(
        _moments(), _tx_merged(), [_pin()])
    assert stale == [] and held == []
    assert sorted(r["reel"] for r in applied) == [2, 9, 20, 26]
    for moment in moments:
        assert moment.call_to_action.timeline_start == pytest.approx(
            NEW_START)
        assert moment.call_to_action.timeline_end == pytest.approx(CTA_END)


def test_an_overlapping_word_still_refuses():
    """An overlapping speaker's word strictly containing the opening:
    starting there would cut their word in half, so the pin reports
    STALE rather than shipping a half-word."""
    tx = _tx_merged()
    tx["segments"].append(
        _seg("Akshita", "yeah", 319.0, 319.5, "overlap",
             words=[("yeah", 319.0, 319.5)]))
    moments, applied, held, stale = captain_edits.apply_closer_redraws(
        [_moment(2, 10.0, 40.0)], tx, [_pin()])
    assert applied == [] and held == []
    assert len(stale) == 1 and stale[0].get("reel") == 2
    assert "word edge" in stale[0]["reason"]
    assert moments[0].call_to_action.timeline_start == pytest.approx(
        OLD_START)


def test_a_pinned_cta_passes_validation_where_unpinned_refuses():
    """Regeneration draws the pinned start and then validates: without
    the pin's own word-edge guarantee the whole-segments refusal would
    break every future selection run - a recorded decision breaking
    the loop it closed. Every other reel, and every body span, is
    checked exactly as before."""
    from library.tools.reel_proposal import (
        ProposalError, _check_call_to_action)
    tx = _tx_merged()
    moments, applied, _, _ = captain_edits.apply_closer_redraws(
        [_moment(9, 50.0, 110.0)], tx, [_pin()])
    assert [r["reel"] for r in applied] == [9]
    redrawn = moments[0]
    duration = max(redrawn.timeline_end,
                   redrawn.call_to_action.timeline_end)
    label = "reel 9 ('your-website-is-only-20-percent')"
    with pytest.raises(ProposalError) as exc:
        _check_call_to_action(redrawn, tx, duration, label)
    assert "cuts 1 segment" in str(exc.value)
    _check_call_to_action(redrawn, tx, duration, label, pinned=True)


# ── 9. Durability through the write side ─────────────────────────────

def test_a_recorded_pin_survives_the_read_and_two_rebuilds(tmp_path):
    """The whole loop through the dormant store: record (the write
    side, correspondence-checked against measured speech) -> load
    (the reader the build uses) -> apply, twice. The first rebuild
    redraws; the second holds. That is what "persist" means."""
    project = _project(tmp_path)
    transcript_path = (project / "pipeline_output" / "scratch"
                       / "timeline_transcript" / "transcript.json")
    transcript_path.parent.mkdir(parents=True, exist_ok=True)
    transcript_path.write_text(json.dumps(_tx()), encoding="utf-8")
    edit, action = captain_edits.record_edit(
        str(project), _pin(), "captain, 2026-09-10")
    assert action == "recorded"
    moments = _moments()
    for round in range(2):
        edits = captain_edits.load_edits(str(project))
        assert edits == [edit]
        moments, applied, held, stale = (
            captain_edits.apply_closer_redraws(moments, _tx(), edits))
        assert stale == []
        if round == 0:
            assert sorted(r["reel"] for r in applied) == [2, 9, 20, 26]
            assert held == []
        else:
            assert applied == []
            assert sorted(h["reel"] for h in held) == [2, 9, 20, 26]
    for moment in moments:
        assert moment.call_to_action.timeline_start == pytest.approx(
            NEW_START)


# ── 10. The gate derives what the build placed ────────────────────────
#
# The build redraws approved moments in memory and places the redrawn
# spans; the verifier derived the un-pinned file and read Reel 09's
# +2.25s CTA growth as 54 dropped frames, failing a correct build.
# The gate applies the same pins before deriving anything.

def test_the_gate_applies_recorded_pins_before_deriving(tmp_path):
    import io
    from library.tools.reel_conformance_verifier import (
        _apply_recorded_pins)
    project = _project(tmp_path)
    _write_edits_file(project, [_pin()])
    redrawn = _apply_recorded_pins(
        _moments(), _tx_merged(), str(project), io.StringIO())
    for moment in redrawn:
        assert moment.call_to_action.timeline_start == pytest.approx(
            NEW_START)


def test_the_gate_without_a_project_grades_the_file_as_before(tmp_path):
    import io
    from library.tools.reel_conformance_verifier import (
        _apply_recorded_pins)
    moments = _moments()
    assert _apply_recorded_pins(
        moments, _tx_merged(), "", io.StringIO()) == moments


def test_the_gate_refuses_an_unreadable_pin_file(tmp_path):
    import io
    from library.tools.reel_conformance_verifier import (
        _apply_recorded_pins)
    project = _project(tmp_path)
    _write_edits_file(project, [{"kind": "redraw_closer"}])
    with pytest.raises(RuntimeError) as exc:
        _apply_recorded_pins(
            _moments(), _tx_merged(), str(project), io.StringIO())
    assert "cannot be read" in str(exc.value)


def test_the_gate_repair_keeps_moments_without_moves(tmp_path):
    """The gate grades the batch the build placed: the repair reports
    moves and keeps every moment, moveless ones included. Dropping a
    moment here grades the batch against a smaller plan - Reel 09's
    rebuild failed the gate on an empty derived plan for exactly this
    reason, after a re-indentation left the append outside the loop."""
    import io
    from library.tools.reel_conformance_verifier import _repair_moments
    moments = _moments()
    repaired = _repair_moments(moments, _tx(), io.StringIO())
    assert [m.number for m in repaired] == [2, 9, 20, 26]
