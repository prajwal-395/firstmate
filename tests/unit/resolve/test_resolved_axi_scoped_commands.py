"""Broker-scoped Resolve commands must stay inside Ren's fixture projects."""

import pytest

from library.tools.resolved import jobs
from library.tools.resolved.jobs import JobRefused


@pytest.mark.parametrize("project,timeline", [
    ("Ren Qualification", "Q Promotion Proof R5"),
    ("Ren Qualification", "fm-scratch qualify"),
    ("Ren Scratch - breaktest", "Reel 13 - scratch"),
])
def test_scoped_marker_snapshot_is_admitted_for_ren_fixture_targets(
        project, timeline):
    shape = jobs._resolve_axi_shape({
        "argv": ["markers", "--project", project, "--timeline", timeline,
                 "snapshot", "--out", "/tmp/markers.json"],
        "cwd": "/tmp",
    })

    assert shape["project"] == project
    assert shape["timeline"] == timeline
    assert shape["mode"] == "exclusive"


def test_scoped_timeline_duplicate_is_exclusive_on_a_ren_fixture():
    from library.tools.resolved.scheduler import EXCLUSIVE

    shape = jobs._resolve_axi_shape({
        "argv": ["timeline", "duplicate", "--project",
                 "Ren Qualification", "--timeline",
                 "Q Promotion Proof R5", "--name", "Q Proof Copy",
                 "--apply"],
        "cwd": "/tmp",
    })

    assert shape["project"] == "Ren Qualification"
    assert shape["timeline"] == "Q Promotion Proof R5"
    assert shape["mode"] == EXCLUSIVE


def test_live_timeline_refresh_can_select_named_ren_master_through_broker():
    shape = jobs._resolve_axi_shape({
        "argv": ["timeline", "current", "--project",
                 "Ren Scratch - breaktest", "--timeline",
                 "GEO Podcast - Synced", "--apply"],
    })

    assert shape["project"] == "Ren Scratch - breaktest"
    assert shape["timeline"] == "GEO Podcast - Synced"
    assert shape["mode"] == "exclusive"


def test_live_timeline_selection_refuses_captain_project_before_queueing():
    with pytest.raises(JobRefused, match="only for an explicit Ren-owned"):
        jobs._resolve_axi_shape({
            "argv": ["timeline", "current", "--project",
                     "Lucie Content/Social Media/Podcast", "--timeline",
                     "GEO Podcast - Synced", "--apply"],
        })


def test_live_timeline_selection_refuses_empty_ren_scratch_namespace():
    with pytest.raises(JobRefused, match="only for an explicit Ren-owned"):
        jobs._resolve_axi_shape({
            "argv": ["timeline", "current", "--project", "Ren Scratch - ",
                     "--timeline", "GEO Podcast - Synced", "--apply"],
        })


@pytest.mark.parametrize("project,timeline", [
    ("Podcast (field test)", "Reel 13"),
    ("Ren Qualification", "Reel 13"),
    ("Ren Qualification", ""),
    ("", "Q Promotion Proof R5"),
])
@pytest.mark.parametrize("command", ["snapshot", "duplicate"])
def test_scoped_commands_refuse_non_fixture_targets(project, timeline,
                                                     command):
    if command == "snapshot":
        argv = ["markers", "--project", project, "--timeline", timeline,
                "snapshot", "--out", "/tmp/markers.json"]
    else:
        argv = ["timeline", "duplicate", "--project", project,
                "--timeline", timeline, "--name", "Fixture Copy"]

    with pytest.raises(JobRefused, match="only for an explicit Ren-owned"):
        jobs._resolve_axi_shape({"argv": argv, "cwd": "/tmp"})


def test_scoped_commands_refuse_when_project_is_implicit():
    argv = ["timeline", "duplicate", "--timeline", "Q Marker Fixture",
            "--name", "Fixture Copy"]

    with pytest.raises(JobRefused, match="only for an explicit Ren-owned"):
        jobs._resolve_axi_shape({"argv": argv, "cwd": "/tmp"})
