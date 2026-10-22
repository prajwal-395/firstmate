"""PROD-002: a capability's readiness is ONE join, and it is measured.

`ren doctor` answers the dependency axis - the needs a capability
refuses without.  It did not answer the executor axis: a capability
whose every need is met can still be unable to DELIVER, because the
edit operation it dispatches was qualified on another Resolve build, or
has no live-qualified backend at all, or the machine cannot hold the
memory the scheduler reserves for its phase.

The defects these tests name:

- a capability ADVERTISED production support its executor cannot
  deliver here - every need met, and the qualification the executor
  carries names a Resolve build this machine does not run;
- an UNREAD Resolve build read as a pass.  A build nobody read is not a
  build that satisfies the predicate: the answer is unknown, and an
  unknown is not a pass;
- a FAILING need and an UNDETERMINED need collapsing into one answer.
  A check that could not run is not a measured absence;
- a second roster of the capabilities' edit operations, which is what
  `library/tools/operation_map.py` exists to prevent.  The composition
  comes from the operation registry's own declaration;
- a machine that cannot hold the scheduler's reservation reported as
  ready, because the reservation was read from a table written here
  instead of the scheduler's own demand map;
- the environment read twice, once per surface, so the doctor and the
  catalog could disagree about which Resolve build is running.  The
  facts travel with the check that measured them.

Read-only: every test here reads registries and builds fixtures.  No
test reaches a real project, a real Resolve or a real model.
"""

from __future__ import annotations

import json

from library.tools import (
    capability_coverage,
    capability_discovery,
    capability_guidance,
    capability_readiness,
    machine_needs,
    resource_scheduler,
)
from ren import doctor


def _recorded_resolve_version() -> str:
    """The Resolve build the qualification records were earned on.

    Read from the registry through the coverage report's own reader, so
    this test does not pin a version string that may move.
    """
    rows = capability_coverage.qualification_rows()
    recorded = rows[("clip.insert", "resolve_axi")]["environment"]
    return str(recorded["resolve_version"])


def _environment(*, resolve_version: str | None, memory_gb: float | None = 24.0):
    return capability_readiness.Environment(
        platform="darwin",
        resolve_connected=True if resolve_version else None,
        resolve_version=resolve_version,
        memory_gb=memory_gb)



def _covered(capability_id: str) -> set:
    """The needs a machine checked and passed.

    A required need NO check answered is undetermined, so a fixture that
    means "every need passed" has to say which needs it answered.
    """
    return set(machine_needs.needs_of(capability_id).requires)

def _machine_checks(*, resolve_version: str | None, memory_gb: float | None = 24.0):
    """A machine whose every need passes, with the facts it measured.

    EVERY need is answered: a required need no check answered is
    undetermined, so a fixture that means "every need passed" has to
    say so.
    """
    facts = {"resolve_connected": True if resolve_version else None,
             "resolve_version": resolve_version}
    checks = []
    for need in sorted(machine_needs.NEEDS):
        measured = {}
        if need == "resolve.scripting":
            measured = facts
        if need == "hardware.memory" and memory_gb is not None:
            measured = {"total_memory_gb": memory_gb}
        checks.append(doctor.Check(need, True, "stub", need=need,
                                   measured=measured))
    return checks


# ── The executor axis ─────────────────────────────────────────────────


def test_a_capability_whose_executor_was_qualified_elsewhere_is_not_available():
    """Every need met, and the work still cannot be delivered.

    The defect: the catalog and the doctor answered the dependency
    axis only, so a machine running a Resolve build the qualification
    does not name advertised the capability as supported - and the
    executor's first production edit would be refused by the router.
    """
    other = "0.0.0.0"
    assert other != _recorded_resolve_version()
    row = capability_readiness.capability_readiness(
        "reel.build",
        failing_needs=(), undetermined_needs=(),
        covered_needs=_covered("reel.build"),
        environment=_environment(resolve_version=other))

    # The dependency axis is satisfied: the needs are met.
    assert row["required"]["missing"] == []
    # The executor axis is not.
    assert row["features"]["state"] == machine_needs.UNAVAILABLE
    assert row["state"] == machine_needs.UNAVAILABLE
    assert "clip.insert" in row["features"]["unavailable"]
    # The refusal is the ROUTER's own wording, so the report and the
    # router cannot disagree about the build.
    from library.tools import execution_router

    inspected = {"inspected": True, "state": {"resolve_version": other}}
    mismatch = execution_router.qualification_mismatches(
        "clip.insert", inspected)
    assert any(line.startswith("resolve_version") for line in mismatch)
    assert mismatch[0] in row["reason"] or any(
        line in row["reason"] for line in mismatch)


def test_a_capability_whose_executor_matches_this_machine_is_available():
    """The mirror of the test above: the same machine, on the build the
    evidence was earned on, delivers the work."""
    row = capability_readiness.capability_readiness(
        "reel.build",
        failing_needs=(), undetermined_needs=(),
        covered_needs=_covered("reel.build"),
        environment=_environment(resolve_version=_recorded_resolve_version()))

    assert row["features"]["state"] == machine_needs.AVAILABLE
    assert row["features"]["ready"] == ["clip.insert"]
    assert row["state"] == machine_needs.AVAILABLE
    assert row["reason"] == ""


def test_a_resolve_build_nobody_read_is_unknown_not_a_pass():
    """An unread build is not a build that satisfies the predicate.

    The defect: a busy Resolve (the probe timed out) is the common case
    on a working machine, and reading it as a mismatch would refuse
    every real delivery - while reading it as a match would advertise
    a capability the machine cannot deliver.
    """
    row = capability_readiness.capability_readiness(
        "reel.build",
        failing_needs=(), undetermined_needs=(),
        covered_needs=_covered("reel.build"),
        environment=_environment(resolve_version=None))

    assert row["features"]["state"] == machine_needs.UNKNOWN
    assert row["state"] == machine_needs.UNKNOWN
    assert "clip.insert" in row["features"]["unknown"]
    assert row["features"]["unavailable"] == []


def test_a_capability_with_no_edit_operation_declares_no_executor():
    """The executor axis is not applicable when there is no executor.

    A capability that dispatches no edit operation is not held back by
    an edit qualification it never uses.
    """
    row = capability_readiness.capability_readiness(
        "footage.scan",
        failing_needs=(), undetermined_needs=(),
        covered_needs=_covered("footage.scan"),
        environment=_environment(resolve_version=None))

    assert row["features"]["operations"] == []
    assert row["features"]["state"] == capability_readiness.NOT_APPLICABLE
    assert row["state"] == machine_needs.AVAILABLE


def test_the_executor_operations_come_from_the_declared_composition():
    """One roster: the operation registry's own EditPatch declaration.

    A second list of "what each capability dispatches" is exactly the
    drift `library/tools/operation_map.py` exists to prevent.
    """
    from library.tools import operations

    declared = {operation.name: tuple(operation.execution.patch.operations)
                for operation in operations.all()
                if operation.execution.patch is not None
                and operation.execution.patch.operations}
    assert capability_readiness.executor_operations() == declared
    assert declared["reel.build"] == ("clip.insert",)


def test_a_timeline_condition_is_listed_and_never_judged():
    """The timeline half of a qualification is not a machine fact.

    The media type, the density and the frame rate describe the
    timeline in front of you; `resolve-axi support` answers them
    against a measured timeline, and this report only names them.
    """
    row = capability_readiness.capability_readiness(
        "reel.build",
        failing_needs=(), undetermined_needs=(),
        covered_needs=_covered("reel.build"),
        environment=_environment(resolve_version=_recorded_resolve_version()))

    fields = {condition["field"] for condition in row["features"]["conditions"]}
    assert "media_type" in fields
    assert "resolve_version" not in fields, (
        "the Resolve build is a machine fact and is judged, not listed")


# ── The dependency axis, kept apart ───────────────────────────────────


def test_a_failing_need_and_an_undetermined_need_are_different_answers():
    """A check that could not run is not a measured absence."""
    failing = capability_readiness.capability_readiness(
        "reel.build",
        failing_needs={"resolve.scripting"}, undetermined_needs=(),
        covered_needs=_covered("reel.build"),
        environment=_environment(resolve_version=_recorded_resolve_version()))
    unsure = capability_readiness.capability_readiness(
        "reel.build",
        failing_needs=(), undetermined_needs={"resolve.scripting"},
        covered_needs=_covered("reel.build"),
        environment=_environment(resolve_version=_recorded_resolve_version()))

    assert failing["state"] == machine_needs.UNAVAILABLE
    assert unsure["state"] == machine_needs.UNKNOWN
    assert failing["required"]["missing"] == ["resolve.scripting"]
    assert unsure["required"]["undetermined"] == ["resolve.scripting"]


def test_the_optional_enhancements_are_reported_apart_from_the_required():
    """A need the capability runs without is a degradation, not a
    refusal, and the report says which is which."""
    row = capability_readiness.capability_readiness(
        "reel.build",
        failing_needs=(), undetermined_needs=(),
        covered_needs=_covered("reel.build"),
        environment=_environment(resolve_version=_recorded_resolve_version()))

    assert row["required"]["missing"] == []
    assert row["optional"]["needs"]
    assert row["optional"]["degraded"] == {}


# ── The resource axis ─────────────────────────────────────────────────


def test_a_machine_that_cannot_hold_the_reservation_cannot_run_the_capability(
        monkeypatch):
    """The reservation is the scheduler's own number, judged by the
    scheduler's own admission rule.

    The defect: a report that compared the reservation with the
    machine's TOTAL read 21.6 GB of headroom on a box that has 6.7 GB
    free and is already paging - and the scheduler would have refused
    the very job the report called ready.
    """
    reservation = resource_scheduler.MEASURED_MEMORY_RESERVATIONS_GB[
        "semantics.analyse:model_memory"]
    assert reservation, "the scheduler declares no reservation to check"

    # The suite stubs the machine's memory reading; a test OF the guard
    # patches the reading itself.
    monkeypatch.setattr(resource_scheduler, "_memory_available_gb",
                        lambda: (20.0, 24.0))
    fits = capability_readiness.capability_readiness(
        "semantics.analyse", failing_needs=(), undetermined_needs=(),
        covered_needs=_covered("semantics.analyse"),
        environment=_environment(resolve_version=None, memory_gb=24.0))

    monkeypatch.setattr(resource_scheduler, "_memory_available_gb",
                        lambda: (6.0, 24.0))
    tight = capability_readiness.capability_readiness(
        "semantics.analyse", failing_needs=(), undetermined_needs=(),
        covered_needs=_covered("semantics.analyse"),
        environment=_environment(resolve_version=None, memory_gb=24.0))

    assert fits["resources"], "the reservation was not read from the scheduler"
    assert fits["resources"][0]["ram_gb"] == reservation
    assert fits["resources"][0]["satisfied"] is True
    assert fits["state"] == machine_needs.AVAILABLE

    assert tight["resources"][0]["satisfied"] is False
    assert tight["state"] == machine_needs.UNAVAILABLE
    assert "reservation" in tight["reason"]


def test_a_machine_whose_memory_nobody_read_is_not_refused(monkeypatch):
    """An unmeasured reservation is unknown, not a refusal."""
    monkeypatch.setattr(resource_scheduler, "_memory_available_gb",
                        lambda: None)
    row = capability_readiness.capability_readiness(
        "semantics.analyse", failing_needs=(), undetermined_needs=(),
        covered_needs=_covered("semantics.analyse"),
        environment=_environment(resolve_version=None, memory_gb=None))

    assert row["resources"][0]["satisfied"] is None
    assert row["state"] == machine_needs.AVAILABLE


# ── The environment travels with the check that measured it ───────────


def test_the_probe_facts_travel_with_the_line_that_measured_them():
    """One environment, read from the checks.

    The defect: every surface that asked the doctor probed Resolve
    again, and two probes can answer differently.
    """
    connected = doctor.resolve_checks(
        {"running": True, "module": True, "connected": True,
         "product": "DaVinci Resolve Studio", "version": "21.1.0.14"})
    assert connected[0].measured["resolve_connected"] is True
    assert connected[0].measured["resolve_version"] == "21.1.0.14"

    timed_out = doctor.resolve_checks(
        {"running": True, "module": False, "connected": False,
         "timed_out": True, "error": "the scripting connection did not answer"})
    assert timed_out[0].measured["resolve_connected"] is None, (
        "a probe that timed out measured nothing; it is not an absence")

    not_running = doctor.resolve_checks(
        {"running": False, "module": False, "connected": False})
    assert not_running[0].measured["resolve_connected"] is False


def test_the_readiness_reads_the_environment_out_of_the_checks():
    """The doctor's own checks are the only source of machine facts."""
    checks = _machine_checks(resolve_version=_recorded_resolve_version())
    environment = capability_readiness.environment_from_checks(checks)

    assert environment.resolve_version == _recorded_resolve_version()
    assert environment.resolve_connected is True
    assert environment.memory_gb == 24.0
    assert environment.platform == "darwin"


# ── The surfaces that publish it ──────────────────────────────────────


def _stub_groups(monkeypatch, resolve_version: str | None):
    """Every doctor group passes, with the Resolve facts this machine has."""
    for name, attr, covers in doctor.GROUPS:
        if attr == "resolve_checks":
            monkeypatch.setattr(doctor, attr, lambda _probe=None, version=resolve_version: [
                doctor.Check("Resolve scripting", True, "stub",
                             need="resolve.scripting",
                             measured={"resolve_connected": True,
                                       "resolve_version": version}),
                doctor.Check("Resolve edition", True, "stub",
                             need="resolve.studio")])
        else:
            monkeypatch.setattr(doctor, attr, lambda name=name, covers=covers: [
                doctor.Check(name, True, "stub", need=need)
                for need in covers])


def test_the_doctor_refuses_a_capability_its_executor_cannot_deliver(monkeypatch):
    """`ren doctor --for <id>` exits 1 when the work cannot be done.

    The advertisement and the exit code are the same claim: a caller
    that asked may not be told "available" for a machine that will
    refuse the edit.
    """
    _stub_groups(monkeypatch, "0.0.0.0")
    assert doctor.main(["--for", "reel.build"]) == 1

    _stub_groups(monkeypatch, _recorded_resolve_version())
    assert doctor.main(["--for", "reel.build"]) == 0


def test_the_doctor_report_carries_the_readiness(monkeypatch, capsys):
    """The JSON report names what this machine can actually perform."""
    _stub_groups(monkeypatch, _recorded_resolve_version())
    assert doctor.main(["--json"]) == 0
    payload = json.loads(capsys.readouterr().out)

    readiness = payload["readiness"]
    assert readiness["schema"] == capability_readiness.SCHEMA
    assert readiness["environment"]["resolve_version"] == \
        _recorded_resolve_version()
    row = readiness["capabilities"]["reel.build"]
    assert row["state"] == machine_needs.AVAILABLE
    assert row["features"]["ready"] == ["clip.insert"]
    assert row["features"]["conditions"], "the timeline conditions are listed"


def test_the_budgeted_default_stays_budgeted(monkeypatch, capsys):
    """LE-12: the readiness does not turn the brief into a ledger."""
    _stub_groups(monkeypatch, _recorded_resolve_version())
    assert doctor.main([]) == 0
    brief = capsys.readouterr().out

    assert len(brief.encode("utf-8")) <= 1500
    assert "PASS" not in brief
    assert "passed," in brief


def test_the_catalog_does_not_advertise_a_capability_it_cannot_deliver():
    """The catalog's supported list is an advertisement.

    A capability whose executor cannot run here is not on it, however
    well its needs are met.
    """
    checks = _machine_checks(resolve_version="0.0.0.0")
    payload = capability_discovery.catalog(checks=checks)
    rows = {row["id"]: row for row in payload["capabilities"]}

    assert rows["reel.build"]["verdict"] == machine_needs.AVAILABLE, (
        "the needs are met; the executor axis is what refuses it")
    assert rows["reel.build"]["readiness"]["state"] == machine_needs.UNAVAILABLE
    supported = capability_discovery.unknown_guidance(
        "nope", checks=checks)["supported"]
    assert "reel.build" not in supported, (
        "the catalog advertised a capability its executor cannot deliver")

    good_checks = _machine_checks(resolve_version=_recorded_resolve_version())
    good = capability_discovery.catalog(checks=good_checks)
    good_rows = {row["id"]: row for row in good["capabilities"]}
    assert good_rows["reel.build"]["readiness"]["state"] == machine_needs.AVAILABLE
    assert "reel.build" in capability_discovery.unknown_guidance(
        "nope", checks=good_checks)["supported"]


def test_a_capability_the_machine_cannot_host_is_not_advertised(monkeypatch):
    """The advertisement and the scheduler agree.

    The defect: the catalog advertised a capability the scheduler would
    refuse - a vision pass whose resident model does not fit in the
    memory this machine has free.
    """
    monkeypatch.setattr(resource_scheduler, "_memory_available_gb",
                        lambda: (6.0, 24.0))
    checks = _machine_checks(resolve_version=_recorded_resolve_version())
    rows = {row["id"]: row for row in
            capability_discovery.catalog(checks=checks)["capabilities"]}

    assert rows["semantics.analyse"]["verdict"] == machine_needs.AVAILABLE, (
        "the needs are met; the machine's memory is what refuses it")
    assert rows["semantics.analyse"]["readiness"]["state"] == \
        machine_needs.UNAVAILABLE
    supported = capability_discovery.unknown_guidance(
        "nope", checks=checks)["supported"]
    assert "semantics.analyse" not in supported


def test_an_unmeasured_catalog_says_not_measured_for_the_executor_too():
    """No probe behind the catalog means no claim about the executor."""
    payload = capability_discovery.catalog(checks=None)
    states = {row["readiness"]["state"] for row in payload["capabilities"]}
    assert states == {capability_readiness.NOT_MEASURED}


def test_a_capability_its_executor_cannot_deliver_is_guided_not_bare():
    """The refusal names the executor, and says what to run next."""
    checks = _machine_checks(resolve_version="0.0.0.0")
    row = capability_discovery.record("reel.build", checks=checks)

    guidance = row["guidance"]
    assert guidance is not None, "an undeliverable capability came back bare"
    assert guidance["status"] == capability_guidance.UNAVAILABLE
    assert "deliver" in guidance["what"]
    assert "ren doctor --for reel.build" in guidance["fix"]


def test_the_per_capability_view_carries_the_readiness(monkeypatch, capsys):
    """`ren doctor --for <id>` names the executor's answer, not only the
    needs'."""
    _stub_groups(monkeypatch, "0.0.0.0")
    doctor.main(["--for", "reel.build", "--all"])
    out = capsys.readouterr().out

    assert "readiness: unavailable" in out
    assert "clip.insert" in out
    assert "timeline conditions" in out
