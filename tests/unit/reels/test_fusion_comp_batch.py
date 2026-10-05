"""The batch Fusion comp pass runs ONE subprocess for multiple reels.

The defect this prevents: each reel's Fusion comp pass ran in its own
subprocess, paying 17.0-63.7s of FIXED overhead per reel (the Resolve
connection + project/timeline resolution). For a 26-reel build that is
7-27 minutes of Resolve hold. The batch pass connects once and applies
every reel's comps in a single subprocess.
"""
import json
import os
import subprocess
from unittest.mock import patch, MagicMock

import pytest

from library.tools import reel_look


def _manifest(timeline_name):
    return {
        "fusion_effects": {"per_clip": {"clip0": {"zoom": 1.1}}},
        "tracks": {"V1": {"clips": [
            {"label": "clip0", "source_file": "/media/clip0.mov"}]}},
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
        result.stdout = json.dumps({
            "Reel 01": True, "Reel 02": True, "Reel 03": True,
        })
        result.stderr = ""
        return result

    with patch("library.tools.reel_look.subprocess.run", side_effect=fake_run):
        results = reel_look.apply_comps_batch(
            entries, str(tmp_path), "Test Project")

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
        results = reel_look.apply_comps_batch(
            entries, str(tmp_path), "Test Project")

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
        results = reel_look.apply_comps_batch(
            entries, str(tmp_path), "Test Project")

    assert results == {"Reel 02": True}
    scratch = tmp_path / "pipeline_output" / "scratch" / "reel_look"
    manifest_files = list(scratch.glob("*_fusion_manifest.json"))
    assert len(manifest_files) == 1


def test_apply_comps_batch_empty_entries_returns_empty(tmp_path):
    """No reels means no subprocess and no results."""
    results = reel_look.apply_comps_batch([], str(tmp_path), "Test Project")
    assert results == {}
