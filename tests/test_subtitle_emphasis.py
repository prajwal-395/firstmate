"""Emphasis words must reach the overlay, and the overlay must match the plan.

step_4_01 computes emphasis words for every caption and
generate_remotion_props serialises them as `emphasisWords`. The renderer
declared `isEmphasis` on AnimatedWord, never passed it, and never read it
inside - so every word was styled identically and the whole pass was
invisible.

There is no JS test runner in this project, so the component side is
covered structurally: the prop must be passed at the call site and used in
the component. That is exactly the defect that occurred - a prop declared
and never wired - so a structural assertion is the right shape of guard.
"""
import pathlib
import re

import pytest

REPO = pathlib.Path(__file__).resolve().parent.parent
OVERLAY = REPO / "remotion-subtitles/src/compositions/SubtitleOverlay"
INDEX = OVERLAY / "index.tsx"
WORD = OVERLAY / "AnimatedWord.tsx"


# ── The renderer wires the prop through ──

def test_the_overlay_passes_isemphasis_to_every_word():
    source = INDEX.read_text()
    call = re.search(r"<AnimatedWord\b(.*?)/>", source, re.S)
    assert call, "AnimatedWord is not rendered"
    assert "isEmphasis=" in call.group(1)


def test_animated_word_actually_uses_isemphasis():
    source = WORD.read_text()
    # Once, not just in the props type: the file opened with an
    # eslint-disable for unused vars precisely because it did not.
    body = source.split("}) => {", 1)[1]
    assert "isEmphasis" in body
    assert "eslint-disable @typescript-eslint/no-unused-vars" not in source


def test_emphasis_matching_ignores_case_and_punctuation():
    """The word timings carry the spoken token ("post,") while the
    emphasis list carries the bare word ("post")."""
    source = INDEX.read_text()
    assert "normaliseWord" in source
    assert "toLowerCase()" in source


# ── The plan still serialises them ──

def test_props_carry_the_emphasis_words():
    from library.steps.step_4_05_render_subtitles.generate_remotion_props import (
        generate_subtitle_props_per_block,
    )
    from library.tools.subtitle_style import (
        resolve_subtitle_style,
    )
    entries = [{
        "text": "post every single day",
        "spine_block_position": 3,
        "timeline_start": 1.0,
        "timeline_end": 3.0,
        "emphasis_words": ["post", "single"],
        "words": [
            {"word": w, "start": 1.0 + i * 0.5, "end": 1.5 + i * 0.5}
            for i, w in enumerate("post every single day".split())
        ],
    }]
    blocks = generate_subtitle_props_per_block(
        {"subtitle_entries": entries, "style": resolve_subtitle_style()})
    assert blocks, "no props generated"
    assert blocks[0]["subtitles"][0]["emphasisWords"] == ["post", "single"]


# ── 4.5: the manifest's subtitle list is compared against what renders ──

def _manifest(sub_blocks, seg_blocks):
    return {
        "project": {"frame_rate": 30.0},
        "subtitles": [
            {"spine_block_position": position,
             "timeline_start": start, "timeline_end": end}
            for position, start, end in sub_blocks
        ],
        "subtitle_overlay": {"segments": [
            {"block_position": position,
             "timeline_start": start, "timeline_end": end}
            for position, start, end in seg_blocks
        ]},
    }


def test_a_covered_plan_passes():
    from library.steps.step_5_04_compile_manifest.step import (
        _assert_subtitle_overlay_matches_plan,
    )
    _assert_subtitle_overlay_matches_plan(
        _manifest([(3, 4.4, 6.0)], [(3, 3.9, 6.5)]))


def test_a_block_with_no_rendered_segment_fails():
    from library.steps.step_5_04_compile_manifest.step import (
        _assert_subtitle_overlay_matches_plan,
    )
    with pytest.raises(ValueError, match="no rendered overlay segment"):
        _assert_subtitle_overlay_matches_plan(
            _manifest([(3, 4.4, 6.0), (4, 6.0, 8.0)], [(3, 3.9, 6.5)]))


def test_a_segment_that_stops_short_of_its_captions_fails():
    from library.steps.step_5_04_compile_manifest.step import (
        _assert_subtitle_overlay_matches_plan,
    )
    with pytest.raises(ValueError, match="only covers"):
        _assert_subtitle_overlay_matches_plan(
            _manifest([(3, 4.4, 9.0)], [(3, 3.9, 6.5)]))


def test_no_overlay_at_all_is_not_this_check_s_business():
    from library.steps.step_5_04_compile_manifest.step import (
        _assert_subtitle_overlay_matches_plan,
    )
    _assert_subtitle_overlay_matches_plan(_manifest([(3, 4.4, 6.0)], []))


# ── Delivery: the emphasis word must not be coloured prematurely ──

import os
import json
import shutil
import subprocess
from test_timed_text_delivery import _extract_frames, REMOTION_DIR, remotion_available

def _get_pixel_colors(png_path: str) -> set:
    from PIL import Image
    image = Image.open(png_path).convert("RGBA")
    data = image.getdata()
    colors = set()
    for item in data:
        if item[3] > 200:  # near opaque
            hex_color = '#{:02x}{:02x}{:02x}'.format(item[0], item[1], item[2])
            colors.add(hex_color)
    return colors

@pytest.fixture(scope="module")
def rendered_karaoke_emphasis(tmp_path_factory):
    out_dir = tmp_path_factory.mktemp("subtitle_karaoke")
    props_path = out_dir / "props.json"
    mov_path = out_dir / "output.mov"
    
    props = {
        "subtitles": [
            {
                "text": "silent",
                "startFrame": 0,
                "endFrame": 20,
                "emphasisWords": ["silent"],
                "words": [{"word": "silent", "startFrame": 10, "endFrame": 15}],
                "fitScale": 1.0
            }
        ],
        "fps": 30.0, "width": 1080, "height": 1920, "durationInFrames": 20,
        "style": {
            "fontFamily": "Montserrat", "fontSize": 85, "fontWeight": 800,
            "fontColor": "#FFFFFF", "accentColor": "#FBF0B8",
            "outlineColor": "#000000", "outlineWidth": 0, "position": "bottom",
            "safeArea": {"top": 0, "right": 0, "bottom": 0, "left": 0},
            "captionMaxWidth": 840
        }
    }
    
    props_path.write_text(json.dumps(props))
    subprocess.run(
        ["npx", "remotion", "render", "SubtitleOverlay", str(mov_path),
         "--props", str(props_path), "--codec", "prores", "--prores-profile", "4444"],
        cwd=REMOTION_DIR, check=True, capture_output=True
    )
    return _extract_frames(str(mov_path), str(out_dir / "frames"))


@remotion_available
def test_emphasis_word_does_not_carry_accent_before_it_is_spoken(rendered_karaoke_emphasis):
    frames = rendered_karaoke_emphasis
    
    # At frame 5, "silent" (startFrame=10) has not been spoken yet.
    colors_before = _get_pixel_colors(frames[5])
    yellows_before = [c for c in colors_before if c.startswith('#fbf0') or c.startswith('#fbef')]
    assert not yellows_before, f"Emphasis word has accent color before being spoken: {yellows_before}"
    
    # At frame 12, "silent" is being spoken. It should have the accent color.
    colors_during = _get_pixel_colors(frames[12])
    yellows_during = [c for c in colors_during if c.startswith('#fbf0') or c.startswith('#fbef')]
    assert yellows_during, "Emphasis word lacks accent color while being spoken"
    
    # At frame 18, "silent" has been spoken (endFrame=15). It should no longer have the accent color.
    colors_after = _get_pixel_colors(frames[18])
    yellows_after = [c for c in colors_after if c.startswith('#fbf0') or c.startswith('#fbef')]
    assert not yellows_after, f"Emphasis word keeps accent color after being spoken: {yellows_after}"
