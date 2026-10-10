"""`ren ci ...` / `python3 -m library.tools.local_ci ...`: the operator CLI.

- status: queue, running slots, last check results (read only).
- check <pr>: run one PR's check now, in a slot, posting the status.
- ready <pr>: mark a PR ready for the queue (adds the label).
- process-once: one queue pass (test + merge in order), for the
  throwaway-PR proof and for manual operation before install.
- daemon: serve until killed (what launchd runs).
- install / uninstall: the launchd agent (install only after merge).

Standard library only: status must work before any environment exists.
"""

from __future__ import annotations

import argparse
import os
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)


def cmd_status(args) -> int:
    from library.tools.local_ci import config, hygiene, state
    print(f"ren-ci state: {args.state or config.state_dir()}")
    entries = state.read_queue(args.state)
    if entries:
        print("queue (oldest first):")
        for entry in entries:
            reason = f" - {entry['reason']}" if entry.get("reason") else ""
            latency = ""
            if entry.get("merged_at") and entry.get("enqueued_at"):
                wait = entry["merged_at"] - entry["enqueued_at"]
                latency = f" ready->merged {wait:.0f}s"
            print(f"  PR #{entry['pr']} {entry['head'][:12]} "
                  f"{entry['status']}{latency}{reason}")
    else:
        print("queue: empty")
    running = hygiene.running_jobs(args.state)
    gates = hygiene.running_gate_jobs()
    if running or gates:
        print("running jobs:")
        for job in running:
            identity = f"PR #{job['pr']}" if job.get("pr") else "job"
            if job.get("head"):
                identity += f" {job['head'][:12]}"
            print(f"  {job.get('kind', 'check')} {identity} "
                  f"slot {job['slot']} {job['elapsed_seconds']:.0f}s "
                  f"pid {job['pid']}")
        testing = [entry for entry in entries
                   if entry.get("status") == state.ENTRY_TESTING]
        members = ", ".join(f"#{entry['pr']}" for entry in testing)
        for job in gates:
            identity = "Tier-2 full-suite gate"
            if members:
                identity += f" for PRs {members}"
            print(f"  {identity} {job['elapsed_seconds']:.0f}s "
                  f"pid {job['pid']}")
    else:
        print("running jobs: none")
    checks = state.read_checks(args.state)
    if checks:
        print("last checks (seen->result latency):")
        for head, record in sorted(checks.items(),
                                   key=lambda kv: kv[1].get("recorded_at", 0),
                                   reverse=True)[:10]:
            verdict = "PASS" if record.get("ok") else "FAIL"
            latency = record.get("tier1_latency")
            latency_s = f" {latency:.0f}s" if latency is not None else ""
            delivery = ("status posted" if record.get("status_posted")
                        else "status pending")
            print(f"  PR #{record.get('pr')} {head[:12]} {verdict} "
                  f"{record.get('passed', 0)} passed, "
                  f"{record.get('failed', 0)} failed{latency_s} "
                  f"{delivery}")
            if record.get("url") or record.get("log"):
                print(f"    {record.get('url', '')}"
                      f"{(' | log ' + record['log']) if record.get('log') else ''}")
            if record.get("status_error"):
                print(f"    status delivery error: {record['status_error']}")
    else:
        print("checks: none recorded")
    from library.tools.local_ci import load, service
    plan = load.targets(load.measure())
    print(f"dispatch now: {load.describe(plan)}")
    if service.installed():
        agent_state = f"installed, {service.launchd_state()}"
    else:
        agent_state = "not installed"
    print(f"agent: {agent_state} ({service.plist_path()})")
    from library.tools.local_ci import upstream
    print("upstream intake pins:")
    for name, pin in upstream.pin_status(args.state).items():
        state_label = (f"stale candidate {pin['candidate_sha'][:12]}"
                       if pin.get("candidate_sha") else "current")
        print(f"  {name}: qualified={pin['qualified_sha'][:12]} "
              f"last-seen={pin['last_seen_upstream_sha'][:12]} {state_label}")
    return 0


def cmd_check(args) -> int:
    from library.tools.local_ci import daemon, github, hygiene, load, queue
    world = queue.World(state_dir=args.state or "")
    plan = load.targets(load.measure())
    slots = hygiene.Slots(world.state_dir, jobs=plan["tier1_jobs"])
    with slots.claim({"kind": "manual-check", "pr": args.pr}) as slot:
        if slot is None:
            print("ren-ci: no free slot - the check waits for the daemon")
            return 2
        pr = github.get_pr(args.pr)
        record = daemon.check_pr(world, pr, slot, workers=plan["pytest_n"])
        print(record.get("report", ""))
        return 0 if record.get("ok") else 1


def cmd_ready(args) -> int:
    from library.tools.local_ci import github
    github.set_label(args.pr, __ready_label())
    print(f"PR #{args.pr} marked ready for the ren-ci queue")
    return 0


def __ready_label() -> str:
    from library.tools.local_ci import config
    return config.READY_LABEL


def cmd_process_once(args) -> int:
    from library.tools.local_ci import load, queue
    world = queue.World(state_dir=args.state or "")
    plan = load.targets(load.measure())
    summary = queue.process_once(world, window=plan["window"])
    print(f"ripe={summary['ripe']} waiting={summary['waiting']} "
          f"merged={summary['merged']} kicked={summary['kicked']} "
          f"requeued={summary['requeued']}")
    return 0 if not summary["kicked"] else 1


def cmd_daemon(args) -> int:
    from library.tools.local_ci import daemon
    daemon.serve(args.state or "", poll=args.poll, repo=args.repo or "")
    return 0


def cmd_install(args) -> int:
    from library.tools.local_ci import service
    path = service.install(args.repo or os.getcwd())
    print(f"ren-ci agent installed: {path}")
    return 0


def cmd_uninstall(_args) -> int:
    from library.tools.local_ci import service
    service.uninstall()
    print("ren-ci agent uninstalled (state and logs kept)")
    return 0


def cmd_upstream(args) -> int:
    from library.tools.local_ci import upstream
    if args.action == "status":
        for name, pin in upstream.pin_status(args.state).items():
            print(f"{name}: {pin}")
        return 0
    request = upstream.request_expedited(args.engine, args.reason, args.state)
    print(f"ren-ci expedited upstream intake queued: {request}")
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="ren ci")
    parser.add_argument("--state", default="",
                        help="state dir override (default ~/.ren-ci)")
    sub = parser.add_subparsers(dest="verb", required=True)
    sub.add_parser("status", help="queue, slots, last results (read only)")
    check = sub.add_parser("check", help="run one PR's check now")
    check.add_argument("pr", type=int)
    ready = sub.add_parser("ready", help="mark a PR ready for the queue")
    ready.add_argument("pr", type=int)
    sub.add_parser("process-once", help="one queue pass: test + merge")
    daemon_p = sub.add_parser("daemon", help="serve until killed")
    daemon_p.add_argument("--repo", default="")
    daemon_p.add_argument("--poll", type=int, default=30)
    install = sub.add_parser("install", help="install the launchd agent")
    install.add_argument("--repo", default="")
    sub.add_parser("uninstall", help="remove the launchd agent")
    upstream_p = sub.add_parser("upstream", help="upstream engine intake")
    upstream_sub = upstream_p.add_subparsers(dest="action", required=True)
    upstream_sub.add_parser("status")
    expedite = upstream_sub.add_parser("expedite")
    expedite.add_argument("engine", choices=("filmcraft", "effectcraft"))
    expedite.add_argument("--reason", required=True)
    args = parser.parse_args(argv)
    commands = {"status": cmd_status, "check": cmd_check,
                "ready": cmd_ready, "process-once": cmd_process_once,
                "daemon": cmd_daemon, "install": cmd_install,
                "uninstall": cmd_uninstall, "upstream": cmd_upstream}
    return commands[args.verb](args)


if __name__ == "__main__":
    raise SystemExit(main())
