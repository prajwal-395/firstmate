#!/usr/bin/env python3
"""The narrowest selection covering a change, computed - not judged.

AGENTS.md 9 says to run the tests of what a change touches, in both
directions: BACKWARD (what the changed module uses) and FORWARD (what
uses the changed module, plus one hop through re-exporters).
`tests/tooling/test_select_dependent_tests.py` pins the incident edges.

Hybrid selector (CI-06): runtime coverage evidence NARROWS the static
selection only when the CI-05 index is fresh for the exact tree under
test. Any change to a file outside the index, any dynamic-import
signal, any fixture/config/native/env change falls back to the wide
static selection below. Coverage never adds a test, only removes
forward fan-out the index proves independent; the changed test itself,
same-stem and backward entries, and contract-set expansions are always
kept, so an uncovered-but-importable edge still selects.

The index is read from `VEP_COVERAGE_INDEX_FILE` (else the shared
cache path beside the timing telemetry). `VEP_COVERAGE_TREE_SHA`,
`VEP_COVERAGE_INTERPRETER` and `VEP_COVERAGE_NATIVE` override the
freshness stamps the index is checked against (hermetic fixture
trees); unset means the checkout's own stamps.

Usage: `python3 scripts/select_dependent_tests.py <changed-file> ...`
prints test paths relative to the repo root, one per line.
`--no-coverage` forces the wide static selection.

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
    "contract_audit_surveys": [
        "tests/contracts/test_contract_audit_surveys.py",
    ],
    "qualification_workflow": [
        "tests/unit/reels/test_reel_build_whole_lease.py",
        "tests/unit/resolve/test_reel_promotion_job.py",
        "tests/unit/reels/test_reel_conformance_verifier.py",
        "tests/unit/picture/test_picture_qa.py",
        "tests/unit/resolve/test_resolve_render.py",
        "tests/scenarios/test_golden_conversation.py",
        "tests/qualification/test_golden_conversation_live.py",
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

# These editorial-output owners must select the existing live Resolve edit,
# delivery, and render scenario. Keep ownership explicit so unit-only
# selection cannot stand in for an actual rendered result.
EDITORIAL_OUTPUT_PATHS = (
    "library/tools/composed_edit.py",
    "library/tools/edit_backend.py",
    "library/tools/edit_graph.py",
    "library/tools/edit_operations.py",
    "library/tools/edit_patch.py",
    "library/tools/edit_spec.py",
    "library/tools/evidence_to_edit.py",
    "library/tools/project_model_editorial.py",
    "library/tools/timeline_decisions.py",
    "library/tools/execution/resolve_render.py",
    "library/steps/",
)
EDITORIAL_RENDER_TEST = "tests/qualification/test_golden_conversation_live.py"

# These shared scenario helpers generate the cached source media and define
# the deterministic seams used by the golden end-to-end checks. They are
# dependencies rather than pytest tests themselves, so the import graph cannot
# discover their callers when non-Python recipe data changes.
SCENARIO_DEPENDENCIES = {
    "tests/scenarios/golden.py": [
        "tests/scenarios/test_golden_conversation.py",
        "tests/scenarios/test_reel_preview.py",
        "tests/scenarios/test_golden_structure.py",
    ],
    "tests/scenarios/golden_edit.py": [
        "tests/scenarios/test_golden_monologue.py",
        "tests/scenarios/test_golden_structure.py",
    ],
    "tests/scenarios/golden_monologue_answers.json": [
        "tests/scenarios/test_golden_monologue.py",
        "tests/scenarios/test_golden_structure.py",
    ],
}
SCENARIO_HELPERS = {
    "tests/scenarios/golden.py": SCENARIO_DEPENDENCIES[
        "tests/scenarios/golden.py"],
    "tests/scenarios/golden_edit.py": SCENARIO_DEPENDENCIES[
        "tests/scenarios/golden_edit.py"],
}
# The source surveys scan repository Python files dynamically, so the
# static import graph cannot express their dependency one module at a
# time. Any library source change selects the survey explicitly.
SOURCE_SCAN_PREFIX_SETS = {
    "library/": "contract_audit_surveys",
}

# These nested tests are intentionally outside the routine guard modules.
# Keep their cost on setup/isolation changes and full-suite runs, where the
# ordinary changed-test selection would otherwise miss the cross-suite proof.
NESTED_SAFEGUARD_TESTS = {
    "tests/tooling/test_nested_project_collection.py",
    "tests/tooling/test_nested_broker_isolation.py",
}
_PROJECT_GUARD_PATHS = {
    "library/tools/paths.py",
    "tests/tooling/test_tests_never_reach_real_projects.py",
    "tests/tooling/test_nested_project_collection.py",
}
_BROKER_GUARD_PATHS = {
    "library/tools/resolved/client.py",
    "library/tools/resolve_lock.py",
    "tests/tooling/test_tests_never_start_a_real_broker.py",
    "tests/tooling/test_nested_broker_isolation.py",
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


def select(changed, use_coverage=True):
    """Test paths (repo-relative) covering the changed files.

    With coverage enabled (default) a fresh CI-05 index narrows the
    static selection as documented above; without one the exact static
    selection is returned.
    """
    return sorted(_select_with_distances(changed, use_coverage=use_coverage))


def select_with_distances(changed, use_coverage=True):
    """Test paths mapped to import distance from the changed files.

    0 is the changed test itself, 1 a direct importer or same-stem
    test, 2 a hop through a re-exporter or a contract-set entry.
    The minimum wins when several changed files reach one test.
    """
    return dict(_select_with_distances(changed, use_coverage=use_coverage))


def select_with_coverage_info(changed):
    """The hybrid selection plus the narrowing report.

    Returns `(selection, info)` where `info` is
    `coverage_narrowing_report(static, narrowed)`: counts, the dropped
    paths, and whether the result is a strict subset. The report is
    the precision/recall read for a fixture tree - coverage only ever
    removes, so precision against the static selector is exact by
    construction and the report shows what was removed.
    """
    static = sorted(_select_with_distances(changed, use_coverage=False))
    narrowed = sorted(_select_with_distances(changed, use_coverage=True))
    return narrowed, coverage_narrowing_report(static, narrowed)


def coverage_narrowing_report(static, narrowed):
    """Precision/recall of a narrowed selection vs the static one."""
    static_set = set(static)
    narrowed_set = set(narrowed)
    return {
        "static_count": len(static_set),
        "narrowed_count": len(narrowed_set),
        "dropped": sorted(static_set - narrowed_set),
        "added": sorted(narrowed_set - static_set),
        "subset": narrowed_set <= static_set,
        "strictly_narrower": bool(static_set) and narrowed_set < static_set,
    }


# Markers for a dynamic-import signal: the static stems of a file using
# one of these cannot be trusted, because the modules it really loads
# are chosen at runtime. Any such signal in a changed file falls back
# to the wide selection.
_DYNAMIC_IMPORT_MARKERS = (
    "__import__", "importlib", "import_module", "pkgutil", "exec(", "eval(",
)

# Index roots mirror the coverage index (`coverage_index.INDEX_ROOTS`):
# only tree code the index records can ever narrow.
_FALLBACK_INDEX_ROOTS = ("library", "tests", "ren")
_LAST_COVERAGE_REPORT = {"narrowed": False, "reason": "not evaluated"}

# Basename/suffix classes whose change invalidates evidence the index
# cannot see: shared fixtures, configuration, native artifacts and the
# surrounding environment. Any such change falls back wide.
_FIXTURE_BASENAMES = ("conftest.py",)
_CONFIG_BASENAMES = (
    "pytest.ini", "setup.cfg", "pyproject.toml", "tox.ini", ".coveragerc",
    "coverage.rc", "mypy.ini", "ruff.toml", ".ruff.toml", "Cargo.toml",
    "Cargo.lock",
)
_CONFIG_SUFFIXES = (".cfg", ".toml", ".ini", ".yaml", ".yml")
_ENV_BASENAMES = (
    "Dockerfile", "docker-compose.yml", "requirements.txt",
    "environment.yml", "Pipfile", "Pipfile.lock", "poetry.lock",
    "Makefile",
)
_NATIVE_SUFFIXES = (".rs", ".so", ".dylib", ".a")


def _coverage_module():
    """The CI-05 index reader, loaded from this checkout's own tree.

    Loaded by path (not by `REN_SELECT_ROOT`, which points at the tree
    under test and may be a fixture without the module): coverage only
    ever reads JSON beside the tree, so a second copy of the reader is
    stateless. None when the reader itself cannot load - fail closed.
    """
    global _COVERAGE_READER
    try:
        return _COVERAGE_READER
    except NameError:
        pass
    reader = None
    try:
        import importlib.util
        script_root = os.path.dirname(os.path.dirname(
            os.path.abspath(__file__)))
        candidate = os.path.join(
            script_root, "library", "tools", "local_ci",
            "coverage_index.py")
        spec = importlib.util.spec_from_file_location(
            "_ren_coverage_index", candidate)
        if spec is not None and spec.loader is not None:
            reader = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(reader)
    except (OSError, ImportError, SyntaxError):
        reader = None
    _COVERAGE_READER = reader
    return reader


def _coverage_overrides():
    """Freshness-stamp overrides for hermetic fixture trees."""
    def clean(name):
        value = os.environ.get(name, "").strip()
        return value or None
    return (clean("VEP_COVERAGE_TREE_SHA"),
            clean("VEP_COVERAGE_INTERPRETER"),
            clean("VEP_COVERAGE_NATIVE"))


def _is_indexable_path(rel_posix, roots):
    """Whether the index could record this tree-relative path."""
    if not rel_posix.endswith(".py"):
        return False
    return any(rel_posix == root or rel_posix.startswith(root + "/")
               for root in roots)


def _wide_change_reason(rel_posix):
    """Why a changed path invalidates coverage evidence, or None."""
    basename = rel_posix.rsplit("/", 1)[-1]
    segments = rel_posix.split("/")
    if basename in _FIXTURE_BASENAMES or basename.startswith("conftest"):
        return "fixture"
    if (any(segment in ("fixtures", "fixture") for segment in segments)
            and basename.endswith(".py")):
        return "fixture"
    if basename in _CONFIG_BASENAMES or rel_posix.endswith(_CONFIG_SUFFIXES):
        return "config"
    if (rel_posix.startswith("native/")
            or rel_posix.endswith(_NATIVE_SUFFIXES)):
        return "native"
    if (basename in _ENV_BASENAMES
            or basename.startswith(("requirements", ".env"))):
        return "env"
    return None


def _has_dynamic_import_signal(abs_path):
    """Whether a changed file reaches modules its stems do not name."""
    try:
        with open(abs_path, encoding="utf-8") as handle:
            text = handle.read()
    except (OSError, UnicodeDecodeError):
        # An unreadable changed file proves nothing about what it
        # loads: fail closed.
        return True
    return any(marker in text for marker in _DYNAMIC_IMPORT_MARKERS)


def _apply_coverage_narrowing(changed, static, protected):
    """Narrow a static selection with the fresh coverage index.

    Returns the exact static selection whenever independence cannot be
    established: no reader, no fresh index, a changed file outside the
    index, a dynamic-import signal, a fixture/config/native/env change,
    or a changed file no indexed test executed. Otherwise keeps the
    protected entries plus the forward fan-out the index shows
    executing a changed file - a strict subset of the input.
    """
    global _LAST_COVERAGE_REPORT
    def fallback(reason):
        global _LAST_COVERAGE_REPORT
        _LAST_COVERAGE_REPORT = {
            "narrowed": False, "reason": reason,
            "static_count": len(static), "selected_count": len(static)}
        return dict(static)
    if not static:
        return fallback("empty static selection")
    module = _coverage_module()
    if module is None:
        return fallback("coverage reader unavailable")
    sha, interpreter, native = _coverage_overrides()
    try:
        tests_map, _reason, baseline_changed = module.load(
            REPO_ROOT, None, sha=sha, interpreter=interpreter,
            native=native, with_metadata=True)
    except (OSError, ValueError, ImportError, AttributeError, TypeError,
            KeyError):
        return fallback("coverage index unreadable")
    if not isinstance(tests_map, dict):
        detail = (_reason or "no compatible baseline").lower()
        if ("stale:" in detail or "fingerprint" in detail
                or "mismatch" in detail):
            label = "fingerprint mismatch"
        elif "no coverage index" in detail:
            label = "no compatible baseline"
        else:
            label = "incompatible baseline"
        return fallback(label)
    if not tests_map:
        return fallback(_reason or "no compatible baseline")
    roots = getattr(module, "INDEX_ROOTS", _FALLBACK_INDEX_ROOTS)
    changed_rels = []
    for path in changed:
        abs_path = (path if os.path.isabs(path)
                    else os.path.join(REPO_ROOT, path))
        try:
            rel = os.path.relpath(os.path.abspath(abs_path), REPO_ROOT)
        except ValueError:
            return fallback("changed path outside checkout")
        rel_posix = rel.replace(os.sep, "/")
        changed_rels.append(rel_posix)
        if _wide_change_reason(rel_posix) is not None:
            return fallback("changed file invalidates coverage: " +
                            _wide_change_reason(rel_posix))
        if not _is_indexable_path(rel_posix, roots):
            return fallback("changed file outside coverage index")
        if _has_dynamic_import_signal(os.path.abspath(abs_path)):
            return fallback("dynamic import signal")
    # The baseline index says nothing trustworthy about files changed
    # since certification. Keep their complete static selection, while
    # still allowing compatible evidence to narrow other changed files.
    invalidated = set(baseline_changed)
    if any(_wide_change_reason(path) is not None
           or not _is_indexable_path(path, roots)
           for path in invalidated):
        return fallback("baseline invalidation includes unindexable file")
    static_invalidated = {}
    for path, distance in static.items():
        if any(path == candidate or
               path.replace(os.sep, "/") == candidate
               for candidate in invalidated):
            static_invalidated[path] = distance
    if invalidated:
        static_invalidated = _select_with_distances(
            [path for path in changed
             if path.replace(os.sep, "/") in invalidated],
            use_coverage=False)
    active_changed = [path for path in changed
                      if path.replace(os.sep, "/") not in invalidated]
    if invalidated and not active_changed:
        result = {**static, **static_invalidated}
        _LAST_COVERAGE_REPORT = {
            "narrowed": result.keys() < static.keys(),
            "reason": "all changed files invalidated",
            "static_count": len(static), "selected_count": len(result)}
        return result
    changed_rels = [path for path in changed_rels
                    if path not in invalidated]
    if not changed_rels:
        result = {**static, **static_invalidated}
        _LAST_COVERAGE_REPORT = {
            "narrowed": result.keys() < static.keys(),
            "reason": "all changed files invalidated",
            "static_count": len(static), "selected_count": len(result)}
        return result
    covered: set = set()
    for entry in tests_map.values():
        if isinstance(entry, dict) and isinstance(entry.get("files"), list):
            covered.update(name for name in entry["files"]
                           if isinstance(name, str))
    for rel_posix in changed_rels:
        if rel_posix not in covered:
            # Uncovered but importable: the static edge still selects.
            return fallback("changed file absent from coverage baseline")
    executed_by_test_file: dict = {}
    for test_id, entry in tests_map.items():
        if not isinstance(test_id, str) or not isinstance(entry, dict):
            continue
        files = entry.get("files")
        if not isinstance(files, list):
            continue
        head = test_id.split("::")[0].strip().replace("\\", "/")
        while head.startswith("./"):
            head = head[2:]
        if os.path.isabs(head):
            try:
                head = os.path.relpath(head, REPO_ROOT).replace(
                    os.sep, "/")
            except ValueError:
                continue
        executed_by_test_file.setdefault(head, set()).update(
            name for name in files if isinstance(name, str))
    wanted = set(changed_rels)
    narrowed = dict(static_invalidated)
    for path, distance in static.items():
        if path in protected:
            narrowed[path] = distance
            continue
        executed = executed_by_test_file.get(
            path.replace(os.sep, "/"), set())
        if executed & wanted:
            narrowed[path] = distance
    did_narrow = set(narrowed) < set(static)
    _LAST_COVERAGE_REPORT = {
        "narrowed": did_narrow,
        "reason": "coverage narrowed" if did_narrow else
                  "coverage evidence retained full static selection",
        "static_count": len(static), "selected_count": len(narrowed)}
    return narrowed


def _select_with_distances(changed, use_coverage=True):
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
    # Entries coverage must never narrow away: the changed test itself,
    # the changed module's same-stem test, backward entries (a used
    # module's own test), and contract-set expansions. Only forward
    # fan-out (importers of the changed stem) is narrowable.
    protected: set = set()

    def add(path, distance):
        if path in selected:
            selected[path] = min(selected[path], distance)
        else:
            selected[path] = distance

    def keep(path, distance):
        add(path, distance)
        protected.add(path)

    changed_relpaths = {
        os.path.relpath(os.path.abspath(path if os.path.isabs(path)
                                        else os.path.join(REPO_ROOT, path)),
                        REPO_ROOT).replace(os.sep, "/")
        for path in changed
    }
    setup_change = any(
        _wide_change_reason(path) in ("fixture", "config")
        or (path.startswith("tests/")
            and path.endswith(".py")
            and not os.path.basename(path).startswith("test_"))
        for path in changed_relpaths)
    project_guard_change = setup_change or bool(
        changed_relpaths & _PROJECT_GUARD_PATHS)
    broker_guard_change = setup_change or bool(
        changed_relpaths & _BROKER_GUARD_PATHS)
    if project_guard_change or broker_guard_change:
        if project_guard_change:
            keep("tests/tooling/test_nested_project_collection.py", 1)
        if broker_guard_change:
            keep("tests/tooling/test_nested_broker_isolation.py", 1)
    for path in changed:
        abs_path = (path if os.path.isabs(path)
                    else os.path.join(REPO_ROOT, path))
        stem = os.path.splitext(os.path.basename(abs_path))[0]
        rel_path = os.path.relpath(os.path.abspath(abs_path), REPO_ROOT)
        rel_posix = rel_path.replace(os.sep, "/")

        if any(rel_posix == owner or rel_posix.startswith(owner)
               for owner in EDITORIAL_OUTPUT_PATHS):
            keep(EDITORIAL_RENDER_TEST, 2)

        # Recipe helpers and answer data are read by the scenario at runtime,
        # not imported as Python modules. Keep those media checks selected.
        for dependent in SCENARIO_DEPENDENCIES.get(rel_path, ()):
            keep(dependent, 2)
        for helper, dependents in SCENARIO_HELPERS.items():
            helper_path = os.path.join(REPO_ROOT, helper)
            if stem in _imported_by_module(helper_path):
                for dependent in dependents:
                    keep(dependent, 2)

        # A changed test is itself part of the dependent selection. The
        # import graph below finds tests that exercise changed modules,
        # but a test module has no `test_<stem>.py` counterpart to find.
        absolute = os.path.abspath(abs_path)
        if (os.path.commonpath([absolute, TESTS]) == TESTS
                and os.path.basename(absolute).startswith("test_")
                and absolute.endswith(".py") and os.path.isfile(absolute)):
            keep(os.path.relpath(absolute, REPO_ROOT), 0)

        resolvers = set()
        for prefix, resolver in SOURCE_SCAN_PREFIX_SETS.items():
            if rel_posix.startswith(prefix):
                resolvers.add(resolver)
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
                keep(os.path.relpath(test_files[same], REPO_ROOT), 1)

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
                if (os.path.basename(test_abs) == "test_" + carrier + ".py"
                        or carrier in test_imports.get(test_abs, set())):
                    add(rel, hop)
            if carrier in CONTRACT_SETS:
                resolvers.add(carrier)
        for dotted, test_abs in test_modules:
            rel = os.path.relpath(test_abs, REPO_ROOT)
            if os.path.basename(test_abs) == "test_" + stem + ".py":
                keep(rel, 1)
            elif stem in test_imports.get(test_abs, set()):
                add(rel, 1)

        for resolver in resolvers:
            for entry in CONTRACT_SETS[resolver]:
                # Never select a path the checkout being selected
                # against does not have (test_selection_roots_at_ren_select_root):
                # fixture trees have no survey file, so a library/
                # prefix match there must not select this repo's surveys.
                if os.path.isfile(os.path.join(REPO_ROOT, entry)):
                    keep(entry, 2)

    # Pytest treats conftest.py as configuration, not a test module. It can
    # enter the import graph as a carrier, but passing it as a path causes
    # module-name collisions when another subsystem has its own conftest.
    static = {path: distance for path, distance in selected.items()
              if os.path.basename(path).startswith("test_")
              and path.endswith(".py")}
    protected = {path for path in protected if path in static}
    if not use_coverage:
        return static
    return _apply_coverage_narrowing(changed, static, protected)


def loop(changed, use_coverage=True):
    """The per-change loop: reached `unit/` subsystems plus `contracts/`."""
    def absolute(path):
        return os.path.realpath(os.path.join(REPO_ROOT, path))

    # A changed test reaches its own subsystem; a changed module reaches
    # the subsystems of the unit tests its dependent selection picks.
    tests_root = layers.TESTS_ROOT.as_posix() + "/"
    modules = [p for p in changed
               if not absolute(p).startswith(tests_root)]
    subsystems = set()
    for path in [*changed, *(select(modules, use_coverage=use_coverage)
                            if modules else [])]:
        subsystem = layers.subsystem_of(absolute(path))
        if subsystem is not None:
            subsystems.add(subsystem)
    dirs = [layers.unit_dir(name) for name in sorted(subsystems)]
    dirs.append(layers.layer_dir("contracts"))
    return [os.path.relpath(d, REPO_ROOT) + os.sep for d in dirs]


def main(argv):
    args = list(argv)
    use_coverage = True
    while args and args[0] == "--no-coverage":
        use_coverage = False
        args = args[1:]
    if not args or args[0] in ("-h", "--help"):
        print(__doc__.strip().splitlines()[0])
        print("usage: select_dependent_tests.py [--no-coverage] "
              "[--loop] [--distances|--coverage-report] "
              "<changed-file> ...")
        return 0
    if args[0] == "--loop":
        for path in loop(args[1:], use_coverage=use_coverage):
            print(path)
        return 0
    if args[0] == "--distances":
        distances = select_with_distances(
            args[1:], use_coverage=use_coverage)
        for path in sorted(distances):
            print(f"{distances[path]} {path}")
        return 0
    if args[0] == "--coverage-report":
        selected = select_with_distances(args[1:], use_coverage=use_coverage)
        print(json.dumps({**_LAST_COVERAGE_REPORT,
                          "selected": sorted(selected),
                          "distances": selected}))
        return 0
    for path in select(args, use_coverage=use_coverage):
        print(path)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
