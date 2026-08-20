"""P3.3: typography must not be a race with the network.

`remotion-subtitles/src/index.css` imported Montserrat and Inter from
`fonts.googleapis.com` with no `delayRender` behind them, so whether the
correct font was in place when Remotion started rasterising depended on
the network. On a miss the captions rendered in Chromium's fallback sans
at a different width, which changes line breaking as well as the
letterforms - and nothing downstream could tell, because the frames were
still valid PNGs of the right size. (The Inter import was inert on top of
that: nothing in `src/` ever asked for Inter.)

Montserrat now ships as a single variable file covering the whole 100-900
axis, and `src/fonts.ts` blocks the render until the face is usable.
"""
import json
import os
import re
import struct
import sys

import pytest
import yaml

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from library.tools.subtitle_style import SUBTITLE_STYLES

REMOTION_SRC = os.path.join(PROJECT_ROOT, "remotion-subtitles", "src")
FONT_DIR = os.path.join(PROJECT_ROOT, "remotion-subtitles", "public", "fonts")
FONT_FILE = os.path.join(FONT_DIR, "Montserrat-Variable.ttf")
LICENCE_FILE = os.path.join(FONT_DIR, "OFL-Montserrat.txt")

# Fonts a template may name that this repo does NOT bundle. Each is a
# system font, so its exact shape depends on the render machine. Adding to
# this list is a decision to accept that; adding a font to
# `public/fonts/` and loading it in `src/fonts.ts` is the alternative.
ACCEPTED_SYSTEM_FONTS = {
    "Helvetica": (
        "default_brand.yaml names it. Present on macOS, absent on most "
        "Linux render hosts, where Chromium will substitute. Accepted "
        "because default_brand is the fallback template rather than a "
        "shipped look, but it is not deterministic."
    ),
}


def _src_files():
    for root, _dirs, files in os.walk(REMOTION_SRC):
        for name in files:
            if name.endswith((".ts", ".tsx", ".css")):
                yield os.path.join(root, name)


# ─────────────────────────────────────────────────────────
# No fonts over the network at render time
# ─────────────────────────────────────────────────────────

def test_no_font_is_imported_over_http():
    offenders = []
    for path in _src_files():
        with open(path, encoding="utf-8") as f:
            src = f.read()
        # Comments explain the old bug on purpose; only real rules count.
        code = re.sub(r"/\*[\s\S]*?\*/", "", src)
        code = "\n".join(
            l for l in code.splitlines() if not l.strip().startswith("//"))
        if "fonts.googleapis.com" in code or "fonts.gstatic.com" in code:
            offenders.append(os.path.relpath(path, PROJECT_ROOT))
    assert not offenders, (
        f"these files fetch fonts over HTTP at render time: {offenders}. "
        f"Bundle the font in public/fonts/ and load it via src/fonts.ts, "
        f"or typography is a race with the network.")


# ─────────────────────────────────────────────────────────
# The bundled asset, and its licence
# ─────────────────────────────────────────────────────────

def test_the_font_ships():
    assert os.path.exists(FONT_FILE), (
        "the bundled font is missing; every caption render depends on it")
    assert os.path.getsize(FONT_FILE) > 100_000, "font file is implausibly small"


def test_the_licence_ships_beside_the_font():
    """AGENTS.md section 11: an outside asset needs its licence recorded.

    This is the rule the unlicensed PowerGrade broke. Montserrat is SIL
    OFL 1.1, which permits redistribution including commercially.
    """
    assert os.path.exists(LICENCE_FILE), (
        "the font ships without its licence text")
    with open(LICENCE_FILE, encoding="utf-8") as f:
        text = f.read()
    assert "SIL Open Font License" in text
    assert len(text) > 1000


def test_agents_md_records_the_font_licence():
    with open(os.path.join(PROJECT_ROOT, "AGENTS.md"), encoding="utf-8") as f:
        agents = f.read()
    # AGENTS.md is hard-wrapped, so a phrase can straddle a line break.
    # Match on the prose, not on where the wrapping happened to fall.
    flat = re.sub(r"[\s*]+", " ", agents)
    assert "Montserrat" in flat and "Open Font License" in flat, (
        "AGENTS.md section 11 must record the licence of any third-party "
        "asset that ships in this repository")


def _weight_axis():
    """The (min, max) of the font's `wght` axis, read from its fvar table."""
    with open(FONT_FILE, "rb") as f:
        data = f.read()
    num_tables = struct.unpack(">H", data[4:6])[0]
    tables = {}
    for i in range(num_tables):
        off = 12 + 16 * i
        tag = data[off:off + 4].decode("latin-1")
        offset, length = struct.unpack(">II", data[off + 8:off + 16])
        tables[tag] = (offset, length)
    assert "fvar" in tables, "not a variable font"
    o = tables["fvar"][0]
    axes_off = o + struct.unpack(">H", data[o + 4:o + 6])[0]
    count = struct.unpack(">H", data[o + 8:o + 10])[0]
    size = struct.unpack(">H", data[o + 10:o + 12])[0]
    for i in range(count):
        a = axes_off + i * size
        if data[a:a + 4].decode("latin-1") == "wght":
            lo, _default, hi = struct.unpack(">iii", data[a + 4:a + 16])
            return lo / 65536.0, hi / 65536.0
    pytest.fail("font has no wght axis")


def test_the_font_covers_every_weight_a_style_can_ask_for():
    """The asset and the enumeration must agree.

    A style asking for 900 against a font that stops at 700 gets a
    synthesised bold, which is not the same shape - and is invisible in
    the output.
    """
    lo, hi = _weight_axis()
    for name, style in SUBTITLE_STYLES.items():
        assert lo <= style.font_weight <= hi, (
            f"style {name!r} wants weight {style.font_weight}, outside the "
            f"bundled font's {lo:.0f}-{hi:.0f} axis")


# ─────────────────────────────────────────────────────────
# The loader blocks the render
# ─────────────────────────────────────────────────────────

def _fonts_ts():
    with open(os.path.join(REMOTION_SRC, "fonts.ts"), encoding="utf-8") as f:
        return f.read()


def test_the_loader_blocks_the_render():
    src = _fonts_ts()
    assert "delayRender(" in src, (
        "without delayRender the font load races the first frame, which is "
        "the entire bug")
    assert "continueRender(" in src


def test_the_loader_declares_the_variable_axis():
    src = _fonts_ts()
    assert 'weight: "100 900"' in src, (
        "a variable font registered without its axis makes Chromium "
        "synthesise bold instead of using the real weight")


def test_a_missing_font_is_a_loud_failure():
    src = _fonts_ts()
    assert "throw new Error(" in src, (
        "a font that fails to load must raise; falling back to Chromium's "
        "default sans is invisible in the output")


def test_both_rendered_compositions_load_the_font():
    """The pipeline renders exactly two compositions; both draw text."""
    for rel in ("compositions/SubtitleOverlay/index.tsx",
                "compositions/MotionGraphics/index.tsx"):
        with open(os.path.join(REMOTION_SRC, rel), encoding="utf-8") as f:
            src = f.read()
        assert "loadBundledFonts" in src, f"{rel} does not load the font"


# ─────────────────────────────────────────────────────────
# Templates must not name a font nobody bundles
# ─────────────────────────────────────────────────────────

def _font_names(node, path=""):
    """Yield (dotted path, family) for every font-ish key in a template.

    Walks the whole document rather than one known location.  It used to
    read `style.typography.font` alone, and the now-deleted `fourth_wall.yaml` shipped
    `effect.timed_text_overlay.font_family: "\'Nanum Pen Script\', cursive"`
    straight past it - an unbundled Google font, in the one test whose
    job is to stop exactly that.  A guard that inspects one key is a
    guard against one key.
    """
    if isinstance(node, dict):
        for key, value in node.items():
            here = f"{path}.{key}" if path else key
            if key in ("font", "font_family", "fontFamily") and isinstance(
                    value, str) and value.strip():
                yield here, value
            else:
                yield from _font_names(value, here)
    elif isinstance(node, list):
        for i, value in enumerate(node):
            yield from _font_names(value, f"{path}[{i}]")


# CSS font stacks name fallbacks after the first family; only the first is
# the font anyone chose, and it is the one that must be deliverable.
def _primary_family(declared: str) -> str:
    return declared.split(",")[0].strip().strip("'\"")


def test_templates_name_bundled_or_explicitly_accepted_fonts():
    """A new unbundled font must not arrive silently.

    It renders fine on the machine that added it and substitutes on every
    other one, which is the same class of bug as the webfont race.
    """
    from library.tools.subtitle_style import LEGACY_FONT_FAMILY
    bundled = {LEGACY_FONT_FAMILY}
    template_dir = os.path.join(PROJECT_ROOT, "library", "templates")
    unknown = []
    for name in sorted(os.listdir(template_dir)):
        if not name.endswith(".yaml"):
            continue
        with open(os.path.join(template_dir, name), encoding="utf-8") as f:
            tmpl = yaml.safe_load(f) or {}
        for where, declared in _font_names(tmpl):
            font = _primary_family(declared)
            if font not in bundled and font not in ACCEPTED_SYSTEM_FONTS:
                unknown.append((name, where, font))
    assert not unknown, (
        f"templates name fonts that are neither bundled nor accepted as "
        f"system fonts: {unknown}. Bundle it in public/fonts/ and load it "
        f"in src/fonts.ts, or add it to ACCEPTED_SYSTEM_FONTS with the "
        f"reason it is allowed to be non-deterministic.")


def test_the_font_walk_sees_more_than_style_typography():
    """The walk must find a font wherever a template puts one.

    Regression cover for the hole above: this is the exact declaration
    that slipped through, and it is not under `style.typography`.
    """
    found = dict(_font_names({
        "style": {"typography": {"font": "Montserrat"}},
        "effect": {"timed_text_overlay": {
            "font_family": "'Nanum Pen Script', cursive",
            "moments": [{"font_family": "Archivo Black"}],
        }},
    }))
    assert found == {
        "style.typography.font": "Montserrat",
        "effect.timed_text_overlay.font_family": "'Nanum Pen Script', cursive",
        "effect.timed_text_overlay.moments[0].font_family": "Archivo Black",
    }
    assert _primary_family("'Nanum Pen Script', cursive") == "Nanum Pen Script"
