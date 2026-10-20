"""The AXI-010..023 primitive qualification scenarios, checked offline.

The scenarios are LIVE: they run against a real Resolve on the private
scratch timeline, and the live lane (``vep-axc-live2``) runs them.
What is checked here is the contract the harness itself carries, so a
defect in the harness is named before it costs a live run:

* every AXI item the lane owns has at least one scenario;
* every scenario names an operation the algebra declares;
* a qualification row is only ever written for a pair that HAS an
  executor, and a pair that cannot be promoted is reported rather than
  silently dropped;
* a refusal never produces a qualification row;
* an existing registry row is never overwritten;
* the predicates are written in the vocabulary the live measurement
  produces, so a recorded predicate can always be satisfied;
* the judges read the timeline, not the command's exit code.
"""

from __future__ import annotations

from library.tools import axi_qualification, edit_algebra
from tests.live import axi_primitive_qualification as qual


def _primitive(primitive_id: str) -> qual.Primitive:
    return next(item for item in qual.PRIMITIVES
                if item.id == primitive_id)


def _passed_row(primitive_id: str, checks: dict) -> dict:
    primitive = _primitive(primitive_id)
    row = qual.asdict(primitive)
    row.update({"status": "pass", "first_attempt": True,
                "collateral_changes": [], "checks": checks,
                "case": {"qualification_state": {
                    "inspected": True,
                    "state": {"resolve_version": "DaVinci Resolve 21.1.0.14",
                              "project_name": qual.PROJECT,
                              "timeline_name": qual.TIMELINE,
                              "media_type": "video",
                              "clip_composition": "single_video_clip",
                              "linked_audio_configuration": "no_linked_audio",
                              "timeline_structure": "empty_fixture"}}}})
    return row


def test_every_axi_item_this_lane_owns_has_a_scenario():
    covered = {primitive.axi_item for primitive in qual.PRIMITIVES}
    assert covered == set(qual.AXI_ITEMS)


def test_every_scenario_names_an_operation_the_algebra_declares():
    for primitive in qual.PRIMITIVES:
        assert edit_algebra.status(primitive.operation) is not None, (
            f"{primitive.id} names {primitive.operation}, which the algebra "
            "does not declare")


def test_a_scenario_names_a_backend_the_operation_declares_or_a_named_gap():
    for primitive in qual.PRIMITIVES:
        entry = edit_algebra.status(primitive.operation)
        backends = {backend.backend for backend in entry.backends}
        assert (primitive.backend in backends
                or primitive.id in qual.PROMOTION_GAPS), (
            f"{primitive.id} names the backend {primitive.backend!r}, which "
            f"{primitive.operation} does not have: {sorted(backends)}")


def test_a_pair_without_an_executor_is_reported_instead_of_promoted():
    """A row for a pair with no executor would route work to a dead path.

    `clip.move` and `transition` have no executor on the AXI verb path
    (the move re-places the clip, so it cannot be one patch operation;
    the transition is a touch-up op).  The harness must say so rather
    than write a row the registry would refuse.
    """
    rows, gaps = qual._registry_rows({
        "results": [_passed_row("move_keeps_unrelated_state",
                                {"moved_to_requested_frame": True}),
                    _passed_row("transition_at_cut",
                                {"transition_placed": True})]})
    assert not rows
    assert {gap["operation_id"] for gap in gaps} == {
        "clip.move", "transition"}
    for gap in gaps:
        assert gap["reason"]


def test_a_registered_pair_is_written_with_its_merged_checks():
    rows, gaps = qual._registry_rows({
        "results": [_passed_row("set_property_position",
                                {"property_read_back": True,
                                 "record_span_unchanged": True}),
                    _passed_row("set_property_scale",
                                {"property_read_back": True,
                                 "record_span_unchanged": True})]})
    assert not gaps
    assert len(rows) == 1
    key, result = rows[0]
    assert key == ("clip.set_property", "edit_patch")
    assert result["result"] == "passed"
    assert result["schema"] == axi_qualification.REPORT_SCHEMA
    # The loader accepts this document: the evidence is complete.
    pair, record = axi_qualification.parse_result(result)
    assert pair == key
    assert record["result"] == "passed"


def test_a_refusal_never_produces_a_qualification_row():
    rows, gaps = qual._registry_rows({
        "results": [_passed_row("trim_past_handles_refused",
                                {"refused": True,
                                 "timeline_unchanged": True})]})
    assert not rows
    assert not gaps


def test_the_registry_writer_leaves_an_existing_row_in_place(tmp_path,
                                                            monkeypatch):
    monkeypatch.setattr(qual, "REGISTRY_DIR", tmp_path / "registry")
    existing = tmp_path / "registry" / "clip.trim__resolve_axi.json"
    existing.parent.mkdir(parents=True)
    existing.write_text("{}", encoding="utf-8")
    report = {"results": [_passed_row("trim_narrows_source_range",
                                      {"source_range_narrowed": True})]}
    written, _gaps = qual._write_registry_results(report)
    assert len(written) == 1
    assert written[0]["existing_row_left_in_place"] is True
    assert existing.read_text(encoding="utf-8") == "{}"
    assert (tmp_path / "registry"
            / "clip.trim__resolve_axi__axi-primitive.json").is_file()


def test_the_predicates_speak_the_live_measurement_vocabulary():
    assert set(qual._PREDICATE_FIELDS) == set(
        axi_qualification.PREDICATE_FIELDS)


def test_the_predicates_are_written_from_what_was_measured():
    measured = {"inspected": True, "state": {
        "resolve_version": "DaVinci Resolve 21.1.0.14",
        "project_name": qual.PROJECT, "timeline_name": qual.TIMELINE,
        "media_type": "video", "clip_composition": "single_video_clip",
        "linked_audio_configuration": "no_linked_audio",
        "timeline_structure": "empty_fixture"}}
    predicates = qual._predicates(measured)
    assert {row["field"] for row in predicates} == set(qual._PREDICATE_FIELDS)
    assert all(row["operator"] == "equals" for row in predicates)
    unmeasured = qual._predicates({"inspected": False})
    assert unmeasured == []


def test_both_resolve_source_endpoint_forms_read_as_the_same_range():
    """Resolve reports an inclusive or an exclusive source end.

    A fresh placement echoes the exclusive form and an existing clip
    reports the inclusive one; the record duration decides which form
    the reading speaks, so a check must not compare against a guessed
    convention.
    """
    inclusive = {"source_in": 0, "source_out": 47,
                 "record_in": 0, "record_out": 48}
    exclusive = {"source_in": 0, "source_out": 48,
                 "record_in": 0, "record_out": 48}
    assert qual._source_bounds(inclusive) == (0, 47)
    assert qual._source_bounds(exclusive) == (0, 47)


def test_a_judge_reads_the_timeline_not_the_exit_code():
    """A command that answered 0 without moving the timeline fails.

    The trim judge is the sharpest case: the command can succeed while
    the source range did not narrow, and a judge that read the exit
    code would pass it.
    """
    case = {
        "after_setup": {"items": [{"name": "qual_a.mp4", "unique_id": "a",
                                   "source_in": 0, "source_out": 95,
                                   "record_in": 0, "record_out": 96}]},
        "after": {"items": [{"name": "qual_a.mp4", "unique_id": "a",
                             "source_in": 0, "source_out": 95,
                             "record_in": 0, "record_out": 96}]},
    }
    checks = qual._judge_trim(case)
    assert checks["source_range_narrowed"] is False
    assert checks["duration_preserved"] is True
    assert checks["record_start_shifted"] is False


def test_the_broker_script_compiles_and_names_every_scenario():
    cases = qual._cases()
    assert set(cases) == {primitive.id for primitive in qual.PRIMITIVES}
    script = qual._broker_script(cases)
    compile(script, "<axi-primitive-batch>", "exec")
    for primitive in qual.PRIMITIVES:
        assert f"'{primitive.id}'" in script


def test_the_scenario_table_and_the_judges_agree():
    assert set(qual.JUDGES) == {primitive.id for primitive in qual.PRIMITIVES}


def test_every_scenario_names_the_checks_it_must_observe():
    for primitive in qual.PRIMITIVES:
        assert primitive.checks, f"{primitive.id} names no check"
        assert primitive.expectation in {"commit", "refuse"}
        assert primitive.production_entry


def test_a_refusal_scenario_is_never_expected_to_commit():
    for primitive in qual.PRIMITIVES:
        if primitive.expectation == "refuse":
            assert "refused" in primitive.checks, (
                f"{primitive.id} expects a refusal but never checks for one")
