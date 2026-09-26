"""Narrow a built reel's captions to the width every platform leaves clear.

The captain, 2026-09-25, on the post-header reels: yes to narrowing the
captions so the longest lines stop running under the apps' action rails,
on the reels he has already accepted - which rules out a rebuild (his
2026-09-24 ruling).

So a narrowing is a `touch-reel` change. Only a card whose own words wrap
wider than the new width is re-rendered - at that width, through the
caption step's own renderer (`step_4_05_render_subtitles.rerender_and_swap`,
render half only) - and swapped in place (`swap_pixels`), journaled, so
`ren undo` reverses it. A card that already fits is left alone.

Why the swap carries the placed item's own transform: a card is centred
(Pan 0 at any canvas width) and hangs from the bottom of a canvas whose
HEIGHT the narrowing does not change (`tight_box.constant_caption_box`),
so the Tilt that placed the old card - the captain's own adjustments
included - places the new one on the same row. The narrower wrap only
adds lines, which grow the card upward.

Where the width comes from, unless the caller states one: the widest box
centred on the frame that clears every zone of the PROJECT's safe-zone
policy over the rows the reel's captions occupy
(`safe_zone_policy.SafeLayout.centred_clear_width`) - the same width a
rebuild wraps at there, so a narrowed card is the canvas a rebuild would
draw. The wrap bounds the ink: on the geo-podcast finals (2026-09-25) no
card's ink ran past its 840px wrap. Captions that sit INSIDE a zone
vertically (the band has no clear centred width at all) are refused by
name: that is a row to move (`ren shift-rows`), not a width to find.
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable

#: The row the build names the captions' track.
CAPTION_ROW = "Subtitles"


class CaptionWidthError(ValueError):
    """A narrowing that cannot be stated or carried out - RAISED."""


def _props(source_file: str) -> dict:
    """The tight render props written beside a caption card."""
    stem = source_file.removesuffix(".mov")
    try:
        with open(stem + "_props.json", encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, ValueError) as exc:
        raise CaptionWidthError(
            f"caption card {os.path.basename(source_file)!r} has no readable "
            f"props beside it ({exc}); it cannot be re-rendered as it was "
            f"drawn") from None


def caption_band(cards, frame: tuple[int, int], draw_gain: float,
                 project_folder: str = "",
                 wrap: float | None = None) -> tuple[int, int]:
    """The rows (y0, y1) a reel's placed caption cards draw on, at their
    own wrap or at ``wrap``. ``cards`` is ``[(clip, props), ...]``."""
    from library.tools.tight_box import (
        PAD_BOTTOM,
        canvas_screen_origin,
        card_union,
    )

    top, bottom = frame[1], 0
    for clip, props in cards:
        style = props["style"]
        width = float(wrap or style["captionMaxWidth"])
        _w, height = card_union(style, props.get("subtitles") or [], width,
                                project_folder)
        transform = clip.get("transform") or {}
        _x, origin_y = canvas_screen_origin(
            props["width"], props["height"],
            {"pan": transform.get("Pan") or 0.0,
             "tilt": transform.get("Tilt") or 0.0},
            frame[0], frame[1], draw_gain)
        card_bottom = origin_y + props["height"] - PAD_BOTTOM
        top = min(top, int(card_bottom - height))
        bottom = max(bottom, round(card_bottom))
    return top, bottom


def clear_wrap(layout, band: tuple[int, int]) -> int:
    """The wrap width that keeps a centred card clear of every zone of
    ``layout`` over ``band``; refused, naming the zone, where there is
    none."""
    width, bound = layout.centred_clear_width(band[0], band[1])
    if width <= 0:
        where = (f"{bound['platform']} {bound['band']} ({bound['ui']})"
                 if bound else "a zone")
        raise CaptionWidthError(
            f"the captions draw on rows {band[0]}..{band[1]}, inside "
            f"{where}, which covers the centre of the frame: no width "
            f"clears it. Move the captions out of it first (`ren "
            f"shift-rows`), then narrow them")
    return width


def touch_spec(project_folder: str, reel_number: int, tracks, *,
               timeline_label: str, draw_gain: float,
               max_width: int | None = None,
               frame: tuple[int, int] = (1080, 1920),
               render: Callable | None = None) -> dict:
    """The `touch-reel` spec that narrows one reel's captions.

    ``render(project_folder, pairs, draw_gain) -> {old file: new file}``
    draws the cards (the caption step's own renderer by default).
    """
    rows = [t for t in tracks or ()
            if str(t.get("type", "")).lower().startswith("v")
            and str(t.get("name") or "") == CAPTION_ROW]
    if not rows:
        raise CaptionWidthError(f"reel {reel_number} has no "
                                f"{CAPTION_ROW!r} row")
    row = rows[0]
    cards = [(clip, _props(str(clip.get("source_file") or "")))
             for clip in row.get("clips") or ()]
    if max_width is None:
        from library.tools.safe_zone_policy import project_layout

        layout = project_layout(project_folder, frame)
        band = caption_band(cards, frame, draw_gain, project_folder)
        max_width = clear_wrap(layout, band)
        # The narrower wrap adds lines, which lift the band: the rows
        # it grows into must be clear at that width too.
        grown = caption_band(cards, frame, draw_gain, project_folder,
                             wrap=max_width)
        max_width = min(max_width, clear_wrap(layout, grown))

    from library.tools.tight_box import card_union

    pairs, positions = [], []
    for index, (clip, props) in enumerate(cards):
        style = props["style"]
        width, _h = card_union(style, props.get("subtitles") or [],
                               float(style["captionMaxWidth"]),
                               project_folder)
        if width <= max_width:
            continue
        # Re-rendered through the caption step, which takes FULL-frame
        # props and cuts them to the constant canvas. What the card draws
        # depends only on the wrap and that canvas (its height, and the
        # pads the cut sets), never on where the full-frame safe area
        # would have placed it - the swap carries the placed item's own
        # transform - so the tight style rides through as it is.
        full = dict(props)
        full["width"], full["height"] = frame
        full["style"] = dict(style, captionMaxWidth=int(max_width))
        pairs.append({"old_mov": str(clip["source_file"]),
                      "timeline_label": timeline_label, "props": full})
        positions.append(index)
    edits = []
    if pairs:
        mapping = (render or _render)(project_folder, pairs, draw_gain)
        for index, pair in zip(positions, pairs):
            new = mapping.get(pair["old_mov"])
            if not new:
                raise CaptionWidthError(
                    f"reel {reel_number}: the card "
                    f"{os.path.basename(pair['old_mov'])!r} did not "
                    f"render at {max_width}px")
            edits.append({"op": "swap_pixels",
                          "row": f"V{int(row['index'])}", "item": index,
                          "media": new})
    return {"reel": int(reel_number), "edits": edits,
            "max_width": int(max_width), "narrowed": len(edits),
            "cards": len(cards)}


def _render(project_folder: str, pairs: list, draw_gain: float) -> dict:
    from library.tools import operations

    # Through the operation registry, which puts the step's own
    # directory on the path its sibling imports need.
    rerender_and_swap = operations.get("subtitles.rerender_swap").run
    report = rerender_and_swap(project_folder, pairs, swap=False,
                               draw_gain=draw_gain)
    if not report.get("ok"):
        raise CaptionWidthError(report.get("error")
                                or "the caption re-render failed")
    return dict(report.get("map") or {})
