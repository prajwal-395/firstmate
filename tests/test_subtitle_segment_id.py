"""A rendered subtitle segment's name BINDS it to what it captions.

The captain reported the defect (2026-09-04): rendered overlays were
named `sub_block_<block_position>`, an ordinal within one spine, written
into a directory that is per PROJECT and not per timeline.  Two failures
in one name, and they are tested separately because a fix for either
alone is not a fix:

- a master and a reel both have a `body_1`, so one overwrote the other,
- and no part of the name said whose speech it captioned or which span
  of source audio it came from.

`test_a_bigger_number_would_not_have_fixed_it` is the one that pins the
captain's actual instruction - the fix is a binding, not a wider ordinal.
"""

from __future__ import annotations

import pytest

from library.tools.subtitle_segment_id import (
    SEGMENT_BINDING_KEYS,
    binding_digest,
    segment_binding,
    segment_identifier,
    slug,
    timeline_scope,
)


def _binding(**overrides):
    base = dict(
        timeline="GEO Podcast - Synced",
        speaker="Akshita",
        block_position="body_1",
        source_clip_id="clip_003",
        source_start=131.42295,
        source_end=151.69320,
    )
    base.update(overrides)
    return segment_binding(**base)


# ── The collision the captain reported ───────────────────────────────

def test_the_same_block_on_two_timelines_does_not_collide():
    master = _binding(timeline="GEO Podcast - Synced")
    reel = _binding(timeline="Reel 01 - geography")
    assert segment_identifier(master) != segment_identifier(reel)


def test_the_same_block_for_two_speakers_does_not_collide():
    akshita = _binding(speaker="Akshita")
    craig = _binding(speaker="Craig")
    assert segment_identifier(akshita) != segment_identifier(craig)


def test_the_same_block_over_two_source_spans_does_not_collide():
    first = _binding(source_start=131.42295, source_end=151.69320)
    second = _binding(source_start=207.33, source_end=212.96)
    assert segment_identifier(first) != segment_identifier(second)


def test_every_binding_component_changes_the_name():
    """No component is decorative: change any one, get a different name."""
    base = _binding()
    changed = {
        "timeline": "Reel 02 - rivers",
        "speaker": "Craig",
        "block_position": "body_2",
        "source_clip_id": "clip_004",
        "source_start": 99.0,
        "source_end": 199.0,
    }
    for key, value in changed.items():
        other = _binding(**{key: value})
        assert segment_identifier(base) != segment_identifier(other), key


# ── What the captain said the fix must NOT be ────────────────────────

def test_a_bigger_number_would_not_have_fixed_it():
    """Two segments differing ONLY in ordinal collided before; two
    differing only in TIMELINE are what actually collided in the field.
    A wider ordinal separates the first and not the second."""
    master = _binding(timeline="GEO Podcast - Synced", block_position="body_1")
    reel = _binding(timeline="Reel 01 - geography", block_position="body_1")
    assert master["block_position"] == reel["block_position"]
    assert segment_identifier(master) != segment_identifier(reel)


def test_the_name_carries_speaker_and_timeline_readably():
    name = segment_identifier(_binding())
    assert "akshita" in name
    assert "geo-podcast-synced" in name


# ── Absence is recorded, never dropped ───────────────────────────────

def test_an_absent_component_is_named_not_omitted():
    """Two different absences must not both become the empty string."""
    no_speaker = _binding(speaker=None)
    no_block = _binding(block_position=None)
    assert "nospeaker" in segment_identifier(no_speaker)
    assert "noblock" in segment_identifier(no_block)
    assert segment_identifier(no_speaker) != segment_identifier(no_block)


def test_a_missing_binding_key_is_refused_rather_than_defaulted():
    incomplete = _binding()
    del incomplete["speaker"]
    with pytest.raises(ValueError) as excinfo:
        segment_identifier(incomplete)
    assert "speaker" in str(excinfo.value)


def test_slug_requires_the_caller_to_name_the_absence():
    assert slug(None, "nospeaker") == "nospeaker"
    assert slug("   ", "nospeaker") == "nospeaker"
    assert slug("!!!", "nospeaker") == "nospeaker"
    assert slug("Akshita Rao", "nospeaker") == "akshita-rao"


# ── Stability, so a rebuild overwrites ITSELF ────────────────────────

def test_the_name_is_stable_across_rebuilds():
    assert segment_identifier(_binding()) == segment_identifier(_binding())


def test_the_digest_survives_a_json_round_trip():
    """A float that goes through JSON must not move the digest, or a
    rebuild would stop overwriting itself and start accumulating."""
    import json
    binding = _binding()
    revived = json.loads(json.dumps(binding))
    assert binding_digest(binding) == binding_digest(revived)


def test_binding_keys_are_the_whole_enumeration():
    assert set(_binding()) == set(SEGMENT_BINDING_KEYS)


# ── Which timeline a render belongs to ───────────────────────────────

def test_a_measured_spine_names_its_own_timeline():
    spine = {"derived_from": {"timeline": "GEO Podcast - Synced"}}
    assert timeline_scope(spine) == "GEO Podcast - Synced"


def test_a_declaration_is_used_when_nothing_was_measured():
    from types import SimpleNamespace
    config = SimpleNamespace(resolve=SimpleNamespace(timeline_name="Main Edit"))
    assert timeline_scope({}, project_config=config) == "Main Edit"


def test_a_measurement_beats_a_declaration():
    from types import SimpleNamespace
    config = SimpleNamespace(resolve=SimpleNamespace(timeline_name="Main Edit"))
    spine = {"derived_from": {"timeline": "GEO Podcast - Synced"}}
    assert timeline_scope(spine, project_config=config) == "GEO Podcast - Synced"


def test_no_timeline_name_is_invented():
    assert timeline_scope({}, project_config=None) == ""
    assert "notimeline" in segment_identifier(_binding(timeline=""))


# ── A slug breaks on a word boundary, never mid-word ─────────────────

def test_a_truncated_slug_breaks_on_a_word_boundary():
    """Reel 05's caption names broke mid-word (`invisible-o`, `envisio`,
    `goo`): the slug hard-cut at the length limit. A slug longer than the
    limit ends at the last word boundary inside it instead."""
    assert slug("Reel 05 - the-audit-that-was-eye-opening",
               "notimeline") == "reel-05-the-audit-that-was-eye"


def test_a_slug_within_the_limit_is_untouched():
    assert slug("Akshita Rao", "nospeaker") == "akshita-rao"
    assert slug("Reel 01 - geography", "notimeline") == "reel-01-geography"


def test_a_single_word_longer_than_the_limit_keeps_its_cut():
    """One unbreakable word has no boundary to break on. The hard cut
    stays - uniqueness never rested on the readable half, which is what
    the digest is for - it just never splits a word that a boundary
    could have saved."""
    from library.tools.subtitle_segment_id import _SLUG_MAX
    long_word = "a" * (_SLUG_MAX + 8)
    cut = slug(long_word, "x")
    assert len(cut) <= _SLUG_MAX
    assert cut == "a" * _SLUG_MAX
