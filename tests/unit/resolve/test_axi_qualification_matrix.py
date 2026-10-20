"""The matrix a qualification run walks, and the report it produces.

A coverage is what a run MEASURED. A run that changed something it was
not asked to change does not become a coverage, and a report that names
a dimension nobody measured is refused by the importer.

These tests name both.
"""
from __future__ import annotations

import pytest

from library.tools import axi_qualification_dimensions as dimensions
from library.tools import axi_qualification_matrix as matrix


def _state(**overrides) -> dict:
    state = {
        "resolve_version": "DaVinci Resolve 21.1.0.14",
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
    state.update(overrides)
    return state


def _result(configuration: str = "populated_1080p24", **state) -> dict:
    return {"configuration": configuration,
            "qualification_state": {"inspected": True, "state": _state(**state)},
            "verdict": {"requested_changes": [], "unintended_modifications": []}}


def test_the_plan_pairs_every_candidate_with_every_configuration_it_names():
    for candidate, configuration in matrix.plan():
        assert candidate.runs_against(configuration.id)
        assert configuration.id in candidate.configurations


def test_the_coverage_is_the_union_of_what_the_runs_measured():
    results = [_result("populated_1080p24"),
               _result("linked_av_1080p24",
                       track_layout="video+audio",
                       linked_audio_configuration="linked_audio_present")]

    coverage = matrix.measured_coverage(results)

    assert coverage["configurations"] == ["populated_1080p24",
                                          "linked_av_1080p24"]
    assert coverage["dimensions"]["track_layout"] == ["video", "video+audio"]
    assert coverage["dimensions"]["linked_audio_configuration"] == [
        "no_linked_audio", "linked_audio_present"]


def test_a_dimension_nobody_measured_is_not_in_the_coverage():
    """A coverage that fills the gap with a guess is not a coverage."""
    results = [_result()]
    results[0]["qualification_state"]["state"].pop("existing_effects")

    coverage = matrix.measured_coverage(results)

    assert "existing_effects" not in coverage["dimensions"]


def test_a_run_that_changed_what_it_was_not_asked_to_is_not_preserved():
    results = [_result()]
    results[0]["verdict"]["unintended_modifications"] = [
        {"op": "", "unique_id": "other", "change": "removed", "fields": []}]

    ok, unintended = matrix.preserved(results)

    assert not ok
    assert unintended[0]["configuration"] == "populated_1080p24"


def _report(results, **overrides):
    payload = {
        "operation_id": "clip.delete",
        "backend": "edit_patch",
        "results": results,
        "evidence": "tests/live/reports/matrix.json",
        "environment": {"resolve_version": "DaVinci Resolve 21.1.0.14",
                        "platform": "darwin"},
        "semantic_boundary": {"applies_to": ["one delete"],
                              "does_not_apply_to": ["a graded clip"]},
        "project_conditions": ["disposable qualification project"],
        "supported_media_types": ["video"],
        "expected_state": {"target_absent": True},
        "actual_state": {"target_absent": True},
        "preservation_checks": [{"check": "target_absent_on_readback",
                                 "result": "passed"}],
        "preservation_limits": ["one delete per run"],
        "visual_verification": ["the target is absent on a fresh read"],
        "audio_verification": ["not applicable: no audio item"],
        "recovery_status": "not_required",
    }
    payload.update(overrides)
    return matrix.build_report(**payload)


def test_a_clean_matrix_produces_an_importable_report():
    report = _report([_result()])

    from library.tools import axi_qualification

    pair, record = axi_qualification.parse_result(report)

    assert pair == ("clip.delete", "edit_patch")
    assert record["coverage"]["source"] == "measured"
    fields = {predicate["field"] for predicate in
              record["qualification_predicates"]}
    assert fields == set(dimensions.DIMENSIONS)


def test_a_run_with_an_unintended_modification_produces_no_report():
    results = [_result()]
    results[0]["verdict"]["unintended_modifications"] = [
        {"op": "", "unique_id": "other", "change": "removed", "fields": []}]

    with pytest.raises(ValueError, match="not asked to change"):
        _report(results)


def test_a_matrix_that_measured_nothing_cannot_be_reported():
    results = [_result()]
    results[0]["qualification_state"]["state"].pop("frame_rate")

    with pytest.raises(ValueError, match="frame_rate"):
        _report(results)
