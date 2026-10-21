"""`ren`: one verb per thing a person says, each mapped onto what already runs.

The verbs are DATA, in the one registry `ren.commands.VERBS`: a verb
names the existing operation it calls, and the call is an `exec` of
`bin/vep` - so a verb runs under the pipeline's own interpreter and
Resolve variables, exactly as `bin/vep manage_project.py <subcommand>`
does. Arguments after the verb
pass through untouched, so `ren build <project> --only-reel 21` IS
`manage_project.py build-reels <project> --only-reel 21`, and
`ren build --help` prints that subcommand's own help.

Nothing is renamed underneath: `manage_project.py` keeps every
subcommand, and builds its parser from the same registry, so a
subcommand cannot exist there without a verb here.
"""

from __future__ import annotations

import os
import sys

from ren.commands import VERBS
from ren.engine_root import require_engine_root

_BY_NAME = {verb.name: verb for verb in VERBS}


def _engine_paths():
    """`(root, manage_project, vep)` off the one engine resolver.

    Resolved fresh on every call - never at import - so `$REN_ENGINE_ROOT`
    set in-process (tests, a packaged proof) takes effect.
    """
    root = require_engine_root()
    return root, root / "manage_project.py", root / "bin" / "vep"


def version_line() -> str:
    """`ren --version`'s one line: the build, and the engine it runs."""
    from ren.engine_root import find_engine_root
    from ren.version import version_string
    root = find_engine_root()
    return f"ren {version_string()} (engine {root if root else 'not found'})"


def usage() -> str:
    from ren.engine_root import find_engine_root
    from ren.version import version_string
    root = find_engine_root()
    lines = ["usage: ren <verb> [args...]", "",
             ("Ren is the front door to the video editing engine. "
              "`ren <verb> --help` shows a verb's own options; "
              "`ren --help --json` lists every verb with its effects."), ""]
    width = max(len(verb.name) for verb in VERBS)
    group = None
    for verb in VERBS:
        if verb.group != group:
            group = verb.group
            lines.append(f"{group}:")
        lines.append(f"  {verb.name.ljust(width)}  {verb.summary}")
    lines += ["", f"ren {version_string()}",
              f"Engine: {root if root else 'not found'}"]
    return "\n".join(lines)


def _exec_vep(vep, engine_root, argv: list) -> None:
    """Replace this process with `bin/vep <argv>` - signals and exit code pass straight through.

    The engine root goes on PYTHONPATH so a `-m library...` verb resolves from
    whatever directory the person typed `ren` in.
    """
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(
        p for p in (str(engine_root), env.get("PYTHONPATH", "")) if p)
    env["REN_ENGINE_ROOT"] = str(engine_root)
    os.execve(str(vep), [str(vep), *argv], env)


def main(argv=None) -> int:
    # Under a chat harness the host wants data, not prose: default
    # `REN_OUTPUT` to `json` before anything else, so every verb -
    # builtins here and exec'd children through the environment -
    # ends refusals and errors with a `REN_RESULT` line (LE-03). An
    # explicit user value always wins.
    from ren import hosts
    hosts.ensure_harness_output_env()
    argv = list(sys.argv[1:] if argv is None else argv)
    if os.environ.get("REN_PERF_LEDGER") and not (argv and argv[0] == "versions"):
        from library.tools.perf_ledger import count_call
        count_call("cli_starts")
    # `--version` answers from the package itself: no engine needed,
    # so a machine with nothing installed still names its build.
    if argv and argv[0] in ("--version", "-V"):
        print(version_line())
        return 0
    if argv and argv[0] == "version":
        print(version_line())
        return 0

    try:
        engine_root, manage_project, vep = _engine_paths()
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 3

    if not manage_project.is_file() or not vep.is_file():
        print(f"ren: no engine at {engine_root}.\n"
              f"Expected manage_project.py and bin/vep beside it - "
              f"point $REN_ENGINE_ROOT at a built engine tree "
              f"(`python3 -m ren.package_engine --dest <dir>`) or install "
              f"Ren editable from a checkout (README, Quickstart).",
              file=sys.stderr)
        return 3

    if not argv or argv[0] in ("-h", "--help", "help"):
        # `ren --help --json`: the same registry as a catalog a host can
        # parse - each verb's effects, and whether it takes `--json` and
        # `--dry-run` (`ren.commands.catalog`).
        if "--json" in argv[1:]:
            import json

            from ren.commands import catalog
            print(json.dumps(catalog(), indent=2))
            return 0
        print(usage())
        return 0 if argv else 1

    name, rest = argv[0], argv[1:]
    verb = _BY_NAME.get(name)
    if verb is None:
        print(f"ren: unknown verb {name!r}; `ren --help` lists them.",
              file=sys.stderr)
        return 2

    # The version doctor is strictly read-only. Keep it ahead of the
    # ordinary CLI lifecycle reconciliation, which may requeue maintenance
    # work and start cleanup workers as a startup side effect.
    if verb.builtin == "versions":
        from library.tools.versions import doctor as versions_doctor
        return versions_doctor.main(rest)

    if verb.builtin != "storage":
        # Reconcile durable ownership state independently of an editing run.
        # This pass only requeues refused work; every cleanup worker still
        # takes its lease and builds a fresh guarded plan before acting.
        try:
            from library.tools import storage_cli, storage_lifecycle
            startup = storage_lifecycle.startup_reconcile()
            startup["cleanup_workers_started"] = storage_cli.start_cleanup_workers(
                startup, changed_only=True)
            findings = storage_lifecycle.unresolved_findings(startup)
            if findings:
                print("⚠ lifecycle: reconciliation found drift, expired "
                      "quarantine, or retried cleanup jobs; run `ren storage "
                      "reconcile` for details.", file=sys.stderr)
        except Exception as exc:  # noqa: BLE001 - lifecycle check must not block editing
            print(f"⚠ lifecycle: startup check unavailable: {exc}",
                  file=sys.stderr)

    if verb.builtin == "doctor":
        from library.tools.ren_refusal import (
            REFUSAL_EXIT_CODE,
            RenRefusal,
            emit_refusal_result,
        )
        from ren import doctor
        try:
            return doctor.main(rest)
        except RenRefusal as refused:
            print(refused.render(), file=sys.stderr)
            emit_refusal_result(refused)
            return REFUSAL_EXIT_CODE
    if verb.builtin == "config":
        from library.tools.ren_refusal import (
            REFUSAL_EXIT_CODE,
            RenRefusal,
            emit_refusal_result,
        )
        from ren import config
        try:
            return config.main(rest)
        except RenRefusal as refused:
            print(refused.render(), file=sys.stderr)
            emit_refusal_result(refused)
            return REFUSAL_EXIT_CODE
    if verb.builtin == "storage":
        from library.tools import storage_cli
        return storage_cli.main(rest)
    if verb.builtin == "support-bundle":
        from library.tools.ren_refusal import (
            REFUSAL_EXIT_CODE,
            RenRefusal,
            emit_refusal_result,
        )
        from ren import support_bundle
        try:
            return support_bundle.main(rest)
        except RenRefusal as refused:
            print(refused.render(), file=sys.stderr)
            emit_refusal_result(refused)
            return REFUSAL_EXIT_CODE
    if verb.builtin == "setup":
        from ren import setup
        return setup.main(rest)
    if verb.builtin == "uninstall":
        from ren import uninstall
        return uninstall.main(rest)
    if verb.builtin in {"upgrade", "rollback"}:
        from library.tools.ren_refusal import (
            REFUSAL_EXIT_CODE,
            RenRefusal,
            emit_refusal_result,
        )
        from ren import upgrade
        try:
            if verb.builtin == "upgrade":
                return upgrade.main(rest)
            return upgrade.rollback_main(rest)
        except RenRefusal as refused:
            print(refused.render(), file=sys.stderr)
            emit_refusal_result(refused)
            return REFUSAL_EXIT_CODE
    if verb.builtin == "update":
        from library.tools.ren_refusal import (
            REFUSAL_EXIT_CODE,
            RenRefusal,
            emit_refusal_result,
        )
        from ren import update
        try:
            return update.main(rest)
        except RenRefusal as refused:
            print(refused.render(), file=sys.stderr)
            emit_refusal_result(refused)
            return REFUSAL_EXIT_CODE
    if verb.subcommand:
        _exec_vep(vep, engine_root, [str(manage_project), verb.subcommand, *rest])
    _exec_vep(vep, engine_root, ["-m", *verb.module_argv, *rest])
    return 0  # unreachable: exec does not return


if __name__ == "__main__":
    raise SystemExit(main())
