"""The AXI-008 overlay qualification harness, checked offline.

The scenarios are LIVE: they run against a real Resolve on a disposable
copy of the private scratch timeline.  What is checked here is the
contract the harness itself carries, so a defect in the harness is named
before it costs a live run:

* every operation the punch list names for the overlay family has a
  scenario, on every carrier the lane claims;
* every scenario names an operation the algebra declares, and a
  carrier kind the engine's own media enumeration knows;
* a scenario that expects a refusal is judged as a refusal, and a
  refusal never produces a qualification row;
* a row is only ever written for a pair that HAS an executor, and a
  pair that cannot be promoted is reported rather than silently dropped;
* the judges read the RENDERED FRAMES, not only the API read-back, and
  the rendered box is compared with the REQUESTED box in
  delivery-frame pixels;
* the denominators are never combined, and a scenario that was not
  measured is UNMEASURED, never a zero.
"""

from __future__ import annotations

from library.tools import edit_algebra, overlay_media
from tests.live import axi_overlay_qualification as lane


def _scenario(scenario_id: str) -> lane.Scenario:
    return next(item for item in lane.SCENARIOS if item.id == scenario_id)


def test_every_overlay_operation_has_a_scenario_on_every_carrier():
    for operation in lane.OVERLAY_OPERATIONS:
        for carrier in lane.CARRIERS:
            assert any(scenario.operation == operation
                       and scenario.carrier == carrier.id
                       for scenario in lane.SCENARIOS), (
                f"{operation} has no scenario on the {carrier.id} carrier")


def test_every_scenario_names_an_operation_the_algebra_declares():
    for scenario in lane.SCENARIOS:
        assert edit_algebra.status(scenario.operation) is not None, (
            f"{scenario.id} names {scenario.operation}, which the algebra "
            "does not declare")


def test_every_carrier_names_a_media_kind_the_engine_enumerates():
    for carrier in lane.CARRIERS:
        assert carrier.media_kind in overlay_media.MEDIA_KINDS, (
            f"{carrier.id} names the media kind {carrier.media_kind!r}, "
            "which the media enumeration does not carry")


def test_a_carrier_that_carries_a_comp_expects_the_refusal_the_routes_give():
    """A Fusion composition on the target is refused BY NAME.

    The comp draws the overlay, so the native transform is not what
    places it; a route that pretended to place it would be wrong.  The
    lane therefore expects a refusal there, and a commit on such a
    carrier is a FAILED row, not a qualification.
    """
    for scenario in lane.SCENARIOS:
        carrier = next(item for item in lane.CARRIERS
                       if item.id == scenario.carrier)
        if not carrier.carries_comp:
            continue
        refuses = scenario.operation in lane.COMP_REFUSES
        if scenario.operation in lane.ROUTE_DECIDED:
            assert scenario.expectation == "route", (
                f"{scenario.id} is a request the ROUTE decides, so the "
                "lane records its verdict and qualifies nothing")
            continue
        assert scenario.expectation == ("refuse" if refuses else "commit"), (
            f"{scenario.id} targets a comp-bearing carrier, so it must "
            f"expect the {'refusal' if refuses else 'commit'} the "
            "touch-up path gives")
        if refuses:
            assert lane.COMP_REFUSES[scenario.operation]


def test_a_refusal_never_produces_a_qualification_row():
    evidence = [{"case": "position_fusion_composition",
                 "refusal": "the comp draws the overlay itself, so the "
                            "native transform is not what places it"}]
    rows, _gaps = lane._registry_rows({"scenarios": []}, evidence)
    assert not rows


def test_a_pair_without_an_executor_is_reported_instead_of_promoted():
    """A row for a pair with no executor would route work to a dead path."""
    rows, gaps = lane._registry_rows({"scenarios": []}, [])
    promoted = {row["operation_id"] for row in rows}
    reported = {gap["operation_id"] for gap in gaps}
    assert not (promoted & reported)
    for gap in gaps:
        assert gap["reason"]
    # Every overlay operation the lane runs is either promotable or
    # reported - never silently dropped.
    assert reported | promoted == set(lane.OVERLAY_OPERATIONS)


def test_a_refusal_is_judged_as_a_refusal_and_not_as_a_pass():
    checks = lane.judge_refusal({
        "refusal": "the comp draws the overlay itself",
        "before": {"items": [{"unique_id": "1", "record_in": 0,
                              "record_out": 10, "enabled": True,
                              "fusion_comps": [], "transform": {}}]},
        "after": {"items": [{"unique_id": "1", "record_in": 0,
                             "record_out": 10, "enabled": True,
                             "fusion_comps": [], "transform": {}}]},
    })
    assert checks["refused"] is True
    assert checks["timeline_unchanged"] is True


def test_a_refusal_that_moved_the_timeline_is_not_a_pass():
    checks = lane.judge_refusal({
        "refusal": "refused by name",
        "before": {"items": [{"unique_id": "1", "record_in": 0,
                              "record_out": 10, "enabled": True,
                              "fusion_comps": [], "transform": {}}]},
        "after": {"items": [{"unique_id": "1", "record_in": 0,
                             "record_out": 10, "enabled": False,
                             "fusion_comps": [], "transform": {}}]},
    })
    assert checks["timeline_unchanged"] is False


def test_the_rendered_box_is_compared_with_the_requested_box():
    """The geometry verdict is the RENDERED box, not the read-back.

    A stored Pan/Tilt says where Resolve was told to draw; only the
    rendered frames say where it drew.  A lane that judged the stored
    transform would pass a clamp.
    """
    checks = lane.judge_position({
        "projected": {"absolute_box": [100, 200, 420, 290]},
        "measured": {"box": [102, 201, 419, 289]},
        "before": {"items": []},
        "after": {"items": []},
    })
    assert checks["rendered_pixels_match_request"] is True
    assert checks["bounding_box_matches_request"] is True

    off = lane.judge_position({
        "projected": {"absolute_box": [100, 200, 420, 290]},
        "measured": {"box": [400, 500, 720, 590]},
        "before": {"items": []},
        "after": {"items": []},
    })
    assert off["rendered_pixels_match_request"] is False


def test_a_scenario_that_measured_nothing_passes_nothing():
    """An empty evidence set is not a clean run.

    The removal checks read as true on an empty state (the item is
    absent from a state that was never read), so the judge refuses to
    judge without a measured state at all.
    """
    scenario = next(item for item in lane.SCENARIOS
                    if item.operation == "overlay.remove")
    checks = lane.judge_scenario(scenario, {})
    assert checks and all(value is False for value in checks.values())


def test_the_denominators_are_never_combined_and_unmeasured_is_not_zero():
    report = lane.build_report([])
    counts = report["counts"]
    assert counts["scenarios"] == len(lane.SCENARIOS)
    assert counts["unmeasured"] == len(lane.SCENARIOS)
    assert counts["measured"] == 0
    assert counts["failed"] == 0
    assert counts["refused"] == 0
    for row in report["scenarios"]:
        assert row["status"] == "unmeasured"


def test_a_scenario_that_failed_is_a_failed_row_and_not_a_zero():
    report = lane.build_report([{"case": lane.SCENARIOS[0].id,
                                  "error": "RuntimeError: boom"}])
    row = next(item for item in report["scenarios"]
               if item["id"] == lane.SCENARIOS[0].id)
    assert row["status"] == "failed"
    assert report["counts"]["failed"] == 1
    assert report["counts"]["unmeasured"] == len(lane.SCENARIOS) - 1


def test_the_rendered_carrier_is_needed_and_a_missing_one_is_not_a_zero():
    """A production rendered overlay is named, not guessed.

    Without the environment variable the lane renders its OWN movie
    overlay - the media KIND is what is under test, not the artwork -
    and the report says that no production asset was named.  The lane
    must not fall back to the PNG and call it a movie.
    """
    import os

    previous = os.environ.pop(lane.RENDERED_OVERLAY_ENV, None)
    try:
        assert lane.rendered_overlay_path() is None
        assets = lane.prepare_assets()
        assert assets["rendered_source"] == ""
        movie = assets["owned"].get("rendered_video")
        assert movie and movie.endswith(".mov")
        from library.tools import overlay_media
        media = overlay_media.measure(overlay_media.RENDERED_VIDEO, movie)
        assert media.kind == overlay_media.RENDERED_VIDEO
        assert media.has_alpha_plane is True
        assert media.transparent is True
    finally:
        if previous is not None:
            os.environ[lane.RENDERED_OVERLAY_ENV] = previous


def test_the_lane_writes_a_report_it_can_read_back():
    assert lane.REPORT_PATH.name == "axi-overlay-qualification.json"
    assert lane.PROJECT == "Ren Qualification"
    # The lane writes on a DISPOSABLE copy of the private fixture, never
    # on the shared one.
    assert lane.TIMELINE in lane._disposable_name("123")
    assert lane._disposable_name("123") != lane.TIMELINE


def test_the_broker_script_defines_every_name_it_uses():
    """A name the script never defines is a live run spent for nothing.

    The script is assembled as text, so a name that does not exist in
    it is not a syntax error: it compiles, it is submitted, and it
    raises at the first line that uses it.  This names that class of
    defect before it costs a live run.
    """
    import ast
    import builtins

    script = (lane.BROKER_SCRIPT
              .replace("__AXI_PROJECT__", "P")
              .replace("__AXI_TIMELINE__", "T")
              .replace("__AXI_PROJECT_DIR__", "/tmp")
              .replace("__AXI_OWNED__", "{}")
              .replace("__AXI_CASES__", "{}")
              .replace("__AXI_CASE_ASSETS__", "{}")
              .replace("__AXI_SOURCE_OUT__", "{}")
              .replace("__AXI_SAMPLE__", "96")
              .replace("__AXI_PLACEMENT__", "24")
              .replace("__AXI_ROW__", "3"))
    tree = ast.parse(lane._case_script() + script)
    # `manager` and `resolve` are the AXI run scope's own names: the
    # script runs inside Resolve with them in scope.
    defined = (set(dir(builtins)) | {"__name__", "__file__",
                                     "manager", "resolve"})
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef,
                             ast.ClassDef)):
            defined.add(node.name)
        elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
            defined.add(node.id)
        elif isinstance(node, ast.arg):
            defined.add(node.arg)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                defined.add((alias.asname or alias.name).split(".")[0])
        elif isinstance(node, ast.ExceptHandler) and node.name:
            defined.add(node.name)
        elif isinstance(node, (ast.comprehension,)):
            for inner in ast.walk(node.target):
                if isinstance(inner, ast.Name):
                    defined.add(inner.id)
    used = {node.id for node in ast.walk(tree)
            if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load)}
    missing = sorted(used - defined)
    assert not missing, (
        "the broker script uses names it never defines: "
        + ", ".join(missing))
