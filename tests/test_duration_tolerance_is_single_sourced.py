"""One tolerance, one home: DURATION_TOLERANCE is single-sourced.

Step 3.01 (`assign_a_roll`, warns) and step 3.03 (`review_rough_cut`,
refuses) each carried their own ``DURATION_TOLERANCE = 0.15`` literal.
Two literals with one value is a disagreement waiting for an edit to
land on one side - and the scout for WP2a found the disagreement
already exists one layer up: step 3.03's `handoff.md` says 0.1s while
both code sites say 0.15s. The prose is the prompt lane's to fix; the
code side is fixed by giving both steps one import,
`library/tools/duration_tolerance.py`, whose value is authoritative.

Proven in both directions: the value test fails if the constant moves,
and the no-literal test fails if either step reintroduces its own
assignment.
"""

import ast
import pathlib

REPO = pathlib.Path(__file__).resolve().parent.parent
SHARED = REPO / "library" / "tools" / "duration_tolerance.py"
STEP_FILES = [
    REPO / "library" / "steps" / "step_3_01_assign_aroll" / "step.py",
    REPO / "library" / "steps" / "step_3_03_review_rough_cut" / "step.py",
]

AUTHORITATIVE_SECONDS = 0.15


def _tree(path: pathlib.Path) -> ast.AST:
    return ast.parse(path.read_text(encoding="utf-8"))


def test_the_authoritative_value_is_a_sixth_of_a_second():
    """0.15s is what both code sites agreed on; the move is a merge, not
    a change. If this fails, the value moved and every duration
    invariant moved with it."""
    namespace: dict = {}
    exec(compile(_tree(SHARED), str(SHARED), "exec"), namespace)
    assert namespace["DURATION_TOLERANCE"] == AUTHORITATIVE_SECONDS


def test_neither_step_assigns_its_own_tolerance():
    """A reintroduced ``DURATION_TOLERANCE = ...`` literal in either
    step is the two-literal defect back again."""
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


def test_both_steps_import_the_shared_tolerance():
    """The import is what makes the value single-sourced rather than
    merely equal. Without it the literals could drift again silently."""
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
