"""AXI-005: the delete and the ripple are qualified where a real edit is.

Defect, measured on the AXI-11 suite's own fixture (2026-10-21):
four of its ten ordinary editorial requests died in the delete
preflight, and the refusal named the FIXTURE rather than the
operation::

    clip.delete: runtime state does not satisfy recorded qualification
    predicates: timeline_density is 'moderate', not 'light';
    linked_audio_configuration is 'linked_audio_present', not
    'no_linked_audio'; clip_composition is 'linked_video_clip', not
    'single_video_clip'; track_occupancy is 'all_populated', not 'mixed'

    clip.remove: runtime state does not satisfy recorded qualification
    predicates: timeline_density is 'moderate', not 'light';
    existing_effects is 'retiming', not 'none'

The recorded predicates described the ORIGINAL qualification fixture:
one or two clips, on one row, with no linked audio and no effects.  A
real edit is none of those things - the A-roll is a LINKED A/V clip,
the timeline holds several populated rows, and a clip the edit touches
may carry a retime, a Fusion composition or the offset a caption card
always carries.  So the delete was refused on real work while reading
as qualified on the fixture.

These tests name the defect from the STATE side: the states the AXI-11
fixture actually holds, and the boundary that must admit them.  The
states are the ones the live lane measured on the same fixture shape
(`tests/live/axi_primitive_qualification.py`), so the claim here is
about the recorded boundary, not about a guess.

The other half of the defect is a boundary that must NOT open
wholesale: a state nobody demonstrated is still refused.
"""

from __future__ import annotations

import pytest

from library.tools import edit_algebra, execution_router
from library.tools.execution_router import RoutingRefused

RESOLVE_VERSION = "21.1.0.14"


def _state(**overrides) -> dict:
    """A measured state, in the recorded predicate vocabulary."""
    state = {
        "resolve_version": RESOLVE_VERSION,
        "media_type": "video",
        "clip_composition": "single_video_clip",
        "linked_audio_configuration": "no_linked_audio",
        "track_layout": "video+audio",
        "track_occupancy": "mixed",
        "timeline_density": "light",
        "timeline_structure": "multi_item_timeline",
        "project_settings": "landscape_1920x1080",
        "frame_rate": "24",
        "existing_effects": "none",
    }
    state.update(overrides)
    return state


#: The AXI-11 fixture's states, as the lane's own refusals named them.
LINKED_AV_MODERATE = _state(
    media_type="video",
    clip_composition="linked_video_clip",
    linked_audio_configuration="linked_audio_present",
    track_occupancy="all_populated",
    timeline_density="moderate",
)
RETIMED_MODERATE = _state(
    clip_composition="linked_video_clip",
    linked_audio_configuration="linked_audio_present",
    track_occupancy="all_populated",
    timeline_density="moderate",
    existing_effects="retiming",
)
STILL_CARD = _state(
    media_type="still",
    clip_composition="still_clip",
    existing_effects="retiming",
)


def _routes(operation: str, state: dict):
    """Route one operation against one measured state."""
    return execution_router.route_compiled_edit(
        [{"op": operation, "unique_id": "axc-target"}],
        context={"qualification_policy": "production",
                 "qualification_state": {"inspected": True,
                                         "state": state}})


@pytest.mark.parametrize("state", [LINKED_AV_MODERATE, RETIMED_MODERATE,
                                   STILL_CARD])
def test_the_delete_is_routable_in_the_states_the_axc_fixture_holds(state):
    """Defect: the delete was refused on the AXI-11 fixture.

    The refusal named the fixture's complexity, not a limit of the
    operation: the states below are the ones the delete was MEASURED in
    on a live scratch timeline, so the recorded boundary has to admit
    them.
    """
    decision = _routes("clip.delete", state)

    assert decision.backend is not None
    assert "clip.delete" not in [
        gap[0] for gap in decision.qualification_gaps]


def test_the_ripple_is_routable_on_a_moderate_multi_track_timeline():
    """Defect: `clip.remove` refused a 'moderate' timeline as 'light'.

    The B-roll removal on the AXI-11 fixture is a ripple on a timeline
    that holds more than the two clips the original fixture held, so
    the recorded boundary has to admit the state it was measured in.
    """
    decision = _routes("clip.remove", LINKED_AV_MODERATE)

    assert decision.backend is not None
    assert "clip.remove" not in [
        gap[0] for gap in decision.qualification_gaps]


def test_the_widened_boundary_still_refuses_a_state_nobody_demonstrated():
    """The boundary is widened to what was MEASURED, and no further.

    A dense timeline, a portrait project and a fractional frame rate
    were not demonstrated on the delete, so a state that carries them
    is still refused - a qualification that opens wholesale is not a
    qualification.
    """
    undemonstrated = _state(
        timeline_density="dense",
        project_settings="portrait_1080x1920",
        frame_rate="23.976",
        existing_effects="grades+fusion")

    with pytest.raises(RoutingRefused):
        _routes("clip.delete", undemonstrated)


def test_the_delete_boundary_names_the_states_it_was_measured_in():
    """The recorded boundary is readable: it names what it admits.

    A row that says "a timeline" says nothing, and a row that names a
    project is a pointer to the test.  The delete's row names DIMENSION
    values, and the values it names are the ones the live lane measured.
    """
    row = edit_algebra.status("clip.delete").for_backend("resolve_axi")

    assert row is not None
    fields = {predicate["field"] for predicate in row.qualification_predicates}
    assert {"timeline_density", "existing_effects", "linked_audio_configuration",
            "clip_composition", "track_occupancy"} <= fields
    for predicate in row.qualification_predicates:
        assert predicate["field"] != "timeline_name"
        assert predicate["field"] != "project_name"


def test_the_ripple_boundary_names_the_states_it_was_measured_in():
    """The ripple's row names the same dimensions the delete's does."""
    row = edit_algebra.status("clip.remove").for_backend("resolve_axi")

    assert row is not None
    fields = {predicate["field"] for predicate in row.qualification_predicates}
    assert {"timeline_density", "existing_effects",
            "linked_audio_configuration", "track_occupancy"} <= fields
