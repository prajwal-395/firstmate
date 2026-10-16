import pytest

from library.graphics_ir.evaluator import evaluate
from library.graphics_ir.layout import measure_text
from library.graphics_ir.lowering import (
    lower_brand_motion,
    lower_full_frame_card,
    lower_motion_graphics,
    lower_post_header,
    lower_subtitle_overlay,
    lower_timed_text_overlay,
)
from library.graphics_ir.model import Node, NodeType, Scene


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
    def test_word_scale_pulses_only_while_highlighted_and_resets_at_handoff(self):
        scene = lower_subtitle_overlay(_subtitle_props())
        before = evaluate(scene, 29)["subtitle_0_word_0"]["transform.scale"]
        during = evaluate(scene, 34)["subtitle_0_word_0"]["transform.scale"]
        after = evaluate(scene, 45)["subtitle_0_word_0"]["transform.scale"]
        next_word_start = evaluate(scene, 45)["subtitle_0_word_1"]["transform.scale"]

        assert before == pytest.approx([0.95, 0.95])
        assert during == pytest.approx([1.0, 1.0])
        assert after == pytest.approx([0.95, 0.95])
        assert next_word_start == pytest.approx([0.95, 0.95])
        for frame in (29, 30, 34, 41, 45):
            scale = evaluate(scene, frame)["subtitle_0_word_0"]["transform.scale"]
            assert all(0.95 <= axis <= 1.0 for axis in scale)
        # The pulse is a transform over the fixed, font-measured boxes.
        word = next(n for n in scene.nodes if n.id == "subtitle_0_word_0")
        assert word.bounds.width == pytest.approx(measure_text(
            word.text.text, word.text.font_size, word.text.font_file,
            word.text.font_weight))

    def test_word_accent_sweep_steps_at_word_start(self):
        scene = lower_subtitle_overlay(_subtitle_props())
        state = evaluate(scene, 31)
        assert state["subtitle_0_word_0"]["color"] == "#FBF0B8"

    def test_zero_width_word_window_has_no_scale_animation(self):
        props = _subtitle_props()
        props["subtitles"][0]["words"][0]["endFrame"] = 30
        scene = lower_subtitle_overlay(props)
        word = next(n for n in scene.nodes if n.id == "subtitle_0_word_0")
        assert not [a for a in word.animations if a.property == "transform.scale"]


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

    def test_flip_entrance_refuses_by_name(self):
        from library.graphics_ir.lowering import FLIP_REFUSAL

        with pytest.raises(ValueError, match="flip"):
            lower_full_frame_card(_card_props("flip", "cut"))
        try:
            lower_full_frame_card(_card_props("flip", "cut"))
        except ValueError as exc:
            assert FLIP_REFUSAL in str(exc)
        else:
            raise AssertionError("flip entrance did not refuse")


class TestMotionGraphicsLowering:
    def test_element_opacity_start(self):
        scene = lower_motion_graphics(_mg_props())
        state = evaluate(scene, 0)
        assert state["element_title_lockup_0"]["opacity"] == pytest.approx(0.0, abs=0.01)

    def test_element_opacity_complete(self):
        scene = lower_motion_graphics(_mg_props())
        state = evaluate(scene, 8)
        assert state["element_title_lockup_0"]["opacity"] == pytest.approx(1.0, abs=0.01)


def _mg_arm_props(kind, element, duration=90):
    return {
        "fps": 30,
        "width": 1080,
        "height": 1920,
        "durationInFrames": duration,
        "safeArea": {"top": 220, "right": 60, "bottom": 320, "left": 60},
        "elements": [element],
    }


def _mg_element(kind, **overrides):
    element = {
        "element": kind,
        "anchor": "centre",
        "row": 0,
        "runs": [],
        "color": "#7CFC00",
        "entrance": "cut",
        "exit": "cut",
        "startFrame": 0,
        "durationFrames": 90,
        "footprint": 1.0,
        "timelineProgressStart": 0.1,
        "timelineProgressEnd": 0.2,
        "data": {},
    }
    element.update(overrides)
    return element


class TestContextStampLowering:
    def test_pill_draws_plan_colour_with_white_copy_at_point_eight(self):
        props = _mg_arm_props(
            "context_stamp",
            _mg_element("context_stamp", runs=[
                {"text": "Austin, TX", "type_role": "micro"}]),
        )
        scene = lower_motion_graphics(props)
        by_id = {n.id: n for n in scene.nodes}
        pill = by_id["element_context_stamp_0_pill"]
        assert pill.shape.shape == "rect"
        assert pill.shape.fill == "#7CFC00"
        assert pill.shape.corner_radius == pytest.approx(4.0)
        run = by_id["element_context_stamp_0_run_0"]
        assert run.text.text == "Austin, TX"
        assert run.text.color == "#FFFFFF"
        assert run.text.font_size == pytest.approx(24 * 0.8)

    def test_box_carries_the_arm_padding(self):
        props = _mg_arm_props(
            "context_stamp",
            _mg_element("context_stamp", runs=[
                {"text": "Austin, TX", "type_role": "micro"}]),
        )
        scene = lower_motion_graphics(props)
        by_id = {n.id: n for n in scene.nodes}
        pill = by_id["element_context_stamp_0_pill"]
        run = by_id["element_context_stamp_0_run_0"]
        assert run.bounds.x == pytest.approx(pill.bounds.x + 16.0)
        assert run.bounds.y == pytest.approx(pill.bounds.y + 8.0)
        assert pill.bounds.height == pytest.approx(
            run.bounds.height + 16.0)


class TestComparisonBarsLowering:
    def test_no_values_draws_nothing(self):
        props = _mg_arm_props(
            "comparison_bars",
            _mg_element("comparison_bars", runs=[
                {"text": "A", "type_role": "micro"}]),
        )
        scene = lower_motion_graphics(props)
        assert not [n for n in scene.nodes if "comparison_bars" in n.id
                    and "stack_" not in n.id]

    def test_no_runs_draws_nothing(self):
        props = _mg_arm_props(
            "comparison_bars",
            _mg_element("comparison_bars", data={"values": [30]}),
        )
        scene = lower_motion_graphics(props)
        assert not [n for n in scene.nodes if "comparison_bars" in n.id
                    and "stack_" not in n.id]

    def test_items_arrive_on_equal_shares_with_cubic_growth(self):
        # 90 frames, cut exit: hold 90, two bars -> starts 0/45, each
        # growing over 70% of the 45-frame window (31.5 frames).
        props = _mg_arm_props(
            "comparison_bars",
            _mg_element("comparison_bars",
                        runs=[{"text": "A", "type_role": "micro"},
                              {"text": "B", "type_role": "micro"}],
                        data={"values": [30, 70]}),
        )
        scene = lower_motion_graphics(props)
        state = evaluate(scene, 0)
        assert state["element_comparison_bars_0_bar_0"]["opacity"] == pytest.approx(0.0, abs=0.01)
        assert state["element_comparison_bars_0_bar_1"]["opacity"] == pytest.approx(0.0, abs=0.01)
        state = evaluate(scene, 16)
        progress = 16 / 31.5
        expected = 1 - (1 - progress) ** 3
        assert state["element_comparison_bars_0_bar_0"]["opacity"] == pytest.approx(expected, abs=0.01)
        assert state["element_comparison_bars_0_bar_0_fill"]["transform.scale"][0] == pytest.approx(
            expected, abs=0.01)
        assert state["element_comparison_bars_0_bar_1"]["opacity"] == pytest.approx(0.0, abs=0.01)
        state = evaluate(scene, 60)
        progress = (60 - 45) / 31.5
        assert state["element_comparison_bars_0_bar_1"]["opacity"] == pytest.approx(
            1 - (1 - progress) ** 3, abs=0.01)

    def test_fill_growth_pins_the_left_edge(self):
        # Scale about the centre plus the compensating translate keeps
        # the bar's left edge still while the right edge grows.
        props = _mg_arm_props(
            "comparison_bars",
            _mg_element("comparison_bars",
                        runs=[{"text": "A", "type_role": "micro"}],
                        data={"values": [50]}),
        )
        scene = lower_motion_graphics(props)
        by_id = {n.id: n for n in scene.nodes}
        fill = by_id["element_comparison_bars_0_bar_0_fill"]
        scale = next(a for a in fill.animations
                     if a.property == "transform.scale")
        translate = next(a for a in fill.animations
                         if a.property == "transform.translate")
        assert scale.from_value == [0.0, 1.0]
        assert scale.to_value == [1.0, 1.0]
        width = fill.bounds.width
        assert translate.from_value == pytest.approx([-width / 2.0, 0.0])
        assert translate.to_value == [0.0, 0.0]
        assert scale.start == translate.start
        assert scale.duration == translate.duration

    def test_fill_width_is_the_share_of_the_maximum(self):
        props = _mg_arm_props(
            "comparison_bars",
            _mg_element("comparison_bars",
                        runs=[{"text": "A", "type_role": "micro"},
                              {"text": "B", "type_role": "micro"}],
                        data={"values": [30, 70]}),
        )
        scene = lower_motion_graphics(props)
        by_id = {n.id: n for n in scene.nodes}
        track = by_id["element_comparison_bars_0_bar_0_track"]
        first = by_id["element_comparison_bars_0_bar_0_fill"]
        second = by_id["element_comparison_bars_0_bar_1_fill"]
        assert first.bounds.width == pytest.approx(track.bounds.width * 30 / 70)
        assert second.bounds.width == pytest.approx(track.bounds.width)

    def test_side_anchored_track_shrink_wraps_the_widest_label(self):
        # `width: 100%` of a shrink-to-fit side stack resolves against
        # the widest label - measured 292px side-anchored on the
        # Remotion side, not the 960px usable width a centre stack
        # spans. Extra values past the runs are ignored.
        props = _mg_arm_props(
            "comparison_bars",
            _mg_element("comparison_bars", anchor="middle_left",
                        runs=[{"text": "Foot traffic", "type_role": "supporting"},
                              {"text": "Search demand", "type_role": "supporting"}],
                        data={"values": [30, 70, 45]}),
        )
        scene = lower_motion_graphics(props)
        by_id = {n.id: n for n in scene.nodes}
        track = by_id["element_comparison_bars_0_bar_0_track"]
        assert track.bounds.width < 960.0
        assert track.bounds.width == pytest.approx(
            by_id["element_comparison_bars_0_bar_0_label"].bounds.width)
        assert len([n for n in scene.nodes
                    if "_bar_2_" in n.id]) == 0


class TestCounterRollLowering:
    def test_figure_rolls_start_to_end_over_eighty_percent(self):
        props = _mg_arm_props(
            "counter_roll",
            _mg_element("counter_roll", data={"start_value": 0,
                                               "end_value": 1000}),
        )
        scene = lower_motion_graphics(props)
        by_id = {n.id: n for n in scene.nodes}
        first = by_id["element_counter_roll_0_fig_0"]
        assert first.text.text == "0"
        assert first.text.font_size == pytest.approx(56 * 1.5)
        assert first.text.font_weight == 900
        last_id = max(
            (n.id for n in scene.nodes if "_fig_" in n.id and "layer" not in n.id),
            key=lambda i: int(i.rsplit("_fig_", 1)[1]),
        )
        assert by_id[last_id].text.text == "1,000"

    def test_mid_roll_matches_the_cubic_curve(self):
        # Frame 36 of 90: 36 / (90 * 0.8) = 0.5, eased to 0.875 -> 875.
        props = _mg_arm_props(
            "counter_roll",
            _mg_element("counter_roll", data={"start_value": 0,
                                               "end_value": 1000}),
        )
        scene = lower_motion_graphics(props)
        by_id = {n.id: n for n in scene.nodes}
        layers = [n for n in scene.nodes if "_fig_layer_" in n.id]
        # Mid-frame sampling: layer edges sit exactly on frame times,
        # so an exact-boundary sample catches both neighbours in float.
        live = [n for n in layers
                if n.timing.start <= 36.5 / 30 < n.timing.start + n.timing.duration]
        assert len(live) == 1
        assert by_id[live[0].children[0]].text.text == "875"

    def test_decimals_and_affixes_format_as_drawn(self):
        props = _mg_arm_props(
            "counter_roll",
            _mg_element("counter_roll", durationFrames=30,
                        data={"start_value": 0, "end_value": 1,
                              "prefix": "$", "suffix": "M",
                              "decimals": 1}),
        )
        scene = lower_motion_graphics(props)
        by_id = {n.id: n for n in scene.nodes}
        assert by_id["element_counter_roll_0_fig_0"].text.text == "$0.0M"
        last_id = max(
            (n.id for n in scene.nodes if "_fig_" in n.id and "layer" not in n.id),
            key=lambda i: int(i.rsplit("_fig_", 1)[1]),
        )
        assert by_id[last_id].text.text == "$1.0M"

    def test_label_draws_under_the_figure_at_point_seven(self):
        props = _mg_arm_props(
            "counter_roll",
            _mg_element("counter_roll",
                        runs=[{"text": "grew", "type_role": "supporting"}],
                        data={"start_value": 0, "end_value": 100}),
        )
        scene = lower_motion_graphics(props)
        by_id = {n.id: n for n in scene.nodes}
        label = by_id["element_counter_roll_0_label_0"]
        assert label.text.text == "grew"
        assert label.text.font_size == pytest.approx(36 * 0.7)
        figure = by_id["element_counter_roll_0_fig_0"]
        assert label.bounds.y == pytest.approx(
            figure.bounds.y + figure.bounds.height + 8.0)


class TestStepCounterLowering:
    def test_position_and_total_with_faded_of(self):
        props = _mg_arm_props(
            "step_counter",
            _mg_element("step_counter", data={"position": 2, "total": 5}),
        )
        scene = lower_motion_graphics(props)
        by_id = {n.id: n for n in scene.nodes}
        assert by_id["element_step_counter_0_position"].text.text == "2"
        total = by_id["element_step_counter_0_total"]
        assert total.text.text == "of 5"
        assert total.opacity == pytest.approx(0.6)

    def test_missing_data_defaults_to_one_of_one(self):
        props = _mg_arm_props(
            "step_counter", _mg_element("step_counter"))
        scene = lower_motion_graphics(props)
        by_id = {n.id: n for n in scene.nodes}
        assert by_id["element_step_counter_0_position"].text.text == "1"
        assert by_id["element_step_counter_0_total"].text.text == "of 1"


class TestPointerAnnotationLowering:
    def test_ring_is_a_stroked_circle_without_fill(self):
        props = _mg_arm_props(
            "pointer_annotation",
            _mg_element("pointer_annotation"),
        )
        scene = lower_motion_graphics(props)
        by_id = {n.id: n for n in scene.nodes}
        ring = by_id["element_pointer_annotation_0_ring"]
        assert ring.shape.shape == "circle"
        assert ring.shape.stroke == "#7CFC00"
        assert ring.shape.stroke_width == pytest.approx(8.0)
        assert ring.shape.fill is None

    def test_stroke_centres_on_the_template_edge(self):
        # The template's 80px is the border box (Tailwind preflight), so
        # the outer edge is 80 and the 8px stroke centres on the 64px
        # content circle - the ellipse insets half the stroke. Glow
        # spills past layout and never enters the box.
        props = _mg_arm_props(
            "pointer_annotation",
            _mg_element("pointer_annotation"),
        )
        scene = lower_motion_graphics(props)
        by_id = {n.id: n for n in scene.nodes}
        ring = by_id["element_pointer_annotation_0_ring"]
        group = by_id["element_pointer_annotation_0"]
        assert ring.bounds.width == pytest.approx(80.0 - 8.0)
        # A centre stack spans the insets and the fixed ring rides its
        # left, the way the template's flex item does.
        assert group.bounds.width == pytest.approx(960.0)
        assert ring.bounds.x == pytest.approx(group.bounds.x + 4.0)
        assert ring.bounds.y == pytest.approx(group.bounds.y + 4.0)
        assert len(ring.filters) == 1
        assert ring.filters[0].sigma == pytest.approx(8.0)


class TestWebsitePanelLowering:
    def test_no_asset_draws_nothing(self):
        props = _mg_arm_props(
            "website_panel",
            _mg_element("website_panel", runs=[
                {"text": "example.com", "type_role": "micro"}]),
        )
        scene = lower_motion_graphics(props)
        assert not [n for n in scene.nodes if "website_panel" in n.id
                    and "stack_" not in n.id]

    def test_unreadable_asset_draws_nothing(self):
        props = _mg_arm_props(
            "website_panel",
            _mg_element("website_panel", asset="/tmp/does-not-exist.png"),
        )
        scene = lower_motion_graphics(props)
        assert not [n for n in scene.nodes if "website_panel" in n.id
                    and "stack_" not in n.id]

    def test_panel_wraps_address_and_contained_shot(self, tmp_path):
        from PIL import Image
        shot = tmp_path / "capture.png"
        Image.new("RGBA", (800, 600), (20, 40, 80, 255)).save(shot)
        props = _mg_arm_props(
            "website_panel",
            _mg_element("website_panel",
                        runs=[{"text": "example.com", "type_role": "micro"}],
                        asset=str(shot)),
        )
        scene = lower_motion_graphics(props)
        by_id = {n.id: n for n in scene.nodes}
        panel = by_id["element_website_panel_0_panel"]
        assert panel.shape.stroke == "#7CFC00"
        assert panel.shape.corner_radius == pytest.approx(16.0)
        address = by_id["element_website_panel_0_address"]
        assert address.text.text == "example.com"
        assert address.text.color == "#FFFFFF"
        rule = by_id["element_website_panel_0_rule"]
        assert rule.bounds.y > address.bounds.y + address.bounds.height
        shot_node = by_id["element_website_panel_0_shot"]
        assert shot_node.image.src == str(shot)
        assert shot_node.bounds.width == pytest.approx(panel.bounds.width - 6.0)
        assert shot_node.bounds.height == pytest.approx(
            shot_node.bounds.width * 600 / 800)
        assert shot_node.bounds.y == pytest.approx(
            rule.bounds.y + rule.bounds.height)

    def test_no_copy_stated_no_address_bar(self, tmp_path):
        from PIL import Image
        shot = tmp_path / "capture.png"
        Image.new("RGBA", (800, 600), (20, 40, 80, 255)).save(shot)
        props = _mg_arm_props(
            "website_panel",
            _mg_element("website_panel", asset=str(shot)),
        )
        scene = lower_motion_graphics(props)
        by_id = {n.id: n for n in scene.nodes}
        assert "element_website_panel_0_address" not in by_id
        assert "element_website_panel_0_rule" not in by_id
        assert "element_website_panel_0_shot" in by_id
class TestMotionGraphicsChromeArms:
    """`progress_bar` and `frame_accents` (census G2): chrome, opacity only."""

    def _chrome_props(self, element, **over):
        el = {
            "element": element,
            "anchor": "top-centre",
            "color": "#FF0000",
            "entrance": "cut",
            "exit": "cut",
            "startFrame": 0,
            "durationFrames": 90,
            "timelineProgressStart": 0.25,
            "timelineProgressEnd": 0.75,
        }
        el.update(over)
        return {
            "fps": 30,
            "width": 1080,
            "height": 1920,
            "durationInFrames": 90,
            "safeArea": {"top": 60, "right": 60, "bottom": 60, "left": 60},
            "elements": [el],
        }

    def test_bar_track_spans_safe_box_at_top_inset(self):
        scene = lower_motion_graphics(self._chrome_props("progress_bar"))
        track = next(n for n in scene.nodes if n.id == "element_progress_bar_0_track")
        assert track.bounds.x == pytest.approx(60.0)
        assert track.bounds.y == pytest.approx(60.0)
        assert track.bounds.width == pytest.approx(1080 - 120)

    def test_bar_centre_anchor_draws_at_bottom_inset(self):
        scene = lower_motion_graphics(
            self._chrome_props("progress_bar", anchor="centre"))
        track = next(n for n in scene.nodes if n.id == "element_progress_bar_0_track")
        assert track.bounds.y == pytest.approx(1920 - 60 - track.bounds.height)

    def test_bar_fill_lerps_start_to_end_fraction(self):
        scene = lower_motion_graphics(self._chrome_props("progress_bar"))
        state0 = evaluate(scene, 0)
        state89 = evaluate(scene, 89)
        assert state0["element_progress_bar_0_fill"]["transform.scale"][0] == pytest.approx(0.25, abs=0.02)
        assert state89["element_progress_bar_0_fill"]["transform.scale"][0] == pytest.approx(0.75, abs=0.02)

    def test_bar_carries_no_character_animation(self):
        scene = lower_motion_graphics(self._chrome_props("progress_bar"))
        group = next(n for n in scene.nodes if n.id == "element_progress_bar_0")
        assert [a.property for a in (group.animations or [])] == []

    def test_accents_draw_eight_bars_at_four_corners(self):
        scene = lower_motion_graphics(self._chrome_props("frame_accents"))
        bars = [n for n in scene.nodes if "_bar_" in n.id]
        assert len(bars) == 8
        xs = sorted(b.bounds.x for b in bars)
        ys = sorted(b.bounds.y for b in bars)
        assert xs[0] == pytest.approx(60.0)
        assert xs[-1] == pytest.approx(1080 - 60 - 6.0)
        assert ys[0] == pytest.approx(60.0)
        assert ys[-1] == pytest.approx(1920 - 60 - 6.0)


class TestMotionGraphicsDigitCounter:
    """`digit_counter` (census G2): odometer figure over label runs."""

    def _counter_props(self, **over):
        el = {
            "element": "digit_counter",
            "anchor": "centre",
            "color": "#FFFFFF",
            "entrance": "cut",
            "exit": "cut",
            "startFrame": 0,
            "durationFrames": 90,
            "data": {"end_value": 1234, "decimals": 0},
            "runs": [],
        }
        el.update(over)
        return {
            "fps": 30,
            "width": 1080,
            "height": 1920,
            "durationInFrames": 90,
            "safeArea": {"top": 60, "right": 60, "bottom": 60, "left": 60},
            "elements": [el],
        }

    def test_grouped_value_rolls_each_digit(self):
        scene = lower_motion_graphics(self._counter_props())
        rails = [n for n in scene.nodes if "_rail_" in n.id and n.animations]
        assert len(rails) == 4

    def test_grouping_comma_draws_static(self):
        scene = lower_motion_graphics(
            self._counter_props(data={"end_value": 12345, "decimals": 0}))
        rails = [n for n in scene.nodes if "_rail_" in n.id and n.animations]
        statics = [n for n in scene.nodes if "_static_" in n.id]
        assert len(rails) == 5
        assert len(statics) == 1

    def test_rails_use_ir_spring_easing_with_digit_config(self):
        from library.graphics_ir.model import SpringEasing

        scene = lower_motion_graphics(self._counter_props())
        rails = [n for n in scene.nodes if "_rail_" in n.id and n.animations]
        for rail in rails:
            easing = rail.animations[0].easing
            assert isinstance(easing, SpringEasing)
            assert (easing.stiffness, easing.damping, easing.mass) == (80.0, 15.0, 0.8)

    def test_least_significant_digit_starts_first(self):
        scene = lower_motion_graphics(self._counter_props())
        rails = sorted(
            (n for n in scene.nodes if "_rail_" in n.id and n.animations),
            key=lambda n: n.id,
        )
        starts = [r.animations[0].start for r in rails]
        assert starts == sorted(starts, reverse=True)
        assert starts[-1] == pytest.approx(0.0)

    def test_settled_rail_shows_its_digit(self):
        scene = lower_motion_graphics(self._counter_props())
        state = evaluate(scene, 200)
        rails = sorted(
            (n for n in scene.nodes if "_rail_" in n.id and n.animations),
            key=lambda n: n.id,
        )
        assert len(rails) == 4
        heights = {round(r.animations[0].to_value[1] / -d, 6)
                   for r, d in zip(rails, [1, 2, 3, 4])}
        assert len(heights) == 1
        for rail, digit in zip(rails, [1, 2, 3, 4]):
            assert state[rail.id]["transform.translate"][1] == pytest.approx(
                rail.animations[0].to_value[1])

    def test_prefix_draws_before_first_digit(self):
        scene = lower_motion_graphics(
            self._counter_props(data={"end_value": 100, "decimals": 0,
                                      "prefix": "$", "suffix": "+"}))
        ordered = [n for n in scene.nodes
                   if n.id.startswith("element_digit_counter_0_")
                   and ("_cell_" in n.id or "_static_" in n.id)]
        kinds = [("cell" if "_cell_" in n.id else n.text.text) for n in ordered]
        assert kinds[0] == "$"
        assert kinds[-1] == "+"

    def test_spring_curve_matches_reference_integrator(self):
        """The IR spring easing integrates `DigitRoll`'s own spring curve.

        `SpringEasing(80, 15, 0.8)` evaluated frame by frame matches the
        reference damped-harmonic-oscillator spelling to 1e-6: the rail
        draws the same curve from the same numbers, not a second physics.
        """
        from library.graphics_ir.easing import apply_easing
        from library.graphics_ir.model import SpringEasing
        from library.tools.hyperframes_render import _spring_calculation

        easing = SpringEasing(stiffness=80, damping=15, mass=0.8)
        for frame in [0, 5, 10, 20, 30, 60]:
            assert apply_easing(easing, frame / 30.0, fps=30.0) == pytest.approx(
                _spring_calculation(frame=frame, fps=30.0,
                                    damping=15, mass=0.8, stiffness=80),
                abs=1e-6,
            )
def _mg_emblem_props(**over):
    element = {
        "element": "subject_emblem",
        "anchor": "centre",
        "row": 0,
        "runs": [
            {"text": "Aa", "type_role": "display", "uppercase": False},
            {"text": "HUMAN-WRITTEN", "type_role": "supporting", "uppercase": False},
        ],
        "color": "#aabbcc",
        "entrance": "scale",
        "exit": "fade",
        "startFrame": 0,
        "durationFrames": 96,
        "footprint": None,
    }
    element.update(over)
    props = _mg_props()
    props["elements"] = [element]
    props["durationInFrames"] = element["durationFrames"]
    return props


def _mg_review_props(**over):
    element = {
        "element": "review_panel",
        "anchor": "top_right",
        "row": 0,
        "runs": [
            {"text": "Maple Street Kitchen", "type_role": "display",
             "uppercase": False},
            {"text": "Google reviews", "type_role": "micro", "uppercase": False},
        ],
        "color": "",
        "entrance": "cut",
        "exit": "fade",
        "startFrame": 0,
        "durationFrames": 72,
        "footprint": None,
        "data": {
            "rating": 4.8,
            "count": 214,
            "rows": [
                {"author": "Dana R.", "stars": 5, "when": "2 weeks ago",
                 "body": "Best pasta on this side of town."},
                {"author": "Marcus T.", "stars": 5, "when": "a month ago",
                 "body": "Found it at the top of the map."},
            ],
            "palette": {"surface": "#FFFFFF", "ink": "#202124",
                        "muted": "#5F6368", "star": "#FBBC04",
                        "rule": "#E8EAED"},
        },
    }
    element.update(over)
    props = _mg_props()
    props["elements"] = [element]
    props["durationInFrames"] = element["durationFrames"]
    return props


def _mg_beat_props(**over):
    element = {
        "element": "beat_accent",
        "anchor": "centre",
        "row": 0,
        "runs": [],
        "color": "#aabbcc",
        "entrance": "cut",
        "exit": "fade",
        "startFrame": 0,
        "durationFrames": 10,
        "footprint": 0.5,
    }
    element.update(over)
    props = _mg_props()
    props["width"] = 156
    props["height"] = 480
    props["safeArea"] = {"top": 210, "right": 48, "bottom": 210, "left": 48}
    props["elements"] = [element]
    props["durationInFrames"] = element["durationFrames"]
    return props


class TestSubjectEmblemLowering:
    def test_emblem_lowers_plate_mark_and_label(self):
        scene = lower_motion_graphics(_mg_emblem_props())
        nodes = {n.id: n for n in scene.nodes}
        plate = nodes["element_subject_emblem_0_plate"]
        assert plate.shape.shape == "rect"
        assert plate.bounds.width == pytest.approx(200.0)
        assert plate.bounds.height == pytest.approx(200.0)
        mark = nodes["element_subject_emblem_0_mark"]
        assert mark.text.text == "Aa"
        assert mark.text.font_weight == pytest.approx(900.0)
        assert mark.text.color == "#FFFFFF"
        label = nodes["element_subject_emblem_0_label_0"]
        assert "HUMAN-WRITTEN" in label.text.text

    def test_emblem_mark_only_has_no_label(self):
        props = _mg_emblem_props()
        props["elements"][0]["runs"] = [
            {"text": "$", "type_role": "display", "uppercase": False}]
        scene = lower_motion_graphics(props)
        ids = [n.id for n in scene.nodes]
        assert "element_subject_emblem_0_mark" in ids
        assert not [i for i in ids if "_label_" in i]

    def test_emblem_typewriter_reveals_in_layers(self):
        props = _mg_emblem_props(entrance="typewriter", durationFrames=40)
        props["durationInFrames"] = 40
        scene = lower_motion_graphics(props)
        layers = [n for n in scene.nodes
                  if n.id.startswith("element_subject_emblem_0_mark_layer_")]
        assert len(layers) > 1
        texts = {n.text.text for n in scene.nodes
                 if n.type == NodeType.TEXT and n.id.startswith("element_subject_emblem_0_mark_")
                 and not n.id.startswith("element_subject_emblem_0_mark_layer_")}
        assert "Aa" in texts, "a settled layer shows the whole mark"
        assert "|" in texts, "the first layer draws only the caret"
        first, last = layers[0], layers[-1]
        assert first.timing.start == pytest.approx(0.0)
        assert last.timing.start + last.timing.duration == pytest.approx(40 / 30)

    def test_emblem_scale_entrance_animates(self):
        scene = lower_motion_graphics(_mg_emblem_props())
        state = evaluate(scene, 0)
        assert state["element_subject_emblem_0"]["transform.scale"] == pytest.approx(
            [0.7, 0.7])


class TestReviewPanelLowering:
    def test_review_panel_card_size_is_border_box(self):
        from library.graphics_ir.lowering import _mg_review_card_size, _MGContext
        from library.tools.mg_tight_box import review_panel_size
        props = _mg_review_props()
        el = props["elements"][0]
        ctx = _MGContext(30, 1080, 1920, {}, None, "Montserrat", None)
        # Two rows at 145, not 147: the row div is border-box, so the
        # 2px rule lives inside the row height (measured 453 on real pixels).
        assert _mg_review_card_size(el, ctx) == (660.0, 453.0)
        assert review_panel_size(el, 1.0) == (660.0, 453.0)

    def test_review_panel_lowers_header_and_rows(self):
        scene = lower_motion_graphics(_mg_review_props())
        nodes = {n.id: n for n in scene.nodes}
        surface = nodes["element_review_panel_0_surface"]
        assert surface.bounds.width == pytest.approx(660.0)
        assert surface.bounds.height == pytest.approx(453.0)
        assert nodes["element_review_panel_0_name"].text.text == "Maple Street Kitchen"
        assert nodes["element_review_panel_0_score"].text.text == "4.8"
        assert "214 Google reviews" in nodes["element_review_panel_0_label"].text.text
        stars = [n for n in scene.nodes if n.type == NodeType.SVG]
        # Header 5 muted + 5 filled (4.8 fills every star), two rows the same.
        assert len(stars) == 30
        partial = next(n for n in scene.nodes
                       if n.id == "element_review_panel_0_header_star_4_fill")
        assert partial.clip is not None
        right = float(partial.clip[len("inset(0 "):-len("% 0 0)")])
        assert right == pytest.approx(20.0, abs=0.01)
        bodies = [nodes[f"element_review_panel_0_row_{i}_body"] for i in (0, 1)]
        assert all(len(n.text.text.split("\n")) <= 2 for n in bodies)

    def test_review_panel_without_rows_draws_nothing(self):
        props = _mg_review_props()
        props["elements"][0]["data"] = dict(props["elements"][0]["data"], rows=[])
        scene = lower_motion_graphics(props)
        assert not [n for n in scene.nodes if n.id.startswith("element_review_panel_0")]

    def test_review_panel_without_palette_draws_nothing(self):
        props = _mg_review_props()
        data = dict(props["elements"][0]["data"])
        del data["palette"]
        props["elements"][0]["data"] = data
        scene = lower_motion_graphics(props)
        assert not [n for n in scene.nodes if n.id.startswith("element_review_panel_0")]


class TestBeatAccentLowering:
    def test_beat_lowers_a_glowing_disc(self):
        scene = lower_motion_graphics(_mg_beat_props())
        nodes = {n.id: n for n in scene.nodes}
        disc = nodes["element_beat_accent_0_disc"]
        assert disc.shape.shape == "circle"
        assert disc.bounds.width == pytest.approx(90.0)
        assert disc.filters and disc.filters[0].sigma == pytest.approx(12.0)

    def test_beat_opacity_is_baked_per_frame(self):
        scene = lower_motion_graphics(_mg_beat_props())
        nodes = {n.id: n for n in scene.nodes}
        disc = nodes["element_beat_accent_0_disc"]
        opacities = [a for a in disc.animations if a.property == "opacity"]
        # cut entrance, fade exit over 10 frames: the (1-p) product is
        # quadratic where they overlap, so one segment per frame.
        assert len(opacities) == 10
        state = evaluate(scene, 0)
        assert state["element_beat_accent_0_disc"]["opacity"] == pytest.approx(1.0)
        state = evaluate(scene, 5)
        assert state["element_beat_accent_0_disc"]["opacity"] == pytest.approx(
            0.5 * 0.625, abs=0.01)

    def test_beat_carries_no_entrance_character(self):
        scene = lower_motion_graphics(_mg_beat_props(entrance="scale", exit="fade"))
        nodes = {n.id: n for n in scene.nodes}
        group = nodes["element_beat_accent_0"]
        assert group.animations == []
        disc = nodes["element_beat_accent_0_disc"]
        kinds = {a.property for a in disc.animations}
        assert kinds <= {"transform.scale", "transform.translate", "opacity"}
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


def _brand_motion_props(src="/tmp/repro-sting.webm"):
    return {
        "src": src,
        "fps": 30,
        "width": 320,
        "height": 240,
        "durationInFrames": 30,
        "muted": True,
    }


def test_brand_motion_lowers_to_one_full_frame_video_node():
    """BrandMotion is one Video node over the whole delivery frame.

    The composition plays one project-supplied file whole with its own
    alpha (``OffthreadVideo``, ``objectFit: fill``) - so the lowering
    carries no trim, no fit and no conform prop; the renderer draws
    ``native_sample``. Geometry mismatch is refused Python-side in
    ``brand_motion_props`` before props reach here.
    """
    scene = lower_brand_motion(_brand_motion_props())
    assert scene.fps == 30
    assert (scene.width, scene.height) == (320, 240)
    assert scene.duration == pytest.approx(1.0)
    assert len(scene.nodes) == 1
    node = scene.nodes[0]
    assert node.type == NodeType.VIDEO
    assert node.video is not None and node.video.src == "/tmp/repro-sting.webm"
    assert (node.bounds.x, node.bounds.y) == (0, 0)
    assert (node.bounds.width, node.bounds.height) == (320, 240)
    assert node.animations == [] and node.tracks == []


def test_brand_motion_refuses_an_empty_src():
    """An empty ``src`` draws nothing in the composition - refused here.

    An overlay that draws nothing is not rendered (AGENTS.md 10.2), so
    the lowering refuses rather than emitting a node that draws nothing.
    """
    with pytest.raises(ValueError, match="no src"):
        lower_brand_motion(_brand_motion_props(src=""))


def test_brand_motion_scene_round_trips_through_serialization():
    """The Video node survives the scene.json the renderer reads."""
    from library.graphics_ir.serialization import deserialize, serialize

    scene = lower_brand_motion(_brand_motion_props())
    back = deserialize(serialize(scene))
    assert back.nodes[0].type == NodeType.VIDEO
    assert back.nodes[0].video.src == "/tmp/repro-sting.webm"
