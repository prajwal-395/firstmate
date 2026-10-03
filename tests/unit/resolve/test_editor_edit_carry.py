"""The editor's timeline edits are carried through a rebuild, or refused.

History: docs/evidence/resolve_test_history.md#test_editor_edit_carry.
"""
import copy
import json
from contextlib import ExitStack, nullcontext
from unittest.mock import patch

import pytest

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
                    _clip("Akshita", 22_232, 22_348, 0),
                    _clip("Craig", 22_257, 22_694, 116),
                    _clip("Akshita", 25_557, 25_751, 553),
                ],
            ),
            ("Semantic", [_clip("semantic-card", 0, 96, 47, enabled=False)]),
        ],
        audio=[
            (
                "Dialogue",
                [
                    _clip("Akshita", 22_232, 22_348, 0),
                    _clip("Craig", 22_257, 22_694, 116),
                    _clip("Akshita", 25_557, 25_751, 553),
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
                    _clip("Akshita", 22_232, 22_348, 0),
                    _clip("Craig", 22_257, 22_694, 116),
                    _clip("Craig", 25_263, 25_374, 553),
                    _clip("Akshita", 25_557, 25_751, 664),
                    _clip("Akshita", 60_745, 60_979, 858),
                ],
            ),
            ("Semantic", [_clip("semantic-card", 0, 96, 47, enabled=True)]),
        ],
        audio=[
            (
                "Dialogue",
                [
                    _clip("Akshita", 22_232, 22_348, 0),
                    _clip("Craig", 22_257, 22_694, 116),
                    _clip("Craig", 25_263, 25_374, 553),
                    _clip("Akshita", 25_557, 25_751, 664),
                    _clip("Akshita", 60_745, 60_979, 858),
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
def test_a_rippled_trim_under_a_graphic_refuses_with_the_source_ranges(project_dir):
    live, staging = edited_reel_7(), rebuilt_reel_7()
    # The editor also shortened the first Akshita passage by 16 frames
    # and closed the gap - under the Semantic card at 47..143.
    rows = [live.GetItemListInTrack("video", 1)] + [
        live.GetItemListInTrack("audio", index)
        for index in range(1, live.GetTrackCount("audio") + 1)
    ]
    for clips in rows:
        clips[0]._duration = 22_332 - clips[0]._left_offset
        clips[0]._source_end_frame = clips[0]._left_offset + clips[0]._duration
        for later in clips[1:]:
            later._start -= 16
    seed_provenance(project_dir)
    resolve = FakeProject([FakeTimeline(MASTER), live, staging])

    with pytest.raises(ReelBuildError) as refused:
        promote(resolve, project_dir, STAGING)

    message = str(refused.value)
    assert "a rippled trim under another row's item is not carried" in message
    assert "'semantic-card' at record 47..143" in message
    assert "--accept-editor-changes" in message
    assert sorted(resolve.names()) == sorted([MASTER, FINAL, STAGING])
    assert resolve.deleted == []
    assert getattr(staging, "deletes", 0) == 0


@pytest.mark.usefixtures("mock_dvr")
def test_a_cut_staging_plays_only_part_of_refuses_by_name(project_dir):
    edit = {
        "id": "e1",
        "kind": "cut",
        "row": "video:Speakers",
        "name": "Craig",
        "source_identity": "file:/media/Craig.mov",
        "source_in_frame": 25_263,
        "source_out_frame": 25_374,
        "ripple": True,
    }
    staged = {
        "items": [
            {
                "track_type": "video",
                "track_name": "Speakers",
                "name": "Craig",
                "source_identity": "file:/media/Craig.mov",
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
        "name": "Craig",
        "source_identity": "file:/media/Craig.mov",
        "source_in_frame": 25_263,
        "source_out_frame": 25_374,
    }
    staged_after = {
        "items": [
            {
                "track_type": "video",
                "track_name": "Speakers",
                "name": "Craig",
                "source_identity": "file:/media/Craig.mov",
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
        "name": "Craig",
        "source_identity": "file:/media/Craig.mov",
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
                    "name": "Craig",
                    "source_identity": "file:/media/Craig.mov",
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
            ("Akshita", 22_232, 22_347, 0, 116),
            ("Craig", 22_257, 22_693, 116, 553),
            ("Craig", 25_263, 25_374, 553, 665),
            ("Akshita", 25_557, 25_750, 665, 859),
            ("Akshita", 60_745, 60_978, 859, 1_093),
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
            ("Akshita", 22_232, 22_347, 0, 116),
            ("Craig", 22_257, 22_693, 116, 553),
            ("Akshita", 25_557, 25_750, 553, 747),
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
        ("audio:Dialogue", "Akshita", True),
        ("audio:Dialogue", "Craig", True),
        ("video:Speakers", "Akshita", True),
        ("video:Speakers", "Craig", True),
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
# editor trimmed 37 frames off the tail of the Craig passage and closed
# the gap (picture and sound), and dragged a Semantic graphic to sit over
# a different moment of the Akshita shot. A rebuild restores the full
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


AKSHITA = pool_clip("/media/akshita.mov", frames=100_000)
CRAIG = pool_clip("/media/craig.mov", frames=100_000)
CARD = pool_clip("/media/semantic-card.mov", frames=200)
LATE = pool_clip("/media/semantic-late.mov", frames=200)


def reel(name, *, craig=437, late_at=700, card_on=True, comps=False):
    """V1 picture with A1 sound, and two Semantic graphics on V3."""
    shift = 437 - craig
    passages = [(AKSHITA, 22_232, 116, 0), (CRAIG, 22_257, craig, 116),
                (AKSHITA, 25_557, 194, 553 - shift),
                (AKSHITA, 60_745, 234, 747 - shift)]

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
    """The editor's cut: Craig 37 frames shorter with the gap closed,
    the card off, and the late graphic dragged earlier (600, not the
    rippled 663) over Akshita source frame 25,641."""
    return reel(FINAL, craig=400, late_at=600, card_on=False)


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
    assert trim["wording"].startswith("Trim 'craig.mov' source 22,257..")
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
    # A graphic straddling the end of the Craig passage on both sides.
    for timeline, start in ((live, 500), (staging, 500)):
        timeline.add_item("video", 3, item(CARD, start, 80, 0))
        timeline.rows["V3"].sort(key=lambda placed: placed.GetStart())
    before = played_2(staging)
    project = Project([Timeline(MASTER, {}), live, staging])

    with pytest.raises(ReelBuildError) as refused:
        promote_2(project, project_dir_2)

    message = str(refused.value)
    assert "a rippled trim under another row's item is not carried" in message
    assert "'semantic-card.mov' at record 500..580" in message
    assert played_2(staging) == before


@pytest.mark.usefixtures("mock_dvr")
def test_a_comp_bearing_trim_reruns_the_comp_pass_on_its_kept_range(
        project_dir_2):
    """The pass gets the recorded manifest with the trimmed spec moved."""
    staging = reel(STAGING, comps=True)
    live = reel(FINAL, craig=400, late_at=600, card_on=False, comps=True)
    wanted = played_2(live)
    project = Project([Timeline(MASTER, {}), live, staging])
    sources = [i.GetMediaPoolItem().GetClipProperty("File Path")
               for i in staging.rows["V1"]]
    manifest = {
        "fusion_effects": {"per_clip": {"craig": {"zoom": 1.2}}},
        "tracks": {"V1": {"clips": [
            {"label": f"clip{n}", "source_file": path,
             "source_in": 10.0, "source_out": 10.0 + 437 / 30}
            if path.endswith("craig.mov") else
            {"label": f"clip{n}", "source_file": path}
            for n, path in enumerate(sources)]}},
    }
    manifest["tracks"]["V1"]["clips"][1]["label"] = "craig"
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
    craig = given["tracks"]["V1"]["clips"][1]
    assert craig["source_in"] == pytest.approx(10.0)
    assert craig["source_out"] == pytest.approx(10.0 + 400 / 30)


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
        {"clips": [detail("Akshita", 1, "LC4932.MXF", 0, 120,
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
        "name": "Akshita",
        "source_identity": "file:/media/akshita.mov",
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
    gone["items"][0]["source_identity"] = "file:/media/craig.mov"
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
