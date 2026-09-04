#!/usr/bin/env python3
"""Step 3.4 pre-bridge: the tables the handoff tells the model to read.

Two tables, because they describe different things. `turns` is the
CONVERSATION - who spoke, when, what they said - and is what a reader
needs to judge whether a stretch says something whole. `reel_candidates`
is one row per contiguous stretch the measurements found, and is what
tells them the SHAPE of it.

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
    """Who leads and who answers, and it is REPORTED as an inference.

    The lead is the one asking. Counting question WORDS does not find
    them: measured on the field-test episode, that named Akshita, because
    her answers are full of "what your company does" and "how AI sees
    you" and she has more turns to put them in.

    What separates an interviewer is the SHAPE of their turns - they ask
    more often per turn and they talk for less time when they do. Both
    signals are used, and the answer is published in the context as an
    inference rather than a fact, because a project that knows who hosts
    it should be able to say so instead of having it guessed.
    """
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

    # Asks more often per turn, and says less when they do.
    lead = max(counts, key=lambda s: (ask_rate(s), -mean_turn(s)))
    answerer = max((s for s in counts if s != lead),
                   key=lambda s: seconds[s])
    why = (f"{lead} asks on {ask_rate(lead):.0%} of their turns against "
           f"{ask_rate(answerer):.0%} for {answerer}, and speaks for "
           f"{mean_turn(lead):.0f}s a turn against {mean_turn(answerer):.0f}s. "
           f"Inferred, not declared - correct it if it is wrong.")
    return lead, answerer, turns, why


def build_context(data: dict) -> dict:
    """`turns` and `reel_candidates`, as the handoff names them."""
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
    # Same stretch first, THEN recorded-twice: doing it the other way
    # counts five framings of one conversation as five takes of it.
    stretches = collapse_overlapping(windows)
    grouped = collapse_retakes([group[0] for group in stretches])

    candidates: List[dict] = []
    for group in grouped:
        for exchange in group:
            candidates.append(exchange.measurements(lead=lead))
    candidates.sort(key=lambda c: c["start"])

    return {
        "turns": [{
            "speaker": t.speaker,
            "start": round(t.start, 2),
            "end": round(t.end, 2),
            "text": t.text,
        } for t in turns],
        "reel_candidates": candidates,
        "length_guidance_seconds": list(LENGTH_GUIDANCE),
        LEAD: lead,
        ANSWERER: answerer,
        "who_leads_was_inferred": why,
    }
