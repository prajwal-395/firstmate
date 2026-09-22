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


def test_the_length_band_is_gone_from_the_bar():
    """2026-09-18, option c: the 45-90s window came OUT of the quality
    bar, replaced by nothing - no wider window, no warning threshold,
    no configurable default.  The selector still weighs the guidance
    while choosing (that is `reel_exchange`'s, not this module's)."""
    from library.tools.reel_exchange import LENGTH_GUIDANCE

    assert LENGTH_GUIDANCE == (45.0, 90.0)
    assert not hasattr(qb, "LENGTH_GUIDANCE")
    assert not hasattr(qb, "QB_DURATION")
    assert "QB-DURATION" not in set(qb.FINDING_OWNERS)
    assert qb.DURATION_GATE_REMOVED["ruled"] == "2026-09-18"


# ── EXACT: duration is measured, never judged ────────────────────────

def test_a_duration_reading_carries_no_guidance_keys():
    """No band, no side, no distance-to-band: there is nothing left to
    be inside or outside of."""
    transcript = _transcript(SEGMENTS)
    moment = _moment(end=70.0)  # 60s
    reading = qb.duration_reading(moment, transcript)
    assert reading["delivered_seconds"] == 60.0
    for key in ("guidance_seconds", "within_guidance",
                "outside_by_seconds"):
        assert key not in reading
    findings = qb.exact_findings(moment, transcript, {})
    assert [f.code for f in findings
            if f.quality == "duration"] == []


@pytest.mark.parametrize("end", [44.0, 100.0, 170.0])
def test_a_reel_outside_the_old_window_is_not_rejected_for_length(end):
    """2026-09-18, option c: length is never a gate.  A reel delivering
    40s, 96s or 166s carries NO duration finding at all - not an error
    and not a warning - and with a closer and a takeaway it PASSES.
    (Past five minutes the mechanical absurd bound still errors; that
    is not a window, it is where a candidate stops being a reel.)"""
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
    """Body, minus bad takes, plus the closer - three different numbers -
    and without a project to resolve the ending against, the figure says
    it is the body only rather than reporting itself as the reel."""
    transcript = _transcript(SEGMENTS)
    moment = _moment(cta=CLOSER)
    reading = qb.duration_reading(moment, transcript)
    assert reading["body_seconds"] == 60.0
    assert reading["closer_seconds"] == 6.0
    assert reading["delivered_seconds"] == 66.0
    assert reading["ending_resolved"] is False
    assert "no project folder" in reading["ending"]["why"]


def test_the_duration_ruling_records_the_captains_words_and_date():
    """The 2026-09-09 ruling, kept in the COHERENCE_DOES_NOT_GATE shape:
    why the band does not gate, on whose words, and what replaces it."""
    record = qb.DURATION_DOES_NOT_GATE
    assert record["ruled"] == "2026-09-09"
    assert "rule of thumb" in record["captain"]
    assert "render them" in record["captain"]
    # The ten duration-outside reels of the harvest batch are named.
    assert "0.3" in record["why"]
    # The judged number is the delivered one, not the stale body-only
    # field old proposal files carry beside the notes.
    assert record["judged_number"] == "delivered_seconds"
    # No replacement gate was invented: approval is the mechanism.
    assert "assert_approved" in record["ruling"]
    assert "10.5" in record["ruling"]


def test_the_removal_record_names_the_ruling_and_what_replaces_it():
    """2026-09-18, option c, under the standing "no hard coded number"
    ruling: the window is OUT, replaced by nothing, and the reading plus
    the captain's approval are what decide."""
    record = qb.DURATION_GATE_REMOVED
    assert record["ruled"] == "2026-09-18"
    assert "Delete the gate" in record["captain"]
    assert "no hard coded number" in record["standing_ruling"]
    assert "no wider window" in record["what_went"]
    assert "22 reels" in record["evidence"]
    assert "assert_approved" in record["evidence"]


def test_a_surviving_duration_figure_includes_the_ending(tmp_path):
    """The reel the old warning called short built with the ending: a
    60s body, a 6s closer, a 19-frame freeze inherited from the CTA and
    a 3.0s tail card the project declares - 69.8s a viewer sits
    through, resolved never restated."""
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


def test_a_body_only_figure_says_it_is_unresolved():
    """Without a project there is no declaration to resolve the ending
    from.  The figure is the body and SAYS SO - reporting it as the
    reel in silence is the defect the removal records."""
    transcript = _transcript(SEGMENTS)
    moment = _moment(cta=CLOSER)
    reading = qb.duration_reading(moment, transcript)
    assert reading["ending_resolved"] is False
    assert reading["ending"]["total"] == 0.0
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


def test_a_closer_opening_on_a_sentence_back_half_is_named_and_does_not_fail():
    """The segment gate refuses a closer that CUTS a segment, but ASR
    segments split mid-sentence - reel 09's closer starts exactly on
    one and still opens on "we're calling the Lucie visibility
    system", whose head the reel never plays. The bar records what the
    gate cannot see, and records it as a warning: ASR punctuation is a
    measurement, not a verdict, so this never refuses."""
    segments = [
        _segment(10.0, 20.0, "Craig", "so what actually changed"),
        _segment(200.0, 204.0, "Craig",
                 "it's exactly why we've been building this platform"),
        _segment(204.5, 210.0, "Craig",
                 "we're calling the lucie visibility system check it out"),
    ]
    transcript = _transcript(segments)
    back_half = CallToAction(timeline_start=204.5, timeline_end=210.0,
                             text="we're calling the lucie visibility "
                                  "system check it out",
                             speaker="Craig")
    moment = _moment(number=1, cta=back_half)
    closers = qb.declared_closers([moment])
    findings = [f for f in qb.exact_findings(moment, transcript, closers)
                if f.code == qb.QB_CTA_OPENS_MID_SENTENCE]
    assert len(findings) == 1
    assert findings[0].severity == qb.WARNING
    assert "building this platform" in findings[0].message


def test_a_closer_after_a_turn_change_opens_clean():
    """A new speaker starts a new utterance, even where the transcript
    carries no punctuation at all - the fixture closer follows Craig
    with Akshita, so no sentence-start finding fires on correct
    output."""
    transcript = _transcript(SEGMENTS)
    moment = _moment(cta=CLOSER)
    closers = qb.declared_closers([moment])
    assert [f for f in qb.exact_findings(moment, transcript, closers)
            if f.code == qb.QB_CTA_OPENS_MID_SENTENCE] == []


def test_a_closer_after_a_closed_sentence_opens_clean():
    """The same speaker, but the transcriber closed the sentence - the
    closer is the next thought, not the back half of this one."""
    segments = [
        _segment(10.0, 20.0, "Craig", "so what actually changed"),
        _segment(200.0, 204.0, "Craig",
                 "it's exactly why we built this platform."),
        _segment(204.5, 210.0, "Craig",
                 "we'd love for you to go check it out"),
    ]
    transcript = _transcript(segments)
    clean = CallToAction(timeline_start=204.5, timeline_end=210.0,
                         text="we'd love for you to go check it out",
                         speaker="Craig")
    moment = _moment(number=1, cta=clean)
    closers = qb.declared_closers([moment])
    assert [f for f in qb.exact_findings(moment, transcript, closers)
            if f.code == qb.QB_CTA_OPENS_MID_SENTENCE] == []


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


def test_the_bar_carries_no_duration_finding_to_fold():
    """Since 2026-09-18 the bar judges no band, so there is nothing to
    fold twice: length is reported once, by the verifier's own
    `check_plan_length`, and the fold carries the call-to-action half."""
    from library.tools.reel_conformance_verifier import attach_quality_bar

    transcript = _transcript(SEGMENTS)
    short = _moment(number=1, start=300.0, end=340.0)   # 40s, off the old band
    report = qb.judge([short], transcript)
    assert not {f.code for f in report.verdicts[0].findings
                if f.quality == "duration"}
    results = [_reel_result(short.timeline_name, 1)]
    assert attach_quality_bar(report, results) == []
    assert qb.QB_CTA_ABSENT in {f.finding_class
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
