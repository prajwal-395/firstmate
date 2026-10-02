"""One question, asked of every declared output: WHO READS IT.

`library/tools/input_contract.py` asks who REFUSES when a declared input
is absent.  This is the mirror: whether a value a step PRODUCES reaches
anybody.

The defect class
----------------
Data computed and then not reaching where it was needed comes in four
shapes:

  1. computed then dropped before it is stored
  2. stored then never read
  3. declared then not delivered
  4. delivered then silently ignored - a wrong key, a shadowed variable,
     a rebuilt dict that omits fields

Shape 3 is `input_contract`'s and `requirements`'s.  Shapes 1, 2 and 4
are this module's, and 2 is the one a manifest can answer mechanically:
**a declared output with no reader** (`unread`).  Each known one is
recorded in `UNREAD_FINDINGS` with what it costs; an entry leaves that
table by being FIXED, not by being re-worded.

The routes, and why there are three
-----------------------------------
An output is CONSUMED when at least one route carries it.  Leaving any
of them out makes the survey fail correct output (AGENTS.md 10.4).

* ``edge`` (`ROUTE_EDGE`) - a `data_mapping` on an edge OUT of the
  producing node names it.  `run_pipeline.gather_step_inputs` raises when
  the key is missing, so the consumption is enforced.
* ``own prompt`` (`ROUTE_OWN_PROMPT`) - the producing step's OWN
  `bridge.py` emits it, or its `context_fields` names it.
  `run_pipeline.project_step_context` restores bridge-supplied keys BY
  NAME after projection, so a pre-bridge's own table cannot be projected
  away.
* ``code`` (`ROUTE_CODE`) - a module outside the producing step's own
  directory READS the key: `d.get("k")`, `d["k"]`, `"k" in d`,
  `d.pop("k")`, or the key inside a list handed to a call such as
  `require_keys(data, [...])`.  `compile_manifest` reads
  `pipeline_data.json` directly (AGENTS.md 10.1), so this route is not
  optional either.

  It is a READ position, not a bare name match: a dict-literal KEY is a
  WRITE, a comparison operand is a carve-out, and a call argument is
  usually a step id.  The modules in `CLASSIFIERS` name keys only to say
  something ABOUT them and are never counted as readers.

What this survey CANNOT see, stated rather than left implicit
-------------------------------------------------------------
- Even in read position a key-name match can land on an unrelated dict.
  `KNOWN_NAME_COLLISIONS` SUBTRACTS that credit, so the output falls back
  into the unread set; a collision entry whose output has no readers left
  is stale and `disagreements` says so.
- A value that only reaches `summary.md`:
  `step_exporter.generate_summary` renders every key generically, so a
  count that reaches only the summary is REPORTED, not consumed
  (`REPORTED_NOT_CONSUMED`).
- `run_pipeline.validate_step_output` is NOT a consumer, though it
  touches every declared output: it checks the DECLARATION is honoured,
  not a use of the value.  Crediting it would make every output consumed
  by construction.
- A field INSIDE an output that nothing reads.  `uncalled_functions` is
  the one field-level half that is mechanical: a function defined and
  never called is shape 1 with a name.

**`library/tools/data_map.py` is the field-level half**, and
`library/tools/field_flow.py` is what makes it possible: it follows the
VALUE rather than matching the name.  It also covers the documents that
are not declared step outputs (`data_map.DOCUMENTS`).
`docs/DATA_MAP.md` is the prose half.

    python3 -m library.tools.output_contract          # the survey
    python3 -m library.tools.output_contract --bad    # disagreements
    python3 -m library.tools.output_contract --uncalled

`tests/test_output_contract.py`, `tests/test_field_flow.py`.

The seven defects that motivated this survey and how its read-position
rule was found: docs/evidence/output_contract.md.
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
    ("build_reels", "reel_ask"):
        "`reel.ask`'s receipt: one row per approved reel naming the three "
        "request files it wrote under `llm_requests/`. DECISION: it stays "
        "unread - the host model reads those FILES, never this record. "
        "Declared (2026-10-02) so a project recorded before the "
        "capability records migrates it to `reel.ask` rather than to "
        "`reel.build`, whose record it would otherwise ride in.",

    ("validate_sfx_library", "sfx_library_status"):
        "0.01 is an entry node with no outgoing edge. DECISION: it stays "
        "unread. The gate is the step's own exit code - `step.py` calls "
        "`_fail` and exits 1 on an unplayable or empty library - and the "
        "status is the account of what it looked at "
        "(`{valid, sfx_library_path, entries, playable_entries, "
        "missing_files, catalog_entries, catalog_entries_described}` on "
        "001). Routing it would give a consumer a second, weaker copy of "
        "a refusal that has already happened.",

    ("scan", "total_files"):
        "A count. DECISION: it stays unread - it is `len(raw_footage_files)`, "
        "which travels on every edge out of 1.01. 17 and 17 on 001's run "
        "of record. `direction_contradiction.DECLINED_OUTPUTS` says the "
        "same of it from the other side.",
    ("scan", "skipped_files"):
        "A listing of what was NOT read. DECISION: it stays unread - a "
        "reader would act on the files that ARE in "
        "`raw_footage_files`, and an empty list is the normal answer "
        "(it was `[]` on 001). Until 2026-09-07 the survey credited this "
        "to `step_1_02_catalog_footage/step.py:297`, which is the "
        "CATALOG's own key of the same name being WRITTEN.",

    ("catalog", "total_clips"):
        "A count. DECISION: it stays unread - it is `len(clip_catalog)`, "
        "which is carried by nine edges. 17 and 17 on 001. It was "
        "credited to `step_1_05_prosody_analysis/step.py:318` and "
        "`analysis/vision_pipeline_v3.py:1794`, both of which are those "
        "modules' own counts of their own work.",
    ("catalog", "skipped_files"):
        "The same listing, one step later, and the same decision. It was "
        "credited to `step_1_01_scan_project/step.py:99`, which is SCAN's "
        "own `skipped_files` being written, and runs BEFORE the catalog.",
    ("catalog", "source_resolution"):
        "The footage's modal stored resolution, measured by 1.02. "
        "DECISION (2026-09-07): it stays unread, and it should. Every "
        "decision that needs a resolution needs a PER-CLIP one, and that "
        "travels as `clip_catalog[i].width/height` - which is what "
        "`compile_manifest._conform_fields` reads, and what "
        "`apply_fusion_comps` measures live off the media pool item "
        "instead, at the moment it composites. `source_resolution` is "
        "one project-wide number over 17 clips; giving it the reader its "
        "old comment claimed would put back exactly the grain error the "
        "captain's ruling of 2026-08-19 removed, when this value was "
        "handed to Resolve as the timeline size under the name that "
        "ruling retired, and shipped 001 as a 1920x1080 master with the "
        "vertical overlays banded down the middle. It is kept "
        "because it is a true measurement of the material "
        "(`direction_contradiction.MEASURED_OUTPUTS`) and because "
        "`tests/unit/picture/test_delivery_format.py` reads it as the proof that the "
        "render target is NOT the source. The false comment that sent "
        "the last reader looking - `step_5_04_compile_manifest/step.py`, "
        "\"used for conform decisions only\" - is deleted.",

    ("semantic_analysis", "total_clips_analyzed"):
        "A count of a listing. DECISION: it stays unread - it is "
        "`len(semantic_analysis_documents)`, which is carried by six "
        "edges. 17 and 17 on 001. "
        "`direction_contradiction.DECLINED_OUTPUTS` says the same of it "
        "from the other side.",

    ("temporal_index", "total_indexed"):
        "A count. DECISION: it stays unread - it is "
        "`len(temporal_event_indices)`, and the measurements themselves "
        "travel as `temporal_event_indices` on ten edges. 17 and 17 on 001.",
    ("temporal_index", "total_reused"):
        "A count - how much of the index came off cache. DECISION: it "
        "stays unread; the step prints it to stderr and nothing decides "
        "on it. Its manifest entry now declares `may_be_empty`, because "
        "ZERO IS THE CORRECT VALUE on any run that transcribed fresh - "
        "001's run of record reused nothing - and "
        "`run_pipeline.validate_step_output` reported that honest answer "
        "as `semantically empty`. A check that fails correct output is "
        "the same defect as one that cannot fail (AGENTS.md 10.4).",
    ("temporal_index", "total_failed"):
        "A count of clips the index could not be built for. DECISION: it "
        "stays unread - a failure that matters is already visible as a "
        "missing per-clip index, and 1.04 prints the count. Its ONLY "
        "credit was `run_pipeline.py:2207`, `key != \"total_failed\"` "
        "inside `validate_step_output` - the key named to EXEMPT it from "
        "the zero-is-empty rule, which is an exception to a generic "
        "check and not a read of the value. That single carve-out kept "
        "an unread output out of both tables until 2026-09-07.",
    ("temporal_index", "source"):
        "`\"cache\"` or `\"fresh\"` - which route produced the index. "
        "DECISION: it stays unread; `total_reused` beside it carries the "
        "same fact with a number. Recorded in `KNOWN_NAME_COLLISIONS` "
        "as well, because `source` is too generic a key for a name match "
        "to mean anything: `.get(\"source\")` in "
        "`dashboard/server.py`, `schemas/project_config.py` and "
        "`step_2_04_music_selection/post_bridge.py` all read a different "
        "dict.",

}


# ── Declared, produced, and nothing reads it ─────────────────────────
#
# The findings.  Each says what it COSTS - which decision is made on
# missing data, or which check cannot fire - because that is what a
# ranking has to be made from.
#
# An entry leaves this table by being FIXED, not by being re-worded.
# Two left it on 2026-09-07: `validate.final_qa_decision` was declared
# and never produced, and the declaration and the dead echo are both
# deleted; `verify_reels.reel_verification` now has a reader in
# `run_reels.report_reel_verification`, which names the plan and
# the timelines a build graded instead of printing only that nothing
# raised.

UNREAD_FINDINGS = {
    ("ocr_extraction", "ocr_extraction"):
        "Step 1.07 reads the on-screen text off every frame with EasyOCR "
        "and no step, no edge and no tool reads the result. 1.07 has two "
        "incoming edges and NO outgoing edge. The survey credited it "
        "until 2026-09-07 because the output key and the step id are the "
        "same string, so `StepDir(\"ocr_extraction\", ...)`, "
        "`owning_node=\"ocr_extraction\"` and two dispatch-table keys "
        "read as consumption - while "
        "`run_scope.DESELECTED_BY_DEFAULT[\"ocr_extraction\"]` said in "
        "as many words that nothing consumes it. COST: 445s per run on "
        "001, and the step is DESELECTED BY DEFAULT for exactly this "
        "reason, so the cost is only paid by a run that asks for it with "
        "`--with ocr_extraction`. That standing decision is the "
        "mitigation and it is already in code; what is NOT decided is "
        "whether a planning step should read the text, which is the "
        "captain's call and not this table's.",
}


# ── Credited by a name that means something else ─────────────────────
#
# Even in READ position a key-name match can land on an unrelated dict.
# These entries SUBTRACT the credit: the readers stay on the row, the
# `code` route does not fire, and the output falls back into the unread
# set to be adjudicated like any other.  That is the opposite of what
# this table did before 2026-09-07, when it recorded a known-false
# credit and left it standing - which is a stale exemption reading as
# coverage, the one thing the tables exist to prevent.
#
# An entry here whose output has no `code_readers` at all is stale:
# there is no longer a collision to suppress, and `disagreements` says
# so.  Three of the original entries went that way when the `code` route
# became a read-position match - their credits were dict-literal keys,
# which are writes.

KNOWN_NAME_COLLISIONS = {
    ("temporal_index", "source"):
        "`source` is one of the most generic keys in this tree. The "
        "readers credited to it - `dashboard/server.py:406` "
        "(`config[\"source\"][\"resolution\"]`), "
        "`schemas/project_config.py`, "
        "`step_2_04_music_selection/post_bridge.py:111` "
        "(`selection.get(\"source\") == \"external\"`) and "
        "`step_4_05_render_subtitles/step.py:433` (a brand asset's "
        "source) - read four different dicts, none of them 1.04's "
        "output. Its verdict is in REPORTED_NOT_CONSUMED.",
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
    name_collision: bool = False
    """This output's `code_readers` are recorded in
    `KNOWN_NAME_COLLISIONS` as reading something else of the same name,
    so they do not carry it."""

    @property
    def routes(self) -> Tuple[str, ...]:
        found = []
        if self.edge_consumers:
            found.append(ROUTE_EDGE)
        if self.own_prompt:
            found.append(ROUTE_OWN_PROMPT)
        if self.code_readers and not self.name_collision:
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
        if self.code_readers and not self.name_collision:
            return ", ".join(f"{path}:{line}"
                             for path, line in self.code_readers[:3])
        return ""


def _string_constants(path: Path) -> Dict[str, List[int]]:
    """Every string literal in a file, with the lines it appears on.

    Literals rather than a text search: a key named in a COMMENT is a
    claim about a read, not a read, and `catalog.source_resolution` is
    the finding that distinction produced.

    This is the polarity the OWN-PROMPT route needs - "does this
    pre-bridge EMIT a key of this name" - which is a dict-literal key,
    the opposite of a read.  The `code` route uses `_read_literals`.
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


def _read_literals(path: Path) -> Dict[str, List[int]]:
    """String literals a file uses to READ a value out of something.

    The `code` route asks who READS an output, and a bare name match
    answers a different question: it credits the module that WRITES a
    key of the same name into its own dict, the table that lists the key
    as a step id, and the generic checker that names it only to exempt
    it.  Six outputs were credited that way and none of them was read.

    Five shapes count, and they are the shapes a merged input dict or a
    slice of `pipeline_data.json` is actually opened with:

      ``d.get("k")`` / ``d.pop("k")`` / ``d.setdefault("k", ...)``
      ``d["k"]``
      ``"k" in d``
      ``f(..., ["k", ...], ...)`` - a key inside a list, tuple or set
        handed to a call, which is how `require_keys(data, [...])`
        refuses on an absent one.
      ``capability_outputs.value(state, "capability", "k")`` - one key
        of a capability's recorded output, which is how a reader opens
        `pipeline_data.json` since the records replaced `step_outputs`.

    A dict-literal key, a comparison operand and a bare call argument do
    NOT count.  Widening this beyond a read position is how the survey
    stops being able to fail.
    """
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (OSError, SyntaxError, ValueError):
        return {}
    found: Dict[str, List[int]] = {}

    def note(node: ast.Constant) -> None:
        found.setdefault(node.value, []).append(node.lineno)

    def is_str(node) -> bool:
        return isinstance(node, ast.Constant) and isinstance(node.value, str)

    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            if (isinstance(func, ast.Attribute)
                    and func.attr in ("get", "pop", "setdefault")
                    and node.args and is_str(node.args[0])):
                note(node.args[0])
            if (isinstance(func, ast.Attribute) and func.attr == "value"
                    and isinstance(func.value, ast.Name)
                    and func.value.id == "capability_outputs"
                    and len(node.args) >= 3 and is_str(node.args[2])):
                note(node.args[2])
            # `produces=` declares what a capability WRITES
            # (`operations.Operation.produces`) - the producer's own
            # side, so naming a key there is not reading it.
            for argument in list(node.args) + [kw.value for kw in
                                               node.keywords
                                               if kw.arg != "produces"]:
                if isinstance(argument, (ast.List, ast.Tuple, ast.Set)):
                    for element in argument.elts:
                        if is_str(element):
                            note(element)
        elif isinstance(node, ast.Subscript) and is_str(node.slice):
            note(node.slice)
        elif isinstance(node, ast.Compare) and is_str(node.left):
            if any(isinstance(op, (ast.In, ast.NotIn)) for op in node.ops):
                note(node.left)
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
                 _read_literals(path) for path in _python_files()}
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
                # A collision SUBTRACTS the credit. The readers are kept
                # on the row so `disagreements` can tell a live collision
                # from a stale entry, and `routes` ignores them.
                collided = (node_id, name) in KNOWN_NAME_COLLISIONS
                rows.append(OutputRow(
                    process=process_id, node=node_id, step_dir=step_dir,
                    name=name, edge_consumers=consumers, own_prompt=prompt,
                    code_readers=readers, name_collision=collided))
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

    # And the third table's own staleness. A collision entry SUBTRACTS a
    # credit, so one whose output has no `code_readers` left is
    # suppressing nothing and reads as a blind spot that is still there.
    by_key = {row.key: row for row in rows}
    for key in sorted(KNOWN_NAME_COLLISIONS):
        row = by_key.get(key)
        if row is None:
            problems.append(
                f"{key[0]}.{key[1]}: recorded as a name collision but no "
                f"step declares that output any more. Delete the entry.")
        elif not row.code_readers:
            problems.append(
                f"{key[0]}.{key[1]}: recorded as a name collision and "
                f"nothing names it any more, so there is no credit left "
                f"to subtract. Delete the entry.")
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
    for folder in ("library", "tests", "scripts"):
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
