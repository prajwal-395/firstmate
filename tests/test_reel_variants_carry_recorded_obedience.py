"""A variant must carry every RECORDED OBEDIENCE the rebuild carries.

`build_reel_variants` exists to put one reel's alternative treatment
BESIDE the approved reel so the captain can compare them.  That only
works if the two differ in the one thing the variant was built to
change.  Every other input is a recorded decision the captain already
made - a closer pinned to its opening words, a struck span, a hand-set
transform, a declared grade - and a build that silently drops one is
not showing a seam, it is showing two changes at once and calling the
difference the seam.

Measured 2026-09-11 on `Podcast (field test)` / Reel 09, and this is
the incident the file exists for.  `build_reel_variants` landed reading
`transcript_corrections` and `resolve_grade_cdl` but NOT
`captain_edits.apply_closer_redraws` and NOT
`reel_look.resolve_power_grade`.  So:

* the reaction-cutaway variant opened its closer 54 frames later than
  the approved reel (source 483.563s against 481.311s) - which is
  exactly what the captain reported, *"another timeline which has the
  8 frame cutaway to akshita, but does not have the updated cta"*; and
* it would have been graded by SetCDL while the reel it is compared
  against is graded on the Color page - a comparison of grades, not of
  seams.

Both were invisible: the build printed `conformance-clean (6 checks)`
either way, because conformance grades STRUCTURE and a dropped
obedience is structurally perfect.  So the guard is not another
structural check - it is this: whatever the rebuild reads to obey the
captain, the variant path reads too.  AGENTS.md 10.4 - a gate that
cannot fail reads as coverage.
"""
import ast
import pathlib

import pytest

SOURCE = (pathlib.Path(__file__).resolve().parents[1]
          / "library" / "tools" / "reel_build.py")

#: The calls that APPLY something the captain recorded, rather than
#: something a step decided.  Named explicitly rather than inferred:
#: a heuristic over every call in a 6,000-line module would either
#: miss one or drown the failure in noise.
RECORDED_OBEDIENCE = {
    "apply_closer_redraws",   # captain_edits: the pinned closer
    "keep_exclusions",        # transcript_corrections: struck spans
    "exclusion_cuts_for_span",
    "resolve_power_grade",    # the declared Color page grade
    "resolve_grade_cdl",      # the declared CDL half
    "resolve_look",           # the declared series look
}


def _module():
    return ast.parse(SOURCE.read_text(encoding="utf-8"))


def _module_functions():
    """Every top-level function `reel_build.py` defines, by name."""
    return {node.name: node for node in _module().body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))}


def _function(name):
    try:
        return _module_functions()[name]
    except KeyError:
        raise AssertionError(f"{SOURCE.name} defines no {name}()")


def _called_names(func):
    """Every callable NAME invoked anywhere inside `func`."""
    names = set()
    for node in ast.walk(func):
        if not isinstance(node, ast.Call):
            continue
        target = node.func
        if isinstance(target, ast.Attribute):
            names.add(target.attr)
        elif isinstance(target, ast.Name):
            names.add(target.id)
    return names


def _called_names_transitive(name):
    """What `name` reaches, following same-module helpers.

    #1214 moved the rebuild's ending and exclusion handling behind the
    shared spellings `derive_reel_ranges_and_cards` and
    `moment_cuts_and_insistences`, which call `apply_ending` and
    `exclusion_cuts_for_span` one frame out. A flat name check reads
    that refactor as the rebuild dropping two obediences and skips -
    which is how a green suite came to exit 1. The obedience is what
    executes during the build, at whatever depth, so follow the calls
    down to a fixpoint over this module's own functions.
    """
    defined = _module_functions()
    seen, stack = set(), [name]
    while stack:
        current = stack.pop()
        if current in seen:
            continue
        seen.add(current)
        func = defined.get(current)
        if func is None:
            continue
        for called in _called_names(func):
            if called not in seen:
                stack.append(called)
    seen.discard(name)
    return seen


@pytest.mark.parametrize("call", sorted(RECORDED_OBEDIENCE))
def test_variant_carries_every_obedience_the_rebuild_carries(call):
    rebuild = _called_names_transitive("rebuild_reels_in_project")
    variant = _called_names_transitive("build_reel_variants")
    if call not in rebuild:
        pytest.fail(
            f"rebuild_reels_in_project no longer reaches {call}(), even "
            f"through its shared helpers - either the rebuild stopped "
            f"obeying a recorded decision (a regression in the rebuild), "
            f"or RECORDED_OBEDIENCE is stale and must be updated to the "
            f"new spelling. Skipping here would hide which, and the "
            f"session hook fails an undeclared skip anyway.")
    assert call in variant, (
        f"rebuild_reels_in_project applies {call}() and "
        f"build_reel_variants does not. A variant that drops a recorded "
        f"obedience differs from the approved reel somewhere other than "
        f"its seam, so the comparison the captain is looking at is not "
        f"the one he asked for - and conformance passes it, because a "
        f"dropped obedience is structurally perfect. Read it in "
        f"build_reel_variants the same way the rebuild does.")
