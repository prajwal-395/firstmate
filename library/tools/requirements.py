"""One owner for "may this step run", and the only vocabulary for saying no.

The defect this exists to remove
--------------------------------
Every step manifest declared `interface.preconditions` and
`interface.postconditions` as prose - `"'audio_spine' exists in state"`,
`"'rough_cut_review.passed' is true in state"`.  126 strings across 29
manifests, and **nothing evaluated one of them**.  The only consumers
were `library/schema/manifest.schema.json`, which required the FIELD to
exist, and one test asserting the substring `"parselmouth"` appeared in
step 1.05's list.

Meanwhile the real gating lived in three places that read two different
declarations: `run_scope._assert_dependencies_met` (selection time),
`run_pipeline.gather_step_inputs` (mid-run), and each step's own code.
So the document that READ like the contract was the one with no teeth,
and the contract with teeth could only ever say one thing: *a key is
present*.

That limit is the whole problem.  Measured on `b5f6cdd`, driving the
real `gather_step_inputs`:

    CASE 3 - all keys present, transcript EMPTY, review FAILED:
      NO REFUSAL. inputs = ['audio_spine', 'brand_effect', 'brand_style',
                            'rough_cut_review', 'speech_sequence']

An empty transcript and a rejected rough cut both passed the contract and
reached the step.  End to end through the runner, the rejected cut then
produced four real subtitles, and the empty transcript produced a
`subtitle_plan` with zero entries, printed `✓ Completed`, printed no
warning, and recorded a ledger entry.

So the defect is not "there is no contract".  It is **the contract could
only say a key was present, never that its content satisfied the
requirement.**

The four kinds
--------------
All four are MECHANICAL.  None encodes taste and none carries a number
this module invented (the standing ruling on thresholds - a mechanical
proxy fitted to the captain's verdicts failed to predict them, so what is
checkable here is presence, provenance from a declared list, and
buildability; coherence is not).

* ``state_key``   - a key is present.  **Auto-derived**, never hand
  written: `derive_state_keys` wraps `run_scope.prerequisites`, which is
  the same reading of `inputs[].required` + `data_mapping` that
  `gather_step_inputs` raises on.  One derivation, so the refusal and the
  crash it prevents cannot drift apart.
* ``predicate``   - a value satisfies a test.  No expression before this
  module.
* ``environment`` - the machine can do the work.  No expression before
  this module, which is the structural reason "Node.js and npx are
  available" could only ever be prose.
* ``coverage``    - the data spans what was asked for.  No expression
  before this module.

A refusal says what to run; a pass says how it passed
-----------------------------------------------------
`Satisfaction` is never a bare bool.  `SATISFIED(source)` names one of
`IN_STATE` / `RECORDED` / `SUPPLIED` / `PRODUCED_BY`, and
`UNSATISFIED(reason, ...)` carries `produced_by` - the operations that
would satisfy it.  A refusal that cannot say what to run is not a
refusal, it is a dead end.

Checked against what will EXECUTE, not against the plan
--------------------------------------------------------
`--step` and `--from` narrow the step list AFTER `run_scope.resolve` has
already agreed to the selection (`run_pipeline`, where `steps_to_run` is
built from `scope.steps_to_run`).  So the refusal was computed against
the wider set and the run executed the narrower one, and `--step
plan_subtitles` on a fresh project did not refuse - it died forty lines
later inside `gather_step_inputs` with an unhandled traceback:

    RuntimeError: Step 'plan_subtitles': data_mapping expects key
    'audio_spine' from upstream step 'mesh_spine', but it is missing from
    that step's outputs. Available keys: []

`check` therefore takes the EXECUTE set.  `run_pipeline` calls it after
`--from`/`--step` narrowing and before the first step runs, so a
prerequisite that will not be produced is a REFUSED rather than a step
failure - which matters, because a step failure is recorded and colours
`status` on every later run until that step succeeds.

Witnesses are mandatory, and that is the anti-vacuity gate
-----------------------------------------------------------
A gate that cannot fail is worse than no gate, because it reads as
coverage.  This repository has already paid for that lesson twice, and
there is a third live instance in the reel conformance verifier, where an
empty expected side silently disables a check.

So every `Requirement` must carry BOTH witnesses -
`refuting_context()` and `satisfying_context()` - and they have no
defaults, so a requirement **cannot be registered without them**.  The
registration is the gate; `tests/test_every_requirement_can_refuse.py`
only reads it.  A requirement that genuinely cannot refuse is DELETED,
never exempted.

`tests/test_no_requirement_refuses_correct_input.py` is the mirror, so
the layer cannot be vacuously strict either.

    python3 -m library.tools.requirements          # the registry
    python3 -m library.tools.requirements --kinds  # counts by kind

`tests/test_requirements.py`,
`tests/test_every_requirement_can_refuse.py`,
`tests/test_no_requirement_refuses_correct_input.py`.
"""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Mapping, Optional, Sequence, Set, Tuple

_LIBRARY_ROOT = Path(__file__).resolve().parents[1]
_REPO_ROOT = _LIBRARY_ROOT.parent


# ── Kinds ────────────────────────────────────────────────────────────

KIND_STATE_KEY = "state_key"
KIND_PREDICATE = "predicate"
KIND_ENVIRONMENT = "environment"
KIND_COVERAGE = "coverage"

KINDS: Tuple[str, ...] = (
    KIND_STATE_KEY, KIND_PREDICATE, KIND_ENVIRONMENT, KIND_COVERAGE)


# ── How a requirement was satisfied ──────────────────────────────────

IN_STATE = "in this run"
RECORDED = "recorded by a previous run"
SUPPLIED = "supplied from outside the pipeline"
PRODUCED_BY = "produced by a step in this run"
MEASURED = "measured directly"

SOURCES: Tuple[str, ...] = (IN_STATE, RECORDED, SUPPLIED, PRODUCED_BY, MEASURED)


@dataclass(frozen=True)
class Satisfaction:
    """Never a bare bool.

    A pass says HOW it passed, so a reader of `RUN-TRACEBACK.md` can tell
    a value a step produced from one the captain supplied by hand.  A
    refusal says WHAT TO RUN, because a refusal that names no producer
    leaves the operator with nowhere to go - which is exactly what the
    old mid-run RuntimeError did.
    """

    satisfied: bool
    source: str = ""
    reason: str = ""
    missing: str = ""
    produced_by: Tuple[str, ...] = ()

    @property
    def is_satisfied(self) -> bool:
        return self.satisfied

    @property
    def is_unsatisfied(self) -> bool:
        return not self.satisfied


def SATISFIED(source: str) -> Satisfaction:
    if source not in SOURCES:
        raise ValueError(
            f"{source!r} is not one of the declared satisfaction sources "
            f"{list(SOURCES)}. A pass that cannot say how it passed is "
            f"the thing this type exists to prevent.")
    return Satisfaction(satisfied=True, source=source)


def UNSATISFIED(reason: str, missing: str = "",
                produced_by: Iterable[str] = ()) -> Satisfaction:
    if not (reason or "").strip():
        raise ValueError(
            "a refusal must say why; an empty reason is not a refusal")
    return Satisfaction(satisfied=False, reason=reason, missing=missing,
                        produced_by=tuple(produced_by))


# ── What a check may consult ─────────────────────────────────────────

@dataclass(frozen=True)
class Context:
    """Everything a check may read, and nothing it may not.

    `run_set` is the set of steps that will EXECUTE - not the steps the
    selection agreed to.  See the module docstring: the two differ under
    `--step` and `--from`, and the difference is a bug this layer closes.
    """

    project_folder: str = ""
    state: Mapping = field(default_factory=dict)
    run_set: frozenset = field(default_factory=frozenset)
    recorded: Mapping = field(default_factory=dict)
    external: Mapping = field(default_factory=dict)

    overrides: frozenset = field(default_factory=frozenset)
    """Requirement names the operator explicitly overrode on the command
    line. Empty on every run that did not say so - the override is never
    reachable by default, and only a requirement that declares itself
    `overridable` can appear here at all."""

    def step_output(self, node_id: str, key: str, default=None):
        """`state["step_outputs"][node_id][key]`, or `default`."""
        outputs = (self.state.get("step_outputs") or {}).get(node_id) or {}
        return outputs.get(key, default)

    def value_for(self, node_id: str, key: str, default=None):
        """The value by any of the three routes, producer first.

        Mirrors `gather_step_inputs`' own precedence: a step that really
        ran wins, then a recorded output, then verified external state.
        """
        outputs = (self.state.get("step_outputs") or {}).get(node_id) or {}
        if key in outputs:
            return outputs[key]
        recorded = self.recorded.get(node_id) or {}
        if key in recorded:
            return recorded[key]
        if key in self.external:
            return self.external[key]
        return default


# ── A requirement ────────────────────────────────────────────────────

@dataclass(frozen=True)
class Requirement:
    """One condition, one check, and two mandatory witnesses.

    `refuting_context` and `satisfying_context` have NO DEFAULTS on
    purpose.  A requirement that cannot be constructed without them is a
    requirement that cannot be registered without being provably able to
    fail and provably able to pass.  That is the whole anti-vacuity
    design: the gate is the constructor, and the test only reads it.
    """

    name: str
    kind: str
    describe: str
    """One sentence, used VERBATIM in the refusal. Write it for the
    operator who is about to read it, not for this file."""

    produced_by: Tuple[str, ...]
    consumers: Tuple[str, ...]
    """The DAG nodes that need this. A requirement is only asked when one
    of its consumers is in the execute set."""

    check: Callable[[Context], Satisfaction]
    refuting_context: Callable[[], Context]
    satisfying_context: Callable[[], Context]

    overridable: bool = False
    """Whether an operator may deliberately proceed past this refusal.

    FALSE by default, and that default is the safety property: an
    override that applied to every requirement would be a general escape
    hatch around the whole layer, and would put back the mid-run crashes
    `state_key` exists to prevent. A requirement opts in, one at a time,
    and `--override` on one that has not opted in is REFUSED by name.

    Overriding is never silent. `evaluate` records what was overridden
    and the verdict it was overriding, and the runner writes both into
    the run's own outputs."""

    def __post_init__(self):
        if self.kind not in KINDS:
            raise ValueError(f"{self.name}: unknown kind {self.kind!r}")
        if not (self.describe or "").strip():
            raise ValueError(
                f"{self.name}: describe is used verbatim in the refusal "
                f"and must not be empty")
        if not self.consumers:
            raise ValueError(
                f"{self.name}: a requirement no step consumes is a "
                f"requirement nothing can ever ask. Delete it instead.")
        # A predicate or coverage requirement with no producer can never
        # be deferred by `_producer_will_make_it`, so it fires on EVERY
        # run including a plain full one - it is a run-blocker by
        # construction. That is not a hypothetical: `sfx.index_loads`
        # was exactly this shape and refused a fresh checkout of the
        # repository outright, which no local run could see because this
        # machine has the artifact. See DELETED.
        #
        # The empty `produced_by` is the TELL. If no step in the pipeline
        # produces the thing, it is not a pipeline prerequisite - it is
        # either the machine (an `environment` requirement, which is
        # reported rather than refused) or it is some step's own subject
        # matter, which belongs in that step's code.
        if self.kind in (KIND_PREDICATE, KIND_COVERAGE) and not self.produced_by:
            raise ValueError(
                f"{self.name}: a {self.kind} requirement must name what "
                f"produces the thing it checks. With no producer it can "
                f"never be deferred, so it refuses every run - including "
                f"a correct one on a fresh checkout. If nothing in the "
                f"pipeline produces it, this is an `environment` "
                f"requirement or it belongs in the step's own code.")
        if self.kind == KIND_ENVIRONMENT and self.produced_by:
            raise ValueError(
                f"{self.name}: no step produces a machine, so an "
                f"environment requirement must not name a producer.")

    def applies_to(self, run_set: Iterable[str]) -> bool:
        run_set = set(run_set)
        return any(c in run_set for c in self.consumers)


# ── state_key: AUTO-DERIVED, never hand written ──────────────────────

def derive_state_keys(dag: Optional[dict] = None,
                      manifests: Optional[Mapping[str, dict]] = None
                      ) -> List[Requirement]:
    """One `Requirement` per hard prerequisite the DAG already declares.

    Wraps `run_scope.prerequisites` rather than re-deriving, so this
    module cannot disagree with the refusal `run_scope` has always given
    or with the raise in `gather_step_inputs`.  `tests/test_requirements.py`
    pins that the derived set reproduces `run_scope.prerequisites`
    exactly - that assertion IS the proof this change altered no
    behaviour.
    """
    from library.tools import run_scope

    dag, manifests = _graph(dag, manifests)

    out: List[Requirement] = []
    for need in run_scope.prerequisites(dag, manifests):
        out.append(_state_key_requirement(need))
    return out


def _graph(dag: Optional[dict],
           manifests: Optional[Mapping[str, dict]]
           ) -> Tuple[dict, Mapping[str, dict]]:
    """The graph a derivation reads, and the default is THE WHOLE TREE.

    A caller that passes a dag is asking about that dag - the runner does
    exactly this so a reduced graph is judged against itself, and
    `tests/test_requirements.py::test_requirements_come_from_the_run_s_own_dag`
    pins it.

    A caller that passes NOTHING is asking about the repository, and the
    repository has more than one process since
    `library/processes/reels/` landed.  Defaulting to edit_video's graph
    alone would give every node of the reel process ZERO requirements -
    the exact shape `select_reels` was in before
    `derive_runner_injected_keys`, where an operation could not refuse
    for any reason at all (AGENTS.md 10.4, a gate that cannot fail).
    `library/tools/processes.py` owns the merge and refuses two processes
    that share a node id, because a shared id would give two steps one
    requirement name.
    """
    from library.tools import processes, run_scope

    if dag is None:
        dag = processes.merged_dag()
    if manifests is None:
        manifests = run_scope.load_manifests(dag)
    return dag, manifests


def _state_key_requirement(need) -> Requirement:
    key = need.state_key
    what = key if key else "its whole output"
    name = (f"state.{need.consumer}.{key}" if key
            else f"state.{need.consumer}.<{need.producer}>")

    def check(ctx: Context, need=need, key=key) -> Satisfaction:
        if need.producer in ctx.run_set:
            return SATISFIED(PRODUCED_BY)
        if not key:
            if need.producer in ctx.recorded:
                return SATISFIED(RECORDED)
            return UNSATISFIED(
                f"{need.consumer} needs the whole output of "
                f"{need.producer}, which is not in this run and has no "
                f"recorded output in this project",
                missing=need.producer, produced_by=(need.producer,))
        if key in (ctx.recorded.get(need.producer) or {}):
            return SATISFIED(RECORDED)
        if key in ctx.external:
            return SATISFIED(SUPPLIED)
        return UNSATISFIED(
            f"{need.consumer} needs {key} from {need.producer}, which is "
            f"not in this run and has no recorded output in this project",
            missing=key, produced_by=(need.producer,))

    def refuting(need=need) -> Context:
        return Context(run_set=frozenset(), recorded={}, external={})

    def satisfying(need=need) -> Context:
        return Context(run_set=frozenset({need.producer}))

    return Requirement(
        name=name, kind=KIND_STATE_KEY,
        describe=f"{need.consumer} needs {what} from {need.producer}",
        produced_by=(need.producer,), consumers=(need.consumer,),
        check=check, refuting_context=refuting,
        satisfying_context=satisfying)


# ── state_key: the one hard input no EDGE can carry ──────────────────
#
# `derive_state_keys` above reads EDGES, which is the whole of what
# lineage can express.  `gather_step_inputs` raises on one thing an edge
# cannot describe: an input the manifest declares REQUIRED whose producer
# is not a step at all.
#
# Measured over the tree on 2026-09-06, exactly two required manifest
# inputs have no producing edge:
#
#     scan.project_folder             a whitelisted global, never absent
#     select_reels.timeline_transcript  READ OFF DISK, and absent by default
#
# The second is the real one, and until now nothing derived a requirement
# for it - so `select_reels` had ZERO requirements while being the one
# step in the pipeline that cannot run without a file no step writes.
# The runner raised for it mid-run instead, which is exactly the crash
# this module exists to move to before the run starts.
# `tests/test_operations.py` re-measures the enumeration, so a third
# input of this shape cannot appear unnoticed.

TIMELINE_TRANSCRIPT_INPUT = "timeline_transcript"
"""The input name, which must agree with
`run_pipeline.TIMELINE_TRANSCRIPT_INPUT` - the runner is what injects it,
and two spellings would give a requirement about a key nothing supplies.
`tests/test_operations.py` pins the agreement."""


_TRANSCRIPT_WITNESS: List[str] = []


def _timeline_transcript_on_file(ctx: Context) -> Satisfaction:
    """Is the transcript on disk for this project?

    PRESENCE, not content, and deliberately: the runner reads the file
    and json-loads it without judging what is in it, so a content check
    here would refuse a run the runner accepts - vacuously strict, which
    is no more coverage than a gate that cannot fail (AGENTS.md 10.4).
    """
    from library.tools.timeline_transcript import transcript_path

    folder = ctx.project_folder
    if folder and transcript_path(folder).is_file():
        return SATISFIED(SUPPLIED)
    where = str(transcript_path(folder)) if folder else "the project"
    return UNSATISFIED(
        f"there is no timeline transcript at {where}, and NO STEP MAKES "
        f"ONE - it is written by `python3 -m library.tools."
        f"timeline_transcript <project> --write`, which needs Resolve "
        f"open on the project's own timeline. Nothing in this run will "
        f"produce it while it waits.",
        missing=TIMELINE_TRANSCRIPT_INPUT)


def _project_without_a_transcript() -> Context:
    """A real, readable directory that carries no transcript."""
    return Context(project_folder=str(_REPO_ROOT))


def _project_with_a_transcript() -> Context:
    """A project directory that really carries one.

    Built rather than faked, because the check reads the DISK: a witness
    that passed through some other route would prove a door nobody can
    open.  Created once, on first call, and only a witness calls it -
    `python3 -m library.tools.requirements` never does.
    """
    if not _TRANSCRIPT_WITNESS:
        import tempfile

        from library.tools.timeline_transcript import transcript_path

        folder = Path(tempfile.mkdtemp(prefix="requirement-witness-"))
        path = transcript_path(folder)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"segments": []}), encoding="utf-8")
        _TRANSCRIPT_WITNESS.append(str(folder))
    return Context(project_folder=_TRANSCRIPT_WITNESS[0])


def derive_runner_injected_keys(dag: Optional[dict] = None,
                                manifests: Optional[Mapping[str, dict]] = None
                                ) -> List[Requirement]:
    """The hard inputs the RUNNER supplies from outside the DAG.

    Derived, like `derive_state_keys`: the consumers are read off the
    manifests rather than listed here, so a step that starts or stops
    declaring the transcript required reaches this with nothing to
    update.  What is NAMED here is the input, because which routes the
    runner injects is the runner's own enumeration and there is one.

    `produced_by` is EMPTY and stays empty.  That is the truth - no step
    writes this file - and it is load-bearing twice over:
    `_producer_will_make_it` never defers a requirement with no producer,
    so this is asked on every run that schedules a consumer; and
    `describe_refusal` prints no "run the producers" line for it, because
    there is no step to run.  The remedy is in the reason instead.
    """
    dag, manifests = _graph(dag, manifests)

    consumers = tuple(
        node_id for node_id, manifest in sorted(manifests.items())
        if any(inp.get("name") == TIMELINE_TRANSCRIPT_INPUT
               and inp.get("required", True)
               for inp in (((manifest or {}).get("interface") or {})
                           .get("inputs") or []))
    )
    if not consumers:
        return []
    return [Requirement(
        name="timeline_transcript.on_file", kind=KIND_STATE_KEY,
        describe=("the project has a timeline transcript on file, which "
                  "no step produces"),
        produced_by=(), consumers=consumers,
        check=_timeline_transcript_on_file,
        refuting_context=_project_without_a_transcript,
        satisfying_context=_project_with_a_transcript)]


# ── environment: the machine can do the work ─────────────────────────
#
# No expression before this module, which is the structural reason these
# could only ever be prose.  Each probe is cheap and side-effect free.

def _which(binary: str) -> bool:
    return shutil.which(binary) is not None


def _importable(module: str) -> bool:
    try:
        return importlib.util.find_spec(module) is not None
    except (ImportError, ValueError):
        return False


REMOTION_DIR = _REPO_ROOT / "remotion-subtitles"


def _remotion_installed() -> bool:
    """Both halves, and the second half is the point.

    `package.json` is checked into git, so it is ALWAYS present - probing
    it alone would give a requirement that cannot refuse, which is the
    defect this layer exists to remove, reintroduced by the fix for it.
    `node_modules/` is what `npx remotion render` actually needs, and on
    a fresh worktree it is absent.
    """
    return ((REMOTION_DIR / "package.json").is_file()
            and (REMOTION_DIR / "node_modules").is_dir())


def _env_requirement(name: str, describe: str, consumers: Tuple[str, ...],
                     probe: Callable[[], bool], remedy: str,
                     force_key: str) -> Requirement:
    """An environment requirement plus its two witnesses.

    A probe reads the real machine, so its witnesses cannot be built by
    handing it different data.  `_FORCE` is the seam: a context may carry
    a forced verdict under `force_key`, which is how the witnesses -
    and only the witnesses - drive the probe both ways.  Nothing in the
    runner ever sets it.
    """

    def check(ctx: Context) -> Satisfaction:
        forced = (ctx.state or {}).get(_FORCE, {})
        ok = forced[force_key] if force_key in forced else probe()
        if ok:
            return SATISFIED(MEASURED)
        return UNSATISFIED(f"{describe} - {remedy}", missing=name)

    return Requirement(
        name=name, kind=KIND_ENVIRONMENT, describe=describe,
        produced_by=(), consumers=consumers, check=check,
        refuting_context=lambda: Context(state={_FORCE: {force_key: False}}),
        satisfying_context=lambda: Context(state={_FORCE: {force_key: True}}),
    )


_FORCE = "__requirement_probe_override__"
"""Test-only seam. A `Context` carrying this drives an environment probe
to a chosen verdict, so an environment requirement can supply both
witnesses without the test mutating the machine. The runner never writes
it, and `tests/test_requirements.py` pins that nothing in
`library/processes/` or `library/steps/` mentions it."""


_SUBTITLE_RENDERERS = ("render_subtitles", "render_motion_graphics")

_REEL_NODES = ("build_reels", "verify_reels")
"""Both nodes of `library/processes/reels`. What they SHARE is the
machine: each drives a live Resolve project, so `env.resolve_scripting`
names them together.

What they do not share is either half of the state. The approved plan is
the BUILD's, because verify grades what was placed rather than what was
planned; and the Resolve binding is the build's too, because verify takes
the project and timeline names off the build's own record - a
project.yaml edited between the two nodes must not send the verifier
somewhere else."""


def _resolve_scripting_importable() -> bool:
    """Can this machine reach DaVinci Resolve's scripting module at all?

    PRESENCE OF THE MODULE, never a connection: connecting opens a
    conversation with the captain's running application, and a
    prerequisite check must not do that to answer a question about the
    machine.  A closed Resolve is refused later, by the step, with a
    message about Resolve rather than about the installation - which is
    the right division, because they are different problems with
    different fixes.

    Two routes, because both are how the module is really found here:
    already importable, or sitting under the `Modules` directory
    `RESOLVE_SCRIPT_API` names (AGENTS.md 9).
    """
    if _importable("DaVinciResolveScript"):
        return True
    api = os.environ.get("RESOLVE_SCRIPT_API", "")
    return bool(api) and (
        Path(api) / "Modules" / "DaVinciResolveScript.py").is_file()


ENVIRONMENT: Tuple[Requirement, ...] = (
    _env_requirement(
        "env.npx",
        "npx is on PATH",
        _SUBTITLE_RENDERERS,
        lambda: _which("npx"),
        "install Node.js, or put npx on PATH",
        "npx"),
    _env_requirement(
        "env.remotion_installed",
        "the Remotion project in remotion-subtitles/ is installed",
        _SUBTITLE_RENDERERS,
        _remotion_installed,
        "run `npm install` in remotion-subtitles/ - package.json is in "
        "git and is always present, so it is node_modules/ that is "
        "missing",
        "remotion"),
    _env_requirement(
        "env.parselmouth",
        "praat-parselmouth is importable",
        ("prosody_analysis",),
        lambda: _importable("parselmouth"),
        "pip install praat-parselmouth",
        "parselmouth"),
    _env_requirement(
        "env.sfx_library",
        "the SFX library directory is readable and has profiles/",
        ("validate_sfx_library",),
        lambda: _sfx_library_present(),
        "set PIPELINE_SFX_LIBRARY to the library's absolute path",
        "sfx_library"),
    _env_requirement(
        "env.resolve_scripting",
        "DaVinci Resolve's scripting module can be found",
        _REEL_NODES,
        _resolve_scripting_importable,
        "set RESOLVE_SCRIPT_API to the installation's "
        "Developer/Scripting directory and RESOLVE_SCRIPT_LIB to its "
        "libfusionscript.dylib (AGENTS.md 9). Both reel nodes drive a "
        "LIVE Resolve project and neither can start without it",
        "resolve_scripting"),
)


def _sfx_library_present() -> bool:
    from library.tools.sfx_library import resolve_library_path
    path = resolve_library_path("")
    if not path:
        return False
    root = Path(path)
    return root.is_dir() and (
        (root / "profiles").is_dir() or (root / "sfx_index.json").is_file())


# ── predicate: a value satisfies a test ──────────────────────────────

def _prosody_speech_regions(ctx: Context) -> Satisfaction:
    """1.05's manifest said `'temporal_index' exists in state WITH SPEECH
    REGIONS`.  The existence half was already enforced by the edge; the
    trailing clause was a content requirement nothing read."""
    indices = ctx.value_for("temporal_index", "full_indices")
    if indices is None:
        return UNSATISFIED(
            "temporal_index has recorded no full_indices, so there are no "
            "speech regions to measure prosody over",
            missing="full_indices", produced_by=("temporal_index",))
    entries = indices.values() if isinstance(indices, dict) else indices
    for entry in entries or []:
        if isinstance(entry, dict) and entry.get("speech_regions"):
            return SATISFIED(IN_STATE)
    return UNSATISFIED(
        "no clip in temporal_index carries a non-empty speech_regions "
        "list, so prosody has nothing to measure",
        missing="speech_regions", produced_by=("temporal_index",))


def _music_track_path(ctx: Context) -> Satisfaction:
    """2.06's manifest said `'music_selection' exists in state WITH A
    VALID TRACK PATH`.  Same shape: the existence half was enforced and
    the content half was prose.

    The key order below mirrors `step_2_06_music_analysis/step.py:43-52`
    exactly, so the refusal and the step cannot disagree about which key
    names the track; `tests/test_requirements.py` pins that they agree.
    """
    selection = ctx.value_for("music_selection", "music_selection")
    if not isinstance(selection, dict):
        return UNSATISFIED(
            "music_selection recorded no selection object",
            missing="music_selection", produced_by=("music_selection",))
    path = resolve_track_path(selection)
    if not path:
        return UNSATISFIED(
            "music_selection names no track path under any of "
            "track_path, audio_path, file_path, path or tracks[0]",
            missing="track_path", produced_by=("music_selection",))
    if not os.path.exists(path):
        return UNSATISFIED(
            f"music_selection names {path}, which is not on disk",
            missing=path, produced_by=("music_selection",))
    return SATISFIED(MEASURED)


TRACK_PATH_KEYS: Tuple[str, ...] = (
    "track_path", "audio_path", "file_path", "path")
"""The order `step_2_06_music_analysis/step.py` reads them in."""


def resolve_track_path(selection: Mapping) -> str:
    """The track a `music_selection` names, by the order 2.06 reads.

    PUBLIC because `external_inputs._check_music_selection` needs the
    same reading: a hand-placed music spine has to be checked against the
    file the step will really open, and two readings of "which key names
    the track" is the key-name mismatch class (AGENTS.md 10.1) with a
    silent failure at the end of it.
    """
    for key in TRACK_PATH_KEYS:
        value = selection.get(key)
        if value:
            return str(value)
    tracks = selection.get("tracks")
    if isinstance(tracks, list) and tracks and isinstance(tracks[0], dict):
        first = tracks[0]
        return str(first.get("audio_path") or first.get("track_path") or "")
    return ""


def _rough_cut_approved(ctx: Context) -> Satisfaction:
    """The rough cut passed its own mechanical review.

    Declared as prose by four planning steps - 4.01, 4.02, 4.03 and
    4.04 all carried `"'rough_cut_review.passed' is true in state"` - and
    read by nothing: `grep -rn rough_cut_review --include=*.py library/`
    found no read of `.passed` anywhere. Measured, a run against a
    recorded review with `passed: false` planned four real subtitles.

    A full run already stops at a failed review, because
    `step_3_03_review_rough_cut/step.py:386-387` exits 1. So the hole was
    only ever on RE-ENTRY - a `--resume`, a `--from`, a scoped `--only`,
    anything that reads the recorded review instead of re-running 3.03.
    There it proceeded in silence. That is a hole, not a designed
    workflow (firstmate's ruling, 2026-09-05,
    `data/decisions/rough-cut-gate.md`).

    The value is MECHANICAL, so this encodes no taste and invents no
    threshold: `step_3_03/step.py:293-301` computes it as `all()` of six
    deterministic checks.

    A MISSING `passed` is not a FAILED one. A review supplied through
    `external_inputs`, or built from a hand-made timeline, need not carry
    the field; reporting an absent measurement as a failed one is its own
    defect (AGENTS.md 10.3). The two refusals say different things.
    """
    review = ctx.value_for("review_rough_cut", "rough_cut_review")
    if not isinstance(review, dict):
        return UNSATISFIED(
            "no rough_cut_review is on file, so nothing says whether the "
            "cut this would be planned against was ever reviewed",
            missing="rough_cut_review", produced_by=("review_rough_cut",))
    if "passed" not in review:
        return UNSATISFIED(
            "rough_cut_review carries no 'passed' field, so whether the "
            "cut was approved was never measured - which is not the same "
            "as it having failed",
            missing="rough_cut_review.passed",
            produced_by=("review_rough_cut",))
    if review["passed"] is True:
        return SATISFIED(IN_STATE)

    reasons = review.get("rejection_reasons") or []
    detail = ("; ".join(str(r) for r in reasons[:3])
              if reasons else "no reasons recorded")
    return UNSATISFIED(
        f"the rough cut was REJECTED by its own mechanical review, so "
        f"planning against it would decorate a cut that does not work: "
        f"{detail}",
        missing="rough_cut_review.passed",
        produced_by=("review_rough_cut",))


def _review(passed, **extra) -> Context:
    review = {"passed": passed}
    review.update(extra)
    return Context(state={"step_outputs": {
        "review_rough_cut": {"rough_cut_review": review}}})


PREDICATES: Tuple[Requirement, ...] = (
    Requirement(
        name="rough_cut.approved", kind=KIND_PREDICATE,
        describe="the rough cut passed its own mechanical review",
        produced_by=("review_rough_cut",),
        consumers=("plan_subtitles", "plan_transitions", "plan_vfx",
                   "plan_sfx"),
        check=_rough_cut_approved,
        # The ONE overridable requirement, and it is overridable because
        # the alternative readings are both worse. Refusing outright
        # removes the iterate-on-a-rejected-cut loop the captain
        # plausibly wants; proceeding silently is the defect itself.
        # Refuse-with-override keeps the loop and removes the silence.
        overridable=True,
        refuting_context=lambda: _review(
            False, rejection_reasons=["timeline continuity: gap at 12.4s"]),
        satisfying_context=lambda: _review(True),
    ),
    Requirement(
        name="prosody.speech_regions", kind=KIND_PREDICATE,
        describe="temporal_index carries speech regions to measure prosody over",
        produced_by=("temporal_index",), consumers=("prosody_analysis",),
        check=_prosody_speech_regions,
        refuting_context=lambda: Context(
            state={"step_outputs": {"temporal_index": {
                "full_indices": {"clip_001": {"speech_regions": []}}}}}),
        satisfying_context=lambda: Context(
            state={"step_outputs": {"temporal_index": {
                "full_indices": {"clip_001": {
                    "speech_regions": [{"start": 0.0, "end": 1.0}]}}}}}),
    ),
    Requirement(
        name="music.track_on_disk", kind=KIND_PREDICATE,
        describe="the selected music track names a file that exists",
        produced_by=("music_selection",), consumers=("music_analysis",),
        check=_music_track_path,
        refuting_context=lambda: Context(
            state={"step_outputs": {"music_selection": {
                "music_selection": {"track_path":
                                    "/nonexistent/no-such-track.wav"}}}}),
        satisfying_context=lambda: Context(
            state={"step_outputs": {"music_selection": {
                "music_selection": {"track_path": str(
                    Path(__file__).resolve())}}}}),
    ),
)


# ── state_key, hand written: what the project brought with it ────────
#
# `derive_state_keys` reads EDGES and `derive_runner_injected_keys` reads
# the ONE input the runner injects from outside the DAG.  Neither can see
# a condition that is about the PROJECT rather than about lineage: a file
# the captain ruled on, a binding the project.yaml declares.
#
# Both requirements below are `state_key` with an EMPTY `produced_by`,
# the same shape as `timeline_transcript.on_file`, and the emptiness is
# load-bearing for the same two reasons: `_producer_will_make_it` never
# defers a requirement with no producer, so each is asked on every run
# that schedules a consumer; and neither `describe_refusal` nor
# `Operation._teach` prints a "run the producer first" line, because
# there is no step to run.  The remedy is in the reason instead.
#
# They are NOT predicates.  A predicate must name a producer
# (`Requirement.__post_init__`) so that a run scheduling it can defer -
# and naming one here would be a confident wrong answer.  `select_reels`
# writes a plan PROPOSED; the APPROVAL is the captain's act and no step
# in any process makes one.  Nothing writes a project.yaml either.

def _reel_plan_approved(ctx: Context) -> Satisfaction:
    """Is there a reel plan on file with at least one APPROVED moment?

    The captain's rule, already mechanical in
    `library/tools/reel_proposal.py`: *the pipeline proposes and the
    captain approves; nothing is built before that*.  A build against a
    plan where every moment is still `proposed` would overrule them, and
    `reel_proposal.for_building` refuses each moment individually - but
    only once the build has already connected to Resolve, deleted the
    existing reel timelines and started work.  This moves the same
    refusal to before the run.

    BOTH halves are refused separately, because they have different
    remedies: no plan at all means run the selector and publish, while a
    plan nobody has ruled on means go and rule on it.
    """
    from library.tools.reel_proposal import (
        Approval, ProposalError, proposal_path, read_proposal,
    )

    folder = ctx.project_folder
    if not folder:
        return UNSATISFIED(
            "no project folder was given, so there is nowhere to read a "
            "reel plan from",
            missing="reel_proposals_v2.json")

    path = proposal_path(folder)
    if not path.is_file():
        return UNSATISFIED(
            f"there is no reel plan at {path}. Run step 3.04 "
            f"(`--with select_reels`, which needs a timeline transcript), "
            f"then publish its chosen moments for review with "
            f"`python3 manage_project.py propose-reels <project>`.",
            missing="reel_proposals_v2.json")

    try:
        moments = read_proposal(str(path))
    except (ProposalError, ValueError, OSError) as broken:
        return UNSATISFIED(
            f"the reel plan at {path} cannot be read: {broken}",
            missing="reel_proposals_v2.json")

    approved = [m for m in moments if m.approval is Approval.APPROVED]
    if not approved:
        ruled = sum(1 for m in moments if m.approval is Approval.REJECTED)
        return UNSATISFIED(
            f"the reel plan at {path} holds {len(moments)} moment(s), "
            f"{ruled} rejected and NOT ONE APPROVED. Approval is the "
            f"captain's act and no step makes one: review the moments on "
            f"the dashboard, or set a moment's \"approval\" to "
            f"\"approved\" in that file. Building a proposed moment would "
            f"overrule them.",
            missing="reel_proposals_v2.json:approval")
    return SATISFIED(SUPPLIED)


_APPROVED_PLAN_WITNESS: List[str] = []


def _project_without_an_approved_reel() -> Context:
    """A real, readable directory carrying no reel plan at all."""
    return Context(project_folder=str(_REPO_ROOT))


def _project_with_an_approved_reel() -> Context:
    """A project directory that really carries an approved plan.

    BUILT rather than faked, for the same reason
    `_project_with_a_transcript` is: the check reads the DISK through
    `reel_proposal`'s own reader, so a witness that passed through some
    other route would prove a door nobody can open.  Created once, on
    first call, and only a witness calls it.
    """
    if not _APPROVED_PLAN_WITNESS:
        import tempfile

        from library.tools.reel_proposal import (
            Approval, ReelMoment, proposal_path, write_proposal,
        )

        folder = Path(tempfile.mkdtemp(prefix="requirement-witness-reel-"))
        moment = ReelMoment(
            number=1, slug="a-witness", reason="a moment the captain ruled on",
            timeline_start=10.0, timeline_end=40.0,
            approval=Approval.APPROVED)
        write_proposal(proposal_path(folder), [moment],
                       {"derived_from": {"duration_seconds": 60.0}})
        _APPROVED_PLAN_WITNESS.append(str(folder))
    return Context(project_folder=_APPROVED_PLAN_WITNESS[0])


def _resolve_timeline_binding(ctx: Context) -> Satisfaction:
    """Does the project name the Resolve project AND its master timeline?

    A reel is cut FROM a master timeline that already exists, and a
    Resolve project is addressed by its EXACT listed name - a near match
    lands on another project (AGENTS.md 5).  Read through
    `timeline_ingest.resolve_binding`, which is the one reader of that
    declaration, so a refusal here and the address the build uses cannot
    disagree.
    """
    from library.tools.timeline_ingest import resolve_binding

    folder = ctx.project_folder
    if not folder:
        return UNSATISFIED(
            "no project folder was given, so no project.yaml can be read "
            "for a Resolve binding",
            missing="resolve.project_name")

    project_name, timeline_name = resolve_binding(folder)
    missing = [name for name, value in
               (("project_name", project_name),
                ("timeline_name", timeline_name)) if not value]
    if missing:
        return UNSATISFIED(
            f"{folder}/project.yaml declares no {' and no '.join(missing)} "
            f"under `resolve:`, so there is no master timeline to cut a "
            f"reel out of. Add the EXACT name Resolve lists - a prefix or "
            f"substring lands on a different project.",
            missing=f"resolve.{missing[0]}")
    return SATISFIED(SUPPLIED)


_BINDING_WITNESS: List[str] = []


def _project_without_a_resolve_binding() -> Context:
    """A real directory whose project.yaml names no binding.

    The repository root has no project.yaml at all, which is the same
    answer for the same reason - see `resolve_binding`.
    """
    return Context(project_folder=str(_REPO_ROOT))


def _project_with_a_resolve_binding() -> Context:
    """A project.yaml that really declares both names."""
    if not _BINDING_WITNESS:
        import tempfile

        folder = Path(tempfile.mkdtemp(prefix="requirement-witness-bind-"))
        (folder / "project.yaml").write_text(
            "name: witness\n"
            "resolve:\n"
            "  project_name: Witness Project\n"
            "  timeline_name: Witness Timeline\n",
            encoding="utf-8")
        _BINDING_WITNESS.append(str(folder))
    return Context(project_folder=_BINDING_WITNESS[0])


EXTERNAL_STATE: Tuple[Requirement, ...] = (
    Requirement(
        name="reel_plan.approved", kind=KIND_STATE_KEY,
        describe=("the project carries a reel plan with at least one "
                  "moment the captain APPROVED"),
        produced_by=(), consumers=("build_reels",),
        check=_reel_plan_approved,
        refuting_context=_project_without_an_approved_reel,
        satisfying_context=_project_with_an_approved_reel),
    Requirement(
        name="resolve.timeline_binding", kind=KIND_STATE_KEY,
        describe=("the project.yaml names the Resolve project and the "
                  "master timeline a reel is cut out of"),
        # BUILD only. `verify_reels` takes the project and timeline
        # names off the build's own record instead - the build wrote into
        # a named project and that is what must be graded, so a
        # project.yaml edited between the two nodes must not send the
        # verifier somewhere else.
        produced_by=(), consumers=("build_reels",),
        check=_resolve_timeline_binding,
        refuting_context=_project_without_a_resolve_binding,
        satisfying_context=_project_with_a_resolve_binding),
)


# ── coverage: the data spans what was asked for ──────────────────────

def _spine_word_timings(ctx: Context) -> Satisfaction:
    """Every speech and hook block carries word timings.

    This is the requirement `plan_subtitles` was REALLY standing on when
    it declared `speech_sequence` required - a value its `main()` never
    reads.  4.01 gets its timings off the SPINE
    (`step.py:517-533 _require_word_timestamps`), and it already refuses
    correctly when they are absent:

        exit 1  {"error": "Spine block 0 (speech) has empty
        word_timestamps - the spine contract requires word timings on
        every speech block, so subtitles cannot be timed"}

    So nothing new is checked here.  What changes is WHERE the refusal
    fires: `spine_contract.validate_spine_blocks` is the same function
    `external_inputs._check_audio_spine` already delegates to, and moving
    it into the cascade turns a mid-run step failure - which is recorded
    and colours `status` on every later run - into a REFUSED.
    """
    from library.tools import spine_contract

    spine = ctx.value_for("mesh_spine", "audio_spine")
    if not isinstance(spine, dict):
        return UNSATISFIED(
            "no audio_spine is available, so there is no transcript to "
            "time captions against",
            missing="audio_spine", produced_by=("mesh_spine",))
    blocks = spine.get("structure")
    if not isinstance(blocks, list) or not blocks:
        return UNSATISFIED(
            "audio_spine.structure is empty, so there is nothing to "
            "caption - an empty plan is not a captioned video",
            missing="audio_spine.structure", produced_by=("mesh_spine",))
    try:
        spine_contract.validate_spine_blocks(blocks)
    except spine_contract.SpineContractError as exc:
        return UNSATISFIED(
            f"the spine does not carry usable word timings: {exc}",
            missing="word_timestamps",
            produced_by=("temporal_index", "speech_sequence", "mesh_spine"))
    return SATISFIED(IN_STATE)


def _spine(with_words: bool) -> dict:
    words = [{"word": "hello", "source_start": 0.0, "source_end": 0.4}]
    return {"structure": [{
        "position": 0, "block_type": "speech", "clip_id": "clip_001",
        "source_start": 0.0, "source_end": 0.4,
        "timeline_start": 0.0, "timeline_end": 0.4,
        "word_timestamps": words if with_words else [],
        "alignment_method": "whisperx_forced_alignment" if with_words else None,
        "content": {"text": "hello"},
    }]}


COVERAGE: Tuple[Requirement, ...] = (
    Requirement(
        name="spine.word_timings", kind=KIND_COVERAGE,
        describe=("every speech and hook block in the spine carries word "
                  "timings"),
        produced_by=("temporal_index", "speech_sequence", "mesh_spine"),
        consumers=("plan_subtitles",),
        check=_spine_word_timings,
        refuting_context=lambda: Context(
            state={"step_outputs": {"mesh_spine": {
                "audio_spine": _spine(with_words=False)}}}),
        satisfying_context=lambda: Context(
            state={"step_outputs": {"mesh_spine": {
                "audio_spine": _spine(with_words=True)}}}),
    ),
)


# ── The gap: one requirement the captain has not authorised ──────────

UNAUTHORISED: Dict[str, str] = {}
"""Requirements held pending a captain decision. Empty.

`rough_cut.approved` lived here and is now BUILT. The decision
(firstmate, 2026-09-05, `data/decisions/rough-cut-gate.md`) was
REFUSE WITH A DELIBERATE OVERRIDE, and the reasoning is worth keeping
next to the code: a full run already stops at a failed review because
`step_3_03` exits 1, so the gap was only ever on RE-ENTRY, where it
proceeded in silence. That is a hole, not a designed workflow. Refusing
outright would have removed the iterate-on-a-rejected-cut loop; refusing
with an override keeps the loop and removes the silence, which was the
actual defect.

Keep this table. A requirement whose SHAPE is settled but whose
BEHAVIOUR is a captain call belongs here rather than being guessed at,
and shipping one in the wrong direction is not a neutral default -
deleting `rough_cut_review` would have decided this question just as
firmly as enforcing it."""
# ── Declarations that were DELETED rather than converted ─────────────
#
# Six of the prose preconditions named a key the DAG never routes.
# Converting them verbatim would have refused a CORRECT run, which is a
# vacuously STRICT gate - no more coverage than one that cannot fail.
# `tests/test_no_requirement_refuses_correct_input.py` is what catches
# that class, and it is why each of these is recorded here rather than
# silently dropped: a deleted requirement with no reason reads as an
# oversight.

DELETED: Dict[str, str] = {
    "validate_sfx_library: FAISS index has been built":
        "NOT A PREREQUISITE - IT IS THE STEP'S OWN SUBJECT MATTER, and "
        "this one shipped broken and was caught by the clean room. It "
        "was registered as `sfx.index_loads`, a predicate on "
        "`validate_sfx_library`, and it REFUSED A FRESH CHECKOUT OF THE "
        "REPOSITORY OUTRIGHT: on a machine where the index has not been "
        "built the run was refused before it started, so a new clone "
        "could not run the pipeline at all. No local run could see it, "
        "because a developer machine has the artifact.\n"
        "\n"
        "Three things were wrong with it, and the third is the general "
        "lesson. (1) 0.01's whole JOB is to check the index, so making "
        "the index its prerequisite is circular - it stopped the run "
        "before the step that would diagnose the problem could say so. "
        "(2) `step_0_01_validate_sfx_library/step.py:74-80` already "
        "performs exactly this check and reports it better, naming the "
        "command that fixes it. (3) `produced_by` was EMPTY, because no "
        "step in the pipeline produces the index - and that emptiness is "
        "the tell. A predicate with no producer can never be deferred, "
        "so it fires on every run: it is a run-blocker by construction. "
        "`Requirement.__post_init__` now refuses to register one, so "
        "this class cannot come back.\n"
        "\n"
        "What a step needs FROM the library still binds normally: 4.04 "
        "reads the catalogue over a DAG edge from 0.01, which is an "
        "auto-derived `state_key` requirement. And `env.sfx_library` "
        "still REPORTS a missing library at second zero without "
        "refusing.",
    "semantic_analysis: 'clip_catalog' exists in state":
        "STALE. 1.03 declares only `raw_footage_files` and no edge routes "
        "the catalog to it. The step has never read a catalog.",
    "color_grade: 'brand_template' exists in state (optional)":
        "WRONG AS A STATE KEY. `gather_step_inputs` deliberately does not "
        "broadcast `brand_template`: `state['brand_template']` holds a "
        "REFERENCE (a name or a path), and the resolved template reaches "
        "the step as `brand_style`/`brand_effect`. A requirement on the "
        "raw key would refuse every run.",
    "color_grade: 'project_folder' exists in state (optional)":
        "Injected global, never edge-routed, and the prose marked it "
        "optional. Nothing to enforce.",
    "review_rough_cut / color_grade: 'b_roll_interjections' exists in state":
        "CONTRADICTS THE MANIFEST. Both steps declare "
        "`b_roll_interjections` OPTIONAL, and `input_contract."
        "optional_but_refused()` is empty, so neither step's code refuses "
        "without it. A cut with no cutaways is a legitimate run; "
        "requiring the key would refuse it. AGENTS.md 10.5's line "
        "applies - [] is the absence of decoration, not a missing "
        "requirement.",
    "object_segmentation: 'raw_footage_files' / 'clip_catalog' exist in state":
        "THE STEP IS UNWIRED (AGENTS.md 3) - it has a directory and no "
        "DAG node, so it never runs and these can never be checked. A "
        "requirement nothing can ask is the vacuous case; the step's own "
        "`unwired_reason` is where this belongs.",
}


# ── The registry ─────────────────────────────────────────────────────

HAND_WRITTEN: Tuple[Requirement, ...] = (
    ENVIRONMENT + PREDICATES + EXTERNAL_STATE + COVERAGE)


def all_requirements(dag: Optional[dict] = None,
                     manifests: Optional[Mapping[str, dict]] = None
                     ) -> List[Requirement]:
    """Auto-derived `state_key` requirements plus the hand-written ones.

    Two derivations, both auto: the EDGES (`derive_state_keys`) and the
    one hard input no edge can carry (`derive_runner_injected_keys`).
    """
    return (list(derive_state_keys(dag, manifests))
            + list(derive_runner_injected_keys(dag, manifests))
            + list(HAND_WRITTEN))


def registry() -> Tuple[Requirement, ...]:
    """The hand-written registry alone - what the anti-vacuity tests walk.

    The derived half is walked separately, because its witnesses are
    generated from the DAG rather than authored, and a test that mixed
    the two would report 150-odd trivially-refutable rows and hide the
    ten that were written by hand.
    """
    return HAND_WRITTEN


# ── Asking ───────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Unmet:
    requirement: Requirement
    satisfaction: Satisfaction


@dataclass(frozen=True)
class Overridden:
    """A refusal the operator deliberately proceeded past.

    This exists so that proceeding is never the same event as passing.
    The requirement REFUSED; a person said go anyway; and the verdict it
    refused with is carried here verbatim so the run's own outputs can
    say WHAT was overridden rather than merely that something was.
    """

    requirement: Requirement
    satisfaction: Satisfaction

    def as_record(self) -> Dict[str, str]:
        """The durable form, for the run's own outputs."""
        return {
            "requirement": self.requirement.name,
            "kind": self.requirement.kind,
            "describe": self.requirement.describe,
            "refused_because": self.satisfaction.reason,
            "needed_by": ", ".join(self.requirement.consumers),
        }


class OverrideError(ValueError):
    """An override that names nothing, or names something closed."""


def assert_overrides_are_real(names: Iterable[str],
                              requirements: Optional[Sequence[Requirement]]
                              = None) -> None:
    """Refuse an override before the run, by name.

    Two ways to be wrong, and both are refused rather than ignored:
    naming a requirement that does not exist (a typo silently disabling
    nothing is still a lie about what the run did), and naming one that
    has not opted in - which is what stops `--override` becoming a
    general way around the layer.
    """
    pool = list(requirements if requirements is not None
                else all_requirements())
    by_name = {r.name: r for r in pool}
    for name in names:
        requirement = by_name.get(name)
        if requirement is None:
            overridable = sorted(r.name for r in pool if r.overridable)
            raise OverrideError(
                f"--override {name!r} names no requirement. "
                f"Overridable: {', '.join(overridable) or 'none'}.")
        if not requirement.overridable:
            raise OverrideError(
                f"--override {name!r} is refused: that requirement is "
                f"not overridable. It exists to stop a run that cannot "
                f"work, and proceeding past it would move the failure "
                f"into the run instead of removing it.")


@dataclass(frozen=True)
class Verdict:
    """What the layer decided, in one object.

    `unmet` refuses the run. `overridden` does NOT - but it is returned
    rather than discarded, because an override the run does not record
    is exactly the silence this gate was built to remove.
    """

    unmet: List[Unmet] = field(default_factory=list)
    overridden: List[Overridden] = field(default_factory=list)


def evaluate(run_set: Iterable[str],
             context: Context,
             requirements: Optional[Sequence[Requirement]] = None
             ) -> Verdict:
    """Every requirement a step in `run_set` needs, refused and overridden.

    `run_set` is what will EXECUTE. Under `--step` and `--from` that is
    narrower than the selection `run_scope` agreed to, and asking the
    wrong one is the defect described in the module docstring.
    """
    run_set = set(run_set)
    context = (context if context.run_set == frozenset(run_set)
               else Context(project_folder=context.project_folder,
                            state=context.state,
                            run_set=frozenset(run_set),
                            recorded=context.recorded,
                            external=context.external))
    pool = (requirements if requirements is not None
            else all_requirements())
    result = Verdict()
    for req in pool:
        if not req.applies_to(run_set):
            continue
        if _producer_will_make_it(req, run_set):
            continue
        verdict = req.check(context)
        if not verdict.is_unsatisfied:
            continue
        # An override does not make the requirement pass. It refused,
        # and a person said proceed anyway - so it is moved onto the
        # record rather than deleted from it.
        if req.overridable and req.name in (context.overrides or frozenset()):
            result.overridden.append(Overridden(req, verdict))
        else:
            result.unmet.append(Unmet(req, verdict))
    return result


def check(run_set: Iterable[str],
          context: Context,
          requirements: Optional[Sequence[Requirement]] = None
          ) -> List[Unmet]:
    """What REFUSES the run. Overridden requirements are not here.

    Kept as the narrow reading because most callers only ask "may this
    run start". A caller that must also RECORD what was overridden - the
    runner - calls `evaluate` and reads both halves.
    """
    return evaluate(run_set, context, requirements).unmet


def _producer_will_make_it(req: Requirement, run_set: Set[str]) -> bool:
    """A requirement whose producer runs FIRST is not asked before the run.

    This is the same three-way satisfaction the `state_key` kind already
    had - produced in this run, recorded by a previous one, or supplied
    from outside - applied to the kinds that inspect a VALUE rather than
    a key's presence.

    Without it the layer is vacuously STRICT, which is no more coverage
    than a gate that cannot fail. A plain full run schedules
    `temporal_index` and then `prosody_analysis`; asking "does
    temporal_index carry speech regions" BEFORE `temporal_index` has run
    refuses a correct run for a value that does not exist yet and is not
    supposed to. Measured: it refused `full_auto` on a mock project with
    every step selected.

    So a predicate or coverage requirement is asked only when its value
    must ALREADY be on file - a scoped re-entry, which is the case these
    requirements exist for. When the producer is in the run, the producer
    owns the value, and its own step refuses if it cannot make one.

    Environment requirements are exempt: no step produces a machine, so
    `produced_by` is empty and this never fires for them.
    """
    if req.kind == KIND_ENVIRONMENT:
        return False
    if not req.produced_by:
        return False
    return all(producer in run_set for producer in req.produced_by)


def describe_refusal(unmet: Sequence[Unmet]) -> List[str]:
    """The refusal text, in the shape `run_scope` already prints."""
    if not unmet:
        return []
    lines = ["This selection cannot run. Refusing before the run starts.", ""]

    # Two groups, because they have two different remedies and mixing
    # them makes both unreadable: state is fixed by running a producer,
    # a machine is fixed by installing something.
    state_side = [e for e in unmet
                  if e.requirement.kind != KIND_ENVIRONMENT]
    machine_side = [e for e in unmet
                    if e.requirement.kind == KIND_ENVIRONMENT]

    for entry in state_side:
        lines.append(f"  {entry.satisfaction.reason}")

    producers = sorted({p for e in state_side
                        for p in e.satisfaction.produced_by})
    if producers:
        lines += [
            "",
            "Either:",
            f"  - run the producers once so their output is on file: "
            f"--only {' --only '.join(producers)}",
            f"  - or skip the steps that need them",
        ]

    if machine_side:
        if state_side:
            lines.append("")
        lines.append("This machine cannot do the work as it stands:")
        for entry in machine_side:
            consumers = ", ".join(entry.requirement.consumers)
            lines.append(f"  {entry.satisfaction.reason}")
            lines.append(f"      needed by: {consumers}")
    return lines


# ── The survey ───────────────────────────────────────────────────────

def render(requirements: Optional[Sequence[Requirement]] = None) -> List[str]:
    reqs = list(requirements if requirements is not None
                else all_requirements())
    width = max((len(r.name) for r in reqs), default=10)
    lines = []
    for kind in KINDS:
        rows = [r for r in reqs if r.kind == kind]
        if not rows:
            continue
        lines.append(f"\n{kind} ({len(rows)})")
        for r in rows:
            lines.append(f"  {r.name:<{width}}  {r.describe}")
    return lines


def main(argv=None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--kinds", action="store_true",
                        help="counts by kind only")
    args = parser.parse_args(argv)

    reqs = all_requirements()
    if args.kinds:
        for kind in KINDS:
            print(f"  {kind:<12} {sum(1 for r in reqs if r.kind == kind)}")
        print(f"  {'TOTAL':<12} {len(reqs)}")
    else:
        for line in render(reqs):
            print(line)
    print(f"\n{len(UNAUTHORISED)} requirement(s) held pending a captain "
          f"decision:")
    for name in sorted(UNAUTHORISED):
        print(f"  {name}")
    print(f"\n{len(DELETED)} prose precondition group(s) deleted rather "
          f"than converted - see requirements.DELETED for why.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
