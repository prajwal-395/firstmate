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
from unittest.mock import MagicMock, patch

import pytest
from tests.promotion_test_helpers import no_a_roll_track_plans

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
def mock_dvr(stub_resolve_script):
    # Stubbed through the shared fixture: `patch.dict` on
    # `sys.modules` restores the WHOLE dict and so evicts every
    # module first imported inside it (tests/conftest.py).
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


def test_a_corrupt_holds_file_refuses_rather_than_reading_empty(project_dir):
    path = project_dir / "pipeline_output" / "review" / "staging_holds.json"
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(holds.HoldsUnreadable, match="cannot be read"):
        holds.read_holds(str(project_dir))
    artefacts = scratch_pool()
    with pytest.raises(holds.HoldsUnreadable):
        plan_for(artefacts, ABANDONED_SCRATCH, str(project_dir))


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
            organise=False, allow_drops=allow_drops,
            track_plans=no_a_roll_track_plans(staged_to_final))


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
    # DELETED by default - one timeline per reel, nothing archived
    # (`library/tools/reel_retirement.py`).
    assert sorted(t.GetName() for t in project.timelines) == [
        PROMOTE_FINAL]
    assert project.deleted == [f"{PROMOTE_FINAL} (pre-rebuild backup)"]


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


# ── Pending promotions report themselves (Reel 16, 2026-09-19) ──────
# A build that stages but never promotes left its staging protected
# and invisible: the holds file knew, and nothing ever read it as
# unfinished work. These pin the reader a run-end report is built
# on: oldest first, empty when nothing is pending, loud about what
# and how long - and a corrupt holds file still refuses rather than
# reading as "nothing pending".

def _backdate(project_dir, staging, taken_at):
    from pathlib import Path
    path = Path(holds.holds_path_for(str(project_dir)))
    doc = json.loads(path.read_text(encoding="utf-8"))
    doc["holds"][staging]["taken_at"] = taken_at
    path.write_text(json.dumps(doc, indent=2), encoding="utf-8")


def test_pending_promotions_lists_stagings_oldest_first(project_dir):
    from library.tools.reel_build import STAGING_SUFFIX
    first = "Reel 16 - why-ai-trusts-one-brand-over-another" + STAGING_SUFFIX
    second = "Reel 04 - google-gave-a-list-from-2023" + STAGING_SUFFIX
    holds.take_hold(str(project_dir), first,
                    awaiting="Reel 16 - why-ai-trusts-one-brand-over-another",
                    taken_by="rebuild_reels_in_project")
    holds.take_hold(str(project_dir), second,
                    awaiting="Reel 04 - google-gave-a-list-from-2023",
                    taken_by="rebuild_reels_in_project")
    _backdate(project_dir, first, "2026-09-19T15:36:20+00:00")
    _backdate(project_dir, second, "2026-09-19T20:20:33+00:00")
    pending = holds.pending_promotions(str(project_dir))
    assert [row["staging"] for row in pending] == [first, second]
    assert pending[0]["awaiting"] == \
        "Reel 16 - why-ai-trusts-one-brand-over-another"
    assert pending[0]["taken_by"] == "rebuild_reels_in_project"
    assert pending[0]["age"] not in ("", "unknown age")


def test_concurrent_takes_keep_every_hold(project_dir):
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
    folder = str(project_dir)
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


class GhostFakeTimeline:
    def __init__(self, name):
        self._name = name

    def GetName(self):
        return self._name


class GhostFakeProject:
    """A live project holding exactly ONE Reel 26 timeline: the plain
    final. Both builds are gone - plausibly a whole-batch discard -
    and their hold records were never retired."""

    def __init__(self, names):
        self._timelines = [GhostFakeTimeline(name) for name in names]

    def GetTimelineCount(self):
        return len(self._timelines)

    def GetTimelineByIndex(self, index):
        return self._timelines[index - 1]


def _take_ghost_and_live_holds(project_dir):
    from library.tools.reel_build import STAGING_SUFFIX
    live_staging = "Reel 04 - google-gave-a-list-from-2023" + STAGING_SUFFIX
    holds.take_hold(str(project_dir), GHOST_MFA, awaiting=GHOST_FINAL,
                    taken_by="rebuild_reels_in_project")
    holds.take_hold(str(project_dir), GHOST_FIXES, awaiting=GHOST_FINAL,
                    taken_by="rebuild_reels_in_project")
    holds.take_hold(str(project_dir), live_staging,
                    awaiting="Reel 04 - google-gave-a-list-from-2023",
                    taken_by="rebuild_reels_in_project")
    return live_staging


def test_pending_promotions_reports_ghosts_as_stale_not_pending(project_dir):
    """All three shapes at once, against an explicit name listing."""
    live_staging = _take_ghost_and_live_holds(project_dir)
    # The trap's shape, stated plainly: each ghost name carries the
    # living final's whole text as a prefix.
    assert GHOST_MFA.startswith(GHOST_FINAL)
    assert GHOST_FIXES.startswith(GHOST_FINAL)
    rows = holds.pending_promotions(
        str(project_dir), timeline_names=[GHOST_FINAL, live_staging])
    assert {row["staging"]: row["status"] for row in rows} == {
        GHOST_MFA: "stale",
        GHOST_FIXES: "stale",
        live_staging: "pending",
    }
    report = holds.report_pending(
        str(project_dir), timeline_names=[GHOST_FINAL, live_staging])
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
