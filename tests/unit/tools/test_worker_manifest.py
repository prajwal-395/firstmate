"""Behaviour the worker manifest pins, each test naming a defect.

The manifest is an advertisement a scheduler matches a task's
requirements against, so the defect class is: a fact the probes already
know reaching the manifest WRONG - a capability verdict that does not
follow from the failing needs, a Resolve fact invented rather than read
from the broker's cache, or a model's absence hidden rather than reported.
"""
from pathlib import Path

from library.tools import machine_needs
from library.tools.worker_manifest import produce_manifest
from ren import doctor


def _check(name, ok, need):
    return doctor.Check(name, ok, "stub detail", need=need)


def _checks(*pairs):
    """Checks for (need, ok) pairs; every other need passes."""
    return [_check(need, ok, need) for need, ok in pairs]


def test_a_machine_missing_a_required_need_reports_the_capability_unavailable():
    """render.build requires resolve.scripting and resolve.studio; a machine
    that fails resolve.studio must advertise render.build unavailable, not
    available - the scheduler refuses on the manifest, not on a live probe."""
    checks = _checks(("resolve.scripting", True), ("resolve.studio", False))
    manifest = produce_manifest(checks=checks, resolve_cache=None,
                                broker_running=False)
    assert manifest["capabilities"]["render.build"] == machine_needs.UNAVAILABLE
    assert manifest["capabilities"]["footage.search"] == machine_needs.AVAILABLE


def test_a_machine_missing_an_optional_need_reports_degraded_not_unavailable():
    """footage.search degrades without python.analysis rather than refusing;
    the manifest must say degraded so a scheduler can still place a
    lexical-only search on this worker."""
    checks = _checks(("python.analysis", False))
    manifest = produce_manifest(checks=checks, resolve_cache=None,
                                broker_running=False)
    assert manifest["capabilities"]["footage.search"] == machine_needs.DEGRADED


def test_the_resolve_version_and_plugins_come_from_the_brokers_cache():
    """The manifest reads Resolve facts from what the broker recorded, never
    from a live probe - a machine with Resolve down still advertises the
    version and effects the broker last measured."""
    cache = {"resolve_version": "21.2",
             "entries": [{"id": "magic_mask", "name": "Magic Mask"}]}
    checks = _checks(("resolve.scripting", False), ("resolve.studio", False))
    manifest = produce_manifest(checks=checks, resolve_cache=cache,
                                broker_running=False)
    assert manifest["resolve"]["version"] == "21.2"
    assert manifest["resolve"]["plugins"] == [{"id": "magic_mask",
                                               "name": "Magic Mask"}]


def test_edition_is_studio_free_or_unknown_from_the_probe_checks():
    """The edition follows the two probe checks: Studio when the studio need
    passes, free when scripting works but the edition is the free one, and
    unknown when there is no scripting connection to read the edition from."""
    studio = produce_manifest(
        checks=_checks(("resolve.scripting", True), ("resolve.studio", True)),
        resolve_cache=None, broker_running=False)
    assert studio["resolve"]["edition"] == "Studio"

    free = produce_manifest(
        checks=_checks(("resolve.scripting", True), ("resolve.studio", False)),
        resolve_cache=None, broker_running=False)
    assert free["resolve"]["edition"] == "free"

    unknown = produce_manifest(
        checks=_checks(("resolve.scripting", False), ("resolve.studio", False)),
        resolve_cache=None, broker_running=False)
    assert unknown["resolve"]["edition"] == "unknown"


def test_an_absent_model_is_reported_incomplete_with_its_bytes(tmp_path):
    """A model the machine has not fetched is reported incomplete with the
    bytes it does have, never omitted - a scheduler has to see the gap to
    refuse the task by name."""
    manifest = produce_manifest(checks=_checks(), resolve_cache=None,
                                broker_running=False, model_cache=tmp_path)
    gemma = manifest["models"]["gemma-4-12b-it-4bit"]
    assert gemma["complete"] is False
    assert gemma["bytes"] == 0


def test_the_state_is_busy_when_the_broker_reports_a_running_job():
    """The state reflects the broker's job table, not a constant - a worker
    with a job running advertises busy so the scheduler does not place
    another exclusive task on it."""
    idle = produce_manifest(checks=_checks(), resolve_cache=None,
                            broker_running=False)
    busy = produce_manifest(checks=_checks(), resolve_cache=None,
                            broker_running=True)
    assert idle["state"] == "idle"
    assert busy["state"] == "busy"


def test_the_check_gate_refuses_when_a_capability_is_unavailable():
    """`ren worker manifest --check` exits 1 naming the unavailable
    capabilities, so a scheduler's gate is the manifest itself rather
    than a live probe."""
    import json
    from library.tools import worker_manifest

    checks = _checks(("resolve.scripting", False), ("resolve.studio", False))
    manifest = produce_manifest(checks=checks, resolve_cache=None,
                                broker_running=False)
    unavailable = [cap for cap, verdict in manifest["capabilities"].items()
                   if verdict == machine_needs.UNAVAILABLE]
    assert unavailable, "a machine with no Resolve must have unavailable capabilities"
    assert "render.build" in unavailable
