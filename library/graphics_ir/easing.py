"""Easing functions for the graphics IR.

Every easing kind the model declares has a function here that maps a
normalized time ``t`` in ``[0, 1]`` to a progress value. The progress may
overshoot ``[0, 1]`` for spring easing - that is the physics, not a bug.

The spring integrator is the same analytic damped-harmonic-oscillator
solution Remotion's ``spring()`` uses, replicated from
``library/tools/hyperframes_render.py`` so the IR evaluator and the
HyperFrames renderer draw the same curve from the same parameters.
"""

from __future__ import annotations

import math
from typing import Union

from .model import (
    CubicBezierEasing,
    EaseEasing,
    LinearEasing,
    SpringEasing,
    StepsEasing,
)

Easing = Union[LinearEasing, EaseEasing, CubicBezierEasing, SpringEasing, StepsEasing]


def apply_easing(easing: Easing, t: float, fps: float = 60.0) -> float:
    """Map a normalized time ``t`` to a progress value.

    ``t`` is clamped to ``[0, 1]`` for all kinds except spring, where the
    integrator handles the full range and may overshoot. ``fps`` is the
    scene's frame rate, used only by the spring integrator.
    """
    if isinstance(easing, LinearEasing):
        return min(1.0, max(0.0, t))
    if isinstance(easing, EaseEasing):
        return _power_out(t, easing.power)
    if isinstance(easing, CubicBezierEasing):
        return _cubic_bezier(easing.x1, easing.y1, easing.x2, easing.y2, t)
    if isinstance(easing, SpringEasing):
        return _spring_progress(t, easing.stiffness, easing.damping, easing.mass, fps)
    if isinstance(easing, StepsEasing):
        return _steps(t, easing.steps, easing.direction)
    raise TypeError(f"Unknown easing type: {type(easing).__name__}")


def _power_out(t: float, power: float) -> float:
    """Power ease-out: ``1 - (1 - t) ** power``."""
    clamped = min(1.0, max(0.0, t))
    return 1.0 - (1.0 - clamped) ** power


def _steps(t: float, steps: int, direction: str) -> float:
    """Stepped easing: the value holds for ``steps`` equal intervals."""
    clamped = min(1.0, max(0.0, t))
    if steps <= 0:
        return clamped
    if direction == "end":
        return math.floor(clamped * steps) / steps
    return math.ceil(clamped * steps) / steps


def _cubic_bezier(x1: float, y1: float, x2: float, y2: float, t: float) -> float:
    """Evaluate a cubic bezier easing at time ``t``.

    Finds the bezier parameter ``u`` whose x-component equals ``t`` via
    Newton-Raphson, then returns the y-component at ``u``.
    """
    clamped = min(1.0, max(0.0, t))
    if clamped <= 0.0:
        return 0.0
    if clamped >= 1.0:
        return 1.0

    def bezier(u: float, p1: float, p2: float) -> float:
        return 3 * (1 - u) ** 2 * u * p1 + 3 * (1 - u) * u**2 * p2 + u**3

    def bezier_derivative(u: float, p1: float, p2: float) -> float:
        return (
            3 * (1 - u) ** 2 * p1
            + 6 * (1 - u) * u * (p2 - p1)
            + 3 * u**2 * (1 - p2)
        )

    u = clamped
    for _ in range(16):
        bx = bezier(u, x1, x2)
        if abs(bx - clamped) < 1e-7:
            break
        dx = bezier_derivative(u, x1, x2)
        if abs(dx) < 1e-7:
            break
        u = u - (bx - clamped) / dx
        u = min(1.0, max(0.0, u))

    return bezier(u, y1, y2)


def _spring_advance(
    *,
    to_value: float,
    last_timestamp: float,
    current: float,
    velocity: float,
    now: float,
    damping: float,
    mass: float,
    stiffness: float,
) -> tuple[float, float, float]:
    """One analytic integrator step. ``now`` and ``last_timestamp`` are ms.

    Replicated from ``library/tools/hyperframes_render.py`` so the spring
    physics is identical across the IR evaluator and the HyperFrames
    renderer.
    """
    delta_time = min(now - last_timestamp, 64)
    if damping <= 0:
        raise ValueError("spring damping must be greater than 0")
    c, m, k = damping, mass, stiffness
    v0 = -velocity
    x0 = to_value - current
    zeta = c / (2 * math.sqrt(k * m))
    omega0 = math.sqrt(k / m)
    omega1 = omega0 * math.sqrt(1 - zeta**2)
    t = delta_time / 1000
    sin1 = math.sin(omega1 * t)
    cos1 = math.cos(omega1 * t)
    envelope = math.exp(-zeta * omega0 * t)
    frag1 = envelope * (sin1 * ((v0 + zeta * omega0 * x0) / omega1) + x0 * cos1)
    if zeta < 1:
        position = to_value - frag1
        next_velocity = zeta * omega0 * frag1 - envelope * (
            cos1 * (v0 + zeta * omega0 * x0) - omega1 * x0 * sin1
        )
    else:
        critically = math.exp(-omega0 * t)
        position = to_value - critically * (x0 + (v0 + omega0 * x0) * t)
        next_velocity = critically * (
            v0 * (t * omega0 - 1) + t * x0 * omega0 * omega0
        )
    return position, next_velocity, now


def _spring_progress(
    t: float, stiffness: float, damping: float, mass: float, fps: float
) -> float:
    """The 0-to-1 spring progress at normalized time ``t``.

    Integrates the spring physics frame by frame, stepping at 1/fps
    intervals. The result may overshoot 1.0 - that is the spring settling,
    not a bug.
    """
    if t <= 0:
        return 0.0

    frame = t * fps
    frame_clamped = max(0.0, frame)
    uneven_rest = frame_clamped % 1
    last = math.floor(frame_clamped)
    current, velocity, last_timestamp = 0.0, 0.0, 0.0
    f = 0.0
    while f <= last:
        if f == last:
            f = f + uneven_rest
        current, velocity, last_timestamp = _spring_advance(
            to_value=1.0,
            last_timestamp=last_timestamp,
            current=current,
            velocity=velocity,
            now=(f / fps) * 1000,
            damping=damping,
            mass=mass,
            stiffness=stiffness,
        )
        f += 1.0
    return current
