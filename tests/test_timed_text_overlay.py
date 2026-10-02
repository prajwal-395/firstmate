"""TimedTextOverlay - the general timed-text component, and its reader.

Covers the prop-generation contract, the segment plan the reader places
(clustering, rebasing, bounds, spine anchoring), the refusals of a malformed
declaration, and that a reader exists. Pixels: `test_timed_text_delivery.py`.
History (the removed 4th Wall asset, the reader that never existed):
docs/evidence/timed_text_overlay.md.
"""
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
# Undeclared template renders no overlay
# ─────────────────────────────────────────────────────────

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


# ─────────────────────────────────────────────────────────
# Segment planning: what the reader actually places
# ─────────────────────────────────────────────────────────

def test_moments_cluster_into_segments_only_where_they_overlap():
    """Frames between two moments carry nothing, so nothing renders them."""
    segments = plan_timed_text_segments(_declaration(
        _moment(start_frame=30, duration_frames=30),
        _moment(start_frame=300, duration_frames=60),
    ), width=1080, height=1920)
    assert len(segments) == 2
    assert [s["timeline_start"] for s in segments] == [1.0, 10.0]
    assert [s["timeline_end"] for s in segments] == [2.0, 12.0]
    assert [s["total_frames"] for s in segments] == [30, 60]

    # Two clips cannot occupy the same frames of V6; Remotion composites.
    segments = plan_timed_text_segments(_declaration(
        _moment(start_frame=30, duration_frames=30),
        _moment(start_frame=45, duration_frames=30),
    ), width=1080, height=1920)
    assert len(segments) == 1
    assert segments[0]["moment_count"] == 2
    assert segments[0]["total_frames"] == 45
    assert segments[0]["timeline_start"] == 1.0
    assert segments[0]["timeline_end"] == 2.5


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


def test_an_anchored_moment_lands_where_its_block_offset_and_anchor_say():
    # (anchor fields, timeline_start, timeline_end)
    cases = [
        ({"block": 1, "duration_seconds": 2.0}, 4.0, 6.0),
        ({"block": 2, "offset_seconds": 0.5, "duration_seconds": 1.0},
         12.5, 13.5),
        ({"block": 0, "anchor": "end", "offset_seconds": -1.0,
          "duration_seconds": 1.0}, 3.0, 4.0),
    ]
    for fields, start, end in cases:
        segments = plan_timed_text_segments(
            _declaration({"text": "EPISODE 001", "color": "#fff",
                          **fields, **_LOOK}),
            spine_structure=_SPINE, width=1080, height=1920)
        assert len(segments) == 1, fields
        assert (segments[0]["timeline_start"],
                segments[0]["timeline_end"]) == (start, end), fields
    assert segments[0]["total_frames"] == 30


def test_each_malformed_timing_raises_by_name():
    """A dropped or defaulted timing puts the card in the wrong place."""
    x = {"text": "X", "color": "#fff"}
    cases = [  # (moment, spine, needle)
        (_moment(start_frame=1800, duration_frames=60), _SPINE,
         "reaches no picture"),  # the 4th Wall card's actual defect
        ({**x, "block": 99, "duration_seconds": 1.0}, _SPINE,
         "which is not in"),
        ({**x, "block": 1, "duration_seconds": 1.0}, None,
         "no spine was supplied"),
        ({**x, "block": 1, "duration_seconds": 1.0, "start_frame": 30},
         _SPINE, "not both"),
        (x, None, "neither way"),
        ({**x, "block": 1}, _SPINE, "duration_seconds"),
        ({**x, "block": 0, "offset_seconds": -2.0, "duration_seconds": 1.0},
         _SPINE, "before"),
        ({**x, "block": 1, "anchor": "middle", "duration_seconds": 1.0},
         _SPINE, "anchor"),
    ]
    for moment, spine, needle in cases:
        with pytest.raises(TimedTextDeclarationError) as exc:
            plan_timed_text_segments(_declaration(moment),
                                     spine_structure=spine,
                                     width=1080, height=1920)
        assert needle in " ".join(str(exc.value).split()), (moment, exc.value)
    assert "[0, 1, 2]" in str(
        pytest.raises(TimedTextDeclarationError, plan_timed_text_segments,
                      _declaration(cases[1][0]), spine_structure=_SPINE,
                      width=1080, height=1920).value)


MALFORMED_MOMENTS = [
    ({"text": ""}, "empty text"),
    ({"color": None}, "missing"),
    ({"start_frame": -5}, "non-negative integers"),
    ({"duration_frames": 0}, "appears in no frame"),
    ({"duration_frames": 1.5}, "appears in no frame"),
    ({"fade_in_frames": -1}, "non-negative"),
    ({"fade_in_frames": 20, "fade_out_frames": 20}, "full opacity"),
    ({"y": 1.4}, "outside the frame"),
    ({"x": "left"}, "between 0 and 1"),
    # A look that states nothing renderable is malformed, not defaulted.
    ({"font_size": 0}, "positive number"),
    ({"font_size": -12}, "positive number"),
    ({"font_size": "big"}, "positive number"),
    ({"text_shadow": None}, "omits its look"),
    ({"text_shadow": 12}, "text-shadow string"),
]


def test_a_malformed_moment_raises():
    """Malformed raises; it is never dropped - a dropped declaration is a
    card the editor believes shipped (the rule bookends.py follows)."""
    for bad, needle in MALFORMED_MOMENTS:
        with pytest.raises(TimedTextDeclarationError) as exc:
            plan_timed_text_segments(_declaration(_moment(**bad)),
                                     width=1080, height=1920)
        assert needle in str(exc.value), (bad, exc.value)


def test_zero_fades_are_legal():
    """They crashed the composition; they are a legitimate declaration.

    `interpolate` needs a strictly increasing input range, so a moment
    with no fade produced [0,0,30,30] and threw, killing the render for
    every moment in the segment. See momentOpacity in the composition.
    """
    segments = plan_timed_text_segments(_declaration(
        _moment(fade_in_frames=0, fade_out_frames=0)), width=1080, height=1920)
    assert segments[0]["props"]["moments"][0]["fadeInFrames"] == 0


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


def test_a_typeface_that_would_not_draw_raises():
    """The family is artwork the project declares, and it must really draw
    the glyphs: an omitted family, or an unbundled one with no staged
    file (Chromium substitutes and the frames look fine), raises."""
    with pytest.raises(TimedTextDeclarationError, match="font_family"):
        generate_timed_text_overlay_props({
            "timed_text_overlay": {"moments": [dict(_moment(), text="X")]},
        }, width=1080, height=1920)
    with pytest.raises(TimedTextDeclarationError, match="Nanum Pen Script"):
        plan_timed_text_segments(
            _declaration(_moment(), font_family="Nanum Pen Script"),
            width=1080, height=1920)


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


def test_a_malformed_project_declaration_raises(tmp_path):
    """A dropped declaration is a card the editor believes shipped."""
    from library.tools.timed_text_overlay import resolve_declaration
    for body, needle in (
        ("effect: not-a-mapping\n", "not a mapping"),
        ("effect:\n  timed_text_overlay: [1, 2]\n", "not a declaration"),
    ):
        with pytest.raises(TimedTextDeclarationError, match=needle):
            resolve_declaration({}, _write_project(tmp_path, body))


# ─────────────────────────────────────────────────────────
# The typeface must be one that really draws the glyphs
# ─────────────────────────────────────────────────────────

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


def test_a_rendered_segment_is_carried_as_the_overlay_or_raises(tmp_path,
                                                               monkeypatch):
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

    # A failed carry RAISES: a warning would ship the episode without the
    # text, or a ProRes file stamped as the overlay codec.
    from library.tools.timed_text_render import TimedTextRenderError

    render_mod, out_path, _ = _render_one_with(
        monkeypatch, tmp_path, carry={
            "error": "transcode failed: no ffmpeg here", "changed": False,
            "before": 100, "after": 100})
    with pytest.raises(TimedTextRenderError, match="carried"):
        render_mod._render_one(
            "timed_text_000", out_path,
            str(tmp_path / "timed_text_000_props.json"), str(tmp_path))
