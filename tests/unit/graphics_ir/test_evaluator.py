import pytest

from library.graphics_ir.evaluator import evaluate, evaluate_property
from library.graphics_ir.model import (
    Animation,
    EaseEasing,
    LinearEasing,
    Node,
    NodeType,
    Scene,
    SpringEasing,
    Transform,
)


def _scene_with_animations() -> Scene:
    node = Node(
        id="title",
        type=NodeType.GROUP,
        transform=Transform(scale=[1.0, 1.0]),
        animations=[
            Animation(
                target="title",
                property="transform.scale",
                from_value=[0.92, 0.92],
                to_value=[1.0, 1.0],
                start=3.2,
                duration=0.42,
                easing=EaseEasing(power=2),
            ),
            Animation(
                target="title",
                property="opacity",
                from_value=0.0,
                to_value=1.0,
                start=3.2,
                duration=0.18,
                easing=LinearEasing(),
            ),
        ],
    )
    return Scene(version=1, fps=30, width=1080, height=1920, duration=10.0, nodes=[node])


class TestEvaluate:
    def test_before_animation_start_returns_from_value(self):
        scene = _scene_with_animations()
        state = evaluate(scene, 0)
        assert state["title"]["transform.scale"] == [0.92, 0.92]
        assert state["title"]["opacity"] == 0.0

    def test_after_animation_end_returns_to_value(self):
        scene = _scene_with_animations()
        state = evaluate(scene, 110)
        assert state["title"]["transform.scale"] == [1.0, 1.0]
        assert state["title"]["opacity"] == 1.0

    def test_mid_animation_interpolates(self):
        scene = _scene_with_animations()
        state = evaluate(scene, 100)
        scale = state["title"]["transform.scale"]
        assert scale[0] == pytest.approx(0.96, abs=0.01)
        assert scale[1] == pytest.approx(0.96, abs=0.01)

    def test_opacity_linear_midpoint(self):
        scene = _scene_with_animations()
        state = evaluate(scene, 99)
        assert state["title"]["opacity"] == pytest.approx(0.556, abs=0.01)

    def test_node_without_animations_omitted(self):
        scene = Scene(
            version=1,
            fps=30,
            width=1080,
            height=1920,
            duration=10.0,
            nodes=[Node(id="static", type=NodeType.GROUP)],
        )
        assert evaluate(scene, 0) == {}


class TestEvaluateProperty:
    def test_existing_property(self):
        scene = _scene_with_animations()
        value = evaluate_property(scene, "title", "opacity", 99)
        assert value == pytest.approx(0.556, abs=0.01)

    def test_missing_node_returns_none(self):
        scene = _scene_with_animations()
        assert evaluate_property(scene, "missing", "opacity", 0) is None

    def test_missing_property_returns_none(self):
        scene = _scene_with_animations()
        assert evaluate_property(scene, "title", "blur", 0) is None


class TestSpringAnimation:
    def test_spring_animation_overshoots(self):
        node = Node(
            id="spring_node",
            type=NodeType.GROUP,
            animations=[
                Animation(
                    target="spring_node",
                    property="transform.scale",
                    from_value=[0.5, 0.5],
                    to_value=[1.0, 1.0],
                    start=0.0,
                    duration=1.0,
                    easing=SpringEasing(stiffness=80, damping=15, mass=0.8),
                ),
            ],
        )
        scene = Scene(version=1, fps=30, width=1080, height=1920, duration=10.0, nodes=[node])
        state = evaluate(scene, 28)
        scale = state["spring_node"]["transform.scale"]
        assert scale[0] > 1.0

    def test_spring_animation_settles(self):
        node = Node(
            id="spring_node",
            type=NodeType.GROUP,
            animations=[
                Animation(
                    target="spring_node",
                    property="transform.scale",
                    from_value=[0.5, 0.5],
                    to_value=[1.0, 1.0],
                    start=0.0,
                    duration=2.0,
                    easing=SpringEasing(stiffness=80, damping=15, mass=0.8),
                ),
            ],
        )
        scene = Scene(version=1, fps=30, width=1080, height=1920, duration=10.0, nodes=[node])
        state = evaluate(scene, 60)
        scale = state["spring_node"]["transform.scale"]
        assert scale[0] == pytest.approx(1.0, abs=0.01)
