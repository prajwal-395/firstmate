"""The editor's timeline edits are carried through a rebuild, or refused.

History: docs/evidence/resolve_test_history.md#test_editor_edit_carry.
"""
import copy
import json
import threading
import time
from contextlib import ExitStack, nullcontext
from unittest.mock import patch

import pytest


@pytest.fixture(autouse=True)
def _leases_off_machine(tmp_path, monkeypatch):
    """Carried edits declare their staging now: leases stay in tmp."""
    directory = tmp_path / "locks"
    directory.mkdir()
    monkeypatch.setenv("PIPELINE_RESOLVE_LOCK_DIR", str(directory))

from library.tools import editor_edit_carry as carry
from library.tools import reel_replace_guard as guard
from library.tools import undo_journal
from library.tools.reel_build import ReelBuildError, promote_staged_reels
from library.tools.versions import reel_versions
from tests.composed_edit_harness import (
    covering_window,
    frames_of,
    item,
    pool_clip,
    rows_timeline,
)
from tests.promotion_test_helpers import no_a_roll_track_plans
from tests.resolve_double import (
    FakeProject,
    FakeTimeline,
    TimelineItemSpec,
)

FINAL = "Reel 07 - number-one-on-google-invisible-to-ai"
STAGING = FINAL + " (rebuild staging)"
MASTER = "Podcast - Synced"


@pytest.fixture
def mock_dvr(stub_resolve_script):
    yield


@pytest.fixture
def project_dir(tmp_path):
    root = tmp_path / "project"
    (root / "pipeline_output" / "review").mkdir(parents=True)
    return root


def _clip(name, source_in, source_out, record_in, enabled=True, transform=None):
    duration = source_out - source_in
    return TimelineItemSpec(
        name,
        record_in,
        record_in + duration,
        enabled=enabled,
        path=f"file:/media/{name}.mov",
        left_offset=source_in,
        transform=transform or {"Pan": 0.0, "ZoomX": 1.0},
    )


def snapshot(timeline, project_folder=None):
    """The full read `reel_replace_guard.full_timeline_snapshot` returns."""
    items = []
    for track_type in ("video", "audio"):
        for index in range(1, timeline.GetTrackCount(track_type) + 1):
            row = timeline.GetTrackName(track_type, index)
            for clip in timeline.GetItemListInTrack(track_type, index):
                captured = {
                    "track_type": track_type,
                    "track_index": index,
                    "track_name": row,
                    "name": clip.GetName(),
                    "unique_id": clip.GetUniqueId(),
                    "source_identity": clip.GetMediaPoolItem().GetClipProperty(
                        "File Path"
                    ),
                    "source_in_frame": clip.GetSourceStartFrame(),
                    "source_out_frame": clip.GetSourceEndFrame(),
                    "record_in": clip.GetStart(),
                    "record_out": clip.GetEnd(),
                    "duration": clip.GetDuration(),
                    "enabled": clip.GetClipEnabled(),
                    "transform": clip.GetProperty(),
                    "composite": {},
                    "fusion": {},
                    "color": {},
                    "markers": [],
                }
                items.append(captured)
    if project_folder:
        from library.tools.reel_disabled_clip_carry import (
            add_semantic_graphic_identities,
        )
        add_semantic_graphic_identities(
            str(project_folder), timeline.GetName(), items)
    return {
        "timeline": {
            "name": timeline.GetName(),
            "unique_id": timeline.GetName(),
            "settings": {"timelineFrameRate": 23.976},
            "start_frame": 0,
            "end_frame": max([item["record_out"] for item in items] or [0]),
        },
        "items": items,
        "markers": [],
    }


def edited_reel_7():
    """The live timeline as the editor left it: 747 frames."""
    return FakeTimeline(
        FINAL,
        video=[
            (
                "Speakers",
                [
                    _clip("SpeakerOne", 22_232, 22_348, 0),
                    _clip("SpeakerTwo", 22_257, 22_694, 116),
                    _clip("SpeakerOne", 25_557, 25_751, 553),
                ],
            ),
            ("Semantic", [_clip("semantic-card", 0, 96, 47, enabled=False)]),
        ],
        audio=[
            (
                "Dialogue",
                [
                    _clip("SpeakerOne", 22_232, 22_348, 0),
                    _clip("SpeakerTwo", 22_257, 22_694, 116),
                    _clip("SpeakerOne", 25_557, 25_751, 553),
                ],
            ),
        ],
    )


def rebuilt_reel_7(name=STAGING):
    """The rebuild: both passages back, the graphic on."""
    return FakeTimeline(
        name,
        video=[
            (
                "Speakers",
                [
                    _clip("SpeakerOne", 22_232, 22_348, 0),
                    _clip("SpeakerTwo", 22_257, 22_694, 116),
                    _clip("SpeakerTwo", 25_263, 25_374, 553),
                    _clip("SpeakerOne", 25_557, 25_751, 664),
                    _clip("SpeakerOne", 60_745, 60_979, 858),
                ],
            ),
            ("Semantic", [_clip("semantic-card", 0, 96, 47, enabled=True)]),
        ],
        audio=[
            (
                "Dialogue",
                [
                    _clip("SpeakerOne", 22_232, 22_348, 0),
                    _clip("SpeakerTwo", 22_257, 22_694, 116),
                    _clip("SpeakerTwo", 25_263, 25_374, 553),
                    _clip("SpeakerOne", 25_557, 25_751, 664),
                    _clip("SpeakerOne", 60_745, 60_979, 858),
                ],
            ),
        ],
    )


def played(timeline):
    """What each row plays, in source and record frames."""
    rows = {}
    for timeline_item in snapshot(timeline)["items"]:
        rows.setdefault((timeline_item["track_type"],
                         timeline_item["track_name"]), []).append(
            (
                timeline_item["source_in_frame"],
                timeline_item["source_out_frame"],
                timeline_item["record_in"],
                timeline_item["enabled"],
            )
        )
    return {row: sorted(items) for row, items in rows.items()}


def promote(project, project_dir, staging):
    with ExitStack() as stack:
        stack.enter_context(
            patch.object(
                guard,
                "full_timeline_snapshot",
                side_effect=lambda timeline, _project, folder=None: snapshot(
                    timeline, folder),
            )
        )
        stack.enter_context(
            patch("library.tools.resolve_locale.scriptapp_preserving_locale")
        )
        stack.enter_context(
            patch(
                "library.tools.reel_build.resolve_project_exactly", return_value=project
            )
        )
        stack.enter_context(
            patch(
                "library.tools.reel_disabled_clip_carry.carry_disabled_state",
                return_value={
                    "unchanged_unmatched": [],
                    "safe_replacements": [],
                    "carried": [],
                    "refused": [],
                    "matched": [],
                },
            )
        )
        stack.enter_context(
            patch(
                "library.tools.resolve_lock.cursor_excursion",
                side_effect=lambda *_a, **_k: nullcontext(),
            )
        )
        stack.enter_context(patch("library.tools.reel_read.assert_timeline_current"))
        stack.enter_context(patch("library.tools.marker_gate.verify_promotion"))
        # This helper neuters `cursor_excursion` above, so take the
        # cursor onto staging explicitly - the excursion production
        # would have run. The double refuses off-current writes, as
        # Resolve does, and the carry writes here.
        staged = next(t for t in project.timelines if t.GetName() == staging)
        project.SetCurrentTimeline(staged)
        return promote_staged_reels(
            str(project_dir),
            "Mock Project",
            MASTER,
            {FINAL: staging},
            organise=False,
            track_plans=no_a_roll_track_plans({FINAL: staging}),
        )


def provenance(project_dir):
    return json.loads(
        (project_dir / "pipeline_output" / "review" / "plan_provenance.json").read_text(
            encoding="utf-8"
        )
    )


def seed_provenance(project_dir):
    (project_dir / "pipeline_output" / "review" / "plan_provenance.json").write_text(
        json.dumps({"built_reels": [STAGING], "plan_content_hash": "plan-v1"}),
        encoding="utf-8",
    )


def _semantic_graphic_reel(name, graphic_name, *, enabled=True):
    graphic = ([] if graphic_name is None else [
        _clip(graphic_name, 0, 10, 577, enabled=enabled)])
    return FakeTimeline(
        name,
        video=[
            ("Speakers", [_clip("Akshita", 0, 100, 0)]),
            ("Captions", []),
            ("B-roll", []),
            ("Semantic 2", []),
            ("Explainer", []),
            ("Semantic", graphic),
        ],
    )


def _write_semantic_graphic_records(project_dir, *, stage_has_graphic=True):
    element = {
        "element": "title_lockup",
        "runs": [{"text": "A stable source beat"}],
        "anchor": "top_centre",
    }
    plans = [{
        "reel": FINAL,
        "segments": [{
            "segment_id": "mg_geo-podcast_e1b5df7a",
            "overlay_path": "/media/mg_geo-podcast_e1b5df7a.mov",
            "carry_identity": [element],
        }],
    }]
    if stage_has_graphic:
        plans.append({
            "reel": STAGING,
            "segments": [{
                "segment_id": "mg_c1_8f357b21",
                "overlay_path": "/media/mg_c1_8f357b21.mov",
                "carry_identity": [element],
            }],
        })
    path = project_dir / "pipeline_output" / "review" / "semantic_visual_plans.json"
    path.write_text(json.dumps({"format": "semantic_visual_plans/1",
                                "plans": plans}), encoding="utf-8")


@pytest.mark.usefixtures("mock_dvr")
def test_hand_disabled_motion_graphic_carries_across_rendered_file_names(
        project_dir):
    live = _semantic_graphic_reel(
        FINAL, "mg_geo-podcast_e1b5df7a", enabled=False)
    baseline = _semantic_graphic_reel(
        FINAL, "mg_geo-podcast_e1b5df7a", enabled=True)
    staging = _semantic_graphic_reel(
        STAGING, "mg_c1_8f357b21", enabled=True)
    seed_provenance(project_dir)
    _write_semantic_graphic_records(project_dir)
    from library.tools import plan_provenance
    plan_provenance.record_timeline_snapshot(
        str(project_dir / "pipeline_output" / "review"), FINAL,
        snapshot(baseline, project_dir), action="build_promotion")
    resolve = FakeProject([FakeTimeline(MASTER), live, staging])

    promoted = promote(resolve, project_dir, STAGING)

    assert promoted["promoted"] == [FINAL]
    (graphic,) = staging.GetItemListInTrack("video", 6)
    assert graphic.GetClipEnabled() is False
    [enabled_edit] = [edit for edit in provenance(project_dir)[
        "carried_editor_edits"][FINAL] if edit["kind"] == "enabled"]
    assert enabled_edit["graphic_identity"]


@pytest.mark.usefixtures("mock_dvr")
def test_hand_disabled_motion_graphic_still_refuses_when_its_beat_is_gone(
        project_dir):
    live = _semantic_graphic_reel(
        FINAL, "mg_geo-podcast_e1b5df7a", enabled=False)
    baseline = _semantic_graphic_reel(
        FINAL, "mg_geo-podcast_e1b5df7a", enabled=True)
    staging = _semantic_graphic_reel(STAGING, None)
    seed_provenance(project_dir)
    _write_semantic_graphic_records(project_dir, stage_has_graphic=False)
    from library.tools import plan_provenance
    plan_provenance.record_timeline_snapshot(
        str(project_dir / "pipeline_output" / "review"), FINAL,
        snapshot(baseline, project_dir), action="build_promotion")
    resolve = FakeProject([FakeTimeline(MASTER), live, staging])

    with pytest.raises(ReelBuildError, match="staging plays that passage 0"):
        promote(resolve, project_dir, STAGING)

    assert live in resolve.timelines
    assert staging in resolve.timelines
    assert staging.GetName() == STAGING


@pytest.mark.usefixtures("mock_dvr")
def test_reel_7_cuts_and_disabled_graphic_are_carried_into_the_rebuild(project_dir):
    live, staging = edited_reel_7(), rebuilt_reel_7()
    wanted = played(live)
    seed_provenance(project_dir)
    resolve = FakeProject([FakeTimeline(MASTER), live, staging])

    promoted = promote(resolve, project_dir, STAGING)

    assert promoted["promoted"] == [FINAL]
    # The promoted timeline IS the staging, renamed: it plays exactly
    # what the editor's cut played, passage for passage.
    assert staging.GetName() == FINAL
    assert played(staging) == wanted
    record = provenance(project_dir)["unattributed_editor_changes"][FINAL][0]
    assert record["status"] == "carried"
    edits = provenance(project_dir)["carried_editor_edits"][FINAL]
    by_kind = {}
    for edit in edits:
        by_kind.setdefault(edit["kind"], []).append(edit)
    cuts = sorted(
        (edit["row"], edit["source_in_frame"], edit["source_out_frame"], edit["ripple"])
        for edit in by_kind["cut"]
    )
    assert cuts == [
        ("audio:Dialogue", 25_263, 25_374, True),
        ("audio:Dialogue", 60_745, 60_979, False),
        ("video:Speakers", 25_263, 25_374, True),
        ("video:Speakers", 60_745, 60_979, False),
    ]
    (enabled,) = by_kind["enabled"]
    assert (enabled["row"], enabled["before"], enabled["after"]) == (
        "video:Semantic",
        True,
        False,
    )
    for edit in edits:
        assert edit["status"] == "active"
        assert edit["plan_version"] == "plan-v1"
        assert edit["source"] == f"unattributed_editor_change:{record['id']}"
        assert edit["wording"]
        assert edit["last_carried_by"] == "build promotion"


@pytest.mark.usefixtures("mock_dvr")
def test_the_next_rebuild_carries_the_ledger_with_no_new_change(project_dir):
    live, staging = edited_reel_7(), rebuilt_reel_7()
    wanted = played(live)
    seed_provenance(project_dir)
    resolve = FakeProject([FakeTimeline(MASTER), live, staging])
    promote(resolve, project_dir, STAGING)

    # Rebuild again: the promoted reel is Ren's last read, so nothing
    # new is detected - the carry comes from the ledger alone.
    again = rebuilt_reel_7()
    resolve.adopt(again)
    path = project_dir / "pipeline_output" / "review" / "plan_provenance.json"
    doc = provenance(project_dir)
    doc["built_reels"] = [STAGING]
    path.write_text(json.dumps(doc), encoding="utf-8")
    promoted = promote(resolve, project_dir, STAGING)

    assert promoted["promoted"] == [FINAL]
    assert again.GetName() == FINAL
    assert played(again) == wanted
    changes = provenance(project_dir)["unattributed_editor_changes"][FINAL]
    assert [record["status"] for record in changes] == ["carried"]


@pytest.mark.usefixtures("mock_dvr")
def test_a_cut_staging_plays_only_part_of_refuses_by_name(project_dir):
    edit = {
        "id": "e1",
        "kind": "cut",
        "row": "video:Speakers",
        "name": "SpeakerTwo",
        "source_identity": "file:/media/SpeakerTwo.mov",
        "source_in_frame": 25_263,
        "source_out_frame": 25_374,
        "ripple": True,
    }
    staged = {
        "items": [
            {
                "track_type": "video",
                "track_name": "Speakers",
                "name": "SpeakerTwo",
                "source_identity": "file:/media/SpeakerTwo.mov",
                "source_in_frame": 25_300,
                "source_out_frame": 25_500,
                "record_in": 553,
                "record_out": 753,
            }
        ]
    }

    with pytest.raises(carry.EditorEditCarryRefused) as refused:
        carry.plan_application([edit], staged, FINAL)

    assert "25,263..25,374" in str(refused.value)
    assert "25,300..25,500" in str(refused.value)


@pytest.mark.usefixtures("mock_dvr")
def test_a_cut_that_still_plays_after_the_write_refuses():
    edit = {
        "id": "e1",
        "kind": "cut",
        "row": "video:Speakers",
        "name": "SpeakerTwo",
        "source_identity": "file:/media/SpeakerTwo.mov",
        "source_in_frame": 25_263,
        "source_out_frame": 25_374,
    }
    staged_after = {
        "items": [
            {
                "track_type": "video",
                "track_name": "Speakers",
                "name": "SpeakerTwo",
                "source_identity": "file:/media/SpeakerTwo.mov",
                "source_in_frame": 25_263,
                "source_out_frame": 25_374,
                "record_in": 553,
                "record_out": 664,
            }
        ]
    }

    with pytest.raises(carry.EditorEditCarryRefused, match="did not land"):
        carry.verify_carried_edits({"edits": [edit]}, staged_after, FINAL)


@pytest.mark.usefixtures("mock_dvr")
def test_a_ren_act_that_changes_a_carried_edit_supersedes_it(project_dir):
    from library.tools import plan_provenance

    review = str(project_dir / "pipeline_output" / "review")
    edit = {
        "id": "e1",
        "kind": "enabled",
        "field": "enabled",
        "row": "video:Semantic",
        "name": "semantic-card",
        "source_identity": "file:/media/semantic-card.mov",
        "source_in_frame": 0,
        "source_out_frame": 96,
        "before": True,
        "after": False,
        "status": "active",
    }
    plan_provenance.record_carried_edits(review, FINAL, [edit])
    promoted = {
        "items": [
            {
                "track_type": "video",
                "track_name": "Semantic",
                "name": "semantic-card",
                "source_identity": "file:/media/semantic-card.mov",
                "source_in_frame": 0,
                "source_out_frame": 96,
                "enabled": True,
            }
        ]
    }

    carry.record_after_promotion(
        str(project_dir), FINAL, None, promoted, act="touch t-1"
    )

    assert plan_provenance.carried_editor_edits(review, FINAL) == []
    (stored,) = provenance(project_dir)["carried_editor_edits"][FINAL]
    assert stored["status"] == "superseded"
    assert stored["superseded_by"] == "changed by touch t-1"


@pytest.mark.usefixtures("mock_dvr")
def test_putting_a_cut_passage_back_supersedes_the_cut(project_dir):
    from library.tools import plan_provenance

    review = str(project_dir / "pipeline_output" / "review")
    cut = {
        "id": "c1",
        "kind": "cut",
        "row": "video:Speakers",
        "name": "SpeakerTwo",
        "source_identity": "file:/media/SpeakerTwo.mov",
        "source_in_frame": 25_263,
        "source_out_frame": 25_374,
        "status": "active",
    }
    plan_provenance.record_carried_edits(review, FINAL, [cut])
    back = {
        "id": "r2",
        "timeline": FINAL,
        "changes": [
            {
                "kind": "item_added",
                "before": None,
                "changed": {},
                "after": {
                    "track_type": "video",
                    "track_name": "Speakers",
                    "name": "SpeakerTwo",
                    "source_identity": "file:/media/SpeakerTwo.mov",
                    "source_in_frame": 25_263,
                    "source_out_frame": 25_374,
                    "record_in": 553,
                    "record_out": 664,
                },
            }
        ],
    }

    edits, superseded = carry.edits_in_force(str(project_dir), FINAL, [back])

    assert edits == []
    assert set(superseded) == {"c1"}


@pytest.mark.usefixtures("mock_dvr")
def test_a_rippled_cut_under_another_rows_item_refuses_before_writing(project_dir):
    """A ripple would trim the straddling graphic: refuse, write nothing."""
    live, staging = edited_reel_7(), rebuilt_reel_7()
    for timeline, record in ((live, 540), (staging, 540)):
        timeline.GetItemListInTrack("video", 2).append(
            _clip("semantic-late", 0, 40, record)._as_item(timeline)
        )
    seed_provenance(project_dir)
    resolve = FakeProject([FakeTimeline(MASTER), live, staging])

    with pytest.raises(ReelBuildError) as refused:
        promote(resolve, project_dir, STAGING)

    message = str(refused.value)
    assert "25,263..25,374" in message
    assert "'semantic-late' at record 540..580" in message
    assert getattr(staging, "deletes", 0) == 0
    assert sorted(resolve.names()) == sorted([MASTER, FINAL, STAGING])


def _item(
    row,
    name,
    source_in,
    source_out,
    record_in,
    record_out,
    track_type="video",
    track_index=1,
):
    return {
        "track_type": track_type,
        "track_index": track_index,
        "track_name": row,
        "name": name,
        "source_identity": f"file:/media/{name}",
        "source_in_frame": source_in,
        "source_out_frame": source_out,
        "record_in": record_in,
        "record_out": record_out,
        "duration": record_out - record_in,
        "enabled": True,
        "transform": {},
        "composite": {},
        "fusion": {},
        "color": {},
        "markers": [],
    }


def _pool_item(
    row,
    name,
    pool_id,
    source_in,
    source_out,
    record_in,
    record_out,
    track_type="video",
    track_index=1,
):
    """An item as the live timeline records it under a media pool id:
    `source_identity` names the pool item, `source_file` the file it
    plays - the two halves of one placement."""
    item = _item(row, name, source_in, source_out, record_in, record_out,
                 track_type=track_type, track_index=track_index)
    item["source_identity"] = f"media:{pool_id}"
    item["source_file"] = f"/media/{name}"
    return item


def _change_record(before_items, after_items, *, before_unit=(3840, 2160),
                   after_unit=(3840, 2160), after_end=None):
    before = {"metadata": {"transform_unit_resolution": before_unit},
              "items": before_items}
    after_timeline = {}
    if after_end is not None:
        after_timeline["end_frame"] = after_end
    after = {"metadata": {"transform_unit_resolution": after_unit},
             "timeline": after_timeline, "items": after_items}
    return {
        "id": "change-1",
        "timeline": FINAL,
        "before_snapshot": before,
        "after_snapshot": after,
        "changes": guard.snapshot_diff(before, after),
    }


def test_pan_tilt_rebase_across_unit_epoch_is_refused():
    before = _item("V1", "speaker", 100, 200, 10, 110)
    before["transform"] = {"Pan": 100.0, "Tilt": -40.0}
    after = {**before, "transform": {"Pan": 25.0, "Tilt": -10.0}}
    record = _change_record([before], [after],
                            before_unit=(1920, 1080),
                            after_unit=(3840, 2160))

    edits, uncarried = carry.derive_edits(record)

    assert edits == []
    assert len(uncarried) == 1
    assert "Pan, Tilt" in uncarried[0]["why"]
    assert "project resolution changed from 1920x1080 to 3840x2160" \
        in uncarried[0]["why"]
    assert "Confirm the framing on a fresh snapshot" in uncarried[0]["why"]


def test_promotion_refusal_explains_the_pan_tilt_epoch(project_dir):
    before = _item("V1", "speaker", 100, 200, 10, 110)
    before["transform"] = {"Pan": 100.0, "Tilt": -40.0}
    after = {**before, "transform": {"Pan": 25.0, "Tilt": -10.0}}
    record = _change_record([before], [after],
                            before_unit=(1920, 1080),
                            after_unit=(3840, 2160))
    _edits, uncarried = carry.derive_edits(record)
    detection = {"first_contact": False, "detected": [],
                 "pending": [record]}
    report = {"uncarried": [{"record_id": record["id"], **uncarried[0]}]}

    with pytest.raises(guard.EditorChangeRefused,
                       match="project resolution changed from 1920x1080 to "
                             "3840x2160"):
        guard.protect_editor_changes(
            str(project_dir), FINAL, record["after_snapshot"],
            record["before_snapshot"], record["before_snapshot"],
            detection=detection, carried_edits=report)


@pytest.mark.parametrize(
    ("edited_color", "changed_value"),
    [
        ({"cdl": {}, "color_group": "", "luts": [
            {"node": 2, "path": "Film Looks/Soft.cube"}]}, "luts"),
        ({"cdl": {"slope": [1.1, 1.0, 1.0]},
          "color_group": ""}, "cdl"),
    ],
)
def test_real_lut_and_grade_changes_still_refuse_promotion(
    project_dir, edited_color, changed_value
):
    before = _item("speaker", "speaker.mov", 0, 100, 0, 100)
    before["color"] = {"cdl": {}, "color_group": "", "luts": []}
    after_item = {**before, "color": edited_color}
    record = _change_record([before], [after_item])
    record["id"] = "real-color-change"
    detection = {"first_contact": False, "detected": [], "pending": [record]}
    _edits, uncarried = carry.derive_edits(record)
    report = {
        "uncarried": [{"record_id": record["id"], **entry} for entry in uncarried]
    }

    with pytest.raises(
        guard.EditorChangeRefused, match="color on video:speaker"
    ) as refused:
        guard.protect_editor_changes(
            str(project_dir),
            FINAL,
            record["after_snapshot"],
            record["before_snapshot"],
            record["before_snapshot"],
            detection=detection,
            carried_edits=report,
        )

    assert changed_value in str(refused.value)


def test_pan_tilt_change_in_one_unit_epoch_remains_carryable():
    before = _item("V1", "speaker", 100, 200, 10, 110)
    before["transform"] = {"Pan": 100.0, "Tilt": -40.0}
    after = {**before, "transform": {"Pan": 80.0, "Tilt": -40.0}}
    record = _change_record([before], [after])

    edits, uncarried = carry.derive_edits(record)

    assert [edit["field"] for edit in edits] == ["transform.Pan"]
    assert uncarried == []


def test_pan_tilt_change_without_a_recorded_unit_is_refused():
    before = _item("V1", "speaker", 100, 200, 10, 110)
    before["transform"] = {"Pan": 100.0, "Tilt": -40.0}
    after = {**before, "transform": {"Pan": 25.0, "Tilt": -10.0}}
    record = _change_record([before], [after], before_unit=None)

    edits, uncarried = carry.derive_edits(record)

    assert edits == []
    assert "no readable project resolution" in uncarried[0]["why"]


def test_a_fusion_comp_change_has_a_plain_language_carry_refusal():
    before = _item("V6", "logo.mov", 0, 71, 0, 71,
                   track_index=6)
    before["fusion"] = {"comp_count": 1, "digest": "old-graph"}
    after = {**before, "fusion": {"comp_count": 0}}
    record = _change_record([before], [after])

    edits, uncarried = carry.derive_edits(record)

    assert edits == []
    assert len(uncarried) == 1
    assert "Fusion comp" in uncarried[0]["why"]
    assert "cannot reconstruct a comp graph from an item snapshot" \
        in uncarried[0]["why"]
    assert "record it in a Ren plan" in uncarried[0]["why"]


def test_an_extend_lands_by_basename_and_span_when_its_pool_item_is_gone():
    """Reel 13, 2026-10-06: the recorded `media:` pool item is gone
    from a pool holding eight copies of LC4932.MXF, and the rebuild
    binds whichever lists first - so neither identity nor file matching
    (edits recorded before `source_file` was carried name no file) can
    reach the staged passage, and the extend refuses as unmappable
    although staging plays it. One staged item on the row playing this
    basename over this span is the passage; two is still ambiguity."""
    before = _item("Akshita", "LC4932.MXF", 47_563, 48_022, 1038, 1497)
    before["source_identity"] = "media:pool-dead"
    after = {**before, "source_out_frame": 48_028, "record_out": 1503,
             "duration": 465}
    record = _change_record([before], [after])
    edits, uncarried = carry.derive_edits(record)
    assert uncarried == []
    (extension,) = [edit for edit in edits if edit["kind"] == "extend"]
    assert extension.get("source_file") is None
    staged = _pool_item("Akshita", "LC4932.MXF", "pool-other", 47_563,
                        48_022, 1038, 1497)
    staged["left_offset"] = 0
    staged["right_offset"] = 1000
    plan = carry.plan_application([extension], {"items": [staged]}, FINAL)
    assert plan["trim"][0]["edit"]["kind"] == "extend"
    twin = {**staged, "source_identity": "media:pool-third"}
    with pytest.raises(carry.EditorEditCarryRefused,
                       match="does not play that passage"):
        carry.plan_application([extension],
                               {"items": [staged, twin]}, FINAL)


def test_a_tail_extension_carries_only_with_proven_source_headroom():
    original = _item("V1", "LC4932.MXF", 47_563, 48_022, 100, 559)
    following = _item("V1", "next.MXF", 100, 200, 559, 659)
    extended = {**original, "source_out_frame": 48_028,
                "record_out": 565, "duration": 465}
    shifted = {**following, "record_in": 565, "record_out": 665}
    record = _change_record([original, following], [extended, shifted])

    edits, uncarried = carry.derive_edits(record)

    assert uncarried == []
    (extension,) = [edit for edit in edits if edit["kind"] == "extend"]
    assert extension["after"]["tail"] == -6
    assert extension["ripple"] is True
    assert extension["wording"].startswith("Extend 'LC4932.MXF'")

    staging_item = {**original, "left_offset": 0, "right_offset": 6}
    plan = carry.plan_application([extension], {"items": [staging_item]},
                                  FINAL)
    assert plan["trim"][0]["edit"]["kind"] == "extend"

    staging_item["right_offset"] = 2
    with pytest.raises(carry.EditorEditCarryRefused,
                       match="source headroom cannot be proven"):
        carry.plan_application([extension], {"items": [staging_item]}, FINAL)


def test_a_tail_extension_is_verified_against_its_new_source_window():
    original = _item("V1", "speaker", 100, 200, 10, 110)
    extended = {**original, "source_out_frame": 206,
                "record_out": 116, "duration": 106}
    record = _change_record([original], [extended])

    edits, uncarried = carry.derive_edits(record)
    (extension,) = [edit for edit in edits if edit["kind"] == "extend"]

    assert uncarried == []
    carry.verify_carried_edits(
        {"edits": [extension]}, {"items": [extended]}, FINAL)
    with pytest.raises(carry.EditorEditCarryRefused,
                       match="extend video:V1"):
        carry.verify_carried_edits(
            {"edits": [extension]}, {"items": [original]}, FINAL)


def test_an_extend_carries_when_staging_plays_the_same_file_under_another_pool_id():
    """Reel 13, 2026-10-05: the captain's +6-frame extend on LC4932 could
    not map when the same source file existed as two pool items - an
    accidental duplicate import - and the rebuild picked the other one.
    Same file, same source range, different media identity: the edit
    lands by file."""
    original = _pool_item("V1", "LC4932.MXF", "pool-A", 47_563, 48_022,
                          100, 559)
    extended = {**original, "source_out_frame": 48_028, "record_out": 565,
                "duration": 465}
    record = _change_record([original], [extended])

    edits, uncarried = carry.derive_edits(record)
    assert uncarried == []
    (extension,) = [edit for edit in edits if edit["kind"] == "extend"]
    assert extension["source_file"] == "/media/LC4932.MXF"

    # Staging plays the unextended passage under the OTHER pool item of
    # the same file: same source_file, different source_identity.
    staged_item = {**original, "source_identity": "media:pool-B",
                   "left_offset": 0, "right_offset": 6}
    plan = carry.plan_application([extension], {"items": [staged_item]},
                                  FINAL)
    assert [step["item"]["source_identity"] for step in plan["trim"]] == \
        ["media:pool-B"]

    carried = [{**staged_item, "source_out_frame": 48_028,
                "record_out": 565, "duration": 465}]
    carry.verify_carried_edits({"edits": [extension]},
                               {"items": carried}, FINAL)
    with pytest.raises(carry.EditorEditCarryRefused,
                       match="extend video:V1"):
        carry.verify_carried_edits({"edits": [extension]},
                                   {"items": [staged_item]}, FINAL)


def test_a_passage_played_by_two_pool_items_of_one_file_refuses_as_ambiguous():
    """The same file under two pool items, placed twice on one row, is two
    staged items the edit cannot choose between - refused by name, the
    same rule the captain set for note carry."""
    original = _pool_item("V1", "LC4932.MXF", "pool-A", 47_563, 48_022,
                          100, 559)
    extended = {**original, "source_out_frame": 48_028, "record_out": 565,
                "duration": 465}
    record = _change_record([original], [extended])
    edits, _ = carry.derive_edits(record)
    (extension,) = [edit for edit in edits if edit["kind"] == "extend"]

    staged = [{**original, "source_identity": "media:pool-B",
               "left_offset": 0, "right_offset": 6},
              {**original, "source_identity": "media:pool-C",
               "left_offset": 0, "right_offset": 6}]
    with pytest.raises(carry.EditorEditCarryRefused,
                       match="no single item to land on"):
        carry.plan_application([extension], {"items": staged}, FINAL)


@pytest.mark.usefixtures("mock_dvr")
def test_a_tail_extension_and_ripple_are_written_on_the_resolve_double(
        tmp_path):
    from library.tools import reel_read

    media = pool_clip("/media/speaker.mov", frames=600)
    following_media = pool_clip("/media/next.mov", frames=500)
    staging = rows_timeline(
        STAGING,
        {"V1": [item(media, 0, 100, 100),
                item(following_media, 100, 100, 0)]},
        track_names={"V1": "Speakers"})
    project = FakeProject("Mock Project", [staging], current=staging)
    before_items = guard.snapshot_items(reel_read.read_tracks(staging))
    extended = {**before_items[0], "source_out_frame": 206,
                "record_out": 106, "duration": 106}
    shifted = {**before_items[1], "record_in": 106, "record_out": 206}
    record = _change_record(before_items, [extended, shifted])
    edits, uncarried = carry.derive_edits(record)
    assert uncarried == []
    extension = next(edit for edit in edits if edit["kind"] == "extend")
    plan = carry.plan_application(edits, {"items": before_items}, FINAL)

    assert plan["trim"]
    written = carry._apply_trims(
        str(tmp_path), project, staging,
        [step["edit"] for step in plan["trim"]], FINAL)
    after_items = guard.snapshot_items(reel_read.read_tracks(staging))
    carry.verify_carried_edits(
        {"edits": edits},
        {"timeline": {"end_frame": staging.GetEndFrame()},
         "items": after_items}, FINAL)

    extended_after = next(candidate for candidate in after_items
                          if candidate["name"] == "speaker.mov")
    next_after = next(candidate for candidate in after_items
                      if candidate["name"] == "next.mov")
    assert (extended_after["source_in_frame"],
            extended_after["source_out_frame"],
            extended_after["record_out"]) == (100, 206, 106)
    assert next_after["record_in"] == 106
    assert extension["id"] in written


# Reel 13, 2026-10-05: the captain held Akshita's caption cards a few
# frames longer (a tail extended, the next card's head trimmed to meet it,
# every word left where it was) and extended the picture under them by 6
# frames with a ripple. PR 1689 then re-planned the captions, so every
# card on staging is a new render: same segment, new digest, new identity.
SEGMENT = "sub_akshita_5d01801c-6efe-48c6-ae1b_1983975-1985745"


def _card(digest, source_in, source_out, record_in, *, right=12):
    card = _item("Subtitles", f"{SEGMENT}_{digest}.mov", source_in,
                 source_out, record_in, record_in + source_out - source_in,
                 track_index=4)
    return {**card, "left_offset": source_in, "right_offset": right}


def _picture(pool_id=None):
    """The Reel 13 V1 picture; `pool_id` reads it under a media pool item,
    as the live timeline records a duplicate import."""
    def picture(name, source_in, source_out, record_in, record_out):
        if pool_id is None:
            return _item("Akshita", name, source_in, source_out, record_in,
                         record_out)
        return _pool_item("Akshita", name, pool_id, source_in, source_out,
                          record_in, record_out)
    return [picture("LC4932.MXF", 47563, 48022, 1038, 1497),
            picture("LC4932.MXF", 12006, 12318, 1497, 1809)]


def _caption_record():
    """The captain's two caption re-spans over an unchanged picture."""
    before = [*_picture(), _card("0e607e0c", 12, 53, 1043),
              _card("682aef85", 12, 50, 1086)]
    after = [*_picture(),
             {**_card("0e607e0c", 12, 59, 1043), "right_offset": 6},
             {**_card("682aef85", 17, 56, 1091), "right_offset": 6}]
    return _change_record(before, after)


def _replanned(right=12):
    """Staging: the same picture, the same cards re-rendered."""
    return [*_picture(), _card("d6c65589", 12, 53, 1043, right=right),
            _card("45cd4cb1", 12, 51, 1086, right=right)]


def test_caption_card_spans_carry_onto_the_replanned_cards():
    """Each edit lands on the card of its segment that starts over the
    picture the captain's card started over, re-expressed as the record
    span he set - never on the content-hashed name, which is gone."""
    edits, uncarried = carry.derive_edits(_caption_record())
    assert uncarried == []
    assert [edit["kind"] for edit in edits] == ["caption_span"] * 2
    staged = _replanned()
    plan = carry.plan_application(edits, {"items": staged}, FINAL)
    landed = {step["item"]["name"]: step["edit"]["after"]
              for step in plan["trim"]}
    assert landed == {
        f"{SEGMENT}_d6c65589.mov": {"source_in_frame": 12,
                                    "source_out_frame": 59, "head": 0,
                                    "tail": -6, "record_in": 1043},
        f"{SEGMENT}_45cd4cb1.mov": {"source_in_frame": 17,
                                    "source_out_frame": 56, "head": 5,
                                    "tail": -5, "record_in": 1091},
    }
    assert all(step["edit"]["ripple"] is False for step in plan["trim"])
    with pytest.raises(carry.EditorEditCarryRefused,
                       match="wanted one card at record 1,043..1,090"):
        carry.verify_carried_edits({"edits": edits}, {"items": staged},
                                   FINAL)
    carried = [*_picture(), _card("d6c65589", 12, 59, 1043),
               _card("45cd4cb1", 17, 56, 1091)]
    carry.verify_carried_edits({"edits": edits}, {"items": carried}, FINAL)
    again = carry.plan_application(edits, {"items": carried}, FINAL)
    assert sorted(again["already_held"]) == sorted(e["id"] for e in edits)


def test_a_caption_span_anchors_onto_a_picture_under_another_pool_id():
    """The span's picture anchor is read off staging's V1, so it is read
    by the picture's media identity; when the rebuild plays that picture
    under the other pool item of the same file (Reel 13's LC4932), the
    anchor still lands by file."""
    before = [*_picture("pool-A"),
              _card("0e607e0c", 12, 53, 1043)]
    after = [*_picture("pool-A"),
             {**_card("0e607e0c", 12, 59, 1043), "right_offset": 6}]
    edits, uncarried = carry.derive_edits(_change_record(before, after))
    assert uncarried == []
    (span,) = edits
    assert span["kind"] == "caption_span"

    # Staging's V1 plays the same picture file under another pool item.
    staged = [*_picture("pool-B"), _card("d6c65589", 12, 53, 1043)]
    plan = carry.plan_application([span], {"items": staged}, FINAL)
    landed = {step["item"]["name"]: step["edit"]["after"]
              for step in plan["trim"]}
    assert landed == {
        f"{SEGMENT}_d6c65589.mov": {"source_in_frame": 12,
                                    "source_out_frame": 59, "head": 0,
                                    "tail": -6, "record_in": 1043},
    }


def test_caption_span_refuses_when_no_card_starts_where_his_did():
    """The re-plan moved the segment's card off the picture frame the
    captain's card started over: no single card to land on, refused by
    name rather than guessed onto a neighbour."""
    edits, _ = carry.derive_edits(_caption_record())
    staged = [*_picture(), _card("d6c65589", 12, 53, 1047),
              _card("45cd4cb1", 12, 51, 1086)]
    with pytest.raises(carry.EditorEditCarryRefused,
                       match=r"0 card\(s\) of that segment"):
        carry.plan_application(edits, {"items": staged}, FINAL)


def test_caption_span_refuses_without_the_source_headroom():
    edits, _ = carry.derive_edits(_caption_record())
    with pytest.raises(carry.EditorEditCarryRefused,
                       match="the span needs 6"):
        carry.plan_application(edits, {"items": _replanned(right=3)}, FINAL)


def test_a_caption_card_slid_against_its_words_is_not_a_span():
    """Same record span, a different part of the card: a re-time."""
    before = [*_picture(), _card("0e607e0c", 12, 53, 1043)]
    after = [*_picture(), _card("0e607e0c", 15, 56, 1043)]
    edits, uncarried = carry.derive_edits(_change_record(before, after))
    assert edits == []
    assert "moved against its own words" in uncarried[0]["why"]


def _ripple_record(*, frame_after=(0, 1828)):
    """The captain's +6 extension at record 1,497, rippled, run under the
    Frame overlay and a caption card ending with the line."""
    frame = _item("Frame", "tv_frame.mov", 0, 1828, 0, 1828, track_index=3)
    card = _card("11397b27", 12, 60, 1450)
    before = [*_picture(), frame, card]
    after = [_item("Akshita", "LC4932.MXF", 47563, 48028, 1038, 1503),
             _item("Akshita", "LC4932.MXF", 12006, 12318, 1503, 1815),
             {**frame, "source_out_frame": frame_after[1],
              "record_out": frame_after[1],
              "duration": frame_after[1]},
             card]
    return _change_record(before, after)


def _rippled_extend(record):
    edits, _ = carry.derive_edits(record)
    (extend,) = [edit for edit in edits if edit["kind"] == "extend"]
    assert extend["ripple"] is True
    return extend


def test_a_rippled_extension_records_the_rows_it_ran_under_untouched():
    extend = _rippled_extend(_ripple_record())
    assert extend["ripple_held_rows"] == ["video:Frame", "video:Subtitles"]
    assert extend["ripple_moved"] == [
        ["video", 1, "Akshita", "file:/media/LC4932.MXF", 12006, 12318]]


def test_a_rippled_extension_replays_where_staging_is_the_timeline_he_cut():
    """Staging plays the passage ending at the frame he extended it at,
    and every straddler sits on a row he held: the ripple carries, and
    the write leaves those rows alone as Resolve did for him."""
    extend = _rippled_extend(_ripple_record())
    staged = [{**_picture()[0], "right_offset": 100}, _picture()[1],
              _item("Frame", "tv_frame.png", 0, 1840, 0, 1840,
                    track_index=3),
              _card("abe8cc1f", 12, 60, 1450)]
    plan = carry.plan_application([extend], {"items": staged}, FINAL)
    assert [step["item"]["record_out"] for step in plan["trim"]] == [1497]


def test_a_rippled_extension_still_refuses_what_is_not_a_replay():
    """Not widened: a straddler on a row he never ran the ripple under
    (a music bed), or a passage the rebuild moved, refuses as before."""
    extend = _rippled_extend(_ripple_record())
    bed = _item("Music", "bed.wav", 0, 1900, 0, 1900, track_type="audio",
                track_index=3)
    staged = [{**_picture()[0], "right_offset": 100}, _picture()[1], bed]
    with pytest.raises(carry.EditorEditCarryRefused,
                       match="a rippled trim under another row's item"):
        carry.plan_application([extend], {"items": staged}, FINAL)
    moved = [_item("Akshita", "LC4932.MXF", 47563, 48022, 1040, 1499),
             _item("Frame", "tv_frame.png", 0, 1840, 0, 1840,
                   track_index=3)]
    moved[0]["right_offset"] = 100
    with pytest.raises(carry.EditorEditCarryRefused,
                       match="video:Frame 'tv_frame.png'"):
        carry.plan_application([extend], {"items": moved}, FINAL)


def test_a_still_overlay_follows_the_ripple_and_the_rest_is_held():
    """The captain's standing rule, 2026-10-05: under a replayed ripple a
    PNG still extends to the new end - his Reel 13 note asked the TV
    frame and post header to reach it - and anything else is held."""
    extend = _rippled_extend(_ripple_record())
    steps = [{"edit": extend}]
    still = _item("Frame", "tv_frame.png", 0, 1840, 0, 1840, track_index=3)
    movie = _item("Frame", "tv_frame.mov", 0, 1840, 0, 1840, track_index=3)
    card = _card("abe8cc1f", 12, 60, 1450)
    assert carry._ripple_replays(steps, 1497, still)
    assert not carry._ripple_holds(steps, 1497, still)
    assert carry._ripple_holds(steps, 1497, movie)
    assert carry._ripple_holds(steps, 1497, card)
    assert not carry.is_still({**still, "media_type": "Video"})


def test_the_guard_reads_a_note_that_landed_on_its_row_as_carried():
    """Reel 13's post header: his note was typed on the old movie; the
    rebuild's PNG on the same row now carries it, keyed in its own
    source space. A note that did not land is still a loss."""
    note = {"frame": 1815, "color": "Blue", "name": "feedback",
            "note": "this clip does not go all the way to the end",
            "duration": 1, "custom_data": ""}
    old = _item("Post Header", "post_header_old.mov", 0, 1827, 0, 1828,
                track_index=7)
    change = {"kind": "item_changed",
              "identity": list(guard.stable_item_key(old)),
              "before": old, "after": {**old, "markers": [note]},
              "changed": {"markers": {"before": [], "after": [note]}}}
    png = _item("Post Header", "post_header_new.png", 86_313, 88_159, 0,
                1846, track_index=7)
    landed = {"items": [{**png, "markers": [{**note,
                                             "frame": 86_313 + 1815}]}]}
    assert guard._change_is_carried(change, landed)
    assert not guard._change_is_carried(change, {"items": [png]})
    elsewhere = {"items": [{**png, "track_index": 6,
                             "track_name": "Motion Graphics",
                             "markers": [note]}]}
    assert not guard._change_is_carried(change, elsewhere)


def test_the_guard_reads_a_change_onto_the_same_file_under_another_pool_id():
    """The promotion guard matches a change by media pool identity; when
    the rebuild plays the same file under the other pool item of a
    duplicate import, the change is carried by file instead. The extend
    reads as a removal plus an addition; both must be carried."""
    before = _pool_item("V1", "LC4932.MXF", "pool-A", 47_563, 48_022,
                        100, 559)
    after = {**before, "source_out_frame": 48_028, "record_out": 565,
             "duration": 465}
    changes = [c for c in guard.snapshot_diff({"items": [before]},
                                              {"items": [after]})
               if c["kind"].startswith("item_")]
    assert [c["kind"] for c in changes] == ["item_removed", "item_added"]
    carried = {**before, "source_identity": "media:pool-B",
               "source_out_frame": 48_028, "record_out": 565,
               "duration": 465}
    assert all(guard._change_is_carried(c, {"items": [carried]})
               for c in changes)
    unextended = {**before, "source_identity": "media:pool-B"}
    assert not any(guard._change_is_carried(c, {"items": [unextended]})
                   for c in changes)


def test_the_guard_refuses_a_change_two_pool_items_of_one_file_could_be():
    """Two staged items play the same file over the changed range under
    different pool ids: the change cannot choose between them, so it is
    not carried - never guessed onto one."""
    before = _pool_item("V1", "LC4932.MXF", "pool-A", 47_563, 48_022,
                        100, 559)
    after = {**before, "source_out_frame": 48_028, "record_out": 565,
             "duration": 465}
    (added,) = [c for c in guard.snapshot_diff({"items": [before]},
                                              {"items": [after]})
                if c["kind"] == "item_added"]
    extended = {**before, "source_out_frame": 48_028, "record_out": 565,
                "duration": 465}
    assert not guard._change_is_carried(
        added, {"items": [{**extended, "source_identity": "media:pool-B"},
                           {**extended, "source_identity": "media:pool-C"}]})


def test_the_guard_finds_a_replanned_caption_card_at_his_span():
    """The promotion guard cannot match a re-rendered card by identity;
    it matches its segment on the same row, and judges the span."""
    record = _caption_record()
    added = [change for change in record["changes"]
             if change["kind"] == "item_added"]
    carried = {"items": [*_picture(), _card("d6c65589", 12, 59, 1043),
                         _card("45cd4cb1", 17, 56, 1091)]}
    assert all(guard._change_is_carried(change, carried)
               for change in added)
    as_planned = {"items": _replanned()}
    assert not any(guard._change_is_carried(change, as_planned)
                   for change in added)


def test_the_guard_reads_a_ripple_shift_as_carried_with_its_ripple():
    """An item his ripple only moved, which the rebuild re-rendered, is
    carried once the ripple is - and only a pure shift, only then."""
    record = _ripple_record()
    extend = _rippled_extend(record)
    (shifted,) = [change for change in record["changes"]
                  if change["kind"] == "item_changed"]
    rippled = guard._moved_by_carried_ripples({"edits": [extend]})
    assert guard._moved_by_ripple(shifted, rippled)
    assert not guard._moved_by_ripple(shifted, set())
    retimed = {**shifted, "changed": {**shifted["changed"],
                                      "enabled": {"before": True,
                                                  "after": False}}}
    assert not guard._moved_by_ripple(retimed, rippled)


def test_source_partition_splits_overlay_and_defers_its_cut_segments():
    original = _item("TV Frame", "tv_frame.mov", 0, 1000, 0, 1000,
                     track_index=3)
    segments = [
        _item("TV Frame", "tv_frame.mov", 0, 400, 0, 400,
              track_index=3),
        _item("TV Frame", "tv_frame.mov", 500, 800, 400, 700,
              track_index=3),
        _item("TV Frame", "tv_frame.mov", 900, 1000, 700, 800,
              track_index=3),
    ]
    record = _change_record([original], segments, after_end=800)

    edits, uncarried = carry.derive_edits(record)

    assert uncarried == []
    cuts = [edit for edit in edits if edit["kind"] == "cut"]
    assert [(edit["source_in_frame"], edit["source_out_frame"])
            for edit in cuts] == [(400, 500), (800, 900)]
    assert all(edit["ripple"] for edit in cuts)

    staged = {**original, "left_offset": 0, "right_offset": 0}
    plan = carry.plan_application(edits, {"items": [staged]}, FINAL)
    assert len(plan["split"]) == 1
    assert len(plan["deferred_cut"]) == 2
    assert plan["delete"] == []

    final_items = [dict(item) for item in segments]
    carry.verify_carried_edits(
        {"edits": edits},
        {"timeline": {"end_frame": 800}, "items": final_items}, FINAL)


def test_a_split_already_present_on_staging_does_not_require_removed_gaps():
    original = _item("TV Frame", "tv_frame.mov", 0, 1000, 0, 1000,
                     track_index=3)
    segments = [
        _item("TV Frame", "tv_frame.mov", 0, 400, 0, 400,
              track_index=3),
        _item("TV Frame", "tv_frame.mov", 500, 800, 400, 700,
              track_index=3),
        _item("TV Frame", "tv_frame.mov", 900, 1000, 700, 800,
              track_index=3),
    ]
    record = _change_record([original], segments, after_end=800)
    edits, uncarried = carry.derive_edits(record)
    split = next(edit for edit in edits if edit["kind"] == "split")

    assert uncarried == []
    plan = carry.plan_application([split], {"items": segments}, FINAL)
    assert plan["split"] == []
    assert plan["already_held"] == [split["id"]]


def test_overlay_split_and_ripple_cuts_apply_on_the_resolve_double(
        tmp_path):
    from library.tools import reel_read

    media = pool_clip("/media/tv_frame.mov", frames=1338)
    overlay = item(media, 0, 1338, 0)
    speakers = [pool_clip(f"/media/speaker-{n}.mov", frames=700)
                for n in range(1, 6)]
    dialogue = [pool_clip(f"/media/dialogue-{n}.mov", frames=700)
                for n in range(1, 6)]
    staging = rows_timeline(
        STAGING,
        {"V1": [item(speakers[0], 0, 553, 0),
                item(speakers[1], 553, 112, 0),
                item(speakers[2], 665, 421, 0),
                item(speakers[3], 1086, 233, 0),
                item(speakers[4], 1319, 19, 0)],
         "V3": [overlay],
         "A1": [item(dialogue[0], 0, 553, 0),
                item(dialogue[1], 553, 112, 0),
                item(dialogue[2], 665, 421, 0),
                item(dialogue[3], 1086, 233, 0),
                item(dialogue[4], 1319, 19, 0)]},
        track_names={"V1": "Speakers", "V3": "TV Frame",
                     "A1": "Dialogue"})
    project = FakeProject("Mock Project", [staging], current=staging)
    before_items = guard.snapshot_items(reel_read.read_tracks(staging))
    after_items = []
    for original in before_items:
        row = guard.row_key(original["track_type"], original["track_name"])
        if row == "video:TV Frame":
            for source_start, source_end, record_start, record_end in (
                    (0, 553, 0, 553), (665, 1086, 553, 974),
                    (1319, 1338, 974, 993)):
                after_items.append({
                    **original,
                    "source_in_frame": source_start,
                    "source_out_frame": source_end,
                    "left_offset": source_start,
                    "record_in": record_start,
                    "record_out": record_end,
                    "duration": record_end - record_start,
                })
            continue
        if row not in ("video:Speakers", "audio:Dialogue"):
            continue
        start = original["record_in"]
        if start in (553, 1086):
            continue
        shift = -345 if start >= 1319 else -112 if start >= 665 else 0
        after_items.append({**original,
                            "record_in": start + shift,
                            "record_out": original["record_out"] + shift})
    record = _change_record(before_items, after_items, after_end=993)
    record.update({"id": "split-live", "timeline": FINAL})
    edits, uncarried = carry.derive_edits(record)
    assert uncarried == []
    cuts = [edit for edit in edits if edit["kind"] == "cut"]
    assert len(cuts) == 6
    assert all(edit["ripple"] for edit in cuts)
    initial = carry.plan_application(edits, {"items": before_items}, FINAL)

    assert carry._apply_splits(
        str(tmp_path), project, staging, initial["split"], FINAL)
    split_items = guard.snapshot_items(reel_read.read_tracks(staging))
    overlay_parts = [clip for clip in split_items
                     if clip["track_name"] == "TV Frame"]
    assert sorted((clip["source_in_frame"], clip["source_out_frame"])
                  for clip in overlay_parts) == [
                      (0, 553), (553, 665), (665, 1086),
                      (1086, 1319), (1319, 1338)]

    cut_plan = carry.plan_application(cuts, {"items": split_items}, FINAL)
    carry.apply_plan(staging, project, cut_plan, FINAL,
                     str(tmp_path), STAGING)
    final_items = guard.snapshot_items(reel_read.read_tracks(staging))
    carry.verify_carried_edits(
        {"edits": edits},
        {"timeline": {"end_frame": staging.GetEndFrame()},
         "items": final_items}, FINAL)
    assert sorted((clip["source_in_frame"], clip["source_out_frame"],
                   clip["record_in"], clip["record_out"])
                  for clip in final_items
                  if clip["track_name"] == "TV Frame") == [
                      (0, 553, 0, 553), (665, 1086, 553, 974),
                      (1319, 1338, 974, 993)]
    assert [clip["record_in"] for clip in final_items
            if clip["track_name"] == "Speakers"] == [0, 553, 974]
    assert [clip["record_in"] for clip in final_items
            if clip["track_name"] == "Dialogue"] == [0, 553, 974]


def test_a_retimed_added_overlay_segment_is_reported_for_decision():
    original = _item("Post Header", "post_header.mov", 0, 968, 0, 968,
                     track_index=7)
    segments = [
        _item("Post Header", "post_header.mov", 0, 553, 0, 553,
              track_index=7),
        _item("Post Header", "post_header.mov", 665, 968, 553, 856,
              track_index=7),
        _item("Post Header", "post_header.mov", 300, 417, 856, 973,
              track_index=7),
        _item("Post Header", "post_header.mov", 968, 969, 856, 875,
              track_index=7),
    ]
    segments[-1]["record_in"] = 974
    segments[-1]["record_out"] = 993
    segments[-1]["duration"] = 19
    record = _change_record([original], segments, after_end=993)

    edits, uncarried = carry.derive_edits(record)

    assert not any(edit["kind"] == "split" for edit in edits)
    assert any("one-frame-per-record split" in issue["why"]
               for issue in uncarried)
    assert any("added by the editor" in issue["why"]
               and "declare its source, timing and treatment in the Ren plan"
               in issue["why"]
               for issue in uncarried)


def test_promotion_refusal_surfaces_the_unmappable_overlay_split(tmp_path):
    original = _item("Post Header", "post_header.mov", 0, 968, 0, 968,
                     track_index=7)
    segments = [
        _item("Post Header", "post_header.mov", 0, 553, 0, 553,
              track_index=7),
        _item("Post Header", "post_header.mov", 665, 968, 553, 856,
              track_index=7),
        _item("Post Header", "post_header.mov", 300, 417, 856, 973,
              track_index=7),
        _item("Post Header", "post_header.mov", 968, 969, 856, 875,
              track_index=7),
    ]
    segments[-1].update(record_in=974, record_out=993, duration=19)
    record = _change_record([original], segments, after_end=993)
    record.update({"id": "post-header-split", "timeline": FINAL})
    _edits, uncarried = carry.derive_edits(record)
    detection = {"first_contact": False, "detected": [],
                 "pending": [record]}
    report = {"uncarried": [{"record_id": record["id"], **issue}
                             for issue in uncarried]}

    with pytest.raises(guard.EditorChangeRefused) as refused:
        guard.protect_editor_changes(
            str(tmp_path), FINAL, record["after_snapshot"],
            record["before_snapshot"], record["before_snapshot"],
            detection=detection, carried_edits=report)

    assert "one source-ordered, one-frame-per-record split" in str(refused.value)
    assert "declare its source, timing and treatment in the Ren plan" \
        in str(refused.value)


def test_an_ending_overlay_move_uses_the_timeline_end_as_its_anchor():
    before = _item("Logo", "logo.mov", 0, 20, 80, 100,
                   track_index=6)
    after = {**before, "record_in": 90, "record_out": 110}
    record = _change_record([before], [after], after_end=110)

    edits, uncarried = carry.derive_edits(record)

    assert uncarried == []
    (move,) = [edit for edit in edits if edit["kind"] == "move"]
    assert move["after"]["anchor_kind"] == "timeline_end"
    assert move["after"]["anchor_offset"] == -20
    assert carry._move_target([], move["after"], 210) == 190


def test_picture_row_reorder_refuses_with_the_story_mapping_reason():
    before = _item("Speakers", "speaker", 100, 200, 0, 100)
    after = {**before, "record_in": 50, "record_out": 150}
    record = _change_record([before], [after], after_end=150)

    edits, uncarried = carry.derive_edits(record)

    assert edits == []
    assert "reordering V1 can change linked speech, captions and story order" \
        in uncarried[0]["why"]
    assert "approved group reorder mapping" in uncarried[0]["why"]


@pytest.mark.usefixtures("mock_dvr")
def test_reel_7_as_resolve_reads_it_derives_rippled_cuts():
    """The live Reel 7 read, 2026-10-02: each cut passage takes its
    captions with it, and Resolve reads the passage's source out a frame
    short of its record span (112 frames read as 25,263-25,374). Both
    once made the closed gap read as a lift and the picture after it as
    a reorder."""

    def picture(rows):
        return [
            _item("Speakers", name, a, b, r0, r1) for name, a, b, r0, r1 in rows
        ] + [
            _item("Dialogue", name, a, b, r0, r1, "audio", 1)
            for name, a, b, r0, r1 in rows
        ]

    before = picture(
        [
            ("SpeakerOne", 22_232, 22_347, 0, 116),
            ("SpeakerTwo", 22_257, 22_693, 116, 553),
            ("SpeakerTwo", 25_263, 25_374, 553, 665),
            ("SpeakerOne", 25_557, 25_750, 665, 859),
            ("SpeakerOne", 60_745, 60_978, 859, 1_093),
        ]
    ) + [
        _item("Speakers", "freeze", 0, 18, 1_093, 1_112),
        _item("Subtitles", "cap-a", 12, 38, 561, 587, track_index=2),
        _item("Subtitles", "cap-b", 12, 32, 587, 607, track_index=2),
        _item("Subtitles", "cap-c", 12, 50, 669, 707, track_index=2),
        _item("Subtitles", "cap-d", 12, 55, 859, 902, track_index=2),
    ]
    after = picture(
        [
            ("SpeakerOne", 22_232, 22_347, 0, 116),
            ("SpeakerTwo", 22_257, 22_693, 116, 553),
            ("SpeakerOne", 25_557, 25_750, 553, 747),
        ]
    ) + [
        _item("Speakers", "freeze", 0, 18, 747, 766),
        _item("Subtitles", "cap-c", 12, 50, 557, 595, track_index=2),
    ]
    record = {
        "id": "reel-7",
        "timeline": FINAL,
        "changes": guard.snapshot_diff({"items": before}, {"items": after}),
        "before_snapshot": {"items": before},
        "after_snapshot": {"items": after},
    }

    edits, uncarried = carry.derive_edits(record, plan_version="plan-v1")

    assert uncarried == []
    cuts = sorted(
        (edit["row"], edit["name"], edit["ripple"])
        for edit in edits
        if edit["kind"] == "cut"
    )
    assert cuts == [
        ("audio:Dialogue", "SpeakerOne", True),
        ("audio:Dialogue", "SpeakerTwo", True),
        ("video:Speakers", "SpeakerOne", True),
        ("video:Speakers", "SpeakerTwo", True),
        ("video:Subtitles", "cap-a", True),
        ("video:Subtitles", "cap-b", True),
        ("video:Subtitles", "cap-d", True),
    ]
    assert [edit for edit in edits if edit["kind"] == "move"] == []


# --------------------------------------------------------------------------
# From test_editor_edit_carry_composed.py
#
# The editor's trims and moves are carried through a rebuild, or refused.
#
# Reel 7's shape again, with the two edits Resolve has no verb for: the
# editor trimmed 37 frames off the tail of the SpeakerTwo passage and closed
# the gap (picture and sound), and dragged a Semantic graphic to sit over
# a different moment of the SpeakerOne shot. A rebuild restores the full
# passage and the graphic's planned place. Carrying them goes through
# `composed_edit` - delete and re-place - against the fake Resolve that
# models what the composition defends against
# (`tests/composed_edit_harness.py`), and the promoted timeline must
# play what the edited one played, item for item.

NAMES = {"V1": "Speakers", "V3": "Semantic", "A1": "Dialogue"}


@pytest.fixture
def project_dir_2(tmp_path):
    root = tmp_path / "project"
    (root / "pipeline_output" / "review").mkdir(parents=True)
    (root / "pipeline_output" / "review" / "plan_provenance.json"
     ).write_text(json.dumps({"built_reels": [STAGING],
                              "plan_content_hash": "plan-v1"}),
                  encoding="utf-8")
    return root


def Timeline(name, rows):
    """A Reel 7 timeline with the field test's row names."""
    return rows_timeline(name, rows, track_names=NAMES)


def Project(timelines):
    """The live project, the master timeline current."""
    return FakeProject("Mock Project", timelines, current=timelines[0])


SPEAKERONE = pool_clip("/media/speakerone.mov", frames=100_000)
SPEAKERTWO = pool_clip("/media/speakertwo.mov", frames=100_000)
CARD = pool_clip("/media/semantic-card.mov", frames=200)
LATE = pool_clip("/media/semantic-late.mov", frames=200)


def reel(name, *, speakertwo=437, late_at=700, card_on=True, comps=False):
    """V1 picture with A1 sound, and two Semantic graphics on V3."""
    shift = 437 - speakertwo
    passages = [(SPEAKERONE, 22_232, 116, 0), (SPEAKERTWO, 22_257, speakertwo, 116),
                (SPEAKERONE, 25_557, 194, 553 - shift),
                (SPEAKERONE, 60_745, 234, 747 - shift)]

    def picture(mpi, left, duration, start):
        frames = frames_of(mpi)
        windows = ([{"MediaSource": "Timeline", "GlobalIn": -left,
                     "GlobalOut": frames - left - 1,
                     "ClipTimeStart": -left,
                     "ClipTimeEnd": frames - left - 1,
                     "MediaID": "", "AudioTrack": "Timeline Audio"}]
                   if comps else [])
        return item(mpi, start, duration, left, comp_windows=windows)

    timeline = Timeline(name, {
        "V1": [picture(*p) for p in passages],
        "V3": [item(CARD, 20, 40, 0, enabled=card_on),
               item(LATE, late_at, 30, 0)],
        "A1": [item(mpi, start, duration, left)
               for mpi, left, duration, start in passages],
    })
    return timeline


def edited():
    """The editor's cut: SpeakerTwo 37 frames shorter with the gap closed,
    the card off, and the late graphic dragged earlier (600, not the
    rippled 663) over SpeakerOne source frame 25,641."""
    return reel(FINAL, speakertwo=400, late_at=600, card_on=False)


def played_2(timeline):
    return {key: [(i.GetMediaPoolItem().GetName(), i.GetLeftOffset(),
                   i.GetDuration(), i.GetStart(), i.GetClipEnabled())
                  for i in items]
            for key, items in sorted(timeline.rows.items())}


def promote_2(project, project_dir_2):
    with ExitStack() as stack:
        stack.enter_context(patch(
            "library.tools.resolve_locale.scriptapp_preserving_locale"))
        stack.enter_context(patch(
            "library.tools.reel_build.resolve_project_exactly",
            return_value=project))
        stack.enter_context(patch(
            "library.tools.reel_disabled_clip_carry.carry_disabled_state",
            return_value={"unchanged_unmatched": [], "safe_replacements": [],
                          "carried": [], "refused": [], "matched": []}))
        stack.enter_context(patch(
            "library.tools.marker_feedback.read_notes", return_value=[]))
        stack.enter_context(patch(
            "library.tools.marker_gate.verify_promotion"))
        return promote_staged_reels(
            str(project_dir_2), "Mock Project", MASTER, {FINAL: STAGING},
            organise=False,
            track_plans=no_a_roll_track_plans({FINAL: STAGING}))


def provenance_2(project_dir_2):
    return json.loads((project_dir_2 / "pipeline_output" / "review"
                       / "plan_provenance.json").read_text(encoding="utf-8"))


@pytest.mark.usefixtures("mock_dvr")
def test_a_rippled_trim_and_a_moved_graphic_are_carried(project_dir_2):
    live, staging = edited(), reel(STAGING)
    wanted = played_2(live)
    project = Project([Timeline(MASTER, {}), live, staging])

    promoted = promote_2(project, project_dir_2)

    assert promoted["promoted"] == [FINAL]
    assert staging.GetName() == FINAL
    assert played_2(staging) == wanted
    # The re-placed items kept their treatment: the reference copy is
    # gone and every grade came across.
    assert sorted(project.names()) == sorted([MASTER, FINAL])
    edits = provenance_2(project_dir_2)["carried_editor_edits"][FINAL]
    kinds = sorted((edit["kind"], edit["row"]) for edit in edits)
    assert kinds == [("enabled", "video:Semantic"),
                     ("move", "video:Semantic"),
                     ("trim", "audio:Dialogue"),
                     ("trim", "video:Speakers")]
    trim = next(edit for edit in edits if edit["kind"] == "trim"
                and edit["row"] == "video:Speakers")
    assert trim["ripple"] is True
    assert (trim["after"]["head"], trim["after"]["tail"]) == (0, 37)
    assert trim["wording"].startswith("Trim 'speakertwo.mov' source 22,257..")
    move = next(edit for edit in edits if edit["kind"] == "move")
    assert move["after"]["anchor_source_frame"] == 25_641
    assert move["before"] == {"record_in": 700}


@pytest.mark.usefixtures("mock_dvr")
def test_the_next_rebuild_trims_and_moves_again_from_the_ledger(
        project_dir_2):
    live, staging = edited(), reel(STAGING)
    wanted = played_2(live)
    project = Project([Timeline(MASTER, {}), live, staging])
    promote_2(project, project_dir_2)

    again = project.adopt(reel(STAGING))
    doc = provenance_2(project_dir_2)
    doc["built_reels"] = [STAGING]
    (project_dir_2 / "pipeline_output" / "review" / "plan_provenance.json"
     ).write_text(json.dumps(doc), encoding="utf-8")
    promoted = promote_2(project, project_dir_2)

    assert promoted["promoted"] == [FINAL]
    assert played_2(again) == wanted


@pytest.mark.usefixtures("mock_dvr")
def test_a_trim_of_a_comp_bearing_clip_with_no_manifest_refuses_unwritten(
        project_dir_2):
    live, staging = edited(), reel(STAGING, comps=True)
    before = played_2(staging)
    project = Project([Timeline(MASTER, {}), live, staging])

    with pytest.raises(ReelBuildError) as refused:
        promote_2(project, project_dir_2)

    assert "recorded fusion manifest" in str(refused.value)
    assert played_2(staging) == before
    assert sorted(project.names()) == sorted([MASTER, FINAL, STAGING])


@pytest.mark.usefixtures("mock_dvr")
def test_a_trim_under_another_rows_item_refuses_unwritten(project_dir_2):
    live, staging = edited(), reel(STAGING)
    # The rebuild put a graphic across the end of the SpeakerTwo passage;
    # the editor's ripple never ran under one, so it is no replay.
    staging.add_item("video", 3, item(CARD, 500, 80, 0))
    staging.rows["V3"].sort(key=lambda placed: placed.GetStart())
    before = played_2(staging)
    project = Project([Timeline(MASTER, {}), live, staging])

    with pytest.raises(ReelBuildError) as refused:
        promote_2(project, project_dir_2)

    message = str(refused.value)
    assert "a rippled trim under another row's item is not carried" in message
    assert "'semantic-card.mov' at record 500..580" in message
    assert played_2(staging) == before


@pytest.mark.usefixtures("mock_dvr")
def test_a_trim_under_a_card_the_editor_held_replays_exactly(project_dir_2):
    """Reel 13's shape: the editor's ripple ran under a graphic and Resolve
    left it where it was. Staging is the timeline he cut, so the ripple
    replays - the graphic held, everything after it moved - and staging
    plays what his timeline plays."""
    live, staging = edited(), reel(STAGING)
    for timeline in (live, staging):
        timeline.add_item("video", 3, item(CARD, 500, 80, 0))
        timeline.rows["V3"].sort(key=lambda placed: placed.GetStart())
    wanted = played_2(live)
    project = Project([Timeline(MASTER, {}), live, staging])

    promote_2(project, project_dir_2)

    assert staging.GetName() == FINAL
    assert played_2(staging) == wanted


@pytest.mark.usefixtures("mock_dvr")
def test_a_comp_bearing_trim_reruns_the_comp_pass_on_its_kept_range(
        project_dir_2):
    """The pass gets the recorded manifest with the trimmed spec moved."""
    staging = reel(STAGING, comps=True)
    live = reel(FINAL, speakertwo=400, late_at=600, card_on=False, comps=True)
    wanted = played_2(live)
    project = Project([Timeline(MASTER, {}), live, staging])
    sources = [i.GetMediaPoolItem().GetClipProperty("File Path")
               for i in staging.rows["V1"]]
    manifest = {
        "fusion_effects": {"per_clip": {"speakertwo": {"zoom": 1.2}}},
        "tracks": {"V1": {"clips": [
            {"label": f"clip{n}", "source_file": path,
             "source_in": 10.0, "source_out": 10.0 + 437 / 30}
            if path.endswith("speakertwo.mov") else
            {"label": f"clip{n}", "source_file": path}
            for n, path in enumerate(sources)]}},
    }
    manifest["tracks"]["V1"]["clips"][1]["label"] = "speakertwo"
    scratch = project_dir_2 / "pipeline_output" / "scratch" / "reel_look"
    scratch.mkdir(parents=True)
    (scratch / "reel_07_number_one_on_google_invisible_to_ai_rebuild_staging"
     "_fusion_manifest.json").write_text(json.dumps(manifest),
                                         encoding="utf-8")
    passes = []

    def comp_pass(given, _folder, _project, timeline_name, **_kw):
        passes.append((given, timeline_name))
        for placed in project.GetCurrentTimeline().rows["V1"]:
            placed.comps = item(
                placed.GetMediaPoolItem(), placed.GetStart(),
                placed.GetDuration(), placed.GetLeftOffset(),
                comp_windows=[covering_window(
                    placed.GetDuration(), placed.GetLeftOffset(),
                    frames_of(placed.GetMediaPoolItem()))]).comps
        return True

    with patch("library.tools.reel_look.apply_comps", side_effect=comp_pass):
        promoted = promote_2(project, project_dir_2)

    assert promoted["promoted"] == [FINAL]
    assert played_2(staging) == wanted
    (given, timeline_name), = passes
    assert timeline_name == STAGING
    speakertwo = given["tracks"]["V1"]["clips"][1]
    assert speakertwo["source_in"] == pytest.approx(10.0)
    assert speakertwo["source_out"] == pytest.approx(10.0 + 400 / 30)


# --------------------------------------------------------------------------
# From test_first_contact_journaled_baseline.py
#
# First-contact edit preservation uses the best known Ren baseline.
#
# The incident history and preservation contract live in
# `docs/evidence/edit_preservation.md`.

FINAL_2 = "Reel 09 - your-website-is-only-20-percent"
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
        {"clips": [detail("SpeakerOne", 1, "LC4932.MXF", 0, 120,
                          source_in=34305, pan=-8.11)]},
        {"clips": [detail("Subtitles", 4, "sub_a.mov", 0, 40),
                   detail("Subtitles", 4, "sub_b.mov", 40, 120)]},
        {"clips": [detail("Semantic", 5, "mg.mov", 30, 60,
                          enabled=False)]},
    ]


def snapshot_2(tracks, name=FINAL_2, unique_id="live"):
    return {"timeline": {"name": name, "unique_id": unique_id,
                         "settings": {"timelineFrameRate": "23.976"},
                         "start_frame": 0, "end_frame": 120},
            "items": guard.snapshot_items(tracks), "markers": []}


def journal_a_touch(project_dir, tracks):
    undo_journal.write_entry(project_dir, {
        "format": undo_journal.JOURNAL_FORMAT, "id": JOURNAL,
        "final": FINAL_2, "reel": 9, "status": "applied",
        "before": {"tracks": tracks}, "after": {"tracks": tracks}})
    reel_versions.record(project_dir, FINAL_2, kind=reel_versions.KIND_TOUCH,
                         rows={}, journal=JOURNAL)


def rebuilt_staging():
    """What a rebuild stages: framing restored, captions re-split, the
    graphic placed enabled again - all Ren's own plan, none the editor's."""
    tracks = touched_tracks()
    tracks[0]["clips"][0]["transform"]["Pan"] = -32.445
    tracks[1]["clips"] = [detail("Subtitles", 4, "sub_c.mov", 0, 60),
                          detail("Subtitles", 4, "sub_d.mov", 60, 120)]
    tracks[2]["clips"][0]["enabled"] = True
    return snapshot_2(tracks, name=FINAL_2 + " (rebuild staging)",
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
        reel_versions.record(tmp_path, FINAL_2, kind=reel_versions.KIND_BUILD,
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
        str(tmp_path), FINAL_2, snapshot_2(live_tracks), rebuilt_staging())

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


def _touch_item(record_in, transform=None, enabled=True):
    return {
        "track_type": "video",
        "track_index": 1,
        "track_name": "Speakers",
        "name": "SpeakerOne",
        "source_identity": "file:/media/speakerone.mov",
        "source_in_frame": 100,
        "source_out_frame": 200,
        "record_in": record_in,
        "record_out": record_in + 100,
        "enabled": enabled,
        "transform": dict(transform or {"ZoomX": 1.0, "ZoomY": 1.0}),
    }


def _touch_snapshots(record_in=0, transform=None, enabled=True):
    item = _touch_item(record_in, transform, enabled)
    return {"items": [dict(item)]}


def test_a_touch_property_write_is_carried_onto_moved_staging():
    live = _touch_snapshots(0, {"ZoomX": 1.0, "ZoomY": 1.0})
    staged = _touch_snapshots(0, {"ZoomX": 1.25, "ZoomY": 1.25})
    applied = {"properties": [{
        "row": "V1", "record_frame": 0,
        "properties": {"ZoomX": 1.25, "ZoomY": 1.25}}]}

    (edit_x, edit_y) = carry.derive_touch_edits(
        FINAL, live, staged, applied, journal_id="touch-7",
        plan_version="plan-v1")
    assert edit_x["kind"] == "transform"
    assert edit_x["field"] == "transform.ZoomX"
    assert edit_x["after"] == 1.25
    assert edit_x["source"] == "ren_touch:touch-7"
    assert edit_x["author"] == "ren touch"

    # The rebuild moved every record frame; the source passage did not
    # move, so the write still maps - onto the moved item.
    moved = _touch_snapshots(50, {"ZoomX": 1.0, "ZoomY": 1.0})
    plan = carry.plan_application([edit_x, edit_y], moved, FINAL)
    assert {step["key"] for step in plan["set_transform"]} == {
        "ZoomX", "ZoomY"}


def test_a_touch_write_whose_clip_is_gone_refuses_naming_the_touch(
        project_dir):
    from library.tools import plan_provenance

    review = str(project_dir / "pipeline_output" / "review")
    live = _touch_snapshots(0, {"ZoomX": 1.0})
    staged = _touch_snapshots(0, {"ZoomX": 1.25})
    applied = {"properties": [{
        "row": "V1", "record_frame": 0, "properties": {"ZoomX": 1.25}}]}
    (edit,) = carry.derive_touch_edits(
        FINAL, live, staged, applied, journal_id="touch-7",
        plan_version="plan-v1")
    plan_provenance.record_carried_edits(review, FINAL, [edit])

    # The plan change dropped the touched passage entirely: no staged
    # item plays it, so the rebuild refuses instead of dropping it.
    gone = {"items": [_touch_item(0, {"ZoomX": 1.0})]}
    gone["items"][0]["source_identity"] = "file:/media/speakertwo.mov"
    with pytest.raises(carry.EditorEditCarryRefused,
                       match=r"Ren touch touch-7"):
        carry.plan_application(
            plan_provenance.carried_editor_edits(review, FINAL),
            gone, FINAL)


def test_a_touch_enabled_write_is_carried_onto_moved_staging():
    live = _touch_snapshots(0, enabled=True)
    staged = _touch_snapshots(0, enabled=False)
    applied = {"enabled": [{
        "row": "V1", "record_frame": 0, "enabled": False}]}
    (edit,) = carry.derive_touch_edits(
        FINAL, live, staged, applied, journal_id="touch-8")

    moved = _touch_snapshots(50, enabled=True)
    plan = carry.plan_application([edit], moved, FINAL)
    assert edit["kind"] == "enabled"
    assert plan["set_enabled"][0]["edit"]["after"] is False


def test_a_touch_write_that_changed_nothing_files_nothing():
    live = _touch_snapshots(0, {"ZoomX": 1.25})
    staged = _touch_snapshots(0, {"ZoomX": 1.25})
    applied = {"properties": [{
        "row": "V1", "record_frame": 0, "properties": {"ZoomX": 1.25}}]}
    assert carry.derive_touch_edits(
        FINAL, live, staged, applied, journal_id="touch-7") == []

    live_on = _touch_snapshots(enabled=True)
    staged_on = _touch_snapshots(enabled=True)
    assert carry.derive_touch_edits(
        FINAL, live_on, staged_on,
        {"enabled": [{"row": "V1", "record_frame": 0, "enabled": True}]},
        journal_id="touch-7") == []


# ── The carry declares the staging timeline ─────────────────────────

_EMPTY_PLAN = {"set_enabled": [], "set_transform": [], "delete": []}


@pytest.mark.usefixtures("mock_dvr")
def test_an_empty_carry_plan_applies_nothing_and_refuses_nothing():
    """The wired path still runs: nothing to write, nothing refused.

    Defect caught: a declaration that refuses its own carry turns
    every edit carry into a refusal, and editor edits stop reaching
    rebuilds.
    """
    from tests.resolve_double import make_project
    staging = FakeTimeline(STAGING)
    project = make_project("Podcast")
    assert carry.apply_plan(staging, project, dict(_EMPTY_PLAN), FINAL) == []


@pytest.mark.usefixtures("mock_dvr")
def test_carrying_waits_for_a_touchup_on_the_staging():
    """Carried edits and restructuring on one staging serialise: wait.

    Defect caught: with no declaration the carry edits items a
    touch-up's deletes are shifting - handles land on items that are
    gone. On origin/main the carry returns before the holder releases.
    """
    from library.tools.resource_leases import (
        LeaseDeclaration,
        ResourceRef,
        resource_lease,
    )
    from tests.resolve_double import make_project
    staging = FakeTimeline(STAGING)
    project = make_project("Podcast")
    touchup = LeaseDeclaration(
        write=frozenset({ResourceRef("timeline", STAGING)}),
        exclusive=frozenset({ResourceRef("timeline", STAGING)}),
    )
    released_at: list = []

    def restructure():
        with resource_lease(touchup, "touch up the staging",
                           heartbeat_interval=30.0):
            time.sleep(2.0)
        released_at.append(time.monotonic())

    holder = threading.Thread(target=restructure)
    holder.start()
    time.sleep(0.3)  # let the touch-up acquire first
    assert carry.apply_plan(staging, project, dict(_EMPTY_PLAN),
                            FINAL) == []
    returned_at = time.monotonic()
    holder.join(30.0)
    assert released_at, "the touch-up never released"
    assert returned_at >= released_at[0], \
        "the carry returned mid-touch-up"


# ── The composed funnel declares the staging timeline ────────────────

def _composed_staging():
    from tests.resolve_double import FakeProject, FakeTimeline
    staging = FakeTimeline(STAGING)
    project = FakeProject("Mock Project", [staging], current=staging)
    return project, staging


def _mock_compose(monkeypatch, calls):
    from types import SimpleNamespace

    import library.tools.composed_edit as ce

    def fake_apply(**kwargs):
        calls.append(kwargs.get("changes"))
        return SimpleNamespace(plan=[], verified={}, rederived=[])

    monkeypatch.setattr(ce, "apply_composed_edit", fake_apply)


def test_a_composed_edit_runs_through_its_lease(tmp_path, monkeypatch):
    """The wired funnel still runs: the inner edit executes, verified.

    Defect caught: a declaration that refuses its own funnel turns
    every split, trim and move into a refusal, and carried edits stop
    reaching staging.
    """
    from library.tools import editor_edit_carry as carry_mod
    from library.tools.resolve_lock import assume_sole_writer
    calls = []
    _mock_compose(monkeypatch, calls)
    project, staging = _composed_staging()
    with assume_sole_writer("test: fakes have no instance to contend for"):
        report = carry_mod._composed(
            str(tmp_path), project, staging, [], [], None,
            "splits", FINAL)
    assert calls == [[]]
    assert report["verified"] == {}


def test_a_composed_edit_waits_for_a_touchup_on_the_staging(
        tmp_path, monkeypatch):
    """Composed writes and restructuring on one staging serialise: wait.

    Defect caught: with no declaration the funnel replaces items while
    a touch-up restructures the same staging - the edit lands where
    the items were. On origin/main the funnel returns before the
    holder releases.
    """
    from library.tools import editor_edit_carry as carry_mod
    from library.tools.resolve_lock import assume_sole_writer
    from library.tools.resource_leases import (
        LeaseDeclaration,
        ResourceRef,
        resource_lease,
    )
    calls = []
    _mock_compose(monkeypatch, calls)
    project, staging = _composed_staging()
    touchup = LeaseDeclaration(
        write=frozenset({ResourceRef("timeline", STAGING)}),
        exclusive=frozenset({ResourceRef("timeline", STAGING)}),
    )
    released_at: list = []

    def restructure():
        with resource_lease(touchup, "touch up the staging",
                           heartbeat_interval=30.0):
            time.sleep(2.0)
        released_at.append(time.monotonic())

    holder = threading.Thread(target=restructure)
    holder.start()
    time.sleep(0.3)  # let the touch-up acquire first
    with assume_sole_writer("test: fakes have no instance to contend for"):
        carry_mod._composed(
            str(tmp_path), project, staging, [], [], None,
            "splits", FINAL)
    returned_at = time.monotonic()
    holder.join(30.0)
    assert released_at, "the touch-up never released"
    assert returned_at >= released_at[0], \
        "the funnel returned mid-touch-up"
    assert calls == [[]]


def _header(name, *, record_out=2183, tilt=1836.0):
    """A post-header PNG still as a read reports it (zero-length source)."""
    still = _item("Post Header", name, 39, 39, 0, record_out, track_index=7)
    return {**still, "transform": {"Tilt": tilt}, "left_offset": 86313}


def test_a_still_transform_lands_on_the_rerendered_still():
    """Reel 23, 2026-10-05: the editor's Tilt on the post header named
    `..._7b7411c39f_tight.png`, a still reading source 39..39. The rebuild
    re-rendered it as `..._60bd7f7ded_tight.png`; the edit had no single
    item to land on and the promotion refused. It lands on the one still
    on the same row over the same record span."""
    record = _change_record([_header("post_header_7b7411c39f_tight.png")],
                            [_header("post_header_7b7411c39f_tight.png",
                                     tilt=459.0)])
    edits, uncarried = carry.derive_edits(record)
    assert uncarried == []
    (tilt,) = edits
    staged = _header("post_header_60bd7f7ded_tight.png")
    plan = carry.plan_application([tilt], {"items": [staged]}, FINAL)
    assert [step["item"]["name"] for step in plan["set_transform"]] == [
        "post_header_60bd7f7ded_tight.png"]
    carry.verify_carried_edits(
        {"edits": [tilt]},
        {"items": [{**staged, "transform": {"Tilt": 459.0}}]}, FINAL)


def test_a_still_transform_refuses_without_one_still_over_its_span():
    record = _change_record([_header("post_header_7b7411c39f_tight.png")],
                            [_header("post_header_7b7411c39f_tight.png",
                                     tilt=459.0)])
    (tilt,), _ = carry.derive_edits(record)
    shorter = _header("post_header_60bd7f7ded_tight.png", record_out=2100)
    for items in ([shorter],
                  [_header("a.png"), _header("b.png")]):
        with pytest.raises(carry.EditorEditCarryRefused,
                           match="no single item to land on"):
            carry.plan_application([tilt], {"items": items}, FINAL)


def test_the_guard_finds_a_rerendered_still_by_row_and_span():
    old = _header("post_header_7b7411c39f_tight.png")
    record = _change_record([old], [{**old, "transform": {"Tilt": 459.0}}])
    (change,) = record["changes"]
    rebuilt = _header("post_header_60bd7f7ded_tight.png", tilt=459.0)
    assert guard._change_is_carried(change, {"items": [rebuilt]})
    assert not guard._change_is_carried(
        change, {"items": [{**rebuilt, "transform": {"Tilt": 1836.0}}]})


def test_replacement_refuses_an_observed_grade_lost_on_staging(tmp_path):
    item = {
        "name": "closing shot", "source_identity": "media:closing",
        "source_in_frame": 100, "source_out_frame": 200,
        "color": {"cdl": {"saturation": 1.2}, "luts": []},
        "fusion": {"comp_count": 0, "comp_names": [],
                   "media_windows": [], "mattes": None},
        "composite": {},
    }
    detection = {"first_contact": False, "detected": [], "pending": []}
    with pytest.raises(guard.ReplaceGuardRefused, match="color"):
        guard.protect_editor_changes(
            str(tmp_path), FINAL, {"items": [item]}, {"items": [item]},
            {"items": [{**item, "color": {"cdl": {}, "luts": []}}]},
            detection=detection)


def test_replacement_report_never_claims_full_native_preservation(tmp_path):
    detection = {"first_contact": False, "detected": [], "pending": []}
    result = guard.protect_editor_changes(
        str(tmp_path), FINAL, {"items": []}, {"items": []}, {"items": []},
        detection=detection)
    assert result["preservation"]["full_preservation"] is False
    assert set(result["preservation"]["contract"].values()) <= {
        "supported", "unsupported", "partially_observable"}
