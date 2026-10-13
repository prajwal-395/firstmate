"""Shared content-hash verification cache (benchmark Spec 2, CI-01/CI-02).

A selection reruns only what its inputs cannot prove unchanged: each
test FILE carries a key over every input that could flip its verdict,
and a stored PASS under the same key is reused without executing the
file. Every eligible execution produces reusable evidence regardless
of origin - a worker-local `ren ci check`, a PR Tier-1 run, a Tier-2
integration batch, or a periodic full validation: they all read and
write the same rows, keyed identically for the same tree.

Tier-2 remains in CI-08 shadow mode: it records the cache hits it would
reuse and the tests it would execute, while the full gate still executes
everything and remains the merge authority. The Tier-2 write hook files
verdicts from the gate's own JUnit report after the full run.

The key (report section 7, plus the prototype-bug fix it proves): the
test file itself, its static closure INCLUDING tests-helpers (the
PR-2145 miss was `tests/scenarios/golden_edit.py`, reached only by
stem), the conftest chain above it, its directory's data files (the
other PR-2145 miss was `golden_monologue_answers.json`), the
pytest/marker config, the interpreter version, native artifacts
(`ren_native` sources, Remotion lockfile state), and - for env-gated
tests only - an environment fingerprint (the 2145 failure was
npm-missing, which no content hash catches; env-gated tests never reuse
unless the fingerprint matches).

 Fail-closed everywhere: only PASS verdicts with executed tests are
 stored; failures and all-skipped files delete any stored row; a missing
 or unparseable junit report stores nothing; an unhashable input
 (unreadable file) makes the key unmatchable, never empty.

 Each row also records its SOURCE - which executor produced the
 evidence (`local`, `pr`, `batch`, `periodic`) - for the evidence
 ledger. The source is metadata only: it never enters the key, so the
 same tree under any source computes the same key and reuses across
 sources.


Store: a multiversion SQLite store under the state dir, one row per
(test path, verification key): parallel branches verifying the same
file under different keys coexist (the AAA/BBB case), and `lookup`
hits whichever key this run computed regardless of later writes by
other branches. History per path is bounded (`REN_CI_CACHE_VERSIONS`,
default above); past the bound the least-recently-used key goes
first. The table as a whole stays bounded by the measured suite size
(distinct paths in the store) times the per-path bound - never by a
fixed row count. Guarded by a lock file so the daemon's parallel
Tier-1 workers have one writer. Stdlib only.
"""

from __future__ import annotations

import ast
import hashlib
import os
import shutil
import sqlite3
import subprocess
import sys
import threading

CACHE_FILENAME = "tier1_result_cache.sqlite3"
LOCK_FILENAME = "tier1_result_cache.lock"

# Executors that may file evidence, for the `source` column. Stored
# verbatim (unknown values are kept, never refused - the source never
# affects reuse); this tuple is the vocabulary writers use.
SOURCES = ("local", "pr", "batch", "periodic")

# Successful verdicts kept per test path. Two parallel branches (the
# AAA/BBB case) need two rows to coexist; one spare covers a third
# writer landing between a store and its lookup. Overridable via
# REN_CI_CACHE_VERSIONS (tests pin eviction through it); values below
# 1 are refused and fall back to the default - a bound of zero rows
# would store nothing and read as a miss everywhere.
_DEFAULT_VERSIONS_PER_PATH = 3

# Files over this size hash by (name, size, head+tail sample) instead of
# full content: a qualification media file beside a test must not cost a
# full read per Tier-1 run. Sampling is still content-derived, so equal
# content on any checkout hashes equal - never mtime.
_LARGE_FILE_BYTES = 32 * 1024 * 1024

# Substrings (case-insensitive) marking a test env-gated: its verdict can
# flip with the machine (renderers, media tools, model credentials, live
# Resolve) rather than with the tree. Wide on purpose: over-gating only
# re-runs, under-gating ships a stale green.
_ENV_TOKENS = (
    "shutil.which", "skipif", "npm", "node", "remotion", "ffmpeg",
    "ffprobe", "hf_token", "huggingface", "pipeline_", "cuda", "gpu",
    "onnx", "whisper", "easyocr", "real_model", "resolve_live",
    "os.environ", "os.getenv", "environ.get",
)

# Test layers whose tests render media or need models regardless of what
# their source mentions: scenarios render golden media, qualification
# needs real Resolve/models/media.
_ENV_GATED_DIRS = ("tests/scenarios", "tests/qualification")

# Presence (never values) for credentials; values for machine paths.
_ENV_PRESENCE_VARS = ("HF_TOKEN",)
_ENV_VALUE_VARS = (
    "PIPELINE_SFX_LIBRARY", "PIPELINE_MUSIC_LIBRARY",
    "PIPELINE_PROJECTS_ROOT",
)

_CONFIG_FILES = ("pyproject.toml", "pytest.ini", "setup.cfg", "tox.ini")

_lock = threading.Lock()

# Version probes are machine-constant within one Tier-1 run; without
# this, every gated test file would respawn node/npm/ffmpeg (hundreds
# of subprocesses per selection). Tests reset it via `override=`.
_PROBE_CACHE: dict = {}


def enabled() -> bool:
    """Whether the cache consults and records (kill-switch for debugging).

    `REN_CI_RESULT_CACHE=0/off/no/false` disables both directions: no
    reuse, no recording. Default on.
    """
    return os.environ.get("REN_CI_RESULT_CACHE", "").strip().lower() not in (
        "0", "off", "no", "false")


def cache_path(state_dir: str) -> str:
    """The SQLite file under the state dir, beside the ledger."""
    from library.tools.local_ci import config
    root = state_dir or config.state_dir()
    return os.path.join(root, CACHE_FILENAME)


def _hash_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _hash_file(path: str) -> str | None:
    """sha256 of a file's content, or None when unreadable.

    None poisons the key (fail closed): an input that cannot be read
    must never compare equal.
    """
    try:
        size = os.path.getsize(path)
    except OSError:
        return None
    try:
        if size <= _LARGE_FILE_BYTES:
            with open(path, "rb") as handle:
                return _hash_bytes(handle.read())
        with open(path, "rb") as handle:
            head = handle.read(1024 * 1024)
            handle.seek(max(0, size - 1024 * 1024))
            tail = handle.read(1024 * 1024)
        return _hash_bytes(
            f"{os.path.basename(path)}:{size}:".encode()
            + head + tail)
    except OSError:
        return None


def _parse_import_stems(path: str) -> set[str]:
    """Every module stem imported by a file, at any depth (AST)."""
    try:
        with open(path, encoding="utf-8") as handle:
            tree = ast.parse(handle.read())
    except (OSError, SyntaxError, UnicodeDecodeError, ValueError):
        return set()
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


class KeyBuilder:
    """Content keys for one checkout; the stem index builds once.

    Building the index walks `library/`, `tests/` and `ren/` (the trees
    tests import from) - the same ~20s IO the selector already pays per
    Tier-1 run. Keying per file WITHOUT a shared index would repay that
    per selected file; callers partition one selection per builder.
    """

    INDEX_ROOTS = ("library", "tests", "ren")

    def __init__(self, checkout: str) -> None:
        self.checkout = os.path.abspath(checkout)
        self._index: dict[str, list[str]] | None = None
        self._imports_by_path: dict[str, set[str]] = {}

    def _imports(self, path: str) -> set[str]:
        """Parse each source file at most once for this immutable checkout.

        `closure()` walks overlapping dependency graphs for every test in
        a selection. Keeping the parsed edges beside the filename index
        avoids reparsing shared dependencies for each selected test.
        """
        imports = self._imports_by_path.get(path)
        if imports is None:
            imports = _parse_import_stems(path)
            self._imports_by_path[path] = imports
        return imports

    def _stem_index(self) -> dict[str, list[str]]:
        if self._index is None:
            index: dict[str, list[str]] = {}
            for root in self.INDEX_ROOTS:
                base = os.path.join(self.checkout, root)
                for dirpath, dirnames, filenames in os.walk(base):
                    dirnames[:] = [d for d in dirnames
                                   if d != "__pycache__"]
                    for name in filenames:
                        if not name.endswith(".py"):
                            continue
                        stem = os.path.splitext(name)[0]
                        rel = os.path.relpath(
                            os.path.join(dirpath, name), self.checkout)
                        index.setdefault(stem, []).append(rel)
            for stem in index:
                index[stem] = sorted(index[stem])
            self._index = index
        return self._index

    def closure(self, test_rel: str) -> list[str]:
        """Transitive static closure of a test file, repo-relative.

        Follows imported stems into `library/`, `tests/` (helpers like
        `golden_edit.py` live here - the PR-2145 prototype miss) and
        `ren/`, transitively. Conftest files are excluded: the conftest
        chain is its own key component, hashed separately.
        """
        index = self._stem_index()
        seen = {test_rel}
        stack = [test_rel]
        while stack:
            current = stack.pop()
            absolute = os.path.join(self.checkout, current)
            for stem in self._imports(absolute):
                for candidate in index.get(stem, []):
                    if candidate in seen:
                        continue
                    if os.path.basename(candidate) == "conftest.py":
                        continue
                    seen.add(candidate)
                    stack.append(candidate)
        seen.discard(test_rel)
        return sorted(seen)

    def conftest_chain(self, test_rel: str) -> list[str]:
        """Every `conftest.py` from the repo root down to the test's dir."""
        chain = []
        directory = os.path.dirname(
            os.path.join(self.checkout, test_rel))
        while True:
            candidate = os.path.join(directory, "conftest.py")
            if os.path.isfile(candidate):
                chain.append(os.path.relpath(candidate, self.checkout))
            parent = os.path.dirname(directory)
            if parent == directory or not directory.startswith(
                    self.checkout):
                break
            directory = parent
            if os.path.relpath(directory, self.checkout).startswith(".."):
                break
        return sorted(chain)

    def data_files(self, test_rel: str) -> list[str]:
        """Non-Python files under the test's own directory, recursively.

        The other PR-2145 miss was `golden_monologue_answers.json` beside
        its scenario test. `*.py` is excluded (code is the closure's),
        as are caches. Recursive within the directory: precise (only
        this directory's files affect the key) and fail-closed.
        """
        directory = os.path.dirname(
            os.path.join(self.checkout, test_rel))
        found = []
        for dirpath, dirnames, filenames in os.walk(directory):
            dirnames[:] = [d for d in dirnames
                           if d != "__pycache__"]
            for name in filenames:
                if name.endswith((".py", ".pyc")):
                    continue
                rel = os.path.relpath(
                    os.path.join(dirpath, name), self.checkout)
                found.append(rel)
        return sorted(found)


def env_gated(test_rel: str, content: str = "") -> bool:
    """Whether a test's verdict can flip with the machine, not the tree."""
    normalized = test_rel.replace(os.sep, "/")
    if normalized.startswith(_ENV_GATED_DIRS):
        return True
    lowered = content.lower()
    return any(token in lowered for token in _ENV_TOKENS)


def _probe_version(command: list[str]) -> str:
    """First line of a tool's version output, or `absent`."""
    key = tuple(command)
    if key not in _PROBE_CACHE:
        _PROBE_CACHE[key] = _probe_version_uncached(command)
    return _PROBE_CACHE[key]


def _probe_version_uncached(command: list[str]) -> str:
    """First line of a tool's version output, or `absent`."""
    if shutil.which(command[0]) is None:
        return "absent"
    try:
        result = subprocess.run(
            command, capture_output=True, encoding="utf-8", check=False,
            timeout=30)
    except (OSError, subprocess.SubprocessError):
        return "unknown"
    if result.returncode != 0:
        return "unknown"
    line = (result.stdout or "").strip().splitlines()
    return line[0][:120] if line else "unknown"


def env_fingerprint(override: str | None = None) -> str:
    """Machine state env-gated tests depend on; `override` pins it in tests."""
    if override is not None:
        return override
    parts = [
        f"node={_probe_version(['node', '--version'])}",
        f"npm={_probe_version(['npm', '--version'])}",
        f"ffmpeg={_probe_version(['ffmpeg', '-version'])}",
    ]
    for var in _ENV_PRESENCE_VARS:
        parts.append(f"{var}={'set' if os.environ.get(var) else 'unset'}")
    for var in _ENV_VALUE_VARS:
        parts.append(f"{var}={os.environ.get(var, 'unset')}")
    return ";".join(parts)


def native_fingerprint(checkout: str, override: str | None = None) -> str:
    """What the test run builds on: extension sources + renderer lockfile."""
    if override is not None:
        return override
    key = ("native", os.path.abspath(checkout))
    if key not in _PROBE_CACHE:
        _PROBE_CACHE[key] = _native_fingerprint_uncached(checkout)
    return _PROBE_CACHE[key]


def _native_fingerprint_uncached(checkout: str) -> str:
    try:
        from library.tools import ren_native_source
        digest = ren_native_source.source_fingerprint(checkout)
    except (ImportError, OSError, RuntimeError):
        digest = None
    parts = [f"ren-native={digest or 'unverifiable'}"]
    for name in ("package.json", "package-lock.json"):
        path = os.path.join(checkout, "remotion-subtitles", name)
        digest = _hash_file(path)
        parts.append(f"remotion-{name}={digest or 'absent'}")
    return ";".join(parts)


def config_fingerprint(checkout: str, override: str | None = None) -> str:
    """The pytest/marker config selecting and filtering tests."""
    if override is not None:
        return override
    key = ("config", os.path.abspath(checkout))
    if key not in _PROBE_CACHE:
        _PROBE_CACHE[key] = _config_fingerprint_uncached(checkout)
    return _PROBE_CACHE[key]


def _config_fingerprint_uncached(checkout: str) -> str:
    from library.tools.local_ci import config
    parts = []
    for name in _CONFIG_FILES:
        digest = _hash_file(os.path.join(checkout, name))
        parts.append(f"{name}={digest or 'absent'}")
    parts.append(f"markers={config.PYTEST_MARKERS}")
    parts.append(f"driving={','.join(config.RESOLVE_DRIVING)}")
    return ";".join(parts)


def pytest_interpreter_version(python: str = "",
                               override: str | None = None) -> str:
    """The version string of the interpreter pytest runs under."""
    if override is not None:
        return override
    if not python:
        return sys.version
    try:
        result = subprocess.run(
            [python, "-c", "import sys; print(sys.version)"],
            capture_output=True, encoding="utf-8", check=False, timeout=60)
    except (OSError, subprocess.SubprocessError):
        return sys.version
    text = (result.stdout or "").strip()
    return text or sys.version


def compute_key(checkout: str, test_rel: str,
                builder: KeyBuilder | None = None,
                interpreter: str | None = None,
                native: str | None = None,
                env: str | None = None,
                config_text: str | None = None) -> str:
    """The full invalidation key for one test file (hex sha256).

    Overrides pin one component for deterministic tests; omitted ones
    resolve from the checkout and machine. An unreadable input poisons
    the key (`None` hashes distinctly), never comparing equal.
    """
    checkout = os.path.abspath(checkout)
    own = builder or KeyBuilder(checkout)
    test_abs = os.path.join(checkout, test_rel)
    try:
        with open(test_abs, encoding="utf-8") as handle:
            content = handle.read()
    except (OSError, UnicodeDecodeError):
        content = ""
        test_digest: str | None = None
    else:
        test_digest = _hash_bytes(content.encode("utf-8"))
    components = [f"test={test_rel}:{test_digest}"]
    for rel in own.closure(test_rel):
        components.append(f"closure={rel}:{_hash_file(
            os.path.join(checkout, rel))}")
    for rel in own.conftest_chain(test_rel):
        components.append(f"conftest={rel}:{_hash_file(
            os.path.join(checkout, rel))}")
    for rel in own.data_files(test_rel):
        components.append(f"data={rel}:{_hash_file(
            os.path.join(checkout, rel))}")
    components.append(f"config={config_fingerprint(checkout, config_text)}")
    components.append(
        f"python={interpreter if interpreter is not None
                   else pytest_interpreter_version()}")
    components.append(f"native={native_fingerprint(checkout, native)}")
    if env_gated(test_rel, content):
        components.append(f"env={env_fingerprint(env)}")
    else:
        components.append("env=ungated")
    return _hash_bytes("\n".join(components).encode("utf-8"))


def _versions_per_path() -> int:
    """History rows kept per test path (see `_DEFAULT_VERSIONS_PER_PATH`)."""
    try:
        configured = int(os.environ.get("REN_CI_CACHE_VERSIONS", "")
                         or _DEFAULT_VERSIONS_PER_PATH)
    except ValueError:
        return _DEFAULT_VERSIONS_PER_PATH
    if configured < 1:
        return _DEFAULT_VERSIONS_PER_PATH
    return configured


def _connect(state_dir: str) -> sqlite3.Connection:
    os.makedirs(state_dir or ".", exist_ok=True)
    connection = sqlite3.connect(cache_path(state_dir), timeout=30)
    _ensure_schema(connection)
    return connection


_MULTIVERSION_SCHEMA = (
    "CREATE TABLE IF NOT EXISTS tier1_results ("
    " test_path TEXT NOT NULL,"
    " key TEXT NOT NULL,"
    " passed INTEGER NOT NULL,"
    " total INTEGER NOT NULL,"
    " head TEXT NOT NULL DEFAULT '',"
    " recorded_at REAL NOT NULL,"
    " last_used REAL NOT NULL,"
    " source TEXT NOT NULL DEFAULT '',"
    " PRIMARY KEY (test_path, key))")


def _ensure_schema(connection: sqlite3.Connection) -> None:
    """Create the multiversion store, migrating legacy databases.

    Databases written before the multiversion store hold one row per
    path (`test_path PRIMARY KEY`, no `last_used`). Their verdicts
    carry over one-to-one - each legacy row already names its key -
    so no stored PASS is wiped or stranded by the upgrade. Databases
    written before the source column (CI-01 era) gain it with the
    empty default: old rows keep their verdicts, only their source
    reads as unrecorded.
    """
    columns = [row[1] for row in connection.execute(
        "PRAGMA table_info(tier1_results)").fetchall()]
    if not columns:
        connection.execute(_MULTIVERSION_SCHEMA)
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_tier1_results_lru"
            " ON tier1_results (test_path, last_used)")
        connection.commit()
        return
    if "last_used" not in columns:
        connection.execute(
            "ALTER TABLE tier1_results RENAME TO tier1_results_legacy")
        connection.execute(_MULTIVERSION_SCHEMA)
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_tier1_results_lru"
            " ON tier1_results (test_path, last_used)")
        connection.execute(
            "INSERT INTO tier1_results"
            " (test_path, key, passed, total, head, recorded_at, last_used)"
            " SELECT test_path, key, passed, total, head, recorded_at,"
            " recorded_at FROM tier1_results_legacy")
        connection.execute("DROP TABLE tier1_results_legacy")
        connection.commit()
        return
    if "source" not in columns:
        connection.execute(
            "ALTER TABLE tier1_results ADD COLUMN"
            " source TEXT NOT NULL DEFAULT ''")
        connection.commit()


def _lookup_row(connection: sqlite3.Connection, test_rel: str,
                key: str):
    """The stored row for this exact (path, key), or None.

    A hit refreshes `last_used`: use recency is what eviction orders
    by, so a reused verdict is not evicted as stale. Rows carrying no
    passed tests are never evidence (fail closed) and refresh nothing.
    """
    import time
    row = connection.execute(
        "SELECT key, passed, total, head, recorded_at, source"
        " FROM tier1_results WHERE test_path = ? AND key = ?",
        (test_rel, key)).fetchone()
    if row is None or (row[1] or 0) <= 0:
        return None
    connection.execute(
        "UPDATE tier1_results SET last_used = ?"
        " WHERE test_path = ? AND key = ?",
        (time.time(), test_rel, key))
    connection.commit()
    return row


def lookup(state_dir: str, checkout: str, test_rel: str,
           builder: KeyBuilder | None = None, **overrides) -> dict | None:
    """The stored PASS for this exact key, or None (miss - run the test).

    A row under a DIFFERENT key is a miss, never a hit: the inputs moved,
    so the old verdict proves nothing. Rows stored later by other
    branches under their own keys never disturb this run's row: the
    lookup matches (path, key), not the path's latest write. Failures
    are never stored, so a hit always carries passed > 0.
    """
    key = compute_key(checkout, test_rel, builder, **overrides)
    with _lock:
        lock_path = os.path.join(
            state_dir or ".", LOCK_FILENAME)
        os.makedirs(os.path.dirname(os.path.abspath(lock_path)),
                    exist_ok=True)
        try:
            import fcntl
            with open(lock_path, "a", encoding="utf-8") as guard:
                fcntl.flock(guard.fileno(), fcntl.LOCK_EX)
                try:
                    connection = _connect(state_dir)
                    try:
                        row = _lookup_row(connection, test_rel, key)
                    finally:
                        connection.close()
                finally:
                    fcntl.flock(guard.fileno(), fcntl.LOCK_UN)
        except ImportError:  # pragma: no cover - non-posix fallback
            connection = _connect(state_dir)
            try:
                row = _lookup_row(connection, test_rel, key)
            finally:
                connection.close()
    if row is None:
        return None
    import time
    return {"key": row[0], "passed": row[1], "total": row[2],
            "head": row[3], "recorded_at": row[4], "source": row[5],
            "checked_at": time.time()}


def _enforce_bounds(connection: sqlite3.Connection,
                    test_rel: str) -> None:
    """Evict least-recently-used rows past the history bound.

    Per path, only the most recently used `_versions_per_path()` rows
    survive; ties break toward the later write. The table as a whole
    then stays within the measured suite size (distinct paths in the
    store) times the per-path bound - a safety net for a bound lowered
    between runs, since per-path enforcement alone already implies it.
    """
    versions = _versions_per_path()
    connection.execute(
        "DELETE FROM tier1_results WHERE test_path = ?"
        " AND rowid NOT IN (SELECT rowid FROM tier1_results"
        " WHERE test_path = ? ORDER BY last_used DESC, rowid DESC"
        " LIMIT ?)",
        (test_rel, test_rel, versions))
    measured = connection.execute(
        "SELECT COUNT(DISTINCT test_path) FROM tier1_results").fetchone()
    bound = (measured[0] or 0) * versions
    connection.execute(
        "DELETE FROM tier1_results WHERE rowid NOT IN"
        " (SELECT rowid FROM tier1_results"
        " ORDER BY last_used DESC, rowid DESC LIMIT ?)",
        (bound,))


def store(state_dir: str, test_rel: str, key: str, passed: int,
          total: int, head: str = "", source: str = "") -> None:
    """Record one PASS verdict under its key.

    One row per (path, key): a branch's store never overwrites another
    branch's row for the same path. Re-recording the same key refreshes
    its verdict. History past the bound evicts least-recently-used
    first, so the table stays bounded by measured suite size, never by
    an unbounded per-path history. `source` names the executor that
    produced the evidence (`local` / `pr` / `batch` / `periodic`) and
    never enters the key."""
    import time
    now = time.time()
    with _lock:
        connection = _connect(state_dir)
        try:
            connection.execute(
                "INSERT OR REPLACE INTO tier1_results"
                " (test_path, key, passed, total, head, recorded_at,"
                " last_used, source)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (test_rel, key, passed, total, head, now, now, source))
            _enforce_bounds(connection, test_rel)
            connection.commit()
        finally:
            connection.close()


def drop(state_dir: str, test_rel: str) -> None:
    """Forget a path: failures and empty runs leave no reusable verdict.

    Every stored version for the path goes: a failure under the
    current key must not leave an older key's PASS reusable as if the
    path still verified."""
    with _lock:
        connection = _connect(state_dir)
        try:
            connection.execute(
                "DELETE FROM tier1_results WHERE test_path = ?",
                (test_rel,))
            connection.commit()
        finally:
            connection.close()


def expand_selection(checkout: str, paths: list) -> list:
    """Selected DIRS to their test files; files pass through, conftest
    and missing paths drop out (the runner never executes them either)."""
    expanded = []
    for path in paths:
        absolute = (path if os.path.isabs(path)
                    else os.path.join(checkout, path))
        if os.path.isdir(absolute):
            for dirpath, dirnames, filenames in os.walk(absolute):
                dirnames[:] = [d for d in dirnames
                               if d != "__pycache__"]
                for name in sorted(filenames):
                    if name.startswith("test_") and name.endswith(".py"):
                        expanded.append(os.path.relpath(
                            os.path.join(dirpath, name), checkout))
        elif (os.path.isfile(absolute)
                and os.path.basename(absolute).startswith("test_")
                and absolute.endswith(".py")):
            expanded.append(os.path.relpath(absolute, checkout))
    seen = set()
    ordered = []
    for rel in expanded:
        if rel not in seen:
            seen.add(rel)
            ordered.append(rel)
    return ordered


def partition(state_dir: str, checkout: str, paths: list,
              builder: KeyBuilder | None = None,
              pytest_python: str = "",
              **overrides) -> dict:
    """Split an expanded selection into reused hits and files to run.

    Returns {expanded, run, reused:[{path, passed}], reused_passed,
    total}: `reused N/M` reads off `len(reused)`/`total`. Disabled cache
    runs everything (all miss, honestly reported as reused 0/M).
    """
    own = builder or KeyBuilder(checkout)
    expanded = expand_selection(checkout, paths)
    if "interpreter" not in overrides:
        overrides = {**overrides, "interpreter":
                     pytest_interpreter_version(pytest_python)}
    reused: list = []
    run: list = []
    reused_passed = 0
    for rel in expanded:
        hit = None
        if enabled():
            hit = lookup(state_dir, checkout, rel, own, **overrides)
        if hit is None:
            run.append(rel)
        else:
            reused.append({"path": rel, "passed": hit["passed"],
                           "source": hit.get("source", "")})
            reused_passed += hit["passed"]
    return {"expanded": expanded, "run": run, "reused": reused,
            "reused_passed": reused_passed, "total": len(expanded)}


def parse_junit(path: str) -> dict:
    """Per-test-file outcomes from a pytest `--junitxml` report.

    Returns {rel_path: {passed, failed, skipped, total}}. Missing or
    unparseable XML is {} - the caller stores nothing (fail closed).
    """
    import xml.etree.ElementTree as element_tree
    try:
        root = element_tree.parse(path).getroot()
    except (OSError, element_tree.ParseError):
        return {}
    files: dict = {}
    for case in root.iter("testcase"):
        status = "passed"
        for child in case:
            if child.tag in ("failure", "error"):
                status = "failed"
                break
            if child.tag == "skipped":
                status = "skipped"
        rel = (case.get("file") or "").replace("\\", "/")
        if not rel or rel.startswith("/"):
            classname = (case.get("classname") or "").replace(".", "/")
            candidate = f"{classname}.py" if classname else ""
            rel = candidate
        if not rel:
            continue
        entry = files.setdefault(
            rel, {"passed": 0, "failed": 0, "skipped": 0, "total": 0})
        entry[status] = entry.get(status, 0) + 1
        entry["total"] += 1
    return files


def record_from_junit(state_dir: str, checkout: str, junit_path: str,
                      executed: list, head: str = "",
                      builder: KeyBuilder | None = None,
                      pytest_python: str = "",
                      source: str = "",
                      **overrides) -> dict:
    """Store PASS rows for executed files the junit report clears.

    A file with any failure/error, with zero executed tests (all
    skipped), or absent from the report is DROPPED, never stored. Keys
    recompute here (not reused from partition) so a tree that moved
    mid-run cannot file a verdict under a stale key. `source` records
    which executor produced the evidence without changing any key.
    """
    own = builder or KeyBuilder(checkout)
    if "interpreter" not in overrides:
        overrides = {**overrides, "interpreter":
                     pytest_interpreter_version(pytest_python)}
    outcomes = parse_junit(junit_path)
    stored, dropped = [], []
    for rel in executed:
        outcome = outcomes.get(rel)
        if outcome is None:
            dropped.append(rel)
            drop(state_dir, rel)
            continue
        executed_count = outcome["total"] - outcome["skipped"]
        if outcome["failed"] == 0 and executed_count > 0:
            key = compute_key(checkout, rel, own, **overrides)
            store(state_dir, rel, key, executed_count, outcome["total"],
                  head, source)
            stored.append(rel)
        else:
            dropped.append(rel)
            drop(state_dir, rel)
    return {"stored": stored, "dropped": dropped, "outcomes": outcomes}
