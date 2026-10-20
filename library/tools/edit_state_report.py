"""AGENT-014: one read of what an execution actually did.

Every write path already captures the state before and the state after.
`edit_patch` re-reads the live timeline under the lease and refuses when
it cannot (`edit_patch.py:867-900`, `:1162-1196`); the AXI path verifies
the whole request against one fresh read (`resolve_axi_core.py:962-968`);
the UI transaction records `observed_before` and `observed_after`; a
touch-up records `timeline_inventory_before`; the undo journal records
`preservation_before` and `preservation_after`; a build records its
inventory before it starts.

So the capture is universal. What was missing is a READER: the data sits
in five different places, and an agent that wanted to know "did my edit
land, and what did it change?" had to know which of them to ask, and
hand-author the comparison. The co-editor skill listed eight or nine
verification verbs for exactly this.

This module is that reader, over the artifacts that already exist. It
adds NO capture path - a second capture is how two records of the same
state come to disagree.

**It says what the RECORDED state shows, and it says which.** The shadow
store's generations are the recorded states; each one names its source,
so a reader can tell a Ren write (`patch`) from a hand edit
(`observed`) from an import. The verdict is about the recorded state, not
about the live timeline: the head generation is the timeline as of its
`verified_at`, and a hand edit made afterwards is invisible until the
next read. A report that hid that would over-claim.

The verdicts are:

``unreadable``   the state cannot be established - no generation, or the
                 head's body is gone.
``first_read``   this is the first generation; there is no "before" to
                 compare against, and the report says so rather than
                 reporting a change of nothing.
``unchanged``    the head's hash equals the previous generation's: the
                 state did not change.
``changed``      the head's hash differs: something changed, and the diff
                 says what.

`timeline_shadow.diff` is the diff, so this report cannot disagree with
`versions.rounds.diff_reel` - the differ promotion and round-diff already
use - about what a row holds.
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from library.tools import timeline_shadow
from library.tools.timeline_shadow import ShadowError, ShadowStore

#: The source of a generation that Ren itself wrote.
PATCH = timeline_shadow.PATCH
#: The source of a generation read from the live timeline.
OBSERVED = timeline_shadow.OBSERVED

UNREADABLE = "unreadable"
FIRST_READ = "first_read"
UNCHANGED = "unchanged"
CHANGED = "changed"

VERDICTS = (UNREADABLE, FIRST_READ, UNCHANGED, CHANGED)


def _state_of(generation, snapshot: Mapping | None) -> dict:
    """One generation as the report states it."""
    return {
        "generation": generation.generation,
        "hash": generation.hash,
        "source": generation.source,
        "patch_id": (generation.patch or {}).get("id"),
        "recorded_at": generation.recorded_at,
        "verified_at": generation.verified_at,
        "clips": len(snapshot.get("tracks") or ()) if snapshot else 0,
    }


def state_report(project: str, timeline: str, *,
                 generation: int | None = None,
                 store: ShadowStore | None = None) -> dict:
    """What the recorded generations say happened to a timeline.

    `timeline` is the timeline's EXACT name or its id - the shadow
    store's own addressing, never a prefix.
    """
    store = store or ShadowStore()
    try:
        timeline_id = store.resolve_timeline(project, timeline)
    except ShadowError as exc:
        return {
            "project": project, "timeline": timeline, "verdict": UNREADABLE,
            "reason": str(exc), "before": None, "after": None,
            "changed": False, "diff": None,
        }
    rows = store.since_with_snapshots(project, timeline_id, 0)
    if not rows:
        return {
            "project": project, "timeline": timeline, "verdict": UNREADABLE,
            "reason": (f"timeline {timeline!r} in project {project!r} has no "
                       "recorded generation"),
            "before": None, "after": None, "changed": False, "diff": None,
        }
    if generation is None:
        selected = rows[-1]
    else:
        matching = [row for row in rows if row[0].generation == generation]
        if not matching:
            return {
                "project": project, "timeline": timeline,
                "verdict": UNREADABLE,
                "reason": (f"no generation {generation} of timeline "
                           f"{timeline!r} in project {project!r}"),
                "before": None, "after": None, "changed": False,
                "diff": None,
            }
        selected = matching[-1]
    index = rows.index(selected)
    before = rows[index - 1] if index else None
    after_generation, after_snapshot = selected
    report: dict[str, Any] = {
        "project": project,
        "timeline": after_generation.timeline_name,
        "timeline_id": timeline_id,
        "generations": len(rows),
    }
    if before is None:
        report.update({
            "verdict": FIRST_READ,
            "reason": ("this is the first recorded generation of this "
                       "timeline - there is no earlier state to compare "
                       "against, so the report cannot say what changed"),
            "before": None,
            "after": _state_of(after_generation, after_snapshot),
            "changed": False,
            "diff": None,
        })
        return report
    before_generation, before_snapshot = before
    changed = before_generation.hash != after_generation.hash
    report.update({
        "verdict": CHANGED if changed else UNCHANGED,
        "reason": "",
        "before": _state_of(before_generation, before_snapshot),
        "after": _state_of(after_generation, after_snapshot),
        "changed": changed,
        "diff": (timeline_shadow.diff(before_snapshot, after_snapshot)
                 if changed else None),
    })
    return report


def render(report: Mapping) -> str:
    """The report as the front door prints it."""
    import json

    return json.dumps(report, indent=2, sort_keys=True, default=str)
