"""The QA retry loop, driven through the agent-mode file handshake.

`--full-auto api` (and its mocked `LLMClient`) is gone: Ren answers LLM
steps through the host harness, never through a provider API call. These
tests answer the request files the way a host harness does - a responder
thread writes the response file - and prove the QA loop still retries
with feedback and still respects its budget.
"""

import json
import threading
import time
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from library.processes.edit_video.run_pipeline import present_llm_step


@pytest.fixture
def mock_manifest():
    return {
        "interface": {
            "outputs": [{"name": "result", "type": "string"}]
        }
    }


def _answerer(req, res, answers, seen):
    """Answer agent-backend request files the way a host harness does.

    The thread's lifetime ends HERE, owned by the test that started it:
    call the returned stopper in a `finally` (see
    test_post_bridge_rejection_reaches_the_model for why a stray
    answerer poisons later agent-stub tests at suite scale).
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


@patch("library.tools.model_task.validate_declared_output")
@patch("library.tools.template_loader.TemplateLoader")
def test_failing_qa_triggers_retry(mock_template_loader, mock_validate,
                                   mock_manifest, tmp_path):
    mock_template_loader.return_value.get_brand_constraints.return_value = ""

    # Two answers: first fails QA, second passes
    project = tmp_path / "project"
    project.mkdir()
    req = (project / "pipeline_output" / "llm_requests" / "test_node.json")
    res = (project / "pipeline_output" / "llm_responses" / "test_node.json")
    seen = []
    stop_answerer = _answerer(
        req, res, [{"result": "fail"}, {"result": "success"}], seen)

    # First answer raises RuntimeError, second passes
    mock_validate.side_effect = [RuntimeError("Missing required key"), None]

    prompt_file = tmp_path / "prompt.txt"
    prompt_file.write_text("Test prompt")

    with patch("library.tools.model_task._agent_sleep",
               side_effect=lambda seconds: time.sleep(0.01)):
        try:
            result = present_llm_step(
                str(prompt_file),
                {"project_folder": str(project), "some_input": "hello"},
                "test_node", manifest=mock_manifest, full_auto="agent",
                llm_timeout=30)
        finally:
            stop_answerer()

    assert result == {"result": "success"}
    assert len(seen) == 2
    assert mock_validate.call_count == 2

    # Check that feedback was appended to the second attempt's context
    assert "QA Feedback from previous attempt" in seen[1]["context"]
    assert "Missing required key" in seen[1]["context"]


@patch("library.tools.model_task.validate_declared_output")
@patch("library.tools.template_loader.TemplateLoader")
def test_retry_budget_respected(mock_template_loader, mock_validate,
                                mock_manifest, tmp_path):
    mock_template_loader.return_value.get_brand_constraints.return_value = ""

    # Always fail
    project = tmp_path / "project"
    project.mkdir()
    req = (project / "pipeline_output" / "llm_requests" / "test_node.json")
    res = (project / "pipeline_output" / "llm_responses" / "test_node.json")
    seen = []
    stop_answerer = _answerer(
        req, res, [{"result": "fail"}] * 5, seen)
    mock_validate.side_effect = RuntimeError("Always fails")

    prompt_file = tmp_path / "prompt.txt"
    prompt_file.write_text("Test prompt")

    with patch("library.tools.model_task._agent_sleep",
               side_effect=lambda seconds: time.sleep(0.01)):
        try:
            result = present_llm_step(
                str(prompt_file),
                {"project_folder": str(project), "some_input": "hello"},
                "test_node", manifest=mock_manifest, full_auto="agent",
                llm_timeout=30)
        finally:
            stop_answerer()

    # Returns best attempt (the last parsed output)
    assert result == {"result": "fail"}
    # Max retries is 2, so 3 attempts total (initial + 2 retries)
    assert len(seen) == 3
    assert mock_validate.call_count == 3
