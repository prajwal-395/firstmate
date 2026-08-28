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
DELIBERATE_EXCLUSIONS: dict[str, str] = {
    "library/steps/step_6_01_render/test_integration_demo.py": (
        "Manual Resolve integration demo script.  Has no test_ functions "
        "and requires a live DaVinci Resolve instance.  Collects 0 items."
    ),
}


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


def test_deliberate_exclusions_still_exist():
    """A stale exclusion means somebody moved a file without updating."""
    for rel_path, reason in DELIBERATE_EXCLUSIONS.items():
        full = REPO_ROOT / rel_path
        assert full.exists(), (
            f"deliberate exclusion {rel_path!r} no longer exists on disk "
            f"- remove it from DELIBERATE_EXCLUSIONS"
        )
        assert reason.strip(), (
            f"deliberate exclusion {rel_path!r} has no reason"
        )


def test_collected_roots_match_pyproject_testpaths():
    """The two declarations of 'where tests live' must agree."""
    try:
        import tomllib
    except ModuleNotFoundError:
        import tomli as tomllib  # type: ignore[no-redef]

    pyproject = REPO_ROOT / "pyproject.toml"
    assert pyproject.exists(), "pyproject.toml not found"

    with open(pyproject, "rb") as f:
        cfg = tomllib.load(f)

    testpaths = cfg.get("tool", {}).get("pytest", {}).get("ini_options", {}).get("testpaths", [])
    assert testpaths, "no testpaths in pyproject.toml"

    # Resolve both sets to absolute paths for comparison
    pyproject_roots = {(REPO_ROOT / p).resolve() for p in testpaths}
    declared_roots = {r.resolve() for r in COLLECTED_ROOTS}

    assert pyproject_roots == declared_roots, (
        f"COLLECTED_ROOTS and pyproject.toml testpaths disagree:\n"
        f"  pyproject.toml: {sorted(str(p.relative_to(REPO_ROOT)) for p in pyproject_roots)}\n"
        f"  this file:      {sorted(str(p.relative_to(REPO_ROOT)) for p in declared_roots)}"
    )


def test_every_collected_root_has_tests():
    """A collected root with no test files is a broken declaration."""
    all_files = _all_test_files()
    for root in COLLECTED_ROOTS:
        root_files = [f for f in all_files if _inside_collected_root(f)
                      and root.resolve() in f.parents or f == root.resolve()]
        try:
            rel = root.relative_to(REPO_ROOT)
        except ValueError:
            rel = root
        assert root_files, (
            f"collected root {rel} contains no test_*.py files"
        )
