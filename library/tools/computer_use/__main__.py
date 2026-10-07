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
               driver. `--recipe NAME` executes the stored recipe first
               and plans only where no recipe runs.

    script     run one deterministic route with no model: either a
                script FILE the mapping runs wrote (anchors re-resolve
                at run time; `--teach` writes LLM-taught anchors back
                to the file), or one NAMED recipe (`--recipe`) whose
                builder carries the measured route. Recipe steps that
                must land on Qt-drawn controls need `--allow-foreground`:
                background input changes nothing there (measured), so
                the flag posts at the HID tap and activates Resolve
                first - explicitly, never by default, because it takes
                the captain's screen.

    recipe-save  name a verified script file as a recipe for later
               runs: `recipe-save NAME --script script.json
               [--layout LAYOUT]`.

`--target` selects the app (resolve today; canva reports its
declaration and gate, and refuses `run` - no Chrome executor exists
yet). The verb execs `python -m library.tools.computer_use` under the engine
interpreter (`ren/commands.py`, the one registry).
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import sys
from pathlib import Path

from library.tools.computer_use.client import DriverClient
from library.tools.computer_use.loop import run_loop
from library.tools.computer_use.model import ENV_URL, ModelConfig, ModelSession
from library.tools.computer_use.observe import Observation
from library.tools.computer_use.scripts import (
    Script,
    ScriptStep,
    load_script,
    run_script,
)
from library.tools.computer_use.targets import (
    AppTarget,
    TargetError,
    canva_scratch_designs,
    require_executor,
    select_target,
)
from library.tools.ren_refusal import RenRefusal

#: App name to driver target: the loop observes and acts through the
#: driver's window with this name. Resolve is itself; Canva is the
#: Chrome window (a web app has no other handle).
DRIVER_TARGETS = {"resolve": "resolve", "canva": "chrome"}


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
    client: DriverClient,
    goal: str,
    model_kind: str,
    max_cost_usd: float | None,
    recipe: str | None = None,
    recipes_dir: str | None = None,
    layout: str | None = None,
    target_name: str = "resolve",
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

    `--recipe NAME` executes the stored recipe first: a committed
    recipe ends the run without the model ever being asked, and an
    unknown name, a moved layout or a failed recipe falls through to
    the planner. `--recipes-dir` overrides `$PIPELINE_RECIPES_DIR`;
    `--layout` is the opaque layout string the recipe was verified on.

    On resolve the loop runs inside `qualification_project.borrowed()`:
    the captain's project is saved, the qualification project is opened
    for the driver's gates, and the captain's project is restored on
    exit. The exclusive instance lease is taken for the duration - a
    plain lease, not the broker's qualification interlock, which refuses
    before `borrowed()` can open the project whenever the captain's
    project is open (measured 2026-10-06: the interlock granted nothing
    with 'Podcast (field test)' open). `borrowed()` is the guarantee.
    On canva there is no Resolve to borrow and no lease to take: the
    loop observes the Chrome window and every act goes through
    verify-or-escalate (clicks measured filtered - a click that changes
    nothing fails its step loudly).
    """
    from library.tools.computer_use.observe import Observation
    from library.tools.marker_feedback import connect_resolve
    from library.tools.qualification_project import borrowed
    from library.tools.resolve_lock import resolve_lease

    if model_kind == "api":
        config = ModelConfig.from_env()
        if max_cost_usd is not None:
            config.max_cost_usd = max_cost_usd
        model = ModelSession(config=config)
    else:

        def model(observation: Observation) -> dict:
            return {"action": "done"}

    driver_target = DRIVER_TARGETS[target_name]
    store = None
    if recipe is not None:
        import os
        from pathlib import Path

        from library.tools.computer_use.scripts import (
            RECIPES_ROOT_ENV,
            RecipeStore,
            default_recipes_root,
        )

        override = recipes_dir or os.environ.get(RECIPES_ROOT_ENV, "")
        root = Path(override).expanduser() if override else default_recipes_root()
        store = RecipeStore(root)
    if target_name == "resolve":
        with resolve_lease("computer-use"):
            resolve = connect_resolve()
            with borrowed(resolve):
                result = run_loop(client, model, goal, recipe=recipe,
                                  store=store, layout=layout,
                                  target=driver_target)
    else:
        if recipe is not None:
            raise TargetError(
                "recipes drive Resolve anchors, not canva",
                "a stored recipe's anchors resolve against Resolve's window",
                "run with --target resolve, or drop --recipe and let the loop plan",
                code="TARGET_RECIPE_ONLY_RESOLVE",
            )
        pid = _canva_window_pid(client, target_name)
        result = run_loop(client, model, goal, target=driver_target, pid=pid)
    print(f"outcome: {result.outcome}")
    for step in result.steps:
        print(f"  {step['action']}: {step['outcome']}")
    return 0 if result.outcome == "done" else 1


def _run_recipe(client: DriverClient, args) -> int:
    """Run one named recipe's Script, deterministically, no model.

    Inside `qualification_project.borrowed()` under the exclusive
    lease, like `run`: the captain's project is saved, Ren
    Qualification is opened and read back by name, the named timeline
    is made current, the playhead is parked where the caller measured
    it, and the captain's project is restored on exit - also when a
    step fails. Each step is timed; the report names the step, its
    anchor, its outcome and its seconds, which is the live evidence a
    recipe's promotion is judged on.
    """
    import json
    import time

    from library.tools import magic_mask
    from library.tools.mask_seed import MaskSeed, Point

    scale: float | None = None
    if args.recipe == "magic-mask-seed":
        if not args.seed:
            print("ren: magic-mask-seed needs --seed, the MaskSeed "
                  "receipt as JSON", file=sys.stderr)
            return 2
        if not args.timeline:
            print("ren: magic-mask-seed needs --timeline, the Ren "
                  "Qualification timeline by exact name", file=sys.stderr)
            return 2
        try:
            raw = json.loads(args.seed)
            seed = MaskSeed(
                clip_id=raw["clip_id"], frame=int(raw["frame"]),
                subject=raw["subject"],
                source=Point(float(raw["source"]["x"]),
                             float(raw["source"]["y"])),
                viewer=Point(float(raw["viewer"]["x"]),
                             float(raw["viewer"]["y"])),
                object_id=raw["object_id"],
                sample_index=int(raw["sample_index"]),
                seed_frame=int(raw["seed_frame"]))
        except (ValueError, KeyError, TypeError) as exc:
            print(f"ren: --seed is a MaskSeed receipt as JSON: {exc}",
                  file=sys.stderr)
            return 2
        try:
            scale = _main_window_scale(client)
        except (KeyError, TypeError, ValueError) as exc:
            print(f"ren: the main window's backing scale did not read "
                  f"back: {exc}", file=sys.stderr)
            return 1
        if scale is None:
            print(f"ren: no main window reported a backing scale - "
                  f"cannot place the seed click", file=sys.stderr)
            return 1
        try:
            script = magic_mask.seed_script(
                seed, mode=args.mode, scale_factor=scale)
        except ValueError as exc:
            print(f"ren: {exc}", file=sys.stderr)
            return 2
    elif args.recipe == "magic-mask-track":
        if not args.timeline:
            print("ren: magic-mask-track needs --timeline, the Ren "
                  "Qualification timeline by exact name", file=sys.stderr)
            return 2
        if args.seed is not None:
            print("ren: magic-mask-track takes no --seed - a seed no "
                  "builder reads would run the wrong route", file=sys.stderr)
            return 2
        try:
            script = magic_mask.track_script(args.direction)
        except ValueError as exc:
            print(f"ren: {exc}", file=sys.stderr)
            return 2
    elif args.recipe == "window-delete":
        from library.tools.computer_use import window_delete

        return window_delete.run_recipe_verb(client, args)
    elif args.recipe == "effect-panels":
        if not args.timeline:
            print("ren: effect-panels needs --timeline, the Ren "
                  "Qualification timeline by exact name", file=sys.stderr)
            return 2
        if args.seed is not None:
            print("ren: effect-panels takes no --seed - a seed no "
                  "builder reads would run the wrong route", file=sys.stderr)
            return 2
        from library.tools.computer_use.scripts import (
            effect_panels_script)
        script = effect_panels_script()
    elif args.recipe == "window-create":
        from library.tools.computer_use import window_recipe

        return window_recipe.run_recipe_verb(client, args)
    elif args.recipe == "qualifier-sample":
        from library.tools.computer_use import qualifier_recipe

        return qualifier_recipe.run_recipe_verb(client, args)
    elif args.recipe == "window-modify":
        from library.tools.computer_use import window_modify

        return window_modify.run_recipe_verb(client, args)
    else:
        print(f"ren: unknown recipe {args.recipe!r}: magic-mask-seed, "
              f"magic-mask-track, window-create, window-delete, "
              f"window-modify, effect-panels or qualifier-sample",
              file=sys.stderr)
        return 2

    from library.tools.marker_feedback import connect_resolve
    from library.tools.qualification_project import borrowed
    from library.tools.resolve_lock import (
        assert_current_timeline, resolve_lease)
    from library.tools.resolved.jobs import QUALIFICATION_PROJECT

    started = time.monotonic()
    # A plain lease: this verb borrows Resolve and OPENS the
    # qualification project itself, so the broker interlock (which
    # refuses a qualification-marked lease while the captain's
    # project is open - i.e. nearly always) would refuse before
    # borrowed() can switch. Found live 2026-10-06 (SkyLight lane,
    # PR 1933 follow-up); `run` carries the same shape.
    # The handshake happens INSIDE the lease: resolve_locale refuses
    # connect_resolve() outside it, so connecting first crashes every
    # live run before any UI (found live 2026-10-07).
    with resolve_lease("computer-use script"):
        resolve = connect_resolve()
        with borrowed(resolve):
            manager = resolve.GetProjectManager()
            project = manager.LoadProject(QUALIFICATION_PROJECT)
            if project is None or project.GetName() != QUALIFICATION_PROJECT:
                print(f"ren: {QUALIFICATION_PROJECT!r} did not open for "
                      f"the recipe", file=sys.stderr)
                return 1
            timeline = _exact_timeline(project, args.timeline)
            if timeline is None:
                print(f"ren: no timeline named exactly {args.timeline!r} "
                      f"in {QUALIFICATION_PROJECT!r}", file=sys.stderr)
                return 1
            # The cursor move goes through the guarded setter: it sets
            # and reads back by unique id, and refuses without the
            # exclusive lease this verb holds.
            assert_current_timeline(project, timeline)
            if args.playhead_timecode:
                timeline.SetCurrentTimecode(args.playhead_timecode)
                if (timeline.GetCurrentTimecode() or "") != args.playhead_timecode:
                    print(f"ren: the playhead did not read back as "
                          f"{args.playhead_timecode!r}", file=sys.stderr)
                    return 1
            if args.allow_foreground:
                client.call("activate_resolve", {"allow_foreground": True})
            step_started = time.monotonic()
            result = run_script(client, script, foreground=args.allow_foreground)
            elapsed = time.monotonic() - step_started
    print(f"recipe: {args.recipe}")
    print(f"goal: {script.goal}")
    if scale is not None:
        print(f"backing scale: {scale}")
    for row in result.steps:
        print(f"  step {row['step']} {row['action']}: {row['outcome']} "
              f"via {row['via']}")
    print(f"outcome: {result.outcome} in {elapsed:.1f}s "
          f"(+ {(time.monotonic() - started - elapsed):.1f}s establish)")
    return 0 if result.outcome == "done" else 1


def _main_window_scale(client) -> float | None:
    """The main window's measured backing scale, or None when no main
    window reports one.

    A background-safe driver read (no pixels, no focus): the same
    `is_main` row `observe` reads the window frame off. The seed
    recipe needs it to turn the seed's device pixels into the window
    points the click takes - a stored number would go stale the
    moment the window moves displays.
    """
    windows = client.call("get_windows").get("windows") or []
    main = next((w for w in windows if w.get("is_main")), {})
    scale = main.get("backing_scale")
    if scale is None:
        return None
    return float(scale)


def _exact_timeline(project, name: str):
    """The timeline with exactly this name, or None - never a prefix."""
    try:
        count = int(project.GetTimelineCount() or 0)
    except Exception:                                   # noqa: BLE001
        return None
    for index in range(1, count + 1):
        timeline = project.GetTimelineByIndex(index)
        if timeline is not None and timeline.GetName() == name:
            return timeline
    return None


def _canva_window_pid(client: DriverClient, target_name: str) -> int:
    """Pin the Chrome process the loop will drive: the window whose
    title carries the target's `title_contains`, largest wins. No such
    window is refused naming what to open - the loop never picks among
    the captain's windows, and an empty match never defaults to one."""
    from library.tools.computer_use.targets import select_target

    target = select_target(target_name)
    windows = client.call("get_windows", {"target": "chrome"})["windows"]
    ours = [
        w for w in windows
        if target.title_contains.lower() in (w.get("title") or "").lower()
    ]
    if not ours:
        raise TargetError(
            f"no {target.title_contains!r} window is open in Chrome",
            "the loop drives the window the target names, and none matches",
            "open the page in Chrome and retry the run",
            code="TARGET_NOT_OPEN",
        )
    best = max(ours, key=lambda w: w["frame"]["width"] * w["frame"]["height"])
    return int(best["owner_pid"])


def _canva_status(target: AppTarget) -> int:
    """What the Canva target is and what it may touch.

    Reports the declaration: the window match, the executor state, the
    scratch-design gate, and the background capability with its reason.
    Read-only: no driver input, only the window list.
    """
    designs = canva_scratch_designs()
    print(f"target: {target.name} ({target.bundle_id}, {target.title_contains!r} windows)")
    print(f"executor: the observe-decide-act loop (observes + focuses; clicks measured filtered)")
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


def _script(
    client: DriverClient,
    path: str,
    model_kind: str,
    max_cost_usd: float | None,
    teach: bool,
) -> int:
    """Run a deterministic UI script from a JSON file.

    `--model stub` (the default) runs deterministic only: a step no
    anchor resolves fails the script. `--model api` falls back to the
    decide session, which sees the observation with the failed step
    appended to its history. Learned anchors live in memory; `--teach`
    writes them back to the file so the next run is deterministic,
    otherwise the verb names how many were learned and the flag that
    would keep them.

    Like `run`, the script runs inside
    `qualification_project.borrowed()`: the captain's project is saved
    and restored around the run.
    """
    from library.tools.marker_feedback import connect_resolve
    from library.tools.qualification_project import borrowed
    from library.tools.resolve_lock import resolve_lease

    script = load_script(path)
    before = _anchor_count(script)
    if model_kind == "api":
        config = ModelConfig.from_env()
        if max_cost_usd is not None:
            config.max_cost_usd = max_cost_usd
        session = ModelSession(config=config)

        def llm(obs: Observation, step: ScriptStep) -> dict:
            taught = dataclasses.replace(
                obs,
                history=[
                    *obs.history,
                    {
                        "action": f"script:{step.action}",
                        "outcome": "no anchor resolved",
                    },
                ],
            )
            return session(taught)

    else:
        llm = None

    with resolve_lease("computer-use"):
        resolve = connect_resolve()
        with borrowed(resolve):
            result = run_script(client, script, llm=llm)
    print(f"outcome: {result.outcome}")
    for entry in result.steps:
        print(f"  step {entry['step']} {entry['action']}: {entry['outcome']} (via {entry['via']})")
    learned = _anchor_count(script) - before
    if learned and teach:
        Path(path).write_text(
            json.dumps(script.to_dict(), indent=2) + "\n", encoding="utf-8"
        )
        print(f"taught {learned} anchor(s) - saved to {path}")
    elif learned:
        print(f"{learned} anchor(s) learned - pass --teach to save them to {path}")
    return 0 if result.outcome == "done" else 1


def _anchor_count(script: Script) -> int:
    return sum(len(step.anchors) for step in script.steps)


def _recipe_save(name: str, script_path: str, layout: str | None) -> int:
    """Name a verified script file as a recipe for later runs."""
    import json

    from pathlib import Path

    from library.tools.computer_use.scripts import (
        RecipeStore,
        Script,
        default_recipes_root,
    )

    raw = Path(script_path).read_text(encoding="utf-8")
    script = Script.from_dict(json.loads(raw))
    store = RecipeStore(default_recipes_root())
    saved = store.save(name, script, layout)
    print(f"recipe {name} saved to {saved}")
    return 0


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
    script_parser = sub.add_parser(
        "script", help="run one deterministic route, file or recipe (no model)")
    script_parser.add_argument(
        "file", nargs="?", default=None,
        help="the script JSON file to run (omit with --recipe)")
    script_parser.add_argument(
        "--recipe", choices=("magic-mask-seed", "magic-mask-track",
                             "window-create", "window-delete",
                             "window-modify",
                             "effect-panels", "qualifier-sample"),
        default=None,
        help="run one named recipe instead of a file")
    script_parser.add_argument(
        "--timeline", default=None,
        help="recipes: the Ren Qualification timeline, by exact name")
    script_parser.add_argument(
        "--seed", default=None,
        help="magic-mask-seed: the MaskSeed receipt as JSON")
    script_parser.add_argument(
        "--mode", default="BI",
        help="magic-mask-seed: the CreateMagicMask mode (F|B|BI)")
    script_parser.add_argument(
        "--direction", default="forward",
        help="magic-mask-track: forward or backward")
    script_parser.add_argument(
        "--playhead-timecode", default=None,
        help="park the playhead here first (read back before running)")
    script_parser.add_argument(
        "--allow-foreground", action="store_true",
        help="post input at the HID tap and activate Resolve first; "
        "takes the captain's screen, so it is never the default")
    script_parser.add_argument(
        "--clip",
        default=None,
        help="window/qualifier recipes: the clip's timeline unique id",
    )
    script_parser.add_argument(
        "--node",
        default=None,
        help="window recipes: the node index the window is on",
    )
    script_parser.add_argument(
        "--shape",
        default=None,
        help="window-create: circle, linear, polygon or powercurve",
    )
    script_parser.add_argument(
        "--point",
        default=None,
        help="qualifier-sample: the normalized sample point as JSON [x, y]",
    )
    script_parser.add_argument(
        "--values",
        default=None,
        help="window-modify: the panel values as JSON {size, pan, ...}",
    )
    _target_flag(script_parser)
    script_parser.add_argument(
        "--model",
        choices=("stub", "api"),
        default="stub",
        help="stub runs deterministic only (a step no anchor resolves fails); "
        f"api falls back to the endpoint in ${ENV_URL}",
    )
    script_parser.add_argument(
        "--max-cost-usd",
        type=float,
        default=None,
        help="cap the decide calls' spend (overrides $COMPUTER_USE_MAX_COST_USD)",
    )
    script_parser.add_argument(
        "--teach",
        action="store_true",
        help="write LLM-taught anchors back to the file",
    )
    run_parser.add_argument(
        "--recipe",
        default=None,
        help="execute the stored recipe first; plan only where no recipe runs",
    )
    run_parser.add_argument(
        "--recipes-dir",
        default=None,
        help="recipes root (overrides $PIPELINE_RECIPES_DIR)",
    )
    run_parser.add_argument(
        "--layout",
        default=None,
        help="the layout string the recipe was verified on",
    )
    save_parser = sub.add_parser(
        "recipe-save", help="name a verified script file as a recipe")
    save_parser.add_argument("name", help="the recipe name (a lower-case slug)")
    save_parser.add_argument("--script", required=True,
                             help="the verified script JSON file")
    save_parser.add_argument("--layout", default=None,
                             help="the layout string the script was verified on")
    args = parser.parse_args(argv)

    try:
        target = select_target(getattr(args, "target", "resolve"))
        if args.subcommand == "status":
            if target.name == "canva":
                return _canva_status(target)
            with DriverClient() as client:
                return _status(client)
        if args.subcommand == "run":
            with DriverClient() as client:
                return _run(client, args.goal, args.model, args.max_cost_usd,
                            recipe=args.recipe, recipes_dir=args.recipes_dir,
                            layout=args.layout, target_name=args.target)
        if args.subcommand == "script":
            if target.name != "resolve":
                raise TargetError(
                    f"the script runner drives Resolve anchors, not {target.name}",
                    "deterministic anchors (AX paths, OCR frames) are resolved "
                    "against Resolve's window; chrome anchors are unbuilt",
                    "run with --target resolve, or run the loop: ren computer-use run --target canva",
                    code="TARGET_SCRIPT_ONLY_RESOLVE",
                )
            if args.recipe and args.file:
                print("ren: a run takes a recipe or a file, never both",
                      file=sys.stderr)
                return 2
            if args.recipe:
                if (args.model != "stub" or args.max_cost_usd is not None
                        or args.teach):
                    print("ren: --model/--max-cost-usd/--teach run with "
                          "a file, not a recipe", file=sys.stderr)
                    return 2
                require_executor(target)
                with DriverClient() as client:
                    return _run_recipe(client, args)
            if args.file:
                if (args.seed is not None or args.timeline is not None
                        or args.playhead_timecode is not None
                        or args.allow_foreground
                        or args.mode != "BI"
                        or args.direction != "forward"
                        or args.clip is not None
                        or args.node is not None
                        or args.shape is not None
                        or args.point is not None
                        or args.values is not None):
                    print("ren: --seed/--timeline/--playhead-timecode/"
                          "--mode/--direction/--allow-foreground/--clip/"
                          "--node/--shape/--point/--values need "
                          "--recipe", file=sys.stderr)
                    return 2
                require_executor(target)
                with DriverClient() as client:
                    return _script(
                        client, args.file, args.model, args.max_cost_usd,
                        args.teach)
            print("ren: pass a script file or --recipe",
                  file=sys.stderr)
            return 2
        if args.subcommand == "recipe-save":
            return _recipe_save(args.name, args.script, args.layout)
    except RenRefusal as exc:
        print(exc.render(), file=sys.stderr)
        return 4
    return 2


if __name__ == "__main__":
    sys.exit(main())
