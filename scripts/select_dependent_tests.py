#!/usr/bin/env python3
"""The narrowest selection covering a change, computed - not judged.

AGENTS.md 9 says to run the tests of what a change touches, in both
directions: BACKWARD (what the changed module uses) and FORWARD (what
uses the changed module, plus one hop through re-exporters).
`tests/tooling/test_select_dependent_tests.py` pins the incident edges.

Usage: `python3 scripts/select_dependent_tests.py <changed-file> ...`
prints test paths relative to the repo root, one per line.

`--loop` prints the per-change loop instead: the `tests/unit/<subsystem>/`
directory of every subsystem the change reaches, plus `tests/contracts/`,
the global contract suite (`tests/layers.py`). A subsystem is reached when
the change is one of its test files, or when the dependent selection
above picks one of its unit tests. Directories, not files, so a new test
in a reached subsystem runs without anyone listing it.

The procedure errs WIDE, never narrow. A wider selection costs
seconds; a narrower one costs a red main.
"""

from __future__ import annotations

import ast
import os
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from tests import layers

LIBRARY = os.path.join(REPO_ROOT, "library")
TESTS = os.path.join(REPO_ROOT, "tests")

# A shared resolver earns a named contract set: the test files that
# feed entries through it. Any change to the resolver runs the whole
# set. Pinned by `tests/tooling/test_select_dependent_tests.py`.
CONTRACT_SETS = {
    "motion_graphics_plan": [
        "tests/unit/captions/test_caption_band.py",
        "tests/unit/captions/test_explainer_plan.py",
        "tests/unit/captions/test_motion_graphics_data_slot.py",
        "tests/unit/captions/test_motion_graphics_plan.py",
        "tests/unit/reels/test_reel_semantic_visual.py",
        "tests/unit/picture/test_semantic_visual.py",
        "tests/unit/audio/test_speaker_identity.py",
    ],
}


def _imported_stems(tree: ast.Module) -> set[str]:
    """Every module stem imported anywhere in the tree, at any depth.

    One AST walk sees function-level imports exactly like top-level
    ones, so no line-anchored regex is needed.
    """
    stems = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                stems.add(alias.name.split(".")[-1])
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                stems.add(node.module.split(".")[-1])
            for alias in node.names:
                if alias.name != "*":
                    stems.add(alias.name.split(".")[-1])
    return stems


def _parse(path: str) -> ast.Module | None:
    try:
        with open(path, encoding="utf-8") as handle:
            return ast.parse(handle.read())
    except (OSError, SyntaxError):
        return None


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


def _imported_by_module(abs_path):
    """Stems the module itself imports (AST, all depths)."""
    tree = _parse(abs_path)
    if tree is None:
        return set()
    return _imported_stems(tree)


def _reexports(text):
    """Whether the module presents itself as a re-exporter."""
    return "re-export" in text.lower()


def _read(path):
    with open(path, encoding="utf-8") as handle:
        return handle.read()


def select(changed):
    """Test paths (repo-relative) covering the changed files."""
    library_modules = _modules_under(LIBRARY)
    test_modules = _modules_under(TESTS)
    # One index per invocation: every module's imported stems, parsed
    # once. A per-changed-file rescan would reparse the whole tree
    # once per file.
    lib_imports = {}
    lib_text = {}
    for dotted, abs_path in library_modules:
        tree = _parse(abs_path)
        lib_imports[dotted] = _imported_stems(tree) if tree is not None else set()
        if tree is None:
            try:
                lib_text[dotted] = _read(abs_path)
            except OSError:
                lib_text[dotted] = ""
        else:
            try:
                lib_text[dotted] = _read(abs_path)
            except OSError:
                lib_text[dotted] = ""
    test_imports = {}
    for dotted, abs_path in test_modules:
        tree = _parse(abs_path)
        test_imports[abs_path] = _imported_stems(tree) if tree is not None else set()
    test_files = {os.path.basename(abs_path): abs_path
                  for _dotted, abs_path in test_modules
                  if os.path.basename(abs_path).startswith("test_")}
    selected = set()
    for path in changed:
        abs_path = (path if os.path.isabs(path)
                    else os.path.join(REPO_ROOT, path))
        stem = os.path.splitext(os.path.basename(abs_path))[0]

        resolvers = set()
        if stem in CONTRACT_SETS:
            resolvers.add(stem)

        # BACKWARD: what the changed module uses. Only the used
        # module's OWN test file plus any contract set it belongs to -
        # every test importing e.g. `os` does not cover this change.
        uses = _imported_by_module(abs_path)
        for used in uses:
            if used in CONTRACT_SETS:
                resolvers.add(used)
            same = "test_" + used + ".py"
            if same in test_files:
                selected.add(os.path.relpath(test_files[same], REPO_ROOT))

        # FORWARD: what uses the changed module. Only RE-EXPORTERS
        # extend the carrier set: a module that re-exports a name is
        # imported under its own stem by tests that still exercise
        # this change. Every other importer's own test fan-out is
        # deliberately NOT followed: following it turns every
        # selection into the whole suite.
        direct = {dotted for dotted, stems in lib_imports.items()
                  if stem in stems
                  and not (dotted.endswith("." + stem) or dotted == stem)}
        carriers = {stem}
        for importer in direct:
            if _reexports(lib_text.get(importer, "")):
                carriers.add(importer.split(".")[-1])
        for carrier in carriers:
            for dotted, test_abs in test_modules:
                rel = os.path.relpath(test_abs, REPO_ROOT)
                if os.path.basename(test_abs) == "test_" + carrier + ".py":
                    selected.add(rel)
                elif carrier in test_imports.get(test_abs, set()):
                    selected.add(rel)
            if carrier in CONTRACT_SETS:
                resolvers.add(carrier)
        for dotted, test_abs in test_modules:
            rel = os.path.relpath(test_abs, REPO_ROOT)
            if os.path.basename(test_abs) == "test_" + stem + ".py":
                selected.add(rel)
            elif stem in test_imports.get(test_abs, set()):
                selected.add(rel)

        for resolver in resolvers:
            selected.update(CONTRACT_SETS[resolver])

    return sorted(selected)


def loop(changed):
    """The per-change loop: reached `unit/` subsystems plus `contracts/`."""
    def absolute(path):
        return os.path.realpath(os.path.join(REPO_ROOT, path))

    # A changed test reaches its own subsystem; a changed module reaches
    # the subsystems of the unit tests its dependent selection picks.
    tests_root = layers.TESTS_ROOT.as_posix() + "/"
    modules = [p for p in changed
               if not absolute(p).startswith(tests_root)]
    subsystems = set()
    for path in [*changed, *(select(modules) if modules else [])]:
        subsystem = layers.subsystem_of(absolute(path))
        if subsystem is not None:
            subsystems.add(subsystem)
    dirs = [layers.unit_dir(name) for name in sorted(subsystems)]
    dirs.append(layers.layer_dir("contracts"))
    return [os.path.relpath(d, REPO_ROOT) + os.sep for d in dirs]


def main(argv):
    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__.strip().splitlines()[0])
        print("usage: select_dependent_tests.py [--loop] <changed-file> ...")
        return 0
    if argv[0] == "--loop":
        for path in loop(argv[1:]):
            print(path)
        return 0
    for path in select(argv):
        print(path)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
