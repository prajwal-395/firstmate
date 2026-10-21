"""A span segment that carries a PROJECT-OWNED file, and the two moves it declares.

The defect this names
---------------------
The break test (firstmate home, ``data/vep-ren-breaktest/findings.md``
§10.4) took the captain's flagship ask - *"replace everything visual with
a custom, high-fidelity, full-screen Vox-style animated explainer over
the existing audio"* - through the sanctioned route, rendered it, and
looked at the frames.  The engine's full-frame composition drew **type
on a ground**: no illustration, no highlighter stroke on the key phrase,
and no camera move.  The gap was exact and it was in the DECLARATION:

    the vocabulary had the `asset` axis, and `full_frame_clip` was the
    project-authored file the engine places verbatim - but a
    `full_frame_span` SEGMENT could name neither.

So an explainer segment could not carry illustrated artwork.  A segment
that wrote ``asset:`` had it silently ignored: the engine drew the
words and dropped the picture the project had rendered, and the segment
that was supposed to visualise an IDEA typed it instead.  The two other
gaps the break test named - no highlighter stroke and no push/pan of the
frame - were the same shape: the composition had no way to draw them and
the declaration had no way to ask.

What this file proves
---------------------
- a segment that names a project file is PLACED with that file, for its
  own window, and the engine draws nothing at all for it;
- every declaration that would put two pictures on one segment is
  refused BY NAME, because the engine authors no artwork (AGENTS.md 14);
- a segment that draws type may declare a highlighter stroke and a
  camera move, and both reach the renderer with no engine default
  anywhere in them (AGENTS.md 10.5);
- the renderer draws them: the stroke marks the word that owns the
  clock, and the camera travels from the neutral frame to the declared
  one across the segment.

The plan half runs anywhere.  The renderer half executes the
composition's own functions under node, the way
``test_full_frame_element.py`` executes the cue clock's.
"""
from __future__ import annotations

import os
import shutil
import subprocess

import pytest

from library.tools import full_frame_element as ffe

# ── The delivery frame a full-frame element IS ────────────────────────

FPS = 24000 / 1001
WIDTH = 1080
HEIGHT = 1920

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))))
COMPONENT = os.path.join(
    PROJECT_ROOT, "remotion-subtitles", "src", "compositions",
    "FullFrameCard", "index.tsx")
TSC_DIR = os.path.join(
    PROJECT_ROOT, "remotion-subtitles", "node_modules", "typescript")

node_available = pytest.mark.skipif(
    shutil.which("node") is None or not os.path.isdir(TSC_DIR),
    reason=(
        "needs node and remotion-subtitles/node_modules/typescript. Runs "
        "anywhere the Remotion dev deps are installed - the same "
        "environment every other delivery test in this suite needs."
    ),
)

ffmpeg_available = pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason=(
        "needs ffmpeg and ffprobe: a segment's asset is MEASURED, never "
        "assumed, and the measurement is ffprobe's."
    ),
)


# ── A project-owned file, rendered by the project ─────────────────────


def _asset_file(tmp_path, name="scene_03", seconds=1.0, fps="24000/1001",
                width=WIDTH, height=HEIGHT) -> str:
    """A file the PROJECT rendered: the delivery frame, at the reel's rate."""
    path = tmp_path / f"{name}.mp4"
    subprocess.run(
        ["ffmpeg", "-v", "error", "-f", "lavfi",
         "-i", f"color=c=0x0B1B2B:s={width}x{height}:r={fps}",
         "-t", str(seconds), "-c:v", "libx264", "-pix_fmt", "yuv420p",
         "-y", str(path)],
        check=True, capture_output=True, encoding="utf-8", timeout=120)
    return str(path)


# ── The declaration ───────────────────────────────────────────────────

RANGES = [(10.0, 11.0)]
"""One keep range of one second, so a segment's window is the range."""

WINDOW = [10.0, 11.0]


def _transcript():
    """Three timed words inside the range, so a segment can quote them."""
    return {"segments": [{
        "speaker": "SpeakerOne",
        "timeline_start": 10.0, "timeline_end": 11.0,
        "text": "connect the dots",
        "words": [
            {"word": word, "start": 10.0 + i * 0.25,
             "end": 10.2 + i * 0.25, "timed": True}
            for i, word in enumerate(["connect", "the", "dots"])
        ],
    }]}


class _Moment:
    number = 3
    speakers = ("SpeakerOne",)


def _facts():
    return ffe.ReelFacts.from_moment(
        _Moment(), RANGES, _transcript(), opening_seconds=3.0)


def _span(segment: dict | None = None, **span_overrides) -> dict:
    """A span declaration carrying ONE segment."""
    one = {
        "window": list(WINDOW),
        "runs": [{"bind": "range_line", "type_role": "display",
                  "colour": "#FFFFFF"}],
    }
    one.update(segment or {})
    declaration = {
        "element": "full_frame_span",
        "placement": "span",
        "background": "#0B1B2B",
        "entrance": "draw",
        "exit": "fade",
        "font_family": "Montserrat",
        "segments": [one],
    }
    declaration.update(span_overrides)
    return {"full_frame_elements": [declaration]}


def _plan(declaration, project_folder, facts=None):
    return ffe.plan_reel_cards(
        ffe.declared_elements(declaration),
        facts or _facts(), 0, FPS,
        width=WIDTH, height=HEIGHT,
        project_folder=str(project_folder),
        ranges=RANGES, transcript=_transcript())


def test_a_span_for_an_unrelated_master_window_is_not_applied_to_this_reel():
    """A project can declare a span for one source passage and build a
    different reel. The source window has no picture to replace here, so
    it contributes no card to this reel."""
    declaration = _span()
    declaration["full_frame_elements"][0]["segments"][0]["window"] = [
        890.02, 890.84]

    assert _plan(declaration, None) == []


def test_a_span_window_that_partly_touches_this_reel_still_refuses():
    """Skipping an unrelated span must not hide a bad cut boundary."""
    declaration = _span()
    declaration["full_frame_elements"][0]["segments"][0]["window"] = [
        10.5, 11.5]

    with pytest.raises(ffe.FullFrameDeclarationError,
                       match="no keep range contains"):
        _plan(declaration, None)


# ── The asset: a segment that carries the project's own picture ───────


@ffmpeg_available
def test_a_segment_that_names_a_project_file_carries_that_file(tmp_path):
    """THE DEFECT.  A segment may name a PROJECT-OWNED file, and the
    plan places it - the engine draws nothing for that segment at all.

    Before this, `asset:` on a segment was silently ignored: the engine
    rendered the segment's type and the project's illustration was
    nowhere, which is how "the words, typed" passed for "the idea,
    drawn".
    """
    asset = _asset_file(tmp_path)
    cards = _plan(_span({"asset": asset, "runs": None}), tmp_path)

    assert len(cards) == 1
    card = cards[0]
    # The file IS the segment's picture: the plan carries the project's
    # own path, not a render of it.
    assert card.rendered_path == asset
    assert card.props == {}, (
        "the engine drew something for a segment that names a project "
        "file: the project rendered the frame, and the engine authors no "
        "artwork (AGENTS.md 14)")
    # Timed to its words: the segment's own window, in the reel's own
    # frames - the same arithmetic every other segment is laid down in.
    assert card.duration_frames == 24
    assert card.reel_start_frame == 0
    assert card.element == "full_frame_span"
    # Identified on the timeline by the file it places, the way a
    # `full_frame_clip` is: the timeline carries the project's own name.
    assert card.render_name == "scene_03"


@ffmpeg_available
def test_the_asset_is_never_rendered_by_the_engine(tmp_path, monkeypatch):
    """The render step passes it through: the file is already on disk.

    A segment that names a project file arrives with its file, so the
    batch renderer has nothing to draw for it - and a renderer that
    drew one anyway would overwrite the client's picture with the
    engine's reading of it (AGENTS.md 13).
    """
    asset = _asset_file(tmp_path)
    cards = _plan(_span({"asset": asset, "runs": None}), tmp_path)

    def _no_render(*args, **kwargs):  # pragma: no cover - must not run
        raise AssertionError("the engine rendered a project-owned file")

    monkeypatch.setattr(ffe, "render_batch", _no_render)
    rendered = ffe.render_reel_cards(cards, str(tmp_path), str(tmp_path))
    assert rendered[0].rendered_path == asset


@ffmpeg_available
def test_an_asset_segment_may_carry_no_runs_and_no_second_picture(tmp_path):
    """Two pictures on the same seconds is an overlap, not a composite."""
    asset = _asset_file(tmp_path)
    with pytest.raises(ffe.FullFrameDeclarationError, match="runs"):
        ffe.declared_elements(_span({
            "asset": asset,
            "runs": [{"text": "connect the dots", "type_role": "display",
                      "colour": "#FFFFFF"}],
        }))


@ffmpeg_available
def test_a_segment_that_names_a_file_may_carry_no_ground_or_mark(tmp_path):
    """The file draws its own ground; a mark above nothing has no reader."""
    asset = _asset_file(tmp_path)
    for field, value in (("background", "#FFFFFF"), ("entrance", "fade"),
                          ("image", "logo.png")):
        with pytest.raises(ffe.FullFrameDeclarationError,
                           match=field):
            ffe.declared_elements(_span({
                "asset": asset, "runs": None, field: value}))


@ffmpeg_available
def test_a_segment_that_places_a_file_may_not_also_ask_for_a_move(tmp_path):
    """No reader, so refused: the file is placed verbatim."""
    asset = _asset_file(tmp_path)
    with pytest.raises(ffe.FullFrameDeclarationError, match="camera"):
        ffe.declared_elements(_span({
            "asset": asset, "runs": None,
            "camera": {"move": "push", "to": 1.1}}))
    with pytest.raises(ffe.FullFrameDeclarationError, match="highlighter"):
        ffe.declared_elements(_span({
            "asset": asset, "runs": None,
            "highlighter": {"colour": "#F2C94C", "width": 22}}))


@ffmpeg_available
def test_a_file_that_is_not_the_delivery_frame_is_refused(tmp_path):
    """Fitting it would letterbox the scene or crop it - taste."""
    asset = _asset_file(tmp_path, "small", width=540, height=960)
    with pytest.raises(ffe.FullFrameDeclarationError,
                       match="delivery|reel delivers"):
        _plan(_span({"asset": asset, "runs": None}), tmp_path)


@ffmpeg_available
def test_a_file_at_another_rate_is_refused(tmp_path):
    """A conform would not land on the window the segment chose."""
    asset = _asset_file(tmp_path, "fast", seconds=1.0, fps="30")
    with pytest.raises(ffe.FullFrameDeclarationError, match="rate"):
        _plan(_span({"asset": asset, "runs": None}), tmp_path)


@ffmpeg_available
def test_a_file_that_ends_before_the_segment_does_is_refused(tmp_path):
    """The tail of the segment would be a hole."""
    asset = _asset_file(tmp_path, "short", seconds=0.4)
    with pytest.raises(ffe.FullFrameDeclarationError, match="before the segment"):
        _plan(_span({"asset": asset, "runs": None}), tmp_path)


@ffmpeg_available
def test_two_segments_may_not_place_one_file(tmp_path):
    """The timeline carries the file, so one file is one identity."""
    asset = _asset_file(tmp_path)
    declaration = _span({"asset": asset, "runs": None,
                         "window": [10.0, 10.5]})
    declaration["full_frame_elements"][0]["segments"].append({
        "window": [10.5, 11.0], "asset": asset})
    with pytest.raises(ffe.FullFrameDeclarationError,
                       match="already places"):
        _plan(declaration, tmp_path)


@ffmpeg_available
def test_a_file_that_is_not_on_disk_is_refused(tmp_path):
    """A declaration that renders nothing is indistinguishable from none."""
    with pytest.raises(ffe.FullFrameDeclarationError, match="not a file"):
        _plan(_span({"asset": "art/nowhere.mov", "runs": None}), tmp_path)


# ── The highlighter stroke ────────────────────────────────────────────


def test_a_segment_may_declare_a_highlighter_and_it_reaches_the_renderer():
    """Declared per segment, and declared all the way down: the colour
    and the weight are the project's, and the engine holds none."""
    declaration = _span({
        "highlighter": {"colour": "#F2C94C", "width": 22}},
        word_sync=True)
    cards = _plan(declaration, None)
    assert cards[0].props["highlighter"] == {"colour": "#F2C94C", "width": 22}


def test_a_segment_that_declares_no_highlighter_carries_none():
    """Absent, not defaulted: no stroke, no weight nobody chose."""
    cards = _plan(_span(), None)
    assert "highlighter" not in cards[0].props


MALFORMED_HIGHLIGHTERS = [
    ({"width": 22}, "colour"),
    ({"colour": "#F2C94C"}, "width"),
    ({"colour": "#F2C94C", "width": 0}, "width"),
    ({"colour": "#F2C94C", "width": -4}, "width"),
    ({"colour": "", "width": 22}, "colour"),
    ("#F2C94C", "mapping"),
]


def test_a_malformed_highlighter_is_refused_by_name():
    for highlighter, expect in MALFORMED_HIGHLIGHTERS:
        with pytest.raises(ffe.FullFrameDeclarationError, match=expect):
            ffe.declared_elements(_span({"highlighter": highlighter}))
            pytest.fail(f"accepted {highlighter}")


def test_a_highlighter_needs_a_word_clock():
    """The stroke marks the word that OWNS the clock."""
    with pytest.raises(ffe.FullFrameDeclarationError, match="word_sync"):
        ffe.declared_elements(_span({
            "highlighter": {"colour": "#F2C94C", "width": 22}}))


def test_a_highlighter_cannot_ride_a_typewriter():
    """A typewriter is a reveal, not a per-word drawing."""
    with pytest.raises(ffe.FullFrameDeclarationError, match="typewriter"):
        ffe.declared_elements(_span({
            "highlighter": {"colour": "#F2C94C", "width": 22}},
            entrance="typewriter", word_sync=True))


# ── The camera ────────────────────────────────────────────────────────


def test_a_segment_may_declare_a_camera_and_it_reaches_the_renderer():
    cards = _plan(_span({"camera": {"move": "push", "to": 1.08}}), None)
    assert cards[0].props["camera"] == {"move": "push", "to": 1.08}
    cards = _plan(_span({"camera": {"move": "pan", "x": 0.02, "y": -0.03}}),
                  None)
    assert cards[0].props["camera"] == {"move": "pan", "x": 0.02, "y": -0.03}


def test_a_segment_that_declares_no_camera_carries_none():
    cards = _plan(_span(), None)
    assert "camera" not in cards[0].props


MALFORMED_CAMERAS = [
    ({"move": "zoom"}, "camera moves"),
    ({"move": "push"}, "push"),
    ({"move": "push", "to": 1.0}, "push"),
    ({"move": "push", "to": 0.9}, "push"),
    ({"move": "push", "to": 1.1, "x": 0.1}, "push"),
    ({"move": "pan"}, "offset"),
    ({"move": "pan", "x": 0.0, "y": 0.0}, "offset"),
    ({"move": "pan", "x": 0.1, "to": 1.1}, "pan"),
    ({"move": "pan", "x": "left"}, "number"),
    ("push", "mapping"),
]


def test_a_malformed_camera_is_refused_by_name():
    for camera, expect in MALFORMED_CAMERAS:
        with pytest.raises(ffe.FullFrameDeclarationError, match=expect):
            ffe.declared_elements(_span({"camera": camera}))
            pytest.fail(f"accepted {camera}")


def test_the_camera_states_no_magnitude_of_its_own():
    """No default scale, no default offset, no default move.

    AGENTS.md 10.5: a creative value is DECIDED, and the decision says
    what decided it.  A camera the engine picked is a camera nobody
    asked for.
    """
    cards = _plan(_span(), None)
    assert "camera" not in cards[0].props
    with pytest.raises(ffe.FullFrameDeclarationError):
        ffe.declared_elements(_span({"camera": {"move": "push"}}))


# ── The renderer half: the component's own functions, executed ────────

HARNESS = r"""
const Module = require("module");
const ts = require(process.env.TSC_PATH);
const fs = require("fs");

const src = fs.readFileSync(process.env.COMPONENT_PATH, "utf8");
const { outputText } = ts.transpileModule(src, {
  compilerOptions: {
    module: ts.ModuleKind.CommonJS,
    target: ts.ScriptTarget.ES2019,
    jsx: ts.JsxEmit.React,
  },
});

const Easing = {
  cubic: (t) => t * t * t,
  linear: (t) => t,
  out: (fn) => (t) => 1 - fn(1 - t),
  in: (fn) => fn,
  inOut: (fn) => (t) => t,
};
const remotionStub = {
  AbsoluteFill: () => null,
  Easing,
  Img: () => null,
  staticFile: (p) => p,
  useCurrentFrame: () => 0,
};
const reactStub = { Fragment: Symbol("F"), createElement: () => null };
const schemaStub = new Proxy(function () {}, {
  apply: () => schemaStub,
  get: () => schemaStub,
});
const zodStub = new Proxy({}, { get: () => schemaStub });
const stubOf = (id) => {
  if (id === "react") return reactStub;
  if (id === "remotion") return remotionStub;
  if (id === "zod") return { z: zodStub };
  if (id.endsWith("fonts")) return { loadBundledFonts: () => {}, loadProjectFont: () => {} };
  if (id.endsWith("MotionGraphics"))
    return {
      elementOpacity: () => 1,
      entranceTransform: () => ({}),
      exitTransform: () => ({}),
      rampFrames: () => 0,
      typewriterCursorOn: () => false,
      typewriterShown: () => 1,
      typewriterSplit: (lens) => ({ shown: lens, cursorRun: -1 }),
    };
  throw new Error("unexpected import " + id);
};

const m = new Module("FullFrameCard", null);
m.require = (id) => stubOf(id);
m._compile(outputText, process.env.COMPONENT_PATH);
const f = m.exports;

const check = (name, cond, extra) => {
  if (!cond) {
    console.error("FAIL " + name + (extra === undefined ? "" : " :: " + extra));
    process.exitCode = 1;
  } else {
    console.log("PASS " + name);
  }
};

// ── The camera ────────────────────────────────────────────────────────
// 61 frames, so the clock divides: the last frame is the declared
// state and the middle frame is halfway there.
const DURATION = 61;
const W = 1080;
const H = 1920;

check("no camera draws no transform", f.cameraTransform(0, 30, DURATION, undefined, W) === "");
check("no camera draws no transform mid-segment", f.cameraTransform(30, 30, DURATION, undefined, W) === "");

const push = { move: "push", to: 1.1 };
check("a push starts at the neutral scale", f.cameraTransform(0, 30, DURATION, push, W) === "scale(1)");
check("a push is halfway at the middle frame", f.cameraTransform(30, 30, DURATION, push, W) === "scale(1.05)", f.cameraTransform(30, 30, DURATION, push, W));
check("a push reaches the declared scale on the last frame", f.cameraTransform(60, 30, DURATION, push, W) === "scale(1.1)", f.cameraTransform(60, 30, DURATION, push, W));

const pan = { move: "pan", x: 0.25, y: -0.25 };
check("a pan starts at no offset", f.cameraTransform(0, 30, DURATION, pan, W) === "translate(0px, 0px)", f.cameraTransform(0, 30, DURATION, pan, W));
check("a pan moves the content the other way, by a fraction of the delivery width", f.cameraTransform(30, 30, DURATION, pan, W) === "translate(-135px, 135px)", f.cameraTransform(30, 30, DURATION, pan, W));
check("a pan reaches its offset on the last frame", f.cameraTransform(60, 30, DURATION, pan, W) === "translate(-270px, 270px)", f.cameraTransform(60, 30, DURATION, pan, W));

// ── The highlighter ───────────────────────────────────────────────────
// Two words: ALPHA [0.0, 0.25], BETA [1.0, 1.25] at 30 fps.
const cues = [
  { word: "ALPHA", start: 0.0, end: 0.25, chars: 5 },
  { word: "BETA", start: 1.0, end: 1.25, chars: 10 },
];
const stroke = { colour: "#F2C94C", width: 22 };

check("no highlighter strokes no word", f.highlightedWordIndex(0, 30, cues, undefined) === -1);
check("the stroke lands on the word that owns the clock", f.highlightedWordIndex(30, 30, cues, stroke) === 1);
check("the stroke owns nothing between words", f.highlightedWordIndex(29, 30, cues, stroke) === -1);
check("the stroke owns nothing before the first word", f.highlightedWordIndex(0, 30, cues, stroke) === 0);

const background = f.highlighterBackground(stroke);
check("the stroke is the declared colour", background.includes("#F2C94C"), background);
check("the stroke is the declared weight", background.includes("calc(50% - 11px)") && background.includes("calc(50% + 11px)"), background);
check("the stroke is a band and not a fill", background.includes("transparent"), background);
"""


@node_available
def test_the_renderer_draws_the_stroke_and_the_camera(tmp_path):
    """The component's own functions, executed: no render, no eyeballing.

    The camera travels from the neutral frame to the declared one across
    the segment, and the stroke marks the word that owns the clock.
    """
    harness = tmp_path / "span_asset_harness.cjs"
    harness.write_text(HARNESS, encoding="utf-8")
    env = dict(os.environ, TSC_PATH=TSC_DIR, COMPONENT_PATH=COMPONENT)
    result = subprocess.run(
        ["node", str(harness)], capture_output=True, text=True,
        encoding="utf-8", timeout=120, check=False, env=env)
    assert result.returncode == 0, (
        "the harness failed:\n" + result.stdout[-3000:]
        + result.stderr[-2000:])
    assert "FAIL" not in result.stdout
    for name in ("a push reaches the declared scale on the last frame",
                 "a pan moves the content the other way, by a fraction of the delivery width",
                 "the stroke lands on the word that owns the clock",
                 "the stroke is the declared colour"):
        assert f"PASS {name}" in result.stdout, (
            f"harness did not report {name!r}:\n{result.stdout[-2000:]}")
