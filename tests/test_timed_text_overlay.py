"""Q7: TimedTextOverlay - general timed text overlay component.

Verifies:
- The prop generator renders deterministically (same input -> identical JSON).
- A template that declares timed_text_overlay gets rendered props.
- A template that omits timed_text_overlay gets None (no overlay).
- The 4th Wall template carries the corrected copy (Night X, no counter).
- The schema loads the new slot without error.
- Every moment in the output satisfies the TimedTextOverlay contract.

The component itself (remotion-subtitles/src/compositions/TimedTextOverlay)
renders in Remotion; these tests cover the prop-generation contract that the
Remotion component consumes. The deterministic check is JSON equality on the
prop generator's output, which is the same technique
test_motion_graphics_template uses for its prop assertions.
"""
import glob
import json
import os
import sys

import pytest
import yaml

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

from library.tools.timed_text_overlay import generate_timed_text_overlay_props

TEMPLATE_DIR = os.path.join(PROJECT_ROOT, "library", "templates")


def _template(name: str) -> dict:
    with open(os.path.join(TEMPLATE_DIR, f"{name}.yaml"), encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


# ─────────────────────────────────────────────────────────
# Deterministic rendering (prop stability)
# ─────────────────────────────────────────────────────────

SAMPLE_DECLARATION = {
    "timed_text_overlay": {
        "font_family": "'Nanum Pen Script', cursive",
        "moments": [
            {
                "text": "Night 1 — Through the 4th Wall",
                "color": "#D4A34A",
                "font_size": 48,
                "start_frame": 162,
                "duration_frames": 60,
                "x": 0.5,
                "y": 0.15,
                "fade_in_frames": 10,
                "fade_out_frames": 10,
            },
            {
                "text": "It's 2:16.",
                "color": "#00BFFF",
                "font_size": 42,
                "start_frame": 1725,
                "duration_frames": 75,
                "x": 0.5,
                "y": 0.5,
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


# ─────────────────────────────────────────────────────────
# Template declaration reaches the render
# ─────────────────────────────────────────────────────────

def test_declared_template_produces_props():
    """A template that declares timed_text_overlay gets rendered props."""
    tmpl = _template("fourth_wall")
    effect = tmpl.get("effect", {})
    result = generate_timed_text_overlay_props(effect)
    assert result is not None, "fourth_wall template should produce overlay props"
    assert len(result["moments"]) > 0
    assert result["fontFamily"] == "'Nanum Pen Script', cursive"


def test_declared_template_moments_count():
    """The 4th Wall overlay has exactly 3 moments (night card + 2 closing lines)."""
    tmpl = _template("fourth_wall")
    result = generate_timed_text_overlay_props(tmpl.get("effect", {}))
    assert len(result["moments"]) == 3


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

@pytest.mark.parametrize("name", [
    "default_brand", "shortform_energetic",
    "cinematic_narrative", "interview_professional",
])
def test_undeclared_template_produces_nothing(name):
    """A template that omits timed_text_overlay gets None - no overlay."""
    tmpl = _template(name)
    effect = tmpl.get("effect", {})
    result = generate_timed_text_overlay_props(effect)
    assert result is None, f"{name} should not produce an overlay"


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
# Corrected 4th Wall copy
# ─────────────────────────────────────────────────────────

def test_corrected_copy_uses_night_not_day():
    """The 4th Wall series uses 'Night X', never 'Day 1' or 'Day X'."""
    tmpl = _template("fourth_wall")
    result = generate_timed_text_overlay_props(tmpl.get("effect", {}))
    all_text = " ".join(m["text"] for m in result["moments"])
    assert "Day 1" not in all_text, "Series spec requires 'Night X', not 'Day 1'"
    assert "Night" in all_text


def test_corrected_copy_has_no_counter():
    """The '1 / 100' counter is dropped - no basis in the series docs."""
    tmpl = _template("fourth_wall")
    result = generate_timed_text_overlay_props(tmpl.get("effect", {}))
    all_text = " ".join(m["text"] for m in result["moments"])
    assert "1 / 100" not in all_text, "Counter was dropped per series spec"
    assert "/ 100" not in all_text


def test_no_day_1_or_counter_in_root_tsx():
    """Root.tsx default props must not contain the old placeholder copy."""
    root_path = os.path.join(PROJECT_ROOT, "remotion-subtitles", "src", "Root.tsx")
    with open(root_path, encoding="utf-8") as f:
        src = f.read()
    # "Day 1" was the old closing line; the spec says "Night X"
    assert "Day 1" not in src, "Root.tsx still contains 'Day 1'"
    # "1 / 100" was the old counter; it was dropped
    assert "1 / 100" not in src, "Root.tsx still contains '1 / 100'"


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
                "font_family": "Helvetica",
                "moments": [{"text": "test", "color": "#fff",
                             "start_frame": 0, "duration_frames": 30}],
            }
        }
    })
    assert tmpl.effect.timed_text_overlay is not None
    assert tmpl.effect.timed_text_overlay["font_family"] == "Helvetica"


def test_schema_omitted_is_none():
    """A template that omits timed_text_overlay gets None, not an empty dict."""
    from library.schemas.brand_template import BrandTemplate
    tmpl = BrandTemplate.from_dict({"series_id": "test"})
    assert tmpl.effect.timed_text_overlay is None


def test_fourth_wall_template_loads_via_schema():
    """The shipped fourth_wall.yaml loads into BrandTemplate without error."""
    from library.schemas.brand_template import BrandTemplate
    raw = _template("fourth_wall")
    tmpl = BrandTemplate.from_dict(raw)
    assert tmpl.series_id == "fourth_wall"
    assert tmpl.effect.timed_text_overlay is not None
    assert len(tmpl.effect.timed_text_overlay["moments"]) == 3


# ─────────────────────────────────────────────────────────
# Template accounting (like test_motion_graphics_template)
# ─────────────────────────────────────────────────────────

# Only fourth_wall declares a timed_text_overlay.
EXPECTED_OVERLAY_TEMPLATES = {"fourth_wall"}


def test_only_expected_templates_declare_overlay():
    """A new template declaring timed_text_overlay should be a decision."""
    on_disk = set()
    for path in glob.glob(os.path.join(TEMPLATE_DIR, "*.yaml")):
        name = os.path.basename(path)[:-5]
        with open(path, encoding="utf-8") as f:
            tmpl = yaml.safe_load(f) or {}
        effect = tmpl.get("effect", {})
        if effect.get("timed_text_overlay"):
            on_disk.add(name)
    assert on_disk == EXPECTED_OVERLAY_TEMPLATES, (
        "a template was added or removed with timed_text_overlay "
        "without updating the expected set")


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
