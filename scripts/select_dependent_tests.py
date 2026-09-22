#!/usr/bin/env python3
"""The narrowest selection covering a change, computed - not judged.

AGENTS.md 9 says to run the tests of what a change touches, in both
directions. This script is what "both directions" means, mechanically:

- BACKWARD (what the changed module uses): every module it imports -
  matched textually, so FUNCTION-level imports count exactly like
  top-level ones. PR #1258's miss was this edge in reverse:
  `speaker_identity` imports `motion_graphics_plan` at function level
  (`speaker_identity.py:313`), so a top-level import graph says the
  speaker tests do not cover a `resolve_plan` change, and the repo's
  own end-to-end guarantee sat red through the merge.
- FORWARD (what uses the changed module): every module that imports
  it, plus ONE transitive hop through re-exporters (a module that
  re-exports a name carries its dependents one hop further out -
  e.g. step 4.06's `generate_motion_props` re-exports `resolve_plan`).
- CONTRACT SETS below: a shared resolver earns a named set of the
  test files that feed it. Any change to the resolver - or to anything
  that resolves through it - runs the whole set, whatever else it
  runs. A named set rots unless this procedure rebuilds who belongs;
  a procedure misjudges depth unless the set pins the answer. The two
  are kept together here so neither simplifies away.

Usage: `python3 scripts/select_dependent_tests.py <changed-file> ...`
prints test paths relative to the repo root, one per line.

The procedure errs WIDE, never narrow: same-stem collisions (every
step has a `bridge.py`) qualify by package directory but still match
broadly, and non-Python changed files match by stem mention. A wider
selection costs seconds; a narrower one costs a red main.
"""

from __future__ import annotations

import os
import re
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LIBRARY = os.path.join(REPO_ROOT, "library")
TESTS = os.path.join(REPO_ROOT, "tests")

# A shared resolver earns a named contract set: the test files that
# feed entries through it. Any change to the resolver runs the whole
# set. Pinned by `tests/test_select_dependent_tests.py`.
CONTRACT_SETS = {
    "motion_graphics_plan": [
        "tests/test_caption_band.py",
        "tests/test_explainer_plan.py",
        "tests/test_motion_graphics_data_slot.py",
        "tests/test_motion_graphics_plan.py",
        "tests/test_reel_semantic_visual.py",
        "tests/test_review_panel.py",
        "tests/test_semantic_visual.py",
        "tests/test_speaker_identity.py",
        "tests/test_website_panel.py",
    ],
}

def _imports_stem_line(line, stem):
    """Whether one source line imports `stem`.

    Line-anchored (`^\\s*`), so a docstring that merely MENTIONS the
    module never matches - but ANY indentation does, so a
    function-level import counts exactly like a top-level one. PR
    #1258's miss was this edge: `speaker_identity` imports
    `motion_graphics_plan` inside a function (`speaker_identity.py:313`),
    which a top-level import graph never sees.
    """
    line = line.split("#", 1)[0]
    match = re.match(r"\s*import\s+(.+)", line)
    if match:
        for name in match.group(1).split(","):
            name = name.strip().split(" as ")[0].strip()
            if name.split(".")[-1] == stem or name == stem:
                return True
        return False
    match = re.match(r"\s*from\s+([\w.]+)\s+import\s+(.+)", line)
    if match:
        module, names = match.groups()
        if module.split(".")[-1] == stem:
            return True
        for name in re.split(r"[,\s()]+", names):
            name = name.strip().split(" as ")[0].strip()
            if name == stem:
                return True
    return False


def _imports_stem(text, stem):
    return any(_imports_stem_line(line, stem) for line in text.splitlines())


def _modules_under(root):
    """Every `*.py` under root as dotted path + absolute path."""
    out = []
    for dirpath, _dirnames, filenames in os.walk(root):
        for name in filenames:
            if not name.endswith(".py"):
                continue
            abs_path = os.path.join(dirpath, name)
            rel = os.path.relpath(abs_path, REPO_ROOT)[:-len(".py")]
            out.append((rel.replace(os.sep, "."), abs_path))
    return out


def _read(path):
    with open(path, encoding="utf-8") as handle:
        return handle.read()


def _importers_of(stem, package, modules):
    """Dotted paths of modules importing `stem` (any depth)."""
    found = set()
    for dotted, abs_path in modules:
        if dotted.endswith("." + stem) or dotted == stem:
            continue
        try:
            text = _read(abs_path)
        except OSError:
            continue
        if _imports_stem(text, stem):
            found.add(dotted)
            continue
        if package and re.search(
                r"\b" + re.escape(package + "." + stem) + r"\b", text):
            found.add(dotted)
    return found


def _imported_by_module(abs_path):
    """Stems the module itself imports (textual, all depths)."""
    try:
        text = _read(abs_path)
    except OSError:
        return set()
    stems = set()
    for match in re.finditer(
            r"(?m)^\s*from\s+([\w.]+)\s+import\s+(.+)$", text):
        module, names = match.groups()
        stems.add(module.split(".")[-1])
        for name in names.split(","):
            name = name.strip().split(" as ")[0].strip()
            if name and name != "*":
                stems.add(name)
    for match in re.finditer(r"(?m)^\s*import\s+(.+)$", text):
        for name in match.group(1).split(","):
            name = name.strip().split(" as ")[0].strip()
            if name:
                stems.add(name.split(".")[-1])
    return stems


def _tests_for_stem(stem, test_modules):
    """Test files covering `stem`: same-stem file plus importers."""
    found = set()
    for dotted, abs_path in test_modules:
        rel = os.path.relpath(abs_path, REPO_ROOT)
        if os.path.basename(abs_path) == "test_" + stem + ".py":
            found.add(rel)
            continue
        try:
            text = _read(abs_path)
        except OSError:
            continue
        if _imports_stem(text, stem):
            found.add(rel)
    return found


def _reexports(text):
    """Whether the module presents itself as a re-exporter."""
    return "re-export" in text.lower()


def select(changed):
    """Test paths (repo-relative) covering the changed files."""
    library_modules = _modules_under(LIBRARY)
    test_modules = _modules_under(TESTS)
    dotted_by_stem = {}
    for dotted, _abs in library_modules:
        dotted_by_stem.setdefault(dotted.split(".")[-1], set()).add(dotted)

    selected = set()
    for path in changed:
        abs_path = (path if os.path.isabs(path)
                    else os.path.join(REPO_ROOT, path))
        stem = os.path.splitext(os.path.basename(abs_path))[0]
        parent = os.path.basename(os.path.dirname(abs_path))
        package = None
        if abs_path.startswith(LIBRARY + os.sep):
            rel_dir = os.path.dirname(
                os.path.relpath(abs_path, REPO_ROOT))
            package = rel_dir.replace(os.sep, ".")

        resolvers = set()
        if stem in CONTRACT_SETS:
            resolvers.add(stem)

        # BACKWARD: what the changed module uses. Only the used
        # module's OWN test file plus any contract set it belongs to -
        # every test importing e.g. `os` does not cover this change,
        # and pulling those in turns every selection into the whole
        # suite. The direction that validates a change to a shared
        # module is FORWARD (below); backward exists so a change to a
        # PRODUCER also runs the resolver contract set it feeds.
        uses = _imported_by_module(abs_path)
        for used in uses:
            if used in CONTRACT_SETS:
                resolvers.add(used)
            same = os.path.join(TESTS, "test_" + used + ".py")
            if os.path.isfile(same):
                selected.add(os.path.relpath(same, REPO_ROOT))

        # FORWARD: what uses the changed module - the tests importing
        # it directly are the contract tests (producers resolving
        # through it, readers of its output). Only RE-EXPORTERS extend
        # the carrier set: a module that re-exports a name is imported
        # under its own stem by tests that still exercise this change
        # (e.g. step 4.06's `generate_motion_props` re-exports
        # `resolve_plan`). Every other importer's own test fan-out is
        # deliberately NOT followed: tests importing a heavy consumer
        # (every test importing `reel_build`) exercise this change
        # only transitively, and following them turns every selection
        # into the whole suite - the "large in the wrong direction"
        # failure of #1258. Re-exporters say so in their own text.
        direct = _importers_of(stem, package, library_modules)
        by_dotted = {dotted: abs_path for dotted, abs_path in library_modules}
        carriers = {stem}
        for importer in direct:
            abs_importer = by_dotted.get(importer)
            if abs_importer is None:
                continue
            try:
                text = _read(abs_importer)
            except OSError:
                continue
            if _reexports(text):
                carriers.add(importer.split(".")[-1])
        for carrier in carriers:
            selected.update(_tests_for_stem(carrier, test_modules))
            if carrier in CONTRACT_SETS:
                resolvers.add(carrier)
        selected.update(_tests_for_stem(stem, test_modules))

        for resolver in resolvers:
            selected.update(CONTRACT_SETS[resolver])

    return sorted(selected)


def main(argv):
    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__.strip().splitlines()[0])
        print("usage: select_dependent_tests.py <changed-file> ...")
        return 0
    for path in select(argv):
        print(path)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
