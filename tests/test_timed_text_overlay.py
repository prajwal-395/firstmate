"""TimedTextOverlay - the general timed-text component, and its reader.

The component and its prop generator survive: the captain confirmed on
2026-08-20 that N timed text moments with per-item colour, size, start
frame and fade IS a general engine component, and all eight series want
intro cards and episode text.

What did NOT survive is the one asset that declared it.  The 4th Wall
night card and closing "end card" ritual were lifted verbatim out of a
previous manual trial run and checked into the now-deleted `fourth_wall.yaml` as series
DEFAULTS - absolute frame numbers baked to that run's 60.000s timeline,
normalised y positions authored against a full-bleed vertical frame, and
an unbundled typeface.  Captain, 2026-08-20: "it was something made in a
previous trial run and is a pretty shoddy asset, so lets just get rid of
it".

And what did not EXIST until now is a reader.  #119 shipped the schema
field, the generator and the Remotion composition, `docs/PIPELINE_PLAN.md`
recorded the gap as closed, and no step in `library/steps/` ever imported
the generator - so three declared moments reached no frame of any render
and every run still reported SUCCESS.  `library.tools.timed_text_overlay`
held the slot shut with a `NO_READER` constant until the step that reads
it landed; both are gone together, which was the condition.

So these tests cover four things:

- the prop-generation contract, unchanged;
- the segment plan the reader places - clustering, rebasing, bounds;
- that a reader really exists, the inverse of the guard that retired here;
- that a declared moment reaches actual PIXELS, through the real render
  path, at fixture scale (`test_timed_text_delivery.py`).

See docs/ASSET_LIBRARY_PLAN.md for the general-vs-project test this
enforces the mechanical half of.
"""
import ast
import glob
import json
import os
import sys

import pytest
import yaml

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

from library.tools.timed_text_overlay import (
    TimedTextDeclarationError,
    generate_timed_text_overlay_props,
    plan_timed_text_segments,
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


def _moment(**overrides) -> dict:
    moment = {
        "text": "Moment",
        "color": "#FFFFFF",
        "start_frame": 30,
        "duration_frames": 30,
    }
    moment.update(overrides)
    return moment


def _declaration(*moments, **declaration) -> dict:
    return {"timed_text_overlay": {"moments": list(moments), **declaration}}


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
def test_an_undeclared_template_plans_no_segments(name):
    """A template that declares nothing gets nothing - the opt-in shape.

    Templates MAY now declare the slot; this asserts the default, which
    is that omitting it costs nothing and renders nothing.
    """
    effect = _template(name).get("effect", {})
    if effect.get("timed_text_overlay"):
        pytest.skip(f"{name} declares the slot; covered by its own tests")
    assert generate_timed_text_overlay_props(effect) is None
    assert plan_timed_text_segments(effect) == []


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
# The slot has a reader (this replaces the guard that held it shut)
# ─────────────────────────────────────────────────────────

def test_a_pipeline_step_reads_the_slot():
    """The inverse of the retired `NO_READER` guard.

    That guard asserted NOTHING under library/ imported the generator,
    because a declaration with no reader is indistinguishable from no
    declaration at all: the run summary says SUCCESS either way.  Now the
    reader has to exist, or the slot has quietly gone inert again.
    """
    readers = []
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
                src = f.read()
            if ("generate_timed_text_overlay_props" in src
                    or "plan_timed_text_segments" in src
                    or "render_timed_text_segments" in src):
                readers.append(os.path.relpath(path, PROJECT_ROOT))
    assert "library/steps/step_4_06_render_motion_graphics/step.py" in readers, (
        f"no pipeline step reads effect.timed_text_overlay; readers found: "
        f"{readers}. A declared moment would render nothing and warn about "
        f"nothing - the exact defect this slot spent four months in.")


def test_no_reader_record_is_gone():
    """`NO_READER` retired with the reader, and must not come back.

    It said "do not declare this slot until a step reads it". A step
    does. Leaving the record in place would tell the next agent to keep
    the working capability switched off.
    """
    source = open(
        os.path.join(PROJECT_ROOT, "library", "tools",
                     "timed_text_overlay.py"), encoding="utf-8").read()
    tree = ast.parse(source)
    names = {
        target.id
        for node in tree.body if isinstance(node, ast.Assign)
        for target in node.targets if isinstance(target, ast.Name)
    }
    assert "NO_READER" not in names, (
        "library/tools/timed_text_overlay.py still declares NO_READER, "
        "but step 4.06 reads the slot.")


def test_the_manifest_carries_the_segments():
    """compile_manifest must emit the key, or the renderer never sees it."""
    compile_step = os.path.join(
        PROJECT_ROOT, "library", "steps", "step_5_04_compile_manifest",
        "step.py")
    with open(compile_step, encoding="utf-8") as f:
        assert '"timed_text_overlay"' in f.read(), (
            "compile_manifest does not emit timed_text_overlay, so 4.06's "
            "rendered segments stop at pipeline_data.json")


# ─────────────────────────────────────────────────────────
# The removed 4th Wall asset stays removed
# ─────────────────────────────────────────────────────────

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
# Segment planning: what the reader actually places
# ─────────────────────────────────────────────────────────

def test_moments_far_apart_become_separate_segments():
    """Frames between two moments carry nothing, so nothing renders them."""
    segments = plan_timed_text_segments(_declaration(
        _moment(start_frame=30, duration_frames=30),
        _moment(start_frame=300, duration_frames=60),
    ))
    assert len(segments) == 2
    assert [s["timeline_start"] for s in segments] == [1.0, 10.0]
    assert [s["timeline_end"] for s in segments] == [2.0, 12.0]
    assert [s["total_frames"] for s in segments] == [30, 60]


def test_overlapping_moments_share_one_segment():
    """Two clips cannot occupy the same frames of V6; Remotion composites."""
    segments = plan_timed_text_segments(_declaration(
        _moment(start_frame=30, duration_frames=30),
        _moment(start_frame=45, duration_frames=30),
    ))
    assert len(segments) == 1
    assert segments[0]["moment_count"] == 2
    assert segments[0]["total_frames"] == 45
    assert segments[0]["timeline_start"] == 1.0
    assert segments[0]["timeline_end"] == 2.5


def test_touching_moments_share_one_segment():
    """A moment starting on the frame the previous one ends is contiguous."""
    segments = plan_timed_text_segments(_declaration(
        _moment(start_frame=30, duration_frames=30),
        _moment(start_frame=60, duration_frames=30),
    ))
    assert len(segments) == 1
    assert segments[0]["total_frames"] == 60


def test_planned_segments_never_overlap():
    """The property that lets every segment share one video track."""
    segments = plan_timed_text_segments(_declaration(
        _moment(start_frame=300, duration_frames=60),
        _moment(start_frame=30, duration_frames=30),
        _moment(start_frame=100, duration_frames=30),
    ))
    for prev, curr in zip(segments, segments[1:]):
        assert curr["timeline_start"] >= prev["timeline_end"]


def test_moment_frames_are_rebased_against_their_segment():
    """The rendered file starts at frame 0; the timeline knows the offset.

    Leaving the declaration's timeline frames in the props would render a
    segment whose text appears `start_frame` frames after the clip
    begins - i.e. never, because the clip is only as long as the moment.
    """
    segments = plan_timed_text_segments(_declaration(
        _moment(start_frame=90, duration_frames=30, text="A"),
        _moment(start_frame=105, duration_frames=30, text="B"),
    ))
    assert len(segments) == 1
    props = segments[0]["props"]
    assert [m["startFrame"] for m in props["moments"]] == [0, 15]
    assert props["durationInFrames"] == 45
    assert segments[0]["timeline_start"] == 3.0


def test_segment_props_carry_the_fixture_geometry():
    segments = plan_timed_text_segments(
        _declaration(_moment(), font_family="Montserrat"),
        fps=30, width=320, height=568)
    props = segments[0]["props"]
    assert (props["width"], props["height"], props["fps"]) == (320, 568, 30)
    assert props["fontFamily"] == "Montserrat"


def test_a_moment_past_the_end_of_the_edit_is_rejected():
    """The 4th Wall card's actual defect: frames from a different cut."""
    with pytest.raises(TimedTextDeclarationError) as exc:
        plan_timed_text_segments(
            _declaration(_moment(start_frame=1800, duration_frames=60)),
            spine_structure=_SPINE)
    assert "reaches no picture" in str(exc.value)


def test_timeline_bound_is_optional():
    """A caller that does not know the edit's length still gets a plan."""
    segments = plan_timed_text_segments(
        _declaration(_moment(start_frame=1800, duration_frames=60)))
    assert len(segments) == 1


# ─────────────────────────────────────────────────────────
# Timing from the spine
# ─────────────────────────────────────────────────────────
# docs/ASSET_LIBRARY_PLAN.md section 5: "It is timed from the spine. No
# absolute frames, no assumed total." The 4th Wall card's frame numbers
# were baked to a 60.000s cut that no longer existed.

_SPINE = [
    {"position": 0, "block_type": "hook",
     "timeline_start": 0.0, "timeline_end": 4.0},
    {"position": 1, "block_type": "speech",
     "timeline_start": 4.0, "timeline_end": 12.0},
    {"position": 2, "block_type": "speech",
     "timeline_start": 12.0, "timeline_end": 20.0},
]


def test_a_moment_anchors_to_a_spine_block():
    segments = plan_timed_text_segments(
        _declaration({
            "text": "EPISODE 001", "color": "#fff",
            "block": 1, "duration_seconds": 2.0,
        }),
        spine_structure=_SPINE)
    assert len(segments) == 1
    assert segments[0]["timeline_start"] == 4.0
    assert segments[0]["total_frames"] == 60


def test_an_anchored_moment_takes_an_offset():
    segments = plan_timed_text_segments(
        _declaration({
            "text": "EPISODE 001", "color": "#fff",
            "block": 2, "offset_seconds": 0.5, "duration_seconds": 1.0,
        }),
        spine_structure=_SPINE)
    assert segments[0]["timeline_start"] == 12.5


def test_a_moment_may_anchor_to_the_end_of_its_block():
    segments = plan_timed_text_segments(
        _declaration({
            "text": "OUT", "color": "#fff", "block": 0,
            "anchor": "end", "offset_seconds": -1.0, "duration_seconds": 1.0,
        }),
        spine_structure=_SPINE)
    assert segments[0]["timeline_start"] == 3.0
    assert segments[0]["timeline_end"] == 4.0


def test_an_anchored_moment_moves_when_the_edit_is_recut():
    """The whole point of anchoring: no frame number survives a re-cut."""
    declaration = _declaration({
        "text": "EPISODE 001", "color": "#fff",
        "block": 2, "duration_seconds": 1.0,
    })
    recut = [dict(b) for b in _SPINE]
    recut[1]["timeline_end"] = 9.0
    recut[2].update({"timeline_start": 9.0, "timeline_end": 17.0})
    before = plan_timed_text_segments(declaration, spine_structure=_SPINE)
    after = plan_timed_text_segments(declaration, spine_structure=recut)
    assert before[0]["timeline_start"] == 12.0
    assert after[0]["timeline_start"] == 9.0


def test_anchoring_to_a_block_that_is_not_in_the_edit_raises():
    with pytest.raises(TimedTextDeclarationError) as exc:
        plan_timed_text_segments(
            _declaration({"text": "X", "color": "#fff",
                          "block": 99, "duration_seconds": 1.0}),
            spine_structure=_SPINE)
    assert "which is not in" in str(exc.value)
    assert "[0, 1, 2]" in str(exc.value)


def test_anchoring_with_no_spine_raises():
    """Silently falling back to frame 0 would put the card in the wrong place."""
    with pytest.raises(TimedTextDeclarationError) as exc:
        plan_timed_text_segments(
            _declaration({"text": "X", "color": "#fff",
                          "block": 1, "duration_seconds": 1.0}))
    assert "no spine was supplied" in " ".join(str(exc.value).split())


def test_declaring_both_timings_raises():
    with pytest.raises(TimedTextDeclarationError) as exc:
        plan_timed_text_segments(
            _declaration({"text": "X", "color": "#fff", "block": 1,
                          "duration_seconds": 1.0, "start_frame": 30}),
            spine_structure=_SPINE)
    assert "not both" in str(exc.value)


def test_declaring_neither_timing_raises():
    with pytest.raises(TimedTextDeclarationError) as exc:
        plan_timed_text_segments(
            _declaration({"text": "X", "color": "#fff"}))
    assert "neither way" in str(exc.value)


def test_an_anchored_moment_needs_a_duration_in_seconds():
    with pytest.raises(TimedTextDeclarationError) as exc:
        plan_timed_text_segments(
            _declaration({"text": "X", "color": "#fff", "block": 1}),
            spine_structure=_SPINE)
    assert "duration_seconds" in str(exc.value)


def test_an_offset_before_the_first_frame_raises():
    with pytest.raises(TimedTextDeclarationError) as exc:
        plan_timed_text_segments(
            _declaration({"text": "X", "color": "#fff", "block": 0,
                          "offset_seconds": -2.0, "duration_seconds": 1.0}),
            spine_structure=_SPINE)
    assert "before" in str(exc.value)


def test_an_unknown_anchor_raises():
    with pytest.raises(TimedTextDeclarationError) as exc:
        plan_timed_text_segments(
            _declaration({"text": "X", "color": "#fff", "block": 1,
                          "anchor": "middle", "duration_seconds": 1.0}),
            spine_structure=_SPINE)
    assert "anchor" in str(exc.value)


@pytest.mark.parametrize("bad,needle", [
    ({"text": ""}, "empty text"),
    ({"color": None}, "missing"),
    ({"start_frame": -5}, "non-negative integers"),
    ({"duration_frames": 0}, "appears in no frame"),
    ({"duration_frames": 1.5}, "appears in no frame"),
    ({"fade_in_frames": -1}, "non-negative"),
    ({"fade_in_frames": 20, "fade_out_frames": 20}, "full opacity"),
    ({"y": 1.4}, "outside the frame"),
    ({"x": "left"}, "between 0 and 1"),
])
def test_a_malformed_moment_raises(bad, needle):
    """Malformed raises; it is never dropped.

    A dropped declaration is a card the editor believes shipped - the
    same rule bookends follow (library/tools/bookends.py).
    """
    with pytest.raises(TimedTextDeclarationError) as exc:
        plan_timed_text_segments(_declaration(_moment(**bad)))
    assert needle in str(exc.value)


def test_zero_fades_are_legal():
    """They crashed the composition; they are a legitimate declaration.

    `interpolate` needs a strictly increasing input range, so a moment
    with no fade produced [0,0,30,30] and threw, killing the render for
    every moment in the segment. See momentOpacity in the composition.
    """
    segments = plan_timed_text_segments(_declaration(
        _moment(fade_in_frames=0, fade_out_frames=0)))
    assert segments[0]["props"]["moments"][0]["fadeInFrames"] == 0


def test_a_non_mapping_declaration_raises():
    with pytest.raises(TimedTextDeclarationError):
        plan_timed_text_segments({"timed_text_overlay": {"moments": "nope"}})


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
