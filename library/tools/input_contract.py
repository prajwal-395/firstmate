"""One question, asked of every declared input: WHO REFUSES when it is absent.

The defect this exists to catch
-------------------------------
`compile_manifest` declared `transition_spec`, `enhancement_spec`,
`sfx_spec` and `color_grade_spec` REQUIRED while its own code called
three of them "optional enhancement specs" and read them with defaults.
One of the two was lying, and because `required` is what
`run_pipeline.gather_step_inputs` raises on and what
`library/tools/run_scope.py` derives a refusal from, the stricter of the
two won: a rough cut could not skip four planners it did not need, at a
measured cost of 446.5s on 001 (#260).

That is one instance of a class, so this module reads the whole class.

The two declarations, and the one question
------------------------------------------
A step declares `interface.inputs[].required`.  Its code does something
when the value is absent.  The question is not "does the code use
`.get()`" - almost every step in this pipeline does, and a `.get()` whose
default is unreachable is harmless.  The question is **who refuses**:

* `runner` - the input arrives on a DAG edge and is declared required, so
  `gather_step_inputs` raises before the step's code runs at all.  The
  step's default is dead code on that route.  The declaration is the
  enforcement.
* `step` - the step's own code refuses, and the evidence is a file and a
  line: a direct subscript, a `require_keys` call, or a guard that
  raises or exits.  This is the only enforcement a NON-edge input can
  have, because nothing raises on the way in.
* `nobody` - declared required and neither of the above.  A requirement
  nothing enforces.

And the mirror, for a declared-OPTIONAL input: the step's code must NOT
refuse, or a run that legitimately omits it dies inside the step.

Warrant is a different question from enforcement
------------------------------------------------
That a requirement is ENFORCED does not make it TRUE.  `compile_manifest`
was enforced perfectly and still wrong.  Establishing the truth of a
requirement means running the step without the input and looking at what
comes out, which this module does not do - `tests/test_compile_manifest_
without_the_decoration.py` does it for the step the captain's case runs
through.  What this module adds is the third fact that makes an
unwarranted requirement visible: whether anything CONSUMES the input at
all.  An input that reaches neither the step's code nor its prompt is a
requirement with no consumer.

Reaching the prompt is read off `context_fields`, the same allow-list
`run_pipeline.project_step_context` applies (AGENTS.md section 10.1): a
step declaring none is handed every byte it was routed, so every input
reaches its prompt.

The step that has no prompt at all
----------------------------------
That reading had one silent hole, and the very next task fell in it.
A step with no `handoff.md` declares no `context_fields` because there
is no prompt to project, and reading that absence as "handed every
byte" made `prompt_reads` True for every input of all fifteen
prompt-less steps in the DAG.  So `render_motion_graphics` declared
`creative_direction` and `enhancement_spec` REQUIRED, the DAG routed
both, `generate_motion_props` read neither, and the survey called them
consumed (#330).

A guard with a silent hole is worse than a known gap, because the
fields inside it read as verified.  Two things close this one:

* `prompt_reads` is False for a step with no prompt, asked of
  `run_pipeline.get_step_implementation` rather than of a list kept
  here;
* and because that step's CODE is then the only consumer it can have,
  "the code names the key" is no longer enough.  `trace_step_values`
  asks whether the value the key yields REACHES A USE - the two motion-
  graphics inputs are named in `step.py` and handed to a function that
  never mentions either parameter.

The findings are REPORTED, not failed.  Every one of them predates the
change that made the class visible, and escalating a pre-existing
finding to a build failure is the captain's decision;
`unread_by_a_prompt_less_step` is the report and `disagreements` is
unchanged.

What this still cannot see is printed by the survey itself rather than
left implicit: a step WITH a prompt is judged on whether its code NAMES
the key, because the prompt consumes it either way and the dataflow
question decides nothing there; and the value read is one-sided by
design - `_UNTRACEABLE` is the list of what it reads as used rather
than guessing about.

    python3 -m library.tools.input_contract          # the survey
    python3 -m library.tools.input_contract --bad    # disagreements only

`tests/test_input_declarations_are_true.py`.


Rules relocated from AGENTS.md 3
--------------------------------
These are the engine's rules for this module.  They lived in AGENTS.md
until it was split by subsystem; the wording is unchanged, so each rule
is findable by its own words, and AGENTS.md 3 keeps the headline
and points here.

**No step may declare an input required that nothing refuses on, or optional that its own code refuses without.**
`library/tools/input_contract.py` surveys all 144 declared inputs of the DAG's 26 steps and says, for each, WHO refuses when it is absent - the runner (edge-routed and required), the step (with a file and a line), or nobody.
- **Enforcement is not warrant.** Establishing warrant means RUNNING the step without the input; `tests/test_compile_manifest_without_the_decoration.py` does that for every input of the one step that reads state directly instead of taking `gather_step_inputs`' word for it.
- A required input the step nonetheless runs without is recorded in `REQUIRED_THOUGH_THE_STEP_RUNS_WITHOUT_IT` with what would go silently missing - and the test checks the record BOTH ways, so an entry for an input that really refuses is stale and fails.
- The line is AGENTS.md section 10.5's: `[]` for transitions is the absence of decoration and is optional; `{}` for the audio mix is the spine's declared `music_behavior` going missing and is not.
- `UNCONSUMED_DECLARATIONS` records an input read by neither the step's code nor its prompt, still declared because unrouting it would leave a frozen `handoff.md` documenting a read that no longer happens. `UNROUTED_THOUGH_THE_HANDOFF_DOCUMENTS_IT` is its MIRROR - the declaration has gone and the frozen line has stayed - and `creative_direction.prosody_analysis` is its one entry, held open until the captain rules on `step_2_01_creative_direction/handoff.md:127`. It fails from BOTH sides: an entry whose input is declared again is stale, and so is one whose handoff no longer names the key.
- **A step with no `handoff.md` reaches no prompt, and its CODE is the only consumer it can have.** `step_has_a_prompt` asks `run_pipeline.get_step_implementation`, and `trace_step_values` then asks whether the value the key yields REACHES A USE - naming the key is not reading it.
- **That half REPORTS; it does not fail**, because escalating a pre-existing finding is the captain's call. `unread_by_a_prompt_less_step` is the report; `disagreements` is unchanged.
- **The value read is one-sided and says so.** `_UNTRACEABLE` is what it reads as USED rather than guessing about - anything but a plain function the step's own files define, an alias, a second hop.
- **Deterministic**: a `step.py`, run automatically over JSON stdin/stdout.
- **Hybrid**: a `bridge.py` that pre-computes context plus a `handoff.md` prompt for an LLM.
- **LLM-only**: only a `handoff.md`, generating the output from upstream context.
"""

from __future__ import annotations

import ast
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Mapping, Optional, Sequence, Set, Tuple

from library.tools import run_scope

_LIBRARY_ROOT = Path(__file__).resolve().parents[1]


# ── Declared, and nothing reads it ───────────────────────────────────
#
# A declaration nothing consumes is the purest unwarranted requirement:
# it costs a scoped run its producer and buys nothing.  Both entries here
# are the same case, found by the survey and relaxed to optional in the
# same change - the input is still ROUTED, because unrouting it would
# leave the step's `handoff.md` documenting a read that no longer
# happens, and those files are the captain's.
#
# Widening this table is not a way to make the survey quiet.  A new entry
# means a new declaration nobody reads.

UNCONSUMED_DECLARATIONS = {
    ("mesh_spine", "temporal_index"):
        "The post_bridge never names it and `context_fields` does not "
        "select it, so the projection deletes it before the prompt. "
        "Relaxed to optional under #260; unrouting it is a separate "
        "decision because the handoff's State Interaction table still "
        "lists it.",
}


# ── Unrouted, though a frozen handoff still documents the read ───────
#
# The other side of the same disagreement.  Above, the input is still
# DECLARED because unrouting it would leave the handoff documenting a
# read that no longer happens.  Here the declaration has GONE and the
# handoff line has stayed, because the two are owned by different
# people: the declaration is this engine's, the `handoff.md` is the
# captain's and is frozen.
#
# So an entry here is a record that a frozen file and the DAG disagree,
# held open until the captain rules on the line.  It is NOT a way to go
# quiet: `disagreements` fails it from BOTH sides - if the input is
# declared again the entry is stale, and if the handoff stops naming
# the key the captain has ruled and the entry is stale too.

UNROUTED_THOUGH_THE_HANDOFF_DOCUMENTS_IT = {
    # (creative_direction, prosody_analysis): CLOSED 2026-09-01.
    # The captain ruled that prosody is deterministic measurement the
    # model lacks, and wired step 1.05 back into the DAG with its output
    # routed to 2.01 and 2.02.  The handoff line (127) and the
    # declaration now agree.
}


# ── Required, though the step runs without it ────────────────────────
#
# Enforcement is not warrant.  A step can refuse an input it would in
# fact have compiled without, and `compile_manifest` did that for four
# specs at a measured cost of 446.5s (#260).  So the requirement has to
# be justified by what goes MISSING, not by the fact that nothing
# crashes.
#
# The line AGENTS.md section 10.5 draws is the one used here.  A value
# meaning "nothing is drawn" - no transition, no effect, no sound, no
# grade - is the absence of decoration, and those four are now declared
# optional.  A value standing in for a DECISION somebody made or a
# MEASUREMENT somebody took is different: the run would ship without it
# and nothing would say so.
#
# `tests/test_compile_manifest_without_the_decoration.py` runs the step
# with each input absent and checks this table in BOTH directions - an
# unrecorded input the step compiles without fails, and a recorded input
# the step refuses without is stale and fails too.

REQUIRED_THOUGH_THE_STEP_RUNS_WITHOUT_IT = {
    ("compile_manifest", "audio_mix_spec"):
        "Compiles to `audio_mix: {}`, so the bed plays at its recorded "
        "level under the speech and every dB the spine's declared "
        "`music_behavior` asked for is gone. A planned silence is a "
        "decision (library/tools/music_behavior.py); losing it is not "
        "the absence of decoration.",
    ("compile_manifest", "music_selection"):
        "Compiles to an empty A2. The bed was chosen against the "
        "creative direction under library/tools/"
        "music_selection_contract.py, and a video that silently ships "
        "without it is not the same edit.",
    ("compile_manifest", "semantic_analysis"):
        "Compiles with no `neural_engine_directives`, so no clip is "
        "stabilised and no low-resolution clip gets Super Scale. That "
        "is a MEASUREMENT going missing, and the empty lookup this "
        "reader had for months is the reason the step now raises when "
        "documents are present and none of them join.",
    ("compile_manifest", "subtitle_plan"):
        "Compiles to `subtitles: []`. The captions are the product for "
        "the one target this pipeline has; dropping them silently is "
        "not decoration, and `_assert_subtitle_overlay_matches_plan` "
        "returns early when either side is empty, so nothing else "
        "would say so.",
    ("compile_manifest", "subtitle_overlay"):
        "Compiles with the caption list intact and NOTHING on screen: "
        "the renderer places the rendered Remotion segments and never "
        "reads `subtitles`. `_assert_subtitle_overlay_matches_plan` "
        "returns early when there are no segments, so an absent "
        "overlay is exactly the stale-render case that gate exists to "
        "catch.",
    ("compile_manifest", "a_roll_assignments"):
        "Compiles, because V1 falls back to the spine's own blocks - "
        "but the assignment is where step 3.01 recorded WHICH source "
        "range each block plays. Falling back replaces a decision with "
        "a different reading of the spine, silently.",
    ("compile_manifest", "b_roll_assignments"):
        "Compiles whenever the spine happens to be all speech, as the "
        "measurement's fixture is. It does NOT compile for a spine "
        "carrying a block only B-roll can cover: "
        "`_assert_timeline_fully_covered` fails on the hole. The step "
        "cannot know which spine it will be handed, so the requirement "
        "stands - AGENTS.md 10.5 keeps the coverage requirement for "
        "exactly this reason.",
}


# ── How an input can reach a step ────────────────────────────────────
#
# These are the routes `gather_step_inputs` really has, named here so the
# survey's reading of "the runner would have raised" is the runner's.

ROUTE_EDGE = "edge"
"""A DAG `data_mapping` targets this input name. `gather_step_inputs`
raises when the source key is missing and the input is not optional."""

ROUTE_EDGE_MERGE = "edge (merged)"
"""An inbound edge with no `data_mapping` merges the producer's whole
output. There is no key to judge, so nothing raises for this input by
name."""

ROUTE_PROCESS = "process"
"""`run_pipeline.PROCESS_LEVEL_INPUTS` - injected only if the step's own
manifest declares it, and silently absent if the state has no value."""

ROUTE_GLOBAL = "global"
"""`project_folder` / `project_config`, whitelisted onto every step."""

ROUTE_BRAND = "brand"
"""Resolved and injected by the brand block, which raises on a template
reference it cannot resolve."""

ROUTE_LAST_RENDER = "last render"
"""`run_pipeline.QA_FINDINGS_INPUT` - the last render's QA findings,
injected only if the step's own manifest declares it. No edge can carry
it: the producer is the DAG's final node and the consumer sits upstream
of it, so it is a statement about state rather than about lineage. Absent
on a project that has never rendered, and never raising."""

ROUTE_NONE = "unrouted"
"""Nothing supplies it. A declaration the runner can never meet."""

_PROCESS_LEVEL = ("sfx_library", "music_library", "creative_brief")
_GLOBALS = ("project_folder", "project_config")
_BRAND = ("brand_template", "brand_style", "brand_effect", "brand_content")
_LAST_RENDER = ("render_qa_findings",)
_TIMELINE_TRANSCRIPT = ("timeline_transcript",)

ROUTE_TIMELINE_TRANSCRIPT = "timeline transcript"
"""`run_pipeline.TIMELINE_TRANSCRIPT_INPUT` - the per-speaker timeline
transcript, produced by `library/tools/timeline_transcript.py` outside
the pipeline (it needs Resolve open and WhisperX).  Injected only if the
step's own manifest declares it, and read from the project's scratch
directory.  Required inputs raise when the file is absent."""


# ── Who refuses ──────────────────────────────────────────────────────

REFUSED_BY_RUNNER = "runner"
REFUSED_BY_STEP = "step"
REFUSED_BY_NOBODY = "nobody"


@dataclass(frozen=True)
class InputContract:
    """One declared input of one step, and what happens without it."""

    node_id: str
    step_ref: str
    name: str
    required: bool
    route: str
    step_refusal: str
    """`file.py:line what` for the code's own refusal, or "" if it has
    none. Evidence, so a verdict can be checked rather than believed."""

    code_reads: bool
    """The step's own Python names this key at all."""

    prompt_reads: bool
    """The projection lets it reach the prompt (AGENTS.md 10.1). Always
    False for a step with no `handoff.md`: it has no prompt to reach."""

    has_prompt: bool = True
    """The step puts something in front of a model, asked of
    `run_pipeline.get_step_implementation`."""

    code_consumes: Optional[bool] = None
    """Whether the value the key yields REACHES A USE, for a step whose
    code is the only consumer it can have.

    `True` the value is used somewhere; `False` every place the step's
    own files obtain it, it goes nowhere; `None` not asked - the step
    has a prompt, so the prompt already consumes it, or the step's own
    files never obtain the value and there is nothing to trace.
    """

    value_evidence: str = ""
    """`file.py:line what` for a value that goes nowhere. Evidence, so a
    finding can be checked rather than believed."""

    @property
    def refused_by(self) -> str:
        if self.step_refusal:
            return REFUSED_BY_STEP
        if self.required and self.route in (
                ROUTE_EDGE, ROUTE_TIMELINE_TRANSCRIPT):
            return REFUSED_BY_RUNNER
        return REFUSED_BY_NOBODY

    @property
    def consumed(self) -> bool:
        """Something downstream of the declaration actually reads it.

        A key the code NAMES and then drops is not read. That is the
        whole of the deterministic blind spot: `code_reads` answers
        whether the key appears, `code_consumes` whether the value it
        yields does anything.
        """
        if self.code_consumes is False:
            return False
        return self.code_reads or self.prompt_reads

    @property
    def warrant(self) -> str:
        """Why the requirement is believed, which is not why it holds.

        `enforced` is the honest word for most of this pipeline: the
        runner refuses without the input and nobody has run the step to
        see whether it needed to. It is reported as such rather than
        dressed up, the same way `provenance.py` keeps `declared` and
        `observed` apart.
        """
        if not self.required:
            return "optional"
        if (self.node_id, self.name) in REQUIRED_THOUGH_THE_STEP_RUNS_WITHOUT_IT:
            return "measured"
        if self.step_refusal:
            return "step states it"
        return "enforced"


# ── Reading the code ─────────────────────────────────────────────────

_REFUSAL_CALLS = frozenset({"require_keys", "_require_keys"})
_EXIT_CALLS = frozenset({"_fail", "_die", "exit"})


def _string_key(node) -> Optional[str]:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _get_call_key(node) -> Optional[str]:
    """`x.get("K", ...)` -> "K"."""
    if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
            and node.func.attr == "get" and node.args):
        return _string_key(node.args[0])
    return None


def _bound_key(value) -> Optional[str]:
    """The key a name is bound to, when the binding IS the read.

    `v = data.get("K", {})` binds K.  `v = f(data.get("K", ""))` does
    NOT: the guard that follows tests f's result, not the key.  Reading
    it as a binding is what made `validate`'s `if not
    result.get("distribution_ready")` look like a refusal on
    `project_folder`.
    """
    if isinstance(value, ast.BoolOp):
        for part in value.values:
            key = _bound_key(part)
            if key:
                return key
        return None
    if isinstance(value, ast.IfExp):
        return _bound_key(value.body) or _bound_key(value.orelse)
    if isinstance(value, ast.Subscript) and isinstance(value.value, ast.Name):
        return _string_key(value.slice)
    return _get_call_key(value)


def _exits(body: Sequence[ast.stmt]) -> bool:
    """Does this block raise, `sys.exit`, or call a fail helper?"""
    for node in ast.walk(ast.Module(body=list(body), type_ignores=[])):
        if isinstance(node, ast.Raise):
            return True
        if isinstance(node, ast.Call):
            func = node.func
            name = (func.id if isinstance(func, ast.Name)
                    else func.attr if isinstance(func, ast.Attribute) else "")
            if name in _EXIT_CALLS:
                return True
    return False


def _refusals_and_reads(tree: ast.AST, filename: str
                        ) -> Tuple[Dict[str, str], Set[str]]:
    """`({key: evidence}, {every key the module names})` for one file."""
    refusals: Dict[str, str] = {}
    reads: Set[str] = set()
    bindings: Dict[str, str] = {}

    for node in ast.walk(tree):
        if isinstance(node, ast.Subscript) and isinstance(node.value, ast.Name):
            key = _string_key(node.slice)
            if key:
                reads.add(key)
                refusals.setdefault(
                    key, f"{filename}:{node.lineno} {node.value.id}[{key!r}]")
        key = _get_call_key(node)
        if key:
            reads.add(key)
        if isinstance(node, ast.Call):
            func = node.func
            name = (func.id if isinstance(func, ast.Name)
                    else func.attr if isinstance(func, ast.Attribute) else "")
            if name in _REFUSAL_CALLS:
                for arg in node.args:
                    if isinstance(arg, (ast.List, ast.Tuple)):
                        for element in arg.elts:
                            element_key = _string_key(element)
                            if element_key:
                                reads.add(element_key)
                                refusals[element_key] = (
                                    f"{filename}:{node.lineno} {name}()")
        if (isinstance(node, ast.Assign) and len(node.targets) == 1
                and isinstance(node.targets[0], ast.Name)):
            key = _bound_key(node.value)
            if key:
                bindings[node.targets[0].id] = key

    for node in ast.walk(tree):
        if not isinstance(node, ast.If) or not _exits(node.body):
            continue
        for name_node in ast.walk(node.test):
            if isinstance(name_node, ast.Name) and name_node.id in bindings:
                key = bindings[name_node.id]
                refusals.setdefault(
                    key, f"{filename}:{node.lineno} guard raises")

    return refusals, reads


def _imported_library_modules(tree: ast.AST) -> Set[str]:
    """`library.tools.x` / `library.steps...` modules this file imports.

    One hop, and for READS only. `review_rough_cut` reads
    `project_config` through `library/tools/duration_targets.py`, so a
    scan of the step directory alone reports a declared input nothing
    consumes, which is false. Refusal evidence is NOT taken from a shared
    tool: a helper indexing `block["clip_id"]` is refusing on a block,
    not on a step input that happens to share the name.
    """
    modules: Set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and (node.module or "").startswith(
                "library."):
            for alias in node.names:
                modules.add(f"{node.module}.{alias.name}")
                modules.add(node.module)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.startswith("library."):
                    modules.add(alias.name)
    return modules


def _module_path(dotted: str) -> Optional[Path]:
    path = _LIBRARY_ROOT.parent / (dotted.replace(".", "/") + ".py")
    return path if path.is_file() else None


def read_step_code(step_dir: Path) -> Tuple[Dict[str, str], Set[str]]:
    """Every refusal and every key read, for one step.

    Refusals come from the step's own modules. Reads follow one hop into
    the `library/` modules those files import, because a step that hands
    its inputs to a shared tool is still reading them.
    """
    refusals: Dict[str, str] = {}
    reads: Set[str] = set()
    imported: Set[str] = set()
    for path in sorted(step_dir.glob("*.py")):
        if path.name.startswith("test_"):
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (OSError, SyntaxError):
            continue
        file_refusals, file_reads = _refusals_and_reads(tree, path.name)
        for key, evidence in file_refusals.items():
            refusals.setdefault(key, evidence)
        reads |= file_reads
        imported |= _imported_library_modules(tree)

    for dotted in sorted(imported):
        path = _module_path(dotted)
        if path is None:
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (OSError, SyntaxError):
            continue
        _, module_reads = _refusals_and_reads(tree, path.name)
        reads |= module_reads
    return refusals, reads


# ── Does the value go anywhere ───────────────────────────────────────
#
# A step with no `handoff.md` has no prompt, so "reaches the prompt" is
# a statement about something that does not exist and the code is the
# only consumer there can be.  For that step, "the code NAMES the key"
# is not enough: `render_motion_graphics` does
# `data.get("enhancement_spec", {})`, hands the value to
# `generate_motion_props`, and that function never mentions its own
# parameter again.  The key is named, the value goes nowhere, and the
# survey called it consumed.
#
# So for a prompt-less step the survey asks the harder question: does
# the value the key yields REACH A USE.  It is a small dataflow read,
# and it is deliberately one-sided - everything it cannot follow is
# read as a use, so it under-reports rather than inventing a finding.
# `_UNTRACEABLE` records what it cannot follow.

_UNTRACEABLE = (
    ("a value passed to anything but a plain function this step's own "
     "files define - a method, an imported library tool, a builtin - is "
     "read as used, because the callee is not traced"),
    ("a value rebound to a second name is read as used, rather than "
     "followed through the alias"),
    ("one hop only: a parameter the callee passes on again is read as "
     "used wherever the callee names it"),
)


def _parent_table(tree: ast.AST) -> Dict[ast.AST, ast.AST]:
    table: Dict[ast.AST, ast.AST] = {}
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            table[child] = node
    return table


def _origin_key(node) -> Optional[str]:
    """The input key a node OBTAINS, for `x["K"]` and `x.get("K", ...)`.

    The object has to be a plain name - the dict the step was handed.
    `manifest["K"]` qualifies and so does `data.get("K")`; a chained
    `a.b["K"]` does not, because the value came from somewhere else.
    """
    if isinstance(node, ast.Subscript) and isinstance(node.value, ast.Name):
        return _string_key(node.slice)
    if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
            and node.func.attr == "get" and node.args
            and isinstance(node.func.value, ast.Name)):
        return _string_key(node.args[0])
    return None


def _function_table(trees: Sequence[Tuple[str, ast.AST]]
                    ) -> Dict[str, Optional[ast.FunctionDef]]:
    """`{name: def}` for the step's own functions.

    A name two files define is mapped to None, so a call on it resolves
    to nothing and the argument is read as used.  Guessing which of two
    definitions ran is how a dataflow read starts inventing findings.
    """
    table: Dict[str, Optional[ast.FunctionDef]] = {}
    for _, tree in trees:
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                table[node.name] = None if node.name in table else node
    return table


def _parameter_names(fn) -> List[str]:
    return [a.arg for a in list(fn.args.posonlyargs) + list(fn.args.args)]


def _parameter_for(fn, call: ast.Call, argument) -> Optional[str]:
    """Which parameter this argument lands on, or None if unknowable."""
    for keyword in call.keywords:
        if keyword.value is argument:
            if keyword.arg is None:
                return None
            names = _parameter_names(fn) + [a.arg for a in fn.args.kwonlyargs]
            return keyword.arg if keyword.arg in names else None
    if any(isinstance(a, ast.Starred) for a in call.args):
        return None
    for index, given in enumerate(call.args):
        if given is argument:
            names = _parameter_names(fn)
            return names[index] if index < len(names) else None
    return None


def _parameter_is_read(fn, parameter: str) -> bool:
    for node in ast.walk(ast.Module(body=list(fn.body), type_ignores=[])):
        if (isinstance(node, ast.Name) and node.id == parameter
                and isinstance(node.ctx, ast.Load)):
            return True
    return False


def _is_a_use(node, parents: Mapping, functions: Mapping) -> bool:
    """Does this occurrence of the value do anything with it?

    One case answers no: the value is handed to a plain function this
    step defines, and that function never names the parameter it landed
    on.  Everything else - a subscript, an attribute, a comparison, a
    return, a call this read cannot resolve - is a use.
    """
    parent = parents.get(node)
    if not isinstance(parent, ast.Call) or parent.func is node:
        return True
    callee = parent.func
    if not isinstance(callee, ast.Name):
        return True
    fn = functions.get(callee.id)
    if fn is None:
        return True
    parameter = _parameter_for(fn, parent, node)
    if parameter is None:
        return True
    return _parameter_is_read(fn, parameter)


def _enclosing_scope(node, parents: Mapping):
    walker = parents.get(node)
    while walker is not None and not isinstance(
            walker, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Module)):
        walker = parents.get(walker)
    return walker


def _binding_reaches_a_use(name: str, scope, binding, parents: Mapping,
                           functions: Mapping) -> bool:
    if scope is None:
        return True
    for node in ast.walk(scope):
        if (isinstance(node, ast.Name) and node.id == name
                and isinstance(node.ctx, ast.Load) and node is not binding
                and _is_a_use(node, parents, functions)):
            return True
    return False


def trace_step_values(step_dir: Path) -> Tuple[Set[str], Dict[str, str]]:
    """`({keys the step's own code obtains}, {key: where it goes nowhere})`.

    A key is in the second mapping only when EVERY place the step's own
    files obtain it, the value reaches no use.  One use anywhere keeps
    it out: a step that reads an input on one route and drops it on
    another is reading it.
    """
    trees: List[Tuple[str, ast.AST]] = []
    for path in sorted(step_dir.glob("*.py")):
        if path.name.startswith("test_"):
            continue
        try:
            trees.append((path.name, ast.parse(
                path.read_text(encoding="utf-8"))))
        except (OSError, SyntaxError):
            continue

    functions = _function_table(trees)
    obtained: Set[str] = set()
    used: Set[str] = set()
    dead: Dict[str, str] = {}

    for filename, tree in trees:
        parents = _parent_table(tree)
        for node in ast.walk(tree):
            key = _origin_key(node)
            if key is None:
                continue
            obtained.add(key)
            parent = parents.get(node)
            if (isinstance(parent, ast.Assign) and parent.value is node
                    and len(parent.targets) == 1
                    and isinstance(parent.targets[0], ast.Name)):
                target = parent.targets[0]
                reaches = _binding_reaches_a_use(
                    target.id, _enclosing_scope(parent, parents), target,
                    parents, functions)
                where = (f"{filename}:{node.lineno} bound to "
                         f"{target.id!r} and never used")
            else:
                reaches = _is_a_use(node, parents, functions)
                where = (f"{filename}:{node.lineno} handed to a function "
                         f"that never names the parameter")
            if reaches:
                used.add(key)
            else:
                dead.setdefault(key, where)

    return obtained, {k: v for k, v in dead.items() if k not in used}


def step_has_a_prompt(step_dir: Path) -> bool:
    """Whether this step puts anything in front of a model.

    Asked of the runner, which is the authority: `handoff.md` is what
    `get_step_implementation` turns into every `prompt` it returns, and
    a copy of that rule here is a second list that can drift.  The
    import is local because the runner imports this package.
    """
    try:
        from library.processes.edit_video import run_pipeline
    except Exception:
        return (step_dir / "handoff.md").is_file()
    return bool(run_pipeline.get_step_implementation(step_dir).get("prompt"))


# ── Reading the routes ───────────────────────────────────────────────

def routed_inputs(dag: Mapping) -> Dict[str, Set[str]]:
    """`{consumer: {input names a data_mapping targets}}`."""
    out: Dict[str, Set[str]] = {}
    for edge in dag.get("edges", []):
        for destination in (edge.get("data_mapping") or {}).values():
            out.setdefault(edge["to"], set()).add(destination)
    return out


def merging_consumers(dag: Mapping) -> Set[str]:
    """Consumers with an inbound edge carrying no `data_mapping`."""
    return {edge["to"] for edge in dag.get("edges", [])
            if not (edge.get("data_mapping") or {})}


def _route(name: str, node_id: str, routed: Mapping[str, Set[str]],
           merging: Set[str]) -> str:
    if name in routed.get(node_id, set()):
        return ROUTE_EDGE
    if name in _PROCESS_LEVEL:
        return ROUTE_PROCESS
    if name in _GLOBALS:
        return ROUTE_GLOBAL
    if name in _BRAND:
        return ROUTE_BRAND
    if name in _LAST_RENDER:
        return ROUTE_LAST_RENDER
    if name in _TIMELINE_TRANSCRIPT:
        return ROUTE_TIMELINE_TRANSCRIPT
    if node_id in merging:
        return ROUTE_EDGE_MERGE
    return ROUTE_NONE


# Keys `run_pipeline.project_step_context` saves and restores AROUND the
# projection, so a step's allow-list neither has to list them nor can
# drop them (AGENTS.md section 10.1).
# `timeline_notes` is on this list for the reason `creative_brief` is:
# `run_pipeline.project_step_context` restores it BY NAME around the
# projection, so a step's allow-list neither has to list the captain's
# notes nor can drop them.  See library/tools/marker_routing.py.
_RESTORED_AROUND_PROJECTION = ("project_folder", "project_fps",
                               "brand_template", "creative_brief",
                               "timeline_notes")


class _Recorder(dict):
    """A dict that remembers which keys were asked for."""

    def __init__(self):
        super().__init__()
        self.touched: Set[str] = set()

    def get(self, key, default=None):
        self.touched.add(key)
        return default

    def __getitem__(self, key):
        self.touched.add(key)
        raise KeyError(key)

    def __contains__(self, key):
        self.touched.add(key)
        return False


def view_sources(view: str) -> Set[str]:
    """The inputs a named view READS, asked of the builder itself.

    `context_views.CONTEXT_VIEWS` maps a name to a function and records
    the source input in that function's body, not in a table. Running
    the builder against a recording mapping reads the answer off the one
    authority instead of copying it into a second list that can drift.
    """
    from library.tools import context_views

    recorder = _Recorder()
    try:
        context_views.build_view(view, recorder)
    except ValueError:
        return set()
    except Exception:
        pass
    # `build_view` asks whether the view's own NAME is already in the
    # tree - that is how a second projection of an already-projected
    # tree stays a no-op - and that lookup is not a source.
    return set(recorder.touched) - {view}


def _reaches_prompt(manifest: Mapping, name: str,
                    has_prompt: bool = True) -> bool:
    """Whether the projection lets this input reach the prompt.

    Three ways in, and all three are the projection's own (AGENTS.md
    section 10.1): a step declaring no `context_fields` is handed every
    byte it was routed; a declaration of nothing but `-` paths is
    "everything, minus these"; and a `view:<name>` entry is a READING of
    a routed input, so the input it reads reaches the prompt through it.

    A step with no `handoff.md` reaches NOTHING, and that is the blind
    spot this argument closes.  It declares no `context_fields` because
    it has no prompt to project, and reading that absence as "handed
    every byte" answered True for every input of every deterministic
    step - so `render_motion_graphics` declared `creative_direction` and
    `enhancement_spec` REQUIRED, read neither, and surveyed clean.
    """
    if not has_prompt:
        return False
    if name in _RESTORED_AROUND_PROJECTION:
        return True
    fields = manifest.get("context_fields")
    if fields is None:
        return True
    selectors = [str(f) for f in fields if not str(f).startswith("-")]
    if not selectors:
        return f"-{name}" not in [str(f) for f in fields]
    for selector in selectors:
        if selector.split(".")[0] == name:
            return True
        if selector.startswith("view:") and name in view_sources(
                selector[len("view:"):]):
            return True
    return False


# ── The survey ───────────────────────────────────────────────────────

def survey(dag: Optional[Mapping] = None,
           manifests: Optional[Mapping[str, dict]] = None
           ) -> List[InputContract]:
    """Every declared input of every step in the DAG, in run order."""
    dag = dag if dag is not None else run_scope.load_dag()
    manifests = (manifests if manifests is not None
                 else run_scope.load_manifests(dag))
    routed = routed_inputs(dag)
    merging = merging_consumers(dag)
    step_ref = {node["id"]: node["step_ref"] for node in dag.get("nodes", [])}

    rows: List[InputContract] = []
    for node_id in run_scope.topological_order(dag):
        manifest = manifests.get(node_id) or {}
        step_dir = _LIBRARY_ROOT / step_ref[node_id]
        refusals, reads = read_step_code(step_dir)
        has_prompt = step_has_a_prompt(step_dir)
        # The dataflow read is asked only where it decides something.
        # A step WITH a prompt already has a consumer, so tracing its
        # values would answer a question nobody is asking - and would
        # change the survey's verdict on every LLM step, which is a
        # separate decision from closing this blind spot.
        obtained, dead = (set(), {}) if has_prompt else trace_step_values(
            step_dir)
        for declaration in ((manifest.get("interface") or {}).get("inputs")
                            or []):
            name = declaration.get("name", "")
            if name in dead:
                consumes = False
            elif name in obtained:
                consumes = True
            else:
                consumes = None
            rows.append(InputContract(
                node_id=node_id,
                step_ref=step_ref[node_id],
                name=name,
                required=declaration.get("required", True),
                route=_route(name, node_id, routed, merging),
                step_refusal=refusals.get(name, ""),
                code_reads=name in reads,
                prompt_reads=_reaches_prompt(manifest, name, has_prompt),
                has_prompt=has_prompt,
                code_consumes=consumes,
                value_evidence=dead.get(name, ""),
            ))
    return rows


# ── The verdicts ─────────────────────────────────────────────────────

def unenforced(rows: Sequence[InputContract]) -> List[InputContract]:
    """Required, and nothing refuses when it is absent."""
    return [r for r in rows if r.required and r.refused_by == REFUSED_BY_NOBODY]


def optional_but_refused(rows: Sequence[InputContract]) -> List[InputContract]:
    """Declared optional, and the step's own code refuses anyway. A run
    that legitimately omits it dies inside the step."""
    return [r for r in rows if not r.required and r.step_refusal]


def unconsumed(rows: Sequence[InputContract]) -> List[InputContract]:
    """Declared, and read by neither the step's code nor its prompt."""
    return [r for r in rows if not r.consumed]


def unread_by_a_prompt_less_step(rows: Sequence[InputContract]
                                 ) -> List[InputContract]:
    """Declared by a step with no prompt, and its code does not read it.

    This whole class was invisible until the survey stopped answering
    "reaches the prompt" for a step that has none. It is REPORTED and
    does not fail: every row in it predates the fix, and escalating a
    pre-existing finding to a build failure is the captain's decision,
    not this module's. `unconsumed` still fails for a step WITH a
    prompt, exactly as it did.
    """
    return [r for r in unconsumed(rows) if not r.has_prompt]


def unread_basis(row: InputContract) -> str:
    """Why this row reads as unread, in the survey's own terms."""
    if row.code_consumes is False:
        return (f"the code names it and the value goes nowhere: "
                f"{row.value_evidence}")
    return "the step's own code and the modules it imports never name it"


def disagreements(rows: Sequence[InputContract]) -> List[str]:
    """Every way the declaration and the code can contradict each other."""
    lines = []
    for row in optional_but_refused(rows):
        lines.append(
            f"{row.node_id}.{row.name}: declared OPTIONAL, but the step "
            f"refuses without it at {row.step_refusal}. A run that leaves "
            f"it out dies inside the step.")
    for row in unenforced(rows):
        lines.append(
            f"{row.node_id}.{row.name}: declared REQUIRED, but it arrives "
            f"by {row.route} and the step's code does not refuse without "
            f"it. Nothing enforces the requirement.")
    for row in unconsumed(rows):
        if (row.node_id, row.name) in UNCONSUMED_DECLARATIONS:
            continue
        if not row.has_prompt:
            continue  # reported by `unread_by_a_prompt_less_step`
        lines.append(
            f"{row.node_id}.{row.name}: declared, and read by neither the "
            f"step's code nor its prompt. Nothing consumes it. Drop the "
            f"declaration, or record it in "
            f"input_contract.UNCONSUMED_DECLARATIONS with what it is for.")
    for node_id, name in sorted(UNCONSUMED_DECLARATIONS):
        if not any(r.node_id == node_id and r.name == name
                   and not r.consumed for r in rows):
            lines.append(
                f"{node_id}.{name} is recorded in UNCONSUMED_DECLARATIONS "
                f"and something now reads it. Delete the entry.")
    lines.extend(unrouted_but_documented(rows))
    return lines


PROSE_FIELDS = ("preconditions", "postconditions")
"""The two `interface` fields that held 126 strings nothing evaluated."""


def prose_preconditions() -> List[str]:
    """Manifests still carrying a prose `preconditions`/`postconditions`.

    This is what stops the defect returning.  Both fields were prose -
    `"'audio_spine' exists in state"`, `"'rough_cut_review.passed' is
    true in state"` - and across 29 manifests **nothing evaluated one of
    them**.  71 of the 90 preconditions merely restated an
    `inputs[].required` the DAG already enforced; the rest expressed
    things `required` cannot say at all, so they could only ever be
    prose.

    They are replaced by `interface.requirements`, naming executable
    `Requirement`s in `library/tools/requirements.py`.  A manifest that
    grows the old field back is a manifest declaring a contract again
    with nothing behind it, which is worse than declaring none - it reads
    as coverage.

    `tests/test_input_declarations_are_true.py` asserts this is empty.
    """
    out: List[str] = []
    for path in sorted((_LIBRARY_ROOT / "steps").glob("*/manifest.json")):
        try:
            manifest = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        interface = manifest.get("interface") or {}
        for field_name in PROSE_FIELDS:
            if field_name in interface:
                out.append(f"{path.parent.name}: interface.{field_name}")
    return out


def unrouted_but_documented(rows: Sequence[InputContract]) -> List[str]:
    """Both ways an `UNROUTED_THOUGH_THE_HANDOFF_DOCUMENTS_IT` entry
    goes stale: the declaration came back, or the frozen line went."""
    lines = []
    step_ref = {r.node_id: r.step_ref for r in rows}
    declared = {(r.node_id, r.name) for r in rows}
    for node_id, name in sorted(UNROUTED_THOUGH_THE_HANDOFF_DOCUMENTS_IT):
        if (node_id, name) in declared:
            lines.append(
                f"{node_id}.{name} is recorded in "
                f"UNROUTED_THOUGH_THE_HANDOFF_DOCUMENTS_IT and is a "
                f"declared input again. Delete the entry.")
            continue
        ref = step_ref.get(node_id)
        if ref is None:
            lines.append(
                f"{node_id} is recorded in "
                f"UNROUTED_THOUGH_THE_HANDOFF_DOCUMENTS_IT and is not a "
                f"step of this DAG. Delete the entry.")
            continue
        handoff = _LIBRARY_ROOT / ref / "handoff.md"
        if not handoff.is_file() or name not in handoff.read_text(
                encoding="utf-8"):
            lines.append(
                f"{node_id}.{name} is recorded in "
                f"UNROUTED_THOUGH_THE_HANDOFF_DOCUMENTS_IT and the "
                f"handoff no longer names it. The disagreement is over. "
                f"Delete the entry.")
    return lines


# ── Reporting ────────────────────────────────────────────────────────

def render(rows: Sequence[InputContract]) -> List[str]:
    """The survey as the captain reads it, one line per declared input."""
    lines = [
        f"{'step':<24}{'input':<30}{'decl':<9}{'route':<14}"
        f"{'refused by':<12}{'warrant':<16}{'consumed by':<14}evidence",
        "-" * 144,
    ]
    current = None
    for row in rows:
        if row.node_id != current:
            current = row.node_id
            lines.append("")
        consumers = ", ".join(
            part for part, on in (("code", row.consumed and row.code_reads),
                                  ("prompt", row.prompt_reads)) if on)
        if row.code_reads and row.code_consumes is False:
            consumers = "NAMED ONLY"
        lines.append(
            f"{row.node_id:<24}{row.name:<30}"
            f"{'required' if row.required else 'optional':<9}"
            f"{row.route:<14}{row.refused_by:<12}{row.warrant:<16}"
            f"{consumers or 'NOTHING':<14}{row.step_refusal}")
    return lines


def main(argv=None) -> int:
    import sys

    argv = list(sys.argv[1:] if argv is None else argv)
    rows = survey()
    problems = disagreements(rows)
    if "--bad" not in argv:
        print("\n".join(render(rows)))
        print()
    steps = len({r.node_id for r in rows})
    print(f"{len(rows)} declared inputs across {steps} steps in the DAG.")
    counts = {}
    for row in rows:
        counts[row.refused_by] = counts.get(row.refused_by, 0) + 1
    for verdict in (REFUSED_BY_RUNNER, REFUSED_BY_STEP, REFUSED_BY_NOBODY):
        print(f"  refused by {verdict:<8}: {counts.get(verdict, 0)}")
    warrants = {}
    for row in rows:
        warrants[row.warrant] = warrants.get(row.warrant, 0) + 1
    print("warrant for the required ones:")
    for warrant in ("measured", "step states it", "enforced"):
        print(f"  {warrant:<16}: {warrants.get(warrant, 0)}")
    print(f"  {'(optional)':<16}: {warrants.get('optional', 0)}")

    unread = [r for r in unconsumed(rows) if r.has_prompt]
    if unread:
        print(f"\n{len(unread)} declared input(s) nothing consumes "
              f"(recorded in UNCONSUMED_DECLARATIONS):")
        for row in unread:
            print(f"  - {row.node_id}.{row.name}")

    prompt_less = sorted({r.node_id for r in rows if not r.has_prompt})
    dropped = unread_by_a_prompt_less_step(rows)
    print(f"\n{len(prompt_less)} step(s) in the DAG have no prompt, so "
          f"their code is the only consumer they can have.")
    if dropped:
        print(f"  {len(dropped)} declared input(s) that code does not read:")
        for row in dropped:
            print(f"    UNREAD  {row.node_id}.{row.name} "
                  f"({'required' if row.required else 'optional'}) - "
                  f"{unread_basis(row)}")
        print("  (reporting only - this class was invisible until the "
              "survey\n  stopped answering \"reaches the prompt\" for a "
              "step with none, and\n  escalating a pre-existing finding "
              "to a failure is the captain's call.)")
    else:
        print("  Every one of their declared inputs is read.")
    print("  Blind spot, stated: a step WITH a prompt is still judged on")
    print("  whether its code NAMES the key, not on whether the value")
    print("  goes anywhere - the prompt consumes it either way, so the")
    print("  question does not decide. And the value read is one-sided:")
    for limit in _UNTRACEABLE:
        print(f"    - {limit}")

    if problems:
        print(f"\n{len(problems)} disagreement(s):")
        for line in problems:
            print(f"  - {line}")
        return 1
    print("\nNo step declares an input required that nothing enforces, and "
          "no step refuses an input it declared optional.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
