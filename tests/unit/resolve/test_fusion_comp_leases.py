"""Batch Fusion passes declare the compositions they import.

Two batch passes importing the same composition would interleave
`DeleteFusionCompByName` and `ImportFusionComp` on one clip; the batch
loop declares each entry's comps first. Every test names a defect it
catches - a case that fails on origin/main, where the batch takes no
lease.
"""
import threading
import time

import pytest

import library.tools.execution.apply_fusion_comps as afc
from library.tools.resource_leases import (
    LeaseDeclaration,
    ResourceRef,
    resource_lease,
)
from library.tools.resolve_lock import assume_sole_writer


@pytest.fixture
def lock_dir(tmp_path, monkeypatch):
    """Leases of our own, never the machine's."""
    directory = tmp_path / "locks"
    directory.mkdir()
    monkeypatch.setenv("PIPELINE_RESOLVE_LOCK_DIR", str(directory))
    return directory


@pytest.fixture
def sole_writer():
    with assume_sole_writer("test: no Resolve instance to contend for"):
        yield


def _manifest(*labels, generators=()):
    effects = {"per_clip": {label: {} for label in labels}}
    if generators:
        effects["generator_overlays"] = [
            {"effect_name": name} for name in generators]
    return {"fusion_effects": effects}


def _wired(monkeypatch, applied):
    """The batch loop with Resolve and the per-reel pass stubbed."""
    import library.tools.resolve_locale as locale
    monkeypatch.setattr(locale, "scriptapp_preserving_locale",
                        lambda dvr, name: object())
    monkeypatch.setattr(
        afc, "apply_fusion_comps_with_resolve",
        lambda resolve, manifest, project_folder, expected_project=None,
        expected_timeline=None, step_id="render": applied.append(
            expected_timeline) or True)


def test_batch_entries_on_different_timelines_all_apply(
        lock_dir, sole_writer, monkeypatch):
    """The wired loop still runs: every entry's pass runs, named by reel.

    Defect caught: a declaration that refuses its own entries turns
    the batch into a refusal, and no reel's comps reach the timeline.
    """
    applied = []
    _wired(monkeypatch, applied)
    results = afc.apply_fusion_comps_batch(
        [(_manifest("clip_0"), "Reel 09"),
         (_manifest("clip_0"), "Reel 10")], "")
    assert results == {"Reel 09": True, "Reel 10": True}
    assert sorted(applied) == ["Reel 09", "Reel 10"]


def test_a_batch_entry_waits_for_a_pass_on_the_same_comp(
        lock_dir, sole_writer, monkeypatch):
    """Two passes, one composition: the batch entry waits for the release.

    Defect caught: with no declaration two batch passes import
    different comps onto one clip at once - deletes and imports
    interleave and the clip keeps whichever landed last, unrecorded.
    On origin/main the batch returns before the holder releases.
    """
    applied = []
    _wired(monkeypatch, applied)
    other_pass = LeaseDeclaration(
        write=frozenset({ResourceRef("fusion_comp", "Reel 09:clip_0")}),
        exclusive=frozenset({ResourceRef("fusion_comp", "Reel 09:clip_0")}),
    )
    released_at: list = []

    def import_first():
        with resource_lease(other_pass, "comp Reel 09 clip_0 first",
                           heartbeat_interval=30.0):
            time.sleep(2.0)
        released_at.append(time.monotonic())

    holder = threading.Thread(target=import_first)
    holder.start()
    time.sleep(0.3)  # let the first pass acquire first
    results = afc.apply_fusion_comps_batch(
        [(_manifest("clip_0"), "Reel 09")], "")
    returned_at = time.monotonic()
    holder.join(30.0)
    assert released_at, "the first pass never released"
    assert returned_at >= released_at[0], \
        "the batch returned mid-import"
    assert results == {"Reel 09": True}
    assert applied == ["Reel 09"]
