"""A rendered subtitle segment's name BINDS it to what it captions.

Identity is PROVENANCE keyed by content - `sub_<speaker>_<clip>_<span>_<digest>`:
no timeline or block ordinal names a file, so variants captioning the same
words share one render, and different pixels can never overwrite each other.
History (the `sub_block_<n>` collision, the timeline discriminator):
docs/evidence/subtitle_segment_id.md.
"""

from __future__ import annotations

import pytest

from library.tools.subtitle_segment_id import (
    assert_no_content_collision,
    SegmentNameCollision,
    provenance_stem,
    segment_binding,
    segment_identifier,
    slug,
    speaker_slug_from_segment_id,
    stable_prefix,
    timeline_scope,
)


def _binding(**overrides):
    base = dict(
        timeline="Studio Chat - Synced",
        speaker="Akshita",
        block_position="body_1",
        source_clip_id="clip_003",
        source_start=131.42295,
        source_end=151.69320,
    )
    base.update(overrides)
    return segment_binding(**base)


DIGEST_A = "a" * 64
DIGEST_B = "b" * 64


def _name(binding=None, digest=DIGEST_A):
    return segment_identifier(_binding() if binding is None else binding,
                              digest)


# ── The collision the captain reported ───────────────────────────────

def test_two_placements_of_different_pixels_never_share_a_name():
    """A reel can never overwrite the master's caption again.

    Same source span, different words: the digest differs, so the
    filenames differ, so no render overwrites the other.  This is the
    ordinal bug staying dead under the new identity.
    """
    assert _name() != _name(digest=DIGEST_B)


def test_provenance_changes_the_name_but_placement_does_not():
    """No provenance component is decorative; no placement component is
    load-bearing.  Change speaker, clip or span, get a different stem;
    change timeline or block ordinal, get the same stem - a wider ordinal
    would have separated neither case that matters."""
    base = _binding()
    for key, value in (("speaker", "Craig"),
                       ("source_clip_id", "clip_004"),
                       ("source_start", 99.0),
                       ("source_end", 199.0)):
        other = _binding(**{key: value})
        assert provenance_stem(base) != provenance_stem(other), key
        assert _name(base) != _name(other), key
    for key, value in (("timeline", "Reel 02 - rivers"),
                       ("block_position", "body_2")):
        other = _binding(**{key: value})
        assert provenance_stem(base) == provenance_stem(other), key
        assert _name(base) == _name(other), key


# ── The timeline left the identity entirely ──────────────────────────

def test_no_timeline_names_a_file():
    """Three variant timelines captioning the same words compute the
    same filename.  That sameness IS the cross-variant sharing - not a
    collision - and it is what the old timeline discriminator split
    apart into three renders of identical pixels."""
    variants = [
        "Reel 09 - your-website-is-only-20-percent (rebuild staging)",
        "Reel 09 - your-website-is-only-20-percent (j-cut)",
        "Reel 09 - your-website-is-only-20-percent (reaction-cutaway)",
    ]
    names = {_name(_binding(timeline=v)) for v in variants}
    assert len(names) == 1, names
    for name in names:
        assert "reel" not in name
        assert "j-cut" not in name
        assert "staging" not in name


def test_the_name_carries_speaker_and_source_span_readably():
    name = _name()
    assert "akshita" in name
    assert "clip-003" in name
    assert "131423-151693" in name


def test_speaker_reader_handles_current_and_legacy_names():
    current = segment_identifier(
        _binding(source_clip_id="b191411a-d2bf-4549-a09b"), DIGEST_A)
    legacy = ("sub_reel-17_akshita_body0_3135634-3141184_"
              "f32a24c3.mov")

    assert speaker_slug_from_segment_id(f"/rendered/{current}.mov") == \
        "akshita"
    assert speaker_slug_from_segment_id(legacy) == "akshita"
    assert speaker_slug_from_segment_id("sub_nospeaker_clip_0-1000_abc") \
        is None


def test_a_digest_and_every_binding_key_are_required_never_defaulted():
    """A provenance stem alone names WHERE the speech came from but not
    WHICH pixels, so "" is refused as a digest; a missing key is refused
    by name rather than defaulted."""
    with pytest.raises(ValueError):
        segment_identifier(_binding(), "")
    incomplete = _binding()
    del incomplete["speaker"]
    with pytest.raises(ValueError, match="speaker"):
        segment_identifier(incomplete, DIGEST_A)


# ── Absence is recorded, never dropped ───────────────────────────────

def test_an_absent_component_is_named_not_omitted():
    """Two different absences must not both become the empty string."""
    no_speaker = _binding(speaker=None)
    no_clip = _binding(source_clip_id=None)
    assert "nospeaker" in _name(no_speaker)
    assert "noclip" in _name(no_clip)
    assert _name(no_speaker) != _name(no_clip)


def test_slug_names_the_absence_and_truncates_on_a_word_boundary():
    assert slug(None, "nospeaker") == "nospeaker"
    assert slug("   ", "nospeaker") == "nospeaker"
    assert slug("!!!", "nospeaker") == "nospeaker"
    assert slug("Akshita Rao", "nospeaker") == "akshita-rao"
    # A truncated slug breaks on a word boundary, never mid-word (Reel
    # 05's `invisible-o`, `envisio`, `goo`) ...
    assert slug("Reel 05 - the-audit-that-was-eye-opening",
                "notimeline") == "reel-05-the-audit-that-was-eye"
    # ... and one unbreakable word keeps its hard cut (uniqueness rests on
    # the digest, not the readable half).
    from library.tools.subtitle_segment_id import _SLUG_MAX
    assert slug("a" * (_SLUG_MAX + 8), "x") == "a" * _SLUG_MAX


# ── Stability, so a rebuild overwrites ITSELF ────────────────────────







# ── One filename, two pixels is refused ──────────────────────────────

def test_one_name_behind_two_content_keys_is_refused():
    """The guard the timeline discriminator used to be: a shared
    filename with two content keys means a drawing input escaped the
    digest, and the second render would overwrite the first with
    nothing downstream reading content to notice."""
    name = _name()
    with pytest.raises(SegmentNameCollision) as exc:
        assert_no_content_collision([
            (name, f"{DIGEST_A}+fp+carriage"),
            (name, f"{DIGEST_B}+fp+carriage"),
        ])
    assert name in str(exc.value)
    # The sharing the identity exists for: one name, one key, passes.
    assert_no_content_collision([(name, f"{DIGEST_A}+fp+carriage")] * 3)


# ── Which timeline a placing belongs to ──────────────────────────────

def test_timeline_scope_prefers_a_measurement_over_a_declaration():
    from types import SimpleNamespace
    config = SimpleNamespace(resolve=SimpleNamespace(timeline_name="Main Edit"))
    spine = {"derived_from": {"timeline": "Studio Chat - Synced"}}
    assert timeline_scope(spine) == "Studio Chat - Synced"
    assert timeline_scope({}, project_config=config) == "Main Edit"
    assert timeline_scope(spine, project_config=config) == "Studio Chat - Synced"
    assert timeline_scope({}, project_config=None) == ""  # never invented


def test_stable_prefix_is_provenance_not_pixels():
    """The re-renderable half of a subtitle id: speaker, clip, span.

    Pins key on this (`overlay_intent`), because a re-render changes
    the digest and nothing else. The 2026-09-13 wipe proved it: every
    one of the captain's caption pins died on the digest while its
    prefix was still live.
    """
    assert stable_prefix(
        "sub_craig_341446bc-389b-468c-9add_1853716-1855056_1f0a29bf"
    ) == "sub_craig_341446bc-389b-468c-9add_1853716-1855056"
    assert stable_prefix(
        "sub_craig_341446bc-389b-468c-9add_1853716-1855056_fdc48282"
    ) == "sub_craig_341446bc-389b-468c-9add_1853716-1855056"
    # Two spans off one clip stay two keys: re-keying a pin can never
    # bind the neighbour.
    assert (stable_prefix(
        "sub_craig_341446bc-389b-468c-9add_1853716-1855056_1f0a29bf")
        != stable_prefix(
            "sub_craig_341446bc-389b-468c-9add_1855196-1856821_6b66c72d"))
    # A motion-graphics name is ALL digest past the project, and a kind
    # default or a bare prefix is already a key: left whole.
    assert stable_prefix("mg_geo-podcast_622f69cb") == "mg_geo-podcast_622f69cb"
    assert stable_prefix("caption") == "caption"
    assert (stable_prefix("sub_craig_341446bc-389b-468c-9add_1853716-1855056")
            == "sub_craig_341446bc-389b-468c-9add_1853716-1855056")
    assert stable_prefix(None) == ""
