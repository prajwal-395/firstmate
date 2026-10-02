"""One enumeration for AUTOMATIC behaviour: a condition, and what it fires.

This module is the whole of the pipeline's trigger and hook layer.  The
conditional behaviour that predates it - `run_pipeline.apply_source_identity`
and `apply_code_identity` invalidating cached work, and the model steers
through `present_llm_step(retry_feedback=)` in `post_bridge_retry`,
`second_pass` and the QA loop - is precedent, not a second hook layer;
the budget below copies `second_pass`'s policy rather than inventing a
fourth.

What a hook is
--------------
A CONDITION the pipeline observes (`CONDITIONS`), and an ACTION
(`ACTION_KINDS`: `run`, `steer`).  Both are closed vocabularies and both
are DATA - `library/hooks.json` (`ENGINE`), or a project's own
`<project>/hooks.json` (`PROJECT`), read by `load`.  There is no code in a
config string and no shell string anywhere: a `run` action names a script
that must already be in `scripts/hooks/` (`allowed_scripts`), and the
payload reaches it on stdin.  `describe` is REQUIRED on every hook, and it
is printed in the run header and in the fired record, so the reason
reaches the report a reader opens.

A condition not in the vocabulary is REFUSED AT LOAD
----------------------------------------------------
`HookError`, named, before anything runs, with the known set listed - the
shape `run_scope._reject_unknown` gives an unknown step.  `dispatch` on an
unknown condition raises too: that is a programming error at the site.

Three guards, and none of them is a preference
----------------------------------------------
1. **Depth zero only, and NOT configurable.**  A hook fires only when
   `PIPELINE_HOOK_DEPTH` (`ENV_DEPTH`) is unset; a `run` action sets it
   to 1 in the child's environment (`child_env`), and every descendant
   inherits it.  An operation invoked BY a hook fires nothing.
2. **A condition instance fires a hook ONCE.**  The ledger is keyed by
   `(run_id, hook_name, condition_fingerprint)` and appended to
   `pipeline_output/provenance/hooks.jsonl` (`ledger_path`).  The
   `fingerprint` is READABLE (`qa_finding_raised:subtitle_gaps`), so a
   hook that fired is explicable in the run summary (`ALREADY_FIRED`).
3. **A per-run budget, whose exhaustion is REPORTED and does not fail.**
   `MAX_FIRES_PER_RUN` is a backstop, not a tuning knob.  At the bound
   further dispatches decline (`OVER_BUDGET`) and SAY SO, naming which
   hooks fired and how many times (`summary_lines`).  An unfired hook
   leaves the run's own output valid, so the run is not failed.

Every automated steer lands in the feed the captain already reads
-----------------------------------------------------------------
A `steer` writes into `library/dashboard/review_channel.py` - the same
store as the captain's own notes - tagged `origin="hook"`, as its own
batch.  Never a second feed.

* **A hook WRITES to the channel and never POLLS it.**  `wait_for_batch`
  marks delivered the batch it sees (`mark_delivered`), so a second poller
  would consume the captain's batch.
* **A hook's note is anchored like anybody else's.**  `normalise_anchor`
  REFUSES an empty `selector` (AGENTS.md section 4); a steer anchors to
  `[data-step-id="<step>"]`, taken from the hook's `anchor_step` or the
  payload's `step`, and refuses when it has neither.

Where it fires today
--------------------
One firing site: `reel_hearing` raises `qa_finding_raised` for each
non-clean finding, and gates nothing either way.  `run_pipeline.py` does
not call `dispatch`; the other conditions have no site yet.  The
summary-side conditions (`qa_finding_raised`, `gate_verdict`,
`output_empty`) have a payload in the run summary's reporter blocks, but
those run after `status` is decided and "reading is not gating" - whether
a `run` action may ACT there is a decision for the captain, not an
implication of wiring a site.  `tests/test_hooks.py` drives every path
through the public API.

The operation conditions and `OperationResult`
----------------------------------------------
The operation-derived conditions (`OPERATION_DERIVED`) take their payload
off ONE type, `operations.OperationResult`.  The standing instruction to
whoever wires those sites:

* **Do NOT define a result type in this module, and do not define a
  second hollow rule.**  `dispatch` takes a plain `Mapping` and computes
  no verdict of its own; `hollow` is `run_pipeline.check_output_is_real`'s
  verdict, carried on the result.
* **A REFUSAL WITH NO REQUIREMENTS IS LEGAL, and must still reach a
  hook.**  An environment refusal (`error=...`, `unsatisfied=()`) fires
  `requirement_unsatisfied` ZERO times, so the site MUST dispatch
  `operation_refused` unconditionally for every refusal.
  `tests/test_hooks.py::test_a_refusal_with_no_requirements_still_reaches_a_hook`
  pins it.
* The `identity` fields for those conditions were PROVISIONAL until
  `library.tools.operations` existed; they are now reconciled against the
  real `OperationResult` by
  `tests/test_hooks.py::test_the_operation_conditions_match_the_real_result_type`,
  which fails on any identity field the type does not carry.  Reconcile
  `CONDITIONS`; never add a field to `OperationResult` to suit it.

The measurements and rulings behind these rules (the pre-build audit, the
JSON-versus-YAML argument, the increment plan): docs/evidence/hooks.md.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from library.tools.ren_refusal import REFUSAL_EXIT_CODE, RenRefusal


class HookError(RenRefusal):
    """A hook declaration that cannot be used, refused before the run."""


# ── The closed condition vocabulary ──────────────────────────────────

@dataclass(frozen=True)
class Condition:
    """One thing the pipeline can observe, and what identifies an instance.

    `identity` names the payload fields that make one occurrence
    different from another. It is REQUIRED rather than optional: the
    once-only ledger is keyed on it, so a fingerprint computed from a
    default would make two different findings look like the same one and
    the second would be silently swallowed.

    The remaining three say where a firing site GETS that payload, and
    they exist because four of the six read one type and one of those
    four is one-to-many. Declaring the shape is what lets
    `tests/test_hooks.py` check this vocabulary against the real
    `operations.OperationResult` instead of assuming every identity field
    is a field on it - which is the assumption that made the tripwire
    fire.
    """

    name: str
    describe: str
    identity: Tuple[str, ...]

    reads_operation_result: bool = False
    """A firing site derives this condition's payload from
    `operations.OperationResult` (increment 4)."""

    fires_once_per: str = ""
    """The COLLECTION field on `OperationResult` this condition fires once
    per ELEMENT of. Empty means once per result, which is the one-to-one
    case the other three are.

    This needs no mechanism: `dispatch` already takes one payload and is
    called once per occurrence, and the fingerprint separates them, so a
    one-to-many condition is a firing site writing a `for` loop. Nothing
    here iterates on its own and nothing collects."""

    element_identity: Tuple[str, ...] = ()
    """Which of `identity` come from the ELEMENT rather than the result.
    The rest come from the result. Only meaningful with `fires_once_per`."""


CONDITIONS: Dict[str, Condition] = {
    "operation_completed": Condition(
        "operation_completed",
        "A named operation finished and wrote its output.",
        ("operation", "scope"),
        reads_operation_result=True),
    "operation_refused": Condition(
        "operation_refused",
        "A named operation was refused before it ran, with a reason.",
        ("operation", "scope"),
        reads_operation_result=True),
    # ONE-TO-MANY, and the only one. `OperationResult.unsatisfied` is a
    # TUPLE of Requirement objects, so this fires once per requirement
    # with the PAIR (result, requirement) as its payload.
    #
    # The pair, not the requirement alone: a Requirement carries its own
    # `name` and `produced_by` and does NOT carry operation context -
    # `hasattr(req, "operation")` is False - while which operation
    # refused, at what scope, lives on the result. Two operations can
    # refuse on the same requirement, so a payload that could not name
    # the refusing one would route and report the wrong thing.
    #
    # `(result.operation, requirement.name)` is therefore the composite
    # identity. Both halves exist today; neither side needs a new field,
    # and nothing is computed here. `name` is spelled as the ELEMENT
    # spells it - `requirements.Requirement.name` - rather than renamed
    # to `requirement` on the way past, because a rename is a mapping
    # layer and a mapping layer is the thing that drifts.
    "requirement_unsatisfied": Condition(
        "requirement_unsatisfied",
        "A declared requirement was not met, naming what would produce it.",
        ("operation", "name"),
        reads_operation_result=True,
        fires_once_per="unsatisfied",
        element_identity=("name",)),
    "qa_finding_raised": Condition(
        "qa_finding_raised",
        "The render QA measured something it is asked to report.",
        ("metric",)),
    "gate_verdict": Condition(
        "gate_verdict",
        "A review gate was answered: approved, rejected or revised.",
        ("step", "verdict")),
    # `OperationResult.produced_nothing` is the predicate, and its own
    # docstring names this condition. It reads the result's `hollow` -
    # which CARRIES `run_pipeline.check_output_is_real`'s verdict rather
    # than a second opinion about it - so nothing here judges hollowness.
    "output_empty": Condition(
        "output_empty",
        "An operation produced nothing usable: refused, empty payload, "
        "or the hollow-output check found something.",
        ("operation", "scope"),
        reads_operation_result=True),
}

# The four that read `operations.OperationResult`, derived rather than
# listed twice: a second list is a list that drifts.
OPERATION_DERIVED = tuple(
    name for name, c in CONDITIONS.items() if c.reads_operation_result)


# ── The closed action vocabulary ─────────────────────────────────────

ACTION_RUN = "run"
ACTION_STEER = "steer"
ACTION_KINDS = (ACTION_RUN, ACTION_STEER)

# Where an executable hook script may live. A declaration may NAME a
# script; it may never supply one, and a project may not add to this
# directory - the allow-list is the engine's, so a project declaration
# cannot introduce code.
SCRIPTS_DIRNAME = os.path.join("scripts", "hooks")

# A `run` action is executed as `sys.executable <script>`, never through
# a shell. That is why only `.py` is allowed: a shebang plus an exec bit
# would put the choice of interpreter in the file, which is one step
# away from the arbitrary command string this refuses to have.
SCRIPT_SUFFIX = ".py"

# How long one hook script may take before it is killed and reported.
# A backstop against a wedge, not a performance budget.
SCRIPT_TIMEOUT_S = 60


@dataclass(frozen=True)
class Action:
    """What a hook does. One kind, and only that kind's fields."""

    kind: str
    script: str = ""
    """`run`: the file in scripts/hooks/, by name."""

    text: str = ""
    """`steer`: what the note says."""

    anchor_step: str = ""
    """`steer`: the step whose card the note is pinned to. Empty means
    the payload's own `step` field answers."""


@dataclass(frozen=True)
class Hook:
    """One declared condition-and-action, as the file said."""

    name: str
    describe: str
    when: str
    action: Action
    match: Mapping[str, Any] = field(default_factory=dict)
    """Optional field equality against the payload. A value may be a list,
    meaning any of. Nothing here is an expression - a predicate in a
    config string is code in a config string."""

    def matches(self, payload: Mapping[str, Any]) -> bool:
        for key, wanted in self.match.items():
            got = payload.get(key)
            if isinstance(wanted, list):
                if got not in wanted:
                    return False
            elif got != wanted:
                return False
        return True


@dataclass(frozen=True)
class Declaration:
    """The hooks in force for a run, and which file said so."""

    hooks: Tuple[Hook, ...] = ()
    path: str = ""
    source: str = ""
    """`project`, `engine`, or empty when neither file exists."""

    @property
    def is_declared(self) -> bool:
        return bool(self.path)


NO_HOOKS = Declaration()
"""The absence of a declaration. Stated once per run rather than left
silent, the shape `brief_attachment.describe` established."""


# ── Where declarations and scripts live ──────────────────────────────

_REPO_ROOT = Path(__file__).resolve().parents[2]

DECLARATION_FILENAME = "hooks.json"

ENGINE = "engine"
PROJECT = "project"


def engine_declaration_path() -> Path:
    return _REPO_ROOT / "library" / DECLARATION_FILENAME


def project_declaration_path(project_dir: str) -> Path:
    return Path(project_dir) / DECLARATION_FILENAME


def scripts_dir() -> Path:
    return _REPO_ROOT / SCRIPTS_DIRNAME


def allowed_scripts() -> Dict[str, Path]:
    """The scripts a `run` action may name, read off disk every load.

    An allow-list computed from the directory rather than written down
    twice: a list in this file would be wrong the first time somebody
    added a script, and a script nobody may name is not an allow-list
    entry.
    """
    directory = scripts_dir()
    if not directory.is_dir():
        return {}
    return {p.name: p for p in sorted(directory.iterdir())
            if p.is_file() and p.suffix == SCRIPT_SUFFIX}


# ── Loading, and the refusals ────────────────────────────────────────

DECLARATION_KEYS = frozenset({"hooks"})
HOOK_KEYS = frozenset({"name", "describe", "when", "match", "action"})
ACTION_KEYS = frozenset({"kind", "script", "text", "anchor_step"})


def _reject_unknown(got: Sequence[str], known: frozenset, what: str,
                    where: str) -> None:
    """The shape `run_scope._reject_unknown` takes: name it, list the set.

    A key nobody reads is the trap - a misspelled `when` that armed
    nothing would look exactly like a hook that had nothing to do.
    """
    unknown = [k for k in got if k not in known]
    if unknown:
        raise HookError(
            f"{where}: unknown {what} {', '.join(repr(k) for k in unknown)}",
            f"a key nobody reads is the trap - a misspelled entry that "
            f"armed nothing would look like one with nothing to do. "
            f"Known: {', '.join(sorted(known))}",
            f"spell it as one of {', '.join(sorted(known))} in {where}"
        )


def _parse_action(raw: Any, where: str) -> Action:
    if not isinstance(raw, dict):
        raise HookError(
            f"{where}: 'action' must be an object",
            "a hook fires one action, 'run' or 'steer', and anything else "
            "cannot be armed",
            f"write the action as an object with a 'kind' in {where}")
    _reject_unknown(list(raw), ACTION_KEYS, "action key", where)

    kind = raw.get("kind", "")
    if kind not in ACTION_KINDS:
        raise HookError(
            f"{where}: unknown action kind {kind!r}",
            f"a hook fires 'run' or 'steer'. Known: "
            f"{', '.join(ACTION_KINDS)}",
            f"set 'kind' to one of {', '.join(ACTION_KINDS)} in {where}"
        )

    if kind == ACTION_RUN:
        script = raw.get("script", "")
        if not script:
            raise HookError(
                f"{where}: a 'run' action needs a 'script'",
                "a run action with nothing to run would arm and fire nothing",
                f"name a file in {SCRIPTS_DIRNAME}/ as 'script' in {where}")
        # A script is NAMED, never supplied. Anything with a path
        # separator or a shell metacharacter in it is refused here rather
        # than sanitised, because a name that needs sanitising is not a
        # name - it is the arbitrary command string this design refuses
        # to have.
        if any(c in script for c in "/\\ ;|&$><`\n\t") or ".." in script:
            raise HookError(
                f"{where}: script {script!r} is not a bare filename",
                f"a 'run' action names a file in {SCRIPTS_DIRNAME}/; it "
                f"never carries a path, an argument or a shell string",
                f"put the command in a file in {SCRIPTS_DIRNAME}/ and "
                f"name that file as 'script' in {where}"
            )
        allowed = allowed_scripts()
        if script not in allowed:
            raise HookError(
                f"{where}: script {script!r} is not in {SCRIPTS_DIRNAME}/",
                "a hook may only run a script that is already in the "
                "repository. "
                f"Allowed: {', '.join(sorted(allowed)) or '(none)'}",
                f"add the script to {SCRIPTS_DIRNAME}/ first, then "
                f"declare it in {where}"
            )
        for unexpected in ("text", "anchor_step"):
            if raw.get(unexpected):
                raise HookError(
                    f"{where}: a 'run' action may not carry "
                    f"{unexpected!r}",
                    f"{unexpected!r} belongs to 'steer'",
                    f"move {unexpected!r} into a 'steer' action in {where}")
        return Action(kind=ACTION_RUN, script=script)

    text = raw.get("text", "")
    if not text:
        raise HookError(
            f"{where}: a 'steer' action needs a 'text'",
            "a steer action with no text steers nothing",
            f"write the steering text as 'text' in {where}")
    if raw.get("script"):
        raise HookError(
            f"{where}: a 'steer' action may not carry 'script'",
            "'script' belongs to 'run'",
            f"move the script into a 'run' action in {where}"
        )
    return Action(kind=ACTION_STEER, text=text,
                  anchor_step=raw.get("anchor_step", ""))


def _parse_hook(raw: Any, index: int, path: Path) -> Hook:
    where = f"{path}: hooks[{index}]"
    if not isinstance(raw, dict):
        raise HookError(
            f"{where}: each hook must be an object",
            "a hook is a named declaration, not a bare value",
            f"write the hook as an object with name/describe/when/action "
            f"in {path}")
    _reject_unknown(list(raw), HOOK_KEYS, "hook key", where)

    name = raw.get("name", "")
    if not name:
        raise HookError(
            f"{where}: every hook needs a 'name'",
            "the name is the ledger key - without it one hook's fires "
            "would be recorded as another's",
            f"give the hook a unique 'name' in {path}")
    where = f"{path}: hook {name!r}"

    describe = raw.get("describe", "")
    if not describe:
        raise HookError(
            f"{where}: every hook needs a 'describe' saying WHY it exists",
            "JSON carries no comments, so the reason lives in the data - "
            "and it is what the run header and the fired record print",
            f"write one sentence as 'describe' in {where}")

    when = raw.get("when", "")
    if when not in CONDITIONS:
        raise HookError(
            f"{where}: unknown condition {when!r}",
            f"a hook fires on a declared condition. Known: "
            f"{', '.join(sorted(CONDITIONS))}",
            f"set 'when' to one of {', '.join(sorted(CONDITIONS))} in "
            f"{where}"
        )

    match = raw.get("match", {})
    if not isinstance(match, dict):
        raise HookError(
            f"{where}: 'match' must be an object of field: value",
            "match narrows which firings of the condition arm this hook",
            f"write 'match' as an object of field: value in {where}")

    return Hook(name=name, describe=describe, when=when, match=match,
                action=_parse_action(raw.get("action"), where))


def _load_file(path: Path, source: str) -> Declaration:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except ValueError as exc:
        raise HookError(
            f"{path}: not valid JSON",
            f"{exc}",
            f"fix the JSON in {path} (a trailing comma is the usual "
            f"cause), then re-run") from exc
    except OSError as exc:
        raise HookError(
            f"{path}: cannot be read",
            f"{exc}",
            f"restore the hooks file at {path}, or remove the hooks "
            f"declaration that points at it") from exc

    if not isinstance(raw, dict):
        raise HookError(
            f"{path}: the top level must be an object",
            "a hooks file declares named hooks, not a bare list",
            f"wrap the declaration as an object with a 'hooks' list in "
            f"{path}")
    _reject_unknown(list(raw), DECLARATION_KEYS, "key", str(path))

    hooks_raw = raw.get("hooks", [])
    if not isinstance(hooks_raw, list):
        raise HookError(
            f"{path}: 'hooks' must be a list",
            "the file declares zero or more hooks",
            f"write 'hooks' as a list in {path}")

    hooks = [_parse_hook(h, i, path) for i, h in enumerate(hooks_raw)]
    seen: Dict[str, int] = {}
    for hook in hooks:
        if hook.name in seen:
            raise HookError(
                f"{path}: two hooks are called {hook.name!r}",
                "the name is the ledger key, so it has to be unique or "
                "one hook's fires would be recorded as the other's",
                f"rename one of the two {hook.name!r} hooks in {path}"
            )
        seen[hook.name] = 1
    return Declaration(hooks=tuple(hooks), path=str(path), source=source)


def load(project_dir: str = "") -> Declaration:
    """The hooks in force, refused by name if the file cannot be used.

    A project's own file REPLACES the engine's whole declaration rather
    than merging with it - the same choice `timed_text_overlay` makes for
    a project's effect slot (AGENTS.md section 14), and for the same
    reason: a merge needs a rule for what wins per hook, and a fourth
    mechanism needs evidence that replacing could not say it.

    Which file answered is reported on every run.
    """
    if project_dir:
        own = project_declaration_path(project_dir)
        if own.is_file():
            return _load_file(own, PROJECT)
    engine = engine_declaration_path()
    if engine.is_file():
        return _load_file(engine, ENGINE)
    return NO_HOOKS


def describe(declaration: Declaration) -> List[str]:
    """The lines a run prints about the hooks it is running under.

    An absence is STATED, once, rather than left silent - the shape
    `brief_attachment.describe` and `brand_registry.describe_brand_absence`
    established. A layer that says nothing when it has nothing to do
    reads exactly like a layer that is broken.
    """
    if not declaration.is_declared:
        return ["  Hooks: none declared - nothing fires automatically"]
    if not declaration.hooks:
        return [f"  Hooks: none declared in {declaration.path} "
                f"- nothing fires automatically"]
    lines = [f"  Hooks: {len(declaration.hooks)} declared "
             f"({declaration.source}) {declaration.path}"]
    for hook in declaration.hooks:
        lines.append(f"    - {hook.name} on {hook.when} "
                     f"-> {hook.action.kind}: {hook.describe}")
    if depth():
        lines.append(f"    ! this run is itself inside a hook "
                     f"({ENV_DEPTH}={depth()}), so none of them will fire")
    return lines


# ── Guard 1: depth ───────────────────────────────────────────────────

ENV_DEPTH = "PIPELINE_HOOK_DEPTH"
"""Set to 1 in the environment of every `run` action's child. A hook
fires only at depth zero, and there is no setting for that: a
configurable loop guard is a loop guard somebody turns off."""


def depth() -> int:
    """How deep inside a hook this process is. 0 means not inside one."""
    raw = (os.environ.get(ENV_DEPTH) or "").strip()
    if not raw.isdigit():
        return 0
    return int(raw)


def child_env() -> Dict[str, str]:
    """The environment a `run` action's child gets.

    `run_pipeline._run_step_subprocess` passes no explicit env, so a
    pipeline launched by a hook inherits this and every step it runs
    inherits it in turn. The guard therefore holds however deep the
    child goes, without anything having to thread it through.
    """
    env = dict(os.environ)
    env[ENV_DEPTH] = str(depth() + 1)
    return env


# ── Guard 2: a condition instance fires a hook once ──────────────────

LEDGER_FILENAME = "hooks.jsonl"


def fingerprint(condition: str, payload: Mapping[str, Any]) -> str:
    """What makes one occurrence of a condition different from another.

    Readable rather than hashed - `qa_finding_raised:subtitle_gaps` - the
    shape `marker_routing._note_id` takes, because a hook that fired has
    to be explicable to the captain in the run summary.

    A payload missing an identity field RAISES. A fingerprint completed
    from a default would make two different findings look like one, and
    the once-only guard would swallow the second in silence.
    """
    spec = CONDITIONS.get(condition)
    if spec is None:
        raise HookError(
            f"unknown condition {condition!r}",
            f"a fingerprint is keyed on a declared condition. Known: "
            f"{', '.join(sorted(CONDITIONS))}",
            "this is a caller bug, not a declaration bug - the condition "
            "comes from code, so fix the caller to pass a declared one")
    parts = [condition]
    for name in spec.identity:
        if name not in payload:
            raise HookError(
                f"a {condition!r} payload must carry {name!r}",
                "it is what tells one occurrence from another, and the "
                "once-only ledger is keyed on it. Got: "
                f"{', '.join(sorted(payload)) or '(nothing)'}",
                "this is a caller bug, not a declaration bug - fix the "
                "caller to attach the identity field to the payload"
            )
        parts.append(str(payload[name]))
    return ":".join(parts)


def ledger_path(project_dir: str) -> Path:
    from library.tools.project_layout import Area, ProjectLayout
    return ProjectLayout(project_dir).write_path(Area.PROVENANCE,
                                                 LEDGER_FILENAME)


def _ledger_keys(project_dir: str) -> set:
    from library.tools.project_layout import Area, ProjectLayout
    path = ProjectLayout(project_dir).read_path(Area.PROVENANCE,
                                                LEDGER_FILENAME)
    if not path.is_file():
        return set()
    keys = set()
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except ValueError:
                continue
            keys.add((row.get("run_id", ""), row.get("hook", ""),
                      row.get("fingerprint", "")))
    return keys


def _record_fire(project_dir: str, record: dict) -> None:
    """Append-only, for the reason `provenance` is: a fire is a thing
    that happened, and a later run firing the same hook does not unmake
    the record of the first."""
    path = ledger_path(project_dir)
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, sort_keys=True) + "\n")


# ── Guard 3: a per-run budget, reported at the bound ─────────────────

MAX_FIRES_PER_RUN = 20
"""Total hook fires one run may cause. A BACKSTOP against a condition
that recurs unexpectedly, not a tuning knob - the same standing this
module's siblings give `post_bridge_retry.MAX_ATTEMPTS` and
`second_pass.MAX_PASSES`.

At the bound further dispatches DECLINE and say so; the run does not
fail. That is `second_pass`'s policy: an unfired hook leaves the run's
own output perfectly valid, so failing would throw away good work to
enforce a round trip."""


# ── What happened, collected for the run summary ─────────────────────

@dataclass
class Fire:
    """One hook that fired, or declined to, and why."""

    hook: str
    condition: str
    fingerprint: str
    action: str
    outcome: str
    """`fired`, `failed`, `already_fired`, `over_budget` or `inside_hook`."""

    detail: str = ""
    at: str = ""

    def as_record(self) -> dict:
        return {"hook": self.hook, "condition": self.condition,
                "fingerprint": self.fingerprint, "action": self.action,
                "outcome": self.outcome, "detail": self.detail, "at": self.at}


FIRED = "fired"
FAILED = "failed"
ALREADY_FIRED = "already_fired"
OVER_BUDGET = "over_budget"
INSIDE_HOOK = "inside_hook"

_COLLECTED: List[Fire] = []


def reset() -> None:
    """A fresh collector per run.

    The runner is a process, but the dashboard and the tests drive
    `run_pipeline` more than once inside one, and a fire from the
    previous run is not this run's. Same reason `undetermined.reset`,
    `direction_contradiction.reset` and `briefing_interview.reset` exist.
    """
    _COLLECTED.clear()


def fires() -> List[Fire]:
    return list(_COLLECTED)


def as_records() -> List[dict]:
    return [f.as_record() for f in _COLLECTED]


def summary_lines() -> List[str]:
    """What the run summary prints about what fired.

    The seventh instance of a shape this runner already uses six times -
    `qa_findings`, `cut_verdicts`, `marker_routing.undelivered`,
    `alignment_findings`, `undetermined`, `direction_contradiction` and
    `briefing_interview` all print after `status` is decided and none of
    them gates. Reading is not gating here either.
    """
    if not _COLLECTED:
        return []
    lines = []
    counts: Dict[str, int] = {}
    for fire in _COLLECTED:
        counts[fire.outcome] = counts.get(fire.outcome, 0) + 1
    fired = counts.get(FIRED, 0)
    lines.append(
        f"  Hooks: {fired} fired of {len(_COLLECTED)} considered "
        f"({', '.join(f'{k} {v}' for k, v in sorted(counts.items()))})")
    for fire in _COLLECTED:
        if fire.outcome in (FIRED, FAILED):
            detail = f" - {fire.detail}" if fire.detail else ""
            lines.append(f"    {fire.outcome}: {fire.hook} on "
                         f"{fire.fingerprint} [{fire.action}]{detail}")
    if counts.get(OVER_BUDGET):
        # A layer that quietly gave up is indistinguishable from one with
        # nothing to do, so the bound names what it spent the budget on.
        spent: Dict[str, int] = {}
        for fire in _COLLECTED:
            if fire.outcome == FIRED:
                spent[fire.hook] = spent.get(fire.hook, 0) + 1
        lines.append(
            f"    ! the per-run budget of {MAX_FIRES_PER_RUN} was reached "
            f"and {counts[OVER_BUDGET]} later hook(s) did not fire. Spent "
            f"on: {', '.join(f'{k} x{v}' for k, v in sorted(spent.items()))}"
            f" - this does not fail the run.")
    return lines


# ── Firing ───────────────────────────────────────────────────────────

def _fire_run(hook: Hook, project_dir: str, condition: str,
              payload: Mapping[str, Any]) -> Tuple[bool, str]:
    """Execute the named script with the payload on stdin.

    Never a shell, never a string: `sys.executable <script>` with the
    argv built as a list, so there is nothing for a shell to interpret
    even if a payload value contained one.

    `encoding="utf-8"` is mandatory here (AGENTS.md section 9): `text=True`
    decodes with the locale codec, and this repository writes UTF-8
    status glyphs.
    """
    script = allowed_scripts().get(hook.action.script)
    if script is None:
        # The allow-list is read at load AND here, because the directory
        # can change between the two and a hook must never run something
        # that is no longer in the repository.
        return False, (f"script {hook.action.script!r} is no longer in "
                       f"{SCRIPTS_DIRNAME}/")
    # The condition is an ARGUMENT, not a payload field. Reading it off
    # the payload sent every script an empty string, which is exactly the
    # key-name class AGENTS.md 10.1 names - and a hook script that cannot
    # tell which condition fired it cannot do anything useful.
    body = json.dumps({"condition": condition,
                       "hook": hook.name,
                       "project_dir": project_dir,
                       "payload": dict(payload)}, sort_keys=True)
    try:
        done = subprocess.run(
            [sys.executable, str(script)],
            input=body, capture_output=True, encoding="utf-8",
            timeout=SCRIPT_TIMEOUT_S, env=child_env(), check=False,
        )
    except subprocess.TimeoutExpired:
        return False, (f"{hook.action.script} did not finish within "
                       f"{SCRIPT_TIMEOUT_S}s and was killed")
    except OSError as exc:
        return False, f"{hook.action.script} could not be started: {exc}"
    if done.returncode != 0:
        tail = (done.stderr or "").strip().splitlines()
        return False, (f"{hook.action.script} exited {done.returncode}"
                       + (f": {tail[-1][:200]}" if tail else ""))
    return True, f"{hook.action.script} exited 0"


def _fire_steer(hook: Hook, project_dir: str,
                payload: Mapping[str, Any]) -> Tuple[bool, str]:
    """Queue a note into the captain's own review channel, and send it.

    Imported here rather than at module scope on purpose. `review_channel`
    lives under `library/dashboard/` and this is `library/tools/`, so the
    import runs against the usual direction; doing it at the point of use
    keeps that visible and keeps this module importable without the
    dashboard package. It is NOT moved: the channel is the dashboard's,
    and a store that both halves already agree on is the whole point.

    The note is sent as its OWN batch, naming only its own id. Sending
    the whole queue would post the captain's half-written drafts along
    with it.
    """
    from library.dashboard import review_channel

    step = hook.action.anchor_step or str(payload.get("step") or "")
    if not step:
        return False, ("a steer needs a step to pin the note to - declare "
                       "'anchor_step', or fire on a condition whose payload "
                       "carries 'step'")
    # A real, resolvable selector: `pipeline-view.js` renders
    # `data-step-id` on every step card and `resolveAnchor` finds it by
    # exactly this path. `normalise_anchor` refuses an empty selector and
    # that rule is satisfied, not weakened.
    anchor = {
        "selector": f'[data-step-id="{step}"]',
        "tag": "div",
        "text": "",
        "label": f"{step} ({hook.when})",
        "view": "pipeline",
        "step_id": step,
    }
    # The fingerprint already begins with the condition, so naming the
    # condition beside it printed "qa_finding_raised:
    # qa_finding_raised:subtitle_gaps" in the captain's own feed.
    text = f"{hook.action.text}\n\n[{hook.name} fired on " \
           f"{fingerprint(hook.when, payload)}]"
    try:
        note = review_channel.queue_note(project_dir, text, anchor,
                                         origin=review_channel.ORIGIN_HOOK)
        batch = review_channel.send_queued(project_dir, [note["id"]])
    except (ValueError, KeyError, OSError) as exc:
        return False, f"the note could not be queued: {exc}"
    return True, (f"note {note['id']} on {step}"
                  + (f", batch {batch['id']}" if batch else ""))


def dispatch(project_dir: str, condition: str, payload: Mapping[str, Any],
             run_id: str,
             declaration: Optional[Declaration] = None) -> List[Fire]:
    """Fire whatever this condition arms, once, within the guards.

    Returns what happened, and records the same on the collector the run
    summary reads. Never raises for a hook that failed: a hook is
    automation ON TOP of the run, and a run whose real work succeeded
    must not be failed by it. A DECLARATION that cannot be read is a
    different thing and refuses at load, before the run starts.
    """
    if condition not in CONDITIONS:
        raise HookError(
            f"unknown condition {condition!r}",
            f"a hook fires on a declared condition. Known: "
            f"{', '.join(sorted(CONDITIONS))}",
            "this is a caller bug, not a declaration bug - fix the "
            "caller to collect a declared condition"
        )

    declaration = load(project_dir) if declaration is None else declaration
    armed = [h for h in declaration.hooks
             if h.when == condition and h.matches(payload)]
    if not armed:
        return []

    print_fp = fingerprint(condition, payload)
    now = time.strftime("%Y-%m-%dT%H:%M:%S")

    # Guard 1. Checked once, for the whole dispatch: being inside a hook
    # is a property of the process, not of one hook.
    if depth():
        out = [Fire(h.name, condition, print_fp, h.action.kind, INSIDE_HOOK,
                    f"this process is inside a hook ({ENV_DEPTH}={depth()})",
                    now) for h in armed]
        _COLLECTED.extend(out)
        return out

    already = _ledger_keys(project_dir)
    results: List[Fire] = []
    for hook in armed:
        # Guard 2.
        if (run_id, hook.name, print_fp) in already:
            results.append(Fire(hook.name, condition, print_fp,
                                hook.action.kind, ALREADY_FIRED,
                                "this run already fired it on this instance",
                                now))
            continue
        # Guard 3.
        if sum(1 for f in _COLLECTED if f.outcome == FIRED) >= MAX_FIRES_PER_RUN:
            results.append(Fire(hook.name, condition, print_fp,
                                hook.action.kind, OVER_BUDGET,
                                f"the per-run budget of {MAX_FIRES_PER_RUN} "
                                f"is spent", now))
            _COLLECTED.extend(results[-1:])
            continue

        if hook.action.kind == ACTION_RUN:
            ok, detail = _fire_run(hook, project_dir, condition, payload)
        else:
            ok, detail = _fire_steer(hook, project_dir, payload)

        fire = Fire(hook.name, condition, print_fp, hook.action.kind,
                    FIRED if ok else FAILED, detail, now)
        results.append(fire)
        # Recorded whether it succeeded or failed: a hook that ran and
        # blew up has still run, and firing it again on the same instance
        # is the loop this ledger exists to stop.
        _record_fire(project_dir, {
            "run_id": run_id, "hook": hook.name, "condition": condition,
            "fingerprint": print_fp, "action": hook.action.kind,
            "outcome": fire.outcome, "detail": detail, "at": now,
            "describe": hook.describe,
        })
        already.add((run_id, hook.name, print_fp))

    for fire in results:
        if fire not in _COLLECTED:
            _COLLECTED.append(fire)
    return results


# ── The CLI half ─────────────────────────────────────────────────────

def _main(argv: Optional[Sequence[str]] = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(
        prog="python3 -m library.tools.hooks",
        description="Show the hooks in force, and what may fire them.")
    parser.add_argument("project", nargs="?", default="",
                        help="Project directory (optional)")
    parser.add_argument("--conditions", action="store_true",
                        help="Print the condition vocabulary and stop")
    args = parser.parse_args(argv)

    if args.conditions:
        print("Conditions a hook may declare:")
        for name in sorted(CONDITIONS):
            spec = CONDITIONS[name]
            print(f"  {name:<26} {spec.describe}")
            print(f"  {'':<26} identity: {', '.join(spec.identity)}")
        return 0

    project = os.path.abspath(args.project) if args.project else ""
    try:
        declaration = load(project)
    except HookError as exc:
        print(exc.render())
        return REFUSAL_EXIT_CODE
    for line in describe(declaration):
        print(line)
    print(f"  Scripts a 'run' action may name ({SCRIPTS_DIRNAME}/): "
          f"{', '.join(sorted(allowed_scripts())) or '(none)'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
