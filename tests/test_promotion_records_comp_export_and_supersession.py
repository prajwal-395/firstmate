"""One promotion records BOTH the comp export and the supersession.

`record_reel_promotion` is now the shared call site of two best-effort
promotion records: PR 1296's per-reel Fusion comp export
(`reel_fusion_comps.export_built_reels`) and G6's snapshot
supersession (`plan_provenance.record_snapshot_supersession`).  Neither
existed when the other landed and nobody has exercised the pairing, so
this drives one promotion through both and asserts each half's record -
plus that neither half's failure skips the other.

``library/tools/versions/store.py``.
"""

from __future__ import annotations

import json
import sys

from library.tools.versions import store as bvc
from library.tools.plan_provenance import (
    built_from_snapshot,
    is_snapshot_superseded,
)

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

