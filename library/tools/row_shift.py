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
from functools import lru_cache


class RowShiftError(ValueError):
    """A move that cannot be stated as written - RAISED."""


@lru_cache(maxsize=None)
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
