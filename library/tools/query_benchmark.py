"""Cold/warm split for the repeated footage-query path (scout item 6).

The scout measured one number - an 11.1 s `ren search` process wall - and
item 6 refuses to build a resident service on it: no evidence says how
much of that wall is model load versus query, and a resident embedder
competes for the same Mac memory as Resolve, ffmpeg and ML workers. This
module is the first half of that item: a fixture-based benchmark that
separates process startup, model load, query encode, index read and
`ren now` read costs over repeated identical requests, and records peak
RSS and cache state.

The second half - the opt-in resident worker - lives in
`library/tools/resident_query.py`. The verdict below is the go/no-go
between them: the service half lands as opt-in only, and only when the
measurement shows a material repeatable model-load fraction inside the
declared memory budget. The one real-model pass the item asks for is a
later manual step (`--real --project`), never part of the test suite:
every test here runs the fixture (a fake embedder with controlled
sleeps), so the contract suite never loads a real model.

Usage:

    python3 -m library.tools.query_benchmark --json
    python3 -m library.tools.query_benchmark --repeats 5 --out report.json
    python3 -m library.tools.query_benchmark --real --project <dir> --json
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

STAGES = ("process_startup", "model_load", "query_encode", "index_read", "now_read")
"""The five costs one repeated request is split into, in first-query order."""

RESIDENT_RSS_BUDGET_MB = 512.0
"""The user-session query worker's declared memory budget.

A resident embedder shares the captain's Mac with Resolve, ffmpeg and
the ML workers, so the worker must fit inside this - not beside them.
The benchmark's verdict compares measured peak RSS against it, and the
worker reports its own RSS on every `status` so an over-budget resident
is visible rather than silent.
"""

MODEL_LOAD_FRACTION_THRESHOLD = 0.5
"""A model load is material when it is at least half the query path.

The fraction is load over (load + encode + index + now) - the steady
query path, not the fresh-process wall. Process startup is reported
beside it but never in the fraction: startup is paid once per process
either way, while only a repeatable load fraction justifies residency.
"""

MODEL_LOAD_MIN_S = 1.0
"""... and at least this long in absolute terms.

A 90%-of-nothing load is not worth a service: both the fraction and
this floor must hold for the verdict to say the service is justified.
"""

REAL_LOADER_MODULES = ("torch", "sentence_transformers", "transformers")
"""Imports that prove a real model loader ran in this process.

The fixture path never touches them; the report lists whichever are
present so a contract test can assert the benchmark ran model-free.
"""


def _rss_mb() -> float:
    """This process's peak RSS in MiB."""
    import resource

    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    if sys.platform == "darwin":
        return peak / 1024 / 1024
    return peak / 1024


def _children_rss_mb() -> float:
    """The peak RSS of waited-for child processes in MiB, else 0."""
    import resource

    try:
        peak = resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss
    except Exception:  # noqa: BLE001 - no children yet is not a measurement
        return 0.0
    if sys.platform == "darwin":
        return peak / 1024 / 1024
    return peak / 1024


def peak_rss_mb() -> dict:
    """Peak RSS of this process and its waited-for children."""
    own = _rss_mb()
    children = _children_rss_mb()
    return {
        "self_mb": round(own, 2),
        "children_mb": round(children, 2),
        "peak_mb": round(max(own, children), 2),
    }


def loaded_real_modules() -> list:
    """Which real model-loader modules are imported in this process."""
    return sorted(mod for mod in REAL_LOADER_MODULES if mod in sys.modules)


class FakeEmbedder:
    """A stand-in encoder with a controlled load cost and encode cost.

    `load_s` sleeps once per instance (the model load the benchmark is
    trying to isolate); `encode_s` sleeps per `encode` call (the query
    cost that stays even when resident). Vectors are deterministic
    sha256 slices, so repeated identical requests encode identically.
    """

    def __init__(
        self, load_s: float = 2.0, encode_s: float = 0.02, dim: int = 8
    ) -> None:
        self.load_s = load_s
        self.encode_s = encode_s
        self.dim = dim
        self.loads = 0
        self.encodes = 0

    def load(self) -> float:
        """Pay the one-time load cost. Returns its wall seconds."""
        started = time.perf_counter()
        time.sleep(self.load_s)
        self.loads += 1
        return time.perf_counter() - started

    def encode(self, texts: list) -> list:
        """Encode texts, paying the per-query cost. Returns vectors."""
        started = time.perf_counter()
        time.sleep(self.encode_s)
        self.encodes += 1
        vectors = []
        for text in texts:
            digest = hashlib.sha256(text.encode("utf-8")).digest()
            vectors.append([b / 255.0 for b in digest[: self.dim]])
        self.last_wall_s = time.perf_counter() - started
        return vectors


class FixtureIndex:
    """A tiny file-backed index with a recorded fingerprint.

    The payload carries its own `fingerprint`, the way the real index
    records its ingest fingerprint - so the worker's staleness check
    exercises the same shape here as against real footage.
    """

    FILENAME = "fixture_index.json"

    def __init__(self, root) -> None:
        self.root = Path(root)
        self.path = self.root / self.FILENAME

    def write(self, documents: list, fingerprint: str) -> int:
        """Write the index. Returns its size in bytes."""
        self.root.mkdir(parents=True, exist_ok=True)
        payload = {"fingerprint": fingerprint, "documents": documents}
        self.path.write_text(json.dumps(payload), encoding="utf-8")
        return self.path.stat().st_size

    def read(self) -> tuple:
        """Read the index. Returns `(payload, wall_s, size_bytes)`."""
        started = time.perf_counter()
        payload = json.loads(self.path.read_text(encoding="utf-8"))
        return payload, time.perf_counter() - started, self.path.stat().st_size


def process_startup_cost(
    import_module: str = "library.tools.analysis.footage_query",
) -> dict:
    """Fresh-process startup: bare interpreter versus importing query code.

    Both run in a child process, so the parent's own imports never
    pollute the measurement. The import half is the query path's real
    startup - `load_embedder` is lazy, so importing never loads a model.
    """
    bare = [sys.executable, "-c", "pass"]
    started = time.perf_counter()
    subprocess.run(bare, capture_output=True, check=True, encoding="utf-8")
    interpreter_s = time.perf_counter() - started
    started = time.perf_counter()
    proc = subprocess.run(
        [sys.executable, "-c", f"import {import_module}"],
        capture_output=True,
        check=False,
        encoding="utf-8",
    )
    import_s = time.perf_counter() - started
    return {
        "interpreter_s": round(interpreter_s, 4),
        "import_s": round(import_s, 4),
        "wall_s": round(import_s, 4),
        "import_returncode": proc.returncode,
        "import_stderr_tail": (proc.stderr or "")[-300:],
    }


def fixture_now_state(root, entries: int = 12) -> Path:
    """Write a representative `ren now`-shaped state file for timing."""
    path = Path(root) / "fixture_now_state.json"
    state = {
        "summary": "fixture project state",
        "next_actions": [
            {
                "n": n,
                "verb": "search",
                "detail": f"fixture action {n} over repeated requests",
            }
            for n in range(entries)
        ],
    }
    path.write_text(json.dumps(state), encoding="utf-8")
    return path


def read_now_state(path) -> tuple:
    """Read and parse a now-state file. Returns `(state, wall_s)`."""
    started = time.perf_counter()
    state = json.loads(Path(path).read_text(encoding="utf-8"))
    return state, time.perf_counter() - started


def model_cache_state(model_name: str | None = None) -> dict:
    """Whether the named model's files are already on this machine.

    The fixture (no model) reports absent with zero bytes - an honest
    cold cache, not a skipped measurement. The real path inspects the
    HuggingFace cache for the checkpoint the query loader would read.
    """
    if not model_name:
        return {
            "kind": "fake",
            "present": False,
            "bytes": 0,
            "note": "fixture embedder ships no files; nothing was fetched",
        }
    cache_root = Path(os.environ.get("HF_HOME", Path.home() / ".cache" / "huggingface"))
    slug = "models--" + model_name.replace("/", "--")
    model_dir = cache_root / "hub" / slug
    total = 0
    if model_dir.is_dir():
        for path in model_dir.rglob("*"):
            try:
                if path.is_file():
                    total += path.stat().st_size
            except OSError:
                continue
    return {
        "kind": "real",
        "present": model_dir.is_dir(),
        "bytes": total,
        "path": str(model_dir),
    }


def split_report(
    timings: dict,
    repeats: int,
    model_backend: str,
    cache_state: dict,
    rss: dict,
    real_modules: list,
) -> dict:
    """Assemble the cold/warm report from measured stage walls.

    `timings` carries `process_startup_s`, `model_load_s`,
    `query_encode_s` (mean per query), `index_read_s` and `now_read_s`.
    Pure arithmetic - no timing, no IO - so the verdict math is
    unit-testable without sleeping.
    """
    startup = float(timings["process_startup_s"])
    load = float(timings["model_load_s"])
    encode = float(timings["query_encode_s"])
    index = float(timings["index_read_s"])
    now = float(timings["now_read_s"])
    query_path = load + encode + index + now
    warm = encode + index + now
    cold = startup + query_path
    fraction = (load / query_path) if query_path > 0 else 0.0
    material = load >= MODEL_LOAD_MIN_S and fraction >= MODEL_LOAD_FRACTION_THRESHOLD
    fits = rss["peak_mb"] <= RESIDENT_RSS_BUDGET_MB
    reasons = [
        (
            f"model load {load:.3f}s is "
            f"{fraction * 100:.1f}% of the {query_path:.3f}s query path "
            f"(threshold {MODEL_LOAD_FRACTION_THRESHOLD * 100:.0f}% "
            f"and {MODEL_LOAD_MIN_S:.1f}s)"
        ),
        (
            f"peak RSS {rss['peak_mb']:.1f} MiB against the "
            f"{RESIDENT_RSS_BUDGET_MB:.0f} MiB resident budget"
        ),
    ]
    return {
        "stages": {
            "process_startup": {"wall_s": round(startup, 4)},
            "model_load": {"wall_s": round(load, 4)},
            "query_encode": {"wall_s": round(encode, 4), "repeats": repeats},
            "index_read": {"wall_s": round(index, 4)},
            "now_read": {"wall_s": round(now, 4)},
        },
        "cold_wall_s": round(cold, 4),
        "warm_wall_s": round(warm, 4),
        "model_load_s": round(load, 4),
        "model_load_fraction": round(fraction, 4),
        "peak_rss_mb": rss,
        "cache_state": cache_state,
        "model_backend": model_backend,
        "repeats": repeats,
        "loaded_real_modules": list(real_modules),
        "verdict": {
            "model_load_material": material,
            "rss_fits_budget": fits,
            "service_justified": bool(material and fits),
            "reasons": reasons,
        },
    }


def run_fixture_benchmark(
    repeats: int = 5,
    load_s: float = 0.5,
    encode_s: float = 0.005,
    workdir=None,
    query: str = "where does he talk about parking",
) -> dict:
    """Time the five stages against fakes. No model, no network."""
    # Measure imports caused by this invocation, not modules another test
    # (or caller) loaded before it. A process-wide snapshot made this report
    # depend on test order and falsely blamed the fixture for preloaded torch.
    real_modules_before = set(loaded_real_modules())
    root = (
        Path(workdir) if workdir else Path(tempfile.mkdtemp(prefix="ren-query-bench-"))
    )
    root.mkdir(parents=True, exist_ok=True)
    startup = process_startup_cost()
    embedder = FakeEmbedder(load_s=load_s, encode_s=encode_s)
    load_wall = embedder.load()
    encode_walls = []
    for _ in range(repeats):
        embedder.encode([query])
        encode_walls.append(embedder.last_wall_s)
    encode_mean = sum(encode_walls) / len(encode_walls)
    index = FixtureIndex(root / "index")
    index_bytes = index.write(
        [
            {"id": f"doc_{n}", "text": f"fixture segment {n} about parking"}
            for n in range(20)
        ],
        fingerprint="fixture-v1",
    )
    _payload, index_wall, _ = index.read()
    now_path = fixture_now_state(root)
    _state, now_wall = read_now_state(now_path)
    timings = {
        "process_startup_s": startup["wall_s"],
        "model_load_s": load_wall,
        "query_encode_s": encode_mean,
        "index_read_s": index_wall,
        "now_read_s": now_wall,
    }
    report = split_report(
        timings,
        repeats,
        model_backend=f"fake:load={load_s}s_encode={encode_s}s",
        cache_state=model_cache_state(),
        rss=peak_rss_mb(),
        real_modules=sorted(set(loaded_real_modules()) - real_modules_before),
    )
    report["detail"] = {
        "process_startup": startup,
        "index_bytes": index_bytes,
        "now_state_file": str(now_path),
    }
    return report


def run_real_benchmark(
    project: str, repeats: int = 3, query: str = "where does he talk about parking"
) -> dict:
    """One real-model pass over a real project: the owed measurement.

    Manual and opt-in - never called by the test suite. Loads the real
    text embedder once, encodes one query `repeats` times, reads the
    real index and runs one real `ren now` read, reporting the same
    cold/warm split. Run this once and record the verdict before the
    resident service is enabled anywhere beyond opt-in use.
    """
    from library.tools import project_work_state
    from library.tools.analysis import footage_query

    startup = process_startup_cost()
    started = time.perf_counter()
    encode_fn, backend = footage_query.load_embedder()
    load_wall = time.perf_counter() - started
    if encode_fn is None:
        raise RuntimeError(f"no real embedder available: {backend}")
    encode_walls = []
    for _ in range(repeats):
        started = time.perf_counter()
        encode_fn([query])
        encode_walls.append(time.perf_counter() - started)
    encode_mean = sum(encode_walls) / len(encode_walls)
    started = time.perf_counter()
    index = footage_query.FootageIndex(project)
    _segments = index.segments
    _matrix = index.matrix
    index_wall = time.perf_counter() - started
    started = time.perf_counter()
    project_work_state.project_work_state(project)
    now_wall = time.perf_counter() - started
    timings = {
        "process_startup_s": startup["wall_s"],
        "model_load_s": load_wall,
        "query_encode_s": encode_mean,
        "index_read_s": index_wall,
        "now_read_s": now_wall,
    }
    report = split_report(
        timings,
        repeats,
        model_backend=f"real:{backend}",
        cache_state=model_cache_state(footage_query.EMBED_MODEL),
        rss=peak_rss_mb(),
        real_modules=loaded_real_modules(),
    )
    report["detail"] = {"process_startup": startup, "project": project}
    return report


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="query_benchmark",
        description="Split one repeated footage query into its five "
        "costs and report the cold/warm verdict.",
    )
    parser.add_argument(
        "--repeats",
        type=int,
        default=5,
        help="identical requests to average the warm path over",
    )
    parser.add_argument(
        "--load-s",
        type=float,
        default=0.5,
        help="fixture embedder load cost in seconds",
    )
    parser.add_argument(
        "--encode-s",
        type=float,
        default=0.005,
        help="fixture embedder per-query cost in seconds",
    )
    parser.add_argument("--workdir", help="scratch dir (default: a fresh temp dir)")
    parser.add_argument(
        "--query",
        default="where does he talk about parking",
        help="the repeated query text",
    )
    parser.add_argument(
        "--real",
        action="store_true",
        help="run the one owed real-model pass instead of "
        "the fixture (needs --project; never used "
        "by tests)",
    )
    parser.add_argument("--project", help="real project dir for --real")
    parser.add_argument(
        "--json", action="store_true", help="print the whole report as JSON"
    )
    parser.add_argument("--out", help="write the JSON report to this file")
    args = parser.parse_args(argv)
    if args.real:
        if not args.project:
            parser.error("--real needs --project")
        report = run_real_benchmark(
            args.project, repeats=args.repeats, query=args.query
        )
    else:
        report = run_fixture_benchmark(
            repeats=args.repeats,
            load_s=args.load_s,
            encode_s=args.encode_s,
            workdir=args.workdir,
            query=args.query,
        )
    verdict = report["verdict"]
    print(
        f"cold {report['cold_wall_s']:.3f}s "
        f"warm {report['warm_wall_s']:.3f}s "
        f"load {report['model_load_s']:.3f}s "
        f"({report['model_load_fraction'] * 100:.1f}% of query path) "
        f"rss {report['peak_rss_mb']['peak_mb']:.1f} MiB "
        f"justified={verdict['service_justified']}"
    )
    if args.json or args.out:
        text = json.dumps(report, indent=2, sort_keys=True)
        if args.json:
            print(text)
        if args.out:
            Path(args.out).write_text(text, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
