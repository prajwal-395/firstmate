"""Defect-naming contracts for the canonical `clip.remove` operation.

The ordinary request "Remove the selected B-roll shot and close its gap
while keeping adjacent speech and sync intact" failed with "no production
path for clip.remove: clip.remove is absent from the edit algebra". This
lane owns that operation: a ripple delete (the `clip.delete` physical
write plus the gap close) with its own acceptance contract. Each test
below names the defect it would catch if the operation regressed.
"""

from __future__ import annotations


def _clip(uid, start, end, **extra):
    data = {
        "unique_id": uid, "name": f"{uid}.mov", "track_type": "video",
        "track_index": 1, "track_name": "V1",
        "record_in": start, "record_out": end, "duration": end - start,
        "source_in_frame": start, "source_out_frame": end,
        "left_offset": start, "right_offset": 0,
        "source_file": f"{uid}.mov", "media_type": "Video",
        "source_frames": end - start, "media_pool_item_id": f"pool-{uid}",
        "clip_color": "", "flags": [], "enabled": True,
        "transform": {"Pan": 0.0, "Tilt": 0.0, "ZoomX": 1.0, "ZoomY": 1.0},
        "fusion": {"comp_count": 0, "comp_names": [], "media_windows": []},
        "markers": [], "speaker": None,
    }
    data.update(extra)
    return data


def _base_snapshot():
    return {
        "start_frame": 0, "end_frame": 300, "fps": 30.0,
        "width": 1920, "height": 1080, "clip_count": 3,
        "tracks": [
            {"type": "video", "index": 1, "name": "V1", "speaker": None,
             "clips": [_clip("a", 0, 100), _clip("b", 100, 200),
                       _clip("c", 200, 300)]},
            {"type": "audio", "index": 1, "name": "A1", "speaker": None,
             "clips": []},
        ],
        "markers": {"timeline": [], "notes": []},
    }


def test_clip_remove_is_a_registered_canonical_operation():
    """Defect: the ripple remove has no canonical op, so the ordinary
    request fails with "clip.remove is absent from the edit algebra"
    instead of routing to a production path."""
    from library.tools import edit_algebra

    spec = edit_algebra.get("clip.remove")
    assert spec is not None
    assert spec.required == ("unique_id",)
    assert spec.temporal == "ripple"
    assert spec.item is True
    assert callable(spec.apply)
    assert edit_algebra.safety_class("clip.remove") == (
        edit_algebra.SAFETY_STRUCTURAL)
    assert edit_algebra.has_independent_readback("clip.remove") is True
    # The inverse terminates: remove -> insert -> delete -> insert is
    # the mutual pair, never a longer cycle and never an unproven name.
    assert spec.inverse.op == "clip.insert"
    assert spec.inverse.op in edit_algebra.ALGEBRA


def test_clip_remove_span_covers_the_ripple_it_declares():
    """Defect: a span covering only the removed clip would let patch
    validation miss the downstream move the ripple performs."""
    from library.tools import edit_algebra

    base = _base_snapshot()
    op = {"unique_id": "b"}
    span = edit_algebra.get("clip.remove").span(op, base)
    assert span == (100, 300)


def test_clip_remove_backend_refuses_resolves_timeline_wide_ripple():
    """Defect: the EditPatch backend used Resolve's global ripple and
    could move or delete content outside the requested track."""
    from library.tools import edit_algebra

    class Timeline:
        def DeleteClips(self, *_args):
            raise AssertionError("unsafe timeline-wide ripple was called")

    try:
        edit_algebra.get("clip.remove").apply(
            {"unique_id": "b"}, Timeline(), object())
    except RuntimeError as exc:
        assert "track-scoped gap closure is unavailable" in str(exc)
    else:
        raise AssertionError("unsupported clip.remove did not refuse")


def test_clip_remove_verify_detects_the_absent_write():
    """Defect: a verifier that passes on a timeline the op did not
    change would commit a lie - absence must read back, not be
    assumed."""
    from library.tools import edit_algebra

    base = _base_snapshot()
    failure = edit_algebra.get("clip.remove").verify(
        {"unique_id": "b"}, base)
    assert isinstance(failure, str) and failure


def test_clip_remove_acceptance_names_gap_and_continuity_checks():
    """Defect: claiming success from the absence alone while the gap
    stays open or the downstream continuity breaks."""
    from library.tools import operation_acceptance

    contract = operation_acceptance.contract_for("clip.remove")
    assert contract["checks"] == [
        "target_absent", "gap_closed",
        "downstream_continuity_preserved", "unrelated_items_unchanged",
    ]
    incomplete = operation_acceptance.evaluate(
        {"op": "clip.remove", "unique_id": "b"}, readback_passed=True,
        evidence={"target_absent": True,
                  "unrelated_items_unchanged": True})
    assert incomplete["passed"] is False
    assert "gap_closed" in incomplete["missing"]
    assert "downstream_continuity_preserved" in incomplete["missing"]
    complete = operation_acceptance.evaluate(
        {"op": "clip.remove", "unique_id": "b"}, readback_passed=True,
        evidence={"target_absent": True, "gap_closed": True,
                  "downstream_continuity_preserved": True,
                  "unrelated_items_unchanged": True})
    assert complete["passed"] is True


def test_clip_remove_never_claims_local_delete_as_gap_closure():
    """Defect: a requested gap close was silently changed into a local
    delete on a cover track, leaving the downstream continuity broken."""
    from library.tools import operation_acceptance

    contract = operation_acceptance.contract_for("clip.remove")
    assert contract["checks"] == [
        "target_absent", "gap_closed",
        "downstream_continuity_preserved", "unrelated_items_unchanged",
    ]


def test_clip_remove_has_a_registered_resolve_axi_backend():
    """Defect: an algebra op with no registered adapter is a name with
    no production path - the router can select it but nothing executes
    it, and a later write reuses a stale prepared position."""
    from library.tools import (
        edit_algebra,
        edit_execution_adapters,
        resolve_axi_core,
    )

    row = edit_algebra.status("clip.remove").for_backend("resolve_axi")
    assert row is not None and row.compilable is True
    assert "clip.remove" in (
        edit_execution_adapters._POSITION_INVALIDATING_OPERATIONS)
    edit_execution_adapters.register_builtin_edit_executors()
    assert ("clip.remove", "resolve_axi") in set(
        resolve_axi_core.registered_edit_executors())


def test_clip_remove_stays_fail_closed_until_live_qualification():
    """Defect: an unqualified ripple reaching production would move
    every later cut on measured evidence nobody recorded."""
    from library.tools import edit_algebra, execution_router

    assert edit_algebra.status("clip.remove").production_enabled is False
    assert all(row.production_enabled is False
               for row in edit_algebra.status("clip.remove").backends)
    try:
        execution_router.route_axi_dispatch(
            [{"op": "clip.remove", "unique_id": "b"}])
    except execution_router.RoutingRefused as exc:
        assert "clip.remove" in str(exc)
    else:
        raise AssertionError(
            "clip.remove routed under production without qualification")
