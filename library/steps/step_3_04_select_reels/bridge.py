#!/usr/bin/env python3
"""Step 3.4 pre-bridge: the tables the handoff tells the model to read.

Two tables, because they describe different things. `turns` is the
STRUCTURE of the conversation - who spoke and between which two seconds -
and is what a reader needs to judge whether a stretch is a two-hander.
`reel_candidates` is one row per contiguous stretch the measurements
found, and is what tells them the SHAPE of it.

A third table is beside them and is not built here: `spoken_lines`, the
conversation itself, one row per line of speech with the seconds it may
be cut at. It is a declared VIEW of `timeline_transcript` rather than a
pre-bridge table (`library/tools/context_views._spoken_lines`), because
what it exists to leave behind - 8,509 per-word timings, 940 absolute
source paths, 940 Resolve item ids - is left behind by the PROJECTION,
which is the mechanism a step declares what may reach its prompt with.
`turns` no longer carries the words, because that view does.

Nothing here is ranked, scored or filtered, and that is the point of the
file rather than a nicety about it. Reel selection existed for two
rejected batches as a crewmate's own judgement wrapped in a validator -
see section 15 of docs/FIELD_TEST_PODCAST_FINDINGS.md - and the specific
mechanism was a `pitch_share >= 0.15` exclusion that removed ten
candidates for containing a call to action. The captain's format ENDS on
one. So pitch is published as a measurement beside `closes_on_pitch`, and
every stretch found reaches the table including the ones the
measurements are unenthusiastic about.

`within_length_guidance` is published rather than enforced for the same
reason: the 45-90s brief is guidance the model weighs, and a hard ceiling
silently withheld every story that needed longer to finish.
"""

from __future__ import annotations

from typing import Dict, List

LEAD = "lead_speaker"
ANSWERER = "answering_speaker"


def _speakers(transcript: dict) -> tuple:
    from library.tools.reel_exchange import turns_from_transcript

    turns = turns_from_transcript(transcript)
    asks: Dict[str, int] = {}
    counts: Dict[str, int] = {}
    seconds: Dict[str, float] = {}
    for turn in turns:
        if not turn.speaker:
            continue
        counts[turn.speaker] = counts.get(turn.speaker, 0) + 1
        seconds[turn.speaker] = seconds.get(turn.speaker, 0.0) + turn.duration
        if turn.asks:
            asks[turn.speaker] = asks.get(turn.speaker, 0) + 1
    if len(counts) < 2:
        return None, None, turns, ""

    def ask_rate(speaker: str) -> float:
        return asks.get(speaker, 0) / counts[speaker]

    def mean_turn(speaker: str) -> float:
        return seconds[speaker] / counts[speaker]

    lead = max(counts, key=lambda s: (ask_rate(s), -mean_turn(s)))
    answerer = max((s for s in counts if s != lead),
                   key=lambda s: seconds[s])
    why = (f"{lead} asks on {ask_rate(lead):.0%} of their turns against "
           f"{ask_rate(answerer):.0%} for {answerer}, and speaks for "
           f"{mean_turn(lead):.0f}s a turn against {mean_turn(answerer):.0f}s. "
           f"Inferred, not declared - correct it if it is wrong.")
    return lead, answerer, turns, why


def repetition_inside(start: float, end: float, transcript: dict) -> list[dict]:
    """The repeated runs INSIDE one candidate window, and whether a build
    will remove them.

    `retake_of` already tells this step when a whole stretch is another
    stretch recorded again.  It says nothing about a stretch that
    contains a repetition of its OWN, and that is the case that decides
    where a reel starts.

    Measured on reel 03 of the field test, 2026-09-06.  Akshita says one
    sentence twice inside 301.24-341.27, and the build cannot separate
    the takes: `redundant_runs` holds a run WHOLE when any member of it
    is refused by the duration guard, so the repetition stays in the
    reel and `refused_take_groups` reports it.  That report is produced
    by the BUILD, after the span is fixed, and reaches nothing that can
    move a boundary.  The step that CAN move one is this one, and its
    handoff already promised the facts - *"Both whole exchanges and
    single lines are repeated, and both are reported with the timecodes
    of each take"* - while the candidate table carried only the first
    half of that.  A handoff that names a table the context does not
    deliver is the contract defect AGENTS.md 10.1 is about.

    So the same measurement is offered where the boundary is still open.
    It is a MEASUREMENT and not a verdict: whether a repetition is worth
    redrawing a span for, and which take to keep, stays the model's call
    (AGENTS.md 10.5).  Nothing here filters a candidate out for having
    one.

    Each run's cuts arrive with what the model needs to judge them -
    `take_cut_context`: the dropped and kept tellings' own sentences,
    where each sits in its sentence (a tail-drop orphans the head it
    was cut from; a false start opens a new one), whether the repeat
    crosses a speaker turn, and what the second telling adds. A
    cross-turn paraphrase the cut lane refuses outright is not in any
    run at all, so candidates carry those separately (see below).
    """
    from library.tools.reel_build import redundant_runs, take_cut_context

    out: list[dict] = []
    for run in redundant_runs(float(start), float(end), transcript):
        cuts = []
        for cut in run.cuts:
            context = take_cut_context(cut, transcript)
            cuts.append(context)
        out.append({
            "start": round(run.start, 2),
            "end": round(run.end, 2),
            "speaker": run.speaker,
            "lines": [(segment.get("text") or "").strip()
                      for segment in run.segments],
            "build_removes_it": run.whole,
            "cuts": cuts,
            "why": (
                "every line of this run pairs with a later one, so the "
                "build removes the run whole and the reel does not play it"
                if run.whole else
                f"{len(run.cuts)} of {len(run.segments)} lines pair safely "
                f"and {len(run.blocked)} do not, so the build leaves the "
                f"run ENTIRE - a take is removed whole or not at all. "
                f"These seconds WILL play twice unless the span is drawn "
                f"clear of one of the takes."),
        })
    return out


def retellings_inside(start: float, end: float, transcript: dict) -> list:
    """The cross-turn paraphrases inside one candidate window.

    The cut lane refuses these by design - reworded past its bars - so
    no run contains them and `repetition_inside` never names them. The
    model that can still redraw past one telling gets them here, with
    both sentences, the turn crossed, and what the second telling adds.
    Empty (the common case) is offered as nothing: a call with nothing
    to ask is not made, and a candidate with no retelling carries no
    new key.
    """
    from library.tools.reel_build import possible_retellings

    return possible_retellings(float(start), float(end), transcript)


def build_context(data: dict) -> dict:
    from library.tools.reel_exchange import (
        LENGTH_GUIDANCE, collapse_overlapping, collapse_retakes,
        exchange_windows)

    transcript = data.get("timeline_transcript") or {}
    lead, answerer, turns, why = _speakers(transcript)
    if not lead or not answerer:
        return {
            "turns": [],
            "reel_candidates": [],
            "undetermined": [
                "the transcript names fewer than two speakers, so no "
                "exchange can be identified - a reel is a conversation "
                "and this step cannot invent a second voice"
            ],
        }

    windows = exchange_windows(turns, lead, answerer)
    stretches = collapse_overlapping(windows)
    grouped = collapse_retakes([group[0] for group in stretches])

    candidates: List[dict] = []
    for group in grouped:
        for exchange in group:
            candidates.append(exchange.measurements(lead=lead))
    candidates.sort(key=lambda c: c["start"])
    for candidate in candidates:
        inside = repetition_inside(candidate["start"], candidate["end"],
                                    transcript)
        if inside:
            candidate["repetition_inside"] = inside
        retold = retellings_inside(candidate["start"], candidate["end"],
                                   transcript)
        if retold:
            candidate["possible_retellings"] = retold

    out = {
        # The turn STRUCTURE, and no longer the words.  A turn's text is
        # exactly its segments' texts joined by a space - on the field
        # test, 47,975 characters against the segments' 47,182 plus the
        # 793 joining spaces, to the byte - so publishing it beside
        # `view:spoken_lines` would be the summary and the structure it
        # was rendered from, which AGENTS.md 10.1 forbids.  What is left
        # is the other axis: how the conversation divides into turns,
        # which is what `reel_candidates`' own `turns` and `alternations`
        # columns count.  See library/tools/context_views._spoken_lines.
        "turns": [{
            "speaker": t.speaker,
            "start": round(t.start, 2),
            "end": round(t.end, 2),
        } for t in turns],
        "reel_candidates": candidates,
        "length_guidance_seconds": list(LENGTH_GUIDANCE),
        "picture_holes": (transcript.get("derived_from") or {}).get(
            "picture_holes") or [],
        LEAD: lead,
        ANSWERER: answerer,
        "who_leads_was_inferred": why,
    }
    # Recorded spelling corrections, enforced on the regenerated
    # measurements (the 3.04 keep-exclusion precedent): a candidate
    # quoting speech the correction respelt carries the corrected
    # spelling, deterministically, even when the transcript copy this
    # run read predates it. Identity keys (slugs, paths) never move.
    from library.tools.display_respell import apply_post_pass
    apply_post_pass(out, (data or {}).get("project_folder") or "",
                    "select_reels bridge (reel_candidates)")
    return out


def main():
    import sys
    import json
    import traceback
    try:
        data = json.loads(sys.stdin.read())
        print(json.dumps(build_context(data)))
    except Exception as e:
        sys.stderr.write(traceback.format_exc())
        sys.exit(1)

if __name__ == "__main__":
    main()
