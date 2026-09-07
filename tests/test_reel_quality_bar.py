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

REPO = Path(__file__).resolve().parents[1]
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


# ── The four qualities are declared, and the declaration is checked ──

def test_the_four_qualities_are_the_captains_four():
    assert [q.name for q in qb.QUALITIES] == [
        "duration", "call_to_action", "coherence", "value"]
    assert [q.kind for q in qb.QUALITIES] == [
        qb.EXACT, qb.EXACT, qb.JUDGEMENT, qb.JUDGEMENT]


def test_a_quality_that_names_nothing_real_is_refused():
    """The well-formedness check runs at import; this is that it can fail."""
    original = qb.QUALITIES
    try:
        qb.QUALITIES = original + (
            qb.Quality(name="vibes", kind=qb.EXACT, asked="?",
                       held_by=("measure_the_vibes",)),)
        with pytest.raises(RuntimeError, match="measure_the_vibes"):
            qb.assert_qualities_are_well_formed()
    finally:
        qb.QUALITIES = original
    qb.assert_qualities_are_well_formed()


def test_there_is_no_third_kind():
    original = qb.QUALITIES
    try:
        qb.QUALITIES = original + (
            qb.Quality(name="x", kind="sort_of", asked="?", held_by=("a",)),)
        with pytest.raises(RuntimeError, match="neither"):
            qb.assert_qualities_are_well_formed()
    finally:
        qb.QUALITIES = original


def test_the_coherence_ruling_carries_the_looking_that_produced_it():
    """"No deterministic half exists" is only worth anything with the
    measurements in it, so the record has to keep them.

    Same bar `FIRST_MEASUREMENT` is held to: a design argument that has
    never met the material is a design argument.
    """
    record = qb.COHERENCE_DOES_NOT_GATE
    candidates = record["candidates_measured"]
    assert len(candidates) >= 4
    for candidate in candidates:
        assert candidate["half"].strip()
        assert candidate["measured"].strip()
        assert candidate["rejected"].strip()
        assert isinstance(candidate["needs_a_model"], bool)
    # At least one that needed NO model was tried, or the search was
    # never made.
    assert any(not c["needs_a_model"] for c in candidates)
    assert "31" in record["why"]


def test_the_length_band_is_the_one_reel_exchange_declares():
    """A guidance spelled twice is this repository's dominant bug class."""
    from library.tools import reel_conformance_verifier as verifier
    from library.tools.reel_exchange import LENGTH_GUIDANCE

    assert qb.LENGTH_GUIDANCE is LENGTH_GUIDANCE
    assert (verifier.REEL_LENGTH_MIN,
            verifier.REEL_LENGTH_MAX) == LENGTH_GUIDANCE


# ── EXACT: duration, both directions ─────────────────────────────────

def test_a_reel_inside_the_band_produces_no_duration_finding():
    transcript = _transcript(SEGMENTS)
    moment = _moment(end=70.0)  # 60s
    reading = qb.duration_reading(moment, transcript)
    assert reading["delivered_seconds"] == 60.0
    assert reading["within_guidance"]
    findings = qb.exact_findings(moment, transcript, {})
    assert [f.code for f in findings if f.code == qb.QB_DURATION] == []


@pytest.mark.parametrize("end,side", [(40.0, "under"), (170.0, "over")])
def test_a_reel_outside_the_band_is_REPORTED_and_does_not_fail(end, side):
    """The brief says "no fixed target ... preferably between 45-90
    seconds" and "No hard cap", and settles it with "a coherent
    90-second reel is right, a stitched 47-second one is not" - 47s
    being inside the band. So the band is measured and reported, and it
    does not decide (reel_quality_bar's docstring carries the reading).

    The MEASUREMENT is unchanged: the finding still fires, still names
    the side, and `within_guidance` still reads False."""
    transcript = _transcript(SEGMENTS)
    moment = _moment(end=end)
    reading = qb.duration_reading(moment, transcript)
    assert not reading["within_guidance"]
    findings = [f for f in qb.exact_findings(moment, transcript, {})
                if f.code == qb.QB_DURATION]
    assert len(findings) == 1
    assert findings[0].severity == qb.WARNING
    assert side in findings[0].message
    # Nothing about its LENGTH fails it. (The bare moment carries no
    # closer, which is a separate quality and still an error.)
    errors = {f.code for f in qb.judge([moment], transcript)
              .verdicts[0].errors}
    assert qb.QB_DURATION not in errors
    assert qb.QB_ABSURD_LENGTH not in errors


def test_the_length_ERROR_is_the_mechanical_bound_and_it_CAN_fire():
    """Duration still has a half that fails, and it is the one bound the
    brief leaves standing - `reel_exchange.ABSURD_SECONDS`, past which a
    candidate is most of the episode rather than a reel. Mechanical, not
    editorial (AGENTS.md 10.5).

    Both directions, because a bound nothing can trip reads as coverage
    (AGENTS.md 10.4)."""
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


def test_the_duration_measured_is_what_plays_not_the_body_window():
    """Body, minus bad takes, plus the closer - three different numbers."""
    transcript = _transcript(SEGMENTS)
    moment = _moment(cta=CLOSER)
    reading = qb.duration_reading(moment, transcript)
    assert reading["body_seconds"] == 60.0
    assert reading["closer_seconds"] == 6.0
    assert reading["delivered_seconds"] == 66.0


# ── EXACT: the call to action, all three sources ─────────────────────

def test_a_declared_closer_reads_as_declared_and_raises_nothing():
    transcript = _transcript(SEGMENTS)
    moment = _moment(cta=CLOSER)
    closers = qb.declared_closers([moment])
    reading = qb.cta_reading(moment, transcript, closers)
    assert reading["source"] == "declared"
    assert reading["is_the_ending"]
    codes = {f.code for f in qb.exact_findings(moment, transcript, closers)}
    assert qb.QB_CTA_ABSENT not in codes


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


def test_a_reel_that_plays_no_closer_at_all_is_a_finding():
    transcript = _transcript(SEGMENTS)
    declarer = _moment(number=1, cta=CLOSER)
    bare = _moment(number=2, start=300.0, end=340.0)
    closers = qb.declared_closers([declarer, bare])
    codes = {f.code for f in qb.exact_findings(bare, transcript, closers)}
    assert qb.QB_CTA_ABSENT in codes


def test_a_batch_that_names_no_closer_says_so_rather_than_going_quiet():
    """`declared_closers` is the whole evidence base, and an empty one
    establishes nothing - which is a finding, not a silence."""
    transcript = _transcript(SEGMENTS)
    moment = _moment()
    findings = [f for f in qb.exact_findings(moment, transcript, {})
                if f.code == qb.QB_CTA_ABSENT]
    assert len(findings) == 1
    assert "no moment in this batch names a closer" in \
        findings[0].message.lower()


def test_a_closer_inside_its_own_body_is_refused_again_at_judging_time():
    """`validate_proposal` runs when the plan is WRITTEN and
    `read_proposal` does not re-run it, so a hand-edited plan reaches
    here unchecked."""
    transcript = _transcript(SEGMENTS)
    inside = CallToAction(timeline_start=20.0, timeline_end=40.0,
                          text="the question changed")
    moment = _moment(cta=inside)
    closers = qb.declared_closers([moment])
    codes = {f.code for f in qb.exact_findings(moment, transcript, closers)}
    assert qb.QB_CTA_IN_BODY in codes


def test_a_closer_outside_the_episode_is_a_finding():
    transcript = _transcript(SEGMENTS, duration=500.0)
    beyond = CallToAction(timeline_start=900.0, timeline_end=906.0,
                          text="something")
    moment = _moment(cta=beyond)
    closers = qb.declared_closers([moment])
    codes = {f.code for f in qb.exact_findings(moment, transcript, closers)}
    assert qb.QB_CTA_OUTSIDE in codes


def test_a_closer_nobody_speaks_in_is_a_finding():
    transcript = _transcript(SEGMENTS)
    silent = CallToAction(timeline_start=250.0, timeline_end=256.0, text="")
    moment = _moment(cta=silent)
    closers = qb.declared_closers([moment])
    codes = {f.code for f in qb.exact_findings(moment, transcript, closers)}
    assert qb.QB_CTA_SILENT in codes


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


def test_a_closer_used_once_raises_no_sharing_finding():
    transcript = _transcript(SEGMENTS)
    one = _moment(number=1, cta=CLOSER)
    closers = qb.declared_closers([one])
    assert [f for f in qb.exact_findings(one, transcript, closers)
            if f.code == qb.QB_CTA_SHARED] == []


# ── The judge is never handed the answer sheet ───────────────────────

def test_the_real_handoff_names_none_of_the_criteria():
    qb.assert_ask_is_uncontaminated(
        (STEP / "handoff.md").read_text(encoding="utf-8"),
        "step 3.05's handoff")


def test_the_whole_assembled_prompt_names_none_of_the_criteria():
    """The handoff is not the whole prompt.

    `present_llm_step` prepends the craft role and appends the
    `could_not_determine` block, and a criterion arriving through either
    would contaminate the reader exactly as one in the handoff would.
    """
    from library.tools import craft_role, undetermined

    assembled = (craft_role.prompt_block("judge_reels")
                 + (STEP / "handoff.md").read_text(encoding="utf-8")
                 + undetermined.prompt_block())
    assert craft_role.prompt_block("judge_reels"), (
        "3.05 has no craft role, so this test is asserting nothing")
    qb.assert_ask_is_uncontaminated(assembled, "the assembled prompt")


@pytest.mark.parametrize("leak", [
    "judge whether the reel is coherent",
    "does it provide value to a viewer",
    "reels should run 45 to 90 seconds",
    "does it end on a call to action",
    "score each reel out of ten",
    "is the opening a strong hook",
])
def test_the_contamination_guard_fires_on_a_leaked_criterion(leak):
    with pytest.raises(RuntimeError, match="READING"):
        qb.assert_ask_is_uncontaminated(leak)


def test_the_guard_does_not_fire_on_ordinary_wording():
    """A guard that fires on correct wording gets disabled.

    'passage' contains 'pass' and is the ordinary word for a stretch of
    speech; matching at word boundaries is what keeps it usable.
    """
    qb.assert_ask_is_uncontaminated(
        "copy the passage where it happens, and say what it assumes")


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


def test_the_bridge_refuses_rather_than_reading_nothing():
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "judge_reels_bridge2", STEP / "bridge.py")
    bridge = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(bridge)

    with pytest.raises(bridge.JudgeReelsRefused, match="timeline_transcript"):
        bridge.build_context({"reel_selection": {"moments": []}})
    with pytest.raises(bridge.JudgeReelsRefused, match="reel_selection"):
        bridge.build_context({"timeline_transcript": _transcript(SEGMENTS)})


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


def test_a_reading_quoted_from_the_reel_is_kept():
    reading = qb.read_one(_reading(), WORDS, 60.0)
    assert not reading.refused
    assert reading.ungrounded == ()
    assert reading.misplaced == ()


def test_a_quote_that_is_not_in_the_reel_refuses_the_whole_reading():
    reading = qb.read_one(
        _reading(claim_quote="artificial intelligence is eating search"),
        WORDS, 60.0)
    assert reading.refused
    assert any("claim_quote" in why for why in reading.ungrounded)


def test_punctuation_and_case_do_not_break_a_real_quote():
    reading = qb.read_one(
        _reading(claim_quote="The Question, Changed!"), WORDS, 60.0)
    assert not reading.refused


def test_an_opening_quote_from_the_middle_is_reported_not_refused():
    """A judge that read the reel and quoted from one word in got the
    boundary wrong; throwing the whole reading away for it would fail
    correct work."""
    reading = qb.read_one(
        _reading(opening_quote="what actually changed about search"),
        WORDS, 60.0)
    assert not reading.refused
    assert any("opens" in m for m in reading.misplaced)


def test_a_closing_quote_from_the_middle_is_reported_not_refused():
    reading = qb.read_one(
        _reading(closing_quote="the question changed"), WORDS, 60.0)
    assert not reading.refused
    assert any("ends" in m for m in reading.misplaced)


def test_an_assumption_with_no_quote_refuses():
    reading = qb.read_one(
        _reading(assumes_known=[{"what": "the audit example"}]), WORDS, 60.0)
    assert reading.refused


def test_an_assumption_quoting_words_the_reel_does_not_say_refuses():
    reading = qb.read_one(
        _reading(assumes_known=[{"what": "the audit",
                                 "quote": "like we said earlier"}]),
        WORDS, 60.0)
    assert reading.refused


def test_a_stop_point_outside_the_reel_refuses():
    reading = qb.read_one(_reading(stops_developing_at=400.0), WORDS, 60.0)
    assert reading.refused
    assert any("stops_developing_at" in why for why in reading.ungrounded)


def test_an_empty_takeaway_is_a_real_answer_and_not_a_refusal():
    reading = qb.read_one(_reading(takeaway_quote="", takeaway=""),
                          WORDS, 60.0)
    assert not reading.refused
    assert qb.value_of(reading) == qb.DELIVERS_NOTHING


# ── The verdicts are DERIVED, and never asked ────────────────────────

def test_coherence_is_derived_from_what_the_reel_assumes():
    followable = qb.read_one(_reading(), WORDS, 60.0)
    assert qb.coherence_of(followable) == qb.FOLLOWABLE

    leans = qb.read_one(
        _reading(assumes_known=[
            {"what": "the ranking game", "quote": "the old ranking game"}]),
        WORDS, 60.0)
    assert qb.coherence_of(leans) == qb.NOT_FOLLOWABLE


def test_value_is_derived_from_whether_a_takeaway_could_be_quoted():
    assert qb.value_of(qb.read_one(_reading(), WORDS, 60.0)) == qb.DELIVERS
    assert qb.value_of(qb.read_one(_reading(takeaway_quote=""),
                                   WORDS, 60.0)) == qb.DELIVERS_NOTHING


def test_a_refused_reading_reads_unjudged_rather_than_badly():
    """Evidence that failed its own check says nothing about the reel."""
    refused = qb.read_one(_reading(claim_quote="nothing like this"),
                          WORDS, 60.0)
    assert qb.coherence_of(refused) == qb.UNJUDGED
    assert qb.value_of(refused) == qb.UNJUDGED
    assert qb.coherence_of(None) == qb.UNJUDGED
    assert qb.value_of(None) == qb.UNJUDGED


def test_the_derivations_are_not_in_the_prompt():
    """The mapping from observation to verdict is the whole trick, and a
    reader that knew it could satisfy it directly."""
    handoff = (STEP / "handoff.md").read_text(encoding="utf-8").lower()
    for word in ("followable", "not_followable", "delivers_nothing",
                 "unjudged", "empty list is better", "an empty list means"):
        assert word not in handoff


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


def test_the_bar_runs_the_exact_half_with_no_reading_at_all():
    transcript, moments = _batch()
    report = qb.judge(moments, transcript, None)
    assert report.judged is False
    assert report.not_read == [1, 2]
    assert all(v.coherence == qb.UNJUDGED for v in report.verdicts)
    assert any(v.verdict == qb.FAIL for v in report.verdicts), (
        "duration and the closer are properties of the plan and are worth "
        "holding before anyone has been asked to read anything")


def test_a_reading_that_does_not_check_out_fails_the_reel_as_unusable():
    transcript, moments = _batch()
    report = qb.judge(moments, transcript, {"readings": [
        dict(_reading(reel=1), claim_quote="words nobody said here")]})
    first = next(v for v in report.verdicts if v.number == 1)
    assert qb.QB_UNGROUNDED in {f.code for f in first.findings}
    assert first.verdict == qb.FAIL
    assert first.coherence == qb.UNJUDGED


def test_a_reel_that_leans_on_the_episode_RECORDS_and_does_not_fail():
    """Asked as "does it lean on anything unheard", this read
    not_followable on 31 of 31 real reels under two independent readers.
    A column constant across a batch carries no information about that
    batch, and four deterministic halves were measured and rejected
    (`COHERENCE_DOES_NOT_GATE`). So the reading is RECORDED and the
    verdict is not derived from it (AGENTS.md 10.4)."""
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
    finding = next(f for f in first.findings
                   if f.code == qb.QB_NOT_FOLLOWABLE)
    assert finding.severity == qb.WARNING
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


def test_ranked_puts_the_judges_order_first_and_the_unread_after():
    transcript, moments = _batch()
    words = qb.reel_text(moments[1], transcript)
    report = qb.judge(moments, transcript, {"readings": [{
        "reel": 2,
        "claim_quote": words.split()[0],
        "opening_quote": words.split()[0],
        "closing_quote": words.split()[-1],
        "closing_asks_for": "",
        "takeaway_quote": words.split()[0],
        "assumes_known": [],
        "stops_developing_at": 5.0,
        "rank": 1, "basis": "x",
    }]})
    order = [v.number for v in qb.ranked(report)]
    assert order == [2, 1]
    assert qb.ranked(report)[1].rank is None


def test_the_bar_never_drops_a_reel():
    transcript, moments = _batch()
    report = qb.judge(moments, transcript)
    assert [v.number for v in report.verdicts] == [1, 2]
    assert "-" in qb.format_table(report)


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


def test_the_post_bridge_refuses_a_reading_of_a_reel_that_is_not_here():
    transcript, moments = _batch()
    out = _post_bridge().resolve(
        {"readings": [_reading(reel=99)]},
        {"timeline_transcript": transcript,
         "reel_selection": {"moments": [m.as_dict() for m in moments]}})
    assert out["reel_judgement"]["refused"][0]["reel"] == 99


# ── What the reel says is what it PLAYS ──────────────────────────────

def test_the_words_are_the_played_ranges_in_play_order():
    transcript = _transcript(SEGMENTS)
    moment = _moment(cta=CLOSER)
    text = qb.reel_text(moment, transcript)
    assert text.startswith("so what actually changed")
    assert text.endswith("run the free check"), (
        "the closer is played LAST, so it is the reel's last words")


def test_straddling_speech_is_in_the_reels_own_words():
    """The reel PLAYS it; a reader shown only the bound rows would be
    reading a reel that does not exist."""
    segments = list(SEGMENTS) + [
        _segment(45.0, 50.0, "Akshita", "and nobody noticed", bound=False)]
    transcript = _transcript(segments)
    assert "nobody noticed" in qb.reel_text(_moment(), transcript)


def test_a_reel_whose_bad_take_is_cut_runs_shorter_than_its_window():
    """`delivered_seconds` is the sum of the ranges that are PLACED."""
    from library.tools.reel_build import reel_ranges

    transcript = _transcript(SEGMENTS)
    moment = _moment()
    assert qb.delivered_seconds(moment, transcript) == sum(
        end - start for start, end in reel_ranges(moment, transcript))


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


def test_the_unbuildable_finding_does_not_double_report_the_cta_defect():
    """The overlap is BOTH a closer defect and the reason the layout
    refuses. One cause, one finding, and it is the one that names the
    cause."""
    transcript = _transcript(SEGMENTS)
    inside = CallToAction(timeline_start=20.0, timeline_end=40.0,
                          text="the question changed")
    moment = _moment(cta=inside)
    codes = {f.code for f in qb.exact_findings(
        moment, transcript, qb.declared_closers([moment]))}
    assert qb.QB_CTA_IN_BODY in codes
    assert qb.QB_UNBUILDABLE not in codes


# ── What the bar said the first time it met real work ────────────────

def test_the_first_measurement_records_both_sides_of_every_quality():
    """A design argument that has never met the material is a design
    argument, and the honest half of a measurement is the column that
    did not move."""
    m = qb.FIRST_MEASUREMENT
    assert m["verdict"]["pass"] and m["verdict"]["fail"], (
        "a bar that passed everything or failed everything is not "
        "measuring")
    assert m["duration"]["within_guidance"] and m["duration"]["outside"]
    assert m["coherence"]["followable"] and m["coherence"]["not_followable"]
    assert m["call_to_action"]["absent"] and m["call_to_action"]["declared"]
    assert m["value"]["delivers_nothing"] == 0, (
        "the recorded fact is that value did NOT discriminate on that "
        "batch; editing this number without re-running the bar would "
        "make the record a claim rather than a measurement")
    assert "did not discriminate" in m["value"]["reading"]


def test_the_disagreement_fires_in_the_direction_that_found_something():
    """The reader was never told a closer was wanted, so a reel it says
    ends on an invitation while the batch names none is real evidence
    about the limit of `declared_closers`."""
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


def test_a_bar_finding_reaches_the_reel_it_is_about():
    from library.tools.reel_conformance_verifier import attach_quality_bar

    transcript, moments = _batch()
    report = qb.judge(moments, transcript)
    results = [_reel_result(v.name, v.number) for v in report.verdicts]
    assert attach_quality_bar(report, results) == []
    attached = [f.finding_class for r in results for f in r.findings]
    assert qb.QB_CTA_ABSENT in attached


def test_a_built_reel_whose_name_gained_a_suffix_still_matches():
    """A built reel is named `Reel 20 - slug (selector redraw)` while the
    plan calls it `Reel 20 - slug`. Matching on name alone attached 20 of
    25 on the field test and dropped five without a word."""
    from library.tools.reel_conformance_verifier import attach_quality_bar

    transcript, moments = _batch()
    report = qb.judge(moments, transcript)
    results = [_reel_result(f"{v.name} (pipeline rebuild)", v.number)
               for v in report.verdicts]
    assert attach_quality_bar(report, results) == []
    assert any(r.findings for r in results)


def test_a_planned_reel_with_no_timeline_is_REPORTED_not_dropped():
    """A finding that quietly attaches to nothing makes the report read
    as though the bar had nothing to say (AGENTS.md 10.4)."""
    from library.tools.reel_conformance_verifier import attach_quality_bar

    transcript, moments = _batch()
    report = qb.judge(moments, transcript)
    failing = next(v for v in report.verdicts if v.findings)
    unattached = attach_quality_bar(report, [])
    assert any(failing.name in line for line in unattached)
    assert any("finding(s)" in line for line in unattached)


def test_duration_is_not_folded_because_pq_length_already_carries_it():
    """One reel's length reported twice under two codes is the
    double-counting that made 22 PQ-LENGTH findings out of 11."""
    from library.tools.reel_conformance_verifier import attach_quality_bar

    transcript = _transcript(SEGMENTS)
    short = _moment(number=1, start=300.0, end=340.0)   # 40s, out of band
    report = qb.judge([short], transcript)
    assert qb.QB_DURATION in {f.code for f in report.verdicts[0].findings}
    results = [_reel_result(short.timeline_name, 1)]
    attach_quality_bar(report, results)
    assert qb.QB_DURATION not in {f.finding_class
                                  for f in results[0].findings}


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


def test_a_reel_that_keeps_talking_past_its_closer_still_reports_it():
    """The other direction: reels 23 and 24 play 1.2s of speech after
    their closer and genuinely do not end on it."""
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
