"""Defect-naming contracts for the broll.insert cover operation.

Covering a marked phrase with matching B-roll is a placement the
algebra could not name (the live punch list failed it with "broll.insert
is absent from the edit algebra"), so every request needing it had no
production path. These tests pin the operation's own contract: its
declaration, its read-back verifier, its acceptance checks (including
the underlying-speech promise), its fail-closed backend registration,
and the adapter's pre-write refusals. They run without Resolve.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest


def _clip(uid, start, end, name="a.mov", **extra):
    data = {
        "unique_id": uid, "name": name, "track_type": "video",
        "track_index": 1, "track_name": "V1",
        "record_in": start, "record_out": end, "duration": end - start,
        "source_in_frame": start, "source_out_frame": end - 1,
        "source_file": name, "enabled": True, "markers": [],
    }
    data.update(extra)
    return data


def _snapshot():
    """One A-roll clip on V1, one audio clip on A1, an empty V2 cover."""
    return {
        "start_frame": 0, "end_frame": 100, "reported_fps": 30.0,
        "tracks": [
            {"type": "video", "index": 1, "name": "V1", "clips": [
                _clip("aroll-1", 0, 100, name="interview.mp4")]},
            {"type": "video", "index": 2, "name": "V2 B-roll", "clips": []},
            {"type": "audio", "index": 1, "name": "A1", "clips": [
                _clip("audio-1", 0, 100, name="interview.wav")]},
        ],
        "markers": {"timeline": [], "notes": []},
    }


def _operation(**over):
    op = {"op": "broll.insert", "unique_id": "cover-1",
          "track_type": "video", "track_index": 2,
          "record_in": 10, "record_out": 60,
          "source_in": 100, "source_out": 149, "clip": "broll.mp4"}
    op.update(over)
    return op


def test_broll_insert_is_declared_with_cover_source_and_record_placement():
    """Defect: the cover request names no algebra id, so no backend can
    prove it and routing fails open or mislabels it a clip.insert."""
    from library.tools import edit_algebra

    spec = edit_algebra.get("broll.insert")
    assert spec is not None
    assert spec.domain == "timeline_structure"
    assert spec.required == ("unique_id", "track_type", "track_index",
                             "record_in", "record_out", "source_in",
                             "source_out", "clip")
    assert spec.span(_operation(), _snapshot()) == (10, 60)
    assert spec.merge == "exclusive"
    assert spec.temporal == "local"
    assert spec.inverse.op == "clip.delete"


def test_broll_insert_verifier_detects_the_absent_write():
    """Defect: a verifier that passes on a timeline the op did not change
    would commit a lie - the op is judged on the base snapshot, where its
    cover is absent."""
    from library.tools import edit_algebra

    spec = edit_algebra.get("broll.insert")
    base = _snapshot()
    assert isinstance(spec.verify(_operation(), base), str)
    # Wrong track, wrong span, wrong source, wrong name: each refuses.
    placed = _clip("cover-1", 10, 60, name="broll.mp4",
                   source_in_frame=100, source_out_frame=149)
    wrong_track = {"type": "video", "index": 3, "name": "V3",
                   "clips": [dict(placed)]}
    assert isinstance(
        spec.verify(_operation(),
                    {**base, "tracks": [base["tracks"][0], wrong_track,
                                       base["tracks"][2]]}), str)
    assert isinstance(spec.verify(
        _operation(record_out=70),
        {**base, "tracks": [base["tracks"][0],
                            {"type": "video", "index": 2, "name": "V2",
                             "clips": [dict(placed)]},
                            base["tracks"][2]]}), str)
    assert isinstance(spec.verify(
        _operation(clip="other.mp4"),
        {**base, "tracks": [base["tracks"][0],
                            {"type": "video", "index": 2, "name": "V2",
                             "clips": [dict(placed)]},
                            base["tracks"][2]]}), str)


def test_broll_insert_verifier_accepts_the_exact_cover_placement():
    """Defect: a verifier that rejects Resolve's exclusive source endpoint
    on a fresh placement fails correct output - the record duration
    decides which endpoint form reads as the requested range."""
    from library.tools import edit_algebra

    spec = edit_algebra.get("broll.insert")
    base = _snapshot()
    for source_out in (149, 150):
        placed = _clip("cover-1", 10, 60, name="broll.mp4",
                       source_in_frame=100, source_out_frame=source_out)
        after = {**base, "tracks": [base["tracks"][0],
                                    {"type": "video", "index": 2,
                                     "name": "V2", "clips": [placed]},
                                    base["tracks"][2]]}
        assert spec.verify(_operation(), after) is None


def test_broll_insert_acceptance_names_speech_preservation_and_collateral():
    """Defect: a cover acceptance without the underlying-speech promise
    proves the placement and forgets what the instruction protects."""
    from library.tools import operation_acceptance

    contract = operation_acceptance.contract_for("broll.insert")
    assert contract["checks"] == [
        "target_identity", "source_identity", "requested_record_span",
        "requested_track", "underlying_speech_preserved",
        "unrelated_items_unchanged"]
    assert contract["structural_source"] == "timeline_readback"
    refused = operation_acceptance.evaluate(
        _operation(), readback_passed=True, evidence={})
    assert not refused["passed"]
    assert "underlying_speech_preserved" in refused["missing"]
    assert "unrelated_items_unchanged" in refused["missing"]
    accepted = operation_acceptance.evaluate(
        _operation(), readback_passed=True,
        evidence={check: True for check in contract["checks"]})
    assert accepted["passed"]


def test_broll_insert_is_registered_but_stays_fail_closed():
    """Defect: an implemented adapter that reads as production-enabled
    before live qualification routes an unproven cover onto the reel."""
    from library.tools import edit_algebra, edit_execution_adapters
    from library.tools import resolve_axi_core

    edit_execution_adapters.register_builtin_edit_executors()
    assert ("broll.insert", "resolve_axi") in set(
        resolve_axi_core.registered_edit_executors())
    status = edit_algebra.status("broll.insert")
    assert status is not None and status.compilable
    assert status.for_backend("resolve_axi") is not None
    assert not status.production_enabled
    assert edit_algebra.safety_class("broll.insert") == (
        edit_algebra.SAFETY_STRUCTURAL)
    assert edit_algebra.has_independent_readback("broll.insert")
    # The ported core carries a BrollInsert arm, so the switch judges
    # through the port like every other ported op - no carve-out.
    assert edit_algebra._beyond_ported_core({"op": "broll.insert"}) is False


def _context(operation, snapshot):
    return SimpleNamespace(
        operation=operation, snapshot=snapshot, operation_index=0,
        request=SimpleNamespace(id="request-1", project="project-1",
                                timeline="Reel 01 - opening",
                                operations=[operation]))


def test_broll_insert_refuses_non_cover_tracks_audio_and_bad_spans(
        monkeypatch):
    """Defect: a cover placed on V1 overwrites the A-roll it was meant to
    preserve; an audio-typed cover is a different operation wearing this
    one's name. Both refuse before any Resolve write."""
    from library.tools import edit_execution_adapters as adapters
    from library.tools import resolve_axi_core

    def fail(*_args, **_kwargs):
        raise AssertionError("no Resolve contact may precede the refusal")

    monkeypatch.setattr(resolve_axi_core, "execute", fail)
    monkeypatch.setattr(resolve_axi_core, "read_live_timeline", fail)
    for bad in ({"track_index": 1},
                {"track_type": "audio", "track_index": 1},
                {"clip": ""},
                {"record_in": 10, "record_out": 10},
                {"record_in": 10, "record_out": 61}):
        with pytest.raises((ValueError, RuntimeError), match="broll.insert"):
            adapters._broll_insert(_context(_operation(**bad), _snapshot()))


def test_broll_insert_revalidates_speech_and_cover_before_placing(monkeypatch):
    """Defect: covering speech that changed since preparation, or a cover
    span something else already occupies, executes the wrong instruction
    on the wrong timeline."""
    import copy

    from library.tools import edit_execution_adapters as adapters
    from library.tools import resolve_axi_core

    base = _snapshot()

    def fail(_command):
        raise AssertionError("a stale preflight must not write")

    monkeypatch.setattr(resolve_axi_core, "execute", fail)
    monkeypatch.setattr(adapters, "_project_folder",
                        lambda _context: "/tmp/project")
    stale = copy.deepcopy(base)
    stale["tracks"][0]["clips"][0]["record_out"] = 90
    monkeypatch.setattr(resolve_axi_core, "read_live_timeline",
                        lambda _project, _timeline: stale)
    with pytest.raises(ValueError, match="changed since preparation"):
        adapters._broll_insert(_context(_operation(), base))
    occupied = copy.deepcopy(base)
    occupied["tracks"][1]["clips"].append(_clip("other-cover", 50, 70))
    monkeypatch.setattr(resolve_axi_core, "read_live_timeline",
                        lambda _project, _timeline: occupied)
    with pytest.raises(ValueError, match="occupied"):
        adapters._broll_insert(_context(_operation(), base))


def test_broll_insert_claims_only_a_journaled_verified_placement(monkeypatch):
    """Defect: a cover claimed without an undo journal reference cannot be
    recovered; one claimed without read-back acceptance is command prose,
    not evidence."""
    import copy

    from library.tools import edit_execution_adapters as adapters
    from library.tools import resolve_axi_core

    base = _snapshot()
    after = copy.deepcopy(base)
    after["tracks"][1]["clips"].append(
        _clip("placed-9", 10, 60, name="broll.mp4",
              source_in_frame=100, source_out_frame=149,
              source_file="/pool/broll.mp4"))
    reads = {"count": 0}

    def live(_project, _timeline):
        reads["count"] += 1
        return copy.deepcopy(base) if reads["count"] == 1 else after

    monkeypatch.setattr(resolve_axi_core, "read_live_timeline", live)
    monkeypatch.setattr(adapters, "_project_folder",
                        lambda _context: "/tmp/project")
    calls = []
    monkeypatch.setattr(
        resolve_axi_core, "execute",
        lambda command: calls.append(command) or SimpleNamespace(
            ok=True,
            stdout=("placed the cover\njournal:\n  entry: journal-9\n"),
            stderr=""))
    receipt = adapters._broll_insert(_context(_operation(), base))
    assert ["edit", "place"] == calls[0][:2]
    assert "--require-recovery" in calls[0] and "--apply" in calls[0]
    assert calls[0][calls[0].index("--media") + 1] == "video"
    assert receipt["status"] == "committed"
    assert receipt["backend"] == "resolve_axi"
    assert receipt["operation_id"] == "broll.insert"
    assert receipt["target_identity"]["unique_id"] == "placed-9"
    assert receipt["target_identity"]["client_unique_id"] == "cover-1"
    assert receipt["acceptance_contracts"][0]["passed"] is True
    verification = receipt["execution_contract"]["verification"]
    assert verification["status"] == "verified"
    recovery = receipt["execution_contract"]["recovery"]
    assert recovery["status"] == "available"
    assert recovery["reference"]["entry_id"] == "journal-9"

    def unjournaled(_command):
        return SimpleNamespace(ok=True, stdout="placed\n", stderr="")

    monkeypatch.setattr(resolve_axi_core, "execute", unjournaled)
    reads["count"] = 0
    with pytest.raises(RuntimeError, match="journal"):
        adapters._broll_insert(_context(_operation(), base))


def test_broll_insert_inverse_terminates_in_the_delete_insert_pair():
    """Defect: a cover with no undo leaves the A-roll buried - its inverse
    deletes the placed item through the proven delete path."""
    from library.tools import edit_algebra

    spec = edit_algebra.get("broll.insert")
    assert spec is not None and spec.inverse.op == "clip.delete"
    seen, current = set(), "broll.insert"
    while current is not None:
        assert current not in seen
        seen.add(current)
        step = edit_algebra.ALGEBRA[current].inverse
        if step.self_inverse or step.op is None:
            break
        current = step.op
    assert seen == {"broll.insert", "clip.delete", "clip.insert"}


def test_broll_insert_previews_as_a_cover_placement_without_touching_speech():
    """Defect: a program preview that cannot name the cover's effect
    refuses the whole program, even though the placement is exactly a
    clip.insert-shaped write onto a cover track."""
    from library.tools import edit_preview

    base = _snapshot()
    op = _operation()
    artifact = edit_preview.preview_program(
        [{"op": "broll.insert", "params": dict(op)}],
        {**base, "tracks": [dict(track) for track in base["tracks"]]})
    assert artifact["ops"][0]["op"] == "broll.insert"
    assert artifact["total_duration_change"] == 0
    placed = artifact["ops"][0]["after"]
    assert placed["record_in"] == 10 and placed["record_out"] == 60
    assert placed["track_index"] == 2
