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

# Tier-1 is the quick check, not the verdict: the dependent half is
# bounded so one hub-module PR cannot bill half the suite per push.
# Above this many dependent files the run keeps only the direct tests
# (same-stem test files plus changed test files) and leaves breadth to
# the Tier-2 batched full suite, which already runs everything on the
# exact stacked tree before any merge. 40 files is roughly 800-1,200
# tests (the October floor was 320 always-run tests; uncapped hub
# selections reached 3,500-5,000) - small enough to stay quick under
# agent load, large enough that ordinary PRs never notice the cap.
TIER1_DEPENDENT_BUDGET_FILES = 40

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


def direct_tests_for(paths: list, repo: str = REPO_ROOT) -> list:
    """The uncapped floor of a selection: changed test files themselves
    plus each changed module's same-stem test file, when the checkout
    has it. No import-graph fan-out, so a hub module stays at a handful
    of files no matter how widely it is imported."""
    import os
    repo = os.path.abspath(repo)
    direct = set()
    for path in paths:
        abs_path = (path if os.path.isabs(path)
                    else os.path.join(repo, path))
        absolute = os.path.abspath(abs_path)
        if (absolute.endswith(".py") and os.path.isfile(absolute)
                and os.path.commonpath(
                    [absolute, os.path.join(repo, "tests")])
                == os.path.join(repo, "tests")
                and os.path.basename(absolute).startswith("test_")):
            direct.add(os.path.relpath(absolute, repo))
            continue
        stem = os.path.splitext(os.path.basename(abs_path))[0]
        candidate = os.path.join(repo, "tests")
        found = ""
        for dirpath, _dirnames, filenames in os.walk(candidate):
            if f"test_{stem}.py" in filenames:
                found = os.path.relpath(
                    os.path.join(dirpath, f"test_{stem}.py"), repo)
                break
        if found:
            direct.add(found)
    return sorted(direct)


def capped_selection_for(paths: list, repo: str = REPO_ROOT) -> dict:
    """The Tier-1 selection with a bounded cost.

    Returns {paths, capped, dependent_total, dependent_kept, budget}:
    paths is dependent (capped to direct when over budget) plus the
    always-run set. capped is True exactly when the dependent half was
    cut; the daemon records it on the Tier-1 status so a capped run
    reads as capped, never as full coverage.
    """
    full_dependent = dependent(paths, repo)
    budget = TIER1_DEPENDENT_BUDGET_FILES
    if len(full_dependent) <= budget:
        selected = list(full_dependent)
        capped = False
        kept = list(full_dependent)
    else:
        kept = direct_tests_for(paths, repo)
        selected = list(kept)
        capped = True
    for entry in config.ALWAYS_RUN:
        if entry not in selected:
            selected.append(entry)
    return {"paths": selected, "capped": capped,
            "dependent_total": len(full_dependent),
            "dependent_kept": len(kept), "budget": budget}


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
