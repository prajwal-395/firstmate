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
import json
from unittest.mock import MagicMock, patch

import pytest
from tests.promotion_test_helpers import no_a_roll_track_plans

from library.tools import reel_replace_guard as guard
from library.tools.reel_build import (
    ReelBuildError,
    promote_staged_reels,
)


FINAL = "Reel 09 - your-website-is-only-20-percent (final)"
MASTER = "Podcast - Synced"


@pytest.fixture(autouse=True)
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


class FakeItem:
    """One timeline item: a name over a record span."""

    def __init__(self, name, start, end, enabled=True):
        self._name = name
        self._start = start
        self._end = end
        self.enabled = enabled

    def GetName(self):
        return self._name

    def GetStart(self):
        return self._start

    def GetEnd(self):
        return self._end

    def GetDuration(self):
        return self._end - self._start

    def GetClipEnabled(self):
        return self.enabled


class FakeTimeline:
    """A timeline with real rows: names, items, spans."""

    def __init__(self, name, video=(), audio=()):
        self._name = name
        self._unique_id = name
        self._rows = {"video": list(video), "audio": list(audio)}

    def GetName(self):
        return self._name

    def GetUniqueId(self):
        return self._unique_id

    def SetName(self, name):
        self._name = name
        return True

    def GetTrackCount(self, kind):
        return len(self._rows[kind])

    def GetTrackName(self, kind, index):
        return self._rows[kind][index - 1][0]

    def GetItemListInTrack(self, kind, index):
        return self._rows[kind][index - 1][1]

    # Promotion reads the captain's markers off the retiring timeline
    # before anything is renamed (`library/tools/marker_carry.py`), and
    # REFUSES a timeline whose markers it cannot see - so a fake that
    # cannot answer for them is a fake of a different object.
    def GetStartFrame(self):
        return 0

    def GetMarkers(self):
        return dict(getattr(self, "_markers", {}))

    def AddMarker(self, frame, color, name, note, duration, custom=""):
        self.added_markers = getattr(self, "added_markers", [])
        self.added_markers.append((frame, color, name, note))
        return True


class UnreadableTimeline(FakeTimeline):
    """Resolve mid-wobble: the row count itself will not read."""

    def GetTrackCount(self, kind):
        raise RuntimeError("Resolve is busy")


class FakeProject:
    """A Resolve project whose pool really renames and deletes."""

    def __init__(self, timelines):
        self.timelines = list(timelines)
        pool = MagicMock()
        pool.DeleteTimelines.side_effect = self._delete
        self._pool = pool
        self.deleted = []

    def _delete(self, timelines):
        for timeline in timelines:
            self.deleted.append(timeline.GetName())
            self.timelines.remove(timeline)
        return True

    def GetMediaPool(self):
        return self._pool

    def GetTimelineCount(self):
        return len(self.timelines)

    def GetTimelineByIndex(self, index):
        return self.timelines[index - 1]

    def names(self):
        return [t.GetName() for t in self.timelines]


def _full_snapshot(timeline):
    items = []
    for track_type, rows in timeline._rows.items():
        for track_name, clips in rows:
            for clip in clips:
                items.append({
                    "track_type": track_type, "track_name": track_name,
                    "name": clip.GetName(),
                    "source_identity": f"fake:{clip.GetName()}",
                    "source_in_frame": clip.GetStart(),
                    "source_out_frame": clip.GetEnd(),
                    "record_in": clip.GetStart(),
                    "record_out": clip.GetEnd(),
                    "duration": clip.GetDuration(),
                    "enabled": clip.GetClipEnabled(),
                    "transform": {}, "fusion": {}, "color": {},
                    "markers": [],
                })
    return {
        "timeline": {"name": timeline.GetName(),
                     "unique_id": timeline.GetName(),
                     "settings": {"timelineFrameRate": 23.976},
                     "start_frame": timeline.GetStartFrame(),
                     "end_frame": max(
                         [item["record_out"] for item in items] or [0])},
        "items": items,
        "markers": [],
    }


def _promote(project, project_dir, staged_to_final, allow_drops=None,
             full_snapshots=None, baseline_snapshot=True,
             timeline_inventory_before=None):
    import json
    from contextlib import ExitStack

    # The baselines the gate graded, filed under the staging names -
    # which is what a real staged build leaves behind. Only the
    # provenance sidecar is strict about existing.
    snapshots = full_snapshots or {
        id(timeline): _full_snapshot(timeline)
        for timeline in project.timelines
    }
    provenance = {"built_reels": sorted(staged_to_final.values())}
    if baseline_snapshot:
        final = next(iter(staged_to_final))
        live = next(t for t in project.timelines if t.GetName() == final)
        provenance["ren_timeline_snapshots"] = {
            final: {"snapshot": snapshots[id(live)]}}
    (project_dir / "pipeline_output" / "review" / "plan_provenance.json").write_text(
        json.dumps(provenance), encoding="utf-8")
    with ExitStack() as stack:
        stack.enter_context(patch.object(
            guard, "full_timeline_snapshot",
            side_effect=lambda timeline, _project, _folder=None:
            snapshots[id(timeline)]))
        stack.enter_context(patch(
            "library.tools.resolve_locale.scriptapp_preserving_locale"))
        stack.enter_context(patch(
            "library.tools.reel_build.resolve_project_exactly",
            return_value=project))
        return promote_staged_reels(
            str(project_dir), "Mock Project", MASTER, staged_to_final,
            organise=False, allow_drops=allow_drops,
            track_plans=no_a_roll_track_plans(staged_to_final),
            timeline_inventory_before=timeline_inventory_before)


def _cutaway_timelines():
    """The 12:33 rebuild: plan-faithful, cutaway gone, hole closed."""
    retired = FakeTimeline(FINAL, video=[
        ("Akshita", [FakeItem("Craig A", 0, 55),
                     FakeItem("LC4932 cover", 574, 598),
                     FakeItem("Craig B", 598, 657)]),
        ("Craig", [FakeItem("Craig wide", 0, 131)]),
        ("Subtitles", [FakeItem("card 1", 0, 60),
                       FakeItem("card 2", 60, 131)]),
    ])
    staging = FakeTimeline(FINAL + " (rebuild staging)", video=[
        ("Akshita", [FakeItem("Craig A", 0, 67),
                     FakeItem("Craig B", 67, 138)]),
        ("Craig", [FakeItem("Craig wide", 0, 131)]),
        ("Subtitles", [FakeItem("card 1", 0, 60),
                       FakeItem("card 2", 60, 131)]),
    ])
    return retired, staging


def _semantic_timelines():
    """The node_modules-less build: all four semantic visuals failed to
    render, so the whole V5 row is absent from the staging."""
    visual = [FakeItem(f"semantic {n}", n * 100, n * 100 + 40)
              for n in range(4)]
    retired = FakeTimeline(FINAL, video=[
        ("Akshita", [FakeItem("Akshita A", 0, 131)]),
        ("Craig", [FakeItem("Craig wide", 0, 131)]),
        ("Subtitles", [FakeItem("card 1", 0, 131)]),
        ("Transitions", [FakeItem("flash", 60, 66)]),
        ("Semantic", visual),
    ])
    staging = FakeTimeline(FINAL + " (rebuild staging)", video=[
        ("Akshita", [FakeItem("Akshita A", 0, 131)]),
        ("Craig", [FakeItem("Craig wide", 0, 131)]),
        ("Subtitles", [FakeItem("card 1", 0, 131)]),
        ("Transitions", [FakeItem("flash", 60, 66)]),
    ])
    return retired, staging


def test_only_reel_rebuild_over_cutaway_refuses(project_dir):
    """Drop 1: V1 3 items -> 2, the 24-frame cover at rec 574 named."""
    retired, staging = _cutaway_timelines()
    resolve = FakeProject([FakeTimeline(MASTER), retired, staging])

    with pytest.raises(ReelBuildError) as refused:
        _promote(resolve, project_dir, {FINAL: staging.GetName()})

    message = str(refused.value)
    assert "video:Akshita" in message
    assert "3 item(s) -> 2" in message
    assert "LC4932 cover" in message and "574..598" in message
    assert "--allow-drop 'video:Akshita'" in message
    # Nothing was renamed and nothing deleted: the check runs before
    # the first rename, so the approved timeline is still there.
    assert sorted(resolve.names()) == sorted(
        [MASTER, FINAL, staging.GetName()])
    assert resolve.deleted == []


def test_build_with_failed_overlay_renders_refuses(project_dir):
    """Drop 2: the V5 row exists retired and not at all incoming."""
    retired, staging = _semantic_timelines()
    resolve = FakeProject([FakeTimeline(MASTER), retired, staging])

    with pytest.raises(ReelBuildError) as refused:
        _promote(resolve, project_dir, {FINAL: staging.GetName()})

    message = str(refused.value)
    assert "video:Semantic" in message
    assert "row absent" in message
    assert "4 item(s)" in message
    assert "--allow-drop 'video:Semantic'" in message
    assert sorted(resolve.names()) == sorted(
        [MASTER, FINAL, staging.GetName()])
    assert resolve.deleted == []


def test_snapshot_and_replace_diff_report_enabled_state_change():
    retired = FakeTimeline(FINAL, video=[
        ("Semantic", [FakeItem("semantic-card", 120, 168, enabled=False)])])
    incoming = FakeTimeline(FINAL + " (rebuild staging)", video=[
        ("Semantic", [FakeItem("semantic-card", 120, 168, enabled=True)])])

    old_rows = guard.snapshot_timeline(retired, FINAL)
    new_rows = guard.snapshot_timeline(
        incoming, incoming.GetName(), side="staged")
    semantic = old_rows["video:Semantic"]

    assert semantic["items"][0]["enabled"] is False
    report = guard.check_replacement(
        FINAL, incoming.GetName(), old_rows, new_rows)
    assert report["rows"][0]["enabled_changes"] == [{
        "name": "semantic-card", "start": 120, "end": 168,
        "retired_enabled": False, "incoming_enabled": True,
    }]


def test_full_snapshot_diff_detects_replaced_timeline_identity():
    before = {"timeline": {"name": FINAL, "unique_id": "ren-id",
                           "settings": {}}, "items": [], "markers": []}
    live = {"timeline": {"name": FINAL, "unique_id": "editor-id",
                          "settings": {}}, "items": [], "markers": []}
    staged = {"timeline": {"name": FINAL, "unique_id": "staging-id",
                            "settings": {}}, "items": [], "markers": []}

    changes = guard.snapshot_diff(before, live)

    assert changes == [{"kind": "timeline_identity", "field": "unique_id",
                        "before": "ren-id", "after": "editor-id"}]
    assert not guard._change_is_carried(changes[0], staged)


def test_marker_change_is_carried_when_its_content_moves_with_the_picture():
    before = {"source": "timeline_marker", "frame": 100,
              "frame_in_timeline_space": 100, "color": "Blue",
              "name": "Captain", "note": "keep this", "duration_frames": 1,
              "custom_data": {}, "custom_data_raw": ""}
    carried = {**before, "frame": 85, "frame_in_timeline_space": 85}
    change = {"kind": "marker_added", "identity": guard._marker_key(before),
              "before": None, "after": before}

    assert guard._change_is_carried(change, {"markers": [carried]})


def test_removed_marker_is_not_carried_by_a_relocated_copy():
    removed = {"source": "timeline_marker", "frame": 100,
               "frame_in_timeline_space": 100, "color": "Blue",
               "name": "Captain", "note": "remove this",
               "duration_frames": 1, "custom_data": {},
               "custom_data_raw": ""}
    relocated = {**removed, "frame": 80,
                 "frame_in_timeline_space": 80}
    change = {"kind": "marker_removed",
              "identity": guard._marker_key(removed),
              "before": removed, "after": None}

    assert not guard._change_is_carried(change, {"markers": [relocated]})


def test_full_snapshot_excludes_ephemeral_and_non_timeline_marker_data(
        monkeypatch):
    from contextlib import nullcontext
    from dataclasses import replace

    from library.tools.marker_feedback import MarkerNote

    class Timeline:
        def GetName(self):
            return FINAL

        def GetUniqueId(self):
            return "timeline-id"

        def GetSetting(self, key=None):
            settings = {"timelineFrameRate": "23.976",
                        "timelineResolutionWidth": "1080",
                        "timelineResolutionHeight": "1920"}
            return settings if key is None else settings.get(key, "")

        def GetStartFrame(self):
            return 0

        def GetEndFrame(self):
            return 100

    note = MarkerNote(
        source="timeline_marker", name="Captain", note="keep this",
        text="Captain\n\nkeep this", frame=42, timecode="00:00:01:18",
        frame_in_timeline_space=42, color="Blue", duration_frames=3,
        custom_data={"id": "m-1"}, custom_data_raw='{"id":"m-1"}',
        clips=[{"name": "context-only"}], read_at="first read")
    clip_note = replace(note, source="clip_marker", read_at="first read")
    reads = iter(([note, clip_note],
                  [replace(note, read_at="later read"),
                   replace(clip_note, read_at="later read")]))
    monkeypatch.setattr(
        "library.tools.resolve_lock.cursor_excursion",
        lambda *_args, **_kwargs: nullcontext())
    monkeypatch.setattr("library.tools.reel_read.read_tracks",
                        lambda *_args, **_kwargs: [])
    monkeypatch.setattr("library.tools.marker_feedback.read_notes",
                        lambda *_args, **_kwargs: next(reads))

    first = guard.full_timeline_snapshot(Timeline(), object())
    second = guard.full_timeline_snapshot(Timeline(), object())

    assert first == second
    assert first["markers"] == [{
        "source": "timeline_marker", "frame": 42,
        "frame_in_timeline_space": 42, "color": "Blue",
        "name": "Captain", "note": "keep this", "duration_frames": 3,
        "custom_data": {"id": "m-1"},
        "custom_data_raw": '{"id":"m-1"}',
    }]


def test_first_contact_carries_a_manual_marker_before_comparing(
        project_dir, monkeypatch):
    marker = {
        "frame": 40, "color": "Blue", "name": "Captain",
        "note": "keep this", "duration": 1, "custom_data": "",
        "anchor": ("/media/craig.mov", 400),
    }
    live_marker = {
        "source": "timeline_marker", "frame": 40,
        "frame_in_timeline_space": 40, "color": "Blue",
        "name": "Captain", "note": "keep this", "duration_frames": 1,
        "custom_data": {}, "custom_data_raw": "",
    }
    staged_marker = {**live_marker, "frame": 35,
                     "frame_in_timeline_space": 35}
    retired = FakeTimeline(FINAL)
    staging = FakeTimeline(FINAL + " (rebuild staging)")
    live_snapshot = {"timeline": {"name": FINAL, "unique_id": "live",
                                   "settings": {"timelineFrameRate": 23.976},
                                   "start_frame": 0, "end_frame": 100},
                    "items": [], "markers": [live_marker]}
    staging_snapshot = {
        "timeline": {"name": staging.GetName(), "unique_id": "staging",
                     "settings": {"timelineFrameRate": 23.976},
                     "start_frame": 0, "end_frame": 100},
        "items": [], "markers": [],
    }
    snapshots = {id(retired): live_snapshot, id(staging): staging_snapshot}

    def place(timeline, carried):
        assert carried == [{**marker, "to_frame": 35,
                            "pairing": "note"}]
        snapshots[id(timeline)] = {
            **staging_snapshot, "markers": [staged_marker]}
        return []

    monkeypatch.setattr("library.tools.marker_carry.read_markers",
                        lambda *_args: [marker])
    monkeypatch.setattr("library.tools.marker_carry.plan_carry",
                        lambda *_args: ([{**marker, "to_frame": 35,
                                          "pairing": "note"}], []))
    monkeypatch.setattr("library.tools.marker_carry.place", place)
    monkeypatch.setattr("library.tools.marker_carry.read_clip_markers",
                        lambda *_args: [])
    monkeypatch.setattr("library.tools.marker_gate.verify_promotion",
                        lambda *_args, **_kwargs: None)

    resolve = FakeProject([FakeTimeline(MASTER), retired, staging])
    promoted = _promote(
        resolve, project_dir, {FINAL: staging.GetName()},
        full_snapshots=snapshots, baseline_snapshot=False)

    assert promoted["promoted"] == [FINAL]
    provenance = json.loads((project_dir / "pipeline_output" / "review"
                             / "plan_provenance.json").read_text(
                                 encoding="utf-8"))
    record = provenance["unattributed_editor_changes"][FINAL][0]
    assert record["status"] == "carried"

def test_promotion_refuses_a_target_created_during_the_build(project_dir):
    retired, staging = _semantic_timelines()
    resolve = FakeProject([FakeTimeline(MASTER), retired, staging])

    with pytest.raises(ReelBuildError, match="not in the project's inventory"):
        _promote(
            resolve, project_dir, {FINAL: staging.GetName()},
            timeline_inventory_before=[{
                "name": MASTER, "unique_id": MASTER, "settings": {}}])

    assert sorted(resolve.names()) == sorted(
        [MASTER, FINAL, staging.GetName()])
    assert resolve.deleted == []


def test_legacy_snapshots_without_enabled_are_unknown_not_changes():
    legacy = {"video:Semantic": {
        "media_type": "video", "index": 1, "name": "Semantic",
        "count": 1, "frames": 48,
        "items": [{"name": "semantic-card", "start": 120,
                   "end": 168, "duration": 48}],
    }}
    current = {"video:Semantic": {
        "media_type": "video", "index": 1, "name": "Semantic",
        "count": 1, "frames": 48,
        "items": [{"name": "semantic-card", "start": 120,
                   "end": 168, "duration": 48, "enabled": False}],
    }}

    changes = guard.diff_rows(legacy, current)[0]["enabled_changes"]

    assert changes == []


def test_live_snapshot_refuses_unreadable_enabled_state():
    timeline = FakeTimeline(FINAL, video=[(
        "Semantic", [FakeItem("semantic-card", 120, 168, enabled=None)])])

    with pytest.raises(guard.ReplaceGuardUnreadable,
                       match="enabled state"):
        guard.snapshot_timeline(timeline, FINAL)


@pytest.mark.parametrize("declaration", [
    ["video:Semantic"],
])
def test_declared_reduction_passes_and_names_what_it_declared(
        project_dir, declaration):
    """The intended change: silent on stdout, named in the record."""
    retired, staging = _semantic_timelines()
    resolve = FakeProject([FakeTimeline(MASTER), retired, staging])

    promoted = _promote(resolve, project_dir, {FINAL: staging.GetName()},
                        allow_drops={FINAL: declaration})

    assert promoted["promoted"] == [FINAL]
    report = promoted["replace_reports"][FINAL]
    assert report["refused"] is False
    assert report["allowed"] == ["video:Semantic"]
    # The replaced timeline is DELETED by default: one timeline per
    # reel, nothing archived (`library/tools/reel_retirement.py`).
    assert sorted(resolve.names()) == sorted([MASTER, FINAL])
    assert resolve.deleted == [f"{FINAL} (pre-rebuild backup)"]


def test_unreadable_retiring_timeline_refuses(project_dir):
    """Fail closed: what cannot be read cannot be judged, so no promote."""
    retired = UnreadableTimeline(FINAL)
    staging = FakeTimeline(FINAL + " (rebuild staging)")
    resolve = FakeProject([FakeTimeline(MASTER), retired, staging])

    with pytest.raises(ReelBuildError, match="could not be read"):
        _promote(resolve, project_dir, {FINAL: staging.GetName()})

    assert sorted(resolve.names()) == sorted(
        [MASTER, FINAL, staging.GetName()])
    assert resolve.deleted == []


def _join_timelines():
    """The lc-0004 shape: keep insistence withdrew a take cut, so two
    adjacent Craig placements became one continuous one - 2 items to 1
    over MORE frames (the restored seconds are back in)."""
    retired = FakeTimeline(FINAL, video=[
        ("Craig", [FakeItem("Craig", 0, 100),
                   FakeItem("Craig", 100, 190)]),
        ("Subtitles", [FakeItem("card 1", 0, 190)]),
    ])
    staging = FakeTimeline(FINAL + " (rebuild staging)", video=[
        ("Craig", [FakeItem("Craig", 0, 203)]),
        ("Subtitles", [FakeItem("card 1", 0, 203)]),
    ])
    return retired, staging


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


def test_a_loss_that_gains_frames_still_refuses(project_dir):
    """The counter-example frames alone cannot catch: the cover is gone
    and the surviving clip grew past the old row total - frames gained,
    content lost. Names are the load-bearing half, so this refuses."""
    retired = FakeTimeline(FINAL, video=[
        ("Akshita", [FakeItem("Craig A", 0, 100),
                      FakeItem("LC4932 cover", 100, 124)]),
    ])
    staging = FakeTimeline(FINAL + " (rebuild staging)", video=[
        ("Akshita", [FakeItem("Craig A", 0, 140)]),
    ])
    resolve = FakeProject([FakeTimeline(MASTER), retired, staging])

    with pytest.raises(ReelBuildError) as refused:
        _promote(resolve, project_dir, {FINAL: staging.GetName()})

    message = str(refused.value)
    assert "video:Akshita" in message
    assert "2 item(s) -> 1" in message
    assert "LC4932 cover" in message
    assert sorted(resolve.names()) == sorted(
        [MASTER, FINAL, staging.GetName()])
    assert resolve.deleted == []


def test_a_float_read_of_an_int_is_the_same_value_not_a_change():
    """Resolve reads `AudioPitchSemiTones` back as 0.0 on a re-placed
    item where it read 0 before (live, 2026-10-02): not an edit."""
    item = {"track_type": "audio", "track_name": "Dialogue",
            "source_identity": "file:/media/a.mov", "source_in_frame": 0,
            "source_out_frame": 40, "record_in": 0, "record_out": 40,
            "transform": {"AudioPitchSemiTones": 0, "AudioVolume": 0.0}}
    reread = {**item, "transform": {"AudioPitchSemiTones": 0.0,
                                    "AudioVolume": 0.0}}

    assert guard.snapshot_diff({"items": [item]}, {"items": [reread]}) == []
    added = {"kind": "item_added", "identity": guard._stable_item_key(item),
             "before": None, "after": item, "changed": {}}
    assert guard._change_is_carried(added, {"items": [reread]})
    moved = {**reread, "transform": {"AudioPitchSemiTones": 1.0,
                                     "AudioVolume": 0.0}}
    assert not guard._change_is_carried(added, {"items": [moved]})
