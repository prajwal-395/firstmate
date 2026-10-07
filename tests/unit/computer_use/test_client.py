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
        "find_element", {"path": ["Workspace", "Switch to Page"],
                         "title": "Media"}
    )
    ref = found["elements"][0]["ref"]
    assert client.call("press", {"element": ref}) == {"pressed": True}


def test_an_explicit_driver_binary_does_not_refresh_native_builds(
        client, monkeypatch):
    from library.tools import native_refresh

    refreshes = []
    monkeypatch.setattr(native_refresh, "ensure_current",
                        lambda root: refreshes.append(root))

    result = client.call("find_element", {"path": ["Workspace"],
                                           "title": "Switch to Page"})

    assert result["elements"]
    assert refreshes == []


def test_a_driver_refusal_becomes_a_ren_refusal_carrying_the_code(client, monkeypatch):
    """The qualification gate is the driver's; the client's job is to
    surface it as a RenRefusal with the driver's code and message, not
    as a raw dict or a generic error. A client that let the raw JSON
    through would leave every caller to re-interpret the driver's
    vocabulary."""
    monkeypatch.setenv("MOCK_OPEN_PROJECT", "Podcast (field test)")
    found = client.call(
        "find_element", {"path": ["Workspace", "Switch to Page"],
                         "title": "Media"}
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


class _FakePipe:
    def close(self):
        pass


class _FakePopen:
    """A driver stand-in with explicit liveness: already dead (exits
    on EOF), dying on terminate, dying on kill, or never dying. The
    calls list records the reaping sequence."""

    def __init__(self, died=False, dies_on_terminate=False):
        self.died = died
        self.dies_on_terminate = dies_on_terminate
        self.calls: list = []
        self.stdin = _FakePipe()
        self.stdout = _FakePipe()

    def wait(self, timeout=None):
        import subprocess

        self.calls.append("wait")
        if self.died:
            return 0
        raise subprocess.TimeoutExpired(cmd="driver", timeout=timeout)

    def terminate(self):
        self.calls.append("terminate")
        if self.dies_on_terminate:
            self.died = True

    def kill(self):
        self.calls.append("kill")
        self.died = True


def _client_with(fake) -> DriverClient:
    from pathlib import Path

    client = DriverClient(binary=Path("/nonexistent-resolve-control"))
    client._process = fake
    return client


def test_close_reaps_a_driver_that_exits_on_eof():
    """The healthy path: stdin EOF ends the driver, one bounded wait
    reaps it, nothing is signalled. A close that only drops pipes
    leaves the dead process a zombie."""
    fake = _FakePopen(died=True)
    client = _client_with(fake)
    client.close()
    assert fake.calls == ["wait"]
    assert client._process is None


def test_close_terminates_a_driver_stuck_mid_call():
    """A driver wedged mid-call (hours-old orphans, ppid broker) never
    reads EOF: the bounded wait times out and terminate ends it. A
    close without escalation abandons it."""
    fake = _FakePopen(dies_on_terminate=True)
    client = _client_with(fake)
    client.close()
    assert fake.calls == ["wait", "terminate", "wait"]
    assert client._process is None


def test_close_kills_a_driver_that_ignores_terminate():
    """Terminate refused: SIGKILL is the last resort, still bounded,
    still reaped. Every stage records, so a hung close reads as a
    sequence instead of a mystery."""
    fake = _FakePopen()
    client = _client_with(fake)
    client.close()
    assert fake.calls == ["wait", "terminate", "wait", "kill", "wait"]
    assert client._process is None


def test_close_with_no_process_clears_refs_quietly():
    """Closing an unspawned client is a no-op that still drops refs."""
    client = _client_with(None)
    client._refs["ref-1"] = {}
    client.close()
    assert client._refs == {}
