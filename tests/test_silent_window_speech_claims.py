"""No quoted speech out of a window the transcript gives nothing.

Action windows carry their audio track, which the model hears
(`extract_video_clips` keeps it and `analyze_actions` passes it as
`audio=`). Delivery is heard; words still come only from the transcript
text the prompt carries. Measured defect: on a window with no speech it
invented a quotation and attributed it to the person on screen, and that
text flowed into searchable action segments as though it had been
spoken.

Two halves, matching the fix:
1. `_strip_unheard_quotations` removes quoted spans the window's
   transcript cannot verify (straight apostrophes are kept - they are
   not quotes).
2. `analyze_actions` strips them on windows the transcript gives no
   words of its own, and records `speech_quote_stripped` on the window
   entry. Windows WITH word-timed speech may echo the transcript text
   they were given, which is attributed correctly by construction.

These tests name that defect: they fail if quoted spans from a
wordless window reach the entry, or if the strip eats legitimate
visible-delivery prose.
"""
from unittest.mock import MagicMock, patch
from pathlib import Path

# Mock heavy mlx_vlm dependency before importing vision_pipeline_v3
mlx_mock = MagicMock()
mlx_prompt_utils = MagicMock()
mlx_prompt_utils.apply_chat_template.return_value = "prompt"
mlx_mock.prompt_utils = mlx_prompt_utils

from library.tools.analysis import vision_pipeline_v3 as vp


def test_quoted_span_is_stripped_but_description_survives():
    cleaned, stripped = vp._strip_unheard_quotations(
        'mouth moving as if speaking, says "hello there friends" loudly')
    assert stripped is True
    assert '"' not in cleaned
    assert "mouth moving" in cleaned
    assert "hello there friends" not in cleaned


def test_apostrophe_is_not_a_quotation():
    cleaned, stripped = vp._strip_unheard_quotations(
        "person's mouth moving, head nodding")
    assert stripped is False
    assert cleaned == "person's mouth moving, head nodding"


def test_unbalanced_quote_nulls_the_field():
    cleaned, stripped = vp._strip_unheard_quotations(
        'mouth moving, says "hello there')
    assert stripped is True
    assert cleaned is None


def test_window_transcript_reports_its_precision():
    temporal = {"speech_regions": [
        {"start": 1.0, "end": 3.0, "text": "hello world"},
    ]}
    text, timed = vp.get_window_transcript(temporal, 0.0, 10.0, "")
    assert (text, timed) == ("hello world", True)

    text, timed = vp.get_window_transcript(
        None, 0.0, 10.0, full_transcript="a b c d e f g h")
    assert timed is False and text != ""

    text, timed = vp.get_window_transcript(None, 0.0, 10.0, "")
    assert (text, timed) == ("", False)


def _analyzer_saying(raw_actions):
    analyzer = MagicMock()
    analyzer.analyze_with_retry.return_value = (
        {"actions": raw_actions}, "raw", 0.5)
    return analyzer


def test_untranscribed_window_quotation_is_stripped_and_recorded():
    analyzer = _analyzer_saying([{
        "start": 0.0, "end": 10.0,
        "action": "person talking to camera",
        "speech_cue": 'says "welcome back to the show" with energy',
        "body_language": "seated, hands visible",
    }])
    clips = [{"index": 0, "start": 0.0, "end": 10.0, "path": "/tmp/w.mp4"}]
    out = vp.analyze_actions(analyzer, clips, 10.0, None, "")
    assert len(out) == 1
    entry = out[0]
    assert entry.get("speech_quote_stripped") is True
    cue = entry["actions"][0]["speech_cue"]
    assert cue is None or '"' not in cue
    assert "welcome back to the show" not in str(entry["actions"])
    # The visible delivery around the quotation survives.
    assert entry["actions"][0]["action"] == "person talking to camera"
    assert entry["actions"][0]["body_language"] == "seated, hands visible"


def test_word_timed_window_may_echo_its_transcript():
    analyzer = _analyzer_saying([{
        "start": 1.0, "end": 3.0,
        "action": "person talking to camera",
        "speech_cue": "mouth moving steadily",
        "body_language": "seated",
    }])
    clips = [{"index": 0, "start": 0.0, "end": 10.0, "path": "/tmp/w.mp4"}]
    temporal = {"speech_regions": [
        {"start": 1.0, "end": 3.0, "text": "hello world"},
    ]}
    out = vp.analyze_actions(analyzer, clips, 10.0, temporal, "")
    assert out[0].get("speech_quote_stripped") is None
    assert out[0]["actions"][0]["speech_cue"] == "mouth moving steadily"
