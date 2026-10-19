"""Semantic adapter bindings for existing Resolve editing commands."""

from pathlib import Path
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
                "duration": 60,
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
        ("clip.delete", "resolve_axi"),
        ("clip.trim", "resolve_axi"),
        ("caption_fix", "resolve_axi"),
        ("clip.move", "reel_touchup"),
        ("clip.ripple", "reel_touchup"),
        ("clip.slip", "reel_touchup"),
        ("overlay.add", "reel_touchup"),
        ("overlay.remove", "reel_touchup"),
        ("overlay.swap_pixels", "reel_touchup"),
    } <= set(resolve_axi_core.registered_edit_executors())


def test_capability_discovery_explains_missing_routes_without_claiming_resize():
    from library.tools import edit_algebra

    resize = edit_algebra.capability("overlay.resize")
    assert resize["backends"] == []
    assert "protected adapter" in " ".join(
        resize["route_evaluation"]["gaps"])
    assert edit_algebra.capability("clip.slip")["backends"][0]["id"] == (
        "reel_touchup")


def test_ripple_adapter_converts_delta_to_a_staged_duration(monkeypatch):
    from library.tools import project_registry, reel_proposal, reel_touchup

    _context_obj = _context({
        "op": "clip.ripple", "unique_id": "clip-17", "delta_frames": 12,
    })
    _context_obj.request = SimpleNamespace(
        id="request-1", project="project-1", timeline="Reel 01 - opening")
    monkeypatch.setattr(project_registry, "resolve_project_path",
                        lambda _project: Path("/tmp/project/project.yaml"))
    proposal = SimpleNamespace(number=1, timeline_name="Reel 01 - opening")
    monkeypatch.setattr(reel_proposal, "proposal_path",
                        lambda _folder: "/tmp/project/proposal.json")
    monkeypatch.setattr(reel_proposal, "read_proposal",
                        lambda _path: [proposal])
    calls = []
    monkeypatch.setattr(reel_touchup, "apply_touchup", lambda _folder, spec: (
        calls.append(spec) or {"transaction": {
            "verification": {"status": "verified"}}}))

    adapters._touchup(_context_obj)

    assert calls[0]["edits"] == [{
        "op": "retime", "row": "V2", "item": 0, "duration": 72,
    }]


def test_slip_adapter_routes_source_window_delta_to_staged_touchup(monkeypatch):
    from library.tools import project_registry, reel_proposal, reel_touchup

    context = _context({
        "op": "clip.slip", "unique_id": "clip-17", "delta_frames": 7,
    })
    monkeypatch.setattr(project_registry, "resolve_project_path",
                        lambda _project: Path("/tmp/project/project.yaml"))
    proposal = SimpleNamespace(number=1, timeline_name="Reel 01 - opening")
    monkeypatch.setattr(reel_proposal, "proposal_path",
                        lambda _folder: "/tmp/project/proposal.json")
    monkeypatch.setattr(reel_proposal, "read_proposal",
                        lambda _path: [proposal])
    calls = []
    monkeypatch.setattr(reel_touchup, "apply_touchup", lambda _folder, spec: (
        calls.append(spec) or {"transaction": {
            "verification": {"status": "verified"}}}))

    adapters._touchup(context)

    assert calls[0]["edits"] == [{
        "op": "slip", "row": "V2", "item": 0, "delta_frames": 7,
    }]


def test_slip_touchup_keeps_record_span_and_refuses_source_overrun():
    from library.tools import reel_touchup

    tracks = [{
        "type": "video", "index": 2, "clips": [{
            "record_in": 20, "record_out": 80, "duration": 60,
            "left_offset": 5, "source_frames": 100, "name": "shot",
        }],
    }]
    changes = []
    reel_touchup._op_slip(
        {"row": "V2", "item": 0, "delta_frames": 7}, 0,
        tracks, {}, changes, [], [], [], [], [], [])
    assert changes[0].record_frame == 20
    assert changes[0].duration == 60
    assert changes[0].left_offset == 12
    assert changes[0].right_offset == 28

    with pytest.raises(reel_touchup.TouchupRefused, match="source frames"):
        reel_touchup._op_slip(
            {"row": "V2", "item": 0, "delta_frames": 40}, 0,
            tracks, {}, [], [], [], [], [], [], [])
