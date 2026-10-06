"""The client's contract, pinned against a mock driver.

Each test names the defect it catches: a behaviour that is wrong on
origin/main (where none of this exists) and would be wrong again if the
client regressed. No test asserts a field exists or round-trips a value
the same test put in.
"""

import os

import pytest

from library.tools.computer_use import client as client_module
from library.tools.computer_use.client import DriverClient, DriverError


def test_press_reaches_the_driver_with_the_ref_find_element_issued(client):
    """The ref table is the client's: a press without a prior
    find_element is refused locally, and one with the ref reaches the
    driver. A client that dropped the table would send `press` with no
    element and the driver would answer ELEMENT_NOT_FOUND - the defect
    this pins."""
    with pytest.raises(DriverError) as never_issued:
        client.call("press", {"element": "ax-never-issued"})
    assert never_issued.value.driver_code == "ELEMENT_NOT_FOUND"

    found = client.call(
        "find_element", {"path": ["Workspace", "Switch to Page", "Media"]}
    )
    ref = found["elements"][0]["ref"]
    assert client.call("press", {"element": ref}) == {"pressed": True}


def test_a_driver_refusal_becomes_a_ren_refusal_carrying_the_code(client, monkeypatch):
    """The qualification gate is the driver's; the client's job is to
    surface it as a RenRefusal with the driver's code and message, not
    as a raw dict or a generic error. A client that let the raw JSON
    through would leave every caller to re-interpret the driver's
    vocabulary."""
    monkeypatch.setenv("MOCK_OPEN_PROJECT", "Podcast (field test)")
    found = client.call(
        "find_element", {"path": ["Workspace", "Switch to Page", "Media"]}
    )
    with pytest.raises(DriverError) as refused:
        client.call("press", {"element": found["elements"][0]["ref"]})
    assert refused.value.driver_code == "PROJECT_NOT_QUALIFICATION"
    assert "Podcast (field test)" in refused.value.what
    assert "DRIVER_PROJECT_NOT_QUALIFICATION" == refused.value.code


def test_a_dead_driver_is_respawned_and_the_call_answers(client, mock_bin, monkeypatch):
    """The probe measured the driver's session dying mid-task (Resolve
    exited between two reads). A client that wedges on a dead stdin
    turns a recoverable death into a hung run; the respawn is the
    behaviour this pins."""
    first = client.call("get_windows")
    assert first["windows"][0]["id"] == 9295
    assert client._process is not None
    client._process.kill()
    client._process.wait()
    second = client.call("get_windows")
    assert second["windows"][0]["id"] == 9295


def test_a_response_with_a_foreign_id_is_refused_not_returned(client, monkeypatch):
    """A response whose id is not the pending request's is a protocol
    violation - the answer to a dead process's request, or a corrupted
    stream - and must be refused, never returned as if it answered."""
    monkeypatch.setenv("MOCK_WRONG_ID", "1")
    with pytest.raises(DriverError) as foreign:
        client.call("get_windows")
    assert foreign.value.driver_code == "INTERNAL"


def test_the_status_verb_reports_the_driver_surface_without_touching_a_project(
    client, capsys
):
    """`ren computer-use status` is read-only: it runs against whatever
    project is open (the captain's included) and reports the surface.
    A status that refused on the captain's project would be useless as a
    diagnostic; a status that called an action method would violate the
    driver's whitelist."""
    from library.tools.computer_use.__main__ import main

    monkey_project = os.environ.get("MOCK_OPEN_PROJECT")
    os.environ["MOCK_OPEN_PROJECT"] = "Podcast (field test)"
    try:
        code = main(["status"])
    finally:
        if monkey_project is None:
            del os.environ["MOCK_OPEN_PROJECT"]
        else:
            os.environ["MOCK_OPEN_PROJECT"] = monkey_project
    assert code == 0
    out = capsys.readouterr().out
    assert "Resolve running: True" in out
    assert "Podcast (field test)" in out
    assert "AXMenuItem" in out


def test_a_missing_binary_is_a_refusal_naming_the_build(tmp_path, monkeypatch):
    """The driver is a build product; a client that skipped a missing
    binary would mock the whole layer. The refusal names the build."""
    monkeypatch.setenv(client_module.ENV_BIN, str(tmp_path / "not-built"))
    with pytest.raises(client_module.DriverDead) as missing:
        DriverClient()
    assert "cargo build --release" in missing.value.fix


def test_the_verb_is_registered_in_the_one_registry():
    """`ren computer-use` reaches this module through `ren/commands.py`,
    the one registry `ren --help` renders. A module nothing calls is not
    shipped: without this row the package is unreachable from the CLI."""
    from ren.commands import VERBS

    row = next((verb for verb in VERBS if verb.name == "computer-use"), None)
    assert row is not None, "the computer-use verb is missing from ren.commands.VERBS"
    assert row.module_argv == ("library.tools.computer_use",)
