"""The test tree's layout: a directory per LAYER, and per subsystem in `unit/`.

A test's directory says what it establishes, never how long it took:

- `unit/<subsystem>/`  pure algorithms and one module's behaviour, filed
  by the subsystem it belongs to.
- `contracts/`         registry, schema, capability and source-policy
  invariants over the whole engine: the global contract suite.
- `scenarios/`         realistic multi-component Ren workflows.
- `qualification/`     real Resolve, real models, real media.
- `tooling/`           the CI, test-runner and install machinery.

The per-change loop is the changed subsystem's `unit/` directory plus
`contracts/` (`scripts/select_dependent_tests.py --loop`); the batch gate
runs everything. `tests/tooling/test_no_orphan_test_files.py` fails a test
file that sits outside this layout, and `tests/conftest.py` marks
`scenarios/` as `scenario` from it.

Stdlib only: the gate's helpers import it under the resolving `python3`.
"""

from __future__ import annotations

from pathlib import Path

TESTS_ROOT = Path(__file__).resolve().parent

LAYERS = ("unit", "contracts", "scenarios", "qualification", "tooling")

#: `unit/` subdirectories, one per subsystem.
SUBSYSTEMS = ("audio", "captions", "context", "picture", "reels", "resolve")


def _parts(path) -> tuple[str, ...]:
    """The path's directories below `tests/`, or () outside it."""
    try:
        rel = Path(path).resolve().relative_to(TESTS_ROOT)
    except ValueError:
        return ()
    return rel.parts[:-1]


def layer_of(path) -> str | None:
    """The layer a test file sits in, or None when it sits in none."""
    parts = _parts(path)
    if not parts or parts[0] not in LAYERS:
        return None
    if parts[0] == "unit" and (len(parts) != 2 or parts[1] not in SUBSYSTEMS):
        return None
    if parts[0] != "unit" and len(parts) != 1:
        return None
    return parts[0]


def subsystem_of(path) -> str | None:
    """The `unit/` subsystem a test file is filed under, or None."""
    parts = _parts(path)
    if layer_of(path) == "unit":
        return parts[1]
    return None


def unit_dir(subsystem: str) -> Path:
    return TESTS_ROOT / "unit" / subsystem


def layer_dir(layer: str) -> Path:
    return TESTS_ROOT / layer
