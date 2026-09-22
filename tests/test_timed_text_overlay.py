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
import json
import os
import sys

import pytest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

from library.tools.timed_text_overlay import (
    TimedTextDeclarationError,
    generate_timed_text_overlay_props,
    plan_timed_text_segments,
)

ROOT_TSX = os.path.join(
    PROJECT_ROOT, "remotion-subtitles", "src", "Root.tsx")


def _moment(**overrides) -> dict:
    """A moment stating its full look, as a declaration now must.

    Size, fade, weight, shadow and position are artwork the project
    states (AGENTS.md 10.5, 14) - `library.tools.timed_text_overlay`
    raises on an omission rather than rendering in an engine constant.
    Only `text_align` is left out on purpose, exercising the one
    surviving default the shipped Night card relies on (see
    `STYLE_MOMENT_KEYS`).
    """
    moment = {
        "text": "Moment",
        "color": "#FFFFFF",
        "font_size": 42,
        "font_weight": 400,
        "text_shadow": "0px 4px 12px rgba(0,0,0,0.6)",
        "start_frame": 30,
        "duration_frames": 30,
        "x": 0.5,
        "y": 0.5,
        "fade_in_frames": 10,
        "fade_out_frames": 10,
    }
    moment.update(overrides)
    return moment


def _declaration(*moments, **declaration) -> dict:
    base = {"font_family": "Montserrat"}
    base.update(declaration)
    return {"timed_text_overlay": {"moments": list(moments), **base}}


# The look, for inline declarations that do not go through `_moment`.
_LOOK = {
    "font_size": 42,
    "font_weight": 400,
    "text_shadow": "0px 4px 12px rgba(0,0,0,0.6)",
    "x": 0.5,
    "y": 0.5,
    "fade_in_frames": 10,
    "fade_out_frames": 10,
}


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
                "font_weight": 400,
                "text_shadow": "0px 4px 12px rgba(0,0,0,0.6)",
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
                "font_weight": 700,
                "text_align": "center",
                "text_shadow": "none",
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
    a = generate_timed_text_overlay_props(SAMPLE_DECLARATION, width=1080, height=1920)
    b = generate_timed_text_overlay_props(SAMPLE_DECLARATION, width=1080, height=1920)
    assert json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)


def test_deterministic_across_multiple_runs():
    """Run the generator 10 times; all results must be identical."""
    baseline = json.dumps(
        generate_timed_text_overlay_props(SAMPLE_DECLARATION, width=1080, height=1920), sort_keys=True)
    for _ in range(10):
        assert json.dumps(
            generate_timed_text_overlay_props(SAMPLE_DECLARATION, width=1080, height=1920),
            sort_keys=True) == baseline


def test_props_match_remotion_schema():
    """Output props must match TimedTextOverlay's expected shape."""
    result = generate_timed_text_overlay_props(SAMPLE_DECLARATION, width=1080, height=1920)
    assert "moments" in result
    assert "fontFamily" in result
    assert "fps" in result
    assert "width" in result
    assert "height" in result
    assert "durationInFrames" in result


def test_moments_have_required_fields():
    """Every moment in the output carries the full TimedTextOverlay contract."""
    result = generate_timed_text_overlay_props(SAMPLE_DECLARATION, width=1080, height=1920)
    required = {"text", "color", "fontSize", "startFrame", "durationFrames",
                "x", "y", "fadeInFrames", "fadeOutFrames"}
    for i, moment in enumerate(result["moments"]):
        missing = required - set(moment.keys())
        assert not missing, f"Moment {i} missing keys: {missing}"


# ─────────────────────────────────────────────────────────
# Undeclared template renders no overlay
# ─────────────────────────────────────────────────────────

# There used to be a sweep here, parametrized over every
# `library/templates/*.yaml`, asserting that a template declaring
# nothing plans no segments. #1262 removed the in-engine templates by
# product decision (a brand lives in the project's own brand.json;
# `test_no_project_copy_declares_a_look` pins the directory absent), so
# the sweep parametrized over an empty set and reported an undeclared
# collection skip instead of measuring anything. The opt-in shape it
# asserted - omitting the slot costs nothing and renders nothing - is
# still covered, per shape, by the unit tests below; do not restore a
# sweep over a directory the product deliberately does not ship.


def test_empty_effect_dict_produces_nothing():
    result = generate_timed_text_overlay_props({}, width=1080, height=1920)
    assert result is None


def test_empty_moments_produces_nothing():
    """A declaration with an empty moments list is treated as undeclared."""
    result = generate_timed_text_overlay_props(
        {"timed_text_overlay": {"moments": []}}, width=1080, height=1920)
    assert result is None


def test_none_declaration_produces_nothing():
    result = generate_timed_text_overlay_props(
        {"timed_text_overlay": None}, width=1080, height=1920)
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
    # `post_bridge.py`, not `step.py`: 4.06 became a hybrid step on
    # 2026-09-02 and the renderer half moved to the post-bridge, which
    # is the file the hybrid runner executes.
    assert ("library/steps/step_4_06_render_motion_graphics/post_bridge.py"
            in readers), (
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

    `FullFrameCard` is listed because it is series-NEUTRAL in the same
    way the other three are: it draws whatever runs, colours and
    typeface a project declares and states none of its own
    (`library/tools/full_frame_element.py`, AGENTS.md 14).  The
    assertion stays an EQUALITY rather than a subset check, so a
    composition added without this reasoning still fails here.

    `BrandMotion` is listed for the same reason: it plays whatever
    staged brand file a project declares and states none of its own -
    the studio default is an empty `src` that renders null, and there
    is no copy, colour, typeface or motion character in the component
    (`library/tools/brand_motion.py`, AGENTS.md 14).  PR 704 registered
    it without writing this paragraph down; `test_brand_motion.py`
    pins the registration itself, so removing it here is not the fix.

    `StagedScene` is listed for the same reason, and it is the one that
    most needed the paragraph: it draws a whole animated PICTURE rather
    than an overlay, which is exactly where a house look would hide.  It
    states no ground colour, no texture, no vignette, no palette, no
    typeface, no size, no camera move and no duration - all of them
    arrive in props from a declaration, and the studio default is a
    black ground with no layers, which draws nothing.  The three values
    it does supply are the neutral camera (no move), an absent frame
    hold (every frame drawn) and what its four ease NAMES look like -
    each the absence of a choice or the drawing of a word, in the
    reading AGENTS.md 10.5 gives `CUT_TYPES` and `RAMP_FRAMES`.
    `docs/ANIMATION_FIRST_REFERENCE.md` is the measurement it answers
    and `tests/test_staged_scene.py` pins the registration itself.
    """
    with open(ROOT_TSX, encoding="utf-8") as f:
        src = f.read()
    ids = set(
        line.split('id="', 1)[1].split('"', 1)[0]
        for line in src.splitlines() if 'id="' in line
    )
    assert ids == {"SubtitleOverlay", "MotionGraphics", "TimedTextOverlay",
                   "FullFrameCard", "BrandMotion", "StagedScene"}, (
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
    ), width=1080, height=1920)
    assert len(segments) == 2
    assert [s["timeline_start"] for s in segments] == [1.0, 10.0]
    assert [s["timeline_end"] for s in segments] == [2.0, 12.0]
    assert [s["total_frames"] for s in segments] == [30, 60]


def test_overlapping_moments_share_one_segment():
    """Two clips cannot occupy the same frames of V6; Remotion composites."""
    segments = plan_timed_text_segments(_declaration(
        _moment(start_frame=30, duration_frames=30),
        _moment(start_frame=45, duration_frames=30),
    ), width=1080, height=1920)
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
    ), width=1080, height=1920)
    assert len(segments) == 1
    assert segments[0]["total_frames"] == 60


def test_planned_segments_never_overlap():
    """The property that lets every segment share one video track."""
    segments = plan_timed_text_segments(_declaration(
        _moment(start_frame=300, duration_frames=60),
        _moment(start_frame=30, duration_frames=30),
        _moment(start_frame=100, duration_frames=30),
    ), width=1080, height=1920)
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
    ), width=1080, height=1920)
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
            spine_structure=_SPINE, width=1080, height=1920)
    assert "reaches no picture" in str(exc.value)


def test_timeline_bound_is_optional():
    """A caller that does not know the edit's length still gets a plan."""
    segments = plan_timed_text_segments(
        _declaration(_moment(start_frame=1800, duration_frames=60)), width=1080, height=1920)
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
            **_LOOK,
        }),
        spine_structure=_SPINE, width=1080, height=1920)
    assert len(segments) == 1
    assert segments[0]["timeline_start"] == 4.0
    assert segments[0]["total_frames"] == 60


def test_an_anchored_moment_takes_an_offset():
    segments = plan_timed_text_segments(
        _declaration({
            "text": "EPISODE 001", "color": "#fff",
            "block": 2, "offset_seconds": 0.5, "duration_seconds": 1.0,
            **_LOOK,
        }),
        spine_structure=_SPINE, width=1080, height=1920)
    assert segments[0]["timeline_start"] == 12.5


def test_a_moment_may_anchor_to_the_end_of_its_block():
    segments = plan_timed_text_segments(
        _declaration({
            "text": "OUT", "color": "#fff", "block": 0,
            "anchor": "end", "offset_seconds": -1.0, "duration_seconds": 1.0,
            **_LOOK,
        }),
        spine_structure=_SPINE, width=1080, height=1920)
    assert segments[0]["timeline_start"] == 3.0
    assert segments[0]["timeline_end"] == 4.0


def test_an_anchored_moment_moves_when_the_edit_is_recut():
    """The whole point of anchoring: no frame number survives a re-cut."""
    declaration = _declaration({
        "text": "EPISODE 001", "color": "#fff",
        "block": 2, "duration_seconds": 1.0,
        **_LOOK,
    })
    recut = [dict(b) for b in _SPINE]
    recut[1]["timeline_end"] = 9.0
    recut[2].update({"timeline_start": 9.0, "timeline_end": 17.0})
    before = plan_timed_text_segments(declaration, spine_structure=_SPINE, width=1080, height=1920)
    after = plan_timed_text_segments(declaration, spine_structure=recut, width=1080, height=1920)
    assert before[0]["timeline_start"] == 12.0
    assert after[0]["timeline_start"] == 9.0


def test_anchoring_to_a_block_that_is_not_in_the_edit_raises():
    with pytest.raises(TimedTextDeclarationError) as exc:
        plan_timed_text_segments(
            _declaration({"text": "X", "color": "#fff",
                          "block": 99, "duration_seconds": 1.0}),
            spine_structure=_SPINE, width=1080, height=1920)
    assert "which is not in" in str(exc.value)
    assert "[0, 1, 2]" in str(exc.value)


def test_anchoring_with_no_spine_raises():
    """Silently falling back to frame 0 would put the card in the wrong place."""
    with pytest.raises(TimedTextDeclarationError) as exc:
        plan_timed_text_segments(
            _declaration({"text": "X", "color": "#fff",
                          "block": 1, "duration_seconds": 1.0}), width=1080, height=1920)
    assert "no spine was supplied" in " ".join(str(exc.value).split())


def test_declaring_both_timings_raises():
    with pytest.raises(TimedTextDeclarationError) as exc:
        plan_timed_text_segments(
            _declaration({"text": "X", "color": "#fff", "block": 1,
                          "duration_seconds": 1.0, "start_frame": 30}),
            spine_structure=_SPINE, width=1080, height=1920)
    assert "not both" in str(exc.value)


def test_declaring_neither_timing_raises():
    with pytest.raises(TimedTextDeclarationError) as exc:
        plan_timed_text_segments(
            _declaration({"text": "X", "color": "#fff"}), width=1080, height=1920)
    assert "neither way" in str(exc.value)


def test_an_anchored_moment_needs_a_duration_in_seconds():
    with pytest.raises(TimedTextDeclarationError) as exc:
        plan_timed_text_segments(
            _declaration({"text": "X", "color": "#fff", "block": 1}),
            spine_structure=_SPINE, width=1080, height=1920)
    assert "duration_seconds" in str(exc.value)


def test_an_offset_before_the_first_frame_raises():
    with pytest.raises(TimedTextDeclarationError) as exc:
        plan_timed_text_segments(
            _declaration({"text": "X", "color": "#fff", "block": 0,
                          "offset_seconds": -2.0, "duration_seconds": 1.0}),
            spine_structure=_SPINE, width=1080, height=1920)
    assert "before" in str(exc.value)


def test_an_unknown_anchor_raises():
    with pytest.raises(TimedTextDeclarationError) as exc:
        plan_timed_text_segments(
            _declaration({"text": "X", "color": "#fff", "block": 1,
                          "anchor": "middle", "duration_seconds": 1.0}),
            spine_structure=_SPINE, width=1080, height=1920)
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
        plan_timed_text_segments(_declaration(_moment(**bad)), width=1080, height=1920)
    assert needle in str(exc.value)


def test_zero_fades_are_legal():
    """They crashed the composition; they are a legitimate declaration.

    `interpolate` needs a strictly increasing input range, so a moment
    with no fade produced [0,0,30,30] and threw, killing the render for
    every moment in the segment. See momentOpacity in the composition.
    """
    segments = plan_timed_text_segments(_declaration(
        _moment(fade_in_frames=0, fade_out_frames=0)), width=1080, height=1920)
    assert segments[0]["props"]["moments"][0]["fadeInFrames"] == 0


def test_a_non_mapping_declaration_raises():
    with pytest.raises(TimedTextDeclarationError):
        plan_timed_text_segments({"timed_text_overlay": {"moments": "nope"}}, width=1080, height=1920)


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
# The look is declared, never defaulted (AGENTS.md 10.5)
# ─────────────────────────────────────────────────────────

def test_a_moment_with_no_look_raises_naming_what_is_missing():
    """Size, fade, weight, shadow and position are artwork.

    The engine states none of them: a moment omitting one raises
    rather than rendering in a constant nobody chose - the same
    refusal bookends.py makes on a malformed declaration.
    """
    with pytest.raises(TimedTextDeclarationError) as exc:
        generate_timed_text_overlay_props({
            "timed_text_overlay": {
                "font_family": "Montserrat",
                "moments": [{
                    "text": "Hello",
                    "color": "#fff",
                    "start_frame": 0,
                    "duration_frames": 30,
                }]
            }
        }, width=1080, height=1920)
    message = str(exc.value)
    for key in ("font_size", "fade_in_frames", "fade_out_frames",
                "font_weight", "text_shadow", "x", "y"):
        assert key in message, f"{key} not named in: {message}"


def test_an_omitted_typeface_raises_rather_than_substituting():
    """The family a card is drawn in is artwork the project declares."""
    with pytest.raises(TimedTextDeclarationError) as exc:
        generate_timed_text_overlay_props({
            "timed_text_overlay": {
                "moments": [dict(_moment(), text="X")],
            }
        }, width=1080, height=1920)
    assert "font_family" in str(exc.value)


@pytest.mark.parametrize("key", [
    "font_size", "fade_in_frames", "fade_out_frames", "font_weight",
    "text_shadow", "x", "y",
])
def test_each_look_key_is_required_on_its_own(key):
    """Dropping any one key drops nothing silently: it raises, by name."""
    moment = _moment()
    del moment[key]
    with pytest.raises(TimedTextDeclarationError) as exc:
        plan_timed_text_segments(_declaration(moment), width=1080,
                                 height=1920)
    assert key in str(exc.value)


@pytest.mark.parametrize("bad,needle", [
    ({"font_size": 0}, "positive number"),
    ({"font_size": -12}, "positive number"),
    ({"font_size": "big"}, "positive number"),
    ({"text_shadow": None}, "omits its look"),
    ({"text_shadow": 12}, "text-shadow string"),
])
def test_a_moment_with_an_unstatable_look_raises(bad, needle):
    """A look that states nothing renderable is malformed, not defaulted."""
    with pytest.raises(TimedTextDeclarationError) as exc:
        plan_timed_text_segments(_declaration(_moment(**bad)), width=1080,
                                 height=1920)
    assert needle in str(exc.value)


def test_an_omitted_alignment_still_renders_centred():
    """The one surviving default, pinned until the captain decides it.

    The shipped Night card omits `text_align` and renders today
    (see STYLE_MOMENT_KEYS): requiring it would stop that project's
    render, so the `center` default stays and this test pins that an
    omission renders centred rather than raising.
    """
    segments = plan_timed_text_segments(
        _declaration(_moment()), width=1080, height=1920)
    assert segments[0]["props"]["moments"][0]["textAlign"] == "center"
    declared = plan_timed_text_segments(
        _declaration(_moment(text_align="right")), width=1080, height=1920)
    assert declared[0]["props"]["moments"][0]["textAlign"] == "right"


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


# ─────────────────────────────────────────────────────────
# Where a declaration may live: the project wins
# ─────────────────────────────────────────────────────────

def _write_project(tmp_path, body: str) -> str:
    (tmp_path / "project.yaml").write_text(body, encoding="utf-8")
    return str(tmp_path)


PROJECT_CARD = """
name: Night 1
effect:
  timed_text_overlay:
    font_family: Helvetica
    moments:
      - text: "Night 1"
        color: "#D4A34A"
        block: hook
        anchor: end
        duration_seconds: 2.0
"""


def test_a_project_declaration_beats_the_templates(tmp_path):
    """Series artwork belongs to the project, not to the engine.

    `docs/ASSET_LIBRARY_PLAN.md` section 3, ratified 2026-08-20: a brand
    template sets parameters and may not carry copy the viewer reads.
    Until this route existed the only place a card could be written was a
    template, which is the corner the removed end card died in.
    """
    from library.tools.timed_text_overlay import resolve_declaration

    template_effect = {
        "sfx_density": "sparse",
        "timed_text_overlay": {"moments": [
            {"text": "FROM THE TEMPLATE", "color": "#FFFFFF",
             "start_frame": 0, "duration_frames": 30}]},
    }
    project = _write_project(tmp_path, PROJECT_CARD)

    resolved = resolve_declaration(template_effect, project)
    texts = [m["text"] for m in resolved["timed_text_overlay"]["moments"]]
    assert texts == ["Night 1"], "the project's card must win outright"
    # The rest of the effect slots are untouched: this replaces one slot,
    # not the template.
    assert resolved["sfx_density"] == "sparse"
    # And the template's own dict is not mutated under it.
    assert template_effect["timed_text_overlay"]["moments"][0]["text"] == (
        "FROM THE TEMPLATE")


def test_a_project_that_declares_nothing_leaves_the_template_alone(tmp_path):
    from library.tools.timed_text_overlay import resolve_declaration

    template_effect = {"timed_text_overlay": {"moments": [
        {"text": "KEEP ME", "color": "#FFFFFF",
         "start_frame": 0, "duration_frames": 30}]}}
    project = _write_project(tmp_path, "name: No Card\n")
    assert resolve_declaration(template_effect, project) == template_effect

    # No project.yaml at all, and no project folder at all.
    assert resolve_declaration(template_effect, str(tmp_path / "nope")) == (
        template_effect)
    assert resolve_declaration(template_effect, "") == template_effect


@pytest.mark.parametrize("body,needle", [
    ("effect: not-a-mapping\n", "not a mapping"),
    ("effect:\n  timed_text_overlay: [1, 2]\n", "not a declaration"),
])
def test_a_malformed_project_declaration_raises(tmp_path, body, needle):
    """A dropped declaration is a card the editor believes shipped."""
    from library.tools.timed_text_overlay import (
        TimedTextDeclarationError,
        resolve_declaration,
    )
    with pytest.raises(TimedTextDeclarationError) as raised:
        resolve_declaration({}, _write_project(tmp_path, body))
    assert needle in str(raised.value)


# ─────────────────────────────────────────────────────────
# The typeface must be one that really draws the glyphs
# ─────────────────────────────────────────────────────────

def test_an_unbundled_family_without_a_file_raises():
    """The silent failure: Chromium substitutes and the frames look fine.

    A per-series typeface is not bundled and must not be - it lives with
    its project - so the declaration names the staged file. See
    library/tools/render_fonts.py.
    """
    from library.tools.timed_text_overlay import (
        TimedTextDeclarationError,
        plan_timed_text_segments,
    )
    declaration = {"timed_text_overlay": {
        "font_family": "Nanum Pen Script",
        "moments": [{"text": "Night 1", "color": "#D4A34A",
                      "start_frame": 0, "duration_frames": 30,
                      **_LOOK}],
    }}
    with pytest.raises(TimedTextDeclarationError) as raised:
        plan_timed_text_segments(declaration, width=1080, height=1920)
    assert "Nanum Pen Script" in str(raised.value)


def test_a_project_font_reaches_the_props_as_a_static_path():
    """`prep_remotion` stages brand_assets/ into public/brand/."""
    from library.tools.timed_text_overlay import plan_timed_text_segments

    declaration = {"timed_text_overlay": {
        "font_family": "Nanum Pen Script",
        "font_file": "NanumPenScript-Regular.ttf",
        "moments": [{"text": "Night 1", "color": "#D4A34A",
                      "start_frame": 0, "duration_frames": 30,
                      **_LOOK}],
    }}
    props = plan_timed_text_segments(declaration, width=1080, height=1920)[0]["props"]
    assert props["fontFamily"] == "Nanum Pen Script"
    assert props["fontFile"] == "brand/NanumPenScript-Regular.ttf"


def test_a_bundled_or_accepted_family_needs_no_file():
    from library.tools.render_fonts import (
        ACCEPTED_SYSTEM_FONTS,
        BUNDLED_FONT_FAMILY,
    )
    from library.tools.timed_text_overlay import plan_timed_text_segments

    for family in [BUNDLED_FONT_FAMILY, *ACCEPTED_SYSTEM_FONTS]:
        declaration = {"timed_text_overlay": {
            "font_family": family,
            "moments": [{"text": "x", "color": "#FFFFFF",
                          "start_frame": 0, "duration_frames": 30,
                          **_LOOK}],
        }}
        props = plan_timed_text_segments(declaration, width=1080, height=1920)[0]["props"]
        assert props["fontFamily"] == family
        assert "fontFile" not in props


# ─────────────────────────────────────────────────────────
# The render carries the overlay codec, or it raises
# ─────────────────────────────────────────────────────────

class _RenderOk:
    returncode = 0
    stderr = ""


def _render_one_with(monkeypatch, tmp_path, *, carry):
    """`_render_one` with the Remotion launch and the carry stubbed.

    Nothing here shells out: the launch is faked at `subprocess.run`
    and the carry is the *carry* argument. The render is "done" by
    pre-creating the output file, which is exactly the state a
    successful Remotion run leaves behind.
    """
    from library.tools import timed_text_render as render_mod

    out_path = str(tmp_path / "timed_text_000.mov")
    with open(out_path, "wb") as handle:
        handle.write(b"not a real mov, only os.path.exists matters here")
    monkeypatch.setattr(
        render_mod.subprocess, "run",
        lambda *args, **kwargs: _RenderOk())
    calls = []
    if callable(carry):
        def _carry(path):
            calls.append(path)
            return carry(path)
    else:
        def _carry(path):
            calls.append(path)
            return carry
    monkeypatch.setattr(render_mod, "transcode_in_place", _carry)
    return render_mod, out_path, calls


def test_a_rendered_segment_is_carried_as_the_overlay(tmp_path, monkeypatch):
    """Step 4.06 stamps these segments as the overlay format, so the
    file must BE that codec - Remotion cannot write it, which is why
    the carry happens here and not in the render command."""
    from library.tools.overlay_carriage import OVERLAY_VIDEO_CODEC

    render_mod, out_path, calls = _render_one_with(
        monkeypatch, tmp_path, carry={
            "error": "", "changed": True, "before": 100, "after": 40})
    render_mod._render_one(
        "timed_text_000", out_path,
        str(tmp_path / "timed_text_000_props.json"), str(tmp_path))
    assert calls == [out_path], (
        "the Remotion ProRes render was left on disk as the artefact; "
        f"step 4.06 reports it as {OVERLAY_VIDEO_CODEC}")


def test_a_segment_whose_carry_fails_raises(tmp_path, monkeypatch):
    """A failed carry RAISES like every other render failure here.

    A missing overlay leaves the picture underneath intact, so a
    warning would ship an episode silently without the text the
    template declared - and a ProRes file stamped as the overlay
    codec would be the old-way/new-stamp combination the carriage
    exists to forbid.
    """
    from library.tools.timed_text_render import TimedTextRenderError

    render_mod, out_path, _ = _render_one_with(
        monkeypatch, tmp_path, carry={
            "error": "transcode failed: no ffmpeg here", "changed": False,
            "before": 100, "after": 100})
    with pytest.raises(TimedTextRenderError, match="carried"):
        render_mod._render_one(
            "timed_text_000", out_path,
            str(tmp_path / "timed_text_000_props.json"), str(tmp_path))
