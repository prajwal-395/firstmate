"""Durable records and named workflows for caller-supplied Resolve scripts.

Project records live beside Ren's review and edit-history files, inside the
project's local version store. User records live in Ren's shared per-user state.
The store keeps the exact script, its stated purpose, target, caller, outcome,
and (for writes) the recovery archive and measured timeline inventory diff.

Recurring scripts are surfaced as candidates after two matching runs. A person
or agent explicitly promotes one recorded run to a named workflow; nothing is
promoted just because it recurred.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path

FORMAT = "resolve_script_run/1"
WORKFLOW_FORMAT = "resolve_script_workflow/1"
RUNS_DIRNAME = "runs"
WORKFLOWS_DIRNAME = "workflows"
PROJECT_STORE_DIRNAME = "resolve_scripts"
USER_STORE_DIRNAME = "resolve-scripts"
_SAFE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$")


class ScriptStoreError(RuntimeError):
    """A script record or workflow could not be read or saved safely."""


def project_root_from(cwd: str | os.PathLike, explicit: str = "") -> Path | None:
    """Find a Ren project root without treating an enclosing checkout as one."""
    if explicit:
        candidate = Path(explicit).expanduser().resolve()
        if candidate.is_file() and candidate.name == "project.yaml":
            candidate = candidate.parent
        if not candidate.is_dir() or not (candidate / "project.yaml").is_file():
            raise ScriptStoreError(
                f"project directory {str(candidate)!r} has no project.yaml")
        return candidate
    candidate = Path(cwd).expanduser().resolve()
    if candidate.is_file():
        candidate = candidate.parent
    for parent in (candidate, *candidate.parents):
        if (parent / "project.yaml").is_file():
            return parent
    return None


def project_store_dir(project_root: str | os.PathLike) -> Path:
    from library.tools.project_layout import Area, ProjectLayout

    root = Path(project_root).expanduser().resolve()
    return ProjectLayout(root).read_dir(Area.REVIEW) / PROJECT_STORE_DIRNAME


def user_store_dir() -> Path:
    from library.tools.shared_environment import vep_home

    return (vep_home() / USER_STORE_DIRNAME).expanduser().resolve()


def store_dir(scope: str, project_root: str | os.PathLike | None = None) -> Path:
    if scope == "project":
        if project_root is None:
            raise ScriptStoreError(
                "project-scoped Resolve script records need a Ren project "
                "directory; pass --project-dir or run from inside the project")
        return project_store_dir(project_root)
    if scope == "user":
        return user_store_dir()
    raise ScriptStoreError(f"unknown Resolve script store scope {scope!r}")


def _write_json(path: Path, document: dict, prefix: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(document, indent=2, sort_keys=True,
                         ensure_ascii=False, default=str) + "\n"
    fd, staged = tempfile.mkstemp(dir=path.parent, prefix=prefix,
                                  suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as out:
            out.write(payload)
        os.replace(staged, path)
    except BaseException:
        try:
            os.unlink(staged)
        except OSError:
            pass
        raise
    return path


def normalise_purpose(purpose: str) -> str:
    return " ".join(str(purpose).casefold().split())


def normalized_script_hash(script: str) -> str:
    """Hash Python syntax, so whitespace and comments do not split a match."""
    import ast

    try:
        tree = ast.parse(script)
    except SyntaxError:
        canonical = " ".join(script.split())
    else:
        canonical = ast.dump(tree, annotate_fields=True,
                             include_attributes=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def purpose_hash(purpose: str) -> str:
    return hashlib.sha256(normalise_purpose(purpose).encode("utf-8")).hexdigest()


def new_run_id() -> str:
    from uuid import uuid4

    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ") + "-" + uuid4().hex[:12]


def _project_git_root(project_root: Path) -> bool:
    """Return true only when this project is the exact local git store root."""
    proc = subprocess.run(
        ["git", "-C", str(project_root), "rev-parse", "--show-toplevel"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=10, check=False)
    if proc.returncode != 0:
        return False
    try:
        return Path(proc.stdout.strip()).resolve() == project_root.resolve()
    except OSError:
        return False


def _commit_project_paths(project_root: Path, paths: list[Path], message: str) -> dict:
    """Commit only the named Ren records, never footage or unrelated edits."""
    if not _project_git_root(project_root):
        return {"committed": False, "reason": "project version store not initialized"}
    relative = [str(path.resolve().relative_to(project_root.resolve()))
                for path in paths]
    add = subprocess.run(
        ["git", "-C", str(project_root), "add", "--", *relative],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=30, check=False)
    if add.returncode != 0:
        return {"committed": False, "reason": add.stderr.strip()[-500:]}
    commit = subprocess.run(
        ["git", "-C", str(project_root), "commit", "--only", "-m",
         message, "--", *relative], capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=30, check=False)
    if commit.returncode != 0:
        return {"committed": False,
                "reason": (commit.stderr or commit.stdout).strip()[-500:]}
    return {"committed": True, "summary": commit.stdout.strip()[-500:]}


def save_run(scope: str, record: dict,
             project_root: str | os.PathLike | None = None,
             extra_paths: tuple[Path, ...] = ()) -> tuple[Path, dict]:
    """Persist a run atomically, and version only its record and backup."""
    root = Path(project_root).expanduser().resolve() if project_root else None
    directory = store_dir(scope, root)
    path = directory / RUNS_DIRNAME / f"{record['run_id']}.json"
    _write_json(path, {"format": FORMAT, **record}, ".resolve-script-run-")
    commit = {"committed": False, "reason": "user-scoped record"}
    if scope == "project" and root is not None:
        commit = _commit_project_paths(
            root, [path, *extra_paths],
            f"resolve script run {record['run_id']}")
    elif root is not None and extra_paths:
        commit = _commit_project_paths(
            root, list(extra_paths),
            f"resolve script recovery {record['run_id']}")
    return path, commit


def _read_records(directory: Path) -> list[dict]:
    root = directory / RUNS_DIRNAME
    if not root.exists():
        return []
    records = []
    for path in sorted(root.glob("*.json")):
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise ScriptStoreError(
                f"cannot read Resolve script record {str(path)!r}: {exc}") from exc
        if record.get("format") != FORMAT:
            raise ScriptStoreError(
                f"unsupported Resolve script record format in {str(path)!r}")
        records.append(record)
    return records


def _candidate_rows(records: list[dict]) -> list[dict]:
    """Return recurring script or purpose groups, without auto-promotion."""
    groups: dict[tuple[str, str], list[dict]] = {}
    for record in records:
        groups.setdefault(("script", str(record["script_hash"])), []).append(record)
        groups.setdefault(("purpose", str(record["purpose_hash"])), []).append(record)
    rows = []
    for (kind, digest), matches in groups.items():
        if len(matches) < 2:
            continue
        matches = sorted(matches, key=lambda row: row["at"])
        representative = matches[-1]
        ids = [str(row["run_id"]) for row in matches]
        rows.append({
            "candidate_id": f"{kind}-{digest[:16]}",
            "matched_by": kind,
            "occurrences": len(matches),
            "script_variants": len({str(row["script_hash"]) for row in matches}),
            "purposes": sorted({str(row["purpose"]) for row in matches}),
            "sample_run_id": representative["run_id"],
            "run_ids": ids,
            "scope": representative["scope"],
        })
    return sorted(rows, key=lambda row: (row["matched_by"], row["candidate_id"]))


def candidates(scope: str, project_root: str | os.PathLike | None = None) -> list[dict]:
    return _candidate_rows(_read_records(store_dir(scope, project_root)))


def list_runs(scope: str, project_root: str | os.PathLike | None = None) -> list[dict]:
    return _read_records(store_dir(scope, project_root))


def _safe_workflow_name(name: str) -> str:
    value = str(name or "").strip()
    if not _SAFE_NAME.fullmatch(value):
        raise ScriptStoreError(
            "workflow names must be 1-80 letters, digits, dots, underscores "
            "or dashes, and begin with a letter or digit")
    return value


def promote(scope: str, project_root: str | os.PathLike | None,
            candidate_id: str, run_id: str, name: str,
            caller: str) -> tuple[Path, dict]:
    """Save one explicitly selected recurring run as a named workflow."""
    directory = store_dir(scope, project_root)
    record = next((row for row in _read_records(directory)
                   if str(row["run_id"]) == str(run_id)), None)
    if record is None:
        raise ScriptStoreError(f"no recorded run {run_id!r} in {scope} store")
    choices = {row["candidate_id"]: row for row in
               _candidate_rows(_read_records(directory))}
    candidate = choices.get(candidate_id)
    if candidate is None or run_id not in candidate["run_ids"]:
        raise ScriptStoreError(
            f"run {run_id!r} is not a member of recurring candidate "
            f"{candidate_id!r}")
    workflow_name = _safe_workflow_name(name)
    path = directory / WORKFLOWS_DIRNAME / f"{workflow_name}.json"
    if path.exists():
        raise ScriptStoreError(f"workflow {workflow_name!r} already exists")
    workflow = {
        "format": WORKFLOW_FORMAT,
        "name": workflow_name,
        "scope": scope,
        "purpose": record["purpose"],
        "script": record["script"],
        "script_hash": record["script_hash"],
        "promoted_from": {"candidate_id": candidate_id, "run_id": run_id},
        "promoted_at": datetime.now(timezone.utc).isoformat(),
        "promoted_by": caller,
    }
    _write_json(path, workflow, ".resolve-script-workflow-")
    commit = {"committed": False, "reason": "user-scoped workflow"}
    if scope == "project" and project_root is not None:
        root = Path(project_root).expanduser().resolve()
        commit = _commit_project_paths(
            root, [path], f"promote Resolve workflow {workflow_name}")
    return path, commit


def workflows(scope: str, project_root: str | os.PathLike | None = None) -> list[dict]:
    root = store_dir(scope, project_root) / WORKFLOWS_DIRNAME
    if not root.exists():
        return []
    found = []
    for path in sorted(root.glob("*.json")):
        try:
            workflow = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise ScriptStoreError(
                f"cannot read Resolve workflow {str(path)!r}: {exc}") from exc
        if workflow.get("format") != WORKFLOW_FORMAT:
            raise ScriptStoreError(
                f"unsupported Resolve workflow format in {str(path)!r}")
        found.append(workflow)
    return found


def load_workflow(scope: str, project_root: str | os.PathLike | None,
                  name: str) -> dict:
    wanted = _safe_workflow_name(name)
    workflow = next((row for row in workflows(scope, project_root)
                     if row["name"] == wanted), None)
    if workflow is None:
        raise ScriptStoreError(f"no {scope} Resolve workflow named {wanted!r}")
    return workflow


def project_inventory_diff(before: list[dict], after: list[dict],
                           target_name: str, target_id: str) -> dict:
    """Compare all project timelines, allowing edits only to the target."""
    def keyed(rows):
        return {str(row["unique_id"] or row["name"]): row for row in rows}

    old, new = keyed(before), keyed(after)
    added = [new[key] for key in sorted(new.keys() - old.keys())]
    removed = [old[key] for key in sorted(old.keys() - new.keys())]
    changed = []
    for key in sorted(old.keys() & new.keys()):
        if old[key] != new[key]:
            changed.append({"before": old[key], "after": new[key]})
    target_key = target_id or target_name
    foreign = ([row for row in added if str(row.get("unique_id") or row["name"]) != target_key]
               + [row for row in removed if str(row.get("unique_id") or row["name"]) != target_key]
               + [row for row in changed
                  if str(row["before"].get("unique_id") or row["before"]["name"])
                  != target_key])
    target_after = new.get(target_key)
    target_present = bool(target_after and target_after.get("name") == target_name)
    return {"before": before, "after": after,
            "added": added, "removed": removed, "changed": changed,
            "target_present": target_present,
            "guard_passed": not foreign and target_present}
