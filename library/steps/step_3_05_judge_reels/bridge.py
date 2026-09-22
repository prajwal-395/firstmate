#!/usr/bin/env python3
"""Step 3.5 pre-bridge: the reels, as words, and NOTHING ELSE.

This file's whole job is subtraction.

The selector's own handoff states what a reel has to be, in full, and
asks the selector to argue for every choice it makes: each moment
arrives carrying a `slug`, a `reason`, and - inside `reel_selection` -
the `hook`, the `close` and the case the selector made for it.  A reader
handed that argument would be reading the argument.  On 2026-09-05 a
model was told the criteria it would be graded on and duly graded itself
well, and nothing about that outcome was surprising.

So one table goes out: `reels_to_read`, one row per proposed moment,
carrying the reel's number, how long it runs - reconciled with the
ending the build appends, because the reader judges length as part of
judging the reel - and every line a listener hears in the order they
hear it.  Not the slug.  Not the reason.  Not the measurements, the
concerns, the openings, the repeated takes, the speakers' shares, the
picture holes or the length guidance.  Not the creative brief, which
is where a project says what it wants from a reel.

The step's manifest declares `context_fields: ["reels_to_read"]` and the
projection is what enforces the subtraction; this file is the other half
- what it never builds cannot be projected away by accident.

Why the closer is not marked
----------------------------
`reel_ranges` lays a borrowed closing passage down last, and a reader
told which lines those are would read them as the ending they are meant
to be rather than as the ending they make.  A listener is not told
either.  So the lines are joined into one stream at reel-relative
seconds and nothing says where the seam is.

Why straddling speech is in it
------------------------------
`played_speech` includes segments with no `resolve_item_id`, which
`bound_segments` deliberately excludes everywhere a BOUNDARY is placed.
A boundary may never be put on one - it has no single source clip - but
the reel PLAYS it, and a reader shown only the bound rows would be
reading a reel that does not exist.  See
`library/tools/reel_quality_bar.played_speech`.
"""

from __future__ import annotations

from typing import List


class JudgeReelsRefused(RuntimeError):
    """This step cannot build a reading request, and says which half is
    missing.  Both inputs are declared REQUIRED and this is the code
    that refuses on them (`library/tools/input_contract.py`)."""


def _runs_for_seconds(moment, transcript: dict, lines: list,
                      data: dict) -> float:
    """How long the reel runs, as the reader weighs it: reconciled.

    The body the lines already cover, plus the ending the build appends
    after them (`reel_quality_bar.ending_seconds` - freeze tail and
    head/tail cards, resolved never restated).  The reader judges length
    as part of judging the reel (captain, 2026-09-18, option c), so the
    length it weighs must be the one a viewer sits through: the reel the
    old warning called 5.5s short built at 45.92s, and a reading that
    weighed 42.1 would be weighing a reel that never exists.

    A reel whose ending cannot be resolved reads the body figure - the
    same number this row carried before - because one unreadable
    declaration must not cost the reading of the whole batch.  The bar's
    own `duration_reading` is where the unresolved half is SAID.
    """
    body = round(lines[-1]["reel_end"], 1)
    try:
        from library.tools.reel_quality_bar import (
            ending_seconds as _ending_seconds,
        )

        ending = _ending_seconds(moment, transcript,
                                 (data or {}).get("project_folder") or "")
    except Exception:  # noqa: BLE001 - see above
        return body
    if not ending["resolved"]:
        return body
    if ending["span_present"]:
        return round(ending["card_seconds"] + ending["freeze_seconds"], 1)
    return round(body + ending["total"], 1)


def build_context(data: dict) -> dict:
    from library.tools.reel_proposal import ReelMoment
    from library.tools.reel_quality_bar import played_speech

    transcript = (data or {}).get("timeline_transcript")
    if not transcript:
        raise JudgeReelsRefused(
            "judge_reels has no timeline_transcript, and a reel's own words "
            "are the only thing this step reads. NO STEP MAKES ONE - see "
            "`python3 -m library.tools.timeline_transcript <project> "
            "--write`.")

    selection = (data or {}).get("reel_selection")
    if selection is None:
        raise JudgeReelsRefused(
            "judge_reels has no reel_selection, so there is nothing to "
            "read. Run select_reels first - it is the producing node of "
            "this edge.")

    rows: List[dict] = []
    unreadable: List[dict] = []
    for entry in (selection.get("moments") or []):
        moment = ReelMoment.from_dict(entry)
        try:
            lines = played_speech(moment, transcript)
        except Exception as refused:
            # One moment the builder cannot lay out must not cost the
            # reading of the other twenty-four. The reason is REPORTED so
            # a reel that reached no reader is visible as that rather
            # than as one nobody happened to rank.
            unreadable.append({"reel": int(moment.number),
                               "why": f"{type(refused).__name__}: {refused}"})
            continue
        if not lines:
            unreadable.append({
                "reel": int(moment.number),
                "why": ("the seconds this reel plays contain no measured "
                        "speech, so there is nothing to read")})
            continue
        rows.append({
            "reel": int(moment.number),
            "runs_for_seconds": _runs_for_seconds(moment, transcript,
                                                  lines, data),
            "lines": [{"at": line["reel_start"],
                       "speaker": line["speaker"],
                       "says": line["text"]} for line in lines],
        })

    out = {"reels_to_read": rows}
    if unreadable:
        # Not projected into the prompt - it is about reels the reader is
        # not being shown - but carried so the post-bridge can say who
        # was never offered.
        out["reels_not_readable"] = unreadable
    # Recorded spelling corrections on the regenerated lines (the 3.04
    # keep-exclusion precedent): the reader reads corrected words even
    # when the transcript copy this run read predates the correction.
    from library.tools.display_respell import apply_post_pass
    apply_post_pass(out, (data or {}).get("project_folder") or "",
                    "judge_reels bridge (reels_to_read)")
    return out


def main():
    import json
    import sys
    import traceback

    try:
        data = json.loads(sys.stdin.read())
        print(json.dumps(build_context(data)))
    except Exception:
        sys.stderr.write(traceback.format_exc())
        sys.exit(1)


if __name__ == "__main__":
    main()
