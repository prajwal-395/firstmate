"""Plan and apply bounded collection of Ren's private data stores.

The collector is intentionally separate from project retention. It only
walks Ren's own data home, keeps the newest qualification workflow runs,
and keeps source-memory digests referenced by project state or retained
qualification runs. Qualification media and repository fixtures are never
candidate roots. A plan and JSON proof are written before apply; apply
recomputes the plan and moves exact, non-media candidates to a 24-hour
restorable quarantine.
"""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import logging
import os
import re
import subprocess
import sys
import time
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from library.tools import git_guard as _git_guard
from library.tools import journal_naming, qualification_project, source_memory
from library.tools.shared_environment import vep_home

KEEP_WORKFLOW_RUNS = 2
PLAN_DIRNAME = "gc-plans"
PROTECTED_SUFFIXES = (".drp", ".drt", ".otio")
PROTECTED_MEDIA_SUFFIXES = frozenset({
    ".3gp", ".aac", ".aif", ".aiff", ".alac", ".amr", ".arw",
    ".avi", ".avif", ".bmp", ".cr2", ".flac", ".flv", ".gif",
    ".heic", ".ico", ".jpeg", ".jpg", ".m2ts", ".m4a", ".m4v",
    ".mkv", ".mov", ".mp3", ".mp4", ".mpeg", ".mpg", ".mxf",
    ".nef", ".oga", ".ogg", ".opus", ".png", ".raw", ".svg",
    ".tif", ".tiff", ".ts", ".wav", ".webm", ".webp",
})
PROTECTED_MODEL_SUFFIXES = frozenset({
    ".bin", ".ckpt", ".gguf", ".h5", ".model", ".onnx", ".pt",
    ".pth", ".safetensors",
})
QUARANTINE_GRACE = timedelta(hours=24)


class WorkspaceGcRefused(RuntimeError):
    """Collection could not prove that the full plan remains safe."""


@dataclass(frozen=True)
class Entry:
    path: str
    bytes: int
    sha256: str
    reason: str
    device: int
    inode: int
    mtime_ns: int
    link_count: int


def _entry(path: Path, reason: str) -> Entry:
    info = path.stat()
    return Entry(str(path.resolve()), info.st_size, _digest(path), reason,
                 info.st_dev, info.st_ino, info.st_mtime_ns, info.st_nlink)


def _protected(path: Path) -> bool:
    suffix = path.suffix.lower()
    return (suffix in PROTECTED_SUFFIXES
            or suffix in PROTECTED_MEDIA_SUFFIXES
            or suffix in PROTECTED_MODEL_SUFFIXES
            or any(part.lower() in {"model", "models", "weights",
                                    "model_weights"} for part in path.parts)
            or suffix == ".gz")


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
            # The evidence an APPROVED decision rests on is evidence the
            # captain's own ruling still needs. A clip that left the
            # catalog does not stop being the footage an approved reel
            # was cut from, and collecting its measurements would
            # invalidate the decision that cites them.
            try:
                from library.tools import decision_evidence
                found.update(decision_evidence.cited_digests(str(root)))
            except (OSError, TypeError, ValueError) as exc:
                raise WorkspaceGcRefused(
                    f"cannot establish decision reachability for "
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


def _reference_text(roots: list[Path]):
    """Read durable project and fixture records that may pin an exact path."""
    for root in roots:
        for path in root.rglob("*"):
            if path.is_symlink() or not path.is_file() or path.suffix.lower() not in {
                    ".json", ".txt", ".md", ".yaml", ".yml"}:
                continue
            try:
                yield path.read_text(encoding="utf-8")
            except (OSError, UnicodeError) as exc:
                raise WorkspaceGcRefused(
                    f"cannot inspect project reachability record {path}: {exc}") from exc
    tests = Path(__file__).resolve().parents[2] / "tests"
    for path in [*tests.rglob("*.py"), *tests.rglob("*.json"),
                 *tests.rglob("*.md")]:
        try:
            yield path.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            raise WorkspaceGcRefused(
                f"cannot inspect retained fixture reference {path}: {exc}") from exc


def plan() -> dict:
    from library.tools.retention import KEEP, retention_mode

    home = vep_home().expanduser().resolve()
    mode = retention_mode()
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
    retained = (runs if mode == KEEP else
                list(dict.fromkeys([*recent, *unfinished])))
    roots = _project_roots()
    digests = _referenced_digests(roots, retained)
    candidates: list[Entry] = []

    if mode != KEEP:
        for old_run in runs:
            if old_run in retained:
                continue
            for path in _files(old_run):
                if _protected(path) or path.stat().st_nlink != 1:
                    continue
                candidates.append(_entry(
                    path, "workflow run outside newest-two retention"))

    memory_root = source_memory.memory_root().expanduser().resolve()
    if not memory_root.is_relative_to(home):
        raise WorkspaceGcRefused(
            f"source-memory root {memory_root} is outside Ren's data home "
            f"{home}; refusing cross-root collection")
    if mode != KEEP and memory_root.exists():
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
                if _protected(path) or path.stat().st_nlink != 1:
                    continue
                candidates.append(_entry(
                    path, "source digest has no project-state reference"))

    scratch = home / "scratch"
    if scratch.is_dir():
        # Scratch files are eligible only when no project state names their
        # absolute path. Read failures refuse, rather than implying deadness.
        for path in _files(scratch):
            if _protected(path) or path.stat().st_nlink != 1:
                continue
            candidates.append(_entry(path, "unreferenced Ren scratch file"))

    # Search each durable record once for any candidate path. Checking every
    # path against every retained record made the plan quadratic in the
    # candidate count and held the full project corpus in memory.
    candidate_paths = {entry.path for entry in candidates}
    referenced: set[str] = set()
    if candidate_paths:
        alternatives = "|".join(
            re.escape(path) for path in sorted(candidate_paths, key=len, reverse=True))
        path_pattern = re.compile(f"(?=({alternatives}))")
        for record in _reference_text([*roots, *retained]):
            referenced.update(match.group(1)
                              for match in path_pattern.finditer(record))
            if len(referenced) == len(candidate_paths):
                break
    candidates = [entry for entry in candidates
                  if entry.path not in referenced]

    unique = {entry.path: entry for entry in candidates}
    collectable, refused = _git_guard.partition(
        sorted(unique), owned_roots=[str(home)])
    refused_git = [
        {"path": hit.path, "resolved_path": hit.resolved_path,
         "reason": hit.reason, "work_tree": hit.work_tree,
         "detail": _git_guard.describe(hit)}
        for hit in refused]
    return {"format": "ren-workspace-gc/2", "home": str(home), "mode": mode,
            "created_at": datetime.now(UTC).isoformat(),
            "kept_workflow_runs": [str(path) for path in retained],
            "candidates": [asdict(unique[path]) for path in collectable],
            "refused_git": refused_git}


def write_plan(document: dict) -> Path:
    directory = vep_home() / PLAN_DIRNAME
    directory.mkdir(parents=True, exist_ok=True)
    path = Path(journal_naming.unique_path(str(directory), "workspace-gc"))
    path.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
    return path


def _require_cleanup_qualification() -> None:
    """Require every project in scope to carry intact lifecycle evidence."""
    from library.tools.storage_lifecycle import cleanup_qualification

    projects = _project_roots()
    unqualified = [str(root) for root in projects
                   if cleanup_qualification(str(root)) is None]
    if unqualified:
        raise WorkspaceGcRefused(
            "automatic workspace retirement requires storage lifecycle "
            f"qualification for every project in scope; missing or invalid: {unqualified}"
        )


@contextmanager
def _manifest_lock(path: Path):
    lock_path = path.with_suffix(path.suffix + ".lock")
    with lock_path.open("a", encoding="utf-8") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


def _read_manifest(path: Path) -> dict:
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise WorkspaceGcRefused(f"cannot read GC manifest {path}: {exc}") from exc
    if document.get("format") != "ren-workspace-gc/2":
        raise WorkspaceGcRefused("unsupported workspace GC manifest")
    return document


def _same_identity(path: Path, item: dict) -> bool:
    try:
        info = path.lstat()
    except OSError:
        return False
    return (path.is_file() and not path.is_symlink()
            and info.st_dev == item["device"]
            and info.st_ino == item["inode"]
            and info.st_size == item["bytes"]
            and info.st_mtime_ns == item["mtime_ns"]
            and info.st_nlink == item["link_count"]
            and _digest(path) == item["sha256"])


def _inside_home(path: Path, home: Path) -> bool:
    try:
        return path.resolve(strict=False).is_relative_to(home.resolve(strict=True))
    except OSError:
        return False


def _write_manifest(path: Path, document: dict) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(document, indent=2) + "\n",
                         encoding="utf-8")
    os.replace(temporary, path)


def apply_plan(manifest: str | Path) -> dict:
    path = Path(manifest).expanduser().resolve()
    with _manifest_lock(path):
        before = _read_manifest(path)
        if before.get("mode") != "lean":
            if before.get("mode") == "keep":
                return {"quarantined": [], "bytes": 0}
            raise WorkspaceGcRefused("manifest has no declared retention mode")
        if not before["candidates"]:
            return {"quarantined": [], "bytes": 0}
        if before.get("quarantined"):
            raise WorkspaceGcRefused("manifest has already been applied")
        _require_cleanup_qualification()
        fresh = plan()
        expected = {row["path"]: row for row in before["candidates"]}
        current = {row["path"]: row for row in fresh["candidates"]}
        if any(current.get(key) != value for key, value in expected.items()):
            raise WorkspaceGcRefused(
                "candidate reachability or content changed; nothing moved")
        home = Path(before["home"]).expanduser().resolve(strict=True)
        if home != vep_home().expanduser().resolve(strict=True):
            raise WorkspaceGcRefused(
                "manifest Ren data home does not match this machine")
        stamp = datetime.now(UTC)
        quarantine_parent = home / "quarantine"
        quarantine_root = quarantine_parent / "workspace-gc"
        if quarantine_parent.is_symlink() or quarantine_root.is_symlink():
            raise WorkspaceGcRefused("Ren workspace quarantine path is a symlink")
        quarantine = quarantine_root / stamp.strftime("%Y%m%dT%H%M%S%fZ")
        if not _inside_home(quarantine, home):
            raise WorkspaceGcRefused("quarantine path escaped Ren's data home")
        quarantine.mkdir(parents=True, exist_ok=False)
        quarantined = []
        before["applied_at"] = stamp.isoformat()
        before["restore_until"] = (stamp + QUARANTINE_GRACE).isoformat()
        before["quarantine_root"] = str(quarantine)
        before["quarantined"] = quarantined
        _write_manifest(path, before)
        worker = subprocess.Popen(
            [sys.executable, "-m", "library.tools.workspace_gc", "--expire",
             str(path)], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, close_fds=True, start_new_session=True)
        before["expiry_worker_pid"] = worker.pid
        _write_manifest(path, before)
        for item in before["candidates"]:
            target = Path(item["path"])
            hit = _git_guard.check(target, owned_roots=[str(home)])
            if hit is not None:
                raise WorkspaceGcRefused(_git_guard.describe(hit))
            if not target.is_absolute() or not _inside_home(target, home):
                raise WorkspaceGcRefused(
                    f"candidate escaped Ren's data home: {target}")
            if not _same_identity(target, item):
                raise WorkspaceGcRefused(
                    f"candidate identity changed since planning: {target}")
            digest = hashlib.sha256(item["path"].encode("utf-8")).hexdigest()[:16]
            destination = quarantine / f"{digest}-{target.name}"
            if os.path.lexists(destination):
                raise WorkspaceGcRefused(
                    f"quarantine destination already exists: {destination}")
            os.replace(target, destination)
            quarantined.append({**item, "quarantine_path": str(destination),
                                "state": "quarantined"})
            _write_manifest(path, before)
        return {"quarantined": quarantined,
                "bytes": sum(row["bytes"] for row in quarantined),
                "restore_until": before["restore_until"],
                "manifest": str(path), "expiry_worker_pid": worker.pid}


def restore_plan(manifest: str | Path) -> dict:
    path = Path(manifest).expanduser().resolve()
    with _manifest_lock(path):
        document = _read_manifest(path)
        if not document.get("quarantined"):
            return {"restored": [], "bytes": 0}
        if datetime.now(UTC) >= datetime.fromisoformat(document["restore_until"]):
            raise WorkspaceGcRefused("the 24-hour restore window has expired")
        home = Path(document["home"]).expanduser().resolve(strict=True)
        if home != vep_home().expanduser().resolve(strict=True):
            raise WorkspaceGcRefused(
                "manifest Ren data home does not match this machine")
        quarantine_root = Path(document["quarantine_root"]).resolve(strict=True)
        if quarantine_root != home / "quarantine" / "workspace-gc" / quarantine_root.name:
            raise WorkspaceGcRefused("manifest quarantine root is not owned by Ren")
        restored = []
        for item in document["quarantined"]:
            if item["state"] != "quarantined":
                continue
            source = Path(item["quarantine_path"])
            target = Path(item["path"])
            if (not _inside_home(source, home)
                    or not _inside_home(target, home)):
                raise WorkspaceGcRefused("restore path escaped Ren's data home")
            if source.parent != quarantine_root:
                raise WorkspaceGcRefused("quarantined file escaped its batch")
            if not _same_identity(source, item):
                raise WorkspaceGcRefused(
                    f"quarantined identity changed: {source}")
            if os.path.lexists(target):
                raise WorkspaceGcRefused(
                    f"restore destination already exists: {target}")
            target.parent.mkdir(parents=True, exist_ok=True)
            os.replace(source, target)
            item["state"] = "restored"
            restored.append(str(target))
            _write_manifest(path, document)
        return {"restored": restored,
                "bytes": sum(row["bytes"] for row in document["quarantined"]
                             if row["path"] in restored),
                "manifest": str(path)}


def expire_plan(manifest: str | Path) -> dict:
    path = Path(manifest).expanduser().resolve()
    with _manifest_lock(path):
        document = _read_manifest(path)
        until = datetime.fromisoformat(document["restore_until"])
        delay = (until - datetime.now(UTC)).total_seconds()
    if delay > 0:
        time.sleep(delay)
    with _manifest_lock(path):
        document = _read_manifest(path)
        home = Path(document["home"]).expanduser().resolve(strict=True)
        if home != vep_home().expanduser().resolve(strict=True):
            raise WorkspaceGcRefused(
                "manifest Ren data home does not match this machine")
        _require_cleanup_qualification()
        quarantine_root = Path(document["quarantine_root"]).resolve(strict=True)
        if quarantine_root != home / "quarantine" / "workspace-gc" / quarantine_root.name:
            raise WorkspaceGcRefused("manifest quarantine root is not owned by Ren")
        expired = []
        for item in document.get("quarantined", []):
            if item["state"] != "quarantined":
                continue
            source = Path(item["quarantine_path"])
            target = Path(item["path"])
            if (not _inside_home(source, home)
                    or not _inside_home(target, home)):
                raise WorkspaceGcRefused("expiry path escaped Ren's data home")
            if source.parent != quarantine_root:
                raise WorkspaceGcRefused("quarantined file escaped its batch")
            if os.path.lexists(target):
                raise WorkspaceGcRefused(
                    f"original path was recreated before expiry: {target}")
            hit = _git_guard.check(source, owned_roots=[str(home)])
            if hit is not None:
                raise WorkspaceGcRefused(_git_guard.describe(hit))
            if not _same_identity(source, item):
                raise WorkspaceGcRefused(f"quarantined identity changed: {source}")
            source.unlink()
            item["state"] = "expired"
            expired.append(str(source))
            _write_manifest(path, document)
        document["expired_at"] = datetime.now(UTC).isoformat()
        _write_manifest(path, document)
        return {"expired": expired,
                "bytes": sum(row["bytes"] for row in document.get("quarantined", [])
                             if row["quarantine_path"] in expired)}


def _expire_worker(manifest: str) -> int:
    try:
        expire_plan(manifest)
    except Exception as exc:  # noqa: BLE001 - persist any missed expiry
        path = Path(manifest).expanduser().resolve()
        try:
            with _manifest_lock(path):
                document = _read_manifest(path)
                document["expiry_error"] = str(exc)
                _write_manifest(path, document)
        except (OSError, WorkspaceGcRefused, ValueError, KeyError) as persist_error:
            logging.getLogger(__name__).error(
                "could not persist workspace GC expiry failure for %s: %s",
                path,
                persist_error,
            )
        return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="workspace-gc-worker")
    parser.add_argument("--expire", required=True)
    args = parser.parse_args(argv)
    return _expire_worker(args.expire)


if __name__ == "__main__":
    raise SystemExit(main())
