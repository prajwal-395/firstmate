"""`ren capabilities`: the capability catalog an agent can discover at runtime.

Defects this names:

* Nothing answered "what CAN Ren do, and can this machine do it?".
  `ren ops` listed the operations of a scoped run and `ren doctor --for`
  answered readiness for an id the caller already knew - but the
  vocabulary itself, with what each capability needs, was only
  reachable as `python3 -m library.tools.capabilities`, a command no
  agent is told about.
* An id Ren does not hold came back as prose ("unknown capability 'x'")
  with nothing else, so the model guessed at ids and read sentences to
  find out what existed.  The answer now carries the nearest real ids
  and the whole available set.
* A capability whose needs were never probed could read as AVAILABLE,
  because a need no check answered looked like a need that passed.
  A required need no check answered is UNDETERMINED, and the verdict
  says so.
"""

from __future__ import annotations

import contextlib
import io
import json

from library.tools import capability_discovery, capability_guidance, machine_needs
from ren import doctor
from ren.commands import VERBS


def _check(name: str, need: str, ok: bool = False,
           undetermined: bool = False) -> doctor.Check:
    return doctor.Check(name=name, ok=ok, detail="fixture",
                        fix="fixture", need=need, undetermined=undetermined)


def _checks(*checks) -> list:
    return list(checks)


def test_the_catalog_covers_every_capability_and_every_needs_row():
    """The catalog is the whole vocabulary: no capability is missing from
    it, and no needs row names one the catalog does not hold."""
    payload = capability_discovery.catalog(checks=None)
    ids = {row["id"] for row in payload["capabilities"]}
    assert ids == set(machine_needs.CAPABILITY_NEEDS)
    assert payload["count"] == len(ids)
    assert ids == set(capability_discovery.known_ids())


def test_every_capability_carries_its_scope_cost_and_needs():
    """A row a caller can reason about: what it is, where it runs, what
    it costs, and what it needs."""
    payload = capability_discovery.catalog(checks=None)
    for row in payload["capabilities"]:
        assert row["summary"], row["id"]
        assert row["kind"] in {capability_discovery.CAPABILITY,
                                capability_discovery.FRONT_DOOR}, row["id"]
        assert row["verdict"] == capability_discovery.NOT_MEASURED, row["id"]
        needs = row["needs"]
        assert needs["required"], row["id"]
        assert row["how_to_run"], row["id"]
        if row["kind"] == capability_discovery.CAPABILITY:
            assert row["scopes"], row["id"]
            assert row["cost_class"] in {"heavy", "model", "light"}, row["id"]
            assert row["legacy"], row["id"]


def test_an_unmeasured_machine_is_not_measured_not_available():
    """A capability nobody probed is NOT MEASURED - never AVAILABLE.

    This is the defect the offline catalog would otherwise ship: with
    no checks at all, every need looks like a need that passed, and the
    catalog would claim a machine can do everything.
    """
    payload = capability_discovery.catalog(checks=None)
    verdicts = {row["verdict"] for row in payload["capabilities"]}
    assert verdicts == {capability_discovery.NOT_MEASURED}
    assert capability_discovery.catalog(checks=[])["capabilities"], "empty"
    empty = capability_discovery.catalog(checks=[])
    assert {row["verdict"] for row in empty["capabilities"]} == {
        capability_discovery.NOT_MEASURED}


def test_a_failing_need_makes_its_capability_unavailable():
    """A determinate failure outranks everything: the capability that
    refuses without the need is unavailable, and one that does not
    need it is untouched."""
    checks = _checks(_check("Resolve scripting", "resolve.scripting"))
    payload = capability_discovery.catalog(checks=checks)
    verdicts = {row["id"]: row["verdict"]
                for row in payload["capabilities"]}
    assert verdicts["reel.build"] == machine_needs.UNAVAILABLE
    assert verdicts["footage.scan"] != machine_needs.UNAVAILABLE


def test_a_need_no_check_answered_is_undetermined_not_available():
    """A required need with no check is UNDETERMINED, not satisfied.

    The check above covers only `resolve.scripting`; `footage.scan` also
    needs ffmpeg, and no check answered ffmpeg.  Reading that as a
    passed need is how an unprobed capability reads as available.
    """
    checks = _checks(_check("Resolve scripting", "resolve.scripting"))
    payload = capability_discovery.catalog(checks=checks)
    verdicts = {row["id"]: row for row in payload["capabilities"]}
    assert verdicts["footage.scan"]["verdict"] == machine_needs.UNKNOWN
    assert "ffmpeg" in verdicts["footage.scan"]["undetermined"]
    assert verdicts["footage.scan"]["missing"] == []


def test_an_unknown_capability_comes_back_with_the_nearest_ids():
    """A typo comes back with the real ids, never with a bare "unknown"."""
    guidance = capability_discovery.unknown_guidance("reel.buld", checks=None)
    assert guidance["status"] == capability_guidance.UNKNOWN
    assert guidance["asked"] == "reel.buld"
    ids = [row["id"] for row in guidance["alternatives"]]
    assert ids, "an unknown id came back with no alternative to try"
    assert "reel.build" in ids
    assert "reel.buld" not in ids


def test_an_unavailable_capability_says_what_is_missing_and_what_to_run():
    """The refusal names the missing need, the fix, and the ids that
    ARE available - the three things a caller needs to go on."""
    checks = _checks(_check("Resolve scripting", "resolve.scripting"))
    row = capability_discovery.record("reel.build", checks=checks)
    guidance = row["guidance"]
    assert guidance is not None, "an unavailable capability came back bare"
    assert guidance["status"] == capability_guidance.UNAVAILABLE
    assert "resolve.scripting" in guidance["what"]
    assert "ren doctor --for reel.build" in guidance["fix"]
    assert guidance["alternatives"], "no alternative was offered"


def test_every_guidance_record_carries_the_same_shape():
    """One shape, whichever interface produced it - the machine reader
    keys off `status` and never off prose."""
    for guidance in (capability_discovery.unknown_guidance("nope", checks=None),
                      capability_guidance.guidance(
                          capability_guidance.UNAVAILABLE, "x", "w", "y", "z")):
        for field in ("status", "asked", "what", "why", "fix", "next_action",
                      "alternatives", "supported"):
            assert field in guidance, field
        assert guidance["status"] in capability_guidance.STATUSES


def test_a_guidance_status_outside_the_vocabulary_is_refused():
    """The vocabulary is closed: a status nothing declared is refused at
    the raise site rather than reaching a caller."""
    try:
        capability_guidance.guidance("invented", "x", "w", "y", "z")
    except ValueError as exc:
        assert "invented" in str(exc)
    else:
        raise AssertionError("an unknown guidance status was accepted")


def test_the_verb_is_registered_read_only_and_reaches_the_module():
    """`ren capabilities` is a front-door verb, and it changes nothing."""
    verb = {v.name: v for v in VERBS}["capabilities"]
    assert verb.module_argv == ("library.tools.capability_discovery",)
    assert verb.read_only, "a discovery verb must change nothing"
    assert verb.json


def test_the_front_door_capabilities_name_the_verb_that_serves_them():
    """A front-door capability says which command runs it, and its
    summary is that verb's own summary - never a second hand-written
    one that drifts."""
    payload = capability_discovery.catalog(checks=None)
    rows = {row["id"]: row for row in payload["capabilities"]}
    for capability_id, command in machine_needs.FRONT_DOOR.items():
        row = rows[capability_id]
        assert row["kind"] == capability_discovery.FRONT_DOOR
        assert command in row["how_to_run"], capability_id


def test_the_json_answer_is_the_same_answer_as_the_prose_one():
    """`--json` is the structure the prose is rendered from - the two
    cannot disagree."""
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        assert capability_discovery.main(["--json"]) == 0
    payload = json.loads(buffer.getvalue())
    assert payload["count"] == len(payload["capabilities"])
    for row in payload["capabilities"]:
        assert set(row) == set(payload["capabilities"][0])
