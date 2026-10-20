"""Semantic adapter bindings for existing Resolve editing commands."""

from pathlib import Path
from types import SimpleNamespace

import pytest

from library.tools import edit_execution_adapters as adapters


def _context(operation):
    return SimpleNamespace(
        operation=operation,
        snapshot={"reported_fps": 30.0, "tracks": [{
            "type": "video", "index": 2,
            "clips": [{
                "unique_id": "clip-17", "record_in": 20, "record_out": 80,
                "source_in_frame": 5, "source_out_frame": 64,
                "duration": 60, "source_file": "/media/music.wav",
            }],
        }]},
        request=SimpleNamespace(id="request-1", project="project-1",
                                timeline="Reel 01 - opening",
                                operations=[operation]),
    )


def _verified_touchup_receipt(spec):
    operation = spec["acceptance_operations"][0]
    return {
        "transaction": {"verification": {"status": "verified"}},
        "acceptance_contracts": [{
            "operation": operation["op"], "passed": True,
            "results": [{"check": "operation_readback_matches_request",
                         "passed": True}],
        }],
    }


def test_trim_adapter_converts_record_edges_to_the_existing_source_trim(monkeypatch):
    from library.tools import resolve_axi_core

    calls = []
    after = {"reported_fps": 30.0, "tracks": [{
        "type": "video", "index": 2, "clips": [{
            "unique_id": "clip-17", "record_in": 24, "record_out": 76,
            "source_in_frame": 9, "source_out_frame": 60,
            "duration": 52, "source_file": "/media/music.wav",
        }],
    }]}
    monkeypatch.setattr(resolve_axi_core, "execute", lambda command: (
        calls.append(command) or SimpleNamespace(
            ok=True, stdout="command completed", stderr="")))
    monkeypatch.setattr(resolve_axi_core, "read_live_timeline",
                        lambda *_args: after)

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
    assert receipt["acceptance_contracts"][0]["passed"]


def test_caption_fix_refuses_without_readable_title_text():
    with pytest.raises(RuntimeError, match="title text.*evidence is missing"):
        adapters._caption_fix(_context({
            "op": "caption_fix", "unique_id": "clip-17",
            "text": "Correct words",
        }))


def test_target_identity_must_resolve_exactly_once():
    snapshot = {"tracks": [
        {"type": "video", "index": 1,
         "clips": [{"unique_id": "duplicate"}]},
        {"type": "video", "index": 2,
         "clips": [{"unique_id": "duplicate"}]},
    ]}

    with pytest.raises(ValueError, match="resolved to 2 timeline items"):
        adapters._target_position(snapshot, "duplicate")


def test_prepared_position_is_refused_after_prior_structural_mutation():
    context = _context({"op": "clip.trim", "unique_id": "clip-17"})
    context.compiled = SimpleNamespace(operations=(
        {"op": "clip.delete", "unique_id": "clip-99"},
        context.operation,
    ))
    context.operation_index = 1

    with pytest.raises(ValueError, match="cannot safely reuse a prepared"):
        adapters._target_position_for_context(context, "clip-17")


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


def test_capability_discovery_reports_staged_resize_without_qualification():
    from library.tools import edit_algebra

    resize = edit_algebra.capability("overlay.resize")
    assert [backend["id"] for backend in resize["backends"]] == [
        "reel_touchup"]
    assert resize["implemented"] is True
    assert resize["production_enabled"] is False
    assert any("live Resolve qualification remains deferred" in gap
               for gap in resize["route_evaluation"]["gaps"])
    assert edit_algebra.capability("clip.slip")["backends"][0]["id"] == (
        "reel_touchup")


def test_ripple_adapter_refuses_retime_semantics(monkeypatch):
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
    monkeypatch.setattr(reel_touchup, "apply_touchup", lambda *_args: (
        calls.append(_args)))

    with pytest.raises(ValueError, match="canonical ripple shifts later clips"):
        adapters._touchup(_context_obj)
    assert not calls


def test_audio_gain_refuses_without_independent_audio_render_evidence():
    with pytest.raises(RuntimeError, match="independent audio-render evidence"):
        adapters._audio_gain(_context({
            "op": "audio.gain", "unique_id": "clip-17", "db": 3.0,
        }))


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
        calls.append(spec) or _verified_touchup_receipt(spec)))

    adapters._touchup(context)

    assert calls[0]["edits"] == [{
        "op": "slip", "row": "V2", "item": 0, "delta_frames": 7,
    }]
    assert calls[0]["acceptance_operations"] == [context.operation]


def test_audio_fade_adapter_converts_seconds_using_timeline_rate(monkeypatch):
    from library.tools import project_registry, reel_proposal, reel_touchup

    operation = {"op": "audio.fade", "unique_id": "clip-17",
                 "fade_in": 0.5, "fade_out": 1.0}
    context = _context(operation)
    context.snapshot["tracks"][0]["type"] = "audio"
    context.snapshot["tracks"][0]["index"] = 1
    monkeypatch.setattr(project_registry, "resolve_project_path",
                        lambda _project: Path("/tmp/project/project.yaml"))
    proposal = SimpleNamespace(number=1, timeline_name="Reel 01 - opening")
    monkeypatch.setattr(reel_proposal, "proposal_path",
                        lambda _folder: "/tmp/project/proposal.json")
    monkeypatch.setattr(reel_proposal, "read_proposal",
                        lambda _path: [proposal])
    calls = []
    monkeypatch.setattr(reel_touchup, "apply_touchup", lambda _folder, spec: (
        calls.append(spec) or _verified_touchup_receipt(spec)))

    adapters._touchup(context)

    assert calls[0]["edits"] == [{
        "op": "audio_fade", "row": "A1", "item": 0,
        "fade_in_frames": 15, "fade_out_frames": 30,
    }]


def test_audio_gain_uses_exact_item_and_protected_otio_candidate(monkeypatch):
    from library.tools import project_registry
    from library.tools.execution import deliver_audio_mix

    operation = {"op": "audio.gain", "unique_id": "clip-17", "db": -6.0}
    context = _context(operation)
    context.snapshot["tracks"][0]["type"] = "audio"
    context.snapshot["tracks"][0]["index"] = 1
    monkeypatch.setattr(project_registry, "resolve_project_path",
                        lambda _project: None)
    monkeypatch.setattr(project_registry, "get_project", lambda _project: (
        SimpleNamespace(project_root=Path("/tmp/project"),
                         resolve=SimpleNamespace(
                             project_name="Resolve Project"))))
    calls = []
    report = {
        "delivered": True,
        "full_preservation_verified": True,
        "complaints": [],
        "applied": [{"matched_start_frame": 20}],
    }
    monkeypatch.setattr(deliver_audio_mix, "deliver_semantic_gain",
                        lambda *args, **kwargs: (
                            calls.append((args, kwargs)) or report))

    result = adapters._audio_gain(context)

    assert calls[0][0][0:3] == (
        "/tmp/project", "Resolve Project", "Reel 01 - opening")
    assert calls[0][0][3] == {
        "role": "sfx", "source_file": "/media/music.wav",
        "start_frame": 20, "level_db": -6.0, "keyframes": {},
        "label": "music.wav",
    }
    assert calls[0][1] == {"fps": 30.0}
    assert result["execution_contract"]["verification"]["status"] == "verified"


def test_audio_fade_stages_native_frames_and_preserves_other_item_fades(
        monkeypatch):
    from library.tools import reel_read, reel_touchup

    class PoolItem:
        def GetClipProperty(self, name):
            return "/media/music.wav" if name == "File Path" else None

    class AudioItem:
        def __init__(self, fades):
            self.fades = dict(fades)
            self.writes = []

        def GetMediaPoolItem(self):
            return PoolItem()

        def GetSourceStartFrame(self):
            return 0

        def GetSourceEndFrame(self):
            return 39

        def GetStart(self):
            return 10

        def GetEnd(self):
            return 50

        def GetFades(self):
            return dict(self.fades)

        def SetFades(self, fades):
            self.writes.append(dict(fades))
            self.fades = dict(fades)
            return True

    class Timeline:
        def __init__(self, item):
            self.item = item

        def GetTrackCount(self, track_type):
            return 1 if track_type == "audio" else 0

        def GetItemListInTrack(self, track_type, _index):
            return [self.item] if track_type == "audio" else []

    source_item = AudioItem({"FadeIn": 2, "FadeOut": 3})
    staged_item = AudioItem({"FadeIn": 2, "FadeOut": 3})
    source, staged = Timeline(source_item), Timeline(staged_item)
    monkeypatch.setattr(reel_read, "live_items", lambda timeline: [{
        "type": "audio", "index": 1, "items": [timeline.item],
    }])
    entry = {
        "kind": "audio_fade", "row": "A1", "item_index": 0,
        "record_frame": 10, "record_out": 50, "source_in": 0,
        "source_out": 39, "source_file": "/media/music.wav",
        "fade_in_frames": 15, "fade_out_frames": 20,
    }

    receipt = reel_touchup._apply_in_place(
        None, staged,
        reel_touchup.Qualification("composed", in_place=[entry]),
        "/tmp/comps", "journal-1", source=source)

    assert source_item.GetFades() == {"FadeIn": 2, "FadeOut": 3}
    assert staged_item.GetFades() == {"FadeIn": 15, "FadeOut": 20}
    assert receipt["audio_fades"][0]["verified"] is True


def test_transition_adapter_resolves_exact_adjacent_cut(monkeypatch):
    from library.tools import project_registry, reel_proposal, reel_touchup

    operation = {
        "op": "transition",
        "carrier": {
            "outgoing_unique_id": "clip-out",
            "incoming_unique_id": "clip-in",
            "cut_frame": 99,
        },
        "transition": {
            "type": "cross_dissolve", "category": "simple",
            "duration_frames": 12, "alignment": "center",
        },
    }
    context = _context(operation)
    context.snapshot["tracks"] = [{
        "type": "video", "index": 3, "clips": [
            {"unique_id": "clip-out", "record_in": 40, "record_out": 99,
                "source_in_frame": 0, "source_out_frame": 59, "duration": 60},
            {"unique_id": "clip-in", "record_in": 100, "record_out": 159,
                "source_in_frame": 0, "source_out_frame": 59, "duration": 60},
        ],
    }]
    monkeypatch.setattr(project_registry, "resolve_project_path",
                        lambda _project: Path("/tmp/project/project.yaml"))
    proposal = SimpleNamespace(number=1, timeline_name="Reel 01 - opening")
    monkeypatch.setattr(reel_proposal, "proposal_path",
                        lambda _folder: "/tmp/project/proposal.json")
    monkeypatch.setattr(reel_proposal, "read_proposal",
                        lambda _path: [proposal])
    calls = []
    monkeypatch.setattr(reel_touchup, "apply_touchup", lambda _folder, spec: (
        calls.append(spec) or _verified_touchup_receipt(spec)))

    adapters._touchup(context)

    assert calls[0]["edits"] == [{
        "op": "native_transition", "row": "V3", "item": 1,
        "outgoing_unique_id": "clip-out", "incoming_unique_id": "clip-in",
        "cut_frame": 99, "transition_type": "cross_dissolve",
        "category": "simple", "duration_frames": 12,
        "alignment": "center",
    }]


def test_transition_qualification_refuses_a_different_or_nonadjacent_cut():
    from library.tools import reel_touchup

    tracks = [{
        "type": "video", "index": 3, "clips": [
            {"unique_id": "clip-out", "record_in": 40, "record_out": 99,
                "duration": 60},
            {"unique_id": "clip-in", "record_in": 100, "record_out": 159,
                "duration": 60},
        ],
    }]
    edit = {
        "row": "V3", "item": 1,
        "outgoing_unique_id": "clip-out", "incoming_unique_id": "clip-in",
        "cut_frame": 98, "transition_type": "Cross Dissolve",
        "category": "simple", "duration_frames": 12,
    }

    with pytest.raises(reel_touchup.TouchupRefused, match="exact cut"):
        reel_touchup._op_native_transition(
            edit, 1, tracks, [], [], [], [], [], set(), [], [])


def test_transition_addition_is_staged_and_reads_back_exact_cut(monkeypatch):
    from library.tools import reel_read, reel_touchup, resolve_api_capabilities

    class Clip:
        def __init__(self, uid, start, end):
            self.uid, self.start, self.end = uid, start, end

        def GetUniqueId(self):
            return self.uid

        def GetStart(self):
            return self.start

        def GetEnd(self):
            return self.end

        def AddTransition(self, payload):
            assert payload == {
                "type": "Cross Dissolve", "category": "simple",
                "position": "start", "alignment": "center", "duration": 12,
            }
            return SimpleNamespace(
                GetName=lambda: "Cross Dissolve",
                GetStart=lambda: 93,
                GetEnd=lambda: 104,
                GetDuration=lambda: 12,
            )

    class Timeline:
        def __init__(self):
            self.items = [Clip("clip-out", 40, 99), Clip("clip-in", 100, 159)]

        def GetItemListInTrack(self, track_type, index):
            assert (track_type, index) == ("video", 3)
            return list(self.items)

    source, staged = Timeline(), Timeline()
    monkeypatch.setattr(reel_read, "live_items", lambda timeline: [{
        "type": "video", "index": 3, "items": timeline.items,
    }])
    monkeypatch.setattr(resolve_api_capabilities, "callable_method",
                        lambda obj, name: getattr(obj, name, None))
    entry = {
        "kind": "native_transition", "row": "V3", "item_index": 1,
        "record_frame": 100, "cut_frame": 99,
        "outgoing_unique_id": "clip-out", "incoming_unique_id": "clip-in",
        "duration_frames": 12, "transition_type": "Cross Dissolve",
        "category": "simple", "alignment": "center",
    }

    receipt = reel_touchup._apply_in_place(
        None, staged,
        reel_touchup.Qualification("composed", in_place=[entry]),
        "/tmp/comps", "journal-1", source=source)

    assert receipt["transitions"] == [{
        "row": "V3", "cut_frame": 99,
        "outgoing_unique_id": "clip-out", "incoming_unique_id": "clip-in",
        "type": "Cross Dissolve", "category": "simple",
        "record_in": 93, "record_out": 104,
        "duration_frames": 12, "verified": True,
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
