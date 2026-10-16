"""Runtime coverage index from periodic full-suite runs (CI-05).

A `--cov-context=test` collection pass over the unit/scenario selection
writes a test→executed-code index: for each test, the tree files it
executed (file-level always; executed line numbers ride along because
`coverage json --show-contexts` reports them for free). CI-06 will read
this to narrow selections; until then the index is advisory - sharing
evidence is not skipping execution yet.

On demand only, never on the merge path: the collection instruments a
gate-scale pytest run, so it runs on a scratch checkout behind the heavy
lock (`scripts/collect_coverage_index.py` refuses while a full-suite
gate holds the machine) and never beside a pooled gate.

Fail-closed everywhere, mirroring `result_cache` and `pytest_timing`:

- the index is versioned by tree sha + interpreter + native fingerprint
  (reusing `result_cache`'s helpers); a stale or unparseable index is
  REFUSED with the reason, never used as if fresh;
- a red collection run writes nothing: partial coverage from a failing
  suite would let a later selector narrow on missing evidence;
- only files inside the checkout are indexed: site-packages and stdlib
  executions are environment, not tree dependencies, and recording them
  would let a selector treat the machine as the tree;
- without `pytest-cov` importable the collection refuses rather than
  writing a hollow index.

Overhead (captain call C5): the collection records its own wall time
and, when the caller passes the last uninstrumented run's wall time as
the baseline, the delta and ratio. The numbers live in the index's
`collection` envelope; the policy decision (always trace / sample /
refuse) reads them there.

Stdlib only: `library/` never imports coverage or pytest-cov (they are
dev requirements, like pytest itself). The instrumented run shells out
to them; everything this module reads and writes is JSON.

Index location follows `scripts/pytest_timing.py`'s data convention:
`VEP_COVERAGE_INDEX_FILE` wins, else
`$XDG_CACHE_HOME/ren/pytest-coverage-index.json`.
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
import time
from pathlib import Path

INDEX_VERSION = 1

INDEX_FILENAME = "pytest-coverage-index.json"

# Roots whose executions are tree dependencies. Mirrors
# `result_cache.KeyBuilder.INDEX_ROOTS`: the same trees tests import
# from, so the index and the invalidation key agree on what the tree is.
INDEX_ROOTS = ("library", "tests", "ren")

# Selection the collection instruments: the gate's main phase
# (`scripts/full_suite_gate.sh` runs `tests/` under the shared markers).
# Real-model and resolve-live tests stay excluded on every new selection
# path; the caller passes `config.PYTEST_MARKERS`, never its own.
DEFAULT_SELECTION = ("tests/unit", "tests/scenarios")


def index_path() -> Path:
    """Where the index lives: beside the timing telemetry."""
    configured = os.environ.get("VEP_COVERAGE_INDEX_FILE")
    if configured:
        return Path(configured).expanduser()
    cache_root = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache"))
    return cache_root / "ren" / INDEX_FILENAME


def tree_sha(checkout: str, override: str | None = None) -> str | None:
    """The checkout's HEAD sha, or None when the tree has none.

    None poisons the version check (fail closed): an index whose tree
    cannot be named must never compare equal.
    """
    if override is not None:
        return override
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=checkout,
            capture_output=True, encoding="utf-8", check=False, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return None
    sha = (result.stdout or "").strip()
    return sha if result.returncode == 0 and sha else None


def has_pytest_cov(python: str) -> bool:
    """Whether the collection interpreter can trace test contexts."""
    try:
        result = subprocess.run(
            [python, "-c", "import pytest_cov"],
            capture_output=True, encoding="utf-8", check=False, timeout=60)
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0


def _relative_tree_file(checkout: str, value: str) -> str | None:
    """Repo-relative path for an executed file, or None to skip it.

    Skips absolute paths that resolve outside the checkout
    (site-packages, stdlib, the machine's own files) and anything that
    is not a `.py` file under the indexed roots: only tree code the
    selector could narrow on is recorded.
    """
    path = Path(value)
    if not path.is_absolute():
        path = Path(checkout) / path
    try:
        relative = path.resolve().relative_to(Path(checkout).resolve())
    except (OSError, ValueError):
        return None
    text = relative.as_posix()
    if relative.suffix != ".py":
        return None
    if not any(text == root or text.startswith(root + "/")
               for root in INDEX_ROOTS):
        return None
    return text


def build_index(contexts_by_file: dict, checkout: str) -> dict[str, dict]:
    """Invert coverage contexts into a test→executed-code index.

    `contexts_by_file` maps a measured file to its
    `{context: [executed lines]}` mapping - the shape `coverage json
    --show-contexts` reports per file. Returns
    `{test_id: {"files": [...], "lines": {file: [...]}}}` with files
    sorted. Contexts with no executed tree lines and files outside the
    checkout contribute nothing: a test that executed nothing
    attributable names nothing, rather than an empty claim the selector
    could misread as independence.
    """
    inverted: dict[str, dict[str, set]] = {}
    for filename, contexts in (contexts_by_file or {}).items():
        relative = _relative_tree_file(checkout, str(filename))
        if relative is None or not isinstance(contexts, dict):
            continue
        for context, lines in contexts.items():
            if not isinstance(context, str) or not context:
                continue
            try:
                executed = sorted({int(line) for line in lines or []})
            except (TypeError, ValueError):
                continue
            if not executed:
                continue
            entry = inverted.setdefault(context, {})
            entry.setdefault(relative, set()).update(executed)
    return {
        context: {
            "files": sorted(files),
            "lines": {name: sorted(lines)
                      for name, lines in sorted(files.items())},
        }
        for context, files in sorted(inverted.items())
    }


def contexts_from_coverage_json(payload: dict) -> dict:
    """Pull the `{file: {context: [lines]}}` mapping from coverage JSON.

    Tolerates the envelope `coverage json --show-contexts` writes
    (`{"files": {name: {"contexts": ...}}}`) and skips files without
    one: an unfamiliar schema yields less evidence, never a crash, and
    less evidence only ever widens a later selection.
    """
    if not isinstance(payload, dict):
        return {}
    files = payload.get("files")
    if not isinstance(files, dict):
        return {}
    contexts_by_file = {}
    for filename, record in files.items():
        if not isinstance(record, dict):
            continue
        contexts = record.get("contexts")
        if isinstance(contexts, dict) and contexts:
            contexts_by_file[filename] = contexts
    return contexts_by_file


def _version_envelope(checkout: str, sha: str | None,
                      interpreter: str | None,
                      native: str | None) -> dict:
    from library.tools.local_ci import result_cache
    resolved_sha = sha if sha is not None else tree_sha(checkout)
    resolved_interpreter = (
        interpreter if interpreter is not None
        else result_cache.pytest_interpreter_version())
    resolved_native = (
        native if native is not None
        else result_cache.native_fingerprint(checkout))
    return {"tree_sha": resolved_sha, "interpreter": resolved_interpreter,
            "native": resolved_native}


def overhead_seconds(wall: float,
                     baseline: float | None) -> tuple[float | None,
                                                     float | None]:
    """The tracing cost: absolute delta and ratio over the baseline.

    No baseline (the first collection on a tree) records no overhead:
    inventing one from a constant would mislead C5's policy read.
    """
    if baseline is None:
        return None, None
    delta = wall - baseline
    try:
        ratio = delta / baseline if baseline > 0 else None
    except (TypeError, ArithmeticError):
        return delta, None
    return delta, ratio


def write_index(destination: Path | str, checkout: str,
                tests: dict[str, dict], collection: dict,
                sha: str | None = None, interpreter: str | None = None,
                native: str | None = None) -> Path:
    """Atomically replace the index (same mkstemp+replace as timings)."""
    target = Path(destination)
    payload = {"version": INDEX_VERSION,
               **_version_envelope(checkout, sha, interpreter, native),
               "collection": collection, "tests": tests}
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{target.name}.",
                                     dir=target.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)
            handle.write("\n")
        os.replace(temporary, target)
    except BaseException:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise
    return target


def load(checkout: str, path: Path | str | None = None,
         sha: str | None = None, interpreter: str | None = None,
         native: str | None = None, *, state_dir: str = "",
         with_metadata: bool = False):
    """Read a fresh index, or refuse with the reason.

    Returns `(tests, None)` when the index at `path` (default
    `index_path()`) versions exactly this checkout, else `(None,
    reason)`: missing, unparseable, wrong version, or a stale
    tree/interpreter/native stamp. A certified CI-07 baseline index may
    also be reused when its tree is an ancestor and environment stamps
    match; with `with_metadata`, the third return value names files
    changed since that baseline. Callers must use static selection for
    those paths. Every refusal names what mismatched.
    """
    def result(tests, reason, invalidated=()):
        return ((tests, reason, sorted(invalidated)) if with_metadata
                else (tests, reason))
    source = Path(path) if path is not None else index_path()
    try:
        payload = json.loads(source.read_text(encoding="utf-8"))
    except OSError:
        return result(None, (
            f"no coverage index at {source} - collect one with "
            f"scripts/collect_coverage_index.py --checkout {checkout}"))
    except ValueError as exc:
        return result(None, (
            f"coverage index at {source} is unparseable ({exc}) - "
            f"refusing; re-collect to replace it"))
    if not isinstance(payload, dict) or payload.get("version") != INDEX_VERSION:
        return result(None, (
            f"coverage index at {source} has version "
            f"{payload.get('version') if isinstance(payload, dict) else '?'} "
            f"(want {INDEX_VERSION}) - refusing; re-collect to replace it"))
    expected = _version_envelope(checkout, sha, interpreter, native)
    for key in ("interpreter", "native"):
        if payload.get(key) != expected[key]:
            return result(None, (
                f"coverage index at {source} is stale: {key} "
                f"{payload.get(key)!r} != {expected[key]!r} - refusing; "
                f"re-collect on this checkout to replace it"))
    invalidated = set()
    if payload.get("tree_sha") != expected["tree_sha"]:
        # Reuse only a fully certified CI-07 baseline, and only when its
        # environment is identical and its commit is an ancestor.
        try:
            from library.tools.local_ci import config, state
            baseline_sha = payload.get("tree_sha")
            certification = state.certified_baseline(
                baseline_sha, state_dir or config.state_dir())
            same_environment = (
                payload.get("interpreter") == expected["interpreter"]
                == (certification or {}).get("interpreter")
                and payload.get("native") == expected["native"]
                == (certification or {}).get("native"))
            ancestor = subprocess.run(
                ["git", "merge-base", "--is-ancestor", str(baseline_sha),
                 str(expected["tree_sha"])], cwd=checkout,
                capture_output=True, encoding="utf-8", check=False,
                timeout=30)
            if certification and same_environment and ancestor.returncode == 0:
                delta = subprocess.run(
                    ["git", "diff", "--name-only", "-z",
                     f"{baseline_sha}..{expected['tree_sha']}"], cwd=checkout,
                    capture_output=True, encoding="utf-8", check=False,
                    timeout=30)
                if delta.returncode == 0:
                    invalidated = {name for name in delta.stdout.split("\0")
                                   if name}
                else:
                    invalidated = {"<unknown>"}
            else:
                invalidated = {"<unknown>"}
        except (OSError, subprocess.SubprocessError, ImportError,
                AttributeError, TypeError, ValueError):
            invalidated = {"<unknown>"}
        if "<unknown>" in invalidated:
            return result(None, (
                f"coverage index at {source} is stale: tree_sha "
                f"{payload.get('tree_sha')!r} != {expected['tree_sha']!r} "
                "and no compatible certified baseline was established"))
    tests = payload.get("tests")
    if not isinstance(tests, dict):
        return result(None, (
            f"coverage index at {source} carries no test mapping - "
            f"refusing; re-collect to replace it"))
    return result(tests, None, invalidated)


def collection_argv(python: str, selection: list[str], checkout: str,
                    workers: int = 2) -> list[str]:
    """The instrumented pytest invocation over the selection.

    Markers and Resolve-driver ignores match the gate's main phase
    (`runner.pytest_argv` minus the Tier-1-only ignores: collection
    covers the whole unit/scenario selection, the way the gate runs
    it). Workers default to the measured count and
    REN_CI_PYTEST_WORKERS overrides them; pytest-cov
    combines the workers' data files itself.
    """
    from library.tools.local_ci import config
    try:
        workers = int(os.environ.get("REN_CI_PYTEST_WORKERS", workers))
    except (TypeError, ValueError):
        raise ValueError("REN_CI_PYTEST_WORKERS must be a positive integer")
    if workers < 1:
        raise ValueError("REN_CI_PYTEST_WORKERS must be a positive integer")
    argv = [python, "-m", "pytest", *selection,
            "-m", config.PYTEST_MARKERS, "--tb=short",
            "-o", "faulthandler_timeout=300",
            "-p", "no:cacheprovider",
            "--cov=library", "--cov=tests", "--cov=ren",
            "--cov-context=test", "--cov-report=",
            "-n", str(workers)]
    for driven in config.RESOLVE_DRIVING:
        if os.path.exists(os.path.join(checkout, driven)):
            argv.extend(["--ignore", driven])
    return argv


def collect(checkout: str, selection: list[str] | None = None,
            python: str = "", baseline_seconds: float | None = None,
            destination: Path | str | None = None,
            workers: int = 2, timeout: int = 3600) -> dict:
    """Run the instrumented selection and write the index. One shot.

    Raises `HygieneRefused` when the run could not start (a gate holds
    the machine, the disk floor fails, pytest-cov is absent): the index
    is on-demand evidence, never worth disturbing the merge path.
    Otherwise returns a result dict; `ok` is True only when the index
    was written. A red suite writes nothing - partial coverage from a
    failing tree would narrow a later selection on missing evidence.
    """
    from library.tools.local_ci import hygiene, runner
    checkout = os.path.abspath(checkout)
    if not python:
        python = runner._pytest_python()
    if hygiene.gate_running():
        raise hygiene.HygieneRefused(
            "a full-suite gate holds the machine - collection is "
            "deferred, not skipped")
    if not hygiene.disk_ok(checkout):
        raise hygiene.HygieneRefused("disk floor fails - refusing")
    if not has_pytest_cov(python):
        raise hygiene.HygieneRefused(
            f"pytest-cov is not importable by {python} - install the dev "
            f"requirements, refusing rather than writing a hollow index")
    selected = list(selection) if selection else list(DEFAULT_SELECTION)
    command = collection_argv(python, selected, checkout, workers)
    started = time.monotonic()
    try:
        run = subprocess.run(
            command, cwd=checkout, capture_output=True, encoding="utf-8",
            check=False, timeout=timeout)
    except (OSError, subprocess.SubprocessError) as exc:
        wall = time.monotonic() - started
        return {"ok": False, "wall_seconds": wall,
                "reason": f"instrumented run did not start ({exc})"}
    wall = time.monotonic() - started
    if run.returncode != 0:
        tail = (run.stderr or run.stdout or "").strip().splitlines()
        tail = tail[-5:] if tail else ["no output"]
        return {"ok": False, "wall_seconds": wall,
                "reason": ("instrumented run exited "
                           f"{run.returncode} - refusing to index a red "
                           f"tree ({'; '.join(tail)[:300]})")}
    contexts_path = os.path.join(checkout, ".coverage-contexts.json")
    dumped = subprocess.run(
        [python, "-m", "coverage", "json", "--show-contexts",
         "-o", contexts_path],
        cwd=checkout, capture_output=True, encoding="utf-8",
        check=False, timeout=300)
    if dumped.returncode != 0:
        return {"ok": False, "wall_seconds": wall,
                "reason": ("coverage json --show-contexts failed "
                           f"({(dumped.stderr or '').strip()[:200]})")}
    try:
        with open(contexts_path, encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, ValueError) as exc:
        return {"ok": False, "wall_seconds": wall,
                "reason": f"coverage contexts unreadable ({exc})"}
    finally:
        try:
            os.unlink(contexts_path)
        except OSError:
            pass
    tests = build_index(contexts_from_coverage_json(payload), checkout)
    delta, ratio = overhead_seconds(wall, baseline_seconds)
    collection = {
        "collected_at": int(time.time()),
        "wall_seconds": round(wall, 3),
        "baseline_seconds": baseline_seconds,
        "overhead_seconds": round(delta, 3) if delta is not None else None,
        "overhead_ratio": round(ratio, 4) if ratio is not None else None,
        "selection": selected,
        "pytest": command,
    }
    target = write_index(
        destination if destination is not None else index_path(),
        checkout, tests, collection)
    return {"ok": True, "wall_seconds": round(wall, 3),
            "overhead_seconds": collection["overhead_seconds"],
            "overhead_ratio": collection["overhead_ratio"],
            "index": str(target), "tests": len(tests)}
