"""The timeline, as a picture the panel draws itself.

There is no canvas in Resolve's `UIManager`, no chart and no
`ScrollArea`, so nine lanes of proportional time cannot be laid out in
widgets.  The panel therefore encodes a PNG - from `zlib` and `struct`,
in this process, in about twenty lines - and puts it in a rich-text
widget through `<img>`, which is the one image route that actually
draws (`Label.Pixmap = "/path.png"` is accepted and renders nothing:
the silent no-op AGENTS.md section 5 is about).

No Pillow, no matplotlib, no server, no browser.  That is not a stunt -
Resolve launches whatever interpreter it finds, so anything the panel
needs beyond the standard library is a thing that will one day not be
installed on the captain's machine.

What it draws, and what it does NOT claim
-----------------------------------------
Every lane is read off the assembly manifest the last build compiled,
so the picture is the PLAN, and the white line is the LIVE playhead read
out of Resolve.  Those are two different clocks agreeing only while the
timeline on screen is the one that manifest built - so the caller states
which timeline is open, and the strip carries no timecode arithmetic
beyond a plain frame count.  Drop-frame is not handled and the caption
says so.
"""

from __future__ import annotations

import struct
import zlib
from dataclasses import dataclass
from typing import List, Optional, Sequence, Tuple

# The lane colours, in the palette `richtext` already names. Kept here as
# RGB triples because a PNG has no CSS.
LANE_COLOURS = (
    (122, 162, 247),   # V1
    (125, 207, 255),   # V2
    (158, 206, 106),   # A1
    (224, 175, 104),   # A2
    (247, 118, 142),   # A3
    (187, 154, 247),   # spine
    (115, 218, 202),   # subtitles
    (255, 158, 100),   # transitions
)
BACKGROUND = (22, 22, 30)
LANE_BED = (30, 31, 42)
PLAYHEAD = (255, 255, 255)
STATUS_COLOURS = {
    "done": (86, 140, 70),
    "failed": (200, 70, 90),
    "pending": (52, 54, 70),
    "unwired": (40, 41, 54),
}

WIDTH = 880
LANE_HEIGHT = 13
LANE_GAP = 4
TOP = 8


def write_png(path: str, width: int, height: int,
              rows: Sequence[Sequence[int]]) -> str:
    """A PNG encoder in a dozen lines. `rows` is flat RGB byte lists."""
    raw = b"".join(b"\x00" + bytes(row) for row in rows)

    def chunk(tag: bytes, data: bytes) -> bytes:
        body = tag + data
        return (struct.pack(">I", len(data)) + body
                + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF))

    blob = (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR",
                    struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 6))
            + chunk(b"IEND", b""))
    with open(path, "wb") as handle:
        handle.write(blob)
    return path


class Canvas(object):
    """Enough drawing for a timeline strip. Deliberately tiny."""

    def __init__(self, width: int, height: int, background=BACKGROUND):
        self.width, self.height = width, height
        self.pixels = [list(background) * width for _ in range(height)]

    def rect(self, x, y, w, h, rgb) -> None:
        x0, y0 = max(0, int(x)), max(0, int(y))
        x1, y1 = min(self.width, int(x + w)), min(self.height, int(y + h))
        for row_index in range(y0, y1):
            row = self.pixels[row_index]
            for column in range(x0, x1):
                row[column * 3], row[column * 3 + 1], row[column * 3 + 2] = rgb

    def save(self, path: str) -> str:
        return write_png(path, self.width, self.height, self.pixels)


@dataclass
class Lane:
    name: str
    spans: List[Tuple[float, float]]

    @property
    def count(self) -> int:
        return len(self.spans)


def lanes_from_manifest(manifest: dict, spine: Optional[list] = None
                        ) -> List[Lane]:
    """The plan's own lanes, read off the manifest and nothing else."""
    tracks = manifest.get("tracks") or {}
    lanes: List[Lane] = []
    for name in ("V1", "V2", "A1", "A2", "A3"):
        clips = (tracks.get(name) or {}).get("clips") or []
        lanes.append(Lane(name, [(_seconds(c, "timeline_in"),
                                  _seconds(c, "timeline_out"))
                                 for c in clips]))
    blocks = manifest.get("_spine_blocks") or spine or []
    lanes.append(Lane("spine", [(_seconds(b, "timeline_start"),
                                 _seconds(b, "timeline_end"))
                                for b in blocks]))
    subtitles = (manifest.get("subtitles")
                 or (manifest.get("subtitle_overlay") or {}).get("segments")
                 or [])
    lanes.append(Lane("subtitles", [(_seconds(s, "timeline_start",
                                              "timeline_in"),
                                     _seconds(s, "timeline_end",
                                              "timeline_out"))
                                    for s in subtitles]))
    transitions = manifest.get("transitions") or []
    lanes.append(Lane("transitions",
                      [(_seconds(t, "cut_point_timeline"),
                        _seconds(t, "cut_point_timeline") + 0.12)
                       for t in transitions]))
    return lanes


def _seconds(entry: dict, *keys) -> float:
    for key in keys:
        value = entry.get(key)
        if value is not None:
            try:
                return float(value)
            except (TypeError, ValueError):
                return 0.0
    return 0.0


@dataclass
class Strip:
    path: str
    width: int
    height: int
    total_seconds: float
    lanes: List[Lane]
    bytes_written: int
    playhead_seconds: Optional[float] = None


def draw(path: str, lanes: Sequence[Lane], statuses: Sequence[str] = (),
         playhead_seconds: Optional[float] = None,
         width: int = WIDTH) -> Strip:
    """Draw the strip and return what was drawn, so the caller can SAY it."""
    total = 0.0
    for lane in lanes:
        for _start, end in lane.spans:
            total = max(total, end)

    ribbon = 24 if statuses else 0
    height = TOP + len(lanes) * (LANE_HEIGHT + LANE_GAP) + ribbon + 6
    canvas = Canvas(width, height)

    y = TOP
    for index, lane in enumerate(lanes):
        canvas.rect(0, y, width, LANE_HEIGHT, LANE_BED)
        if total > 0:
            for span_index, (start, end) in enumerate(lane.spans):
                x0 = start / total * (width - 2) + 1
                x1 = end / total * (width - 2) + 1
                colour = LANE_COLOURS[index % len(LANE_COLOURS)]
                if span_index % 2:
                    colour = tuple(max(0, int(v * 0.72)) for v in colour)
                canvas.rect(x0, y, max(1.0, x1 - x0 - 0.5), LANE_HEIGHT,
                            colour)
        y += LANE_HEIGHT + LANE_GAP

    if statuses:
        step_width = (width - 2) / float(len(statuses))
        for index, status in enumerate(statuses):
            canvas.rect(1 + index * step_width, y + 4,
                        max(1.0, step_width - 1), 18,
                        STATUS_COLOURS.get(status, STATUS_COLOURS["pending"]))

    if (total > 0 and playhead_seconds is not None
            and 0 <= playhead_seconds <= total):
        canvas.rect(playhead_seconds / total * (width - 2) + 1, 0, 2,
                    TOP + len(lanes) * (LANE_HEIGHT + LANE_GAP), PLAYHEAD)

    canvas.save(path)
    import os

    return Strip(path=path, width=width, height=height, total_seconds=total,
                 lanes=list(lanes),
                 bytes_written=os.path.getsize(path),
                 playhead_seconds=playhead_seconds)


def timecode_seconds(timecode: str, fps: float = 30.0) -> Optional[float]:
    """`00:00:12:07` -> seconds. Drop-frame is NOT handled and the view
    says so: the strip is a picture, not a measurement."""
    if not timecode:
        return None
    parts = str(timecode).replace(";", ":").split(":")
    if len(parts) != 4:
        return None
    try:
        hours, minutes, seconds, frames = (int(p) for p in parts)
    except ValueError:
        return None
    if not fps:
        return None
    return hours * 3600 + minutes * 60 + seconds + frames / float(fps)
