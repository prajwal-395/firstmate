"""Markers on the master: located by measurement, additive, reversible.

The captain's ruling of 2026-09-07 is "Let the pipeline add markers
only", so the two things this file has to pin are that the location is a
MEASUREMENT rather than a name match, and that a marker this writes can
be taken off again without touching one the captain typed.
"""
from pathlib import Path

import pytest

from library.tools import marker_payload
from library.tools.master_markers import (
    STATE_MARKER_COLOURS,
    MarkerRefused,
    Region,
    SourceWindow,
    assert_no_collision,
    coverage_regions,
    is_ours,
    locate_on_master,
    marker_for,
    merge,
)
from library.tools.resolve_organization import CURRENT, EARLIER, UNRECORDED

CAM = "/footage/LC4930.MXF"
OTHER = "/footage/LC4931.MXF"


def master_at(tl_start, src_in, src_out, path=CAM):
    return SourceWindow(path, src_in, src_out, tl_start)


# ------------------------------------------------------ locating a reel


def test_a_reel_is_located_by_source_overlap_not_by_name():
    """The reel's own timeline name never enters this: the master item
    carries the offset and the overlap is a fact about the footage."""
    master = [master_at(594, 3151, 3637)]
    reel = [SourceWindow(CAM, 3151, 3630, 0)]
    assert locate_on_master(reel, master) == [(594, 1073)]


def test_a_cut_outside_the_master_s_source_range_locates_nowhere():
    """Same file, different part of it: the master never played this."""
    master = [master_at(594, 3151, 3637)]
    assert locate_on_master([SourceWindow(CAM, 9000, 9100, 0)], master) == []


def test_a_partial_overlap_is_clipped_to_what_the_master_plays():
    master = [master_at(100, 1000, 1100)]
    assert locate_on_master([SourceWindow(CAM, 1050, 1200, 0)], master) == [
        (150, 200)]


# ------------------------------------------------------------- regions


def test_reels_sharing_footage_become_ONE_region_naming_both():
    """21 of the field test's reels end on the same call-to-action and
    map to one master frame. Resolve keeps one marker per frame, so a
    per-reel marker cannot express this and a per-region one can."""
    regions = coverage_regions([
        ("Reel A", CURRENT, [(100, 200)]),
        ("Reel B", EARLIER, [(150, 250)]),
        ("Reel C", CURRENT, [(900, 1000)]),
    ])
    assert [(r.start, r.end) for r in regions] == [(100, 250), (900, 1000)]
    assert regions[0].reels == (("Reel A", CURRENT), ("Reel B", EARLIER))
    assert regions[1].reels == (("Reel C", CURRENT),)


# ------------------------------------------------------------- markers


def a_marker(state=CURRENT, start=100, end=200):
    return marker_for(Region(start, end, (("Reel 01", state),)), 63694)


def test_a_marker_carries_the_state_colour_and_names_its_reels():
    marker = marker_for(
        Region(100, 250, (("Reel A", CURRENT), ("Reel B", EARLIER))), 63694)
    assert marker.frame == 100 and marker.duration == 150
    assert marker.colour == STATE_MARKER_COLOURS[CURRENT]
    assert "Reel A" in marker.note and "Reel B" in marker.note
    assert EARLIER in marker.note, "a colour must not hide the other state"
    assert marker.name == "2 reels use this"


def test_a_region_past_the_end_of_the_master_refuses():
    with pytest.raises(MarkerRefused) as refused:
        marker_for(Region(63000, 70000, (("Reel", CURRENT),)), 63694)
    assert "outside the master" in str(refused.value)


# ------------------------------------------------------- ours vs theirs


def test_a_marker_the_captain_typed_is_never_ours():
    """The whole basis of removal being exact."""
    assert is_ours("") is False
    assert is_ours("check this bit") is False
    assert is_ours("{not json") is False
    foreign = marker_payload.merge_record(marker_payload.new_envelope(), {
        "kind": "still", "writer": "capture_frame", "writer_version": 1,
        "id": "still_x", "at": "2026-09-07T00:00:00+00:00"})
    assert is_ours(marker_payload.dumps(foreign)) is False


def test_writing_onto_the_captains_marker_refuses():
    existing = {100: {"customData": "captain's own note"}}
    with pytest.raises(MarkerRefused) as refused:
        assert_no_collision([a_marker(start=100, end=200)], existing)
    assert "already has a marker" in str(refused.value)


def test_writing_over_this_pipelines_own_marker_is_allowed():
    """A second run must be able to REPLACE its own markers, or it can
    never be re-run."""
    existing = {100: {"customData": a_marker().custom_data}}
    assert assert_no_collision([a_marker(start=100, end=200)], existing) is None


# ------------------------------------------- the master is read-only else


FORBIDDEN = (
    "MoveClips(", "SetClipColor(", "ClearClipColor(", "SetMetadata(",
    "SetName(", "DeleteTimelines(", "DeleteClips(", "SetSetting(",
    "AppendToTimeline(", "DeleteClipsInTimeline(", "SetStartFrame(",
    "AddTrack(", "DeleteTrack(", "SetTrackName(", "Render(",
    "AddRenderJob(", "ImportFusionComp(", "SetCurrentTimecode(",
)


@pytest.mark.parametrize("module", [
    "library/tools/master_markers.py",
    "library/tools/execution/mark_master.py",
])
def test_nothing_here_can_change_the_master_except_its_markers(module):
    """The captain authorised markers and nothing else. Asserted of the
    SOURCE, because a reviewer cannot see a call that is not there."""
    source = Path(module).read_text(encoding="utf-8")
    executable = "".join(source.split('"""')[::2])
    for call in FORBIDDEN:
        assert call not in executable, (
            f"{module} calls {call} - the master is read-only except for "
            f"markers (the captain's ruling of 2026-09-07)")


def test_the_executor_only_ever_calls_the_three_marker_methods():
    source = Path("library/tools/execution/mark_master.py").read_text(
        encoding="utf-8")
    executable = "".join(source.split('"""')[::2])
    assert "AddMarker(" in executable
    assert "DeleteMarkerAtFrame(" in executable
    assert "DeleteMarkersByColor(" not in executable, (
        "deleting by colour would take the captain's own green markers")
