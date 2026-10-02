"""A replace is a diff, and the diff refuses (issue #925).

`promote_staged_reels` is the ONE place a captain-visible reel timeline
is replaced, and it renamed without ever reading what it retired. Two
rebuilds proved the shape in one day, reconstructed here against fake
timelines whose rows really hold items:

1. the cutaway: a `--only-reel` rebuild over a cutaway-bearing timeline
   must refuse - V1 goes 3 items to 2 and the 24-frame Akshita cover at
   record frame 574 is named as missing;
2. the semantic visuals: a build whose overlay renders failed leaves
   the V5 'Semantic' row absent, and the promote must refuse naming
   the whole feature class gone.

Plus the two halves that keep the guard from becoming a nuisance:

3. a DECLARED reduction passes silently and names what it declared;
4. growth (a cutaway ADDED) and a shortened cut (same items, fewer
   frames) pass with nothing declared.

And the fail-closed half: a retiring timeline that cannot be read
refuses rather than passing. Every refusal asserts NOTHING was
renamed - the check runs before the first rename, so the approved
timeline is still in the project afterwards.

A guard nobody has watched fire is not a guard: cases 1, 2 and the
unreadable half assert the raise, not just the report.
"""
from __future__ import annotations
import json
from unittest.mock import patch
import pytest
from library.tools import reel_replace_guard as guard
from library.tools.reel_build import (
    ReelBuildError,
    promote_staged_reels,
)
from tests.promotion_test_helpers import no_a_roll_track_plans
from tests.resolve_double import (
    FakeProject,
    FakeTimeline,
)
from tests.resolve_double import (
    TimelineItemSpec as FakeItem,
)
from tests.promotion_test_helpers import (
    install_fake_timeline_snapshots,
)
from library.tools import staging_holds as holds
from library.tools.execution import remove_proof as proof_ex
from library.tools.proof_cleanup import (
    PROTECTED_TIMELINES,
    ProofRemovalRefused,
    declined_held_scratch_timelines,
    discover_scratch_timelines,
    plan_proof_removal,
)
from library.tools.resolve_organization import Artefact
from tests.resolve_double import timeline_item
import sys
from library.tools.versions import store as bvc
from library.tools.plan_provenance import (
    built_from_snapshot,
    is_snapshot_superseded,
)
from contextlib import ExitStack
from tests.resolve_double import (
    FakeResolve,
    TimelineItemSpec,
)
from library.tools import reel_signoff as signoff
import dataclasses
import os
from library.tools.project_layout import Area
from library.tools.reel_placed_assets import (
    PlacedAssetInScratch,
    assert_placeable,
    is_under_scratch,
    promote_cards,
    promote_frame_overlays,
    promote_to_durable,
)


FINAL = "Reel 09 - your-website-is-only-20-percent (final)"
MASTER = "Podcast - Synced"


@pytest.fixture
def mock_dvr(stub_resolve_script):
    # Stubbed through the shared fixture: `patch.dict` on
    # `sys.modules` restores the WHOLE dict and so evicts every
    # module first imported inside it (tests/conftest.py).
    yield


@pytest.fixture
def project_dir(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    review = root / "pipeline_output" / "review"
    review.mkdir(parents=True)
    return root


def _full_snapshot(timeline):
    items = []
    try:
        counts = {kind: timeline.GetTrackCount(kind) for kind in ("video", "audio")}
    except RuntimeError:
        # An unreadable timeline snapshots as nothing held: production's
        # own live read is what refuses, not this test-side record.
        counts = {}
    for kind, count in counts.items():
        for index in range(1, count + 1):
            track_name = timeline.GetTrackName(kind, index)
            for clip in timeline.GetItemListInTrack(kind, index):
                items.append(
                    {
                        "track_type": kind,
                        "track_name": track_name,
                        "name": clip.GetName(),
                        "source_identity": f"fake:{clip.GetName()}",
                        "source_in_frame": clip.GetStart(),
                        "source_out_frame": clip.GetEnd(),
                        "record_in": clip.GetStart(),
                        "record_out": clip.GetEnd(),
                        "duration": clip.GetDuration(),
                        "enabled": clip.GetClipEnabled(),
                        "transform": {},
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
            "start_frame": timeline.GetStartFrame(),
            "end_frame": max([item["record_out"] for item in items] or [0]),
        },
        "items": items,
        "markers": [],
    }


def _promote(
    project,
    project_dir,
    staged_to_final,
    allow_drops=None,
    full_snapshots=None,
    baseline_snapshot=True,
    timeline_inventory_before=None,
    built_reels=None,
):
    import json
    from contextlib import ExitStack

    # The baselines the gate graded, filed under the staging names -
    # which is what a real staged build leaves behind. Only the
    # provenance sidecar is strict about existing.
    snapshots = full_snapshots or {
        id(timeline): _full_snapshot(timeline) for timeline in project.timelines
    }
    provenance = {
        "built_reels": sorted(
            staged_to_final.values() if built_reels is None else built_reels
        )
    }
    if baseline_snapshot:
        final = next(iter(staged_to_final))
        live = next(t for t in project.timelines if t.GetName() == final)
        provenance["ren_timeline_snapshots"] = {
            final: {"snapshot": snapshots[id(live)]}
        }
    (project_dir / "pipeline_output" / "review" / "plan_provenance.json").write_text(
        json.dumps(provenance), encoding="utf-8"
    )
    with ExitStack() as stack:
        stack.enter_context(
            patch.object(
                guard,
                "full_timeline_snapshot",
                side_effect=lambda timeline, _project, _folder=None: snapshots[
                    id(timeline)
                ],
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
        return promote_staged_reels(
            str(project_dir),
            "Mock Project",
            MASTER,
            staged_to_final,
            organise=False,
            allow_drops=allow_drops,
            track_plans=no_a_roll_track_plans(staged_to_final),
            timeline_inventory_before=timeline_inventory_before,
        )


def _cutaway_timelines():
    """The 12:33 rebuild: plan-faithful, cutaway gone, hole closed."""
    retired = FakeTimeline(
        FINAL,
        video=[
            (
                "Akshita",
                [
                    FakeItem("Craig A", 0, 55),
                    FakeItem("LC4932 cover", 574, 598),
                    FakeItem("Craig B", 598, 657),
                ],
            ),
            ("Craig", [FakeItem("Craig wide", 0, 131)]),
            ("Subtitles", [FakeItem("card 1", 0, 60), FakeItem("card 2", 60, 131)]),
        ],
    )
    staging = FakeTimeline(
        FINAL + " (rebuild staging)",
        video=[
            ("Akshita", [FakeItem("Craig A", 0, 67), FakeItem("Craig B", 67, 138)]),
            ("Craig", [FakeItem("Craig wide", 0, 131)]),
            ("Subtitles", [FakeItem("card 1", 0, 60), FakeItem("card 2", 60, 131)]),
        ],
    )
    return retired, staging


def _semantic_timelines():
    """The node_modules-less build: all four semantic visuals failed to
    render, so the whole V5 row is absent from the staging."""
    visual = [FakeItem(f"semantic {n}", n * 100, n * 100 + 40) for n in range(4)]
    retired = FakeTimeline(
        FINAL,
        video=[
            ("Akshita", [FakeItem("Akshita A", 0, 131)]),
            ("Craig", [FakeItem("Craig wide", 0, 131)]),
            ("Subtitles", [FakeItem("card 1", 0, 131)]),
            ("Transitions", [FakeItem("flash", 60, 66)]),
            ("Semantic", visual),
        ],
    )
    staging = FakeTimeline(
        FINAL + " (rebuild staging)",
        video=[
            ("Akshita", [FakeItem("Akshita A", 0, 131)]),
            ("Craig", [FakeItem("Craig wide", 0, 131)]),
            ("Subtitles", [FakeItem("card 1", 0, 131)]),
            ("Transitions", [FakeItem("flash", 60, 66)]),
        ],
    )
    return retired, staging


def _gains_frames_loses_cover():
    """Frames alone cannot catch this: the cover is gone and the
    surviving clip grew past the old row total. Names are the
    load-bearing half."""
    retired = FakeTimeline(
        FINAL,
        video=[
            (
                "Akshita",
                [FakeItem("Craig A", 0, 100), FakeItem("LC4932 cover", 100, 124)],
            ),
        ],
    )
    staging = FakeTimeline(
        FINAL + " (rebuild staging)",
        video=[("Akshita", [FakeItem("Craig A", 0, 140)])],
    )
    return retired, staging


#: Each undeclared-loss shape, and what its refusal must name.
LOSS_SHAPES = [
    # Drop 1: V1 3 items -> 2, the 24-frame cover at rec 574 named.
    (
        _cutaway_timelines,
        ["video:Akshita", "3 item(s) -> 2", "LC4932 cover", "574..598",
         "--allow-drop 'video:Akshita'"],
    ),
    # Drop 2: the V5 row exists retired and not at all incoming.
    (
        _semantic_timelines,
        ["video:Semantic", "row absent", "4 item(s)",
         "--allow-drop 'video:Semantic'"],
    ),
    # A loss that gains frames still refuses.
    (_gains_frames_loses_cover, ["video:Akshita", "2 item(s) -> 1", "LC4932 cover"]),
]


@pytest.mark.usefixtures("mock_dvr")
def test_an_undeclared_loss_refuses_before_any_rename(project_dir):
    for shape, expected in LOSS_SHAPES:
        retired, staging = shape()
        resolve = FakeProject([FakeTimeline(MASTER), retired, staging])

        with pytest.raises(ReelBuildError) as refused:
            _promote(resolve, project_dir, {FINAL: staging.GetName()})

        message = str(refused.value)
        for needle in expected:
            assert needle in message, (shape.__name__, needle)
        # Nothing was renamed and nothing deleted: the check runs before
        # the first rename, so the approved timeline is still there.
        assert sorted(resolve.names()) == sorted(
            [MASTER, FINAL, staging.GetName()])
        assert resolve.deleted == []


@pytest.mark.usefixtures("mock_dvr")
def test_snapshot_and_replace_diff_report_enabled_state_change():
    retired = FakeTimeline(
        FINAL,
        video=[("Semantic", [FakeItem("semantic-card", 120, 168, enabled=False)])],
    )
    incoming = FakeTimeline(
        FINAL + " (rebuild staging)",
        video=[("Semantic", [FakeItem("semantic-card", 120, 168, enabled=True)])],
    )

    old_rows = guard.snapshot_timeline(retired, FINAL)
    new_rows = guard.snapshot_timeline(incoming, incoming.GetName(), side="staged")
    semantic = old_rows["video:Semantic"]

    assert semantic["items"][0]["enabled"] is False
    report = guard.check_replacement(FINAL, incoming.GetName(), old_rows, new_rows)
    assert report["rows"][0]["enabled_changes"] == [
        {
            "name": "semantic-card",
            "start": 120,
            "end": 168,
            "retired_enabled": False,
            "incoming_enabled": True,
        }
    ]
    # A legacy snapshot that never recorded `enabled` is UNKNOWN, not a
    # change.
    legacy = {
        "video:Semantic": {
            "media_type": "video",
            "index": 1,
            "name": "Semantic",
            "count": 1,
            "frames": 48,
            "items": [
                {"name": "semantic-card", "start": 120, "end": 168, "duration": 48}
            ],
        }
    }
    current = {
        "video:Semantic": {
            "media_type": "video",
            "index": 1,
            "name": "Semantic",
            "count": 1,
            "frames": 48,
            "items": [
                {
                    "name": "semantic-card",
                    "start": 120,
                    "end": 168,
                    "duration": 48,
                    "enabled": False,
                }
            ],
        }
    }

    changes = guard.diff_rows(legacy, current)[0]["enabled_changes"]

    assert changes == []


@pytest.mark.usefixtures("mock_dvr")
def test_full_snapshot_diff_detects_replaced_timeline_identity():
    before = {
        "timeline": {"name": FINAL, "unique_id": "ren-id", "settings": {}},
        "items": [],
        "markers": [],
    }
    live = {
        "timeline": {"name": FINAL, "unique_id": "editor-id", "settings": {}},
        "items": [],
        "markers": [],
    }
    staged = {
        "timeline": {"name": FINAL, "unique_id": "staging-id", "settings": {}},
        "items": [],
        "markers": [],
    }

    changes = guard.snapshot_diff(before, live)

    assert changes == [
        {
            "kind": "timeline_identity",
            "field": "unique_id",
            "before": "ren-id",
            "after": "editor-id",
        }
    ]
    assert not guard._change_is_carried(changes[0], staged)


@pytest.mark.usefixtures("mock_dvr")
def test_a_marker_change_is_carried_only_in_its_own_direction():
    before = {
        "source": "timeline_marker",
        "frame": 100,
        "frame_in_timeline_space": 100,
        "color": "Blue",
        "name": "Captain",
        "note": "keep this",
        "duration_frames": 1,
        "custom_data": {},
        "custom_data_raw": "",
    }
    carried = {**before, "frame": 85, "frame_in_timeline_space": 85}
    change = {
        "kind": "marker_added",
        "identity": guard._marker_key(before),
        "before": None,
        "after": before,
    }

    assert guard._change_is_carried(change, {"markers": [carried]})

    # A REMOVED marker is not carried by a relocated copy of it.
    removed = {
        "source": "timeline_marker",
        "frame": 100,
        "frame_in_timeline_space": 100,
        "color": "Blue",
        "name": "Captain",
        "note": "remove this",
        "duration_frames": 1,
        "custom_data": {},
        "custom_data_raw": "",
    }
    relocated = {**removed, "frame": 80, "frame_in_timeline_space": 80}
    change = {
        "kind": "marker_removed",
        "identity": guard._marker_key(removed),
        "before": removed,
        "after": None,
    }

    assert not guard._change_is_carried(change, {"markers": [relocated]})


@pytest.mark.usefixtures("mock_dvr")
def test_full_snapshot_excludes_ephemeral_and_non_timeline_marker_data(monkeypatch):
    from contextlib import nullcontext
    from dataclasses import replace

    from library.tools.marker_feedback import MarkerNote

    class Timeline:
        def GetName(self):
            return FINAL

        def GetUniqueId(self):
            return "timeline-id"

        def GetSetting(self, key=None):
            settings = {
                "timelineFrameRate": "23.976",
                "timelineResolutionWidth": "1080",
                "timelineResolutionHeight": "1920",
            }
            return settings if key is None else settings.get(key, "")

        def GetStartFrame(self):
            return 0

        def GetEndFrame(self):
            return 100

    note = MarkerNote(
        source="timeline_marker",
        name="Captain",
        note="keep this",
        text="Captain\n\nkeep this",
        frame=42,
        timecode="00:00:01:18",
        frame_in_timeline_space=42,
        color="Blue",
        duration_frames=3,
        custom_data={"id": "m-1"},
        custom_data_raw='{"id":"m-1"}',
        clips=[{"name": "context-only"}],
        read_at="first read",
    )
    clip_note = replace(note, source="clip_marker", read_at="first read")
    reads = iter(
        (
            [note, clip_note],
            [
                replace(note, read_at="later read"),
                replace(clip_note, read_at="later read"),
            ],
        )
    )
    monkeypatch.setattr(
        "library.tools.resolve_lock.cursor_excursion",
        lambda *_args, **_kwargs: nullcontext(),
    )
    monkeypatch.setattr(
        "library.tools.reel_read.read_tracks", lambda *_args, **_kwargs: []
    )
    monkeypatch.setattr(
        "library.tools.marker_feedback.read_notes",
        lambda *_args, **_kwargs: next(reads),
    )

    first = guard.full_timeline_snapshot(Timeline(), object())
    second = guard.full_timeline_snapshot(Timeline(), object())

    assert first == second
    assert first["markers"] == [
        {
            "source": "timeline_marker",
            "frame": 42,
            "frame_in_timeline_space": 42,
            "color": "Blue",
            "name": "Captain",
            "note": "keep this",
            "duration_frames": 3,
            "custom_data": {"id": "m-1"},
            "custom_data_raw": '{"id":"m-1"}',
        }
    ]


@pytest.mark.usefixtures("mock_dvr")
def test_first_contact_carries_a_manual_marker_before_comparing(
    project_dir, monkeypatch
):
    marker = {
        "frame": 40,
        "color": "Blue",
        "name": "Captain",
        "note": "keep this",
        "duration": 1,
        "custom_data": "",
        "anchor": ("/media/craig.mov", 400),
    }
    live_marker = {
        "source": "timeline_marker",
        "frame": 40,
        "frame_in_timeline_space": 40,
        "color": "Blue",
        "name": "Captain",
        "note": "keep this",
        "duration_frames": 1,
        "custom_data": {},
        "custom_data_raw": "",
    }
    staged_marker = {**live_marker, "frame": 35, "frame_in_timeline_space": 35}
    retired = FakeTimeline(FINAL)
    staging = FakeTimeline(FINAL + " (rebuild staging)")
    live_snapshot = {
        "timeline": {
            "name": FINAL,
            "unique_id": "live",
            "settings": {"timelineFrameRate": 23.976},
            "start_frame": 0,
            "end_frame": 100,
        },
        "items": [],
        "markers": [live_marker],
    }
    staging_snapshot = {
        "timeline": {
            "name": staging.GetName(),
            "unique_id": "staging",
            "settings": {"timelineFrameRate": 23.976},
            "start_frame": 0,
            "end_frame": 100,
        },
        "items": [],
        "markers": [],
    }
    snapshots = {id(retired): live_snapshot, id(staging): staging_snapshot}

    def place(timeline, carried):
        assert carried == [{**marker, "to_frame": 35, "pairing": "note"}]
        snapshots[id(timeline)] = {**staging_snapshot, "markers": [staged_marker]}
        return []

    monkeypatch.setattr(
        "library.tools.marker_carry.read_markers", lambda *_args: [marker]
    )
    monkeypatch.setattr(
        "library.tools.marker_carry.plan_carry",
        lambda *_args: ([{**marker, "to_frame": 35, "pairing": "note"}], []),
    )
    monkeypatch.setattr("library.tools.marker_carry.place", place)
    monkeypatch.setattr(
        "library.tools.marker_carry.read_clip_markers", lambda *_args: []
    )
    monkeypatch.setattr(
        "library.tools.marker_gate.verify_promotion", lambda *_args, **_kwargs: None
    )

    resolve = FakeProject([FakeTimeline(MASTER), retired, staging])
    promoted = _promote(
        resolve,
        project_dir,
        {FINAL: staging.GetName()},
        full_snapshots=snapshots,
        baseline_snapshot=False,
    )

    assert promoted["promoted"] == [FINAL]
    provenance = json.loads(
        (project_dir / "pipeline_output" / "review" / "plan_provenance.json").read_text(
            encoding="utf-8"
        )
    )
    record = provenance["unattributed_editor_changes"][FINAL][0]
    assert record["status"] == "carried"


@pytest.mark.usefixtures("mock_dvr")
def test_promotion_refuses_a_target_created_during_the_build(project_dir):
    retired, staging = _semantic_timelines()
    resolve = FakeProject([FakeTimeline(MASTER), retired, staging])

    with pytest.raises(ReelBuildError, match="not in the project's inventory"):
        _promote(
            resolve,
            project_dir,
            {FINAL: staging.GetName()},
            timeline_inventory_before=[
                {"name": MASTER, "unique_id": MASTER, "settings": {}}
            ],
        )

    assert sorted(resolve.names()) == sorted([MASTER, FINAL, staging.GetName()])
    assert resolve.deleted == []


@pytest.mark.usefixtures("mock_dvr")
def test_live_snapshot_refuses_unreadable_enabled_state():
    timeline = FakeTimeline(
        FINAL, video=[("Semantic", [FakeItem("semantic-card", 120, 168, enabled=None)])]
    )

    with pytest.raises(guard.ReplaceGuardUnreadable, match="enabled state"):
        guard.snapshot_timeline(timeline, FINAL)


@pytest.mark.usefixtures("mock_dvr")
def test_declared_reduction_passes_and_names_what_it_declared(project_dir):
    """The intended change: silent on stdout, named in the record."""
    declaration = ["video:Semantic"]
    retired, staging = _semantic_timelines()
    resolve = FakeProject([FakeTimeline(MASTER), retired, staging])

    promoted = _promote(
        resolve,
        project_dir,
        {FINAL: staging.GetName()},
        allow_drops={FINAL: declaration},
    )

    assert promoted["promoted"] == [FINAL]
    report = promoted["replace_reports"][FINAL]
    assert report["refused"] is False
    assert report["allowed"] == ["video:Semantic"]
    # The replaced timeline is DELETED by default: one timeline per
    # reel, nothing archived (`library/tools/reel_retirement.py`).
    assert sorted(resolve.names()) == sorted([MASTER, FINAL])
    assert resolve.deleted == [f"{FINAL} (pre-rebuild backup)"]


@pytest.mark.usefixtures("mock_dvr")
def test_unreadable_retiring_timeline_refuses(project_dir):
    """Fail closed: what cannot be read cannot be judged, so no promote."""
    retired = FakeTimeline(FINAL)
    retired.raise_on_methods["GetTrackCount"] = RuntimeError("Resolve is busy")
    staging = FakeTimeline(FINAL + " (rebuild staging)")
    resolve = FakeProject([FakeTimeline(MASTER), retired, staging])

    with pytest.raises(ReelBuildError, match="could not be read"):
        _promote(resolve, project_dir, {FINAL: staging.GetName()})

    assert sorted(resolve.names()) == sorted([MASTER, FINAL, staging.GetName()])
    assert resolve.deleted == []


def _join_timelines():
    """The lc-0004 shape: keep insistence withdrew a take cut, so two
    adjacent Craig placements became one continuous one - 2 items to 1
    over MORE frames (the restored seconds are back in)."""
    retired = FakeTimeline(
        FINAL,
        video=[
            ("Craig", [FakeItem("Craig", 0, 100), FakeItem("Craig", 100, 190)]),
            ("Subtitles", [FakeItem("card 1", 0, 190)]),
        ],
    )
    staging = FakeTimeline(
        FINAL + " (rebuild staging)",
        video=[
            ("Craig", [FakeItem("Craig", 0, 203)]),
            ("Subtitles", [FakeItem("card 1", 0, 203)]),
        ],
    )
    return retired, staging


@pytest.mark.usefixtures("mock_dvr")
def test_a_join_passes_undeclared_and_says_so(project_dir):
    """2 items to 1 with frames gained and every name still playing is
    a merge, not a deletion - the lc-0004 refusal must not fire."""
    retired, staging = _join_timelines()
    resolve = FakeProject([FakeTimeline(MASTER), retired, staging])

    promoted = _promote(resolve, project_dir, {FINAL: staging.GetName()})

    assert promoted["promoted"] == [FINAL]
    report = promoted["replace_reports"][FINAL]
    assert report["refused"] is False
    assert report["joined"] == ["video:Craig"]
    # The replaced timeline is DELETED by default: one timeline per
    # reel, nothing archived (`library/tools/reel_retirement.py`).
    assert sorted(resolve.names()) == sorted([MASTER, FINAL])
    assert resolve.deleted == [f"{FINAL} (pre-rebuild backup)"]


@pytest.mark.usefixtures("mock_dvr")
def test_a_float_read_of_an_int_is_the_same_value_not_a_change():
    """Resolve reads `AudioPitchSemiTones` back as 0.0 on a re-placed
    item where it read 0 before (live, 2026-10-02): not an edit."""
    item = {
        "track_type": "audio",
        "track_name": "Dialogue",
        "source_identity": "file:/media/a.mov",
        "source_in_frame": 0,
        "source_out_frame": 40,
        "record_in": 0,
        "record_out": 40,
        "transform": {"AudioPitchSemiTones": 0, "AudioVolume": 0.0},
    }
    reread = {**item, "transform": {"AudioPitchSemiTones": 0.0, "AudioVolume": 0.0}}

    assert guard.snapshot_diff({"items": [item]}, {"items": [reread]}) == []
    added = {
        "kind": "item_added",
        "identity": guard._stable_item_key(item),
        "before": None,
        "after": item,
        "changed": {},
    }
    assert guard._change_is_carried(added, {"items": [reread]})
    moved = {**reread, "transform": {"AudioPitchSemiTones": 1.0, "AudioVolume": 0.0}}
    assert not guard._change_is_carried(added, {"items": [moved]})


@pytest.mark.usefixtures("mock_dvr")
def test_a_staging_no_build_recorded_drops_the_replaced_provenance(project_dir):
    """Reel 09, 2026-10-02: a promoted VARIANT has no provenance entry
    (`build_reel_variants` writes none), so promotion renamed both
    timelines and then raised at `rename_reel_entries`, leaving every
    step after it undone. The final's entry describes the build just
    replaced, so it is dropped rather than left as a wrong record."""

    def reel(name):
        return FakeTimeline(
            name,
            video=[
                ("Akshita", [FakeItem("Akshita A", 0, 131)]),
                ("Subtitles", [FakeItem("card 1", 0, 131)]),
            ],
        )

    retired, staging = reel(FINAL), reel(FINAL + " (rebuild staging)")
    resolve = FakeProject([FakeTimeline(MASTER), retired, staging])

    promoted = _promote(
        resolve, project_dir, {FINAL: staging.GetName()}, built_reels=[FINAL]
    )

    assert promoted["promoted"] == [FINAL]
    provenance = json.loads(
        (project_dir / "pipeline_output" / "review" / "plan_provenance.json").read_text(
            encoding="utf-8"
        )
    )
    assert FINAL not in provenance["built_reels"]
    assert staging.GetName() not in provenance["built_reels"]


# --------------------------------------------------------------------------
# From test_promote_per_reel.py
#
# Promotion is per reel: one refusal must not discard its siblings.
#
# The 2026-09-11 round built Reels 01/23/30/31, one refusal fired (the
# lc-0004 join the guard could not read), and all-or-nothing promotion
# discarded all four - the three that were fine were rebuilt from
# scratch an hour later. Promotion now diffs and renames per reel: a
# reel that passes promotes, a reel that refuses stays staged with its
# baselines and hold intact, and the refusal names only itself.
#
# Proven here on fixtures carrying the round's real names: four reels
# promote together, one with the cutaway loss shape, and the other
# three land while the refusal names just the one.

REEL_01 = "Reel 01 - hook-and-promise (final)"
REEL_23 = "Reel 23 - pricing-truth (final)"
REEL_30 = "Reel 30 - audio-seam (final)"
REEL_31 = "Reel 31 - kept-phrase (final)"


@pytest.fixture
def fake_preservation_snapshots(monkeypatch):
    install_fake_timeline_snapshots(monkeypatch)


def _clean_reel(final):
    """A reel whose rebuild changes nothing the guard cares about."""
    retired = FakeTimeline(
        final,
        video=[
            ("Akshita", [FakeItem("Akshita A", 0, 131)]),
            ("Subtitles", [FakeItem("card 1", 0, 131)]),
        ],
    )
    staging = FakeTimeline(
        final + " (rebuild staging)",
        video=[
            ("Akshita", [FakeItem("Akshita A", 0, 131)]),
            ("Subtitles", [FakeItem("card 1", 0, 131)]),
        ],
    )
    return retired, staging


def _refused_reel():
    """Reel 31 with the cutaway loss shape: V1 3 items to 2, the cover
    gone and nothing declaring it."""
    retired = FakeTimeline(
        REEL_31,
        video=[
            (
                "Akshita",
                [
                    FakeItem("Craig A", 0, 55),
                    FakeItem("LC4932 cover", 574, 598),
                    FakeItem("Craig B", 598, 657),
                ],
            ),
            ("Subtitles", [FakeItem("card 1", 0, 60), FakeItem("card 2", 60, 131)]),
        ],
    )
    staging = FakeTimeline(
        REEL_31 + " (rebuild staging)",
        video=[
            ("Akshita", [FakeItem("Craig A", 0, 67), FakeItem("Craig B", 67, 138)]),
            ("Subtitles", [FakeItem("card 1", 0, 60), FakeItem("card 2", 60, 131)]),
        ],
    )
    return retired, staging


@pytest.mark.usefixtures("mock_dvr")
@pytest.mark.usefixtures("fake_preservation_snapshots")
def test_one_refusal_promotes_its_siblings(project_dir):
    retired_01, staging_01 = _clean_reel(REEL_01)
    retired_23, staging_23 = _clean_reel(REEL_23)
    retired_30, staging_30 = _clean_reel(REEL_30)
    retired_31, staging_31 = _refused_reel()
    resolve = FakeProject(
        [
            FakeTimeline(MASTER),
            retired_01,
            retired_23,
            retired_30,
            retired_31,
            staging_01,
            staging_23,
            staging_30,
            staging_31,
        ]
    )
    staged_to_final = {
        REEL_01: staging_01.GetName(),
        REEL_23: staging_23.GetName(),
        REEL_30: staging_30.GetName(),
        REEL_31: staging_31.GetName(),
    }
    (project_dir / "pipeline_output" / "review" / "plan_provenance.json").write_text(
        json.dumps({"built_reels": sorted(staged_to_final.values())}), encoding="utf-8"
    )

    with (
        patch("library.tools.resolve_locale.scriptapp_preserving_locale"),
        patch("library.tools.reel_build.resolve_project_exactly", return_value=resolve),
        pytest.raises(ReelBuildError) as refused,
    ):
        promote_staged_reels(
            str(project_dir),
            "Mock Project",
            MASTER,
            staged_to_final,
            organise=False,
            track_plans=no_a_roll_track_plans(staged_to_final),
        )

    message = str(refused.value)
    # The promoted line says what landed; the refusal body names only
    # itself - no sibling is implicated in another reel's refusal.
    assert f"Promoted 3 reel(s): {[REEL_01, REEL_23, REEL_30]}" in message
    _header, refusal_body = message.split(
        f"REFUSING to promote 1 reel(s): {[REEL_31]}."
    )
    assert REEL_31 in refusal_body
    assert "video:Akshita" in refusal_body
    assert "LC4932 cover" in refusal_body
    assert REEL_01 not in refusal_body
    assert REEL_23 not in refusal_body
    assert REEL_30 not in refusal_body
    # ...the three passing reels landed under their final names with
    # no staging or backup debris left for them (each final appears
    # exactly once - the replaced originals were backed up and then
    # DELETED, the stagings renamed onto the final names;
    # `library/tools/reel_retirement.py`)...
    names = resolve.names()
    assert REEL_01 in names and REEL_23 in names and REEL_30 in names
    assert names.count(REEL_01) == 1
    assert names.count(REEL_23) == 1
    assert names.count(REEL_30) == 1
    assert not [
        name
        for name in names
        if name.endswith(("(rebuild staging)", "(pre-rebuild backup)"))
        and name != staging_31.GetName()
    ]
    # ...and the refused reel is exactly as it was: approved timeline
    # untouched, staging still in the project for a deliberate re-run.
    assert retired_31 in resolve.timelines
    assert staging_31 in resolve.timelines
    assert REEL_31 in names and staging_31.GetName() in names
    # The three passing reels deleted exactly their own backups - one
    # timeline per reel - and nothing was renamed into the archive
    # (the captain, 2026-09-18: no leftovers by default; there is no
    # earlier generation here, so nothing is collected either).
    assert sorted(resolve.deleted) == sorted(
        [
            f"{REEL_01} (pre-rebuild backup)",
            f"{REEL_23} (pre-rebuild backup)",
            f"{REEL_30} (pre-rebuild backup)",
        ]
    )
    from library.tools import reel_retirement

    archived = [name for name in names if reel_retirement.is_archived_timeline(name)]
    assert archived == []


@pytest.mark.usefixtures("mock_dvr")
@pytest.mark.usefixtures("fake_preservation_snapshots")
def test_a_staging_without_a_passing_track_plan_is_refused_untouched(project_dir):
    """Promotion reads the same a-roll link verdict as verify_timeline,
    and a staging with no track plan at all refuses too. Either way
    nothing is renamed."""
    from library.tools.timeline_layout import A_ROLL, SPEECH, TrackSpec

    final = "Reel 11 - your-website-is-your-resume"
    staging_name = final + " (rebuild staging)"
    raw_plan = {
        "video_tracks": [vars(TrackSpec(1, "video", A_ROLL, "Craig", "2"))],
        "audio_tracks": [vars(TrackSpec(1, "audio", SPEECH, "Craig CH1", "2"))],
        "material": {},
    }
    for track_plans, refusal in (
        ({staging_name: raw_plan}, "failed `aroll_linked`"),
        (None, "has no track plan"),
    ):
        retired = FakeTimeline(
            final,
            video=[("Craig", [FakeItem("LCATL0013.MXF", 0, 138)])],
            audio=[("Craig CH1", [FakeItem("LCATL0013.MXF", 0, 138)])],
        )
        # The picture slipped 7 frames off its speech: unlinked.
        staging = FakeTimeline(
            staging_name,
            video=[("Craig", [FakeItem("LCATL0013.MXF", 7, 138)])],
            audio=[("Craig CH1", [FakeItem("LCATL0013.MXF", 0, 138)])],
        )
        resolve = FakeProject([FakeTimeline(MASTER), retired, staging])

        with (
            patch("library.tools.resolve_locale.scriptapp_preserving_locale"),
            patch(
                "library.tools.reel_build.resolve_project_exactly",
                return_value=resolve,
            ),
            pytest.raises(ReelBuildError, match=refusal),
        ):
            promote_staged_reels(
                str(project_dir),
                "Mock Project",
                MASTER,
                {final: staging_name},
                organise=False,
                track_plans=track_plans,
            )

        assert retired.GetName() == final
        assert staging.GetName() == staging_name
        assert retired in resolve.timelines
        assert staging in resolve.timelines


@pytest.mark.usefixtures("mock_dvr")
@pytest.mark.usefixtures("fake_preservation_snapshots")
def test_filing_refusal_never_fails_promotion(project_dir, capsys):
    """`promote_staged_reels(organise=True)` files the media pool after
    the renames land; a filing pass that errors is said, recorded, and
    does not take the promoted reels down with it."""
    final = "Reel 31 - is-there-a-way-to-game-ai"
    retired, staging = _clean_reel(final)
    project = FakeProject([FakeTimeline(MASTER), retired, staging])
    staged_to_final = {final: staging.GetName()}
    (project_dir / "pipeline_output" / "review" / "plan_provenance.json").write_text(
        json.dumps({"built_reels": sorted(staged_to_final.values())}), encoding="utf-8"
    )
    swept = {
        "applied": True,
        "pool": {"removed": 0, "counts": {}},
        "files": {"areas": []},
        "bins": {},
        "refused": [],
        "journal_path": "",
    }
    with (
        patch("library.tools.resolve_locale.scriptapp_preserving_locale"),
        patch("library.tools.reel_build.resolve_project_exactly", return_value=project),
        patch(
            "library.tools.execution.organise_media_pool.organise_project",
            side_effect=RuntimeError("MoveClips returned False"),
        ),
        patch("library.tools.build_sweep.sweep_build", return_value=swept),
    ):
        result = promote_staged_reels(
            str(project_dir),
            "Mock Project",
            MASTER,
            staged_to_final,
            track_plans=no_a_roll_track_plans(staged_to_final),
        )
    assert result["promoted"] == [final]
    assert result["organised"] == {"refused": "MoveClips returned False"}
    assert "media-pool filing refused" in capsys.readouterr().err
    assert final in [t.GetName() for t in project.timelines]


# --------------------------------------------------------------------------
# From test_pending_promotion_hold.py
#
# A verified-but-unpromoted staging survives the cleanup sweep (issue #971).
#
# Reconstruction of the 2026-09-11 incident: Reel 13's marker fix was
# built into a scratch timeline at 04:22Z and verified correct at 1921
# frames. At 04:46Z the cleanup sweep deleted it as scratch - a
# timeline with a scratch-shaped name that no plan claims. It had never
# been promoted, so the fix existed, was proven, and was thrown away by
# tidying.
#
# The defect: the sweep knew "disposable scratch" but had no notion of
# "a scratch that is a PENDING PROMOTION". The fix is a durable hold a
# build takes out on its staging timeline (`library/tools/staging_holds.py`)
# and releases at promotion - and a sweep that REFUSES a held timeline
# loudly, naming what it declined, instead of skipping silently.
#
# These tests pin both halves of the distinction, in both directions
# (AGENTS.md 10.4): a verified-but-unpromoted staging MUST survive the
# sweep (plan-time and execution-time), and an ordinary abandoned
# scratch MUST still be collected - a guard that keeps everything is
# the failure mode on the other side. Every hold test below fails on
# the old behaviour, which had no hold concept at all.
#
# The scratch names here wear the sweep's own `SOP Proof...` family -
# the vocabulary the sweep is authorised to collect - standing in for
# Reel 13's `(baseline scratch)` container, which is the same shape:
# a verified fix in a scratch-shaped name, pending a promotion that
# has not happened yet.

MASTER_2 = "GEO Podcast - Synced"
PROJECT_ROOT = "/projects/geo-podcast"

# The incident's shape in the sweep's own vocabulary: a verified fix
# staged under a scratch-shaped name, awaiting promotion to the reel
# the captain opens.
HELD_SCRATCH = "SOP Proof_reel13_tail_breath (baseline scratch)"
HELD_FINAL = "Reel 13 - the-accounting-firm-ai-called-healthcare"
ABANDONED_SCRATCH = "SOP Proof_reel13_old_probe"


@pytest.fixture
def project_dir_2(tmp_path):
    root = tmp_path / "project"
    (root / "pipeline_output" / "review").mkdir(parents=True)
    return root


def clip(item_id, name, *, path, placed_by=(), folder=()):
    return Artefact(item_id=item_id, name=name, kind="clip", file_path=path,
                    placed_by=tuple(placed_by), folder_path=tuple(folder))


def timeline(item_id, name, *, folder=()):
    return Artefact(item_id=item_id, name=name, kind="timeline",
                    file_path="", placed_by=(),
                    folder_path=tuple(folder))


def generated(name):
    return (f"{PROJECT_ROOT}/pipeline_output/steps/"
            f"4_05_render_subtitles/{name}")


def scratch_pool():
    """Protected timelines plus the master present, the verified fix
    staged as scratch beside one ordinary abandoned scratch."""
    artefacts = [timeline("t-master", MASTER_2)]
    for i, name in enumerate(sorted(PROTECTED_TIMELINES)):
        artefacts.append(timeline(f"t-p{i}", name))
    artefacts.append(timeline("t-held", HELD_SCRATCH))
    artefacts.append(timeline("t-abandoned", ABANDONED_SCRATCH))
    return artefacts


def tree_of(artefacts):
    known = set()
    for a in artefacts:
        folder = tuple(a.folder_path)
        for depth in range(1, len(folder) + 1):
            known.add(folder[:depth])
    known.discard(())
    return sorted(known)


def plan_for(artefacts, name, project_root):
    return plan_proof_removal(
        artefacts, tree_of(artefacts), timeline_name=name,
        bin_names=[], project_root=project_root, master_name=MASTER_2)


# ------------------------------------------------- the hold primitives


@pytest.mark.usefixtures("mock_dvr")
@pytest.mark.usefixtures("fake_preservation_snapshots")
def test_a_corrupt_holds_file_refuses_rather_than_reading_empty(project_dir_2):
    path = project_dir_2 / "pipeline_output" / "review" / "staging_holds.json"
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(holds.HoldsUnreadable, match="cannot be read"):
        holds.read_holds(str(project_dir_2))
    artefacts = scratch_pool()
    with pytest.raises(holds.HoldsUnreadable):
        plan_for(artefacts, ABANDONED_SCRATCH, str(project_dir_2))


# --------------------------------- the incident, at plan time (04:46Z)


@pytest.mark.usefixtures("mock_dvr")
@pytest.mark.usefixtures("fake_preservation_snapshots")
def test_verified_unpromoted_scratch_survives_the_plan(project_dir_2):
    """The 04:46Z sweep planned the verified fix as scratch. It must
    refuse instead - loudly, naming the timeline, the promotion it
    awaits and how long it has waited. On the old behaviour this
    plans the deletion, so the test fails there."""
    holds.take_hold(
        str(project_dir_2), HELD_SCRATCH, awaiting=HELD_FINAL,
        reason="staged rebuild awaiting promotion",
        taken_by="rebuild_reels_in_project")
    artefacts = scratch_pool()
    with pytest.raises(ProofRemovalRefused) as refused:
        plan_for(artefacts, HELD_SCRATCH, str(project_dir_2))
    message = str(refused.value)
    assert HELD_SCRATCH in message
    assert HELD_FINAL in message
    assert "pending promotion" in message.lower() or "STAGED" in message
    assert "ago" in message
    assert "release_hold" in message
    assert "Nothing was removed" in message


@pytest.mark.usefixtures("mock_dvr")
@pytest.mark.usefixtures("fake_preservation_snapshots")
def test_ordinary_abandoned_scratch_still_plans(project_dir_2):
    """The failure mode on the other side: a guard that keeps
    everything. The abandoned scratch beside the held fix must still
    plan for removal."""
    holds.take_hold(str(project_dir_2), HELD_SCRATCH, awaiting=HELD_FINAL)
    artefacts = scratch_pool()
    plan = plan_for(artefacts, ABANDONED_SCRATCH, str(project_dir_2))
    assert plan["timeline"]["name"] == ABANDONED_SCRATCH


@pytest.mark.usefixtures("mock_dvr")
@pytest.mark.usefixtures("fake_preservation_snapshots")
def test_the_sweep_names_what_it_declined(project_dir_2):
    """Discovery is by name and knows no holds; the declined half is
    enumerated beside it and says what each held scratch awaits.
    Skipping the held ones silently would replace the deletion bug
    with the accumulation bug."""
    holds.take_hold(str(project_dir_2), HELD_SCRATCH, awaiting=HELD_FINAL,
                    reason="staged rebuild awaiting promotion")
    artefacts = scratch_pool()
    assert discover_scratch_timelines(artefacts) == sorted(
        [HELD_SCRATCH, ABANDONED_SCRATCH])
    declined = declined_held_scratch_timelines(artefacts, str(project_dir_2))
    assert [d["name"] for d in declined] == [HELD_SCRATCH]
    assert declined[0]["awaiting"] == HELD_FINAL
    assert declined[0]["age"] != ""
    assert "promotion" in declined[0]["reason"]


# --------------------------- the incident, at execution time (TOCTOU)


def live_scratch_project():
    """A live pool holding the verified fix as scratch, with no bins
    of its own - the incident's shape at 04:46Z."""
    proj = FakeProject("Fake")
    pool = proj.GetMediaPool()
    pool.SetCurrentFolder(pool.AddSubFolder(pool.GetRootFolder(), "Unsorted"))
    for uid, name in (("t-held", HELD_SCRATCH),
                      ("t-abandoned", ABANDONED_SCRATCH)):
        pool.next_timeline = FakeTimeline(name, pool_uid=uid)
        pool.CreateEmptyTimeline(name)
    pool.SetCurrentFolder(pool.GetRootFolder())
    return proj


def pool_timeline_names(proj):
    out = []

    def walk(folder):
        for clip in folder.GetClipList():
            if (clip.GetClipProperty("Type") or "") == "Timeline":
                out.append(clip.GetName())
        for sub in folder.GetSubFolderList():
            walk(sub)

    walk(proj.GetMediaPool().GetRootFolder())
    return sorted(out)


@pytest.mark.usefixtures("mock_dvr")
@pytest.mark.usefixtures("fake_preservation_snapshots")
def test_hold_taken_between_plan_and_apply_still_refuses(project_dir_2):
    """The plan is a claim about an earlier moment: the hold lands
    AFTER the plan is proven but BEFORE the deletion runs - a build
    staging while the operator sweeps. Execution re-checks live and
    refuses. On the old behaviour the timeline is deleted."""
    artefacts = scratch_pool()
    plan = plan_for(artefacts, HELD_SCRATCH, str(project_dir_2))
    holds.take_hold(
        str(project_dir_2), HELD_SCRATCH, awaiting=HELD_FINAL,
        reason="staged rebuild awaiting promotion",
        taken_by="rebuild_reels_in_project")
    proj = live_scratch_project()
    journal = str(project_dir_2 / "pipeline_output" / "review"
                  / "resolve_remove_proof_test.json")
    with pytest.raises(ProofRemovalRefused) as refused:
        proof_ex.remove_proof(proj, plan, journal)
    assert HELD_SCRATCH in str(refused.value)
    assert HELD_FINAL in str(refused.value)
    assert pool_timeline_names(proj) == sorted(
        [HELD_SCRATCH, ABANDONED_SCRATCH])


@pytest.mark.usefixtures("mock_dvr")
@pytest.mark.usefixtures("fake_preservation_snapshots")
def test_ordinary_abandoned_scratch_is_still_collected(project_dir_2):
    """The same execution path with no hold deletes the abandoned
    scratch and journals it - the clutter still goes."""
    artefacts = scratch_pool()
    plan = plan_for(artefacts, ABANDONED_SCRATCH, str(project_dir_2))
    proj = live_scratch_project()
    journal = str(project_dir_2 / "pipeline_output" / "review"
                  / "resolve_remove_proof_test.json")
    result = proof_ex.remove_proof(proj, plan, journal)
    assert result["timeline"] == ABANDONED_SCRATCH
    assert pool_timeline_names(proj) == [HELD_SCRATCH]
    record = json.loads(
        (project_dir_2 / "pipeline_output" / "review"
         / "resolve_remove_proof_test.json").read_text(encoding="utf-8"))
    assert record["timeline"]["name"] == ABANDONED_SCRATCH


# ------------------ the lifecycle: promotion releases, the guard stays


PROMOTE_FINAL = "Reel 09 - your-website-is-only-20-percent (final)"

def _promote_2(project, project_dir_2, staged_to_final, allow_drops=None):
    (project_dir_2 / "pipeline_output" / "review"
     / "plan_provenance.json").write_text(json.dumps(
         {"built_reels": sorted(staged_to_final.values())}),
        encoding="utf-8")
    with patch("library.tools.resolve_locale.scriptapp_preserving_locale"), \
            patch("library.tools.reel_build.resolve_project_exactly",
                  return_value=project):
        from library.tools.reel_build import promote_staged_reels
        return promote_staged_reels(
            str(project_dir_2), "Mock Project", MASTER_2, staged_to_final,
            organise=False, allow_drops=allow_drops,
            track_plans=no_a_roll_track_plans(staged_to_final))


def _full_rows(extra=()):
    visual = [timeline_item(f"semantic {n}", n * 100, n * 100 + 40)
              for n in range(4)]
    return [
        ("Akshita", [timeline_item("Akshita A", 0, 131)]),
        ("Semantic", list(visual) + list(extra)),
    ]


@pytest.mark.usefixtures("mock_dvr")
@pytest.mark.usefixtures("fake_preservation_snapshots")
def test_promote_releases_the_hold(project_dir_2):
    """The verified staging promotes cleanly and its hold goes with
    it - the sweep may collect the staging NAME afterwards because
    nothing under it exists any more."""
    from library.tools.reel_build import STAGING_SUFFIX
    staging = PROMOTE_FINAL + STAGING_SUFFIX
    retired = FakeTimeline(PROMOTE_FINAL, video=_full_rows())
    staged = FakeTimeline(staging, video=_full_rows())
    project = FakeProject([retired, staged])
    holds.take_hold(str(project_dir_2), staging, awaiting=PROMOTE_FINAL,
                    reason="staged rebuild awaiting promotion",
                    taken_by="rebuild_reels_in_project")
    result = _promote_2(project, project_dir_2, {PROMOTE_FINAL: staging})
    assert result["promoted"] == [PROMOTE_FINAL]
    assert holds.read_holds(str(project_dir_2)) == {}
    # The staging took the final name; the timeline it replaced is
    # DELETED by default - one timeline per reel, nothing archived
    # (`library/tools/reel_retirement.py`).
    assert sorted(t.GetName() for t in project.timelines) == [
        PROMOTE_FINAL]
    assert project.deleted == [f"{PROMOTE_FINAL} (pre-rebuild backup)"]


@pytest.mark.usefixtures("mock_dvr")
@pytest.mark.usefixtures("fake_preservation_snapshots")
def test_guard_still_refuses_a_held_lossy_staging_and_keeps_the_hold(project_dir_2):
    """How the two compose: the hold gets the staging TO the
    promotion; the row-diff guard (issue #925) still refuses a
    promotion that would LOSE a row - and the hold is retained,
    which is the safe direction on both halves."""
    from library.tools.reel_build import ReelBuildError, STAGING_SUFFIX
    staging = PROMOTE_FINAL + STAGING_SUFFIX
    retired = FakeTimeline(PROMOTE_FINAL, video=_full_rows())
    staged = FakeTimeline(
        staging, video=_full_rows()[:1])  # the Semantic row is gone
    project = FakeProject([retired, staged])
    holds.take_hold(str(project_dir_2), staging, awaiting=PROMOTE_FINAL)
    with pytest.raises(ReelBuildError) as refused:
        _promote_2(project, project_dir_2, {PROMOTE_FINAL: staging})
    assert "Semantic" in str(refused.value)
    assert set(holds.read_holds(str(project_dir_2))) == {staging}
    assert sorted(t.GetName() for t in project.timelines) == sorted(
        [PROMOTE_FINAL, staging])


# ── Pending promotions report themselves (Reel 16, 2026-09-19) ──────
# A build that stages but never promotes left its staging protected
# and invisible: the holds file knew, and nothing ever read it as
# unfinished work. These pin the reader a run-end report is built
# on: oldest first, empty when nothing is pending, loud about what
# and how long - and a corrupt holds file still refuses rather than
# reading as "nothing pending".

def _backdate(project_dir_2, staging, taken_at):
    from pathlib import Path
    path = Path(holds.holds_path_for(str(project_dir_2)))
    doc = json.loads(path.read_text(encoding="utf-8"))
    doc["holds"][staging]["taken_at"] = taken_at
    path.write_text(json.dumps(doc, indent=2), encoding="utf-8")


@pytest.mark.usefixtures("mock_dvr")
@pytest.mark.usefixtures("fake_preservation_snapshots")
def test_pending_promotions_lists_stagings_oldest_first(project_dir_2):
    from library.tools.reel_build import STAGING_SUFFIX
    first = "Reel 16 - why-ai-trusts-one-brand-over-another" + STAGING_SUFFIX
    second = "Reel 04 - google-gave-a-list-from-2023" + STAGING_SUFFIX
    holds.take_hold(str(project_dir_2), first,
                    awaiting="Reel 16 - why-ai-trusts-one-brand-over-another",
                    taken_by="rebuild_reels_in_project")
    holds.take_hold(str(project_dir_2), second,
                    awaiting="Reel 04 - google-gave-a-list-from-2023",
                    taken_by="rebuild_reels_in_project")
    _backdate(project_dir_2, first, "2026-09-19T15:36:20+00:00")
    _backdate(project_dir_2, second, "2026-09-19T20:20:33+00:00")
    pending = holds.pending_promotions(str(project_dir_2))
    assert [row["staging"] for row in pending] == [first, second]
    assert pending[0]["awaiting"] == \
        "Reel 16 - why-ai-trusts-one-brand-over-another"
    assert pending[0]["taken_by"] == "rebuild_reels_in_project"
    assert pending[0]["age"] not in ("", "unknown age")


@pytest.mark.usefixtures("mock_dvr")
@pytest.mark.usefixtures("fake_preservation_snapshots")
def test_concurrent_takes_keep_every_hold(project_dir_2):
    """The 2026-09-20 lane lost a hold between two interleaved writers:
    take/release were atomic-rename writes but unlocked
    read-modify-write, so each writer read the same set and wrote back
    only its own entry. Takes now run under an exclusive file lock, so
    N barrier-synchronised takers keep all N entries.

    The sleep widens the real read-to-write window (it delays only,
    the logic is untouched) so the pre-lock code drops entries on
    nearly every run - verified by reverting `staging_holds.py` alone
    and watching this fail - while the locked code passes with it.
    """
    import threading
    import time
    from unittest.mock import patch

    threads = 8
    per_thread = 20
    folder = str(project_dir_2)
    real_write = holds._write_holds

    def slow_write(project_folder, data):
        time.sleep(0.002)
        real_write(project_folder, data)

    barrier = threading.Barrier(threads)

    def take_many(tid):
        barrier.wait()
        for index in range(per_thread):
            holds.take_hold(
                folder, f"Reel {tid:02d} - lane-{tid} take-{index}",
                awaiting=f"Reel {tid:02d} - lane-{tid}",
                taken_by="rebuild_reels_in_project")

    with patch.object(holds, "_write_holds", side_effect=slow_write):
        workers = [threading.Thread(target=take_many, args=(tid,))
                   for tid in range(threads)]
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join(timeout=120)

    kept = holds.held_names(folder)
    assert len(kept) == threads * per_thread, (
        f"concurrent takes lost {threads * per_thread - len(kept)} "
        f"hold(s) - an interleaved writer dropped them")


# ── Ghost holds reconcile against the live project (2026-09-20) ─────
# Two holds named `(MFA timings)` and `(all three fixes)` stagings of
# Reel 26, both deleted days earlier. With no live listing both read
# as unpromoted work for three days: "four builds held awaiting a
# promotion decision" went out with two of the four never real. These
# pin the reconciliation: a hold whose timeline exists reads as
# pending, a hold whose timeline is gone reads as its own
# non-pending state, and a hold whose name merely shares a prefix
# with a living timeline does NOT resolve onto it.

GHOST_FINAL = "Reel 26 - write-for-the-question-your-customer-ask"
GHOST_MFA = GHOST_FINAL + " (MFA timings)"
GHOST_FIXES = GHOST_FINAL + " (all three fixes)"


def _take_ghost_and_live_holds(project_dir_2):
    from library.tools.reel_build import STAGING_SUFFIX
    live_staging = "Reel 04 - google-gave-a-list-from-2023" + STAGING_SUFFIX
    holds.take_hold(str(project_dir_2), GHOST_MFA, awaiting=GHOST_FINAL,
                    taken_by="rebuild_reels_in_project")
    holds.take_hold(str(project_dir_2), GHOST_FIXES, awaiting=GHOST_FINAL,
                    taken_by="rebuild_reels_in_project")
    holds.take_hold(str(project_dir_2), live_staging,
                    awaiting="Reel 04 - google-gave-a-list-from-2023",
                    taken_by="rebuild_reels_in_project")
    return live_staging


@pytest.mark.usefixtures("mock_dvr")
@pytest.mark.usefixtures("fake_preservation_snapshots")
def test_pending_promotions_reports_ghosts_as_stale_not_pending(project_dir_2):
    """All three shapes at once, against an explicit name listing."""
    live_staging = _take_ghost_and_live_holds(project_dir_2)
    # The trap's shape, stated plainly: each ghost name carries the
    # living final's whole text as a prefix.
    assert GHOST_MFA.startswith(GHOST_FINAL)
    assert GHOST_FIXES.startswith(GHOST_FINAL)
    rows = holds.pending_promotions(
        str(project_dir_2), timeline_names=[GHOST_FINAL, live_staging])
    assert {row["staging"]: row["status"] for row in rows} == {
        GHOST_MFA: "stale",
        GHOST_FIXES: "stale",
        live_staging: "pending",
    }
    report = holds.report_pending(
        str(project_dir_2), timeline_names=[GHOST_FINAL, live_staging])
    assert "UNPROMOTED STAGING: 1 staged timeline(s)" in report
    assert live_staging in report
    assert "STALE HOLDS: 2 hold(s)" in report
    assert GHOST_MFA in report
    assert GHOST_FIXES in report
    assert "release_hold" in report
    # The ghost names appear only past the STALE section - never
    # counted as work awaiting a promotion decision.
    unpromoted, _, stale = report.partition("STALE HOLDS")
    assert GHOST_MFA not in unpromoted
    assert GHOST_FIXES not in unpromoted
    assert live_staging not in stale


# --------------------------------------------------------------------------
# From test_promotion_records_comp_export_and_supersession.py
#
# One promotion records BOTH the comp export and the supersession.
#
# `record_reel_promotion` is now the shared call site of two best-effort
# promotion records: PR 1296's per-reel Fusion comp export
# (`reel_fusion_comps.export_built_reels`) and G6's snapshot
# supersession (`plan_provenance.record_snapshot_supersession`).  Neither
# existed when the other landed and nobody has exercised the pairing, so
# this drives one promotion through both and asserts each half's record -
# plus that neither half's failure skips the other.
#
# ``library/tools/versions/store.py``.

REEL = "Reel 09 - your-website-is-only-20-percent"
LUA = "-- comp export: plain Lua text, committed verbatim\n"


# ── Fakes ────────────────────────────────────────────────────────────

class _FakeItem:
    def __init__(self, fail=False):
        self._fail = fail

    def GetFusionCompCount(self):
        return 1

    def ExportFusionComp(self, path, index):
        if self._fail:
            raise RuntimeError("Resolve declined the export")
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(LUA)
        return True


class _FakeTimeline:
    def __init__(self, name, items=()):
        self._name = name
        self._items = list(items)

    def GetName(self):
        return self._name

    def GetTrackCount(self, kind):
        return 1 if kind == "video" else 0

    def GetItemListInTrack(self, kind, index):
        return list(self._items)


class _FakeProject:
    def __init__(self, name, timelines):
        self._name = name
        self._timelines = list(timelines)

    def GetName(self):
        return self._name

    def GetTimelineCount(self):
        return len(self._timelines)

    def GetTimelineByIndex(self, index):
        return self._timelines[index - 1]


class _FakeManager:
    def __init__(self, project):
        self._project = project

    def GetCurrentProject(self):
        return self._project


class _FakeResolve:
    def __init__(self, project):
        self._project = project

    def GetProjectManager(self):
        return _FakeManager(self._project)


class _FakeDvr:
    def __init__(self, resolve):
        self._resolve = resolve

    def scriptapp(self, name):
        return self._resolve


def _install(monkeypatch, project):
    monkeypatch.setitem(sys.modules, "DaVinciResolveScript",
                        _FakeDvr(_FakeResolve(project)))
    import library.tools.timeline_serializer as ser

    def _fake_serialize(resolve_mock=None, timeline=None):
        return {"schema_version": "1.0",
                "metadata": {"name": timeline.GetName()},
                "tracks": []}

    monkeypatch.setattr(ser, "serialize_timeline_state", _fake_serialize)


def _write_stale_snapshot(project_folder, filename, timeline_name, bound):
    review = (project_folder / "pipeline_output" / "review")
    review.mkdir(parents=True, exist_ok=True)
    path = review / filename
    path.write_text(json.dumps(
        {"schema_version": "1.0",
         "metadata": {"name": timeline_name},
         "tracks": [
             {"type": "video", "index": 1, "name": "V1",
              "clips": [{"unique_id": "clip-1", "name": "LC4932.MXF",
                         "source_in": 35714, "source_out": bound}]}]}),
        encoding="utf-8")
    return path


# ── The pairing ──────────────────────────────────────────────────────

class TestPromotionRecordsBoth:
    def test_comp_export_and_supersession_in_one_promotion(
            self, tmp_path, monkeypatch):
        bvc.init_project_repo(str(tmp_path))
        stale = _write_stale_snapshot(
            tmp_path, "Reel_09_stale.timeline.json", REEL, 36490)
        timeline = _FakeTimeline(REEL, [_FakeItem()])
        _install(monkeypatch, _FakeProject("Podcast (field test)",
                                           [timeline]))

        report = bvc.record_reel_promotion(
            str(tmp_path), "Podcast (field test)", [REEL])

        assert report["committed"] is True
        assert len(report["snapshots"]) == 1
        # The comp export ran: one verbatim Lua file.
        assert len(report["fusion_comps"]["files"]) == 1
        comp_path = report["fusion_comps"]["files"][0]
        with open(comp_path, encoding="utf-8") as handle:
            assert handle.read() == LUA
        # The supersession ran: the new snapshot is authoritative,
        # the stale same-named file reads as superseded.
        new_snapshot = report["snapshots"][0]
        assert report["snapshot_supersession"][REEL]["snapshot"] == (
            new_snapshot.split("review/")[-1])
        review = str(tmp_path / "pipeline_output" / "review")
        entry, reason = built_from_snapshot(review, REEL)
        assert entry is not None, reason
        superseded, reason = is_snapshot_superseded(review, stale.name)
        assert superseded, reason

    def test_comp_export_failure_does_not_skip_supersession(
            self, tmp_path, monkeypatch):
        bvc.init_project_repo(str(tmp_path))
        stale = _write_stale_snapshot(
            tmp_path, "Reel_09_stale.timeline.json", REEL, 36490)
        timeline = _FakeTimeline(REEL, [_FakeItem(fail=True)])
        _install(monkeypatch, _FakeProject("Podcast (field test)",
                                           [timeline]))

        report = bvc.record_reel_promotion(
            str(tmp_path), "Podcast (field test)", [REEL])

        assert report["committed"] is True
        assert report["fusion_comps"]["files"] == []
        assert report["fusion_comps"]["errors"]
        # The export failed and the supersession still recorded.
        assert REEL in report["snapshot_supersession"]
        review = str(tmp_path / "pipeline_output" / "review")
        superseded, reason = is_snapshot_superseded(review, stale.name)
        assert superseded, reason


# --------------------------------------------------------------------------
# From test_build_refuses_when_the_record_would_lie.py
#
# Promotion record failures remain visible after a timeline lands.
#
# The incident history and failure matrix live in
# `docs/evidence/promotion_record_consistency.md`.

STAGING_01 = REEL_01 + " (rebuild staging)"


@pytest.fixture
def mock_dvr_2(stub_resolve_script):
    yield


def _clean_reel_2():
    retired = FakeTimeline(REEL_01, video=[
        ("Akshita", [TimelineItemSpec("Akshita A", 0, 131)]),
        ("Subtitles", [TimelineItemSpec("card 1", 0, 131)]),
    ])
    staging = FakeTimeline(STAGING_01, video=[
        ("Akshita", [TimelineItemSpec("Akshita A", 0, 131)]),
        ("Subtitles", [TimelineItemSpec("card 1", 0, 131)]),
    ])
    return retired, staging


def _seed_provenance(project_dir):
    (project_dir / "pipeline_output" / "review"
     / "plan_provenance.json").write_text(json.dumps(
         {"built_reels": [STAGING_01]}), encoding="utf-8")


def _promote_3(project_dir, resolve, **kwargs):
    kwargs.setdefault("organise", False)
    kwargs.setdefault("track_plans", no_a_roll_track_plans(
        {REEL_01: STAGING_01}))
    with patch("library.tools.resolve_locale.scriptapp_preserving_locale"), \
            patch("library.tools.reel_build.resolve_project_exactly",
                  return_value=resolve):
        return promote_staged_reels(
            str(project_dir), "Mock Project", MASTER,
            {REEL_01: STAGING_01}, **kwargs)


class _RecordFailure:
    def __init__(self, patch_target, message, *, supersede=False,
                 sign_off=False, returns_none=False, retirement=False):
        self.patch_target = patch_target
        self.message = message
        self.supersede = supersede
        self.sign_off = sign_off
        self.returns_none = returns_none
        self.retirement = retirement


@pytest.mark.usefixtures("mock_dvr_2")
@pytest.mark.usefixtures("fake_preservation_snapshots")
@pytest.mark.parametrize(
    "failure",
    [
        pytest.param(_RecordFailure(None, "retirement failed",
                                    retirement=True),
                     id="declined-backup-retirement"),
        pytest.param(_RecordFailure(
            "library.tools.comparison_retirement.collect_for_bases",
            "comparison timelines"), id="comparison-retirement"),
        pytest.param(_RecordFailure(
            "library.tools.reel_signoff.supersede", "supersession",
            supersede=True), id="declared-signoff-supersession"),
        pytest.param(_RecordFailure(
            "library.tools.reel_signoff.supersede", "mid-promotion",
            supersede=True, sign_off=True, returns_none=True),
            id="signoff-disappears-mid-promotion"),
        pytest.param(_RecordFailure(
            "library.tools.versions.rounds.stamp_promotion", "not stamped"),
            id="round-stamp"),
        pytest.param(_RecordFailure(
            "library.tools.plan_provenance.record_carried_digests",
            "signatures did not"), id="carried-signatures"),
        pytest.param(_RecordFailure(
            "library.tools.caption_asset_gc.rename_ledger_timelines",
            "render-ledger"), id="render-ledger-binding"),
    ],
)
def test_post_promotion_record_failures_raise_after_the_reel_lands(
        project_dir, failure):
    """Each row exercises a post-promotion record boundary; see the evidence table."""
    retired, staging = _clean_reel_2()
    resolve = FakeProject(
        [FakeTimeline(MASTER), retired, staging],
        delete_ok=not failure.retirement)
    _seed_provenance(project_dir)
    if failure.sign_off:
        from library.tools import reel_signoff as signoff

        signoff.sign_off(str(project_dir), REEL_01, note="ships")

    promotion_args = {"supersede": [REEL_01]} if failure.supersede else {}
    with ExitStack() as stack:
        if failure.patch_target:
            patch_args = ({"return_value": None} if failure.returns_none
                          else {"side_effect": OSError("disk full")})
            stack.enter_context(patch(failure.patch_target, **patch_args))
        with pytest.raises(ReelBuildError, match=failure.message):
            _promote_3(project_dir, resolve, **promotion_args)

    assert REEL_01 in resolve.names()
    assert STAGING_01 not in resolve.names()
    assert staging.GetName() == REEL_01
    if failure.retirement:
        assert resolve.deleted == []


@pytest.mark.usefixtures("mock_dvr_2")
@pytest.mark.usefixtures("fake_preservation_snapshots")
def test_a_pass_records_its_verdict_before_returning(project_dir):
    """The verify-record half of the ruling, pinned at the verifier:
    a 0 from `run_verification` means the conformance report is on
    disk with the graded reel rows - so no build can promote off an
    unrecorded verdict, by construction rather than by check.

    Driven against a stand-in Resolve project (the scope harness in
    `test_verify_scopes_to_built_reels.py` measures which timelines
    are read; this one measures what a pass leaves behind).
    """
    import io

    from library.tools.reel_conformance_verifier import ReelTimeline, run_verification

    master = FakeTimeline(MASTER, settings={
        "timelineResolutionWidth": "1080",
        "timelineResolutionHeight": "1920",
    })
    fake = FakeProject([master, FakeTimeline("Reel 01 - a")], current=master)

    fps = 24000 / 1001

    def fake_snapshot(timeline, project_name):
        from library.tools.timeline_ingest import TimelineSnapshot

        return TimelineSnapshot(
            project_name=project_name,
            timeline_name=timeline.GetName(),
            fps=fps, reported_fps=23.976,
            width=1080, height=1920,
            start_frame=0, end_frame=240, clips=())

    json_path = str(project_dir / "conformance_report.json")
    with patch("library.tools.marker_feedback.connect_resolve") as connect, \
            patch("library.tools.timeline_ingest.resolve_project_exactly",
                  return_value=fake), \
            patch("library.tools.timeline_ingest.snapshot_timeline",
                  side_effect=fake_snapshot), \
            patch("library.tools.timeline_ingest.snapshot_to_dict",
                  side_effect=lambda snap: {
                      "timeline": snap.timeline_name}), \
            patch("library.tools.reel_conformance_verifier._snapshot_to_reel_timeline",
                  side_effect=lambda snap, **kwargs: ReelTimeline(
                      reel_name=snap.timeline_name, fps=fps, total_frames=240,
                      video_items=(), audio_items=(),
                      caption_items=())), \
            patch("library.tools.reel_conformance_verifier.verify_reel") as verified:
        from library.tools.reel_conformance_verifier import ReelResult

        def _clean(plan, timeline, **kwargs):
            return ReelResult(
                reel_name=plan.reel_name, reel_number=1, plan_seconds=0.0,
                plan_frames=0.0, actual_frames=0, items_expected=0,
                items_actual=0, one_frame_holes=0, big_holes=[],
                captions_expected=0, captions_actual=0, speech_seconds=0.0,
                uncaptioned_seconds=0.0, uncaptioned_pct=0.0,
                short_captions=0, edge_cuts=0, bad_take_cuts=0,
                markers=0, findings=[])
        verified.side_effect = _clean
        connect.return_value = FakeResolve()
        code = run_verification(
            project_name="Mock Project",
            master_name=MASTER,
            transcript={"segments": []},
            json_path=json_path,
            only_reels=["Reel 01 - a"],
            out=io.StringIO())

    assert code == 0
    with open(json_path, encoding="utf-8") as handle:
        report = json.load(handle)
    assert [row["reel_name"] for row in report["reels"]] == ["Reel 01 - a"]


# --------------------------------------------------------------------------
# From test_reel_signoff.py
#
# A BUILT reel carries a durable sign-off, and promotion respects it.
#
# A signed-off reel refuses promotion unless the promotion declares
# `--supersede`, per reel (a refusal never holds back a sibling); an
# unreadable sign-off file refuses rather than reading as "nobody approved
# anything". The declare-then-proceed half is pinned in
# `tests/unit/reels/test_reel_retirement.py::test_a_signed_off_backup_retires_on_the_default_path`.

OTHER = "Reel 13 - the-accounting-firm"


@pytest.fixture
def mock_dvr_3(stub_resolve_script, monkeypatch):
    # Stubbed through the shared fixture: `patch.dict` on
    # `sys.modules` restores the WHOLE dict and so evicts every
    # module first imported inside it (tests/conftest.py).
    install_fake_timeline_snapshots(monkeypatch)
    yield


@pytest.fixture
def project(tmp_path):
    root = tmp_path / "project"
    (root / "pipeline_output" / "review").mkdir(parents=True)
    return root


# ── The store ────────────────────────────────────────────────────


@pytest.mark.usefixtures("mock_dvr_3")
def test_a_signoff_survives_its_containers_and_records_which_build(project):
    """A reel is staged, backed up and promoted - three names for one
    reel. A sign-off keyed on the container name would evaporate the
    moment the build touched it."""
    signoff.sign_off(str(project), REEL, note="this one ships")
    for container in (
        REEL,
        f"{REEL} (rebuild staging)",
        f"{REEL} (pre-rebuild backup)",
    ):
        assert signoff.signoff_for(str(project), container) is not None
    # And it records WHICH build was approved: a bare flag cannot answer
    # whether the timeline in front of you is still the signed one.
    rows = {
        "video:A": {
            "media_type": "video",
            "index": 1,
            "name": "A",
            "items": [],
            "count": 0,
            "frames": 0,
        }
    }
    signoff.sign_off(str(project), OTHER, rows=rows)
    assert "still carries the rows that were approved" in signoff.describe(
        str(project), OTHER, rows
    )
    moved = {"video:A": {**rows["video:A"], "count": 1, "frames": 10}}
    assert "a later build has taken this name" in signoff.describe(
        str(project), OTHER, moved
    )


@pytest.mark.usefixtures("mock_dvr_3")
def test_an_unreadable_signoff_file_refuses(project):
    """An unreadable approval reads exactly like no approval, and
    promotion would then overwrite the reel the captain signed off."""
    (project / "pipeline_output" / "review" / signoff.SIGNOFF_FILENAME).write_text(
        "{broken", encoding="utf-8"
    )
    with pytest.raises(signoff.SignOffsUnreadable):
        signoff.read_signoffs(str(project))


# ── The promotion ────────────────────────────────────────────────


def _rows(name):
    return [("Akshita", [FakeItem(f"{name} clip", 0, 100)])]


def _pair(final):
    return (
        FakeTimeline(final, video=_rows(final)),
        FakeTimeline(f"{final} (rebuild staging)", video=_rows(final)),
    )


def _promote_4(resolve, project, staged_to_final, supersede=None):
    import json

    (project / "pipeline_output" / "review" / "plan_provenance.json").write_text(
        json.dumps({"built_reels": sorted(staged_to_final.values())}), encoding="utf-8"
    )
    with (
        patch("library.tools.resolve_locale.scriptapp_preserving_locale"),
        patch("library.tools.reel_build.resolve_project_exactly", return_value=resolve),
    ):
        return promote_staged_reels(
            str(project),
            "Mock Project",
            MASTER,
            staged_to_final,
            organise=False,
            supersede=supersede,
            track_plans=no_a_roll_track_plans(staged_to_final),
        )


@pytest.mark.usefixtures("mock_dvr_3")
def test_promotion_refuses_over_an_undeclared_signoff(project):
    """Remove `assert_declared` from the promotion and the signed-off
    timeline is replaced with no word said. Per reel, like the guard: the
    refusal never holds back a sibling (the 2026-09-11 round lost three
    buildable reels to one refusal)."""
    signoff.sign_off(str(project), REEL, note="the ending is right now")
    original, staging = _pair(REEL)
    other, other_staging = _pair(OTHER)
    resolve = FakeProject(
        [FakeTimeline(MASTER), original, staging, other, other_staging]
    )

    with pytest.raises(ReelBuildError) as refused:
        _promote_4(
            resolve,
            project,
            {REEL: staging.GetName(), OTHER: other_staging.GetName()},
        )

    message = str(refused.value)
    assert "SIGNED OFF" in message
    assert "the ending is right now" in message
    assert f"--supersede {REEL!r}" in message
    assert f"Promoted 1 reel(s): {[OTHER]}" in message
    assert OTHER in resolve.names()
    # Nothing of the signed reel was renamed: the approved timeline is
    # still there and the staging is untouched for a deliberate re-run.
    assert original.GetName() == REEL
    assert staging.GetName() == f"{REEL} (rebuild staging)"
    assert resolve.deleted == [f"{OTHER} (pre-rebuild backup)"]
    assert signoff.signoff_for(str(project), REEL) is not None


# --------------------------------------------------------------------------
# From test_reel_placed_assets.py
#
# Scratch holds no file a live timeline points at.
#
# The builder promotes placed overlays and cards into step-owned OUTPUT
# areas, and every placement site refuses a scratch path. The measured
# incident is in `library/tools/reel_placed_assets.py`'s docstring.

def _project(tmp_path):
    """A project folder with a scratch overlay drafted in it."""
    project = str(tmp_path / "proj")
    os.makedirs(
        os.path.join(
            project, "pipeline_output", "scratch", "reel_look", "frame_overlays"
        )
    )
    src = os.path.join(
        project,
        "pipeline_output",
        "scratch",
        "reel_look",
        "frame_overlays",
        "tv_frame_deadbeef01_100f.mov",
    )
    with open(src, "wb") as handle:
        handle.write(os.urandom(1 << 20))
    return project, src


def test_only_a_scratch_path_is_refused_placement(tmp_path):
    """The guard refuses scratch, not everything: the promoted copy
    passes, and so does footage from outside the project entirely."""
    project, src = _project(tmp_path)
    assert is_under_scratch(src, project)
    with pytest.raises(PlacedAssetInScratch, match="scratch"):
        assert_placeable(src, project)
    dest = promote_to_durable(src, project, Area.REEL_FRAME_OVERLAYS)
    assert assert_placeable(dest, project) == dest
    assert assert_placeable("/footage/clip_001.mxf", project) == "/footage/clip_001.mxf"


def test_promoted_overlay_lands_durable_not_under_scratch(tmp_path):
    """The whole point: what the timeline will point at can no longer
    be removed by anything that trusts the SCRATCH declaration."""
    project, src = _project(tmp_path)
    before = os.path.getsize(src)
    (segments,) = promote_frame_overlays(
        [{"overlay_path": src, "timeline_start": 0.0, "total_frames": 100}], project
    )
    dest = segments["overlay_path"]
    assert dest != src
    assert not is_under_scratch(dest, project)
    assert dest.startswith(
        os.path.join(
            project, "pipeline_output", "steps", "7_01_build_reels", "frame_overlays"
        )
        + os.sep
    )
    assert os.path.isfile(dest)
    assert os.path.getsize(dest) == before
    # The renderer's own cache is untouched: promotion shares bytes,
    # it does not move the source.
    assert os.path.isfile(src)
    assert os.path.getsize(src) == before


def test_promoting_a_missing_file_is_refused_not_placed(tmp_path):
    project, _ = _project(tmp_path)
    missing = os.path.join(
        project, "pipeline_output", "scratch", "reel_look", "frame_overlays", "gone.mov"
    )
    with pytest.raises(PlacedAssetInScratch, match="not a file"):
        promote_to_durable(missing, project, Area.REEL_FRAME_OVERLAYS)


def test_cards_promote_to_the_card_area(tmp_path):
    """Dict cards and frozen PlannedCard-shaped dataclasses alike."""
    project, src = _project(tmp_path)

    @dataclasses.dataclass(frozen=True)
    class _Card:
        render_name: str
        rendered_path: str = ""

    card_src = os.path.join(project, "pipeline_output", "scratch", "reel_cards")
    os.makedirs(card_src, exist_ok=True)
    card_file = os.path.join(card_src, "reel_01_card_00.mov")
    with open(card_file, "wb") as handle:
        handle.write(b"card-bytes")
    (as_dict, as_dataclass) = promote_cards(
        [
            {"render_name": "reel_01_card_00", "rendered_path": card_file},
            _Card(render_name="reel_01_card_00", rendered_path=card_file),
        ],
        project,
    )
    for promoted in (as_dict["rendered_path"], as_dataclass.rendered_path):
        assert not is_under_scratch(promoted, project)
        assert os.path.join("7_01_build_reels", "reel_cards") in promoted
        with open(promoted, "rb") as handle:
            assert handle.read() == b"card-bytes"
    # Promotion re-points; it does not report. A card with no file
    # is the builder's own missing-file refusal, unchanged.
    (card,) = promote_cards([{"render_name": "x", "rendered_path": ""}], project)
    assert card["rendered_path"] == ""
