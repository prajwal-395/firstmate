"""The build REPORTS which approved reels diverge, whether or not
anyone remembers to ask.

A survey nobody is forced to run is a document, not a mechanism.
`tests/test_reel_divergence.py` proves the survey measures what it
says; this file proves the BUILD runs it, over EVERY approved reel and
not just the ones it places, and fails the day a refactor stops.

The reel build path needs Resolve, a project and a rendered caption
set, so the consultation is asserted structurally - on the call graph
of `rebuild_reels_in_project` - which is the evidence
`tests/test_ending_and_caption_wiring.py` already accepts for the
owners beside it.
"""

import ast
import inspect

from library.tools import reel_build


SOURCE = inspect.getsource(reel_build)


def _function(name):
    for node in ast.walk(ast.parse(SOURCE)):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise AssertionError(f"{name} is not in reel_build")


def _calls(node):
    out = []
    for child in ast.walk(node):
        func = getattr(child, "func", None) if isinstance(
            child, ast.Call) else None
        if isinstance(func, ast.Attribute) and isinstance(
                func.value, ast.Name):
            out.append(f"{func.value.id}.{func.attr}")
        elif isinstance(func, ast.Name):
            out.append(func.id)
    return out


def test_the_rebuild_surveys_divergence():
    """Remove the call and a build says nothing about the reels it
    left behind - which is exactly how a logo card reached one reel of
    eight while the claim was "every reel inherits it"."""
    calls = _calls(_function("rebuild_reels_in_project"))
    for spelled in ("_divergence.snapshots_for",
                    "_divergence.report_divergence"):
        assert spelled in calls, (
            f"{spelled} is no longer called by rebuild_reels_in_project: "
            f"the divergence survey has become a document again.")


def test_the_survey_covers_every_APPROVED_reel_not_the_ones_building():
    """The reels a build leaves out are the ones that go stale.

    Surveying `staged_to_final` would report only what was just
    rebuilt - which is guaranteed to agree and therefore proves
    nothing.
    """
    body = SOURCE[SOURCE.index("def rebuild_reels_in_project"):]
    block = body[body.index("_divergence.snapshots_for") - 2000:
                 body.index("_divergence.report_divergence")]
    assert "_approved" in block
    assert "approved" in block
    assert "staged_to_final" not in block.split("_approved = ")[-1]


def test_the_report_lands_on_the_record_the_verifier_reads():
    """A measurement nobody can read is not a report (AGENTS.md 10.4)."""
    assert '"divergence": divergence_report,' in SOURCE


def test_the_survey_cannot_fail_the_build():
    """Whether to rebuild a diverged reel is the captain's decision,
    so this reports and never refuses - the same discipline
    `reel_prebuild_census.report_prebuild` states for itself."""
    body = SOURCE[SOURCE.index("def rebuild_reels_in_project"):]
    guarded = body[body.index("from library.tools import reel_divergence"):
                   body.index("divergence_report = {\"unavailable\"")]
    assert "except Exception" in guarded
