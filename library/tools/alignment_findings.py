"""What the aligner measured about a passage's INSIDES, and its readers.

Step 2.02's post-bridge writes `alignment_report` on its own output: one
record per passage carrying `leading_gap_seconds`, `largest_gap_seconds`,
`voiced_fraction`, `anchors_considered`, `hint_nearest_start` and
`chosen_start`.  Every number is a real measurement of the passage that
was cut.

**Nothing read it.**  Two steps are routed `speech_sequence` whole and
both drop the report by name (`-speech_sequence.alignment_report`), the
run summary never mentioned it, and no gate looks at it.  On project
001's run of record that meant body[0] - *"today is march 25th, 2026."* -
shipped with a 1.169 s silence between "25th," and "2026.": 39% of a
2.982 s block, `voiced_fraction 0.474`, the worst of the eight passages,
and no step, no gate and no report ever said so.

The leading-gap half of this is already spoken for and stays where it
is: AGENTS.md §6 says the anchor search prints `reanchored`/`held` to
stderr and that there is **no gap threshold and no voiced-fraction
band**.  This module keeps that line.  It ORDERS and it REPORTS; it
never fires, never filters and never says what to conclude.  A passage
with a long internal pause may be a real dramatic beat, and deciding
that is the model's job - which is exactly why the numbers have to reach
it.

Two readers, one module, the shape `qa_findings` already has:

- the **run summary**, printed after `status` is decided, so reading can
  never be gating;
- **step 3.03 `review_rough_cut`**, through `view:alignment`.  3.03 is
  the step whose job is to look at the assembled cut and say what is
  wrong with it, and it is the last step before the transitions and VFX
  planners commit.  It has no `bridge.py`, so the legend travels as a
  view rather than as a pre-bridge table.  **Not a freeze workaround
  any more**: the captain's freeze on `handoff.md` was lifted
  2026-09-09 and the definition-in-the-prompt fold of that day covered
  4.02, 4.03, 4.04, 3.03 and 2.04.  This one was left as data and is on
  the record as still foldable - the columns are constants and 3.03's
  prose could carry them.
"""

from __future__ import annotations

ALIGNMENT_LEGEND = {
    "block":
        "which passage of the speech sequence this is.",
    "clip_id":
        "the source clip the passage was aligned against.",
    "source_start / source_end":
        "the passage's final source range, in the clip's own seconds. "
        "These are the timings the timeline is cut to.",
    "leading_gap_seconds":
        "silence between the start of the cut and the first word. A cut "
        "opens on this much nothing.",
    "largest_gap_seconds":
        "the longest silence BETWEEN two words inside the passage. This "
        "is the one nothing else in the pipeline reports.",
    "voiced_fraction":
        "the share of the passage's duration that is inside a word. 1.0 "
        "is continuous speech; 0.5 means half the block is silence.",
    "anchors_considered":
        "how many candidate alignments the search ranked before choosing "
        "this one. 1 means there was no alternative.",
    "event":
        "ok, reanchored (a better anchor removed a leading gap) or held "
        "(a gap survived because no equally complete anchor removed it).",
    "reading":
        "These are MEASUREMENTS of the passage the sequence already "
        "chose, not faults. A long internal pause can be a real dramatic "
        "beat and can equally be dead air; NOTHING in the pipeline "
        "decides which, and no threshold fires on any of these numbers.",
}


def _rows(report):
    return [r for r in (report or []) if isinstance(r, dict)]


def passage_rows(report):
    """One row per passage, ordered by the largest internal gap first.

    Ordering is not ranking a fault: it puts the longest silence where a
    reader will see it. A passage the aligner measured nothing for keeps
    its row and says `unmeasured` rather than being dropped or read as
    zero.
    """
    rows = []
    for record in _rows(report):
        largest = record.get("largest_gap_seconds")
        voiced = record.get("voiced_fraction")
        start = record.get("source_start")
        end = record.get("source_end")
        duration = None
        if isinstance(start, (int, float)) and isinstance(end, (int, float)):
            duration = round(float(end) - float(start), 3)
        rows.append({
            "block": record.get("block") or "?",
            "clip_id": record.get("clip_id") or "?",
            "source_start": start,
            "source_end": end,
            "duration_seconds": duration,
            "leading_gap_seconds": record.get("leading_gap_seconds"),
            "largest_gap_seconds": largest,
            "voiced_fraction": voiced,
            "anchors_considered": record.get("anchors_considered"),
            "event": record.get("event") or "ok",
        })
    rows.sort(key=lambda r: (
        -1.0 if r["largest_gap_seconds"] is None
        else -float(r["largest_gap_seconds"])))
    return rows


def _cell(value, suffix=""):
    if value is None:
        return "unmeasured"
    if isinstance(value, float):
        return f"{value:g}{suffix}"
    return f"{value}{suffix}"


def summary_lines(report):
    """The run summary's block. Empty when 2.02 measured nothing."""
    rows = passage_rows(report)
    if not rows:
        return []
    measured = [r for r in rows if r["largest_gap_seconds"] is not None]
    lines = [
        f"  Passage alignment: {len(rows)} passage(s), "
        f"{len(measured)} with a measured internal gap. Ordered by the "
        f"longest silence between two words. No threshold fires on these."
    ]
    for row in rows:
        share = ""
        if (row["largest_gap_seconds"] is not None
                and row["duration_seconds"]):
            share = (f" = {row['largest_gap_seconds'] / row['duration_seconds'] * 100:.0f}%"
                     f" of the block")
        lines.append(
            f"      {row['block']} ({row['clip_id']}, "
            f"{_cell(row['duration_seconds'], 's')}): "
            f"largest internal gap {_cell(row['largest_gap_seconds'], 's')}"
            f"{share}, voiced {_cell(row['voiced_fraction'])}, "
            f"leading gap {_cell(row['leading_gap_seconds'], 's')}, "
            f"{row['event']}"
        )
    return lines
