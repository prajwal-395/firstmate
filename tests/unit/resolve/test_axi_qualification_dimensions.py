"""The qualification dimensions: what a predicate may name, and what it means.

A qualification predicate says "in what state may this operation run?".
The dimension vocabulary is that question's answer, and it has to be a
property of a STATE - never of a project, a timeline or a fixture. The
three shipped records that named `seed_aroll_plus_placed_cover` and
`video + audio` could never satisfy their own predicates: the route
read as open and was closed.

These tests name that failure and the fix.
"""
from __future__ import annotations

import pytest

from library.tools import axi_qualification_dimensions as dimensions


def _clip(**overrides) -> dict:
    clip = {
        "unique_id": "item-1",
        "name": "clip.mp4",
        "media_type": "Video",
        "linked_unique_ids": [],
        "track_type": "video",
        "track_index": 1,
        "record_in": 0,
        "record_out": 48,
        "source_in_frame": 0,
        "source_out_frame": 48,
        "left_offset": 0,
        "right_offset": 0,
        "enabled": True,
        "speed": 100.0,
        "transform": {},
        "color": {},
        "fusion": {"comp_count": 0, "comp_names": []},
        "item_type": "video",
    }
    clip.update(overrides)
    return clip


def _row(*clips, track_type: str = "video", index: int = 1) -> dict:
    return {"type": track_type, "index": index, "name": "",
            "clips": list(clips)}


# ── The vocabulary ───────────────────────────────────────────────────


def test_a_predicate_may_name_a_dimension_and_nothing_else():
    """The predicate fields ARE the dimensions - one enumeration."""
    for field in dimensions.DIMENSIONS:
        assert dimensions.is_dimension(field)
    for field in ("project_name", "timeline_name", "fixture", "clip_name"):
        assert not dimensions.is_dimension(field)
        assert dimensions.is_identity_field(field) == (
            field in dimensions.IDENTITY_FIELDS)


def test_every_dimension_but_the_resolve_version_has_a_closed_vocabulary():
    """A claim the measurement cannot produce is refused at the door."""
    for field in dimensions.DIMENSIONS:
        if field == "resolve_version":
            assert dimensions.vocabulary(field) is None
            assert dimensions.value_in_vocabulary(field, "21.1.0") is True
            continue
        vocabulary = dimensions.vocabulary(field)
        assert vocabulary, field
        for value in vocabulary:
            assert dimensions.value_in_vocabulary(field, value)
        assert not dimensions.value_in_vocabulary(field, "not-a-value")


def test_the_fixture_prose_the_old_records_carried_is_not_a_value():
    """The three shipped records named values no measurement emits."""
    for field, value in (("clip_composition", "seed_aroll_plus_placed_cover"),
                         ("clip_composition", "two_caption_cards"),
                         ("linked_audio_configuration", "linked_av_aroll_untouched"),
                         ("linked_audio_configuration", "no_audio_items"),
                         ("timeline_structure", "seed_plus_cover_plus_subtitles_row"),
                         ("timeline_structure", "empty_fixture"),
                         ("timeline_structure", "single_seeded_clip_empty_baseline"),
                         ("media_type", "video + audio")):
        assert not dimensions.value_in_vocabulary(field, value), (field, value)


# ── The measurement ──────────────────────────────────────────────────


def test_the_media_kind_is_the_measurement_not_the_pool_spelling():
    """Resolve answers "Video + Audio"; the dimension answers "video"."""
    assert dimensions.media_kind(_clip(media_type="Video")) == "video"
    assert dimensions.media_kind(_clip(media_type="Video + Audio")) == "video"
    assert dimensions.media_kind(_clip(media_type="Still")) == "still"
    assert dimensions.media_kind(_clip(media_type="Audio")) == "audio"
    assert dimensions.media_kind(_clip(media_type=None)) == "unknown"


def test_a_request_that_spans_two_kinds_is_mixed():
    """A request that touches more than one kind matches no evidence."""
    chosen = [_clip(unique_id="a"), _clip(unique_id="b", media_type="Still")]
    assert dimensions.media_type(chosen, []) == "mixed"


def test_a_request_that_creates_its_own_item_declares_its_kind():
    """An insert names no existing item, so it declares what it touches."""
    operations = [{"op": "clip.insert", "track_type": "video"}]
    assert dimensions.media_type([], operations) == "video"
    assert dimensions.clip_composition([]) == "none"


def test_a_linked_clip_and_a_plain_one_are_different_compositions():
    """Linked audio is an ARRANGEMENT, and the composition says so."""
    assert dimensions.clip_composition([_clip()]) == "single_video_clip"
    assert dimensions.clip_composition(
        [_clip(linked_unique_ids=["audio-1"])]) == "linked_video_clip"
    assert dimensions.clip_composition(
        [_clip(media_type="Still")]) == "still_clip"
    assert dimensions.clip_composition(
        [_clip(media_type="Audio", track_type="audio")]) == "audio_clip"
    assert dimensions.clip_composition(
        [_clip(media_type=None)]) == "unknown_item"


def test_a_linked_item_the_row_read_did_not_resolve_counts_as_linked():
    """The measurement cannot claim the qualified condition, so it refuses."""
    chosen = [_clip(linked_unique_ids=["audio-1"])]
    assert dimensions.linked_audio_configuration(chosen, {}) == \
        "linked_audio_present"
    by_id = {"audio-1": _clip(unique_id="audio-1", media_type="Audio",
                              track_type="audio")}
    assert dimensions.linked_audio_configuration(chosen, by_id) == \
        "linked_audio_present"
    assert dimensions.linked_audio_configuration([_clip()], by_id) == \
        "no_linked_audio"


def test_an_empty_track_and_a_populated_one_are_different_layouts():
    """AXI-027: both are states an operation has to be qualified in."""
    empty = [_row()]
    populated = [_row(_clip())]
    assert dimensions.track_layout(empty) == "video"
    assert dimensions.track_layout(populated) == "video"
    assert dimensions.track_occupancy(empty) == "all_empty"
    assert dimensions.track_occupancy(populated) == "all_populated"
    assert dimensions.track_occupancy([_row(_clip()), _row()]) == "mixed"
    assert dimensions.track_layout(
        [_row(_clip()), _row(track_type="audio", index=1)]) == "video+audio"


def test_the_density_bands_count_items_and_not_rows():
    """A timeline with ten empty rows is not denser than one with an item."""
    rows = [_row() for _ in range(10)]
    assert dimensions.item_count(rows) == 0
    assert dimensions.timeline_density(rows) == "empty"
    assert dimensions.timeline_structure(rows) == "empty_timeline"
    assert dimensions.timeline_density([_row(_clip())]) == "light"
    assert dimensions.timeline_structure([_row(_clip())]) == \
        "single_item_timeline"
    assert dimensions.timeline_density([_row(_clip(), _clip())]) == "light"
    assert dimensions.timeline_structure([_row(_clip(), _clip())]) == \
        "multi_item_timeline"


def test_the_project_settings_say_which_way_the_frame_is_turned():
    """AXI-029: portrait as well as landscape."""
    assert dimensions.project_settings(1920, 1080) == "landscape_1920x1080"
    assert dimensions.project_settings(1080, 1920) == "portrait_1080x1920"
    assert dimensions.project_settings(1920, 1920) == "square_1920x1920"
    assert dimensions.project_settings(0, 0) == "unknown"
    assert dimensions.orientation(1920, 1080) == "landscape"


@pytest.mark.parametrize("reported,expected", [
    (24, "24"), (23.976, "23.976"), (25, "25"), (29.97, "29.97"),
    (30, "30"), (50, "50"), (59.94, "59.94"), (60, "60"),
    (12, "other"),
])
def test_the_frame_rate_is_named_the_way_the_dimension_names_it(
        reported, expected):
    assert dimensions.frame_rate(reported) == expected


def test_the_existing_effects_are_what_the_item_already_carries():
    """AXI-028: the states where an edit can destroy work."""
    assert dimensions.existing_effects([_clip()]) == "none"
    assert dimensions.existing_effects(
        [_clip(transform={"Pan": 40.0})]) == "transforms"
    assert dimensions.existing_effects(
        [_clip(color={"cdl": {"Slope": "1.0 1.0 1.0"}})]) == "grades"
    assert dimensions.existing_effects([_clip(speed=50.0)]) == "retiming"
    assert dimensions.existing_effects(
        [_clip(item_type="transition")]) == "transitions"
    assert dimensions.existing_effects(
        [_clip(fusion={"comp_count": 1,
                       "comp_names": ["Fusion Composition"]})]) == "none"
    assert dimensions.existing_effects(
        [_clip(fusion={"comp_count": 1, "comp_names": ["Treatment"]})]) == \
        "fusion"
    assert dimensions.existing_effects(
        [_clip(transform={"Pan": 40.0}, speed=50.0)]) == \
        "transforms+retiming"


def test_a_pass_through_comp_is_not_a_treatment():
    """Resolve gives every item a comp; a comp that draws nothing is not one."""
    passthrough = {"comp_count": 1, "comp_names": ["Fusion Composition"],
                   "media_windows": [{"tools": ["MediaIn", "MediaOut",
                                                "AudioDisplay"]}]}
    assert dimensions.existing_effects([_clip(fusion=passthrough)]) == "none"
    drawing = {"comp_count": 1, "comp_names": ["Fusion Composition"],
               "media_windows": [{"tools": ["MediaIn", "BrightnessContrast",
                                            "MediaOut"]}]}
    assert dimensions.existing_effects([_clip(fusion=drawing)]) == "fusion"


def test_the_predicates_are_derived_from_a_coverage():
    """The coverage is the claim; the predicates are that claim, one per
    dimension."""
    coverage = {field: [value] for field, value
                in (("media_type", "video"),
                    ("clip_composition", "single_video_clip"))}
    predicates = dimensions.predicates_for(coverage)
    assert [predicate["field"] for predicate in predicates] == [
        "media_type", "clip_composition"]
    assert predicates[0] == {"field": "media_type", "operator": "equals",
                              "value": "video"}
    many = dimensions.predicates_for({"media_type": ["video", "still"]})
    assert many[0] == {"field": "media_type", "operator": "one_of",
                       "value": ["video", "still"]}
