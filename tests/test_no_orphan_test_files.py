"""Every test file in the repository must be collected by `pytest`.

`library/tools/fusion/tests/` held 65 tests for the parser, effects and
node builder.  They were never collected by CI, which runs `pytest tests/`,
and never by a developer who ran the same command.  Nine of those tests
skipped silently for months (#249, fixed by PR #265); the other 56 passed -
and nobody knew.

This guard works from the FILESYSTEM, not from collection output: a test
that is never collected reports nothing to a collection-based check.  It
walks the tree for `test_*.py` files and fails when any live outside the
declared `COLLECTED_ROOTS`.

The fix for the roots themselves is `testpaths` in `pyproject.toml`, which
tells pytest where to look when invoked without arguments.  This test is
the part that stops the next directory from slipping through.
"""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

# Every directory that `pytest` collects from.  Matches the `testpaths`
# setting in `pyproject.toml`.  When you add a test root, add it to BOTH
# places or this test will tell you.
COLLECTED_ROOTS = (
    REPO_ROOT / "tests",
    REPO_ROOT / "library" / "tools" / "fusion" / "tests",
)

# Test-shaped files that are NOT pytest tests and are deliberately
# excluded.  Each entry records the path relative to REPO_ROOT and the
# reason, so the next person knows why it is here and can decide whether
# it should stay.
# Empty, and that is the state to keep it in.  Its one entry was
# `library/steps/step_6_01_render/test_integration_demo.py`, a manual
# Resolve demo that collected 0 items - and the only importer of the two
# dead thin wrappers `fusion_comp_generator.py` and
# `fusion_transition_generator.py`.  All three were removed 2026-09-12;
# docs/CHROMA_KEY_TRANSITIONS_MEASURED.md had already measured that no
# step, bridge, manifest or DAG node reached them.
DELIBERATE_EXCLUSIONS: dict[str, str] = {}


def _all_test_files() -> set[Path]:
    """Every `test_*.py` in the tree, excluding vendored and generated."""
    skip_parts = {".git", ".venv", "node_modules", "__pycache__"}
    results = set()
    for path in sorted(REPO_ROOT.rglob("test_*.py")):
        if skip_parts.intersection(path.parts):
            continue
        results.add(path.resolve())
    return results


def _inside_collected_root(path: Path) -> bool:
    resolved = path.resolve()
    return any(
        resolved == root or root in resolved.parents
        for root in COLLECTED_ROOTS
    )


def _is_deliberately_excluded(path: Path) -> bool:
    try:
        rel = str(path.resolve().relative_to(REPO_ROOT))
    except ValueError:
        return False
    return rel in DELIBERATE_EXCLUSIONS


def test_no_test_files_outside_collected_roots():
    """Fail if any test_*.py lives where pytest will never find it."""
    orphans = []
    for path in sorted(_all_test_files()):
        if _inside_collected_root(path):
            continue
        if _is_deliberately_excluded(path):
            continue
        try:
            rel = path.relative_to(REPO_ROOT)
        except ValueError:
            rel = path
        orphans.append(str(rel))

    assert not orphans, (
        "test file(s) outside every collected root "
        "(pyproject.toml [tool.pytest.ini_options] testpaths):\n  "
        + "\n  ".join(orphans)
        + "\n\nEither:\n"
        "  - move the file into an existing collected root, or\n"
        "  - add its directory to testpaths in pyproject.toml AND to\n"
        "    COLLECTED_ROOTS in this file, or\n"
        "  - record it in DELIBERATE_EXCLUSIONS with a reason."
    )


