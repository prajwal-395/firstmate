#!/usr/bin/env python3
"""The narrowest selection covering a change, computed - not judged.

AGENTS.md 9 says to run the tests of what a change touches, in both
directions: BACKWARD (what the changed module uses) and FORWARD (what
uses the changed module, plus one hop through re-exporters).
`tests/tooling/test_select_dependent_tests.py` pins the incident edges.

Usage: `python3 scripts/select_dependent_tests.py <changed-file> ...`
prints test paths relative to the repo root, one per line.

`--distances` prints `<hops> <path>` instead: the import distance
from the change to each selected test (0 the changed test itself, 1
a direct importer or same-stem test, 2 a hop through a re-exporter
or a contract set). Tier-1 fills its time budget in this order.

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
import hashlib
import json
import os
import sys
import tempfile

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# ren-ci selects against the PR CHECKOUT, not the tree this script file
# lives in: REN_SELECT_ROOT points the index at the tree under test.
# Without it a PR predating a test file would select paths its own tree
# does not have, and pytest would error on the missing files.
REPO_ROOT = os.environ.get("REN_SELECT_ROOT", REPO_ROOT)
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from tests import layers

LIBRARY = os.path.join(REPO_ROOT, "library")
TESTS = os.path.join(REPO_ROOT, "tests")

# A shared resolver earns a named contract set: the test files that
# feed entries through it. Any change to the resolver runs the whole
# set. Pinned by `tests/tooling/test_select_dependent_tests.py`.
CONTRACT_SETS = {
    "qualification_workflow": [
        "tests/unit/reels/test_reel_build_whole_lease.py",
        "tests/unit/resolve/test_reel_promotion_job.py",
        "tests/unit/reels/test_reel_conformance_verifier.py",
        "tests/unit/picture/test_picture_qa.py",
        "tests/unit/resolve/test_resolve_render.py",
        "tests/scenarios/test_golden_conversation.py",
    ],
    "motion_graphics_plan": [
        "tests/unit/audio/test_speaker_identity.py",
        "tests/unit/captions/test_caption_layout.py",
        "tests/unit/captions/test_full_frame_element.py",
        "tests/unit/captions/test_motion_graphics_plan.py",
        "tests/unit/picture/test_semantic_visual.py",
        "tests/unit/reels/test_reel_conformance_verifier.py",
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
    except (OSError, SyntaxError, UnicodeDecodeError):
        # A changed PNG (or any binary a PR carries) is not a module:
        # it contributes no stems, it never crashes the selection.
        # ren-ci shells this script per PR, and PRs carry images.
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


# The import index survives across invocations: every Tier-1 pays one
# selection before pytest starts (17-26s wall, ~4.5s CPU, IO-bound AST
# walk over ~1000 files), and re-parsing the whole tree per run is
# what costs it. The cache stores each file's imported stems keyed by
# its content fingerprint (size + mtime); a repeat selection re-stats
# the tree and re-parses only what moved, so it costs about its CPU
# time (~5s). One cache file per checkout (the filename hashes
# REPO_ROOT), overridable via REN_SELECTOR_CACHE_DIR, off via
# REN_SELECTOR_NO_CACHE=1. An unreadable or version-mismatched cache
# is a cold start, never an error: the selection parses everything,
# exactly as before.
SELECTOR_CACHE_VERSION = 1
_MEMO: dict = {}


def _selector_cache_path() -> str:
    directory = os.environ.get("REN_SELECTOR_CACHE_DIR", "").strip()
    if not directory:
        cache_root = os.environ.get(
            "XDG_CACHE_HOME", os.path.join(os.path.expanduser("~"), ".cache"))
        directory = os.path.join(cache_root, "ren")
    digest = hashlib.sha256(
        os.path.abspath(REPO_ROOT).encode("utf-8")).hexdigest()[:16]
    return os.path.join(directory, f"selector-index-{digest}.json")


def _cache_enabled() -> bool:
    return os.environ.get(
        "REN_SELECTOR_NO_CACHE", "").strip().lower() not in (
            "1", "true", "yes")


def _read_cache(path: str) -> dict:
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return {}
    if (not isinstance(data, dict)
            or data.get("version") != SELECTOR_CACHE_VERSION
            or not isinstance(data.get("files"), dict)):
        return {}
    return data["files"]


def _write_cache(path: str, records: dict) -> None:
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=".selector-index.",
                                    dir=os.path.dirname(path))
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump({"version": SELECTOR_CACHE_VERSION,
                           "files": records}, handle, sort_keys=True)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
        os.replace(tmp, path)
    except OSError:
        # A cache that cannot be written is a slower next run, not a
        # failed selection.
        pass


def _parse_file(abs_path: str) -> tuple:
    """One file's (imported stems, presents-as-re-exporter)."""
    tree = _parse(abs_path)
    stems = _imported_stems(tree) if tree is not None else set()
    reexport = False
    if abs_path.startswith(LIBRARY + os.sep):
        try:
            reexport = _reexports(_read(abs_path))
        except OSError:
            reexport = False
    return stems, reexport


def _load_records() -> dict:
    """Every `*.py` under library/ and tests/ as cached parse records.

    A record is valid while its size and mtime match the cache entry;
    anything else (new, moved, rewritten, unreadable cache) re-parses
    that file alone. Added and removed files fall out of the walk.
    """
    rels = []
    for root in (LIBRARY, TESTS):
        for dirpath, _dirnames, filenames in os.walk(root):
            for name in filenames:
                if name.endswith(".py"):
                    rels.append(os.path.relpath(
                        os.path.join(dirpath, name), REPO_ROOT))
    cached = _MEMO.get(REPO_ROOT)
    if cached is None:
        cached = _read_cache(_selector_cache_path()) if _cache_enabled() else {}
        _MEMO[REPO_ROOT] = cached
    records = {}
    dirty = False
    for rel in rels:
        abs_path = os.path.join(REPO_ROOT, rel)
        try:
            stat = os.stat(abs_path)
        except OSError:
            dirty = True
            continue
        old = cached.get(rel)
        if (isinstance(old, dict)
                and old.get("size") == stat.st_size
                and old.get("mtime") == stat.st_mtime_ns
                and isinstance(old.get("stems"), list)):
            records[rel] = old
            continue
        stems, reexport = _parse_file(abs_path)
        record: dict = {"size": stat.st_size, "mtime": stat.st_mtime_ns,
                        "stems": sorted(stems)}
        if abs_path.startswith(LIBRARY + os.sep):
            record["reexport"] = bool(reexport)
        records[rel] = record
        dirty = True
    if dirty:
        _MEMO[REPO_ROOT] = records
        if _cache_enabled():
            _write_cache(_selector_cache_path(), records)
    return records


def _load_index() -> tuple:
    """The selection's whole index: library imports, re-export flags,
    test imports, test files by basename. Identical content to the
    per-invocation walk it replaces; the walk now happens only for
    files the cache has not seen."""
    records = _load_records()
    lib_imports: dict = {}
    lib_reexport: dict = {}
    test_imports: dict = {}
    test_files: dict = {}
    for rel, record in records.items():
        abs_path = os.path.join(REPO_ROOT, rel)
        stems = set(record.get("stems", []))
        if abs_path.startswith(LIBRARY + os.sep):
            dotted = rel[:-len(".py")].replace(os.sep, ".")
            lib_imports[dotted] = stems
            lib_reexport[dotted] = bool(record.get("reexport", False))
        elif abs_path.startswith(TESTS + os.sep):
            test_imports[abs_path] = stems
            if os.path.basename(abs_path).startswith("test_"):
                test_files[os.path.basename(abs_path)] = abs_path
    return lib_imports, lib_reexport, test_imports, test_files


def select(changed):
    """Test paths (repo-relative) covering the changed files."""
    return sorted(_select_with_distances(changed))


def select_with_distances(changed):
    """Test paths mapped to import distance from the changed files.

    0 is the changed test itself, 1 a direct importer or same-stem
    test, 2 a hop through a re-exporter or a contract-set entry.
    The minimum wins when several changed files reach one test.
    """
    return dict(_select_with_distances(changed))


def _select_with_distances(changed):
    # One index per invocation: every module's imported stems, parsed
    # once. A per-changed-file rescan would reparse the whole tree
    # once per file. The index is cached across invocations
    # (`_load_index`); only files the cache has not seen are parsed.
    lib_imports, lib_reexport, test_imports, test_files = _load_index()
    test_modules = sorted(
        (rel, abs_path) for rel, abs_path in
        ((os.path.relpath(path, REPO_ROOT), path)
         for path in test_imports))
    selected: dict = {}
    def add(path, distance):
        if path in selected:
            selected[path] = min(selected[path], distance)
        else:
            selected[path] = distance
    for path in changed:
        abs_path = (path if os.path.isabs(path)
                    else os.path.join(REPO_ROOT, path))
        stem = os.path.splitext(os.path.basename(abs_path))[0]

        # A changed test is itself part of the dependent selection. The
        # import graph below finds tests that exercise changed modules,
        # but a test module has no `test_<stem>.py` counterpart to find.
        absolute = os.path.abspath(abs_path)
        if (os.path.commonpath([absolute, TESTS]) == TESTS
                and os.path.basename(absolute).startswith("test_")
                and absolute.endswith(".py") and os.path.isfile(absolute)):
            add(os.path.relpath(absolute, REPO_ROOT), 0)

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
                add(os.path.relpath(test_files[same], REPO_ROOT), 1)

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
            if lib_reexport.get(importer, False):
                carriers.add(importer.split(".")[-1])
        for carrier in carriers:
            hop = 1 if carrier == stem else 2
            for dotted, test_abs in test_modules:
                rel = os.path.relpath(test_abs, REPO_ROOT)
                if os.path.basename(test_abs) == "test_" + carrier + ".py":
                    add(rel, hop)
                elif carrier in test_imports.get(test_abs, set()):
                    add(rel, hop)
            if carrier in CONTRACT_SETS:
                resolvers.add(carrier)
        for dotted, test_abs in test_modules:
            rel = os.path.relpath(test_abs, REPO_ROOT)
            if os.path.basename(test_abs) == "test_" + stem + ".py":
                add(rel, 1)
            elif stem in test_imports.get(test_abs, set()):
                add(rel, 1)

        for resolver in resolvers:
            for entry in CONTRACT_SETS[resolver]:
                add(entry, 2)

    # Pytest treats conftest.py as configuration, not a test module. It can
    # enter the import graph as a carrier, but passing it as a path causes
    # module-name collisions when another subsystem has its own conftest.
    return {path: distance for path, distance in selected.items()
            if os.path.basename(path).startswith("test_")
            and path.endswith(".py")}


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
        print("usage: select_dependent_tests.py [--loop] [--distances] "
              "<changed-file> ...")
        return 0
    if argv[0] == "--loop":
        for path in loop(argv[1:]):
            print(path)
        return 0
    if argv[0] == "--distances":
        distances = select_with_distances(argv[1:])
        for path in sorted(distances):
            print(f"{distances[path]} {path}")
        return 0
    for path in select(argv):
        print(path)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
