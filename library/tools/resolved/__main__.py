"""`ren resolved serve|ensure|status|list|stop|submit|result|kpi` (see the package)."""

from __future__ import annotations

import argparse
import json
import os
import sys
import time

from library.tools.resolved import client


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="ren resolved",
        description="The broker in front of the one Resolve instance.")
    verbs = parser.add_subparsers(dest="verb", required=True)
    verbs.add_parser("serve", help="run the broker in the foreground")
    verbs.add_parser("ensure",
                     help="start the broker detached if none is serving")
    verbs.add_parser("status", help="is a broker serving, and what lease it holds")
    listing = verbs.add_parser("list", help="recent jobs and their receipts")
    listing.add_argument("--limit", type=int, default=20)
    verbs.add_parser("stop", help="ask the serving broker to exit")
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
        serve()
        return 0
    if args.verb == "kpi":
        from library.tools.resolved import kpi as kpi_report
        return kpi_report.main(args.hours, args.json, args.since, args.until)
    if args.verb == "status":
        answer = client.ping()
        print(json.dumps(_status(answer)))
        return 0 if answer is not None else 1
    if args.verb == "ensure":
        answer = client.ensure()
        print(json.dumps(_status(answer)))
        return 0 if answer is not None else 1
    try:
        if args.verb == "list":
            print(json.dumps(client.call({"op": "list",
                                           "limit": args.limit}), indent=2))
        elif args.verb == "stop":
            try:
                client.call({"op": "shutdown"})
            except ConnectionError:
                print("ren-resolved: no broker is serving - nothing to stop")
                return 1
            deadline = time.time() + 10.0
            while client.ping() is not None and time.time() < deadline:
                time.sleep(0.05)
            print("ren-resolved: stopped")
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
            print(json.dumps(client.result(args.id, args.wait), indent=2))
    except (ConnectionError, client.BrokerError) as exc:
        print(f"ren resolved: {exc}", file=sys.stderr)
        return 1
    return 0


def _status(answer: dict | None) -> dict:
    """The process status plus any lease holding Resolve right now."""
    result = ({"serving": False, "pid": None, "responsive": False,
               "busy": False, "lease": None} if answer is None else {
        "serving": answer.get("serving", True),
        "pid": answer.get("pid"),
        "responsive": answer.get("responsive", True),
        "busy": bool(answer.get("busy", False)),
        "lease": answer.get("lease"),
    })
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
    raise SystemExit(main())
