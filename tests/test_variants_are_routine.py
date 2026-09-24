"""Two versions of a reel alive at once, without breaking anything else.

`build_reel_variants` and `versions.variants` were written, tested and
demonstrated once - and nothing routine used them. Making them routine
is not only a command: the round diff, the sign-off, the archive and the
conformance verifier all now key on REEL IDENTITY, and a second timeline
carrying a reel's name plus a suffix has to participate in each of them
rather than break them.

This file pins how a variant participates, one mechanism per test, each
failing if its mechanism is removed.

- the conformance sweep does not grade a live variant, for the sharper
  version of the reason it does not grade a retired generation;
- a variant that DECLARES is built from its own branch, because that is
  the only place a declaration lives;
- the variant build reads every per-reel declaration the rebuild reads,
  so a variant differs from the approved reel only where its spec says;
- the CLI exposes the whole workflow.
"""
import ast
import pathlib

import pytest

from library.tools.versions import variants
from library.tools.reel_conformance_verifier import grades_as_a_reel

REEL = "Reel 09 - your-website-is-only-20-percent (final)"
CUTAWAY = f"{REEL} (reaction-cutaway)"

SOURCE = (pathlib.Path(__file__).resolve().parents[1]
          / "library" / "tools" / "reel_build.py")


# ── The conformance verifier ─────────────────────────────────────

def test_a_live_variant_is_not_graded_by_the_plan_verifier():
    """A variant's offsets are INTENTIONAL deviations from the plan, so
    the plan gate fails them BY DESIGN - which is exactly why
    `build_reel_variants` grades its own output with the STRUCTURAL
    verifier instead. Grading one here would report a designed
    difference as a defect: a gate that fails correct output, which is
    no more coverage than one that cannot fail (AGENTS.md 10.4)."""
    assert grades_as_a_reel(REEL, variant_names={CUTAWAY})
    assert not grades_as_a_reel(CUTAWAY, variant_names={CUTAWAY})


def test_a_variant_is_excluded_by_DECLARATION_never_by_a_name_guess():
    """`(final)` is a reel's own name and `(reaction-cutaway)` is a
    variation of it, and NO syntactic rule tells them apart. Excluding
    by parse would silently drop a deliverable out of the sweep."""
    # Nothing declared: the same timeline is graded, as it always was.
    assert grades_as_a_reel(CUTAWAY)
    assert grades_as_a_reel(CUTAWAY, variant_names=set())
    # A reel whose own name ends in parens is never mistaken for one.
    assert grades_as_a_reel(REEL, variant_names={CUTAWAY})


def test_a_variant_named_by_the_operator_is_still_graded():
    """Refusing to look at something the operator asked for by name is
    a different failure from quietly grading what they did not - the
    same escape a retired generation has."""
    assert grades_as_a_reel(CUTAWAY, only_reels=[CUTAWAY],
                            variant_names={CUTAWAY})


def test_the_retired_generation_exclusion_still_holds():
    """The variant rule was added beside the archive rule, not over
    it."""
    from library.tools import reel_retirement as retire

    archived = retire.archived_name(REEL, 3)
    assert not grades_as_a_reel(archived, variant_names={CUTAWAY})
    assert not grades_as_a_reel("Podcast - Synced",
                                variant_names={CUTAWAY})


def test_declared_variants_resolve_through_the_plans_own_reel_name(
        tmp_path, monkeypatch):
    """A variant name is its reel's name plus a suffix, and the reel's
    name comes from the proposal - the same read the variant builder
    makes, so the two cannot disagree."""
    project = tmp_path / "p"
    (project / "pipeline_output" / "review").mkdir(parents=True)
    variants.write_variant_specs(str(project), {"variants": {
        "9": [{"suffix": " (reaction-cutaway)",
               "cutaway": {"hide_angle": "A", "window_seconds": [1, 2]}}],
        "13": [{"suffix": " (tight)", "declares": ["caption_timing"]}]}})

    class Moment:
        def __init__(self, number, name):
            self.number = number
            self.timeline_name = name

    monkeypatch.setattr(
        "library.tools.reel_proposal.read_proposal",
        lambda path: [Moment(9, REEL),
                      Moment(13, "Reel 13 - the-accounting-firm (final)")])
    monkeypatch.setattr("library.tools.reel_proposal.proposal_path",
                        lambda folder: "unused")
    assert variants.declared_variant_timelines(str(project)) == {
        CUTAWAY, "Reel 13 - the-accounting-firm (final) (tight)"}


def test_a_project_that_declares_no_variant_is_unchanged(tmp_path):
    """An absent or unreadable record answers EMPTY, so every live
    timeline is graded exactly as it was before this existed - the loud
    direction."""
    assert variants.declared_variant_timelines(str(tmp_path)) == set()


# ── A declaring variant is built from its own branch ─────────────

def _repo(tmp_path):
    import subprocess

    project = tmp_path / "p"
    (project / "pipeline_output" / "review").mkdir(parents=True)
    for args in (("init", "-b", "master"),
                 ("config", "user.email", "t@t"),
                 ("config", "user.name", "t")):
        subprocess.run(["git", *args], cwd=project, check=True,
                       capture_output=True)
    (project / "project.yaml").write_text("resolve: {}\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=project, check=True,
                   capture_output=True)
    subprocess.run(["git", "commit", "-m", "base"], cwd=project,
                   check=True, capture_output=True)
    return project


def test_a_seam_variant_builds_from_any_branch(tmp_path):
    """A seam offset is IN the spec, so the builder has everything it
    needs wherever it is - which is what lets two seam treatments be
    built side by side in ONE atomic call."""
    project = _repo(tmp_path)
    spec = {"suffix": " (reaction-cutaway)",
            "cutaway": {"hide_angle": "A", "window_seconds": [1, 2]}}
    assert variants.branch_requirement(str(project), 9, spec) == ""


def test_a_declaring_variant_refuses_off_its_branch(tmp_path):
    """Its difference is not in the spec at all: it is the CONTENT of
    `external/<store>.json` on its own branch. Built from the wrong
    branch it would carry the other version's declaration and differ
    from the approved reel NOWHERE, reported as a comparison."""
    project = _repo(tmp_path)
    spec = {"suffix": " (cta-b)", "declares": ["reel_ending"]}
    blocked = variants.branch_requirement(str(project), 9, spec)
    assert blocked
    assert "variant/r09-cta-b" in blocked
    assert "checkout" in blocked






# ── The declarations reach the variant build ─────────────────────

def _module_functions():
    """Every top-level function `reel_build.py` defines, by name."""
    return {node.name: node
            for node in ast.parse(SOURCE.read_text(encoding="utf-8")).body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))}


def _function(name):
    try:
        return _module_functions()[name]
    except KeyError:
        raise AssertionError(f"reel_build.py defines no {name}()")


def _called_names(func):
    names = set()
    for node in ast.walk(func):
        if isinstance(node, ast.Call):
            target = node.func
            if isinstance(target, ast.Attribute):
                names.add(target.attr)
            elif isinstance(target, ast.Name):
                names.add(target.id)
    return names


def _called_names_transitive(name):
    """What `name` reaches, following same-module helpers.

    #1214 moved the rebuild's ending handling behind the shared
    spelling `derive_reel_ranges_and_cards`, which calls
    `apply_ending` one frame out. A flat name check reads that
    refactor as the rebuild dropping a declaration and skips - which
    is how a green suite came to exit 1. Reading a declaration is
    what happens during the build, at whatever depth, so follow the
    calls down to a fixpoint over this module's own functions.
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


#: The per-reel DECLARATION readers. These are the stores a variant may
#: now DIFFER in, so they are also the ones it must READ: a widened spec
#: that named a store the builder never opened would be a vocabulary for
#: a difference the picture cannot carry.
DECLARATION_READERS = ("load_intent", "load_pins", "resolve_ending",
                       "apply_ending", "apply_pins")


def test_the_rebuild_reads_these():
    """A SUBSET check with an empty left side passes forever."""
    called = _called_names_transitive("rebuild_reels_in_project")
    assert set(DECLARATION_READERS) & called, (
        "the rebuild reads none of these - either it stopped reading "
        "the captain's declarations, or they were renamed and this "
        "list is stale.")


@pytest.mark.parametrize("reader", DECLARATION_READERS)
def test_the_variant_build_reads_every_declaration_the_rebuild_reads(
        reader):
    """Measured before this landed: `build_reel_variants` read NONE of
    them, so every variant was built with no declared ending, no pinned
    overlay positions and no caption-timing pins - three differences
    from the approved reel on top of the one it was built to show, and
    all three structurally perfect, so conformance passed them."""
    rebuild = _called_names_transitive("rebuild_reels_in_project")
    variant = _called_names_transitive("build_reel_variants")
    if reader not in rebuild:
        pytest.fail(
            f"rebuild_reels_in_project no longer reaches {reader}(), even "
            f"through its shared helpers - either the rebuild stopped "
            f"reading a declaration (a regression in the rebuild), or "
            f"DECLARATION_READERS is stale and must be updated to the new "
            f"spelling. Skipping here would hide which, and the session "
            f"hook fails an undeclared skip anyway.")
    assert reader in variant, (
        f"rebuild_reels_in_project reads {reader}() and "
        f"build_reel_variants does not. A variant that drops a "
        f"declaration differs from the approved reel somewhere other "
        f"than where its spec says, so the comparison is not the one "
        f"the captain asked for - and it is structurally perfect, so "
        f"conformance passes it.")


@pytest.mark.parametrize("keyword", ["ending", "overlay_intent"])
def test_the_declarations_reach_build_reel_timeline(keyword):
    """Reading a declaration and not passing it on is the same defect
    one step later, and it reads as honoured."""
    for node in ast.walk(_function("build_reel_variants")):
        if (isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "build_reel_timeline"):
            assert keyword in {kw.arg for kw in node.keywords}, (
                f"build_reel_variants calls build_reel_timeline without "
                f"{keyword}=, so the declaration it read never reaches "
                f"the picture.")
            return
    raise AssertionError("build_reel_variants calls no "
                         "build_reel_timeline()")






# ── The command ──────────────────────────────────────────────────



def test_choosing_requires_a_reason_at_the_command_line():
    """A choice with no reason is not a decision anybody can read six
    weeks later, so `--why` is required rather than defaulted."""
    import subprocess
    import sys

    result = subprocess.run(
        [sys.executable, "manage_project.py", "variant", "x", "choose",
         "9", " (a)"],
        cwd=str(pathlib.Path(__file__).resolve().parents[1]),
        capture_output=True, encoding="utf-8", check=False)
    assert result.returncode != 0
    assert "--why" in result.stderr
