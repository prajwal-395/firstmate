"""First contact reads the editor's changes off Ren's last journaled touch.

Edit preservation compares the live reel with Ren's last known snapshot
(`reel_replace_guard.detect_editor_changes`). A reel last written by a
touch before snapshots were recorded has none, and first contact fell
back to the incoming STAGING - so every difference the rebuild itself
brings (re-split captions, un-halved framing, a new ending) was filed as
an "editor change" and then refused or, worse, carried back onto the
rebuild. Reel 09 was the case: live matched its 2026-10-01 touch's
journal `after` exactly, while its staging differed on most rows.

The touch's journal `after` is Ren's own read of what it left, so it is
the baseline: what the editor changed since is detected, and what the
rebuild changes is not.
"""
import copy

from library.tools import reel_replace_guard as guard
from library.tools import undo_journal
from library.tools.versions import reel_versions

FINAL = "Reel 09 - your-website-is-only-20-percent"
JOURNAL = "20261001T160717Z-reel-09-your-website-is-only-20-percent-f20923"


def detail(row, index, name, record_in, record_out, *, source_in=0,
           pan=0.0, enabled=True):
    return {
        "track_type": "video", "track_index": index, "track_name": row,
        "name": name, "media_pool_item_id": f"media-{name}",
        "source_file": f"/media/{name}", "unique_id": f"item-{name}",
        "source_in_frame": source_in,
        "source_out_frame": source_in + record_out - record_in,
        "record_in": record_in, "record_out": record_out,
        "duration": record_out - record_in, "enabled": enabled,
        "transform": {"Pan": pan, "Tilt": 0.0, "ZoomX": 2.13859,
                      "Opacity": 100.0, "CompositeMode": 0},
        "fusion": {}, "color": {}, "clip_color": "", "flags": [],
        "markers": [],
    }


def touched_tracks():
    return [
        {"clips": [detail("Akshita", 1, "LC4932.MXF", 0, 120,
                          source_in=34305, pan=-8.11)]},
        {"clips": [detail("Subtitles", 4, "sub_a.mov", 0, 40),
                   detail("Subtitles", 4, "sub_b.mov", 40, 120)]},
        {"clips": [detail("Semantic", 5, "mg.mov", 30, 60,
                          enabled=False)]},
    ]


def snapshot(tracks, name=FINAL, unique_id="live"):
    return {"timeline": {"name": name, "unique_id": unique_id,
                         "settings": {"timelineFrameRate": "23.976"},
                         "start_frame": 0, "end_frame": 120},
            "items": guard.snapshot_items(tracks), "markers": []}


def journal_a_touch(project_dir, tracks):
    undo_journal.write_entry(project_dir, {
        "format": undo_journal.JOURNAL_FORMAT, "id": JOURNAL,
        "final": FINAL, "reel": 9, "status": "applied",
        "before": {"tracks": tracks}, "after": {"tracks": tracks}})
    reel_versions.record(project_dir, FINAL, kind=reel_versions.KIND_TOUCH,
                         rows={}, journal=JOURNAL)


def rebuilt_staging():
    """What a rebuild stages: framing restored, captions re-split, the
    graphic placed enabled again - all Ren's own plan, none the editor's."""
    tracks = touched_tracks()
    tracks[0]["clips"][0]["transform"]["Pan"] = -32.445
    tracks[1]["clips"] = [detail("Subtitles", 4, "sub_c.mov", 0, 60),
                          detail("Subtitles", 4, "sub_d.mov", 60, 120)]
    tracks[2]["clips"][0]["enabled"] = True
    return snapshot(tracks, name=FINAL + " (rebuild staging)",
                    unique_id="staging")


def test_a_reel_untouched_since_its_journaled_touch_has_no_editor_change(
        tmp_path):
    journal_a_touch(tmp_path, touched_tracks())
    live = snapshot(touched_tracks())

    detection = guard.detect_editor_changes(
        str(tmp_path), FINAL, live, rebuilt_staging())

    assert detection["first_contact"] is True
    assert detection["baseline"] == "first_contact_journaled_touch"
    assert detection["detected"] == []
    assert detection["pending"] == []


def test_only_what_the_editor_changed_after_the_touch_is_detected(tmp_path):
    journal_a_touch(tmp_path, touched_tracks())
    edited = copy.deepcopy(touched_tracks())
    edited[0]["clips"][0]["enabled"] = False
    live = snapshot(edited)

    detection = guard.detect_editor_changes(
        str(tmp_path), FINAL, live, rebuilt_staging())

    [record] = detection["detected"]
    assert record["baseline"] == "first_contact_journaled_touch"
    assert record["ren_action_journal"] == JOURNAL
    [change] = record["changes"]
    assert change["kind"] == "item_changed"
    assert change["after"]["name"] == "LC4932.MXF"
    assert set(change["changed"]) == {"enabled"}


def test_a_rebuild_after_the_touch_is_not_judged_against_the_touch(
        tmp_path):
    journal_a_touch(tmp_path, touched_tracks())
    reel_versions.record(tmp_path, FINAL, kind=reel_versions.KIND_BUILD,
                         rows={})
    live = snapshot(touched_tracks())

    detection = guard.detect_editor_changes(
        str(tmp_path), FINAL, live, rebuilt_staging())

    assert detection["baseline"] == "first_contact_staging"


def test_a_unit_epoch_rescale_since_the_touch_is_not_an_editor_change(
        tmp_path):
    """Reel 09, 2026-10-02: a project-resolution change rescaled every
    stored Pan/Tilt x4 after its touch, picture unmoved. A journal records
    no unit epoch, so its Pan/Tilt are not compared - read as edits, the
    carry would have written the old unit's values over the rebuild."""
    journal_a_touch(tmp_path, touched_tracks())
    rescaled = copy.deepcopy(touched_tracks())
    for track in rescaled:
        for clip in track["clips"]:
            clip["transform"]["Pan"] *= 4
            clip["transform"]["Tilt"] = clip["transform"]["Tilt"] * 4 - 696.0
    live = snapshot(rescaled)

    detection = guard.detect_editor_changes(
        str(tmp_path), FINAL, live, rebuilt_staging())

    assert detection["baseline"] == "first_contact_journaled_touch"
    assert detection["detected"] == []
