"""The render path checks its requirements against the local manifest.

The defect this pins: a render that starts on a worker which cannot
perform it - no Resolve, the free edition, no ffmpeg - burns the
captain's machine to discover what `ren doctor` already knew. The render
is the expensive thing the captain asks for, so it is the path that
checks before connecting and refuses by name.
"""
from pathlib import Path

import pytest

from library.tools import machine_needs, reel_deliver, reel_build, resolve_lock
from library.tools.worker_requirements import (
    check_render, render_requirements,
)


class _Connected(Exception):
    pass


def _refusal_manifest():
    """A manifest where render.build is unavailable (no Resolve Studio)."""
    return {
        "capabilities": {"render.build": machine_needs.UNAVAILABLE},
        "resolve": {"plugins": []},
        "models": {},
    }


def _capable_manifest():
    """A manifest where render.build is available."""
    return {
        "capabilities": {"render.build": machine_needs.AVAILABLE},
        "resolve": {"plugins": []},
        "models": {},
    }


def test_render_requirements_declares_the_render_build_capability():
    """A render task declares render.build, so the matcher checks the one
    capability that carries Resolve scripting, the Studio edition and
    ffmpeg."""
    requirements = render_requirements()
    assert requirements.capabilities == ("render.build",)


def test_check_render_refuses_a_worker_without_resolve_studio():
    """A worker whose manifest reports render.build unavailable is refused
    by name - the render does not start to discover it cannot."""
    refusals = check_render(produce=_refusal_manifest)
    assert len(refusals) == 1
    assert "render.build" in refusals[0]


def test_check_render_passes_a_worker_that_can_render():
    """A worker whose manifest reports render.build available is not
    refused - the gate must not block a capable worker."""
    assert check_render(produce=_capable_manifest) == []


def test_deliver_refuses_on_a_worker_that_cannot_render(tmp_path, monkeypatch):
    """deliver_reel checks the worker before connecting to Resolve and
    refuses naming the requirement, not a generic render failure."""
    monkeypatch.setattr(reel_deliver, "check_render",
                        lambda: ["capability render.build: unavailable on "
                                 "this worker (missing resolve.studio)"])
    (tmp_path / "project.yaml").write_text(
        "resolve:\n  project_name: Scratch\n", encoding="utf-8")
    with pytest.raises(reel_deliver.DeliverRefused) as exc:
        reel_deliver.deliver_reel(str(tmp_path), 1,
                                  output_dir=str(tmp_path / "out"))
    assert "render.build" in str(exc.value)


def test_deliver_checks_the_worker_before_anything_else(tmp_path, monkeypatch):
    """The worker check is the first thing deliver_reel does - before it
    reads the project, takes a lease or connects to Resolve. A worker
    that cannot render refuses before any of that, so the captain's
    machine is not touched."""
    class _GateCalled(Exception):
        pass

    def _gate():
        raise _GateCalled

    monkeypatch.setattr(reel_deliver, "check_render", _gate)
    (tmp_path / "project.yaml").write_text(
        "resolve:\n  project_name: Scratch\n", encoding="utf-8")
    with pytest.raises(_GateCalled):
        reel_deliver.deliver_reel(str(tmp_path), 1,
                                  output_dir=str(tmp_path / "out"))
