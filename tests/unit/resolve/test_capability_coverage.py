"""The capability coverage report and the read-only support question.

Each test here names a defect the report exists to remove.  A report
that drifts from the vocabulary, that reads a qualification as a
permission, that counts a declaration as a request, or that offers a
fallback the router does not have, is worse than no report: an agent
reads it and believes it.
"""

from __future__ import annotations

import json
from argparse import Namespace

from library.tools import (
    axi_qualification,
    capability_coverage,
    edit_algebra,
    execution_router,
)
from library.tools import axi_qualification_dimensions as dimensions

#: A measured state that satisfies the recorded predicates of the
#: operations qualified on `resolve_axi` (the same reading the
#: qualification tests use).
MEASURED = {
    "resolve_version": "21.1.0.14",
    "media_type": "video",
    "clip_composition": "single_video_clip",
    "linked_audio_configuration": "no_linked_audio",
    "track_layout": "video",
    "track_occupancy": "mixed",
    "timeline_density": "light",
    "timeline_structure": "multi_item_timeline",
    "project_settings": "landscape_1920x1080",
    "frame_rate": "24",
    "existing_effects": "none",
}


def test_the_report_covers_the_vocabulary_and_nothing_else():
    """Defect: a second roster of operations.

    The report is generated from `edit_algebra.OP_STATUS`, so a
    hand-written list beside it could only ever drift - and a drift
    here reads as "Ren does not support this", which is a fact about
    the product, not about the list.
    """
    report = capability_coverage.coverage_report()
    assert set(report["operations"]) == set(edit_algebra.OP_STATUS)
    assert report["operation_count"] == len(edit_algebra.OP_STATUS)
    for op_id, row in report["operations"].items():
        assert row["id"] == op_id
        assert row["operation_qualification"]["status"] == \
            edit_algebra.status(op_id).qualification_status


def test_the_dimension_coverage_is_read_from_the_qualification_registry():
    """Defect: a report that says which states were never measured,
    from a list written beside the evidence.

    The coverage is the RECORD's own: for a qualified operation the
    report must name the same dimension values the registry recorded,
    and the untested ones must be the vocabulary's, not the record's
    own shorter list.
    """
    report = capability_coverage.coverage_report()
    record = axi_qualification.load_qualification_records()[
        ("clip.set_property", "edit_patch")]
    measured = record["coverage"]["dimensions"]
    row = next(backend for backend
               in report["operations"]["clip.set_property"]
               ["operation_qualification"]["backends"]
               if backend["backend"] == "edit_patch")
    coverage = row["qualification_dimensions"]
    assert coverage["source"] == record["coverage"]["source"]
    for dimension, values in measured.items():
        if dimension == "resolve_version":
            continue
        vocabulary = dimensions.vocabulary(dimension)
        if dimension in coverage["covered"]:
            assert [value for value in vocabulary if value not in values] == []
            continue
        assert coverage["partial"][dimension]["tested"] == list(values)
        assert coverage["partial"][dimension]["untested"] == [
            value for value in vocabulary if value not in values]
    # An operation with no evidence at all has every dimension missing,
    # and says so rather than reading as covered.
    assert report["operations"]["clip.trim"]["qualification_dimensions"][
        "missing"] == list(dimensions.DIMENSIONS)


def test_a_qualified_operation_is_refused_outside_its_recorded_predicates():
    """Defect: a qualification that did not check the live state would
    enable the operation on every timeline, not only the one the
    evidence was earned on.

    The operation-level qualification and the runtime answer are two
    facts, and the report keeps them apart: `caption.style` is
    production-enabled, and it is still refused on a timeline whose
    state does not satisfy the recorded predicates.
    """
    inside = capability_coverage.support("caption.style",
                                         conditions=dict(MEASURED))
    assert inside["operation_qualification"]["production_enabled"] is True
    assert inside["answer"] == "supported"
    assert inside["runtime_eligibility"] == "eligible"

    outside = capability_coverage.support(
        "caption.style", conditions=dict(MEASURED, media_type="audio"))
    assert outside["operation_qualification"]["production_enabled"] is True
    assert outside["answer"] == "not_supported"
    assert outside["runtime_eligibility"] == "not_eligible"
    assert any("media_type" in line for line in outside["why"])


def test_an_uninspected_state_is_never_reported_as_supported():
    """Defect: an unknown state read as a pass.

    The engine has no telemetry of the open timeline unless it reads
    it, and a state nobody read cannot satisfy a recorded predicate.
    The answer says "unknown" and names the missing inspection.
    """
    answer = capability_coverage.support("clip.delete")
    assert answer["answer"] == "unknown"
    assert answer["runtime_eligibility"] == "unknown"
    assert any("not inspected" in line or "state" in line
               for line in answer["why"])
    assert answer["runtime_routing"]["production"]["admitted"] is False


def test_a_rebuild_is_not_a_way_around_a_qualification_gap():
    """Defect: a fallback that does not exist.

    `execution_router` preflights the full-rebuild route with the same
    qualification policy as every other route, so an unqualified
    operation is refused by the rebuild too.  A report that implied a
    rebuild could carry the edit would send a planner down a path that
    ends in the same refusal.
    """
    answer = capability_coverage.support("clip.trim",
                                         conditions=dict(MEASURED))
    assert answer["alternatives"]["rebuild_route_admitted"] is False
    assert answer["alternatives"]["rebuild_route_refusal"]
    row = capability_coverage.coverage_report()["operations"]["clip.trim"]
    assert row["alternatives"]["rebuild_route"]["admitted"] is False
    # And the router itself says so - the report does not decide it.
    try:
        execution_router.route_compiled_edit(
            [{"op": "clip.trim", "unique_id": "probe-item",
              "in_frame": 0, "out_frame": 10}],
            context={"qualification_policy": "production",
                     "requested_backend": "full_rebuild"})
    except execution_router.RoutingRefused as refused:
        assert "clip.trim" in refused.why
    else:  # pragma: no cover - the router admitted an unqualified rebuild
        raise AssertionError("the rebuild route admitted an unqualified op")


def test_the_frequency_is_measured_from_the_pipeline_not_from_the_vocabulary():
    """Defect: counting a declaration as a request.

    Every operation is named once in the algebra that declares it, so
    a frequency that counted those sites would rank the vocabulary
    rather than the editorial loop.  The vocabulary, the operation map
    and this report are excluded from the scan.
    """
    sites = capability_coverage.request_sites()
    assert set(sites) == set(edit_algebra.OP_STATUS)
    for entries in sites.values():
        for entry in entries:
            path = entry.rsplit(":", 1)[0]
            assert path not in capability_coverage._FREQUENCY_EXCLUDED
    # The editorial loop really does ask for operations, and the
    # measurement finds them.
    assert any(sites[op_id] for op_id in ("clip.insert", "clip.trim"))


def test_the_priority_order_is_the_measured_score_it_publishes():
    """Defect: a priority list whose order nobody can recompute.

    The order is the measured request count times the impact tier the
    algebra's safety class gives the operation, and every row publishes
    the inputs it was ranked by.
    """
    report = capability_coverage.coverage_report()
    scores = []
    for op_id in report["priority"]:
        row = report["operations"][op_id]["priority"]
        assert row["editorial_request_count"] == len(
            row["editorial_request_sites"])
        assert row["impact_tier"] in capability_coverage.IMPACT_WEIGHTS
        assert row["priority_score"] == (
            row["editorial_request_count"] * row["impact_weight"])
        scores.append(row["priority_score"])
    assert scores == sorted(scores, reverse=True)
    # The gap priority is the same order over the operations that are
    # not production-enabled.
    for op_id in report["qualification_gap_priority"]:
        assert not report["operations"][op_id][
            "operation_qualification"]["production_enabled"]


def test_the_support_verb_answers_supported_or_not_and_why(capsys):
    """The read-only question BASE-002 asks for, served by the CLI."""
    from library.tools.resolve_axi_core_commands import cmd_support

    assert cmd_support(Namespace(
        operation="caption.style", conditions=json.dumps(MEASURED),
        conditions_file="", params="")) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["answer"] == "supported"
    assert payload["runtime_eligibility"] == "eligible"

    assert cmd_support(Namespace(
        operation="caption.style",
        conditions=json.dumps(dict(MEASURED, media_type="audio")),
        conditions_file="", params="")) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["answer"] == "not_supported"
    assert payload["why"]


def test_the_coverage_verb_serves_the_summary_and_one_row(capsys):
    from library.tools.resolve_axi_core_commands import cmd_capability_coverage

    assert cmd_capability_coverage(Namespace(
        operation="", full=False)) == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary["operation_count"] == len(edit_algebra.OP_STATUS)
    assert summary["production_enabled_operations"]

    assert cmd_capability_coverage(Namespace(
        operation="clip.trim", full=False)) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["operation"]["id"] == "clip.trim"
    assert payload["operation"]["runtime_routing"]["production"]["admitted"] \
        is False
