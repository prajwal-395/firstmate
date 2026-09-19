"""Closer fit: the share count is measured, fit is judged, nothing is scored.

`library/tools/closer_fit.py` answers the captain's 2026-09-19 ruling:
reuse is not a defect (so grouping never de-duplicates and never
proposes to), topical misfit is (so the model is given the reel's own
kept words and its closer and answers with a reason), and the subset is
measured before anything is re-cut (so the survey reports a list and a
count, and the build only reports).

These tests pin what is measured, what is deliberately NOT decided, and
that a verdict without a reason reads as unjudged rather than as a
verdict - the last of which is what stops this becoming a gate that
fires on correct output.
"""

from __future__ import annotations

from types import SimpleNamespace

from library.tools import closer_fit as fit


def _cta(start: float, end: float):
    return SimpleNamespace(timeline_start=start, timeline_end=end)


def _moment(number: int, body: tuple, closer=None):
    return SimpleNamespace(
        number=number,
        timeline_start=float(body[0]),
        timeline_end=float(body[1]),
        call_to_action=_cta(*closer) if closer else None,
        timeline_name=f"Reel {number:02d}",
        approval=SimpleNamespace(value="approved"),
    )


def _segment(start: float, words: list, speaker: str = "Akshita"):
    """One transcript segment with evenly spaced timed words."""
    step = 0.4
    timed = [{"word": token, "start": start + i * step,
              "end": start + (i + 1) * step, "timed": True}
             for i, token in enumerate(words)]
    end = start + len(words) * step
    return {"timeline_start": start, "timeline_end": end,
            "speaker": speaker, "text": " ".join(words),
            "words": timed}


def _transcript(*segments):
    return {"segments": list(segments)}


# ── Reuse is grouped, never judged ───────────────────────────────────

def test_exact_duplicate_closers_group():
    moments = [_moment(1, (0.0, 10.0), (100.0, 107.0)),
               _moment(2, (20.0, 30.0), (100.0, 107.0))]
    groups = fit.reuse_groups(moments)

    assert len(groups) == 1
    assert groups[0]["reels"] == [1, 2]
    assert groups[0]["count"] == 2


def test_snapped_boundaries_still_group():
    """A shared clip re-snapped by fractions of a second is one group.

    The live plan carries 328.61-341.27 and 328.61-342.03 as the same
    ending; exact equality would count them as two clips.
    """
    moments = [_moment(3, (0.0, 10.0), (328.61, 341.27)),
               _moment(13, (20.0, 30.0), (328.61, 342.03))]
    groups = fit.reuse_groups(moments)

    assert len(groups) == 1
    assert groups[0]["reels"] == [3, 13]


def test_disjoint_closers_do_not_group():
    moments = [_moment(1, (0.0, 10.0), (100.0, 107.0)),
               _moment(4, (20.0, 30.0), (200.0, 205.0))]
    groups = fit.reuse_groups(moments)

    assert [group["reels"] for group in groups] == [[1], [4]]


def test_abutting_closers_do_not_group():
    """Ranges that merely touch share no speech: not the same clip."""
    moments = [_moment(1, (0.0, 10.0), (100.0, 107.0)),
               _moment(2, (20.0, 30.0), (107.0, 114.0))]

    assert [group["reels"] for group in fit.reuse_groups(moments)] == \
        [[1], [2]]


def test_a_reel_with_no_closer_belongs_to_no_group():
    """'No ending' is not a shared ending."""
    moments = [_moment(11, (0.0, 10.0), None),
               _moment(14, (20.0, 30.0), None),
               _moment(1, (40.0, 50.0), (100.0, 107.0))]

    assert fit.reuse_groups(moments)[0]["reels"] == [1]


def test_group_for_finds_a_moment_own_group():
    moments = [_moment(1, (0.0, 10.0), (100.0, 107.0)),
               _moment(2, (20.0, 30.0), (100.0, 107.0))]
    groups = fit.reuse_groups(moments)

    assert fit.group_for(moments[0], groups)["reels"] == [1, 2]
    assert fit.group_for(_moment(9, (0.0, 10.0), (300.0, 307.0)),
                         groups) is None


# ── The context is the reel's own kept words ─────────────────────────

def _body_and_closer():
    body = _segment(0.0, "reviews build trust and authority for a company".split())
    closer = _segment(100.0, "check it out on our website today".split(),
                      speaker="Akshita")
    transcript = _transcript(body, closer)
    moment = _moment(27, (0.0, 2.8), (100.0, 102.8))
    return moment, transcript


def test_body_words_exclude_the_closer():
    moment, transcript = _body_and_closer()
    groups = fit.reuse_groups([moment])
    context = fit.fit_context(
        moment, transcript, fit.group_for(moment, groups))

    assert "reviews build trust" in context["body_text"]
    assert "check it out" not in context["body_text"]
    assert context["closer_text"] == "check it out on our website today"
    assert context["body_speakers"] == ["Akshita"]
    assert context["reuse_count"] == 1
    assert context["shared_with"] == []


def test_shared_with_names_the_other_reels():
    first, transcript = _body_and_closer()
    second = _moment(4, (20.0, 22.8), (100.0, 102.8))
    groups = fit.reuse_groups([first, second])
    context = fit.fit_context(
        first, transcript, fit.group_for(first, groups))

    assert context["reuse_count"] == 2
    assert context["shared_with"] == [4]


def test_closer_cutting_into_a_sentence_is_said():
    """A closer opening mid-sentence carries the sentence it cuts."""
    segment = _segment(100.0, "so check it out on our website today".split())
    transcript = _transcript(
        _segment(0.0, "reviews build trust".split()), segment)
    # Starts on "check", inside the segment's "so ... today".
    moment = _moment(27, (0.0, 1.2), (100.4, 103.2))
    context = fit.fit_context(moment, transcript, None)

    assert context["closer_cut_from_sentence"] == \
        "so check it out on our website today"


def test_closer_starting_clean_carries_no_cut_sentence():
    moment, transcript = _body_and_closer()
    context = fit.fit_context(moment, transcript, None)

    assert context["closer_cut_from_sentence"] is None


# ── The reader takes verdicts, never magnitudes ──────────────────────

def test_a_verdict_with_a_reason_is_read():
    read = fit.read_fit_answer({"verdict": "misfit",
                                "reason": "the closer answers a question "
                                          "the reel never asked"})

    assert read == {"verdict": "misfit",
                    "reason": "the closer answers a question the reel "
                              "never asked"}


def test_a_numeric_beside_a_verdict_is_dropped_unread():
    """There is no magnitude in this judgement, so none is read."""
    read = fit.read_fit_answer({"verdict": "follows", "reason": "lands it",
                                "score": 0.92, "confidence": 0.99})

    assert read == {"verdict": "follows", "reason": "lands it"}


def test_an_unknown_verdict_is_unjudged_not_a_verdict():
    read = fit.read_fit_answer({"verdict": "somewhat", "reason": "maybe"})

    assert read["verdict"] == "unjudged"
    assert read["reason"]


def test_a_verdict_without_a_reason_is_unjudged():
    """A verdict nobody can show the captain is not a verdict."""
    assert fit.read_fit_answer(
        {"verdict": "misfit", "reason": "  "})["verdict"] == "unjudged"
    assert fit.read_fit_answer({"verdict": "misfit"})["verdict"] == \
        "unjudged"


def test_no_answer_is_unjudged():
    assert fit.read_fit_answer(None)["verdict"] == "unjudged"
    assert fit.read_fit_answer({})["verdict"] == "unjudged"


# ── The prompt asks fit, and only fit ────────────────────────────────

def test_the_prompt_carries_body_closer_and_the_reuse_doctrine():
    moment, transcript = _body_and_closer()
    context = fit.fit_context(moment, transcript, None)
    prompt = fit.render_fit_prompt(context)

    assert "reviews build trust" in prompt
    assert "check it out on our website today" in prompt
    assert "Reuse is NOT a defect" in prompt
    assert '"follows" | "misfit"' in prompt
    assert "score" not in prompt.lower()
    assert "threshold" not in prompt.lower()


# ── The build-time lines report, never gate ──────────────────────────

def test_no_closer_is_silence():
    context = {"closer_range": None}

    assert fit.verdict_lines(11, context, None) == []


def test_an_unrecorded_verdict_says_so():
    moment, transcript = _body_and_closer()
    context = fit.fit_context(moment, transcript, None)

    lines = fit.verdict_lines(27, context, None)

    assert any("not yet judged" in line for line in lines)


def test_a_misfit_is_named_with_its_reason():
    moment, transcript = _body_and_closer()
    context = fit.fit_context(moment, transcript, None)
    verdict = {"verdict": "misfit", "reason": "answers nothing asked",
               "content_hash": context["content_hash"]}

    lines = fit.verdict_lines(27, context, verdict)

    assert any("MISFIT" in line and "answers nothing asked" in line
               for line in lines)


def test_a_verdict_over_moved_words_reads_as_stale():
    moment, transcript = _body_and_closer()
    context = fit.fit_context(moment, transcript, None)
    verdict = {"verdict": "follows", "reason": "lands it",
               "content_hash": "movedwords00"}

    lines = fit.verdict_lines(27, context, verdict)

    assert any("STALE" in line for line in lines)


def test_summarize_counts_and_lists_misfits():
    verdicts = {"1": {"verdict": "follows", "reason": "lands it"},
                "27": {"verdict": "misfit", "reason": "no antecedent"},
                "4": {"verdict": "misfit", "reason": "new thought"}}

    lines = fit.summarize(verdicts)

    assert lines[0] == "closer fit: 2 of 3 judged reels misfit: reels 4, 27"
    assert any("reel 27" in line for line in lines)


def test_summarize_says_when_every_closer_follows():
    lines = fit.summarize({"1": {"verdict": "follows", "reason": "lands"}})

    assert "every judged closer follows its reel" in lines[0]
