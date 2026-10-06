"""`ren computer-use <subcommand>`: the computer-use layer's front door.

One verb, one module, extended as the layer grows:

    status     what the driver can see right now - Resolve running, the
               main window, the frontmost app, the AX surface. Read
               only: every method it calls is on the driver's
               `READ_ONLY_METHODS` whitelist, so it runs against the
               captain's own project without touching it.

    run        the observe-decide-act loop on a task. Runs inside
               `qualification_project.borrowed()` once the broker is
               back; until then it is an offline tool against the mock
               driver.

    script     (PR 4) run a deterministic UI script.

The verb execs `python -m library.tools.computer_use` under the engine
interpreter (`ren/commands.py`, the one registry).
"""

from __future__ import annotations

import argparse
import sys

from library.tools.computer_use.client import DriverClient, DriverError
from library.tools.computer_use.loop import run_loop


def _status(client: DriverClient) -> int:
    windows = client.call("get_windows")["windows"]
    frontmost = client.call("get_frontmost_app")
    print(f"Resolve running: {bool(windows)}")
    for window in windows:
        if window.get("is_main"):
            frame = window["frame"]
            print(
                f"main window: {window['title']!r} "
                f"{frame['width']:.0f}x{frame['height']:.0f} "
                f"at ({frame['x']:.0f}, {frame['y']:.0f}), "
                f"backing scale {window['backing_scale']}"
            )
    print(f"frontmost app: {frontmost.get('name')} ({frontmost.get('bundle_id')})")
    tree = client.call("get_accessibility_tree", {"max_depth": 12})["tree"]

    def count(node) -> dict:
        counts: dict[str, int] = {}
        role = node.get("role")
        if role:
            counts[role] = 1
        for child in node.get("children") or []:
            for role, n in count(child).items():
                counts[role] = counts.get(role, 0) + n
        return counts

    roles = count(tree)
    print(
        f"AX surface: {sum(roles.values())} nodes: "
        + ", ".join(f"{role} x{n}" for role, n in sorted(roles.items()))
    )
    return 0


def _run(client: DriverClient, goal: str) -> int:
    """Run the observe-decide-act loop on a goal.

    The model is a stub for now - it returns `done` immediately. The
    production model sends the observation to a hosted model API (Claude
    Sonnet 5) or the local Gemma server and parses the response into an
    action. The loop, the observation and the action schema are the
    deliverable; the model is the pluggable half.

    The loop runs inside `qualification_project.borrowed()`: the
    captain's project is saved, the qualification project is opened for
    the driver's gates, and the captain's project is restored on exit.
    The exclusive instance lease is taken for the duration.
    """
    from library.tools.computer_use.observe import Observation
    from library.tools.marker_feedback import connect_resolve
    from library.tools.qualification_project import borrowed
    from library.tools.resolve_lock import resolve_lease
    from library.tools.resolved.jobs import QUALIFICATION_PROJECT

    def model(observation: Observation) -> dict:
        return {"action": "done"}

    resolve = connect_resolve()
    with resolve_lease(
        "computer-use", qualification_project=QUALIFICATION_PROJECT
    ), borrowed(resolve):
        result = run_loop(client, model, goal)
    print(f"outcome: {result.outcome}")
    for step in result.steps:
        print(f"  {step['action']}: {step['outcome']}")
    return 0 if result.outcome == "done" else 1


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="ren computer-use", description="Computer use above the native driver."
    )
    sub = parser.add_subparsers(dest="subcommand", required=True)
    sub.add_parser("status", help="what the driver can see right now (read only)")
    run_parser = sub.add_parser("run", help="run the observe-decide-act loop on a task")
    run_parser.add_argument("goal", help="the task for the loop to perform")
    args = parser.parse_args(argv)

    try:
        with DriverClient() as client:
            if args.subcommand == "status":
                return _status(client)
            if args.subcommand == "run":
                return _run(client, args.goal)
    except DriverError as exc:
        print(exc.render(), file=sys.stderr)
        return 4
    return 2


if __name__ == "__main__":
    sys.exit(main())
