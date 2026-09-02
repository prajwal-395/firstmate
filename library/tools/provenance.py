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
"""

from __future__ import annotations

import json
import os
import time
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
    run_id: str | None = None
    recorded_at: str | None = None
    bytes: int = 0
    area: str | None = None
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


@dataclass
class RunRecord:
    """One invocation of the runner."""

    run_id: str
    started_at: str
    mode: str = ""
    steps: list = field(default_factory=list)
    ended_at: str = ""
    status: str = ""
    restart: dict | None = None
    """How the run BEFORE this one ended, when it did not end cleanly.

    None on a run that followed a clean one, and on every run recorded
    before this field existed - which is not the same claim, and
    `run_restart.reconstruct_from_ledger` is what answers for those.
    """


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

    def __init__(self, project_folder, step_ids=None) -> None:
        self.layout = (project_folder if isinstance(project_folder, ProjectLayout)
                       else ProjectLayout(project_folder))
        self.root = self.layout.root
        # The DAG's node ids, when the caller has them.  Used only to
        # check that a name read back out of a per-step export really is
        # a step, so `notes.json` dropped in by hand is not attributed to
        # a step called "notes".
        self.step_ids = frozenset(step_ids or ())

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
                after: dict) -> list:
        """Record every file that appeared or changed while a step ran.

        The strongest attribution the pipeline can make, and it costs the
        steps nothing - which is the point, because half of them hand the
        actual writing to ffmpeg, Remotion or Resolve.
        """
        changed = [rel for rel, sig in after.items()
                   if before.get(rel) != sig]
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
                derived_from=read_declared_sources(p),
            )
            records.append(rec)
            self._append(ARTIFACTS_FILE, asdict(rec))
        return records

    # ── Runs ────────────────────────────────────────────────────────

    def start_run(self, run_id: str, mode: str = "",
                  restart: dict | None = None) -> RunRecord:
        rec = RunRecord(run_id=run_id,
                        started_at=time.strftime("%Y-%m-%dT%H:%M:%S"),
                        mode=mode, restart=restart)
        self._append(RUNS_FILE, asdict(rec))
        return rec

    def end_run(self, run_id: str, status: str, steps: list) -> None:
        self._append(RUNS_FILE, {
            "run_id": run_id,
            "started_at": "",
            "mode": "",
            "steps": list(steps),
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
