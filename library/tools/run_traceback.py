"""
run_traceback.py - the project folder, read back as the story of a run.

The captain is walking all 28 steps of this pipeline by hand and needs to
open the folder and read what each step did without anyone narrating it.
That is two documents, both generated - never hand-written - from state
the pipeline already keeps:

``pipeline_output/RUN-TRACEBACK.md``
    The steps in execution order.  For each: when it ran, how long it
    took, whether it worked, what it consumed and from which step, what
    data it produced, and what files it left on disk.

``pipeline_output/ARTIFACTS.md``
    The other direction.  Every file under the output root with the step
    that wrote it, how that was established, and what the file itself
    names as its source.  This is the one to open when the question is
    "what IS this".

Four sources, no invention
--------------------------
* ``dag.json`` - the order of the steps and, in its 99 edges, which keys
  each step consumes and which step produces them.  A declaration about
  the pipeline, and the only honest answer to "what did this step read"
  for a run nobody was watching.
* the two ledgers in ``pipeline_data.json`` - ``completed_at`` and
  ``elapsed_s`` per step, which is a recorded fact about a real run.
* ``failed_steps`` / ``step_errors`` - whether it worked, and why not.
* the provenance ledger - which run wrote which file
  (``library/tools/provenance.py``).

Where a link is not recorded, these documents say it is not recorded.
The alternative - deriving it from a filename that looks right - is the
thing an audit cannot afford.


Rules relocated from AGENTS.md 8
--------------------------------
These are the engine's rules for this module.  They lived in
AGENTS.md until it was split by subsystem; the wording is unchanged,
so each rule is findable by its own words, and AGENTS.md 8
keeps the headline and points here.

**Two generated documents, regenerated on every run and by `manage_project.py trace <slug>`.**
`library/tools/run_traceback.py`. `RUN-TRACEBACK.md` is the steps in order - when, how long, what it consumed and from which step, what it produced. `ARTIFACTS.md` is the other direction: every file, with the step that wrote it and how that was established.
- Both are generated from `dag.json`, the two ledgers, `step_errors` and the provenance ledger.  `unwired_step_ids` matches by `step_ref`, because the DAG calls `step_1_01_scan_project` simply `scan`.
- `tests/test_run_traceback.py`.
"""

from __future__ import annotations

import json
import time
from collections import Counter, defaultdict
from pathlib import Path

from library.tools import step_ledger
from library.tools.project_layout import (
    AREAS,
    STEPS,
    Area,
    Kind,
    ProjectLayout,
)
from library.tools.provenance import (
    DECLARED,
    METHOD_MEANING,
    OBSERVED,
    UNKNOWN,
    ProvenanceLedger,
)

TRACEBACK_FILE = "RUN-TRACEBACK.md"
ARTIFACT_INDEX_FILE = "ARTIFACTS.md"

_DAG_PATH = (Path(__file__).resolve().parents[2]
             / "library" / "processes" / "edit_video" / "dag.json")


def load_dag(path=None) -> dict:
    return json.loads(Path(path or _DAG_PATH).read_text(encoding="utf-8"))


def unwired_step_ids(dag=None) -> set:
    """Steps the layout gives a directory that no DAG node runs.

    One does: `object_segmentation` (1.06) is implemented and unwired
    (AGENTS.md section 3). It still gets a directory, because it still
    has somewhere its output would land - and the reader has to be told
    nothing puts anything there.

    `ocr_extraction` (1.07) is NOT here. It has a DAG node; it is
    deselected by default, which is a property of a RUN and not of the
    pipeline. `library/tools/run_scope.DESELECTED_BY_DEFAULT` carries
    that, and the run summary reports it on every run.
    """
    dag = dag or load_dag()
    return ({s.node_id for s in STEPS}
            - {n["id"] for n in dag.get("nodes", [])})


def implemented_step_ids(dag=None) -> set:
    """Every step the repository has, named the way the layout names it."""
    return {s.node_id for s in STEPS}


def _consumption(dag: dict) -> dict:
    """`{step: {input_key: [producing_step, ...]}}` straight off the edges."""
    out = defaultdict(lambda: defaultdict(list))
    for edge in dag.get("edges", []):
        src, dst = edge.get("from"), edge.get("to")
        for in_key in (edge.get("data_mapping") or {}).values():
            if src not in out[dst][in_key]:
                out[dst][in_key].append(src)
    return out


def _step_status(state: dict, node_id: str) -> dict:
    """When a step ran, how long it took, and whether it worked."""
    entry = None
    stage = None
    for st in (step_ledger.PREFLIGHT, step_ledger.EDIT):
        ledger = state.get(step_ledger.LEDGER_KEY[st], {}) or {}
        if node_id in ledger:
            entry, stage = ledger[node_id], st
            break
    failed = node_id in (state.get("failed_steps") or [])
    error = (state.get("step_errors") or {}).get(node_id, "")
    if failed:
        verdict = "FAILED"
    elif entry:
        verdict = "completed"
    else:
        verdict = "not run"
    return {
        "verdict": verdict,
        "stage": stage,
        "completed_at": (entry or {}).get("completed_at", ""),
        "elapsed_s": (entry or {}).get("elapsed_s"),
        "note": (entry or {}).get("note", ""),
        "error": error,
    }


def _one_line(text, limit: int = 320) -> str:
    """A multi-line error as one readable clause.

    Taking only the first line loses the answer: `validate`'s error opens
    with "Step failed (exit 1):" and the reason - the LUFS and true-peak
    numbers - is two lines further down.
    """
    joined = " ".join(part.strip() for part in str(text).splitlines()
                      if part.strip())
    return (joined[:limit] + "...") if len(joined) > limit else joined or "(no message)"


def _fmt_duration(seconds) -> str:
    if seconds is None:
        return "-"
    seconds = float(seconds)
    if seconds < 60:
        return f"{seconds:.1f}s"
    return f"{int(seconds // 60)}m {seconds % 60:.0f}s"


def _rel(root: Path, p) -> str:
    try:
        return Path(str(p)).resolve().relative_to(root).as_posix()
    except (ValueError, OSError):
        return str(p)


def build_traceback(project_folder, dag=None) -> dict:
    """Everything both documents need, as data.  Reads only."""
    layout = (project_folder if isinstance(project_folder, ProjectLayout)
              else ProjectLayout(project_folder))
    dag = dag or load_dag()
    ledger = ProvenanceLedger(
        layout, step_ids=[n["id"] for n in dag.get("nodes", [])])

    state_path = layout.pipeline_data_path
    state = (json.loads(state_path.read_text(encoding="utf-8"))
             if state_path.is_file() else {})

    artifacts = ledger.all_artifacts()
    by_step = defaultdict(list)
    ambiguous = defaultdict(list)
    for rec in artifacts:
        if rec.step_id:
            by_step[rec.step_id].append(rec)
        else:
            # A file whose area declares several producers belongs to
            # none of them until a run says which. It is reported under
            # each candidate AS ambiguous rather than assigned to one.
            for cand in rec.candidates:
                ambiguous[cand].append(rec)

    consumption = _consumption(dag)
    step_outputs = state.get("step_outputs", {}) or {}

    steps = []
    for i, node in enumerate(dag.get("nodes", []), 1):
        node_id = node["id"]
        status = _step_status(state, node_id)
        files = by_step.get(node_id, [])
        area_counts = Counter(r.area for r in files)
        output = step_outputs.get(node_id)
        steps.append({
            "position": i,
            "id": node_id,
            "name": node.get("name", node_id),
            "step_ref": node.get("step_ref", ""),
            "status": status,
            "consumed": dict(consumption.get(node_id, {})),
            "produced_keys": sorted(output) if isinstance(output, dict) else [],
            "has_output": output is not None,
            "files": files,
            "file_area_counts": dict(area_counts),
            "methods": Counter(r.method for r in files),
            "derived_from_count": sum(1 for r in files if r.derived_from),
            # The runner's own <step_id>.json / .summary.md exports are
            # not derived artifacts, so "this file names no source" is
            # not a finding about them.
            "derivable_files": sum(1 for r in files
                                   if r.area != Area.OUTPUT_ROOT.value),
            "ambiguous": ambiguous.get(node_id, []),
        })

    dag_step_ids = {n["id"] for n in dag.get("nodes", [])}
    return {
        "project": str(layout.root),
        "dag_step_ids": dag_step_ids,
        "unwired_steps": sorted(unwired_step_ids(dag=dag)),
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "state_exists": state_path.is_file(),
        "started_at": state.get("started_at", ""),
        "last_updated": state.get("last_updated", ""),
        "runs": ledger.runs(),
        "steps": steps,
        "artifacts": artifacts,
        "archived_file_count": ledger.archived_file_count(),
        "source_fingerprints": state.get("source_fingerprints", {}) or {},
        "layout": layout,
    }


# ── RUN-TRACEBACK.md ────────────────────────────────────────────────

def render_traceback(data: dict) -> str:
    root = Path(data["project"])
    runs = data["runs"]
    observed_total = sum(1 for r in data["artifacts"] if r.method == OBSERVED)
    declared_total = sum(1 for r in data["artifacts"] if r.method == DECLARED)
    unknown_total = sum(1 for r in data["artifacts"] if r.method == UNKNOWN)

    L = [
        "# What this pipeline did, step by step",
        "",
        "Generated by `library/tools/run_traceback.py` from `pipeline_data.json`,",
        "`dag.json` and the provenance ledger. Do not edit it - re-generate it",
        "with `python3 manage_project.py trace <project>`.",
        "",
        f"Project: `{root}`",
        f"Generated: {data['generated_at']}",
    ]
    if data["started_at"]:
        L.append(f"First run started: {data['started_at']}")
    if data["last_updated"]:
        L.append(f"State last written: {data['last_updated']}")
    L.append("")

    L += [
        "## How much of this was recorded, and how much is reconstructed",
        "",
        "Every artifact below carries the method that attributed it, because an",
        "audit that cannot tell a measurement from a declaration is not an audit.",
        "",
        "| method | files | means |",
        "|---|---:|---|",
        f"| `{OBSERVED}` | {observed_total} | {METHOD_MEANING[OBSERVED]} |",
        f"| `{DECLARED}` | {declared_total} | {METHOD_MEANING[DECLARED]} |",
        f"| `{UNKNOWN}` | {unknown_total} | {METHOD_MEANING[UNKNOWN]} |",
        "",
    ]
    if not observed_total:
        L += [
            "**No run has been observed yet in this project.** Everything below",
            "was reconstructed after the fact: the timings and verdicts are real",
            "recorded facts from the ledgers in `pipeline_data.json`, but which",
            "step wrote which FILE is the layout's declaration, not an",
            "observation of a run. The next run records the stronger kind.",
            "",
        ]
    if data["archived_file_count"]:
        L += [
            f"{data['archived_file_count']} further files sit under",
            "`pipeline_output/backups/`. They are copies of other runs, so",
            "attributing them to the step that wrote the original would be a",
            "false link. They are counted here and not listed.",
            "",
        ]

    L += ["## Runs", ""]
    if runs:
        L += ["| run | started | ended | mode | status | steps |",
              "|---|---|---|---|---|---:|"]
        for r in runs:
            L.append(f"| `{r.run_id}` | {r.started_at or '-'} | "
                     f"{r.ended_at or '-'} | {r.mode or '-'} | "
                     f"{r.status or '-'} | {len(r.steps)} |")
    else:
        L.append("No run has been recorded through the provenance ledger yet. "
                 "The ledgers in `pipeline_data.json` still say when each step "
                 "last completed, which is what the table below reports.")
    L.append("")

    L += ["## The steps, in execution order", ""]
    for st in data["steps"]:
        s = st["status"]
        L.append(f"### {st['position']}. `{st['id']}` - {st['name']}")
        L.append("")
        L.append(f"- **source**: `library/{st['step_ref']}`" if st["step_ref"]
                 else "- **source**: (not declared in the DAG)")

        if s["verdict"] == "completed":
            stage = f", {s['stage']} stage" if s["stage"] else ""
            L.append(f"- **result**: completed {s['completed_at']} in "
                     f"{_fmt_duration(s['elapsed_s'])}{stage}")
        elif s["verdict"] == "FAILED":
            L.append(f"- **result**: **FAILED** - {_one_line(s['error'])}")
        else:
            L.append("- **result**: not run - no entry in either ledger")
        if s["note"]:
            L.append(f"- **note**: {s['note']}")

        if st["consumed"]:
            parts = [
                f"`{k}` from " + ", ".join(f"`{p}`" for p in v)
                for k, v in sorted(st["consumed"].items())]
            L.append(f"- **consumed**: {'; '.join(parts)}")
        else:
            L.append("- **consumed**: nothing - the DAG gives it no inbound edge")

        if st["produced_keys"]:
            keys = ", ".join(f"`{k}`" for k in st["produced_keys"])
            L.append(f"- **produced (data)**: {keys} - in "
                     f"`pipeline_data.json` under `step_outputs.{st['id']}`")
        elif st["has_output"]:
            L.append(f"- **produced (data)**: `step_outputs.{st['id']}` "
                     f"(not an object)")
        else:
            L.append("- **produced (data)**: nothing recorded in `step_outputs`")

        if st["files"]:
            counts = ", ".join(
                f"{n} in `{AREAS[Area(a)].relpath}/`" if a else f"{n} unfiled"
                for a, n in sorted(st["file_area_counts"].items(),
                                   key=lambda kv: (kv[0] or "")))
            methods = ", ".join(f"{n} {m}" for m, n in st["methods"].most_common())
            L.append(f"- **produced (files)**: {len(st['files'])} - {counts} "
                     f"({methods})")
            if st["derived_from_count"]:
                L.append(f"- **named sources**: {st['derived_from_count']} of "
                         f"{len(st['files'])} files record what they were made "
                         f"from; see `{ARTIFACT_INDEX_FILE}`")
            elif st["derivable_files"]:
                L.append("- **named sources**: none of these files records what "
                         "it was made from, so the link to a clip is not "
                         "recorded and is not inferred here")
        else:
            L.append("- **produced (files)**: none attributed to it")

        if st["ambiguous"]:
            amb = st["ambiguous"]
            others = sorted({c for r in amb for c in r.candidates}
                            - {st["id"]})
            with_whom = (" or " + " or ".join(f"`{o}`" for o in others)
                         if others else " or another step")
            areas = sorted({r.area for r in amb if r.area})
            where = ", ".join(f"`{AREAS[Area(a)].relpath}/`" for a in areas)
            L.append(f"- **shared, not attributed**: {len(amb)} file(s) in "
                     f"{where} are declared to come from this step"
                     f"{with_whom}. Which of them wrote each file is not "
                     f"recorded, and no run has been observed that would "
                     f"settle it.")
        L.append("")

    fps = data["source_fingerprints"]
    if fps:
        L += ["## The footage every clip id refers to", "",
              "From `source_fingerprints` in `pipeline_data.json`. The digest is",
              "what invalidates a clip's cached analysis when the file behind it",
              "changes (AGENTS.md section 3).", "",
              "| clip | file | bytes |", "|---|---|---:|"]
        for clip in sorted(fps):
            fp = fps[clip]
            L.append(f"| `{clip}` | `{_rel(root, fp.get('path', ''))}` | "
                     f"{fp.get('size_bytes', 0):,} |")
        L.append("")
    return "\n".join(L)


# ── ARTIFACTS.md ────────────────────────────────────────────────────

def render_artifact_index(data: dict) -> str:
    root = Path(data["project"])
    artifacts = data["artifacts"]
    by_area = defaultdict(list)
    for rec in artifacts:
        by_area[rec.area].append(rec)

    L = [
        "# Every file the pipeline produced, and where it came from",
        "",
        "Generated by `library/tools/run_traceback.py`. Do not edit it -",
        "re-generate it with `python3 manage_project.py trace <project>`.",
        "",
        f"Project: `{root}`",
        f"Generated: {data['generated_at']}",
        f"Files listed: {len(artifacts)}",
        "",
        "`step` is the step that wrote the file. `how` is what that is based on:",
        "",
    ]
    for method in (OBSERVED, DECLARED, UNKNOWN):
        L.append(f"- `{method}` - {METHOD_MEANING[method]}")
    L += [
        "",
        "`from` is what the file ITSELF names as its source. It is read out of",
        "the file, never inferred from its name, so a blank means the link is",
        "not recorded rather than that there is none.",
        "",
    ]
    if data["archived_file_count"]:
        L += [(f"Not listed: {data['archived_file_count']} files under "
               "`pipeline_output/backups/`, which are copies of other runs."),
              ""]

    for area_value in sorted(by_area, key=lambda a: a or ""):
        recs = by_area[area_value]
        if area_value:
            spec = AREAS[Area(area_value)]
            L += [f"## `{spec.relpath}/`", "", spec.purpose, ""]
            if spec.kind is Kind.INPUT:
                L += ["This is an input area; the pipeline does not write here.",
                      ""]
            unwired = [s for s in spec.produced_by
                       if s in data["unwired_steps"]]
            if unwired:
                names = ", ".join(f"`{s}`" for s in unwired)
                L += [(f"{names} is implemented but is NOT wired into the DAG, "
                       f"so no run of this pipeline produces anything here. "
                       f"Anything present came from somewhere else."), ""]
        else:
            L += ["## Outside every named area", "",
                  "The layout does not name a place for these.", ""]
        L += [f"{len(recs)} file(s).", "",
              "| file | bytes | step | how | run | from |",
              "|---|---:|---|---|---|---|"]
        for r in recs:
            name = r.path.split("/")[-1]
            sources = ", ".join(f"`{_rel(root, s)}`" for s in r.derived_from)
            if r.step_id:
                step_cell = f"`{r.step_id}`"
            elif r.candidates:
                step_cell = " or ".join(f"`{c}`" for c in r.candidates)
            else:
                step_cell = "-"
            run_cell = f"`{r.run_id}`" if r.run_id else "-"
            L.append(f"| `{name}` | {r.bytes:,} | {step_cell} | "
                     f"`{r.method}` | {run_cell} | {sources or '-'} |")
        L.append("")

    unknown = [r for r in artifacts if r.method == UNKNOWN]
    if unknown:
        L += ["## Still unaccounted for", "",
              "No run record and no declaration explains these. That is the",
              "honest answer, and it is left as the answer.", "",
              "| file | bytes |", "|---|---:|"]
        for r in unknown:
            L.append(f"| `{r.path}` | {r.bytes:,} |")
        L.append("")
    return "\n".join(L)


# ── Writing them ────────────────────────────────────────────────────

def write_traceback(project_folder, dag=None) -> dict:
    """Regenerate both documents.  Returns their paths and a summary."""
    data = build_traceback(project_folder, dag=dag)
    layout = data["layout"]
    tb = layout.write_path(Area.OUTPUT_ROOT, TRACEBACK_FILE)
    ix = layout.write_path(Area.OUTPUT_ROOT, ARTIFACT_INDEX_FILE)
    tb.write_text(render_traceback(data), encoding="utf-8")
    ix.write_text(render_artifact_index(data), encoding="utf-8")
    return {
        "traceback": str(tb),
        "artifact_index": str(ix),
        "steps": len(data["steps"]),
        "artifacts": len(data["artifacts"]),
        "observed": sum(1 for r in data["artifacts"] if r.method == OBSERVED),
        "declared": sum(1 for r in data["artifacts"] if r.method == DECLARED),
        "unknown": sum(1 for r in data["artifacts"] if r.method == UNKNOWN),
    }
