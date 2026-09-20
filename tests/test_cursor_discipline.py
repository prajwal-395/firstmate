"""The cursor is settable from N places; this test is the discipline.

`SetCurrentTimeline` is the call that killed a Fusion pass: any holder
can move the instance cursor out from under a sibling lane's build.
`library/tools/resolve_lock.py` already owns the shared layer for this
(`assert_current_timeline` inside a lease, `cursor_fence` for a guarded
section, `cursor_excursion` for a deliberate move-and-return) - but only
three callers use the fence/excursion halves, and eleven shipped files
establish the cursor directly. No discipline about asserting it can be
enforced while that set grows silently.

So this test registers every `SetCurrentTimeline` site in shipped code
(`library/`, `resolve_scripts/`, `resolve_workflow_integration/`,
`bin/`) with its owner and migration state. Adding a new call site
fails here with instructions: route the establishment through
`resolve_lock`, or register the site with a reason. Removing one means
deleting its registry row - a row no longer needed is a lie about what
is still owed.

This test changes no runtime behaviour; what it stops is silent growth
of the setter set. The migration itself is ordered in
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

SCOPES = ("library", "resolve_scripts", "resolve_workflow_integration",
          "bin")

#: file -> (expected site count, owner note). `state` is one of:
#: `shared` (the implementation itself), `probe` (capability checks that
#: never drive a build), `grandfathered` (a real establishment that the
#: migration has not routed through `resolve_lock` yet - each names its
#: target shape in docs/RESOLVE_AXI_ROUND5.md).
CURSOR_SETTERS = {
    # The shared implementation: the one place that SHOULD set it.
    "library/tools/resolve_lock.py": (1, "shared: assert_current_timeline"),
    # Capability probes: guarded try/except checks, never a build write.
    "library/steps/step_6_01_render/probe_resolve_capabilities.py": (
        2, "probe: capability checks only"),
    # Grandfathered establishments, migration order in the round-5 doc.
    "library/steps/step_6_01_render/resolve_build_timeline.py": (
        1, "grandfathered: build establishment -> cursor_fence"),
    "library/tools/build_version_control.py": (
        2, "grandfathered: promotion establishment -> cursor_fence"),
    "library/tools/draw_gain_probe.py": (
        4, "grandfathered: scratch establishment -> cursor_excursion"),
    "library/tools/execution/apply_fusion_comps.py": (
        1, "grandfathered: destination assertion -> shared layer"),
    "library/tools/execution/deliver_audio_mix.py": (
        1, "grandfathered: import establishment -> cursor_fence"),
    "library/tools/execution/resolve_render.py": (
        1, "grandfathered: render selection -> cursor_fence"),
    "library/tools/qa/timeline_sync_qa.py": (
        1, "grandfathered: QA selection -> cursor_excursion (read)"),
    "library/tools/reel_build.py": (
        1, "grandfathered: reel establishment -> cursor_fence"),
    "library/tools/reel_deliver.py": (
        1, "grandfathered: delivery selection -> cursor_fence"),
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
