"""The pure evaluator: sample any animated property at a frame.

This is the reference other adapters are checked against. Given a scene
and a frame number, it computes the value of every animated property by
applying the animation's easing to the elapsed time and interpolating
between the from and to values.

The evaluator is pure: it reads the scene and returns state, it does not
mutate anything and it has no dependency on any rendering engine.
"""

from __future__ import annotations

import math
from typing import Any, Union

from .easing import apply_easing
from .model import Animation, Scene

PropertyValue = Union[float, list[float], str]


def evaluate(scene: Scene, frame: float) -> dict[str, dict[str, PropertyValue]]:
    """Sample all animated properties at a given frame.

    ``frame`` is a float frame number on the scene's clock (``time * fps``).
    Returns a dict mapping ``node_id -> property -> value``. Nodes with no
    animations are omitted. When two animations target the same property,
    the one that starts later wins where they overlap.
    """
    time_seconds = frame / scene.fps if scene.fps > 0 else 0.0
    result: dict[str, dict[str, PropertyValue]] = {}
    for node in scene.nodes:
        node_state: dict[str, PropertyValue] = {}
        by_property: dict[str, list[Animation]] = {}
        for anim in node.animations:
            by_property.setdefault(anim.property, []).append(anim)
        for prop, anims in by_property.items():
            anims.sort(key=lambda a: a.start)
            node_state[prop] = _sample_animations(anims, time_seconds, scene.fps)
        if node_state:
            result[node.id] = node_state
    return result


def _sample_animations(
    anims: list[Animation], time_seconds: float, fps: float
) -> PropertyValue:
    """Sample a list of animations on the same property at a given time.

    The latest animation that has started wins. If none has started, the
    first animation's from_value is returned.
    """
    latest: Animation | None = None
    for anim in anims:
        if anim.start <= time_seconds:
            latest = anim
        else:
            break
    if latest is None:
        return anims[0].from_value
    return _sample_animation(latest, time_seconds, fps)


def _sample_animation(
    anim: Animation, time_seconds: float, fps: float
) -> PropertyValue:
    """The value of one animation at a given time."""
    if time_seconds < anim.start:
        return anim.from_value
    if time_seconds >= anim.start + anim.duration:
        return anim.to_value

    local_time = time_seconds - anim.start
    progress = apply_easing(anim.easing, local_time / anim.duration, fps)
    return _interpolate(anim.from_value, anim.to_value, progress)


def _interpolate(
    from_value: PropertyValue, to_value: PropertyValue, progress: float
) -> PropertyValue:
    """Interpolate between two values at a given progress.

    Numeric values (float or list of floats) are linearly interpolated.
    String values (colours, paths) switch at progress >= 0.5.
    """
    if isinstance(from_value, (int, float)) and isinstance(to_value, (int, float)):
        return from_value + (to_value - from_value) * progress
    if isinstance(from_value, list) and isinstance(to_value, list):
        if len(from_value) != len(to_value):
            raise ValueError(
                f"Cannot interpolate lists of different lengths: "
                f"{len(from_value)} vs {len(to_value)}"
            )
        return [
            f + (t - f) * progress
            for f, t in zip(from_value, to_value)
        ]
    if isinstance(from_value, str) and isinstance(to_value, str):
        return to_value if progress >= 0.5 else from_value
    raise ValueError(
        f"Cannot interpolate between {type(from_value).__name__} and "
        f"{type(to_value).__name__}"
    )


def evaluate_property(
    scene: Scene, node_id: str, property_path: str, frame: float
) -> PropertyValue | None:
    """Sample one property of one node at a given frame.

    Returns None when the node or property does not exist.
    """
    time_seconds = frame / scene.fps if scene.fps > 0 else 0.0
    for node in scene.nodes:
        if node.id != node_id:
            continue
        for anim in node.animations:
            if anim.property == property_path:
                return _sample_animation(anim, time_seconds, scene.fps)
        return None
    return None
