"""A QA retry sends the DELTA, not the whole request again.

The defect (scout `scout-opt-planning-cost`, C6): `model_task._run`
re-filed the WHOLE request on every QA retry - the same prompt, the same
context, with the feedback appended.  `qa_loop.max_retries` is three, so
a step that failed its QA cost four times the tokens of one attempt.  The
run's own log has `mesh_spine` called twice at 249,501 tokens each and
`plan_vfx` three times at 29,610 tokens each for a two-token answer.

What a model needs to correct itself is three things, and all three are
on disk when the retry is filed: the request it answered, the answer it
wrote, and what was wrong with it.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO))

from library.tools import llm_handshake, model_task  # noqa: E402
from library.tools.project_layout import Area, layout_for  # noqa: E402

NODE_ID = "retrying_step"

BIG_CONTEXT = "\n".join(
    f"row {index}: the measured thing about this clip, at length {index}"
    for index in range(400))


def _run_two_attempts(tmp_path, monkeypatch):
    """Run one step whose first answer fails QA, and read both requests."""
    project = tmp_path / "proj"
    layout = layout_for(str(project))
    layout.ensure()

    handoff = tmp_path / "handoff.md"
    handoff.write_text("# Step\n\nChoose something.\n", encoding="utf-8")

    responses = layout.write_dir(Area.LLM_RESPONSES)
    requests_dir = layout.read_dir(Area.LLM_REQUESTS)
    original_sleep = model_task._agent_sleep
    state = {"calls": 0, "first": None}

    def _sleep(seconds):
        state["calls"] += 1
        if state["calls"] == 1:
            # The FIRST request, read while it is still the one on disk.
            state["first"] = llm_handshake.read_request(
                requests_dir / f"{NODE_ID}.json")
            # An answer with no `answer` field: QA rejects it.
            (responses / f"{NODE_ID}.json").write_text(
                json.dumps({"wrong": "shape"}), encoding="utf-8")
        elif state["calls"] == 3:
            # The retry's answer: the runner polls for it.
            (responses / f"{NODE_ID}.json").write_text(
                json.dumps({"answer": "corrected"}), encoding="utf-8")
        return original_sleep(0)

    monkeypatch.setattr(model_task, "_agent_sleep", _sleep)

    task = model_task.ModelTask(
        key=NODE_ID,
        handoff_path=str(handoff),
        outputs=({"name": "answer", "type": "string", "required": True,
                  "description": "your answer"},),
        llm_outputs=({"name": "answer", "type": "string", "required": True,
                      "description": "your answer"},),
        context_fields=None,
        reads_brief=False,
        skills=None,
    )
    result = model_task.run_model_task(
        task, {"project_folder": str(project), "measured": BIG_CONTEXT},
        full_auto="agent", llm_timeout=5)

    requests = layout.read_dir(Area.LLM_REQUESTS)
    first = state["first"]
    return result, first, requests


def test_the_retry_is_filed_and_the_first_answer_is_rejected(tmp_path,
                                                              monkeypatch):
    result, _first, _requests = _run_two_attempts(tmp_path, monkeypatch)

    assert result == {"answer": "corrected"}, (
        "the retry never reached the model, or its answer was not taken")


def test_the_retry_does_not_re_file_the_whole_request(tmp_path, monkeypatch):
    """The defect, pinned: the second request must be a fraction of the
    first, not a copy of it."""
    _result, first, requests = _run_two_attempts(tmp_path, monkeypatch)

    # The canonical request now holds the RETRY.
    retry = llm_handshake.read_request(requests / f"{NODE_ID}.json")
    first_size = len((first["prompt"] or "") + (first["context"] or ""))
    retry_size = len((retry["prompt"] or "") + (retry["context"] or ""))

    assert retry_size < first_size / 4, (
        f"the retry carries {retry_size} bytes of a {first_size}-byte "
        "request: the whole request went out again")
    assert "measured thing about this clip" not in (retry["context"] or ""), (
        "the context was re-filed whole on the retry")


def test_the_retry_names_the_request_and_the_answer_it_must_correct(
        tmp_path, monkeypatch):
    _result, _first, requests = _run_two_attempts(tmp_path, monkeypatch)

    retry = llm_handshake.read_request(requests / f"{NODE_ID}.json")
    context = retry["context"] or ""
    assert "FILE:" in context, (
        "the retry has to say where the request it answered went")

    attempts = requests / "attempts"
    saved = sorted(path.name for path in attempts.glob("*"))
    assert saved, "the retry saved nothing to correct itself against"
    for name in saved:
        assert str(attempts / name) in context, (
            f"the retry does not name {name}, so the model cannot read it")

    request_copy = next(path for path in attempts.glob("*.md"))
    body = request_copy.read_text(encoding="utf-8")
    assert "Choose something." in body, (
        "the saved request does not hold the task instructions")
    assert "measured thing about this clip" in body, (
        "the saved request does not hold the context the model decided from")

    answer_copy = next(path for path in attempts.glob("*.answer.json"))
    assert json.loads(answer_copy.read_text(encoding="utf-8")) == {
        "wrong": "shape"}, "the rejected answer was not saved"


def test_the_retry_still_asks_for_the_same_shape(tmp_path, monkeypatch):
    """The delta saves tokens; it does not change what the answer is."""
    _result, first, requests = _run_two_attempts(tmp_path, monkeypatch)

    retry = llm_handshake.read_request(requests / f"{NODE_ID}.json")
    assert retry["expected_schema"] == first["expected_schema"]
    assert retry["step_id"] == NODE_ID


def test_a_second_retry_names_the_request_the_model_was_first_asked(
        tmp_path, monkeypatch):
    """The chain keeps the QUESTION.

    A retry that saved "the request currently on disk" would save the
    first retry's delta, and the second retry would point at a delta
    instead of at the question - a chain that loses what the model was
    asked.
    """
    project = tmp_path / "proj"
    layout = layout_for(str(project))
    layout.ensure()
    handoff = tmp_path / "handoff.md"
    handoff.write_text("# Step\n\nChoose something.\n", encoding="utf-8")
    responses = layout.write_dir(Area.LLM_RESPONSES)
    original_sleep = model_task._agent_sleep
    state = {"calls": 0}

    def _sleep(seconds):
        state["calls"] += 1
        if state["calls"] in (1, 3):
            # An answer with no `answer` field: QA rejects it.
            (responses / f"{NODE_ID}.json").write_text(
                json.dumps({"wrong": "shape"}), encoding="utf-8")
        elif state["calls"] == 5:
            (responses / f"{NODE_ID}.json").write_text(
                json.dumps({"answer": "corrected"}), encoding="utf-8")
        return original_sleep(0)

    monkeypatch.setattr(model_task, "_agent_sleep", _sleep)

    task = model_task.ModelTask(
        key=NODE_ID,
        handoff_path=str(handoff),
        outputs=({"name": "answer", "type": "string", "required": True,
                  "description": "your answer"},),
        llm_outputs=({"name": "answer", "type": "string", "required": True,
                      "description": "your answer"},),
        context_fields=None,
        reads_brief=False,
        skills=None,
    )
    model_task.run_model_task(
        task, {"project_folder": str(project)}, full_auto="agent",
        llm_timeout=5)

    requests = layout.read_dir(Area.LLM_REQUESTS)
    retry = llm_handshake.read_request(requests / f"{NODE_ID}.json")
    context = retry["context"] or ""
    assert context.count("FILE:") == 2
    named = [line.split("FILE:", 1)[1].strip()
             for line in context.split("\n") if "FILE:" in line]
    request_copy = next(path for path in named if path.endswith(".md"))
    body = open(request_copy, encoding="utf-8").read()
    assert "Choose something." in body, (
        "the saved request is not the request the model was first asked")
    assert "rejected" not in body, (
        "the saved request is a delta, not the question")
