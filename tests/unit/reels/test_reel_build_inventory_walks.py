"""Fewer Resolve walks for the same answers.

The one-reel build walked the whole project once per question it asked:
the debris check enumerated every timeline twice (stagings, then
backups), the pending-promotion census walked it again for names, and
the promotion refusal and build-end records each walked it twice more.
Each cut below removes a walk without changing what the build decides:

- `timelines_by_name` answers several wanted sets off one enumeration;
- `pending_report_from_inventory` reconciles holds off an inventory the
  build already took instead of a fresh name census;
- the refusal and reconcile/finish records land on one read.

These tests pin the walk counts and the answer equality, not timings:
a timing assertion would be flaky on a shared machine and would pass
without proving the duplicate work is gone.
"""

from __future__ import annotations

from library.tools import reel_build
from library.tools import staging_holds as holds


class _Timeline:
    def __init__(self, name, calls, broken=False):
        self._name = name
        self._calls = calls
        self._broken = broken

    def GetName(self):
        self._calls["GetName"] += 1
        if self._broken:
            raise RuntimeError("unreadable row")
        return self._name


class _Project:
    def __init__(self, names, calls, broken=()):
        self._timelines = [_Timeline(name, calls, name in broken)
                           for name in names]
        self._calls = calls

    def GetTimelineCount(self):
        self._calls["GetTimelineCount"] += 1
        return len(self._timelines)

    def GetTimelineByIndex(self, index):
        self._calls["GetTimelineByIndex"] += 1
        return self._timelines[index - 1]


def _project(names, broken=()):
    calls = {"GetTimelineCount": 0, "GetTimelineByIndex": 0, "GetName": 0}
    return _Project(names, calls, broken), calls


NAMES = ["Podcast master", "Reel 26 - question (v22 restore staging)",
         "Reel 26 - question", "Reel 09", "Reel 26 - question (backup)"]


def test_timelines_by_name_walks_the_project_once():
    project, calls = _project(NAMES)
    found = reel_build.timelines_by_name(
        project, {"Reel 26 - question (v22 restore staging)",
                  "Reel 26 - question (backup)", "Reel 09"})
    assert calls["GetTimelineCount"] == 1
    assert calls["GetTimelineByIndex"] == len(NAMES)
    assert sorted(found) == ["Reel 09", "Reel 26 - question (backup)",
                             "Reel 26 - question (v22 restore staging)"]
    # Exact names, never a prefix: the plain staging is not the backup.
    assert [timeline.GetName() for timeline in
            found["Reel 26 - question (v22 restore staging)"]] == [
        "Reel 26 - question (v22 restore staging)"]


def test_timelines_by_name_matches_timelines_to_replace():
    project, _ = _project(NAMES)
    wanted = {"Reel 09", "Reel 26 - question (backup)",
              "a timeline that is not there"}
    by_name = reel_build.timelines_by_name(project, wanted)
    project2, _ = _project(NAMES)
    legacy = reel_build.timelines_to_replace(project2, wanted)
    assert ({name for name, rows in by_name.items() for _ in rows}
            == {timeline.GetName() for timeline in legacy} == set(wanted)
            - {"a timeline that is not there"})


def test_timelines_by_name_skips_an_unreadable_row():
    project, calls = _project(NAMES, broken={"Reel 09"})
    found = reel_build.timelines_by_name(project, set(NAMES))
    assert "Reel 09" not in found
    assert "Reel 26 - question" in found
    assert calls["GetTimelineCount"] == 1


def test_pending_report_from_inventory_matches_the_live_census(tmp_path):
    holds.take_hold(str(tmp_path), "Reel 26 - question (staging)",
                    awaiting="Reel 26 - question")
    holds.take_hold(str(tmp_path), "a deleted staging",
                    awaiting="Reel 09")
    names = ["Reel 26 - question (staging)", "Reel 26 - question"]
    inventory = [{"name": name, "unique_id": f"id-{index}", "settings": {}}
                 for index, name in enumerate(names)]

    pending, report = reel_build.pending_report_from_inventory(
        str(tmp_path), inventory,
        owned_staging_names={"Reel 26 - question (staging)"})

    direct_pending = holds.pending_promotions(
        str(tmp_path), timeline_names=names)
    direct_report = holds.report_pending(
        str(tmp_path), timeline_names=names,
        owned_staging_names={"Reel 26 - question (staging)"})
    assert pending == direct_pending
    assert report == direct_report
    assert [row["status"] for row in pending] == ["pending", "stale"]


def test_pending_report_from_inventory_ignores_non_dict_rows(tmp_path):
    pending, report = reel_build.pending_report_from_inventory(
        str(tmp_path), [{"name": "only row"}, None, "not a dict", {}])
    assert pending == []
    assert report == ""


def test_pending_report_from_inventory_without_a_holds_file(tmp_path):
    pending, report = reel_build.pending_report_from_inventory(
        str(tmp_path), [{"name": "Reel 26 - question"}])
    assert (pending, report) == ([], "")


def test_promotion_refusal_records_on_a_single_inventory_read(
        tmp_path, monkeypatch):
    """The concurrent-edit refusal path reads the project once, not twice.

    `promote_staged_reels` used to take one full inventory for the
    refusal attribution and a second for the closing record, with no
    write between them. Both records now land on the same read; the
    refusal still names the changed timeline.
    """
    from unittest.mock import patch

    from tests.promotion_test_helpers import no_a_roll_track_plans
    from tests.resolve_double import FakeProject, FakeTimeline

    locks = tmp_path / "locks"
    locks.mkdir()
    monkeypatch.setenv("PIPELINE_RESOLVE_LOCK_DIR", str(locks))
    (tmp_path / "pipeline_output" / "review").mkdir(parents=True)

    final = "Reel 09 - q (final)"
    staging = final + " (rebuild staging)"
    project = FakeProject("Mock Project", [
        FakeTimeline(staging, uid="staging-1"),
        FakeTimeline(final, uid="final-live")])

    calls = {"count": 0}
    from library.tools import reel_replace_guard as guard

    real_inventory = guard.timeline_inventory
    while hasattr(real_inventory, "__wrapped__"):
        real_inventory = real_inventory.__wrapped__

    def counting_inventory(proj):
        calls["count"] += 1
        return real_inventory(proj)

    with patch.object(guard, "timeline_inventory",
                      side_effect=counting_inventory), \
        patch("library.tools.reel_build._connect_resolve_project",
              return_value=project):
        import pytest

        with pytest.raises(reel_build.ReelBuildError, match="final-live"):
            reel_build.promote_staged_reels(
                str(tmp_path), "Mock Project", "Master",
                {final: staging}, organise=False,
                track_plans=no_a_roll_track_plans({final: staging}),
                timeline_inventory_before=[{
                    "name": final, "unique_id": "final-stale",
                    "settings": {}}],
                staged_timeline_ids={staging: "staging-1"})
    # One read on entry plus one shared refusal read - never one per
    # record on the refusal path.
    assert calls["count"] == 2

