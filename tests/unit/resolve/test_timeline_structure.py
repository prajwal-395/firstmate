"""QA-001: the whole timeline is read back and judged as a STRUCTURE.

AXI-013 judges a MUTATION by the engine's own comparison of two fresh
reads.  A whole-timeline outcome - a build, a touch-up, a revision of
more than one operation - is not a mutation: it is a STRUCTURE, and a
structure can be wrong in ways a delta cannot see.

The defect these tests name: a whole-timeline outcome was judged by the
writer that performed it, so a timeline that came back with a hole in
it, an item placed twice, a clip whose media is gone, or a link to
something that is not there was accepted on the writer's word.

Each test below seeds ONE structural defect on a scratch timeline and
shows the engine's own read-back naming it.  The last one is the
control: a clean edit passes.
"""

from __future__ import annotations

import os

from library.tools import timeline_structure


def _clip(unique_id, record_in, record_out, **over):
    """One timeline item, in the shape a live snapshot carries."""
    clip = {
        "unique_id": unique_id,
        "name": unique_id,
        "record_in": record_in,
        "record_out": record_out,
        "duration": record_out - record_in,
        "source_in_frame": 0,
        "source_out_frame": record_out - record_in,
        "source_frames": 1000,
        "source_file": f"/media/{unique_id}.mov",
        "media_pool_item_id": f"media-{unique_id}",
        "media_type": "video",
        "track_type": "video",
        "enabled": True,
        "speed": 100.0,
        "transform": {},
        "linked_unique_ids": [],
    }
    clip.update(over)
    return clip


def _snapshot(*clips, media_type="video", index=1, name="V1",
              timeline="Reel 01", **over):
    """A timeline snapshot: one row holding these items."""
    snapshot = {
        "timeline": timeline,
        "project": "Project",
        "start_frame": 0,
        "end_frame": 1000,
        "tracks": [{"type": media_type, "index": index, "name": name,
                    "clips": [dict(clip) for clip in clips]}],
    }
    snapshot.update(over)
    return snapshot


def _checks(report, check):
    return [finding for finding in report.findings
            if finding.check == check]


def test_a_clip_whose_media_is_not_on_disk_is_reported(tmp_path):
    """A file that is gone is REPORTED, and it does not fail the gate.

    Measured on 2026-10-21: the engine deletes a built reel's rendered
    caption cards after the build (`library/tools/asset_gc.py`), so a
    reel legitimately references files that are no longer on disk.  A
    file that is gone is named - and it is not a refusal.
    """
    gone = str(tmp_path / "gone.mov")
    snapshot = _snapshot(_clip("clip-1", 0, 100, source_file=gone))

    report = timeline_structure.compare(None, snapshot, media_on_disk=True)

    findings = _checks(report, "missing_media")
    assert findings, report.as_dict()
    assert findings[0].severity == timeline_structure.WARNING
    assert report.passed, report.as_dict()


def test_a_clip_that_carried_media_and_now_carries_none_is_caught():
    """Defect: the write took the media off a clip that had some.

    The clip is on the timeline, it occupies its frames, and it plays
    nothing at all.
    """
    before = _snapshot(_clip("clip-1", 0, 100))
    after = _snapshot(_clip("clip-1", 0, 100, source_file="",
                            media_pool_item_id=""))

    report = timeline_structure.compare(
        before, after, media_on_disk=False)

    assert not report.passed
    assert _checks(report, "missing_media"), report.as_dict()


def test_two_items_on_one_row_that_overlap_are_caught():
    """Defect: one row carries two items on the same frames.

    Resolve will not place them, so this is what a writer that placed
    the wrong thing looks like on the way back.
    """
    snapshot = _snapshot(
        _clip("clip-1", 0, 100),
        _clip("clip-2", 50, 150))

    report = timeline_structure.compare(None, snapshot, media_on_disk=False)

    assert not report.passed
    assert _checks(report, "overlaps"), report.as_dict()


def test_the_same_placement_twice_is_caught():
    """Defect: the same item placed twice on one row.

    A placement loop that runs once per clip PER CLIP produces N*N
    items at duplicate positions, and every one of them is on the
    timeline.
    """
    snapshot = _snapshot(
        _clip("clip-1", 0, 100),
        _clip("clip-2", 0, 100, source_file="/media/other.mov"))

    report = timeline_structure.compare(None, snapshot, media_on_disk=False)

    assert not report.passed
    assert _checks(report, "duplicates"), report.as_dict()


def test_a_hole_where_the_plan_said_there_would_be_picture_is_caught():
    """Defect: a picture row the plan filled, with a hole in it.

    The plan authorized coverage from 0 to 200.  The timeline covers
    0 to 100 and then nothing until 200, and the frames in between are
    black.
    """
    snapshot = _snapshot(
        _clip("clip-1", 0, 100),
        _clip("clip-2", 200, 300))
    expectation = timeline_structure.expectation_from_placements([
        {"track_type": "video", "track_index": 1,
         "record_in": 0, "record_out": 300}])

    report = timeline_structure.compare(
        None, snapshot, expectation=expectation, media_on_disk=False)

    assert not report.passed
    gaps = _checks(report, "gaps")
    assert gaps, report.as_dict()
    assert "missing" in gaps[0].detail


def test_a_hole_that_opened_where_the_row_was_covered_is_caught():
    """Defect: a row that was covered, and is not any more.

    No plan is needed here: the row carried picture before the write,
    and the write left a hole in it that no operation asked for.
    """
    before = _snapshot(
        _clip("clip-1", 0, 100),
        _clip("clip-2", 100, 200))
    after = _snapshot(_clip("clip-1", 0, 100))

    report = timeline_structure.compare(
        before, after, media_on_disk=False,
        authorized_holes=())

    assert not report.passed
    assert _checks(report, "gaps"), report.as_dict()


def test_a_removal_may_leave_the_hole_it_was_authorized_to_leave():
    """The gate is not a blanket refusal: an authorized removal passes.

    The item that was there is gone, and the hole it left is the hole
    the request asked for - named by the item's own span.
    """
    before = _snapshot(
        _clip("clip-1", 0, 100),
        _clip("clip-2", 100, 200))
    after = _snapshot(_clip("clip-1", 0, 100))

    report = timeline_structure.compare(
        before, after, media_on_disk=False,
        authorized_holes=((100, 200),))

    assert report.passed, report.as_dict()


def test_a_clip_that_plays_past_the_end_of_its_media_is_caught():
    """Defect: the clip reads frames the file does not have.

    The media pool reports a thousand frames in the file, and the clip
    plays to frame two thousand.  The frames it plays are not there.
    """
    snapshot = _snapshot(_clip("clip-1", 0, 100, source_in_frame=0,
                               source_out_frame=2000, source_frames=1000))

    report = timeline_structure.compare(None, snapshot, media_on_disk=False)

    assert not report.passed
    assert _checks(report, "source_alignment"), report.as_dict()


def test_a_clip_that_starts_before_its_file_begins_is_caught():
    """Defect: a source interval that starts before the file does."""
    snapshot = _snapshot(_clip("clip-1", 0, 100, source_in_frame=-40,
                               source_out_frame=60))

    report = timeline_structure.compare(None, snapshot, media_on_disk=False)

    assert not report.passed
    assert _checks(report, "source_alignment"), report.as_dict()


def test_a_link_to_an_item_that_is_not_there_is_caught():
    """Defect: a link that points at nothing.

    The picture says it is linked to its sound, and the sound is not on
    the timeline.
    """
    snapshot = _snapshot(_clip("clip-1", 0, 100,
                               linked_unique_ids=["clip-gone"]))

    report = timeline_structure.compare(None, snapshot, media_on_disk=False)

    assert not report.passed
    assert _checks(report, "linked_audio"), report.as_dict()


def test_a_picture_and_its_own_sound_that_are_not_linked_are_caught():
    """Defect: the same media on two rows, and the two are not linked.

    The picture and the sound of one clip are the same file on two
    rows, and nothing joined them.
    """
    picture = _clip("clip-1", 0, 100, source_file="/media/one.mov")
    sound = _clip("clip-2", 0, 100, source_file="/media/one.mov",
                  media_type="audio", track_type="audio")
    snapshot = {
        "timeline": "Reel 01",
        "project": "Project",
        "tracks": [
            {"type": "video", "index": 1, "name": "V1", "clips": [picture]},
            {"type": "audio", "index": 1, "name": "A1", "clips": [sound]},
        ],
    }

    report = timeline_structure.compare(None, snapshot, media_on_disk=False)

    findings = _checks(report, "linked_audio")
    assert findings, report.as_dict()
    # Measured on 2026-10-21: a built reel places the picture and the
    # program audio of one clip on two rows, and the shadow records no
    # link between them.  The pairing is REPORTED and it is not a
    # refusal - a gate that refused every built reel would be a gate
    # that fails correct output.
    assert findings[0].severity == timeline_structure.WARNING


def test_the_wrong_timeline_answering_is_caught():
    """Defect: the read-back came from a timeline nobody asked about."""
    snapshot = _snapshot(_clip("clip-1", 0, 100), timeline="Reel 09")

    report = timeline_structure.compare(
        None, snapshot, media_on_disk=False,
        expected_timeline="Reel 01")

    assert not report.passed
    assert _checks(report, "timeline_identity"), report.as_dict()


def test_the_same_item_twice_on_the_timeline_is_caught():
    """Defect: one identity carrying two items."""
    snapshot = _snapshot(
        _clip("clip-1", 0, 100),
        _clip("clip-1", 100, 200))

    report = timeline_structure.compare(None, snapshot, media_on_disk=False)

    assert not report.passed
    assert _checks(report, "timeline_identity"), report.as_dict()


def test_a_clean_timeline_passes(tmp_path):
    """The control: a clean edit is not refused.

    One clip on a picture row, its media on disk, its source interval
    inside its file, and nothing else on the timeline.
    """
    media = tmp_path / "one.mov"
    media.write_bytes(b"not really a movie")
    snapshot = _snapshot(_clip("clip-1", 0, 100, source_file=str(media)))

    report = timeline_structure.compare(None, snapshot, media_on_disk=True)

    assert report.passed, report.as_dict()
    assert not report.errors


def test_a_row_that_reaches_further_than_it_did_is_not_a_gap():
    """A row that grew is not a row that lost picture.

    An insert at 500 on a row that covered 0 to 100 leaves the row
    reaching further than it did.  The frames between are not a hole
    that opened - nothing was there to be lost.
    """
    before = _snapshot(_clip("clip-1", 0, 100))
    after = _snapshot(
        _clip("clip-1", 0, 100),
        _clip("clip-2", 500, 600))

    report = timeline_structure.compare(
        before, after, media_on_disk=False,
        authorized_spans=((500, 600),))

    assert report.passed, report.as_dict()


def test_the_build_expectation_follows_the_rows_the_build_removed():
    """A row index shift is applied to the plan, not to the timeline.

    The build removes the rows nothing landed on, and every later row
    moves down by one.  The expectation is stated in the row indices
    the timeline carries, so a build that did what it planned is not
    accused of leaving a hole.
    """
    expectation = timeline_structure.expectation_from_placements(
        [{"track_type": "video", "track_index": 2,
          "record_in": 0, "record_out": 100}],
        deleted_tracks=[{"media_type": "video", "index": 1}])

    assert expectation["rows"]["video"] == {1: [(0, 100)]}


def test_the_report_names_every_check_it_ran():
    """The report says what it looked at, so a pass is not a silence."""
    snapshot = _snapshot(_clip("clip-1", 0, 100))

    report = timeline_structure.compare(None, snapshot, media_on_disk=False)

    ran = [check["check"] for check in report.checks]
    assert ran == list(timeline_structure.CHECKS)
    assert all(check["passed"] for check in report.checks)


def test_the_media_check_only_looks_at_absolute_paths():
    """A relative path is not a missing file; it is a path we cannot judge.

    The engine reports what it could not determine as unmeasured, and
    never as a pass dressed as a measurement.
    """
    snapshot = _snapshot(_clip("clip-1", 0, 100, source_file="one.mov"))

    report = timeline_structure.compare(None, snapshot, media_on_disk=True)

    assert not _checks(report, "missing_media"), report.as_dict()
    assert report.passed, report.as_dict()
    assert os.path.isabs("one.mov") is False


# ── The build path: the inventory the build already holds ─────────────


def _inventory_row(record_in, record_out, **over):
    """One row of a build inventory, as `read_inventory` reports it."""
    row = {
        "track_type": "video",
        "track_index": 1,
        "source_file": "/media/one.mov",
        "source_in": 0,
        "source_out": record_out - record_in,
        "record_in": record_in,
        "record_out": record_out,
        "unique_id": f"item-{record_in}",
        "linked_ids": [],
        "media_errors": [],
    }
    row.update(over)
    return row


def test_a_build_that_left_a_hole_in_the_picture_row_is_caught():
    """Defect: the build placed what it planned, and left a hole.

    The plan covered the picture row from 0 to 200.  The timeline
    carries 0 to 100, and the frames after it are black.
    """
    inventory = [_inventory_row(0, 100)]
    expectation = timeline_structure.expectation_from_placements([
        {"track_type": "video", "track_index": 1,
         "record_in": 0, "record_out": 200}])

    structure = timeline_structure.structure_from_inventory(
        inventory, timeline_name="Reel 01")
    report = timeline_structure.analyze(
        structure, expectation=expectation, expected_timeline="Reel 01",
        media_on_disk=False)

    assert not report.passed
    assert _checks(report, "gaps"), report.as_dict()


def test_a_build_that_placed_what_it_planned_passes():
    """The control: the build's own outcome is not refused."""
    inventory = [_inventory_row(0, 100), _inventory_row(100, 200)]
    expectation = timeline_structure.expectation_from_placements([
        {"track_type": "video", "track_index": 1,
         "record_in": 0, "record_out": 100},
        {"track_type": "video", "track_index": 1,
         "record_in": 100, "record_out": 200}])

    structure = timeline_structure.structure_from_inventory(
        inventory, timeline_name="Reel 01")
    report = timeline_structure.analyze(
        structure, expectation=expectation, expected_timeline="Reel 01",
        media_on_disk=False)

    assert report.passed, report.as_dict()


def test_a_build_that_placed_media_which_is_not_there_is_caught():
    """Defect: the media the build placed cannot be opened.

    The build's own inventory measured it - the file is not at the path
    the pool names - and the structural read-back carries that
    measurement into the verdict.
    """
    inventory = [_inventory_row(
        0, 100, media_errors=["media is missing at /media/one.mov"])]

    structure = timeline_structure.structure_from_inventory(
        inventory, timeline_name="Reel 01")
    report = timeline_structure.analyze(
        structure, expected_timeline="Reel 01", media_on_disk=False)

    assert not report.passed
    assert _checks(report, "missing_media"), report.as_dict()
