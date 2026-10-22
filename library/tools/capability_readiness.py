"""What the installed machine can actually perform, per capability.

`ren doctor` already answers one axis: the DEPENDENCY axis - the needs
a capability refuses without (`library/tools/machine_needs.py`), read
from the doctor's own checks.  That axis says a machine has the
software, the models and the Resolve connection a capability asks for.

It does not answer the EXECUTOR axis: whether the capability's executor
can actually DELIVER its work here.  A capability can have every need
satisfied and still be unable to deliver, because

  * the edit operation its executor dispatches has no live-qualified
    backend in the qualification registry, so ordinary production
    routing refuses it (`library/tools/axi_qualification_records.json`);
  * the qualification it WAS earned in names a Resolve build or a
    platform this machine does not run, so the recorded predicates can
    never be satisfied here;
  * the machine's memory cannot hold the reservation the resource
    scheduler declares for the capability's phase
    (`library/tools/resource_scheduler.py`).

This module joins those facts into one readiness row per capability, and
reads every one of them from the registry that owns it:

    the dependency axis   library/tools/machine_needs.py
    the executor axis     library/tools/edit_algebra.py (OP_STATUS) and
                          library/tools/capability_coverage.py (the
                          qualification rows the coverage report
                          publishes)
    the resource axis     library/tools/resource_scheduler.py

Nothing here declares an operation, a backend, a qualification or a
capability.  A second roster is exactly the defect
`library/tools/operation_map.py` exists to prevent, and this file sits
BESIDE that map: the map answers "what does this operation MEAN", and
this one answers "may this machine perform this capability here".

THE ENVIRONMENT, AND WHO MEASURED IT
------------------------------------
The facts a readiness answer depends on - the running Resolve's build,
whether it is connected, how much memory the machine has - are measured
by the doctor, and travel with the check that measured them
(`ren/doctor.py` puts them in `Check.measured`).  This module reads
them from the check list, so every surface that asks the doctor (the
report, the per-capability view, the capability catalog) reads ONE
environment and cannot disagree about it.

TWO QUALIFICATIONS, KEPT APART
-------------------------------
An operation can be QUALIFIED (live evidence exists, under recorded
predicates) and still not be RUNNABLE here (the evidence names another
Resolve build, or the machine cannot reach Resolve, or the reservation
does not fit).  Those are different facts, and this module keeps them
apart: `support_level` is the registry's own band, `state` is the
machine's answer.

The TIMELINE half of a qualification - the media type, the frame rate,
the density - is not a machine fact at all.  It is answered against the
timeline in front of you by `resolve-axi support <operation>
--conditions ...`, and this module LISTS those conditions rather than
pretending to judge them.

An UNKNOWN answer is never a pass.  A machine whose Resolve build
could not be read, or whose memory could not be measured, is not a
machine that satisfies a predicate: the state stays `unknown` and the
capability is not advertised as performable.

    library/tools/capability_readiness.py
"""

from __future__ import annotations

import sys
from collections.abc import Mapping
from dataclasses import dataclass
from functools import cache

from library.tools import machine_needs

SCHEMA = "ren-capability-readiness/v1"

#: The needs every Resolve-backed executor runs under.
RESOLVE_NEEDS = ("resolve.scripting", "resolve.studio")

#: The capability declares no edit operation, so no executor runs here.
NOT_APPLICABLE = "not_applicable"

#: The verdict of a capability nobody measured on this machine.  It is
#: the catalog's own word (`library/tools/capability_discovery.py`),
#: named here so two modules cannot drift apart on it.
NOT_MEASURED = "not_measured"

#: The qualification dimension that describes the Resolve BUILD rather
#: than the timeline.  Every other recorded predicate is a property of
#: the timeline in front of you.
RESOLVE_VERSION_FIELD = "resolve_version"


@dataclass(frozen=True)
class Environment:
    """What this machine measured about the environment an executor needs.

    `None` means "could not be determined", which is not the same as
    False: an environment nobody read is not an environment that fails a
    predicate.
    """

    platform: str | None = None
    resolve_connected: bool | None = None
    resolve_version: str | None = None
    memory_gb: float | None = None

    def as_dict(self) -> dict:
        return {
            "platform": self.platform,
            "resolve_connected": self.resolve_connected,
            "resolve_version": self.resolve_version,
            "memory_gb": self.memory_gb,
        }


def canonical_version(value) -> str:
    """The Resolve version in the one form a predicate can compare.

    One reading, from the module that owns it: Resolve's own
    `GetVersionString` reports a bare dotted version, and a qualification
    record may carry the product label.  Both are canonicalised here, so
    a record and a live probe compare.
    """
    from library.tools.axi_qualification import canonical_resolve_version

    return canonical_resolve_version(value)


def machine_platform() -> str:
    """This machine's platform, in the vocabulary the records use."""
    return "darwin" if sys.platform == "darwin" else sys.platform


def need_states(checks) -> tuple[set, set, set]:
    """`(failing, undetermined, covered)` need ids, read from a check list.

    An undetermined check is not a failing one: a check that could not
    run is not a measured absence.  `covered` is the set of needs some
    check answered at all, which is how a required need nobody measured
    is told apart from a required need that passed.
    """
    failing: set[str] = set()
    undetermined: set[str] = set()
    covered: set[str] = set()
    for check in checks or ():
        need = getattr(check, "need", "")
        if not need:
            continue
        covered.add(need)
        if getattr(check, "undetermined", False):
            undetermined.add(need)
        elif not getattr(check, "ok", False):
            failing.add(need)
    return failing, undetermined, covered


def environment_from_checks(checks) -> Environment:
    """The machine facts the checks measured, and the platform.

    A fact nobody measured stays `None`: the readiness answer for a
    machine whose Resolve build was never read is `unknown`, which is
    not the same as `unavailable`.
    """
    connected: bool | None = None
    version: str | None = None
    memory: float | None = None
    for check in checks or ():
        facts = getattr(check, "measured", None) or {}
        if "resolve_connected" in facts:
            connected = facts["resolve_connected"]
            version = canonical_version(facts.get("resolve_version")) or None
        if "total_memory_gb" in facts:
            memory = float(facts["total_memory_gb"])
    return Environment(platform=machine_platform(), resolve_connected=connected,
                       resolve_version=version, memory_gb=memory)


# ── The registries, read once ─────────────────────────────────────────


@cache
def executor_operations() -> dict:
    """Capability id -> the edit operations its executor declares.

    Read from the operation registry's own EditPatch composition
    (`library/tools/operations.py`), which is where a capability says
    what its executor may dispatch.  A capability that declares no
    composition dispatches no edit operation, and says so by being
    absent here.
    """
    from library.tools import operations

    declared: dict[str, tuple[str, ...]] = {}
    for operation in operations.all():
        patch = operation.execution.patch
        if patch is not None and patch.operations:
            declared[operation.name] = tuple(patch.operations)
    return declared


@cache
def _qualification_rows() -> dict:
    """The qualification registry's evidence-complete rows.

    Read through the coverage report's own reader, so the readiness
    report and the capability coverage report cannot disagree about what
    evidence exists.
    """
    from library.tools import capability_coverage

    return capability_coverage.qualification_rows()


def _recorded_environment(op_id: str, backend: str) -> Mapping:
    """The environment one backend's evidence was earned in."""
    row = _qualification_rows().get((op_id, backend)) or {}
    return row.get("environment") or {}


def _required_resolve_versions(op_id: str, backend: str) -> list:
    """The Resolve builds one backend's evidence is valid for.

    Read from the recorded PREDICATE, because that is what the router
    compares the running Resolve against at edit time; the registry's
    environment block records the same build, and either reading names
    the same build.
    """
    from library.tools import edit_algebra

    entry = edit_algebra.status(op_id)
    if entry is None:
        return []
    versions: list[str] = []
    for row in entry.backends:
        if row.backend != backend or not row.production_enabled:
            continue
        for predicate in row.qualification_predicates:
            if predicate.get("field") != RESOLVE_VERSION_FIELD:
                continue
            value = predicate.get("value")
            for candidate in (value if isinstance(value, list) else [value]):
                canonical = canonical_version(candidate)
                if canonical and canonical not in versions:
                    versions.append(canonical)
    return versions


# ── One edit operation, on this machine ───────────────────────────────


def operation_readiness(op_id: str, *, environment: Environment,
                        failing_needs=(), undetermined_needs=()) -> dict:
    """Whether one edit operation can run in production on this machine.

    The machine half of the answer: does a production-enabled backend
    exist, can this machine reach Resolve, and does the Resolve build
    this machine runs match the build the evidence was earned on.  The
    TIMELINE half - the media type, the density, the frame rate - is
    listed, never judged: that is a property of the timeline in front of
    you, and `resolve-axi support <operation> --conditions ...` answers
    it against a measured timeline.
    """
    from library.tools import edit_algebra, operation_map

    entry = edit_algebra.status(op_id)
    if entry is None:
        return {
            "operation": op_id,
            "support_level": operation_map.SUPPORT_UNSUPPORTED,
            "state": machine_needs.UNAVAILABLE,
            "reason": f"no edit operation is declared for {op_id!r}",
            "environment": {},
            "conditions": [],
        }

    support_level = operation_map.support_level(entry)
    conditions = _conditions(entry)
    production = [backend for backend in entry.backends
                  if backend.production_enabled]
    if not production:
        return {
            "operation": op_id,
            "support_level": support_level,
            "state": machine_needs.UNAVAILABLE,
            "reason": (f"no production-enabled backend for {op_id!r} "
                       f"(support level: {support_level})"),
            "environment": {},
            "conditions": conditions,
        }

    # The executor writes the live timeline through Resolve, so a
    # machine that cannot reach a Studio Resolve cannot run it at all.
    failing = set(failing_needs)
    undetermined = set(undetermined_needs)
    missing = [need for need in RESOLVE_NEEDS if need in failing]
    if missing:
        return {
            "operation": op_id,
            "support_level": support_level,
            "state": machine_needs.UNAVAILABLE,
            "reason": ("the executor cannot run: "
                        + ", ".join(missing) + " is not satisfied"),
            "environment": {},
            "conditions": conditions,
        }
    unsure = [need for need in RESOLVE_NEEDS if need in undetermined]
    if unsure:
        return {
            "operation": op_id,
            "support_level": support_level,
            "state": machine_needs.UNKNOWN,
            "reason": ("the executor could not be determined: "
                        + ", ".join(unsure) + " is undetermined"),
            "environment": {},
            "conditions": conditions,
        }

    satisfied, reason, detail = _environment_state(op_id, production,
                                                    environment)
    if satisfied is False:
        return {
            "operation": op_id,
            "support_level": support_level,
            "state": machine_needs.UNAVAILABLE,
            "reason": reason,
            "environment": detail,
            "conditions": conditions,
        }
    if satisfied is None:
        return {
            "operation": op_id,
            "support_level": support_level,
            "state": machine_needs.UNKNOWN,
            "reason": reason,
            "environment": detail,
            "conditions": conditions,
        }
    return {
        "operation": op_id,
        "support_level": support_level,
        "state": machine_needs.AVAILABLE,
        "reason": "",
        "environment": detail,
        "conditions": conditions,
    }


def _conditions(entry) -> list:
    """The recorded qualification predicates, listed and never judged."""
    conditions: list[dict] = []
    for backend in entry.backends:
        if not backend.production_enabled:
            continue
        for predicate in backend.qualification_predicates:
            condition = {"field": str(predicate.get("field", "")),
                         "operator": str(predicate.get("operator", "")),
                         "value": predicate.get("value")}
            if condition not in conditions:
                conditions.append(condition)
    return conditions


def _environment_state(op_id: str, backends, environment: Environment):
    """The recorded environment, against this machine.

    `(satisfied, reason, detail)`: `satisfied` is True, False, or None
    when this machine could not be measured.

    The DECISION is the router's own: `execution_router
    .qualification_mismatches` compares the recorded predicates against
    this machine's own reading, so the report and the router cannot
    disagree about a build - a second comparison here would be a second
    answer.  Only the machine's half is fed to it: the timeline's half
    is listed, never judged, because `resolve-axi support <operation>
    --conditions ...` answers that against a measured timeline.

    The platform the evidence was earned on is REPORTED, not enforced:
    the router does not refuse on it, and a report that refuses where
    the router admits would be the disagreement this module exists to
    prevent.
    """
    from library.tools import execution_router

    required_versions: set = set()
    platforms: set = set()
    for backend in backends:
        recorded = _recorded_environment(op_id, backend.backend)
        platform = str(recorded.get("platform") or "")
        if platform:
            platforms.add(platform)
        required_versions.update(
            _required_resolve_versions(op_id, backend.backend))
    detail = {
        "required_resolve_version": sorted(required_versions),
        "installed_resolve_version": environment.resolve_version,
        "required_platform": sorted(platforms),
        "installed_platform": environment.platform,
        "satisfied": None,
    }
    if not required_versions:
        # The evidence names no build, so this machine's build cannot
        # refuse it.
        detail["satisfied"] = True
        return True, "", detail
    if not environment.resolve_version:
        return None, ("this machine's Resolve version could not be "
                      "read, so the recorded build cannot be "
                      "compared"), detail
    inspected = {"inspected": True,
                 "state": {"resolve_version": environment.resolve_version}}
    mismatches = execution_router.qualification_mismatches(op_id, inspected)
    version_mismatches = [mismatch for mismatch in mismatches
                          if mismatch.startswith(RESOLVE_VERSION_FIELD)]
    if version_mismatches:
        detail["satisfied"] = False
        return False, ("the recorded qualification names a Resolve build "
                       "this machine does not run: "
                       + "; ".join(version_mismatches)), detail
    detail["satisfied"] = True
    return True, "", detail


# ── The resource axis ─────────────────────────────────────────────────


@cache
def _profiles() -> dict:
    """The scheduler's demand map, read once."""
    from library.tools import resource_scheduler

    return resource_scheduler.profiles()


def resource_reservations(capability_id: str, *,
                           environment: Environment) -> list:
    """The RAM the capability's phases reserve, against this machine.

    Read from the scheduler's own demand map - not a number written
    here - and judged by the scheduler's own admission rule
    (`resource_scheduler.admission_verdict`), so a report and the
    scheduler cannot disagree about whether the reservation fits.  The
    scheduler admits against LIVE available memory and refuses on a
    paging machine (PROD-006), and a report that used the machine's
    TOTAL instead would read 21.6 GB of headroom on a box that has
    6.7 GB free and is already paging.
    """
    from library.tools import resource_scheduler

    rows = []
    for profile, demand in sorted(_profiles().items()):
        if not profile.startswith(f"{capability_id}:"):
            continue
        reserved = int(demand.get("ram_gb") or 0)
        if not reserved:
            continue
        verdict = resource_scheduler.admission_verdict(reserved)
        rows.append({
            "profile": profile,
            "ram_gb": reserved,
            "machine_memory_gb": environment.memory_gb,
            "available_gb": verdict["available_gb"],
            "headroom_gb": verdict["headroom_gb"],
            "satisfied": verdict["fits"],
            "reason": verdict["reason"],
        })
    return rows


# ── One capability, on this machine ───────────────────────────────────


def capability_readiness(capability_id: str, *, failing_needs=(),
                         undetermined_needs=(), covered_needs=(),
                         environment: Environment | None = None,
                         require_measured: bool = True) -> dict:
    """One capability's readiness on this machine.

    Three groups, reported apart because they are apart: the REQUIRED
    dependencies the capability refuses without, the OPTIONAL
    enhancements it runs without, and the FEATURES its executor cannot
    deliver here.

    `require_measured` says what a required need NO check answered
    means.  True (the catalog's reading): the machine was never measured
    for it, so it is undetermined.  False (the doctor's reading): only
    the needs a check answered count, because the doctor says the same
    about the capability it prints above this one.
    """
    env = environment or Environment()
    needs = machine_needs.needs_of(capability_id)
    covered = set(covered_needs)
    unmeasured = ({need for need in needs.requires
                   if need not in covered} if require_measured else set())
    verdict, missing, degraded, undetermined = machine_needs.availability(
        capability_id, failing_needs, set(undetermined_needs) | unmeasured)
    operations = executor_operations().get(capability_id, ())
    features = {
        operation: operation_readiness(
            operation, environment=env, failing_needs=failing_needs,
            undetermined_needs=undetermined_needs)
        for operation in operations
    }
    resources = resource_reservations(capability_id, environment=env)
    production = _production_state(features)
    state = _state(verdict, production, resources)
    return {
        "id": capability_id,
        "state": state,
        "reason": _reason(state, verdict, missing, degraded, undetermined,
                          features, resources),
        "required": {
            "needs": list(needs.requires),
            "missing": list(missing),
            "undetermined": list(undetermined),
        },
        "optional": {
            "needs": dict(needs.degrades),
            "degraded": dict(degraded),
        },
        "features": {
            "state": production,
            "operations": list(operations),
            "ready": sorted(operation for operation, row in features.items()
                            if row["state"] == machine_needs.AVAILABLE),
            "unavailable": sorted(operation for operation, row
                                  in features.items()
                                  if row["state"] == machine_needs.UNAVAILABLE),
            "unknown": sorted(operation for operation, row in features.items()
                              if row["state"] == machine_needs.UNKNOWN),
            "conditions": _timeline_conditions(features),
        },
        "qualification": _qualification(features),
        "resources": resources,
        "detail": features,
    }


def _timeline_conditions(features: Mapping) -> list:
    """The recorded predicates that describe the TIMELINE, not the machine.

    The Resolve BUILD is judged against this machine (above); every
    other recorded predicate is a property of the timeline in front of
    you, and `resolve-axi support <operation> --conditions ...` answers
    it against a measured timeline.
    """
    conditions: list = []
    for row in features.values():
        for condition in row.get("conditions") or ():
            if condition.get("field") == RESOLVE_VERSION_FIELD:
                continue
            if condition not in conditions:
                conditions.append(condition)
    return conditions


def _qualification(features: Mapping) -> dict:
    """The Resolve-qualification constraint the executor carries here.

    The build and platform the evidence was earned on, and the build and
    platform this machine runs.  `satisfied` is the executor's own
    answer - None when this machine's build could not be read, which is
    not the same as a mismatch.
    """
    required_versions: set = set()
    platforms: set = set()
    installed_version = None
    installed_platform = None
    satisfied = True
    for row in features.values():
        environment = row.get("environment") or {}
        required_versions.update(
            environment.get("required_resolve_version") or ())
        platforms.update(environment.get("required_platform") or ())
        if environment.get("installed_resolve_version"):
            installed_version = environment["installed_resolve_version"]
        if environment.get("installed_platform"):
            installed_platform = environment["installed_platform"]
        answer = environment.get("satisfied")
        if answer is False:
            satisfied = False
        elif answer is None and environment:
            satisfied = None
    if not required_versions:
        satisfied = None
    return {
        "required_resolve_version": sorted(required_versions),
        "installed_resolve_version": installed_version,
        "required_platform": sorted(platforms),
        "installed_platform": installed_platform,
        "satisfied": satisfied,
    }


def _production_state(features: Mapping) -> str:
    """The executor axis, in the dependency vocabulary.

    `not_applicable` when the capability declares no edit operation;
    otherwise the same four words the dependency axis uses, so a reader
    compares two axes without learning a second set of words.
    """
    if not features:
        return NOT_APPLICABLE
    states = {row["state"] for row in features.values()}
    if states == {machine_needs.AVAILABLE}:
        return machine_needs.AVAILABLE
    if machine_needs.AVAILABLE in states:
        return machine_needs.DEGRADED
    if machine_needs.UNKNOWN in states:
        return machine_needs.UNKNOWN
    return machine_needs.UNAVAILABLE


def _state(verdict: str, production: str, resources: list) -> str:
    """The capability's overall state on this machine.

    A determinate failure outranks an undeterminable one - the same
    order `machine_needs.availability` uses - and the resource axis
    outranks both, because a machine that cannot hold the reservation
    cannot run the capability whatever else it has.
    """
    if any(row["satisfied"] is False for row in resources):
        return machine_needs.UNAVAILABLE
    if verdict == machine_needs.UNAVAILABLE:
        return machine_needs.UNAVAILABLE
    if production == machine_needs.UNAVAILABLE:
        return machine_needs.UNAVAILABLE
    if verdict == machine_needs.UNKNOWN:
        return machine_needs.UNKNOWN
    if production == machine_needs.UNKNOWN:
        return machine_needs.UNKNOWN
    if verdict == machine_needs.DEGRADED or production == machine_needs.DEGRADED:
        return machine_needs.DEGRADED
    return machine_needs.AVAILABLE


def _reason(state: str, verdict: str, missing, degraded, undetermined,
            features: Mapping, resources: list) -> str:
    """Why this capability is not simply available here.

    One line, in the words the dependency axis uses, naming the first
    thing a reader can go and fix.  An empty reason means available.
    A DEGRADED capability names both halves it runs short of: the
    optional need it does without, and the declared edits its executor
    cannot deliver.
    """
    if state == machine_needs.AVAILABLE:
        return ""
    short = [row for row in resources if row["satisfied"] is False]
    if short:
        row = short[0]
        return (f"the scheduler would refuse the {row['ram_gb']} GB "
                f"{row['profile']} reservation: {row['reason']}")
    if missing:
        return ("the needs it refuses without are missing: "
                + ", ".join(missing))
    if undetermined:
        return ("the needs behind it could not be measured: "
                + ", ".join(undetermined))
    blocked = [row for row in features.values()
               if row["state"] == machine_needs.UNAVAILABLE]
    unsure = [row for row in features.values()
              if row["state"] == machine_needs.UNKNOWN]
    if state == machine_needs.UNAVAILABLE:
        if blocked:
            row = blocked[0]
            return (f"the executor cannot deliver here: {row['operation']} - "
                    f"{row['reason']}")
        return f"{state}: the executor has no production-enabled backend"
    if state == machine_needs.UNKNOWN and unsure:
        row = unsure[0]
        return (f"the executor could not be determined: {row['operation']} - "
                f"{row['reason']}")
    parts = []
    if degraded:
        parts.append("an optional need it runs without is missing: "
                     + ", ".join(degraded))
    if blocked:
        parts.append(f"{len(blocked)} of {len(features)} declared edits have "
                     "no production-enabled backend")
    if unsure:
        parts.append(f"{len(unsure)} of {len(features)} declared edits could "
                     "not be determined here")
    return "; ".join(parts) or (
        f"{state}: the executor cannot deliver every declared edit")


# ── The whole machine ─────────────────────────────────────────────────


def report(*, failing_needs=(), undetermined_needs=(), covered_needs=(),
           environment: Environment | None = None, capability_ids=None,
           require_measured: bool = True) -> dict:
    """Every capability's readiness on this machine.

    The report is plain data: a consumer reads it, never calls into it.
    """
    env = environment or Environment()
    ids = tuple(capability_ids or sorted(machine_needs.CAPABILITY_NEEDS))
    capabilities = {
        capability_id: capability_readiness(
            capability_id, failing_needs=failing_needs,
            undetermined_needs=undetermined_needs,
            covered_needs=covered_needs, environment=env,
            require_measured=require_measured)
        for capability_id in ids
    }
    return {
        "schema": SCHEMA,
        "environment": env.as_dict(),
        "capabilities": capabilities,
        "summary": summary(capabilities),
    }


def summary(capabilities: Mapping) -> dict:
    """The counts a reader needs before the rows."""
    states: dict = {}
    features: dict = {}
    for row in capabilities.values():
        states[row["state"]] = states.get(row["state"], 0) + 1
        key = row["features"]["state"]
        features[key] = features.get(key, 0) + 1
    return {"states": dict(sorted(states.items())),
            "features": dict(sorted(features.items()))}
