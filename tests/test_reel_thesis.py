"""The thesis check separates the five incoherent reels (04, 10, 13, 14,
27) from their fixes; verdicts are derived, never scored, and an answer
that cannot be checked reads as unjudged. History and fixture provenance:
docs/evidence/reel_thesis.md ("The acceptance tests")."""

from __future__ import annotations

import json


from library.tools import reel_thesis as thesis
from library.tools.reel_proposal import ReelMoment

STEP = 0.4


def _segment(start: float, text: str, speaker: str = "Akshita"):
    """One bound transcript segment with evenly spaced timed words."""
    tokens = text.split()
    timed = [{"word": token, "start": round(start + i * STEP, 3),
              "end": round(start + (i + 1) * STEP, 3), "timed": True}
             for i, token in enumerate(tokens)]
    end = round(start + len(tokens) * STEP, 3)
    return {"timeline_start": start, "timeline_end": end,
            "speaker": speaker, "text": text, "words": timed,
            "resolve_item_id": f"clip-{start:.1f}"}


def _transcript(*segments):
    return {"segments": list(segments),
            "derived_from": {"fps": 23.976}}


def _moment(number: int, body: tuple, closer=None, slug: str = "topic"):
    data = {"number": number, "slug": slug,
            "timeline_start": float(body[0]), "timeline_end": float(body[1]),
            "approval": "approved"}
    if closer is not None:
        data["call_to_action"] = {"timeline_start": float(closer[0]),
                                  "timeline_end": float(closer[1])}
    return ReelMoment.from_dict(data)


def _sibling_five():
    """A far-away moment 5, so foreign claims have somewhere to verify."""
    return _moment(5, (320.0, 340.0), slug="elsewhere")


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
    body = _segment(100.0, R04_BODY)
    closer = _segment(500.0, R04_CLOSER)
    transcript = _transcript(body, closer)
    moment = _moment(4, (100.0, body["timeline_end"]),
                     (500.0, closer["timeline_end"]))
    return moment, transcript


# ── Reel 10: the answer skipped for a sales pitch ──────────────────

R10_BODY = "the audit found everything else was broken"
R10_CLOSER = "what we do different book a score call with you"
R10_ANSWER = "your website is your resume and everything else are your references"


def _reel_10_failing():
    body = _segment(200.0, R10_BODY)
    closer = _segment(800.0, R10_CLOSER)
    transcript = _transcript(body, closer)
    moment = _moment(10, (200.0, body["timeline_end"]),
                     (800.0, closer["timeline_end"]))
    return moment, transcript


# ── Reel 13: moment 5's opener playing after the closer ────────────

R13_BODY = "the accounting firm blamed ai but it was an information problem"
R13_CTA = "see how your brand appears links in our bio"
R13_TAIL = "so what we are hearing they search their business"


def _reel_13_pieces():
    body = _segment(889.0, R13_BODY)
    cta = _segment(320.0, R13_CTA)
    tail = _segment(cta["timeline_end"], R13_TAIL, speaker="Craig")
    transcript = _transcript(body, cta, tail)
    return body, cta, tail, transcript


def test_reel_13_reads_incoherent_with_the_tail_and_coherent_fixed():
    body, cta, tail, transcript = _reel_13_pieces()
    # This fixture explicitly approves a closer through Craig's whole
    # sentence. The build's large-snap guard prevents that cascade from
    # an edge before the sentence, but a thesis check still judges a
    # plan that already includes the sentence.
    moment = _moment(13, (889.0, body["timeline_end"]),
                     (320.0, tail["timeline_end"]))
    sibling = _moment(5, (320.0, 340.0), slug="opener")
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
    moment = _moment(13, (889.0, body["timeline_end"]),
                     (320.0, cta["timeline_end"]))
    sibling = _moment(5, (320.0, 340.0), slug="opener")
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
    moment = _moment(13, (889.0, body["timeline_end"]),
                     (320.0, cta["timeline_end"]))
    sibling = _moment(5, (320.0, 340.0), slug="opener")
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
    body = _segment(200.0, R14_SETUP)
    closer = _segment(600.0, R14_CLOSER)
    transcript = _transcript(body, closer)
    moment = _moment(14, (200.0, body["timeline_end"]),
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
    body = _segment(700.0, R27_BODY)
    closer = _segment(500.0, R27_CLOSER)
    transcript = _transcript(body, closer)
    moment = _moment(27, (700.0, body["timeline_end"]),
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
    assert "Akshita:" in prompt
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
        body27 = _segment(700.0, R27_BODY)
        transcript = {"segments": transcript4["segments"] + [body27],
                      "derived_from": {"fps": 23.976}}
        moment27 = _moment(27, (700.0, body27["timeline_end"]))
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
