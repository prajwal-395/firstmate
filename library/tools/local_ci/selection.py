"""What a PR's check runs: its dependent selection plus the always-run set.

The dependent half is COMPUTED, not judged
(`scripts/select_dependent_tests.py`: backward what the change uses,
forward what uses the change). The always-run half is the cross-cutting
contract suite no filename predicts (Firstmate spec 1). A PR whose diff
touches nothing testable still runs the always-run half: "no selection"
is never "no check".
"""

from __future__ import annotations

import os
import subprocess

from library.tools.local_ci import config

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))))
SELECT_SCRIPT = os.path.join(REPO_ROOT, "scripts", "select_dependent_tests.py")


def changed_files(repo: str, base: str, head: str) -> list:
    """Paths changed between base and head, repo-relative.

    Three-dot symmetric difference would flatter a stale branch (it
    hides main's movement); two-dot names what the PR WOULD land, which
    is what the check judges.
    """
    result = subprocess.run(
        ["git", "-C", repo, "diff", "--name-only", f"{base}..{head}"],
        capture_output=True, encoding="utf-8", check=False, timeout=60)
    if result.returncode != 0:
        raise RuntimeError(f"git diff {base}..{head} failed: "
                           f"{result.stderr.strip()}")
    return sorted(line.strip() for line in result.stdout.splitlines()
                  if line.strip())


def dependent(paths: list, repo: str = REPO_ROOT) -> list:
    """The computed selection for changed paths, repo-relative.

    Indexed against the tree under test (REN_SELECT_ROOT): a PR that
    predates a test file must never select a path its own checkout
    does not have.
    """
    if not paths:
        return []
    result = subprocess.run(
        ["python3", SELECT_SCRIPT, *paths],
        capture_output=True, encoding="utf-8", check=False, timeout=300,
        cwd=repo, env={**os.environ, "REN_SELECT_ROOT": repo})
    if result.returncode != 0:
        raise RuntimeError("select_dependent_tests failed: "
                           f"{result.stderr.strip()}")
    return sorted(line.strip() for line in result.stdout.splitlines()
                  if line.strip())


def selection_for(paths: list, repo: str = REPO_ROOT) -> list:
    """The PR check's full selection: dependent plus always-run, deduped.

    Always-run entries are kept even when the file is absent from this
    checkout (a PR that DELETES a cross-cutting test still answers for
    the suite that remains): the runner, not the selection, decides what
    exists on disk.
    """
    selected = list(dependent(paths, repo))
    for entry in config.ALWAYS_RUN:
        if entry not in selected:
            selected.append(entry)
    return selected


def selection_command(paths: list) -> str:
    """The report line's `<command and paths>`: what to paste to re-run it."""
    return "python3 scripts/select_dependent_tests.py " + " ".join(paths)
