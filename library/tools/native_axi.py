"""native-axi: agent-ergonomic reads over Ren's native engine.

The captain's ask: the Project Model plus the headless FilmCraft/EffectCraft
adapters cost agents full-JSON dumps, hand-written driver scripts and a
private op mapping buried in the qualification module. This command wraps
those reads the way `resolve-axi` wraps Resolve reads: TOON tables with
counts, one-line errors with fix hints, bare positional primary arguments.

The verbs come from four measured agent tasks, not from the module surface:
`model` (what clip uses, edits and renders a model holds), `timeline` (a
FilmCraft project's clips as id/track/start/duration - the read-back without
the full inspect dump), `masks` (masks plus effects in two small tables),
`reel-model` (the stored reel-built model per reel: uses, effects,
identity linkage), `ops` (a projection file's ops as effectcraft-cli
command lines - the mapping no other agent surface exposes), and
`swap-plan` (a resolve_swapback plan from measured trim numbers, writing
only the caller-named file).

Safety shape, stated once:

- Every verb here is a READ except `swap-plan`, which writes exactly one
  file the caller names with `--out`. Nothing here opens Resolve, moves a
  cursor, or saves an engine project: `ops` prints command lines, it does
  not run them.
- `ops` refuses fail-closed on an op kind with no measured CLI mapping,
  naming the op. An unmapped op is a gap in this tool, never a guess.
- The engine CLIs are deliberately NOT wrapped: filmcraft-cli and
  effectcraft-cli already answer exit 0/1/2 with `--compact` JSON and
  aggregate-carrying receipts. The waste this tool removes is Ren-side.

Output contract (axi.md principles 1-6, 9-10, shared with resolve-axi):

- stdout carries TOON tables (`name[N]{cols}:` plus backtick-quoted CSV
  rows, the `toon_serializer` quote rule) followed by `help[]` next-step
  hints. The emitters are imported from `resolve_axi`, so the two tools
  speak one dialect.
- A command's own refusals go to stdout as `error: ...` plus a
  `help: ...` fix line. Exit 0 is success (including no-ops), 1 is an
  error, 2 is a usage error such as an unknown flag - a misuse of the
  COMMAND LINE, which goes to stderr the way argparse's own usage
  errors do and the way `ren` answers an unknown verb. `--help` is not
  an error: it prints on stdout and exits 0.
- Outputs stay small by minimal schema (3-4 fields per row), not by
  truncation: no verb here dumps a whole model, project or sequence.
- `-v` / `-V` / `--version` answer before any heavy import runs.

Command-shape rule (the resolve-axi round-2 lesson, kept here): any verb
taking one obvious primary argument accepts it positionally.
`test_positional_primary_args` enumerates the verbs and asserts the shape,
so the next verb built without a positional fails in tests instead of on
first real use.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

VERSION = "0.1.0"

DESCRIPTION = ("Token-cheap reads over the native engine: Project Model "
               "summaries, FilmCraft clip tables, and plan verbs")

TOOL = "native-axi"


class _ModelError(Exception):
    """A refusal with the fix the caller runs next already named."""


def _read_model(path: str):
    """The model document, or a refusal naming the unreadable path."""
    from library.tools.project_model import read_project_model
    try:
        return read_project_model(Path(path))
    except ValueError as exc:
        raise _ModelError(
            f"invalid Project Model {path!r}: {exc}") from exc


def _span(interval) -> str:
    if not isinstance(interval, dict):
        return ""
    return f"{interval.get('start')}..{interval.get('end')}"


def cmd_home(_args) -> int:
    from library.tools import resolve_axi as axi
    axi.emit([
        f"bin: {_bin_path()}",
        f"description: {DESCRIPTION}",
        axi.table("commands", [
            {"command": "model <model.json>",
             "reads": "clip uses, edits, renders + counts"},
            {"command": "timeline <project.fcproj>",
             "reads": "timeline clips (id, track, start, duration)"},
            {"command": "masks <model.json>",
             "reads": "masks + effects, two small tables"},
            {"command": "reel-model <project> [--reel N]",
             "reads": "stored reel models: uses, effects, identity"},
            {"command": "ops <projection.json>",
             "reads": "effectcraft-cli command lines, one per op"},
            {"command": "swap-plan --kept-start N ... --out <plan.json>",
             "reads": "swap-back plan from measured trim numbers"},
        ], ["command", "reads"]),
        axi.help_block([f"{TOOL} model <model.json>",
                        f"{TOOL} timeline <project.fcproj>"]),
    ])
    return 0


def _bin_path() -> str:
    path = str(Path(sys.argv[0]).resolve())
    home = str(Path.home())
    return f"~{path[len(home):]}" if path.startswith(home) else path


def cmd_model(args) -> int:
    from library.tools import resolve_axi as axi
    try:
        model = _read_model(args.path)
    except _ModelError as exc:
        return axi.fail(str(exc), f"{TOOL} model <model.json>")
    uses = model.get("clip_uses", [])
    clips = [{"clip": use.get("id"), "role": use.get("role"),
              "source": _span(use.get("source_interval")),
              "timeline": _span(use.get("timeline_interval"))}
             for use in uses]
    edits = [{"edit": edit.get("id"),
              "operation": edit.get("operation")}
             for edit in model.get("edits", [])]
    renders = [{"render": render.get("id"),
                "frames": render.get("settings", {}).get(
                    "expected_frames", "")}
               for render in model.get("renders", [])]
    axi.emit([
        axi.kv_block("model", {
            "id": model.get("id"), "clips": len(clips),
            "edits": len(edits), "renders": len(renders),
            "effects": len(model.get("effects", [])),
            "masks": len(model.get("masks", [])),
            "entities": len(model.get("entities", [])),
        }),
        axi.table("clips", clips, ["clip", "role", "source", "timeline"]),
        axi.table("edits", edits, ["edit", "operation"]),
        axi.table("renders", renders, ["render", "frames"]),
        axi.help_block([f"{TOOL} masks {args.path}"]),
    ])
    return 0


def cmd_timeline(args) -> int:
    from library.tools import resolve_axi as axi
    import subprocess
    try:
        from library.tools.project_model_qualification import _filmcraft_cli
        cli = str(_filmcraft_cli())
    except Exception as exc:
        return axi.fail(f"pinned FilmCraft CLI is absent ({exc})",
                        "run the upstream intake gate to build it")
    if not Path(args.path).is_file():
        return axi.fail(f"no project at {args.path!r}",
                        f"{TOOL} timeline <project.fcproj>")
    done = subprocess.run(
        [cli, "--project", args.path, "exec", "sequence.inspect", "{}",
         "--compact"], capture_output=True, text=True, encoding="utf-8",
        timeout=120, check=False)
    if done.returncode != 0:
        return axi.fail(f"inspect failed: {done.stderr.strip()[-300:]}",
                        f"{TOOL} timeline {args.path}")
    try:
        snapshot = json.loads(done.stdout)
    except json.JSONDecodeError as exc:
        return axi.fail(f"inspect answered non-JSON ({exc})",
                        f"{TOOL} timeline {args.path}")
    rows = []
    for plane in ("video", "audio"):
        for track in snapshot.get(plane, []):
            label = track.get("name") or plane
            for item in track.get("items", []):
                rows.append({"clip": item.get("clip"),
                             "track": label,
                             "start": item.get("startFrame"),
                             "duration": item.get("durationFrames")})
    axi.emit([
        axi.kv_block("timeline", {
            "project": args.path, "clips": len(rows),
            "frames": snapshot.get("durationFrames", "")}),
        axi.table("clips", rows, ["clip", "track", "start", "duration"]),
        axi.help_block([f"{TOOL} model <model.json>"]),
    ])
    return 0


def cmd_masks(args) -> int:
    from library.tools import resolve_axi as axi
    try:
        model = _read_model(args.path)
    except _ModelError as exc:
        return axi.fail(str(exc), f"{TOOL} masks <model.json>")
    masks = [{"mask": mask.get("id"), "entity": mask.get("entity_id"),
              "kind": (mask.get("representation") or {}).get("kind", "")}
             for mask in model.get("masks", [])]
    effects = [{"effect": effect.get("id"),
                "target": effect.get("target", {}).get("clip_use_id", "")
                if isinstance(effect.get("target"), dict) else ""}
               for effect in model.get("effects", [])]
    axi.emit([
        axi.kv_block("overlays", {"masks": len(masks),
                                  "effects": len(effects)}),
        axi.table("masks", masks, ["mask", "entity", "kind"]),
        axi.table("effects", effects, ["effect", "target"]),
        axi.help_block([f"{TOOL} ops <projection.json>"]),
    ])
    return 0


def cmd_reel_model(args) -> int:
    from library.tools import resolve_axi as axi
    try:
        from library.tools import reel_project_model as rpm
        payload = rpm.show(args.project, args.reel)
    except FileNotFoundError as exc:
        return axi.fail(f"no Project Model stored ({exc})",
                        f"build the reels first: ren build {args.project}")
    except ValueError as exc:
        return axi.fail(str(exc),
                        f"{TOOL} reel-model {args.project}")
    reels = [{"reel": row.get("reel"), "uses": row.get("uses"),
              "a_roll": row.get("a_roll"), "effects": row.get("effects"),
              "titles": row.get("titles"), "renders": row.get("renders"),
              "persons": ",".join(row.get("persons") or []),
              "masks": row.get("masks"), "speech": row.get("speech")}
             for row in payload.get("reels", [])]
    parts = [
        axi.kv_block("reel_model", {
            "project": args.project,
            "revision": payload.get("revision", ""),
            "reels": len(reels)}),
        axi.table("reels", reels,
                  ["reel", "uses", "a_roll", "effects", "titles",
                   "renders", "persons", "masks", "speech"]),
        axi.help_block([f"{TOOL} model "
                        f"<{args.project}/project_model/snapshot.json>"]),
    ]
    axi.emit(parts)
    return 0


def op_to_commands(op: dict, identity_map: dict) -> list:
    """One projection op to effectcraft-cli (command, params) pairs.

    Public so tests and future verbs share the one measured mapping; it
    mirrors the qualification runner's mapping for the projectable op
    kinds. Anything else raises `_ModelError` fail-closed, naming the op.
    """
    kind = op.get("op")
    if kind == "new_comp":
        return [("comp.new", {"name": op["name"], "width": op["width"],
                              "height": op["height"],
                              "frameRate": op["frame_rate"],
                              "duration": op["duration"]})]
    if kind == "import_footage":
        return [("file.import", {"paths": [op["path"]]})]
    if kind == "add_footage_layer":
        params: dict = {"item": op["footage"]}
        if "time" in op:
            params["time"] = op["time"]
        return [("layer.addItem", params)]
    if kind == "add_mask":
        return [("layer.addMask", {"layer": op["layer"],
                                   "shape": op["shape"],
                                   "rect": op["rect"]})]
    if kind == "interpret_footage":
        return [("file.interpretFootage",
                 {"frameRate": op["frame_rate"]})]
    if kind == "set_track_matte":
        return [("layer.setTrackMatte",
                 {"layer": op["layer"], "matte": op["matte"],
                  "kind": op["kind"]})]
    if kind == "apply_effect":
        # Mirrors the driver: one effect.apply, then one prop.set per
        # parameter on that effect's index slot.
        index = next(
            (entry["effect_index"]
             for entry in (identity_map.get("effects") or {}).values()
             if entry["layer"] == op["layer"]
             and entry["effectcraft_id"] == op["effect_id"]), None)
        if index is None:
            raise _ModelError(
                f"effect {op['effect_id']!r} on {op['layer']!r} has no "
                "identity-map slot - re-project the model before executing")
        commands = [("effect.apply", {"layer": op["layer"],
                                      "effect": op["effect_id"]})]
        for param, value in (op.get("params") or {}).items():
            commands.append(("prop.set", {"layer": op["layer"],
                                          "path": f"effects/#{index}/{param}",
                                          "value": value}))
        return commands
    if kind == "keyframe":
        return [("prop.addKey", {"layer": op["layer"], "path": op["path"],
                                 "time": op["time"], "value": op["value"]})]
    raise _ModelError(f"no CLI mapping for op {kind!r} - extend "
                      f"{__name__}.op_to_commands with the measured command")


def cmd_ops(args) -> int:
    from library.tools import resolve_axi as axi
    try:
        stored = json.loads(Path(args.path).read_text(encoding="utf-8"))
        projection = stored["projection"]
    except (OSError, ValueError, KeyError) as exc:
        return axi.fail(f"cannot read projection {args.path!r} ({exc})",
                        f"{TOOL} ops <projection.json>")
    rows, refused = [], []
    for op in projection["request"]["operations"]:
        try:
            for name, params in op_to_commands(
                    op, projection.get("identity_map", {})):
                rows.append({"command": name,
                             "params": json.dumps(params)[:160]})
        except _ModelError as exc:
            refused.append({"op": op.get("op"), "reason": str(exc)})
    parts = [axi.kv_block("translation", {
        "operations": len(projection["request"]["operations"]),
        "commands": len(rows), "refused": len(refused)}),
        axi.table("commands", rows, ["command", "params"])]
    if refused:
        parts.append(axi.table("refused", refused, ["op", "reason"]))
    parts.append(axi.help_block(
        ["effectcraft-cli run <command> '<json>' ... --empty "
         "--save-as <out.ecproj> --json"]))
    axi.emit(parts)
    return 0 if not refused else 1


def cmd_swap_plan(args) -> int:
    from library.tools import resolve_axi as axi
    from library.tools import resolve_swapback as swap
    try:
        kept_start = int(args.kept_start)
        kept_frames = int(args.kept_frames)
        keys = json.loads(args.keys)
    except (TypeError, ValueError) as exc:
        return axi.fail(f"bad numbers ({exc})",
                        f"{TOOL} swap-plan --help lists the shapes")
    end = kept_start + kept_frames
    model = {
        "renders": [
            {"id": "render-prior", "input_revision": "rev-0",
             "output_asset_id": "asset-prior",
             "receipt_id": "receipt-prior",
             "frame_map_id": "map-prior", "settings": {}},
            {"id": "render-new", "input_revision": "rev-1",
             "output_asset_id": "asset-new", "receipt_id": "receipt-new",
             "frame_map_id": "map-new", "settings": {}},
        ],
        "receipts": [
            {"id": "receipt-prior", "status": "ok",
             "input_revision": "rev-0", "output_asset_id": "asset-prior",
             "renderer": "filmcraft", "details": {}},
            {"id": "receipt-new", "status": "ok",
             "input_revision": "rev-1", "output_asset_id": "asset-new",
             "renderer": "filmcraft", "details": {}},
        ],
        "frame_maps": [
            {"id": "map-prior", "from_clock": "clock-project",
             "to_clock": "clock-prior",
             "segments": [{"from": {"clock_id": "clock-project",
                                    "start": 0, "end": args.prior_frames},
                           "to": {"clock_id": "clock-prior", "start": 0,
                                  "end": args.prior_frames},
                           "mapping_kind": "one_to_one",
                           "rate": {"numerator": 1, "denominator": 1}}]},
            {"id": "map-new", "from_clock": "clock-project",
             "to_clock": "clock-new",
             "segments": [{
                 "from": {"clock_id": "clock-project",
                          "start": kept_start, "end": end},
                 "to": {"clock_id": "clock-new", "start": 0,
                        "end": kept_frames},
                 "mapping_kind": "one_to_one",
                 "rate": {"numerator": 1, "denominator": 1}}]},
        ],
    }
    try:
        plan_doc = swap.plan(
            model, prior_render_id="render-prior",
            new_render_id="render-new", unique_id=args.id,
            old_source=args.prior_name, new_source=args.trimmed_name,
            played_anchor={"clock_id": "clock-project",
                           "start": kept_start, "end": end},
            fusion_keys=[{"comp": args.comp, "tool": key["tool"],
                          "param": key["param"], "frame": key["frame"],
                          "value": key["value"]} for key in keys],
            grade={"luts": [], "cdl": {},
                   "ranges": [{"start": kept_start, "end": end}]})
    except Exception as exc:
        return axi.fail(f"swap-back refused: {exc}",
                        f"{TOOL} swap-plan --help lists the shapes")
    try:
        Path(args.out).write_text(json.dumps(plan_doc, indent=2) + "\n",
                                   encoding="utf-8")
    except OSError as exc:
        return axi.fail(f"cannot write {args.out!r} ({exc})",
                        f"{TOOL} swap-plan --out <path> ...")
    moves = [{"from": move["from_frame"], "to": move["to_frame"],
              "value": move["value"]} for move in plan_doc["key_moves"]]
    axi.emit([
        axi.kv_block("swap_plan", {"keys": len(moves), "out": args.out}),
        axi.table("key_moves", moves, ["from", "to", "value"]),
        axi.help_block(["bin/resolve-axi ingest <render> (dry-run first)"]),
    ])
    return 0


def build_parser() -> argparse.ArgumentParser:
    from library.tools.resolve_axi import Parser
    parser = Parser(prog=TOOL, description=__doc__)
    sub = parser.add_subparsers(dest="command")
    model = sub.add_parser("model", help="summarize a Project Model file")
    model.add_argument("path", help="Project Model JSON")
    model.set_defaults(func=cmd_model)
    timeline = sub.add_parser("timeline",
                              help="summarize a FilmCraft project's clips")
    timeline.add_argument("path", help="project .fcproj")
    timeline.set_defaults(func=cmd_timeline)
    masks = sub.add_parser("masks", help="masks + effects of a model")
    masks.add_argument("path", help="Project Model JSON")
    masks.set_defaults(func=cmd_masks)
    reel_model = sub.add_parser("reel-model",
                                help="stored reel Project Model of a project")
    reel_model.add_argument("project", help="project folder")
    reel_model.add_argument("--reel", default=None,
                            help="one reel number or name (default: all)")
    reel_model.set_defaults(func=cmd_reel_model)
    ops = sub.add_parser("ops",
                         help="projection ops to effectcraft-cli commands")
    ops.add_argument("path", help="projection JSON")
    ops.set_defaults(func=cmd_ops)
    plan = sub.add_parser("swap-plan",
                          help="swap-back plan from measured trim numbers")
    plan.add_argument("--kept-start", required=True)
    plan.add_argument("--kept-frames", required=True)
    plan.add_argument("--prior-frames", type=int, required=True)
    plan.add_argument("--prior-name", required=True)
    plan.add_argument("--trimmed-name", required=True)
    plan.add_argument("--comp", default="Main")
    plan.add_argument("--id", default="swap-1")
    plan.add_argument("--keys", default="[]",
                      help="JSON list like [{\"tool\":\"Transform1\","
                           "\"param\":\"Size\",\"frame\":12,\"value\":1.0}]")
    plan.add_argument("--out", required=True)
    plan.set_defaults(func=cmd_swap_plan)
    return parser


def main(argv=None) -> int:
    # Version fast path: answer before any heavy import (axi principle 10).
    argv = list(sys.argv[1:] if argv is None else argv)
    if len(argv) == 1 and argv[0] in ("-v", "-V", "--version"):
        sys.stdout.write(f"{VERSION}\n")
        return 0
    parser = build_parser()
    if not argv:
        return cmd_home(argparse.Namespace())
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        # Parser already reported the usage error on stdout; main keeps
        # its int contract so callers and tests see the exit code.
        if exc.code is None:
            return 0
        return exc.code if isinstance(exc.code, int) else 2
    func = getattr(args, "func", None)
    if func is None:
        parser.print_help(sys.stdout)
        return 2
    try:
        return func(args)
    except _ModelError as exc:
        from library.tools import resolve_axi as axi
        return axi.fail(str(exc), f"{TOOL} --help lists the five verbs")


if __name__ == "__main__":
    _HERE = str(Path(__file__).resolve())
    _ROOT = str(Path(_HERE).parents[2])
    if _ROOT not in sys.path:
        sys.path.insert(0, _ROOT)
    raise SystemExit(main())
