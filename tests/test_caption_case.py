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
    UnknownCaptionCase,
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
        # A typo used to lowercase the whole video silently. Which case
        # the copy is set in is the template's decision, and an
        # unrecognised value is not a licence to make it here.
        with pytest.raises(UnknownCaptionCase):
            apply_caption_case("Hello World", "bogus")

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


# ── Recorded corrections survive into the captions they were ordered into ──
# Reel 09, 2026-09-19: a model-proposed spelling ("Google Business
# profile" reads "Google Business Profile", lc-0046) carried recorded
# case into caption words AFTER the lowercase + reading transforms, so
# the reading-idempotency gate below refused the whole reel build.
# The first fix conformed every correction to house lowercase - which
# holds the gate but disobeys the six active CAPTAIN-said spellings
# (lc-0001, lc-0084..lc-0088: write the casing "in every caption").
# So corrections run after the transforms, and the reading restores
# the same store: a card is a fixed point of the reading
# (library/tools/caption_reading.py).

class TestCorrectionsConformToCaptionContract:
    def _spine(self, words):
        timed = []
        t = 0.0
        for w in words:
            timed.append({"word": w, "source_start": t,
                          "source_end": t + 0.3})
            t += 0.4
        return {
            "structure": [
                {
                    "block_type": "speech",
                    "position": 1,
                    "timeline_start": 0.0,
                    "timeline_end": t,
                    "source_start": 0.0,
                    "source_end": t,
                    "clip_id": "clip_001",
                    "alignment_method": "whisperx",
                    "word_timestamps": timed,
                    "content": {"text": " ".join(words)},
                }
            ]
        }

    def _assert_fixed_point(self, entries, project_folder):
        from library.tools import transcript_corrections
        from library.tools.caption_reading import (
            apply_caption_reading_text,
        )
        corrections = transcript_corrections.spelling_corrections(
            project_folder)
        for entry in entries:
            assert apply_caption_reading_text(
                entry["text"],
                corrections=corrections) == entry["text"], entry["text"]

    def test_case_bearing_correction_plans_and_reads_as_recorded(
            self, tmp_path):
        """A correction emitting recorded case does not refuse the plan."""
        from library.tools import transcript_corrections
        transcript_corrections.record_spelling(
            str(tmp_path), "Google Business profile",
            "Google Business Profile",
            "the product name's casing", proposed_by="model")
        spine = self._spine(
            ["So", "Google", "Business", "profile", "obviously"])
        result = generate_subtitles(spine, caption_case="lowercase",
                                    project_folder=str(tmp_path))
        entries = result["subtitle_plan"]["subtitle_entries"]
        assert len(entries) > 0
        all_text = " ".join(e["text"] for e in entries)
        assert "Google Business Profile" in all_text
        self._assert_fixed_point(entries, str(tmp_path))

    def test_single_token_correction_conforms_too(self, tmp_path):
        """The lucy->Lucie shape conforms the same way."""
        from library.tools import transcript_corrections
        transcript_corrections.record_spelling(
            str(tmp_path), "lucy", "Lucie", "the client's name")
        spine = self._spine(["go", "check", "out", "lucy", "today"])
        result = generate_subtitles(spine, caption_case="lowercase",
                                    project_folder=str(tmp_path))
        entries = result["subtitle_plan"]["subtitle_entries"]
        assert len(entries) > 0
        all_text = " ".join(e["text"] for e in entries)
        assert "Lucie" in all_text
        self._assert_fixed_point(entries, str(tmp_path))

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


# ── Synthetic project copies test ──

class TestSyntheticCopies:
    def test_all_synthetic_generic_copies_specify_lowercase(self):
        """Every synthetic generic copy sets caption_case: lowercase."""
        from tests.brand_fixtures import ALL_SYNTHETIC

        expected = [
            "synthetic_default",
            "synthetic_shortform",
            "synthetic_cinematic",
            "synthetic_interview",
        ]
        for name in expected:
            effect = ALL_SYNTHETIC[name].get("effect", {})
            assert effect.get("caption_case") == "lowercase", (
                f"Copy {name} should set caption_case: lowercase, "
                f"got: {effect.get('caption_case')!r}"
            )


# ── A project's own copy declares its own casing ──
# Q5, decided 2026-08-16: caption case is a per-template setting rather
# than a hidden global, and every project copy declares its CURRENT
# behaviour so nothing about any existing video changed. The mechanism
# landed earlier (#102); this pins the declarations so the decision
# is durable rather than incidental.

# Lowercase everywhere except the client copy: lowercase captions are
# the channel's voice, and a client's brand is not the channel's.
EXPECTED_CAPTION_CASE = {
    "synthetic_cinematic": "lowercase",
    "synthetic_default": "lowercase",
    "synthetic_interview": "lowercase",
    "synthetic_client": "as_written",
    "synthetic_shortform": "lowercase",
}


def _templates():
    from tests.brand_fixtures import ALL_SYNTHETIC
    for name in sorted(ALL_SYNTHETIC):
        yield name, ALL_SYNTHETIC[name]


def test_every_copy_is_accounted_for():
    assert {n for n, _ in _templates()} == set(EXPECTED_CAPTION_CASE), (
        "a project copy was added or removed without deciding its caption case")


def test_every_copy_declares_its_caption_case_explicitly():
    """A hidden global became a declared choice; keep it declared.

    Relying on the default would work, and would put the decision back
    where it was - implicit.
    """
    missing = [n for n, t in _templates()
               if "caption_case" not in (t.get("effect") or {})]
    assert not missing, (
        f"{missing} do not declare effect.caption_case. Every project "
        f"copy states its own casing rather than inheriting it.")


def test_the_declared_values_are_the_approved_ones():
    for name, tmpl in _templates():
        assert (tmpl.get("effect") or {})["caption_case"] == \
            EXPECTED_CAPTION_CASE[name], name


def test_no_template_declares_an_unsupported_case():
    valid = {"lowercase", "as_written"}
    for name, tmpl in _templates():
        value = (tmpl.get("effect") or {}).get("caption_case")
        assert value in valid, f"{name}: {value!r} is not one of {valid}"
