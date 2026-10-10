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

from library.tools.local_ci import config, history

# Tier-1 is the quick check, not the verdict: the dependent half is
# bounded by TEST TIME (config.TIER1_TIME_BUDGET_SECONDS, filled from
# historical per-test durations) so one hub-module PR cannot bill half
# the suite per push. Above the budget the run keeps the
# highest-priority tests - direct tests of the changed files first,
# then dependents by import distance, then by historical failure rate -
# and leaves breadth to the Tier-2 batched full suite, which already
# runs everything on the exact stacked tree before any merge.

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


def dependent_distances(paths: list, repo: str = REPO_ROOT) -> dict:
    """The computed selection mapped to import distance (0 direct).

    Same index as `dependent`, one subprocess: 0 is the changed test
    itself, 1 a direct importer or same-stem test, 2 a hop through a
    re-exporter or a contract-set entry. Empty for an empty diff.
    """
    if not paths:
        return {}
    result = subprocess.run(
        ["python3", SELECT_SCRIPT, "--distances", *paths],
        capture_output=True, encoding="utf-8", check=False, timeout=300,
        cwd=repo, env={**os.environ, "REN_SELECT_ROOT": repo})
    if result.returncode != 0:
        raise RuntimeError("select_dependent_tests failed: "
                           f"{result.stderr.strip()}")
    distances = {}
    for line in result.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        hops, _, path = line.partition(" ")
        path = path.strip()
        if not path:
            continue
        try:
            distances[path] = int(hops)
        except ValueError:
            continue
    return distances


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


def capped_selection_for(paths: list, repo: str = REPO_ROOT,
                           budget_seconds: float | None = None,
                           durations: dict | None = None,
                           failure_rates: dict | None = None) -> dict:
    """The Tier-1 selection with a bounded cost, in seconds of test time.

    Fills the budget by priority: direct tests of the changed files
    first, then dependents ordered by import distance, then by
    historical failure rate (flaky-before-quiet when distances tie).
    Costs come from historical per-test durations; files with no
    measurement cost the unknown-file estimate, never zero. The
    always-run cross-cutting set stays mandatory OUTSIDE the budget:
    no filename predicts those failures, so the budget must not eat
    them.

    Returns {paths, capped, dependent_total, dependent_kept, budget,
    budget_seconds, estimated_seconds, deferred, deferred_paths}:
    capped is True exactly when the dependent half was cut; the
    daemon records it on the Tier-1 status so a budgeted run reads
    as partial, never as full coverage. `budget` carries the seconds
    for readers of the older file-count shape.
    """
    budget = (float(budget_seconds) if budget_seconds is not None
              else config.tier1_time_budget_seconds())
    distances = dependent_distances(paths, repo)
    full_dependent = sorted(distances)
    if durations is None:
        durations = history.test_durations()
    if failure_rates is None:
        failure_rates = history.failure_rates()
    unknown = float(config.TIER1_UNKNOWN_FILE_ESTIMATE_SECONDS)

    def cost(path: str) -> float:
        try:
            estimate = float(durations.get(path, unknown))
        except (TypeError, ValueError):
            estimate = unknown
        return estimate if estimate >= 0 else unknown

    direct = set(direct_tests_for(paths, repo))

    def rate(path: str) -> float:
        try:
            value = float((failure_rates or {}).get(path, 0.0) or 0.0)
        except (TypeError, ValueError):
            return 0.0
        return value if 0.0 <= value <= 1.0 else 0.0

    def priority(path: str) -> tuple:
        return (0 if path in direct else 1,
                int(distances.get(path, 3)),
                -rate(path),
                cost(path), path)

    kept: list = []
    deferred: list = []
    spent = 0.0
    for path in sorted(full_dependent, key=priority):
        estimate = cost(path)
        if path in direct:
            # The floor: a change always answers for its own tests,
            # even when they alone exceed the budget.
            kept.append(path)
            spent += estimate
        elif spent + estimate <= budget:
            kept.append(path)
            spent += estimate
        else:
            deferred.append(path)
    # Direct tests the graph did not select are still the floor (a
    # changed test with no importers selects nothing, but must run).
    for path in sorted(direct):
        if path not in kept and path not in deferred:
            kept.append(path)
            spent += cost(path)
    capped = bool(deferred)
    selected = list(kept)
    for entry in config.ALWAYS_RUN:
        if entry not in selected:
            selected.append(entry)
    return {"paths": selected, "capped": capped,
            "dependent_total": len(full_dependent),
            "dependent_kept": len(kept), "budget": budget,
            "budget_seconds": budget,
            "estimated_seconds": round(spent, 3),
            "deferred": len(deferred),
            "deferred_paths": deferred}


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
