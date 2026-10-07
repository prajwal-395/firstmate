"""The event-driven loop: heads, slots, clusters, and the queue.

Two tiers, one loop:

- TIER-1 (per PR, immediate, parallel): every new head runs its
  dependent selection plus the always-run set the moment a slot is
  free - up to load.targets() jobs at once, each with its measured
  pytest -n. This is the fast signal and the GATE into the queue, not
  a merge verdict.
- TIER-2 (integration, timeboxed): ripe ready-clusters run the FULL
  suite once on the exact stacked tree that would land, then merge in
  order with freshness chaining. No PR merges without that pass.

Events, not windows: a new head starts Tier-1 when a slot frees; a
freed slot dispatches the oldest waiting head; a ripe cluster starts
Tier-2. The only timers are head DETECTION (config.POLL_SECONDS, a
cheap REST poll, zero Actions minutes) and the cluster WINDOW (which
groups, never gates Tier-1). GitHub offers no push channel to a Mac
behind NAT without opening a port; a webhook receiver is the
documented future upgrade.

One loop pass:
1. Measure the machine -> dispatch plan (jobs, pytest -n, window).
2. Read open PRs targeting main (skip drafts); note first-seen heads.
3. Known heads: enqueue when green + ready. New heads: Tier-1 now,
   in threads, as many as the plan allows.
4. Ripe ready-cluster with no batch in flight -> Tier-2 process_once.

A pass never overlaps itself: Tier-1 threads join before Tier-2, and
ledger writes happen on the main thread only, so two workers never
record over each other.
"""

from __future__ import annotations

import concurrent.futures
import os
import time

from library.tools.local_ci import (
    config,
    hygiene,
    load,
    queue,
    runner,
    selection,
    state,
)


def run_tier1(world: queue.World, pr: dict, workers: int) -> dict:
    """Execute one PR's Tier-1 check: static gate, then its selection.

    Thread-safe: posts statuses and runs subprocesses, but writes no
    ledger (the main thread records). Returns the outcome; raises
    HygieneRefused when the run could not start (disk, gate, moved
    head) - a refusal is not a verdict, the next pass retries.
    """
    from library.tools.local_ci import github
    number, head, url = pr["number"], pr["head"], pr["url"]
    github.post_status(head, "pending", "ren-ci: selecting tests", url)
    mirror = world.mirror or hygiene.ensure_mirror(
        f"https://github.com/{config.OWNER}/{config.REPO}.git", world.state_dir)
    world.mirror = mirror
    base = github.main_sha()
    hygiene.fetch_base(mirror, base)
    local_head = hygiene.fetch_pr(mirror, number)
    if local_head != head:
        raise hygiene.HygieneRefused(
            f"PR #{number} moved during fetch ({head[:12]} -> "
            f"{local_head[:12]}): next pass tests the new head")
    with hygiene.disposable_checkout(mirror, head, world.state_dir) as checkout:
        try:
            files = selection.changed_files(checkout, base, head)
        except RuntimeError as exc:
            raise hygiene.HygieneRefused(
                f"PR #{number} diff unreadable ({exc}): next pass retries "
                "on a fresh fetch") from exc
        paths = selection.selection_for(files, checkout)
        command = (selection.selection_command(files) + " + always-run(" +
                   ", ".join(config.ALWAYS_RUN) + ")")
        static = runner.run_static(checkout, files)
        if not static["ok"]:
            return {"pr": number, "head": head, "url": url, "ok": False,
                    "passed": 0, "failed": 1, "elapsed": 0.0,
                    "selection": paths, "command": command,
                    "static_failed": static["reason"]}
        log = os.path.join(world.state_dir, "logs",
                           f"pr-{number}-{head[:12]}.log")
        github.post_status(head, "pending",
                           f"ren-ci: running {len(paths)} selections", url)
        result = runner.run(paths, checkout, log, workers=workers)
        return {"pr": number, "head": head, "url": url,
                "ok": result["ok"], "passed": result["passed"],
                "failed": result["failed"], "elapsed": result["elapsed"],
                "log": result["log"], "selection": paths,
                "command": command,
                "broker_leaked": result.get("broker_leaked", False),
                "timed_out": result.get("timed_out", False),
                "timeout_diagnostic": result.get("timeout_diagnostic", ""),
                "process_group_leaked": result.get(
                    "process_group_leaked", False)}


def finish_tier1(world: queue.World, pr: dict, outcome: dict,
                 seen_at: float, slot: int) -> dict:
    """Record a Tier-1 outcome and post its verdict (main thread only).

    Returns the check record, carrying the latency the captain tunes
    against: seen (first observed) -> done. A red Tier-1 with the ready
    label loses the label: it cannot queue until it is green again.
    """
    from library.tools.local_ci import github
    done_at = time.time()
    record = state.record_check(
        {**outcome, "seen_at": seen_at, "done_at": done_at,
         "tier1_latency": round(done_at - seen_at, 1), "slot": slot,
         "status_posted": False, "status_error": ""},
        world.state_dir)
    line = runner.format_report(
        outcome["url"], outcome["head"], outcome["passed"],
        outcome["failed"], outcome["command"])
    record["report"] = line
    print(line, flush=True)
    _publish_check_status(record, world.state_dir)
    record = state.read_checks(world.state_dir).get(outcome["head"], record)
    if (not outcome["ok"]
            and config.READY_LABEL in (pr.get("labels") or [])):
        github.remove_label(outcome["pr"], config.READY_LABEL)
    return record


def _publish_check_status(record: dict, state_dir: str = "") -> bool:
    """Publish a cached verdict; retry it without re-running pytest.

    A status API failure is not a test verdict. The ledger keeps the
    result and the next pass retries delivery, so GitHub cannot remain
    pending forever after a completed local run.
    """
    from library.tools.local_ci import github
    head = record["head"]
    if record.get("static_failed"):
        first = record["static_failed"].splitlines()[0] or "failed"
        status, description = "failure", f"ren-ci static gate: {first}"
    elif record.get("ok"):
        status = "success"
        description = f"ren-ci: {record.get('passed', 0)} passed"
    else:
        if record.get("process_group_leaked"):
            reason = "pytest process group did not exit"
        elif record.get("broker_leaked"):
            reason = "broker leaked"
        elif record.get("timed_out"):
            reason = "timed out"
            diagnostic = record.get("timeout_diagnostic", "")
            if diagnostic:
                reason += f": {diagnostic}"
        else:
            reason = f"{record.get('failed', 1)} failed"
        status, description = "failure", f"ren-ci: {reason}"
    target_url = record.get("url") or github.pull_url(record["pr"])
    try:
        github.post_status(head, status, description[:140], target_url)
    except github.GitHubError as exc:
        state.update_check(head, {"status_posted": False,
                                  "status_error": str(exc)}, state_dir)
        print(f"ren-ci status delivery deferred for {head[:12]}: {exc}",
              flush=True)
        return False
    state.update_check(head, {"status_posted": True, "status_error": ""},
                       state_dir)
    return True


def pass_once(world: queue.World, plan: dict | None = None) -> dict:
    """One detection+scheduling pass. Returns what it did."""
    from library.tools.local_ci import github
    plan = plan or load.targets(load.measure())
    done: dict = {"plan": load.describe(plan), "checked": [],
                  "queued": [], "refused": [], "queue": {}}
    try:
        prs = github.list_open_prs()
    except github.GitHubError as exc:
        done["refused"].append(f"github unreachable: {exc}")
        return done
    checks = state.read_checks(world.state_dir)
    due = []
    for pr in sorted(prs, key=lambda p: p["number"]):
        if pr.get("draft") or pr.get("base") != "main":
            continue
        seen_at = state.note_seen(pr["number"], pr["head"], world.state_dir)
        known = checks.get(pr["head"])
        if known and known.get("pr") == pr["number"]:
            if not known.get("status_posted", False):
                if not _publish_check_status(known, world.state_dir):
                    done["refused"].append(
                        f"PR #{pr['number']}: verdict ready; status delivery "
                        "will retry next pass")
                    continue
                known = state.read_checks(world.state_dir).get(
                    pr["head"], known)
            _maybe_enqueue(world, pr, known, done)
            continue
        due.append((pr, seen_at))
    if due:
        slots = hygiene.Slots(world.state_dir, jobs=plan["tier1_jobs"])

        def work(item):
            pr, seen_at = item
            with slots.claim({"kind": "tier1", "pr": pr["number"],
                              "head": pr["head"]}) as slot:
                if slot is None:
                    return (pr, seen_at, None, None, "no free slot")
                try:
                    outcome = run_tier1(world, pr, plan["pytest_n"])
                except hygiene.HygieneRefused as exc:
                    return (pr, seen_at, slot, None, str(exc))
                except Exception as exc:  # noqa: BLE001 - keep daemon crashes pending
                    return (pr, seen_at, slot, {"crashed": str(exc)}, None)
                return (pr, seen_at, slot, outcome, None)

        with concurrent.futures.ThreadPoolExecutor(
                max_workers=plan["tier1_jobs"]) as pool:
            for pr, seen_at, slot, outcome, problem in pool.map(work, due):
                if outcome is None:
                    done["refused"].append(f"PR #{pr['number']}: {problem}")
                    if problem != "no free slot":
                        try:
                            github.post_status(
                                pr["head"], "pending",
                                f"ren-ci deferred: {problem}"[:140],
                                pr["url"])
                        except github.GitHubError:
                            pass
                    continue
                if "crashed" in outcome:
                    # A crash is the DAEMON's failure, never the PR's
                    # verdict: pending (retry next pass), never error.
                    # An error state blocks merges and cries wolf on a
                    # PR that was never judged; the refused list in the
                    # pass log is where the crash stays visible.
                    try:
                        github.post_status(pr["head"], "pending",
                                           "ren-ci: internal error, "
                                           "retrying next pass"[:140],
                                           pr["url"])
                    except github.GitHubError:
                        pass
                    done["refused"].append(
                        f"PR #{pr['number']}: crashed: {outcome['crashed']}")
                    continue
                record = finish_tier1(world, pr, outcome, seen_at, slot)
                checks[pr["head"]] = record
                done["checked"].append(pr["number"])
                _maybe_enqueue(world, pr, record, done)
    entries = state.read_queue(world.state_dir)
    if (any(e.get("status") in (state.ENTRY_QUEUED, state.ENTRY_GREEN)
            for e in entries)
            and not any(e.get("status") == state.ENTRY_TESTING
                        for e in entries)):
        done["queue"] = queue.process_once(world, window=plan["window"])
    return done


def _maybe_enqueue(world: queue.World, pr: dict, record: dict, done: dict) -> None:
    if not record.get("ok") or not record.get("status_posted"):
        return
    if config.READY_LABEL not in (pr.get("labels") or []):
        return
    state.enqueue(pr["number"], pr["head"], world.state_dir)
    done["queued"].append(pr["number"])


def check_pr(world: queue.World, pr: dict, slot: int,
             workers: int = 2) -> dict:
    """Run one PR's check end to end (manual `ren ci check` path)."""
    seen_at = state.note_seen(pr["number"], pr["head"], world.state_dir)
    outcome = run_tier1(world, pr, workers)
    return finish_tier1(world, pr, outcome, seen_at, slot)


def serve(state_dir: str = "", poll: int = config.POLL_SECONDS) -> int:
    """The daemon: passes until killed. Returns never (0 on clean exit)."""
    world = queue.World(state_dir=state_dir or config.state_dir())
    os.makedirs(os.path.join(world.state_dir, "logs"), exist_ok=True)
    print(f"ren-ci serving {config.OWNER}/{config.REPO} "
          f"from {world.state_dir}", flush=True)
    while True:
        try:
            result = pass_once(world)
            print(f"ren-ci pass [{result['plan']}]: "
                  f"checked={result['checked']} queued={result['queued']} "
                  f"refused={result['refused']} queue={result['queue']}",
                  flush=True)
        except Exception as exc:  # noqa: BLE001 - one pass must not stop polling
            print(f"ren-ci pass failed: {exc}", flush=True)
        time.sleep(poll)
