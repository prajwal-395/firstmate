"""Every step's intermediate output, readable, without dumping it.

The captain's stated purpose for the panel: *trace all of the
intermediate outputs so we can see if everything is lining up*.

Why a drill-down and not a document
-----------------------------------
001's `temporal_index` output is 2.89 MB.  Rendering it into a widget is
not a feature - it is a wall the captain scrolls past.  So a step's
output is navigated one LEVEL at a time: the keys at the current path,
each with its kind, its size and a one-line preview, and descending is a
click.  Nothing below the current level is serialised.

Three rules this holds to, and they are the same three the rest of this
repository holds to:

* **A bound is stated, never silent.**  A leaf that does not fit in the
  budget says how many bytes were withheld and what path they are at, so
  a truncation can never be mistaken for the end of the value.
* **A path that does not exist is refused BY NAME.**  Walking into a
  missing key raises `PathError` naming the path and what was there
  instead, rather than coming back empty - an empty view of a mistyped
  path reads exactly like a step that produced nothing.
* **The preview is a reading, not a re-serialisation.**  A dict's
  preview is its key names; a list's is its length and its first item's
  shape.  It never costs the subtree.

Two places a step's output lives, and the panel says which
----------------------------------------------------------
`pipeline_data.json` holds `step_outputs[<node_id>]` and is what
`compile_manifest` reads; `pipeline_output/steps/<dir>/output.json` is
the dashboard export, and a missing one reads as `{}` (AGENTS.md 10.1).
They can disagree - the export is best-effort - so the trace offers BOTH
and labels each, rather than picking one and being quietly wrong.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Any, List, Optional, Sequence, Tuple

from library.tools import project_layout, step_ledger
from library.tools.project_layout import Area, ProjectLayout


class PathError(KeyError):
    """A drill-down path that does not exist, refused by name."""


# How much of a leaf value is rendered before the rest is WITHHELD and
# said to be withheld. Mechanical: a screen holds a few thousand
# characters and the panel is a reading surface, not an editor.
LEAF_BUDGET_BYTES = 12000

# How many entries of one level are listed before the rest are counted
# rather than drawn. A level with 40,000 keys is a real shape and the
# count is the useful fact about it.
LEVEL_LIMIT = 400

# Where a step's output can be read FROM. Both, always labelled.
FROM_STATE = "pipeline_data.json"
FROM_EXPORT = "output.json"


# ── The step list ────────────────────────────────────────────────────

@dataclass(frozen=True)
class StepRow:
    """One step, as the trace shows it."""

    node_id: str
    dirname: str
    status: str
    """`done`, `failed`, `pending`, or `unwired` for a step no DAG node
    runs."""

    ledger: str
    """`preflight`, `edit` or `-`."""

    wired: bool
    files: int
    bytes: int
    has_state_output: bool
    has_export: bool
    error: str = ""
    directory: str = ""


def step_rows(project_folder: str, state: Optional[dict] = None
              ) -> List[StepRow]:
    """Every step of this pipeline, in RUN ORDER, with what it left.

    Driven by `project_layout.STEPS` rather than by listing the steps
    directory, so a step that has never run is a row saying `pending`
    instead of being absent - which is the difference between "nothing
    ran it" and "it is not part of this pipeline".
    """
    state = state if state is not None else read_state(project_folder)
    layout = ProjectLayout(project_folder)
    outputs = (state or {}).get("step_outputs") or {}
    errors = (state or {}).get("step_errors") or {}
    failed = set((state or {}).get("failed_steps") or [])
    completed = step_ledger.all_completed(state or {})

    rows: List[StepRow] = []
    for step in project_layout.STEPS:
        node_id = step.node_id
        directory = str(layout.read_dir(Area.STEPS_ROOT) / step.dirname)
        files, size = _directory_size(directory)
        if not step.wired:
            status = "unwired"
        elif node_id in failed:
            status = "failed"
        elif node_id in completed:
            status = "done"
        else:
            status = "pending"
        rows.append(StepRow(
            node_id=node_id,
            dirname=step.dirname,
            status=status,
            ledger=_ledger_of(state or {}, node_id),
            wired=step.wired,
            files=files,
            bytes=size,
            has_state_output=isinstance(outputs.get(node_id), (dict, list)),
            has_export=os.path.isfile(
                os.path.join(directory, project_layout.STEP_OUTPUT_FILE)),
            error=str(errors.get(node_id) or "")[:600],
            directory=directory,
        ))
    return rows


def counts(rows: Sequence[StepRow]) -> dict:
    out = {"total": len(rows), "done": 0, "failed": 0, "pending": 0,
           "unwired": 0}
    for row in rows:
        out[row.status] = out.get(row.status, 0) + 1
    return out


def stranded_failures(rows: Sequence[StepRow],
                      state: Optional[dict]) -> List[str]:
    """Recorded failures of steps this DAG no longer runs.

    `failed_steps` is cleared when a step SUCCEEDS, so a step with no DAG
    node can never clear one - unwiring `prosody_analysis` left exactly
    that on 001. The runner names them first and says no run can clear
    them (AGENTS.md section 3), and a panel that quietly counted them as
    `unwired` and moved on would be the going-quiet that rule forbids.
    """
    unwired = {row.node_id for row in rows if not row.wired}
    return [node for node in sorted(set((state or {}).get("failed_steps") or []))
            if node in unwired
            or node not in {row.node_id for row in rows}]


def _ledger_of(state: dict, node_id: str) -> str:
    for stage in (step_ledger.PREFLIGHT, step_ledger.EDIT):
        if node_id in (state.get(step_ledger.LEDGER_KEY[stage]) or {}):
            return stage
    return "-"


def _directory_size(directory: str) -> Tuple[int, int]:
    if not os.path.isdir(directory):
        return 0, 0
    files = 0
    size = 0
    for root, _dirs, names in os.walk(directory):
        for name in names:
            files += 1
            try:
                size += os.path.getsize(os.path.join(root, name))
            except OSError:
                pass
    return files, size


def read_state(project_folder: str) -> dict:
    """The 4.5 MB read. The entry point does this on a worker thread."""
    path = ProjectLayout(project_folder).pipeline_data_path
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, ValueError):
        return {}


# ── Where a step's output can be read from ───────────────────────────

@dataclass(frozen=True)
class Source:
    """One place a step's output can be read, and what is there."""

    name: str
    path: str
    available: bool
    note: str = ""


def sources_for(project_folder: str, row: StepRow) -> List[Source]:
    """Both readings, labelled, never merged.

    The two can legitimately differ - the per-step export is best-effort
    and a failed export leaves a stale or absent file while the state
    file is correct (AGENTS.md 10.1). Offering one and calling it "the
    output" is how that difference goes unnoticed.
    """
    return [
        Source(FROM_STATE,
               str(ProjectLayout(project_folder).pipeline_data_path),
               row.has_state_output,
               "what compile_manifest and every consumer actually read"),
        Source(FROM_EXPORT,
               os.path.join(row.directory, project_layout.STEP_OUTPUT_FILE),
               row.has_export,
               "the dashboard export - best effort, and a missing one "
               "reads as {} downstream"),
    ]


def load_output(project_folder: str, row: StepRow, source: str,
                state: Optional[dict] = None) -> Any:
    """The step's output from ONE named source, or raise.

    An unknown source name raises rather than defaulting: a trace that
    quietly showed the other file would be answering a different
    question from the one asked.
    """
    if source == FROM_STATE:
        state = state if state is not None else read_state(project_folder)
        outputs = (state or {}).get("step_outputs") or {}
        if row.node_id not in outputs:
            raise PathError(
                f"{row.node_id} has no entry under step_outputs in "
                f"pipeline_data.json. The step has not recorded an output "
                f"in this project.")
        return outputs[row.node_id]
    if source == FROM_EXPORT:
        path = os.path.join(row.directory, project_layout.STEP_OUTPUT_FILE)
        if not os.path.isfile(path):
            raise PathError(
                f"{row.node_id} has no {project_layout.STEP_OUTPUT_FILE} in "
                f"{row.directory}. The export is best-effort and this one "
                f"was never written.")
        with open(path, "r", encoding="utf-8") as handle:
            return json.load(handle)
    raise PathError(
        f"unknown source {source!r}. A step's output can be read from: "
        f"{FROM_STATE}, {FROM_EXPORT}.")


# ── The drill-down ───────────────────────────────────────────────────

@dataclass(frozen=True)
class Entry:
    """One row of the current level."""

    key: str
    """The key, or `[3]` for a list index."""

    step: Any
    """What to append to the path to descend: a `str` key or an `int`."""

    kind: str
    size: str
    preview: str
    descendable: bool


def walk(document: Any, path: Sequence[Any]) -> Any:
    """The value at `path`, or raise `PathError` naming where it broke.

    A missing key is refused rather than returning `{}`, because an empty
    view of a mistyped path reads exactly like a step that produced
    nothing - which is the confusion this whole surface exists to remove.
    """
    value = document
    walked: List[Any] = []
    for step in path:
        walked.append(step)
        here = "/".join(str(p) for p in walked)
        if isinstance(value, dict):
            if step not in value:
                raise PathError(
                    f"no {step!r} at {here}. What is there: "
                    f"{', '.join(sorted(map(str, value))[:12]) or '(nothing)'}")
            value = value[step]
        elif isinstance(value, list):
            try:
                index = int(step)
            except (TypeError, ValueError):
                raise PathError(
                    f"{here} is a list of {len(value)}; {step!r} is not an "
                    f"index into it.") from None
            if not -len(value) <= index < len(value):
                raise PathError(
                    f"{here} is a list of {len(value)}; index {index} is "
                    f"outside it.")
            value = value[index]
        else:
            raise PathError(
                f"{here} cannot be descended into - what is above it is a "
                f"{type(value).__name__}, which has no members.")
    return value


def entries(value: Any, limit: int = LEVEL_LIMIT
            ) -> Tuple[List[Entry], int]:
    """The rows of one level, and how many were NOT listed.

    Returns `([], 0)` for a leaf. The withheld count is returned rather
    than logged, because the caller has to say it on screen.
    """
    if isinstance(value, dict):
        keys = list(value)
        shown = keys[:limit]
        return ([Entry(str(k), k, kind_of(value[k]), size_of(value[k]),
                       preview(value[k]), is_descendable(value[k]))
                 for k in shown],
                len(keys) - len(shown))
    if isinstance(value, list):
        shown = value[:limit]
        return ([Entry("[%d]" % i, i, kind_of(v), size_of(v), preview(v),
                       is_descendable(v))
                 for i, v in enumerate(shown)],
                len(value) - len(shown))
    return [], 0


def is_descendable(value: Any) -> bool:
    return isinstance(value, (dict, list)) and len(value) > 0


def kind_of(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "bool"
    return type(value).__name__


def size_of(value: Any) -> str:
    if isinstance(value, dict):
        return "%d key%s" % (len(value), "" if len(value) == 1 else "s")
    if isinstance(value, list):
        return "%d item%s" % (len(value), "" if len(value) == 1 else "s")
    if isinstance(value, str):
        return "%d char%s" % (len(value), "" if len(value) == 1 else "s")
    return ""


def preview(value: Any, width: int = 90) -> str:
    """One line describing a value, WITHOUT serialising its subtree.

    A dict previews as its key names, a list as the shape of its first
    item. Rendering the subtree here is what makes a "summary" view cost
    the same as a dump.
    """
    if isinstance(value, dict):
        if not value:
            return "{}"
        keys = ", ".join(str(k) for k in list(value)[:8])
        if len(value) > 8:
            keys += ", ..."
        return "{ %s }" % _clip(keys, width - 4)
    if isinstance(value, list):
        if not value:
            return "[]"
        first = value[0]
        if isinstance(first, dict):
            keys = ", ".join(str(k) for k in list(first)[:6])
            return "[%d x { %s%s }]" % (len(value), _clip(keys, width - 20),
                                        ", ..." if len(first) > 6 else "")
        if isinstance(first, list):
            return "[%d x [%d]]" % (len(value), len(first))
        return "[%d x %s] %s" % (len(value), kind_of(first),
                                 _clip(str(first), 30))
    if value is None:
        return "null"
    if isinstance(value, str):
        return _clip(value.replace("\n", " "), width)
    return _clip(str(value), width)


def _clip(text: str, width: int) -> str:
    text = str(text)
    return text if len(text) <= width else text[:max(0, width - 3)] + "..."


@dataclass(frozen=True)
class Leaf:
    """A value rendered whole, or the part of it that fit."""

    text: str
    withheld_bytes: int = 0
    path: str = ""

    @property
    def truncated(self) -> bool:
        return self.withheld_bytes > 0

    def note(self) -> str:
        """The sentence that keeps a truncation from reading as an end."""
        if not self.truncated:
            return ""
        return (f"{self.withheld_bytes:,} more bytes at {self.path} are not "
                f"shown. Descend into it, or read the file - nothing here "
                f"is a summary of what was withheld.")


def render_leaf(value: Any, path: Sequence[Any] = (),
                budget: int = LEAF_BUDGET_BYTES) -> Leaf:
    """A value as text, bounded, SAYING what did not fit."""
    if isinstance(value, str):
        text = value
    else:
        try:
            text = json.dumps(value, indent=1, default=str)
        except (TypeError, ValueError):
            text = repr(value)
    where = "/".join(str(p) for p in path) or "(root)"
    if len(text) <= budget:
        return Leaf(text, 0, where)
    return Leaf(text[:budget], len(text) - budget, where)


def breadcrumb(path: Sequence[Any], root: str = "output") -> str:
    return " / ".join([root] + [str(p) for p in path])


# ── The whole reading of one step, for the view to draw ──────────────

@dataclass
class StepTrace:
    """What the trace view needs to draw one step at one path."""

    row: StepRow
    source: str
    path: Tuple[Any, ...] = ()
    value: Any = None
    entries: List[Entry] = field(default_factory=list)
    withheld_entries: int = 0
    leaf: Optional[Leaf] = None
    error: str = ""
    other_files: List[str] = field(default_factory=list)

    def value_of(self, entry: Entry) -> Any:
        """The value one entry of this level names.

        The level is already in hand, so reading an entry costs nothing
        beyond what the level cost - which is the point of the whole
        drill-down. Raises `PathError` if the entry is not of this level,
        rather than answering about something else.
        """
        return walk(self.value, (entry.step,))


def trace_step(project_folder: str, row: StepRow, source: str = FROM_STATE,
               path: Sequence[Any] = (), state: Optional[dict] = None
               ) -> StepTrace:
    """One step, at one path, ready to draw.

    Everything that can fail - a missing output, a bad path - comes back
    as `error` on the result rather than as an exception, because the
    view has to draw something and "this is why there is nothing here"
    is the thing worth drawing.
    """
    trace = StepTrace(row=row, source=source, path=tuple(path))
    trace.other_files = _other_files(row)
    try:
        document = load_output(project_folder, row, source, state)
        trace.value = walk(document, path)
    except PathError as exc:
        trace.error = str(exc)
        return trace
    except (OSError, ValueError) as exc:
        trace.error = "%s: %s" % (type(exc).__name__, exc)
        return trace

    trace.entries, trace.withheld_entries = entries(trace.value)
    if not trace.entries:
        trace.leaf = render_leaf(trace.value, path)
    return trace


def _other_files(row: StepRow) -> List[str]:
    """What else the step left in its own directory.

    A step's directory is its product (AGENTS.md section 8), so the files
    beside `output.json` are part of the trace - the 17 per-clip indices
    are the answer to "did the temporal index really run on everything".
    """
    if not os.path.isdir(row.directory):
        return []
    names = []
    for entry in sorted(os.listdir(row.directory)):
        if entry.startswith("."):
            continue
        if entry in (project_layout.STEP_OUTPUT_FILE,
                     project_layout.STEP_SUMMARY_FILE):
            continue
        full = os.path.join(row.directory, entry)
        if os.path.isdir(full):
            try:
                count = len(os.listdir(full))
            except OSError:
                count = 0
            names.append("%s/  (%d files)" % (entry, count))
        else:
            try:
                names.append("%s  (%d B)" % (entry, os.path.getsize(full)))
            except OSError:
                names.append(entry)
    return names


def summary_markdown(row: StepRow, limit: int = 12000) -> str:
    """The step's own `summary.md`, which is what `step_exporter` wrote."""
    path = os.path.join(row.directory, project_layout.STEP_SUMMARY_FILE)
    if not os.path.isfile(path):
        return ""
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as handle:
            return handle.read(limit)
    except OSError:
        return ""
