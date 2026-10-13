"""The opt-in resident query worker (scout item 6, second half).

Acceptance, verbatim from the item: a fake embedder with a controlled
2 s load and 20 ms encode shows the first query pays load once and 20
subsequent queries do not reload it; worker restart, stale index
fingerprint and idle eviction all recover safely. The benchmark half
reports cold/warm wall and RSS without loading a real model (see
`test_query_benchmark.py`). Main has neither the worker nor the split
report - these tests import both, so they fail there and pass here.

Hermetic by construction: every worker here serves `--embedder fake`
over a fixture JSON index. No model pass, no network, no Resolve, no
real project - index files live under `tmp_path`, and only the socket
runtime dir sits under `/tmp` (Unix socket paths cannot be 100+
characters long, which a pytest tmp dir always is).
"""

import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import pytest

from library.tools import resident_query
from library.tools.resident_query import StaleSocket

REPO_ROOT = Path(__file__).resolve().parents[3]
QUERY = "where does he talk about parking"
REQUEST_COUNT = 21  # first query pays load; 20 subsequent must not reload


def _write_index(path: Path, fingerprint: str, documents: list) -> None:
    path.write_text(
        json.dumps({"fingerprint": fingerprint, "documents": documents}),
        encoding="utf-8",
    )


@pytest.fixture()
def runtime(tmp_path):
    """A fixture index plus a short socket dir, cleaned up afterwards."""
    index_file = tmp_path / "index.json"
    _write_index(
        index_file,
        "v1",
        [
            {"id": "a", "text": "he talks about parking the car"},
            {"id": "b", "text": "waves crash on the shore"},
        ],
    )
    socket_dir = tempfile.mkdtemp(prefix="renq-", dir="/tmp")
    procs = []

    def start(extra_args):
        proc = resident_query.ensure_started(
            socket_dir, extra_args=extra_args, cwd=str(REPO_ROOT)
        )
        if proc is not None:
            procs.append(proc)
        return proc

    yield {"index_file": index_file, "socket_dir": socket_dir, "start": start}
    resident_query.stop(socket_dir)
    for proc in procs:
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=10)
    for proc in procs:
        assert proc.poll() is not None, "worker process leaked"
    try:
        Path(socket_dir).rmdir()
    except OSError:
        pass


def _serve_args(runtime, **overrides):
    args = [
        "--index-file",
        str(runtime["index_file"]),
        "--fake-load-s",
        "2.0",
        "--fake-encode-s",
        "0.02",
        "--idle-seconds",
        "60",
    ]
    for key, value in overrides.items():
        args.extend([f"--{key.replace('_', '-')}", str(value)])
    return args


def test_first_query_pays_load_once_over_21_queries(runtime):
    """The item's core claim: 2 s load on query one, ~20 ms encodes
    after, one embedder load across all 21 identical requests."""
    runtime["start"](_serve_args(runtime))
    walls, replies = [], []
    started = time.perf_counter()
    for _ in range(REQUEST_COUNT):
        call_started = time.perf_counter()
        replies.append(resident_query.query(runtime["socket_dir"], QUERY))
        walls.append(time.perf_counter() - call_started)
    total = time.perf_counter() - started
    assert all(reply.get("ok") for reply in replies)
    assert all(reply["model_loads"] == 1 for reply in replies)
    assert replies[0]["cold"] is True
    assert all(reply["cold"] is False for reply in replies[1:])
    assert len({reply["generation"] for reply in replies}) == 1
    # The load happened exactly once: the whole run still paid the
    # controlled 2 s, while no later query came near paying it again.
    # Bounds are loose on purpose - the counters above, not the clock,
    # carry the assertion on a loaded machine.
    assert total >= 1.5
    assert max(walls[1:]) < 1.5
    assert replies[0]["hits"][0]["id"] == "a"


def test_worker_restart_recovers_safely(runtime):
    proc = runtime["start"](_serve_args(runtime))
    first = resident_query.query(runtime["socket_dir"], QUERY)
    assert first["ok"] and first["model_loads"] == 1
    proc.terminate()
    proc.wait(timeout=15)
    assert proc.poll() is not None
    runtime["start"](_serve_args(runtime))
    second = resident_query.query(runtime["socket_dir"], QUERY)
    assert second["ok"] and second["model_loads"] == 1
    assert second["generation"] != first["generation"]
    assert second["hits"][0]["id"] == "a"


def test_stale_index_fingerprint_reloads_index_not_embedder(runtime):
    runtime["start"](_serve_args(runtime))
    first = resident_query.query(runtime["socket_dir"], QUERY)
    assert first["index_fingerprint"] == "v1"
    _write_index(
        runtime["index_file"],
        "v2",
        [
            {"id": "c", "text": "he talks about parking again, rebuilt"},
        ],
    )
    second = resident_query.query(runtime["socket_dir"], QUERY)
    assert second["ok"] is True
    assert second["reloaded"] == "stale-index"
    assert second["index_fingerprint"] == "v2"
    assert second["index_loads"] == first["index_loads"] + 1
    assert second["model_loads"] == 1, (
        "a rebuilt index must not pay the embedder load again"
    )
    assert second["hits"][0]["id"] == "c"


def test_idle_eviction_exits_and_next_query_recovers(runtime):
    proc = runtime["start"](_serve_args(runtime, idle_seconds="0.3"))
    assert resident_query.query(runtime["socket_dir"], QUERY)["ok"] is True
    deadline = time.monotonic() + 15
    while proc.poll() is None and time.monotonic() < deadline:
        time.sleep(0.1)
    assert proc.poll() == 0, "idle worker must exit cleanly, not linger"
    with pytest.raises(StaleSocket):
        resident_query.status(runtime["socket_dir"])
    runtime["start"](_serve_args(runtime))
    revived = resident_query.query(runtime["socket_dir"], QUERY)
    assert revived["ok"] is True and revived["model_loads"] == 1


def test_missing_index_refuses_with_a_fix(runtime):
    runtime["index_file"].unlink()
    runtime["start"](_serve_args(runtime))
    reply = resident_query.query(runtime["socket_dir"], QUERY)
    assert reply.get("ok") is False
    assert reply.get("fix"), (
        "a refusal without a fix is the defect ren_refusal exists to remove"
    )
    assert resident_query.status(runtime["socket_dir"])["ok"] is True


def test_status_reports_generations_counts_and_budget(runtime):
    runtime["start"](_serve_args(runtime))
    resident_query.query(runtime["socket_dir"], QUERY)
    info = resident_query.status(runtime["socket_dir"])
    assert info["ok"] is True
    assert info["model_loads"] == 1
    assert info["index_loads"] == 1
    assert info["index_fingerprint"] == "v1"
    assert info["budget_mb"] == 512.0
    assert info["over_budget"] is False
    assert info["rss_mb"] > 0


def test_socket_dir_is_opt_in_only(tmp_path):
    """Nothing is listening where nobody served: a cold socket dir
    refuses with the exact start command, and status agrees."""
    empty = tmp_path / "never-served"
    empty.mkdir()
    with pytest.raises(StaleSocket):
        resident_query.query(str(empty), QUERY)
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "library.tools.resident_query",
            "status",
            "--socket-dir",
            str(empty),
        ],
        capture_output=True,
        check=False,
        encoding="utf-8",
        cwd=str(REPO_ROOT),
    )
    assert proc.returncode == 4
    assert "serve --socket-dir" in proc.stderr
