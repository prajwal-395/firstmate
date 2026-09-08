"""Point at a region of the frame and ask the model about it.

The region-granular scout established that the valuable thing generation
models do is REGION SPECIFICATION - name, delineate, track, apply - and
that we own delineate and track through SAM 2.1 while open-vocabulary
naming is the genuine gap. This is the human-driven half of exactly that:
instead of the model naming the region, a person points at it.

One working path, kept small on purpose:

1. **Indicate** - a tile on a 3x3 grid (`B2`) or an explicit normalized
   box (`0.1,0.2,0.4,0.6`). A tile is shorthand for a box; both burn the
   same way. Columns run A-C left to right, rows 1-3 top to bottom, so a
   label reads the same in prose as on the picture.
2. **Burn** - the box is drawn in RED onto a COPY of the frame. Standard
   library only: Resolve launches stock `/usr/bin/python3`, so there is
   no Pillow here - the same reason `panel/strip.py` encodes its PNG from
   `zlib` and `struct`. The source frame is never touched.
3. **Ask** - the marked copy is what the prompt names and what the call
   can read (`frame_attach.reaches_the_file` is the guarantee), and the
   prompt tells the model to answer about what is INSIDE the box and to
   call it "the marked region", so the answer demonstrably refers to what
   was marked rather than the whole frame.

Both readings of the known unknown travel: the box is burned in VISUALLY
(the model sees it) AND passed as coordinates separately (normalized and
pixel, so the model can quote them). A region that names no area is
REFUSED, never shrunk to a point - the same rule that drops a plan entry
naming no effect (AGENTS.md 10.5).

The panel package rule holds here too: nothing in this module imports Qt
or Resolve, and live facts never enter - a source path, a label and a box
are all it takes, so ordinary tests drive it.
"""

from __future__ import annotations

import argparse
import os
import struct
import subprocess
import sys
import zlib
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Sequence, Tuple

from library.tools.panel import strip

GRID_COLS = 3
GRID_ROWS = 3

BOX_COLOR = (255, 0, 0)
"""The burn-in. Red against almost anything, and named in the prompt, so
the model is never guessing which overlay is the region."""

COLUMN_NAMES = "ABCDEFGH"
"""Column labels. The grid is 3 wide, so only ABC are reachable; the rest
exist so a wider grid stays in the same vocabulary."""

MODEL_DEFAULT = "claude-haiku-4-5-20251001"
"""The panel's own default (`resolve_scripts/VEP Pipeline Panel.py`), so a
question asked here and one asked there reach the same model."""

ASK_TIMEOUT_SECONDS = 180


class RegionError(RuntimeError):
    """The region could not be indicated, marked or asked about, with the
    reason to show. Refusals carry the offending value, never a guess."""


# ── 1. Indicate ───────────────────────────────────────────────────────

def tile_labels(cols: int = GRID_COLS, rows: int = GRID_ROWS) -> List[str]:
    """Every tile label, row-major: A1 B1 C1 A2 ... C3."""
    return ["%s%d" % (COLUMN_NAMES[c], r + 1)
            for r in range(rows) for c in range(cols)]


def tile_box(label: str, cols: int = GRID_COLS,
             rows: int = GRID_ROWS) -> Tuple[float, float, float, float]:
    """The tile's normalized box `(x0, y0, x1, y1)`, origin top-left.

    An unknown label is REFUSED with the label in the message - matching
    case-sensitively, so `b2` does not quietly mean `B2`.
    """
    text = (label or "").strip()
    valid = set(tile_labels(cols, rows))
    if text not in valid:
        raise RegionError(
            "Unknown region tile %r - tiles are %s."
            % (label, ", ".join(tile_labels(cols, rows))))
    column = COLUMN_NAMES.index(text[0])
    row = int(text[1:]) - 1
    return (column / cols, row / rows, (column + 1) / cols, (row + 1) / rows)


def parse_box(text: str) -> Tuple[float, float, float, float]:
    """`x0,y0,x1,y1` in normalized coordinates, checked, never repaired."""
    parts = (text or "").split(",")
    try:
        box = tuple(float(part.strip()) for part in parts)
    except ValueError:
        raise RegionError(
            "Unparsable region box %r - want four numbers x0,y0,x1,y1 "
            "in 0..1." % (text,))
    if len(box) != 4:
        raise RegionError(
            "Region box %r has %d numbers - want four: x0,y0,x1,y1."
            % (text, len(box)))
    return check_box(box)


def check_box(box: Sequence[float]) -> Tuple[float, float, float, float]:
    """A normalized box that really is an area, or a refusal saying why."""
    x0, y0, x1, y1 = (float(v) for v in box)
    for value in (x0, y0, x1, y1):
        if not 0.0 <= value <= 1.0:
            raise RegionError(
                "Region box %r leaves the frame - coordinates are "
                "normalized, 0..1." % (tuple(box),))
    if not x0 < x1 and y0 < y1:
        raise RegionError(
            "Region box %r is no area - need x0<x1 and y0<y1, got %s."
            % (tuple(box), "x0>=x1" if x0 >= x1 else "y0>=y1"))
    if not x0 < x1:
        raise RegionError("Region box %r has no width - x0>=x1."
                          % (tuple(box),))
    if not y0 < y1:
        raise RegionError("Region box %r has no height - y0>=y1."
                          % (tuple(box),))
    return (x0, y0, x1, y1)


def box_pixels(box: Sequence[float], width: int,
               height: int) -> Tuple[int, int, int, int]:
    """The normalized box as integer pixels `(x0, y0, x1, y1)`, x1/y1
    exclusive. Rounded to nearest, so a tile edge shared with its
    neighbour lands on the same pixel from both sides; clamped into the
    frame, and widened by one pixel rather than collapsed when rounding
    would erase a sub-pixel box."""
    x0, y0, x1, y1 = check_box(box)
    px0 = min(width - 1, max(0, round(x0 * width)))
    py0 = min(height - 1, max(0, round(y0 * height)))
    px1 = min(width, max(1, round(x1 * width)))
    py1 = min(height, max(1, round(y1 * height)))
    if px1 <= px0:
        px1 = min(width, px0 + 1)
    if py1 <= py0:
        py1 = min(height, py0 + 1)
    return (px0, py0, px1, py1)


def marked_filename(source_name: str, label: str) -> str:
    """`<stem>.marked-<label>.png` - beside the question, the answer names
    the picture it was given, so the marked copy keeps its own name."""
    stem = Path(source_name).stem or "frame"
    safe = "".join(c for c in (label or "box") if c.isalnum()) or "box"
    return "%s.marked-%s.png" % (stem, safe)


# ── 2. Burn ───────────────────────────────────────────────────────────

@dataclass
class Marked:
    """The copy that goes to the model, and the region burned into it."""

    source: str
    path: str
    width: int
    height: int
    box: Tuple[float, float, float, float]
    box_pixels: Tuple[int, int, int, int]
    label: str = ""
    color: Tuple[int, int, int] = BOX_COLOR


def _paeth(a: int, b: int, c: int) -> int:
    proximity = abs(b - c) + abs(a - c) + abs(a + b - 2 * c)
    _ = proximity
    pa, pb, pc = abs(b - c), abs(a - c), abs(a + b - 2 * c)
    if pa <= pb and pa <= pc:
        return a
    return b if pb <= pc else c


def read_pixels(path: str) -> Tuple[int, int, List[List[List[int]]]]:
    """`(width, height, rows)` off a PNG, with the standard library only.

    Reads what `strip.write_png` writes and what `ffmpeg` extracts: 8-bit,
    non-interlaced, RGB or RGBA (alpha is dropped - the mark is opaque).
    Anything else is REFUSED with what was found, never decoded by guess:
    a 16-bit frame silently truncated to 8 would mark a picture the model
    never sees.
    """
    try:
        blob = Path(path).read_bytes()
    except OSError as exc:
        raise RegionError("Cannot read frame %r: %s." % (path, exc))
    if blob[:8] != b"\x89PNG\r\n\x1a\n":
        raise RegionError("Not a PNG: %r." % (path,))
    pos, width, height, depth, color = 8, 0, 0, 0, 0
    idat = bytearray()
    while pos + 8 <= len(blob):
        (length,) = struct.unpack(">I", blob[pos:pos + 4])
        tag = blob[pos + 4:pos + 8]
        data = blob[pos + 8:pos + 8 + length]
        if len(data) != length:
            raise RegionError("Truncated PNG: %r." % (path,))
        if tag == b"IHDR":
            width, height, depth, color, _, _, interlace = struct.unpack(
                ">IIBBBBB", data)
            if depth != 8 or color not in (2, 6) or interlace != 0:
                raise RegionError(
                    "Unreadable PNG %r: 8-bit non-interlaced RGB/RGBA only, "
                    "found bit-depth %d color-type %d interlace %d."
                    % (path, depth, color, interlace))
        elif tag == b"IDAT":
            idat += data
        elif tag == b"IEND":
            break
        pos += 12 + length
    if not width:
        raise RegionError("No IHDR in %r." % (path,))
    try:
        raw = zlib.decompress(bytes(idat))
    except zlib.error as exc:
        raise RegionError("Cannot decompress %r: %s." % (path, exc))

    channels = 3 if color == 2 else 4
    stride = width * channels
    rows: List[List[List[int]]] = []
    previous = bytearray(stride)
    cursor = 0
    for _ in range(height):
        if cursor + 1 + stride > len(raw):
            raise RegionError("Truncated pixels in %r." % (path,))
        kind, cursor = raw[cursor], cursor + 1
        current = bytearray(raw[cursor:cursor + stride])
        cursor += stride
        if kind == 1:  # Sub
            for i in range(channels, stride):
                current[i] = (current[i] + current[i - channels]) & 0xFF
        elif kind == 2:  # Up
            for i in range(stride):
                current[i] = (current[i] + previous[i]) & 0xFF
        elif kind == 3:  # Average
            for i in range(stride):
                left = current[i - channels] if i >= channels else 0
                current[i] = (current[i] + ((left + previous[i]) >> 1)) & 0xFF
        elif kind == 4:  # Paeth
            for i in range(stride):
                left = current[i - channels] if i >= channels else 0
                upper = previous[i]
                upper_left = previous[i - channels] if i >= channels else 0
                current[i] = (current[i]
                              + _paeth(left, upper, upper_left)) & 0xFF
        elif kind != 0:
            raise RegionError("Unknown PNG filter %d in %r."
                              % (kind, path))
        rows.append([[current[i], current[i + 1], current[i + 2]]
                     for i in range(0, stride, channels)])
        previous = current
    return width, height, rows


def mark_frame(source: str, dest: str, box: Sequence[float],
               label: str = "",
               color: Tuple[int, int, int] = BOX_COLOR,
               thickness: Optional[int] = None) -> Marked:
    """Draw the box onto a COPY of the frame and return what was drawn.

    The border is `max(3, min(w, h) // 200)` pixels - visible on a phone
    frame without eating a small tile. The source file is only read.
    """
    checked = check_box(box)
    width, height, rows = read_pixels(source)
    x0, y0, x1, y1 = box_pixels(checked, width, height)
    border = thickness or max(3, min(width, height) // 200)
    red = [color[0], color[1], color[2]]
    for x in range(x0, x1):
        for y in list(range(y0, min(y0 + border, y1))) + \
                list(range(max(y1 - border, y0), y1)):
            if 0 <= y < height:
                rows[y][x] = list(red)
    for y in range(y0, y1):
        for x in list(range(x0, min(x0 + border, x1))) + \
                list(range(max(x1 - border, x0), x1)):
            if 0 <= x < width:
                rows[y][x] = list(red)
    Path(dest).parent.mkdir(parents=True, exist_ok=True)
    strip.write_png(dest, width, height,
                    [[v for pixel in row for v in pixel] for row in rows])
    return Marked(source=source, path=dest, width=width, height=height,
                  box=checked, box_pixels=(x0, y0, x1, y1), label=label,
                  color=color)


# ── 3. Ask ────────────────────────────────────────────────────────────

REGION_HEADING = "THE MARKED REGION - ANSWER ABOUT THIS, NOT THE WHOLE FRAME"


def prompt_lines(marked: Marked) -> List[str]:
    """The region's own section of the prompt: the marked picture to read,
    the box in both coordinate systems, and the instruction that makes the
    answer refer to what was marked."""
    x0, y0, x1, y1 = marked.box
    px0, py0, px1, py1 = marked.box_pixels
    named = ("tile %s" % marked.label) if marked.label else "a free box"
    r, g, b = marked.color
    return [
        REGION_HEADING, "",
        "A PNG of the frame is on this machine at:",
        "  " + marked.path,
        "Read it. A %s box is burned into that copy%s - "
        "rgb(%d, %d, %d), %d px on a %dx%d picture - outlining %s:"
        % ("RED" if (r, g, b) == (255, 0, 0) else "coloured",
           " for %s" % named if marked.label else "",
           r, g, b, max(3, min(marked.width, marked.height) // 200),
           marked.width, marked.height, named),
        "  normalized (origin top-left): "
        "x0=%.3f y0=%.3f x1=%.3f y1=%.3f" % (x0, y0, x1, y1),
        "  pixels (x1/y1 exclusive): (%d, %d)-(%d, %d)"
        % (px0, py0, px1, py1),
        "",
        "Answer about what is INSIDE the box. Say what you see there, and "
        "call it \"the marked region\" so the answer can only be about "
        "what was marked. If the box holds nothing recognisable, say that "
        "rather than describing the rest of the frame.",
        "",
    ]


def attach_to_block(context_block: str, marked: Marked) -> str:
    """The region's section, then the joined context.

    It goes FIRST, for the reason `frame_attach.attach_to_block` puts the
    frame before the measurements: the model latches onto the most
    concrete thing in the prompt, and the marked region is the most
    concrete thing there is. It also puts the picture out of reach of any
    budget that cuts the tail.
    """
    return "\n".join(prompt_lines(marked)) + "\n" + (context_block or "")


def build_prompt(question: str, marked: Marked,
                 context_block: str = "") -> str:
    """The whole prompt one model call gets: region, context, question."""
    prompt = attach_to_block(context_block, marked)
    return (prompt + "\nTheir question: " + (question or "").strip()
            + "\n\nAnswer from what is above. Be concrete. Under 150 words.")


def ask_model(question: str, marked: Marked, context_block: str = "",
              model: str = MODEL_DEFAULT,
              timeout: int = ASK_TIMEOUT_SECONDS) -> dict:
    """Shell out to an already-authenticated `claude` CLI, keylessly.

    The call runs in the directory holding the marked copy, which is the
    same wall `frame_attach.call_site` closes: the CLI will not open a
    file outside its working directory, so without this the model answers
    *I need permission to read the file* and it reads as an unhelpful
    model rather than a permissions bug.
    """
    prompt = build_prompt(question, marked, context_block)
    directory = os.path.dirname(os.path.abspath(marked.path))
    argv = ["claude", "-p", "--model", model]
    try:
        proc = subprocess.run(
            argv, cwd=directory, input=prompt, capture_output=True,
            encoding="utf-8", timeout=timeout, check=False)
        text = (proc.stdout or "").strip() or (proc.stderr or "").strip()
        return {"ok": proc.returncode == 0, "text": text, "model": model,
                "prompt": prompt, "marked": marked.path,
                "box": marked.box, "label": marked.label}
    except Exception as exc:                       # noqa: BLE001 - reported
        return {"ok": False, "text": "%s: %s" % (type(exc).__name__, exc),
                "model": model, "prompt": prompt, "marked": marked.path,
                "box": marked.box, "label": marked.label}


# ── The one working path ──────────────────────────────────────────────

def main(argv: Optional[Sequence[str]] = None) -> int:
    """`python3 -m library.tools.panel.region_mark --frame F --tile B2
    --question "..."` - burn the box, ask about the marked copy. `--no-ask`
    stops after the burn and prints the prompt that would have gone."""
    parser = argparse.ArgumentParser(
        description="Mark a region of a frame and ask the model about it.")
    parser.add_argument("--frame", required=True,
                        help="the source frame (PNG); never modified")
    parser.add_argument("--tile", default="",
                        help="a grid tile, e.g. B2 (columns A-C, rows 1-3)")
    parser.add_argument("--box", default="",
                        help="or a free normalized box x0,y0,x1,y1")
    parser.add_argument("--question", default="What is in the marked region?",
                        help="what to ask about the inside of the box")
    parser.add_argument("--context-file", default="",
                        help="optional text prepended after the region block")
    parser.add_argument("--dest-dir", default="",
                        help="where the marked copy goes "
                             "(default: beside the source frame)")
    parser.add_argument("--model", default=MODEL_DEFAULT)
    parser.add_argument("--no-ask", action="store_true",
                        help="burn and print the prompt, call no model")
    args = parser.parse_args(list(argv) if argv is not None else None)

    if bool(args.tile) == bool(args.box):
        parser.error("give exactly one of --tile and --box")
    try:
        box = tile_box(args.tile) if args.tile else parse_box(args.box)
        label = args.tile.strip() if args.tile else ""
        dest_dir = args.dest_dir or str(Path(args.frame).parent)
        dest = os.path.join(dest_dir,
                            marked_filename(Path(args.frame).name, label))
        marked = mark_frame(args.frame, dest, box, label=label)
        context = ""
        if args.context_file:
            context = Path(args.context_file).read_text(encoding="utf-8")
        prompt = build_prompt(args.question, marked, context)
    except RegionError as exc:
        print("region_mark: %s" % exc, file=sys.stderr)
        return 1
    print("marked: %s" % marked.path)
    if args.no_ask:
        print(prompt)
        return 0
    result = ask_model(args.question, marked, context, model=args.model)
    print(result["text"])
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
