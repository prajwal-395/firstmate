"""`ren analyze`: the analysis-only run - footage in, memory out, no edit.

The separable-product report (`data/vep-phase1-separable-product/report.md`
in firstmate's home, "Recommendation" items 1-3) asked for one run that
analyses a folder of footage and stops, reusing the step implementations
rather than copying them. This module is that run, and it owns nothing it
measures: every number comes from a module that already existed.

    ren analyze <folder-of-footage | project> [--into DIR] [--memory-only]

Two halves, in order:

1. **The analysis capabilities**, through footage intelligence's own
   orchestration (`library/tools/footage_intelligence.py`) - never the
   editing runner: scan, catalog, semantic analysis, temporal index and
   prosody, composed by what each requires. `--memory-only` runs scan
   and catalog alone; `--with ocr.extract` / `--skip semantics.analyse`
   adjust the selection (a legacy node name is accepted too).
2. **The memory lanes** (`LANES`), per source, into the per-machine memory
   (docs/SOURCE_MEMORY.md): M0+M1 transcript, M2 frame sample, M3 Vision
   faces and hands, M3b person identity, M6 conversation clock, M7 event
   spans, then the text and frame search indexes over them. A lane whose
   record is already FRESH for a source is reused, never rebuilt: a
   re-run over the same folder costs the fingerprints and nothing else.

A folder that is not a project becomes a COLLECTION: an ordinary project
(`project_registry.create_project`) whose `source.footage_root` names the
folder, so the footage is read where it lies and never copied. Being an
ordinary project is the compatibility adapter the report asked for -
`ren edit <collection>` continues into an edit with preflight already done,
and `ren search <collection>` answers off what this run built.

What the run did is written down per lane and per source in
`pipeline_output/footage_memory/analysis_run.json` (`Area.FOOTAGE_MEMORY`):
`built`, `reused`, `skipped` with the reason, or `failed` with the error.
`ren export-memory` (library/tools/memory_export.py) reads it so a failure
travels with the export. Nothing here is a default standing in for a
measurement: a lane that did not run for a source has no status, not a
guessed one.

Heavy: decode, transcription and model inference. The run takes the
heavy-work lock itself (re-entrant, so `heavy_work_lock run -- ren analyze`
works too). Media is read-only.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import re
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence

from library.tools import source_memory
from library.tools.project_layout import Area, ProjectLayout
from library.tools.ren_refusal import REFUSAL_EXIT_CODE, RenRefusal

REPO_ROOT = Path(__file__).resolve().parents[2]

RUN_RECORD_FILE = "analysis_run.json"
RUN_RECORD_VERSION = 1
LOCK_OWNER = "footage-analysis"

# Per-source outcomes. Every source a lane considered gets exactly one.
BUILT = "built"
REUSED = "reused"
SKIPPED = "skipped"
FAILED = "failed"


# ── The lanes ───────────────────────────────────────────────────────


@dataclass(frozen=True)
class Lane:
    """One producer of per-source memory, named once.

    `slots` are the memory files the lane writes; `reads` the files it
    reads, so a record older than its own input is stale even when its
    digest matches (an M3 measured off an M2 that was since rebuilt).
    `per_source` lanes are asked only for the sources that need them;
    the rest are project-wide and cheap, and always rebuild.
    """

    name: str
    label: str
    summary: str
    slots: tuple
    reads: tuple
    heavy: bool
    per_source: bool
    build: Callable


def _build_transcripts(project, clip_ids, root):
    return source_memory.build_project(project, clip_ids, root)


def _build_frames(project, clip_ids, root):
    return source_memory.build_project_frames(project, clip_ids, root)


def _build_persons(project, clip_ids, root):
    from library.tools import person_measurements
    return person_measurements.build_project_persons(project, clip_ids, root)


def _build_identity(project, clip_ids, root):
    from library.tools import person_entity
    return person_entity.build_project_identity(project, clip_ids, root)


def _build_clock(project, _clip_ids, root):
    from library.tools import conversation_clock
    return conversation_clock.build_project(project, root)


def _build_events(project, _clip_ids, root):
    from library.tools import event_spans
    return event_spans.build_project_events(project, root)


def _build_text_index(project, _clip_ids, _root):
    from library.tools.analysis import footage_query
    return footage_query.build_index(project)


def _build_frame_index(project, _clip_ids, _root):
    from library.tools.analysis import footage_frames
    return footage_frames.build_frame_index(project)


SM = source_memory
LANES: tuple = (
    Lane("transcript", "M0+M1", "probe, program track, whole-source words",
         (SM.SLOT_SOURCE, SM.SLOT_TRANSCRIPT), (), True, True,
         _build_transcripts),
    Lane("frames", "M2", "one shared I-frame sample per source",
         (SM.SLOT_FRAMES_INDEX,), (), True, True, _build_frames),
    Lane("persons", "M3", "Apple Vision faces, lips and hands per frame",
         (SM.SLOT_PERSONS,), (SM.SLOT_FRAMES_INDEX,), True, True,
         _build_persons),
    Lane("identity", "M3b", "face and voice tracks within the source",
         (SM.SLOT_IDENTITY,), (SM.SLOT_FRAMES_INDEX,), True, True,
         _build_identity),
    Lane("clock", "M6", "multicam offsets measured between transcripts",
         (SM.SLOT_CLOCK,), (SM.SLOT_TRANSCRIPT,), False, False, _build_clock),
    Lane("events", "M7", "on-screen and speaking spans per person",
         (SM.SLOT_EVENTS,), (SM.SLOT_PERSONS, SM.SLOT_IDENTITY), False,
         False, _build_events),
    Lane("text_index", "index", "the speech/vision text index `ren search` reads",
         (), (SM.SLOT_TRANSCRIPT,), True, False, _build_text_index),
    Lane("frame_index", "index", "the CLIP frame index `ren search --visual` reads",
         (), (SM.SLOT_FRAMES_INDEX,), True, False, _build_frame_index),
)
LANE_NAMES = tuple(lane.name for lane in LANES)
_BY_NAME = {lane.name: lane for lane in LANES}


# ── Freshness ───────────────────────────────────────────────────────


def _record(digest: str, slot: str, root: Optional[Path]) -> Optional[dict]:
    path = source_memory.source_dir(digest, root) / slot
    try:
        with open(path, encoding="utf-8") as handle:
            doc = json.load(handle)
    except (OSError, ValueError):
        return None
    return doc if isinstance(doc, dict) else None


def slot_is_fresh(lane: Lane, digest: str, root: Optional[Path]) -> bool:
    """Whether every slot `lane` writes is current for `digest`.

    A slot is current when it exists, names this digest, and is no older
    than any slot the lane reads. A recorded refusal (an `untranscribed-*`
    transcript, a `no-faces-detected` identity) is current: it is the
    measured answer, and rebuilding it would measure the same thing.
    """
    sdir = source_memory.source_dir(digest, root)
    newest_input = 0.0
    for slot in lane.reads:
        try:
            newest_input = max(newest_input, (sdir / slot).stat().st_mtime)
        except OSError:
            pass
    for slot in lane.slots:
        doc = _record(digest, slot, root)
        if doc is None or doc.get("content_digest") != digest:
            return False
        if (sdir / slot).stat().st_mtime < newest_input:
            return False
    return True


# ── The collection ──────────────────────────────────────────────────


def slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return slug or "footage"


def resolve_target(ref: str, into: Optional[str] = None,
                   slug: Optional[str] = None) -> tuple:
    """`(project_folder, created)` for what `ren analyze` was pointed at.

    A project (path or slug) is analysed in place. A folder that is not
    one becomes a collection under `into` (default: the projects root):
    a project declaring the folder as its `source.footage_root`. Pointing
    at the same folder again finds the same collection; a collection of
    that name over a DIFFERENT folder is refused, never repointed.
    """
    from library.tools.project_registry import (
        get_project, resolve_project_path)

    project_yaml = resolve_project_path(ref)
    if project_yaml is not None:
        return str(project_yaml.parent), False
    folder = Path(ref).expanduser()
    if not folder.is_dir():
        try:
            config = get_project(ref)
        except Exception:
            config = None
        root = getattr(config, "_project_root", None) if config else None
        if root:
            return str(root), False
        raise RenRefusal(
            f"nothing to analyse at {ref!r}",
            "it is neither a project nor a folder on disk",
            "ren analyze <folder of footage>  or  ren analyze <project>")
    return ensure_collection(str(folder.resolve()), into, slug)


def ensure_collection(footage_dir: str, into: Optional[str] = None,
                      slug: Optional[str] = None) -> tuple:
    """The collection project over `footage_dir`, created when absent."""
    from library.schemas.project_config import load_project_config
    from library.tools.paths import PROJECTS_ROOT
    from library.tools.project_registry import (
        _write_project_yaml, create_project)

    parent = Path(into).expanduser().resolve() if into else Path(PROJECTS_ROOT)
    name = slug or slugify(Path(footage_dir).name)
    folder = parent / name
    yaml_path = folder / "project.yaml"
    if yaml_path.is_file():
        config = load_project_config(str(yaml_path))
        declared = (config.source.footage_root or "").rstrip("/")
        if declared != footage_dir.rstrip("/"):
            raise RenRefusal(
                f"{folder} already exists over different footage",
                f"it declares source.footage_root {declared or '(raw/)'!r}, "
                f"not {footage_dir!r}",
                "pass --slug <another-name> or --into <another-folder>")
        return str(folder), False
    create_project(name, f"Footage: {Path(footage_dir).name}", root=parent,
                   description=("Analysis-only collection made by `ren "
                                "analyze`; the footage is read in place."),
                   tags=["analysis-only"])
    config = load_project_config(str(yaml_path))
    config.source.footage_root = footage_dir
    _write_project_yaml(yaml_path, config)
    return str(folder), True


# ── The capability half ─────────────────────────────────────────────


def run_steps(project: str, memory_only: bool, with_: Sequence[str] = (),
              skip: Sequence[str] = ()) -> dict:
    """Execute the analysis capabilities; per-capability outcomes."""
    from library.tools import footage_intelligence
    selected = footage_intelligence.select(memory_only, with_, skip)
    record = footage_intelligence.run(project, selected)
    record["scope"] = "scan+catalog" if memory_only else "analysis"
    return record


# ── The lane half ───────────────────────────────────────────────────


def catalog_sources(project: str) -> List[dict]:
    """`[{clip_id, digest, path, duration_seconds}]` in catalog order."""
    recorded = source_memory.load_recorded_fingerprints(project)
    out = []
    for clip in source_memory.load_catalog(project):
        digest, basis = source_memory.digest_for_clip(project, clip, recorded)
        out.append({
            "clip_id": clip.get("clip_id"),
            "digest": digest,
            "digest_basis": basis,
            "path": clip.get("source_file") or clip.get("path") or "",
            "duration_seconds": clip.get("duration_seconds"),
        })
    return out


def _outcome(account: dict) -> dict:
    """One build_project row as `{status, reason?}`."""
    if account.get("failed"):
        return {"status": FAILED, "reason": account["failed"]}
    if account.get("skipped"):
        reason = account["skipped"]
        return {"status": REUSED if reason == "reused" else SKIPPED,
                "reason": reason}
    if account.get("reused"):
        return {"status": REUSED}
    return {"status": BUILT}


def run_lane(lane: Lane, project: str, sources: List[dict],
             root: Optional[Path]) -> dict:
    """Run one lane over the sources that need it; per-source outcomes."""
    per_source: Dict[str, dict] = {}
    todo: List[str] = []
    for src in sources:
        if src["digest"] is None:
            per_source[src["clip_id"]] = {"status": SKIPPED,
                                          "reason": "no content digest"}
        elif lane.per_source and slot_is_fresh(lane, src["digest"], root):
            per_source[src["clip_id"]] = {"status": REUSED}
        else:
            todo.append(src["clip_id"])

    if lane.name == "clock" and todo and clock_is_fresh(sources, root):
        for clip_id in todo:
            per_source[clip_id] = {"status": REUSED}
        todo = []

    started = time.perf_counter()
    report: dict = {}
    error = None
    if not todo:
        pass
    else:
        try:
            report = lane.build(project, todo if lane.per_source else None,
                                root) or {}
        except Exception as exc:  # one lane failing never stops the others
            error = f"{type(exc).__name__}: {exc}"
    seconds = round(time.perf_counter() - started, 1)

    if error:
        for clip_id in todo:
            per_source[clip_id] = {"status": FAILED, "reason": error}
    # Per-source rows, where the lane's report has them. An index's report
    # uses `clips` as a COUNT, so only a list of rows is read as rows.
    rows = report.get("clips") if isinstance(report, dict) else None
    for account in rows if isinstance(rows, list) else []:
        clip_id = account.get("clip_id")
        if clip_id is None or (lane.per_source and clip_id not in todo):
            continue
        per_source[clip_id] = _outcome(account)
    if not lane.per_source and not error:
        for clip_id in todo:
            per_source.setdefault(clip_id, {"status": BUILT})
    if lane.name == "clock":
        _clock_outcomes(per_source, sources, root)

    built = [c for c, o in per_source.items() if o["status"] == BUILT]
    built_minutes = sum((s["duration_seconds"] or 0.0) for s in sources
                        if s["clip_id"] in built) / 60.0
    return {
        "lane": lane.name,
        "label": lane.label,
        "seconds": seconds,
        "built_video_minutes": round(built_minutes, 2),
        "seconds_per_video_minute": (round(seconds / built_minutes, 2)
                                     if lane.per_source and built_minutes
                                     else None),
        "error": error,
        "sources": per_source,
        "summary": _lane_summary(report),
    }


def clock_is_fresh(sources: List[dict], root: Optional[Path]) -> bool:
    """Whether M6 was measured after every transcript in this catalog.

    M6 is project-wide (a group needs its partners), so it is fresh as a
    whole or not at all: when the oldest clock record is newer than the
    newest transcript, every source's membership - including "in no
    group", which writes no record - was decided against these
    transcripts. Rebuilding then would only lose what the earlier build
    could see and this one cannot: a collection over the bare folder has
    no Resolve timeline, so its rebuild would overwrite the recorded
    Resolve cross-check with null.
    """
    newest_transcript, clocks = 0.0, []
    for src in sources:
        if src["digest"] is None:
            continue
        sdir = source_memory.source_dir(src["digest"], root)
        try:
            newest_transcript = max(newest_transcript, (
                sdir / source_memory.SLOT_TRANSCRIPT).stat().st_mtime)
        except OSError:
            return False  # an untranscribed source has not been heard yet
        try:
            clocks.append((sdir / source_memory.SLOT_CLOCK).stat().st_mtime)
        except OSError:
            pass
    return bool(clocks) and min(clocks) >= newest_transcript


def _clock_outcomes(per_source: dict, sources: List[dict],
                    root: Optional[Path]) -> None:
    """M6 writes nothing for a source in no multicam group - say so."""
    for src in sources:
        if src["digest"] is None or per_source.get(src["clip_id"], {}).get(
                "status") == FAILED:
            continue
        if _record(src["digest"], source_memory.SLOT_CLOCK, root) is None:
            per_source[src["clip_id"]] = {
                "status": SKIPPED,
                "reason": "in no measured multicam group (no clock.json)"}


def _lane_summary(report) -> dict:
    """The scalar half of a lane's own report - counts, not rows."""
    if not isinstance(report, dict):
        return {}
    keep = {}
    for key, value in report.items():
        if key in ("clips", "project_folder", "segments"):
            continue
        if isinstance(value, (int, float, str, bool)) or value is None:
            keep[key] = value
        elif (isinstance(value, (dict, list))
              and len(json.dumps(value, default=str)) < 2000):
            keep[key] = value
    return keep


# ── The run ─────────────────────────────────────────────────────────


def _git_head() -> Optional[str]:
    try:
        out = subprocess.run(["git", "rev-parse", "HEAD"], cwd=str(REPO_ROOT),
                             capture_output=True, encoding="utf-8",
                             check=False)
    except OSError:
        return None
    return out.stdout.strip() or None


def run_record_path(project: str) -> Path:
    return ProjectLayout(project).read_path(Area.FOOTAGE_MEMORY,
                                            RUN_RECORD_FILE)


def read_run_record(project: str) -> Optional[dict]:
    try:
        with open(run_record_path(project), encoding="utf-8") as handle:
            doc = json.load(handle)
    except (OSError, ValueError):
        return None
    return doc if isinstance(doc, dict) else None


def analyze(project: str, lanes: Sequence[str] = LANE_NAMES,
            memory_only: bool = False, steps: bool = True,
            with_: Sequence[str] = (), skip: Sequence[str] = (),
            root: Optional[Path] = None, created: bool = False) -> dict:
    """The whole analysis-only run over one project or collection."""
    from library.tools.heavy_work_lock import heavy_work_lock

    started_at = _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")
    t0 = time.perf_counter()
    record = {
        "version": RUN_RECORD_VERSION,
        "kind": "footage-analysis-run",
        "project_folder": os.path.abspath(project),
        "collection_created": created,
        "memory_root": str(source_memory.source_dir("x", root).parent),
        "code_revision": _git_head(),
        "started_at": started_at,
        "steps": None,
        "lanes": [],
    }
    with heavy_work_lock(LOCK_OWNER):
        if steps:
            record["steps"] = run_steps(project, memory_only, with_, skip)
        sources = catalog_sources(project)
        record["sources"] = [{k: s[k] for k in ("clip_id", "digest",
                                                "digest_basis",
                                                "duration_seconds")}
                             for s in sources]
        if not sources:
            record["error"] = ("the catalog is empty: no footage was found, "
                               "or the catalog step did not run")
        else:
            for name in lanes:
                lane = _BY_NAME[name]
                print(f"[analyze] {lane.label} {lane.name}: {lane.summary}",
                      file=sys.stderr, flush=True)
                record["lanes"].append(run_lane(lane, project, sources, root))
                _write_record(project, record, t0)
    video_minutes = sum((s.get("duration_seconds") or 0.0)
                        for s in record.get("sources", [])) / 60.0
    record["video_minutes"] = round(video_minutes, 2)
    _write_record(project, record, t0, finished=True)
    return record


def _write_record(project: str, record: dict, t0: float,
                  finished: bool = False) -> None:
    record["seconds"] = round(time.perf_counter() - t0, 1)
    if finished:
        record["finished_at"] = _dt.datetime.now(
            _dt.timezone.utc).isoformat(timespec="seconds")
        record["failed"] = sorted({
            clip for lane in record["lanes"]
            for clip, outcome in lane["sources"].items()
            if outcome["status"] == FAILED})
        steps = record.get("steps")
        record["status"] = ("failed" if record.get("error")
                            or (steps and steps["status"] != "complete")
                            or record["failed"] else "complete")
    path = ProjectLayout(project).write_path(Area.FOOTAGE_MEMORY,
                                             RUN_RECORD_FILE)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(record, indent=1), encoding="utf-8")
    os.replace(tmp, path)


# ── Status ──────────────────────────────────────────────────────────


def status(project: str, root: Optional[Path] = None) -> dict:
    """Per source, per memory lane: fresh or not, read off the memory. Light."""
    rows = []
    for src in catalog_sources(project):
        lanes = {}
        for lane in LANES:
            if not lane.slots:
                continue
            if src["digest"] is None:
                lanes[lane.name] = "no-digest"
            else:
                lanes[lane.name] = ("fresh" if slot_is_fresh(
                    lane, src["digest"], root) else "absent-or-stale")
        rows.append({"clip_id": src["clip_id"], "digest": src["digest"],
                     "lanes": lanes})
    return {"project_folder": os.path.abspath(project),
            "last_run": (read_run_record(project) or {}).get("status"),
            "sources": rows}


def _print_run(record: dict) -> None:
    print(f"\nAnalysis run: {record['status']} in {record['seconds']} s over "
          f"{record.get('video_minutes', 0)} video minutes")
    if record.get("steps"):
        s = record["steps"]
        print(f"  steps ({s['scope']}): {s['status']}, {s['seconds']} s")
        for row in s["capabilities"]:
            print(f"    {row['capability']:18s} {row['status']}"
                  + (f"  {row['seconds']} s" if "seconds" in row else ""))
    for lane in record["lanes"]:
        counts: Dict[str, int] = {}
        for outcome in lane["sources"].values():
            counts[outcome["status"]] = counts.get(outcome["status"], 0) + 1
        rate = lane["seconds_per_video_minute"]
        print(f"  {lane['label']:6s} {lane['lane']:12s} {lane['seconds']:8.1f} s"
              f"  {', '.join(f'{k} {v}' for k, v in sorted(counts.items()))}"
              + (f"  ({rate} s/video-min)" if rate else ""))
        for clip, outcome in lane["sources"].items():
            if outcome["status"] == FAILED:
                print(f"      {clip}: {outcome['reason']}")
    print(f"\nRecord: {run_record_path(record['project_folder'])}")
    print(f"Export: ren export-memory {record['project_folder']}")


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if "--" in argv:
        raise RenRefusal(
            "`ren analyze` no longer passes flags to the step runner",
            "analysis runs its own capabilities, not a partial edit run",
            "select with --with <capability> / --skip <capability>, "
            "e.g. --with ocr.extract")
    parser = argparse.ArgumentParser(
        prog="ren analyze",
        description=("Analyse a folder of footage (or a project) without "
                     "editing it: the analysis steps, then the per-source "
                     "memory lanes and search indexes. Fresh records are "
                     "reused."))
    parser.add_argument("target", help="a folder of footage, or a project")
    parser.add_argument("--into", help="where a new collection is made "
                        "(default: the projects root)")
    parser.add_argument("--slug", help="the collection's name "
                        "(default: from the folder name)")
    parser.add_argument("--memory-only", action="store_true",
                        help="run scan and catalog only, then the memory "
                        "lanes (skips vision, temporal index and prosody)")
    parser.add_argument("--no-steps", action="store_true",
                        help="run no analysis capability at all (catalog "
                        "must exist)")
    parser.add_argument("--with", dest="with_", action="append", default=[],
                        metavar="CAPABILITY",
                        help="also run an opt-in capability (ocr.extract)")
    parser.add_argument("--skip", action="append", default=[],
                        metavar="CAPABILITY",
                        help="leave an analysis capability out")
    parser.add_argument("--lanes", nargs="+", choices=LANE_NAMES,
                        default=list(LANE_NAMES),
                        help="which memory lanes to run, in table order")
    parser.add_argument("--memory-root", help="override the memory root "
                        "(default: $PIPELINE_SOURCE_MEMORY_ROOT or vep home)")
    parser.add_argument("--status", action="store_true",
                        help="print per-source lane freshness and stop (light)")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    if args.memory_root:
        # Through the environment, not a parameter: the capabilities, the
        # search indexes and every reader must agree on ONE root.
        os.environ[source_memory.MEMORY_ROOT_ENV] = str(
            Path(args.memory_root).expanduser().resolve())
    root = None

    if args.status:
        from library.tools.project_registry import resolve_project_path
        found = resolve_project_path(args.target)
        if found is None:
            raise RenRefusal(f"{args.target!r} is not a project",
                             "status reads a project or collection",
                             "ren analyze <project> --status")
        print(json.dumps(status(str(found.parent), root), indent=2))
        return 0

    project, created = resolve_target(args.target, args.into, args.slug)
    if created:
        print(f"[analyze] made collection {project} over {args.target}",
              file=sys.stderr)
    ordered = [n for n in LANE_NAMES if n in set(args.lanes)]
    record = analyze(project, ordered, memory_only=args.memory_only,
                     steps=not args.no_steps, with_=args.with_,
                     skip=args.skip,
                     root=root, created=created)
    if args.json:
        print(json.dumps(record, indent=2))
    else:
        _print_run(record)
    return 0 if record["status"] == "complete" else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except RenRefusal as refused:
        print(refused.render(), file=sys.stderr)
        sys.exit(REFUSAL_EXIT_CODE)
