"""The cursor is settable from N places; this test is the discipline.

`SetCurrentTimeline` is the call that killed a Fusion pass: any holder
can move the instance cursor out from under a sibling lane's build.
`library/tools/resolve_lock.py` already owns the shared layer for this
(`assert_current_timeline` inside a lease, `cursor_fence` for a guarded
section, `cursor_excursion` for a deliberate move-and-return) - and
since the 2026-09-20 migration every in-pipeline establishment goes
through it, while reads that never needed the cursor stopped moving
it at all (`timeline_serializer`'s `timeline=` handle,
`timeline_sync_qa`, `reel_deliver`'s lookup). No discipline about
asserting it can be enforced while the setter set grows silently.

So this test registers every `SetCurrentTimeline` site in shipped code
(`library/`, `bin/`) with its owner and migration state. Adding a new call site
fails here with instructions: route the establishment through
`resolve_lock`, or register the site with a reason. Removing one means
deleting its registry row - a row no longer needed is a lie about what
is still owed.

This test changes no runtime behaviour; what it stops is silent growth
of the setter set. The migration order it once tracked lives in
`docs/RESOLVE_AXI_ROUND5.md`.

`tests/` is out of scope by construction: fakes there RAISE on
`SetCurrentTimeline` (reads must not move the cursor), and the nine
live `*_against_resolve` / SOP tests declare their writes against a
real session. `library/tools/resolve_axi.py` is covered by its own AST
test and must never appear here.
"""

import ast
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent

SCOPES = ("library", "bin")

#: file -> (expected site count, owner note). `state` is one of:
#: `shared` (the implementation itself), `probe` (capability checks that
#: never drive a build - hand-run by an operator, so the fence detects
#: their moves rather than the lease serialising them).
CURSOR_SETTERS = {
    # The shared implementation: the one place that SHOULD set it.
    "library/tools/resolve_lock.py": (1, "shared: assert_current_timeline"),
    # Capability probes: guarded try/except checks, never a build write.
    "library/steps/step_6_01_render/probe_resolve_capabilities.py": (
        2, "probe: capability checks only"),
}

METHOD = "SetCurrentTimeline"


def _sites(path: Path) -> list:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return [node.lineno for node in ast.walk(tree)
            if isinstance(node, ast.Attribute) and node.attr == METHOD]


def test_every_cursor_setter_is_registered():
    """No shipped file sets the cursor without a registry row."""
    seen = {}
    for scope in SCOPES:
        root = REPO_ROOT / scope
        if not root.exists():
            continue
        for path in sorted(root.rglob("*.py")):
            sites = _sites(path)
            if sites:
                seen[path.relative_to(REPO_ROOT).as_posix()] = sites
    unregistered = sorted(set(seen) - set(CURSOR_SETTERS))
    assert unregistered == [], (
        f"new {METHOD} call sites with no registry row: "
        + ", ".join(f"{name} (lines {seen[name]})"
                    for name in unregistered)
        + ". Route the establishment through library/tools/resolve_lock.py "
          "(assert_current_timeline in a lease, cursor_fence for a guarded "
          "section, cursor_excursion for move-and-return), or register the "
          "site in CURSOR_SETTERS with a reason."
    )


def test_registered_counts_are_exact():
    """A registry row that no longer matches is a lie about what is owed."""
    for relpath, (expected, _note) in CURSOR_SETTERS.items():
        sites = _sites(REPO_ROOT / relpath)
        assert len(sites) == expected, (
            f"{relpath}: registry says {expected} {METHOD} sites, "
            f"found {len(sites)} at lines {sites}. "
            + ("Migrated a site to the shared layer? Delete the row's "
               "count down (or the row, if it reached zero)."
               if len(sites) < expected else
               "New establishment? Route it through resolve_lock or "
               "raise this row's count with a reason.")
        )
