"""The Workflow Integration's one route into this repository's Python.

Every test here builds its own project under `tmp_path` (AGENTS.md
section 8, "No test reaches a real project") and none of them opens
DaVinci Resolve, which is the whole point of the module under test: the
plugin is JavaScript, and keeping the judgements on this side is what
keeps them testable at all.
"""

from __future__ import annotations

import json
import subprocess
import sys

import pytest

from library.tools import workflow_bridge


def _project(tmp_path, **outputs):
    """A project folder with a state file, and nothing else."""
    folder = tmp_path / "proj"
    (folder / "raw").mkdir(parents=True)
    (folder / "pipeline_data.json").write_text(
        json.dumps({"step_outputs": outputs}), encoding="utf-8")
    clip = folder / "raw" / "IMG_0001.MOV"
    clip.write_bytes(b"not really a movie")
    return folder, clip


# ── The enumeration ──────────────────────────────────────────────────

def test_an_unknown_op_is_refused_by_name_and_lists_what_is_answered():
    """A request that does nothing and says nothing is the failure this
    enumeration exists to prevent."""
    result = workflow_bridge.answer({"op": "teleport"})
    assert result["ok"] is False
    assert "teleport" in result["error"]
    for op in workflow_bridge.OPERATIONS:
        assert op in result["error"]


def test_every_operation_reports_its_own_cost():
    """`python_ms` is on every answer, so the plugin's round trip can be
    split into the work and the process spawn rather than guessed at."""
    for op in workflow_bridge.OPERATIONS:
        result = workflow_bridge.answer({"op": op})
        assert "python_ms" in result, op


def test_surface_is_the_enumeration_itself():
    result = workflow_bridge.answer({"op": "surface"})
    assert result["operations"] == sorted(workflow_bridge.OPERATIONS)


# ── Finding the project ──────────────────────────────────────────────

def test_the_project_is_measured_by_walking_up_from_a_source_file(tmp_path):
    folder, clip = _project(tmp_path)
    assert workflow_bridge.project_for_file(str(clip)) == str(folder)


def test_a_file_under_no_project_answers_none_rather_than_a_guess(tmp_path):
    stray = tmp_path / "elsewhere" / "IMG_0002.MOV"
    stray.parent.mkdir(parents=True)
    stray.write_bytes(b"")
    assert workflow_bridge.project_for_file(str(stray)) is None
    assert workflow_bridge.project_for_file("") is None


def test_the_walk_is_bounded(tmp_path):
    """An unbounded walk reaches the filesystem root and answers with
    whatever it finds there."""
    deep = tmp_path.joinpath(*["d"] * (workflow_bridge.PROJECT_WALK_LIMIT + 3))
    deep.mkdir(parents=True)
    (tmp_path / "pipeline_data.json").write_text("{}", encoding="utf-8")
    clip = deep / "IMG_0003.MOV"
    clip.write_bytes(b"")
    assert workflow_bridge.project_for_file(str(clip)) is None


# ── An absence is stated ─────────────────────────────────────────────

def test_a_project_with_no_render_says_so_and_offers_no_path(tmp_path):
    folder, clip = _project(tmp_path)
    result = workflow_bridge.answer(
        {"op": "render_output", "source_file": str(clip)})
    assert result["ok"] is True
    assert result["path"] == ""
    assert "no render_output" in result["reason"]


def test_a_recorded_render_comes_back_with_whether_it_is_on_disk(tmp_path):
    folder, clip = _project(tmp_path)
    master = folder / "exports" / "cut.mp4"
    master.parent.mkdir()
    master.write_bytes(b"0")
    (folder / "pipeline_data.json").write_text(json.dumps({"step_outputs": {
        "render": {"render_output": {"output_path": str(master)}}}}),
        encoding="utf-8")
    result = workflow_bridge.answer(
        {"op": "render_output", "source_file": str(clip)})
    assert result["path"] == str(master)
    assert result["on_disk"] is True


def test_a_recorded_render_that_is_gone_is_reported_not_hidden(tmp_path):
    """`on_disk` False is what lets the plugin say "recorded, but not on
    disk" instead of showing a player that never starts."""
    folder, clip = _project(tmp_path)
    (folder / "pipeline_data.json").write_text(json.dumps({"step_outputs": {
        "render": {"render_output": {
            "output_path": str(folder / "exports" / "gone.mp4")}}}}),
        encoding="utf-8")
    result = workflow_bridge.answer(
        {"op": "render_output", "source_file": str(clip)})
    assert result["path"].endswith("gone.mp4")
    assert result["on_disk"] is False


def test_a_clip_under_no_project_is_an_absence_with_a_reason(tmp_path):
    stray = tmp_path / "IMG_0004.MOV"
    stray.write_bytes(b"")
    result = workflow_bridge.answer({"op": "clip_facts", "context": {
        "clip": {"file": str(stray), "start": 0, "end": 10}}})
    assert result["ok"] is True
    assert result["project_folder"] == ""
    assert "no pipeline project" in result["reason"]


def test_the_join_is_clip_context_and_not_a_second_opinion(tmp_path):
    """The plugin gets the SAME join the Qt panel gets - two surfaces
    answering "what is this clip" differently would be invisible."""
    folder, clip = _project(tmp_path, catalog={"clip_catalog": [
        {"clip_id": "clip_001", "filename": "IMG_0001.MOV", "duration": 12.0}]})
    result = workflow_bridge.answer({"op": "clip_facts", "context": {
        "timecode": "01:00:00:10", "timeline_frame": 10, "fps": 30.0,
        "clip": {"file": str(clip), "name": "IMG_0001.MOV",
                 "start": 0, "end": 60, "left_offset": 0}}})
    assert result["ok"] is True
    assert result["clip_id"] == "clip_001"
    assert "IMG_0001.MOV" in result["prompt_block"]


# ── The process contract ─────────────────────────────────────────────

def test_it_answers_over_stdin_and_stdout_as_the_plugin_calls_it():
    """The plugin spawns this module and writes JSON at it.  Driving the
    real subprocess is the only thing that proves that contract."""
    proc = subprocess.run(
        [sys.executable, "-m", "library.tools.workflow_bridge"],
        input=json.dumps({"op": "ping"}), capture_output=True,
        encoding="utf-8", check=True)
    answer = json.loads(proc.stdout)
    assert answer["ok"] is True
    assert answer["executable"] == sys.executable


def test_a_request_that_is_not_json_is_answered_rather_than_crashing():
    proc = subprocess.run(
        [sys.executable, "-m", "library.tools.workflow_bridge"],
        input="{not json", capture_output=True, encoding="utf-8", check=True)
    assert json.loads(proc.stdout)["ok"] is False
