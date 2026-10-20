"""Refuse cleanup of git-tracked files and foreign git work trees.

Defect named: on 2026-10-19 a stale-media sweep removed 84 git-TRACKED
PNGs, fixtures and templates from the full-suite pool's git clone and
failed ~22 suite tests (restored with `git checkout -- .`). Ren's own
cleanup lanes (workspace_gc, storage_lifecycle, artifact_lifecycle,
cache_lifecycle, retention, asset_gc, caption_asset_gc) delete or
quarantine files by their own rules and could do the same in a working
copy: nothing in any lane proved a candidate was not git-tracked or
outside a foreign work tree.

The one shared guard every deletion and quarantine path routes through:

- refuse any path git tracks (`git ls-files`), resolving symlinks first,
  so a symlink into a repo is judged as its target;
- refuse any path inside a git work tree Ren does not own. Ren-owned
  work trees are the ones its lifecycle records name: the caller passes
  them as `owned_roots` (a work tree whose top equals an owned root is
  owned; a nested clone inside an owned directory is not);
- the refusal is a structured reason (`Refusal`), recorded in the
  plan/receipt by the caller, never a silent skip.

A tracked check inside an owned work tree still refuses: ownership
exempts only the work-tree rule, never the tracked rule.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

GIT_TRACKED = "git-tracked"
FOREIGN_WORK_TREE = "foreign-work-tree"

_TIMEOUT_SECONDS = 10


@dataclass(frozen=True)
class Refusal:
    """Why the guard refused one path. Recorded, never silently skipped."""

    path: str
    resolved_path: str
    reason: str
    work_tree: str
    detail: str


def resolve_physical(path: str | Path) -> Path:
    """The path with every symlink resolved, before any git question."""
    return Path(path).expanduser().resolve(strict=False)


def find_work_tree(resolved: Path) -> Path | None:
    """The git work-tree top containing `resolved`, or None outside git."""
    candidate = resolved if resolved.is_dir() else resolved.parent
    while not candidate.exists():
        parent = candidate.parent
        if parent == candidate:
            return None
        candidate = parent
    try:
        proc = subprocess.run(
            ["git", "-C", str(candidate), "rev-parse", "--show-toplevel"],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            encoding="utf-8", timeout=_TIMEOUT_SECONDS, check=False)
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    top = proc.stdout.strip()
    if not top:
        return None
    return Path(top).resolve(strict=False)


def is_tracked(resolved: Path, work_tree: Path) -> bool:
    """Whether git tracks `resolved` (or, for a directory, anything under it)."""
    try:
        relative = resolved.relative_to(work_tree)
    except ValueError:
        return False
    spec = "." if str(relative) == "." else relative.as_posix()
    try:
        if resolved.is_dir():
            proc = subprocess.run(
                ["git", "-C", str(work_tree), "ls-files", "--full-name",
                 "--", spec],
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                encoding="utf-8", timeout=_TIMEOUT_SECONDS, check=False)
            return proc.returncode == 0 and bool(proc.stdout.strip())
        proc = subprocess.run(
            ["git", "-C", str(work_tree), "ls-files", "--error-unmatch",
             "--", spec],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            encoding="utf-8", timeout=_TIMEOUT_SECONDS, check=False)
        return proc.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def _owned_tops(owned_roots, owned_work_trees) -> set[str]:
    tops = set()
    for root in (*owned_roots, *owned_work_trees):
        if root is None or root == "":
            continue
        tops.add(str(resolve_physical(root)))
    return tops


def check(path: str | Path, *, owned_roots=(),
         owned_work_trees=()) -> Refusal | None:
    """Guard one path. None means collectable; a Refusal names why not."""
    resolved = resolve_physical(path)
    work_tree = find_work_tree(resolved)
    if work_tree is None:
        return None
    work_tree_str = str(work_tree)
    if is_tracked(resolved, work_tree):
        return Refusal(
            path=str(path), resolved_path=str(resolved),
            reason=GIT_TRACKED, work_tree=work_tree_str,
            detail=f"{resolved} is tracked by git in work tree {work_tree}")
    if work_tree_str not in _owned_tops(owned_roots, owned_work_trees):
        return Refusal(
            path=str(path), resolved_path=str(resolved),
            reason=FOREIGN_WORK_TREE, work_tree=work_tree_str,
            detail=f"{resolved} is inside git work tree {work_tree}, "
            "which no lifecycle record names as Ren-owned")
    return None


def describe(refusal: Refusal) -> str:
    """The structured reason, in the sentences a plan or receipt records."""
    if refusal.reason == GIT_TRACKED:
        return (f"{refusal.path} is git-tracked "
                f"(work tree {refusal.work_tree}); refusing")
    return (f"{refusal.path} is inside foreign git work tree "
            f"{refusal.work_tree}; refusing")


def partition(paths, *, owned_roots=(),
              owned_work_trees=()) -> tuple[list, list[Refusal]]:
    """Split paths into (collectable, refused), preserving input order."""
    allowed: list = []
    refused: list[Refusal] = []
    for path in paths:
        hit = check(path, owned_roots=owned_roots,
                    owned_work_trees=owned_work_trees)
        if hit is None:
            allowed.append(path)
        else:
            refused.append(hit)
    return allowed, refused
