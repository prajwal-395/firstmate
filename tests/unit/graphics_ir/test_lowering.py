import pytest

from library.graphics_ir.evaluator import evaluate
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
