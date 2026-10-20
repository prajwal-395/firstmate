"""The disposable configurations a qualification run covers.

AXI-026 asks for each candidate primitive to run against varied
disposable timeline configurations; AXI-027 asks for empty AND
populated tracks; AXI-028 asks for the clips that already carry linked
audio, transitions, transforms, grades, retiming and Fusion state;
AXI-029 asks for the common frame rates and portrait as well as
landscape.

These tests name the gap: the enumeration the live runs used before
this one covered a single timeline shape, so a qualification earned on
it said nothing about any other.
"""
from __future__ import annotations

from library.tools import axi_qualification_configurations as configurations
from library.tools import axi_qualification_dimensions as dimensions


def _ids() -> set[str]:
    return {configuration.id for configuration in configurations.CONFIGURATIONS}


def test_every_configuration_has_its_own_name():
    identifiers = [configuration.id
                   for configuration in configurations.CONFIGURATIONS]
    assert len(identifiers) == len(set(identifiers))
    assert configurations.configuration_by_id("empty_1080p24").id == \
        "empty_1080p24"


def test_an_unknown_configuration_is_refused_by_name():
    import pytest

    with pytest.raises(configurations.ConfigurationError,
                       match="no qualification configuration"):
        configurations.configuration_by_id("not_a_configuration")


def test_the_enumeration_covers_empty_and_populated_tracks():
    """AXI-027: both are states an operation has to be qualified in."""
    occupancies = {configurations.track_occupancy(configuration)
                   for configuration in configurations.CONFIGURATIONS}
    assert "all_empty" in occupancies
    assert "all_populated" in occupancies
    assert "mixed" in occupancies


def test_the_enumeration_covers_the_effects_a_clip_may_already_carry():
    """AXI-028: the states where an edit can destroy work."""
    effects = {effect for configuration in configurations.CONFIGURATIONS
               for effect in configuration.effects}
    assert {"transitions", "transforms", "grades", "retiming", "fusion"} \
        <= effects
    kinds = {kind for configuration in configurations.CONFIGURATIONS
             for kind, _source in configuration.media}
    assert "linked_av" in kinds
    assert "still" in kinds
    assert "caption" in kinds


def test_the_enumeration_covers_the_common_frame_rates():
    """AXI-029: a rate is chosen for a reason the geometry does not carry."""
    covered = set(configurations.COVERED_FRAME_RATES)
    assert {"23.976", "24", "25", "29.97", "30", "60"} <= covered
    for rate in covered:
        assert rate in dimensions.FRAME_RATES


def test_the_enumeration_covers_portrait_and_landscape():
    """AXI-029: a project turned on its side is a different project."""
    settings = set(configurations.COVERED_PROJECT_SETTINGS)
    assert "landscape_1920x1080" in settings
    assert "portrait_1080x1920" in settings
    assert "landscape_3840x2160" in settings


def test_every_configuration_declares_a_shape_it_can_measure():
    """A declaration the measurement cannot produce is a closed route."""
    for configuration in configurations.CONFIGURATIONS:
        for field, value in configurations.shape_of(configuration).items():
            assert dimensions.value_in_vocabulary(field, value), (
                configuration.id, field, value)


def test_the_shape_is_derived_from_the_recipe_and_not_asserted():
    """The declaration cannot drift from the recipe that produced it."""
    for configuration in configurations.CONFIGURATIONS:
        shape = configurations.shape_of(configuration)
        assert shape["track_layout"] == configurations.track_layout(
            configuration)
        assert shape["track_occupancy"] == configurations.track_occupancy(
            configuration)
        assert shape["timeline_structure"] == (
            configurations.timeline_structure(configuration))
        assert shape["timeline_density"] == (
            configurations.timeline_density(configuration))


def test_a_configuration_names_a_pool_source_for_every_item_it_places():
    """A configuration that places an item it cannot source is refused."""
    for configuration in configurations.CONFIGURATIONS:
        placed = {kind for row in configuration.rows for kind in row.items
                  if kind != "linked_audio"}
        sourced = {kind for kind, _source in configuration.media}
        assert placed <= sourced, (configuration.id, placed - sourced)


def test_the_uncovered_values_name_what_the_coverage_did_not_reach():
    covered = {"media_type": ["video"]}
    uncovered = configurations.uncovered_values(covered)
    assert "still" in uncovered["media_type"]
    assert "video" not in uncovered["media_type"]
