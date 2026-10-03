"""TASK WORKTREES: one project store, many tasks each holding its own
semantic state, merged back. Part of the version model
(`library/tools/versions/__init__.py`); AGENTS.md 3: run state.

Reached through `ren task start|finish` for automatic semantic work, and
`ren worktree add|list|commit|merge|remove` for the underlying manual
operations.

Why
---
The project's store (`store`) is ONE git repo with ONE checkout, and a
branch is a property of that checkout, not of a task. `variants`
switches it (`git checkout -b variant/...`), so while one task works on
a variant every other task - a reel build, a touch-up, a planning step -
writes onto that variant's branch. Measured 2026-10-02 on the captain's
geo-podcast project: the checkout had sat on
`variant/r09-batch-rebuild-2026-09-29` since 2026-09-29 with 127 tracked
modifications to master's declarations and run state on it.

A task worktree gives a task its own checkout of the store, on its own
branch `ren/<task>`, outside the project folder
(`$REN_WORKTREES`, default `~/.ren/worktrees/<project>-<hash>/<task>`).
Opening, committing and removing one never moves the project
checkout's branch.

Task lifecycle
--------------
`ren task start <project> <task>` returns the workspace an agent must use.
An omitted `--claim` reserves the whole semantic store; repeated
project-relative `--claim` paths allow concurrent tasks only when their
declared write paths do not overlap. The finish command validates those
claims, commits the worktree, merges semantic state, then removes it.
Generated run state is discarded from the merge. A successful semantic
change reports that the appropriate Resolve build and verification may be
needed; finish never calls Resolve.

The commands coordinate task starts and finishes with a per-project file
lock. A second task with overlapping claims, an unknown open `ren/` branch,
or a project checkout already on a Ren task branch refuses. Resolve work is
outside this semantic lifecycle: finish the task first, then rebuild from
the project checkout.

What a worktree holds, and what it does not
-------------------------------------------
Exactly what git owns: the store's allow-list - declarations, plans,
step outputs, model answers, review records, provenance. Runtime state
stays where it is and is never versioned: the timeline shadow store
(`timeline_shadow`) and the resource scheduler (`resource_scheduler`)
are machine SQLite files, and renders, caches and footage are outside
the allow-list. So a worktree is where a task does its FREE work; the
Resolve build runs from the project folder after the merge, as for any
declaration change ("merge the source, rebuild", `variants`).

Merge
-----
The manual `merge` is `variants.merge_variations` into the project
checkout's current branch. `task finish` uses its semantic-only mode:
generated run state is kept at the target, semantic conflicts abort the
merge so the task can be revised and retried, and a clean semantic merge
reports the rebuild obligation. Both paths require a clean tracked tree;
neither silently rebuilds a Resolve timeline.

`tests/unit/context/test_project_layout.py`.
"""

from __future__ import annotations

import argparse
import contextlib
import datetime as dt
import fcntl
import fnmatch
import hashlib
import json
import os
import re
import sys
import tempfile
from pathlib import Path

from library.tools.versions import store, variants

ROOT_ENV = "REN_WORKTREES"
BRANCH_PREFIX = "ren/"
TASK_REGISTRY = ".ren-tasks.json"
TASK_REGISTRY_LOCK = ".ren-tasks.lock"
TASKS_FORMAT = "ren_tasks/1"


def worktrees_root() -> Path:
    """`$REN_WORKTREES`, or `~/.ren/worktrees`."""
    explicit = os.environ.get(ROOT_ENV)
    return Path(explicit) if explicit else Path.home() / ".ren" / "worktrees"


def task_slug(task: str) -> str:
    """A task name as a branch and directory component; refuses an empty one."""
    slug = re.sub(r"[^a-z0-9._-]+", "-", task.strip().lower()).strip("-.")
    if not slug:
        raise ValueError(f"task name {task!r} has no usable characters")
    return slug


def task_branch(task: str) -> str:
    return BRANCH_PREFIX + task_slug(task)


def task_path(project_folder: str, task: str) -> Path:
    """Where `task`'s worktree of this project lives.

    Keyed by the project folder's name plus a hash of its resolved path,
    so two projects that share a folder name never share worktrees.
    """
    return project_worktrees_dir(project_folder) / task_slug(task)


def project_worktrees_dir(project_folder: str) -> Path:
    """The per-project directory that holds task checkouts and records."""
    root = Path(project_folder).resolve()
    key = hashlib.sha1(str(root).encode("utf-8")).hexdigest()[:8]
    return worktrees_root() / f"{root.name}-{key}"


def _no_repo(project_folder: str) -> str:
    if (Path(project_folder) / ".git").exists():
        return ""
    return ("no git repo - run init_project_repo "
            "(library/tools/versions/store.py) first")


def add(project_folder: str, task: str, base: str | None = None) -> dict:
    """Open a worktree for `task` on a new branch `ren/<task>` at `base`.

    `base` defaults to the project checkout's HEAD COMMIT. Uncommitted
    edits in the project checkout are not carried, and the result names
    the commit the task forked from.
    """
    blocked = _no_repo(project_folder)
    if blocked:
        return {"added": False, "reason": blocked}
    branch = task_branch(task)
    path = task_path(project_folder, task)
    if store.git(project_folder, "rev-parse", "--verify", "--quiet",
                 f"refs/heads/{branch}").returncode == 0:
        return {"added": False, "reason": f"branch {branch} already exists"}
    if path.exists():
        return {"added": False, "reason": f"{path} already exists"}
    start = store.git(project_folder, "rev-parse", "--verify",
                      f"{base or 'HEAD'}^{{commit}}")
    if start.returncode != 0:
        return {"added": False,
                "reason": f"base {base or 'HEAD'!r} is not a commit"}
    path.parent.mkdir(parents=True, exist_ok=True)
    proc = store.git(project_folder, "worktree", "add", "-b", branch,
                     str(path), start.stdout.strip())
    if proc.returncode != 0:
        return {"added": False,
                "reason": f"git worktree add failed: "
                          f"{proc.stderr.strip()[-200:]}"}
    return {"added": True, "task": task_slug(task), "branch": branch,
            "path": str(path), "base": start.stdout.strip()[:12]}


def list_tasks(project_folder: str) -> list[dict]:
    """Every task worktree of this project: task, branch, path, head."""
    if _no_repo(project_folder):
        return []
    proc = store.git(project_folder, "worktree", "list", "--porcelain")
    tasks, entry = [], {}
    for line in proc.stdout.splitlines() + [""]:
        if not line:
            ref = entry.get("branch", "")
            if ref.startswith("refs/heads/" + BRANCH_PREFIX):
                branch = ref[len("refs/heads/"):]
                tasks.append({"task": branch[len(BRANCH_PREFIX):],
                              "branch": branch,
                              "path": entry.get("worktree", ""),
                              "head": entry.get("HEAD", "")[:12]})
            entry = {}
            continue
        key, _, value = line.partition(" ")
        entry[key] = value
    return tasks


def _open_task(project_folder: str, task: str) -> dict | None:
    branch = task_branch(task)
    return next((t for t in list_tasks(project_folder)
                 if t["branch"] == branch), None)


def commit(project_folder: str, task: str, message: str) -> dict:
    """Commit the task worktree's allow-listed text (`store.commit_build`)."""
    opened = _open_task(project_folder, task)
    if opened is None:
        return {"committed": False,
                "reason": f"no worktree is open for task {task!r}"}
    return store.commit_build(opened["path"], message)


def merge(project_folder: str, task: str) -> dict:
    """Merge `ren/<task>` into the project checkout's current branch."""
    opened = _open_task(project_folder, task)
    if opened is None:
        return {"merged": False,
                "reason": f"no worktree is open for task {task!r}"}
    pending = store.git(opened["path"], "status", "--porcelain")
    if pending.returncode != 0 or pending.stdout.strip():
        return {"merged": False,
                "reason": f"task {task!r} has uncommitted work in "
                          f"{opened['path']} - `ren worktree commit` it "
                          f"first; a merge carries only what was committed"}
    return variants.merge_variations(project_folder, opened["branch"])


def remove(project_folder: str, task: str,
           discard_unmerged: bool = False) -> dict:
    """Close the task's worktree, and delete its branch once it is merged.

    `git worktree remove` refuses a worktree with uncommitted work and
    `git branch -d` refuses an unmerged branch; both refusals are kept,
    so nothing a task wrote is lost here.
    """
    opened = _open_task(project_folder, task)
    if opened is None:
        return {"removed": False,
                "reason": f"no worktree is open for task {task!r}"}
    proc = store.git(project_folder, "worktree", "remove", opened["path"])
    if proc.returncode != 0:
        return {"removed": False,
                "reason": f"git worktree remove refused: "
                          f"{proc.stderr.strip()[-200:]}"}
    deleted = store.git(project_folder,
                        "branch", "-D" if discard_unmerged else "-d",
                        opened["branch"])
    return {"removed": True, "path": opened["path"],
            "branch": opened["branch"],
            "branch_deleted": deleted.returncode == 0,
            **({} if deleted.returncode == 0 else
               {"branch_kept": "not merged into the project checkout's "
                               "branch - merge it, or delete it by hand"})}


def _tracked_changes(project_folder: str) -> list[str] | None:
    status = store.git(project_folder, "status", "--porcelain")
    if status.returncode != 0:
        return None
    return [line for line in status.stdout.splitlines()
            if line.strip() and not line.startswith("??")]


def _registry_paths(project_folder: str) -> tuple[Path, Path]:
    directory = project_worktrees_dir(project_folder)
    return directory / TASK_REGISTRY, directory / TASK_REGISTRY_LOCK


def _read_registry(path: Path) -> dict:
    if not path.exists():
        return {"format": TASKS_FORMAT, "tasks": {}}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ValueError(f"task registry {path} cannot be read: {exc}") from exc
    if (not isinstance(value, dict) or value.get("format") != TASKS_FORMAT
            or not isinstance(value.get("tasks"), dict)):
        raise ValueError(f"task registry {path} has an unsupported shape")
    for task, record in value["tasks"].items():
        if (not isinstance(record, dict)
                or record.get("task") != task
                or record.get("branch") != task_branch(task)
                or not isinstance(record.get("path"), str)
                or not isinstance(record.get("project"), str)
                or not isinstance(record.get("target_branch"), str)
                or not isinstance(record.get("claims"), list)):
            raise ValueError(
                f"task registry {path} has an invalid entry for {task!r}")
    return value


def _write_registry(path: Path, document: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(
        dir=path.parent, prefix=path.name + ".", suffix=".tmp")
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as out:
            json.dump(document, out, indent=2, sort_keys=True,
                      ensure_ascii=False)
            out.write("\n")
        os.replace(temporary, path)
    except BaseException:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


@contextlib.contextmanager
def _locked_registry(project_folder: str):
    registry_path, lock_path = _registry_paths(project_folder)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with open(lock_path, "a+", encoding="utf-8") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        try:
            yield registry_path, _read_registry(registry_path)
        finally:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


def _claim_path(value: str) -> str:
    claim = str(value).strip().replace("\\", "/")
    if (not claim or claim.startswith("/")
            or any(part in ("", ".", "..")
                   for part in claim.rstrip("/").split("/"))
            or any(char in claim for char in "*?[]")):
        raise ValueError(
            f"invalid task write claim {value!r}; use a project-relative "
            "file or directory path")
    claim = claim.rstrip("/")
    for raw_pattern in store.ALLOW_LIST:
        pattern = raw_pattern.lstrip("/")
        directory = pattern.rstrip("/")
        if (raw_pattern.endswith("/")
                and (claim == directory or claim.startswith(directory + "/"))):
            return claim
        if fnmatch.fnmatchcase(claim, pattern):
            return claim
    raise ValueError(
        f"task write claim {claim!r} is outside the project's "
        "versioned semantic store")


def _claims_for_start(claims: list[str] | None) -> list[str]:
    if not claims:
        return ["*"]
    normalized = sorted({_claim_path(value) for value in claims})
    for index, left in enumerate(normalized):
        for right in normalized[index + 1:]:
            if _claims_overlap(left, right):
                raise ValueError(
                    f"task write claims overlap: {left!r} and {right!r}")
    return normalized


def _claims_overlap(left: str, right: str) -> bool:
    if left == "*" or right == "*":
        return True
    return (left == right
            or left.startswith(right.rstrip("/") + "/")
            or right.startswith(left.rstrip("/") + "/"))


def _claim_covers(path: str, claims: list[str]) -> bool:
    return any(claim == "*" or path == claim
               or path.startswith(claim.rstrip("/") + "/")
               for claim in claims)


def _changes_since(project_folder: str, target: str,
                   source: str) -> list[str]:
    result = store.git(project_folder, "diff", "--name-only",
                       f"{target}...{source}")
    if result.returncode != 0:
        raise ValueError("cannot compare task worktree with project: "
                         f"{result.stderr.strip()[-200:]}")
    return sorted(path for path in result.stdout.splitlines() if path.strip())


def task_start(project_folder: str, task: str,
               claims: list[str] | None = None) -> dict:
    """Start an isolated semantic task and return the checkout its agent uses.

    Write claims are paths in the versioned semantic store. Missing claims
    mean an exclusive claim on the project store; explicit disjoint claims
    can run concurrently because each task has a separate git worktree.
    Tasks end with `task_finish`; Resolve work happens after that merge.
    """
    blocked = _no_repo(project_folder)
    if blocked:
        return {"started": False, "reason": blocked}
    try:
        task_name = task_slug(task)
        task_claims = _claims_for_start(claims)
    except ValueError as invalid:
        return {"started": False, "reason": str(invalid)}

    branch_result = store.git(project_folder, "symbolic-ref", "--quiet",
                              "--short", "HEAD")
    if branch_result.returncode != 0:
        return {"started": False,
                "reason": "the project store is detached; task work needs "
                          "a named target branch"}
    target_branch = branch_result.stdout.strip()
    if target_branch.startswith(BRANCH_PREFIX):
        return {"started": False,
                "reason": "the project checkout is already on a Ren task "
                          "branch; start from the project checkout instead"}
    dirty = _tracked_changes(project_folder)
    if dirty is None:
        return {"started": False,
                "reason": "cannot read project store status"}
    if dirty:
        return {"started": False,
                "reason": "the project store has tracked changes; commit "
                          "them before starting an isolated task: "
                          + "; ".join(dirty[:5])}

    try:
        with _locked_registry(project_folder) as (registry_path, registry):
            records = registry["tasks"]
            for active_slug, active in records.items():
                active_path = active.get("path")
                if not active_path or not Path(active_path).is_dir():
                    return {"started": False,
                            "reason": f"task {active_slug!r} has a missing "
                                      "workspace; finish or recover it first"}
                active_claims = active.get("claims")
                if not isinstance(active_claims, list):
                    return {"started": False,
                            "reason": f"task {active_slug!r} has unreadable "
                                      "write claims; recover it before starting "
                                      "another task"}
                if any(_claims_overlap(wanted, held)
                       for wanted in task_claims
                       for held in active_claims):
                    return {"started": False,
                            "reason": f"task {active_slug!r} is running with "
                                      f"overlapping write claims "
                                      f"{active_claims}; use disjoint "
                                      "project-relative --claim paths or wait "
                                      "for it to finish"}

            recorded = {entry.get("branch") for entry in records.values()}
            unmanaged = [entry for entry in list_tasks(project_folder)
                         if entry["branch"] not in recorded]
            if unmanaged:
                return {"started": False,
                        "reason": "an open Ren worktree has no managed task "
                                  "record; finish or remove it before starting "
                                  f"another task: {unmanaged[0]['branch']}"}

            head = store.git(project_folder, "rev-parse", "HEAD")
            if head.returncode != 0:
                return {"started": False,
                        "reason": f"cannot read project HEAD: "
                                  f"{head.stderr.strip()[-200:]}"}
            opened = add(project_folder, task_name, base=head.stdout.strip())
            if not opened.get("added"):
                return {"started": False, "reason": opened["reason"]}
            record = {
                "task": task_name,
                "branch": opened["branch"],
                "path": opened["path"],
                "project": str(Path(project_folder).resolve()),
                "target_branch": target_branch,
                "base": head.stdout.strip(),
                "claims": task_claims,
                "started_at": dt.datetime.now(dt.timezone.utc).isoformat(),
            }
            records[task_name] = record
            try:
                _write_registry(registry_path, registry)
            except BaseException:
                remove(project_folder, task_name)
                raise
            return {"started": True, **record,
                    "boundary": "Do semantic work only in this workspace. "
                                "Finish merges semantic state; build or "
                                "verify Resolve after finish."}
    except (OSError, ValueError) as failed:
        return {"started": False, "reason": str(failed)}


def task_finish(project_folder: str, task: str) -> dict:
    """Commit and merge a task's semantic work, then remove its checkout."""
    blocked = _no_repo(project_folder)
    if blocked:
        return {"finished": False, "reason": blocked}
    try:
        task_name = task_slug(task)
    except ValueError as invalid:
        return {"finished": False, "reason": str(invalid)}
    with _locked_registry(project_folder) as (registry_path, registry):
        record = registry["tasks"].get(task_name)
        if record is None:
            return {"finished": False,
                    "reason": f"no managed task {task_name!r} is running"}
        if record["project"] != str(Path(project_folder).resolve()):
            return {"finished": False,
                    "reason": "task record belongs to a different project "
                              "folder"}
        current_branch = store.git(project_folder, "symbolic-ref", "--quiet",
                                   "--short", "HEAD")
        if (current_branch.returncode != 0
                or current_branch.stdout.strip() != record["target_branch"]):
            return {"finished": False,
                    "reason": "project checkout moved from the task's target "
                              f"branch {record['target_branch']!r}; return to "
                              "that branch before finishing"}
        dirty = _tracked_changes(project_folder)
        if dirty is None or dirty:
            detail = ("git status could not be read" if dirty is None else
                      "; ".join(dirty[:5]))
            return {"finished": False,
                    "reason": "project store has tracked changes; task merge "
                              "refused to mix them: " + detail}
        opened = _open_task(project_folder, task_name)
        if opened is None or opened["path"] != record["path"]:
            return {"finished": False,
                    "reason": "managed task worktree is missing or moved"}

        committed = commit(project_folder, task_name,
                           f"Ren task {task_name}: semantic work")
        if not committed.get("committed") and committed.get("reason") != "clean":
            return {"finished": False,
                    "reason": f"task commit failed: "
                              f"{committed.get('reason', 'unknown failure')}"}
        try:
            changed = _changes_since(project_folder, record["target_branch"],
                                     record["branch"])
        except ValueError as failed:
            return {"finished": False, "reason": str(failed)}
        semantic_changes = [path for path in changed
                            if not variants._is_generated(path)]
        unclaimed = [path for path in semantic_changes
                     if not _claim_covers(path, record["claims"])]
        if unclaimed:
            return {"finished": False,
                    "reason": "task changed semantic paths outside its "
                              f"write claims: {unclaimed}; revise the task "
                              "worktree and finish again"}

        if semantic_changes:
            merged = variants.merge_variations(
                project_folder, record["branch"], semantic_only=True)
            if not merged.get("merged"):
                return {"finished": False, **merged,
                        "committed": committed}
        else:
            head = store.git(project_folder, "rev-parse", "--short", "HEAD")
            merged = {"merged": True, "target": record["target_branch"],
                      "source": record["branch"], "commit": head.stdout.strip(),
                      "semantic_changes": [],
                      "generated_discarded": changed,
                      "note": "no semantic changes; generated task state "
                              "was not merged"}

        removed = remove(project_folder, task_name,
                         discard_unmerged=not semantic_changes)
        if not removed.get("removed"):
            return {"finished": False, **merged,
                    "committed": committed,
                    "reason": f"semantic merge succeeded but task cleanup "
                              f"failed: {removed.get('reason', '')}"}
        del registry["tasks"][task_name]
        _write_registry(registry_path, registry)
        semantic_merged = list(merged.get("semantic_changes",
                                         semantic_changes))
        return {"finished": True, "task": task_name,
                "committed": committed, "merged": merged,
                "removed": removed,
                "resolve_rebuild_required": bool(semantic_merged),
                "next_action": (
                    "semantic state merged; run and verify the appropriate "
                    "Ren build on the project when its Resolve timeline must "
                    "reflect these changes"
                    if semantic_merged else
                    "no semantic state changed; no Resolve rebuild is due")}


def main(project_folder: str, argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="ren worktree")
    sub = parser.add_subparsers(dest="command", required=True)
    p_add = sub.add_parser("add", help="Open a worktree for a task")
    p_add.add_argument("task")
    p_add.add_argument("--base", help="Commit to fork from (default HEAD)")
    sub.add_parser("list", help="Every open task worktree")
    p_commit = sub.add_parser("commit", help="Commit a task's worktree")
    p_commit.add_argument("task")
    p_commit.add_argument("-m", "--message", required=True)
    for name, text in (("merge", "Merge a task into the project checkout"),
                       ("remove", "Close a task's worktree")):
        sub.add_parser(name, help=text).add_argument("task")
    args = parser.parse_args(argv)
    try:
        if args.command == "add":
            result, ok = add(project_folder, args.task, args.base), "added"
        elif args.command == "list":
            print(json.dumps(list_tasks(project_folder), indent=2))
            return 0
        elif args.command == "commit":
            result = commit(project_folder, args.task, args.message)
            ok = "committed"
        elif args.command == "merge":
            managed = _managed_task(project_folder, args.task)
            result = ({"merged": False,
                       "reason": "managed Ren tasks finish through "
                                 "`ren task <project> finish <task>` so the "
                                 "merge keeps generated state out"}
                      if managed else merge(project_folder, args.task))
            ok = "merged"
        else:
            managed = _managed_task(project_folder, args.task)
            result = ({"removed": False,
                       "reason": "managed Ren tasks finish through "
                                 "`ren task <project> finish <task>` so their "
                                 "write claims and merge are checked"}
                      if managed else remove(project_folder, args.task))
            ok = "removed"
    except ValueError as bad:
        print(f"REFUSED: {bad}", file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2))
    return 0 if result.get(ok) or result.get("reason") == "clean" else 1


def _managed_task(project_folder: str, task: str) -> bool:
    """Whether the automatic task lifecycle owns this worktree."""
    try:
        task_name = task_slug(task)
        with _locked_registry(project_folder) as (_, registry):
            return task_name in registry["tasks"]
    except (OSError, ValueError):
        return True  # an unreadable registry must fail closed


def task_main(project_folder: str, argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="ren task")
    sub = parser.add_subparsers(dest="command", required=True)
    p_start = sub.add_parser(
        "start", help="Allocate an isolated semantic worktree for a task")
    p_start.add_argument("task")
    p_start.add_argument(
        "--claim", action="append", default=[], metavar="PROJECT_PATH",
        help="Versioned semantic path this task may change; repeatable. "
             "Omit to claim the whole project store exclusively")
    p_finish = sub.add_parser(
        "finish", help="Commit and merge semantic work, then remove its worktree")
    p_finish.add_argument("task")
    args = parser.parse_args(argv)
    try:
        if args.command == "start":
            result = task_start(project_folder, args.task, args.claim)
            ok = "started"
        else:
            result = task_finish(project_folder, args.task)
            ok = "finished"
    except (OSError, ValueError) as failed:
        print(f"REFUSED: {failed}", file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2))
    return 0 if result.get(ok) else 1
