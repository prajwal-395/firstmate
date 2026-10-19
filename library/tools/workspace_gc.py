"""Plan and apply bounded collection of Ren's private data stores.

The collector is intentionally separate from project retention. It only
walks Ren's own data home, keeps the newest qualification workflow runs,
and keeps source-memory digests referenced by project state or retained
qualification runs. Qualification media and repository fixtures are never
candidate roots. A plan and JSON proof are written before apply; apply
recomputes the plan and refuses if a candidate changed.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

from library.tools import journal_naming, qualification_project, source_memory
from library.tools.shared_environment import vep_home

KEEP_WORKFLOW_RUNS = 2
PLAN_DIRNAME = "gc-plans"
PROTECTED_SUFFIXES = (".drp", ".drt", ".otio")


class WorkspaceGcRefused(RuntimeError):
    """Collection could not prove that the full plan remains safe."""


@dataclass(frozen=True)
class Entry:
    path: str
    bytes: int
    sha256: str
    reason: str


def _digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _files(root: Path):
    if not root.exists():
        return
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            continue
        if path.is_file():
            yield path


def _project_roots() -> list[Path]:
    raw = os.environ.get("PIPELINE_PROJECTS_ROOT")
    if not raw:
        raise WorkspaceGcRefused(
            "PIPELINE_PROJECTS_ROOT is unset; source-memory reachability "
            "cannot be proved")
    root = Path(raw).expanduser().resolve()
    if not root.is_dir():
        raise WorkspaceGcRefused(
            f"PIPELINE_PROJECTS_ROOT is not a directory: {root}")
    projects = [p.parent for p in root.rglob("pipeline_data.json")]
    if not projects:
        raise WorkspaceGcRefused(
            f"no project state records found under {root}; source-memory "
            "reachability cannot be proved")
    return projects


def _referenced_digests(roots: list[Path], retained_runs: list[Path]) -> set[str]:
    found: set[str] = set()
    repo_tests = Path(__file__).resolve().parents[2] / "tests"
    reference_files = [*repo_tests.rglob("*.py"),
                       *repo_tests.rglob("*.json"),
                       *repo_tests.rglob("*.md")]
    for path in reference_files:
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            raise WorkspaceGcRefused(
                f"cannot inspect retained test fixture reference {path}: {exc}") from exc
        found.update(re.findall(r"(?<![0-9a-f])[0-9a-f]{64}(?![0-9a-f])", text))
    for root in [*roots, *retained_runs]:
        state = root / "pipeline_data.json"
        if state.is_file():
            try:
                data = json.loads(state.read_text(encoding="utf-8"))
            except (OSError, ValueError) as exc:
                raise WorkspaceGcRefused(
                    f"cannot read reachability record {state}: {exc}") from exc
            for row in (data.get("source_fingerprints") or {}).values():
                digest = row.get("content_digest") if isinstance(row, dict) else None
                if digest:
                    found.add(str(digest))
        if root in roots:
            try:
                catalog = source_memory.load_catalog(str(root))
                recorded = source_memory.load_recorded_fingerprints(str(root))
                for clip in catalog:
                    digest, _basis = source_memory.digest_for_clip(
                        str(root), clip, recorded)
                    if digest:
                        found.add(str(digest))
            except (OSError, TypeError, ValueError) as exc:
                raise WorkspaceGcRefused(
                    f"cannot establish source-memory reachability for "
                    f"{root}: {exc}") from exc
        if root in retained_runs:
            for path in _files(root):
                if path.suffix.lower() not in {".json", ".txt", ".md"}:
                    continue
                try:
                    text = path.read_text(encoding="utf-8")
                except (OSError, UnicodeError) as exc:
                    raise WorkspaceGcRefused(
                        f"cannot inspect retained run reference {path}: {exc}") from exc
                found.update(re.findall(
                    r"(?<![0-9a-f])[0-9a-f]{64}(?![0-9a-f])", text))
    return found


def _reference_text(roots: list[Path]) -> list[str]:
    """Read durable project and fixture records that may pin an exact path."""
    records = []
    for root in roots:
        for path in root.rglob("*"):
            if path.is_symlink() or not path.is_file() or path.suffix.lower() not in {
                    ".json", ".txt", ".md", ".yaml", ".yml"}:
                continue
            try:
                records.append(path.read_text(encoding="utf-8"))
            except (OSError, UnicodeError) as exc:
                raise WorkspaceGcRefused(
                    f"cannot inspect project reachability record {path}: {exc}") from exc
    tests = Path(__file__).resolve().parents[2] / "tests"
    for path in [*tests.rglob("*.py"), *tests.rglob("*.json"),
                 *tests.rglob("*.md")]:
        try:
            records.append(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError) as exc:
            raise WorkspaceGcRefused(
                f"cannot inspect retained fixture reference {path}: {exc}") from exc
    return records


def plan() -> dict:
    from library.tools.retention import KEEP, retention_mode

    home = vep_home().expanduser().resolve()
    mode = retention_mode()
    if mode == KEEP:
        return {"format": "ren-workspace-gc/1", "home": str(home),
                "mode": mode, "created_at": datetime.now(UTC).isoformat(),
                "kept_workflow_runs": [], "candidates": []}
    qualification = qualification_project.media_dir().resolve()
    if not qualification.is_relative_to(home):
        raise WorkspaceGcRefused(
            f"qualification root {qualification} is outside Ren's data home "
            f"{home}; refusing cross-root collection")
    workflow_root = qualification / "workflow-runs"
    runs = sorted((p for p in workflow_root.iterdir() if p.is_dir()),
                  key=lambda p: (p.stat().st_mtime_ns, p.name), reverse=True) \
        if workflow_root.is_dir() else []
    unfinished = []
    for run in runs:
        report = run / "qualification-report.json"
        try:
            complete = bool(json.loads(report.read_text(encoding="utf-8"))
                            .get("finished_at"))
        except FileNotFoundError:
            complete = False
        except (OSError, ValueError) as exc:
            raise WorkspaceGcRefused(
                f"cannot prove workflow run completion from {report}: {exc}") from exc
        if not complete:
            unfinished.append(run)
    recent = runs[:KEEP_WORKFLOW_RUNS]
    retained = list(dict.fromkeys([*recent, *unfinished]))
    roots = _project_roots()
    references = _reference_text(roots)
    digests = _referenced_digests(roots, retained)
    candidates: list[Entry] = []

    for old_run in runs:
        if old_run in retained:
            continue
        for path in _files(old_run):
            if path.suffix.lower() in PROTECTED_SUFFIXES:
                continue
            absolute = str(path.resolve())
            if any(absolute in record for record in references):
                continue
            candidates.append(Entry(str(path), path.stat().st_size,
                                    _digest(path), "workflow run outside newest-two retention"))

    memory_root = source_memory.memory_root().expanduser().resolve()
    if not memory_root.is_relative_to(home):
        raise WorkspaceGcRefused(
            f"source-memory root {memory_root} is outside Ren's data home "
            f"{home}; refusing cross-root collection")
    if memory_root.exists():
        for entry in sorted(memory_root.iterdir()):
            if (not entry.is_dir() or entry.is_symlink()
                    or entry.name in digests):
                continue
            # Keep malformed or non-content-addressed directories. Only a
            # 64-character digest directory has the cache's owned shape.
            if len(entry.name) != 64 or any(c not in "0123456789abcdef" for c in entry.name):
                continue
            files = list(_files(entry))
            for path in files:
                if path.suffix.lower() in PROTECTED_SUFFIXES:
                    continue
                candidates.append(Entry(str(path), path.stat().st_size,
                                        _digest(path), "source digest has no project-state reference"))

    scratch = home / "scratch"
    if scratch.is_dir():
        # Scratch files are eligible only when no project state names their
        # absolute path. Read failures refuse, rather than implying deadness.
        for path in _files(scratch):
            if path.suffix.lower() in PROTECTED_SUFFIXES:
                continue
            absolute = str(path.resolve())
            if any(absolute in record for record in references):
                continue
            candidates.append(Entry(absolute, path.stat().st_size,
                                    _digest(path), "unreferenced Ren scratch file"))

    unique = {entry.path: entry for entry in candidates}
    return {"format": "ren-workspace-gc/1", "home": str(home), "mode": mode,
            "created_at": datetime.now(UTC).isoformat(),
            "kept_workflow_runs": [str(path) for path in retained],
            "candidates": [asdict(unique[path]) for path in sorted(unique)]}


def write_plan(document: dict) -> Path:
    directory = vep_home() / PLAN_DIRNAME
    directory.mkdir(parents=True, exist_ok=True)
    path = Path(journal_naming.unique_path(str(directory), "workspace-gc"))
    path.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
    return path


def apply_plan(manifest: str | Path) -> dict:
    path = Path(manifest).expanduser().resolve()
    try:
        before = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise WorkspaceGcRefused(f"cannot read GC manifest {path}: {exc}") from exc
    if before.get("format") != "ren-workspace-gc/1":
        raise WorkspaceGcRefused("unsupported workspace GC manifest")
    if before.get("mode") != "lean":
        if before.get("mode") == "keep":
            return {"removed": [], "bytes": 0}
        raise WorkspaceGcRefused("manifest has no declared retention mode")
    fresh = plan()
    expected = {row["path"]: row for row in before["candidates"]}
    current = {row["path"]: row for row in fresh["candidates"]}
    if any(current.get(key) != value for key, value in expected.items()):
        raise WorkspaceGcRefused("candidate reachability or content changed; nothing removed")
    home = Path(before["home"]).resolve()
    removed = []
    for item in before["candidates"]:
        target = Path(item["path"]).resolve()
        if not target.is_relative_to(home):
            raise WorkspaceGcRefused(f"candidate escaped Ren's data home: {target}")
        if not target.is_file() or target.is_symlink():
            raise WorkspaceGcRefused(f"candidate is no longer a regular file: {target}")
        if target.stat().st_size != item["bytes"] or _digest(target) != item["sha256"]:
            raise WorkspaceGcRefused(f"candidate changed since planning: {target}")
    for item in before["candidates"]:
        target = Path(item["path"])
        target.unlink()
        removed.append(str(target))
    before["applied_at"] = datetime.now(UTC).isoformat()
    before["removed"] = removed
    path.write_text(json.dumps(before, indent=2) + "\n", encoding="utf-8")
    return {"removed": removed, "bytes": sum(row["bytes"] for row in before["candidates"])}
