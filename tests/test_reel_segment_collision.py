"""Two reels that share a closer SHARE a file; two that differ must not.

The captain's format closes every reel on a spoken call to action taken
from anywhere in the episode, so several reels legitimately carry the
SAME `source_clip_id` and the same source span. Measured on the field
test: seven of nineteen reels close on one identical sentence and five
more on another.

Under the old timeline-carrying identity those twelve closers were a
collision: reel 09 re-rendered over reel 03's file, reel 03's manifest
still pointed at that path, and
`compile_manifest._assert_subtitle_overlay_matches_plan` PASSED - it
compares block position and time span and reads no content. The captain
saw the wrong words on screen with every check green.

Under the provenance-plus-content identity the same twelve closers are
the SHARING: same source span plus same pixels means one filename, one
render, twelve placements. What is refused now is one filename behind
TWO content keys - a drawing input that escaped the digest - because
that is the overwrite nothing downstream would catch.

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
is under test - that two reels sharing ANY span share the file is.
"""

import json
from pathlib import Path

import pytest

from library.tools.subtitle_segment_id import (
    SegmentNameCollision, assert_no_content_collision, segment_binding,
    segment_identifier, timeline_scope,
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

CLOSER_DIGEST = "c" * 64
OTHER_DIGEST = "d" * 64


def _binding(timeline):
    return segment_binding(timeline=timeline, **CLOSER)


def test_reels_sharing_one_closer_share_one_filename():
    """The old collision is the new sharing.

    Every reel in a pass gets its own timeline value, and all of them
    compute the same filename for identical closer pixels. The readable
    half carries no timeline, so the sixteen real reel names cannot
    separate what the digest already proved identical - which is what
    renders the shared closer ONCE instead of sixteen times.
    """
    names = {segment_identifier(_binding(n), CLOSER_DIGEST)
             for n in _field_test_reel_names()}
    assert len(names) == 1, names


def test_a_closer_with_different_words_never_shares_the_filename():
    """The overwrite the old guard existed to stop, under the new
    identity: a reel and the master captioning the same source span
    with different words digest differently, so no render overwrites
    the other and the wrong-words-on-screen failure stays dead."""
    reel = segment_identifier(_binding("Reel 09"), CLOSER_DIGEST)
    master = segment_identifier(_binding("Studio Chat"), OTHER_DIGEST)
    assert reel != master


def test_one_filename_behind_two_content_keys_is_refused():
    """The guard, over the whole set, before anything is written.

    One shared filename with two content keys means a drawing input
    escaped the digest, and the second render would overwrite the first
    while `_assert_subtitle_overlay_matches_plan` - block position and
    time span only - passes. Refused here, naming the filename and both
    keys, because "duplicate segment name" sends a reader to the wrong
    place: the name is a symptom and the escaped input is the cause.
    """
    name = segment_identifier(_binding(REEL_NAMES[0]), CLOSER_DIGEST)
    with pytest.raises(SegmentNameCollision) as exc:
        assert_no_content_collision([
            (name, f"{CLOSER_DIGEST}+fp+carriage"),
            (name, f"{OTHER_DIGEST}+fp+carriage"),
        ])
    message = str(exc.value)
    assert name in message
    assert "different pixels" in message


def test_the_master_timeline_needs_no_discriminator():
    """A project with ONE timeline shares its captions with its reels
    wherever the pixels agree - the master path renders once and every
    identical reel placing pairs back to it."""
    assert timeline_scope({"structure": []}, project_config=None) == ""
    master = segment_identifier(
        segment_binding(timeline=None, speaker=None,
                        block_position="body_1",
                        source_clip_id="clip_011", source_start=1.0,
                        source_end=2.0),
        CLOSER_DIGEST)
    reel = segment_identifier(
        segment_binding(timeline="Reel 01", speaker=None,
                        block_position="body_3",
                        source_clip_id="clip_011", source_start=1.0,
                        source_end=2.0),
        CLOSER_DIGEST)
    assert master == reel
