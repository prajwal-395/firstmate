"""Behaviour the requirements matcher pins, each test naming a defect.

The matcher is the scheduler's gate: it must refuse a task to a worker
that cannot perform it, naming the requirement. The defect class is a
requirement that is met being refused (blocking a capable worker) or a
requirement that is unmet being passed (placing a task on a worker that
cannot do it).
"""
from library.tools import machine_needs
from library.tools.worker_requirements import (
    TaskRequirements, can_perform, match_requirements,
)
from ren import doctor


def _check(name, ok, need):
    return doctor.Check(name, ok, "stub detail", need=need)


def _manifest(capabilities=None, plugins=None, models=None):
    return {
        "capabilities": capabilities or {},
        "resolve": {"plugins": plugins or []},
        "models": models or {},
    }


def test_a_task_requiring_an_unavailable_capability_is_refused_by_name():
    """render.build is unavailable without resolve.studio; a task that
    needs it must be refused naming the capability and the missing need."""
    manifest = _manifest(capabilities={
        "render.build": machine_needs.UNAVAILABLE,
        "footage.search": machine_needs.AVAILABLE,
    })
    task = TaskRequirements(capabilities=("render.build",))
    refusals = match_requirements(manifest, task)
    assert len(refusals) == 1
    assert "render.build" in refusals[0]
    assert "resolve.studio" in refusals[0]


def test_a_task_requiring_a_degraded_capability_is_not_refused():
    """footage.search degrades without python.analysis but still runs; the
    matcher must not refuse a task the worker can perform, only slower."""
    manifest = _manifest(capabilities={
        "footage.search": machine_needs.DEGRADED,
    })
    task = TaskRequirements(capabilities=("footage.search",))
    assert match_requirements(manifest, task) == []


def test_a_task_requiring_a_failing_need_is_refused_by_name():
    """A need the worker's checks report as failing is refused, naming the
    need and what it is - the scheduler places the task elsewhere."""
    manifest = _manifest()
    checks = [_check("resolve.studio", False, "resolve.studio")]
    task = TaskRequirements(needs=("resolve.studio",))
    refusals = match_requirements(manifest, task, checks=checks)
    assert len(refusals) == 1
    assert "resolve.studio" in refusals[0]


def test_a_task_requiring_a_met_need_is_not_refused():
    """A need the worker satisfies is not refused - the matcher must not
    block a capable worker."""
    manifest = _manifest()
    checks = [_check("resolve.studio", True, "resolve.studio")]
    task = TaskRequirements(needs=("resolve.studio",))
    assert match_requirements(manifest, task, checks=checks) == []


def test_a_task_requiring_an_uninstalled_plugin_is_refused_by_name():
    """Magic Mask is a Resolve effect; a task that needs it is refused when
    the worker's Resolve has not got it installed."""
    manifest = _manifest(plugins=[{"id": "film_convert", "name": "FilmConvert"}])
    task = TaskRequirements(plugins=("Magic Mask",))
    refusals = match_requirements(manifest, task)
    assert len(refusals) == 1
    assert "Magic Mask" in refusals[0]


def test_a_task_requiring_an_installed_plugin_is_not_refused():
    manifest = _manifest(plugins=[{"id": "magic_mask", "name": "Magic Mask"}])
    task = TaskRequirements(plugins=("Magic Mask",))
    assert match_requirements(manifest, task) == []


def test_a_task_requiring_an_uncached_model_is_refused_by_name():
    """A model the worker has not cached complete is refused, naming the
    model - the scheduler does not place a vision task on a worker that
    cannot hold the model."""
    manifest = _manifest(models={
        "gemma-4-12b-it-4bit": {"complete": False, "bytes": 0},
    })
    task = TaskRequirements(models=("gemma-4-12b-it-4bit",))
    refusals = match_requirements(manifest, task)
    assert len(refusals) == 1
    assert "gemma-4-12b-it-4bit" in refusals[0]


def test_a_task_requiring_an_unknown_capability_is_refused_by_name():
    """A capability the worker does not advertise at all is refused as
    unknown, not silently passed - a typo in a task must not place it on
    the wrong worker."""
    manifest = _manifest(capabilities={
        "render.build": machine_needs.AVAILABLE,
    })
    task = TaskRequirements(capabilities=("render.buildd",))
    refusals = match_requirements(manifest, task)
    assert len(refusals) == 1
    assert "render.buildd" in refusals[0]


def test_can_perform_is_false_when_any_requirement_is_unmet():
    """The scheduler's gate: one unmet requirement makes the whole task
    unplaceable on this worker."""
    manifest = _manifest(
        capabilities={"render.build": machine_needs.AVAILABLE},
        plugins=[{"id": "magic_mask", "name": "Magic Mask"}],
        models={"gemma-4-12b-it-4bit": {"complete": True, "bytes": 100}},
    )
    checks = [_check("resolve.studio", True, "resolve.studio")]
    placeable = TaskRequirements(
        capabilities=("render.build",), plugins=("Magic Mask",),
        models=("gemma-4-12b-it-4bit",), needs=("resolve.studio",))
    assert can_perform(manifest, placeable, checks=checks) is True

    unplaceable = TaskRequirements(
        capabilities=("render.build",), plugins=("Magic Bullet",))
    assert can_perform(manifest, unplaceable, checks=checks) is False


def test_without_checks_a_need_requirement_is_not_matched():
    """A caller that has not run the checks cannot name a failing need, so
    the matcher does not guess - it matches only what the manifest carries."""
    manifest = _manifest()
    task = TaskRequirements(needs=("resolve.studio",))
    assert match_requirements(manifest, task) == []
