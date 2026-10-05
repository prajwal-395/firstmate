import pytest

from library.graphics_ir.easing import apply_easing
from library.graphics_ir.model import (
    CubicBezierEasing,
    EaseEasing,
    LinearEasing,
    SpringEasing,
    StepsEasing,
)


class TestLinearEasing:
    def test_linear_is_identity(self):
        assert apply_easing(LinearEasing(), 0.0) == 0.0
        assert apply_easing(LinearEasing(), 0.5) == 0.5
        assert apply_easing(LinearEasing(), 1.0) == 1.0

    def test_linear_clamps(self):
        assert apply_easing(LinearEasing(), -0.5) == 0.0
        assert apply_easing(LinearEasing(), 1.5) == 1.0


class TestEaseEasing:
    def test_power1_is_linear(self):
        assert apply_easing(EaseEasing(power=1), 0.0) == 0.0
        assert apply_easing(EaseEasing(power=1), 0.5) == 0.5
        assert apply_easing(EaseEasing(power=1), 1.0) == 1.0

    def test_power2_quadratic_out(self):
        assert apply_easing(EaseEasing(power=2), 0.0) == 0.0
        assert apply_easing(EaseEasing(power=2), 0.5) == pytest.approx(0.75)
        assert apply_easing(EaseEasing(power=2), 1.0) == 1.0

    def test_power3_cubic_out(self):
        assert apply_easing(EaseEasing(power=3), 0.0) == 0.0
        assert apply_easing(EaseEasing(power=3), 0.5) == pytest.approx(0.875)
        assert apply_easing(EaseEasing(power=3), 1.0) == 1.0

    def test_power2_matches_gsap_power1_out(self):
        for t in [0.0, 0.25, 0.5, 0.75, 1.0]:
            expected = 1 - (1 - t) ** 2
            assert apply_easing(EaseEasing(power=2), t) == pytest.approx(expected)

    def test_power3_matches_gsap_power2_out(self):
        for t in [0.0, 0.25, 0.5, 0.75, 1.0]:
            expected = 1 - (1 - t) ** 3
            assert apply_easing(EaseEasing(power=3), t) == pytest.approx(expected)


class TestCubicBezierEasing:
    def test_linear_bezier(self):
        easing = CubicBezierEasing(x1=0.0, y1=0.0, x2=1.0, y2=1.0)
        assert apply_easing(easing, 0.0) == pytest.approx(0.0)
        assert apply_easing(easing, 0.5) == pytest.approx(0.5)
        assert apply_easing(easing, 1.0) == pytest.approx(1.0)

    def test_css_ease(self):
        easing = CubicBezierEasing(x1=0.25, y1=0.1, x2=0.25, y2=1.0)
        assert apply_easing(easing, 0.0) == pytest.approx(0.0)
        assert apply_easing(easing, 1.0) == pytest.approx(1.0)
        assert apply_easing(easing, 0.5) == pytest.approx(0.8024, abs=1e-3)

    def test_endpoints(self):
        easing = CubicBezierEasing(x1=0.4, y1=0.0, x2=0.6, y2=1.0)
        assert apply_easing(easing, 0.0) == pytest.approx(0.0)
        assert apply_easing(easing, 1.0) == pytest.approx(1.0)


class TestSpringEasing:
    def test_spring_starts_at_zero(self):
        easing = SpringEasing(stiffness=80, damping=15, mass=0.8)
        assert apply_easing(easing, 0.0) == pytest.approx(0.0)

    def test_spring_settles_at_one(self):
        easing = SpringEasing(stiffness=80, damping=15, mass=0.8)
        assert apply_easing(easing, 1.0, fps=60.0) == pytest.approx(1.0, abs=0.01)

    def test_spring_overshoots(self):
        easing = SpringEasing(stiffness=80, damping=15, mass=0.8)
        values = [apply_easing(easing, t / 100) for t in range(101)]
        assert max(values) > 1.0

    def test_spring_matches_hyperframes_physics(self):
        from library.tools.hyperframes_render import _spring_calculation

        easing = SpringEasing(stiffness=80, damping=15, mass=0.8)
        fps = 60.0
        for frame in [0, 10, 30, 60, 120]:
            t = frame / fps
            expected = _spring_calculation(
                frame=frame, fps=fps, damping=15, mass=0.8, stiffness=80
            )
            assert apply_easing(easing, t) == pytest.approx(expected, abs=1e-6)


class TestStepsEasing:
    def test_steps_end(self):
        easing = StepsEasing(steps=4, direction="end")
        assert apply_easing(easing, 0.0) == pytest.approx(0.0)
        assert apply_easing(easing, 0.24) == pytest.approx(0.0)
        assert apply_easing(easing, 0.25) == pytest.approx(0.25)
        assert apply_easing(easing, 0.5) == pytest.approx(0.5)
        assert apply_easing(easing, 0.75) == pytest.approx(0.75)
        assert apply_easing(easing, 1.0) == pytest.approx(1.0)

    def test_steps_start(self):
        easing = StepsEasing(steps=4, direction="start")
        assert apply_easing(easing, 0.0) == pytest.approx(0.0)
        assert apply_easing(easing, 0.01) == pytest.approx(0.25)
        assert apply_easing(easing, 0.26) == pytest.approx(0.5)
        assert apply_easing(easing, 1.0) == pytest.approx(1.0)

    def test_steps_clamps(self):
        easing = StepsEasing(steps=2, direction="end")
        assert apply_easing(easing, -0.5) == pytest.approx(0.0)
        assert apply_easing(easing, 1.5) == pytest.approx(1.0)
