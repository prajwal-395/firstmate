"""`ren resolved serve|status|list|stop|submit|result|kpi` (see the package)."""

from __future__ import annotations

import argparse
import json
import sys

from library.tools.resolved import client


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="ren resolved",
        description="The broker in front of the one Resolve instance.")
    verbs = parser.add_subparsers(dest="verb", required=True)
    verbs.add_parser("serve", help="run the broker in the foreground")
    verbs.add_parser("status", help="is a broker serving, and on which pid")
    listing = verbs.add_parser("list", help="recent jobs and their receipts")
    listing.add_argument("--limit", type=int, default=20)
    verbs.add_parser("stop", help="ask the serving broker to exit")
    submit = verbs.add_parser("submit", help="queue one job; prints its id")
    submit.add_argument("kind")
    submit.add_argument("params", nargs="?", default="{}",
                        help="the job's parameters, as a JSON object")
    submit.add_argument("--qualification", action="store_true",
                        help="a test job: only the qualification project")
    submit.add_argument("--wait", type=float, default=0.0,
                        help="wait this long for the receipt")
    result = verbs.add_parser("result", help="a job's receipt")
    result.add_argument("id")
    result.add_argument("--wait", type=float, default=0.0)
    kpi = verbs.add_parser("kpi", help="what Resolve cost, off the receipts")
    kpi.add_argument("--hours", type=float, default=24.0,
                     help="the window, ending now (default 24)")
    kpi.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    if args.verb == "serve":
        from library.tools.resolved.server import serve
        serve()
        return 0
    if args.verb == "kpi":
        from library.tools.resolved import kpi as kpi_report
        return kpi_report.main(args.hours, args.json)
    if args.verb == "status":
        answer = client.ping()
        print(json.dumps({"serving": answer is not None,
                          "pid": answer and answer.get("pid")}))
        return 0 if answer is not None else 1
    try:
        if args.verb == "list":
            print(json.dumps(client.call({"op": "list",
                                          "limit": args.limit}), indent=2))
        elif args.verb == "stop":
            client.call({"op": "shutdown"})
        elif args.verb == "submit":
            submitted = client.submit(args.kind, json.loads(args.params),
                                      qualification=args.qualification)
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


if __name__ == "__main__":
    raise SystemExit(main())
