"""TimedTextOverlay - the general timed-text component, and the empty slot.

The component and its prop generator survive: the captain confirmed on
2026-08-20 that N timed text moments with per-item colour, size, start
frame and fade IS a general engine component, and all eight series want
intro cards and episode text.

What does NOT survive is the one asset that declared it.  The 4th Wall
night card and closing "end card" ritual were lifted verbatim out of a
previous manual trial run and checked into the now-deleted `fourth_wall.yaml` as series
DEFAULTS - absolute frame numbers baked to that run's 60.000s timeline,
normalised y positions authored against a full-bleed vertical frame, and
an unbundled typeface.  Captain, 2026-08-20: "it was something made in a
previous trial run and is a pretty shoddy asset, so lets just get rid of
it".

So these tests now cover three things:

- the prop-generation contract, unchanged, for whoever wires the reader;
- that the removed asset is really gone, engine-side as well as template-side;
- that the empty slot stays empty while it has no reader
  (`library.tools.timed_text_overlay.NO_READER`), because a declaration
  that renders nothing and warns about nothing is how the last one
  survived four months.

See docs/ASSET_LIBRARY_PLAN.md for the general-vs-project test this
enforces the mechanical half of.
"""
import glob
import json
import os
import sys

import pytest
import yaml

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

from library.tools.timed_text_overlay import (
    NO_READER,
    generate_timed_text_overlay_props,
)

TEMPLATE_DIR = os.path.join(PROJECT_ROOT, "library", "templates")
ROOT_TSX = os.path.join(
    PROJECT_ROOT, "remotion-subtitles", "src", "Root.tsx")


def _template(name: str) -> dict:
    with open(os.path.join(TEMPLATE_DIR, f"{name}.yaml"), encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _all_templates() -> dict[str, dict]:
    out = {}
    for path in sorted(glob.glob(os.path.join(TEMPLATE_DIR, "*.yaml"))):
        with open(path, encoding="utf-8") as f:
            out[os.path.basename(path)[:-5]] = yaml.safe_load(f) or {}
    return out


# ─────────────────────────────────────────────────────────
# Deterministic rendering (prop stability)
# ─────────────────────────────────────────────────────────

SAMPLE_DECLARATION = {
    "timed_text_overlay": {
        "font_family": "Montserrat",
        "moments": [
            {
                "text": "First moment",
                "color": "#D4A34A",
                "font_size": 48,
                "start_frame": 30,
                "duration_frames": 60,
                "x": 0.5,
                "y": 0.5,
                "fade_in_frames": 10,
                "fade_out_frames": 10,
            },
            {
                "text": "Second moment",
                "color": "#00BFFF",
                "font_size": 42,
                "start_frame": 120,
                "duration_frames": 75,
                "x": 0.5,
                "y": 0.6,
                "fade_in_frames": 8,
                "fade_out_frames": 8,
            },
        ],
    }
}


def test_props_are_deterministic():
    """Same input declaration -> identical JSON output, byte-for-byte."""
    a = generate_timed_text_overlay_props(SAMPLE_DECLARATION)
    b = generate_timed_text_overlay_props(SAMPLE_DECLARATION)
    assert json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)


def test_deterministic_across_multiple_runs():
    """Run the generator 10 times; all results must be identical."""
    baseline = json.dumps(
        generate_timed_text_overlay_props(SAMPLE_DECLARATION), sort_keys=True)
    for _ in range(10):
        assert json.dumps(
            generate_timed_text_overlay_props(SAMPLE_DECLARATION),
            sort_keys=True) == baseline


def test_props_match_remotion_schema():
    """Output props must match TimedTextOverlay's expected shape."""
    result = generate_timed_text_overlay_props(SAMPLE_DECLARATION)
    assert "moments" in result
    assert "fontFamily" in result
    assert "fps" in result
    assert "width" in result
    assert "height" in result
    assert "durationInFrames" in result


def test_moments_have_required_fields():
    """Every moment in the output carries the full TimedTextOverlay contract."""
    result = generate_timed_text_overlay_props(SAMPLE_DECLARATION)
    required = {"text", "color", "fontSize", "startFrame", "durationFrames",
                "x", "y", "fadeInFrames", "fadeOutFrames"}
    for i, moment in enumerate(result["moments"]):
        missing = required - set(moment.keys())
        assert not missing, f"Moment {i} missing keys: {missing}"


# ─────────────────────────────────────────────────────────
# Undeclared template renders no overlay
# ─────────────────────────────────────────────────────────

@pytest.mark.parametrize("name", sorted(_all_templates()))
def test_no_template_produces_an_overlay(name):
    """No shipped template declares the slot - see NO_READER."""
    result = generate_timed_text_overlay_props(
        _template(name).get("effect", {}))
    assert result is None, (
        f"{name} declares effect.timed_text_overlay. {NO_READER}")


def test_empty_effect_dict_produces_nothing():
    result = generate_timed_text_overlay_props({})
    assert result is None


def test_empty_moments_produces_nothing():
    """A declaration with an empty moments list is treated as undeclared."""
    result = generate_timed_text_overlay_props(
        {"timed_text_overlay": {"moments": []}})
    assert result is None


def test_none_declaration_produces_nothing():
    result = generate_timed_text_overlay_props(
        {"timed_text_overlay": None})
    assert result is None


# ─────────────────────────────────────────────────────────
# The removed 4th Wall asset stays removed
# ─────────────────────────────────────────────────────────

def test_no_template_declares_the_slot_while_it_has_no_reader():
    """The slot may not be declared until a step reads it.

    The 4th Wall asset survived four months of audits precisely because a
    declaration with no reader is indistinguishable from no declaration
    at all: the run summary says SUCCESS either way.  Wire the reader
    first, delete `NO_READER` in that commit, then declare.
    """
    declaring = [
        name for name, tmpl in _all_templates().items()
        if (tmpl.get("effect") or {}).get("timed_text_overlay")
    ]
    assert declaring == [], f"{declaring} declare the slot. {NO_READER}"


def test_the_slot_really_has_no_reader():
    """`NO_READER` must describe the tree, not a stale memory of it.

    If someone wires a step to `generate_timed_text_overlay_props`, this
    fails and points them at the record to delete.
    """
    importers = []
    for root, dirs, files in os.walk(os.path.join(PROJECT_ROOT, "library")):
        dirs[:] = [d for d in dirs if d != "__pycache__"]
        for name in files:
            if not name.endswith(".py"):
                continue
            path = os.path.join(root, name)
            if os.path.samefile(
                    path,
                    os.path.join(PROJECT_ROOT, "library", "tools",
                                 "timed_text_overlay.py")):
                continue
            with open(path, encoding="utf-8") as f:
                if "generate_timed_text_overlay_props" in f.read():
                    importers.append(os.path.relpath(path, PROJECT_ROOT))
    assert importers == [], (
        f"{importers} now read the slot, so it has a reader. Delete "
        f"NO_READER from library/tools/timed_text_overlay.py and this "
        f"test, and assert the moments reach the picture instead.")


def test_fourth_wall_trial_run_asset_is_gone_from_the_engine():
    """No series-specific overlay artwork left in the engine repo."""
    offenders = []
    for root, dirs, files in os.walk(PROJECT_ROOT):
        dirs[:] = [d for d in dirs if d not in {
            ".git", "node_modules", "__pycache__", ".venv", ".pytest_cache",
            "pipeline_output", "docs", "tests"}]
        for name in files:
            if not name.endswith((".tsx", ".ts", ".yaml", ".yml")):
                continue
            path = os.path.join(root, name)
            with open(path, encoding="utf-8", errors="replace") as f:
                src = f.read()
            # The asset's own artwork and identity. Not the typeface -
            # Nanum Pen Script is the series' locked display face and may
            # return legitimately; whether it is deliverable is
            # tests/test_bundled_fonts.py's question, not this one.
            for needle in ("FourthWallOverlay", "Attack the day tomorrow",
                           "It's 2:16.", "1 / 100"):
                if needle in src:
                    offenders.append(
                        (os.path.relpath(path, PROJECT_ROOT), needle))
    assert offenders == [], (
        f"the removed 4th Wall trial-run asset is back: {offenders}")


def test_root_tsx_registers_only_general_compositions():
    """Root.tsx is the ENGINE's composition registry.

    A composition named after one series belongs with that series'
    project, not here - that is what `content.bookends` + `source:` is
    for (library/tools/bookends.py).  `FourthWallOverlay` was registered
    here; it is gone.
    """
    with open(ROOT_TSX, encoding="utf-8") as f:
        src = f.read()
    ids = set(
        line.split('id="', 1)[1].split('"', 1)[0]
        for line in src.splitlines() if 'id="' in line
    )
    assert ids == {"SubtitleOverlay", "MotionGraphics", "TimedTextOverlay"}, (
        f"Root.tsx registers {sorted(ids)}. A composition named after one "
        f"series is a project asset - declare it with content.bookends "
        f"and a project-owned `source:` instead.")


# ─────────────────────────────────────────────────────────
# Schema integration
# ─────────────────────────────────────────────────────────

def test_schema_loads_timed_text_overlay():
    """BrandTemplate.from_dict loads the timed_text_overlay slot."""
    from library.schemas.brand_template import BrandTemplate
    tmpl = BrandTemplate.from_dict({
        "series_id": "test",
        "effect": {
            "timed_text_overlay": {
                "font_family": "Montserrat",
                "moments": [{"text": "test", "color": "#fff",
                             "start_frame": 0, "duration_frames": 30}],
            }
        }
    })
    assert tmpl.effect.timed_text_overlay is not None
    assert tmpl.effect.timed_text_overlay["font_family"] == "Montserrat"


def test_schema_omitted_is_none():
    """A template that omits timed_text_overlay gets None, not an empty dict."""
    from library.schemas.brand_template import BrandTemplate
    tmpl = BrandTemplate.from_dict({"series_id": "test"})
    assert tmpl.effect.timed_text_overlay is None




# ─────────────────────────────────────────────────────────
# Default values and edge cases
# ─────────────────────────────────────────────────────────

def test_default_values_applied():
    """Moments with minimal keys get sensible defaults."""
    result = generate_timed_text_overlay_props({
        "timed_text_overlay": {
            "moments": [{
                "text": "Hello",
                "color": "#fff",
                "start_frame": 0,
                "duration_frames": 30,
            }]
        }
    })
    m = result["moments"][0]
    assert m["fontSize"] == 42  # default
    assert m["x"] == 0.5  # default center
    assert m["y"] == 0.5  # default center
    assert m["fadeInFrames"] == 10  # default
    assert m["fadeOutFrames"] == 10  # default
    assert m["fontWeight"] == 400  # default
    assert m["textAlign"] == "center"  # default


def test_custom_fps_and_dimensions():
    """fps, width, height, duration_in_frames are forwarded."""
    result = generate_timed_text_overlay_props(
        SAMPLE_DECLARATION,
        fps=60, width=1920, height=1080, duration_in_frames=3600,
    )
    assert result["fps"] == 60
    assert result["width"] == 1920
    assert result["height"] == 1080
    assert result["durationInFrames"] == 3600


def test_default_font_family():
    """When font_family is omitted, Helvetica is the default."""
    result = generate_timed_text_overlay_props({
        "timed_text_overlay": {
            "moments": [{"text": "X", "color": "#fff",
                         "start_frame": 0, "duration_frames": 30}]
        }
    })
    assert result["fontFamily"] == "Helvetica"
