"""First-contact edit preservation uses the best known Ren baseline.

The incident history and preservation contract live in
`docs/evidence/edit_preservation.md`.
"""
import copy

import pytest

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


@pytest.mark.parametrize(
    ("case", "editor_change", "later_build", "rescaled_units",
     "expected_baseline", "expected_change"),
    [
        pytest.param("untouched", None, False, False,
                     "first_contact_journaled_touch", None,
                     id="journaled-touch-is-baseline"),
        pytest.param("editor-change", "enabled", False, False,
                     "first_contact_journaled_touch", "enabled",
                     id="detect-only-the-edit-after-touch"),
        pytest.param("later-build", None, True, False,
                     "first_contact_staging", None,
                     id="newer-build-supersedes-touch"),
        pytest.param("unit-rescale", None, False, True,
                     "first_contact_journaled_touch", None,
                     id="resolution-unit-change-is-not-an-edit"),
    ],
)
def test_first_contact_preserves_edits_against_the_right_baseline(
        tmp_path, case, editor_change, later_build, rescaled_units,
        expected_baseline, expected_change):
    """See the scenario table and pointer in `docs/evidence/edit_preservation.md`."""
    tracks = touched_tracks()
    journal_a_touch(tmp_path, tracks)
    if later_build:
        reel_versions.record(tmp_path, FINAL, kind=reel_versions.KIND_BUILD,
                             rows={})

    live_tracks = copy.deepcopy(tracks)
    if editor_change == "enabled":
        live_tracks[0]["clips"][0]["enabled"] = False
    if rescaled_units:
        for track in live_tracks:
            for clip in track["clips"]:
                clip["transform"]["Pan"] *= 4
                clip["transform"]["Tilt"] = (
                    clip["transform"]["Tilt"] * 4 - 696.0)

    detection = guard.detect_editor_changes(
        str(tmp_path), FINAL, snapshot(live_tracks), rebuilt_staging())

    assert detection["first_contact"] is True
    assert detection["baseline"] == expected_baseline
    if (expected_change is None
            and expected_baseline != "first_contact_staging"):
        assert detection["detected"] == []
    elif expected_change is not None:
        [record] = detection["detected"]
        assert record["baseline"] == expected_baseline
        assert record["ren_action_journal"] == JOURNAL
        [change] = record["changes"]
        assert change["kind"] == "item_changed"
        assert change["after"]["name"] == "LC4932.MXF"
        assert set(change["changed"]) == {expected_change}
    if case == "untouched":
        assert detection["pending"] == []
