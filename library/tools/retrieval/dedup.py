"""Two hits on the same moment are ONE moment.

A search returns WINDOWS, and windows overlap.  A 20-second speech
utterance, the 12-second action window the vision pass cut inside it and
the 6-second OCR appearance inside that all describe the same stretch of
timeline.  Returned side by side they read as three candidate shots, and
an editor picking the third has picked the first.

`dedupe_windows` collapses them, and says what it collapsed.  The merged
window keeps the strongest hit's identity, carries the union of the
merged hits' evidence, and records every row it absorbed under
`merged_from` - so the merge is auditable and reversible by a reader,
which matters because a merge is a claim that two things are the same
thing.

**Redundant is not the same as overlapping.**  Two windows that touch by
one second are two windows: a long speech segment and a short action
inside it are different measurements, and merging them would report the
action as if it lasted as long as the speech.  Two windows are REDUNDANT
when the shorter one is mostly INSIDE the longer one - that is one
moment described twice.  The fraction is `REDUNDANT_OVERLAP_FRACTION`,
and it is a constant here rather than a per-project guess.

**The gap is separate, and opt-in.**  `merge_gap_s` joins windows that
NEARLY touch - one shot cutting to the next.  It is off by default
because joining two shots is an editorial claim, not a redundancy
claim, and the caller has to say so.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence

REDUNDANT_OVERLAP_FRACTION = 0.5
"""A window is redundant with another when the shorter is at least this
much of its duration inside the longer."""

DEFAULT_MERGE_GAP_SECONDS = 0.0
"""Windows closer than this are joined.  Zero means never, unless the
caller says otherwise."""


def _seconds(value) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _span(hit: dict) -> tuple[str, float, float] | None:
    """The clip and interval of a hit, or None when it has neither."""
    clip_id = hit.get("clip_id")
    start = _seconds(hit.get("start"))
    end = _seconds(hit.get("end"))
    if not clip_id or start is None or end is None:
        return None
    return str(clip_id), start, max(start, end)


def _overlap(a: tuple[float, float], b: tuple[float, float]) -> float:
    return max(0.0, min(a[1], b[1]) - max(a[0], b[0]))


def _gap(a: tuple[float, float], b: tuple[float, float]) -> float:
    if a[1] < b[0]:
        return b[0] - a[1]
    if b[1] < a[0]:
        return a[0] - b[1]
    return 0.0


def _is_redundant(a: tuple[float, float], b: tuple[float, float],
                  merge_gap_s: float) -> tuple[bool, str]:
    """Whether two intervals describe the same moment."""
    overlap = _overlap(a, b)
    shorter = min(a[1] - a[0], b[1] - b[0])
    if shorter > 0 and overlap >= REDUNDANT_OVERLAP_FRACTION * shorter:
        return True, "one window is mostly inside the other"
    if merge_gap_s > 0 and _gap(a, b) <= merge_gap_s:
        return True, f"the windows are within {merge_gap_s} s of each other"
    return False, ""


def _score_of(hit: dict) -> float:
    value = _seconds(hit.get("score"))
    return value if value is not None else 0.0


def _timecode(seconds: float) -> str:
    minutes, rest = divmod(seconds, 60)
    return f"{int(minutes):02d}:{rest:06.3f}"


def _merged_row(rows: Sequence[dict], span: tuple[float, float]) -> dict:
    """The one row that stands for several overlapping rows."""
    best = max(rows, key=lambda row: (_score_of(row), -span[0]))
    merged = dict(best)
    merged["start"] = round(span[0], 3)
    merged["end"] = round(span[1], 3)
    merged["duration"] = round(span[1] - span[0], 3)
    merged["timecode"] = f"{_timecode(span[0])}-{_timecode(span[1])}"
    merged["merged_from"] = [
        {
            "segment_id": row.get("segment_id"),
            "kind": row.get("kind"),
            "score": _score_of(row),
            "start": _seconds(row.get("start")),
            "end": _seconds(row.get("end")),
        }
        for row in rows
    ]
    kinds: list[str] = []
    for row in rows:
        kind = row.get("kind")
        if kind and kind not in kinds:
            kinds.append(kind)
    merged["kinds"] = kinds
    words: list[dict] = []
    for row in rows:
        for word in row.get("word_hits") or []:
            if word not in words:
                words.append(word)
    if words:
        merged["word_hits"] = words
    return merged


def dedupe_windows(hits: Iterable[dict], *,
                   merge_gap_s: float = DEFAULT_MERGE_GAP_SECONDS
                   ) -> tuple[list[dict], dict]:
    """Collapse overlapping and redundant windows, and report the merge.

    Returns the surviving rows and a report naming what was merged and
    why.  The surviving rows keep their original order within a clip and
    the clips keep their original order, so a caller that cared about
    ranking still has it.

    A row without a clip and an interval is passed through UNTOUCHED:
    a hit that cannot be placed on the timeline cannot be judged
    redundant against anything.
    """
    rows = list(hits or [])
    placed: list[tuple[int, dict, tuple[str, float, float]]] = []
    untouched: list[tuple[int, dict]] = []
    for index, hit in enumerate(rows):
        span = _span(hit)
        if span is None:
            untouched.append((index, hit))
        else:
            placed.append((index, hit, span))

    by_clip: dict[str, list[tuple[int, dict, tuple[str, float, float]]]] = {}
    for entry in placed:
        by_clip.setdefault(entry[2][0], []).append(entry)

    merged_rows: list[tuple[int, dict]] = []
    redundant_pairs: list[dict] = []
    for clip_id, clip_entries in by_clip.items():
        entries = sorted(clip_entries, key=lambda e: (e[2][1], e[2][2]))
        group: list[tuple[int, dict, tuple[str, float, float]]] = []
        span: tuple[float, float] | None = None
        for entry in entries:
            interval = (entry[2][1], entry[2][2])
            if span is None:
                group = [entry]
                span = interval
                continue
            redundant, reason = _is_redundant(span, interval, merge_gap_s)
            if redundant:
                redundant_pairs.append({
                    "clip_id": clip_id,
                    "kept": group[0][1].get("segment_id"),
                    "absorbed": entry[1].get("segment_id"),
                    "reason": reason,
                })
                group.append(entry)
                span = (min(span[0], interval[0]), max(span[1], interval[1]))
                continue
            merged_rows.append((group[0][0], _merged_row(
                [row for _, row, _ in group], span)))
            group = [entry]
            span = interval
        if group:
            merged_rows.append((group[0][0], _merged_row(
                [row for _, row, _ in group], span)))

    survivors = merged_rows + untouched
    survivors.sort(key=lambda entry: entry[0])
    kept = [row for _, row in survivors]
    report = {
        "input": len(rows),
        "output": len(kept),
        "merged": len(rows) - len(kept),
        "redundant_pairs": redundant_pairs,
        "overlap_fraction": REDUNDANT_OVERLAP_FRACTION,
        "merge_gap_seconds": merge_gap_s,
    }
    return kept, report
