"""Resolve a named revision to a tree the reconstructor can import from.

`WORKTREE` means this checkout as it stands, uncommitted edits included -
which is what you want while you are changing a manifest and asking what
it did to a prompt.  Anything else is a git revision, checked out into a
detached worktree under the snapshot store and REUSED, because a
comparison across a dozen steps would otherwise pay for a checkout a dozen
times.

The worktrees live outside the repository on purpose.  A stray untracked
directory inside the checkout blocks a worktree teardown, and the cleanup
guard is right not to guess whether it is scaffolding or unlanded work.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

WORKTREE = "WORKTREE"


def repo_root(start: Path | None = None) -> Path:
    start = start or Path(__file__).resolve()
    r = subprocess.run(["git", "-C", str(start.parent if start.is_file() else start),
                        "rev-parse", "--show-toplevel"],
                       capture_output=True, text=True, encoding="utf-8",
                       check=False)
    if r.returncode != 0:
        raise RuntimeError(f"not inside a git repository: {r.stderr.strip()}")
    return Path(r.stdout.strip())


def resolve_sha(rev: str, root: Path | None = None) -> str:
    root = root or repo_root()
    if rev == WORKTREE:
        return WORKTREE
    r = subprocess.run(["git", "-C", str(root), "rev-parse", rev],
                       capture_output=True, text=True, encoding="utf-8",
                       check=False)
    if r.returncode != 0:
        raise ValueError(f"cannot resolve revision {rev!r}: {r.stderr.strip()}")
    return r.stdout.strip()


def describe(rev: str, root: Path | None = None) -> str:
    """What a reader needs to tell two columns of a diff apart."""
    root = root or repo_root()
    if rev == WORKTREE:
        head = subprocess.run(["git", "-C", str(root), "rev-parse", "--short", "HEAD"],
                              capture_output=True, text=True, encoding="utf-8",
                       check=False)
        dirty = subprocess.run(["git", "-C", str(root), "status", "--porcelain"],
                               capture_output=True, text=True, encoding="utf-8",
                       check=False)
        suffix = "+dirty" if dirty.stdout.strip() else ""
        return f"WORKTREE ({head.stdout.strip()}{suffix})"
    sha = resolve_sha(rev, root)
    subj = subprocess.run(["git", "-C", str(root), "log", "-1", "--format=%s", sha],
                          capture_output=True, text=True, encoding="utf-8",
                       check=False)
    return f"{rev} ({sha[:7]}) {subj.stdout.strip()}"


def tree_for(rev: str, cache_dir: Path, root: Path | None = None) -> Path:
    """The directory to import `library.*` from for this revision."""
    root = root or repo_root()
    if rev == WORKTREE:
        return root
    sha = resolve_sha(rev, root)
    dest = Path(cache_dir) / f"tree-{sha[:12]}"
    if (dest / "library" / "processes" / "edit_video" / "dag.json").is_file():
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        subprocess.run(["git", "-C", str(root), "worktree", "remove", "--force",
                        str(dest)], capture_output=True, text=True, encoding="utf-8",
                       check=False)
    # A cached worktree whose directory was removed underneath git stays
    # REGISTERED, and the next `worktree add` refuses the same path. That
    # happens the moment a snapshot is re-captured over an old one, which
    # is an ordinary thing to do.
    subprocess.run(["git", "-C", str(root), "worktree", "prune"],
                   capture_output=True, text=True, encoding="utf-8",
                       check=False)
    r = subprocess.run(["git", "-C", str(root), "worktree", "add", "--detach",
                        str(dest), sha],
                       capture_output=True, text=True, encoding="utf-8",
                       check=False)
    if r.returncode != 0:
        raise RuntimeError(
            f"could not check out {rev} ({sha[:7]}) at {dest}: {r.stderr.strip()}")
    return dest


def forget(rev: str, cache_dir: Path, root: Path | None = None) -> bool:
    """Drop a cached worktree.  `WORKTREE` is never touched."""
    if rev == WORKTREE:
        return False
    root = root or repo_root()
    dest = Path(cache_dir) / f"tree-{resolve_sha(rev, root)[:12]}"
    if not dest.exists():
        return False
    subprocess.run(["git", "-C", str(root), "worktree", "remove", "--force",
                    str(dest)], capture_output=True, text=True, encoding="utf-8",
                       check=False)
    return not dest.exists()
