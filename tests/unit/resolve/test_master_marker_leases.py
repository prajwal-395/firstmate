"""Master marker writes declare the master timeline.

`mark_master.apply_markers` is the only write the master takes, and a
touch-up restructuring the master moves footage under markers placed
mid-run. Every test names a defect it catches - a case that fails on
origin/main, where the marker write takes no lease.
"""
import threading
import time
from types import SimpleNamespace

import pytest

from library.tools.execution.mark_master import apply_markers
from library.tools.resource_leases import (
    LeaseDeclaration,
    ResourceRef,
    resource_lease,
)
from library.tools.resolve_lock import assume_sole_writer
from tests.resolve_double import FakeTimeline


@pytest.fixture
def lock_dir(tmp_path, monkeypatch):
    """Leases of our own, never the machine's.

    The redirect `resolve_lock` itself uses - the resource lease layer
    shares that one home.
    """
    directory = tmp_path / "locks"
    directory.mkdir()
    monkeypatch.setenv("PIPELINE_RESOLVE_LOCK_DIR", str(directory))
    return directory


@pytest.fixture
def sole_writer():
    with assume_sole_writer("test: fake master has no instance to contend for"):
        yield


def _marker(frame, name="reel coverage"):
    return SimpleNamespace(
        frame=frame, colour="Blue", name=name, note="reels 1-2",
        duration=10, custom_data="")


def _plan(master, frames=(100, 200)):
    return {"master": master,
            "markers": [_marker(frame) for frame in frames],
            "existing": {}}


def test_marking_the_master_places_markers_and_journals_them(
        lock_dir, sole_writer, tmp_path):
    """The wired path still writes: markers land, the journal names them.

    Defect caught: a lease wrapped around the wrong section (or a
    declaration that refuses its own write) turns the master's only
    write into a refusal, and the master goes unmarked.
    """
    master = FakeTimeline("Master")
    journal_path = str(tmp_path / "journal.json")
    journal = apply_markers(_plan(master), journal_path)
    assert sorted(master.GetMarkers()) == [100, 200]
    assert [entry["frame"] for entry in journal["written"]] == [100, 200]
    assert journal["timeline"] == "Master"


def test_marking_waits_for_a_touchup_restructuring_the_master(
        lock_dir, sole_writer, tmp_path):
    """A marker write does not land mid-touch-up - it waits for the release.

    Defect caught: with no declaration the marker write runs beside a
    touch-up holding the master EXCLUSIVE, and the markers are placed
    by footage overlap that the restructure is moving - they land where
    the footage was, not where it is. On origin/main `apply_markers`
    returns before the holder releases.
    """
    master = FakeTimeline("Master")
    touchup = LeaseDeclaration(
        write=frozenset({ResourceRef("timeline", "Master")}),
        exclusive=frozenset({ResourceRef("timeline", "Master")}),
    )
    released_at: list = []

    def restructure():
        with resource_lease(touchup, "touch up Master",
                           heartbeat_interval=30.0):
            time.sleep(2.0)
        released_at.append(time.monotonic())

    holder = threading.Thread(target=restructure)
    holder.start()
    time.sleep(0.3)  # let the touch-up acquire first
    journal = apply_markers(_plan(master), str(tmp_path / "journal.json"))
    returned_at = time.monotonic()
    holder.join(30.0)
    assert released_at, "the touch-up never released"
    assert returned_at >= released_at[0], \
        "the marker write returned mid-touch-up"
    assert sorted(master.GetMarkers()) == [100, 200]
    assert len(journal["written"]) == 2
