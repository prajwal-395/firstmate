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

The daemon keeps Tier-1 workers in a persistent pool while it polls:
completed outcomes are recorded on the daemon thread, and a newly free
slot triggers an immediate detection pass. This lets a new head use a
free slot while another head is still running.

One synchronous/manual pass:
1. Measure the machine -> dispatch plan (jobs, pytest -n, window).
2. Read open PRs targeting main (skip drafts); note first-seen heads.
3. Known heads: enqueue when green + ready. New heads: Tier-1 now,
   in threads, as many as the plan allows.
4. Ripe ready-cluster with no batch in flight -> Tier-2 process_once.

Tier-1 never yields to a ripe Tier-2 cluster at dispatch: new heads
are checked even while a batch is ripe or testing, so every ready PR
enters the queue. Heavy work still never overlaps: a Tier-1 pytest
refuses while a full-suite gate holds the machine lock (runner.run),
and a ripe batch fires through the idle gate once running Tier-1
jobs drain.

Tier-1 ledger writes stay on the daemon thread, so concurrent results
cannot overwrite each other's records.
"""

from __future__ import annotations

import concurrent.futures
import os
import threading
import time
from urllib.parse import urlsplit

from library.tools.local_ci import (
    config,
    hygiene,
    load,
    queue,
    runner,
    selection,
    self_update,
    state,
)


def tier2_only_slugs() -> list:
    """Short names for the Tier-2-only files, for the status line.

    The posted description is truncated at 140 characters, so full
    repo-relative paths cannot fit there; the report line carries the
    full paths, the description carries these slugs.
    """
    slugs = []
    for entry in config.TIER1_TIER2_ONLY:
        base = os.path.basename(entry)
        if base.startswith("test_"):
            base = base[len("test_"):]
        if base.endswith(".py"):
            base = base[:-len(".py")]
        slugs.append(base.replace("_", "-"))
    return slugs


def tier2_only_marker() -> str:
    """The Tier-1 status line's account of what it skipped on purpose."""
    return ("[tier2-only to Tier-2: "
            + ", ".join(config.TIER1_TIER2_ONLY) + "]")


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
        capped = selection.capped_selection_for(files, checkout)
        paths = capped["paths"]
        command = (selection.selection_command(files) + " + always-run(" +
                   ", ".join(config.ALWAYS_RUN) + ") "
                   + tier2_only_marker())
        if capped["capped"]:
            command += (f" [time-budget: {capped['dependent_kept']} of "
                        f"{capped['dependent_total']} dependent "
                        f"(~{capped['estimated_seconds']:.1f}s of "
                        f"{capped['budget_seconds']:.0f}s, "
                        f"{capped['deferred']} deferred); "
                        f"breadth to Tier-2]")
        static = runner.run_static(checkout, files)
        if not static["ok"]:
            return {"pr": number, "head": head, "url": url, "ok": False,
                    "passed": 0, "failed": 1, "elapsed": 0.0,
                    "selection": paths, "command": command,
                    "selection_capped": capped["capped"],
                    "selection_detail": capped,
                    "static_failed": static["reason"]}
        log = os.path.join(world.state_dir, "logs",
                           f"pr-{number}-{head[:12]}.log")
        github.post_status(head, "pending",
                           f"ren-ci: running {len(paths)} selections", url)
        from library.tools.local_ci import result_cache
        builder = result_cache.KeyBuilder(checkout)
        part = result_cache.partition(
            world.state_dir, checkout, paths, builder=builder,
            pytest_python=runner._pytest_python())
        cache_detail = {"reused": len(part["reused"]),
                        "total": part["total"],
                        "reused_passed": part["reused_passed"]}
        if part["total"] == 0:
            # Nothing cacheable expanded (conftest-only or vanished
            # selection): the runner's refusal below is the verdict, as
            # before the cache existed.
            result = runner.run(paths, checkout, log, workers=workers)
        elif not part["run"]:
            # Every selected file reuses a stored PASS on identical
            # inputs: no pytest to run, and nothing to record.
            with open(log, "w", encoding="utf-8") as handle:
                handle.write(
                    f"ren-ci result cache: reused "
                    f"{len(part['reused'])}/{part['total']} test files; "
                    f"inputs unchanged, no pytest executed\n")
            result = {"passed": part["reused_passed"], "failed": 0,
                      "ok": True, "exit": 0, "elapsed": 0.0,
                      "timed_out": False, "timeout_diagnostic": "",
                      "process_group_leaked": False,
                      "broker_leaked": False, "reason": "", "log": log}
        else:
            junit_path = log + ".junit.xml"
            raw = runner.run(part["run"], checkout, log, workers=workers,
                             junit_path=junit_path)
            result_cache.record_from_junit(
                world.state_dir, checkout, junit_path, part["run"],
                head=head, builder=builder,
                pytest_python=runner._pytest_python())
            result = dict(raw)
            # Reused files passed on identical inputs; the run's own
            # failures still fail the check.
            result["passed"] = raw["passed"] + part["reused_passed"]
        return {"pr": number, "head": head, "url": url,
                "ok": result["ok"], "passed": result["passed"],
                "failed": result["failed"], "elapsed": result["elapsed"],
                "log": result["log"], "selection": paths,
                "command": command,
                "selection_capped": capped["capped"],
                "selection_detail": capped,
                "cache_reused": cache_detail["reused"],
                "cache_total": cache_detail["total"],
                "broker_leaked": result.get("broker_leaked", False),
                "timed_out": result.get("timed_out", False),
                "reason": result.get("reason", ""),
                "timeout_diagnostic": result.get("timeout_diagnostic", ""),
                "process_group_leaked": result.get(
                    "process_group_leaked", False)}


def _report_line(outcome: dict) -> str:
    """The greppable report line plus the cache reuse it consulted.

    `reused N/M` names how many of the M expanded test files reused a
    stored PASS on identical inputs instead of executing.
    """
    line = runner.format_report(
        outcome["url"], outcome["head"], outcome["passed"],
        outcome["failed"], outcome["command"])
    if int(outcome.get("cache_total", 0) or 0) > 0:
        line += (f" [reused {int(outcome.get('cache_reused', 0) or 0)}/"
                 f"{int(outcome['cache_total'])}]")
    return line


def finish_tier1(world: queue.World, pr: dict, outcome: dict,
                 seen_at: float, slot: int) -> dict:
    """Record a Tier-1 outcome and post its verdict (main thread only).

    Returns the check record, carrying the latency the captain tunes
    against: seen (first observed) -> done. A red Tier-1 with the ready
    label loses the label: it cannot queue until it is green again.
    A TIMED-OUT Tier-1 judged nothing: it posts pending with the reason
    and re-queues the head automatically (up to
    config.TIER1_TIMEOUT_MAX_RETRIES retries with backoff), keeping the
    ready label and queue membership so the retry can still gate.
    """
    from library.tools.local_ci import github
    done_at = time.time()
    if outcome.get("timed_out"):
        prior = state.read_checks(world.state_dir).get(outcome["head"], {})
        retries = int(prior.get("timeout_retries", 0) or 0) + 1
        if retries <= config.TIER1_TIMEOUT_MAX_RETRIES:
            backoff = (config.TIER1_TIMEOUT_RETRY_BASE_SECONDS
                       * (2 ** (retries - 1)))
            record = state.record_check(
                {**outcome, "seen_at": seen_at, "done_at": done_at,
                 "tier1_latency": round(done_at - seen_at, 1), "slot": slot,
                 "timeout_retries": retries,
                 "timeout_retry_at": done_at + backoff,
                 "status_posted": False, "status_error": ""},
                world.state_dir)
            line = _report_line(outcome)
            record["report"] = line
            # record_check persisted the entry BEFORE the line existed;
            # the in-memory assignment above never reaches the file, so
            # persist it (the manual `ren ci check` path prints it off
            # the returned record, which is re-read from disk below).
            state.update_check(outcome["head"], {"report": line},
                               world.state_dir)
            print(f"{line} [timeout retry {retries}/"
                  f"{config.TIER1_TIMEOUT_MAX_RETRIES} in {backoff:.0f}s]",
                  flush=True)
            _publish_check_status(record, world.state_dir)
            return state.read_checks(world.state_dir).get(
                outcome["head"], record)
    extra: dict = {}
    if outcome.get("timed_out"):
        prior = state.read_checks(world.state_dir).get(outcome["head"], {})
        extra["timeout_retries"] = int(
            prior.get("timeout_retries", 0) or 0) + 1
    record = state.record_check(
        {**outcome, "seen_at": seen_at, "done_at": done_at,
         "tier1_latency": round(done_at - seen_at, 1), "slot": slot,
         **extra, "status_posted": False, "status_error": ""},
        world.state_dir)
    line = _report_line(outcome)
    record["report"] = line
    # See above: the line must reach the file, not just this dict.
    state.update_check(outcome["head"], {"report": line}, world.state_dir)
    print(line, flush=True)
    _publish_check_status(record, world.state_dir)
    record = state.read_checks(world.state_dir).get(outcome["head"], record)
    if not outcome["ok"]:
        state.remove_active_queue_entries(outcome["pr"], world.state_dir)
        if config.READY_LABEL in (pr.get("labels") or []):
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
    capped_suffix = " [capped, breadth to Tier-2]" if record.get(
        "selection_capped") else ""
    reuse_suffix = ""
    if int(record.get("cache_total", 0) or 0) > 0:
        reuse_suffix = (f", reused {int(record.get('cache_reused', 0) or 0)}/"
                        f"{int(record['cache_total'])}")
    # The five slow files Tier-1 skips on purpose, by short name: the
    # description truncates at 140 characters, so full paths live on
    # the report line and the slugs live here.
    tier2_suffix = (" [tier2-only to Tier-2: "
                    + ", ".join(tier2_only_slugs()) + "]")
    if record.get("static_failed"):
        first = record["static_failed"].splitlines()[0] or "failed"
        status, description = "failure", f"ren-ci static gate: {first}"
    elif record.get("ok"):
        status = "success"
        description = f"ren-ci: {record.get('passed', 0)} passed"
        description += capped_suffix + tier2_suffix + reuse_suffix
    elif record.get("timed_out") and int(
            record.get("timeout_retries", 0) or 0) <= (
                config.TIER1_TIMEOUT_MAX_RETRIES):
        reason = "timed out"
        diagnostic = record.get("timeout_diagnostic", "")
        if diagnostic:
            reason += f": {diagnostic}"
        retries = int(record.get("timeout_retries", 0) or 0)
        status, description = (
            "pending",
            f"ren-ci: {reason} - retrying "
            f"({retries}/{config.TIER1_TIMEOUT_MAX_RETRIES})")
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
        elif record.get("reason"):
            reason = record["reason"]
        else:
            reason = f"{record.get('failed', 1)} failed"
        status, description = "failure", f"ren-ci: {reason}{reuse_suffix}"
    target_url = record.get("url")
    try:
        parsed_target = urlsplit(target_url) if isinstance(
            target_url, str) else None
    except ValueError:
        parsed_target = None
    if (parsed_target is None
            or parsed_target.scheme not in ("http", "https")
            or not parsed_target.netloc):
        # Older ledgers stored the local run log as `url`. Keep retries
        # deliverable after an upgrade by replacing it with the PR page.
        target_url = github.pull_url(record["pr"])
    try:
        github.post_status(head, status, description[:140], target_url)
    except github.GitHubError as exc:
        state.update_check(head, {"status_posted": False,
                                  "status_error": str(exc),
                                  "url": target_url}, state_dir)
        print(f"ren-ci status delivery deferred for {head[:12]}: {exc}",
              flush=True)
        return False
    state.update_check(head, {"status_posted": True, "status_error": "",
                              "url": target_url}, state_dir)
    return True


def pass_once(world: queue.World, plan: dict | None = None) -> dict:
    """One detection+scheduling pass. Returns what it did."""
    from library.tools.local_ci import github
    plan = plan or load.targets(load.measure())
    done: dict = {"plan": load.describe(plan), "checked": [],
                  "queued": [], "refused": [], "queue": {}}
    due, checks = _discover_due(world, done)
    # No ripe-cluster deferral: Tier-1 keeps running while a Tier-2
    # cluster is ripe or testing. PRs 2287-2302 sat ready for hours
    # without a Tier-1 run - and so never entered the queue - because
    # a ripe batch (with chained bisects behind it) refused every new
    # head. Serialization is the heavy-work lock's job, not the
    # dispatcher's: runner.run refuses while a full-suite gate holds
    # the machine, and Tier-2 fires through the idle gate below once
    # running Tier-1 jobs drain. Tier-1 rounds are time-boxed, so a
    # ripe batch waits minutes for the drain, never hours for heads.
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
    if pr["number"] not in done["queued"]:
        done["queued"].append(pr["number"])


def _discover_due(world: queue.World, done: dict) -> tuple[list, dict]:
    """Read open heads and return only new, non-draft main-bound heads."""
    from library.tools.local_ci import github
    try:
        prs = github.list_open_prs()
    except github.GitHubError as exc:
        done["refused"].append(f"github unreachable: {exc}")
        return [], state.read_checks(world.state_dir)

    checks = state.read_checks(world.state_dir)
    due = []
    for pr in sorted(prs, key=lambda item: item["number"]):
        if pr.get("draft") or pr.get("base") != "main":
            continue
        seen_at = state.note_seen(pr["number"], pr["head"], world.state_dir)
        known = checks.get(pr["head"])
        if known and known.get("pr") == pr["number"]:
            # A timed-out Tier-1 judged nothing: re-queue the head while
            # retries remain and the backoff has elapsed. Past the retry
            # budget (or while backing off) it stays a known head, never
            # a silent re-run: backing off waits, exhausted stays red.
            if known.get("timed_out") and int(
                    known.get("timeout_retries", 0) or 0) <= (
                        config.TIER1_TIMEOUT_MAX_RETRIES):
                retry_at = float(known.get("timeout_retry_at", 0) or 0)
                if time.time() >= retry_at:
                    due.append((pr, seen_at))
                    continue
                if not known.get("status_posted", False):
                    if not _publish_check_status(known, world.state_dir):
                        done["refused"].append(
                            f"PR #{pr['number']}: timeout retry pending; "
                            "status delivery will retry next pass")
                        continue
                else:
                    done["refused"].append(
                        f"PR #{pr['number']}: timed out - "
                        "retrying after backoff")
                continue
            # Older daemon versions could mark an unparsed or empty
            # pytest summary green. Tier-1 always selects cross-cutting
            # tests, so a zero/zero success is not reusable evidence.
            if (known.get("ok") is True
                    and known.get("passed", 0) == 0
                    and known.get("failed", 0) == 0):
                due.append((pr, seen_at))
                continue
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
    return due, checks


class Tier1Scheduler:
    """Dispatch Tier-1 asynchronously and serialize result bookkeeping."""

    def __init__(self, world: queue.World,
                 max_workers: int = load.TIER1_MAX) -> None:
        self.world = world
        self._pool = concurrent.futures.ThreadPoolExecutor(
            max_workers=max(1, max_workers),
            thread_name_prefix="ren-ci-tier1")
        self._lock = threading.Lock()
        self._inflight: set[str] = set()
        self._futures: dict[concurrent.futures.Future, tuple] = {}

    def has_completed(self) -> bool:
        """Whether result bookkeeping should run before the next timer."""
        with self._lock:
            return any(future.done() for future in self._futures)

    def idle(self) -> bool:
        """Whether no Tier-1 worker is in flight (a reload may happen)."""
        with self._lock:
            return not self._futures and not self._inflight

    def _run(self, pr: dict, seen_at: float, workers: int,
             slots_limit: int) -> dict:
        slots = hygiene.Slots(self.world.state_dir, jobs=slots_limit)
        with slots.claim({"kind": "tier1", "pr": pr["number"],
                          "head": pr["head"]}) as slot:
            if slot is None:
                return {"slot": None, "problem": "no free slot"}
            try:
                outcome = run_tier1(self.world, pr, workers)
            except hygiene.HygieneRefused as exc:
                return {"slot": slot, "problem": str(exc)}
            except Exception as exc:  # noqa: BLE001 - retry daemon crashes
                return {"slot": slot, "crashed": str(exc)}
            return {"slot": slot, "outcome": outcome}

    def collect(self, done: dict) -> None:
        """Record completed worker results on this caller thread.

        Once a shutdown was requested, completed runs are discarded, not
        recorded: a run the shutdown killed judged nothing, and a
        failure verdict for it would be a verdict about the restart,
        not the PR. The head stays due for the replacement daemon.
        """
        with self._lock:
            completed = [(future, *self._futures[future])
                         for future in self._futures if future.done()]
        from library.tools.local_ci import github
        for future, pr, seen_at in completed:
            try:
                if _shutdown_requested:
                    done["refused"].append(
                        f"PR #{pr['number']}: daemon shutting down - "
                        "re-testing on restart")
                    continue
                try:
                    result = future.result()
                except Exception as exc:  # noqa: BLE001 - fail pending
                    result = {"slot": None, "crashed": str(exc)}
                slot = result.get("slot")
                if "outcome" in result:
                    outcome = result["outcome"]
                    record = finish_tier1(
                        self.world, pr, outcome, seen_at, slot)
                    done["checked"].append(pr["number"])
                    _maybe_enqueue(self.world, pr, record, done)
                elif "crashed" in result:
                    try:
                        github.post_status(
                            pr["head"], "pending",
                            "ren-ci: internal error, retrying next pass",
                            pr["url"])
                    except github.GitHubError:
                        pass
                    done["refused"].append(
                        f"PR #{pr['number']}: crashed: {result['crashed']}")
                else:
                    problem = result.get("problem", "check did not start")
                    done["refused"].append(
                        f"PR #{pr['number']}: {problem}")
                    if problem != "no free slot":
                        try:
                            github.post_status(
                                pr["head"], "pending",
                                f"ren-ci deferred: {problem}"[:140],
                                pr["url"])
                        except github.GitHubError:
                            pass
            finally:
                with self._lock:
                    self._futures.pop(future, None)
                    self._inflight.discard(pr["head"])

    def poll_once(self, plan: dict | None = None) -> dict:
        """Collect completed work, detect heads, and fill free slots."""
        plan = plan or load.targets(load.measure())
        done = {"plan": load.describe(plan), "checked": [], "started": [],
                "queued": [], "refused": [], "queue": {}}
        self.collect(done)
        due, _ = _discover_due(self.world, done)

        active_jobs = hygiene.running_jobs(self.world.state_dir)
        active_heads = {job.get("head") for job in active_jobs}
        active_slots = {job["slot"] for job in active_jobs}
        with self._lock:
            active_reserved = len(self._inflight - active_heads)
            in_flight = set(self._inflight)
        active_count = len(active_slots) + active_reserved
        target = min(max(1, int(plan["tier1_jobs"])), load.TIER1_MAX)
        capacity = max(0, target - active_count)
        # No ripe-cluster deferral here either (see pass_once): new
        # heads start even while a batch is ripe. The heavy-work lock
        # serializes execution - a Tier-1 pytest refuses while the gate
        # holds the machine - and the idle gate below fires Tier-2 once
        # these workers drain, so neither tier starves the other.
        from library.tools.local_ci import upstream
        queued = state.read_queue(self.world.state_dir)
        intake_idle = (not due and active_count == 0
                       and not hygiene.running_gate_jobs()
                       and not any(entry.get("status") == state.ENTRY_TESTING
                                   for entry in queued))
        periodic = upstream.run_due(self.world.state_dir, idle=intake_idle)
        if periodic.get("poll"):
            print("ren-ci upstream daily poll recorded", flush=True)
        if periodic.get("intake"):
            result = periodic["intake"]
            print(f"ren-ci upstream weekly intake: {result.get('status', 'failed')} "
                  f"record={result.get('record', '')}", flush=True)
        for pr, seen_at in sorted(due, key=lambda item: item[1]):
            head = pr["head"]
            if capacity <= 0:
                break
            if head in active_heads or head in in_flight:
                continue
            with self._lock:
                if head in self._inflight:
                    continue
                self._inflight.add(head)
                future = self._pool.submit(
                    self._run, pr, seen_at, int(plan["pytest_n"]), target)
                self._futures[future] = (pr, seen_at)
            in_flight.add(head)
            capacity -= 1
            done["started"].append(pr["number"])

        entries = state.read_queue(self.world.state_dir)
        running_jobs = hygiene.running_jobs(self.world.state_dir)
        gate_running = hygiene.gate_running()
        from library.tools.local_ci import gate_cleanup
        orphaned = gate_cleanup.find_own_orphaned_gate_pgids(
            self.world.state_dir)
        if orphaned:
            # A dead daemon's gate still runs: do not start a second
            # batch beside it and do not recover its testing entries.
            # Startup recovery reaps it; until then Tier-1 also yields.
            done["refused"].append(
                f"orphaned gate group(s) {orphaned} still running - "
                "awaiting reap")
        if (not self._futures and not running_jobs and not gate_running
                and not orphaned):
            recovered = state.recover_interrupted_queue(self.world.state_dir)
            if recovered:
                print(f"ren-ci recovered {recovered} interrupted Tier-2 "
                      "queue member(s); re-testing", flush=True)
            entries = state.read_queue(self.world.state_dir)
        if (not self._futures
                and not running_jobs
                and any(entry.get("status") in
                        (state.ENTRY_QUEUED, state.ENTRY_GREEN)
                        for entry in entries)
                and not any(entry.get("status") == state.ENTRY_TESTING
                            for entry in entries)
                and not gate_running
                and not orphaned):
            done["queue"] = queue.process_once(
                self.world, window=plan["window"])
        return done

    def shutdown(self, wait: bool = True) -> None:
        """Close test workers; used by deterministic scheduler tests."""
        self._pool.shutdown(wait=wait)


def check_pr(world: queue.World, pr: dict, slot: int,
             workers: int = 2) -> dict:
    """Run one PR's check end to end (manual `ren ci check` path)."""
    seen_at = state.note_seen(pr["number"], pr["head"], world.state_dir)
    outcome = run_tier1(world, pr, workers)
    return finish_tier1(world, pr, outcome, seen_at, slot)


# Set by the SIGTERM/SIGINT handler: a launchd restart must terminate
# the gate process groups this daemon started, never leave them running
# old code beside the replacement daemon (2026-10-10). Collect skips
# verdicts once set - a killed run judged nothing - and the serve loop
# exits, leaving testing entries for startup recovery.
_shutdown_requested = False


def shutdown_requested() -> bool:
    """Whether a termination signal asked this daemon to exit."""
    return _shutdown_requested


def install_shutdown_handlers() -> None:
    """Terminate tracked run groups on SIGTERM/SIGINT, then exit cleanly.

    The handler only records the signal and terminates the exact pgids
    runner._run_logged registered - never a name or pattern. It never
    raises: raising inside the admitting transaction could strand the
    machine grant. The serve loop sees the flag and exits without
    recording verdicts for killed runs.
    """
    import signal as _signal

    def _handle(signum, _frame) -> None:
        global _shutdown_requested
        _shutdown_requested = True
        try:
            from library.tools.local_ci import runner
            killed = runner.terminate_active_runs()
            if killed:
                print(f"ren-ci shutdown (signal {signum}): terminated "
                      f"run groups {sorted(killed)}", flush=True)
            else:
                print(f"ren-ci shutdown (signal {signum}): no runs active",
                      flush=True)
        except Exception as exc:  # noqa: BLE001 - shutdown must not crash
            print(f"ren-ci shutdown cleanup failed: {exc}", flush=True)

    for sig in (_signal.SIGTERM, _signal.SIGINT):
        try:
            _signal.signal(sig, _handle)
        except (OSError, ValueError):
            pass


def recover_orphaned_gates_on_startup(state_dir: str) -> dict:
    """Reap gates a dead daemon left behind, then recover their queue.

    Startup layer beside the shutdown handler (which covers SIGTERM):
    a SIGKILLed daemon never runs its handler, so its gate survives
    reparented to pid 1. Reap those exact pgids first - while one still
    runs its queue entries belong to it - then requeue testing entries
    whose gate is gone. Returns what was done.
    """
    from library.tools.local_ci import gate_cleanup

    summary: dict = {"reaped": {}, "recovered": 0}
    try:
        summary["reaped"] = gate_cleanup.reap_orphaned_gates(state_dir)
    except Exception as exc:  # noqa: BLE001 - startup must continue
        print(f"ren-ci orphan reap failed: {exc}", flush=True)
        summary["reaped"] = {}
    if summary["reaped"]:
        print(f"ren-ci reaped orphaned gate group(s) "
              f"{sorted(summary['reaped'])}", flush=True)
    try:
        summary["recovered"] = (
            gate_cleanup.recover_testing_entries_if_gate_gone(state_dir))
    except Exception as exc:  # noqa: BLE001 - startup must continue
        print(f"ren-ci testing recovery failed: {exc}", flush=True)
        summary["recovered"] = 0
    if summary["recovered"]:
        print(f"ren-ci recovered {summary['recovered']} interrupted Tier-2 "
              "queue member(s); re-testing", flush=True)
    return summary


def serve(state_dir: str = "", poll: int = config.POLL_SECONDS,
          repo: str = "") -> int:
    """Keep polling while Tier-1 checks run; return 0 only on clean exit.

    The daemon serves the given repo checkout: when that checkout moves
    to a commit that changes the daemon's own code, the loop re-execs
    onto the new tree while idle (self_update), so periodic upstream
    polling and intake never strand on stale code.
    """
    from pathlib import Path

    from library.tools.local_ci import upstream
    repo = repo or str(Path(__file__).resolve().parents[3])
    resolved_state = state_dir or config.state_dir()
    install_shutdown_handlers()
    recover_orphaned_gates_on_startup(resolved_state)
    upstream.recover_weekly(resolved_state)
    world = queue.World(state_dir=resolved_state)
    os.makedirs(os.path.join(world.state_dir, "logs"), exist_ok=True)
    running_sha = self_update.served_commit(repo)
    print(f"ren-ci serving {config.OWNER}/{config.REPO} "
          f"from {world.state_dir} "
          f"(checkout {repo} at "
          f"{running_sha[:12] if running_sha else 'unknown'})",
          flush=True)
    scheduler = Tier1Scheduler(world)
    next_poll = 0.0
    while True:
        if _shutdown_requested:
            # A termination signal already killed the tracked run
            # groups; exit without recording verdicts for them. Testing
            # entries stay testing on disk for the replacement daemon's
            # startup recovery - a killed run judged nothing.
            print("ren-ci shutting down on signal; runs terminated",
                  flush=True)
            scheduler.shutdown(wait=False)
            return 128 + 15
        now = time.monotonic()
        if now >= next_poll or scheduler.has_completed():
            running_sha = self_update.check_and_reload(
                scheduler, repo, running_sha, world.state_dir, poll)
            try:
                result = scheduler.poll_once(load.targets(load.measure()))
                print(f"ren-ci pass [{result['plan']}]: "
                      f"started={result['started']} "
                      f"checked={result['checked']} queued={result['queued']} "
                      f"refused={result['refused']} queue={result['queue']}",
                      flush=True)
                if result.get("queue", {}).get("merged"):
                    # Tier-2 just landed: the running image may be stale
                    # behind the new main, so offer the reload now while
                    # idle rather than waiting for the next poll window.
                    running_sha = self_update.check_and_reload(
                        scheduler, repo, running_sha, world.state_dir,
                        poll)
            except Exception as exc:  # noqa: BLE001 - polling must continue
                print(f"ren-ci pass failed: {exc}", flush=True)
            next_poll = time.monotonic() + poll
        wait = max(0.0, next_poll - time.monotonic())
        time.sleep(min(1.0, wait))
