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

def test_shared_closers_group():
    cases = [
        (((1, (100.0, 107.0)), (2, (100.0, 107.0))), [1, 2]),
        # A shared clip re-snapped by fractions of a second is one group:
        # the live plan carries 328.61-341.27 and 328.61-342.03 as one.
        (((3, (328.61, 341.27)), (13, (328.61, 342.03))), [3, 13]),
    ]
    for closers, reels in cases:
        moments = [_moment(n, (20.0 * i, 20.0 * i + 10.0), c)
                   for i, (n, c) in enumerate(closers)]
        groups = fit.reuse_groups(moments)
        assert [(g["reels"], g["count"]) for g in groups] == [(reels, 2)]


# ── The context is the reel's own kept words ─────────────────────────

def _body_and_closer():
    body = _segment(0.0, "reviews build trust and authority for a company".split())
    closer = _segment(100.0, "check it out on our website today".split(),
                      speaker="Akshita")
    transcript = _transcript(body, closer)
    moment = _moment(27, (0.0, 2.8), (100.0, 102.8))
    return moment, transcript


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


# ── The reader takes verdicts, never magnitudes ──────────────────────

def test_the_reader_takes_a_reasoned_verdict_only():
    cases = [
    ({"verdict": "misfit", "reason": "answers what the reel never asked"},
     {"verdict": "misfit", "reason": "answers what the reel never asked"}),
    # There is no magnitude in this judgement, so none is read.
    ({"verdict": "follows", "reason": "lands it", "score": 0.92,
      "confidence": 0.99},
     {"verdict": "follows", "reason": "lands it"}),
    # An unknown verdict, or one nobody can show the captain, is unjudged.
    ({"verdict": "somewhat", "reason": "maybe"}, "unjudged"),
    ({"verdict": "misfit", "reason": "  "}, "unjudged"),
    ({"verdict": "misfit"}, "unjudged"),
    ]
    for answer, expected in cases:
        read = fit.read_fit_answer(answer)
        if expected == "unjudged":
            assert read["verdict"] == "unjudged" and read["reason"], answer
        else:
            assert read == expected


# ── The prompt asks fit, and only fit ────────────────────────────────


# ── The build-time lines report, never gate ──────────────────────────


def test_a_verdict_line_names_a_misfit_and_flags_moved_words_stale():
    moment, transcript = _body_and_closer()
    context = fit.fit_context(moment, transcript, None)
    misfit = fit.verdict_lines(27, context, {
        "verdict": "misfit", "reason": "answers nothing asked",
        "content_hash": context["content_hash"]})
    assert any("MISFIT" in line and "answers nothing asked" in line
               for line in misfit)
    stale = fit.verdict_lines(27, context, {
        "verdict": "follows", "reason": "lands it",
        "content_hash": "movedwords00"})
    assert any("STALE" in line for line in stale)
