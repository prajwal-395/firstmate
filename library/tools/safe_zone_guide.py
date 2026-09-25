"""Put a platform safe-zone guide on a reel timeline, where it can never render.

The captain, 2026-09-25, asked for overlays "that allows us to make sure
our video and all of the elements in it are positioned outside of the
boundings of where the UI elements for these apps are". The overlays
are ``library/presets/safe-zones/`` (``platform_safe_zones``); this
module is how one reaches a timeline.

A guide goes on its OWN video row, named ``timeline_layout.GUIDES_NAME``,
above every row the build placed, and its ITEM is switched OFF - Resolve
does not render a disabled clip, so a guide cannot reach an export
however the reel is delivered. The switch is judged by what Resolve
reads back, never by what the setter returned (AGENTS.md 5), and the
row disable is attempted too but not trusted: on Resolve 21.1 it
returned True, read back enabled and rendered (see
:func:`place_on_timeline`). To look through a guide, select it in the
Edit page and enable the clip (D).

Placing is idempotent: an existing guide row is removed first, so a
second call swaps the overlay rather than stacking two. ``remove`` takes
the row away and places nothing. A rebuild replaces the whole timeline,
so a guide does not survive one; place it again afterwards.

The cursor moves only through `resolve_lock.cursor_excursion`, which
puts it back.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Sequence


class SafeZoneGuideError(RuntimeError):
    """A guide that could not be placed, disabled or removed - RAISED."""


def guide_movie(project_folder: str, overlay: str, frames: int,
                fps: float) -> str:
    """The overlay PNG carried as a looped movie of at least ``frames``.

    A still cannot be placed for an arbitrary length through Resolve's
    API (``reel_look.frame_overlay_segments`` has the measurement), so
    the guide takes the TV frame's carriage.
    """
    from library.tools import platform_safe_zones as psz
    from library.tools.project_layout import Area, ProjectLayout
    from library.tools.reel_post_header import carry_still

    png = psz.overlay_path(overlay)
    if not os.path.isfile(png):
        raise SafeZoneGuideError(
            f"no overlay at {png}; regenerate with "
            f"`python3 -m library.tools.platform_safe_zones --write`")
    out_dir = str(ProjectLayout(project_folder).write_dir(
        Area.REEL_SAFE_ZONE_GUIDES))
    os.makedirs(out_dir, exist_ok=True)
    stamp = f"{os.path.getsize(png)}_{int(os.path.getmtime(png))}"
    return carry_still(png, int(frames), float(fps),
                       os.path.join(out_dir,
                                    f"safe_zones_{overlay}_{stamp}.mov"))


def _guide_rows(timeline) -> list[int]:
    from library.tools.timeline_layout import GUIDES_NAME

    count = int(timeline.GetTrackCount("video") or 0)
    return [i for i in range(1, count + 1)
            if timeline.GetTrackName("video", i) == GUIDES_NAME]


def _remove_guides(timeline) -> int:
    removed = 0
    for index in sorted(_guide_rows(timeline), reverse=True):
        if not timeline.DeleteTrack("video", index):
            raise SafeZoneGuideError(
                f"Resolve refused to delete guide row V{index} on "
                f"{timeline.GetName()!r}")
        removed += 1
    return removed


def place_on_timeline(project, timeline, project_folder: str, overlay: str,
                      fps: float) -> dict:
    """Place (or with ``overlay=None`` remove) the guide on one timeline.

    ``timeline`` must be CURRENT; the caller owns the cursor.
    """
    from library.tools.timeline_layout import GUIDES_NAME

    name = timeline.GetName()
    removed = _remove_guides(timeline)
    if overlay is None:
        return {"timeline": name, "removed": removed, "placed": None}

    start = int(timeline.GetStartFrame())
    frames = int(timeline.GetEndFrame()) - start + 1
    movie = guide_movie(project_folder, overlay, frames, fps)

    if not timeline.AddTrack("video"):
        raise SafeZoneGuideError(f"Resolve would not add a row to {name!r}")
    index = int(timeline.GetTrackCount("video"))
    timeline.SetTrackName("video", index, GUIDES_NAME)
    if timeline.GetTrackName("video", index) != GUIDES_NAME:
        raise SafeZoneGuideError(f"the guide row on {name!r} would not "
                                 f"take its name")

    pool = project.GetMediaPool()
    imported = pool.ImportMedia([movie]) or []
    if not imported:
        raise SafeZoneGuideError(f"Resolve would not import {movie}")
    appended = pool.AppendToTimeline([{
        "mediaPoolItem": imported[0], "startFrame": 0,
        "endFrame": frames - 1, "trackIndex": index,
        "recordFrame": start, "mediaType": 1,
    }]) or []
    if not appended:
        raise SafeZoneGuideError(f"Resolve would not place the guide on "
                                 f"{name!r} V{index}")

    # The ITEM is what keeps the guide out of a render. Measured on
    # Resolve Studio 21.1, 2026-09-25, on a duplicate of Reel 04:
    # `SetTrackEnable("video", n, False)` returned True five times in a
    # row, `GetIsTrackEnabled` read True after every one, and the guide
    # RENDERED into the export; `SetClipEnabled(False)` read back False
    # and the next render carried no guide pixel. So each placed item is
    # switched off and read back, and the row disable is only an extra.
    placed = timeline.GetItemListInTrack("video", index) or []
    for item in placed:
        item.SetClipEnabled(False)
        if item.GetClipEnabled() is not False:
            timeline.DeleteTrack("video", index)
            raise SafeZoneGuideError(
                f"the guide on {name!r} V{index} reads back ENABLED after "
                f"the switch-off, so it would render; the row was removed "
                f"again")
    timeline.SetTrackEnable("video", index, False)
    row_disabled = timeline.GetIsTrackEnabled("video", index) is False
    return {"timeline": name, "removed": removed, "placed": overlay,
            "row": index, "movie": movie, "enabled": False,
            "row_disabled": row_disabled}


def place_guides(project_folder: str, reel_numbers: Sequence[int] | None,
                 overlay: str | None) -> list[dict]:
    """Place ``overlay`` (or remove, with None) on the named reels' timelines.

    ``reel_numbers`` None means every approved reel. The caller holds the
    Resolve lease.
    """
    from library.tools import platform_safe_zones as psz
    from library.tools.marker_feedback import connect_resolve
    from library.tools.reel_proposal import approved_only, proposal_path, read_proposal
    from library.tools.resolve_lock import cursor_excursion
    from library.tools.timeline_ingest import (
        resolve_binding,
        resolve_project_exactly,
        timeline_named,
    )

    if overlay is not None:
        psz.platforms_for(overlay)
    moments = approved_only(read_proposal(proposal_path(project_folder)))
    if reel_numbers:
        wanted = {int(n) for n in reel_numbers}
        moments = [m for m in moments if int(m.number) in wanted]
        missing = wanted - {int(m.number) for m in moments}
        if missing:
            raise SafeZoneGuideError(
                f"no approved reel numbered {sorted(missing)}")

    project_name, _master = resolve_binding(project_folder)
    resolve = connect_resolve()
    project = resolve_project_exactly(resolve.GetProjectManager(),
                                      project_name)
    fps = float(project.GetSetting("timelineFrameRate"))
    results = []
    for moment in moments:
        timeline = timeline_named(project, moment.timeline_name)
        # `AppendToTimeline` writes to the CURRENT timeline: move there
        # under the guard and put the cursor back after
        # (`resolve_lock.cursor_excursion`, inside the verb's lease).
        with cursor_excursion(project, timeline,
                              f"safe-zone guide on {moment.timeline_name}"):
            result = place_on_timeline(project, timeline, project_folder,
                                       overlay, fps)
        print(f"  {result['timeline']}: "
              + (f"guide {overlay!r} on V{result['row']}, clip switched "
                 f"off" if overlay else f"removed {result['removed']} "
                                        f"guide row(s)"),
              file=sys.stderr)
        results.append(result)
    return results
