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

The second change (2026-09-10): the name used to carry the TIMELINE as
the discriminator, so three variant timelines captioning the same words
rendered the same pixels three times.  The captain's ruling roots the
identity in PROVENANCE - the source footage for subtitles - and keys it
by content: `sub_<speaker>_<clip>_<span>_<digest>`.  No timeline names a
file any more.  Two variants captioning the same words compute the same
name (the sharing); anything that draws differently digests differently
and can never overwrite (the collision stays dead).
"""

from __future__ import annotations

import pytest

from library.tools.subtitle_segment_id import (
    PROVENANCE_STEM_KEYS,
    SEGMENT_BINDING_KEYS,
    assert_no_content_collision,
    SegmentNameCollision,
    provenance_stem,
    segment_binding,
    segment_identifier,
    slug,
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


def test_the_same_block_for_two_speakers_does_not_collide():
    akshita = _binding(speaker="Akshita")
    craig = _binding(speaker="Craig")
    assert _name(akshita) != _name(craig)


def test_the_same_block_over_two_source_spans_does_not_collide():
    first = _binding(source_start=131.42295, source_end=151.69320)
    second = _binding(source_start=207.33, source_end=212.96)
    assert _name(first) != _name(second)


def test_provenance_changes_the_name_but_placement_does_not():
    """No provenance component is decorative; no placement component is
    load-bearing.  Change speaker, clip or span, get a different stem;
    change timeline or block ordinal, get the same stem."""
    base = _binding()
    for key, value in (("speaker", "Craig"),
                       ("source_clip_id", "clip_004"),
                       ("source_start", 99.0),
                       ("source_end", 199.0)):
        other = _binding(**{key: value})
        assert provenance_stem(base) != provenance_stem(other), key
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


def test_a_digest_is_required_never_defaulted():
    """A provenance stem alone names WHERE the speech came from but not
    WHICH pixels - two different captions over one span would share a
    filename.  The digest is required, and \"\" is refused rather than
    hashed into a name that skips nothing and proves nothing."""
    with pytest.raises(ValueError):
        segment_identifier(_binding(), "")


# ── What the captain said the fix must NOT be ────────────────────────

def test_a_bigger_number_would_not_have_fixed_it():
    """Two segments differing ONLY in ordinal collided before; two
    differing only in TIMELINE shared nothing before and share the
    file now.  A wider ordinal separates neither case that matters."""
    master = _binding(timeline="Studio Chat - Synced", block_position="body_1")
    reel = _binding(timeline="Reel 01 - geography", block_position="body_1")
    assert master["block_position"] == reel["block_position"]
    assert _name(master) == _name(reel)


# ── Absence is recorded, never dropped ───────────────────────────────

def test_an_absent_component_is_named_not_omitted():
    """Two different absences must not both become the empty string."""
    no_speaker = _binding(speaker=None)
    no_clip = _binding(source_clip_id=None)
    assert "nospeaker" in _name(no_speaker)
    assert "noclip" in _name(no_clip)
    assert _name(no_speaker) != _name(no_clip)


def test_a_missing_binding_key_is_refused_rather_than_defaulted():
    incomplete = _binding()
    del incomplete["speaker"]
    with pytest.raises(ValueError) as excinfo:
        segment_identifier(incomplete, DIGEST_A)
    assert "speaker" in str(excinfo.value)


def test_slug_requires_the_caller_to_name_the_absence():
    assert slug(None, "nospeaker") == "nospeaker"
    assert slug("   ", "nospeaker") == "nospeaker"
    assert slug("!!!", "nospeaker") == "nospeaker"
    assert slug("Akshita Rao", "nospeaker") == "akshita-rao"


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


def test_one_name_behind_one_content_key_passes():
    """The sharing the identity exists for: three variants, one name,
    one key - refused nothing, rendered once."""
    name = _name()
    assert_no_content_collision([(name, f"{DIGEST_A}+fp+carriage")] * 3)


# ── Which timeline a placing belongs to ──────────────────────────────

def test_a_measured_spine_names_its_own_timeline():
    spine = {"derived_from": {"timeline": "Studio Chat - Synced"}}
    assert timeline_scope(spine) == "Studio Chat - Synced"


def test_a_declaration_is_used_when_nothing_was_measured():
    from types import SimpleNamespace
    config = SimpleNamespace(resolve=SimpleNamespace(timeline_name="Main Edit"))
    assert timeline_scope({}, project_config=config) == "Main Edit"


def test_a_measurement_beats_a_declaration():
    from types import SimpleNamespace
    config = SimpleNamespace(resolve=SimpleNamespace(timeline_name="Main Edit"))
    spine = {"derived_from": {"timeline": "Studio Chat - Synced"}}
    assert timeline_scope(spine, project_config=config) == "Studio Chat - Synced"


def test_no_timeline_name_is_invented():
    assert timeline_scope({}, project_config=None) == ""


# ── A slug breaks on a word boundary, never mid-word ─────────────────

def test_a_truncated_slug_breaks_on_a_word_boundary():
    """Reel 05's caption names broke mid-word (`invisible-o`, `envisio`,
    `goo`): the slug hard-cut at the length limit. A slug longer than the
    limit ends at the last word boundary inside it instead."""
    assert slug("Reel 05 - the-audit-that-was-eye-opening",
               "notimeline") == "reel-05-the-audit-that-was-eye"




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


def test_stable_prefix_keeps_two_spans_distinct():
    """Two segments off one clip stay two keys: the span is IN the
    prefix, so re-keying a pin on it can never bind the neighbour."""
    assert (stable_prefix(
        "sub_craig_341446bc-389b-468c-9add_1853716-1855056_1f0a29bf")
        != stable_prefix(
            "sub_craig_341446bc-389b-468c-9add_1855196-1856821_6b66c72d"))


def test_stable_prefix_leaves_non_subtitle_ids_whole():
    """A motion-graphics name is ALL digest past the project - there
    is no safe prefix to take, so none is taken. A kind default and
    a bare prefix are already keys, not ids, and pass through."""
    assert stable_prefix("mg_geo-podcast_622f69cb") == "mg_geo-podcast_622f69cb"
    assert stable_prefix("caption") == "caption"
    assert (stable_prefix("sub_craig_341446bc-389b-468c-9add_1853716-1855056")
            == "sub_craig_341446bc-389b-468c-9add_1853716-1855056")
    assert stable_prefix(None) == ""
