"""Measure before you build: the cross-reel census that runs first.

The 2026-09-11 round learned the caption row from a census that
existed only AFTER a four-reel build, a refusal and a discard: four
reels at tilt -425..-436 against one at -870. That census is a
read-only probe over state the build is about to change, and it takes
minutes. Had it run first, the day would have been census, decide the
row, build once.

So `rebuild_reels_in_project` calls `report_prebuild` before placing
anything, on every multi-reel build. It reads each reel-about-to-build's
existing final timeline (exact name; a reel with no timeline yet is
noted, not compared - a fresh build changes no state), takes the
characteristic stored Pan/Tilt per overlay row, and PRINTS the table
with any cross-reel disagreement flagged. It never refuses: a
legitimate difference is allowed, and a build must not be hostage to
a warning.

What is compared
----------------
Singleton video rows whose placements come from project-level values
(the caption row from `subtitle_style.project_caption_row`, intent
pins from `overlay_intent`) - the same kind of element sitting in
materially different places on different reels is the signal that a
decision is owed before the build, not after. `B-Roll` is excluded:
its placements are per-cutaway content decisions, so a cross-reel
spread there signals nothing actionable. Picture and speech rows are
excluded for the same reason - every reel conforms differently.

`CROSS_REEL_SPREAD_UNITS` is calibrated off the round that motivated
this: the agreeing reels spread 11 stored units, the defect 435. The
flag must clear the first and catch the second; 40 sits between them
with margin on both sides. A wrong threshold costs one printed line,
never a blocked build - which is why this reports rather than
refuses.
"""

from __future__ import annotations

import statistics
from typing import Dict, List, Mapping, Optional, Sequence


#: Stored Pan/Tilt units. Agreement spread 11, defect 435 (2026-09-11);
#: the flag must clear the first and catch the second.
CROSS_REEL_SPREAD_UNITS = 40.0

#: Singleton video rows compared across reels. `B-Roll` is out: its
#: placements are per-cutaway content, not a project-level value, so a
#: spread there is expected rather than actionable. Audio rows carry
#: no Pan/Tilt meaning.
CENSUS_ROWS = frozenset({
    "Subtitles", "Captions", "Transitions", "Explainer", "Semantic",
    "Frame", "Generator Effects", "Timed Text",
})


def _position(clip) -> Optional[tuple]:
    """A snapshot clip's stored (pan, tilt), or None when unreadable."""
    transform = getattr(clip, "transform", None) or {}
    try:
        return (float(transform["Pan"]), float(transform["Tilt"]))
    except (KeyError, TypeError, ValueError):
        return None


def row_characteristics(clips: Sequence) -> dict:
    """Characteristic stored position per census row: `{row: {...}}`.

    The median Pan/Tilt over the row's readable clips - caption cards
    each carry their own tight-box tilt, so the median is the row's
    characteristic place, robust to one odd card. `n` counts the
    clips behind it; rows with no readable clip are absent, never
    zero-filled.
    """
    by_row: Dict[str, dict] = {}
    for clip in clips or ():
        if getattr(clip, "track_type", "") != "video":
            continue
        row = getattr(clip, "track_name", "") or ""
        if row not in CENSUS_ROWS:
            continue
        position = _position(clip)
        if position is None:
            continue
        entry = by_row.setdefault(row, {"pans": [], "tilts": []})
        entry["pans"].append(position[0])
        entry["tilts"].append(position[1])
    return {
        row: {"pan": statistics.median(entry["pans"]),
              "tilt": statistics.median(entry["tilts"]),
              "n": len(entry["pans"])}
        for row, entry in by_row.items()
    }


def compare_reel_rows(per_reel: Mapping[str, dict]) -> dict:
    """Cross-reel spreads per row, over reels that have the row.

    `per_reel` maps final timeline name to its `row_characteristics`.
    A row on fewer than two reels has nothing to disagree with and is
    reported as single, not flagged. Otherwise the tilt and pan
    spreads (max minus min of the medians) flag past
    `CROSS_REEL_SPREAD_UNITS`. Returns the per-row table plus the flat
    `disagreements` list - row keys a decision is owed on.
    """
    rows: Dict[str, dict] = {}
    disagreements: List[str] = []
    row_names = sorted({row for chars in per_reel.values() for row in chars})
    for row in row_names:
        holders = sorted(final for final, chars in per_reel.items()
                         if row in chars)
        if len(holders) < 2:
            rows[row] = {"holders": holders, "single": True,
                         "disagree": False}
            continue
        tilts = [per_reel[final][row]["tilt"] for final in holders]
        pans = [per_reel[final][row]["pan"] for final in holders]
        tilt_spread = max(tilts) - min(tilts)
        pan_spread = max(pans) - min(pans)
        disagree = (tilt_spread > CROSS_REEL_SPREAD_UNITS
                    or pan_spread > CROSS_REEL_SPREAD_UNITS)
        rows[row] = {
            "holders": holders,
            "tilt_spread": tilt_spread,
            "pan_spread": pan_spread,
            "values": {final: {"pan": per_reel[final][row]["pan"],
                               "tilt": per_reel[final][row]["tilt"],
                               "n": per_reel[final][row]["n"]}
                       for final in holders},
            "single": False,
            "disagree": disagree,
        }
        if disagree:
            disagreements.append(row)
    return {"rows": rows, "disagreements": sorted(disagreements),
            "reels_compared": sorted(per_reel)}


def render_census(report: Mapping, finals: Sequence[str]) -> str:
    """The printable census: every compared row, flags where owed."""
    lines = [f"── Pre-build cross-reel census "
             f"({len(finals)} reel(s) about to build) ──"]
    rows = (report or {}).get("rows", {})
    if not rows:
        lines.append("  no overlay row on two or more reels to compare - "
                     "nothing cross-reel to disagree on.")
        return "\n".join(lines)
    for row in sorted(rows):
        entry = rows[row]
        if entry.get("single"):
            lines.append(f"  video:{row}: only on "
                         f"{', '.join(entry['holders'])} - no cross-reel "
                         f"comparison.")
            continue
        cells = " ".join(
            f"{final.split(' - ')[0]}:"
            f"{entry['values'][final]['tilt']:.0f}"
            for final in entry["holders"])
        verdict = (f"spread {entry['tilt_spread']:.0f} - DISAGREES, decide "
                   f"the row before building (advisory: the build proceeds)")
        if not entry["disagree"]:
            verdict = f"spread {entry['tilt_spread']:.0f} - agree."
        lines.append(f"  video:{row} tilt medians {cells} {verdict}")
    return "\n".join(lines)


def census_for_build(project, finals: Sequence[str],
                     snapshot_fn=None) -> dict:
    """Read the state a build is about to change, without changing it.

    For each final, the existing timeline under its exact name is
    snapshotted read-only; a final with no timeline yet, or one that
    will not read, is recorded (`absent` / `error`) and simply takes
    no part in the comparison. Returns the `compare_reel_rows` report
    plus the per-final reading notes.
    """
    if snapshot_fn is None:
        from library.tools.timeline_ingest import snapshot_timeline
        snapshot_fn = snapshot_timeline
    by_name = {}
    for index in range(1, project.GetTimelineCount() + 1):
        timeline = project.GetTimelineByIndex(index)
        if timeline is not None:
            by_name[timeline.GetName()] = timeline
    per_reel: Dict[str, dict] = {}
    notes: Dict[str, str] = {}
    for final in finals:
        timeline = by_name.get(final)
        if timeline is None:
            notes[final] = "no existing timeline - fresh build, nothing to compare"
            continue
        try:
            snapshot = snapshot_fn(timeline, project.GetName())
            per_reel[final] = row_characteristics(snapshot.clips)
            notes[final] = (f"read "
                            f"{sum(e['n'] for e in per_reel[final].values())} "
                            f"overlay clip(s)")
        except Exception as unreadable:  # noqa: BLE001
            notes[final] = f"could not be read ({unreadable}) - not compared"
    report = compare_reel_rows(per_reel)
    report["notes"] = notes
    return report


def report_prebuild(project, finals: Sequence[str],
                    snapshot_fn=None) -> dict:
    """Read, compare, PRINT, return. Never raises, never refuses.

    The whole point is that this fires whether or not anyone
    remembers: the build calls it unconditionally, and a probe that
    could fail the build would be a warning holding a build hostage.
    Any failure - including a total one - is printed and returned as
    `{"unavailable": reason}`, and the build proceeds.
    """
    finals = list(finals or ())
    if len(finals) < 2:
        return {"rows": {}, "disagreements": [],
                "reels_compared": [],
                "notes": {final: "single-reel build - no cross-reel comparison"
                          for final in finals}}
    try:
        report = census_for_build(project, finals, snapshot_fn=snapshot_fn)
    except Exception as failed:  # noqa: BLE001
        report = {"rows": {}, "disagreements": [],
                  "reels_compared": [],
                  "unavailable": str(failed)}
    print(render_census(report, finals), flush=True)
    return report
