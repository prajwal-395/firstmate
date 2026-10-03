"""The captain's four qualities, and the proof each one can go both ways.

A gate that cannot fail is worse than no gate, and a gate that fails
correct output is no more coverage than one that cannot fail
(AGENTS.md 10.4).  Every check in `library/tools/reel_quality_bar.py`
therefore gets two tests here: one where it fires and one where it does
not.

The anti-contamination guarantee gets a third kind of test.  It is not
enough that the handoff happens to be worded carefully today - the
question is whether a later edit that hands the judge its criteria would
be caught.  So `assert_ask_is_uncontaminated` is run over the REAL files
on disk, and it is separately shown to fire on a document that names one.
"""
from __future__ import annotations
import json
import sys
from pathlib import Path
import pytest
from library.tools import reel_thesis as thesis
from library.tools.reel_proposal import ReelMoment


REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO))

from library.tools import reel_quality_bar as qb
from library.tools.reel_proposal import (
    CallToAction,
)

STEP = REPO / "library" / "steps" / "step_3_05_judge_reels"


# ── A transcript small enough to reason about ────────────────────────

def _segment(start, end, speaker, text, bound=True):
    row = {"timeline_start": start, "timeline_end": end,
           "speaker": speaker, "text": text}
    if bound:
        row["resolve_item_id"] = f"item_{start}"
    return row


def _transcript(segments, duration=1000.0, fps=24000 / 1001):
    return {"segments": segments,
            "derived_from": {"duration_seconds": duration, "fps": fps}}


#: One conversation with a spoken closer at 200-206s, well away from the
#: body, so a moment can borrow it or not.
SEGMENTS = [
    _segment(10.0, 20.0, "SpeakerTwo", "so what actually changed about search"),
    _segment(20.0, 40.0, "SpeakerOne",
             "the question changed people ask a full sentence now"),
    _segment(40.0, 70.0, "SpeakerTwo",
             "and that means the old ranking game stops paying"),
    _segment(200.0, 206.0, "SpeakerOne",
             "jump on our site and run the free check"),
    _segment(300.0, 340.0, "SpeakerTwo", "a second conversation entirely here"),
]


def _moment(number=1, start=10.0, end=70.0, cta=None, slug="the-question"):
    return ReelMoment(number=number, slug=slug, reason="",
                      timeline_start=start, timeline_end=end,
                      call_to_action=cta)


CLOSER = CallToAction(timeline_start=200.0, timeline_end=206.0,
                      text="jump on our site and run the free check",
                      speaker="SpeakerOne")


def _project_with_tail_card(tmp_path):
    """A project declaring a 3.0s tail card, written under tmp_path.

    Beside the 19-frame freeze a CTA-closing reel inherits, this is the
    other half of the 3.8s standard ending the build appends - so a
    duration figure resolved against it must read ~69.8s for the 60s
    body plus 6s closer above, never 66.0s."""
    (tmp_path / "project.yaml").write_text(
        "effect:\n"
        "  full_frame_elements:\n"
        "    - element: full_frame_card\n"
        "      placement: tail\n"
        "      duration_seconds: 3.0\n"
        "      background: \"#101014\"\n"
        "      entrance: blur\n"
        "      exit: fade\n"
        "      font_family: Montserrat\n"
        "      runs:\n"
        "        - text: \"LUCIE\"\n"
        "          type_role: display\n"
        "          colour: \"#FFFFFF\"\n",
        encoding="utf-8")
    return tmp_path


# ── EXACT: duration is measured, never judged ────────────────────────


def test_length_is_never_a_gate_except_the_mechanical_absurd_bound():
    """2026-09-18, option c: length is never a gate.  A reel delivering
    40s, 96s or 166s carries NO duration finding at all - not an error
    and not a warning - and with a closer and a takeaway it PASSES.
    (Past five minutes the mechanical absurd bound still errors; that
    is not a window, it is where a candidate stops being a reel.)"""
    end = 170.0
    transcript = _transcript(SEGMENTS)
    moment = _moment(end=end, cta=CLOSER)
    assert not hasattr(qb, "QB_DURATION")
    codes = {f.code for f in qb.exact_findings(moment, transcript,
                                               qb.declared_closers([moment]))}
    assert not {c for c in codes
                if qb.FINDING_OWNERS.get(c) == "duration"}
    report = qb.judge([moment], transcript, {"readings": [{
        "reel": 1,
        "claim_quote": "so what actually changed",
        "opening_quote": "so what actually changed",
        "closing_quote": "run the free check",
        "closing_asks_for": "run the free check",
        "takeaway_quote": "people ask a full sentence now",
        "assumes_known": [],
        "stops_developing_at": 10.0,
        "rank": 1, "basis": "x",
    }]})
    verdict = report.verdicts[0]
    assert verdict.verdict == qb.PASS
    assert verdict.as_dict()["duration_gates"] is False
    # Duration still has a half that fails, and it is the one bound the
    # brief leaves standing - `reel_exchange.ABSURD_SECONDS`, past which a
    # candidate is most of the episode rather than a reel. Mechanical, not
    # editorial (AGENTS.md 10.5).
    #
    # Both directions, because a bound nothing can trip reads as coverage
    # (AGENTS.md 10.4).
    from library.tools.reel_exchange import ABSURD_SECONDS

    transcript = _transcript(SEGMENTS + [
        _segment(float(t), t + 4.0, "SpeakerTwo", f"and then point {t} follows")
        for t in range(104, 700, 4)], duration=1000.0)
    absurd = _moment(end=10.0 + ABSURD_SECONDS + 30.0)
    codes = {f.code: f for f in qb.exact_findings(absurd, transcript, {})}
    assert qb.QB_ABSURD_LENGTH in codes
    assert codes[qb.QB_ABSURD_LENGTH].severity == qb.ERROR
    assert qb.QB_ABSURD_LENGTH in {
        f.code for f in qb.judge([absurd], transcript).verdicts[0].errors}

    # And it does NOT fire on a reel that is merely long.
    long_but_real = _moment(end=10.0 + 170.0)
    assert qb.QB_ABSURD_LENGTH not in {
        f.code for f in qb.exact_findings(long_but_real, transcript, {})}


# ── EXACT: the call to action, all three sources ─────────────────────


def test_a_reel_whose_body_already_plays_a_closer_is_not_missing_one():
    """The false positive this check has to avoid.

    Seven of the field test's twenty-five carry their closer inside the
    body and declare none. Reading those as "no call to action" would be
    findings about correct output.
    """
    transcript = _transcript(SEGMENTS)
    declarer = _moment(number=1, cta=CLOSER)
    # Its body runs 150-206 and ENDS on the closer's own seconds.
    carries_it = _moment(number=2, start=150.0, end=206.0)
    closers = qb.declared_closers([declarer, carries_it])
    reading = qb.cta_reading(carries_it, transcript, closers)
    assert reading["source"] == "in_body"
    assert reading["is_the_ending"]
    codes = {f.code for f in qb.exact_findings(carries_it, transcript,
                                               closers)}
    assert qb.QB_CTA_ABSENT not in codes


def test_an_unreal_closer_is_a_finding_at_judging_time():
    """validate_proposal runs when the plan is WRITTEN and read_proposal
    does not re-run it, so a hand-edited plan reaches here unchecked."""
    table = [
        (CallToAction(timeline_start=20.0, timeline_end=40.0,
                      text="the question changed"), 1000.0, qb.QB_CTA_IN_BODY),
        (CallToAction(timeline_start=900.0, timeline_end=906.0,
                      text="something"), 500.0, qb.QB_CTA_OUTSIDE),
        (CallToAction(timeline_start=250.0, timeline_end=256.0, text=""),
         1000.0, qb.QB_CTA_SILENT),
    ]
    for cta, duration, code in table:
        transcript = _transcript(SEGMENTS, duration=duration)
        moment = _moment(cta=cta)
        closers = qb.declared_closers([moment])
        codes = {f.code for f in qb.exact_findings(moment, transcript, closers)}
        assert code in codes, (cta, code)


def test_a_shared_closer_is_counted_and_named_and_does_not_fail():
    """Reuse is BY DESIGN (`reel_proposal`'s docstring). What was missing
    was never a rule - it was the count."""
    transcript = _transcript(SEGMENTS)
    one = _moment(number=1, cta=CLOSER)
    two = _moment(number=2, start=300.0, end=340.0, cta=CLOSER)
    closers = qb.declared_closers([one, two])
    shared = [f for f in qb.exact_findings(one, transcript, closers)
              if f.code == qb.QB_CTA_SHARED]
    assert len(shared) == 1
    assert shared[0].severity == qb.WARNING
    assert "2" in shared[0].message


# ── The judge is never handed the answer sheet ───────────────────────

def test_the_real_handoff_names_none_of_the_criteria():
    qb.assert_ask_is_uncontaminated(
        (STEP / "handoff.md").read_text(encoding="utf-8"),
        "step 3.05's handoff")


def test_the_contamination_guard_fires_on_a_leaked_criterion():
    for leak in ("judge whether the reel is coherent",
                 "does it provide value to a viewer",
                 "is the opening a strong hook"):
        with pytest.raises(RuntimeError, match="READING"):
            qb.assert_ask_is_uncontaminated(leak)


def test_the_judge_is_given_the_reels_words_and_nothing_else():
    """The structural half of the anti-contamination design.

    The selector's own handoff states what a reel has to be and asks the
    selector to argue for every choice; a reader handed that argument
    would be reading the argument.
    """
    manifest = json.loads((STEP / "manifest.json").read_text())
    assert manifest["context_fields"] == ["reels_to_read"]

    sys.path.insert(0, str(STEP))
    try:
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "judge_reels_bridge", STEP / "bridge.py")
        bridge = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(bridge)
    finally:
        sys.path.remove(str(STEP))

    moment = _moment(cta=CLOSER)
    context = bridge.build_context({
        "timeline_transcript": _transcript(SEGMENTS),
        "reel_selection": {"moments": [moment.as_dict()]}})
    row = context["reels_to_read"][0]
    assert set(row) == {"reel", "runs_for_seconds", "lines"}
    assert set(row["lines"][0]) == {"at", "speaker", "says"}
    blob = json.dumps(context)
    for leaked in ("slug", "reason", "the-question", "concerns",
                   "duplicate_takes", "opening_observations",
                   "within_length_guidance"):
        assert leaked not in blob, f"{leaked!r} reached the reader"


def test_the_reader_weighs_the_reconciled_length(tmp_path):
    """Option c: the script reading judges length as part of judging
    the reel - so the length it is sent must be the one a viewer sits
    through.  The 60s body plus 6s closer reads 69.8s against a project
    declaring the 3.0s tail card (plus the inherited freeze), and the
    body 66.0s where no project resolves an ending."""
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "judge_reels_bridge3", STEP / "bridge.py")
    bridge = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(bridge)

    moment = _moment(cta=CLOSER)
    base = {"timeline_transcript": _transcript(SEGMENTS),
            "reel_selection": {"moments": [moment.as_dict()]}}
    with_project = bridge.build_context(
        {**base, "project_folder": str(_project_with_tail_card(tmp_path))})
    assert with_project["reels_to_read"][0]["runs_for_seconds"] == (
        pytest.approx(69.8, abs=0.05))
    without_project = bridge.build_context(dict(base))
    assert without_project["reels_to_read"][0]["runs_for_seconds"] == 66.0
    # The reel the old warning called short built with the ending: a
    # 60s body, a 6s closer, a 19-frame freeze inherited from the CTA and
    # a 3.0s tail card the project declares - 69.8s a viewer sits
    # through, resolved never restated.
    transcript = _transcript(SEGMENTS)
    moment = _moment(cta=CLOSER)
    project = _project_with_tail_card(tmp_path)
    reading = qb.duration_reading(moment, transcript, str(project))
    assert reading["ending_resolved"] is True
    assert reading["ending"]["ending_source"] == "inherited"
    assert reading["ending"]["freeze_seconds"] == pytest.approx(
        19 / (24000 / 1001))
    assert reading["ending"]["card_seconds"] == pytest.approx(3.0, abs=0.05)
    assert reading["ending_seconds"] == pytest.approx(3.8, abs=0.05)
    assert reading["delivered_seconds"] == pytest.approx(69.8, abs=0.05)


# ── A reading is checked against the reel, both directions ───────────

WORDS = ("so what actually changed about search "
         "the question changed people ask a full sentence now "
         "and that means the old ranking game stops paying")


def _reading(**over):
    entry = {
        "reel": 1,
        "claim_quote": "the question changed",
        "claim": "the question people ask changed",
        "opening_quote": "so what actually changed about search",
        "closing_quote": "the old ranking game stops paying",
        "closing_asks_for": "",
        "takeaway_quote": "people ask a full sentence now",
        "takeaway": "people search in sentences",
        "assumes_known": [],
        "stops_developing_at": 50.0,
        "rank": 1,
        "basis": "it finishes its own thought",
    }
    entry.update(over)
    return entry


def test_an_empty_takeaway_is_a_real_answer_and_not_a_refusal():
    reading = qb.read_one(_reading(takeaway_quote="", takeaway=""),
                          WORDS, 60.0)
    assert not reading.refused
    assert qb.value_of(reading) == qb.DELIVERS_NOTHING


# ── The verdicts are DERIVED, and never asked ────────────────────────


def test_a_refused_reading_reads_unjudged_rather_than_badly():
    """Evidence that failed its own check says nothing about the reel."""
    refused = qb.read_one(_reading(claim_quote="nothing like this"),
                          WORDS, 60.0)
    assert qb.coherence_of(refused) == qb.UNJUDGED
    assert qb.value_of(refused) == qb.UNJUDGED
    assert qb.coherence_of(None) == qb.UNJUDGED
    assert qb.value_of(None) == qb.UNJUDGED


# ── The bar over a batch ─────────────────────────────────────────────

def _batch():
    transcript = _transcript(SEGMENTS)
    good = _moment(number=1, cta=CLOSER)                    # 66s, closes
    short = _moment(number=2, start=300.0, end=340.0)       # 40s, no closer
    return transcript, [good, short]


def test_the_bar_separates_a_passing_reel_from_a_failing_one():
    transcript, moments = _batch()
    report = qb.judge(moments, transcript)
    verdicts = {v.number: v for v in report.verdicts}
    assert verdicts[1].verdict == qb.PASS
    assert verdicts[2].verdict == qb.FAIL
    assert len(report.failing) == 1 and len(report.passing) == 1


def test_a_reel_that_leans_on_the_episode_RECORDS_and_emits_no_signal():
    """Asked as "does it lean on anything unheard", the reading said
    not_followable on 29 of 31 script-QA moments, 20 of the 22 it
    approved - a column constant across approved and rejected alike,
    which is worse than no signal because it looks like diligence.
    Calibration had already been tried and failed
    (`COHERENCE_DOES_NOT_GATE`'s four halves), so the WARNING is
    REMOVED and only the recording stays: the derived value and every
    dependency placed at the word the reel says it, as evidence."""
    transcript, moments = _batch()
    words = qb.reel_text(moments[0], transcript)
    quote = words.split()[0]
    report = qb.judge(moments, transcript, {"readings": [{
        "reel": 1,
        "claim_quote": quote,
        "opening_quote": quote,
        "closing_quote": words.split()[-1],
        "closing_asks_for": "run the free check",
        "takeaway_quote": quote,
        "assumes_known": [{"what": "an earlier example", "quote": quote}],
        "stops_developing_at": 10.0,
        "rank": 1, "basis": "x",
    }]})
    first = next(v for v in report.verdicts if v.number == 1)
    # The observation is kept in full - it is evidence, not a verdict.
    assert first.coherence == qb.NOT_FOLLOWABLE
    assert not hasattr(qb, "QB_NOT_FOLLOWABLE")
    assert [f.code for f in first.findings
            if f.quality == "coherence"] == []
    # ... and the exact half that survived is still placed, word by word.
    assert first.dependencies
    assert first.dependencies[0]["position"] == qb.OPENING
    assert first.verdict == qb.PASS
    assert first.as_dict()["coherence_gates"] is False


def test_a_dependency_is_placed_at_the_word_the_reel_says_it():
    """The exact half that survived: WHERE, not WHETHER.

    Offset zero is the reel's first word - a position, not a window
    somebody chose - and the closer is the range `reel_ranges` lays down
    last."""
    transcript, moments = _batch()
    moment = moments[0]
    words = qb.reel_text(moment, transcript).split()
    opening, middle = words[0], words[len(words) // 2]
    reading = qb.read_one({
        "reel": 1, "claim_quote": opening, "opening_quote": opening,
        "closing_quote": words[-1], "closing_asks_for": "x",
        "takeaway_quote": opening,
        "assumes_known": [{"what": "opens on it", "quote": opening},
                          {"what": "later on", "quote": middle}],
        "stops_developing_at": 1.0, "rank": 1, "basis": "x",
    }, qb.reel_text(moment, transcript))
    assert not reading.refused
    placed = qb.dependency_positions(reading, moment, transcript)
    assert [p["position"] for p in placed] == [qb.OPENING, qb.BODY]
    assert placed[0]["at_word"] == 0
    assert placed[1]["at_word"] > 0
    # A reading nothing could check places nothing.
    assert qb.dependency_positions(None, moment, transcript) == []


def test_the_two_instruments_disagreeing_about_the_closer_is_reported():
    """The reader was never told a closer was wanted. If it read the last
    words as asking for nothing while the exact half found one placed
    last, one of them is wrong and a reader should look."""
    transcript, moments = _batch()
    words = qb.reel_text(moments[0], transcript)
    report = qb.judge(moments, transcript, {"readings": [{
        "reel": 1,
        "claim_quote": words.split()[0],
        "opening_quote": words.split()[0],
        "closing_quote": words.split()[-1],
        "closing_asks_for": "",
        "takeaway_quote": words.split()[0],
        "assumes_known": [],
        "stops_developing_at": 10.0,
        "rank": 1, "basis": "x",
    }]})
    first = next(v for v in report.verdicts if v.number == 1)
    codes = {f.code for f in first.findings}
    assert qb.QB_CTA_DISAGREEMENT in codes
    assert next(f for f in first.findings
                if f.code == qb.QB_CTA_DISAGREEMENT).severity == qb.WARNING
    # ... and in the other direction:
    # The reader was never told a closer was wanted, so a reel it says
    # ends on an invitation while the batch names none is real evidence
    # about the limit of `declared_closers`.
    transcript, moments = _batch()
    words = qb.reel_text(moments[1], transcript)
    report = qb.judge(moments, transcript, {"readings": [{
        "reel": 2,
        "claim_quote": words.split()[0],
        "opening_quote": words.split()[0],
        "closing_quote": words.split()[-1],
        "closing_asks_for": "go and look at the site",
        "takeaway_quote": words.split()[0],
        "assumes_known": [],
        "stops_developing_at": 5.0,
        "rank": 1, "basis": "x",
    }]})
    second = next(v for v in report.verdicts if v.number == 2)
    assert second.cta["source"] == "absent"
    disagreement = [f for f in second.findings
                    if f.code == qb.QB_CTA_DISAGREEMENT]
    assert len(disagreement) == 1
    assert "never named it" in disagreement[0].message


# ── The ranking is an ORDERING and there is no score ─────────────────

def test_an_unranked_reel_is_unplaced_and_not_last():
    transcript, moments = _batch()
    words = qb.reel_text(moments[0], transcript)
    report = qb.judge(moments, transcript, {"readings": [{
        "reel": 1,
        "claim_quote": words.split()[0],
        "opening_quote": words.split()[0],
        "closing_quote": words.split()[-1],
        "closing_asks_for": "run the free check",
        "takeaway_quote": words.split()[0],
        "assumes_known": [],
        "stops_developing_at": 5.0,
        "basis": "no place for it",
    }]})
    first = next(v for v in report.verdicts if v.number == 1)
    assert first.rank is None, (
        "a missing rank must never be coerced to 0 or to last - "
        "passage_engagement holds the same rule")


# ── The post-bridge derives, and refuses ─────────────────────────────

def _post_bridge():
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "judge_reels_post", STEP / "post_bridge.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_post_bridge_keeps_a_checked_reading_and_derives_its_verdicts():
    transcript, moments = _batch()
    words = qb.reel_text(moments[0], transcript)
    out = _post_bridge().resolve(
        {"readings": [{
            "reel": 1,
            "claim_quote": words.split()[0],
            "opening_quote": words.split()[0],
            "closing_quote": words.split()[-1],
            "closing_asks_for": "run the free check",
            "takeaway_quote": words.split()[0],
            "assumes_known": [],
            "stops_developing_at": 5.0,
            "rank": 1, "basis": "x"}]},
        {"timeline_transcript": transcript,
         "reel_selection": {"moments": [m.as_dict() for m in moments]}})
    judgement = out["reel_judgement"]
    assert len(judgement["readings"]) == 1
    assert judgement["readings"][0]["coherence"] == qb.FOLLOWABLE
    assert judgement["readings"][0]["value"] == qb.DELIVERS
    assert judgement["not_read"] == [2]
    assert judgement["refused"] == []


def test_the_post_bridge_refuses_a_reading_it_cannot_check():
    transcript, moments = _batch()
    out = _post_bridge().resolve(
        {"readings": [dict(_reading(reel=1),
                           claim_quote="not a thing anybody said")]},
        {"timeline_transcript": transcript,
         "reel_selection": {"moments": [m.as_dict() for m in moments]}})
    judgement = out["reel_judgement"]
    assert judgement["readings"] == []
    assert judgement["refused"][0]["reel"] == 1
    assert judgement["refused"][0]["why"]


# ── What the reel says is what it PLAYS ──────────────────────────────

def test_the_words_are_the_played_ranges_in_play_order():
    transcript = _transcript(SEGMENTS)
    moment = _moment(cta=CLOSER)
    text = qb.reel_text(moment, transcript)
    assert text.startswith("so what actually changed")
    assert text.endswith("run the free check"), (
        "the closer is played LAST, so it is the reel's last words")
    # The reel PLAYS it; a reader shown only the bound rows would be
    # reading a reel that does not exist.
    segments = list(SEGMENTS) + [
        _segment(45.0, 50.0, "SpeakerOne", "and nobody noticed", bound=False)]
    transcript = _transcript(segments)
    assert "nobody noticed" in qb.reel_text(_moment(), transcript)


def test_a_plan_nothing_can_lay_out_is_a_finding_not_a_crash():
    """`reel_ranges` RAISES for a closer under a frame. A batch report
    that died on one reel would say nothing about the other twenty-four,
    so the exception becomes the finding."""
    transcript = _transcript(SEGMENTS)
    sliver = CallToAction(timeline_start=200.0, timeline_end=200.01,
                          text="go")
    moment = _moment(cta=sliver)
    findings = qb.exact_findings(moment, transcript,
                                 qb.declared_closers([moment]))
    assert qb.QB_UNBUILDABLE in {f.code for f in findings}
    assert qb.delivered_seconds(moment, transcript) == 0.0
    assert qb.reel_text(moment, transcript) == ""


# ── What the bar said the first time it met real work ────────────────


# ── The fold into the conformance report ─────────────────────────────

def _reel_result(name, number):
    from library.tools.reel_conformance_verifier import ReelResult

    return ReelResult(
        reel_name=name, reel_number=number, plan_seconds=60.0,
        plan_frames=1440.0, actual_frames=1440, items_expected=0,
        items_actual=0, one_frame_holes=0, big_holes=[],
        captions_expected=0, captions_actual=0, speech_seconds=0.0,
        uncaptioned_seconds=0.0, uncaptioned_pct=0.0, short_captions=0,
        edge_cuts=0, bad_take_cuts=0, markers=0, findings=[])


def test_a_bar_finding_attaches_to_its_reel_or_is_reported():
    """A finding that quietly attaches to nothing makes the report read
    as though the bar had nothing to say (AGENTS.md 10.4)."""
    from library.tools.reel_conformance_verifier import attach_quality_bar

    transcript, moments = _batch()
    report = qb.judge(moments, transcript)
    failing = next(v for v in report.verdicts if v.findings)
    unattached = attach_quality_bar(report, [])
    assert any(failing.name in line for line in unattached)
    assert any("finding(s)" in line for line in unattached)
    # A built reel is named `Reel 20 - slug (selector redraw)` while the
    # plan calls it `Reel 20 - slug`. Matching on name alone attached 20 of
    # 25 on the field test and dropped five without a word.
    from library.tools.reel_conformance_verifier import attach_quality_bar

    transcript, moments = _batch()
    report = qb.judge(moments, transcript)
    results = [_reel_result(f"{v.name} (pipeline rebuild)", v.number)
               for v in report.verdicts]
    assert attach_quality_bar(report, results) == []
    attached = [f.finding_class for r in results for f in r.findings]
    assert qb.QB_CTA_ABSENT in attached


def test_the_closer_that_decides_is_the_LAST_one_the_reel_plays():
    """A reel can contain several.

    Field test reel 03 plays one closer at 321.6 and ends on a different
    one at 341.3; picking the earliest reported it as carrying a call to
    action but not ending on it, when it does. `is_the_ending` has to be
    a fact about the reel, not about which closer the iteration reached
    first.
    """
    transcript = _transcript(SEGMENTS + [
        _segment(96.0, 100.0, "SpeakerTwo", "go and check it out"),
        _segment(100.0, 104.0, "SpeakerOne", "the link is in our bio"),
    ])
    early = CallToAction(timeline_start=96.0, timeline_end=100.0,
                         text="go and check it out")
    late = CallToAction(timeline_start=100.0, timeline_end=104.0,
                        text="the link is in our bio")
    # A body that plays BOTH and ends on the later one.
    carries_both = _moment(number=3, start=40.0, end=104.0)
    closers = qb.declared_closers([
        _moment(number=1, start=200.0, end=260.0, cta=early),
        _moment(number=2, start=300.0, end=360.0, cta=late),
        carries_both])
    reading = qb.cta_reading(carries_both, transcript, closers)
    assert reading["span"] == [100.0, 104.0], (
        "the LAST closer the reel plays is the one that decides")
    assert reading["is_the_ending"]
    codes = {f.code for f in qb.exact_findings(carries_both, transcript,
                                               closers)}
    assert qb.QB_CTA_NOT_LAST not in codes
    # ... and the other direction:
    # The other direction: reels 23 and 24 play 1.2s of speech after
    # their closer and genuinely do not end on it.
    transcript = _transcript(SEGMENTS + [
        _segment(96.0, 100.0, "SpeakerTwo", "go and check it out"),
        _segment(100.0, 106.0, "SpeakerOne", "anyway that is the whole idea"),
    ])
    early = CallToAction(timeline_start=96.0, timeline_end=100.0,
                         text="go and check it out")
    past_it = _moment(number=3, start=40.0, end=106.0)
    closers = qb.declared_closers([
        _moment(number=1, start=200.0, end=260.0, cta=early), past_it])
    reading = qb.cta_reading(past_it, transcript, closers)
    assert reading["source"] == "in_body"
    assert not reading["is_the_ending"]
    findings = [f for f in qb.exact_findings(past_it, transcript, closers)
                if f.code == qb.QB_CTA_NOT_LAST]
    assert len(findings) == 1 and findings[0].severity == qb.WARNING


# ── No reel to read, no model asked ──────────────────────────────────

def test_no_readable_reel_makes_no_model_call(tmp_path, monkeypatch):
    """Measured 2026-10-01: with no reel's words to read, the step filed
    three handshakes, because the one honest answer - no readings -
    fails QA as empty. The bridge now stands the call down and the
    judgement says why (library/tools/nothing_to_decide.py)."""
    from library.processes.edit_video import run_pipeline

    def no_call(*_a, **_k):
        raise AssertionError("a model was asked to read no reel")

    monkeypatch.setattr(run_pipeline, "present_llm_step", no_call)
    monkeypatch.setenv("PYTHONPATH", str(REPO))
    silent = _moment(start=900.0, end=910.0)
    out = run_pipeline.run_hybrid_step(
        STEP, {"project_folder": str(tmp_path),
               "timeline_transcript": _transcript(SEGMENTS),
               "reel_selection": {"moments": [silent.as_dict()]}},
        node_id="judge_reels",
        manifest=json.loads((STEP / "manifest.json").read_text()),
        full_auto="agent", llm_timeout=1)

    judgement = out["reel_judgement"]
    assert judgement["readings"] == []
    assert judgement["not_read"] == [1]
    assert "1 unreadable" in judgement["not_asked"]
    assert "nothing_to_decide" not in out


# --------------------------------------------------------------------------
# From test_quality_bar_thesis_ending.py
#
# A closer-less reel a project declares ends on its own thesis passes.
#
# Fail-closed both ways: undeclared absent reels still fail, and a
# declaration whose anchor is neither timed nor in the approved preview
# fails too.
#
# History: `docs/evidence/reel_quality_bar.md` (test_quality_bar_thesis_ending.py).

sys.path.insert(0, str(REPO))


def _w(word, start, end):
    return {"word": word, "start": start, "end": end, "timed": True}


def _transcript_2():
    words_a = [_w(w, 10.0 + i * 0.5, 10.5 + i * 0.5)
               for i, w in enumerate(
                   "so what actually changed about search".split())]
    words_b = [_w(w, 20.0 + i * 0.5, 20.5 + i * 0.5)
               for i, w in enumerate(
                   "the question changed people ask now".split())]
    words_c = [_w(w, 40.0 + i * 0.5, 40.5 + i * 0.5)
               for i, w in enumerate(
                   "and that means the old ranking game stops paying".split())]
    return {
        "segments": [
            {"timeline_start": 10.0, "timeline_end": 20.0,
             "speaker": "SpeakerTwo",
             "text": "so what actually changed about search",
             "resolve_item_id": "item_10", "words": words_a},
            {"timeline_start": 20.0, "timeline_end": 40.0,
             "speaker": "SpeakerOne",
             "text": "the question changed people ask now",
             "resolve_item_id": "item_20", "words": words_b},
            {"timeline_start": 40.0, "timeline_end": 50.0,
             "speaker": "SpeakerTwo",
             "text": "and that means the old ranking game stops paying",
             "resolve_item_id": "item_40", "words": words_c},
        ],
        "derived_from": {"duration_seconds": 1000.0,
                         "fps": 24000 / 1001},
    }


def _moment_2(number=2, start=10.0, end=50.0, transcript_preview=""):
    return ReelMoment(number=number, slug="the-question", reason="",
                      timeline_start=start, timeline_end=end,
                      transcript_preview=transcript_preview,
                      call_to_action=None)


def _moment_with_cta(transcript_preview=""):
    return ReelMoment(
        number=2, slug="the-question", reason="",
        timeline_start=10.0, timeline_end=25.0,
        transcript_preview=transcript_preview,
        call_to_action=CallToAction(
            timeline_start=5.0, timeline_end=8.0,
            text="go check it out, the links in the bio",
            speaker="SpeakerTwo"))


def _transcript_with_cta():
    return {
        "segments": [
            {"timeline_start": 5.0, "timeline_end": 8.0,
             "speaker": "SpeakerTwo", "text": "go check it out the links in the bio",
             "resolve_item_id": "cta",
             "words": [
                 _w(word, 5.0 + i * 0.5, 5.4 + i * 0.5)
                 for i, word in enumerate([
                     "go", "check", "it", "out", "the", "links",
                     "in", "bio"])]},
            {"timeline_start": 10.0, "timeline_end": 20.0,
             "speaker": "SpeakerTwo", "text": "so what actually changed about search",
             "resolve_item_id": "body_1",
             "words": [
                 _w(word, 10.0 + i * 0.5, 10.4 + i * 0.5)
                 for i, word in enumerate([
                     "so", "what", "actually", "changed", "about",
                     "search"])]},
            {"timeline_start": 20.0, "timeline_end": 25.0,
             "speaker": "SpeakerOne", "text": "and AI really likes that",
             "resolve_item_id": "body_2",
             "words": [
                 _w(word, 20.0 + i * 0.5, 20.4 + i * 0.5)
                 for i, word in enumerate([
                     "and", "AI", "really", "likes", "that"])]},
        ],
        "derived_from": {"duration_seconds": 1000.0,
                         "fps": 24000 / 1001},
    }


def _thesis(anchor):
    return {"reel": "Reel 02 - the-question",
            "ends_on": {"anchor_phrase": anchor},
            "tail_element": "none",
            "reason": "test: captain-authorised re-cut ends on its thesis"}


def test_declared_and_honoured_thesis_reads_thesis_not_absent():
    transcript = _transcript_2()
    moment = _moment_2()
    thesis = _thesis("ranking game stops paying")
    reading = qb.cta_reading(moment, transcript, {}, thesis=thesis)
    assert reading["source"] == "thesis"
    assert reading["is_the_ending"]
    assert reading["span"][0] < reading["span"][1]
    codes = {f.code for f in qb.exact_findings(moment, transcript, {},
                                               thesis=thesis)}
    assert qb.QB_CTA_ABSENT not in codes


def test_an_unhonoured_or_missing_declaration_still_fails():
    """Fail-closed both ways: no declaration, and a declaration whose
    anchor is true words of the reel but its OPENING, not its tail."""
    transcript = _transcript_2()
    moment = _moment_2()
    for thesis in (None, _thesis("so what actually changed")):
        kwargs = {} if thesis is None else {"thesis": thesis}
        reading = qb.cta_reading(moment, transcript, {}, **kwargs)
        assert reading["source"] == "absent"
        codes = {f.code for f in qb.exact_findings(moment, transcript, {},
                                                   **kwargs)}
        assert qb.QB_CTA_ABSENT in codes


def test_recorded_ending_in_approved_preview_passes_when_timing_omits_words():
    transcript = _transcript_2()
    anchor = "clearly saying why you're better than your competitor"
    moment = _moment_2(
        transcript_preview=(
            "The captain-approved answer ends with clearly saying why "
            "you're better than your competitor. A later exchange follows."))
    thesis = _thesis(anchor)

    reading = qb.cta_reading(moment, transcript, {}, thesis=thesis)
    codes = {f.code for f in qb.exact_findings(
        moment, transcript, {}, thesis=thesis)}

    assert reading["source"] == "thesis"
    assert reading["span"] is None
    assert "approved moment preview" in reading["evidence"]
    assert qb.QB_CTA_ABSENT not in codes


def test_a_recorded_body_ending_removes_a_planned_cta_from_the_batch_check(
        tmp_path):
    moment = _moment_with_cta()
    transcript = _transcript_with_cta()
    ending = _thesis("and AI really likes that")
    external = tmp_path / "external"
    external.mkdir()
    (external / "reel_ending.json").write_text(json.dumps({
        "version": 1,
        "endings": [ending],
    }), encoding="utf-8")

    report = qb.judge([moment], transcript, None,
                      project_folder=str(tmp_path))

    assert report.verdicts[0].cta["source"] == "thesis"
    assert qb.QB_CTA_ABSENT not in {
        f.code for f in report.verdicts[0].findings}


def test_preview_phrase_inside_body_does_not_remove_a_planned_cta(tmp_path):
    moment = _moment_with_cta(
        transcript_preview=(
            "clearly saying why you're better than your competitor. "
            "A follow-up thought comes after it."))
    ending = _thesis("clearly saying why you're better than your competitor")
    external = tmp_path / "external"
    external.mkdir()
    (external / "reel_ending.json").write_text(json.dumps({
        "version": 1,
        "endings": [ending],
    }), encoding="utf-8")

    report = qb.judge([moment], _transcript_with_cta(), None,
                      project_folder=str(tmp_path))

    assert report.verdicts[0].cta["source"] == "declared"


# --------------------------------------------------------------------------
# From test_reel_thesis.py
#
# The thesis check separates the five incoherent reels (04, 10, 13, 14,
# 27) from their fixes; verdicts are derived, never scored, and an answer
# that cannot be checked reads as unjudged. History and fixture provenance:
# docs/evidence/reel_thesis.md ("The acceptance tests").

STEP_2 = 0.4


def _segment_2(start: float, text: str, speaker: str = "SpeakerOne"):
    """One bound transcript segment with evenly spaced timed words."""
    tokens = text.split()
    timed = [{"word": token, "start": round(start + i * STEP_2, 3),
              "end": round(start + (i + 1) * STEP_2, 3), "timed": True}
             for i, token in enumerate(tokens)]
    end = round(start + len(tokens) * STEP_2, 3)
    return {"timeline_start": start, "timeline_end": end,
            "speaker": speaker, "text": text, "words": timed,
            "resolve_item_id": f"clip-{start:.1f}"}


def _transcript_3(*segments):
    return {"segments": list(segments),
            "derived_from": {"fps": 23.976}}


def _moment_3(number: int, body: tuple, closer=None, slug: str = "topic"):
    data = {"number": number, "slug": slug,
            "timeline_start": float(body[0]), "timeline_end": float(body[1]),
            "approval": "approved"}
    if closer is not None:
        data["call_to_action"] = {"timeline_start": float(closer[0]),
                                  "timeline_end": float(closer[1])}
    return ReelMoment.from_dict(data)


def _sibling_five():
    """A far-away moment 5, so foreign claims have somewhere to verify."""
    return _moment_3(5, (320.0, 340.0), slug="elsewhere")


def _check(moment, transcript, siblings, answer):
    """The engine's verdict on one hand-written model answer."""
    check = thesis.survey_verification(moment, transcript, siblings)
    return thesis.read_thesis_answer(
        answer, words=check["words"], kept=check["kept"],
        own_body=check["own_body"], own_closer=check["own_closer"],
        siblings=check["siblings"])


def _ctx(moment, transcript, siblings):
    return thesis.thesis_context(moment, transcript, siblings)


# ── Reel 04: point cut off one sentence past the boundary ──────────

R04_BODY = "we ran the side by side test with specific reasons why"
R04_CLOSER = "so if you are watching this definitely check it out"
R04_POINT = "one is a search engine the other is a decision engine"


def _reel_04_failing():
    body = _segment_2(100.0, R04_BODY)
    closer = _segment_2(500.0, R04_CLOSER)
    transcript = _transcript_3(body, closer)
    moment = _moment_3(4, (100.0, body["timeline_end"]),
                     (500.0, closer["timeline_end"]))
    return moment, transcript


# ── Reel 10: the answer skipped for a sales pitch ──────────────────

R10_BODY = "the audit found everything else was broken"
R10_CLOSER = "what we do different book a score call with you"
R10_ANSWER = "your website is your resume and everything else are your references"


def _reel_10_failing():
    body = _segment_2(200.0, R10_BODY)
    closer = _segment_2(800.0, R10_CLOSER)
    transcript = _transcript_3(body, closer)
    moment = _moment_3(10, (200.0, body["timeline_end"]),
                     (800.0, closer["timeline_end"]))
    return moment, transcript


# ── Reel 13: moment 5's opener playing after the closer ────────────

R13_BODY = "the accounting firm blamed ai but it was an information problem"
R13_CTA = "see how your brand appears links in our bio"
R13_TAIL = "so what we are hearing they search their business"


def _reel_13_pieces():
    body = _segment_2(889.0, R13_BODY)
    cta = _segment_2(320.0, R13_CTA)
    tail = _segment_2(cta["timeline_end"], R13_TAIL, speaker="SpeakerTwo")
    transcript = _transcript_3(body, cta, tail)
    return body, cta, tail, transcript


def test_reel_13_reads_incoherent_with_the_tail_and_coherent_fixed():
    body, cta, tail, transcript = _reel_13_pieces()
    # This fixture explicitly approves a closer through SpeakerTwo's whole
    # sentence. The build's large-snap guard prevents that cascade from
    # an edge before the sentence, but a thesis check still judges a
    # plan that already includes the sentence.
    moment = _moment_3(13, (889.0, body["timeline_end"]),
                     (320.0, tail["timeline_end"]))
    sibling = _moment_3(5, (320.0, 340.0), slug="opener")
    siblings = [moment, sibling]
    context = _ctx(moment, transcript, siblings)
    assert R13_TAIL in context["kept_text"]
    answer = {
        "point": "the firm blamed ai for invisibility but the gap was information",
        "point_quote": "it was an information problem",
        "last_follows": "does_not_follow",
        "closing_quote": "they search their business",
        "closing_reason": "the closer lands, but this extra sentence "
                          "changes its ending to a different conversation",
        "foreign_spans": [],
        "reason": "the approved closer continues into a sentence that "
                  "does not follow the information story",
    }
    read = _check(moment, transcript, siblings, answer)
    assert read["verdict"] == "incoherent"
    assert read["decided_by"] == ["ending"]
    # the fix - the closer ends on its own words - reads coherent
    body, cta, tail, transcript = _reel_13_pieces()
    moment = _moment_3(13, (889.0, body["timeline_end"]),
                     (320.0, cta["timeline_end"]))
    sibling = _moment_3(5, (320.0, 340.0), slug="opener")
    siblings = [moment, sibling]
    context = _ctx(moment, transcript, siblings)
    assert R13_TAIL not in context["kept_text"]
    answer = {
        "point": "the firm blamed ai for invisibility but the gap was information",
        "point_quote": "it was an information problem",
        "last_follows": "follows",
        "closing_quote": "links in our bio",
        "closing_reason": "the last words land the invitation the verdict set up",
        "foreign_spans": [],
        "reason": "whodunnit, verdict, invitation",
    }
    read = _check(moment, transcript, siblings, answer)
    assert read["verdict"] == "coherent"


def test_shared_closer_never_verifies_as_foreign():
    """Reuse is not a defect: a closer inside its own declared window
    cannot verify as foreign, even where a sibling body covers it."""
    body, cta, tail, transcript = _reel_13_pieces()
    moment = _moment_3(13, (889.0, body["timeline_end"]),
                     (320.0, cta["timeline_end"]))
    sibling = _moment_3(5, (320.0, 340.0), slug="opener")
    siblings = [moment, sibling]
    answer = {
        "point": "the firm blamed ai for invisibility but the gap was information",
        "point_quote": "it was an information problem",
        "last_follows": "follows",
        "closing_quote": "links in our bio",
        "closing_reason": "the last words land the invitation",
        "foreign_spans": [{"quote": R13_CTA,
                           "why": "a suspicious reader flags the shared clip"}],
        "reason": "the flag is checked, not honoured",
    }
    read = _check(moment, transcript, siblings, answer)
    assert read["verdict"] == "unjudged"
    assert "own declared windows" in read["reason"]


# ── Reel 14: setup good, closer good, middle missing ───────────────

R14_SETUP = "more content less visibility it is brutal yeah that is really crazy"
R14_CLOSER = "content heavy seo strategy lucie visibility system"
R14_MIDDLE = "the biggest geo mistake is keyword stuffing it rewards understanding and niche unique content"


def _reel_14_failing():
    body = _segment_2(200.0, R14_SETUP)
    closer = _segment_2(600.0, R14_CLOSER)
    transcript = _transcript_3(body, closer)
    moment = _moment_3(14, (200.0, body["timeline_end"]),
                     (600.0, closer["timeline_end"]))
    return moment, transcript


def test_a_reel_missing_its_point_and_ending_reads_incoherent():
    """Reels 04, 10 and 14: the point is cut out of the kept words and the
    closer follows nothing the reel set up."""
    table = [
        (_reel_04_failing, R04_POINT, R04_CLOSER,
         "the reel sets up a side by side test but never says which tool "
         "won or why it matters"),
        (_reel_10_failing, R10_ANSWER, R10_CLOSER,
         "the reel tells an audit horror story but never says what the "
         "audit means"),
        (_reel_14_failing, "keyword stuffing", R14_CLOSER,
         "the reel agrees things are brutal and then pitches, with no "
         "substance between"),
    ]
    for build, missing, closer, point in table:
        moment, transcript = build()
        siblings = [moment, _sibling_five()]
        assert missing not in _ctx(moment, transcript, siblings)["kept_text"]
        answer = {
            "point": point,
            "point_quote": "",
            "last_follows": "does_not_follow",
            "closing_quote": closer,
            "closing_reason": "the last words ask for something the reel "
                              "never set up",
            "foreign_spans": [],
            "reason": "no takeaway, then an unrelated ask",
        }
        read = _check(moment, transcript, siblings, answer)
        assert read["verdict"] == "incoherent", moment.number
        assert read["decided_by"] == ["point", "ending"], moment.number


# ── Reel 27: the closer answers a question never asked ─────────────

R27_BODY = "google reviews build trust niche reviewer language gets picked up and recommended later on"
R27_CLOSER = R04_CLOSER


def _reel_27_failing():
    body = _segment_2(700.0, R27_BODY)
    closer = _segment_2(500.0, R27_CLOSER)
    transcript = _transcript_3(body, closer)
    moment = _moment_3(27, (700.0, body["timeline_end"]),
                     (500.0, closer["timeline_end"]))
    return moment, transcript


def test_reel_27_failing_reads_incoherent_on_the_ending_alone():
    moment, transcript = _reel_27_failing()
    siblings = [moment, _sibling_five()]
    answer = {
        "point": "reviews build trust and get a business recommended later",
        "point_quote": "niche reviewer language gets picked up and recommended later on",
        "last_follows": "does_not_follow",
        "closing_quote": R27_CLOSER,
        "closing_reason": "the last words ask for a website checkout the "
                          "reviews answer never set up",
        "foreign_spans": [],
        "reason": "a complete answer followed by an unrelated ask",
    }
    read = _check(moment, transcript, siblings, answer)
    assert read["verdict"] == "incoherent"
    assert read["decided_by"] == ["ending"]


# ── The ask carries words, not verdicts ────────────────────────────

def test_prompt_carries_the_kept_words_and_no_verdict_vocabulary():
    moment, transcript = _reel_04_failing()
    context = _ctx(moment, transcript, [moment, _sibling_five()])
    prompt = thesis.render_thesis_prompt(context)
    assert "specific reasons why" in prompt
    assert "definitely check it out" in prompt
    assert "SpeakerOne:" in prompt
    thesis.assert_ask_carries_no_verdict(prompt)


# ── Uncheckable answers are unjudged, never verdicts ───────────────

def _good_answer_for_04():
    return {
        "point": "x",
        "point_quote": "specific reasons why",
        "last_follows": "follows",
        "closing_quote": "check it out",
        "closing_reason": "y",
        "foreign_spans": [],
        "reason": "z",
    }


def test_an_uncheckable_answer_is_unjudged():
    moment, transcript = _reel_04_failing()
    siblings = [moment, _sibling_five()]
    assert _check(moment, transcript, siblings, None)["verdict"] == "unjudged"
    answer = _good_answer_for_04()
    answer["point_quote"] = "decision engine"     # invented: not in the reel
    read = _check(moment, transcript, siblings, answer)
    assert read["verdict"] == "unjudged"
    assert "not in what this reel says" in read["reason"]


def test_numeric_fields_beside_a_reading_are_dropped_unread():
    """There is no magnitude in this judgement: a score beside the
    answers can neither save nor sink them."""
    moment, transcript = _reel_04_failing()
    siblings = [moment, _sibling_five()]
    answer = _good_answer_for_04()
    answer["score"] = 0.1
    answer["confidence"] = 0.99
    answer["rating"] = 5
    read = _check(moment, transcript, siblings, answer)
    assert read["verdict"] == "coherent"
    assert read["decided_by"] == []


# ── The promotion gate fails open, refuses fresh incoherence ───────

def _write_project(tmp_path, moments, transcript):
    from library.tools.reel_proposal import proposal_path
    from library.tools.timeline_transcript import transcript_path

    proposal_file = proposal_path(str(tmp_path))
    proposal_file.parent.mkdir(parents=True, exist_ok=True)
    proposal_file.write_text(json.dumps({
        "format": "reel_proposal/1",
        "moment_count": len(moments),
        "moments": [m.as_dict() for m in moments],
    }), encoding="utf-8")
    transcript_file = transcript_path(str(tmp_path))
    transcript_file.parent.mkdir(parents=True, exist_ok=True)
    transcript_file.write_text(json.dumps(transcript), encoding="utf-8")
    return str(tmp_path)


def test_gate_refuses_a_fresh_incoherent_and_promotes_the_rest():
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as tmp:
        project = Path(tmp)
        moment4, transcript4 = _reel_04_failing()
        body27 = _segment_2(700.0, R27_BODY)
        transcript = {"segments": transcript4["segments"] + [body27],
                      "derived_from": {"fps": 23.976}}
        moment27 = _moment_3(27, (700.0, body27["timeline_end"]))
        sibling = _sibling_five()
        moments = [moment4, moment27, sibling]
        _write_project(project, moments, transcript)
        ctx4 = thesis.thesis_context(moment4, transcript, moments)
        ctx27 = thesis.thesis_context(moment27, transcript, moments)
        thesis.write_thesis_verdicts(str(project), {
            "4": {"verdict": "incoherent", "reason": "no point, alien ask",
                  "decided_by": ["point", "ending"],
                  "judged_by": "model", "judged_at": "2026-09-22T00:00Z",
                  "content_hash": ctx4["content_hash"],
                  "word_count": ctx4["word_count"]},
            "27": {"verdict": "coherent", "reason": "lands its answer",
                   "decided_by": [],
                   "judged_by": "model", "judged_at": "2026-09-22T00:00Z",
                   "content_hash": ctx27["content_hash"],
                   "word_count": ctx27["word_count"]}})
        decision = thesis.gate_promotion(
            str(project), {"Reel 04 - topic": "Reel 04 - topic (staging)",
                           "Reel 27 - topic": "Reel 27 - topic (staging)"})
        assert list(decision["promotable"]) == ["Reel 27 - topic"]
        assert list(decision["refused"]) == ["Reel 04 - topic"]
        assert "no point, alien ask" in decision["refused"]["Reel 04 - topic"]


def test_gate_promotes_on_stale_missing_and_unjudged():
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as tmp:
        project = Path(tmp)
        moment, transcript = _reel_04_failing()
        moments = [moment, _sibling_five()]
        _write_project(project, moments, transcript)
        thesis.write_thesis_verdicts(str(project), {
            "4": {"verdict": "incoherent", "reason": "over old words",
                  "decided_by": ["point"],
                  "judged_by": "model", "judged_at": "2026-09-22T00:00Z",
                  "content_hash": "deadbeef0000", "word_count": 3},
            "5": {"verdict": "misfit?", "reason": "not a thesis verdict",
                  "decided_by": [],
                  "judged_by": "model", "judged_at": "2026-09-22T00:00Z",
                  "content_hash": "deadbeef0001", "word_count": 1}})
        decision = thesis.gate_promotion(
            str(project), {"Reel 04 - topic": "Reel 04 - topic (staging)",
                           "Reel 05 - elsewhere": "Reel 05 - elsewhere (staging)"})
        assert sorted(decision["promotable"]) == \
            ["Reel 04 - topic", "Reel 05 - elsewhere"]
        assert decision["refused"] == {}
        assert any("STALE" in line for line in decision["lines"])
