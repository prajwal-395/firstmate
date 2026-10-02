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

import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO))

from library.tools import reel_quality_bar as qb
from library.tools.reel_proposal import (
    CallToAction,
    ReelMoment,
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
    _segment(10.0, 20.0, "Craig", "so what actually changed about search"),
    _segment(20.0, 40.0, "Akshita",
             "the question changed people ask a full sentence now"),
    _segment(40.0, 70.0, "Craig",
             "and that means the old ranking game stops paying"),
    _segment(200.0, 206.0, "Akshita",
             "jump on our site and run the free check"),
    _segment(300.0, 340.0, "Craig", "a second conversation entirely here"),
]


def _moment(number=1, start=10.0, end=70.0, cta=None, slug="the-question"):
    return ReelMoment(number=number, slug=slug, reason="",
                      timeline_start=start, timeline_end=end,
                      call_to_action=cta)


CLOSER = CallToAction(timeline_start=200.0, timeline_end=206.0,
                      text="jump on our site and run the free check",
                      speaker="Akshita")


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
        _segment(float(t), t + 4.0, "Craig", f"and then point {t} follows")
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
        _segment(45.0, 50.0, "Akshita", "and nobody noticed", bound=False)]
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
        _segment(96.0, 100.0, "Craig", "go and check it out"),
        _segment(100.0, 104.0, "Akshita", "the link is in our bio"),
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
        _segment(96.0, 100.0, "Craig", "go and check it out"),
        _segment(100.0, 106.0, "Akshita", "anyway that is the whole idea"),
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
