"""The editor's timeline edits are carried through a rebuild, or refused.

Reel 7 (2026-09-29): the live cut omitted Craig source 25,263-25,374 and
Akshita source 60,745-60,979 and held a disabled Semantic graphic; the
rebuild restored both passages and re-enabled the graphic, and the
promotion overwrote the edit. Step one made that promotion REFUSE. These
tests pin step two: the same shape is CARRIED - the staging timeline
loses the two passages (the first with its gap closed, as the editor
closed it) and the graphic is switched off, judged on a re-read in
source ranges - and the promoted timeline matches the edited one on
those passages. The next rebuild carries the same edits again from the
ledger with no fresh editor change on record.

Trims and moves are carried in `tests/test_editor_edit_carry_composed.py`.
"""

import json
from contextlib import ExitStack, nullcontext
from unittest.mock import patch

import pytest

from library.tools import editor_edit_carry as carry
from library.tools import reel_replace_guard as guard
from library.tools.reel_build import ReelBuildError, promote_staged_reels
from tests.promotion_test_helpers import no_a_roll_track_plans
from tests.resolve_double import (
    FakeProject,
    FakeTimeline,
    TimelineItemSpec,
)

FINAL = "Reel 07 - number-one-on-google-invisible-to-ai"
STAGING = FINAL + " (rebuild staging)"
MASTER = "Podcast - Synced"


@pytest.fixture(autouse=True)
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


def snapshot(timeline):
    """The full read `reel_replace_guard.full_timeline_snapshot` returns."""
    items = []
    for track_type in ("video", "audio"):
        for index in range(1, timeline.GetTrackCount(track_type) + 1):
            row = timeline.GetTrackName(track_type, index)
            for clip in timeline.GetItemListInTrack(track_type, index):
                items.append(
                    {
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
                )
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
    for item in snapshot(timeline)["items"]:
        rows.setdefault((item["track_type"], item["track_name"]), []).append(
            (
                item["source_in_frame"],
                item["source_out_frame"],
                item["record_in"],
                item["enabled"],
            )
        )
    return {row: sorted(items) for row, items in rows.items()}


def promote(project, project_dir, staging):
    with ExitStack() as stack:
        stack.enter_context(
            patch.object(
                guard,
                "full_timeline_snapshot",
                side_effect=lambda timeline, _project, _folder=None: snapshot(timeline),
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
