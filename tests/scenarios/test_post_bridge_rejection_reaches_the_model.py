"""A post-bridge rejection reaches the model that caused it.

Drives the real `run_hybrid_step` against a failing post-bridge and reads
the ARCHIVED REQUEST FILE - the bytes the model is handed. History:
docs/evidence/post_bridge_retry.md.
"""
import json
import threading
import time
from pathlib import Path

import pytest

from library.processes.edit_video.run_pipeline import (
    PostBridgeError, run_hybrid_step)
from library.tools import post_bridge_retry


VIOLATION = "spine duration 52.1s is outside the declared zone"


def _step_dir(tmp_path: Path, fail_attempts: int) -> Path:
    """A hybrid step whose post-bridge rejects its first N answers."""
    step = tmp_path / "step_2_05_mesh_spine"
    step.mkdir()
    (step / "handoff.md").write_text("Mesh the spine.\n", encoding="utf-8")
    counter = tmp_path / "post_bridge_calls"
    (step / "post_bridge.py").write_text(
        "import json, sys\n"
        f"c = {json.dumps(str(counter))}\n"
        "try:\n"
        "    n = int(open(c).read())\n"
        "except OSError:\n"
        "    n = 0\n"
        "n += 1\n"
        "open(c, 'w').write(str(n))\n"
        "json.load(sys.stdin)\n"
        f"if n <= {fail_attempts}:\n"
        f"    print({json.dumps(VIOLATION)}, file=sys.stderr)\n"
        "    sys.exit(1)\n"
        "print(json.dumps({'timed_spine': {'ok': True}}))\n",
        encoding="utf-8")
    return step


def _answerer(req: Path, res: Path, answers: list, seen: list):
    """Answer up to `len(answers)` agent requests, then STOP.

    Surplus answers prove the step stops asking on its own; the thread's
    lifetime is owned by the test - call the returned stopper in a
    `finally` (a stray thread once corrupted later tests' response files;
    docs/evidence/post_bridge_retry.md).
    """
    stop = threading.Event()

    def run():
        deadline = time.time() + 120
        for payload in answers:
            while time.time() < deadline and not stop.is_set():
                if req.exists() and not res.exists():
                    seen.append(json.loads(req.read_text(encoding="utf-8")))
                    res.parent.mkdir(parents=True, exist_ok=True)
                    res.write_text(json.dumps(payload), encoding="utf-8")
                    break
                time.sleep(0.05)

    thread = threading.Thread(target=run, daemon=True)
    thread.start()

    def stop_answerer():
        stop.set()
        thread.join(timeout=10)

    return stop_answerer


def _drive(tmp_path, fail_attempts, answer_count):
    project = tmp_path / "project"
    project.mkdir()
    step = _step_dir(tmp_path, fail_attempts)
    req = project / "pipeline_output" / "llm_requests" / "mesh_spine.json"
    res = project / "pipeline_output" / "llm_responses" / "mesh_spine.json"
    seen = []
    stop_answerer = _answerer(
        req, res, [{"structure": [{"block": "hook"}]}] * answer_count,
        seen)
    manifest = {"interface": {"outputs": [{"name": "structure"}]}}
    return project, step, manifest, seen, stop_answerer


def test_the_second_context_carries_the_violation(tmp_path):
    """The 29 Aug defect, from the other side: attempt two now differs."""
    project, step, manifest, seen, stop_answerer = _drive(
        tmp_path, fail_attempts=1, answer_count=2)
    try:
        result = run_hybrid_step(
            step, {"project_folder": str(project)}, "mesh_spine",
            manifest=manifest, full_auto="agent", llm_timeout=60)
    finally:
        stop_answerer()

    assert result == {"timed_spine": {"ok": True}}
    assert len(seen) == 2, f"expected two model calls, saw {len(seen)}"

    first, second = seen[0]["context"], seen[1]["context"]
    assert VIOLATION not in first
    assert VIOLATION in second, (
        "the retry context does not carry the post-bridge violation - "
        "this is the resample-until-something-passes behaviour the "
        "29 Aug run proved"
    )
    assert post_bridge_retry.carries_violation(second)
    assert first != second, (
        "both attempts saw a byte-identical context, exactly as "
        "mesh_spine did on 29 Aug"
    )


def test_the_retry_is_bounded_and_fails_carrying_the_last_violation(tmp_path):
    """Bounded at MAX_ATTEMPTS model calls; the failure carries the last
    violation; the context grows by one elided block per failed attempt
    and no more."""
    project, step, manifest, seen, stop_answerer = _drive(
        tmp_path, fail_attempts=99,
        answer_count=post_bridge_retry.MAX_ATTEMPTS + 2)
    try:
        with pytest.raises(PostBridgeError) as exc:
            run_hybrid_step(step, {"project_folder": str(project)}, "mesh_spine",
                            manifest=manifest, full_auto="agent", llm_timeout=60)
    finally:
        stop_answerer()

    assert len(seen) == post_bridge_retry.MAX_ATTEMPTS, (
        f"the retry is not bounded at {post_bridge_retry.MAX_ATTEMPTS} "
        f"model calls: it made {len(seen)}"
    )
    assert VIOLATION in str(exc.value)
    assert str(post_bridge_retry.MAX_ATTEMPTS) in str(exc.value)
    contexts = [s["context"] for s in seen]
    counts = [c.count(post_bridge_retry.HEADING) for c in contexts]
    assert counts == list(range(post_bridge_retry.MAX_ATTEMPTS)), counts
    ceiling = (post_bridge_retry.MAX_ATTEMPTS *
               (post_bridge_retry.MAX_VIOLATION_CHARS + 600))
    assert len(contexts[-1]) - len(contexts[0]) < ceiling
