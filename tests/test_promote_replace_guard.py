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

    def __init__(self, name, start, end):
        self._name = name
        self._start = start
        self._end = end

    def GetName(self):
        return self._name

    def GetStart(self):
        return self._start

    def GetEnd(self):
        return self._end

    def GetDuration(self):
        return self._end - self._start


class FakeTimeline:
    """A timeline with real rows: names, items, spans."""

    def __init__(self, name, video=(), audio=()):
        self._name = name
        self._rows = {"video": list(video), "audio": list(audio)}

    def GetName(self):
        return self._name

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


def _promote(project, project_dir, staged_to_final, allow_drops=None):
    import json
    # The baselines the gate graded, filed under the staging names -
    # which is what a real staged build leaves behind. Only the
    # provenance sidecar is strict about existing.
    (project_dir / "pipeline_output" / "review"
     / "plan_provenance.json").write_text(json.dumps(
         {"built_reels": sorted(staged_to_final.values())}),
        encoding="utf-8")
    with patch("library.tools.resolve_locale.scriptapp_preserving_locale"), \
            patch("library.tools.reel_build.resolve_project_exactly",
                  return_value=project):
        return promote_staged_reels(
            str(project_dir), "Mock Project", MASTER, staged_to_final,
            organise=False, allow_drops=allow_drops,
            track_plans=no_a_roll_track_plans(staged_to_final))


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
