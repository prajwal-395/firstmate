"""Marker writes hold the Resolve instance; reads were already leased.

`library/tools/marker_feedback.py` carried exactly one lease construct
and it was on the read (`pull`). Every function that MUTATES a marker -
reply placement, clip-marker removal, resolution deletes, promotion
carry, decision stamping, master marking - wrote with no lease held
anywhere in its own chain. A lane holding an EXCLUSIVE lease to place
clips could run concurrently with another writer adding or deleting
markers under no lease at all.

This pins both halves: every mutation takes an EXCLUSIVE lease, and a
write attempted while another process holds the instance refuses instead
of proceeding - with the marker TEXT and colour read back FROM A
SEPARATE PROCESS, never a count and never an in-script read.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from library.tools import (
    marker_carry,
    marker_feedback,
    marker_payload,
    marker_resolution,
    timeline_decisions,
)
from library.tools import resolve_lock
from library.tools.execution import mark_master
from library.tools.master_markers import KIND_REEL_SPAN, WRITER as MASTER_WRITER

REPO_ROOT = Path(__file__).resolve().parent.parent

MUTATIONS = [
    marker_feedback.place_reply_marker,
    marker_feedback.place_reply_clip_marker,
    marker_feedback.remove_clip_marker,
    marker_resolution.delete_timeline_marker,
    marker_resolution.delete_clip_marker,
    marker_resolution.delete_marker_by_custom_data,
    marker_carry.place,
    timeline_decisions.stamp_timeline,
    mark_master.apply_markers,
    mark_master.clear_markers,
]


@pytest.mark.parametrize("mutate", MUTATIONS,
                         ids=lambda f: f.__module__ + "." + f.__name__)
def test_every_marker_mutation_holds_an_exclusive_lease(mutate):
    """The lease sits on the MUTATION, not on the caller remembering."""
    leased = getattr(mutate, "__resolve_lease__", None)
    assert leased is not None, (
        f"{mutate.__module__}.{mutate.__name__} mutates markers and "
        f"takes no lease - a caller holding EXCLUSIVE for placement "
        f"would still race it.")
    _purpose, exclusive, prefer = leased
    assert exclusive is True, "a marker mutation must exclude writers"
    assert prefer is False, "a person never presses these by hand"


# ── In-memory fakes: every legitimate producer still survives ────────

class _MemTimeline:
    """A timeline handle that stores markers in a dict, Resolve-shaped."""

    def __init__(self, name="Reel 08", start=1000, end=2000):
        self._name = name
        self._start = start
        self._end = end
        self.markers = {}

    def GetName(self):
        return self._name

    def GetStartFrame(self):
        return self._start

    def GetEndFrame(self):
        return self._end

    def GetMarkers(self):
        return dict(self.markers)

    def AddMarker(self, key, color, name, note, duration, *rest):
        if key in self.markers:
            return False
        self.markers[key] = {
            "color": color, "name": name, "note": note,
            "duration": duration,
            "customData": rest[0] if rest else ""}
        return True

    def DeleteMarkerAtFrame(self, key):
        return bool(self.markers.pop(key, None) is not None)

    def DeleteMarkerByCustomData(self, custom_data):
        for key, marker in list(self.markers.items()):
            if marker.get("customData") == custom_data:
                del self.markers[key]
                return True
        return False

    def UpdateMarkerCustomData(self, key, custom_data):
        if key not in self.markers:
            return False
        self.markers[key]["customData"] = custom_data
        return True


class _MemItem:
    """A clip handle: marker keys are SOURCE frames."""

    def __init__(self, name="clip", left=100, duration=50):
        self._name = name
        self._left = left
        self._duration = duration
        self.markers = {}

    def GetName(self):
        return self._name

    def GetLeftOffset(self):
        return self._left

    def GetDuration(self):
        return self._duration

    def GetStart(self):
        return 0

    def GetEnd(self):
        return self._duration

    def GetMarkers(self):
        return dict(self.markers)

    def AddMarker(self, key, color, name, note, duration, *rest):
        if key in self.markers:
            return False
        self.markers[key] = {
            "color": color, "name": name, "note": note,
            "duration": duration,
            "customData": rest[0] if rest else ""}
        return True

    def DeleteMarkerAtFrame(self, key):
        if key not in self.markers:
            return False
        del self.markers[key]
        return True


def _real_lease_env(monkeypatch, tmp_path):
    """Stand outside the session's sole-writer declaration, for real.

    The suite declares sole-writer because its Resolve is a mock
    (`tests/conftest.py`); a test ABOUT the lease must contend for a
    real one, in a redirected directory, or nesting proves nothing.
    """
    previous = resolve_lock._sole_writer_reason
    resolve_lock._sole_writer_reason = None
    monkeypatch.setenv(resolve_lock.LOCK_DIR_ENV, str(tmp_path))
    monkeypatch.delenv(resolve_lock.INHERIT_ENV, raising=False)
    return previous


def test_legitimate_producers_survive_their_own_lease(monkeypatch, tmp_path):
    """Every producer of a refused thing still gets through.

    Each writer below is called from INSIDE an outer EXCLUSIVE hold -
    the shape every already-correct caller has (resolve-axi's reply,
    promotion's carry, the render's stamp) - so a nested exclusive
    that deadlocked would fail here rather than in a lane.
    """
    previous = _real_lease_env(monkeypatch, tmp_path / "locks")
    try:
        with resolve_lock.resolve_lease("test: outer hold",
                                        exclusive=True, timeout=30):
            timeline = _MemTimeline()
            record = marker_feedback.place_reply_marker(
                timeline, 1500, "Green", "reply: done", "the words")
            assert record["name"] == "reply: done"

            item = _MemItem()
            clip_record = marker_feedback.place_reply_clip_marker(
                item, 100, "Green", "reply: done", "the words")
            assert clip_record["source_frame"] == 100
            assert marker_feedback.remove_clip_marker(item, 100) is True

            doomed = _MemTimeline()
            doomed.AddMarker(500, "Blue", "q", "note?", 1)
            outcome = marker_resolution.delete_timeline_marker(doomed, 500)
            assert outcome["removed"] is True

            doomed_item = _MemItem()
            doomed_item.AddMarker(100, "Blue", "q", "note?", 1)
            outcome = marker_resolution.delete_clip_marker(doomed_item, 100)
            assert outcome["removed"] is True

            custom = "record-id-1"
            custom_timeline = _MemTimeline()
            custom_timeline.AddMarker(501, "Blue", "q", "note?", 1, custom)
            outcome = marker_resolution.delete_marker_by_custom_data(
                custom_timeline, custom)
            assert outcome["removed"] is True

            fresh = _MemTimeline()
            failed = marker_carry.place(fresh, [{
                "to_frame": 12, "color": "Blue", "name": "kept",
                "note": "the words", "duration": 1, "custom_data": ""}])
            assert failed == []
            assert fresh.markers[1012]["note"] == "the words"

            stamped = _MemTimeline()
            stamped.AddMarker(150, "Blue", "q", "note?", 1)
            ledger = {"placements": [{
                "track": "V1", "timeline_in_frame": 100,
                "timeline_out_frame": 200, "step": "render",
                "decision_id": "d1", "basis": "b", "locator": "",
                "label": "clip", "routes": {}}]}
            report = timeline_decisions.stamp_timeline(stamped, ledger)
            assert report.markers_stamped == 1
            assert report.records_written == 1

            envelope = marker_payload.parse("")
            marker_payload.merge_record(envelope, {
                "kind": KIND_REEL_SPAN, "writer": MASTER_WRITER,
                "writer_version": 1, "id": "reel_span_V1_0",
                "at": marker_payload.utc_now()})
            master = _MemTimeline(name="Master")
            journal = mark_master.apply_markers(
                {"master": master, "existing": {}, "markers": [
                    SimpleNamespace(
                        frame=10, colour="Yellow", name="Reel 08",
                        note="covers 0..99", duration=100,
                        custom_data=marker_payload.dumps(envelope))]},
                str(tmp_path / "journal.json"))
            assert len(journal["written"]) == 1
            assert mark_master.clear_markers(master) == [10]
    finally:
        resolve_lock._sole_writer_reason = previous


# ── Across processes: a contended write refuses, then lands ──────────

_PROBE_TIMEOUT = 60.0


class _FileTimeline(_MemTimeline):
    """A timeline handle shared across processes through one JSON file."""

    def __init__(self, store: Path):
        super().__init__()
        self._store = store

    def _load(self):
        try:
            body = json.loads(self._store.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return {int(key): value for key, value in body.items()}

    def _save(self, markers):
        self._store.write_text(
            json.dumps({str(key): value for key, value in markers.items()}),
            encoding="utf-8")

    def GetMarkers(self):
        return self._load()

    def AddMarker(self, key, color, name, note, duration, *rest):
        markers = self._load()
        if key in markers:
            return False
        markers[key] = {
            "color": color, "name": name, "note": note,
            "duration": duration,
            "customData": rest[0] if rest else ""}
        self._save(markers)
        return True


def _probe(argv, store: Path, lock_dir: Path, extra_env=None):
    """Run this file's probe entry point as a SEPARATE process."""
    env = dict(os.environ)
    env[resolve_lock.LOCK_DIR_ENV] = str(lock_dir)
    # A script's directory, not the cwd, leads `sys.path` - point the
    # probe at this checkout explicitly.
    env["PYTHONPATH"] = str(REPO_ROOT) + os.pathsep + env.get(
        "PYTHONPATH", "")
    # A lane is not our child: it must contend, never inherit.
    env.pop(resolve_lock.INHERIT_ENV, None)
    env["MARKER_LEASE_PROBE_STORE"] = str(store)
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        [sys.executable, str(Path(__file__)), *argv],
        cwd=str(REPO_ROOT), env=env, capture_output=True, text=True,
        timeout=_PROBE_TIMEOUT, check=False)


def _probe_main(argv):
    """`pytest tests/test_marker_writes_hold_lease.py write|read`."""
    store = Path(os.environ["MARKER_LEASE_PROBE_STORE"])
    timeline = _FileTimeline(store)
    if argv == ["write"]:
        try:
            record = marker_feedback.place_reply_marker(
                timeline, 1500, "Green", "reply: done", "the words")
        except resolve_lock.ResolveBusy as busy:
            print(json.dumps({"refused": str(busy)}))
            return 2
        print(json.dumps({"placed": record}))
        return 0
    if argv == ["read"]:
        markers = timeline.GetMarkers()
        found = [marker for marker in markers.values()
                 if marker.get("name") == "reply: done"]
        if not found:
            print(json.dumps({"absent": True}))
            return 3
        print(json.dumps(found[0]))
        return 0
    raise SystemExit(f"unknown probe {argv!r}")


@pytest.mark.heavy
def test_contended_marker_write_refuses_then_lands(tmp_path):
    """While EXCLUSIVE is held elsewhere the write refuses; after, it
    lands - and a SEPARATE process reads back its TEXT and colour."""
    store = tmp_path / "markers.json"
    store.write_text("{}", encoding="utf-8")
    lock_dir = tmp_path / "locks"
    lock_dir.mkdir()
    child_env = {resolve_lock.TIMEOUT_ENV: "2"}

    previous = resolve_lock._sole_writer_reason
    resolve_lock._sole_writer_reason = None
    os.environ[resolve_lock.LOCK_DIR_ENV] = str(lock_dir)
    try:
        with resolve_lock.resolve_lease("test: a lane placing clips",
                                        exclusive=True, timeout=30):
            refused = _probe(["write"], store, lock_dir, child_env)
    finally:
        resolve_lock._sole_writer_reason = previous
        os.environ.pop(resolve_lock.LOCK_DIR_ENV, None)
    assert refused.returncode == 2, (
        f"a marker write under a foreign EXCLUSIVE lease must refuse, "
        f"not proceed: {refused.stdout} {refused.stderr}")
    assert json.loads(store.read_text(encoding="utf-8")) == {}, (
        "the refused write left a marker behind")

    landed = _probe(["write"], store, lock_dir, child_env)
    assert landed.returncode == 0, f"{landed.stdout} {landed.stderr}"

    readback = _probe(["read"], store, lock_dir, child_env)
    assert readback.returncode == 0, f"{readback.stdout} {readback.stderr}"
    marker = json.loads(readback.stdout)
    assert marker["note"] == "the words"
    assert marker["color"] == "Green"


if __name__ == "__main__":
    raise SystemExit(_probe_main(sys.argv[1:]))
