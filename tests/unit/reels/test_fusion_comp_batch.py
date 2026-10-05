"""The batch Fusion comp pass runs ONE subprocess for multiple reels.

The defect this prevents: each reel's Fusion comp pass ran in its own
subprocess, paying 17.0-63.7s of FIXED overhead per reel (the Resolve
connection + project/timeline resolution). For a 26-reel build that is
7-27 minutes of Resolve hold. The batch pass connects once and applies
every reel's comps in a single subprocess.
"""

import json
import os
import pathlib
import subprocess
import sys
from unittest.mock import patch, MagicMock

import pytest

from library.tools import reel_look, resolve_lock


def _manifest(timeline_name):
    return {
        "fusion_effects": {"per_clip": {"clip0": {"zoom": 1.1}}},
        "tracks": {
            "V1": {"clips": [{"label": "clip0", "source_file": "/media/clip0.mov"}]}
        },
    }


def test_apply_comps_batch_runs_one_subprocess_for_multiple_reels(tmp_path):
    """Three reels' comps go in ONE subprocess, not three.

    The per-reel subprocess was the fixed overhead this change removes.
    """
    entries = [
        (_manifest("Reel 01"), "Reel 01"),
        (_manifest("Reel 02"), "Reel 02"),
        (_manifest("Reel 03"), "Reel 03"),
    ]

    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        result = MagicMock()
        result.returncode = 0
        result.stdout = json.dumps(
            {
                "Reel 01": True,
                "Reel 02": True,
                "Reel 03": True,
            }
        )
        result.stderr = ""
        return result

    with patch("library.tools.reel_look.subprocess.run", side_effect=fake_run):
        results = reel_look.apply_comps_batch(entries, str(tmp_path), "Test Project")

    assert len(calls) == 1
    assert results == {"Reel 01": True, "Reel 02": True, "Reel 03": True}


def test_apply_comps_batch_writes_all_manifests_to_disk(tmp_path):
    """Every reel's manifest is written before the subprocess runs."""
    entries = [
        (_manifest("Reel 01"), "Reel 01"),
        (_manifest("Reel 02"), "Reel 02"),
    ]

    def fake_run(cmd, **kwargs):
        result = MagicMock()
        result.returncode = 0
        result.stdout = json.dumps({"Reel 01": True, "Reel 02": True})
        result.stderr = ""
        return result

    with patch("library.tools.reel_look.subprocess.run", side_effect=fake_run):
        reel_look.apply_comps_batch(entries, str(tmp_path), "Test Project")

    scratch = tmp_path / "pipeline_output" / "scratch" / "reel_look"
    manifest_files = list(scratch.glob("*_fusion_manifest.json"))
    assert len(manifest_files) == 2


def test_apply_comps_batch_returns_per_reel_results(tmp_path):
    """A failing reel is reported by name, not as a blanket failure."""
    entries = [
        (_manifest("Reel 01"), "Reel 01"),
        (_manifest("Reel 02"), "Reel 02"),
    ]

    def fake_run(cmd, **kwargs):
        result = MagicMock()
        result.returncode = 0
        result.stdout = json.dumps({"Reel 01": True, "Reel 02": False})
        result.stderr = ""
        return result

    with patch("library.tools.reel_look.subprocess.run", side_effect=fake_run):
        results = reel_look.apply_comps_batch(entries, str(tmp_path), "Test Project")

    assert results["Reel 01"] is True
    assert results["Reel 02"] is False


def test_apply_comps_batch_skips_reels_with_no_per_clip_effects(tmp_path):
    """A reel with no per_clip effects does not get a manifest file."""
    entries = [
        ({"fusion_effects": {}}, "Reel 01"),
        (_manifest("Reel 02"), "Reel 02"),
    ]

    def fake_run(cmd, **kwargs):
        result = MagicMock()
        result.returncode = 0
        result.stdout = json.dumps({"Reel 02": True})
        result.stderr = ""
        return result

    with patch("library.tools.reel_look.subprocess.run", side_effect=fake_run):
        results = reel_look.apply_comps_batch(entries, str(tmp_path), "Test Project")

    assert results == {"Reel 02": True}
    scratch = tmp_path / "pipeline_output" / "scratch" / "reel_look"
    manifest_files = list(scratch.glob("*_fusion_manifest.json"))
    assert len(manifest_files) == 1


def test_apply_comps_batch_empty_entries_returns_empty(tmp_path):
    """No reels means no subprocess and no results."""
    results = reel_look.apply_comps_batch([], str(tmp_path), "Test Project")
    assert results == {}


# ── The batch subprocess runs under the parent's lease ────────────────
#
# History: PR #1732 batched the per-reel Fusion comp pass into one
# subprocess. The live Reel 10 rebuild then failed at that pass:
# `apply_fusion_comps_batch` calls `scriptapp_preserving_locale`
# directly, the subprocess holds no instance lease, and the handshake
# refused - "refusing Resolve scriptapp handshake outside the instance
# lease". The per-reel subprocess it replaced was decorated
# `@under_lease("apply fusion comps")`, which adopts the parent's lease
# from the inherited environment (`PIPELINE_RESOLVE_LEASE_HELD_BY` +
# `PIPELINE_RESOLVE_LEASE_MODE`, set by `resolve_lease` at
# resolve_lock.py:673-676) instead of taking a flock of its own.


def test_batch_path_adopts_the_lease_the_parent_holds(tmp_path, monkeypatch):
    """The batch path enters the lease, so the handshake does not refuse.

    Stands in for the subprocess: the suite's session-wide sole-writer
    declaration is undone (it would answer `held()` without the
    inheritance path), and the parent's lease arrives exactly as the
    environment carries it - another live process, exclusive.
    """
    import library.tools.execution.apply_fusion_comps as afc

    parent = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        monkeypatch.setattr(resolve_lock, "_sole_writer_reason", None)
        monkeypatch.setenv(resolve_lock.INHERIT_ENV, str(parent.pid))
        monkeypatch.setenv(resolve_lock.INHERIT_MODE_ENV, "exclusive")

        held_inside = []

        def fake_with_resolve(resolve, manifest, project_folder, **kwargs):
            held_inside.append(resolve_lock.held())
            return True

        monkeypatch.setattr(afc, "apply_fusion_comps_with_resolve", fake_with_resolve)
        monkeypatch.setattr(afc.dvr, "scriptapp", lambda name: object())

        results = afc.apply_fusion_comps_batch(
            [(_manifest("Reel 10"), "Reel 10")], str(tmp_path)
        )

        assert results == {"Reel 10": True}
        assert held_inside == [True]
    finally:
        parent.terminate()
        parent.wait(timeout=5)


def test_batch_subprocess_connects_under_the_parents_lease(tmp_path, monkeypatch):
    """The real --batch subprocess, launched the way reel_look launches it.

    With the parent's lease in the inherited environment the handshake
    must not refuse. The expected project names something that cannot be
    open, so a machine with Resolve running refuses at the destination
    guard - read-only, before any mutation - and a machine without it
    fails at the connection. Neither is the lease refusal.
    """
    import library.tools.execution.apply_fusion_comps as afc

    manifest_path = tmp_path / "reel_10_fusion_manifest.json"
    manifest_path.write_text(json.dumps(_manifest("Reel 10")), encoding="utf-8")
    batch_path = tmp_path / "fusion_batch.json"
    batch_path.write_text(
        json.dumps([{"manifest": str(manifest_path), "timeline": "Reel 10"}]),
        encoding="utf-8",
    )

    monkeypatch.setenv(resolve_lock.INHERIT_ENV, str(os.getpid()))
    monkeypatch.setenv(resolve_lock.INHERIT_MODE_ENV, "exclusive")

    module = (
        pathlib.Path(__file__).resolve().parents[3]
        / "library"
        / "tools"
        / "execution"
        / "apply_fusion_comps.py"
    )
    result = subprocess.run(
        [
            sys.executable,
            str(module),
            str(manifest_path),
            "--project-folder",
            str(tmp_path),
            "--expected-project",
            "VeP Test Project (never open)",
            "--expected-timeline",
            "Reel 10",
            "--batch",
            str(batch_path),
        ],
        capture_output=True,
        encoding="utf-8",
        check=False,
        timeout=120,
    )

    assert (
        "refusing Resolve scriptapp handshake outside the instance lease"
    ) not in result.stderr
