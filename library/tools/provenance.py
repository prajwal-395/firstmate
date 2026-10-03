"""
provenance.py - which step wrote this file, in which run, and from what.

`project_layout.py` answers WHERE a file goes.  This answers WHO PUT IT
THERE.  The two axes are different questions and the captain needs both:
the layout groups by kind (prosody here, subtitles there), and an audit
follows the other axis - this artifact came from that step, which read
that clip.

How attribution is obtained, strongest first
--------------------------------------------
Every record carries the METHOD that produced it, because an audit that
cannot tell a measurement from a declaration is not an audit.

``observed``
    The runner listed the output tree before the step and again after,
    and this file appeared or changed in between.  A recorded fact about
    a specific run.  It needs nothing from the step, which is what makes
    it work for steps that shell out to ffmpeg, Remotion or Resolve.

``declared``
    Nothing was watching, but `AREAS[area].produced_by` says which step
    owns this area.  True of the pipeline as it is built, not of this
    file - a file someone dropped in by hand gets the same answer.  It
    is the honest reconstruction for a run that already happened, and it
    is labelled so nobody mistakes it for the first kind.

``unknown``
    Neither.  It reads as unknown and stays unknown.  Project 001's
    `001.mov`, `fully loaded demo v0.mov` and `001.PNG` are this: no run
    record anywhere names them, and inventing a producer for them would
    be worse than the gap.

What a file was derived FROM
----------------------------
Not inferred.  Several artifacts already record their own source -
`temporal_index/clip_001.json` carries `source_file`, a vision profile
carries `file_path`, a prosody profile carries `audio_file` - and
:data:`SOURCE_KEYS` is the list of keys that count.  A file that names no
source gets no `derived_from`, and the traceback says so rather than
guessing from a filename.

Storage
-------
Two append-only JSONL files under `pipeline_output/provenance/`.  Append
-only because a run is a thing that happened: the record of run 3 does
not stop being true when run 4 overwrites the file.  The newest record
for a path wins when the question is "what is this file now".


Rules relocated from AGENTS.md 8
--------------------------------
These are the engine's rules for this module.  They lived in
AGENTS.md until it was split by subsystem; the wording is unchanged,
so each rule is findable by its own words, and AGENTS.md 8
keeps the headline and points here.

**The layout answers "which step wrote this" by where the file is. Provenance adds WHICH RUN and FROM WHAT.**
`library/tools/provenance.py` owns it.

 **Never attribute a file to the nearest plausible step.**
- The runner observes each step **after** `_export_step_for_review`, or a step's own `<step_id>.json` export is attributed to nobody.
- Records are append-only. `derived_from` is READ out of the artifact, never inferred from a filename.
- `derived_from` is READ out of the artifact - `SOURCE_KEYS` names the keys - never inferred from a filename. A `clip_id` is not a path.
"""

from __future__ import annotations

import json
import os
import time
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from pathlib import Path

from library.tools.project_layout import (
    AREAS,
    NON_STEP_PRODUCERS,
    Area,
    Kind,
    ProjectLayout,
)
from library.tools.step_exporter import step_id_for_export

OBSERVED = "observed"
DECLARED = "declared"
UNKNOWN = "unknown"

METHOD_MEANING = {
    OBSERVED: ("the runner watched this file appear or change while that "
               "step ran, in that run"),
    DECLARED: ("nothing was watching; the layout declares this area is "
               "written by that step"),
    UNKNOWN: ("neither a run record nor a declaration accounts for this "
              "file"),
}

# ── WHAT KIND OF THING PRODUCED A FILE ──────────────────────────────
#
# Until now the only producer was a DAG node, so `step_id` said
# everything.  An OPERATION - a named, scoped entry point into a step's
# own code, reachable without a DAG run - is the second kind, and a
# reader that cannot tell the two apart cannot answer "was this file
# written by the pipeline, or by someone correcting one region of it?"
#
# The two are recorded SEPARATELY and both are kept, because they answer
# different questions and neither substitutes for the other:
#
#   `step_id`       WHICH DAG NODE this file is attributed to.  For an
#                   operation this is its OWNING NODE, so every reader
#                   that already groups by step keeps working untouched.
#   `operation_id`  WHICH OPERATION actually ran, or None for a step.
#
# Why an operation MUST name an owning node, and is refused without one:
# four separate records in this pipeline are keyed by node id - the two
# step ledgers, the run status the dashboard and the Resolve panel read,
# and the three run-level collectors.  A producer that resolves to no
# node loses its place in all four, and would be attributed to nothing
# while still appearing to have been recorded.  That is the shape of
# defect this module exists to prevent, so it raises instead.
PRODUCER_STEP = "step"
PRODUCER_OPERATION = "operation"

PRODUCER_MEANING = {
    PRODUCER_STEP: "a node of the DAG, run by the pipeline runner",
    PRODUCER_OPERATION: ("a named operation, invoked against a scope "
                         "without a DAG run, and attributed to the node "
                         "that owns the decision it made"),
}


class ProvenanceError(ValueError):
    """A producer that could not be recorded truthfully."""


# Keys an artifact may use to name what it was made from.  Only these,
# and only at the top level of a JSON document: a heuristic that went
# looking would eventually find a path that means something else.
SOURCE_KEYS = (
    "source_file",
    "file_path",
    "audio_file",
    "clip_path",
    "video_path",
    "source_path",
)

# A JSON artifact bigger than this is not opened to look for a source
# key.  The aggregate temporal index is 2.9 MB on project 001 and the
# per-clip files are what carry the link anyway.
SOURCE_SCAN_CEILING_BYTES = 16 * 1024 * 1024

ARTIFACTS_FILE = "artifacts.jsonl"
RUNS_FILE = "runs.jsonl"

# The two generated documents describe the artifacts; they are not
# artifacts of a step.  Listing them makes the listing describe itself
# and change every time it is regenerated.
GENERATED_DOCS = ("RUN-TRACEBACK.md", "ARTIFACTS.md", "README-LAYOUT.md")

# Areas whose contents are copies of other runs.  Walking them attributes
# an archived copy of a file to the step that wrote the ORIGINAL, which
# is a different file in a different run.  They are listed as a count.
ARCHIVAL_KINDS = frozenset({Kind.BACKUP})

# `unsorted/` means "nobody could say what this is".  `organize` MOVED
# these files there, and saying so as though it had PRODUCED them would
# turn an admitted unknown back into an attribution - which is the whole
# thing this module exists not to do.
UNATTRIBUTABLE_KINDS = frozenset({Kind.UNSORTED})

# Where the pipeline's files live.  The output root plus the exports,
# because "what is Pipeline_Edit.mp4 and which step made it" is the first
# question anyone opening a project asks, and exports/ is not under the
# output root.
WALKED_AREAS = (Area.OUTPUT_ROOT, Area.EXPORTS)


@dataclass
class ArtifactRecord:
    """One file, and what is known about where it came from."""

    path: str                       # project-relative, posix
    method: str                     # OBSERVED | DECLARED | UNKNOWN
    step_id: str | None = None
    """The DAG node this file is attributed to.

    For an operation this is its OWNING node, never None - see
    `PRODUCER_MEANING`.  Keeping this field meaning the same thing for
    both kinds is what lets every existing reader group by step without
    knowing operations exist.
    """
    run_id: str | None = None
    recorded_at: str | None = None
    bytes: int = 0
    area: str | None = None
    producer_kind: str = PRODUCER_STEP
    """PRODUCER_STEP | PRODUCER_OPERATION.

    Defaulted to a step so every record written before operations
    existed reads as what it actually was, rather than as unknown.
    """
    operation_id: str | None = None
    """The operation that ran, or None when a DAG node wrote this."""
    derived_from: list = field(default_factory=list)
    candidates: list = field(default_factory=list)
    """Every step the area declares, when the declaration names more than
    one and nothing observed which of them wrote THIS file.

    `exports/` is written by both `render` and `validate`. Picking the
    first and printing it would be an answer the pipeline does not have,
    so `step_id` is left None and both are named instead. An observed run
    resolves it properly.
    """

    @property
    def is_attributed(self) -> bool:
        return self.method != UNKNOWN and bool(self.step_id)

    @property
    def producer(self) -> str | None:
        """The narrowest name for what wrote this - operation, else step.

        A reader that wants "which operation" asks this; a reader that
        wants "which node" keeps asking `step_id`.  Both are always
        answerable, which is the whole point of recording two fields.
        """
        return self.operation_id or self.step_id


@dataclass
class RunRecord:
    """One invocation of the runner."""

    run_id: str
    started_at: str
    mode: str = ""
    steps: list = field(default_factory=list)
    operations: list = field(default_factory=list)
    """Operations this run performed, if any.

    Recorded separately from `steps` rather than folded in, because the
    run summary's status is computed from DAG completeness alone: an
    operation must be visible in the account of the run without being
    able to make an incomplete DAG look complete.
    """
    ended_at: str = ""
    status: str = ""
    restart: dict | None = None
    """How the run BEFORE this one ended, when it did not end cleanly.

    None on a run that followed a clean one, and on every run recorded
    before this field existed - which is not the same claim, and
    `run_restart.reconstruct_from_ledger` is what answers for those.
    """


@contextmanager
def observing_operation(project_folder, operation_id: str,
                        run_id: str | None = None):
    """The execution receipt for one capability run outside the runner.

    Snapshots the output tree, yields, and records whatever appeared or
    changed under `operation_id` - on a refusal or a raise too, because a
    file half-written by a failed call was still written by it.  Used at
    the front doors that execute a capability as an act of its own (the
    operations CLI, the reels process), never around a capability another
    capability calls, which would attribute one file to both.
    """
    from library.tools import dag_adapter, operations, perf_ledger
    ledger = ProvenanceLedger(project_folder, step_ids=dag_adapter.node_ids(),
                              operation_ids=operations.names())
    run_id = run_id or new_run_id()
    before = ledger.snapshot()
    # The same receipt, priced: `ren profile` reads this row.
    perf = perf_ledger.begin(
        project_folder, operation_id, run_id,
        node=next((dag_adapter.node_of(op) for op in operations.all()
                   if op.name == operation_id), None))
    try:
        yield ledger
    except BaseException:
        if perf:
            perf.row["status"] = "failed"
        raise
    finally:
        after = ledger.snapshot()
        ledger.observe_operation(operation_id, run_id, before, after)
        if perf:
            perf.row["bytes_written"] = perf_ledger.bytes_written(before, after)
            perf.end()


def new_run_id(now=None, pid=None) -> str:
    """An id for one invocation of the runner.

    Time plus pid: two runs cannot share a second AND a pid, and the id
    sorts chronologically, which is what a reader wants when several
    runs touched the same file.
    """
    stamp = time.strftime("%Y%m%dT%H%M%S", time.localtime(now))
    return f"{stamp}-{pid if pid is not None else os.getpid()}"


def _area_of(layout: ProjectLayout, rel: str) -> Area | None:
    """The most specific area a project-relative path sits in."""
    best = None
    for area, spec in AREAS.items():
        if spec.relpath == ".":
            continue
        base = spec.relpath
        if rel == base or rel.startswith(base + "/"):
            depth = base.count("/") + 1
            if best is None or depth > best[0]:
                best = (depth, area)
    return best[1] if best else None


def read_declared_sources(path: Path) -> list:
    """Paths this artifact says it was made from.  Never inferred.

    Returns [] for anything that is not a small JSON document, and for a
    JSON document that names no source.  A missing link is reported as
    missing.
    """
    try:
        if path.suffix.lower() != ".json":
            return []
        if path.stat().st_size > SOURCE_SCAN_CEILING_BYTES:
            return []
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    if not isinstance(doc, dict):
        return []
    found = []
    for key in SOURCE_KEYS:
        value = doc.get(key)
        if (isinstance(value, str) and value.strip() and os.sep in value
                and value not in found):
            found.append(value)
    return found


class ProvenanceLedger:
    """The record of what each run wrote, for one project."""

    def __init__(self, project_folder, step_ids=None,
                 operation_ids=None) -> None:
        self.layout = (project_folder if isinstance(project_folder, ProjectLayout)
                       else ProjectLayout(project_folder))
        self.root = self.layout.root
        # The DAG's node ids, when the caller has them.  Used only to
        # check that a name read back out of a per-step export really is
        # a step, so `notes.json` dropped in by hand is not attributed to
        # a step called "notes".
        self.step_ids = frozenset(step_ids or ())
        # The registry's operation names, on the same terms and for the
        # same reason.  An operation id nobody declared is refused rather
        # than recorded: inventing a producer is the one thing worse than
        # admitting the gap, and `UNKNOWN` already exists to admit it.
        self.operation_ids = frozenset(operation_ids or ())

    # ── Where it is stored ──────────────────────────────────────────

    def _store(self, name: str, create: bool = False) -> Path:
        if create:
            return self.layout.write_path(Area.PROVENANCE, name)
        return self.layout.read_path(Area.PROVENANCE, name)

    def _append(self, name: str, payload: dict) -> None:
        path = self._store(name, create=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(payload, sort_keys=True) + "\n")

    def _read(self, name: str) -> list:
        path = self._store(name)
        if not path.is_file():
            return []
        rows = []
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rows.append(json.loads(line))
                except ValueError:
                    continue
        return rows

    # ── Watching a step ─────────────────────────────────────────────

    def snapshot(self) -> dict:
        """Every file under the output root, as `path -> (size, mtime_ns)`.

        Cheap enough to take twice per step: project 001's output tree is
        a few hundred files, and this is a walk plus a stat.
        """
        out = {}
        base = self.layout.read_dir(Area.OUTPUT_ROOT)
        if not base.is_dir():
            return out
        skip = {str(self.layout.read_dir(Area.PROVENANCE))}
        for root, dirs, files in os.walk(base):
            if root in skip:
                dirs[:] = []
                continue
            for name in files:
                p = Path(root) / name
                try:
                    st = p.stat()
                except OSError:
                    continue
                out[p.relative_to(self.root).as_posix()] = (
                    st.st_size, st.st_mtime_ns)
        return out

    def observe(self, step_id: str, run_id: str, before: dict,
                after: dict, operation_id: str | None = None,
                changed_paths: set[str] | None = None) -> list:
        """Record every file that appeared or changed while a producer ran.

        The strongest attribution the pipeline can make, and it costs the
        producer nothing - which is the point, because half the steps
        hand the actual writing to ffmpeg, Remotion or Resolve.  That is
        also why this generalises to operations without any new
        machinery: it watches the tree, so it does not care what ran.

        `operation_id` names an OPERATION rather than a DAG node.  When
        it is given, `step_id` must still name the operation's OWNING
        node - see `PRODUCER_MEANING` for why that is required rather
        than optional.
        """
        kind = PRODUCER_OPERATION if operation_id else PRODUCER_STEP

        if operation_id:
            # An operation with no owning node cannot be recorded
            # truthfully: four step-keyed records would lose it while
            # this one claimed to have it.
            if not step_id:
                raise ProvenanceError(
                    f"operation {operation_id!r} was given no owning step. "
                    f"Every operation is attributed to the DAG node that "
                    f"owns the decision it makes, because the step "
                    f"ledgers, the run status and the run-level "
                    f"collectors are all keyed by node id. Pass the "
                    f"owning node as step_id.")
            # `if self.operation_ids and ...` was the original shape of
            # both checks below, and it is the defect this whole change
            # exists to remove wearing the other hat: an OMITTED list
            # made the guard check NOTHING rather than everything, so
            # the protection was OFF BY DEFAULT.  Measured: a ledger
            # built without `operation_ids` RECORDED `totally.made.up`,
            # while the same call against a ledger given the list
            # refused it.  Nothing exercised it yet, so it was latent
            # rather than live - and landing a known gate that cannot
            # fail, inside the change whose purpose is removing them,
            # is not something to do knowingly.
            #
            # So an absent declaration is now a REFUSAL, not a permit.
            # The reasoning is the same one `UNKNOWN` rests on: a
            # producer that cannot be CHECKED cannot be recorded as
            # fact.  Steps are untouched - both checks sit inside
            # `if operation_id:`, and the runner records steps against a
            # ledger with no declarations exactly as before.
            if not self.operation_ids:
                raise ProvenanceError(
                    f"operation {operation_id!r} cannot be recorded: this "
                    f"ledger was built with no declared operations, so "
                    f"nothing here can tell a real operation from an "
                    f"invented one. Construct it with "
                    f"operation_ids=operations.names(). Recording an "
                    f"unverifiable producer is the one thing worse than "
                    f"recording UNKNOWN.")
            if operation_id not in self.operation_ids:
                raise ProvenanceError(
                    f"operation {operation_id!r} is not a declared "
                    f"operation. Declared: "
                    f"{sorted(self.operation_ids)}. An "
                    f"undeclared producer is recorded as UNKNOWN rather "
                    f"than invented.")
            if not self.step_ids:
                raise ProvenanceError(
                    f"operation {operation_id!r} names owning step "
                    f"{step_id!r}, but this ledger was built with no "
                    f"declared steps, so the owning node cannot be "
                    f"checked. Construct it with step_ids from the DAG. "
                    f"An owning node that is merely asserted is what "
                    f"lets an operation vanish from the four step-keyed "
                    f"records while this one claims to have it.")
            if step_id not in self.step_ids:
                raise ProvenanceError(
                    f"operation {operation_id!r} names owning step "
                    f"{step_id!r}, which is not a step of this pipeline. "
                    f"Known: {sorted(self.step_ids)}.")

        changed = [rel for rel, sig in after.items()
                   if before.get(rel) != sig
                   and (changed_paths is None or rel in changed_paths)]
        now = time.strftime("%Y-%m-%dT%H:%M:%S")
        records = []
        for rel in sorted(changed):
            p = self.root / rel
            area = _area_of(self.layout, rel)
            rec = ArtifactRecord(
                path=rel,
                method=OBSERVED,
                step_id=step_id,
                run_id=run_id,
                recorded_at=now,
                bytes=after[rel][0],
                area=area.value if area else None,
                producer_kind=kind,
                operation_id=operation_id,
                derived_from=read_declared_sources(p),
            )
            records.append(rec)
            self._append(ARTIFACTS_FILE, asdict(rec))
        return records

    def observe_operation(self, operation_id: str, run_id: str,
                          before: dict, after: dict) -> list:
        """Record what a CAPABILITY wrote, named by its id alone.

        The capability id is the authoritative name; the legacy `step_id`
        every node-keyed reader still groups by is DERIVED through
        `library/tools/dag_adapter.py`, never supplied by the caller - so
        a caller cannot attribute a capability to a node it is not.
        Every refusal `observe` makes still applies.
        """
        from library.tools import dag_adapter, operations
        node = dag_adapter.node_of(operations.get(operation_id))
        return self.observe(node, run_id, before, after,
                            operation_id=operation_id)

    # ── Runs ────────────────────────────────────────────────────────

    def start_run(self, run_id: str, mode: str = "",
                  restart: dict | None = None) -> RunRecord:
        rec = RunRecord(run_id=run_id,
                        started_at=time.strftime("%Y-%m-%dT%H:%M:%S"),
                        mode=mode, restart=restart)
        self._append(RUNS_FILE, asdict(rec))
        return rec

    def end_run(self, run_id: str, status: str, steps: list,
                operations: list | None = None) -> None:
        """Close a run's record.

        `operations` is recorded beside `steps`, never merged into it:
        the run summary decides SUCCESS from DAG completeness, so an
        operation has to be visible in the account without being able to
        make an incomplete DAG read as complete.
        """
        self._append(RUNS_FILE, {
            "run_id": run_id,
            "started_at": "",
            "mode": "",
            "steps": list(steps),
            "operations": list(operations or ()),
            "ended_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "status": status,
        })

    def runs(self) -> list:
        """Every run, merged from its start and end records, oldest first."""
        merged = {}
        for row in self._read(RUNS_FILE):
            rid = row.get("run_id")
            if not rid:
                continue
            cur = merged.setdefault(rid, {"run_id": rid, "started_at": "",
                                          "mode": "", "steps": [],
                                          "operations": [],
                                          "ended_at": "", "status": "",
                                          "restart": None})
            for k, v in row.items():
                if v:
                    cur[k] = v
        return [RunRecord(**merged[k]) for k in sorted(merged)]

    # ── Answering the question ──────────────────────────────────────

    def observed_records(self) -> dict:
        """The newest observed record per path."""
        out = {}
        for row in self._read(ARTIFACTS_FILE):
            try:
                rec = ArtifactRecord(**row)
            except TypeError:
                continue
            out[rec.path] = rec
        return out

    def of(self, path) -> ArtifactRecord:
        """What is known about one file.  Never guesses; may say unknown."""
        p = Path(str(path))
        p = p if p.is_absolute() else self.root / p
        try:
            rel = p.resolve().relative_to(self.root).as_posix()
        except ValueError:
            rel = p.as_posix()

        observed = self.observed_records().get(rel)
        size = p.stat().st_size if p.is_file() else 0
        if observed:
            observed.bytes = size or observed.bytes
            return observed

        area = _area_of(self.layout, rel)
        spec = AREAS[area] if area else None

        # The by-step layout paying off: the file is IN the step's
        # directory, so the answer is the path. No index, no sidecar, and
        # nothing to keep in sync.
        owner = self.layout.step_of(rel)
        if owner:
            return ArtifactRecord(
                path=rel, method=DECLARED, step_id=owner,
                bytes=size, area=area.value if area else None,
                derived_from=read_declared_sources(p) if p.is_file() else [])

        # A project that predates the by-step layout still has flat
        # `<step_id>.json` exports at the output root. The name was BUILT
        # from the step id by `step_exporter`, so reading it back is a
        # mechanism rather than a guess - checked against the real step
        # ids before it is believed.
        if area is Area.OUTPUT_ROOT and self.step_ids:
            candidate = step_id_for_export(rel.rsplit("/", 1)[-1])
            if candidate in self.step_ids:
                return ArtifactRecord(
                    path=rel, method=DECLARED, step_id=candidate,
                    bytes=size, area=area.value,
                    derived_from=(read_declared_sources(p)
                                  if p.is_file() else []))

        if spec and spec.kind in UNATTRIBUTABLE_KINDS:
            return ArtifactRecord(
                path=rel, method=UNKNOWN, bytes=size,
                area=area.value if area else None)
        steps = [s for s in (spec.writers if spec else ())
                 if s not in NON_STEP_PRODUCERS]
        producer = (steps or list(spec.writers) if spec else [])
        if producer:
            return ArtifactRecord(
                path=rel, method=DECLARED,
                step_id=producer[0] if len(producer) == 1 else None,
                bytes=size, area=area.value if area else None,
                derived_from=read_declared_sources(p) if p.is_file() else [],
                candidates=list(producer))
        return ArtifactRecord(
            path=rel, method=UNKNOWN, bytes=size,
            area=area.value if area else None)

    def all_artifacts(self, include_archives: bool = False) -> list:
        """Every file the pipeline wrote, with its provenance.

        `pipeline_output/backups/` is counted rather than listed unless
        asked for: an archived copy is not the artifact the step wrote,
        and attributing it to that step would be a false link.
        """
        observed = self.observed_records()
        out = []
        seen = set()
        for area_name in WALKED_AREAS:
            base = self.layout.read_dir(area_name)
            if not base.is_dir():
                continue
            for root, _dirs, files in os.walk(base):
                for name in sorted(files):
                    p = Path(root) / name
                    rel = p.relative_to(self.root).as_posix()
                    if rel in seen:
                        continue
                    seen.add(rel)
                    if name in GENERATED_DOCS:
                        continue
                    area = _area_of(self.layout, rel)
                    if (not include_archives and area
                            and AREAS[area].kind in ARCHIVAL_KINDS):
                        continue
                    rec = observed.get(rel)
                    out.append(rec if rec else self.of(rel))
        return sorted(out, key=lambda r: r.path)

    def archived_file_count(self) -> int:
        total = 0
        for spec in AREAS.values():
            if spec.kind not in ARCHIVAL_KINDS:
                continue
            d = self.root / spec.relpath
            if d.is_dir():
                total += sum(len(f) for _r, _d, f in os.walk(d))
        return total
