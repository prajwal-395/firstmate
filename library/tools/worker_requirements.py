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


def failing_needs(checks: list) -> set:
    """The need ids doctor's checks report as failing."""
    return {check.need for check in checks if not check.ok and check.need}


def match_requirements(manifest: dict, requirements: TaskRequirements,
                       checks: list | None = None) -> list:
    """One refusal per unmet requirement, each named; empty when the worker
    satisfies them all.

    `checks` are doctor's own checks, from which the failing needs are read;
    without them only the capabilities, plugins and models sections are
    matched (a caller that has not run the checks cannot name a failing
    need, and does not guess).
    """
    from library.tools import machine_needs

    refusals: list = []
    capabilities = manifest.get("capabilities", {})
    for capability in requirements.capabilities:
        verdict = capabilities.get(capability)
        if verdict is None:
            refusals.append(
                f"capability {capability}: not a capability this worker "
                f"advertises (known: {', '.join(sorted(capabilities))})")
        elif verdict == machine_needs.UNAVAILABLE:
            _missing = machine_needs.needs_of(capability).requires
            refusals.append(
                f"capability {capability}: unavailable on this worker "
                f"(missing {', '.join(_missing)})")

    if checks is not None:
        failing = failing_needs(checks)
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
    """Whether the worker satisfies every requirement - the scheduler's gate."""
    return not match_requirements(manifest, requirements, checks)
