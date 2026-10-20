"""The canonical operation map is DERIVED, and its audit cannot miss.

The map exists because a model that picks its own execution path is how
a semantically DIFFERENT edit reaches the timeline.  These tests name the
defects that made that possible, and they fail if the map stops being
derived from the code it describes.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from library.tools import edit_algebra, operation_map

REPO = Path(__file__).resolve().parents[3]

#: The sections the map owes a reader of one operation.  A section that
#: goes missing is a question the map stops answering, so the map is
#: wrong even when every remaining section is right.
REQUIRED_SECTIONS = (
    "semantics",
    "dependencies",
    "backend_capabilities",
    "preservation_constraints",
    "qualification_evidence",
    "supported_project_states",
)


def _map() -> dict:
    return operation_map.operation_map()


def test_the_map_covers_every_registered_operation_and_no_other():
    """Defect: a map that lists fewer operations than the algebra holds
    hides exactly the operations nobody checked, and a map that lists
    more invents capabilities that do not exist."""
    payload = _map()
    assert payload["schema"] == operation_map.SCHEMA
    assert set(payload["operations"]) == set(edit_algebra.ALGEBRA)
    assert payload["operation_count"] == len(edit_algebra.ALGEBRA)


def test_every_operation_carries_the_six_sections_a_reader_needs():
    """Defect: a map row with only an availability flag tells a model
    nothing about what the edit MEANS, what it DEPENDS on, what it
    PRESERVES or where it is supported - which is the whole reason the
    map is not prose."""
    for op_id, entry in _map()["operations"].items():
        for section in REQUIRED_SECTIONS:
            assert section in entry, f"{op_id} has no {section} section"
        assert entry["semantics"]["purpose"]
        assert entry["support_level"] in operation_map.SUPPORT_LEVELS


def test_the_map_declares_no_operation_of_its_own():
    """Defect: a second operation roster beside the algebra's is how two
    vocabularies disagree about what can be done."""
    declared = set(edit_algebra.AXI_OPERATION_MAP)
    controls = set(_map()["axi_controls"])
    assert controls == declared
    for name, row in _map()["axi_controls"].items():
        assert row["algebra_operations"] == list(
            edit_algebra.AXI_OPERATION_MAP[name])


def test_the_audit_cannot_miss_a_declared_backend_without_an_adapter():
    """Defect: the registry declared `marker.add` and `clip.smart_reframe`
    on the `resolve_axi` backend, and no dispatch table provided either -
    so a request for them named a backend that could not execute them.
    The audit has to find that by itself, from the code, not from a
    hand-kept list of known mistakes."""
    declared = operation_map.declared_coverage()
    implemented = operation_map.implemented_coverage()
    expected = {
        (op_id, backend)
        for op_id, backends in declared.items()
        for backend in backends
        if backend not in implemented.get(op_id, {})
    }
    reported = {(row["operation"], row["backend"])
                for row in operation_map.audit()["declared_without_adapter"]}
    assert reported == expected
    assert ("marker.add", "resolve_axi") in reported
    assert ("clip.smart_reframe", "resolve_axi") in reported


def test_a_registered_adapter_the_router_cannot_select_is_reported():
    """Defect: eight operations had a registered semantic adapter on the
    `resolve_axi` backend, and the execution router names no such
    backend - so a semantic request could not reach them, while the
    registry said they were implemented.  An agent that read the
    registry would pick a route the router never takes."""
    rows = {row["backend"]: row
            for row in operation_map.audit()["adapters_without_a_route"]}
    assert "resolve_axi" in rows
    assert "clip.trim" in rows["resolve_axi"]["operations"]
    assert "resolve_axi" not in operation_map.router_backends()


def test_an_operation_with_two_writers_is_reported_as_a_duplicate():
    """Defect: `clip.trim` was writable by the touch-up AND by the AXI
    trim command, and the two are not interchangeable - only one is
    qualified.  A reader who picks by name gets whichever they found
    first."""
    reported = {row["operation"]
                for row in operation_map.audit()["duplicate_mechanisms"]}
    assert "clip.trim" in reported
    for row in operation_map.audit()["duplicate_mechanisms"]:
        assert len(row["mechanisms"]) > 1
        assert all(item.get("mechanism") for item in row["mechanisms"])


def test_an_implementation_no_axi_surface_reaches_is_reported():
    """Defect: the Color page window operations were implemented as UI
    recipes and were discoverable in the registry, and no AXI command
    reached them - so the only way to use them was to leave the request,
    receipt and recovery contract behind."""
    reported = {row["operation"]
                for row in operation_map.audit()["implemented_outside_axi"]}
    assert "color.window.create" in reported
    assert "color.window.modify" in reported
    assert "color.window.track" in reported


def test_an_implementation_outside_the_operation_registry_is_reported():
    """Defect: `fairlight.eq.set` had a working UI recipe and no algebra
    operation, so no AXI request could ever express it."""
    reported = {row["implementation"]
                for row in
                operation_map.audit()["implemented_outside_the_registry"]}
    assert "fairlight.eq.set" in reported


def test_every_planning_operation_compiles_into_primitives_that_write():
    """Defect: a planning operation states an editorial intent, and
    giving it an executor of its own would put a statement where a write
    belongs.  It must compile into operations that HAVE a writer - a
    compilation into something that cannot be executed is not one."""
    rows = operation_map.audit()["planning_only"]
    assert rows
    for row in rows:
        assert row["compiles_into"], (
            f"{row['operation']} is planning-only with no declared "
            "compilation")
        for primitive in row["compiles_into"]:
            status = edit_algebra.status(primitive)
            assert status is not None, (
                f"{row['operation']} compiles into unknown {primitive!r}")
            assert status.implementation_status.startswith("implemented"), (
                f"{row['operation']} compiles into {primitive!r}, which has "
                "no executor")
    assert operation_map.planning_operations_without_primitives() == []
    assert operation_map.stale_planning_declarations() == []


def test_every_implementation_evidence_reference_names_code_in_the_tree():
    """Defect: an evidence entry that points at a symbol the tree no
    longer has is a claim of an implementation that does not exist, and
    a map that publishes it is publishing a lie."""
    assert operation_map.audit()["unresolved_implementation_evidence"] == []
    resolved = 0
    for entry in _map()["operations"].values():
        for row in entry["backend_capabilities"]:
            for item in row["implementation_evidence"]:
                assert item["kind"] in {"resolved", "prose"}
                if item["kind"] == "resolved":
                    resolved += 1
                    assert (REPO / item["path"]).is_file()
    assert resolved, "no implementation evidence resolved at all"


def test_a_support_level_never_claims_more_than_the_evidence_allows():
    """Defect: an operation marked production-enabled without a complete
    live qualification record is a promise the engine cannot keep."""
    for op_id, entry in _map()["operations"].items():
        status = edit_algebra.status(op_id)
        level = entry["support_level"]
        if level == operation_map.SUPPORT_PRODUCTION:
            assert status.production_enabled
            assert any(row["production_enabled"]
                       for row in entry["backend_capabilities"])
        if level == operation_map.SUPPORT_CONTEXT:
            assert status.qualified
            assert not status.production_enabled
        if level == operation_map.SUPPORT_IMPLEMENTED:
            assert status.implementation_status.startswith("implemented")
            assert not status.production_enabled
        if level == operation_map.SUPPORT_UNSUPPORTED:
            assert not status.compilable or not status.backends
            assert status.implementation_status in {
                "planning_only", "workflow_only", "no_executor"}


def test_an_operation_with_no_axi_surface_is_reported_rather_than_hidden():
    """Defect: an operation with an implementation and neither a
    routable backend nor an AXI command has no way in - and an agent
    told only that it is 'implemented' would go looking in the tree for
    a way to write it.  The map must name every one of them, and the
    Color page window operations are three of them today."""
    surfaced = {
        op_id for op_id, entry in _map()["operations"].items()
        if entry["backend_capabilities"]
        and any(row["implemented"] for row in entry["backend_capabilities"])
        and not entry["how_to_request"]["edit_request_routable"]
        and not entry["how_to_request"]["commands"]
    }
    reported = {row["operation"]
                for row in operation_map.audit()["implemented_outside_axi"]}
    assert surfaced == reported, (
        "the map's audit does not cover every operation with no AXI "
        "surface")
    assert "color.window.create" in reported
    assert "color.window.modify" in reported
    assert "color.window.track" in reported


def test_the_map_is_served_through_resolve_axi_as_json():
    """Defect: a map that exists only as a Python object is not a map a
    model can consult - the whole point is that the agent asks AXI."""
    result = subprocess.run(
        [sys.executable, "-m", "library.tools.resolve_axi_core_commands",
         "operation-map", "clip.trim"],
        capture_output=True, text=True, encoding="utf-8",
        cwd=str(REPO), check=False)
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["schema"] == operation_map.SCHEMA
    assert payload["operation"]["id"] == "clip.trim"
    assert payload["operation"]["semantics"]["purpose"]
