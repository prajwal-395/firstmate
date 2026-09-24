"""A gate id becomes a directory name, so it may not be a path.

This lands with the change that makes it REACHABLE. Until operation
breakpoints, every gate id came from the DAG; `--break <address>` makes
it something an operator types and `/api/gates/{step_id}` makes it
something an HTTP path carries.
"""

from __future__ import annotations

import json
import os

import pytest

from library.tools import review_gate


@pytest.fixture
def project(tmp_path):
    folder = tmp_path / "proj"
    folder.mkdir()
    (folder / "pipeline_data.json").write_text(
        json.dumps({"project_folder": str(folder)}), encoding="utf-8")
    return folder


def test_a_step_id_and_an_operation_address_both_work(project):
    for gate_id in ("render", "subtitles.render@45.0-72.0"):
        path = review_gate.save_gate_snapshot(str(project), gate_id, "x", {})
        assert os.path.realpath(path).startswith(os.path.realpath(project))
        assert review_gate.get_gate_status(str(project), gate_id) == "pending"
    assert set(review_gate.list_pending_gates(str(project))) == {
        "render", "subtitles.render@45.0-72.0"}


@pytest.mark.parametrize("attempt", [
    "../../../outside",
])
def test_an_id_that_is_a_path_is_refused(project, attempt):
    """Measured before this check existed: '../../../outside' wrote
    `outside/snapshot.json` OUTSIDE the project directory entirely."""
    with pytest.raises(review_gate.UnsafeGateId):
        review_gate.save_gate_snapshot(str(project), attempt, "x", {})




def test_reads_are_guarded_too(project):
    """A read that built the path would still leave the project - and
    `get_gate_status` is what `/api/gates/{step_id}` calls."""
    for call in (review_gate.get_gate_status,
                 review_gate.load_gate_snapshot,
                 review_gate.load_gate_feedback):
        with pytest.raises(review_gate.UnsafeGateId):
            call(str(project), "../../../outside")
