"""Renderer-neutral graphics scene graph and animation IR.

Ren owns the animation language. FFrames, HyperFrames, Remotion, Fusion and
any other backend are execution adapters that lower to and render from this
IR. An entrance is never a GSAP tween, a CSS transform or an FFrames
``timeline!`` - it is a renderer-neutral animation such as::

    Animation: target: title, property: transform.scale,
               from: 0.92, to: 1.0, start: 3.200, duration: 0.420,
               easing: {kind: spring, stiffness: 80, damping: 15, mass: 0.8}

The contract: the IR is the language, engines are adapters. Every engine
that renders a scene produces the same picture from the same scene.json,
because the evaluator in this module is the reference for what any animated
property is at any frame.

Times are seconds throughout. The scene carries an explicit fps and canvas
size. Colours, fonts and assets are referenced as project data (file paths),
never baked-in defaults - the engine ships no palette, no typeface and no
artwork.
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Literal, Union

from pydantic import BaseModel, Field


class NodeType(str, Enum):
    """The node types the IR can express.

    The five standard compositions exercise Scene, Ground, Group, Text,
    Shape, Image and SVG. Camera, Video, Shader, Mask, Filter, Effect and
    Audio are modelled so a future composition needs no IR extension.
    """

    SCENE = "scene"
    CAMERA = "camera"
    GROUND = "ground"
    GROUP = "group"
    TEXT = "text"
    SHAPE = "shape"
    IMAGE = "image"
    VIDEO = "video"
    SVG = "svg"
    SHADER = "shader"
    MASK = "mask"
    FILTER = "filter"
    EFFECT = "effect"
    AUDIO = "audio"
    HTML = "html"


class EasingKind(str, Enum):
    """The five easing kinds the IR supports."""

    LINEAR = "linear"
    EASE = "ease"
    CUBIC_BEZIER = "cubic_bezier"
    SPRING = "spring"
    STEPS = "steps"


class LinearEasing(BaseModel):
    """No easing: progress equals time."""

    kind: Literal[EasingKind.LINEAR] = EasingKind.LINEAR


class EaseEasing(BaseModel):
    """Power ease-out: ``1 - (1 - t) ** power``.

    ``power=1`` is linear. ``power=3`` is the cubic out
    the word-cue rise draws (GSAP ``power2.out``). The subtitle entry
    scale does NOT use this kind: the template draws Remotion
    ``Easing.out(Easing.ease)``, which the lowering carries as a
    ``CubicBezierEasing`` (``cubic-bezier(0, 0, 0.58, 1)``) - a power
    ease-out sits ~0.06 off mid-ramp.
    """

    kind: Literal[EasingKind.EASE] = EasingKind.EASE
    power: float = 2.0


class CubicBezierEasing(BaseModel):
    """A cubic bezier easing, the CSS ``cubic-bezier()`` function."""

    kind: Literal[EasingKind.CUBIC_BEZIER] = EasingKind.CUBIC_BEZIER
    x1: float
    y1: float
    x2: float
    y2: float


class SpringEasing(BaseModel):
    """Spring physics easing.

    The same analytic damped-harmonic-oscillator integrator Remotion's
    ``spring()`` and HyperFrames' ``_spring_calculation`` use, so every
    engine draws the same curve from the same parameters.
    """

    kind: Literal[EasingKind.SPRING] = EasingKind.SPRING
    stiffness: float
    damping: float
    mass: float


class StepsEasing(BaseModel):
    """Stepped easing: the value holds for ``steps`` equal intervals.

    ``direction="end"`` steps at the end of each interval (the value
    changes on the frame). ``direction="start"`` steps at the beginning.
    """

    kind: Literal[EasingKind.STEPS] = EasingKind.STEPS
    steps: int
    direction: Literal["start", "end"] = "end"


Easing = Union[LinearEasing, EaseEasing, CubicBezierEasing, SpringEasing, StepsEasing]
"""Any easing configuration."""


class Transform(BaseModel):
    """A 2D transform, renderer-neutral.

    Translate is in pixels of the delivery frame. Rotate is degrees.
    Scale is a multiplier (1.0 is rest). Skew is degrees.
    """

    translate: list[float] | None = None
    rotate: float | None = None
    scale: list[float] | None = None
    skew: list[float] | None = None


class Bounds(BaseModel):
    """A node's bounding box in delivery-frame pixels."""

    x: float
    y: float
    width: float
    height: float


class TextContent(BaseModel):
    """The content of a Text node.

    ``font_file`` is the path to the font file, so every engine draws the
    same face, at ``font_weight``. ``font_family`` is the family name for
    engines that need one. Colours are project data, never defaults.

    ``text`` is already broken into lines (``\\n``) by the lowering; the
    node's ``bounds`` is its CSS box and each line is aligned inside its
    width per ``text_align``. ``line_height`` is in ems; ``None`` is CSS
    ``normal``. ``text_transform`` and ``letter_spacing`` (pixels) are
    CSS's. ``text_shadow`` is a CSS ``text-shadow`` list - an outline is
    expressed the way the compositions express it, as zero-blur offset
    shadows.

    ``runs`` carries the node's inline runs when the text is styled run by
    run (StagedScene's model): each run its own size, weight, colour,
    letter-spacing and reveal, sharing baselines. ``layout`` is ``"native"``
    (the engine lays the text out with CSS's rules) or ``"browser"`` (a
    browser measured the boxes and the engine draws at those positions).
    """

    text: str
    font_family: str | None = None
    font_file: str | None = None
    font_size: float | None = None
    font_weight: float | None = None
    color: str | None = None
    text_align: str | None = None
    line_height: float | None = None
    letter_spacing: float | None = None
    text_transform: str | None = None
    text_shadow: str | None = None
    runs: list[TextRun] = Field(default_factory=list)
    layout: str | None = None
    reveal_ramp: float | None = None
    """Seconds a run's reveal fade takes, ending on its ``reveal_at``."""


class ShapeContent(BaseModel):
    """The content of a Shape node.

    ``fill`` is a colour or a gradient (``Paint``); ``pattern`` is an
    image tile. ``clip`` on the node accepts ``inset()`` and ``polygon()``
    in the node's box, the CSS clip-path strings StagedScene already uses.
    """

    shape: str
    fill: Paint | None = None
    pattern: PatternFill | None = None
    stroke: str | None = None
    stroke_width: float | None = None
    path: str | None = None
    corner_radius: float | None = None


class ImageContent(BaseModel):
    """The content of an Image node. ``src`` is a file path or data URI.

    The image is contained in the node's bounds, centred, aspect kept
    (CSS ``object-fit: contain``).
    """

    src: str
    alt: str | None = None


class SVGContent(BaseModel):
    """The content of an SVG node: one path in ``viewBox`` units, scaled
    to fill the node's bounds."""

    viewBox: str | None = None
    path: str | None = None
    fill: Paint | None = None


class ShaderContent(BaseModel):
    """The content of a Shader node.

    ``program`` names a shader from a Ren-owned registry (vignette, cone,
    grain, bokeh); ``uniforms`` are renderer-neutral names, animatable as
    ``shader.<name>``; ``inputs`` are image names or ``pass:<group id>``
    for the two-pass route (render a subtree, shade its pixels).
    """

    program: str | None = None
    uniforms: dict[str, float] = Field(default_factory=dict)
    inputs: list[str] = Field(default_factory=list)


class FilterContent(BaseModel):
    """The content of a Filter node."""

    filter: str
    amount: float | None = None


class EffectContent(BaseModel):
    """The content of an Effect node."""

    effect: str
    params: dict[str, Any] = Field(default_factory=dict)


class AudioContent(BaseModel):
    """The content of an Audio reference node."""

    src: str
    start: float = 0.0
    duration: float | None = None


class PaintStop(BaseModel):
    """One gradient stop.

    Stops interpolate PREMULTIPLIED: a stop that fades to transparent keeps
    its colour and sets ``opacity: 0`` rather than passing through black,
    the way CSS ``linear-gradient`` to ``transparent`` behaves.
    """

    offset: float
    color: str
    opacity: float = 1.0


class LinearGradient(BaseModel):
    """A CSS ``linear-gradient`` over the node's box.

    ``angle`` is degrees in CSS semantics (0 up, 90 right). The gradient
    line runs through the box centre along ``(sin A, -cos A)`` and is
    ``|w sin A| + |h cos A|`` long, so the stops land where CSS puts them.
    Maps 1:1 to CSS ``linear-gradient`` and SVG ``linearGradient``.
    """

    kind: Literal["linear"]
    angle: float
    stops: list[PaintStop]


class RadialGradient(BaseModel):
    """A CSS ``radial-gradient`` ellipse, radii in percent of the box.

    Maps 1:1 to CSS ``radial-gradient(ellipse ...)`` and SVG
    ``radialGradient``.
    """

    kind: Literal["radial"]
    radius_x: float
    radius_y: float
    stops: list[PaintStop]


class ConicGradient(BaseModel):
    """A CSS ``conic-gradient``.

    usvg paints no conic gradient, so the FFrames adapter draws it with a
    shader; the browser adapters draw it with CSS ``conic-gradient``
    directly. Declared for vocabulary completeness.
    """

    kind: Literal["conic"]
    angle: float
    stops: list[PaintStop]


Paint = Union[str, LinearGradient, RadialGradient, ConicGradient]
"""A node's fill: a solid colour (``str``) or a gradient.

A plain string is a solid colour, the way every composition states one
today, so a scene that names no gradient keeps its meaning. A gradient
maps 1:1 to CSS ``linear-gradient`` / ``radial-gradient`` /
``conic-gradient`` and to SVG ``linearGradient`` / ``radialGradient``.
"""


class BlurFilter(BaseModel):
    """A CSS ``blur()``.

    ``sigma`` equals the CSS radius - measured against Chrome, not
    halved. Maps 1:1 to CSS ``filter: blur()`` and SVG ``feGaussianBlur``.
    """

    kind: Literal["blur"]
    sigma: float


class DropShadowFilter(BaseModel):
    """A CSS ``drop-shadow()``.

    ``sigma`` equals the CSS blur radius (measured: 1.0x, not b/2).
    Maps 1:1 to CSS ``filter: drop-shadow()`` and SVG ``feDropShadow``.
    """

    kind: Literal["drop_shadow"]
    dx: float
    dy: float
    sigma: float
    color: str


Filter = Union[BlurFilter, DropShadowFilter]
"""One filter in a node's ordered ``filters`` list.

An ordered list applied in CSS order: filter, then clip, then mask, then
opacity. Each filter maps 1:1 to a CSS ``filter`` function and an SVG
filter primitive.
"""


class PatternFill(BaseModel):
    """An image tile repeated across the node's box (the ground's grain).

    ``src`` is a file path or data URI. ``tile_width`` / ``tile_height``
    are pixels; ``None`` uses the image's own size. Maps 1:1 to CSS
    ``background-image: url(); background-repeat: repeat`` and SVG
    ``pattern``.
    """

    src: str
    tile_width: float | None = None
    tile_height: float | None = None
    opacity: float = 1.0


class TextRun(BaseModel):
    """One inline run of a text node's line.

    Runs share a baseline and flow inline the way StagedScene's
    ``SceneRun`` does: each carries its own size, weight, colour,
    letter-spacing and reveal time, and ``new_line`` starts a fresh line.
    Every run's face is a static instance at its declared weight, because
    usvg cannot apply a variable font's ``wght`` axis and would draw the
    default (Thin) instance.

    Maps 1:1 to a CSS inline ``<span>`` and to an SVG ``text`` run.
    """

    text: str
    font_size: float | None = None
    font_weight: float | None = None
    color: str | None = None
    letter_spacing: float | None = None
    reveal_at: float | None = None
    new_line: bool = False
    font_file: str | None = None
    font_family: str | None = None
    """The static instance's file and family for this run's weight.

    Blank until staging: ``stage_scene_fonts`` cuts one instance per
    distinct run weight and names it here, because one node-level face
    cannot carry runs at several weights. Engines read the run's face
    first and fall back to the node's.
    """


class CameraContent(BaseModel):
    """The camera over the world: ``scale rotate translate`` about the frame
    centre, and ``focus`` as a blur on the world group.

    Maps 1:1 to a CSS transform on the world element plus a ``blur()``
    filter, and to an SVG group transform plus ``feGaussianBlur``.
    """

    scale: float = 1.0
    rotate: float = 0.0
    translate: list[float] = Field(default_factory=lambda: [0.0, 0.0])
    focus: float = 0.0


class Keyframe(BaseModel):
    """One key of a track.

    ``ease`` names the segment ENDING at this key, the way StagedScene's
    keyframes do; the first key's ease is never read. Absent means linear.
    """

    at: float
    value: float
    ease: str | None = None


class Track(BaseModel):
    """A multi-key track: one property's value over time, ease per segment.

    StagedScene's ``valueAt`` semantics: held flat outside the keys, eased
    by the arriving key. Maps 1:1 to a GSAP multi-key tween and to an
    SVG/IR sampled track.
    """

    target: str
    property: str
    keys: list[Keyframe]


class HTMLContent(BaseModel):
    """A node only the browser draws: an HTML/CSS layer.

    ``engine`` is ``"browser"`` - the escape hatch for content FFrames'
    SVG has no answer for (conic gradients, arbitrary HTML, emoji). The
    browser adapters draw it as an HTML layer; FFrames refuses it by name
    rather than approximating it.
    """

    engine: str = "browser"
    html: str | None = None


class Animation(BaseModel):
    """One renderer-neutral animation.

    ``target`` is the node id. ``property`` is a dot-separated path into
    the node's state (``opacity``, ``transform.scale``, ``color``,
    ``transform.translate``, ``blur``, ``clip``, ``width``, ``height``,
    ``shader.<uniform>``, ``camera.<parameter>``). ``start`` and
    ``duration`` are seconds on the scene clock.
    """

    target: str
    property: str
    from_value: Union[float, list[float], str]
    to_value: Union[float, list[float], str]
    start: float
    duration: float
    easing: Easing


class Timing(BaseModel):
    """A node's visibility window on the scene clock.

    The engine's ``.clip`` mechanism in the HyperFrames compositions and
    the ``isLive`` filter in the Remotion compositions both window a node
    to ``[start, start + duration)``. Outside the window the node draws
    nothing.
    """

    start: float = 0.0
    duration: float | None = None


class Node(BaseModel):
    """One node in the scene graph.

    ``mask`` names a node whose alpha masks this one; ``clip`` is a CSS
    ``clip-path`` (``inset()`` or ``polygon()``) in the node's box; ``blend``
    is a CSS ``mix-blend-mode``; ``filters`` is an ordered CSS filter list
    applied filter -> clip -> mask -> opacity; ``camera`` is this node's
    camera content when it is a camera; ``tracks`` are multi-key tracks.
    """

    id: str
    type: NodeType
    transform: Transform | None = None
    opacity: float = 1.0
    bounds: Bounds | None = None
    z_index: int = 0
    mask: str | None = None
    clip: str | None = None
    blend: str = "normal"
    filters: list[Filter] = Field(default_factory=list)
    timing: Timing | None = None
    animations: list[Animation] = Field(default_factory=list)
    tracks: list[Track] = Field(default_factory=list)
    text: TextContent | None = None
    shape: ShapeContent | None = None
    image: ImageContent | None = None
    svg: SVGContent | None = None
    shader: ShaderContent | None = None
    camera: CameraContent | None = None
    filter: FilterContent | None = None
    effect: EffectContent | None = None
    audio: AudioContent | None = None
    html: HTMLContent | None = None
    children: list[str] = Field(default_factory=list)


class Scene(BaseModel):
    """A complete scene: the renderer-neutral description of one composition.

    ``hold_frames`` is the frame quantiser: every Nth frame is drawn twice
    (motion on twos when 2). It belongs at scene level, not as ``steps()``
    on each tween.
    """

    version: int = 1
    fps: float
    width: int
    height: int
    duration: float
    hold_frames: int | None = None
    nodes: list[Node] = Field(default_factory=list)
