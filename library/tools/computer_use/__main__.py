"""`ren computer-use <subcommand>`: the computer-use layer's front door.

One verb, one module, extended as the layer grows:

    status     what the driver can see right now - Resolve running, the
               main window, the frontmost app, the AX surface. Read
               only: every method it calls is on the driver's
               `READ_ONLY_METHODS` whitelist, so it runs against the
               captain's own project without touching it.

    run        (PR 3) the observe-decide-act loop on a task.
    script     (PR 4) run a deterministic UI script.

The verb execs `python -m library.tools.computer_use` under the engine
interpreter (`ren/commands.py`, the one registry).
"""

from __future__ import annotations

import argparse
import sys

from library.tools.computer_use.client import DriverClient, DriverError


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


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="ren computer-use", description="Computer use above the native driver."
    )
    sub = parser.add_subparsers(dest="subcommand", required=True)
    sub.add_parser("status", help="what the driver can see right now (read only)")
    args = parser.parse_args(argv)

    try:
        with DriverClient() as client:
            if args.subcommand == "status":
                return _status(client)
    except DriverError as exc:
        print(exc.render(), file=sys.stderr)
        return 4
    return 2


if __name__ == "__main__":
    sys.exit(main())
