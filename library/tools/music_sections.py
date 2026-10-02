"""The measured section grid, read from what `music_analysis` emits.

Step 2.06's `section_grid` (allin1, harmonix-all) names the track's
functional sections - intro, verse, chorus, bridge, solo, outro and
what else the model gives - with boundaries snapped to the nearest
downbeat OF THE SAME RUN. This module is the single place that knows
that shape, so there is one name to change if it ever moves, exactly
as `library/tools/beat_grid.py` is for the beat series.

**Time domain.** Section times are measured in the MUSIC FILE's clock,
like the beats. The bed plays at whatever `source_in` the model's
chosen section names (`library/tools/music_section.py`), so timeline
time is `file time - source_in` and a grid used unmapped is wrong by
the offset on every section. Every reader here takes the selection
and RETURNS TIMELINE TIME, and the selection argument is required
rather than defaulted for the same reason it is in `beat_grid.py`: a
default of "no offset" is the value that is silently wrong.

**Addressable sections.** The `start`/`end` sentinels allin1 emits are
bookkeeping, not music - no plan entry addresses them and the lookup
refuses them by name. A label the model did not emit (a drop, a
phrase, a chorus on a track whose grid has none) is reported with
what IS present, never coined. An unavailable grid is an empty
reading: callers that PLACE refuse (the anchor), callers that SHOW
omit the view.

`tests/unit/audio/test_music_bed.py`.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

# allin1's bookkeeping sentinels. Kept in the grid (they prove coverage
# is contiguous) but never addressable: targeting a 0.16 s `start`
# sliver is a mistake to report, not one to place.
NON_ADDRESSABLE = ("start", "end")


def available(music_analysis: Optional[Dict[str, Any]]) -> bool:
    """Whether a usable section grid is routed."""
    grid = (music_analysis or {}).get("section_grid")
    if not isinstance(grid, dict):
        return False
    if not grid.get("available"):
        return False
    sections = grid.get("sections")
    return isinstance(sections, list) and bool(sections)


def _raw_sections(music_analysis: Optional[Dict[str, Any]]) -> List[dict]:
    grid = (music_analysis or {}).get("section_grid") or {}
    sections = grid.get("sections")
    return [s for s in sections if isinstance(s, dict)] if isinstance(
        sections, list) else []


def _windows(music_selection: Optional[Dict[str, Any]],
             audio_spine: Optional[Dict[str, Any]],
             timeline_duration: Optional[float]) -> list:
    """File-to-timeline windows, the same shape `beat_grid` maps through.

    A spliced bed maps per segment; the single-section reading maps
    through the chosen section's offset. One function so the two grids
    can never disagree about the clock.
    """
    from library.tools.music_bed import beat_windows, read_bed
    from library.tools.music_section import section_offset_seconds

    windows = []
    if audio_spine is not None and timeline_duration is not None:
        try:
            bed = read_bed(music_selection, audio_spine, timeline_duration)
        except Exception:
            bed = None
        if bed is not None and bed.declared:
            primary = (music_selection or {}).get("audio_path") or ""
            windows = beat_windows(bed, primary)
    if not windows:
        offset = section_offset_seconds(music_selection)
        windows = [{"source_in": offset, "source_out": float("inf"),
                    "timeline_start": 0.0, "offset": offset}]
    return windows


def _map(value: float, windows: list) -> Optional[float]:
    for window in windows:
        if window["source_in"] <= value < window["source_out"]:
            t = value - window["offset"]
            return round(t, 4) if t >= 0 else None
    return None


def sections_timeline(
        music_analysis: Optional[Dict[str, Any]],
        music_selection: Optional[Dict[str, Any]],
        audio_spine: Optional[Dict[str, Any]] = None,
        timeline_duration: Optional[float] = None) -> List[dict]:
    """Every measured section, in TIMELINE seconds, in file order.

    Empty when the grid is absent or unavailable - empty means "do not
    show, do not place", which is what every caller already does with
    an empty beat grid. Each row carries the model label verbatim, the
    timeline-mapped span, the timeline-mapped first downbeat (the
    bar-aligned anchor moment), and the model's own activation reading.
    """
    if not available(music_analysis):
        return []
    windows = _windows(music_selection, audio_spine, timeline_duration)
    rows = []
    for section in _raw_sections(music_analysis):
        try:
            start = float(section["start"])
            end = float(section["end"])
            first = float(section.get("first_downbeat", section["start"]))
        except (TypeError, ValueError, KeyError):
            continue
        mapped_start = _map(start, windows)
        mapped_end = _map(end, windows)
        mapped_first = _map(first, windows)
        if mapped_start is None or mapped_first is None:
            # Starts before the chosen section: not in the edit.
            continue
        rows.append({
            "label": str(section.get("label", "")),
            "start_seconds": mapped_start,
            "end_seconds": (mapped_end if mapped_end is not None
                            else round(mapped_start + max(end - start, 0), 4)),
            "first_downbeat_seconds": mapped_first,
            "mean_label_activation": section.get("mean_label_activation"),
        })
    return rows


def addressable_labels(music_analysis: Optional[Dict[str, Any]]) -> List[str]:
    """Labels a plan entry may name, in grid order, sentinels excluded."""
    return [s["label"] for s in _raw_sections(music_analysis)
            if str(s.get("label", "")) not in NON_ADDRESSABLE]


def find_sections(music_analysis: Optional[Dict[str, Any]],
                  music_selection: Optional[Dict[str, Any]],
                  label: str,
                  audio_spine: Optional[Dict[str, Any]] = None,
                  timeline_duration: Optional[float] = None
                  ) -> Tuple[List[dict], List[str]]:
    """Timeline-mapped sections named `label`, plus labels the grid has.

    Returns `(matches, present)`: the rows named (empty where none) and
    the addressable labels for the refusal fix. Never raises for a
    missing label - the caller owns the refusal shape.
    """
    rows = [r for r in sections_timeline(
        music_analysis, music_selection, audio_spine, timeline_duration)
        if r["label"] == label]
    return rows, addressable_labels(music_analysis)
