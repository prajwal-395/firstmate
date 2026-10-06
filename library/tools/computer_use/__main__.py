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

`--target` selects the app (resolve today; canva reports its
declaration and gate, and refuses `run` - no Chrome executor exists
yet). The verb execs `python -m library.tools.computer_use` under the engine
interpreter (`ren/commands.py`, the one registry).
"""

from __future__ import annotations

import argparse
import sys

from library.tools.computer_use.client import DriverClient
from library.tools.computer_use.loop import run_loop
from library.tools.computer_use.model import ENV_URL, ModelConfig, ModelSession
from library.tools.computer_use.targets import (
    AppTarget,
    canva_scratch_designs,
    require_executor,
    select_target,
)
from library.tools.ren_refusal import RenRefusal


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


def _run(
    client: DriverClient, goal: str, model_kind: str, max_cost_usd: float | None
) -> int:
    """Run the observe-decide-act loop on a goal.

    `--model stub` (the default) returns `done` immediately, for
    offline work against the mock driver. `--model api` decides each
    step through `ModelSession`: the observation goes to the
    OpenAI-compatible endpoint in `$COMPUTER_USE_MODEL_URL` (default:
    the resident local server) and the reply parses into one action,
    with token usage in the perf ledger and the dollar budget enforced.
    `$COMPUTER_USE_MODEL_NAME` selects the model;
    `--max-cost-usd` (or `$COMPUTER_USE_MAX_COST_USD`) caps the spend.

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

    if model_kind == "api":
        config = ModelConfig.from_env()
        if max_cost_usd is not None:
            config.max_cost_usd = max_cost_usd
        model = ModelSession(config=config)
    else:

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


def _canva_status(target: AppTarget) -> int:
    """What the Canva target is and what it may touch - no driver claims.

    There is no Chrome executor, so this reports the declaration: the
    window match, the scratch-design gate state, and the background
    capability with its reason. Read-only: no driver spawns.
    """
    designs = canva_scratch_designs()
    print(f"target: {target.name} ({target.bundle_id}, {target.title_contains!r} windows)")
    print(f"executor: none - the Chrome input route is unbuilt")
    print(f"scratch designs allowlisted: {len(designs)}")
    print(f"background input: {'yes' if target.background_input else 'no'} - {target.background_why}")
    print(f"gate: {target.gate}")
    return 0


def _target_flag(parser) -> None:
    """The `--target` flag, on every subcommand (options follow the verb)."""
    parser.add_argument(
        "--target",
        choices=("resolve", "canva"),
        default="resolve",
        help="which app the loop may act on (default: resolve)",
    )


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="ren computer-use", description="Computer use above the native driver."
    )
    sub = parser.add_subparsers(dest="subcommand", required=True)
    status_parser = sub.add_parser(
        "status", help="what the driver can see right now (read only)"
    )
    _target_flag(status_parser)
    run_parser = sub.add_parser("run", help="run the observe-decide-act loop on a task")
    run_parser.add_argument("goal", help="the task for the loop to perform")
    _target_flag(run_parser)
    run_parser.add_argument(
        "--model",
        choices=("stub", "api"),
        default="stub",
        help="stub returns done immediately (offline default); api decides "
        f"each step through the endpoint in ${ENV_URL}",
    )
    run_parser.add_argument(
        "--max-cost-usd",
        type=float,
        default=None,
        help="cap the decide calls' spend (overrides $COMPUTER_USE_MAX_COST_USD)",
    )
    args = parser.parse_args(argv)

    try:
        target = select_target(args.target)
        if args.subcommand == "status":
            if target.name == "canva":
                return _canva_status(target)
            with DriverClient() as client:
                return _status(client)
        if args.subcommand == "run":
            require_executor(target)
            with DriverClient() as client:
                return _run(client, args.goal, args.model, args.max_cost_usd)
    except RenRefusal as exc:
        print(exc.render(), file=sys.stderr)
        return 4
    return 2


if __name__ == "__main__":
    sys.exit(main())
