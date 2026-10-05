"""Behavioral capability resolution beyond the graph-wide contract audit.
"""
from __future__ import annotations
from dataclasses import replace
import pytest
from library.tools import dag_adapter, operations
from library.tools import capabilities
from library.tools import edit_patch
from library.tools import machine_needs as mn
from library.tools import patch_algebra
from library.tools import processes


def test_a_capability_without_a_legacy_node_refuses_node_keyed_questions():
    """A missing node must not read as a capability with no requirements."""
    scan = operations.get("footage.scan")
    nodeless = replace(scan, owning_dir="step_9_99_nodeless")
    with pytest.raises(dag_adapter.NoLegacyNode, match="footage.scan"):
        nodeless.requires  # noqa: B018


def test_gathering_demands_only_what_a_capability_consumes(tmp_path):
    """Gathering and its refusal agree with the capability's declared reads."""
    for capability in (
        "reel.touchup",
        "transcript.splice",
        "subtitles.render_segment",
    ):
        assert operations.get(capability).consumes == ()
        operations.get(capability).gather(str(tmp_path))
    with pytest.raises(RuntimeError, match="audio_spine"):
        operations.get("subtitles.plan").gather(str(tmp_path))


# --------------------------------------------------------------------------
# From test_machine_needs.py
#
# The capability -> machine-needs table holds to the registry it mirrors.
#
# `machine_needs` is declared rather than derived (doctor cannot import
# the registry: it imports torch), so each of these names the drift that
# would make `ren doctor --for` lie.

def test_every_capability_has_a_needs_row_and_nothing_else_does():
    registry = set(capabilities.ids())
    table = set(mn.CAPABILITY_NEEDS)
    assert registry - table == set(), "capabilities doctor cannot answer for"
    assert table - registry == set(mn.FRONT_DOOR), "rows naming no capability"


def test_every_derived_environment_requirement_is_a_required_need():
    for spec in capabilities.all():
        required = mn.needs_of(spec.id).requires
        for env in spec.assumes_machine:
            assert env in mn.ENV_REQUIREMENT_NEED, (
                f"{spec.id}: {env} has no need in ENV_REQUIREMENT_NEED")
            assert mn.ENV_REQUIREMENT_NEED[env] in required, (
                f"{spec.id} refuses without {env}, but its needs row does "
                f"not require {mn.ENV_REQUIREMENT_NEED[env]}")


def test_a_capability_that_needs_a_model_answer_requires_a_harness():
    for spec in capabilities.all():
        if spec.needs_model_answer:
            assert "chat_harness" in mn.needs_of(spec.id).requires, spec.id


def test_the_table_uses_only_its_own_vocabulary():
    assert mn.problems() == []


def test_a_failing_optional_need_degrades_and_a_required_one_refuses():
    assert mn.availability("footage.search", {"model.search_embedding"})[0] \
        == mn.DEGRADED
    assert mn.availability("render.build", {"resolve.studio"}) \
        == (mn.UNAVAILABLE, ("resolve.studio",), {})
    assert mn.availability("project.inspect", {"resolve.studio"})[0] \
        == mn.AVAILABLE


# --------------------------------------------------------------------------
# From test_processes.py
#
# There is more than one process, and exactly one module knows.
#
# `library/processes/reels/` is the second process beside `edit_video`
# (`docs/REEL_BUILD_HAS_NO_OWNING_NODE.md`). What makes two processes safe:
# node ids unique across both, and an unknown node refused by name rather
# than answered with an empty graph.

# ── The property that makes merging safe ────────────────────────────


def test_node_ids_are_unique_across_every_process(monkeypatch):
    """Node ids key the two ledgers, the run status, the review gate, the
    marker routing, the step export and `step_outputs`; a derived
    requirement is NAMED after one. Two processes sharing an id would
    give two different steps one slot in all of them - and the check
    refuses that."""
    processes.assert_node_ids_are_unique()
    assert len(processes.node_owners()) == sum(
        len(processes.load_dag(pid)["nodes"])
        for pid in processes.process_ids())

    monkeypatch.setattr(processes, "load_dag", lambda pid: {
        "nodes": [{"id": "shared", "name": "x", "step_ref": "steps/x"}]
    })
    with pytest.raises(processes.ProcessError, match="unique"):
        processes.assert_node_ids_are_unique()


# ── The lookup an operation depends on ──────────────────────────────


def test_an_unknown_node_is_refused_by_name():
    """A silent None here would give `Operation.gather` an empty graph and
    hand the step an empty dict, which is the confidently wrong answer."""
    with pytest.raises(processes.ProcessError, match="no process declares"):
        processes.dag_declaring("a_node_that_does_not_exist")
    assert processes.process_of("a_node_that_does_not_exist") is None


# --------------------------------------------------------------------------
# The edit algebra is a registry invariant.
#
# `library/tools/edit_algebra.py` owns the union vocabulary; the five
# existing vocabularies are views over it. These tests enumerate the
# algebra and fail on the defects the union exists to prevent: an op that
# cannot be undone, an op whose span or read-back verifier cannot judge
# it, a review term with no precise reversible op, and a vocabulary that
# drifted from the algebra.


def _clip(uid, start, end, source_file="a.mov", **extra):
    data = {
        "unique_id": uid, "name": source_file, "track_type": "video",
        "track_index": 1, "track_name": "V1",
        "record_in": start, "record_out": end, "duration": end - start,
        "source_in_frame": start, "source_out_frame": end,
        "left_offset": start, "right_offset": 0,
        "source_file": source_file, "media_type": "Video",
        "source_frames": end - start, "media_pool_item_id": f"pool-{uid}",
        "clip_color": "", "flags": [], "enabled": True,
        "transform": {"Pan": 0.0, "Tilt": 0.0, "ZoomX": 1.0, "ZoomY": 1.0},
        "fusion": {"comp_count": 0, "comp_names": [], "media_windows": []},
        "markers": [], "speaker": None,
    }
    data.update(extra)
    return data


def _base_snapshot():
    """A two-clip video timeline and one audio row, as the shadow holds it.

    Clip `d` is a compound item: it exists so `compound.flatten` has
    something to flatten (its verifier checks the compound is gone).
    """
    return {
        "start_frame": 0, "end_frame": 100, "fps": 30.0,
        "width": 1920, "height": 1080, "clip_count": 3,
        "tracks": [
            {"type": "video", "index": 1, "name": "V1", "speaker": None,
             "clips": [_clip("a", 0, 50), _clip("b", 50, 100,
                                            source_file="b.mov"),
                       _clip("d", 0, 100, source_file="compound.mov")]},
            {"type": "audio", "index": 1, "name": "A1", "speaker": None,
             "clips": []},
        ],
        "markers": {"timeline": [], "notes": []},
    }


def test_every_algebra_op_declares_a_consistent_inverse():
    """Defect: an op that cannot be undone, or an inverse that does not
    undo - semantic undo (C-04) addresses acts by what changed, so every
    op needs an inverse that restores the timeline.

    The inverse graph must terminate: following the chain from any op
    reaches a self-inverse value setter or an irreversible op, through
    mutual pairs (insert/delete, split/join) but never a longer cycle.
    """
    from library.tools import edit_algebra
    for op in edit_algebra.ops():
        inverse = op.inverse
        if inverse.op is not None:
            assert inverse.op in edit_algebra.ALGEBRA, (
                f"{op.id}: inverse {inverse.op!r} is not in the algebra")
        elif not inverse.self_inverse:
            assert inverse.reason, (
                f"{op.id}: irreversible without a reason")
        seen = set()
        path = []
        current = op.id
        while current is not None:
            if current in seen:
                cycle = path[path.index(current):]
                assert len(cycle) == 2, (
                    f"{op.id}: inverse chain cycles through {cycle} "
                    f"without terminating in a mutual pair")
                break
            seen.add(current)
            path.append(current)
            step = edit_algebra.ALGEBRA[current].inverse
            if step.self_inverse or step.op is None:
                break
            current = step.op


def test_every_algebra_op_has_a_span_and_a_readback_verifier():
    """Defect: an op whose span cannot be computed cannot be scheduled or
    validated, and an op whose verifier cannot judge the read-back would
    commit a lie."""
    from library.tools import edit_algebra
    for op in edit_algebra.ops():
        assert callable(op.span), f"{op.id}: no span function"
        assert callable(op.verify), f"{op.id}: no read-back verifier"
        assert op.domain in edit_patch.CONFLICT_DOMAINS, (
            f"{op.id}: domain {op.domain!r} is not a conflict domain")
        assert op.temporal in patch_algebra.TEMPORAL_EFFECTS, (
            f"{op.id}: temporal {op.temporal!r} is not a vocabulary")
        assert op.merge in patch_algebra.MERGE_SEMANTICS, (
            f"{op.id}: merge {op.merge!r} is not a vocabulary")


def test_the_structural_ops_verify_detects_the_absent_write():
    """Defect: an op whose verifier passes on a timeline the op did not
    change would commit a lie. Each op is judged on the base snapshot,
    where its effect is absent."""
    from library.tools import edit_algebra
    base = _base_snapshot()
    cases = [
        ("clip.delete", {"unique_id": "a"}),
        ("clip.insert", {"unique_id": "z", "track_type": "video",
                         "track_index": 1, "record_in": 0,
                         "record_out": 10}),
        ("clip.split", {"unique_id": "a", "at_frame": 25}),
        ("clip.join", {"unique_id": "a", "other_unique_id": "b"}),
        ("clip.trim", {"unique_id": "a", "in_frame": 5, "out_frame": 40}),
        ("clip.roll", {"unique_id": "a", "delta_frames": 5,
                       "source_in": 5, "source_out": 55}),
        ("clip.slip", {"unique_id": "a", "delta_frames": 5,
                       "source_in": 5, "source_out": 55}),
        ("clip.slide", {"unique_id": "a", "to_frame": 60}),
        ("clip.move", {"unique_id": "a", "to_frame": 60}),
        ("clip.replace", {"unique_id": "a", "source_file": "c.mov"}),
        ("clip.set_enabled", {"unique_id": "a", "enabled": False}),
        ("clip.set_property", {"unique_id": "a", "key": "ZoomX",
                               "value": 1.5}),
        ("compound.create", {"unique_id": "c", "child_unique_ids": ["a", "b"]}),
        ("compound.flatten", {"unique_id": "d"}),
        ("mask.create", {"unique_id": "a", "matte": "matte-1"}),
        ("mask.edit", {"unique_id": "a", "matte": "matte-1",
                       "geometry": {"x": 1}}),
    ]
    for op_id, params in cases:
        op = edit_algebra.get(op_id)
        assert op is not None, op_id
        failure = op.verify(params, base)
        assert failure, (
            f"{op_id}: verifier passed on a timeline the op did not change")


def test_the_structural_ops_span_covers_what_they_touch():
    """Defect: an op whose span is malformed (empty, or missing its
    target) would be refused by patch validation, or worse, slip past it."""
    from library.tools import edit_algebra
    base = _base_snapshot()
    cases = [
        ("clip.delete", {"unique_id": "a"}),
        ("clip.insert", {"unique_id": "z", "track_type": "video",
                         "track_index": 1, "record_in": 0,
                         "record_out": 10}),
        ("clip.split", {"unique_id": "a", "at_frame": 25}),
        ("clip.join", {"unique_id": "a", "other_unique_id": "b"}),
        ("clip.trim", {"unique_id": "a", "in_frame": 5, "out_frame": 40}),
        ("clip.roll", {"unique_id": "a", "delta_frames": 5,
                       "source_in": 5, "source_out": 55}),
        ("clip.slip", {"unique_id": "a", "delta_frames": 5,
                       "source_in": 5, "source_out": 55}),
        ("clip.slide", {"unique_id": "a", "to_frame": 60}),
        ("clip.move", {"unique_id": "a", "to_frame": 60}),
        ("clip.replace", {"unique_id": "a", "source_file": "c.mov"}),
        ("clip.set_enabled", {"unique_id": "a", "enabled": False}),
        ("clip.set_property", {"unique_id": "a", "key": "ZoomX",
                               "value": 1.5}),
        ("compound.create", {"unique_id": "c", "child_unique_ids": ["a", "b"]}),
        ("compound.flatten", {"unique_id": "d"}),
        ("mask.create", {"unique_id": "a", "matte": "matte-1"}),
        ("mask.edit", {"unique_id": "a", "matte": "matte-1",
                       "geometry": {"x": 1}}),
        ("track.rename", {"track_type": "video", "track_index": 1,
                          "name": "Renamed"}),
        ("track.delete", {"track_type": "video", "track_index": 1}),
    ]
    for op_id, params in cases:
        op = edit_algebra.get(op_id)
        assert op is not None, op_id
        span = op.span(params, base)
        assert span is not None, f"{op_id}: no span on the base snapshot"
        assert len(span) == 2 and span[0] < span[1], (
            f"{op_id}: malformed span {span}")


def test_the_five_vocabularies_are_views_over_the_algebra():
    """Defect: a vocabulary op that resolves to no algebra id is a
    vocabulary that drifted from the union - the algebra would not be
    the single source of truth."""
    from library.tools import edit_algebra
    from library.tools import captain_edits, edit_ledger, edit_operations
    views = {
        "ledger": {name: name for name in edit_ledger.OPS},
        "plan": {name: name for name in edit_operations.PLAN_OPERATION_OWNERS},
        "touchup": edit_algebra.TOUCHUP_VIEW,
        "captain": edit_algebra.CAPTAIN_VIEW,
    }
    for vocabulary, mapping in views.items():
        for name, op_id in mapping.items():
            assert op_id in edit_algebra.ALGEBRA, (
                f"{vocabulary} op {name!r} resolves to {op_id!r}, which is "
                f"not in the algebra")
    for kind in captain_edits.KINDS:
        assert kind in edit_algebra.ALGEBRA, (
            f"captain kind {kind!r} is not in the algebra")
