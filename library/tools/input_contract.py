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

    python3 -m library.tools.input_contract          # the survey
    python3 -m library.tools.input_contract --bad    # disagreements only

`tests/test_input_declarations_are_true.py`.
"""

from __future__ import annotations

import ast
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
    ("review_rough_cut", "temporal_index"):
        "step.py never names it and `context_fields` does not select "
        "it. Same case, same change, same reason for stopping at "
        "optional.",
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

ROUTE_NONE = "unrouted"
"""Nothing supplies it. A declaration the runner can never meet."""

_PROCESS_LEVEL = ("sfx_library", "music_library", "creative_brief")
_GLOBALS = ("project_folder", "project_config")
_BRAND = ("brand_template", "brand_style", "brand_effect", "brand_content")


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
    """The projection lets it reach the prompt (AGENTS.md 10.1)."""

    @property
    def refused_by(self) -> str:
        if self.step_refusal:
            return REFUSED_BY_STEP
        if self.required and self.route == ROUTE_EDGE:
            return REFUSED_BY_RUNNER
        return REFUSED_BY_NOBODY

    @property
    def consumed(self) -> bool:
        """Something downstream of the declaration actually reads it."""
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
    if node_id in merging:
        return ROUTE_EDGE_MERGE
    return ROUTE_NONE


# Keys `run_pipeline.project_step_context` saves and restores AROUND the
# projection, so a step's allow-list neither has to list them nor can
# drop them (AGENTS.md section 10.1).
_RESTORED_AROUND_PROJECTION = ("project_folder", "project_fps",
                               "brand_template", "creative_brief")


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


def _reaches_prompt(manifest: Mapping, name: str) -> bool:
    """Whether the projection lets this input reach the prompt.

    Three ways in, and all three are the projection's own (AGENTS.md
    section 10.1): a step declaring no `context_fields` is handed every
    byte it was routed; a declaration of nothing but `-` paths is
    "everything, minus these"; and a `view:<name>` entry is a READING of
    a routed input, so the input it reads reaches the prompt through it.
    """
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
        refusals, reads = read_step_code(_LIBRARY_ROOT / step_ref[node_id])
        for declaration in ((manifest.get("interface") or {}).get("inputs")
                            or []):
            name = declaration.get("name", "")
            rows.append(InputContract(
                node_id=node_id,
                step_ref=step_ref[node_id],
                name=name,
                required=declaration.get("required", True),
                route=_route(name, node_id, routed, merging),
                step_refusal=refusals.get(name, ""),
                code_reads=name in reads,
                prompt_reads=_reaches_prompt(manifest, name),
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
            part for part, on in (("code", row.code_reads),
                                  ("prompt", row.prompt_reads)) if on)
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

    unread = unconsumed(rows)
    if unread:
        print(f"\n{len(unread)} declared input(s) nothing consumes "
              f"(recorded in UNCONSUMED_DECLARATIONS):")
        for row in unread:
            print(f"  - {row.node_id}.{row.name}")

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
