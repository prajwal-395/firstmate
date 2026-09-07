"""One question, asked of every declared output: WHO READS IT.

`library/tools/input_contract.py` asks who REFUSES when a declared input
is absent.  This is the mirror, and the mirror was missing: nothing in
this repository ever asked whether a value a step PRODUCES reaches
anybody.

The defect class this exists to catch
-------------------------------------
Data computed and then not reaching where it was needed is the most
common real defect in this codebase.  Seven were found in two working
days and every one is the same shape:

* the transcriber's per-line confidence was computed and DISCARDED AT
  WRITE TIME in two places, so no transcript on disk carried one;
* `caption_content_hash` was fed rendered segments instead of caption
  cards, so it digested the segment COUNT and nothing else;
* step 3.04's allow-list was declared one level deeper than the code
  read it, so 92% of its prompt was raw data it was meant to drop;
* `enforce_min_duration` was defined, documented and NEVER CALLED;
* `duplicate_takes` detected repeated speech that nothing consumed;
* captions carried no binding to the footage they were computed against;
* the reels path read no picture at all.

Four shapes, and the reason a mirror is the right mechanism: a one-off
fix for each leaves the eighth to be found by accident.

  1. computed then dropped before it is stored
  2. stored then never read
  3. declared then not delivered
  4. delivered then silently ignored - a wrong key, a shadowed variable,
     a rebuilt dict that omits fields

Shape 3 is `input_contract`'s and `requirements`'s.  Shapes 1, 2 and 4
are this module's, and 2 is the one a manifest can answer mechanically:
**a declared output with no reader.**

The routes, and why there are three
-----------------------------------
An output is CONSUMED when at least one route carries it.  There are
three, and leaving any of them out makes the survey fail correct output
- which AGENTS.md 10.4 calls the same defect as a gate that cannot fail,
from the other side.

* ``edge``       - a `data_mapping` on an edge OUT of the producing node
  names it.  The strongest route: `run_pipeline.gather_step_inputs`
  raises when the key is missing, so the consumption is enforced.
* ``own prompt`` - the producing step's OWN `bridge.py` emits it, or its
  `context_fields` names it.  `run_pipeline.project_step_context`
  restores bridge-supplied keys BY NAME after projection, precisely so a
  pre-bridge's one table cannot be projected away.  Seven outputs travel
  only this way (`cuts_toon`, `cuts_legend`, `vfx_candidates_toon`,
  `sfx_candidates_toon`, `sfx_candidates_legend`, `topics_toon`,
  `transcripts_toon`) and a survey blind to it would report all seven.
* ``code``       - a module outside the producing step's own directory
  names the key as a string literal.  `compile_manifest` reads
  `pipeline_data.json` directly rather than taking the edges' word
  (AGENTS.md 10.1), so this route is not optional either.

What this survey CANNOT see, stated rather than left implicit
-------------------------------------------------------------
The `code` route is a key-NAME match, so it OVER-credits consumption,
and it over-credits in the safe direction: the survey will not fail an
output that really is read, and it will MISS an output whose name
collides with an unrelated reader's.  Three collisions are known and
are recorded in `KNOWN_NAME_COLLISIONS` rather than left for the next
reader to re-derive.

It also cannot see a value that only reaches `summary.md`.
`step_exporter.generate_summary` renders whatever keys an output
happens to carry, generically, so every output is "rendered" and no
output is READ that way.  A count that reaches only the summary is
REPORTED, not consumed, and `REPORTED_NOT_CONSUMED` is where that is
said.

And it is an OUTPUT-level question.  A field INSIDE an output that
nothing reads - which is what the confidence and the caption hash both
were - is not visible here.  `uncalled_functions` is the one field-level
half that is mechanical: a function defined and never called is shape 1
with a name, and it is what `enforce_min_duration` was.

    python3 -m library.tools.output_contract          # the survey
    python3 -m library.tools.output_contract --bad    # disagreements
    python3 -m library.tools.output_contract --uncalled

`tests/test_output_contract.py`.
"""

from __future__ import annotations

import ast
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Sequence, Tuple

_LIBRARY_ROOT = Path(__file__).resolve().parents[1]
_REPO_ROOT = _LIBRARY_ROOT.parent


ROUTE_EDGE = "edge"
ROUTE_OWN_PROMPT = "own prompt"
ROUTE_CODE = "code"


# ── The modules that name a key in order to CLASSIFY it ──────────────
#
# A survey is not a consumer.  These three name output keys in tables
# whose whole purpose is to say something ABOUT the key, and counting
# them as readers would make every output consumed by construction -
# the gate would then be one that cannot fail (AGENTS.md 10.4).
#
# `direction_contradiction` is the reason this list is not empty: its
# MEASURED_OUTPUTS / DECLINED_OUTPUTS tables already account for every
# deterministic output of the DAG, so it names almost all of them.

CLASSIFIERS = (
    "library/tools/direction_contradiction.py",
    "library/tools/input_contract.py",
    "library/tools/output_contract.py",
)


# ── Reported, not consumed ───────────────────────────────────────────
#
# An output nothing reads is not automatically a defect.  A COUNT is
# written for the run's own account of itself and for `summary.md`,
# which renders keys generically; a TERMINAL RECORD is a process's
# answer, and the refusal beside it is the gate.  Both are legitimate,
# and both are indistinguishable from an accident unless they are
# written down.
#
# Widening this table is not a way to make the survey quiet.  A new
# entry means a new value nothing reads, and `disagreements` fails a
# STALE entry too - one whose output has since found a reader.

REPORTED_NOT_CONSUMED = {
    ("validate_sfx_library", "sfx_library_status"):
        "0.01 is an entry node with no outgoing edge. The gate is the "
        "step's own exit code - `step.py` exits 1 on an invalid library "
        "- and the status is the account of what it looked at.",
    ("semantic_analysis", "total_clips_analyzed"):
        "A count of a listing. `direction_contradiction.DECLINED_OUTPUTS` "
        "says the same of it from the other side.",
    ("temporal_index", "total_indexed"):
        "A count. The measurements themselves travel as `full_indices`.",
    ("temporal_index", "total_reused"):
        "A count - how much of the index came off cache. Printed by the "
        "step to stderr; nothing decides on it.",
    ("assign_aroll", "total_a_roll_segments"):
        "A count of what `a_roll_assignments` already carries.",
    ("verify_reels", "reel_verification"):
        "The terminal record of the `reels` process. The GATE is the "
        "raise inside `reel_build.verify_built_reels`; this is what a "
        "run reads back to say which plan and which timelines were "
        "graded rather than only that nothing raised.",
}


# ── Declared, produced, and nothing reads it ─────────────────────────
#
# The findings.  Each says what it COSTS - which decision is made on
# missing data, or which check cannot fire - because that is what a
# ranking has to be made from.
#
# An entry leaves this table by being FIXED, not by being re-worded.

UNREAD_FINDINGS = {
    ("catalog", "source_resolution"):
        "Step 1.02 measures the footage's stored resolution and no step, "
        "no edge and no tool reads the value. "
        "`step_5_04_compile_manifest/step.py:1252` states a read that "
        "does not happen (\"used for conform decisions only\"), and the "
        "only other `source_resolution` in the engine is "
        "`execution/apply_fusion_comps._source_resolution`, which "
        "measures the resolution off Resolve's own media pool item and "
        "never touches the catalog. COST: no decision is made on missing "
        "data today - the Fusion path measures live, which is the more "
        "correct source anyway - but AGENTS.md 5 requires a Background "
        "be sized to the SOURCE clip's resolution, and the comment tells "
        "a reader the manifest path already carries it.",
    ("validate", "final_qa_decision"):
        "Declared as an output of the DAG's exit node, and NOTHING "
        "PRODUCES IT. `step_6_02_validate_output/post_bridge.py:20` "
        "reads `data.get(\"final_qa_decision\", \"\")` - no edge routes "
        "the key, no `handoff.md` asks the model for it, and no default "
        "supplies one - so the value is the empty string on every run "
        "and no reader exists. COST: the verdict a reader would consult "
        "by that name is neither pass nor fail; the real verdict is "
        "`validation_result.status`, which `run_pipeline.py:2147` reads. "
        "Shapes 2 and 3 at once.",
}


# ── Credited by a name that means something else ─────────────────────
#
# The `code` route matches a string literal, so an unrelated reader of
# an unrelated key with the same name credits the output.  These three
# were checked by hand and are wrong; they are written down so the next
# reader does not have to re-derive them, and so the survey's blind spot
# is a list rather than a sentence.
#
# They are NOT failures: over-crediting is the safe direction for a
# gate (it will not fail correct output), and each of these three is a
# count whose real verdict would be REPORTED_NOT_CONSUMED anyway.

KNOWN_NAME_COLLISIONS = {
    ("catalog", "skipped_files"):
        "credited to `step_1_01_scan_project/step.py:99`, which is "
        "SCAN's own `skipped_files` and runs BEFORE the catalog.",
    ("scan", "skipped_files"):
        "credited to `step_1_02_catalog_footage/step.py:297`, which is "
        "the CATALOG's own key of the same name.",
    ("catalog", "total_clips"):
        "credited to `step_1_05_prosody_analysis/step.py:318`, which is "
        "prosody's own count of the profiles it measured.",
}


# ── The survey ───────────────────────────────────────────────────────

@dataclass(frozen=True)
class OutputRow:
    """One declared output, and every route that carries it."""

    process: str
    node: str
    step_dir: str
    name: str
    edge_consumers: Tuple[Tuple[str, str], ...] = ()
    own_prompt: str = ""
    code_readers: Tuple[Tuple[str, int], ...] = ()

    @property
    def routes(self) -> Tuple[str, ...]:
        found = []
        if self.edge_consumers:
            found.append(ROUTE_EDGE)
        if self.own_prompt:
            found.append(ROUTE_OWN_PROMPT)
        if self.code_readers:
            found.append(ROUTE_CODE)
        return tuple(found)

    @property
    def unread(self) -> bool:
        return not self.routes

    @property
    def key(self) -> Tuple[str, str]:
        return (self.node, self.name)

    def evidence(self) -> str:
        if self.edge_consumers:
            return ", ".join(f"{node}.{key}" for node, key in
                             self.edge_consumers)
        if self.own_prompt:
            return self.own_prompt
        if self.code_readers:
            return ", ".join(f"{path}:{line}"
                             for path, line in self.code_readers[:3])
        return ""


def _string_constants(path: Path) -> Dict[str, List[int]]:
    """Every string literal in a file, with the lines it appears on.

    Literals rather than a text search: a key named in a COMMENT is a
    claim about a read, not a read, and `catalog.source_resolution` is
    the finding that distinction produced.
    """
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (OSError, SyntaxError, ValueError):
        return {}
    found: Dict[str, List[int]] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            found.setdefault(node.value, []).append(node.lineno)
    return found


def _python_files() -> List[Path]:
    """The engine's own modules - not the tests.

    A test naming a key is not a consumer: it is a test of the producer,
    and counting it would credit exactly the outputs a producer's own
    tests cover most thoroughly.
    """
    files = [_REPO_ROOT / "manage_project.py"]
    for path in (_REPO_ROOT / "library").rglob("*.py"):
        text = path.as_posix()
        if "__pycache__" in text or "/tests/" in text:
            continue
        if path.name.startswith("test_"):
            continue
        files.append(path)
    return [f for f in files if f.is_file()]


def survey() -> List[OutputRow]:
    """Every declared output of every process, with its routes.

    Reads `library/tools/processes.every_dag`, so a process that exists
    cannot be invisible here the way `reels` was invisible to
    `validate_dag_contracts` (it opened `edit_video/dag.json` by name).
    """
    from library.tools import processes

    constants = {path.relative_to(_REPO_ROOT).as_posix():
                 _string_constants(path) for path in _python_files()}
    for classifier in CLASSIFIERS:
        constants.pop(classifier, None)

    rows: List[OutputRow] = []
    for process_id, dag in processes.every_dag().items():
        node_dir = {node["id"]: node.get("step_ref", "").split("/")[-1]
                    for node in dag.get("nodes", [])}
        edges = dag.get("edges", [])
        for node_id, step_dir in sorted(node_dir.items()):
            manifest_path = (_LIBRARY_ROOT / "steps" / step_dir /
                             "manifest.json")
            if not manifest_path.is_file():
                continue
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            own = f"library/steps/{step_dir}/"

            bridge_path = _LIBRARY_ROOT / "steps" / step_dir / "bridge.py"
            bridge_keys = (_string_constants(bridge_path)
                           if bridge_path.is_file() else {})
            declared_fields = {field_path.split(".")[0].lstrip("-")
                               for field_path in
                               (manifest.get("context_fields") or ())}

            for output in manifest.get("interface", {}).get("outputs", ()):
                name = output.get("name", "")
                if not name:
                    continue
                consumers = tuple(
                    (edge["to"], edge["data_mapping"][name])
                    for edge in edges
                    if edge.get("from") == node_id
                    and name in (edge.get("data_mapping") or {}))
                if name in bridge_keys:
                    prompt = f"{own}bridge.py emits it; restored by name"
                elif name in declared_fields:
                    prompt = "named in this step's own context_fields"
                else:
                    prompt = ""
                readers = tuple(
                    (rel, literals[name][0])
                    for rel, literals in sorted(constants.items())
                    if name in literals and not rel.startswith(own))
                rows.append(OutputRow(
                    process=process_id, node=node_id, step_dir=step_dir,
                    name=name, edge_consumers=consumers, own_prompt=prompt,
                    code_readers=readers))
    return rows


def unread(rows: Sequence[OutputRow] = None) -> List[OutputRow]:
    """The outputs no route carries."""
    return [row for row in (rows if rows is not None else survey())
            if row.unread]


def disagreements(rows: Sequence[OutputRow] = None) -> List[str]:
    """Where the survey and the two tables above do not agree.

    Fails BOTH ways, which is what stops a table from being a way to go
    quiet: an unread output in NEITHER table is a new finding, and an
    entry in either table whose output now HAS a reader is stale and
    must be deleted.  A line that is no longer needed is a lie about
    what is still owed (AGENTS.md 9, on ruff deferrals).
    """
    rows = list(rows if rows is not None else survey())
    found = {row.key for row in rows if row.unread}
    recorded = set(REPORTED_NOT_CONSUMED) | set(UNREAD_FINDINGS)
    live = {row.key for row in rows}

    problems: List[str] = []
    for key in sorted(found - recorded):
        problems.append(
            f"{key[0]}.{key[1]}: declared as an output and NO ROUTE "
            f"carries it - no edge maps it, its own step's prompt does "
            f"not read it, and no module outside "
            f"library/steps/*/ names it. Give it a reader, stop "
            f"declaring it, or record it in REPORTED_NOT_CONSUMED with "
            f"why nothing reads it.")
    for key in sorted(recorded - found):
        if key not in live:
            problems.append(
                f"{key[0]}.{key[1]}: recorded here but no step declares "
                f"that output any more. Delete the entry.")
        else:
            problems.append(
                f"{key[0]}.{key[1]}: recorded as unread and it now HAS a "
                f"reader. Delete the entry - a stale exemption reads as "
                f"coverage.")
    return problems


# ── The field-level half that IS mechanical ─────────────────────────

def uncalled_functions() -> List[Tuple[str, str, int]]:
    """Module-level public functions in `library/` that nothing calls.

    `enforce_min_duration` was "defined, documented and NEVER CALLED"
    (issue #564), and it is why this half exists: a function is the one
    piece of computed-then-dropped that a static reading can see whole.

    Deliberately narrow, so the answer is signal rather than a list to
    scroll past.  Module level only (a method is reached through an
    instance this cannot follow), undecorated (a route handler is called
    by its framework), outside tests, and not named as a string anywhere
    (a dispatch table entry is a call).

    An ALIASED import counts as a call to the name it aliases.  Without
    that, `from library.tools.second_pass import request as
    second_pass_request` made `second_pass.request` read as uncalled -
    and it is the request that drives the entire two-pass music
    measurement, so the report would have claimed the second pass never
    runs.  The instrument was wrong, not the pipeline.


    REPORTED, never failed.  Every entry predates the change that made
    the class visible, and escalating a pre-existing finding is the
    captain's decision - the same posture `input_contract` takes with
    `unread_by_a_prompt_less_step`.
    """
    defined: Dict[str, List[Tuple[str, int]]] = {}
    for path in sorted((_LIBRARY_ROOT).rglob("*.py")):
        text = path.as_posix()
        if "__pycache__" in text or "/tests/" in text:
            continue
        if path.name.startswith("test_"):
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (OSError, SyntaxError, ValueError):
            continue
        rel = path.relative_to(_REPO_ROOT).as_posix()
        for node in tree.body:
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if node.name.startswith("_") or node.decorator_list:
                continue
            defined.setdefault(node.name, []).append((rel, node.lineno))

    called = set()
    named = set()
    roots = [_REPO_ROOT / "manage_project.py"]
    for folder in ("library", "tests", "scripts", "resolve_scripts",
                   "resolve_workflow_integration"):
        root = _REPO_ROOT / folder
        if root.is_dir():
            roots.extend(p for p in root.rglob("*.py")
                         if "__pycache__" not in p.as_posix())
    for path in roots:
        if not path.is_file():
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (OSError, SyntaxError, ValueError):
            continue
        aliases = {}
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                for alias in node.names:
                    if alias.asname:
                        aliases[alias.asname] = alias.name.split(".")[-1]
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                func = node.func
                if isinstance(func, ast.Name):
                    called.add(aliases.get(func.id, func.id))
                elif isinstance(func, ast.Attribute):
                    called.add(aliases.get(func.attr, func.attr))
            elif isinstance(node, ast.Constant) and isinstance(node.value,
                                                               str):
                named.add(node.value)

    findings = []
    for name, sites in sorted(defined.items()):
        if name in called or name in named:
            continue
        for rel, line in sites:
            findings.append((name, rel, line))
    return findings


# ── CLI ──────────────────────────────────────────────────────────────

def format_survey(rows: Sequence[OutputRow]) -> str:
    lines: List[str] = []
    current = ""
    for row in rows:
        if row.node != current:
            lines.append("")
            current = row.node
        route = "/".join(row.routes) or "NOBODY"
        lines.append(f"{row.node:<24} {row.name:<28} {route:<20} "
                     f"{row.evidence()[:70]}")
    orphans = unread(rows)
    lines += [
        "",
        f"{len(rows)} declared outputs across {len({r.node for r in rows})} "
        f"steps in {len({r.process for r in rows})} process(es).",
        f"  carried by an edge          : "
        f"{sum(1 for r in rows if ROUTE_EDGE in r.routes)}",
        f"  read by their own prompt    : "
        f"{sum(1 for r in rows if r.routes and r.routes[0] == ROUTE_OWN_PROMPT)}",
        f"  named by a module elsewhere : "
        f"{sum(1 for r in rows if r.routes == (ROUTE_CODE,))}",
        f"  NO ROUTE AT ALL             : {len(orphans)}",
    ]
    return "\n".join(lines)


def main(argv=None) -> int:
    import argparse
    import sys

    parser = argparse.ArgumentParser(
        description="Who reads what each step produces.")
    parser.add_argument("--bad", action="store_true",
                        help="Print only the disagreements.")
    parser.add_argument("--uncalled", action="store_true",
                        help="Print the never-called functions instead.")
    args = parser.parse_args(argv)

    if args.uncalled:
        findings = uncalled_functions()
        for name, rel, line in findings:
            print(f"  UNCALLED  {name:<40} {rel}:{line}")
        print(f"\n{len(findings)} module-level public function(s) in "
              f"library/ that nothing calls. REPORTED, not failed.")
        return 0

    rows = survey()
    if not args.bad:
        print(format_survey(rows))
        print()
        for key, why in sorted(UNREAD_FINDINGS.items()):
            print(f"FINDING  {key[0]}.{key[1]}")
            print(f"         {why}")
    problems = disagreements(rows)
    for problem in problems:
        print(f"DISAGREEMENT  {problem}", file=sys.stderr)
    if problems:
        print(f"\n{len(problems)} disagreement(s).", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    import sys

    sys.exit(main())
