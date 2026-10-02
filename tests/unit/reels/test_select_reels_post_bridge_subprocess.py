"""The select_reels approval line names THIS project's transcript, or none.

It once carried one project's absolute path as a literal, so every other
project was told its transcript lived in the podcast field test; the
engine states no series' own paths (AGENTS.md 14).
"""
import json
import os
import subprocess
import sys
from pathlib import Path


def _approval(input_data):
    env = os.environ.copy()
    env["PYTHONPATH"] = str(Path.cwd())
    proc = subprocess.run(
        [sys.executable, "library/steps/step_3_04_select_reels/post_bridge.py"],
        input=json.dumps(input_data), capture_output=True,
        encoding="utf-8", env=env, check=False)
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)["reel_selection"]["approval"]


def test_the_approval_line_names_only_this_projects_transcript(tmp_path):
    approval = _approval({
        "project_folder": str(tmp_path),
        "timeline_transcript": {"segments": [],
                                "derived_from": {"duration_seconds": 10.0}},
        "reel_selection": {"moments": []},
    })
    assert "geo-podcast" not in approval, (
        f"one project's path is still baked into the engine: {approval}")
    assert str(tmp_path) in approval, (
        f"the approval names no transcript for the project it ran for: "
        f"{approval}")

    # No project folder, no claim about where a file is: a path derived
    # from an empty folder would print `/pipeline_output/scratch/...` and
    # read as a real location.
    approval = _approval({
        "timeline_transcript": {"segments": [],
                                "derived_from": {"duration_seconds": 1.0}},
        "reel_selection": {"moments": []}})
    assert "transcript" not in approval.lower().replace(
        "reel_proposal.assert_approved", ""), approval
