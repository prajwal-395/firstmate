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

from ren import REPO_ROOT
from ren.commands import VERBS

MANAGE_PROJECT = REPO_ROOT / "manage_project.py"
VEP = REPO_ROOT / "bin" / "vep"
_BY_NAME = {verb.name: verb for verb in VERBS}


def usage() -> str:
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
    lines += ["", f"Engine checkout: {REPO_ROOT}"]
    return "\n".join(lines)


def _exec_vep(argv: list) -> None:
    """Replace this process with `bin/vep <argv>` - signals and exit code pass straight through.

    The checkout goes on PYTHONPATH so a `-m library...` verb resolves from
    whatever directory the person typed `ren` in.
    """
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(
        p for p in (str(REPO_ROOT), env.get("PYTHONPATH", "")) if p)
    os.execve(str(VEP), [str(VEP), *argv], env)


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not MANAGE_PROJECT.is_file() or not VEP.is_file():
        print(f"ren: no engine checkout at {REPO_ROOT}.\n"
              "Install Ren editable from a checkout: "
              "`pip install -e <checkout>` (README, Quickstart).",
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
        _exec_vep([str(MANAGE_PROJECT), verb.subcommand, *rest])
    _exec_vep(["-m", *verb.module_argv, *rest])
    return 0  # unreachable: exec does not return


if __name__ == "__main__":
    raise SystemExit(main())
