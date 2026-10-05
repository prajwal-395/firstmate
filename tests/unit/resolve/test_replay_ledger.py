"""The standalone ledger replay: a recorded ledger applied to a timeline
directly, without a build.

Each test names the defect it catches (docs/TEST_AUTHORING.py):

- a replay that does not produce the same read-back as the build's
  replay of the same rows - the editor tries a recorded change and sees
  a different result than the build would have given;
- a replay that is not journaled - the editor cannot undo it;
- a replay that overwrites the captain's manual edits - an unreplayable
  row is silently dropped instead of refusing;
- a replay that is too slow to be useful - a 10-row replay that takes
  longer than a build would.

Driven against fakes; no Resolve, no render, no model.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from library.tools import edit_ledger  # noqa: E402
from library.tools import replay_ledger  # noqa: E402
from library.tools.edit_ledger import EditLedgerError  # noqa: E402
from library.tools.project_layout import ProjectLayout  # noqa: E402


# ── Fakes ─────────────────────────────────────────────────────────────


class _Graph:
    def __init__(self, nodes=2):
        self.nodes = nodes
        self.luts = {}

    def GetNumNodes(self):
        return self.nodes

    def SetLUT(self, node, lut):
        self.luts[node] = lut
        return True

    def GetLUT(self, node):
        return self.luts.get(node, "")

    def ApplyGradeFromDRX(self, path, _mode):
        self.drx = path
        return True


class _Item:
    _MISSING = object()

    def __init__(self, graph=_MISSING):
        self._graph = _Graph() if graph is _Item._MISSING else graph

    def GetNodeGraph(self):
        return self._graph


class _Timeline:
    def __init__(self, audio_tracks=2):
        self.audio_tracks = audio_tracks
        self.isolation = {}

    def GetTrackCount(self, kind):
        assert kind == "audio"
        return self.audio_tracks

    def SetVoiceIsolationState(self, track, state):
        self.isolation[track] = dict(state)
        return True

    def GetVoiceIsolationState(self, track):
        return dict(self.isolation.get(track, {}))


def _spans():
    return [{"master": (10.0, 20.0)}, {"master": (20.0, 30.0)}]


def _speech():
    segs = []
    cursor = 10.0
    for word in ["hello", "there", "ive", "quit", "every", "single",
                 "day", "for", "years", "and", "back", "again"]:
        segs.append({"word": word, "start": cursor, "end": cursor + 0.3,
                     "timed": True})
        cursor += 0.4
    return {"segments": [{"words": segs}]}


def _isolate(track=1, amount=60, reel="Reel 09 - hook"):
    return {"op": "voice_isolation", "anchor": {"kind": "reel"},
            "reel": reel, "params": {"track": track, "amount": amount},
            "stated_by": "requester",
            "reason": "remove the background noise from the host mic"}


def _lut(phrase="ive quit every single day", reel="Reel 09 - hook",
         lut="Film Looks/Kodak 2383", node=1):
    return {"op": "clip_lut",
            "anchor": {"kind": "words", "phrase": phrase},
            "reel": reel, "params": {"lut": lut, "node": node},
            "stated_by": "requester",
            "reason": "kodak print feel on the hook"}


def _grade(reel="Reel 09 - hook", lut="Film Looks/Kodak 2383"):
    return {"op": "grade", "anchor": {"kind": "reel"},
            "reel": reel, "params": {"lut": lut, "node": 1},
            "stated_by": "requester", "reason": "print grade"}


# ── Fixtures ─────────────────────────────────────────────────────────


def _project(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    ProjectLayout(str(project)).ensure()
    return project


def _mock_journal(monkeypatch):
    """Mock the undo journal so the test does not read a real timeline."""
    from library.tools import undo_journal as _journal

    entries = []

    def _open_entry(project_folder, **kwargs):
        entry = {"id": f"test-entry-{len(entries)}", "status": "open"}
        entries.append(entry)
        return entry

    def _close_entry(project_folder, entry, **kwargs):
        entry["status"] = "applied"

    def _fail_entry(project_folder, entry, why):
        entry["status"] = "failed"
        entry["failed"] = why

    monkeypatch.setattr(_journal, "open_entry", _open_entry)
    monkeypatch.setattr(_journal, "close_entry", _close_entry)
    monkeypatch.setattr(_journal, "fail_entry", _fail_entry)
    return entries


# ── Fidelity: the standalone replay matches the build's replay ───────


def test_a_three_row_ledger_replays_onto_a_scratch_timeline(tmp_path,
                                                            monkeypatch):
    """The standalone replay produces the same read-back as the build's
    replay of the same rows. The defect: an editor replays a recorded
    change and sees a different result than the build would have given.
    """
    _mock_journal(monkeypatch)
    project = _project(tmp_path)
    rows = [_isolate(), _lut(), _grade()]
    for row in rows:
        edit_ledger.record_row(str(project), row)

    timeline = _Timeline()
    items = [_Item(), _Item()]
    spans = _spans()
    transcript = _speech()

    receipt = replay_ledger.replay_ledger(
        str(project), reel_name="Reel 09 - hook", timeline=timeline,
        spans=spans, transcript=transcript,
        item_for_span=items.__getitem__)

    build_report = edit_ledger.replay_on_timeline(
        "Reel 09 - hook", rows, spans, transcript, timeline,
        item_for_span=items.__getitem__, reel_name="Reel 09 - hook")

    assert receipt["applied"] == build_report["applied"]
    assert receipt["unreplayable"] == build_report["unreplayable"]
    assert receipt["journal"] != ""
    assert len(receipt["applied"]) == 4
    assert timeline.GetVoiceIsolationState(1) == {"isEnabled": True,
                                                 "amount": 60}
    assert all(item.GetNodeGraph().GetLUT(1) ==
               "Film Looks/Kodak 2383" for item in items)


def test_a_replay_is_journalled_so_undo_can_reverse_it(tmp_path,
                                                        monkeypatch):
    """The replay writes an undo journal entry. The defect: a replay
    that is not journaled cannot be undone - the editor's only way
    back is another full build.
    """
    entries = _mock_journal(monkeypatch)
    project = _project(tmp_path)
    edit_ledger.record_row(str(project), _isolate())

    timeline = _Timeline()
    receipt = replay_ledger.replay_ledger(
        str(project), reel_name="Reel 09 - hook", timeline=timeline,
        spans=_spans(), transcript=_speech(),
        item_for_span=lambda _i: _Item())

    assert len(entries) == 1
    assert entries[0]["status"] == "applied"
    assert receipt["journal"] == entries[0]["id"]


def test_an_unreplayable_row_refuses_the_replay(tmp_path, monkeypatch):
    """F-17: an unreplayable row whose loss is real REFUSES the replay.
    The defect: a replay that continues with only a stderr note paints
    over the captain's edit.
    """
    _mock_journal(monkeypatch)
    project = _project(tmp_path)
    edit_ledger.record_row(str(project), _isolate(track=4))

    timeline = _Timeline(audio_tracks=1)
    with pytest.raises(replay_ledger.ReplayLedgerRefused,
                       match="cannot be replayed"):
        replay_ledger.replay_ledger(
            str(project), reel_name="Reel 09 - hook", timeline=timeline,
            spans=_spans(), transcript=_speech(),
            item_for_span=lambda _i: _Item())


def test_a_routine_reel_scope_does_not_refuse_the_replay(tmp_path,
                                                         monkeypatch):
    """A row scoped to another reel is routine and does not refuse.
    The defect: a replay that refuses on every row scoped elsewhere
    would refuse on every multi-reel project.
    """
    _mock_journal(monkeypatch)
    project = _project(tmp_path)
    edit_ledger.record_row(str(project), _isolate(reel="Reel 28 - nail"))
    edit_ledger.record_row(str(project), _isolate())

    timeline = _Timeline()
    receipt = replay_ledger.replay_ledger(
        str(project), reel_name="Reel 09 - hook", timeline=timeline,
        spans=_spans(), transcript=_speech(),
        item_for_span=lambda _i: _Item())

    assert len(receipt["applied"]) == 1
    assert receipt["unreplayable"] == []


# ── Performance: a 10-row replay lands in < 30 s ──────────────────────


def test_a_ten_row_replay_lands_in_under_thirty_seconds(tmp_path,
                                                        monkeypatch):
    """A 10-row replay is fast enough to be useful. The defect: a replay
    that takes longer than a build would is not a standalone operation -
    it is a build by another name.
    """
    _mock_journal(monkeypatch)
    project = _project(tmp_path)
    rows = []
    for i in range(10):
        rows.append(_isolate(track=i + 1, amount=50))
    for row in rows:
        edit_ledger.record_row(str(project), row)

    timeline = _Timeline(audio_tracks=10)
    items = [_Item(), _Item()]

    started = time.monotonic()
    receipt = replay_ledger.replay_ledger(
        str(project), reel_name="Reel 09 - hook", timeline=timeline,
        spans=_spans(), transcript=_speech(),
        item_for_span=items.__getitem__)
    elapsed = time.monotonic() - started

    assert len(receipt["applied"]) == 10
    assert elapsed < 30.0
