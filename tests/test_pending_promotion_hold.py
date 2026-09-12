"""A verified-but-unpromoted staging survives the cleanup sweep (issue #971).

Reconstruction of the 2026-09-11 incident: Reel 13's marker fix was
built into a scratch timeline at 04:22Z and verified correct at 1921
frames. At 04:46Z the cleanup sweep deleted it as scratch - a
timeline with a scratch-shaped name that no plan claims. It had never
been promoted, so the fix existed, was proven, and was thrown away by
tidying.

The defect: the sweep knew "disposable scratch" but had no notion of
"a scratch that is a PENDING PROMOTION". The fix is a durable hold a
build takes out on its staging timeline (`library/tools/staging_holds.py`)
and releases at promotion - and a sweep that REFUSES a held timeline
loudly, naming what it declined, instead of skipping silently.

These tests pin both halves of the distinction, in both directions
(AGENTS.md 10.4): a verified-but-unpromoted staging MUST survive the
sweep (plan-time and execution-time), and an ordinary abandoned
scratch MUST still be collected - a guard that keeps everything is
the failure mode on the other side. Every hold test below fails on
the old behaviour, which had no hold concept at all.

The scratch names here wear the sweep's own `SOP Proof...` family -
the vocabulary the sweep is authorised to collect - standing in for
Reel 13's `(baseline scratch)` container, which is the same shape:
a verified fix in a scratch-shaped name, pending a promotion that
has not happened yet.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

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

MASTER = "GEO Podcast - Synced"
PROJECT_ROOT = "/projects/geo-podcast"

# The incident's shape in the sweep's own vocabulary: a verified fix
# staged under a scratch-shaped name, awaiting promotion to the reel
# the captain opens.
HELD_SCRATCH = "SOP Proof_reel13_tail_breath (baseline scratch)"
HELD_FINAL = "Reel 13 - the-accounting-firm-ai-called-healthcare"
ABANDONED_SCRATCH = "SOP Proof_reel13_old_probe"


@pytest.fixture(autouse=True)
def mock_dvr():
    with patch.dict("sys.modules", {"DaVinciResolveScript": MagicMock()}):
        yield


@pytest.fixture
def project_dir(tmp_path):
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
    artefacts = [timeline("t-master", MASTER)]
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
        bin_names=[], project_root=project_root, master_name=MASTER)


# ------------------------------------------------- the hold primitives


def test_hold_take_read_release_roundtrip(project_dir):
    assert holds.read_holds(str(project_dir)) == {}
    entry = holds.take_hold(
        str(project_dir), HELD_SCRATCH, awaiting=HELD_FINAL,
        reason="staged rebuild awaiting promotion",
        taken_by="rebuild_reels_in_project")
    assert entry["awaiting"] == HELD_FINAL
    read = holds.read_holds(str(project_dir))
    assert set(read) == {HELD_SCRATCH}
    assert read[HELD_SCRATCH]["awaiting"] == HELD_FINAL
    # Re-taking restarts the pending window rather than stacking.
    again = holds.take_hold(str(project_dir), HELD_SCRATCH,
                            awaiting=HELD_FINAL)
    assert set(holds.read_holds(str(project_dir))) == {HELD_SCRATCH}
    assert again["taken_at"] >= entry["taken_at"]
    assert holds.release_hold(str(project_dir), "no such timeline") is False
    assert holds.release_hold(str(project_dir), HELD_SCRATCH) is True
    assert holds.read_holds(str(project_dir)) == {}


def test_a_corrupt_holds_file_refuses_rather_than_reading_empty(project_dir):
    path = project_dir / "pipeline_output" / "review" / "staging_holds.json"
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(holds.HoldsUnreadable, match="cannot be read"):
        holds.read_holds(str(project_dir))
    artefacts = scratch_pool()
    with pytest.raises(holds.HoldsUnreadable):
        plan_for(artefacts, ABANDONED_SCRATCH, str(project_dir))


def test_hold_age_reads_in_minutes_hours_days():
    now = datetime(2026, 9, 11, 4, 46, tzinfo=timezone.utc)
    assert holds.hold_age({"taken_at": "2026-09-11T04:22:00+00:00"},
                          now=now) == "24m"
    assert holds.hold_age({"taken_at": "2026-09-11T01:46:00+00:00"},
                          now=now) == "3h00m"
    assert holds.hold_age({"taken_at": "2026-09-09T04:46:00+00:00"},
                          now=now) == "2d0h"
    assert holds.hold_age({}) == "unknown age"


# --------------------------------- the incident, at plan time (04:46Z)


def test_verified_unpromoted_scratch_survives_the_plan(project_dir):
    """The 04:46Z sweep planned the verified fix as scratch. It must
    refuse instead - loudly, naming the timeline, the promotion it
    awaits and how long it has waited. On the old behaviour this
    plans the deletion, so the test fails there."""
    holds.take_hold(
        str(project_dir), HELD_SCRATCH, awaiting=HELD_FINAL,
        reason="staged rebuild awaiting promotion",
        taken_by="rebuild_reels_in_project")
    artefacts = scratch_pool()
    with pytest.raises(ProofRemovalRefused) as refused:
        plan_for(artefacts, HELD_SCRATCH, str(project_dir))
    message = str(refused.value)
    assert HELD_SCRATCH in message
    assert HELD_FINAL in message
    assert "pending promotion" in message.lower() or "STAGED" in message
    assert "ago" in message
    assert "release_hold" in message
    assert "Nothing was removed" in message


def test_ordinary_abandoned_scratch_still_plans(project_dir):
    """The failure mode on the other side: a guard that keeps
    everything. The abandoned scratch beside the held fix must still
    plan for removal."""
    holds.take_hold(str(project_dir), HELD_SCRATCH, awaiting=HELD_FINAL)
    artefacts = scratch_pool()
    plan = plan_for(artefacts, ABANDONED_SCRATCH, str(project_dir))
    assert plan["timeline"]["name"] == ABANDONED_SCRATCH


def test_the_sweep_names_what_it_declined(project_dir):
    """Discovery is by name and knows no holds; the declined half is
    enumerated beside it and says what each held scratch awaits.
    Skipping the held ones silently would replace the deletion bug
    with the accumulation bug."""
    holds.take_hold(str(project_dir), HELD_SCRATCH, awaiting=HELD_FINAL,
                    reason="staged rebuild awaiting promotion")
    artefacts = scratch_pool()
    assert discover_scratch_timelines(artefacts) == sorted(
        [HELD_SCRATCH, ABANDONED_SCRATCH])
    declined = declined_held_scratch_timelines(artefacts, str(project_dir))
    assert [d["name"] for d in declined] == [HELD_SCRATCH]
    assert declined[0]["awaiting"] == HELD_FINAL
    assert declined[0]["age"] != ""
    assert "promotion" in declined[0]["reason"]


# --------------------------- the incident, at execution time (TOCTOU)


class FakeFolder:
    def __init__(self, name, uid):
        self._name, self.uid, self.clips, self.subs = name, uid, [], []

    def GetName(self): return self._name
    def GetUniqueId(self): return self.uid
    def GetClipList(self): return list(self.clips)
    def GetSubFolderList(self): return list(self.subs)


class FakeClip:
    def __init__(self, uid, name, kind="clip", path=""):
        self.uid, self._name, self.kind, self.path = uid, name, kind, path

    def GetUniqueId(self): return self.uid
    def GetName(self): return self._name

    def GetClipProperty(self, key):
        if key == "Type":
            return "Timeline" if self.kind == "timeline" else "Video + Audio"
        if key == "File Path":
            return self.path
        return ""

    def GetMetadata(self, key=None): return ""

    def SetMetadata(self, key, value):
        return key in ("Comments", "Keywords")


class FakeLiveTimeline:
    def __init__(self, name, items=()):
        self._name, self.items = name, list(items)

    def GetName(self): return self._name
    def GetTrackCount(self, kind): return 1 if kind == "video" else 0
    def GetItemListInTrack(self, kind, index):
        return list(self.items) if kind == "video" else []


class FakePool:
    def __init__(self, root):
        self.root, self.current = root, root

    def GetRootFolder(self): return self.root
    def GetCurrentFolder(self): return self.current
    def SetCurrentFolder(self, folder):
        self.current = folder
        return True

    def _home(self, clip):
        def walk(folder):
            if clip in folder.clips:
                return folder
            for sub in folder.subs:
                found = walk(sub)
                if found is not None:
                    return found
            return None
        return walk(self.root)

    def DeleteClips(self, items):
        wanted = {c.GetUniqueId() for c in items}

        def walk(folder):
            folder.clips[:] = [c for c in folder.clips
                                if c.GetUniqueId() not in wanted]
            for sub in folder.subs:
                walk(sub)

        walk(self.root)
        return True

    def DeleteFolders(self, folders):
        for folder in folders:
            if folder.GetClipList() or folder.GetSubFolderList():
                return False
            parent = self._parent(folder)
            if parent is None:
                return False
            parent.subs.remove(folder)
        return True

    def _parent(self, folder):
        def walk(node):
            for sub in node.subs:
                if sub is folder:
                    return node
                found = walk(sub)
                if found is not None:
                    return found
            return None
        return walk(self.root)

    def DeleteTimelines(self, timelines):
        return self.DeleteClips(timelines)


class FakeProject:
    def __init__(self, name, pool, timelines=()):
        self._name, self.pool, self.timelines = name, pool, list(timelines)

    def GetName(self): return self._name
    def GetMediaPool(self): return self.pool
    def GetTimelineCount(self): return len(self.timelines)
    def GetTimelineByIndex(self, i): return self.timelines[i - 1]


def live_scratch_project():
    """A live pool holding the verified fix as scratch, with no bins
    of its own - the incident's shape at 04:46Z."""
    root = FakeFolder("Master", "root")
    holding = FakeFolder("Unsorted", "f-u")
    holding.clips.append(FakeClip("t-held", HELD_SCRATCH, kind="timeline"))
    holding.clips.append(
        FakeClip("t-abandoned", ABANDONED_SCRATCH, kind="timeline"))
    root.subs.append(holding)
    return FakeProject("Fake", FakePool(root), [])


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


def test_hold_taken_between_plan_and_apply_still_refuses(project_dir):
    """The plan is a claim about an earlier moment: the hold lands
    AFTER the plan is proven but BEFORE the deletion runs - a build
    staging while the operator sweeps. Execution re-checks live and
    refuses. On the old behaviour the timeline is deleted."""
    artefacts = scratch_pool()
    plan = plan_for(artefacts, HELD_SCRATCH, str(project_dir))
    holds.take_hold(
        str(project_dir), HELD_SCRATCH, awaiting=HELD_FINAL,
        reason="staged rebuild awaiting promotion",
        taken_by="rebuild_reels_in_project")
    proj = live_scratch_project()
    journal = str(project_dir / "pipeline_output" / "review"
                  / "resolve_remove_proof_test.json")
    with pytest.raises(ProofRemovalRefused) as refused:
        proof_ex.remove_proof(proj, plan, journal)
    assert HELD_SCRATCH in str(refused.value)
    assert HELD_FINAL in str(refused.value)
    assert pool_timeline_names(proj) == sorted(
        [HELD_SCRATCH, ABANDONED_SCRATCH])


def test_ordinary_abandoned_scratch_is_still_collected(project_dir):
    """The same execution path with no hold deletes the abandoned
    scratch and journals it - the clutter still goes."""
    artefacts = scratch_pool()
    plan = plan_for(artefacts, ABANDONED_SCRATCH, str(project_dir))
    proj = live_scratch_project()
    journal = str(project_dir / "pipeline_output" / "review"
                  / "resolve_remove_proof_test.json")
    result = proof_ex.remove_proof(proj, plan, journal)
    assert result["timeline"] == ABANDONED_SCRATCH
    assert pool_timeline_names(proj) == [HELD_SCRATCH]
    record = json.loads(
        (project_dir / "pipeline_output" / "review"
         / "resolve_remove_proof_test.json").read_text(encoding="utf-8"))
    assert record["timeline"]["name"] == ABANDONED_SCRATCH


def test_releasing_the_hold_makes_the_scratch_sweepable_again(project_dir):
    """Promotion releases the hold as a side effect; after it, the
    sweep plans the timeline again. (The promote path itself is
    pinned below; this pins the release-to-sweepable transition.)"""
    holds.take_hold(str(project_dir), ABANDONED_SCRATCH,
                    awaiting="Reel 13 - something (final)")
    artefacts = scratch_pool()
    with pytest.raises(ProofRemovalRefused):
        plan_for(artefacts, ABANDONED_SCRATCH, str(project_dir))
    assert holds.release_hold(str(project_dir), ABANDONED_SCRATCH) is True
    plan = plan_for(artefacts, ABANDONED_SCRATCH, str(project_dir))
    assert plan["timeline"]["name"] == ABANDONED_SCRATCH


# ------------------ the lifecycle: promotion releases, the guard stays


class FakeRowItem:
    def __init__(self, name, start, end):
        self._name, self._start, self._end = name, start, end

    def GetName(self): return self._name
    def GetStart(self): return self._start
    def GetEnd(self): return self._end
    def GetDuration(self): return self._end - self._start


class FakeRowTimeline:
    def __init__(self, name, video=(), audio=(), markers=None):
        self._name = name
        self._rows = {"video": list(video), "audio": list(audio)}
        # Promotion reads the captain's markers off the retiring
        # timeline before it renames anything
        # (`library/tools/marker_carry.py`), so a fake that cannot
        # answer for its markers is a fake of a different object.
        self._markers = dict(markers or {})
        self.added_markers = []

    def GetName(self): return self._name
    def SetName(self, name):
        self._name = name
        return True
    def GetTrackCount(self, kind): return len(self._rows[kind])
    def GetTrackName(self, kind, index):
        return self._rows[kind][index - 1][0]
    def GetItemListInTrack(self, kind, index):
        return self._rows[kind][index - 1][1]
    def GetStartFrame(self): return 0
    def GetMarkers(self): return dict(self._markers)
    def AddMarker(self, frame, color, name, note, duration, custom=""):
        self.added_markers.append((frame, color, name, note))
        return True


class FakeResolveProject:
    def __init__(self, timelines):
        self.timelines = list(timelines)
        pool = MagicMock()
        pool.DeleteTimelines.side_effect = self._delete
        pool.GetCurrentFolder.return_value = None
        self._pool = pool
        self.deleted = []

    def _delete(self, timelines):
        for timeline in timelines:
            self.deleted.append(timeline.GetName())
            self.timelines.remove(timeline)
        return True

    def GetName(self): return "Mock Project"
    def GetMediaPool(self): return self._pool
    def GetTimelineCount(self): return len(self.timelines)
    def GetTimelineByIndex(self, index): return self.timelines[index - 1]


PROMOTE_FINAL = "Reel 09 - your-website-is-only-20-percent (final)"


def _promote(project, project_dir, staged_to_final, allow_drops=None):
    (project_dir / "pipeline_output" / "review"
     / "plan_provenance.json").write_text(json.dumps(
         {"built_reels": sorted(staged_to_final.values())}),
        encoding="utf-8")
    with patch("library.tools.resolve_locale.scriptapp_preserving_locale"), \
            patch("library.tools.reel_build.resolve_project_exactly",
                  return_value=project):
        from library.tools.reel_build import promote_staged_reels
        return promote_staged_reels(
            str(project_dir), "Mock Project", MASTER, staged_to_final,
            organise=False, allow_drops=allow_drops)


def _full_rows(extra=()):
    visual = [FakeRowItem(f"semantic {n}", n * 100, n * 100 + 40)
              for n in range(4)]
    return [
        ("Akshita", [FakeRowItem("Akshita A", 0, 131)]),
        ("Semantic", list(visual) + list(extra)),
    ]


def test_promote_releases_the_hold(project_dir):
    """The verified staging promotes cleanly and its hold goes with
    it - the sweep may collect the staging NAME afterwards because
    nothing under it exists any more."""
    from library.tools.reel_build import STAGING_SUFFIX
    staging = PROMOTE_FINAL + STAGING_SUFFIX
    retired = FakeRowTimeline(PROMOTE_FINAL, video=_full_rows())
    staged = FakeRowTimeline(staging, video=_full_rows())
    project = FakeResolveProject([retired, staged])
    holds.take_hold(str(project_dir), staging, awaiting=PROMOTE_FINAL,
                    reason="staged rebuild awaiting promotion",
                    taken_by="rebuild_reels_in_project")
    result = _promote(project, project_dir, {PROMOTE_FINAL: staging})
    assert result["promoted"] == [PROMOTE_FINAL]
    assert holds.read_holds(str(project_dir)) == {}
    # The staging took the final name; the timeline it replaced is
    # RETIRED to the archive rather than deleted
    # (`library/tools/reel_retirement.py`).
    assert sorted(t.GetName() for t in project.timelines) == [
        PROMOTE_FINAL, f"{PROMOTE_FINAL} (archived round 001)"]


def test_guard_still_refuses_a_held_lossy_staging_and_keeps_the_hold(project_dir):
    """How the two compose: the hold gets the staging TO the
    promotion; the row-diff guard (issue #925) still refuses a
    promotion that would LOSE a row - and the hold is retained,
    which is the safe direction on both halves."""
    from library.tools.reel_build import ReelBuildError, STAGING_SUFFIX
    staging = PROMOTE_FINAL + STAGING_SUFFIX
    retired = FakeRowTimeline(PROMOTE_FINAL, video=_full_rows())
    staged = FakeRowTimeline(
        staging, video=_full_rows()[:1])  # the Semantic row is gone
    project = FakeResolveProject([retired, staged])
    holds.take_hold(str(project_dir), staging, awaiting=PROMOTE_FINAL)
    with pytest.raises(ReelBuildError) as refused:
        _promote(project, project_dir, {PROMOTE_FINAL: staging})
    assert "Semantic" in str(refused.value)
    assert set(holds.read_holds(str(project_dir))) == {staging}
    assert sorted(t.GetName() for t in project.timelines) == sorted(
        [PROMOTE_FINAL, staging])


def test_discard_releases_the_hold(project_dir):
    """The gate-fail path deletes the refused staging and its hold
    goes with it - nothing is pending any more."""
    from library.tools.reel_build import discard_staged_reels
    from library.tools.reel_build import STAGING_SUFFIX
    staging = PROMOTE_FINAL + STAGING_SUFFIX
    project = FakeResolveProject([FakeRowTimeline(staging)])
    holds.take_hold(str(project_dir), staging, awaiting=PROMOTE_FINAL)
    discard_staged_reels(project, str(project_dir), [staging], None)
    assert [t.GetName() for t in project.timelines] == []
    assert holds.read_holds(str(project_dir)) == {}
