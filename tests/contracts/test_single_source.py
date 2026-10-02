"""One tolerance, one home: DURATION_TOLERANCE is single-sourced.

Step 3.01 (`assign_a_roll`, warns) and step 3.03 (`review_rough_cut`,
refuses) each carried their own ``DURATION_TOLERANCE = 0.15`` literal.
Two literals with one value is a disagreement waiting for an edit to
land on one side - and the scout for WP2a found the disagreement
already exists one layer up: step 3.03's `handoff.md` says 0.1s while
both code sites say 0.15s. The prose is the prompt lane's to fix; the
code side is fixed by giving both steps one import,
`library/tools/duration_tolerance.py`, whose value is authoritative.
"""
import ast
import pathlib
import sys
from pathlib import Path


REPO = pathlib.Path(__file__).resolve().parents[2]
STEP_FILES = [
    REPO / "library" / "steps" / "step_3_01_assign_aroll" / "step.py",
    REPO / "library" / "steps" / "step_3_03_review_rough_cut" / "step.py",
]


def _tree(path: pathlib.Path) -> ast.AST:
    return ast.parse(path.read_text(encoding="utf-8"))


def test_both_steps_import_the_tolerance_and_assign_none():
    """A reintroduced ``DURATION_TOLERANCE = ...`` literal in either
    step is the two-literal defect back again; the import is what makes
    the value single-sourced rather than merely equal."""
    offenders = []
    for path in STEP_FILES:
        for node in ast.walk(_tree(path)):
            targets = []
            if isinstance(node, ast.Assign):
                targets = node.targets
            elif isinstance(node, ast.AnnAssign):
                targets = [node.target]
            for target in targets:
                if isinstance(target, ast.Name) and target.id == "DURATION_TOLERANCE":
                    offenders.append(f"{path.name}:{node.lineno}")
    assert offenders == [], (
        "steps carry their own DURATION_TOLERANCE literal again: "
        + ", ".join(offenders)
    )
    missing = []
    for path in STEP_FILES:
        tree = _tree(path)
        imported = any(
            (
                isinstance(node, ast.ImportFrom)
                and "duration_tolerance" in (node.module or "")
            )
            or (
                isinstance(node, ast.Import)
                and any(
                    "duration_tolerance" in (alias.name or "")
                    for alias in node.names
                )
            )
            for node in ast.walk(tree)
        )
        if not imported:
            missing.append(path.name)
    assert missing == [], (
        "steps not importing library/tools/duration_tolerance.py: "
        + ", ".join(missing)
    )


# --------------------------------------------------------------------------
# From test_zoom_bound_single_source.py
#
# The prompt's zoom bound and the code's are one source.
#
# Finding 20, execution-frontier report 2026-09-24: step 4.03's prompt
# stated an animated-zoom bound of 1.04 while the comp builder enforced
# 1.15 (`library/tools/fusion/nodes.py: MAX_ANIMATED_ZOOM`) - the prompt
# went stale when the constant moved. The handoff now carries a marker
# where the number was, and the step's bridge renders it from the live
# constant, so the two cannot disagree again.

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from library.steps.step_4_03_plan_vfx.bridge import (  # noqa: E402
    PUNCH_TIMING_MARKER,
    ZOOM_BOUND_MARKER,
    zoom_bound_additions,
)
from library.tools.fusion.nodes import MAX_ANIMATED_ZOOM  # noqa: E402
from library.tools.punch_timing import (  # noqa: E402
    MAX_PUNCH_RAMP_SECONDS,
    MIN_PUNCH_RAMP_SECONDS,
)

STEP_DIR = (REPO_ROOT / "library" / "steps" / "step_4_03_plan_vfx")


def _handoff() -> str:
    return (STEP_DIR / "handoff.md").read_text(encoding="utf-8")


def test_the_rendered_prompt_states_the_enforced_bound():
    """The marker replacement `present_llm_step` performs, simulated:
    the prompt the model reads names the bound the builder enforces, and
    the punch-ramp band `punch_timing` refuses outside of."""
    rendered = _handoff()
    assert ZOOM_BOUND_MARKER in rendered
    assert PUNCH_TIMING_MARKER in rendered
    for marker, text in zoom_bound_additions().items():
        rendered = rendered.replace(marker, text)
    assert ZOOM_BOUND_MARKER not in rendered
    assert PUNCH_TIMING_MARKER not in rendered
    assert f"{MIN_PUNCH_RAMP_SECONDS:.2f}" in rendered
    assert f"{MAX_PUNCH_RAMP_SECONDS:.1f}" in rendered
    assert f"{MAX_ANIMATED_ZOOM:g}" in rendered
    assert "1.04" not in rendered
