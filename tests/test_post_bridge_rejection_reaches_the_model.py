"""A post-bridge rejection reaches the model that caused it.

PROVEN on the 29 Aug 2026 run of 001: both `mesh_spine` attempts logged
raw 247,336 -> projected 4,911 -> toon 4,070, a BYTE-IDENTICAL context.
`present_llm_step` owns a retry-with-feedback path, but `PostBridgeError`
is raised from `run_hybrid_step` after it has already returned, so the
one class of failure that most needs the feedback bypassed it entirely.
The second answer differed because the model was RESAMPLED, not
corrected - 16.5s of deliberation against the first attempt's 106.7s,
landing 2.6s above a floor it still could not see.

These tests drive the real `run_hybrid_step` against a real failing
post-bridge and read the ARCHIVED REQUEST FILE - the bytes the model is
handed - rather than a captured argument.  That is the exact artefact the
29 Aug run proved was identical across attempts.
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
    def run():
        deadline = time.time() + 120
        for payload in answers:
            while time.time() < deadline:
                if req.exists() and not res.exists():
                    seen.append(json.loads(req.read_text(encoding="utf-8")))
                    res.parent.mkdir(parents=True, exist_ok=True)
                    res.write_text(json.dumps(payload), encoding="utf-8")
                    break
                time.sleep(0.05)
    threading.Thread(target=run, daemon=True).start()


def _drive(tmp_path, fail_attempts, answer_count):
    project = tmp_path / "project"
    project.mkdir()
    step = _step_dir(tmp_path, fail_attempts)
    req = project / "pipeline_output" / "llm_requests" / "mesh_spine.json"
    res = project / "pipeline_output" / "llm_responses" / "mesh_spine.json"
    seen = []
    _answerer(req, res, [{"structure": [{"block": "hook"}]}] * answer_count,
              seen)
    manifest = {"interface": {"outputs": [{"name": "structure"}]}}
    return project, step, manifest, seen


def test_the_second_context_carries_the_violation(tmp_path):
    """The 29 Aug defect, from the other side: attempt two now differs."""
    project, step, manifest, seen = _drive(tmp_path, fail_attempts=1,
                                           answer_count=2)
    result = run_hybrid_step(
        step, {"project_folder": str(project)}, "mesh_spine",
        manifest=manifest, full_auto="agy", llm_timeout=60)

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
    project, step, manifest, seen = _drive(
        tmp_path, fail_attempts=99,
        answer_count=post_bridge_retry.MAX_ATTEMPTS + 2)
    with pytest.raises(PostBridgeError) as exc:
        run_hybrid_step(step, {"project_folder": str(project)}, "mesh_spine",
                        manifest=manifest, full_auto="agy", llm_timeout=60)

    assert len(seen) == post_bridge_retry.MAX_ATTEMPTS, (
        f"the retry is not bounded at {post_bridge_retry.MAX_ATTEMPTS} "
        f"model calls: it made {len(seen)}"
    )
    assert VIOLATION in str(exc.value)
    assert str(post_bridge_retry.MAX_ATTEMPTS) in str(exc.value)


def test_the_feedback_blocks_accumulate_and_stay_bounded(tmp_path):
    """What happens at the bound: the context grows by a fixed, small
    amount and no more - one elided block per failed attempt."""
    project, step, manifest, seen = _drive(
        tmp_path, fail_attempts=99,
        answer_count=post_bridge_retry.MAX_ATTEMPTS + 2)
    with pytest.raises(PostBridgeError):
        run_hybrid_step(step, {"project_folder": str(project)}, "mesh_spine",
                        manifest=manifest, full_auto="agy", llm_timeout=60)
    contexts = [s["context"] for s in seen]
    counts = [c.count(post_bridge_retry.HEADING) for c in contexts]
    assert counts == list(range(post_bridge_retry.MAX_ATTEMPTS)), counts
    ceiling = (post_bridge_retry.MAX_ATTEMPTS *
               (post_bridge_retry.MAX_VIOLATION_CHARS + 600))
    assert len(contexts[-1]) - len(contexts[0]) < ceiling


def test_a_long_violation_is_elided_from_the_middle(tmp_path):
    """The cause of a rejection is as often at the tail as at the head -
    the lesson #409 learned about stderr truncation."""
    text = "HEAD" + ("x" * 9000) + "TAIL"
    block = post_bridge_retry.feedback_block(text, 1)
    assert "HEAD" in block and "TAIL" in block
    assert len(block) < post_bridge_retry.MAX_VIOLATION_CHARS + 600


def test_a_step_with_no_post_bridge_still_calls_once(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    step = tmp_path / "step_2_01_creative_direction"
    step.mkdir()
    (step / "handoff.md").write_text("Direct it.\n", encoding="utf-8")
    req = project / "pipeline_output" / "llm_requests" / "creative_direction.json"
    res = project / "pipeline_output" / "llm_responses" / "creative_direction.json"
    seen = []
    _answerer(req, res, [{"creative_direction": {"target_mood": "wry"}}], seen)
    result = run_hybrid_step(
        step, {"project_folder": str(project)}, "creative_direction",
        manifest={"interface": {"outputs": [{"name": "creative_direction"}]}},
        full_auto="agy", llm_timeout=30)
    assert result == {"creative_direction": {"target_mood": "wry"}}
    assert len(seen) == 1
