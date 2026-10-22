"""Renderer-neutral graphics scene graph and animation IR.

The contract: the IR is the language, engines are adapters. Every engine
that renders a scene produces the same picture from the same scene.json,
because the evaluator in this module is the reference for what any
animated property is at any frame.

A scene is described by a versioned JSON document (scene.json) with a
JSON Schema file (scene.schema.json) so a non-Python renderer can consume
it. Times are seconds throughout. The scene carries an explicit fps and
canvas size. Colours, fonts and assets are referenced as project data,
never baked-in defaults.
"""

from .easing import apply_easing
from .evaluator import evaluate, evaluate_property
from .lowering import (
    AssetNotDrawn,
    lower_brand_motion,
    lower_full_frame_card,
    lower_motion_graphics,
    lower_post_header,
    lower_staged_scene,
    lower_subtitle_overlay,
    lower_timed_text_overlay,
)
from .model import (
    Animation,
    AudioContent,
    Bounds,
    CubicBezierEasing,
    EaseEasing,
    EffectContent,
    FilterContent,
    ImageContent,
    LinearEasing,
    Node,
    NodeType,
    Scene,
    ShaderContent,
    ShapeContent,
    SpringEasing,
    StepsEasing,
    SVGContent,
    TextContent,
    Timing,
    Transform,
    VideoContent,
)
from .serialization import (
    SCHEMA_VERSION,
    deserialize,
    read_scene,
    serialize,
    write_schema,
    write_scene,
)

__all__ = [
    "Animation",
    "AssetNotDrawn",
    "AudioContent",
    "Bounds",
    "CubicBezierEasing",
    "EaseEasing",
    "EffectContent",
    "FilterContent",
    "ImageContent",
    "LinearEasing",
    "Node",
    "NodeType",
    "SCHEMA_VERSION",
    "Scene",
    "ShaderContent",
    "ShapeContent",
    "SpringEasing",
    "StepsEasing",
    "SVGContent",
    "TextContent",
    "Timing",
    "Transform",
    "VideoContent",
    "apply_easing",
    "deserialize",
    "evaluate",
    "evaluate_property",
    "lower_brand_motion",
    "lower_full_frame_card",
    "lower_motion_graphics",
    "lower_post_header",
    "lower_staged_scene",
    "lower_subtitle_overlay",
    "lower_timed_text_overlay",
    "read_scene",
    "serialize",
    "write_schema",
    "write_scene",
]
