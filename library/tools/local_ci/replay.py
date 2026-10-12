"""CI-09: the historical regression replay benchmark.

The plan's fourth metric is nonnegotiable: zero missed historical
regressions before incremental evidence replaces the full-suite gate.
This module is the harness that measures it. Each corpus case (a base
plus a stack of PRs with known outcomes) is replayed under two
policies and the misses are compared:

- (a) the current full-gate policy: every test in the inventory runs
  on the stacked tree. The oracle in production is
  `scripts/full_suite_gate.sh`; the harness never shells it (heavy,
  forbidden beside a pooled gate) - a case's scripted per-tree
  outcomes ARE the recorded gate verdicts for that tree.
- (b) the proposed incremental policy, simulated (CI-08 need not
  exist): the CI-06 hybrid selection over the stack's changed files
  names the tests to consider, valid CI-01/CI-02 cache rows and CI-07
  baseline keys are reused, and everything else - plus every test
  with incomplete dependency info - executes.

The load-bearing property: evidence is keyed by TREE, so a PASS
recorded for one PR's tree never certifies the stack. Reusing
per-PR greens on the stack without re-keying misses exactly the
cross-PR interactions (`queue.resolve_batch`'s "halves pass alone,
whole fails", queue.py:495-503) this benchmark exists to catch.

Corpus sampling: `rank_candidates` orders historical stacks for
inclusion from measured data (`history.test_durations` for cost,
`history.failure_rates` for signal) - known-red batches first, since
a corpus without a red case cannot measure the metric. The shipped
manifest (`replay_corpus.json`) carries the two-PR fixture corpus
from the acceptance check; sampled historical cases (with their
`source` provenance, including at least one red batch / kicked PR)
are appended by the same `build_manifest` validation. Real historical
entries arrive with CI-10's outcome ledger - until then the fixture
corpus proves the mechanics, not the verdict on main.

Trust: `trust_verdict` renders "incremental NOT YET TRUSTWORTHY" by
construction while no shadow runs are recorded (CI-10 has not
landed). The CI-08 promotion bar below is the plan's recommended bar;
the exact bar is the captain's C4 call, which this module states but
does not decide.

Standard library only. No gate runs, no daemon, no `~/.ren-ci/`, no
network, no Resolve. The cases run on recorded outcomes, so the whole
harness is hermetic on any machine.
"""

from __future__ import annotations

import json
import os

SCHEMA_VERSION = 1

DEFAULT_CORPUS_FILENAME = "replay_corpus.json"

# Trees a case's outcomes may record. "base" is main alone,
# "pr:<n>" is main plus that one member, "stack" is main plus the
# whole cluster in order - the tree the ordered merges produce.
BASE_TREE = "base"
STACK_TREE = "stack"

# The bar CI-08 needs, as the plan recommends it. The exact bar
# (zero-miss vs release-gating-only vs measure-first) is the
# captain's C4 call - this text states the recommendation and names
# the open call; it does not decide it.
CI08_PROMOTION_BAR = (
    "CI-08 promotion bar (C4 - the captain's call, stated not decided): "
    "the plan's fourth metric recommends zero missed historical "
    "regressions on the sampled corpus plus first clean shadow runs "
    "(CI-10) before incremental evidence replaces any full-suite gate. "
    "Until the captain picks option (a), (b) or (c), no replay report "
    "promotes the incremental policy."
)


class ReplayRefused(RuntimeError):
    """The corpus cannot run; the benchmark judged nothing."""

    def __init__(self, message: str, result: dict | None = None):
        super().__init__(message)
        self.result = result


def default_corpus_path() -> str:
    """The shipped fixture corpus manifest, beside this module."""
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(here, DEFAULT_CORPUS_FILENAME)


def _member_tree(number: int) -> str:
    return f"pr:{number}"


def _known_trees(case: dict) -> set:
    trees = {BASE_TREE, STACK_TREE}
    for member in case.get("members", []):
        trees.add(_member_tree(member.get("pr")))
    return trees


def build_manifest(cases: list, *, selector: str = "scripted") -> dict:
    """Validate case dicts into a corpus manifest, or refuse with the reason.

    Never raises on a bad case: the refusal names the case and the
    defect so a sampled historical entry that does not fit the schema
    is reported, not silently dropped (a dropped red case would flatter
    the benchmark). Returns the manifest dict on success, stamping
    every case with the corpus selector so the harness replays each
    case under the selection source it was recorded for.
    """
    if selector not in ("scripted", "hybrid"):
        return {"ok": False,
                "reason": (f"manifest refused: unknown selector "
                           f"'{selector}' - 'scripted' pairs with recorded "
                           f"per-tree selections, 'hybrid' with the CI-06 "
                           f"selector")}
    if not isinstance(cases, list) or not cases:
        return {"ok": False,
                "reason": "manifest refused: no cases to replay"}
    seen = set()
    for case in cases:
        if not isinstance(case, dict):
            return {"ok": False,
                    "reason": "manifest refused: a case is not an object"}
        ident = case.get("id", "")
        if not ident or not isinstance(ident, str):
            return {"ok": False,
                    "reason": "manifest refused: a case names no id"}
        if ident in seen:
            return {"ok": False,
                    "reason": f"manifest refused: duplicate case '{ident}'"}
        seen.add(ident)
        members = case.get("members")
        if not isinstance(members, list) or not members:
            return {"ok": False,
                    "reason": f"manifest refused: case '{ident}' "
                              f"stacks no members"}
        numbers = []
        for member in members:
            if not isinstance(member, dict):
                return {"ok": False,
                        "reason": f"manifest refused: case '{ident}' has "
                                  f"a member that is not an object"}
            number = member.get("pr")
            changed = member.get("changed")
            if (not isinstance(number, int) or isinstance(number, bool)
                    or not isinstance(changed, list) or not changed
                    or not all(isinstance(path, str) and path
                               for path in changed)):
                return {"ok": False,
                        "reason": f"manifest refused: case '{ident}' has "
                                  f"a member without a PR number and "
                                  f"changed files"}
            numbers.append(number)
        if len(set(numbers)) != len(numbers):
            return {"ok": False,
                    "reason": f"manifest refused: case '{ident}' repeats "
                              f"a PR number"}
        known = case.get("known")
        if (not isinstance(known, dict)
                or known.get("verdict") not in ("PASS", "FAIL")
                or not isinstance(known.get("source"), str)
                or not known.get("source")):
            return {"ok": False,
                    "reason": f"manifest refused: case '{ident}' records "
                              f"no known verdict with its source"}
        outcomes = case.get("outcomes")
        if not isinstance(outcomes, dict) or not outcomes:
            return {"ok": False,
                    "reason": f"manifest refused: case '{ident}' records "
                              f"no test outcomes"}
        trees = _known_trees(case)
        for test, per_tree in outcomes.items():
            if not isinstance(per_tree, dict):
                return {"ok": False,
                        "reason": f"manifest refused: case '{ident}' "
                                  f"outcome for '{test}' is not per-tree"}
            for tree, passed in per_tree.items():
                if tree not in trees:
                    return {"ok": False,
                            "reason": f"manifest refused: case '{ident}' "
                                      f"outcome for '{test}' names unknown "
                                      f"tree '{tree}'"}
                if not isinstance(passed, bool):
                    return {"ok": False,
                            "reason": f"manifest refused: case '{ident}' "
                                      f"outcome for '{test}' on '{tree}' "
                                      f"is not pass/fail"}
        if selector == "scripted":
            selection = case.get("selection")
            if not isinstance(selection, dict):
                return {"ok": False,
                        "reason": f"manifest refused: case '{ident}' "
                                  f"names no recorded selection for a "
                                  f"scripted corpus"}
            for tree in trees:
                entry = selection.get(tree)
                if (not isinstance(entry, dict)
                        or not isinstance(entry.get("selected"), list)):
                    return {"ok": False,
                            "reason": f"manifest refused: case '{ident}' "
                                      f"selection names no selected tests "
                                      f"for tree '{tree}'"}
    stamped = []
    for case in cases:
        entry = dict(case)
        entry["_selector"] = selector
        stamped.append(entry)
    return {"ok": True,
            "manifest": {"schema_version": SCHEMA_VERSION,
                         "selector": selector,
                         "cases": stamped}}


def load_manifest(path: str = "") -> dict:
    """Read and validate the corpus manifest at `path`.

    Raises ReplayRefused (never returns a partial corpus): a
    benchmark that runs on half a corpus reports half the misses.
    """
    target = path or default_corpus_path()
    try:
        with open(target, encoding="utf-8") as handle:
            payload = json.load(handle)
    except OSError as exc:
        raise ReplayRefused(
            f"replay corpus missing: {target} ({exc})") from exc
    except ValueError as exc:
        raise ReplayRefused(
            f"replay corpus is not JSON: {target} ({exc})") from exc
    if not isinstance(payload, dict):
        raise ReplayRefused(f"replay corpus is not an object: {target}")
    if payload.get("schema_version") != SCHEMA_VERSION:
        raise ReplayRefused(
            f"replay corpus schema {payload.get('schema_version')} "
            f"is not {SCHEMA_VERSION}: {target}")
    checked = build_manifest(payload.get("cases", []),
                             selector=payload.get("selector", "scripted"))
    if not checked.get("ok"):
        raise ReplayRefused(f"{checked.get('reason')}: {target}")
    manifest = checked["manifest"]
    manifest["path"] = target
    return manifest


def tree_key(case: dict, tree: str) -> str:
    """The evidence key for one tree: content-derived, never reused across.

    The stack key folds in every member head, so a PASS recorded for
    one PR's tree never matches the stack lookup. Per-PR greens that
    certified the stack would hide cross-PR interactions - the key
    makes that reuse a miss by construction, not by discipline.
    """
    base = case.get("base", "")
    if tree == BASE_TREE:
        return f"base:{base}"
    if tree == STACK_TREE:
        heads = "+".join(str(member.get("head", ""))
                         for member in case.get("members", []))
        return f"stack:{base}+{heads}"
    for member in case.get("members", []):
        if _member_tree(member.get("pr")) == tree:
            return f"{tree}:{base}+{member.get('head', '')}"
    return f"{tree}:{base}"


def _stack_changed(case: dict) -> list:
    changed = []
    for member in case.get("members", []):
        for path in member.get("changed", []):
            if path not in changed:
                changed.append(path)
    return changed


def _hybrid_select(changed: list) -> list:
    """The CI-06 hybrid selection for changed files, static on failure.

    Loads `scripts/select_dependent_tests.py` by path (scripts/ is not
    a package the harness can import from every directory - the same
    reason `history` loads its telemetry reader by path). Anything
    unreadable selects nothing here: the harness reports the miss
    rather than widening silently, so a broken selector can never
    flatter the benchmark.
    """
    import importlib.util

    here = os.path.dirname(os.path.abspath(__file__))
    repo = os.path.dirname(os.path.dirname(os.path.dirname(here)))
    path = os.path.join(repo, "scripts", "select_dependent_tests.py")
    try:
        spec = importlib.util.spec_from_file_location(
            "ren_replay_selector", path)
        if spec is None or spec.loader is None:
            return []
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        selected = module.select(list(changed), use_coverage=True)
    except (OSError, ImportError, SyntaxError, AttributeError,
            TypeError, ValueError):
        return []
    return [entry for entry in selected
            if isinstance(entry, str) and entry]


def default_selection(case: dict, tree: str) -> tuple:
    """The (selected, incomplete) tests for one tree under the manifest's selector.

    Scripted corpora replay the recorded per-tree selection (the
    fixture path - deterministic on any machine). Hybrid corpora
    (sampled history) consult the real CI-06 selector; nothing is ever
    "incomplete" there because CI-06's own contract falls back to the
    wide static selection where its index has no signal. Returns
    (selected set, incomplete set) - incomplete tests always execute.
    """
    if case.get("_selector", "scripted") == "hybrid":
        if tree == BASE_TREE:
            return set(), set()
        if tree == STACK_TREE:
            return set(_hybrid_select(_stack_changed(case))), set()
        for member in case.get("members", []):
            if _member_tree(member.get("pr")) == tree:
                return set(_hybrid_select(member.get("changed", []))), set()
        return set(), set()
    entry = (case.get("selection") or {}).get(tree, {})
    selected = {path for path in entry.get("selected", [])
                if isinstance(path, str)}
    incomplete = {path for path in entry.get("incomplete", [])
                  if isinstance(path, str)}
    return selected, incomplete


def full_gate_verdict(case: dict) -> dict:
    """Policy (a): the full gate on the stacked tree.

    Every test in the recorded inventory executes; red iff any fails
    on the stack. In production the oracle is `scripts/full_suite_gate.sh`
    on the real stacked checkout - here the case's recorded outcomes
    are those verdicts, which is what keeps the harness hermetic.
    """
    outcomes = case.get("outcomes", {})
    failed = sorted(test for test, per_tree in outcomes.items()
                    if not per_tree.get(STACK_TREE, True))
    return {"ok": not failed, "executed": sorted(outcomes),
            "reused": [], "failed": failed}


def incremental_verdict(case: dict, *, selection_fn=None,
                        evidence=None) -> dict:
    """Policy (b): the incremental simulation on the stacked tree.

    The CI-06 selection over the stack's changed files names the tests
    to consider; tests with valid evidence for the STACK key are
    reused (the CI-01/CI-02 cache + CI-07 baseline half); everything
    else, plus every incomplete-info test, executes with its recorded
    stack outcome. `evidence` is a set of (test, tree_key) pairs with a
    valid PASS - per-PR rows never match the stack key, so per-PR
    greens are re-executed on the stack rather than trusted there.
    """
    select = selection_fn or default_selection
    selected, incomplete = select(case, STACK_TREE)
    selected = set(selected or [])
    incomplete = set(incomplete or [])
    key = tree_key(case, STACK_TREE)
    have = set(evidence or set())
    executed = sorted((selected | incomplete)
                      - {test for test in selected if (test, key) in have})
    outcomes = case.get("outcomes", {})
    failed = sorted(test for test in executed
                    if not outcomes.get(test, {}).get(STACK_TREE, True))
    reused = sorted(test for test in selected
                    if (test, key) in have and test not in executed)
    return {"ok": not failed, "executed": executed,
            "reused": reused, "failed": failed}


def replay_case(case: dict, *, selection_fn=None,
                evidence=None) -> dict:
    """Replay one case under both policies; a miss is policy-green on known-red.

    A full-gate miss means the case's own outcomes contradict its
    recorded known verdict (a bad case, reported not hidden). An
    incremental miss is the metric: the simulation certified a stack
    the gate calls red.
    """
    full = full_gate_verdict(case)
    incremental = incremental_verdict(case, selection_fn=selection_fn,
                                      evidence=evidence)
    known_red = case.get("known", {}).get("verdict") == "FAIL"
    return {"id": case.get("id", ""),
            "known": case.get("known", {}).get("verdict", ""),
            "full": full, "incremental": incremental,
            "missed_full": known_red and full["ok"],
            "missed_incremental": known_red and incremental["ok"]}


def replay_corpus(manifest: dict, *, selection_fn=None,
                  evidence=None) -> dict:
    """Replay every case; totals are missed regressions per policy."""
    results = [replay_case(case, selection_fn=selection_fn,
                           evidence=evidence)
               for case in manifest.get("cases", [])]
    return {"cases": results,
            "total": len(results),
            "missed_full": sum(1 for item in results
                               if item["missed_full"]),
            "missed_incremental": sum(1 for item in results
                                      if item["missed_incremental"])}


def trust_verdict(missed_incremental: int,
                  shadow_clean_runs: int = 0) -> str:
    """What the benchmark says about promoting the incremental policy.

    With no shadow runs recorded the verdict is NOT YET TRUSTWORTHY
    by construction (CI-10 has not landed - there is no data the
    promotion could rest on). With shadow data the report states where
    the replay stands against the recommended bar; promotion itself
    stays the captain's C4 call.
    """
    if shadow_clean_runs <= 0:
        return ("trust: incremental NOT YET TRUSTWORTHY - no shadow "
                "validation recorded (CI-10 has not landed), so the "
                "benchmark cannot promote the incremental policy yet")
    if missed_incremental:
        return ("trust: incremental BELOW THE RECOMMENDED BAR - "
                f"{missed_incremental} replayed regression(s) missed; "
                "promotion waits on the C4 call")
    return ("trust: incremental meets the recommended replay bar on "
            "recorded data; promotion still needs the captain's C4 call")


def render_report(replay: dict, *, shadow_clean_runs: int = 0) -> str:
    """The benchmark report: per-case verdicts, misses per policy, the bar."""
    lines = [f"replay benchmark: {replay.get('total', 0)} case(s)"]
    for item in replay.get("cases", []):
        full = "PASS" if item["full"]["ok"] else "FAIL"
        incremental = ("PASS" if item["incremental"]["ok"] else "FAIL")
        if item["missed_full"] and item["missed_incremental"]:
            missed = "full+incremental"
        elif item["missed_full"]:
            missed = "full"
        elif item["missed_incremental"]:
            missed = "incremental"
        else:
            missed = "none"
        lines.append(f"[{item['id']}] known={item['known']} "
                     f"full={full} incremental={incremental} "
                     f"missed={missed}")
    lines.append(f"missed regressions: full-gate "
                 f"{replay.get('missed_full', 0)}/{replay.get('total', 0)}, "
                 f"incremental "
                 f"{replay.get('missed_incremental', 0)}/"
                 f"{replay.get('total', 0)}")
    lines.append(CI08_PROMOTION_BAR)
    lines.append(trust_verdict(replay.get("missed_incremental", 0),
                               shadow_clean_runs=shadow_clean_runs))
    return "\n".join(lines) + "\n"


def rank_candidates(candidates: list, durations: dict | None = None,
                    failure_rates: dict | None = None) -> list:
    """Order historical stacks for corpus sampling, measured data first.

    Known-red batches come first: a corpus without a red case cannot
    measure the nonnegotiable metric. Within an outcome band, higher
    failure-rate signal (over the stack's tests, from
    `history.failure_rates`) outranks lower; ties break toward cheaper
    expected cost (summed `history.test_durations`), with unmeasured
    stacks last and the PR number final - the order is deterministic
    for the same inputs. Pure function of explicit inputs, so the
    sampling a report claims is the sampling anyone can recompute.
    """
    durations = durations or {}
    failure_rates = failure_rates or {}

    def cost(candidate: dict) -> tuple:
        known = 0.0
        unmeasured = False
        for test in candidate.get("tests", []):
            try:
                seconds = float(durations.get(test, float("nan")))
            except (TypeError, ValueError):
                seconds = float("nan")
            if seconds == seconds and seconds > 0:
                known += seconds
            else:
                unmeasured = True
        return (unmeasured, known)

    def signal(candidate: dict) -> float:
        best = 0.0
        for test in candidate.get("tests", []):
            try:
                rate = float(failure_rates.get(test, 0.0))
            except (TypeError, ValueError):
                continue
            if 0.0 <= rate <= 1.0 and rate > best:
                best = rate
        return best

    def rank(candidate: dict) -> tuple:
        red = 0 if candidate.get("known") == "FAIL" else 1
        unmeasured, known = cost(candidate)
        number = candidate.get("pr", 0)
        try:
            number = int(number)
        except (TypeError, ValueError):
            number = 0
        return (red, -signal(candidate), unmeasured, known, number)

    return sorted(candidates, key=rank)
