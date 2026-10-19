"""Semantic adapter bindings for existing Resolve editing commands."""

from types import SimpleNamespace

import pytest

from library.tools import edit_execution_adapters as adapters


def _context(operation):
    return SimpleNamespace(
        operation=operation,
        snapshot={"tracks": [{
            "type": "video", "index": 2,
            "clips": [{
                "unique_id": "clip-17", "record_in": 20, "record_out": 80,
                "source_in_frame": 5, "source_out_frame": 64,
            }],
        }]},
        request=SimpleNamespace(id="request-1", project="project-1",
                                timeline="Reel 01 - opening"),
    )


def test_trim_adapter_converts_record_edges_to_the_existing_source_trim(monkeypatch):
    from library.tools import resolve_axi_core

    calls = []
    monkeypatch.setattr(resolve_axi_core, "execute", lambda command: (
        calls.append(command) or SimpleNamespace(
            ok=True, stdout="verified: yes (read back equal)", stderr="")))

    receipt = adapters._trim(_context({
        "op": "clip.trim", "unique_id": "clip-17",
        "in_frame": 24, "out_frame": 76,
    }))

    assert calls == [[
        "edit", "trim", "--track", "V2", "--index", "0",
        "--source-in", "9", "--source-out", "60", "--apply",
        "--project", "project-1", "--timeline", "Reel 01 - opening",
    ]]
    assert receipt["execution_contract"]["verification"]["status"] == "verified"


def test_caption_fix_uses_exact_unique_id_position_and_readback(monkeypatch):
    from library.tools import resolve_axi_core

    calls = []
    monkeypatch.setattr(resolve_axi_core, "execute", lambda command: (
        calls.append(command) or SimpleNamespace(
            ok=True, stdout="verified: yes (read back equal)", stderr="")))

    adapters._caption_fix(_context({
        "op": "caption_fix", "unique_id": "clip-17", "text": "Correct words",
    }))

    assert calls == [[
        "edit", "title", "--track", "V2", "--index", "0",
        "--text", "Correct words", "--apply", "--project", "project-1",
        "--timeline", "Reel 01 - opening",
    ]]


def test_target_identity_must_resolve_exactly_once():
    snapshot = {"tracks": [
        {"type": "video", "index": 1,
         "clips": [{"unique_id": "duplicate"}]},
        {"type": "video", "index": 2,
         "clips": [{"unique_id": "duplicate"}]},
    ]}

    with pytest.raises(ValueError, match="resolved to 2 timeline items"):
        adapters._target_position(snapshot, "duplicate")


def test_p0_adapters_register_only_the_implemented_operation_backend_pairs():
    from library.tools import resolve_axi_core

    adapters.register_builtin_edit_executors()

    assert {
        ("clip.trim", "resolve_axi"),
        ("caption_fix", "resolve_axi"),
        ("clip.move", "reel_touchup"),
        ("overlay.add", "reel_touchup"),
        ("overlay.remove", "reel_touchup"),
        ("overlay.swap_pixels", "reel_touchup"),
    } <= set(resolve_axi_core.registered_edit_executors())
