"""The Tier-2 merge queue: timeboxed clusters, one full-gate pass, ordered merges.

Tier-1 (per-PR dependent selections, parallel, immediate) is the fast
signal and the GATE into this queue. Nothing here merges without a
FULL-suite pass on the exact tree that lands - test security is not
given up for speed:

- CLUSTER: ready PRs gather within an adaptive window (load.targets:
  short on a quiet machine, long on a busy one). A ripe cluster
  (window elapsed since the oldest ready entry arrived) tests
  together; an unripe one waits. A ripe cluster takes EVERY queued
  entry: the full gate is one process whatever the cluster size.
- TEST: the cluster stacks onto current main (main + members in queue
  order) in a disposable checkout and runs the FULL suite once on that
  stacked tree - the exact tree the ordered merges will produce.
- BISECT ON RED: a red stack first judges MAIN ALONE (reusing a
  cached green baseline for the current main SHA, else the same full
  gate on main). A red main kicks NOBODY - members stay queued with
  the reason while the operator is woken through the daemon log and
  status. Only a green main bisects: halves split until the culprit
  is named and kicked with the reason; the rest merge. Halves that
  pass ALONE while the whole fails are an INTERACTION (the "green
  alone, red together" main-breaker): the first half merges in order
  and the second re-tests on the new main.
- NARROWED / DID NOT RUN is not red: the machine was incomplete, not
  the PR. The cluster requeues with the reason - re-test, never kick,
  never merge.
- MERGE: in queue order, each with the head-SHA pin after a freshness
  re-check that chains on the previous merge's landed SHA. Stale goes
  back to queued, not to merged.
- KICK: only the culprit, with the reason posted as its commit status
  and its ready label removed so it cannot re-enter silently.

The 1924 PATTERN (one evaluation pass, ordered merges, freshness before
merge, kick only the culprit) re-homed to git branches judged by test
suites - see library/tools/local_ci/__init__.py for why the variant
queue's CODE could not be reused.

`World` is the seam: real git/GitHub in the daemon, scripted verdicts
in tests. The queue logic never touches the network directly, so the
defect case (green alone, red together, culprit kicked, rest merged) is
a unit test, not an integration prayer.
"""

from __future__ import annotations

import os
import subprocess
import time

from library.tools.local_ci import config, history, runner, state


class StaleHead(RuntimeError):
    """The head or the base moved under a tested cluster: re-test, never merge."""


class TestResult:
    """One stack evaluation: the verdict plus what to say about it.

    requeue=True means "the machine could not judge" (narrowed
    environment, crashed harness): the cluster waits for a better
    moment. It is neither green (merge) nor red (bisect/kick).

    failed_tests names the test FILES with any failure/error in the
    run's JUnit report (CI-12's "which tests failed" read, wired from
    `runner.failed_tests_from_junit`). Empty on green, on requeue, and
    whenever no report exists - an empty list falls back to the legacy
    full-gate bisect, never to an empty blame set.
    """

    def __init__(self, ok: bool, passed: int = 0, failed: int = 0,
                 reason: str = "", log: str = "", requeue: bool = False,
                 stack_conflict: str = "",
                 failed_tests: list | tuple = ()) -> None:
        self.ok = ok
        self.passed = passed
        self.failed = failed
        self.reason = reason
        self.log = log
        self.requeue = requeue
        # The full head SHA that would not merge onto the accumulated
        # stack ("" when the stack built). A stacking conflict is
        # decided WITHOUT a gate run: resolve_batch kicks only this
        # member and tests the rest, so no full-suite run is ever spent
        # bisecting a conflict.
        self.stack_conflict = stack_conflict
        self.failed_tests = sorted(failed_tests or [])


class World:
    """Effect seam: git, pytest, GitHub. The daemon subclasses nothing -
    it uses this directly; tests override what they script."""

    def __init__(self, mirror: str = "", state_dir: str = "") -> None:
        self.mirror = mirror
        # Whether the caller named a state dir: the main-baseline FILE
        # cache only runs with an explicit dir, so scripted unit worlds
        # (state_dir="") stay hermetic on the in-memory cache instead of
        # reaching the machine's real ~/.ren-ci ledger.
        self._state_explicit = bool(state_dir)
        self.state_dir = state_dir or config.state_dir()
        # In-memory green main-baseline verdicts, keyed by main SHA.
        # The file ledger (state.main_baseline_*) is the durable layer;
        # this skips repeat judgments within one process without disk.
        self._main_baseline: dict = {}

    # -- reads --
    def main_sha(self) -> str:
        from library.tools.local_ci import github, hygiene
        mirror = self.mirror or hygiene.ensure_mirror(
            f"https://github.com/{config.OWNER}/{config.REPO}.git",
            self.state_dir)
        self.mirror = mirror
        return github.main_sha()

    def pr_head(self, pr: int) -> str:
        from library.tools.local_ci import github, hygiene
        mirror = self.mirror or hygiene.ensure_mirror(
            f"https://github.com/{config.OWNER}/{config.REPO}.git",
            self.state_dir)
        self.mirror = mirror
        hygiene.fetch_pr(mirror, pr)
        return github.get_pr(pr)["head"]

    def pr_files(self, pr: int) -> list:
        """Repo-relative paths the PR touches, or [] when unreadable.

        The grouping advice (CI-11) reads this; an unreadable diff is
        unknown overlap (grouped with eyes open), never a refusal, so
        callers treat [] as "no files seen", not "empty diff".
        """
        from library.tools.local_ci import github
        return github.pr_files(pr)

    def proof_requirements(self, pr: int) -> list[str]:
        """Read current obligations from the PR body at merge time."""
        from library.tools.local_ci import github, proof
        return proof.requirements(github.get_pr(pr).get("body", ""))

    def missing_proofs(self, pr: int, head: str, required: list[str]) -> list[str]:
        from library.tools.local_ci import proof
        return proof.missing(pr, head, required, self.state_dir)

    def test_stack(self, base: str, members: list) -> TestResult:
        """Evaluate main+members stacked: Tier-2 runs the FULL suite once
        on the exact tree the ordered merges would produce.

        The pooled-run verify step consults the shared cache, records the
        CI-08 shadow plan and audit, then executes the full gate as the
        only merge authority. The gate's per-file verdicts are filed as
        `batch` evidence for cross-source reuse."""
        from library.tools.local_ci import hygiene, result_cache
        # The daemon keeps one mirror across queue passes. Main can move
        # after the mirror's last refresh, so fetch this exact base SHA
        # before creating a checkout. A missing/racing base must requeue,
        # never silently test an older tree or blame a PR for the failure.
        hygiene.fetch_base(self.mirror, base)
        with hygiene.disposable_checkout(self.mirror, base, self.state_dir) as checkout:
            stack = _stack_checkout(checkout, base, [m["head"] for m in members])
            if not stack["ok"]:
                return TestResult(False, reason=stack["reason"],
                                  stack_conflict=stack.get("conflict", ""))
            log = os.path.join(self.state_dir, "logs",
                               f"tier2-{int(time.time())}.log")
            builder = result_cache.KeyBuilder(checkout)
            interpreter = runner._tier2_interpreter(checkout)
            consulted = runner.tier2_consult(
                checkout, self.state_dir, interpreter, builder)
            print(f"ren-ci Tier-2 verify: reused "
                  f"{len(consulted['reused'])}/{consulted['total']} "
                  f"test files from shared cache; full gate remains "
                  f"the merge authority", flush=True)
            gate = runner.run_gate(
                checkout, log,
                timeout=history.default_tier2_timeout(self.state_dir),
                cache={"state_dir": self.state_dir, "source": "batch",
                       "builder": builder, "interpreter": interpreter,
                       "consulted": consulted})
            from library.tools.local_ci import shadow
            try:
                outcomes = gate.get("outcomes", {})
                if not isinstance(outcomes, dict):
                    outcomes = {}
                plan = runner.tier2_incremental_plan(
                    checkout, consulted, observed_outcomes=outcomes)
                head_result = subprocess.run(
                    ["git", "rev-parse", "HEAD"], cwd=checkout,
                    capture_output=True, encoding="utf-8", check=False,
                    timeout=30).stdout.strip()
                head = head_result or base
                audit = shadow.record_tier2_audit(
                    self.state_dir, head, plan,
                    {"verdict": gate.get("verdict", "DID NOT RUN")},
                    outcomes)
                print(f"ren-ci CI-08 shadow: hypothetical "
                      f"{audit['incremental_verdict']}; full gate "
                      f"{audit['full_gate_verdict']}; disagreement="
                      f"{audit['disagreement']}; would skip "
                      f"{audit['skipped_tests']}/{audit['total_tests']} "
                      f"test cases ({audit['skipped_fraction']:.1%}), "
                      f"{audit['skipped_files']}/{audit['total_files']} "
                      f"files", flush=True)
            except Exception as exc:  # noqa: BLE001 - observational audit
                # Shadow bookkeeping is observational and must never
                # alter the full-gate merge decision.
                print(f"ren-ci CI-08 shadow audit unavailable: {exc}",
                      flush=True)
            return TestResult(gate["ok"], reason=gate["reason"], log=log,
                              requeue=gate["requeue"],
                              failed_tests=gate.get("failed_tests") or [])

    def test_subset(self, base: str, members: list,
                    test_files: list) -> TestResult:
        """Evaluate ONLY test_files on the stacked tree: the focused
        reproduction (CI-12).

        The same stack the full gate would build (main + members in
        order), but pytest runs just the red stack's failing files
        instead of the whole suite. A red focused run REPRODUCES the
        failure on this subset; a green one exonerates it. Blame lands
        only on reproduction - never on full-gate correlation.

        Conflict maps exactly like test_stack (no gate spent deciding
        it). A run that could not start, ran no tests, or left no
        JUnit report requeues: the subset judged nothing.
        """
        if not test_files:
            return TestResult(False, requeue=True,
                              reason="no failing tests to reproduce")
        from library.tools.local_ci import hygiene, runner
        hygiene.fetch_base(self.mirror, base)
        with hygiene.disposable_checkout(
                self.mirror, base, self.state_dir) as checkout:
            stack = _stack_checkout(checkout, base, [m["head"] for m in members])
            if not stack["ok"]:
                return TestResult(False, reason=stack["reason"],
                                  stack_conflict=stack.get("conflict", ""))
            log = os.path.join(self.state_dir, "logs",
                               f"tier2-focused-{int(time.time())}.log")
            junit_path = log + ".junit.xml"
            try:
                result = runner.run(
                    list(test_files), checkout, log,
                    junit_path=junit_path)
            except Exception as exc:  # noqa: BLE001 - HygieneRefused et al
                return TestResult(
                    False, requeue=True,
                    reason=f"focused run could not start "
                           f"({type(exc).__name__}: {exc})")
            if result.get("timed_out"):
                return TestResult(False, requeue=True,
                                  reason="focused run timed out - re-testing",
                                  log=log)
            failed = runner.failed_tests_from_junit(junit_path)
            if result.get("ok"):
                return TestResult(True, passed=result.get("passed", 0),
                                  log=log)
            if not failed:
                # Red without a machine-readable report: the requested
                # files are the reproduction set (they were the only
                # files run), so carry them rather than an empty list
                # the caller would misread as "no evidence".
                failed = sorted(set(test_files))
            return TestResult(False, passed=result.get("passed", 0),
                              failed=result.get("failed", 1),
                              reason=(result.get("reason")
                                      or "focused run FAIL"),
                              log=log, failed_tests=failed)

    def pr_changed_files(self, base: str, head: str) -> list:
        """Repo-relative paths changed between base and head, or [].

        Unknown (fetch/diff failure) reads as "no evidence", never as
        "changed nothing": the caller treats unknown PRs as relevant,
        so diagnosis widens instead of exonerating blind.
        """
        import subprocess
        try:
            from library.tools.local_ci import hygiene
            mirror = self.mirror or hygiene.ensure_mirror(
                f"https://github.com/{config.OWNER}/{config.REPO}.git",
                self.state_dir)
            self.mirror = mirror
            fetched = subprocess.run(
                ["git", "-C", mirror, "fetch", "--quiet",
                 "origin", head],
                capture_output=True, encoding="utf-8",
                check=False, timeout=120)
            _ = fetched
            result = subprocess.run(
                ["git", "-C", mirror, "diff", "--name-only",
                 f"{base}..{head}"],
                capture_output=True, encoding="utf-8",
                check=False, timeout=120)
        except (OSError, subprocess.SubprocessError):
            return []
        if result.returncode != 0:
            return []
        return sorted(line.strip() for line in result.stdout.splitlines()
                      if line.strip())

    def relevant_members(self, base: str, members: list,
                         failing_tests: list) -> tuple:
        """Split members into (relevant, irrelevant) for failing_tests.

        Dependency-guided (CI-12 §3, CI-06 graph): a PR is relevant
        when the static selector maps any of its changed files onto any
        failing test. Usable with the static graph on day one; sharper
        with CI-06's coverage index (the selector itself narrows when
        its index is fresh). Unknown PRs (no changed files, selector
        failure, unmapped tree) stay relevant: diagnosis widens, never
        exonerates on missing evidence. Empty failing_tests keeps
        everybody relevant - the caller falls back to the legacy bisect
        before consulting this anyway.
        """
        if not failing_tests:
            return list(members), []
        try:
            from library.tools.local_ci import hygiene
            hygiene.fetch_base(self.mirror, base)
            with hygiene.disposable_checkout(
                    self.mirror, base, self.state_dir) as checkout:
                changed_by = {}
                for member in members:
                    changed_by[member["pr"]] = self.pr_changed_files(
                        base, member.get("head", ""))
                return relevant_partition_by_map(
                    changed_by, failing_tests,
                    select_fn=lambda changed: _affected_tests(
                        checkout, changed))
        except Exception:  # noqa: BLE001 - relevance never fails a verdict
            return list(members), []

    def test_main(self, base: str) -> TestResult:
        """Evaluate main ALONE: the same full gate, no member stacked.

        The red-batch baseline (a red main must kick nobody): when a
        stacked cluster fails, resolve_batch judges this before
        bisecting. Failures here belong to main, never to a member.
        Like the stacked verify, it consults first, executes always,
        and files its verdicts as `batch` evidence.
        """
        from library.tools.local_ci import hygiene, result_cache
        hygiene.fetch_base(self.mirror, base)
        with hygiene.disposable_checkout(self.mirror, base, self.state_dir) as checkout:
            log = os.path.join(self.state_dir, "logs",
                               f"tier2-main-{int(time.time())}.log")
            builder = result_cache.KeyBuilder(checkout)
            interpreter = runner._tier2_interpreter(checkout)
            consulted = runner.tier2_consult(
                checkout, self.state_dir, interpreter, builder)
            print(f"ren-ci Tier-2 verify (main): cache reports reused "
                  f"{len(consulted['reused'])}/{consulted['total']} "
                  f"test files; full gate remains the merge authority",
                  flush=True)
            gate = runner.run_gate(
                checkout, log,
                cache={"state_dir": self.state_dir, "source": "batch",
                       "builder": builder, "interpreter": interpreter,
                       "consulted": consulted})
            if (gate["ok"] and not gate["requeue"]
                    and getattr(self, "_state_explicit", False)):
                # A green main judgement generalizes to a certified
                # baseline (CI-07): the checkout that just passed is
                # still in hand, so file its inventory + keys + env now.
                # Best effort only - a refused or failed certification
                # never fails the verdict that already passed.
                try:
                    certified = certify_main_baseline(
                        checkout, self.state_dir, base, gate,
                        interpreter=interpreter, builder=builder,
                        source="batch")
                except OSError as exc:
                    print(f"ren-ci baseline: certify deferred for "
                          f"{base[:12]}: {exc}", flush=True)
                else:
                    if not certified.get("ok"):
                        print(f"ren-ci baseline: "
                              f"{certified.get('reason')}", flush=True)
            return TestResult(gate["ok"], reason=gate["reason"], log=log,
                              requeue=gate["requeue"],
                              failed_tests=gate.get("failed_tests") or [])

    # -- writes --
    def merge_pr(self, pr: int, head: str) -> str:
        from library.tools.local_ci import github
        return github.merge(pr, head)

    def report(self, pr: int, head: str, url: str, ok: bool, reason: str) -> None:
        from library.tools.local_ci import github
        target_url = url or github.pull_url(pr)
        if ok:
            github.post_status(head, "success", "ren-ci merge queue: merged",
                               target_url)
            github.remove_label(pr, config.READY_LABEL)
        else:
            github.post_status(head, "failure",
                               f"ren-ci merge queue: kicked - {reason}",
                               target_url)
            github.remove_label(pr, config.READY_LABEL)


def _git(cwd: str, *argv: str) -> tuple:
    import subprocess
    result = subprocess.run(
        ["git", "-C", cwd, *argv], capture_output=True,
        encoding="utf-8", check=False, timeout=300)
    return result.returncode, result.stderr.strip()


def _stack_checkout(checkout: str, base: str, heads: list) -> dict:
    """Merge heads onto the base checkout in order. Conflict is a
    RESULT (the stack cannot exist), not an exception. Each merge is
    committed locally so the next PR can merge onto the accumulated
    tree; the disposable checkout is never pushed. On conflict the
    result names the FULL head that would not merge (`conflict`), so
    the caller can kick only that member without running the gate."""
    for head in heads:
        code, _err = _git(
            checkout, "-c", "user.name=ren-ci", "-c",
            "user.email=ren-ci@localhost", "merge", "--no-ff", "--no-edit",
            head)
        if code != 0:
            _git(checkout, "merge", "--abort")
            return {"ok": False,
                    "reason": f"conflict stacking {head[:12]} onto batch",
                    "conflict": head}
    return {"ok": True, "reason": "", "conflict": ""}


def plan_cluster(entries: list, now: float, window: int) -> tuple:
    """Split the queue into (ripe cluster, waiting).

    A cluster is ripe when the window elapsed since the OLDEST ready
    entry arrived (the timebox closed). A ripe cluster takes EVERY
    queued entry: the full gate is one process whatever the cluster
    size, so holding part of the queue back for a second cluster buys
    nothing. Otherwise the whole queue waits: merging one PR per pass
    would serialize what the timebox exists to group. An empty queue
    ripens nothing.
    """
    queued = [e for e in entries if e.get("status") == state.ENTRY_QUEUED]
    if not queued:
        return [], []
    oldest = min(float(e.get("enqueued_at") or 0) for e in queued)
    if now - oldest >= window:
        return list(queued), []
    return [], queued


def certify_main_baseline(checkout: str, state_dir: str, sha: str,
                          gate: dict, interpreter: str = "",
                          builder=None, source: str = "") -> dict:
    """Certify main's full-suite baseline from a checkout the gate passed.

    The certified record is what later incremental verdicts (CI-08)
    reuse against: the main sha, the test inventory the gate ran over,
    the per-file verification keys (CI-01) computed under the same
    interpreter the Tier-2 consult used, the environment fingerprints,
    and the gate's own verdict line. Only a full `FULL-SUITE GATE:
    PASS` certifies - the same predicate merges use
    (`runner.certifiable_verdict`): a NARROWED verdict is refused with
    the reason and stores nothing, as is a verdict without test
    evidence. A falsy state dir refuses too, so scripted worlds and
    direct callers without a ledger stay hermetic and never reach the
    machine's real `~/.ren-ci`. Never raises: certification must not
    fail a verdict that already passed.
    """
    if not state_dir:
        return {"ok": False,
                "reason": ("certify refused: no state dir - a baseline "
                           "without a ledger certifies nothing")}
    verdict = (gate or {}).get("verdict", "")
    if not runner.certifiable_verdict(verdict):
        return {"ok": False,
                "reason": (f"certify refused: gate verdict "
                           f"'{verdict or 'missing'}' is not a full PASS - "
                           f"a baseline certifies only on "
                           f"FULL-SUITE GATE: PASS")}
    from library.tools.local_ci import result_cache
    try:
        own = builder or result_cache.KeyBuilder(checkout)
        resolved = interpreter or runner._tier2_interpreter(checkout)
        inventory = runner.tier2_inventory(checkout)
        keys = {rel: result_cache.compute_key(
            checkout, rel, own, interpreter=resolved)
            for rel in inventory}
        return state.certify_baseline(
            sha, verdict=verdict,
            verdict_line=runner.gate_verdict_line(
                (gate or {}).get("log", "")),
            inventory=inventory, keys=keys,
            env=result_cache.env_fingerprint(),
            interpreter=resolved,
            native=result_cache.native_fingerprint(checkout),
            config_text=result_cache.config_fingerprint(checkout),
            reason=(gate or {}).get("reason", ""),
            log=(gate or {}).get("log", ""),
            source=source or "batch", state=state_dir)
    except OSError as exc:
        return {"ok": False,
                "reason": f"certify refused: could not record it ({exc})"}


def judge_main(world: World, base: str) -> TestResult:
    """Judge main ALONE at this SHA: the red-batch baseline.

    Reuses a cached GREEN verdict for the current main SHA (in-memory
    first, then the file ledger) and otherwise runs the same full gate
    on main via world.test_main. A green verdict is cached per SHA on
    both layers; red and unjudgeable verdicts are never cached, so the
    next batch re-judges a main that may since have been fixed.
    """
    cache = getattr(world, "_main_baseline", None)
    if cache is None:
        cache = world._main_baseline = {}
    hit = cache.get(base)
    if hit is not None and hit.ok and not hit.requeue:
        return hit
    if getattr(world, "_state_explicit", False):
        record = state.main_baseline_green(base, world.state_dir)
        if record is not None:
            verdict = TestResult(True, reason=record.get("reason", ""),
                                 log=record.get("log", ""))
            cache[base] = verdict
            return verdict
    verdict = world.test_main(base)
    if verdict.ok and not verdict.requeue:
        cache[base] = verdict
        if getattr(world, "_state_explicit", False):
            try:
                state.record_main_baseline(base, True, verdict.reason,
                                           world.state_dir, log=verdict.log)
            except OSError:
                pass
    return verdict


def main_red_reason(base: str, verdict: TestResult) -> str:
    """The queue-entry reason when main itself is red: process_once
    detects this prefix to wake the operator (daemon log + status)."""
    detail = verdict.reason or "full gate FAIL"
    return (f"main {base[:12]} red: {detail} - members kept queued, "
            "re-testing after main is fixed")


def is_main_red_reason(reason: str) -> bool:
    """Whether a queue-entry reason names a red main baseline."""
    return isinstance(reason, str) and reason.startswith("main ") \
        and " red: " in reason


def is_stack_conflict(verdict: TestResult) -> bool:
    """Whether a stack verdict is a MERGE conflict, not a test failure.

    A conflict means the stack could not be built, so no gate ran and
    none may be spent deciding it: the member that cannot merge onto
    the accumulated stack is kicked and the rest is tested. The
    explicit `stack_conflict` head wins; the `conflict stacking`
    reason prefix is the legacy shape real stacks always carry.
    """
    if getattr(verdict, "stack_conflict", ""):
        return True
    reason = getattr(verdict, "reason", "") or ""
    return isinstance(reason, str) and reason.startswith("conflict stacking")


def _conflict_member_index(members: list, verdict: TestResult) -> int:
    """Position of the member that would not stack, or -1 if unknown."""
    head = getattr(verdict, "stack_conflict", "") or ""
    if head:
        for index, member in enumerate(members):
            if member.get("head") == head:
                return index
        for index, member in enumerate(members):
            candidate = str(member.get("head") or "")
            if candidate and (candidate.startswith(head)
                              or head.startswith(candidate)):
                return index
    reason = getattr(verdict, "reason", "") or ""
    prefix = "conflict stacking "
    if isinstance(reason, str) and reason.startswith(prefix):
        short = reason[len(prefix):].split(" ")[0]
        for index, member in enumerate(members):
            if str(member.get("head") or "").startswith(short):
                return index
    return -1


def _affected_tests(checkout: str, changed: list) -> set | None:
    """Tests the static selector maps changed files onto, or None.

    None means "unknown": the selector failed, the checkout has no
    selector, or the change names nothing testable. The caller treats
    unknown as relevant (widen, never exonerate). Uses the CI-06
    hybrid selector through `selection.dependent`, so a fresh
    coverage index sharpens the answer and a stale one falls back to
    the wide static selection byte-for-byte.
    """
    if not changed:
        return None
    try:
        from library.tools.local_ci import selection
        return set(selection.dependent(list(changed), repo=checkout))
    except Exception:  # noqa: BLE001 - relevance never fails a verdict
        return None


def relevant_partition_by_map(changed_by_pr: dict, failing_tests: list,
                              select_fn=None) -> tuple:
    """Split PR ids into (relevant, irrelevant) for failing test files.

    Pure (no git, no checkout): `changed_by_pr` maps PR id to its
    changed repo-relative paths, `select_fn(changed)` returns the
    affected test set or None for unknown. A PR is relevant when its
    affected set intersects the failing tests; unknown PRs (no changed
    files, selector failure, empty affected set with unchanged files
    present) stay relevant. Returns PR-id lists, order-stable by input.
    """
    failing = set(failing_tests or [])
    relevant, irrelevant = [], []
    for pr, changed in changed_by_pr.items():
        if not failing:
            relevant.append(pr)
            continue
        if not changed:
            relevant.append(pr)
            continue
        try:
            affected = select_fn(list(changed)) if select_fn is not None \
                else None
        except Exception:  # noqa: BLE001 - widen on selector failure
            affected = None
        if affected is None or set(affected) & failing:
            relevant.append(pr)
        else:
            irrelevant.append(pr)
    return relevant, irrelevant


def _focused_bisect(world: World, base: str, members: list,
                    failing_tests: list) -> tuple:
    """Bisect relevant members with focused runs: (culprits, clean,
    requeued, interaction).

    Each node runs ONLY the red stack's failing files
    (`world.test_subset`), never the full gate. Blame lands only on
    reproduction: a single red member is the culprit; red halves
    recurse; both-green halves are an interaction (the failure needs
    the combination). Any unjudgeable node poisons the split: the
    whole relevant set requeues rather than kicking on partial
    evidence. Combinations are included by construction - halves are
    tested as halves, never only as singles.
    """
    if not members:
        return [], [], [], False
    verdict = world.test_subset(base, members, list(failing_tests))
    if verdict.requeue:
        return [], [], list(members), False
    if is_stack_conflict(verdict):
        # A conflict is decided, not bisected: reuse the conflict
        # path's member lookup rather than spending focused runs on
        # what never built.
        return [], [], list(members), False
    if verdict.ok:
        return [], list(members), [], False
    if len(members) == 1:
        return list(members), [], [], False
    half = len(members) // 2
    culprits_a, clean_a, requeued_a, interaction_a = _focused_bisect(
        world, base, members[:half], failing_tests)
    culprits_b, clean_b, requeued_b, interaction_b = _focused_bisect(
        world, base, members[half:], failing_tests)
    if requeued_a or requeued_b or interaction_a or interaction_b:
        # Partial evidence poisons the split: halves that could not
        # be judged (or an interaction inside one half) mean the
        # bisect learned nothing conclusive about the whole.
        return [], [], list(members), False
    if not culprits_a and not culprits_b:
        # Both halves pass the focused files alone: the failure is
        # the COMBINATION of the halves.
        return [], [], [], True
    return (culprits_a + culprits_b, clean_a + clean_b, [], False)


def _legacy_bisect(world: World, base: str, members: list,
                   verdict: TestResult) -> tuple:
    """The pre-CI-12 halve-and-full-gate recursion, unchanged.

    Reached only when the red stack names no failing tests (no JUnit
    report): each bisect node re-runs the FULL gate via
    `world.test_stack`. Fail-closed fallback, kept so a report-less
    red still names its culprit exactly as before.
    """
    if len(members) == 1:
        member = dict(members[0])
        member["status"] = state.ENTRY_KICKED
        member["reason"] = verdict.reason or "stack test failed"
        return [], [member], []
    half = len(members) // 2
    green_a, kicked_a, requeued_a = resolve_batch(world, base, members[:half])
    green_b, kicked_b, requeued_b = resolve_batch(world, base, members[half:])
    if requeued_a or requeued_b:
        # A half the machine could not judge poisons the split: the
        # bisect learned nothing, so the whole cluster waits rather
        # than kicking on partial evidence.
        for member in members:
            if member.get("status") != state.ENTRY_KICKED:
                member["status"] = state.ENTRY_QUEUED
                member["reason"] = verdict.reason or "bisect inconclusive"
        return [], [], list(members)
    if not kicked_a and not kicked_b:
        # Both halves pass alone: the failure is the COMBINATION.
        # Land the first half in order; the second re-tests on the
        # new main rather than merging blind.
        for member in members[half:]:
            member["status"] = state.ENTRY_QUEUED
            member["reason"] = ("interaction with earlier cluster: "
                                "re-testing on the new main")
        return green_a, [], []
    return green_a + green_b, kicked_a + kicked_b, []


def _kick_with_failing_reason(members: list, failing_tests: list,
                              detail: str) -> list:
    """Mark members kicked, naming the failing tests that convicted them."""
    kicked = []
    names = ", ".join(list(failing_tests)[:3])
    suffix = f" (failing: {names})" if names else ""
    for member in members:
        entry = dict(member)
        entry["status"] = state.ENTRY_KICKED
        entry["reason"] = (detail or "stack test failed") + suffix
        kicked.append(entry)
    return kicked


def resolve_batch(world: World, base: str, members: list) -> tuple:
    """Test one cluster and split the verdict: (green, kicked, requeued).

    Green members carry their tested base+head for the freshness
    re-check at merge time. Kicked members carry the reason. Requeued
    members were never judged (narrowed environment, crashed harness):
    they wait for a better moment, they are not culprits. An
    interaction (halves pass, whole fails) returns the FIRST half as
    green and re-queues the second with the reason - the ordered merge
    below lands the first half, and the second re-tests on the new main.

    A red stack is judged against main ALONE before any bisect: when
    main itself is red every half stays red down to single PRs, so
    bisecting would kick each innocent member with main's failure
    reason. A red main kicks nobody - the whole cluster waits queued.
    Only a green main bisects.

    A stacking CONFLICT never reaches main or the bisect: the member
    that cannot merge onto the accumulated stack is KICKED with the
    conflict reason (its ready label is removed so it cannot re-enter
    silently - the author rebases and re-readies), and the rest is
    tested. No full-suite run is spent bisecting a conflict.

    A red stack with a JUnit report names its failing TESTS
    (`verdict.failed_tests`) and bisects FOCUSED (CI-12): the red-main
    baseline check stays, then only the failing files reproduce
    against relevant PR subsets (dependency-guided via
    `relevant_members`, combinations included - halves are tested as
    halves), blame lands only on reproduction, and the surviving
    stack revalidates with the FULL gate before merging (incremental
    after CI-08). A red stack with NO report falls back to the legacy
    halve-and-full-gate recursion, node for node.
    """
    if not members:
        return [], [], []
    verdict = world.test_stack(base, members)
    if not verdict.ok and not verdict.requeue and verdict.log:
        # Preserve the causal edge from this pooled failure to every
        # green Tier-1 head whose selection included the failing file.
        checks = state.read_checks(world.state_dir)
        failed = sorted(set(verdict.failed_tests or []))
        affected = []
        for member in members:
            check = checks.get(member.get("head"), {})
            selected = set(check.get("selected_files", []))
            skipped = set((check.get("skipped_files") or {}).keys())
            if not failed or selected.intersection(failed) or skipped.intersection(failed):
                affected.append(member.get("head", ""))
        state.link_full_run(
            affected,
            {"run_id": os.path.basename(verdict.log), "base_sha": base,
             "stack_sha": "", "verdict": "FAIL",
             "failed_tests": failed, "log": verdict.log},
            world.state_dir)
    if verdict.requeue:
        for member in members:
            member["status"] = state.ENTRY_QUEUED
            member["reason"] = verdict.reason or "gate could not judge"
        return [], [], list(members)
    if is_stack_conflict(verdict):
        index = _conflict_member_index(members, verdict)
        if index < 0:
            # Conflict without an identified member: requeue the whole
            # cluster with the reason rather than kicking blind or
            # spending gate runs bisecting what never built.
            for member in members:
                member["status"] = state.ENTRY_QUEUED
                member["reason"] = (verdict.reason
                                    or "conflict stacking onto batch")
            return [], [], list(members)
        kicked_member = dict(members[index])
        kicked_member["status"] = state.ENTRY_KICKED
        kicked_member["reason"] = (verdict.reason
                                   or "conflict stacking onto batch")
        rest = [m for i, m in enumerate(members) if i != index]
        if not rest:
            return [], [kicked_member], []
        green_rest, kicked_rest, requeued_rest = resolve_batch(
            world, base, rest)
        return green_rest, [kicked_member] + kicked_rest, requeued_rest
    if verdict.ok:
        return [_tested(m, base) for m in members], [], []
    baseline = judge_main(world, base)
    if baseline.requeue:
        for member in members:
            member["status"] = state.ENTRY_QUEUED
            member["reason"] = (baseline.reason
                                or "main baseline could not be judged")
        return [], [], list(members)
    if not baseline.ok:
        reason = main_red_reason(base, baseline)
        for member in members:
            member["status"] = state.ENTRY_QUEUED
            member["reason"] = reason
        return [], [], list(members)
    failing = sorted(getattr(verdict, "failed_tests", None) or [])
    if not failing:
        # No "which tests failed" read (no JUnit report): the legacy
        # halve-and-full-gate recursion, unchanged.
        return _legacy_bisect(world, base, members, verdict)
    if len(members) == 1:
        return _kick_with_failing_reason(
            members, failing, verdict.reason or "stack test failed"), [], []
    relevant, irrelevant = world.relevant_members(base, members, failing)
    if not relevant:
        # Nothing maps onto the failing tests: fail open to the full
        # set rather than exonerating the whole batch on a selector
        # miss.
        relevant, irrelevant = list(members), []
    culprits, _clean, requeued, interaction = _focused_bisect(
        world, base, relevant, failing)
    if requeued:
        # A focused node the machine could not judge poisons the
        # diagnosis: the bisect learned nothing, so the whole cluster
        # waits rather than kicking on partial evidence.
        for member in members:
            if member.get("status") != state.ENTRY_KICKED:
                member["status"] = state.ENTRY_QUEUED
                member["reason"] = verdict.reason or "bisect inconclusive"
        return [], [], list(members)
    if interaction:
        # Halves pass the failing files alone: the failure needs the
        # COMBINATION. Focused passes are not mergeable evidence, so
        # the first half revalidates with the full gate before it
        # lands; the second re-tests on the new main.
        half = len(relevant) // 2
        first, second = relevant[:half], relevant[half:]
        recheck = world.test_stack(base, first)
        if recheck.requeue:
            for member in members:
                member["status"] = state.ENTRY_QUEUED
                member["reason"] = (recheck.reason
                                    or "gate could not judge")
            return [], [], list(members)
        if not recheck.ok:
            # The first half alone is red under the full gate after
            # all: recurse on it for the real culprit.
            return resolve_batch(world, base, first + second)
        for member in second:
            member["status"] = state.ENTRY_QUEUED
            member["reason"] = ("interaction with earlier cluster: "
                                "re-testing on the new main")
        for member in irrelevant:
            member["status"] = state.ENTRY_QUEUED
            member["reason"] = ("interaction with earlier cluster: "
                                "re-testing on the new main")
        return [_tested(m, base) for m in first], [], \
            list(second) + list(irrelevant)
    if not culprits:
        # Focused diagnosis exonerated everybody it tested while the
        # full gate stays red: the failing-test read or the relevance
        # map missed (or the failure is order-dependent). Fail closed
        # to the legacy full-gate bisect over the whole batch.
        return _legacy_bisect(world, base, members, verdict)
    culprit_prs = {m["pr"] for m in culprits}
    survivors = [m for m in members if m.get("pr") not in culprit_prs]
    kicked = _kick_with_failing_reason(
        culprits, failing, verdict.reason or "stack test failed")
    if not survivors:
        return [], kicked, []
    # The surviving stack revalidates with the FULL gate before
    # merging (incremental after CI-08's bar is met): focused green
    # is reproduction evidence, never merge evidence.
    recheck = world.test_stack(base, survivors)
    if recheck.requeue:
        for member in survivors:
            member["status"] = state.ENTRY_QUEUED
            member["reason"] = (recheck.reason
                                or "gate could not judge")
        return [], kicked, list(survivors)
    if recheck.ok:
        return [_tested(m, base) for m in survivors], kicked, []
    # Survivors still red without the culprits: a second culprit, a
    # combination, or stale relevance. Recurse on the survivors (a
    # strictly smaller set, so this terminates) and keep the proven
    # culprits kicked.
    green_rest, kicked_rest, requeued_rest = resolve_batch(
        world, base, survivors)
    return green_rest, kicked + kicked_rest, requeued_rest


def merge_in_order(world: World, base: str, green: list) -> tuple:
    """Merge green members in queue order with a freshness re-check.

    Returns (merged, requeued). Before each merge the daemon re-reads
    main and requires it to be exactly what the batch predicts: the
    tested base before the first merge, the previous merge's landed SHA
    after it. Anything else means main moved under the batch, and this
    member plus the rest go back to queued - they re-test, they never
    merge stale. A moved or refused member also requeues every later
    member: their tested stack included the earlier member, so they
    cannot safely skip ahead. The REST merge's own head-SHA pin (409 on
    a moved head) is the same check through the server's eyes.
    """
    merged, requeued = [], []
    expected = base
    for index, member in enumerate(green):
        if member.get("tested_head") != member.get("head"):
            for offset, rest in enumerate(green[index:]):
                rest["status"] = state.ENTRY_QUEUED
                rest["reason"] = (
                    "head moved after the test - re-testing" if offset == 0
                    else "earlier batch member moved after the test - "
                    "re-testing")
                requeued.append(rest)
            break
        if member.get("tested_base") != base:
            for rest in green[index:]:
                rest["status"] = state.ENTRY_QUEUED
                rest["reason"] = "main moved under the batch - re-testing"
                requeued.append(rest)
            break
        try:
            required = world.proof_requirements(member["pr"])
            missing = world.missing_proofs(
                member["pr"], member["head"], required)
        except (OSError, RuntimeError) as exc:
            missing = [f"proof requirements unavailable: {exc}"]
        if missing:
            for offset, rest in enumerate(green[index:]):
                rest["status"] = state.ENTRY_QUEUED
                rest["reason"] = (
                    "required proof missing: " + ", ".join(missing)
                    if offset == 0 else
                    "earlier batch member did not merge - re-testing")
                requeued.append(rest)
            break
        try:
            current = world.main_sha()
        except (OSError, RuntimeError) as exc:
            for rest in green[index:]:
                rest["status"] = state.ENTRY_QUEUED
                rest["reason"] = f"could not read main: {exc} - re-testing"
                requeued.append(rest)
            break
        if current != expected:
            for rest in green[index:]:
                rest["status"] = state.ENTRY_QUEUED
                rest["reason"] = ("main moved under the batch "
                                   f"({expected[:12]} -> {current[:12]}) - "
                                   "re-testing")
                requeued.append(rest)
            break
        try:
            landed = world.merge_pr(member["pr"], member["head"])
        except (OSError, RuntimeError) as exc:
            for offset, rest in enumerate(green[index:]):
                rest["status"] = state.ENTRY_QUEUED
                rest["reason"] = (
                    f"merge refused ({exc}) - re-testing" if offset == 0
                    else "earlier batch member did not merge - re-testing")
                requeued.append(rest)
            break
        member["status"] = state.ENTRY_MERGED
        member["reason"] = ""
        member["landed_as"] = landed
        merged.append(member)
        expected = landed or member["head"]
    return merged, requeued


def _merge_onto_fresh_ledger(world_state_dir: str, members: list,
                         fields: tuple, match_head: bool) -> list:
    """Write member states back without dropping a concurrent pass's rows.

    A dispatch pass beside a running Tier-2 worker keeps admitting
    heads: a green head is enqueued, a red head's entries are removed.
    Writing this run's snapshot wholesale would silently drop those
    rows (the ledger-write-lost-to-a-thread-race class), so each
    member merges onto a fresh ledger read. A member the fresh ledger
    no longer names was deliberately removed - it stays removed - and
    rows the ledger gained meanwhile are kept as-is. Returns the
    ledger as written.
    """
    fresh = state.read_queue(world_state_dir)
    by_pr = {entry.get("pr"): entry for entry in fresh
             if isinstance(entry, dict)}
    for member in members:
        slot = by_pr.get(member.get("pr"))
        if slot is None:
            continue
        if match_head and slot.get("head") != member.get("head"):
            continue
        for field in fields:
            if field == "reason":
                slot[field] = member.get(field, "")
            elif field in member:
                slot[field] = member[field]
    state.write_queue(fresh, world_state_dir)
    return fresh


def _tested(member: dict, base: str) -> dict:
    member = dict(member)
    member["status"] = state.ENTRY_GREEN
    member["tested_base"] = base
    member["tested_head"] = member.get("head", "")
    return member


def process_once(world: World, window: int = 90,
                 select=None) -> dict:
    """One queue pass: ripen a cluster, test it, merge it, persist.

    At most ONE cluster per pass: Tier-2 owns the machine alone, and a
    second cluster behind it waits for the next pass rather than
    fighting for cores. An unripe queue reports waiting (with the
    oldest entry's age) instead of testing: the timebox exists to group.
    A ripe cluster still takes EVERY queued entry (`plan_cluster` is
    window-only); the cost-aware grouping (CI-11) prints ADVICE with
    its cost terms before the verify, hardening into grouping logic
    only with data. `select` is the dependency-graph callable the
    advice prices overlap with (default the static selector);
    scripted worlds pass a fake to stay hermetic.
    Returns the summary.
    """
    entries = state.read_queue(world.state_dir)
    summary: dict = {"ripe": False, "waiting": 0, "merged": [],
                     "kicked": [], "requeued": [], "green": [],
                     "main_red": False, "main_reason": ""}
    cluster, waiting = plan_cluster(entries, time.time(), window)
    summary["waiting"] = len(waiting)
    if not cluster:
        return summary
    summary["ripe"] = True
    base = world.main_sha()
    # Freshness first: a member whose head moved since queueing is
    # not tested, it is refreshed - the verdict must belong to the
    # tree that would land.
    fresh = []
    for member in cluster:
        try:
            head = world.pr_head(member["pr"])
        except (OSError, RuntimeError) as exc:
            member["status"] = state.ENTRY_QUEUED
            member["reason"] = f"could not read PR head: {exc}"
            summary["requeued"].append(member["pr"])
            continue
        if head != member.get("head"):
            member["head"] = head
            member["status"] = state.ENTRY_QUEUED
            member["reason"] = "new head - awaiting fresh checks"
            summary["requeued"].append(member["pr"])
            continue
        member["status"] = state.ENTRY_TESTING
        fresh.append(member)
    # Merge, not wholesale: a dispatch pass may have admitted heads
    # while the freshness re-checks ran above (heads move under the
    # batch, so members match by PR here, not by head).
    _merge_onto_fresh_ledger(world.state_dir, cluster,
                             ("head", "status", "reason"),
                             match_head=False)
    if select is None:
        from library.tools.local_ci import selection
        select = selection.dependent
    _advise_grouping(world, fresh, select=select)
    try:
        green, kicked, requeued_tier2 = resolve_batch(world, base, fresh)
    except Exception as exc:  # noqa: BLE001 - interruption is not a verdict
        green, kicked = [], []
        requeued_tier2 = []
        for member in fresh:
            member["status"] = state.ENTRY_QUEUED
            member["reason"] = (
                f"Tier-2 runner interrupted ({type(exc).__name__}): {exc}")
            requeued_tier2.append(member)
    for member in kicked:
        _report(world, member, False)
        summary["kicked"].append(member["pr"])
    for member in requeued_tier2:
        summary["requeued"].append(member["pr"])
    if (not green and not kicked and requeued_tier2
            and all(is_main_red_reason(m.get("reason", ""))
                    for m in requeued_tier2)):
        # Main itself is red: kicked nobody, members stay queued. Wake
        # the operator through the daemon log (this line) and the
        # status (queue reasons + this summary) - the batch re-tests
        # after main is fixed, it never bisects a broken baseline.
        summary["main_red"] = True
        summary["main_reason"] = requeued_tier2[0].get("reason", "")
        print(f"ren-ci Tier-2: {summary['main_reason']} "
              f"(members {[m['pr'] for m in requeued_tier2]} kept queued)",
              flush=True)
    merged, requeued = merge_in_order(world, base, green)
    now = time.time()
    for member in merged:
        member["merged_at"] = now
        _report(world, member, True)
        summary["merged"].append(member["pr"])
    for member in requeued:
        summary["requeued"].append(member["pr"])
    summary["green"].extend(m["pr"] for m in green)
    # Verdicts above land on COPIES (resolve_batch never mutates a
    # member it did not decide): merge them onto a fresh ledger read
    # by PR, so rows a dispatch pass wrote while the gate ran (fresh
    # admissions, deliberate removals) survive the write-back. A
    # member still testing after the merge missed every branch -
    # queued, never lost - but only when it is one of ours: a foreign
    # testing row belongs to a run this worker never saw, and the
    # idle recovery path (recover_interrupted_queue) owns it.
    _merge_onto_fresh_ledger(
        world.state_dir, kicked + merged + requeued + requeued_tier2,
        ("status", "reason", "landed_as", "merged_at"), match_head=True)
    tested = {(member.get("pr"), member.get("head")) for member in fresh}
    swept = state.read_queue(world.state_dir)
    if any(entry.get("status") == state.ENTRY_TESTING
           and (entry.get("pr"), entry.get("head")) in tested
           for entry in swept):
        for entry in swept:
            if (entry.get("status") == state.ENTRY_TESTING
                    and (entry.get("pr"), entry.get("head")) in tested):
                entry["status"] = state.ENTRY_QUEUED
        state.write_queue(swept, world.state_dir)
    return summary


def _advise_grouping(world: World, fresh: list, select=None) -> None:
    """Print the CI-11 cost-aware grouping advice for a ripe cluster.

    Advisory only: the cluster below still verifies whole whatever the
    advice says. File lists come from the entry when a previous pass
    filed them, else from `world.pr_files` best-effort; anything
    unreadable stays unknown (eyes-open overlap), never a refusal.
    Never raises: advice must not fail a verdict that already passed.
    """
    try:
        from library.tools.local_ci import grouping
        for member in fresh:
            if member.get("files"):
                continue
            try:
                files = world.pr_files(member["pr"])
            except Exception:  # noqa: BLE001, S112 - unknown, not unjudgeable
                continue
            if files:
                member["files"] = list(files)
        for line in grouping.advise_for_cluster(
                fresh, state_dir=world.state_dir, now=time.time(),
                select=select):
            print(line, flush=True)
    except Exception as exc:  # noqa: BLE001 - advice never fails the run
        print(f"ren-ci grouping: advice skipped ({exc})", flush=True)


def _report(world: World, member: dict, ok: bool) -> None:
    """Do not lose the queue verdict because GitHub reporting failed.

    Queue state is committed after this call, and the delivery failure
    remains visible in the daemon log.
    """
    try:
        world.report(member["pr"], member["head"], "", ok,
                     member.get("reason", ""))
    except Exception as exc:  # noqa: BLE001 - preserve the test/merge result
        print(f"ren-ci queue status delivery deferred for "
              f"{member['pr']}: {exc}", flush=True)
