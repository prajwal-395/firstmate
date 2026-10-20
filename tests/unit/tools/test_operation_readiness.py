"""AGENT-013: the routine readiness check runs itself.

`ren doctor` is the readiness report and `ren doctor --for <capability>`
answers it for one capability, but the agent had to REMEMBER to ask.
The two expensive write paths automate it - `ren build` gates on
`check_build()` before it resolves the project, takes the lease or
connects to Resolve - and the two write paths that write the LIVE
timeline did not.

So a worker that cannot reach Resolve discovered it half way through a
change, which is the most expensive place to discover it.

These tests name the defects:

- an EDIT declares the needs an edit needs, and no more - a
  capability-level declaration that ropes in needs which do not stop a
  real edit refuses real production work (the `python.graphics` history);
- the readiness gate refuses by NAME, and an `unknown:` entry still
  reports and never gates - a requirement the checks could not determine
  is not a proven absence;
- the gate is on the WRITE edge, so a plan that changes nothing is
  still answerable on a machine that cannot write.
"""
from __future__ import annotations

from library.tools import machine_needs
from library.tools.worker_requirements import (
    UNKNOWN_PREFIX,
    edit_requirements,
    is_refusal,
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


def test_an_edit_declares_the_needs_an_edit_needs():
    # Defect: an edit that writes the live timeline needs the same
    # Resolve connection a build needs, and no more.
    assert edit_requirements().needs == ("resolve.scripting", "resolve.studio")
    assert edit_requirements().capabilities == ()


def test_a_worker_that_cannot_reach_resolve_is_refused_by_name():
    # The needs are matched against the CHECKS, so the refusal names
    # the need the checks actually fail - never a need the worker has.
    manifest = _manifest()
    checks = [_check("resolve scripting", False, "resolve.scripting"),
              _check("resolve studio", True, "resolve.studio")]
    from library.tools.worker_requirements import match_requirements

    refusals = match_requirements(manifest, edit_requirements(),
                                  checks=checks)
    assert refusals
    assert any("resolve.scripting" in entry for entry in refusals)
    assert not any("resolve.studio" in entry for entry in refusals)
    assert all(is_refusal(entry) for entry in refusals)


def test_a_worker_that_can_reach_resolve_is_not_refused():
    # The defect this guards is the mirror of the one above: a
    # capability-level declaration that ropes in needs a capable worker
    # does not have refuses real production work.
    manifest = _manifest()
    checks = [_check("resolve scripting", True, "resolve.scripting"),
              _check("resolve studio", True, "resolve.studio")]
    from library.tools.worker_requirements import match_requirements

    refusals = match_requirements(manifest, edit_requirements(),
                                  checks=checks)
    assert refusals == []


def test_the_check_is_scoped_to_the_needs_it_gates_on():
    # The live probe is scoped, so the check costs one Resolve probe
    # group and examines nothing it does not gate on.
    manifest = _manifest()
    checks = [_check("resolve scripting", True, "resolve.scripting"),
              _check("resolve studio", True, "resolve.studio"),
              _check("a need nobody asked about", False, "ffmpeg")]
    from library.tools.worker_requirements import match_requirements

    refusals = match_requirements(manifest, edit_requirements(),
                                  checks=checks)
    assert refusals == []


def test_an_undetermined_check_never_gates():
    # Defect: the busy-Resolve case read as a missing need and refused
    # every real delivery (PR 1871 review). An undetermined check is
    # not a measured absence, so it refuses nothing at all.
    manifest = _manifest()
    checks = [_undetermined("resolve scripting", "resolve.scripting")]
    from library.tools.worker_requirements import match_requirements

    refusals = match_requirements(manifest, edit_requirements(),
                                  checks=checks)
    assert refusals == []
    assert not any(is_refusal(entry) for entry in refusals)


def test_an_undetermined_capability_reports_and_never_gates():
    # The `unknown:` entry is how an undeterminable CAPABILITY reports
    # itself without blocking.
    from library.tools.worker_requirements import TaskRequirements, match_requirements

    manifest = _manifest(capabilities={"render.build": machine_needs.UNKNOWN})
    refusals = match_requirements(
        manifest, TaskRequirements(capabilities=("render.build",)),
        checks=[])
    assert refusals
    assert all(entry.startswith(UNKNOWN_PREFIX) for entry in refusals)
    assert not any(is_refusal(entry) for entry in refusals)


def test_the_gate_is_on_the_write_edge_not_the_start_of_a_revision():
    # Defect: a gate at the start of a revision refuses a `--plan` that
    # changes nothing, on a machine that cannot write.
    from pathlib import Path

    from library.tools import revise

    text = Path(revise.__file__).read_text(encoding="utf-8")
    gate = text.index("blocking = [entry for entry in check_edit()")
    write = text.index("receipt = resolve_axi_core.submit_edit_request(")
    assert gate < write, "the readiness gate must run before the write"
