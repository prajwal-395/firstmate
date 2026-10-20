"""A task's declared requirements, and the matcher that names what a worker fails.

The Ren Fabric scheduler refuses a task to a worker that cannot perform it.
A task declares what it needs (`TaskRequirements`); the matcher checks that
declaration against a worker's manifest (`worker_manifest.produce_manifest`)
and returns one refusal per unmet requirement, each NAMED - the scheduler
places the task elsewhere or refuses it with the reason, never guessing.

Four kinds of requirement, each checked against the manifest section that
carries it:

* **capabilities** - `machine_needs` capability ids; a capability the manifest
  reports `unavailable` is failed, `degraded` is not (the worker can still
  do the task, slower or reduced).
* **needs** - `machine_needs` need ids; a need the worker's own checks report
  as failing is failed.
* **plugins** - Resolve effect names; a plugin the worker's Resolve has not
  got installed is failed.
* **models** - model labels; a model the worker has not cached complete is
  failed.

A capability whose required needs the checks could not DETERMINATE is
UNKNOWN: the gate says so, naming them, and does not refuse - an
undeterminable answer is not a measured absence.

    from library.tools.worker_manifest import produce_manifest
    from library.tools.worker_requirements import TaskRequirements, match_requirements

    manifest = produce_manifest()
    task = TaskRequirements(capabilities=("render.build",),
                            plugins=("Magic Mask",))
    refusals = match_requirements(manifest, task)
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class TaskRequirements:
    """What a task declares it needs from a worker.

    Every field is a `machine_needs` id (capabilities, needs), a Resolve
    effect name (plugins) or a model label (models). An empty field is not
    a requirement. The ids are the vocabularies already declared in
    `library/tools/machine_needs.py` and the manifest sections, so a task
    and a worker speak the same language.
    """
    capabilities: tuple = ()
    needs: tuple = ()
    plugins: tuple = ()
    models: tuple = ()


UNKNOWN_PREFIX = "unknown:"
"""An entry that says what the worker's checks could not determine. It
reports and never gates - the same line `library/tools/undetermined.py`
draws between an admitted absence and a measured one."""


def is_refusal(entry: str) -> bool:
    """Whether an entry from `match_requirements` blocks the task.

    An `unknown:` entry is not a refusal: a requirement the checks could
    not determine is not proven unmet, and refusing on it is what blocked
    every real delivery when the Resolve probe was merely busy (PR 1871
    review).
    """
    return not entry.startswith(UNKNOWN_PREFIX)


def failing_needs(checks: list) -> set:
    """The need ids doctor's checks report as failing - ran and said no.

    An undetermined check is not here: a check that could not determine
    its answer is not a measured absence.
    """
    return {check.need for check in checks
            if not check.ok and not check.undetermined and check.need}


def undetermined_needs(checks: list) -> set:
    """The need ids doctor's checks could not determine."""
    return {check.need for check in checks if check.undetermined and check.need}


def match_requirements(manifest: dict, requirements: TaskRequirements,
                       checks: list | None = None) -> list:
    """One refusal per unmet requirement, each named; empty when the worker
    satisfies them all. An `unknown:` entry says what the checks could not
    determine - it is not a refusal.

    `checks` are doctor's own checks, from which the failing and
    undetermined needs are read; without them only the capabilities,
    plugins and models sections are matched (a caller that has not run
    the checks cannot name a failing need, and does not guess).
    """
    from library.tools import machine_needs

    refusals: list = []
    capabilities = manifest.get("capabilities", {})
    failing = failing_needs(checks) if checks is not None else set()
    undetermined = undetermined_needs(checks) if checks is not None else set()
    for capability in requirements.capabilities:
        verdict = capabilities.get(capability)
        if verdict is None:
            refusals.append(
                f"capability {capability}: not a capability this worker "
                f"advertises (known: {', '.join(sorted(capabilities))})")
        elif verdict == machine_needs.UNAVAILABLE:
            _missing = [need for need in machine_needs.needs_of(capability).requires
                        if need in failing]
            message = f"capability {capability}: unavailable on this worker"
            if _missing:
                message += f" (missing {', '.join(_missing)})"
            refusals.append(message)
        elif verdict == machine_needs.UNKNOWN:
            _could_not = [need for need in machine_needs.needs_of(capability).requires
                          if need in undetermined]
            refusals.append(
                f"{UNKNOWN_PREFIX} capability {capability} - could not determine "
                f"{', '.join(_could_not) or 'its needs'} on this worker")

    if checks is not None:
        for need in requirements.needs:
            if need in failing:
                refusals.append(
                    f"need {need}: not met on this worker "
                    f"({machine_needs.NEEDS[need].summary})")

    installed = {p["name"] for p in manifest.get("resolve", {}).get("plugins", [])}
    for plugin in requirements.plugins:
        if plugin not in installed:
            refusals.append(
                f"plugin {plugin}: not installed on this worker's Resolve")

    models = manifest.get("models", {})
    for model in requirements.models:
        state = models.get(model)
        if state is None:
            refusals.append(
                f"model {model}: not a model this worker advertises "
                f"(known: {', '.join(sorted(models))})")
        elif not state.get("complete"):
            refusals.append(
                f"model {model}: not cached complete on this worker")

    return refusals


def can_perform(manifest: dict, requirements: TaskRequirements,
                checks: list | None = None) -> bool:
    """Whether the worker satisfies every requirement - the scheduler's gate.

    An `unknown:` entry is not a refusal: a requirement no verdict could
    be reached for is not proven unmet.
    """
    return not any(is_refusal(entry)
                   for entry in match_requirements(manifest, requirements, checks))


def render_requirements() -> TaskRequirements:
    """What a render task needs from a worker.

    The render is the expensive thing the captain asks for, so it is the
    path that checks its requirements against the local manifest before
    starting and refuses by name. `render.build` carries Resolve
    scripting, the Studio edition and ffmpeg.
    """
    return TaskRequirements(capabilities=("render.build",))


def check_render(produce=None) -> list:
    """The requirements this worker fails for a render, each named; an
    `unknown:` entry says what the checks could not determine and does
    not refuse.

    The render path calls this before starting; a non-empty list of
    REFUSALS refuses the render by name. `produce` is injectable so a
    test can pin the check against a fixture manifest; the default is the
    live manifest, matched against the very checks that produced it - a
    refusal names the needs those checks actually fail, never a need the
    worker has.
    """
    if produce is None:
        from library.tools.worker_manifest import produce_manifest
        from ren import doctor
        checks = doctor.run_checks()
        manifest = produce_manifest(checks=checks)
        return match_requirements(manifest, render_requirements(), checks=checks)
    manifest = produce()
    return match_requirements(manifest, render_requirements())


def build_requirements() -> TaskRequirements:
    """What a reel-build task needs from a worker.

    The build is the other expensive thing the captain asks for - a
    reel's Resolve pass is 19.4-67.1s, so it is the path that checks
    its requirements against the local manifest before starting and
    refuses by name. It declares ONLY the needs a build provably
    cannot run without - a Resolve scripting connection into the
    Studio edition, which every placement needs. Everything else the
    `reel.build` capability carries (the graphics group, models,
    plugins) is refused just-in-time where it is actually consumed -
    `reel_look` refuses a punch-in the worker cannot draw, `deliver`
    gates the render - because a capability-level declaration ropes
    in needs that do not stop a real build: the test machine and the
    captain's own Mac both report `python.graphics` unmet (cv2
    without its Haar cascade, opencv headless) while building reels
    fine, so refusing on it would refuse real production work.
    """
    return TaskRequirements(needs=("resolve.scripting", "resolve.studio"))


def edit_requirements() -> TaskRequirements:
    """What an EDIT needs from a worker.

    The same two needs a build needs, and for the same reason: an edit
    writes the live timeline through a Resolve scripting connection into
    the Studio edition. Nothing more, for the same reason the build
    declares nothing more - a capability-level declaration that ropes
    in needs which do not stop a real edit refuses real production work.
    """
    return TaskRequirements(needs=("resolve.scripting", "resolve.studio"))


def check_edit(produce=None) -> list:
    """The requirements this worker fails for an edit, each named; an
    `unknown:` entry says what the checks could not determine and does
    not refuse.

    The edit paths call this before they write; a non-empty list of
    REFUSALS refuses the edit by name. `produce` is injectable so a
    test can pin the check against a fixture manifest; the default is
    the live manifest, matched against the very checks that produced
    it - a refusal names the needs those checks actually fail, never a
    need the worker has. The live probe is scoped to the two declared
    needs, so the check costs one Resolve probe group and examines
    nothing it does not gate on.
    """
    if produce is None:
        from library.tools.worker_manifest import produce_manifest
        from ren import doctor
        needs = edit_requirements().needs
        checks = doctor.run_checks(needs=needs)
        manifest = produce_manifest(checks=checks)
        return match_requirements(manifest, edit_requirements(), checks=checks)
    manifest = produce()
    return match_requirements(manifest, edit_requirements())


def check_build(produce=None) -> list:
    """The requirements this worker fails for a reel build, each named;
    an `unknown:` entry says what the checks could not determine and
    does not refuse.

    The build path calls this before starting; a non-empty list of
    REFUSALS refuses the build by name. `produce` is injectable so a
    test can pin the check against a fixture manifest; the default is
    the live manifest, matched against the very checks that produced
    it - a refusal names the needs those checks actually fail, never
    a need the worker has. The live probe is scoped to the two
    declared needs, so the check costs one Resolve probe group and
    examines nothing it does not gate on.
    """
    if produce is None:
        from library.tools.worker_manifest import produce_manifest
        from ren import doctor
        needs = build_requirements().needs
        checks = doctor.run_checks(needs=needs)
        manifest = produce_manifest(checks=checks)
        return match_requirements(manifest, build_requirements(), checks=checks)
    manifest = produce()
    return match_requirements(manifest, build_requirements())
