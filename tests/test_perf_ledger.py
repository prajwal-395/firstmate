"""The run profile charges each second once.

Two defects this pins, both seen on the first smoke run of the ledger:
a render fanned out over four threads summed to four times its wall,
and a model load nested inside an inference span was counted in both
layers. Either makes `ren profile` point at the wrong layer, which is
the one thing it exists not to do.

Fixture-only: no step, model or renderer runs.
"""

import threading
import time

from library.tools import perf_ledger


def _shares(project):
    rows = perf_ledger.read_rows(project)
    report = perf_ledger.profile(rows, "r1")
    return report, {line["name"]: line["wall_s"] for line in report["lines"]}


def test_concurrent_and_nested_spans_are_charged_once(tmp_path):
    project = str(tmp_path)
    with perf_ledger.capability(project, "render_subtitles", "r1"):
        def card():
            with perf_ledger.span("remotion_render"):
                time.sleep(0.2)
        threads = [threading.Thread(target=card) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        with perf_ledger.span("gemma_inference"):
            time.sleep(0.05)
            with perf_ledger.span("model_load"):
                time.sleep(0.15)
            time.sleep(0.05)

    report, shares = _shares(project)
    rows = perf_ledger.read_rows(project)
    render_walls = [r["wall_s"] for r in rows
                    if r.get("layer") == "remotion_render"]
    # Only lower bounds are wall-clock facts (a sleep never returns
    # early); everything else is a relation that holds however a loaded
    # machine schedules the threads.
    assert len(render_walls) == 4
    assert max(render_walls) <= shares["remotion_render"] + 0.01
    assert shares["remotion_render"] < 0.5 * sum(render_walls)
    assert shares["model_load"] >= 0.15
    assert shares["gemma_inference"] >= 0.1
    assert report["overlap_s"] < 0.02
    assert abs(sum(shares.values()) - report["total_wall_s"]) < 0.02


def test_no_ledger_in_the_environment_writes_nothing(tmp_path, monkeypatch):
    monkeypatch.delenv(perf_ledger.LEDGER_ENV, raising=False)
    with perf_ledger.span("gemma_inference") as cost:
        cost["calls"] = 1
    perf_ledger.record("host_model", 1.0)
    assert not list(tmp_path.rglob("*.jsonl"))
