"""Two reels that share a closer must not share a filename.

The captain's format closes every reel on a spoken call to action taken
from anywhere in the episode, so several reels legitimately carry the
SAME `source_clip_id` and the same source span. Measured on the field
test: seven of nineteen reels close on one identical sentence and five
more on another, so a 19-reel pass collides on twelve of them.

`SEGMENT_BINDING_KEYS` carries no caption content, so across reels the
only thing separating two closers is the TIMELINE component - and
`timeline_scope` returns `''` for a spine with no timeline recorded,
which `slug` renders `notimeline`.

The consequence is the worst kind. Reel 09 re-renders over reel 03's
file, reel 03's manifest still points at that path, and
`compile_manifest._assert_subtitle_overlay_matches_plan` PASSES - it
compares block position and time span and reads no content. The captain
sees the wrong words on screen with every check green.

The reel names below are the captain's real ones, and all sixteen live
in `tests/fixtures/field_test_16_reel_names.json`, copied verbatim from
the field test's own report. They are IN THE REPOSITORY on purpose: this
test first read that report out of firstmate's private data directory,
which exists on the captain's machine and nowhere else, so it skipped
silently everywhere else while reporting green. A condition that depends
on a path outside the repository is not an environment, and the fixture
is what makes the check run on every machine.
The shared CTA span is constructed: the plan carrying `call_to_action`
ranges is not on this machine, and the span's exact numbers are not what
is under test - that two reels sharing ANY span collide is.
"""

import json
from pathlib import Path

import pytest

from library.tools.subtitle_segment_id import (
    SegmentNameCollision, UnnamedReelTimeline, assert_named_timeline,
    assert_unique_segment_names, segment_binding, segment_identifier,
    timeline_scope,
)

FIELD_TEST_REEL_NAMES = (
    Path(__file__).parent / "fixtures" / "field_test_16_reel_names.json")


def _field_test_reel_names() -> list:
    """All sixteen names the field test produced, from the fixture.

    Never from a path outside this repository: that is what made this
    check an always-skip on every machine but one.
    """
    return json.loads(FIELD_TEST_REEL_NAMES.read_text())["reel_names"]


# The captain's real reels, verbatim.
REEL_NAMES = [
    "Reel 01 - geo-is-not-seo-2-0",
    "Reel 02 - prove-it-the-h1-audit",
    "Reel 03 - why-your-pipeline-dried-up",
    "Reel 04 - the-lucy-audit-walkthrough",
]

# One closer, shared. The shape a shared CTA really has: same clip, same
# source seconds, same block position within each reel.
CLOSER = {"source_clip_id": "clip_004", "source_start": 742.1,
          "source_end": 746.9, "block_position": "closer", "speaker": None}


def _binding(timeline):
    return segment_binding(timeline=timeline, **CLOSER)


def test_the_collision_is_real_when_the_timeline_is_unnamed():
    """The bug, reproduced rather than described.

    Both sides use `""`, which is what `timeline_scope` really returns
    for a spine with no timeline recorded - so every reel in a pass gets
    the same value and they all collide with each other.

    Note `None` and `""` do NOT collide with one another: both slug to
    `notimeline` in the readable part, but `binding_digest` hashes the
    raw values, so the digests differ. The readable half says they are
    the same segment and the digest says otherwise, which is worth
    knowing but is not the failure here - a real pass gets one value,
    not a mix.
    """
    unnamed = timeline_scope({"structure": []}, project_config=None)
    a, b = segment_identifier(_binding(unnamed)), segment_identifier(
        _binding(unnamed))
    assert a == b, "this test is worthless if these ever stop colliding"
    assert "notimeline" in a


def test_naming_the_reel_separates_them():
    names = {segment_identifier(_binding(n)) for n in REEL_NAMES}
    assert len(names) == len(REEL_NAMES), names


def test_a_set_of_reels_sharing_one_closer_is_REFUSED_when_unnamed():
    """The guard, over the whole set, before anything is written."""
    unnamed = timeline_scope({"structure": []}, project_config=None)
    with pytest.raises(SegmentNameCollision) as exc:
        assert_unique_segment_names([_binding(unnamed) for _ in REEL_NAMES])
    message = str(exc.value)
    assert "would be written to" in message
    # It must name the CAUSE, not just the symptom: identical bindings.
    assert "bindings are identical" in message


def test_the_same_set_PASSES_once_each_reel_is_named():
    """The guard must be able to pass, or it is not a guard."""
    assert_unique_segment_names([_binding(n) for n in REEL_NAMES])


def test_a_collision_that_is_NOT_about_the_timeline_is_caught_too():
    """The set-level guard does not depend on knowing what a reel is."""
    same = dict(CLOSER)
    with pytest.raises(SegmentNameCollision) as exc:
        assert_unique_segment_names([
            segment_binding(timeline="Reel 01", **same),
            segment_binding(timeline="Reel 01", **same),
        ])
    assert "Reel 01" in str(exc.value)


def test_an_unnamed_reel_timeline_is_refused_by_name():
    """An empty discriminator silently disabling uniqueness is the same
    shape as an empty expected side making a comparison vacuous."""
    for empty in (None, "", "   "):
        with pytest.raises(UnnamedReelTimeline) as exc:
            assert_named_timeline(_binding(empty), where="test")
        assert "must name its timeline" in str(exc.value)


def test_a_named_reel_timeline_is_accepted():
    for name in REEL_NAMES:
        assert_named_timeline(_binding(name), where="test")


def test_the_master_timeline_may_still_be_unnamed():
    """A project with ONE timeline needs no discriminator, and project
    001 really has none - refusing there would break the master path."""
    assert timeline_scope({"structure": []}, project_config=None) == ""
    assert_unique_segment_names([
        segment_binding(timeline=None, speaker=None, block_position=p,
                        source_clip_id="clip_011", source_start=float(p),
                        source_end=float(p) + 1.0)
        for p in range(8)
    ])


def test_all_sixteen_real_reel_names_stay_distinct_after_slugging():
    """Slugging truncates at 32 chars and strips punctuation, so two long
    reel names could collapse to one slug even when both are named."""
    names = _field_test_reel_names()
    assert len(names) == 16
    assert_unique_segment_names([_binding(n) for n in names])
