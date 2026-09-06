"""F7 holds a card that ends with its block - and NOTHING wider.

`manifest_validator`'s note on `MIN_CAPTION_DISPLAY_SECONDS` already
decided this: a card that is the last of its spine block and ends where
that block ends cannot be lengthened by any grouping, so it is "counted
and named, and does not fail the build".  The reel path counted them as
defects instead.  Measured on the nineteen approved reels of
`lucie/geo-podcast`: 38 of 832 derived cards, ALL 38 satisfying both
clauses - the same 4.6% the master shows after the grouping fix, which is
the count being wrong rather than the reels.

The exemption is "deliberately the narrowest one that is provable ...
because a floor that exempts the general case is a gate that cannot
fail", so these tests are mostly about what is NOT exempt.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from library.tools import manifest_validator, reel_conformance_verifier
from library.tools.manifest_validator import BLOCK_END_TOLERANCE_SECONDS
from library.tools.reel_conformance_verifier import check_short_captions

FPS = 30.0


def _card(start, end, block="1", block_end=None, text="hi"):
    return {"reel_start": start, "reel_end": end, "text": text,
            "speaker": None, "frames": max(int(round((end - start) * FPS)), 1),
            "block_position": block, "block_end": block_end}


def _split(cards):
    findings = check_short_captions("Reel 01 - x", cards, FPS)
    return ([f for f in findings if f.severity == "error"],
            [f for f in findings if f.severity == "warning"])


def test_the_predicate_is_shared_not_copied():
    """One enumeration: the reel path calls the manifest's own predicate.

    Two copies of a rule is how a reel and a master come to be held to
    different floors, which is the defect this replaced.
    """
    assert (reel_conformance_verifier.ends_with_its_block
            is manifest_validator.ends_with_its_block)


def test_a_card_ending_with_its_block_is_held_and_named():
    """Both clauses met: held, and reported rather than dropped."""
    errors, warnings = _split([
        _card(0.0, 1.4, block_end=2.0),
        _card(1.8, 2.0, block_end=2.0, text="flash"),
    ])
    assert not errors, [f.message for f in errors]
    assert len(warnings) == 1
    held = warnings[0].detail["held_by_block"]
    assert held == 1, warnings[0].message
    assert "flash" in warnings[0].message, (
        "a held card must be NAMED; a count that hides what it exempted "
        "is the vacuous gate this check exists to remove")


def test_last_in_its_block_but_not_at_the_block_end_still_fails():
    """Clause one alone is not the exemption.

    The last card of a block that still has room to extend into is an
    ordinary short card, and holding it would exempt the general case.
    """
    errors, warnings = _split([
        _card(0.0, 1.4, block_end=9.0),
        _card(1.8, 2.0, block_end=9.0, text="room to grow"),
    ])
    assert len(errors) == 1, [f.message for f in errors]
    assert not warnings


def test_ending_at_the_block_end_but_not_last_still_fails():
    """Clause two alone is not the exemption either.

    A card that another card of the same block outlasts is not the one
    nothing can lengthen.
    """
    errors, _ = _split([
        _card(1.9, 2.0, block_end=2.0, text="short but not last"),
        _card(1.0, 2.4, block_end=2.0),
    ])
    assert len(errors) == 1, [f.message for f in errors]
    assert "short but not last" in errors[0].message


def test_a_card_with_no_block_is_not_exempt():
    """No block, no exemption - never a default that lets a card through."""
    errors, warnings = _split([
        {"reel_start": 1.8, "reel_end": 2.0, "text": "orphan",
         "speaker": None, "frames": 6},
    ])
    assert len(errors) == 1
    assert not warnings


def test_the_tolerance_is_one_frame_not_a_band():
    """The block-end clause is a frame's slack, and widening it fails."""
    just_outside = 2.0 - (BLOCK_END_TOLERANCE_SECONDS * 2)
    errors, _ = _split([
        _card(0.0, 1.4, block_end=2.0),
        _card(just_outside - 0.2, just_outside, block_end=2.0, text="adrift"),
    ])
    assert len(errors) == 1, (
        "a card ending two frames clear of its block end is not a card "
        "that ends with its block")
