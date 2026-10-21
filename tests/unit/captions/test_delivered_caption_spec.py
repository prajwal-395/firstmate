"""The delivered file's captions, judged on the delivered file.

The defect this names
---------------------
The delivery judged a delivered file's CAPTIONS by the PLAN: the
caption's timing came from the plan's own subtitle rows, and its
presence came from the build's inventory of placed items. Both are
statements about the plan and the timeline. Neither is a statement
about the file the captain receives.

So a caption could be right in the plan and wrong in the export, and
the delivery said nothing: the card never reached the export, it
reached it late, it was replaced by a card that was never re-rendered,
another graphic was drawn over it, or it said a different word. The
captain once called a broken highlight sweep "subtitles messed up" -
that is a defect in the FILE, and no plan-side check can see it.

The fix is the same move the audio leg made (PR 3046): judge the
delivered file's captions ON THE DELIVERED FILE. The plan supplies
only the intent; every measurement is taken from the file's own frames.

What each test proves
---------------------
* a clean render PASSES every leg - a check that fails correct output
  is not coverage;
* a caption that says the WRONG WORD is caught;
* a card that arrives LATE is caught, in frames;
* a card that COLLIDES with a lower third is caught;
* a card that was REPLACED but never re-rendered - the stale card - is
  caught;
* a caption that never reached the export is caught;
* the check says how long it took, so its cost per minute is a number
  and not a guess.

Every render here is SYNTHETIC and lives under `tmp_path`: the check
needs a file with captions on it, and the captain's media is
read-only.
"""
from __future__ import annotations

import json
import os
import subprocess

import pytest

from library.tools import caption_ocr
from library.tools.delivered_caption_spec import (
    BASELINE_WINDOW_SECONDS,
    CaptionIntent,
    ink_above_baseline,
    ink_profile,
    normalise_words,
    verify_delivered_captions,
    visible_span,
    words_say_the_same,
)

WIDTH, HEIGHT = 540, 960
FPS = 15
FRAMES = 90
CAPTION_FIRST, CAPTION_LAST = 15, 40
CAPTION_TEXT = "this is the caption text"
FONT = "/System/Library/Fonts/Supplemental/Arial Bold.ttf"


def _font(size: int):
    from PIL import ImageFont

    try:
        return ImageFont.truetype(FONT, size)
    except OSError:
        return ImageFont.load_default()


def _frame(index: int, caption: str = "", lower_third: str = ""):
    """One frame: a moving background, an optional caption, a lower third."""
    from PIL import Image, ImageDraw

    image = Image.new("RGB", (WIDTH, HEIGHT), (30, 34, 40))
    draw = ImageDraw.Draw(image)
    for i in range(8):
        x = (index * 5 + i * 60) % WIDTH
        draw.rectangle([x, 120 + i * 90, x + 90, 190 + i * 90],
                       fill=(70 + i * 5, 60, 90))
    if lower_third:
        draw.text((45, 784), lower_third, font=_font(26),
                  fill=(255, 220, 0), stroke_width=2, stroke_fill=(0, 0, 0))
    if caption:
        draw.text((45, 800), caption, font=_font(34), fill=(255, 255, 255),
                  stroke_width=3, stroke_fill=(0, 0, 0))
    return image


def _render(path: str, captions, lower_thirds=(), silent_audio: bool = False
            ) -> str:
    """A synthetic delivered file: a moving picture with captions on it."""
    raw = path + ".rgb"
    with open(raw, "wb") as handle:
        for index in range(FRAMES):
            caption = ""
            for first, last, text in captions:
                if first <= index <= last:
                    caption = text
            lower = ""
            for first, last, text in lower_thirds:
                if first <= index <= last:
                    lower = text
            handle.write(_frame(index, caption, lower).tobytes())
    command = ["ffmpeg", "-v", "error", "-y", "-f", "rawvideo",
               "-pix_fmt", "rgb24", "-s", f"{WIDTH}x{HEIGHT}", "-r", str(FPS),
               "-i", raw]
    if silent_audio:
        command += ["-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo"]
    command += ["-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "18"]
    if silent_audio:
        command += ["-c:a", "aac", "-shortest"]
    command += [path]
    subprocess.run(command, check=True, capture_output=True)
    os.remove(raw)
    return path


@pytest.fixture(scope="module")
def renders(tmp_path_factory):
    """The clean render and the four seeded defects, built once."""
    root = tmp_path_factory.mktemp("delivered-captions")
    return {
        "clean": _render(str(root / "clean.mp4"),
                         [(CAPTION_FIRST, CAPTION_LAST, CAPTION_TEXT)]),
        "wrong_word": _render(str(root / "wrong_word.mp4"),
                              [(CAPTION_FIRST, CAPTION_LAST,
                                "this is the other text")]),
        "late": _render(str(root / "late.mp4"),
                        [(CAPTION_FIRST + 8, CAPTION_LAST + 8, CAPTION_TEXT)]),
        "collision": _render(str(root / "collision.mp4"),
                             [(CAPTION_FIRST, CAPTION_LAST, CAPTION_TEXT)],
                             lower_thirds=[(CAPTION_FIRST, CAPTION_LAST,
                                            "LOWER THIRD LABEL")]),
        "stale": _render(str(root / "stale.mp4"),
                         [(CAPTION_FIRST, CAPTION_LAST, CAPTION_TEXT),
                          (CAPTION_LAST + 20, CAPTION_LAST + 40,
                           "this is the old text")]),
        "empty": _render(str(root / "empty.mp4"), []),
        "with_audio": _render(str(root / "with_audio.mp4"),
                              [(CAPTION_FIRST, CAPTION_LAST,
                                "this is the other text")],
                              silent_audio=True),
    }


def _intents(text: str = CAPTION_TEXT) -> list:
    """The plan's intent for the one caption the renders carry."""
    return [CaptionIntent(
        label="caption-1",
        text=text,
        start=CAPTION_FIRST / FPS,
        end=(CAPTION_LAST + 1) / FPS,
    )]


needs_ocr = pytest.mark.skipif(
    not caption_ocr.helper_available()[0],
    reason="the delivered frames cannot be read without Apple Vision OCR")


def _failed(verdict, check: str) -> list:
    return [finding for finding in verdict.findings
            if finding.check == check and not finding.passed]


@needs_ocr
def test_a_clean_render_passes_every_leg(renders):
    """A check that fails correct output is not coverage."""
    verdict = verify_delivered_captions(renders["clean"], _intents())
    assert verdict.checked, verdict.reason
    assert verdict.passed, [finding.as_dict() for finding in verdict.failures]


@needs_ocr
def test_a_caption_that_says_the_wrong_word_is_caught(renders):
    """The export carrying a different word than the plan asked for."""
    verdict = verify_delivered_captions(renders["wrong_word"], _intents())
    failures = _failed(verdict, "text")
    assert failures, [finding.as_dict() for finding in verdict.findings]
    assert "this is the other text" in failures[0].detail


@needs_ocr
def test_a_card_that_arrives_late_is_caught_in_frames(renders):
    """A card three frames late is a defect, measured in frames."""
    verdict = verify_delivered_captions(renders["late"], _intents())
    failures = _failed(verdict, "timing")
    assert failures, [finding.as_dict() for finding in verdict.findings]
    # The edges are measured by reading the frames around them, so the
    # lateness is a LOWER BOUND: the caption is at least this late.
    assert failures[0].measured["late_frames"] >= 8


@needs_ocr
def test_a_card_that_collides_with_a_lower_third_is_caught(renders):
    """Another graphic drawn over the caption's own box."""
    verdict = verify_delivered_captions(renders["collision"], _intents())
    failures = _failed(verdict, "collision")
    assert failures, [finding.as_dict() for finding in verdict.findings]
    assert "LOWER THIRD" in failures[0].detail


@needs_ocr
def test_a_stale_card_after_a_replacement_is_caught(renders):
    """The replaced card still on the export: a caption nobody declares."""
    verdict = verify_delivered_captions(renders["stale"], _intents())
    failures = _failed(verdict, "stale")
    assert failures, [finding.as_dict() for finding in verdict.findings]
    assert "no caption declares" in failures[0].detail


@needs_ocr
def test_a_caption_that_never_reached_the_export_is_caught(renders):
    """The plan's caption, and nothing on the file."""
    verdict = verify_delivered_captions(renders["empty"], _intents())
    failures = _failed(verdict, "missing")
    assert failures, [finding.as_dict() for finding in verdict.findings]
    assert not verdict.passed


def test_the_ink_pass_measures_the_visible_span_in_frames(renders):
    """The visible span is measured, not declared - and needs no OCR."""
    from library.tools.delivered_caption_spec import probe_video

    facts = probe_video(renders["clean"])
    # The caption's own box, as the OCR measured it on the real file.
    counts = ink_profile(renders["clean"], facts["width"], facts["height"],
                         region=(45, 800, 405, 840))
    print("DEBUG", renders["clean"], facts, sum(1 for c in counts if c > 0), max(counts) if counts else None)
    excess = ink_above_baseline(counts, int(BASELINE_WINDOW_SECONDS * facts["fps"]))
    span = visible_span(excess, 0, len(excess) - 1)
    assert span is not None, counts
    first, last = span
    assert abs(first - CAPTION_FIRST) <= 1, (span, counts)
    assert abs(last - CAPTION_LAST) <= 1, (span, counts)


def test_the_words_are_compared_as_words():
    """A hair space or a capitalisation is not a wrong word."""
    assert words_say_the_same("This is the Caption Text",
                               "this is the caption text")[0]
    assert not words_say_the_same("this is the other text",
                                  "this is the caption text")[0]
    assert normalise_words("AI's plan, isn't it?") == [
        "ai's", "plan", "isn't", "it"]


@needs_ocr
def test_the_check_reports_its_own_cost(renders):
    """The cost per minute of video is a number the check reports."""
    verdict = verify_delivered_captions(renders["clean"], _intents())
    assert verdict.seconds > 0
    assert verdict.frames_measured > 0
    assert verdict.duration_seconds == pytest.approx(FRAMES / FPS, abs=0.2)
    assert verdict.as_dict()["seconds_per_minute"] > 0


def test_the_intent_comes_from_the_built_timeline(tmp_path):
    """The plan side: the built timeline's own snapshot and card props."""
    from library.tools.delivered_caption_intent import caption_plan

    project = tmp_path / "project"
    review = project / "pipeline_output" / "review"
    review.mkdir(parents=True)
    card = project / "pipeline_output" / "subtitle_segments" / "card_1.mov"
    card.parent.mkdir(parents=True)
    _card_file(str(card))
    props = {
        "fps": FPS,
        "width": WIDTH,
        "height": HEIGHT,
        "subtitles": [{
            "text": CAPTION_TEXT,
            "startFrame": 0,
            "endFrame": 26,
            "emphasisWords": ["caption"],
            "words": [{"word": "this", "startFrame": 0, "endFrame": 5},
                      {"word": "caption", "startFrame": 5, "endFrame": 12},
                      {"word": "text", "startFrame": 12, "endFrame": 26}],
        }],
    }
    (card.parent / "card_1_props.json").write_text(
        json.dumps(props), encoding="utf-8")
    snapshot = {
        "metadata": {"fps": FPS},
        "tracks": [
            {"type": "video", "index": 1, "name": "V1", "clips": []},
            {"type": "video", "index": 3, "name": "Subtitles", "clips": [{
                "file_path": str(card),
                "record_in": 30,
                "record_out": 56,
                "source_in": 0,
                "transform": {"Pan": 0.0, "Tilt": 0.0},
            }]},
        ],
    }
    (review / "Reel_01.timeline.json").write_text(
        json.dumps(snapshot), encoding="utf-8")

    plan = caption_plan(str(project), "Reel 01")
    assert plan.attributable, plan.reason
    assert len(plan.intents) == 1
    intent = plan.intents[0]
    assert intent.text == CAPTION_TEXT
    assert intent.start == pytest.approx(30 / FPS)
    assert intent.end == pytest.approx(56 / FPS)
    assert intent.emphasis and intent.emphasis[0][0] == "caption"
    # The card's own geometry: the ink height and the line count the
    # card was drawn with, which is what the delivered file is compared
    # against.
    assert intent.ink_height and intent.ink_height > 0
    assert intent.lines == 1


def _card_file(path: str) -> str:
    """A caption card with alpha, the way the caption step renders one."""
    from PIL import Image, ImageDraw

    frames = []
    for index in range(26):
        image = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 0))
        draw = ImageDraw.Draw(image)
        draw.text((45, 800), CAPTION_TEXT, font=_font(34),
                  fill=(255, 255, 255, 255), stroke_width=3,
                  stroke_fill=(0, 0, 0, 255))
        frames.append(image)
    raw = path + ".png"
    frames[0].save(raw, save_all=True, append_images=frames[1:],
                   duration=int(1000 / FPS), loop=0)
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-framerate", str(FPS), "-i", raw,
         "-c:v", "qtrle", path], check=True, capture_output=True)
    os.remove(raw)
    return path


@needs_ocr
def test_the_delivery_refuses_a_file_whose_caption_says_the_wrong_word(
        renders):
    """The delivery verdict carries the caption spec, and it gates.

    The audio is silent, and the silence is DECLARED (a declared silent
    ending passes), so the file's audio spec passes and the caption is
    the only thing that can refuse it.
    """
    from library.tools import reel_deliver
    from library.tools.delivery_spec import DEFAULT_DELIVERY_SPEC

    verdict = reel_deliver.verify_deliverable(
        renders["with_audio"], FRAMES / FPS, WIDTH, HEIGHT, "",
        DEFAULT_DELIVERY_SPEC,
        declared_silence_spans=[(0.0, FRAMES / FPS)],
        caption_intents=_intents())
    assert verdict["caption_spec"]["passed"] is False, verdict["caption_spec"]
    assert verdict["passed"] is False
    failed = [finding for finding in verdict["caption_spec"]["findings"]
              if not finding["passed"]]
    assert any(finding["check"] == "text" for finding in failed), failed


@needs_ocr
def test_a_file_with_no_caption_intent_is_not_judged(renders):
    """Nothing asked, nothing claimed: the verdict says so."""
    from library.tools import reel_deliver
    from library.tools.delivery_spec import DEFAULT_DELIVERY_SPEC

    verdict = reel_deliver.verify_deliverable(
        renders["with_audio"], FRAMES / FPS, WIDTH, HEIGHT, "",
        DEFAULT_DELIVERY_SPEC,
        declared_silence_spans=[(0.0, FRAMES / FPS)])
    assert "caption_spec" not in verdict
