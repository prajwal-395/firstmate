"""The reel-build path checks its requirements against the local manifest.

The defect this pins: a build that starts on a worker with no
scripting connection into Resolve takes the exclusive instance lease
and burns a 19.4-67.1s Resolve pass per reel to discover what
`ren doctor` already knew. The render path already gates this way
(`test_render_worker_gate`); the build is the other expensive thing
the captain asks for, so it checks before starting and refuses by
name - on the two needs a build provably cannot run without, and on
nothing else.
"""
from pathlib import Path
from types import SimpleNamespace

import pytest

import manage_project
from ren import doctor
from library.tools import machine_needs
from library.tools.ren_refusal import RenRefusal
from library.tools import worker_requirements
from library.tools.worker_requirements import (
    build_requirements, check_build, match_requirements,
)


def _refusal_manifest():
    """A manifest where reel.build is unavailable (no Resolve scripting)."""
    return {
        "capabilities": {"reel.build": machine_needs.UNAVAILABLE},
        "resolve": {"plugins": []},
        "models": {},
    }


def _capable_manifest():
    """A manifest where reel.build is available."""
    return {
        "capabilities": {"reel.build": machine_needs.AVAILABLE},
        "resolve": {"plugins": []},
        "models": {},
    }


def _check(name, ok, need, undetermined=False):
    return doctor.Check(name, ok, "stub detail", need=need,
                        undetermined=undetermined)


def _passing_checks():
    return [_check("Resolve scripting", True, "resolve.scripting"),
            _check("Resolve edition", True, "resolve.studio")]


def test_build_requirements_declares_only_the_unconditional_needs():
    """A build task declares the scripting connection and the Studio
    edition - the two needs every placement provably cannot run
    without - and nothing else. The graphics group, models and
    plugins are refused just-in-time where consumed, because they do
    not stop a real build: this machine and the captain's both lack
    the Haar cascade while building reels fine."""
    requirements = build_requirements()
    assert requirements.needs == ("resolve.scripting", "resolve.studio")
    assert requirements.capabilities == ()
    assert requirements.plugins == ()
    assert requirements.models == ()


def test_check_build_refuses_a_worker_without_resolve_scripting():
    """A worker whose checks fail resolve.scripting is refused by name -
    the build does not start to discover it cannot place."""
    checks = [_check("Resolve scripting", False, "resolve.scripting"),
              _check("Resolve edition", True, "resolve.studio")]
    refusals = match_requirements(_refusal_manifest(), build_requirements(),
                                  checks=checks)
    assert len(refusals) == 1
    assert "resolve.scripting" in refusals[0]


def test_check_build_passes_a_worker_that_can_build():
    """A worker whose checks pass both needs is not refused - the gate
    must not block a capable worker."""
    assert match_requirements(_capable_manifest(), build_requirements(),
                              checks=_passing_checks()) == []


def test_a_failing_graphics_check_does_not_refuse_the_build():
    """A worker failing python.graphics is NOT refused - the defect
    this pins. The test machine and the captain's Mac both report the
    graphics group unmet while building reels fine; refusing on it
    refused real production work (PR 1910 batch). Graphics is refused
    just-in-time where consumed, never here."""
    checks = _passing_checks() + [
        _check("Python graphics", False, "python.graphics")]
    assert match_requirements(_capable_manifest(), build_requirements(),
                              checks=checks) == []


def test_without_checks_an_unavailable_capability_is_not_a_build_refusal():
    """Needs are read from the checks, so a fixture manifest without
    checks refuses nothing - a caller that has not run the checks
    cannot name a failing need, and does not guess from the
    capability verdict alone."""
    assert check_build(produce=_refusal_manifest) == []


def test_build_command_refuses_on_a_worker_that_cannot_build(
        tmp_path, monkeypatch):
    """build-reels checks the worker before resolving the project and
    refuses naming the requirement, not a generic build failure."""
    monkeypatch.setattr(worker_requirements, "check_build",
                        lambda: ["need resolve.scripting: not met on this "
                                 "worker (a scripting connection to a "
                                 "running DaVinci Resolve)"])
    args = SimpleNamespace(project=str(tmp_path))
    with pytest.raises(RenRefusal) as exc:
        manage_project.cmd_build_reels(args)
    assert "resolve.scripting" in exc.value.render()


def test_build_command_checks_the_worker_before_anything_else(
        tmp_path, monkeypatch):
    """The worker check is the first thing build-reels does - before it
    resolves the project, takes a lease or connects to Resolve. A
    worker that cannot build refuses before any of that, so the
    captain's machine is not touched."""
    class _GateCalled(Exception):
        pass

    def _gate():
        raise _GateCalled

    monkeypatch.setattr(worker_requirements, "check_build", _gate)
    args = SimpleNamespace(project=str(tmp_path))
    with pytest.raises(_GateCalled):
        manage_project.cmd_build_reels(args)
