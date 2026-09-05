"""The hook layer: what it refuses, what it fires, and what stops it.

Every test here drives the REAL public API against a project built under
`tmp_path` - the real declaration loader, the real allow-list read off
`scripts/hooks/`, the real subprocess, and the real review channel.
Nothing is faked, because the thing being asserted is that automation
actually happens and actually stops.

The firing SITES are not here: `operation_completed` and
`operation_refused` need increment 4's registry, and the three that have
a payload today sit in the run summary's "reading is not gating" blocks,
which is a decision rather than an implication (see the module docstring).
So the conditions are dispatched directly, which is exactly what a firing
site will do.
"""

from __future__ import annotations

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from library.dashboard import review_channel  # noqa: E402
from library.tools import hooks  # noqa: E402


# ── Fixtures ────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _fresh_collector():
    """A fresh collector per test, and no inherited depth.

    `PIPELINE_HOOK_DEPTH` is process-wide, so a test that sets it must
    not leak into the next one.
    """
    hooks.reset()
    had = os.environ.pop(hooks.ENV_DEPTH, None)
    yield
    hooks.reset()
    os.environ.pop(hooks.ENV_DEPTH, None)
    if had is not None:
        os.environ[hooks.ENV_DEPTH] = had


@pytest.fixture
def project(tmp_path):
    folder = tmp_path / "hook_project"
    folder.mkdir()
    (folder / "pipeline_data.json").write_text(
        json.dumps({"project_folder": str(folder)}), encoding="utf-8")
    (folder / "project.yaml").write_text(
        "name: Hook Project\nslug: hook-project\n", encoding="utf-8")
    return folder


def _declare(folder, hook_list):
    """Write a project's own hooks.json and return the loaded declaration."""
    (folder / hooks.DECLARATION_FILENAME).write_text(
        json.dumps({"hooks": hook_list}), encoding="utf-8")
    return hooks.load(str(folder))


QA_PAYLOAD = {"metric": "subtitle_gaps", "step": "validate", "value": 3}


def _steer_hook(name="tell_the_captain", **over):
    hook = {
        "name": name,
        "describe": "Why this hook exists, in the data because JSON has "
                    "no comments.",
        "when": "qa_finding_raised",
        "action": {"kind": "steer", "text": "The QA measured something.",
                   "anchor_step": "validate"},
    }
    hook.update(over)
    return hook


def _run_hook(name="record_it", **over):
    hook = {
        "name": name,
        "describe": "Recording the payload before writing a real hook.",
        "when": "qa_finding_raised",
        "action": {"kind": "run", "script": "record_payload.py"},
    }
    hook.update(over)
    return hook


# ── The vocabulary is closed, and refuses at LOAD ───────────────────

def test_an_unknown_condition_is_refused_by_name(project):
    with pytest.raises(hooks.HookError) as exc:
        _declare(project, [_steer_hook(when="operation_finished")])
    assert "operation_finished" in str(exc.value)
    # The known set is listed, the shape run_scope._reject_unknown takes.
    assert "qa_finding_raised" in str(exc.value)


def test_an_unknown_key_is_refused_rather_than_ignored(project):
    """A misspelled key that armed nothing would look exactly like a hook
    with nothing to do."""
    with pytest.raises(hooks.HookError) as exc:
        _declare(project, [_steer_hook(**{"matches": {"metric": "x"}})])
    assert "matches" in str(exc.value)


def test_an_unknown_action_kind_is_refused(project):
    with pytest.raises(hooks.HookError) as exc:
        _declare(project, [_steer_hook(action={"kind": "exec", "text": "x"})])
    assert "exec" in str(exc.value)


def test_every_hook_must_say_why_it_exists(project):
    """`describe` carries the reason JSON cannot carry as a comment, and
    it is what the run header and the fired record print."""
    hook = _steer_hook()
    del hook["describe"]
    with pytest.raises(hooks.HookError) as exc:
        _declare(project, [hook])
    assert "describe" in str(exc.value)


def test_two_hooks_may_not_share_a_name(project):
    with pytest.raises(hooks.HookError) as exc:
        _declare(project, [_steer_hook(), _steer_hook()])
    assert "tell_the_captain" in str(exc.value)


def test_a_malformed_file_is_refused_by_name(project):
    (project / hooks.DECLARATION_FILENAME).write_text("{not json",
                                                      encoding="utf-8")
    with pytest.raises(hooks.HookError) as exc:
        hooks.load(str(project))
    assert "not valid JSON" in str(exc.value)


# ── A run action may only name a script that is already here ────────

def test_a_script_outside_the_allow_list_is_refused(project):
    with pytest.raises(hooks.HookError) as exc:
        _declare(project, [_run_hook(
            action={"kind": "run", "script": "not_a_real_hook.py"})])
    assert "not_a_real_hook.py" in str(exc.value)
    assert "record_payload.py" in str(exc.value), "the allow-list is listed"


@pytest.mark.parametrize("attempt", [
    "../../../bin/sh",
    "record_payload.py; rm -rf /",
    "/bin/sh",
    "record_payload.py && echo pwned",
    "$(whoami).py",
    "scripts/hooks/record_payload.py",
])
def test_a_run_action_can_never_be_a_shell_string(project, attempt):
    """A name that needs sanitising is not a name.

    The MESSAGE is asserted, not just the refusal. The allow-list would
    reject every one of these too - they are all "not a file in
    scripts/hooks/" - so asserting only that it raised would pass with
    this check deleted, which is how a redundant guard stops being
    observed at all (AGENTS.md 10.4). What this check earns is telling
    the operator the right thing: that a script is NAMED, and that a
    declaration never carries a path, an argument or a shell string.
    """
    with pytest.raises(hooks.HookError) as exc:
        _declare(project, [_run_hook(
            action={"kind": "run", "script": attempt})])
    message = str(exc.value)
    assert attempt in message
    assert "is not a bare filename" in message, message
    assert "never carries a path, an argument or a shell string" in message


def test_a_plausible_but_absent_script_gets_the_allow_list_refusal(project):
    """The other half of the pair: a well-formed name that is simply not
    here is told what IS here, rather than told about shell strings."""
    with pytest.raises(hooks.HookError) as exc:
        _declare(project, [_run_hook(
            action={"kind": "run", "script": "notify_slack.py"})])
    message = str(exc.value)
    assert "is not in scripts/hooks/" in message
    assert "record_payload.py" in message


def test_a_project_may_not_add_to_the_allow_list(project, tmp_path):
    """The allow-list is the ENGINE's directory, so a project declaration
    cannot introduce executable code."""
    smuggled = project / "scripts" / "hooks"
    smuggled.mkdir(parents=True)
    (smuggled / "mine.py").write_text("print('hi')", encoding="utf-8")
    with pytest.raises(hooks.HookError) as exc:
        _declare(project, [_run_hook(
            action={"kind": "run", "script": "mine.py"})])
    assert "mine.py" in str(exc.value)


def test_the_two_action_kinds_may_not_borrow_each_others_fields(project):
    with pytest.raises(hooks.HookError):
        _declare(project, [_run_hook(action={
            "kind": "run", "script": "record_payload.py", "text": "hi"})])
    with pytest.raises(hooks.HookError):
        _declare(project, [_steer_hook(action={
            "kind": "steer", "text": "hi", "script": "record_payload.py"})])


# ── Where a declaration comes from ──────────────────────────────────

def test_a_project_declaration_replaces_the_engine_one(project):
    declaration = _declare(project, [_steer_hook()])
    assert declaration.source == hooks.PROJECT
    assert declaration.path.endswith(hooks.DECLARATION_FILENAME)
    assert [h.name for h in declaration.hooks] == ["tell_the_captain"]


def test_with_no_project_file_the_engine_answers(project):
    declaration = hooks.load(str(project))
    assert declaration.source == hooks.ENGINE
    assert declaration.hooks == (), "the engine declares none by default"


def test_an_absence_is_stated_once_rather_than_left_silent(project):
    """A layer that says nothing when it has nothing to do reads exactly
    like a layer that is broken."""
    lines = hooks.describe(hooks.load(str(project)))
    assert lines and "nothing fires automatically" in lines[0]


def test_the_header_names_every_hook_and_its_reason(project):
    lines = hooks.describe(_declare(project, [_steer_hook()]))
    body = "\n".join(lines)
    assert "tell_the_captain" in body
    assert "qa_finding_raised" in body
    assert "Why this hook exists" in body


# ── The fingerprint ─────────────────────────────────────────────────

def test_a_fingerprint_is_readable_not_hashed():
    assert hooks.fingerprint("qa_finding_raised", QA_PAYLOAD) == \
        "qa_finding_raised:subtitle_gaps"


def test_a_payload_missing_an_identity_field_raises():
    """A fingerprint completed from a default would make two different
    findings look like one, and the once-only guard would swallow the
    second in silence."""
    with pytest.raises(hooks.HookError) as exc:
        hooks.fingerprint("qa_finding_raised", {"step": "validate"})
    assert "metric" in str(exc.value)


def test_every_condition_declares_what_identifies_an_instance():
    for name, condition in hooks.CONDITIONS.items():
        assert condition.identity, f"{name} has no identity fields"
        assert condition.describe, f"{name} has no description"
        assert condition.name == name


# ── Firing: the run action ──────────────────────────────────────────

def test_a_run_action_really_runs_and_gets_the_payload_on_stdin(project):
    declaration = _declare(project, [_run_hook()])
    fired = hooks.dispatch(str(project), "qa_finding_raised", QA_PAYLOAD,
                           "run-1", declaration)

    assert [f.outcome for f in fired] == [hooks.FIRED], fired
    log = project / "pipeline_output" / "logs" / "hooks.log"
    assert log.is_file(), "the script did not run"
    envelope = json.loads(log.read_text(encoding="utf-8").strip())
    assert envelope["condition"] == "qa_finding_raised"
    assert envelope["hook"] == "record_it"
    assert envelope["payload"]["metric"] == "subtitle_gaps"
    assert envelope["project_dir"] == str(project)


def test_a_failing_script_is_reported_and_does_not_raise(project):
    """A hook is automation ON TOP of the run. A run whose real work
    succeeded must not be failed by it."""
    declaration = _declare(project, [_run_hook()])
    # The script refuses a project_dir that is not a directory.
    fired = hooks.dispatch(str(project) + "_gone", "qa_finding_raised",
                           QA_PAYLOAD, "run-1", declaration)
    assert [f.outcome for f in fired] == [hooks.FAILED]
    assert "exited 2" in fired[0].detail


# ── Firing: the steer action, into the captain's own feed ───────────

def test_a_steer_lands_in_the_review_channel_tagged_as_machine(project):
    declaration = _declare(project, [_steer_hook()])
    fired = hooks.dispatch(str(project), "qa_finding_raised", QA_PAYLOAD,
                           "run-1", declaration)
    assert [f.outcome for f in fired] == [hooks.FIRED], fired

    notes = review_channel.list_notes(str(project))
    assert len(notes) == 1
    note = notes[0]
    # THE rule: same feed, tagged as machine-originated.
    assert note["origin"] == review_channel.ORIGIN_HOOK
    assert "The QA measured something." in note["text"]
    # It says which hook and which instance, so the captain can tell what
    # produced it without opening a log.
    assert "qa_finding_raised:subtitle_gaps" in note["text"]


def test_a_steer_anchors_to_something_the_dashboard_really_renders(project):
    """`normalise_anchor` refuses an empty selector - "a note with no
    anchor is a page comment". That rule is satisfied, not weakened:
    `pipeline-view.js` renders `data-step-id` on every step card."""
    declaration = _declare(project, [_steer_hook()])
    hooks.dispatch(str(project), "qa_finding_raised", QA_PAYLOAD, "run-1",
                   declaration)
    anchor = review_channel.list_notes(str(project))[0]["anchor"]
    assert anchor["selector"] == '[data-step-id="validate"]'
    assert anchor["step_id"] == "validate"
    assert anchor["view"] == "pipeline"


def test_a_steer_sends_only_its_own_note(project):
    """Sending the whole queue would post the captain's half-written
    drafts along with the machine's note."""
    review_channel.queue_note(
        str(project), "my own half-written thought",
        {"selector": "#something", "tag": "div"})
    declaration = _declare(project, [_steer_hook()])
    hooks.dispatch(str(project), "qa_finding_raised", QA_PAYLOAD, "run-1",
                   declaration)

    notes = review_channel.list_notes(str(project))
    captain = [n for n in notes
               if n["origin"] == review_channel.ORIGIN_CAPTAIN][0]
    assert captain["status"] == "queued", "the captain's draft was sent"
    machine = [n for n in notes
               if n["origin"] == review_channel.ORIGIN_HOOK][0]
    assert machine["status"] == "sent"


def test_a_steer_with_nowhere_to_pin_reports_rather_than_guessing(project):
    hook = _steer_hook(when="qa_finding_raised")
    hook["action"] = {"kind": "steer", "text": "x"}
    declaration = _declare(project, [hook])
    fired = hooks.dispatch(str(project), "qa_finding_raised",
                           {"metric": "chroma"}, "run-1", declaration)
    assert [f.outcome for f in fired] == [hooks.FAILED]
    assert "anchor_step" in fired[0].detail


def test_the_browser_cannot_post_a_note_claiming_to_be_a_hook(project):
    """A note posted through the dashboard is the captain's BY
    CONSTRUCTION: the route does not pass an origin, so a browser cannot
    claim to be a hook and a hook cannot claim to be the captain."""
    from library.dashboard.models import ReviewNoteRequest
    assert "origin" not in ReviewNoteRequest.model_fields

    note = review_channel.queue_note(
        str(project), "typed by hand", {"selector": "#a", "tag": "div"})
    assert note["origin"] == review_channel.ORIGIN_CAPTAIN

    with pytest.raises(ValueError):
        review_channel.queue_note(
            str(project), "x", {"selector": "#a", "tag": "div"},
            origin="something_else")


# ── Guard 1: depth zero only ────────────────────────────────────────

def test_a_hook_does_not_fire_inside_a_hook(project):
    declaration = _declare(project, [_run_hook()])
    os.environ[hooks.ENV_DEPTH] = "1"
    fired = hooks.dispatch(str(project), "qa_finding_raised", QA_PAYLOAD,
                           "run-1", declaration)
    assert [f.outcome for f in fired] == [hooks.INSIDE_HOOK]
    assert not (project / "pipeline_output" / "logs" / "hooks.log").exists()


def test_the_depth_guard_is_passed_to_the_child_and_is_not_configurable(project):
    """The child's env carries the next depth. `_run_step_subprocess`
    passes no explicit env, so every descendant inherits it."""
    assert hooks.child_env()[hooks.ENV_DEPTH] == "1"
    os.environ[hooks.ENV_DEPTH] = "1"
    assert hooks.child_env()[hooks.ENV_DEPTH] == "2"
    # There is no declaration key that could switch this off.
    assert "depth" not in hooks.DECLARATION_KEYS
    assert "depth" not in hooks.HOOK_KEYS


def test_the_header_says_when_this_run_can_fire_nothing(project):
    os.environ[hooks.ENV_DEPTH] = "1"
    lines = hooks.describe(_declare(project, [_steer_hook()]))
    assert any("none of them will fire" in line for line in lines)


# ── Guard 2: one instance fires a hook once ─────────────────────────

def test_the_same_instance_fires_once_per_run(project):
    declaration = _declare(project, [_run_hook()])
    first = hooks.dispatch(str(project), "qa_finding_raised", QA_PAYLOAD,
                           "run-1", declaration)
    second = hooks.dispatch(str(project), "qa_finding_raised", QA_PAYLOAD,
                            "run-1", declaration)
    assert [f.outcome for f in first] == [hooks.FIRED]
    assert [f.outcome for f in second] == [hooks.ALREADY_FIRED]

    log = project / "pipeline_output" / "logs" / "hooks.log"
    assert len(log.read_text(encoding="utf-8").strip().splitlines()) == 1


def test_a_different_instance_of_the_same_condition_still_fires(project):
    """The key is the FINGERPRINT, not the condition: two different QA
    findings are two things to act on."""
    declaration = _declare(project, [_run_hook()])
    hooks.dispatch(str(project), "qa_finding_raised", QA_PAYLOAD, "run-1",
                   declaration)
    other = hooks.dispatch(str(project), "qa_finding_raised",
                           {"metric": "chroma", "step": "validate"},
                           "run-1", declaration)
    assert [f.outcome for f in other] == [hooks.FIRED]


def test_a_later_run_may_fire_the_same_instance_again(project):
    """`run_id` is in the key. A new run is a new chance to act; the
    ledger stops a LOOP, not a re-run."""
    declaration = _declare(project, [_run_hook()])
    hooks.dispatch(str(project), "qa_finding_raised", QA_PAYLOAD, "run-1",
                   declaration)
    again = hooks.dispatch(str(project), "qa_finding_raised", QA_PAYLOAD,
                           "run-2", declaration)
    assert [f.outcome for f in again] == [hooks.FIRED]


def test_the_ledger_is_append_only_and_readable(project):
    declaration = _declare(project, [_run_hook()])
    hooks.dispatch(str(project), "qa_finding_raised", QA_PAYLOAD, "run-1",
                   declaration)
    hooks.dispatch(str(project), "qa_finding_raised", QA_PAYLOAD, "run-2",
                   declaration)
    rows = [json.loads(line) for line in
            hooks.ledger_path(str(project)).read_text(
                encoding="utf-8").strip().splitlines()]
    assert [r["run_id"] for r in rows] == ["run-1", "run-2"]
    assert all(r["hook"] == "record_it" for r in rows)
    assert all(r["fingerprint"] == "qa_finding_raised:subtitle_gaps"
               for r in rows)
    # The reason the hook exists travels with the record.
    assert all(r["describe"] for r in rows)


def test_a_failed_fire_is_still_recorded(project):
    """A hook that ran and blew up has still RUN. Firing it again on the
    same instance is the loop the ledger exists to stop."""
    declaration = _declare(project, [_run_hook()])
    hooks.dispatch(str(project) + "_gone", "qa_finding_raised", QA_PAYLOAD,
                   "run-1", declaration)
    # The ledger lands in the real project, which is where it is read.
    rows = hooks.ledger_path(str(project) + "_gone").read_text(
        encoding="utf-8").strip().splitlines()
    assert len(rows) == 1
    assert json.loads(rows[0])["outcome"] == hooks.FAILED


# ── Guard 3: the per-run budget, reported and not fatal ─────────────

def test_the_budget_stops_firing_and_says_so(project, monkeypatch):
    monkeypatch.setattr(hooks, "MAX_FIRES_PER_RUN", 2)
    declaration = _declare(project, [_run_hook()])

    outcomes = []
    for i in range(4):
        fired = hooks.dispatch(str(project), "qa_finding_raised",
                               {"metric": f"m{i}", "step": "validate"},
                               "run-1", declaration)
        outcomes += [f.outcome for f in fired]

    assert outcomes == [hooks.FIRED, hooks.FIRED,
                        hooks.OVER_BUDGET, hooks.OVER_BUDGET]

    body = "\n".join(hooks.summary_lines())
    assert "budget of 2 was reached" in body
    assert "2 later hook(s) did not fire" in body
    # It names what it spent the budget ON. A layer that quietly gave up
    # is indistinguishable from one with nothing to do.
    assert "record_it x2" in body
    assert "does not fail the run" in body


def test_exhausting_the_budget_is_not_an_exception(project, monkeypatch):
    monkeypatch.setattr(hooks, "MAX_FIRES_PER_RUN", 0)
    declaration = _declare(project, [_run_hook()])
    fired = hooks.dispatch(str(project), "qa_finding_raised", QA_PAYLOAD,
                           "run-1", declaration)
    assert [f.outcome for f in fired] == [hooks.OVER_BUDGET]


# ── What the run summary is handed ──────────────────────────────────

def test_nothing_declared_reports_nothing(project):
    hooks.dispatch(str(project), "qa_finding_raised", QA_PAYLOAD, "run-1")
    assert hooks.summary_lines() == []
    assert hooks.as_records() == []


def test_what_fired_is_reported_for_the_run_summary(project):
    declaration = _declare(project, [_run_hook(), _steer_hook()])
    hooks.dispatch(str(project), "qa_finding_raised", QA_PAYLOAD, "run-1",
                   declaration)
    body = "\n".join(hooks.summary_lines())
    assert "2 fired of 2 considered" in body
    assert "record_it" in body and "tell_the_captain" in body

    records = hooks.as_records()
    assert {r["hook"] for r in records} == {"record_it", "tell_the_captain"}
    assert all(r["fingerprint"] == "qa_finding_raised:subtitle_gaps"
               for r in records)


def test_the_collector_is_reset_per_run(project):
    declaration = _declare(project, [_run_hook()])
    hooks.dispatch(str(project), "qa_finding_raised", QA_PAYLOAD, "run-1",
                   declaration)
    assert hooks.fires()
    hooks.reset()
    assert hooks.fires() == []


# ── match, which is field equality and never an expression ──────────

def test_match_narrows_a_hook_to_the_instances_it_wants(project):
    declaration = _declare(project, [_run_hook(match={"metric": "chroma"})])
    missed = hooks.dispatch(str(project), "qa_finding_raised", QA_PAYLOAD,
                            "run-1", declaration)
    assert missed == [], "subtitle_gaps is not chroma"

    hit = hooks.dispatch(str(project), "qa_finding_raised",
                         {"metric": "chroma", "step": "validate"},
                         "run-1", declaration)
    assert [f.outcome for f in hit] == [hooks.FIRED]


def test_match_accepts_a_list_meaning_any_of(project):
    declaration = _declare(
        project, [_run_hook(match={"metric": ["chroma", "subtitle_gaps"]})])
    fired = hooks.dispatch(str(project), "qa_finding_raised", QA_PAYLOAD,
                           "run-1", declaration)
    assert [f.outcome for f in fired] == [hooks.FIRED]


def test_a_condition_nothing_is_armed_for_fires_nothing(project):
    declaration = _declare(project, [_run_hook()])
    assert hooks.dispatch(str(project), "gate_verdict",
                          {"step": "scan", "verdict": "approved"},
                          "run-1", declaration) == []


def test_dispatching_an_unknown_condition_raises(project):
    """A firing site naming a condition that does not exist is a
    programming error, not a runtime outcome."""
    with pytest.raises(hooks.HookError):
        hooks.dispatch(str(project), "something_happened", {}, "run-1")


# ── The seam with increment 4 ───────────────────────────────────────

def test_the_operation_conditions_match_the_real_result_type():
    """Holds the seam with increment 4 open, and RUNS in both worlds.

    This fired on batch 1 and was right to: `requirement_unsatisfied`
    declared an identity field `requirement` that is not on
    `OperationResult`, and `output_empty` declared `step`/`output` which
    are not on it either. The FIX was the vocabulary's, not theirs - no
    field was added to `OperationResult` and nothing is computed here.

    What it checks now is the real relationship rather than assuming
    every identity field is a field on the result:

    * a one-to-one condition's identity is all result fields;
    * a one-to-many condition names the COLLECTION it fires once per,
      which must be a result field, and splits its identity into the
      part from the result and the part from the element.

    The element's own contract (`.name`, `.produced_by`) belongs to
    increment 3's `Requirement`, NOT to `OperationResult` - whose
    annotation is `Tuple[Any, ...]` and which deliberately does not
    validate it, because that would be a second implementation of
    `requirements.py`'s own invariant. So it is asserted against
    `requirements.Requirement` where that exists, and never against
    `OperationResult`.

    Deliberately not a skip: a skip conditioned on whether a file exists
    in THIS repository is an always-skip, which `tests/skip_audit.py`
    exists to catch.
    """
    try:
        from library.tools import operations
    except ImportError:
        operations = None

    if operations is None:
        assert "PROVISIONAL" in hooks.__doc__, (
            "operations.py is not here, so the identity fields for the "
            "operation conditions cannot be reconciled by reading. The "
            "module has to keep saying so - deleting that note is how "
            "the reconciliation gets forgotten."
        )
        assert "OperationResult" in hooks.__doc__
        return

    fields = set(getattr(operations.OperationResult,
                         "__dataclass_fields__", {}))
    assert fields, "OperationResult is not a dataclass any more"

    for name in hooks.OPERATION_DERIVED:
        condition = hooks.CONDITIONS[name]
        from_result = set(condition.identity) - set(condition.element_identity)
        for identity in sorted(from_result):
            assert identity in fields, (
                f"{name}'s identity field {identity!r} is not on "
                f"OperationResult, which carries {sorted(fields)}. "
                f"Reconcile hooks.CONDITIONS against the real type - do "
                f"not add a field to OperationResult to suit this, and "
                f"do not compute one here."
            )
        if condition.fires_once_per:
            assert condition.fires_once_per in fields, (
                f"{name} fires once per {condition.fires_once_per!r}, "
                f"which is not a collection on OperationResult "
                f"({sorted(fields)})."
            )
            assert condition.element_identity, (
                f"{name} is one-to-many but names nothing that tells one "
                f"element from another, so every element of a result "
                f"would share a fingerprint and only the first would fire."
            )
        else:
            assert not condition.element_identity, (
                f"{name} names element_identity but no collection to "
                f"take it from."
            )


def test_the_element_contract_comes_from_requirements_not_operations():
    """`unsatisfied` holds Requirement objects, and `.name` is THEIR
    contract.

    `OperationResult.unsatisfied` is annotated `Tuple[Any, ...]` and is
    deliberately not validated - increment 4 will not add that check,
    because it would be a second implementation of `requirements.py`'s
    own invariant. So the element half of a one-to-many identity is
    asserted here, against the type that owns it.
    """
    try:
        from library.tools import requirements
    except ImportError:
        requirements = None

    if requirements is None:
        assert "increment 3" in hooks.__doc__ or "PROVISIONAL" in hooks.__doc__
        return

    fields = set(getattr(requirements.Requirement,
                         "__dataclass_fields__", {}))
    assert fields, "Requirement is not a dataclass any more"
    for name in hooks.OPERATION_DERIVED:
        condition = hooks.CONDITIONS[name]
        for identity in condition.element_identity:
            assert identity in fields, (
                f"{name}'s element identity {identity!r} is not on "
                f"requirements.Requirement, which carries "
                f"{sorted(fields)}."
            )


def test_hooks_defines_no_result_type_and_no_hollow_rule():
    """Ruling 1, asserted rather than intended.

    `dispatch` takes a plain Mapping and is handed what the operation
    already decided. A `hollow` or `produced_nothing` computed in this
    module would be the second implementation the ruling forbids.
    """
    import inspect
    source = inspect.getsource(hooks)
    body = "\n".join(line for line in source.splitlines()
                     if not line.strip().startswith(("#", "*")))
    for banned in ("check_output_is_real(", "def produced_nothing",
                   "class OperationResult"):
        assert banned not in body, (
            f"hooks.py contains {banned!r}: the operation layer already "
            f"owns that, and a second one is what Ruling 1 forbids."
        )


def test_a_refusal_with_no_requirements_still_reaches_a_hook(project):
    """The edge that would otherwise reach NO hook at all.

    `OperationResult.__post_init__` requires `unsatisfied` OR `error`, so
    a refusal may legally carry zero requirements - an environment
    refusal like `error="npx is not on PATH"`. A firing site LOOPS
    `unsatisfied`, so `requirement_unsatisfied` correctly fires zero
    times there. `operation_refused` therefore has to be dispatched
    unconditionally, or that entire class of refusal is silent.

    This is the firing site's contract, written here as the site will
    write it, so the property is pinned before the site exists.
    """
    declaration = _declare(project, [
        _run_hook(name="on_refusal", when="operation_refused"),
        _run_hook(name="on_requirement", when="requirement_unsatisfied"),
    ])

    # The zero-requirement refusal, exactly as OperationResult permits it.
    refused = {"operation": "subtitles.render", "scope": "REGION 45.0-72.0",
               "status": "refused", "unsatisfied": (),
               "error": "npx is not on PATH"}

    fired = hooks.dispatch(str(project), "operation_refused", refused,
                           "run-1", declaration)
    for requirement in refused["unsatisfied"]:
        fired += hooks.dispatch(
            str(project), "requirement_unsatisfied",
            {"operation": refused["operation"], "name": requirement.name},
            "run-1", declaration)

    assert [(f.hook, f.outcome) for f in fired] == [("on_refusal", hooks.FIRED)], (
        "an environment refusal names no requirement, so the only hook "
        "that can carry it is operation_refused"
    )


def test_a_refusal_that_does_name_requirements_fires_once_for_each(project):
    """The one-to-many case, with no mechanism behind it.

    `dispatch` already takes ONE payload and the fingerprint separates
    instances, so firing once per element is a `for` loop at the site.
    Nothing in hooks.py iterates a collection or collects results.
    """
    declaration = _declare(project, [
        _run_hook(name="on_requirement", when="requirement_unsatisfied")])
    result = {"operation": "subtitles.plan", "scope": "PROJECT"}
    fired = []
    for requirement_name in ("audio_spine", "rough_cut_review"):
        fired += hooks.dispatch(
            str(project), "requirement_unsatisfied",
            {"operation": result["operation"], "name": requirement_name},
            "run-1", declaration)

    assert [f.outcome for f in fired] == [hooks.FIRED, hooks.FIRED]
    assert [f.fingerprint for f in fired] == [
        "requirement_unsatisfied:subtitles.plan:audio_spine",
        "requirement_unsatisfied:subtitles.plan:rough_cut_review",
    ], "each requirement needs its own fingerprint or only the first fires"


def test_two_operations_refusing_on_one_requirement_are_different_instances(project):
    """Why the payload is the PAIR and not the requirement alone.

    A Requirement carries `name` and `produced_by` and does NOT carry
    operation context. Two operations can refuse on the same
    requirement, and they are two things to act on.
    """
    declaration = _declare(project, [
        _run_hook(name="on_requirement", when="requirement_unsatisfied")])
    first = hooks.dispatch(str(project), "requirement_unsatisfied",
                           {"operation": "subtitles.plan",
                            "name": "audio_spine"}, "run-1", declaration)
    second = hooks.dispatch(str(project), "requirement_unsatisfied",
                            {"operation": "subtitles.render",
                             "name": "audio_spine"}, "run-1", declaration)
    assert [f.outcome for f in first] == [hooks.FIRED]
    assert [f.outcome for f in second] == [hooks.FIRED], (
        "the same requirement under a different operation was swallowed "
        "as already-fired: the identity is the PAIR"
    )
