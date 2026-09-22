"""Does a transcript row's TEXT fit the audio it claims to be in?

The detector nothing read
-------------------------
`data/vep-voz-plus-wav2vec2-hybrid/report.md` measured it and named the
gap in one sentence:

    "21 of the shipped transcript's 815 rows fail forced alignment
     outright - 219 words - and they are the same rows the prior report
     traced to the Reel 26 caption defect. Forced alignment is already
     the detector; nothing reads its failures today."

A row that fails forced alignment leaves the transcript carrying TEXT
with no word timings under it. Every word-timed decision downstream -
a caption card, a karaoke highlight, a take boundary - is placed from
word timings, so a row that has none produces NOTHING, silently. That
is the whole Reel 26 story: twelve words in 920 milliseconds with
`words: []`, six of them delivered with no caption at all.

The failure has already happened by the time the transcript is written.
Reading it back costs a pass over a JSON document and needs no aligner,
no audio and no model.

There is no threshold here, and that is the point
-------------------------------------------------
A row is UNFITTED when fewer of its words carry timings than its text
has words. That is a count against a count - exact, and it cannot be
tuned to be quieter.

Measured on this project's own shipped transcript (940 rows, geo-podcast,
2026-09-16): **925 rows have exactly as many timed words as text words,
15 have fewer, and NOT ONE has more.** The two sides are the same
whitespace tokenisation, so the comparison has no false-positive
mechanism to calibrate away.

Of the 15: **13 lost the whole row** (`words: []`) and **2 lost part of
one** - a hallucinated Korean clause whose five invented tokens the
aligner could find no audio for, and a `20%.` the aligner dropped.

What is reported BESIDE the finding, and is not one
---------------------------------------------------
**A numeral the aligner cannot pronounce.** wav2vec2 aligns characters
against audio, so `10`, `2.0` and `2023` have nothing to align; the
transcriber marks them `timed: false` and interpolates a position. All
**19** on the shipped transcript are numerals, every one is placed, and
none is a defect. They are counted and named (`interpolated_words`)
rather than folded into the finding, for the reason
`heard_speech.anomalies` exists: a reader who cannot tell the
transcriber's own structural limits from the edit's mistakes is being
cried wolf at.

How badly a row failed, without inventing a bound
-------------------------------------------------
`implied_rate` is the row's text divided by its own span - the speaking
rate the row asserts. It is REPORTED, never compared against a constant:
the document's own fastest FITTED row is the reference, so a document is
calibrated against itself. On the shipped transcript the fastest row
that did fit runs at **11.76 words per second**, and twelve of the
thirteen lost rows assert between 13.0 and **300.0** - text that no
speaker could have said in that span. The thirteenth asserts 8.97, a
rate a person can reach; it lost its words anyway.

That distinction is what a reader needs and what a threshold would
destroy: an impossible row is a hallucination to be cut, a possible one
is a row to be re-aligned.

Where it is read
----------------
Two places, for two different questions, and the split is deliberate:

- **`library/tools/reel_hearing.py`** asks it of the rows ONE REEL
  PLAYS, and emits `transcript_row_fit` as a finding beside the three
  it already makes. A reader looking at an uncaptioned passage finds
  its cause in the same record.
- **`python3 -m library.tools.transcript_fit <project>`** asks it of
  the WHOLE document. The population is a property of the transcript,
  not of any reel: 15 rows exist whether or not a reel plays one, and a
  project that has delivered nothing yet cannot hear a reel at all. The
  Reel 26 defect was knowable the day the transcript was written.

It REPORTS. Nothing here gates, and `reel_hearing.GATES` governs the
reel-side half unchanged. Fixing the CAUSE is two separately filed
tasks; this reads the signal.
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence

WHOLE_ROW = "whole_row_lost"
"""Every word of the row failed. `words: []` with text still present."""

PART_OF_ROW = "part_of_row_lost"
"""Some words aligned and some did not. The row is half placeable."""

KINDS = (WHOLE_ROW, PART_OF_ROW)
"""Every way a row can fail to fit. Complete."""

TIMED_KEY = "timed"
"""The per-word flag the transcriber sets False on a word it placed by
interpolation rather than by alignment. See `interpolated_words`."""


@dataclass(frozen=True)
class RowFit:
    """One transcript row whose text did not all reach a word timing."""

    kind: str
    source_file: str
    source_start: float
    source_end: float
    text: str
    text_words: int
    timed_words: int
    span_seconds: float
    implied_rate: Optional[float]
    """Words of text per second of the row's own span, or None when the
    span is not positive. REPORTED; compared to nothing."""

    def as_row(self) -> Dict[str, Any]:
        return {
            "kind": self.kind,
            "source_file": self.source_file,
            "source_start": round(self.source_start, 3),
            "source_end": round(self.source_end, 3),
            "text": self.text,
            "text_words": self.text_words,
            "timed_words": self.timed_words,
            "untimed_words": self.text_words - self.timed_words,
            "span_seconds": round(self.span_seconds, 3),
            "implied_words_per_second": (None if self.implied_rate is None
                                         else round(self.implied_rate, 2)),
        }


def _span(segment: Dict[str, Any]) -> Optional[tuple]:
    """The row's source span, falling back to its timeline span.

    A row the pipeline could not bind to a source clip carries None for
    both source times and is still a real row with a real duration, so
    the timeline span answers for it. A row with neither has no span at
    all and no rate can be asserted about it.
    """
    start, end = segment.get("source_start"), segment.get("source_end")
    if start is None or end is None:
        start, end = segment.get("timeline_start"), segment.get("timeline_end")
    if start is None or end is None:
        return None
    return float(start), float(end)


def row_fit(segment: Dict[str, Any]) -> Optional[RowFit]:
    """One row, measured. None when its text all reached a timing.

    The comparison is text words against timed words, both by whitespace
    - see the module docstring on why that has no false-positive
    mechanism on this project's own transcript.
    """
    text = str(segment.get("text") or "")
    text_words = len(text.split())
    if not text_words:
        return None
    timed_words = len(segment.get("words") or [])
    if timed_words >= text_words:
        return None
    span = _span(segment)
    start, end = span if span else (0.0, 0.0)
    width = end - start
    return RowFit(
        kind=WHOLE_ROW if timed_words == 0 else PART_OF_ROW,
        source_file=str(segment.get("source_file") or ""),
        source_start=start, source_end=end, text=text,
        text_words=text_words, timed_words=timed_words,
        span_seconds=width,
        implied_rate=(text_words / width) if width > 0 else None,
    )


def unfitted_rows(document: Dict[str, Any]) -> List[RowFit]:
    """Every row of one transcript document whose text did not fit."""
    found = [row_fit(segment)
             for segment in (document.get("segments") or [])]
    return [row for row in found if row is not None]


def fastest_fitted_rate(document: Dict[str, Any]) -> Optional[float]:
    """The highest speaking rate a row that DID fit asserts.

    The document's own reference for reading an unfitted row's
    `implied_rate`, so nothing here is calibrated against a constant. A
    single-word row is excluded: its span is a word boundary rather than
    a passage, and one short word can assert 25 words per second without
    anything being wrong.
    """
    best: Optional[float] = None
    for segment in document.get("segments") or []:
        text_words = len(str(segment.get("text") or "").split())
        if text_words < 2:
            continue
        if len(segment.get("words") or []) < text_words:
            continue
        span = _span(segment)
        if not span or span[1] - span[0] <= 0:
            continue
        rate = text_words / (span[1] - span[0])
        if best is None or rate > best:
            best = rate
    return best


def interpolated_words(document: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Words the aligner placed WITHOUT aligning them. Not a finding.

    `timed: false` with a start and an end: the transcriber could not
    align the token and interpolated a position for it. Every one of the
    19 on this project's shipped transcript is a numeral, which wav2vec2
    has no characters to align. Counted and named so a reader can tell
    them from the rows that lost their words - which is a different
    thing entirely, and the only thing this module reports on.
    """
    found: List[Dict[str, Any]] = []
    for segment in document.get("segments") or []:
        for word in segment.get("words") or []:
            if word.get(TIMED_KEY, True):
                continue
            found.append({
                "word": word.get("word"),
                "start": word.get("start"),
                "end": word.get("end"),
                "source_file": segment.get("source_file"),
            })
    return found


def scan(document: Dict[str, Any]) -> Dict[str, Any]:
    """The whole document, measured. The upstream half of the report."""
    rows = unfitted_rows(document)
    segments = document.get("segments") or []
    reference = fastest_fitted_rate(document)
    impossible = [row for row in rows
                  if reference is not None and row.implied_rate is not None
                  and row.implied_rate > reference]
    return {
        "rows": len(segments),
        "unfitted_rows": len(rows),
        "words_with_no_timing": sum(r.text_words - r.timed_words
                                    for r in rows),
        "whole_rows_lost": sum(1 for r in rows if r.kind == WHOLE_ROW),
        "parts_of_rows_lost": sum(1 for r in rows if r.kind == PART_OF_ROW),
        "fastest_fitted_words_per_second": (None if reference is None
                                            else round(reference, 2)),
        "rows_asserting_a_rate_no_fitted_row_reaches": len(impossible),
        "interpolated_words": len(interpolated_words(document)),
        "rows_detail": [row.as_row() for row in rows],
    }


def summary_lines(report: Dict[str, Any], where: str) -> List[str]:
    """What a reader is told about a whole transcript, as lines."""
    lines = [
        f"Transcript fit: {where}",
        f"  rows:     {report['rows']}",
        f"  unfitted: {report['unfitted_rows']} "
        f"({report['whole_rows_lost']} lost the whole row, "
        f"{report['parts_of_rows_lost']} lost part of one) - "
        f"{report['words_with_no_timing']} words carry no timing",
    ]
    reference = report["fastest_fitted_words_per_second"]
    if reference is not None:
        lines.append(
            f"  the fastest row that DID fit asserts {reference} words per "
            f"second; {report['rows_asserting_a_rate_no_fitted_row_reaches']}"
            f" unfitted row(s) assert more than that, which no speaker said")
    for row in report["rows_detail"]:
        lines.append(
            f"    {row['source_start']:.2f}s "
            f"({row['untimed_words']} of {row['text_words']} words untimed "
            f"in {row['span_seconds']:.2f}s = "
            f"{row['implied_words_per_second']} w/s): {row['text'][:70]!r}")
    lines.append(
        f"  beside it, NOT a finding: {report['interpolated_words']} word(s) "
        f"the aligner placed without aligning - numerals it has no "
        f"characters for")
    lines.append("  gates:    no - this is a report, not a gate")
    return lines


def main(argv: Optional[List[str]] = None) -> int:
    """`python3 -m library.tools.transcript_fit <project-or-transcript>`.

    Reads a document off disk and says nothing else: no aligner, no
    audio, no model, no write into the project. The one surface that
    can answer this before a reel has ever been delivered.
    """
    import argparse

    from library.tools import timeline_transcript

    parser = argparse.ArgumentParser(
        description="Which transcript rows carry text their audio cannot "
                    "hold. Reports; never gates.")
    parser.add_argument("target",
                        help="A project folder, or a transcript.json")
    parser.add_argument("--json", action="store_true",
                        help="Print the report as JSON")
    args = parser.parse_args(argv)

    path = args.target
    if os.path.isdir(path):
        path = timeline_transcript.transcript_path(path)
    if not os.path.isfile(path):
        print(f"REFUSED: there is no transcript at {path}. It is written "
              f"by library/tools/timeline_transcript.py.", file=sys.stderr)
        return 1
    with open(path, encoding="utf-8") as handle:
        document = json.load(handle)
    report = scan(document)
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print("\n".join(summary_lines(report, path)))
    # Zero whatever it found: a report is not a failure.
    return 0


if __name__ == "__main__":
    sys.exit(main())
