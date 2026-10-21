"""`ren resolved serve|ensure|status|list|stop|submit|result|kpi` (see the package)."""

from __future__ import annotations

import argparse
import json
import os
import sys
import time

from library.tools.resolved import client, queue_status

#: How many recent jobs the queue report reads. The queued jobs are the
#: ones still waiting, so they must all be inside the window - a short
#: one would leave the queue looking empty while jobs sit in it.
QUEUE_LOOKBACK = 500


def main(argv=None) -> int:
    # Same default as the front door, for direct `-m` use: under a
    # harness the host parses `REN_RESULT` instead of prose (LE-03).
    # A `ren` verb already set it through the environment, and an
    # explicit value always wins.
    from ren import hosts
    hosts.ensure_harness_output_env()
    parser = argparse.ArgumentParser(
        prog="ren resolved",
        description="The broker in front of the one Resolve instance.")
    verbs = parser.add_subparsers(dest="verb", required=True)
    serve = verbs.add_parser("serve", help="run the broker in the foreground")
    serve.add_argument(
        "--socket",
        help="serve on this socket instead of the machine's; a "
             "deliberately private broker (the qualification harness), "
             "never a second broker for the one Resolve")
    verbs.add_parser("ensure",
                     help="start the broker detached if none is serving")
    verbs.add_parser("status", help="is a broker serving, and what lease it holds")
    listing = verbs.add_parser("list", help="recent jobs and their receipts")
    listing.add_argument("--limit", type=int, default=20)
    listing.add_argument(
        "--text", action="store_true",
        help="print the queue as text: every waiting job's position, how "
             "long it has waited and what it is waiting for")
    verbs.add_parser("stop", help="ask the serving broker to exit")
    verbs.add_parser("return-to-ren",
                     help="clear the human Resolve handoff fence")
    submit = verbs.add_parser("submit", help="queue one job; prints its id")
    submit.add_argument("kind")
    submit.add_argument("params", nargs="?", default="{}",
                        help="the job's parameters, as a JSON object")
    submit.add_argument("--qualification", action="store_true",
                        help="a test job: only the qualification project")
    submit.add_argument("--idempotency-key",
                        help="reuse this key to return the same job receipt")
    submit.add_argument("--wait", type=float, default=0.0,
                        help="wait this long for the receipt")
    result = verbs.add_parser("result", help="a job's receipt")
    result.add_argument("id")
    result.add_argument("--wait", type=float, default=0.0)
    result.add_argument("--until-done", action="store_true",
                        help="keep waiting until the job reaches a terminal "
                             "state (done, failed, rejected, cancelled), or "
                             "--deadline seconds pass")
    result.add_argument("--deadline", type=float, default=300.0,
                        help="how long --until-done waits, in seconds")
    kpi = verbs.add_parser("kpi", help="what Resolve cost, off the receipts")
    window = kpi.add_mutually_exclusive_group()
    window.add_argument("--hours", type=float,
                        help="the window ending at --until (default 24)")
    window.add_argument("--since", type=float,
                        help="inclusive window start as Unix epoch seconds")
    kpi.add_argument("--until", type=float,
                     help="inclusive window end as Unix epoch seconds")
    kpi.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    if args.verb == "serve":
        from library.tools.resolved.server import serve
        serve(path=args.socket)
        return 0
    if args.verb == "kpi":
        from library.tools.resolved import kpi as kpi_report
        return kpi_report.main(args.hours, args.json, args.since, args.until)
    if args.verb == "status":
        answer = client.ping()
        payload = _status(answer)
        if answer is not None:
            payload["queue"] = _queue_report()
        print(json.dumps(payload))
        return 0 if answer is not None else 1
    if args.verb == "list" and args.text:
        report = _queue_report()
        if not report.get("available", True):
            print(f"ren resolved: the queue could not be read: "
                  f"{report.get('error', 'unknown')}", file=sys.stderr)
            return 1
        print(queue_status.render(report))
        return 0
    if args.verb == "ensure":
        answer = client.ensure()
        print(json.dumps(_status(answer)))
        return 0 if answer is not None else 1
    try:
        if args.verb == "list":
            reply = client.call({"op": "list", "limit": args.limit})
            reply["queue"] = queue_status.queue_report(
                {"jobs": reply.get("jobs") or [],
                 "running": reply.get("running") or []},
                lease=_active_lease())
            print(json.dumps(reply, indent=2))
        elif args.verb == "stop":
            # The production broker is one instance serving every lane:
            # a linked worktree must never stop it (a broker started
            # there would serve production Resolve calls on unmerged
            # code, and stopping it takes every lane's Resolve with
            # it), and neither must the test suite (its brokers live
            # on private sockets). Both refusals are judged by the
            # TARGET, like `ensure`'s: stopping a private-socket broker
            # from anywhere stays allowed.
            target = client._path(None)
            if client._is_default_socket(target) and (
                    client._in_pytest()
                    or client._is_linked_worktree(client._engine_root())):
                print("ren-resolved: refusing to stop the production "
                      "broker from here - run from the installed engine "
                      "checkout; a linked worktree stops only the broker "
                      "on its own socket",
                      file=sys.stderr)
                return 1
            try:
                client.call({"op": "shutdown"})
            except ConnectionError:
                print("ren-resolved: no broker is serving - nothing to stop")
                return 1
            deadline = time.time() + 10.0
            while client.ping() is not None and time.time() < deadline:
                time.sleep(0.05)
            print("ren-resolved: stopped")
        elif args.verb == "return-to-ren":
            print(json.dumps(client.call({"op": "return_to_ren"}),
                             indent=2))
        elif args.verb == "submit":
            submitted = client.submit(args.kind, json.loads(args.params),
                                      qualification=args.qualification,
                                      idempotency_key=args.idempotency_key)
            if args.wait:
                print(json.dumps(client.result(submitted["id"], args.wait),
                                 indent=2))
            else:
                print(json.dumps(submitted))
        else:
            print(json.dumps(
                client.result(args.id, args.wait,
                              until_done=args.until_done,
                              deadline=args.deadline),
                indent=2))
    except (ConnectionError, client.BrokerError) as exc:
        print(f"ren resolved: {exc}", file=sys.stderr)
        return 1
    return 0


def _active_lease() -> dict | None:
    """The instance lease's holder, or None where nothing holds it.

    Read by the FLOCK, not by the record: a record with no flock behind
    it names a holder that is gone.
    """
    from dataclasses import asdict

    from library.tools.resolve_lock import lease_status

    lease = lease_status()
    return asdict(lease.active) if lease.active is not None else None


def _queue_report() -> dict:
    """The broker's queue: positions, wait causes, blocked resources.

    Read from the broker's own `list` reply, over a window wide enough
    to hold every job still waiting - a queued job that fell outside it
    would make the queue look emptier than it is.

    A broker that cannot be asked is reported, never raised: the queue
    is extra information about a broker that IS serving, and a status
    that failed to print it would fail to print the status.
    """
    from library.tools.resolved import queue_status
    try:
        reply = client.call({"op": "list", "limit": QUEUE_LOOKBACK},
                             timeout=5.0)
    except (ConnectionError, client.BrokerError, OSError) as exc:
        return {"available": False, "error": str(exc)}
    report = queue_status.queue_report(
        {"jobs": reply.get("jobs") or [],
         "running": reply.get("running") or []},
        lease=_active_lease())
    report["available"] = True
    return report


def _status(answer: dict | None) -> dict:
    """The process status plus any lease holding Resolve right now.

    The lease is answered by the FLOCK, never by the record alone: a
    killed holder leaves its record behind, and reading that record as
    "busy" called a free instance busy for hours on 2026-10-20. A record
    with no flock behind it is reported as stale - the instance is free,
    and the record names a holder that is gone. The record is never
    unlinked from here: another writer may already be publishing its own.
    """
    from dataclasses import asdict

    from library.tools.resolve_lock import lease_status

    lease = lease_status()
    result = {
        "serving": False if answer is None else answer.get("serving", True),
        "pid": None if answer is None else answer.get("pid"),
        "responsive": (False if answer is None
                       else answer.get("responsive", True)),
        "busy": lease.busy,
        "lease": (asdict(lease.active)
                  if lease.active is not None else None),
        "stale_lease": (asdict(lease.recorded)
                        if lease.stale else None),
    }
    if answer is None:
        result["binary"] = "none"
        result["rebuild"] = "not applicable"
        return result
    pid = result.get("pid")
    if pid:
        try:
            command = __import__("subprocess").run(
                ["ps", "-p", str(pid), "-o", "command="],
                capture_output=True, encoding="utf-8", timeout=2,
                check=False).stdout.strip()
            result["binary"] = command or "unknown (process exited)"
        except Exception as exc:  # noqa: BLE001 - status remains readable
            result["binary"] = f"unknown ({exc})"
    else:
        result["binary"] = "none"
    try:
        from library.tools import native_refresh
        from library.tools.resolved.client import _engine_root
        root = _engine_root()
        if root is not None:
            state, detail = native_refresh.status(root, "ren-resolved")
            result["rebuild"] = ("pending" if state != "fresh" else "none")
            result["native"] = f"{state}: {detail}"
    except Exception as exc:  # status remains useful without native metadata
        result["native"] = f"unavailable: {exc}"
    return result


if __name__ == "__main__":
    # ONE place renders every refusal on this verb's path, in the same
    # shape as the front door. With `REN_OUTPUT=json` both a refusal
    # and an uncaught error end with one final `REN_RESULT` line (LE-03).
    from library.tools.ren_refusal import (
        REFUSAL_EXIT_CODE, RenRefusal, emit_refusal_result,
        report_unexpected)
    try:
        raise SystemExit(main())
    except RenRefusal as refused:
        print(refused.render(), file=sys.stderr)
        emit_refusal_result(refused)
        sys.exit(REFUSAL_EXIT_CODE)
    except Exception as exc:  # noqa: BLE001 - unexpected errors are data too
        sys.exit(report_unexpected(exc))
