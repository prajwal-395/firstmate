"""TASK WORKTREES: one project store, many tasks each holding its own
semantic state, merged back. Part of the version model
(`library/tools/versions/__init__.py`); AGENTS.md 3: run state.

Reached through `ren worktree add|list|commit|merge|remove`.

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
`merge` is `variants.merge_variations` into the project checkout's
current branch: generated run state resolves to the target side and is
rebuilt, declaration conflicts are left for a human, and a clean merge
is still not a green build (issue #925). The worktree must be committed
first (`commit`, which is `store.commit_build` on the worktree), and the
project checkout's tracked tree must be clean, so a merge never mixes
with work nobody committed.

`tests/unit/context/test_project_layout.py`.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from pathlib import Path

from library.tools.versions import store, variants

ROOT_ENV = "REN_WORKTREES"
BRANCH_PREFIX = "ren/"


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
    root = Path(project_folder).resolve()
    key = hashlib.sha1(str(root).encode("utf-8")).hexdigest()[:8]
    return worktrees_root() / f"{root.name}-{key}" / task_slug(task)


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


def remove(project_folder: str, task: str) -> dict:
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
    deleted = store.git(project_folder, "branch", "-d", opened["branch"])
    return {"removed": True, "path": opened["path"],
            "branch": opened["branch"],
            "branch_deleted": deleted.returncode == 0,
            **({} if deleted.returncode == 0 else
               {"branch_kept": "not merged into the project checkout's "
                               "branch - merge it, or delete it by hand"})}


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
            result, ok = merge(project_folder, args.task), "merged"
        else:
            result, ok = remove(project_folder, args.task), "removed"
    except ValueError as bad:
        print(f"REFUSED: {bad}", file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2))
    return 0 if result.get(ok) or result.get("reason") == "clean" else 1
