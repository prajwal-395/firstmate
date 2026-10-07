"""The outcomes report reaches the implementation through both Ren doors."""

from pathlib import Path

from ren.commands import VERBS

REPO = Path(__file__).resolve().parents[3]


def test_outcomes_verb_is_registered_in_python_and_native_front_doors():
    by_name = {verb.name: verb for verb in VERBS}
    assert by_name["outcomes"].module_argv == (
        "library.tools.feedback_outcomes",)
    native = (REPO / "native" / "ren-cli" / "src" / "commands.rs").read_text(
        encoding="utf-8")
    assert 'name: "outcomes"' in native
