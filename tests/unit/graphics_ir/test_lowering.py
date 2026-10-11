import pytest

from library.graphics_ir.evaluator import evaluate
from library.graphics_ir.model import Node, NodeType, Scene
from library.graphics_ir.lowering import (
    lower_full_frame_card,
    lower_motion_graphics,
    lower_post_header,
    lower_subtitle_overlay,
    lower_timed_text_overlay,
)


def _subtitle_props():
    return {
        "fps": 30,
        "width": 1080,
        "height": 1920,
        "durationInFrames": 300,
        "style": {
            "fontFamily": "Montserrat",
            "fontColor": "#FFFFFF",
            "accentColor": "#FBF0B8",
            "fontSize": 58,
            "fontWeight": 800,
            "position": "bottom",
            "safeArea": {"top": 0, "right": 0, "bottom": 200, "left": 0},
            "captionMaxWidth": 800,
        },
        "subtitles": [
            {
                "text": "hello world",
                "startFrame": 30,
                "endFrame": 60,
                "words": [
                    {"word": "hello", "startFrame": 30, "endFrame": 45},
                    {"word": "world", "startFrame": 45, "endFrame": 60},
                ],
            }
        ],
    }


def _timed_text_props():
    return {
        "fps": 30,
        "width": 1080,
        "height": 1920,
        "durationInFrames": 300,
        "fontFamily": "Helvetica",
        "safeArea": {"top": 0, "right": 0, "bottom": 0, "left": 0},
        "moments": [
            {
                "text": "Test moment",
                "color": "#FFFFFF",
                "fontSize": 48,
                "startFrame": 30,
                "durationFrames": 60,
                "x": 0.5,
                "y": 0.5,
                "fadeInFrames": 10,
                "fadeOutFrames": 10,
            }
        ],
    }


def _card_props(entrance="fade", exit_char="cut"):
    return {
        "fps": 30,
        "width": 1080,
        "height": 1920,
        "durationInFrames": 300,
        "runs": [{"text": "Hello world", "type_role": "display", "colour": "#FFFFFF"}],
        "background": "#000000",
        "entrance": entrance,
        "exit": exit_char,
        "fontFamily": "Montserrat",
        "safeArea": {"top": 0, "right": 0, "bottom": 0, "left": 0},
    }


def _mg_props():
    return {
        "fps": 30,
        "width": 1080,
        "height": 1920,
        "durationInFrames": 300,
        "safeArea": {"top": 0, "right": 0, "bottom": 0, "left": 0},
        "elements": [
            {
                "element": "title_lockup",
                "anchor": "centre",
                "row": 0,
                "runs": [{"text": "Title", "type_role": "display"}],
                "color": "#FFFFFF",
                "entrance": "fade",
                "exit": "cut",
                "startFrame": 0,
                "durationFrames": 150,
                "footprint": 1.0,
            }
        ],
    }


class TestSubtitleOverlayLowering:
    def test_word_entry_scale_at_start(self):
        scene = lower_subtitle_overlay(_subtitle_props())
        state = evaluate(scene, 30)
        scale = state["subtitle_0_word_0"]["transform.scale"]
        assert scale[0] == pytest.approx(0.95, abs=0.01)

    def test_word_accent_sweep_steps_at_word_start(self):
        scene = lower_subtitle_overlay(_subtitle_props())
        state = evaluate(scene, 31)
        assert state["subtitle_0_word_0"]["color"] == "#FBF0B8"

    def test_word_scale_reaches_one_after_four_frames(self):
        scene = lower_subtitle_overlay(_subtitle_props())
        state = evaluate(scene, 34)
        scale = state["subtitle_0_word_0"]["transform.scale"]
        assert scale[0] == pytest.approx(1.0, abs=0.01)


class TestTimedTextOverlayLowering:
    def test_fade_in_start(self):
        scene = lower_timed_text_overlay(_timed_text_props())
        state = evaluate(scene, 30)
        assert state["moment_0"]["opacity"] == pytest.approx(0.0, abs=0.01)

    def test_fade_in_complete(self):
        scene = lower_timed_text_overlay(_timed_text_props())
        state = evaluate(scene, 40)
        assert state["moment_0"]["opacity"] == pytest.approx(1.0, abs=0.01)

    def test_fade_out_end(self):
        scene = lower_timed_text_overlay(_timed_text_props())
        state = evaluate(scene, 90)
        assert state["moment_0"]["opacity"] == pytest.approx(0.0, abs=0.01)


class TestFullFrameCardLowering:
    def test_fade_entrance_start(self):
        scene = lower_full_frame_card(_card_props("fade", "cut"))
        state = evaluate(scene, 0)
        assert state["card"]["opacity"] == pytest.approx(0.0, abs=0.01)

    def test_fade_entrance_complete(self):
        scene = lower_full_frame_card(_card_props("fade", "cut"))
        state = evaluate(scene, 8)
        assert state["card"]["opacity"] == pytest.approx(1.0, abs=0.01)

    def test_slide_entrance_start(self):
        scene = lower_full_frame_card(_card_props("slide", "cut"))
        state = evaluate(scene, 0)
        translate = state["card"]["transform.translate"]
        assert translate[1] == pytest.approx(40.0, abs=0.1)


class TestMotionGraphicsLowering:
    def test_element_opacity_start(self):
        scene = lower_motion_graphics(_mg_props())
        state = evaluate(scene, 0)
        assert state["element_title_lockup_0"]["opacity"] == pytest.approx(0.0, abs=0.01)

    def test_element_opacity_complete(self):
        scene = lower_motion_graphics(_mg_props())
        state = evaluate(scene, 8)
        assert state["element_title_lockup_0"]["opacity"] == pytest.approx(1.0, abs=0.01)


def _stat_callout_props():
    return {
        "fps": 30,
        "width": 852,
        "height": 480,
        "durationInFrames": 84,
        "safeArea": {"top": 48, "right": 48, "bottom": 318, "left": 48},
        "layoutWidth": 750.0,
        "elements": [
            {
                "element": "stat_callout",
                "anchor": "top_centre",
                "row": 0,
                "runs": [
                    {"text": "20%", "type_role": "display"},
                    {"text": "had asked", "type_role": "supporting"},
                ],
                "color": "#AABBCC",
                "entrance": "fade",
                "exit": "fade",
                "startFrame": 0,
                "durationFrames": 84,
                "footprint": None,
            }
        ],
    }


def _list_build_props(**overrides):
    el = {
        "element": "list_build",
        "anchor": "middle_left",
        "row": 0,
        "runs": [
            {"text": "Reddit", "type_role": "supporting"},
            {"text": "LinkedIn", "type_role": "supporting"},
            {"text": "Press mentions", "type_role": "supporting"},
            {"text": "Directories", "type_role": "supporting"},
        ],
        "color": "#FFB8D4",
        "entrance": "fade",
        "exit": "fade",
        "startFrame": 0,
        "durationFrames": 96,
        "footprint": None,
        "data": {},
    }
    el.update(overrides)
    return {
        "fps": 30,
        "width": 410,
        "height": 480,
        "durationInFrames": 96,
        "safeArea": {"top": 128, "right": 48, "bottom": 128, "left": 48},
        "elements": [el],
    }


class TestStatCalloutLowering:
    def test_runs_draw_at_one_and_a_half_scale(self):
        scene = lower_motion_graphics(_stat_callout_props())
        by_id = {n.id: n for n in scene.nodes}
        figure = by_id["element_stat_callout_0_run_0"]
        assert figure.text.font_size == pytest.approx(56 * 1.5)
        assert figure.text.font_weight == 900
        label = by_id["element_stat_callout_0_run_1"]
        assert label.text.font_size == pytest.approx(36 * 1.5)

    def test_display_tracking_without_invented_uppercase(self):
        scene = lower_motion_graphics(_stat_callout_props())
        by_id = {n.id: n for n in scene.nodes}
        figure = by_id["element_stat_callout_0_run_0"]
        assert figure.text.letter_spacing == pytest.approx(3.0)
        # The run states no uppercase: the template draws copy as written.
        assert figure.text.text_transform is None
        assert figure.text.text == "20%"

    def test_run_gap_is_the_literal_twelve_pixels(self):
        scene = lower_motion_graphics(_stat_callout_props())
        by_id = {n.id: n for n in scene.nodes}
        figure = by_id["element_stat_callout_0_run_0"]
        label = by_id["element_stat_callout_0_run_1"]
        assert (label.bounds.y - (figure.bounds.y + figure.bounds.height)) == pytest.approx(12.0)


class TestListBuildLowering:
    def test_items_share_the_lead_size_with_per_run_weight(self):
        scene = lower_motion_graphics(_list_build_props())
        by_id = {n.id: n for n in scene.nodes}
        for i in range(4):
            text = by_id[f"element_list_build_0_item_{i}_text"]
            assert text.text.font_size == pytest.approx(36.0)
            assert text.text.font_weight == 700

    def test_display_lead_unifies_supporting_items(self):
        props = _list_build_props()
        props["elements"][0]["runs"] = [
            {"text": "Big", "type_role": "display"},
            {"text": "small", "type_role": "supporting"},
        ]
        scene = lower_motion_graphics(props)
        by_id = {n.id: n for n in scene.nodes}
        assert by_id["element_list_build_0_item_0_text"].text.font_size == pytest.approx(56.0)
        second = by_id["element_list_build_0_item_1_text"]
        assert second.text.font_size == pytest.approx(56.0)
        assert second.text.font_weight == 700

    def test_items_arrive_on_equal_shares_with_cubic_ease(self):
        # 96 frames, fade exit (8): hold 88, four items -> starts 0/22/44/66,
        # each revealed over 60% of the 22-frame window (13.2 frames).
        scene = lower_motion_graphics(_list_build_props())
        state = evaluate(scene, 0)
        assert state["element_list_build_0_item_0"]["opacity"] == pytest.approx(0.0, abs=0.01)
        state = evaluate(scene, 14)
        assert state["element_list_build_0_item_0"]["opacity"] == pytest.approx(1.0, abs=0.01)
        assert state["element_list_build_0_item_1"]["opacity"] == pytest.approx(0.0, abs=0.01)
        state = evaluate(scene, 30)
        progress = (30 - 22) / 13.2
        assert state["element_list_build_0_item_1"]["opacity"] == pytest.approx(
            1 - (1 - progress) ** 3, abs=0.01)
        assert state["element_list_build_0_item_1"]["transform.translate"][1] == pytest.approx(
            20 * (1 - progress) ** 3, abs=0.1)

    def test_anchored_offsets_set_starts(self):
        props = _list_build_props(data={"stage_offsets": [0.0, 1.0, 2.0, 3.0]})
        scene = lower_motion_graphics(props)
        state = evaluate(scene, 31)
        # Second item starts at frame 30, 8-frame entrance ramp -> 1/8 in.
        assert state["element_list_build_0_item_1"]["opacity"] == pytest.approx(
            1 - (1 - 1 / 4.8) ** 3, abs=0.01)

    def test_marker_is_a_circle_on_the_optical_centre(self):
        scene = lower_motion_graphics(_list_build_props())
        by_id = {n.id: n for n in scene.nodes}
        dot = by_id["element_list_build_0_item_0_dot"]
        text = by_id["element_list_build_0_item_0_text"]
        assert dot.shape.shape == "circle"
        assert dot.bounds.width == pytest.approx(8.0)
        assert dot.bounds.x == pytest.approx(text.bounds.x - 20.0)
        assert dot.bounds.y == pytest.approx(
            text.bounds.y + (36 * 1.3 - 8) / 2.0, abs=0.01)

    def test_no_runs_draws_nothing(self):
        props = _list_build_props(runs=[])
        scene = lower_motion_graphics(props)
        assert not [n for n in scene.nodes if "list_build" in n.id]


class TestPostHeaderLowering:
    def test_static_scene(self):
        props = {
            "avatarSrc": "avatar.png",
            "avatarShape": "round",
            "avatarBackground": "#333333",
            "displayName": "Test User",
            "handle": "testuser",
            "verified": True,
            "hook": "Test hook",
            "fontFamily": "Montserrat",
            "nameSize": 32,
            "handleSize": 24,
            "hookSize": 28,
            "box": {"left": 0, "top": 0, "width": 1080},
            "fps": 30,
            "width": 1080,
            "height": 1920,
            "durationInFrames": 300,
        }
        scene = lower_post_header(props)
        assert len(scene.nodes) > 0
        state = evaluate(scene, 0)
        assert state == {}


def test_a_string_fill_still_means_a_solid_colour():
    """A plain string ``fill`` is a solid colour, not a gradient.

    The five standard compositions state their fills as strings; the
    vocabulary must not turn those into gradients. This pins the
    compatibility existing scenes rely on.
    """
    from library.graphics_ir.model import ShapeContent
    from library.graphics_ir.serialization import deserialize, serialize

    node = Node(
        id="s",
        type=NodeType.SHAPE,
        shape=ShapeContent(shape="rect", fill="#fff"),
    )
    scene = Scene(version=1, fps=30, width=1080, height=1920, duration=1.0, nodes=[node])
    back = deserialize(serialize(scene))
    assert back.nodes[0].shape.fill == "#fff"
