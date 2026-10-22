"""The Gemma in-process guard (2026-10-22 three 7 GB copies).

Cheap defect-naming tests only: no model is loaded, no video is
rendered, every memory reading is stubbed. Each test names the defect
it would catch.
"""
import os
import re
from pathlib import Path

import pytest

from library.tools import gemma_inprocess_guard as guard
from library.tools import vision_model

# The conftest session fixture (`_no_real_vision_model_load`) replaces
# `VisionModel._ensure_loaded` with a refusal for every unmarked test -
# which is exactly what these tests run under. The wiring tests below
# need the REAL one, so it is saved here at import time (before any
# fixture runs) and called explicitly.
_REAL_ENSURE_LOADED = vision_model.VisionModel._ensure_loaded


def test_memory_refuses_below_the_floor(monkeypatch):
    """4 GB free+inactive refuses: no second copy beside Resolve."""
    monkeypatch.setattr(guard, "available_gb", lambda: 4.0)
    with pytest.raises(guard.GemmaMemoryRefused, match="below the 8 GB floor"):
        guard.check_memory()


def test_memory_refusal_is_a_memory_error(monkeypatch):
    """The refusal reads as memory pressure, not a model bug."""
    monkeypatch.setattr(guard, "available_gb", lambda: 1.0)
    with pytest.raises(MemoryError):
        guard.check_memory()


def test_memory_refuses_when_unreadable(monkeypatch):
    """An unreadable machine refuses: loading blind held the copies."""
    monkeypatch.setattr(guard, "available_gb", lambda: None)
    with pytest.raises(guard.GemmaMemoryRefused, match="could not be read"):
        guard.check_memory()


def test_memory_passes_when_plentiful(monkeypatch):
    """12 GB free+inactive returns the reading instead of refusing."""
    monkeypatch.setattr(guard, "available_gb", lambda: 12.0)
    assert guard.check_memory() == 12.0


def test_second_holder_is_refused_not_copied(tmp_path, monkeypatch):
    """The flock holds one process: a second slot refuses with the fix."""
    monkeypatch.setenv(
        guard.LOCK_FILE_ENV, str(tmp_path / "gemma-inprocess.lock"))
    fd = guard.hold_inprocess_slot()
    try:
        with pytest.raises(guard.GemmaInprocessBusy, match="gemma up"):
            guard.hold_inprocess_slot(timeout=0)
    finally:
        os.close(fd)


def test_releasing_the_fd_frees_the_slot(tmp_path, monkeypatch):
    """Closing the fd releases the flock: the slot is hold-to-reside."""
    monkeypatch.setenv(
        guard.LOCK_FILE_ENV, str(tmp_path / "gemma-inprocess.lock"))
    fd = guard.hold_inprocess_slot()
    os.close(fd)
    fd2 = guard.hold_inprocess_slot(timeout=0)
    os.close(fd2)


def _fresh_singleton():
    """A VisionModel with no weights and no slot, restored afterwards."""
    inst = vision_model.VisionModel()
    old_model, old_fd = inst._model, inst._inprocess_lock_fd
    inst._model = None
    inst._inprocess_lock_fd = None
    return inst, old_model, old_fd


def test_ensure_loaded_checks_memory_before_weights(monkeypatch):
    """The guard runs before `load`: a refusal loads nothing."""
    calls = []
    monkeypatch.setattr(
        guard, "check_memory",
        lambda: calls.append("check") or (_ for _ in ()).throw(
            guard.GemmaMemoryRefused("below the floor")))
    monkeypatch.setattr(
        guard, "hold_inprocess_slot",
        lambda timeout=None: calls.append("hold") or 999)
    monkeypatch.setattr(
        vision_model, "load",
        lambda *a: calls.append("load") or (object(), object()))
    inst, old_model, old_fd = _fresh_singleton()
    try:
        with pytest.raises(guard.GemmaMemoryRefused):
            _REAL_ENSURE_LOADED(inst)
    finally:
        inst._model, inst._inprocess_lock_fd = old_model, old_fd
    assert calls == ["check"], f"memory refusal must precede everything: {calls}"


def test_ensure_loaded_holds_the_slot_while_resident(tmp_path, monkeypatch):
    """The slot fd stays open on the instance while weights are resident."""
    monkeypatch.setenv(
        guard.LOCK_FILE_ENV, str(tmp_path / "gemma-inprocess.lock"))
    monkeypatch.setattr(guard, "available_gb", lambda: 16.0)
    monkeypatch.setattr(
        vision_model, "load", lambda *a: (object(), object()))
    inst, old_model, old_fd = _fresh_singleton()
    try:
        _REAL_ENSURE_LOADED(inst)
        assert inst._model is not None
        assert inst._inprocess_lock_fd is not None
        with pytest.raises(guard.GemmaInprocessBusy):
            guard.hold_inprocess_slot(timeout=0)
    finally:
        if inst._inprocess_lock_fd is not None:
            os.close(inst._inprocess_lock_fd)
        inst._model, inst._inprocess_lock_fd = old_model, old_fd


def test_provider_url_is_the_shim_not_a_raw_server():
    """The pipeline routes via :8080; the backend lives on :8081."""
    from library.tools.gemma_shim import ShimConfig

    assert vision_model.SERVER_URL == "http://127.0.0.1:8080"
    assert ShimConfig().backend_port == 8081


def test_no_raw_server_spawn_outside_the_shim():
    """Nothing starts a raw `mlx_vlm server`: the shim owns the backend.

    The defect: a second spawner binds its own port (or fights launchd
    for :8080) and its 7 GB is never reaped - the 9.5 h raw server of
    2026-10-22. Allowed: the shim's own backend argv, docs, and tests.
    """
    root = Path(__file__).resolve().parents[3]
    pattern = re.compile(r"mlx_vlm[\"',\s\]]+server")
    offenders = []
    for base in ("library", "ren", "scripts", "native"):
        for path in (root / base).rglob("*"):
            if path.suffix not in {".py", ".sh", ".rs"}:
                continue
            if "target" in path.parts:
                continue
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            if pattern.search(text) and path.name != "gemma_shim.py":
                offenders.append(str(path.relative_to(root)))
    assert offenders == [], f"raw mlx_vlm server spawns: {offenders}"
