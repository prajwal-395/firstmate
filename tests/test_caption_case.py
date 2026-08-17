"""Tests for caption case as a brand template setting.

Covers:
- Lowercase from a template that sets caption_case: lowercase
- Source casing preserved when a template sets caption_case: as_written
- Omitted caption_case defaults to lowercase
- All shipped templates specify caption_case: lowercase
- The apply_caption_case helper function
"""
import os
import pytest

from library.steps.step_4_01_plan_subtitles.step import (
    apply_caption_case,
    generate_subtitles,
)
from library.schemas.brand_template import EffectSlots


# ── Minimal spine fixture ──
# A spine with one speech block carrying mixed-case word timestamps,
# enough to exercise the caption case path without needing a full
# pipeline state.

def _make_spine(text="Hello World", words=None):
    """Build a minimal audio spine with one speech block."""
    if words is None:
        word_list = text.split()
        words = []
        t = 0.0
        for w in word_list:
            words.append({
                "word": w,
                "source_start": t,
                "source_end": t + 0.3,
            })
            t += 0.4
    src_start = words[0]["source_start"]
    src_end = words[-1]["source_end"]
    return {
        "structure": [
            {
                "block_type": "speech",
                "position": 1,
                "timeline_start": 0.0,
                "timeline_end": src_end - src_start,
                "source_start": src_start,
                "source_end": src_end,
                "clip_id": "clip_001",
                "alignment_method": "whisperx",
                "word_timestamps": words,
                "content": {"text": text},
            }
        ]
    }


# ── apply_caption_case unit tests ──

class TestApplyCaptionCase:
    def test_lowercase_mode(self):
        assert apply_caption_case("Hello World", "lowercase") == "hello world"

    def test_as_written_mode(self):
        assert apply_caption_case("Hello World", "as_written") == "Hello World"

    def test_unknown_mode_defaults_to_lowercase(self):
        assert apply_caption_case("Hello World", "bogus") == "hello world"

    def test_empty_string(self):
        assert apply_caption_case("", "lowercase") == ""
        assert apply_caption_case("", "as_written") == ""


# ── generate_subtitles integration tests ──

class TestCaptionCaseInGenerateSubtitles:
    def test_lowercase_from_template(self):
        """A template that sets caption_case: lowercase produces lowercased text."""
        spine = _make_spine("Hello World")
        result = generate_subtitles(spine, caption_case="lowercase")
        entries = result["subtitle_plan"]["subtitle_entries"]
        assert len(entries) > 0
        for entry in entries:
            assert entry["text"] == entry["text"].lower(), (
                f"Expected lowercase but got: {entry['text']!r}"
            )
            for w in entry["words"]:
                assert w["word"] == w["word"].lower(), (
                    f"Expected lowercase word but got: {w['word']!r}"
                )

    def test_as_written_preserves_case(self):
        """A template that sets caption_case: as_written keeps source casing."""
        spine = _make_spine("Hello World")
        result = generate_subtitles(spine, caption_case="as_written")
        entries = result["subtitle_plan"]["subtitle_entries"]
        assert len(entries) > 0
        # The text should preserve the original casing from word timestamps
        all_text = " ".join(e["text"] for e in entries)
        assert "Hello" in all_text, (
            f"Expected 'Hello' with original casing, got: {all_text!r}"
        )
        all_words = [w["word"] for e in entries for w in e["words"]]
        assert "Hello" in all_words, (
            f"Expected 'Hello' in words, got: {all_words}"
        )

    def test_omitted_defaults_to_lowercase(self):
        """When caption_case is not passed, default is lowercase."""
        spine = _make_spine("Hello World")
        # Call without caption_case - should default to "lowercase"
        result = generate_subtitles(spine)
        entries = result["subtitle_plan"]["subtitle_entries"]
        assert len(entries) > 0
        for entry in entries:
            assert entry["text"] == entry["text"].lower(), (
                f"Default should lowercase, got: {entry['text']!r}"
            )

    def test_hook_block_respects_caption_case(self):
        """Hook blocks also honour the caption_case setting."""
        spine = {
            "structure": [
                {
                    "block_type": "hook",
                    "position": 0,
                    "timeline_start": 0.0,
                    "timeline_end": 0.7,
                    "source_start": 0.0,
                    "source_end": 0.7,
                    "clip_id": "clip_001",
                    "alignment_method": "whisperx",
                    "word_timestamps": [
                        {"word": "Check", "source_start": 0.0, "source_end": 0.3},
                        {"word": "This", "source_start": 0.3, "source_end": 0.6},
                    ],
                    "content": {"text": "Check This"},
                }
            ]
        }
        # as_written should preserve casing
        result = generate_subtitles(spine, caption_case="as_written")
        entries = result["subtitle_plan"]["subtitle_entries"]
        assert len(entries) > 0
        all_text = " ".join(e["text"] for e in entries)
        assert "Check" in all_text

        # lowercase should force lowercase
        result_lower = generate_subtitles(spine, caption_case="lowercase")
        entries_lower = result_lower["subtitle_plan"]["subtitle_entries"]
        for entry in entries_lower:
            assert entry["text"] == entry["text"].lower()


# ── Schema default tests ──

class TestEffectSlotsDefault:
    def test_default_caption_case_is_lowercase(self):
        """EffectSlots defaults caption_case to 'lowercase'."""
        slots = EffectSlots()
        assert slots.caption_case == "lowercase"

    def test_from_dict_without_caption_case(self):
        """BrandTemplate.from_dict with no caption_case still defaults to lowercase."""
        from library.schemas.brand_template import BrandTemplate
        data = {
            "series_id": "test",
            "effect": {
                "vfx_intensity": 0.5,
                "sfx_density": "moderate",
            },
        }
        bt = BrandTemplate.from_dict(data)
        assert bt.effect.caption_case == "lowercase"


# ── Shipped templates test ──

class TestShippedTemplates:
    def test_all_shipped_templates_specify_lowercase(self):
        """All four shipped templates set caption_case: lowercase."""
        try:
            import yaml
        except ImportError:
            pytest.skip("PyYAML not installed")

        templates_dir = os.path.join(
            os.path.dirname(os.path.dirname(__file__)),
            "library", "templates",
        )
        expected = [
            "default_brand.yaml",
            "shortform_energetic.yaml",
            "cinematic_narrative.yaml",
            "interview_professional.yaml",
        ]
        for name in expected:
            path = os.path.join(templates_dir, name)
            assert os.path.exists(path), f"Missing shipped template: {name}"
            with open(path, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f)
            effect = data.get("effect", {})
            assert effect.get("caption_case") == "lowercase", (
                f"Template {name} should set caption_case: lowercase, "
                f"got: {effect.get('caption_case')!r}"
            )


# ── The shipped library declares its own casing ──
# Q5, decided 2026-08-16: caption case is a per-template setting rather
# than a hidden global, and every shipped template declares its CURRENT
# behaviour so nothing about any existing video changed. The mechanism
# landed earlier (#102); this pins the four declarations so the decision
# is durable rather than incidental.

import glob
import os

import yaml

_TEMPLATE_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "library", "templates")

# All four lowercase, which is what they already produced.
EXPECTED_CAPTION_CASE = {
    "cinematic_narrative": "lowercase",
    "default_brand": "lowercase",
    "fourth_wall": "lowercase",
    "interview_professional": "lowercase",
    "shortform_energetic": "lowercase",
}


def _templates():
    for path in sorted(glob.glob(os.path.join(_TEMPLATE_DIR, "*.yaml"))):
        with open(path, encoding="utf-8") as f:
            yield os.path.basename(path)[:-5], yaml.safe_load(f) or {}


def test_every_template_is_accounted_for():
    assert {n for n, _ in _templates()} == set(EXPECTED_CAPTION_CASE), (
        "a template was added or removed without deciding its caption case")


def test_every_template_declares_its_caption_case_explicitly():
    """A hidden global became a declared choice; keep it declared.

    Relying on the default would work, and would put the decision back
    where it was - implicit.
    """
    missing = [n for n, t in _templates()
               if "caption_case" not in (t.get("effect") or {})]
    assert not missing, (
        f"{missing} do not declare effect.caption_case. Every shipped "
        f"template states its own casing rather than inheriting it.")


def test_the_declared_values_are_the_approved_ones():
    for name, tmpl in _templates():
        assert (tmpl.get("effect") or {})["caption_case"] == \
            EXPECTED_CAPTION_CASE[name], name


def test_no_template_declares_an_unsupported_case():
    valid = {"lowercase", "as_written"}
    for name, tmpl in _templates():
        value = (tmpl.get("effect") or {}).get("caption_case")
        assert value in valid, f"{name}: {value!r} is not one of {valid}"
