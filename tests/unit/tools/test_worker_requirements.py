"""Behaviour the requirements matcher pins, each test naming a defect.

The matcher is the scheduler's gate: it must refuse a task to a worker
that cannot perform it, naming the requirement. The defect class is a
requirement that is met being refused (blocking a capable worker) or a
requirement that is unmet being passed (placing a task on a worker that
cannot do it).
"""
from library.tools import machine_needs
from library.tools.worker_requirements import (
    TaskRequirements, can_perform, is_refusal, match_requirements,
)
from ren import doctor


def _check(name, ok, need):
    return doctor.Check(name, ok, "stub detail", need=need)


def _undetermined(name, need):
    return doctor.Check(name, False, "the check could not determine the answer",
                        need=need, undetermined=True)


def _manifest(capabilities=None, plugins=None, models=None):
    return {
        "capabilities": capabilities or {},
        "resolve": {"plugins": plugins or []},
        "models": models or {},
    }


def test_a_task_requiring_an_unavailable_capability_is_refused_by_name():
    """render.build is unavailable without resolve.studio; a task that
    needs it must be refused naming the capability and the FAILING need -
    never a need the worker has. The defect: the refusal named every
    require of the capability, so a worker with macOS read as missing
    macOS (PR 1871 review)."""
    manifest = _manifest(capabilities={
        "render.build": machine_needs.UNAVAILABLE,
        "footage.search": machine_needs.AVAILABLE,
    })
    checks = [_check("Resolve edition", False, "resolve.studio"),
              _check("Resolve scripting", True, "resolve.scripting")]
    task = TaskRequirements(capabilities=("render.build",))
    refusals = match_requirements(manifest, task, checks=checks)
    assert len(refusals) == 1
    assert "render.build" in refusals[0]
    assert "resolve.studio" in refusals[0]
    assert "resolve.scripting" not in refusals[0]


def test_without_checks_an_unavailable_capability_is_refused_without_naming_needs():
    """A caller that has not run the checks cannot name a failing need, so
    the matcher does not guess - the same line the needs matching draws.
    The capability verdict is in the manifest; the need-level naming is
    not, and inventing it is the defect."""
    manifest = _manifest(capabilities={
        "render.build": machine_needs.UNAVAILABLE,
    })
    refusals = match_requirements(manifest, TaskRequirements(capabilities=("render.build",)))
    assert len(refusals) == 1
    assert "render.build" in refusals[0]
    assert "resolve.studio" not in refusals[0]
    assert "macos" not in refusals[0]


def test_a_need_the_checks_could_not_determine_is_said_unknown_not_refused():
    """The gate's third answer: when the worker's checks could not
    determine a required need (the Resolve probe was refused, the edition
    unreadable), the gate says unknown naming the need - it does not
    refuse a worker that may have it. The defect: an undeterminable
    answer was read as a failing need, so a busy Resolve refused every
    real delivery."""
    manifest = _manifest(capabilities={
        "render.build": machine_needs.UNKNOWN,
    })
    checks = [_undetermined("Resolve scripting", "resolve.scripting")]
    entries = match_requirements(manifest, TaskRequirements(capabilities=("render.build",)),
                                checks=checks)
    assert len(entries) == 1
    assert entries[0].startswith("unknown:")
    assert "render.build" in entries[0]
    assert "resolve.scripting" in entries[0]
    assert "unavailable" not in entries[0]
    assert is_refusal(entries[0]) is False


def test_can_perform_does_not_refuse_what_it_could_not_determine():
    """The scheduler's gate: a requirement no verdict could be reached
    for is not proven unmet, so the task stays placeable - an
    undeterminable answer reports and never gates."""
    manifest = _manifest(capabilities={
        "render.build": machine_needs.UNKNOWN,
    })
    checks = [_undetermined("Resolve scripting", "resolve.scripting")]
    placeable = TaskRequirements(capabilities=("render.build",))
    assert can_perform(manifest, placeable, checks=checks) is True


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


def _render_needs_all_ok():
    """One passing check per need `render.build` requires (baseline plus
    Resolve scripting, the Studio edition and ffmpeg)."""
    from library.tools.machine_needs import needs_of
    return [doctor.Check(f"stub {need}", True, "stub ok", need=need)
            for need in needs_of("render.build").requires]


def test_check_render_against_the_live_manifest_passes_a_capable_worker(monkeypatch):
    """The live path (`check_render()` with no args) assembles the real
    manifest shape and matches against the very checks that produced it -
    a worker meeting every render need is not refused. The defect: the
    merged gate matched the manifest without its checks and named every
    require as missing, so the captain's capable Mac read as missing
    macOS, python.venv, python.core and ffmpeg (PR 1871 review)."""
    from library.tools import worker_manifest, worker_requirements

    monkeypatch.setattr(doctor, "run_checks",
                        lambda *args, **kwargs: _render_needs_all_ok())
    monkeypatch.setattr(worker_manifest, "_broker_running", lambda: False)
    assert worker_requirements.check_render() == []


def test_check_render_against_the_live_manifest_says_unknown_when_resolve_is_busy(monkeypatch):
    """The live path's third answer: when the Resolve probe cannot
    determine scripting/edition (the broker lockout that made every real
    delivery refuse), the gate reports unknown naming the needs and
    refuses nothing - an undeterminable answer is not a measured
    absence."""
    from library.tools import worker_manifest, worker_requirements
    from library.tools.worker_requirements import is_refusal

    checks = _render_needs_all_ok()
    busy = {check.need for check in checks
            if check.need in ("resolve.scripting", "resolve.studio")}
    live = [doctor.Check(check.name, False,
                         "DaVinci Resolve is busy: stubbed lockout",
                         need=check.need, undetermined=True)
            if check.need in busy else check
            for check in checks]
    monkeypatch.setattr(doctor, "run_checks",
                        lambda *args, **kwargs: live)
    monkeypatch.setattr(worker_manifest, "_broker_running", lambda: False)
    entries = worker_requirements.check_render()
    assert entries, "the busy probe must still be said, as unknown"
    assert not any(is_refusal(entry) for entry in entries)
    assert any(entry.startswith("unknown:") and "render.build" in entry
               for entry in entries)
