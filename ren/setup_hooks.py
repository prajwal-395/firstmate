"""`ren setup-hooks`: install the marker-feedback hook for a host.

The hook (`ren/hooks/marker_hook.py`) is disk-only: at session start it
lists pulled Resolve marker notes no routing record has carried yet,
with the `ren` verb that works each one. It never probes Resolve, so a
closed app costs a session nothing.

Alongside the hook, `--write` persists `REN_OUTPUT=json` in the host's
own session environment, so an agent session gets the machine-readable
`REN_RESULT` line without depending on binary detection at runtime
(LE-03). A value the user set explicitly - even empty - is never
overwritten. Hosts with no env channel in the file this manages
(`codex`: `hooks.json` carries commands, not environment) rely on the
runtime default in `ren/hosts.py` instead.

Supported hosts (P5: OAuth harnesses only):

* `claude-code` - merges a SessionStart entry into
  `~/.claude/settings.json`, plus `REN_OUTPUT` under its `env`.
* `opencode` - copies the plugin to
  `~/.config/opencode/plugins/ren-marker-hook.js` (the plugin defaults
  the variable itself).
* `codex` - merges a session_start entry into `~/.codex/hooks.json`.

Without `--write` this prints what would change and stops. With
`--write` it backs the target up (`<file>.bak`) and merges. A hook
entry already naming `ren-marker-hook` is left alone (idempotent).
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys

APPS = ("claude-code", "opencode", "codex")


def _templates_dir() -> str:
    return os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "hooks", "templates")


def _target(app: str) -> str:
    home = os.path.expanduser("~")
    if app == "claude-code":
        return os.path.join(home, ".claude", "settings.json")
    if app == "opencode":
        return os.path.join(home, ".config", "opencode", "plugins",
                            "ren-marker-hook.js")
    return os.path.join(home, ".codex", "hooks.json")


def _plan(app: str) -> tuple:
    templates = _templates_dir()
    if app == "opencode":
        return (_target(app),
                os.path.join(templates, "opencode.js"),
                "copy the plugin file")
    name = "claude-code.json" if app == "claude-code" else "codex.json"
    return (_target(app), os.path.join(templates, name),
            "merge the ren-marker-hook entry")


def _merge_claude_settings(path: str) -> tuple:
    """Merge the hook and `REN_OUTPUT=json` into Claude settings.

    Returns (changed, note): `changed` is False when the file already
    carries both, else the settings to write. An `env.REN_OUTPUT` the
    user set - even empty - always wins and is left alone."""
    try:
        with open(path, "r", encoding="utf-8") as handle:
            settings = json.load(handle)
    except (OSError, ValueError):
        settings = {}
    if not isinstance(settings, dict):
        return False, "settings file is not an object; left alone"
    notes = []
    wrote_hook = False
    hooks = settings.setdefault("hooks", {})
    starts = hooks.setdefault("SessionStart", [])
    if "ren-marker-hook" in json.dumps(starts):
        notes.append("ren-marker-hook already present")
    else:
        starts.append({
            "hooks": [{"type": "command",
                       "command": "ren-marker-hook check"}]})
        notes.append("SessionStart entry added")
        wrote_hook = True
    wrote_env = False
    env = settings.get("env")
    if env is None:
        settings["env"] = {"REN_OUTPUT": "json"}
        notes.append("REN_OUTPUT=json added to env")
        wrote_env = True
    elif isinstance(env, dict) and "REN_OUTPUT" not in env:
        env["REN_OUTPUT"] = "json"
        notes.append("REN_OUTPUT=json added to env")
        wrote_env = True
    elif isinstance(env, dict):
        notes.append("REN_OUTPUT already set in env; left alone")
    else:
        notes.append("env is not an object; left alone")
    if not wrote_hook and not wrote_env:
        return False, "; ".join(notes)
    return settings, "; ".join(notes)


def _merge_codex_hooks(path: str) -> tuple:
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        data = {}
    if not isinstance(data, dict):
        return False, "hooks file is not an object; left alone"
    blob = json.dumps(data)
    if "ren-marker-hook" in blob:
        return False, "ren-marker-hook already present"
    hooks = data.setdefault("hooks", {})
    starts = hooks.setdefault("session_start", [])
    starts.append({"command": "ren-marker-hook check"})
    return data, "session_start entry added"


def install(app: str, write: bool = False) -> int:
    """Plan (default) or perform (`write`) the hook installation."""
    if app not in APPS:
        print(f"ren setup-hooks: unknown app {app!r}; want one of {APPS}.",
              file=sys.stderr)
        return 2
    target, template, action = _plan(app)
    if not os.path.isfile(template):
        print(f"ren setup-hooks: template missing: {template}",
              file=sys.stderr)
        return 3
    print(f"app:      {app}")
    print(f"target:   {target}")
    print(f"template: {template}")
    print(f"action:   {action}")
    if app == "opencode":
        try:
            with open(template, "r", encoding="utf-8") as handle:
                print("--- plugin preview (first 10 lines) ---")
                for i, line in enumerate(handle):
                    if i >= 10:
                        break
                    print("  " + line.rstrip())
        except OSError as exc:
            print(f"ren setup-hooks: cannot read template: {exc}",
                  file=sys.stderr)
            return 3
    else:
        try:
            with open(template, "r", encoding="utf-8") as handle:
                print("--- hook entry ---")
                print("  " + handle.read().strip())
        except OSError as exc:
            print(f"ren setup-hooks: cannot read template: {exc}",
                  file=sys.stderr)
            return 3
    if not write:
        print("Review the path above, then re-run with --write to install.")
        return 0
    backup = target + ".bak"
    if app == "opencode":
        os.makedirs(os.path.dirname(target), exist_ok=True)
        if os.path.isfile(target):
            shutil.copy2(target, backup)
            print(f"backup:   {backup}")
        shutil.copy2(template, target)
        print(f"installed {target}")
        return 0
    merged, note = (_merge_claude_settings(target) if app == "claude-code"
                    else _merge_codex_hooks(target))
    if merged is False:
        print(f"unchanged: {note}")
        return 0
    os.makedirs(os.path.dirname(target), exist_ok=True)
    if os.path.isfile(target):
        shutil.copy2(target, backup)
        print(f"backup:   {backup}")
    with open(target, "w", encoding="utf-8") as handle:
        json.dump(merged, handle, indent=2)
        handle.write("\n")
    print(f"installed: {note} -> {target}")
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="ren setup-hooks",
        description="Install the marker-feedback hook for a host.")
    parser.add_argument("--app", default="",
                        help="claude-code|opencode|codex")
    parser.add_argument("--write", action="store_true",
                        help="install; without it, plan only")
    parser.add_argument("--list", action="store_true",
                        help="list supported hosts and stop")
    args = parser.parse_args(argv)
    if args.list:
        for app in APPS:
            print(f"{app}: {_target(app)}")
        return 0
    if not args.app:
        print("ren setup-hooks: --app is required "
              "(claude-code|opencode|codex); --list shows targets.",
              file=sys.stderr)
        return 2
    return install(args.app.lower(), write=args.write)


if __name__ == "__main__":
    raise SystemExit(main())
