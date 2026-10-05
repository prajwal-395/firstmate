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

    ``power=1`` is linear. ``power=2`` is the quadratic out the subtitle
    entry scale draws (GSAP ``power1.out``). ``power=3`` is the cubic out
    the word-cue rise draws (GSAP ``power2.out``).
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


class ShapeContent(BaseModel):
    """The content of a Shape node."""

    shape: str
    fill: str | None = None
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
    fill: str | None = None


class ShaderContent(BaseModel):
    """The content of a Shader node. Uniforms are renderer-neutral names."""

    uniforms: dict[str, float] = Field(default_factory=dict)


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
    """One node in the scene graph."""

    id: str
    type: NodeType
    transform: Transform | None = None
    opacity: float = 1.0
    bounds: Bounds | None = None
    z_index: int = 0
    mask: str | None = None
    clip: str | None = None
    blend: str = "normal"
    timing: Timing | None = None
    animations: list[Animation] = Field(default_factory=list)
    text: TextContent | None = None
    shape: ShapeContent | None = None
    image: ImageContent | None = None
    svg: SVGContent | None = None
    shader: ShaderContent | None = None
    filter: FilterContent | None = None
    effect: EffectContent | None = None
    audio: AudioContent | None = None
    children: list[str] = Field(default_factory=list)


class Scene(BaseModel):
    """A complete scene: the renderer-neutral description of one composition."""

    version: int = 1
    fps: float
    width: int
    height: int
    duration: float
    nodes: list[Node] = Field(default_factory=list)
