"""Point at a region of the frame and ask the model about it.

The region-granular scout (`data/vep-region-granular-editing/report.md` in
the firstmate home) established that the valuable thing is REGION
SPECIFICATION - name, delineate, track, apply - and that we own delineate
and track through SAM 2.1 while open-vocabulary naming is the genuine gap.
This is the human-driven half of exactly that: instead of the model naming
the region, a person points at it.

One working path, kept small on purpose:

1. **Indicate** - a tile on a 3x3 grid (`B2`) or an explicit normalized
   box (`--box 0.1,0.2,0.4,0.6`). A tile is shorthand for a box; both burn
   the same way.
2. **Burn** - the box is drawn onto a COPY of the frame in RED, with the
   standard library only (Resolve launches stock `/usr/bin/python3`, so
   no Pillow - same reason `panel/strip.py` encodes its PNG from `zlib`
   and `struct`). The source frame is never touched.
3. **Ask** - the marked copy is what the prompt names and what the call
   can read (`frame_attach.reaches_the_file`), and the prompt instructs
   the model to answer about what is INSIDE the box.

Both halves of the known unknown are carried: the box is burned in
VISUALLY (the model sees it) AND passed as coordinates separately (the
model can quote it). The answer is demonstrably about the marked region
when it refers to the marked region rather than the whole frame.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from library.tools.panel import frame_attach, region_mark, strip

PACKAGE = Path(__file__).resolve().parent.parent / "library" / "tools" / "panel"


def _fixture(path: Path, width: int = 90, height: int = 60) -> Path:
    """A frame with a recognisable inside and outside, via our own encoder.

    Left third blue, middle third green, right third blue again, so tile
    B2 (the centre tile) sits on green with blue on both sides - a border
    drawn in red is then unambiguous against either.
    """
    rows = []
    for _ in range(height):
        row = []
        for x in range(width):
            third = x * 3 // width
            row += [0, 0, 255] if third != 1 else [0, 255, 0]
        rows.append(row)
    strip.write_png(str(path), width, height, rows)
    return path


# ── 1. The grid ───────────────────────────────────────────────────────

def test_the_nine_tiles_cover_the_frame_exactly_once():
    labels = region_mark.tile_labels()
    assert len(set(labels)) == 9
    assert labels[0] == "A1"      # top-left, spreadsheet order
    assert labels[-1] == "C3"     # bottom-right
    total = sum(
        (box[2] - box[0]) * (box[3] - box[1])
        for box in (region_mark.tile_box(label) for label in labels))
    assert total == pytest.approx(1.0)
    for label in labels:
        box = region_mark.tile_box(label)
        assert (box[2] - box[0]) == pytest.approx(1 / 3)
        assert (box[3] - box[1]) == pytest.approx(1 / 3)


def test_an_unknown_tile_is_refused_by_name():
    for bad in ("D4", "B0", "A4", "", "b2", "middle"):
        with pytest.raises(region_mark.RegionError) as excinfo:
            region_mark.tile_box(bad)
        assert bad in str(excinfo.value)


def test_a_degenerate_box_is_refused_not_completed():
    """A plan entry naming no effect is dropped (AGENTS.md 10.5); a region
    naming no area is refused the same way - never shrunk to a point or
    silently reordered into one."""
    for bad in ("0.5,0.5,0.5,0.5",      # zero area
                "0.6,0.2,0.4,0.6",      # x reversed
                "0.1,0.8,0.4,0.6",      # y reversed
                "-0.1,0.2,0.4,0.6",     # out of range
                "0.1,0.2,1.4,0.6",      # out of range
                "a,b,c,d",              # not numbers
                "0.1,0.2,0.4"):         # not four numbers
        with pytest.raises(region_mark.RegionError):
            region_mark.parse_box(bad)


# ── 2. The burn ───────────────────────────────────────────────────────

def test_the_box_is_burned_and_the_inside_survives(tmp_path):
    source = _fixture(tmp_path / "frame.png")
    dest = tmp_path / "frame.marked-B2.png"
    marked = region_mark.mark_frame(
        str(source), str(dest), region_mark.tile_box("B2"), label="B2")

    assert Path(marked.path).is_file()
    assert (marked.width, marked.height) == (90, 60)
    # The centre tile of a 90x60 frame: x 30..60, y 20..40.
    assert marked.box_pixels == (30, 20, 60, 40)

    pixels = region_mark.read_pixels(str(dest))[2]
    red = [255, 0, 0]
    assert pixels[20][30][:3] == red       # top-left corner of the border
    assert pixels[39][59][:3] == red       # bottom-right corner
    assert pixels[30][45] == [0, 255, 0]   # deep inside: untouched green
    assert pixels[10][10] == [0, 0, 255]   # far outside: untouched blue
    assert pixels[30][10] == [0, 0, 255]   # same row, left of the box


def test_a_free_box_burns_the_same_way_as_a_tile(tmp_path):
    source = _fixture(tmp_path / "frame.png")
    marked = region_mark.mark_frame(
        str(source), str(tmp_path / "free.png"),
        region_mark.parse_box("0.333333,0.333333,0.666667,0.666667"))
    assert marked.box_pixels == (30, 20, 60, 40)


def test_what_cannot_be_read_is_refused_not_guessed(tmp_path):
    garbage = tmp_path / "not-a-frame.png"
    garbage.write_bytes(b"this is not a png")
    with pytest.raises(region_mark.RegionError):
        region_mark.mark_frame(str(garbage), str(tmp_path / "out.png"),
                               region_mark.tile_box("A1"))
    with pytest.raises(region_mark.RegionError):
        region_mark.mark_frame(str(tmp_path / "missing.png"),
                               str(tmp_path / "out.png"),
                               region_mark.tile_box("A1"))


# ── 3. The ask ────────────────────────────────────────────────────────

def test_the_prompt_names_the_marked_frame_and_the_region(tmp_path):
    source = _fixture(tmp_path / "frame.png")
    marked = region_mark.mark_frame(
        str(source), str(tmp_path / "frame.marked-B2.png"),
        region_mark.tile_box("B2"), label="B2")
    lines = region_mark.prompt_lines(marked)
    text = "\n".join(lines)
    assert marked.path in text
    assert "B2" in text
    assert "0.333" in text                       # the normalized box travels
    assert "30, 20" in text or "30,20" in text   # and the pixel box too
    assert "INSIDE" in text.upper()


def test_the_region_comes_first_because_it_is_the_question(tmp_path):
    """`frame_attach` puts the frame before the context for the same
    reason: the model latches onto the most concrete thing, and the marked
    region is the most concrete thing there is."""
    source = _fixture(tmp_path / "frame.png")
    marked = region_mark.mark_frame(
        str(source), str(tmp_path / "frame.marked-C1.png"),
        region_mark.tile_box("C1"), label="C1")
    block = region_mark.attach_to_block("pipeline context here", marked)
    assert block.index("C1") < block.index("pipeline context here")


def test_the_marked_frame_reaches_the_model_call(tmp_path):
    """The same wall `test_panel_frame_attach` guards: the CLI will not
    open a file outside its working directory, so the call the CLI makes
    has to reach the marked copy - not the source frame."""
    source = _fixture(tmp_path / "frame.png")
    marked = region_mark.mark_frame(
        str(source), str(tmp_path / "frame.marked-A3.png"),
        region_mark.tile_box("A3"), label="A3")
    site = frame_attach.call_site(marked.path, str(tmp_path))
    assert frame_attach.reaches_the_file(
        marked.path, site.cwd, ["claude", "-p"] + list(site.extra_argv))


def test_the_dry_run_writes_the_frame_and_the_prompt_but_calls_nothing(
        tmp_path, capsys):
    source = _fixture(tmp_path / "frame.png")
    dest = tmp_path / "out"
    code = region_mark.main(["--frame", str(source), "--tile", "B2",
                             "--question", "what sits in the marked region?",
                             "--dest-dir", str(dest), "--no-ask"])
    assert code == 0
    out = capsys.readouterr().out
    assert "B2" in out
    assert "what sits in the marked region?" in out
    assert len(list(dest.glob("*.png"))) == 1


# ── 4. The Resolve constraint ─────────────────────────────────────────

def test_the_new_module_needs_nothing_resolve_does_not_have():
    """Resolve launches whatever interpreter it finds, so the panel half
    is the standard library plus the panel package itself - no Pillow, no
    requests, no Resolve proxy. Pinned the way `strip.py` is pinned."""
    source = (PACKAGE / "region_mark.py").read_text(encoding="utf-8")
    imported = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            imported.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    allowed = {"__future__", "argparse", "os", "struct", "subprocess",
               "sys", "zlib", "dataclasses", "typing", "pathlib",
               "library"}
    assert imported <= allowed, imported - allowed
