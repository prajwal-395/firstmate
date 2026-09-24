"""`ren`: one verb per thing a person says, each mapped onto what already runs.

The verbs are DATA (`VERBS`): a verb names the existing operation it
calls, and the call is an `exec` of `bin/vep` - so a verb runs under the
pipeline's own interpreter and Resolve variables, exactly as a lane runs
`bin/vep manage_project.py <subcommand>` today. Arguments after the verb
pass through untouched, so `ren build <project> --only-reel 21` IS
`manage_project.py build-reels <project> --only-reel 21`, and
`ren build --help` prints that subcommand's own help.

Nothing is renamed underneath: `manage_project.py` keeps every
subcommand, and a subcommand with no verb here fails `ren` loudly
(`_assert_every_subcommand_has_a_verb`) rather than quietly becoming
unreachable from the front door.
"""

from __future__ import annotations

import ast
import os
import sys
from dataclasses import dataclass

from ren import REPO_ROOT

MANAGE_PROJECT = REPO_ROOT / "manage_project.py"
VEP = REPO_ROOT / "bin" / "vep"


@dataclass(frozen=True)
class Verb:
    name: str
    group: str
    summary: str
    subcommand: str = ""
    """The `manage_project.py` subcommand this verb execs, or ""."""
    module_argv: tuple = ()
    """Or: `-m <module> <args...>` this verb execs, arguments appended."""
    builtin: str = ""
    """Or: a command implemented in this package (`doctor`, `config`)."""

    def calls(self) -> str:
        if self.subcommand:
            return f"manage_project.py {self.subcommand}"
        if self.module_argv:
            return "python -m " + " ".join(self.module_argv)
        return f"ren.{self.builtin}"


_FOOTAGE_QUERY = "library.tools.analysis.footage_query"

VERBS = (
    Verb("doctor", "Setup", "Check this Mac can run Ren; changes nothing",
         builtin="doctor"),
    Verb("config", "Setup", "Show where each setting comes from; --init writes a starter file",
         builtin="config"),
    Verb("init", "Setup", "Create the projects root folder", subcommand="init-root"),

    Verb("new", "Projects", "Create a project", subcommand="new"),
    Verb("projects", "Projects", "List projects", subcommand="list"),
    Verb("status", "Projects", "Show a project's pipeline status", subcommand="status"),
    Verb("info", "Projects", "Show a project's configuration as JSON", subcommand="info"),
    Verb("check", "Projects", "Run a project's readiness check", subcommand="check"),
    Verb("edit", "Projects", "Run the editing pipeline on a project", subcommand="run"),
    Verb("trace", "Projects", "Regenerate the run traceback and artifact index", subcommand="trace"),
    Verb("organize", "Projects", "Bring a project folder onto the standard layout", subcommand="organize"),
    Verb("archive", "Projects", "Archive a finished project", subcommand="archive"),

    Verb("propose", "Reels", "Publish the chosen moments as the reel review file", subcommand="propose-reels"),
    Verb("build", "Reels", "Build approved reels in Resolve", subcommand="build-reels"),
    Verb("touch", "Reels", "Apply a small change to a built reel (a touch-up, not a rebuild)", subcommand="touch-reel"),
    Verb("dry-run", "Reels", "Plan a touch-up against the live timeline and stop", subcommand="ren-dry-run"),
    Verb("deliver", "Reels", "Render one approved reel to a file", subcommand="deliver-reel"),
    Verb("watch", "Reels", "Have a model watch a delivered reel", subcommand="watch-reel"),
    Verb("hear", "Reels", "Hear a delivered reel against its plan", subcommand="hear-reel"),
    Verb("drift", "Reels", "Compare each reel's build snapshot with the live timeline", subcommand="drift"),
    Verb("sign-off", "Reels", "Record the captain's sign-off on a built reel", subcommand="sign-off"),
    Verb("purge", "Reels", "Plan (default) or --apply the lean-retention purge", subcommand="purge"),
    Verb("discharge", "Reels", "Discharge a dropped note a promotion filed", subcommand="discharge-uncarried"),
    Verb("variant", "Reels", "Two versions of one reel: new, build, list, diff, choose, merge", subcommand="variant"),
    Verb("rounds", "Reels", "What changed between two feedback rounds", subcommand="round-diff"),
    Verb("notes", "Reels", "Show which timeline note went to which step", subcommand="notes"),

    Verb("pool-organize", "Resolve", "File a Resolve project's media pool", subcommand="resolve-organize"),
    Verb("pool-prune", "Resolve", "Remove media-pool items no timeline plays", subcommand="resolve-prune"),
    Verb("mark-master", "Resolve", "Mark the master timeline where each reel was taken", subcommand="resolve-mark-master"),
    Verb("relink", "Resolve", "Relink offline media after a move", subcommand="relink"),

    Verb("search", "Footage", "Find where in a project's footage something happens",
         module_argv=(_FOOTAGE_QUERY, "search")),
    Verb("search-index", "Footage", "Build the footage search index (writes into the project's scratch)",
         module_argv=(_FOOTAGE_QUERY, "build")),
)

_BY_NAME = {verb.name: verb for verb in VERBS}


def _manage_project_commands() -> tuple:
    """`manage_project.ALL_COMMANDS`, read off the source rather than imported.

    Importing manage_project pulls in the registry and the schemas; the
    help screen should not pay for that, and must work before the ML
    environment exists.
    """
    tree = ast.parse(MANAGE_PROJECT.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id == "ALL_COMMANDS"
                for t in node.targets):
            return tuple(ast.literal_eval(node.value))
    raise RuntimeError(f"no ALL_COMMANDS in {MANAGE_PROJECT}")


def _assert_every_subcommand_has_a_verb() -> None:
    mapped = {verb.subcommand for verb in VERBS if verb.subcommand}
    listed = set(_manage_project_commands())
    unmapped = sorted(listed - mapped)
    unknown = sorted(mapped - listed)
    if unmapped or unknown:
        raise SystemExit(
            "ren: the verb table is out of step with manage_project.py.\n"
            f"  subcommands with no verb: {unmapped or 'none'}\n"
            f"  verbs naming no subcommand: {unknown or 'none'}\n"
            "Add or fix the row in ren/cli.py VERBS.")


def usage() -> str:
    lines = ["usage: ren <verb> [args...]", "",
             "Ren is the front door to the video editing engine. "
             "`ren <verb> --help` shows a verb's own options.", ""]
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
    _assert_every_subcommand_has_a_verb()

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
        from ren import doctor
        return doctor.main(rest)
    if verb.builtin == "config":
        from ren import config
        return config.main(rest)
    if verb.subcommand:
        _exec_vep([str(MANAGE_PROJECT), verb.subcommand, *rest])
    _exec_vep(["-m", *verb.module_argv, *rest])
    return 0  # unreachable: exec does not return


if __name__ == "__main__":
    raise SystemExit(main())
