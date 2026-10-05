"""The dirty-region scoped re-check (gap E8).

The defect this closes: after a `ren touch`, the scoped re-check re-ran
only the cheap located detectors (black, freeze, silence). A touch that
moved a caption card off the speech its filename declares, or swapped in
B-roll that does not illustrate the speech, passed the scoped re-check -
it was a technical-QA re-check, not a full verification re-check.

Each test names that defect: a caption timing error and a B-roll
correspondence failure the scoped re-check now REPORTS, scoped to the
dirty spans, and a check that is not this touch's because it is outside
them.
"""

from unittest.mock import patch

import pytest

from library.skills.verify_render import skill


FPS = 24.0


def _dirty(core_start, core_end, domain="picture"):
    """A scoped dirty block whose CORE is `[core_start, core_end]`."""
    return {
        "whole_reel": False,
        "whole_reel_reason": "",
        "dirty_domains": [domain],
        "dirty_spans": [{
            "domain": domain,
            "start_seconds": core_start - 0.5,
            "end_seconds": core_end + 0.5,
            "core_start_seconds": core_start,
            "core_end_seconds": core_end,
        }],
        "fps": FPS,
        "first_frame": 0,
    }


def _timeline_with_card(card_name, reel_in, reel_out):
    """One speech clip, one transcript segment, one caption card.

    The card declares source 10.0-11.0s (from its filename); the audio
    clip plays source 10-12s at reel 0-2s, so the card's declared speech
    plays at reel 0.2-1.1s. Placed anywhere past ~1.6s it is displaced.
    """
    source_file = "/audio/a.wav"
    timeline = {
        "metadata": {"fps": FPS, "name": "mini"},
        "tracks": [
            {"type": "audio", "index": 1, "name": "Dialogue",
             "clips": [{"file_path": source_file, "name": "a.wav",
                        "record_in": 0, "record_out": int(2.0 * FPS),
                        "source_in": int(10.0 * FPS)}]},
            {"type": "video", "index": 3, "name": "Subtitles",
             "clips": [{"file_path": f"/caps/{card_name}",
                        "name": card_name,
                        "record_in": int(reel_in * FPS),
                        "record_out": int(reel_out * FPS)}]},
        ],
    }
    transcript = {
        "segments": [{
            "source_file": source_file,
            "source_start": 10.0, "source_end": 12.0,
            "timeline_start": 10.0,
            "speaker": "Alice", "text": "hello world",
            "words": [{"word": "hello", "start": 10.2, "end": 10.6},
                      {"word": "world", "start": 10.7, "end": 11.1}],
        }],
    }
    return timeline, transcript


class _StubModel:
    """A VLM stub: returns a canned reply, records what it was asked."""

    MODEL_ID = "stub-model"

    def __init__(self, reply='{"illustrates": "yes", "reason": "ok"}'):
        self.reply = reply
        self.calls = []

    def analyze_images(self, image_paths, prompt, max_tokens=120):
        self.calls.append({"image_paths": list(image_paths), "prompt": prompt})
        return self.reply


def _broll_timeline():
    """One speech clip playing source 0-20s at reel 0-20s, words at 5-7s."""
    source_file = "/audio/a.wav"
    timeline = {
        "metadata": {"fps": FPS, "name": "mini"},
        "tracks": [
            {"type": "audio", "index": 1, "name": "Dialogue",
             "clips": [{"file_path": source_file, "name": "a.wav",
                        "record_in": 0, "record_out": int(20.0 * FPS),
                        "source_in": 0}]},
        ],
    }
    transcript = {
        "segments": [{
            "source_file": source_file,
            "source_start": 0.0, "source_end": 20.0,
            "timeline_start": 0.0,
            "speaker": "Alice", "text": "the launch failed",
            "words": [{"word": "the", "start": 5.0, "end": 5.4},
                      {"word": "launch", "start": 5.5, "end": 6.0},
                      {"word": "failed", "start": 6.1, "end": 6.6}],
        }],
    }
    return timeline, transcript


def _manifest_with_cutaway(timeline_in, timeline_out, label="cutaway_1"):
    return {
        "tracks": {
            "V1": {"clips": [{
                "clip_name": "a_roll",
                "source_file": "/footage/a.mov",
                "source_in": 0.0, "source_out": 20.0,
                "timeline_in": 0.0, "timeline_out": 20.0,
            }]},
            "V2": {"clips": [{
                "clip_name": label,
                "source_file": "/footage/b.mov",
                "source_in": 100.0, "source_out": 104.0,
                "timeline_in": timeline_in,
                "timeline_out": timeline_out,
            }]},
        },
    }


def test_a_late_caption_card_is_reported_by_the_scoped_recheck():
    """A touch that moves a caption card 2s late is caught by the scoped
    re-check - the defect: it passed when the re-check was technical-QA
    only (black/freeze/silence).
    """
    card = "sub_alice_clipa_10000-11000_ab12cd34.mov"
    timeline, transcript = _timeline_with_card(card, 5.0, 5.9)
    dirty = _dirty(5.0, 5.9)

    results, not_rechecked = skill.run_scoped_recheck(
        "video.mp4", dirty, timeline=timeline, transcript=transcript)

    pairing = [r for r in results if r.metric == "heard_caption_pairing"]
    assert len(pairing) == 1
    # Report-only: the failure is reported, it does not block.
    assert pairing[0].passed is True
    assert pairing[0].severity == "warning"
    assert len(pairing[0].value["mispaired"]) == 1
    assert pairing[0].value["mispaired"][0]["kind"] == "displaced"
    assert "REPORTED ONLY" in pairing[0].detail
    # No manifest and no overlay segments: those checks are named, not passed.
    assert "broll_correspondence" in not_rechecked
    assert "caption_quality" in not_rechecked


def test_a_non_illustrating_cutaway_is_reported_by_the_scoped_recheck():
    """A touch that swaps in B-roll that does not illustrate the speech is
    caught by the scoped re-check - the defect: it passed when the
    re-check was technical-QA only.
    """
    timeline, transcript = _broll_timeline()
    manifest = _manifest_with_cutaway(5.0, 7.0)
    dirty = _dirty(5.0, 7.0)
    model = _StubModel('{"illustrates": "no", "reason": "shows a wall"}')

    with patch("library.tools.window_frames.draw_strip",
               return_value=True):
        results, not_rechecked = skill.run_scoped_recheck(
            "video.mp4", dirty, assembly_manifest=manifest,
            timeline=timeline, transcript=transcript, model=model)

    broll = [r for r in results if r.metric == "broll_correspondence"]
    assert len(broll) == 1
    assert broll[0].passed is True
    assert broll[0].severity == "warning"
    assert len(broll[0].value["non_illustrating"]) == 1
    assert broll[0].value["non_illustrating"][0]["illustrates"] is False
    assert "REPORTED ONLY" in broll[0].detail
    # The VLM was asked about the cutaway the touch could have broken.
    assert len(model.calls) == 1
    assert "caption_quality" in not_rechecked


def test_the_scoped_recheck_is_scoped_to_the_dirty_spans():
    """A non-illustrating cutaway OUTSIDE the dirty spans is not this
    touch's - the re-check is scoped to what the touch changed.
    """
    timeline, transcript = _broll_timeline()
    manifest = {
        "tracks": {
            "V1": {"clips": [{
                "clip_name": "a_roll",
                "source_file": "/footage/a.mov",
                "source_in": 0.0, "source_out": 20.0,
                "timeline_in": 0.0, "timeline_out": 20.0,
            }]},
            "V2": {"clips": [
                {"clip_name": "cutaway_in",
                 "source_file": "/footage/b.mov",
                 "source_in": 100.0, "source_out": 104.0,
                 "timeline_in": 5.0, "timeline_out": 7.0},
                {"clip_name": "cutaway_out",
                 "source_file": "/footage/c.mov",
                 "source_in": 100.0, "source_out": 104.0,
                 "timeline_in": 20.0, "timeline_out": 22.0},
            ]},
        },
    }
    dirty = _dirty(5.0, 7.0)
    model = _StubModel('{"illustrates": "no", "reason": "unrelated"}')

    with patch("library.tools.window_frames.draw_strip",
               return_value=True):
        results, _ = skill.run_scoped_recheck(
            "video.mp4", dirty, assembly_manifest=manifest,
            timeline=timeline, transcript=transcript, model=model)

    broll = [r for r in results if r.metric == "broll_correspondence"][0]
    # Only the cutaway inside the dirty span was re-checked.
    assert broll.value["cutaways"] == 1
    assert len(broll.value["non_illustrating"]) == 1
    assert broll.value["non_illustrating"][0]["clip_name"] == "cutaway_in"
    assert len(model.calls) == 1


def test_a_touch_that_changes_no_picture_names_the_checks():
    """A touch that dirtied no picture broke no caption, cutaway or
    overlay - the three checks are named in not_rechecked, never passed.
    """
    timeline, transcript = _broll_timeline()
    dirty = _dirty(5.0, 7.0, domain="audio")

    results, not_rechecked = skill.run_scoped_recheck(
        "video.mp4", dirty, timeline=timeline, transcript=transcript)

    assert results == []
    assert not_rechecked == ["caption_pairing", "broll_correspondence",
                             "caption_quality"]
