#!/usr/bin/env python3
"""Step 3.5 post-bridge: the reading, CHECKED, and the verdicts DERIVED.

Two things happen here and the order matters.

**First the reading is checked against the reel it is about.**  Every
quote the reader wrote is looked for, word for word, in what that reel
says.  A reading whose quotes are not in the reel is REFUSED: it is
recorded in `refused` with the reasons, and no judgement is derived from
it.  That is the difference between evidence and an impression - a
sentence about a recording that cannot be traced back to the recording
says nothing about the recording, and storing it as though it did is how
a report comes to be full of confident, precise, meaningless numbers.

**Then the engine derives the verdicts.**  The reader was never asked
whether a reel is followable or whether it delivers anything; it was
asked what the reel refers to that a listener could not know from it, and
what a listener could repeat or act on.  `reel_quality_bar.coherence_of`
and `value_of` turn those observations into the two judgement verdicts
here, in code the reader never sees.  A reader that does not know which
way an answer counts cannot flatter itself, and that is the whole of the
anti-contamination design: it is structural, not a matter of how the
handoff is worded.

What this step does NOT do
--------------------------
It does not measure how long a reel runs and it does not look for a
closing invitation.  Both are EXACT and belong to
`library/tools/reel_quality_bar.py`, which reads them off the plan; a
prompt naming either is refused by `assert_ask_is_uncontaminated`.

It does not drop a reel, reorder the selection, or change a boundary.
The ordering the reader wrote is recorded as an ordering and nothing
consumes it as a score, for the reason `passage_engagement` records: a
rank compares only near the top, and a reel left unplaced reads UNPLACED
rather than last.
"""

from __future__ import annotations

from typing import List

from library.tools.plan_keys import refuse_unknown_keys


# The reading-entry keys `read_one` checks. Anything else on a reading
# is REFUSED by `refuse_unknown_keys` in `resolve`, never dropped: an
# unread key is how a probe's SFX `at_word` landed 3.06 s early on the
# block start. (`could_not_determine` is not listed because it is not
# a reading key: the runner splits it out of the answer before this
# bridge runs, and `undetermined.py` reads it there.)
READING_ENTRY_KEYS = frozenset({
    "reel",
    "claim_quote",
    "claim",
    "opening_quote",
    "closing_quote",
    "closing_asks_for",
    "takeaway_quote",
    "takeaway",
    "assumes_known",
    "claim_parts",
    "stops_developing_at",
    "rank",
    "basis",
})


def resolve(llm_output: dict, data: dict) -> dict:
    from library.tools.reel_proposal import ReelMoment
    from library.tools.reel_quality_bar import (
        QUALITIES,
        coherence_of,
        read_one,
        reel_text,
        value_of,
    )

    transcript = (data or {}).get("timeline_transcript") or {}
    selection = (data or {}).get("reel_selection") or {}
    moments = {int(m["number"]): ReelMoment.from_dict(m)
               for m in (selection.get("moments") or [])}

    entries = (
        llm_output.get("readings")
        or (llm_output.get("reel_judgement") or {}).get("readings")
        or []
    )

    # A reading key nothing here reads is refused before anything is
    # checked - the refusal travels the post-bridge retry path so the
    # model re-plans instead of the reading shipping without what the
    # key asked for.
    refuse_unknown_keys(entries, READING_ENTRY_KEYS,
                         step="judge_reels", plan="readings")

    readings: List[dict] = []
    refused: List[dict] = []
    seen = set()
    for entry in entries:
        try:
            number = int(entry.get("reel"))
        except (TypeError, ValueError):
            refused.append({
                "reel": entry.get("reel"),
                "why": ["the entry names no reel number, so there is "
                        "nothing to check it against"]})
            continue
        moment = moments.get(number)
        if moment is None:
            refused.append({
                "reel": number,
                "why": [f"reel {number} is not in this selection, so no "
                        f"words exist to check the reading against"]})
            continue
        if number in seen:
            refused.append({
                "reel": number,
                "why": ["a second reading of the same reel arrived; only "
                        "the first is kept, because nothing here can "
                        "choose between two readings of one thing"]})
            continue
        seen.add(number)

        words = reel_text(moment, transcript)
        runs_for = None
        for row in ((data or {}).get("reels_to_read") or []):
            if int(row.get("reel", -1)) == number:
                runs_for = float(row.get("runs_for_seconds") or 0.0)
                break
        # Recorded spelling corrections BEFORE the quote check (the
        # 3.04 keep-exclusion precedent): the entry's quotes and the
        # reel words get the same deterministic transform, so grounding
        # is preserved while spelling is corrected - a reader who wrote
        # the corrected spelling against a stale transcript is no
        # longer refused for it, and one who wrote the old spelling
        # against a clean transcript is corrected rather than refused.
        _pair = {"entry": entry, "words": words}
        from library.tools.display_respell import apply_post_pass
        apply_post_pass(
            _pair, (data or {}).get("project_folder") or "",
            f"judge_reels post-bridge (reel {number} reading)")
        entry, words = _pair["entry"], _pair["words"]
        reading = read_one(entry, words, runs_for)
        if reading.refused:
            refused.append({"reel": number, "why": list(reading.ungrounded),
                            "reading": reading.as_dict()})
            continue
        record = reading.as_dict()
        # DERIVED here, from the observations, in code the reader never
        # saw. Never asked.
        record["coherence"] = coherence_of(reading)
        record["value"] = value_of(reading)
        readings.append(record)

    ordering = [
        {"reel": r["reel"], "rank": r["rank"], "basis": r["basis"]}
        for r in sorted(readings,
                        key=lambda r: (r["rank"] is None, r["rank"] or 0,
                                       r["reel"]))
    ]

    not_read = sorted(
        set(moments) - seen
        | {int(x.get("reel")) for x in
           ((data or {}).get("reels_not_readable") or [])
           if x.get("reel") is not None})

    judgement = {
        "format": "reel_judgement/1",
        "readings": readings,
        "refused": refused,
        "ordering": ordering,
        "not_read": not_read,
        "not_readable": list((data or {}).get("reels_not_readable") or []),
        "contract": {
            "checked_against": (
                "every quote was looked for, word for word, in what "
                "that reel says. A reading whose quotes are not in it "
                "is in `refused` and no verdict is derived from it."),
            "derived_not_asked": [
                {"quality": q.name, "from": list(q.held_by)}
                for q in QUALITIES if q.kind == "judgement"],
            "ordering": (
                "an ORDERING and no score. Compare ranks near the top "
                "only; a reel with rank null is UNPLACED, which is not "
                "last (library/tools/passage_engagement.py)."),
        },
    }
    # The bridge found no reel to read, so no model was asked; the
    # judgement says so rather than reading as one nobody answered.
    # See library/tools/nothing_to_decide.py.
    from library.tools import nothing_to_decide
    if (data or {}).get(nothing_to_decide.KEY):
        judgement["not_asked"] = data[nothing_to_decide.KEY]
    return {"reel_judgement": judgement}


def main():
    import json
    import sys
    import traceback

    try:
        data = json.loads(sys.stdin.read())
        print(json.dumps(resolve(data, data)))
    except Exception:
        sys.stderr.write(traceback.format_exc())
        sys.exit(1)


if __name__ == "__main__":
    main()
