"""Move whole rows of a built reel up or down, by delivery pixels.

The captain, 2026-09-25, on the post-header reels: keep the header where
it sits inside the platforms' safe zone, "move the video itself down and
keep the subtitles above the bottom of the safe zone". That is a move of
named rows by a number of pixels - the picture rows, their frame and
titles down, the header down, the captions up - on reels he has already
accepted, which rules out a rebuild (his 2026-09-24 ruling).

So a move is a `touch-reel` change: one `set_properties` Tilt per item,
journaled, so `ren undo` reverses it. The pixels are converted to Tilt
units by `resolve_transform.units_for_shift` from each item's OWN media
size - a picture clip is drawn through its fit scale, an overlay at
native size, so one pixel count is a different Tilt on each - never by
treating a unit as a pixel. `draw_gain` is what the renderer really
draws per unit on these timelines, and the caller states the one it
MEASURED; this module has no default for it.
"""

from __future__ import annotations

import json
import subprocess
from collections.abc import Callable, Mapping, Sequence
from functools import cache


class RowShiftError(ValueError):
    """A move that cannot be stated as written - RAISED."""


@cache
def media_size(path: str) -> tuple[int, int]:
    """(width, height) of a clip's media, off the file."""
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=width,height", "-of", "json", path],
        capture_output=True, encoding="utf-8", check=False)
    try:
        stream = json.loads(result.stdout)["streams"][0]
        return int(stream["width"]), int(stream["height"])
    except (ValueError, KeyError, IndexError):
        raise RowShiftError(f"cannot read the size of {path!r}") from None


def parse_moves(specs: Sequence[str]) -> dict[str, int]:
    """`["Akshita,Craig=220", "Subtitles=-26"]` -> `{row name: dy}`."""
    moves: dict[str, int] = {}
    for spec in specs or ():
        rows, sep, value = str(spec).rpartition("=")
        if not sep or not rows.strip():
            raise RowShiftError(f"--move {spec!r} is not ROW[,ROW]=PIXELS")
        try:
            dy = int(value)
        except ValueError:
            raise RowShiftError(f"--move {spec!r}: {value!r} is not whole "
                                f"pixels") from None
        for row in rows.split(","):
            moves[row.strip()] = dy
    return moves


def shift_spec(tracks, reel: int, moves: Mapping[str, int], *,
               frame: tuple[int, int], draw_gain: float,
               skip_prefixes: Sequence[str] = (),
               size_of: Callable[[str], tuple[int, int]] = media_size
               ) -> dict:
    """The `touch-reel` spec that moves the named rows by `dy` pixels.

    Positive `dy` is DOWN, which is a negative Tilt. An item whose name
    starts with one of `skip_prefixes` stays where it is (a full-frame
    card sharing a row with graphics that move). A row the reel does
    not carry is refused by name, never skipped silently.
    """
    from library.tools.resolve_transform import fit_base_scale, units_for_shift

    frame_w, frame_h = frame
    named = {str(t.get("name") or ""): t for t in tracks
             if str(t.get("type", "")).lower().startswith("v")}
    missing = sorted(set(moves) - set(named))
    if missing:
        raise RowShiftError(f"reel {reel} carries no row {missing}")
    edits = []
    for row, dy in moves.items():
        track = named[row]
        for index, clip in enumerate(track.get("clips") or ()):
            if any(str(clip.get("name") or "").startswith(p)
                   for p in skip_prefixes):
                continue
            width, height = size_of(str(clip.get("source_file") or ""))
            base = (1.0 if (width, height) == (frame_w, frame_h)
                    or width <= frame_w and height <= frame_h
                    else fit_base_scale(width, height, frame_w, frame_h))
            tilt = float((clip.get("transform") or {}).get("Tilt") or 0.0)
            delta = units_for_shift(abs(dy), height, frame_h,
                                    base_scale=base, draw_gain=draw_gain)
            new = tilt - delta if dy > 0 else tilt + delta
            edits.append({"op": "set_properties",
                          "row": f"V{int(track['index'])}", "item": index,
                          "properties": {"Tilt": round(new, 4)}})
    if not edits:
        raise RowShiftError(f"reel {reel}: nothing on {sorted(moves)} to move")
    return {"reel": int(reel), "edits": edits}


def scale_spec(tracks, reel: int, scale_rows: Sequence[str], factor: float,
               *, move_rows: Sequence[str] = (), anchor: tuple[float, float],
               frame: tuple[int, int], draw_gain: float,
               skip_prefixes: Sequence[str] = (),
               size_of: Callable[[str], tuple[int, int]] = media_size
               ) -> dict:
    """The `touch-reel` spec that shrinks (or grows) named rows by
    ``factor`` about ``anchor``, a point in delivery pixels.

    The captain, 2026-09-25: the picture and its TV frame may need to
    shrink "so that the short form platforms don't cut off the edges of
    our videos". Scaling a picture about a point is two things per
    item: its zoom times ``factor``, and its drawn centre pulled toward
    the anchor by ``factor`` - so a speaker aimed at the TV window's
    centre stays aimed there, at the smaller size. ``move_rows`` get the
    second half only: graphics riding on the picture (a speaker title)
    stay the size they are and keep their place ON the picture. Centres
    are converted by the one Pan/Tilt law from each item's own media
    size (`resolve_transform`); the Pan law does not move under a zoom.
    A row the reel does not carry is refused by name, and so is an item
    whose zoom Resolve did not report - a zoom times a guess is not a
    shrink.
    """
    from library.tools.resolve_transform import (
        fit_base_scale,
        shift_px,
        units_for_shift,
    )

    if not 0 < float(factor) <= 4:
        raise RowShiftError(f"scale factor {factor!r} is not a scale")
    frame_w, frame_h = frame
    named = {str(t.get("name") or ""): t for t in tracks
             if str(t.get("type", "")).lower().startswith("v")}
    scale_rows = picture_rows(tracks) if list(scale_rows) == [PICTURE] \
        else list(scale_rows)
    missing = sorted(set(scale_rows) - set(named))
    if missing:
        raise RowShiftError(f"reel {reel} carries no row {missing}")
    # A reel that carries no row riding on the picture has nothing to
    # move with it; that is said on the spec, not refused.
    absent = sorted(set(move_rows) - set(named))
    wanted = list(scale_rows) + [r for r in move_rows
                                 if r in named and r not in scale_rows]
    ax, ay = anchor[0] - frame_w / 2, anchor[1] - frame_h / 2
    edits = []
    for row in wanted:
        track = named[row]
        zooms = row in scale_rows
        for index, clip in enumerate(track.get("clips") or ()):
            if any(str(clip.get("name") or "").startswith(p)
                   for p in skip_prefixes):
                continue
            width, height = size_of(str(clip.get("source_file") or ""))
            base = (1.0 if width <= frame_w and height <= frame_h
                    else fit_base_scale(width, height, frame_w, frame_h))
            transform = clip.get("transform") or {}
            pan = float(transform.get("Pan") or 0.0)
            tilt = float(transform.get("Tilt") or 0.0)
            dx = shift_px(abs(pan), width, frame_w, base, draw_gain)
            dx = dx if pan >= 0 else -dx
            dy = shift_px(abs(tilt), height, frame_h, base, draw_gain)
            dy = -dy if tilt >= 0 else dy  # positive Tilt is UP
            nx, ny = ax + factor * (dx - ax), ay + factor * (dy - ay)
            new_pan = units_for_shift(abs(nx), width, frame_w, base,
                                      draw_gain)
            new_tilt = units_for_shift(abs(ny), height, frame_h, base,
                                       draw_gain)
            properties = {"Pan": round(new_pan if nx >= 0 else -new_pan, 4),
                          "Tilt": round(-new_tilt if ny >= 0 else new_tilt,
                                        4)}
            if zooms:
                if transform.get("ZoomX") is None or \
                        transform.get("ZoomY") is None:
                    raise RowShiftError(
                        f"reel {reel}: {row}[{index}] "
                        f"{clip.get('name')!r} reports no zoom, so it "
                        f"cannot be scaled")
                properties["ZoomX"] = round(
                    float(transform["ZoomX"]) * factor, 5)
                properties["ZoomY"] = round(
                    float(transform["ZoomY"]) * factor, 5)
            edits.append({"op": "set_properties",
                          "row": f"V{int(track['index'])}", "item": index,
                          "properties": properties})
    if not edits:
        raise RowShiftError(f"reel {reel}: nothing on {wanted} to scale")
    return {"reel": int(reel), "edits": edits, "rows": wanted,
            "absent": absent}


#: The token naming a reel's picture: every row BELOW its TV ``Frame``
#: row - the camera angles the build lays under the frame, whatever each
#: reel names them. The frame itself is not zoomed: a smaller TV is drawn
#: inside its overlay (`reel_look.fit_picture_spec`).
PICTURE = "@picture"


def picture_rows(tracks) -> list[str]:
    """The names of the rows below the ``Frame`` row."""
    from library.tools.timeline_layout import FRAME_NAME as frame_name

    video = sorted((t for t in tracks
                    if str(t.get("type", "")).lower().startswith("v")),
                   key=lambda t: int(t["index"]))
    frame = next((t for t in video if t.get("name") == frame_name), None)
    if frame is None:
        raise RowShiftError(f"no {frame_name!r} row: this reel wears no TV "
                            f"frame, so {PICTURE} names nothing")
    return [str(t["name"]) for t in video
            if int(t["index"]) < int(frame["index"])]
