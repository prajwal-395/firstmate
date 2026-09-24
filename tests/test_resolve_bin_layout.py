"""The canonical bin layout and the reel versioning convention.

AGENTS.md 10.4 cuts both ways here: a test that passes whatever the code
does is coverage theatre, so every case below has a failing side - a
duplicate bin that must not be recreated, a banned suffix that must be
named, a version that must increment past the highest taken rather than
counting rows.
"""
from __future__ import annotations

import pytest

from library.tools.project_layout import Area
from library.tools.resolve_bin_layout import (
    BANNED_SUFFIXES,
    BIN_PATHS,
    BINS,
    banned_suffix,
    bins_to_create,
    format_reel_name,
    next_version,
    parse_reel_name,
)


def test_source_bin_keeps_the_captains_name():
    source = next(b for b in BINS if b.area == Area.RAW)
    assert source.path == ("Source footage",)


def test_bins_to_create_is_lookup_first():
    existing = {("01 - Source",), ("05 - Reels",)}
    missing = bins_to_create(existing)
    assert ("01 - Source",) not in missing
    assert ("05 - Reels",) not in missing
    assert ("05 - Reels", "Archive") in missing
    assert ("02 - Music",) in missing
    # Parents come before their children.
    assert missing.index(("05 - Reels", "Archive")) > 0


def test_bins_to_create_can_come_back_empty():
    assert bins_to_create(set(BIN_PATHS)) == []


def test_parse_reel_name_reads_a_version():
    assert parse_reel_name("Reel 20 - search-didnt-change-the-question-did v003") == (
        "Reel 20 - search-didnt-change-the-question-did", 3)


def test_parse_reel_name_leaves_an_unversioned_name_alone():
    assert parse_reel_name("GEO Podcast - Synced") == ("GEO Podcast - Synced", None)


def test_every_banned_suffix_is_detected():
    for suffix in BANNED_SUFFIXES:
        assert banned_suffix(f"Reel 01 - slug ({suffix})") == suffix
    assert banned_suffix("Reel 01 - slug v002") is None
    assert banned_suffix("GEO Podcast - Synced") is None


def test_format_reel_name_zero_pads():
    assert format_reel_name(20, "search-didnt-change-the-question-did", 3) == (
        "Reel 20 - search-didnt-change-the-question-did v003")


def test_format_reel_name_refuses_version_zero():
    with pytest.raises(ValueError):
        format_reel_name(20, "slug", 0)


def test_next_version_passes_the_highest_taken():
    names = ["Reel 20 - slug v001", "Reel 20 - slug v003",
             "Reel 20 - other v009"]
    assert next_version("Reel 20 - slug", names) == 4


def test_next_version_starts_at_one():
    assert next_version("Reel 20 - slug", ["Reel 20 - slug (harvest)"]) == 1
