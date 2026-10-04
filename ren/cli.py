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
              "`ren <verb> --help` shows a verb's own options."), ""]
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
    argv = list(sys.argv[1:] if argv is None else argv)
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
        print(usage())
        return 0 if argv else 1

    name, rest = argv[0], argv[1:]
    verb = _BY_NAME.get(name)
    if verb is None:
        print(f"ren: unknown verb {name!r}; `ren --help` lists them.",
              file=sys.stderr)
        return 2

    if verb.builtin == "doctor":
        from library.tools.ren_refusal import REFUSAL_EXIT_CODE, RenRefusal
        from ren import doctor
        try:
            return doctor.main(rest)
        except RenRefusal as refused:
            print(refused.render(), file=sys.stderr)
            return REFUSAL_EXIT_CODE
    if verb.builtin == "config":
        from library.tools.ren_refusal import REFUSAL_EXIT_CODE, RenRefusal
        from ren import config
        try:
            return config.main(rest)
        except RenRefusal as refused:
            print(refused.render(), file=sys.stderr)
            return REFUSAL_EXIT_CODE
    if verb.subcommand:
        _exec_vep(vep, engine_root, [str(manage_project), verb.subcommand, *rest])
    _exec_vep(vep, engine_root, ["-m", *verb.module_argv, *rest])
    return 0  # unreachable: exec does not return


if __name__ == "__main__":
    raise SystemExit(main())
