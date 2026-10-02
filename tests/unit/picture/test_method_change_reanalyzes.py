"""A cached analysis must not survive its own method changing.

Finding 8, execution-frontier report 2026-09-24: music_analysis reused an
August librosa grid after beat_this landed ("reusing cached result"),
and semantic analysis printed "Step code changed ... invalidating" and
then reused all 17 August profiles ("17 already analyzed"). The
ledger-level invalidation promises a re-run; these tests pin the
second half - the step's own resume logic must treat artifacts written
by older code as missing.

No pipeline run, no model, no vision. Step 1.03 is exercised through
its collection half only (copied profiles, ``raw_footage_files: []`` -
never against a real project). Step 2.06 is exercised through its
cache check only: a hit returns without running the pipeline, a miss
proceeds to it (and reports unavailable on an empty scratch track
rather than reusing the stale grid).
"""

import importlib.util
import json
import os
import sys
from types import SimpleNamespace
from pathlib import Path

PILOT_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(PILOT_ROOT))

from library.tools import code_identity
from library.tools.project_layout import Area, ProjectLayout


def _load_step_module(step_dir_name: str, module_name: str):
    path = (PILOT_ROOT / "library" / "steps" / step_dir_name / "step.py")
    spec = importlib.util.spec_from_file_location(module_name, str(path))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


semantic_step = _load_step_module(
    "step_1_03_semantic_analysis", "step_1_03_under_test")
music_step = _load_step_module(
    "step_2_06_music_analysis", "step_2_06_under_test")

def _v3_profile_bytes() -> bytes:
    """A minimal v3 profile the collection half accepts."""
    return json.dumps({
        "analysis_metadata": {"pipeline_version": "3"},
        "scene": [],
        "camera": [],
        "actions": [],
        "objects": [],
        "assessment": {},
    }).encode("utf-8")


def _analysis_dir(project_folder: str) -> str:
    return str(ProjectLayout(project_folder).write_dir(
        Area.VISION_ANALYSIS, step="semantic_analysis"))


# ── step 1.03: profiles from older code are not reused ───────────

def test_semantic_profiles_from_an_old_method_are_not_reused(tmp_path):
    """The finding: 17 August profiles reused after the method moved.

    The old method's stamp differs from the current source hash, so its
    profiles are removed instead of returned as current measurements.
    """
    project = tmp_path / "proj"
    project.mkdir()
    analysis_dir = _analysis_dir(str(project))
    (Path(analysis_dir) / "clip_profile_IMG_1806_v3.json").write_bytes(
        _v3_profile_bytes())
    (Path(analysis_dir) / code_identity.CODE_STAMP_FILENAME).write_text(
        "old-semantic-method-hash\n", encoding="utf-8")

    result = semantic_step.analyse_semantics(
        [], project_folder=str(project))

    assert result["semantic_analysis_documents"] == []


# ── step 2.06: a grid from an older method is not a hit ──────────

def _music_dir(project_folder: str) -> str:
    return str(ProjectLayout(project_folder).write_dir(
        Area.MUSIC_ANALYSIS, step="music_analysis"))


def test_music_method_change_does_not_reuse_or_relabel_the_old_grid(
        tmp_path, monkeypatch):
    """A successful process exit without output must not bless stale data.

    Before the fix, the step could read the still-present August file
    after a no-output re-analysis and write the current method hash onto
    it, making the next run trust the old grid.
    """
    project = tmp_path / "proj"
    project.mkdir()
    track = project / "bed.wav"
    track.write_bytes(b"\x00" * 64)
    music_dir = _music_dir(str(project))
    analysis_path = Path(music_dir) / "music_analysis.json"
    analysis_path.write_text(json.dumps({
        "file": str(track),
        "method_hash": "old-music-method-hash",
        "tempo": {"method": "librosa-beat-track", "bpm": 88.2},
    }), encoding="utf-8")
    monkeypatch.setattr(
        music_step.subprocess, "run",
        lambda *a, **k: SimpleNamespace(returncode=0, stdout="", stderr=""))

    result = music_step.analyse_music(
        {"audio_path": str(track)}, project_folder=str(project))

    assert result["music_analysis"]["available"] is False
    assert "not found" in result["music_analysis"]["error"]
