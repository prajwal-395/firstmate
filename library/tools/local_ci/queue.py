"""The Tier-2 merge queue: timeboxed clusters, one full-gate pass, ordered merges.

Tier-1 (per-PR dependent selections, parallel, immediate) is the fast
signal and the GATE into this queue. Nothing here merges without a
FULL-suite pass on the exact tree that lands - test security is not
given up for speed:

- CLUSTER: ready PRs gather within an adaptive window (load.targets:
  short on a quiet machine, long on a busy one). A ripe cluster (window
  elapsed, or BATCH_MAX reached) tests together; an unripe one waits.
- TEST: the cluster stacks onto current main (main + members in queue
  order) in a disposable checkout and runs the FULL suite once on that
  stacked tree - the exact tree the ordered merges will produce.
- BISECT ON RED: a red stack splits in halves until the culprit is
  named and kicked with the reason; the rest merge. Halves that pass
  ALONE while the whole fails are an INTERACTION (the "green alone, red
  together" main-breaker): the first half merges in order and the
  second re-tests on the new main.
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
import time

from library.tools.local_ci import config, runner, state


class StaleHead(RuntimeError):
    """The head or the base moved under a tested cluster: re-test, never merge."""


class TestResult:
    """One stack evaluation: the verdict plus what to say about it.

    requeue=True means "the machine could not judge" (narrowed
    environment, crashed harness): the cluster waits for a better
    moment. It is neither green (merge) nor red (bisect/kick).
    """

    def __init__(self, ok: bool, passed: int = 0, failed: int = 0,
                 reason: str = "", log: str = "", requeue: bool = False) -> None:
        self.ok = ok
        self.passed = passed
        self.failed = failed
        self.reason = reason
        self.log = log
        self.requeue = requeue


class World:
    """Effect seam: git, pytest, GitHub. The daemon subclasses nothing -
    it uses this directly; tests override what they script."""

    def __init__(self, mirror: str = "", state_dir: str = "") -> None:
        self.mirror = mirror
        self.state_dir = state_dir or config.state_dir()

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

    def test_stack(self, base: str, members: list) -> TestResult:
        """Evaluate main+members stacked: Tier-2 runs the FULL suite once
        on the exact tree the ordered merges would produce."""
        from library.tools.local_ci import hygiene
        # The daemon keeps one mirror across queue passes. Main can move
        # after the mirror's last refresh, so fetch this exact base SHA
        # before creating a checkout. A missing/racing base must requeue,
        # never silently test an older tree or blame a PR for the failure.
        hygiene.fetch_base(self.mirror, base)
        with hygiene.disposable_checkout(self.mirror, base, self.state_dir) as checkout:
            stack = _stack_checkout(checkout, base, [m["head"] for m in members])
            if not stack["ok"]:
                return TestResult(False, reason=stack["reason"])
            log = os.path.join(self.state_dir, "logs",
                               f"tier2-{int(time.time())}.log")
            gate = runner.run_gate(checkout, log)
            return TestResult(gate["ok"], reason=gate["reason"], log=log,
                              requeue=gate["requeue"])

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
    tree; the disposable checkout is never pushed."""
    for head in heads:
        code, _err = _git(
            checkout, "-c", "user.name=ren-ci", "-c",
            "user.email=ren-ci@localhost", "merge", "--no-ff", "--no-edit",
            head)
        if code != 0:
            _git(checkout, "merge", "--abort")
            return {"ok": False,
                    "reason": f"conflict stacking {head[:12]} onto batch"}
    return {"ok": True, "reason": ""}


def plan_cluster(entries: list, now: float, window: int) -> tuple:
    """Split the queue into (ripe cluster, waiting).

    A cluster is ripe when the window elapsed since the OLDEST ready
    entry arrived (the timebox closed), or when it reached BATCH_MAX
    (a full box runs now rather than holding the oldest PR hostage for
    company). Otherwise the whole queue waits: merging one PR per pass
    would serialize what the timebox exists to group. An empty queue
    ripens nothing.
    """
    queued = [e for e in entries if e.get("status") == state.ENTRY_QUEUED]
    if not queued:
        return [], []
    oldest = min(float(e.get("enqueued_at") or 0) for e in queued)
    cluster = queued[:config.BATCH_MAX]
    if len(queued) >= config.BATCH_MAX or now - oldest >= window:
        return cluster, queued[len(cluster):]
    return [], queued


def resolve_batch(world: World, base: str, members: list) -> tuple:
    """Test one cluster and split the verdict: (green, kicked, requeued).

    Green members carry their tested base+head for the freshness
    re-check at merge time. Kicked members carry the reason. Requeued
    members were never judged (narrowed environment, crashed harness):
    they wait for a better moment, they are not culprits. An
    interaction (halves pass, whole fails) returns the FIRST half as
    green and re-queues the second with the reason - the ordered merge
    below lands the first half, and the second re-tests on the new main.
    """
    if not members:
        return [], [], []
    verdict = world.test_stack(base, members)
    if verdict.requeue:
        for member in members:
            member["status"] = state.ENTRY_QUEUED
            member["reason"] = verdict.reason or "gate could not judge"
        return [], [], list(members)
    if verdict.ok:
        return [_tested(m, base) for m in members], [], []
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


def _tested(member: dict, base: str) -> dict:
    member = dict(member)
    member["status"] = state.ENTRY_GREEN
    member["tested_base"] = base
    member["tested_head"] = member.get("head", "")
    return member


def process_once(world: World, window: int = 90) -> dict:
    """One queue pass: ripen a cluster, test it, merge it, persist.

    At most ONE cluster per pass: Tier-2 owns the machine alone, and a
    second cluster behind it waits for the next pass rather than
    fighting for cores. An unripe queue reports waiting (with the
    oldest entry's age) instead of testing: the timebox exists to group.
    Returns the summary.
    """
    entries = state.read_queue(world.state_dir)
    summary: dict = {"ripe": False, "waiting": 0, "merged": [],
                     "kicked": [], "requeued": [], "green": []}
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
    state.write_queue(entries, world.state_dir)
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
    # member it did not decide): write them back onto the ledger by
    # PR, so the queue says what happened. Anything still TESTING
    # after the write-back missed every branch - queued, never lost.
    by_pr = {e["pr"]: e for e in entries}
    for member in kicked + merged + requeued + requeued_tier2:
        slot = by_pr.get(member["pr"])
        if slot is not None and slot.get("head") == member.get("head"):
            slot["status"] = member["status"]
            slot["reason"] = member.get("reason", "")
            for stamp in ("landed_as", "merged_at"):
                if stamp in member:
                    slot[stamp] = member[stamp]
    for member in entries:
        if member.get("status") == state.ENTRY_TESTING:
            member["status"] = state.ENTRY_QUEUED
    state.write_queue(entries, world.state_dir)
    return summary


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
