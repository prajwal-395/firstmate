"""The `ci` verb is registered in the ONE command registry, twice.

Defect caught: a verb missing from `ren/commands.VERBS` is a command
`ren` does not have; a verb missing from the native list is refused by
the front door before Python is ever exec'd. Without both, the service
is unreachable through `ren` - the one front door (AGENTS.md 2, 17).
"""

import ren.commands as ren_commands


def test_the_ci_verb_is_registered():
    by_name = {verb.name: verb for verb in ren_commands.VERBS}
    assert "ci" in by_name, "ren lost its 'ci' verb"
    assert "library.tools.local_ci" in " ".join(by_name["ci"].module_argv), (
        "ren 'ci' no longer reaches the local CI service")


def test_the_ci_verb_is_registered_in_both_front_doors():
    by_name = {verb.name: verb for verb in ren_commands.VERBS}
    assert by_name["ci"].module_argv == ("library.tools.local_ci",)
    from pathlib import Path
    repo = Path(__file__).resolve().parents[3]
    native = (repo / "native" / "ren-cli" / "src" / "commands.rs").read_text(
        encoding="utf-8")
    assert 'name: "ci"' in native
