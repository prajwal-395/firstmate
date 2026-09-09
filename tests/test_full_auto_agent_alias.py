"""The `--full-auto agy` mode is named `agent`: the mechanism (the pipeline
writes a request file, an agent answers it) rather than the vendor that
used to supply the agent.

`agy` stays working as a deprecated alias: scripts, briefs, saved commands
and muscle memory all pass it today.
"""
import io
import json
import sys
from contextlib import redirect_stderr
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from library.processes.edit_video.run_pipeline import (
    build_parser,
    normalize_full_auto,
    present_llm_step,
    LLMError,
)
from library.tools.brief_reference import HARNESS_READS_FILES
from library.tools.window_frames import HARNESS_SHOWS_FRAMES


def test_normalize_full_auto_agent_is_canonical(capsys):
    assert normalize_full_auto("agent") == "agent"
    assert capsys.readouterr().err == ""


def test_normalize_full_auto_agy_is_a_deprecated_alias(capsys):
    assert normalize_full_auto("agy") == "agent"
    err = capsys.readouterr().err
    assert "deprecated" in err
    assert "agy" in err
    assert "agent" in err


def test_normalize_full_auto_leaves_other_modes_alone(capsys):
    assert normalize_full_auto("api") == "api"
    assert normalize_full_auto("mock") == "mock"
    assert normalize_full_auto(None) is None
    assert capsys.readouterr().err == ""


def test_parser_accepts_agent_and_agy():
    assert build_parser().parse_args(
        ["--slug", "x", "--full-auto", "agent"]).full_auto == "agent"
    assert build_parser().parse_args(
        ["--slug", "x", "--full-auto", "agy"]).full_auto == "agy"


def test_harness_enumerations_name_agent_not_agy():
    assert HARNESS_READS_FILES["agent"] is True
    assert HARNESS_SHOWS_FRAMES["agent"] is True
    assert "agy" not in HARNESS_READS_FILES
    assert "agy" not in HARNESS_SHOWS_FRAMES


def _write_handoff(project_dir: Path) -> str:
    prompt_path = project_dir / "handoff.md"
    prompt_path.write_text("Test prompt")
    return str(prompt_path)


def test_full_auto_agent_writes_request(tmp_path):
    """`--full-auto agent` writes the request file the agent answers."""
    project_dir = tmp_path / "test_project"
    project_dir.mkdir()
    inputs = {"project_folder": str(project_dir), "some_data": 456}

    with patch("time.sleep"), patch(
        "time.time", side_effect=[0, 0, 0, 10, 10, 10, 10, 10]
    ):
        with pytest.raises(LLMError, match="Timeout"):
            present_llm_step(
                _write_handoff(project_dir), inputs, "test_step",
                full_auto="agent", llm_timeout=1)

    req_file = (project_dir / "pipeline_output"
                / "llm_requests" / "test_step.json")
    assert req_file.exists()
    req_data = json.loads(req_file.read_text(encoding="utf-8"))
    assert req_data["step_id"] == "test_step"
    assert req_data["prompt"] == "Test prompt"
    assert "timestamp" in req_data


def test_full_auto_agy_alias_still_writes_request(tmp_path, capsys):
    """The deprecated alias reaches the same mechanism, and says so."""
    project_dir = tmp_path / "test_project"
    project_dir.mkdir()
    inputs = {"project_folder": str(project_dir)}

    with patch("time.sleep"), patch(
        "time.time", side_effect=[0, 0, 0, 10, 10, 10, 10, 10]
    ):
        with pytest.raises(LLMError, match="Timeout"):
            present_llm_step(
                _write_handoff(project_dir), inputs, "test_step",
                full_auto="agy", llm_timeout=1)

    assert (project_dir / "pipeline_output"
            / "llm_requests" / "test_step.json").exists()
    err = capsys.readouterr().err
    assert "deprecated" in err
    assert "agent" in err


def test_full_auto_agent_reads_response(tmp_path):
    project_dir = tmp_path / "test_project"
    project_dir.mkdir()
    inputs = {"project_folder": str(project_dir)}
    responses_dir = (project_dir / "pipeline_output" / "llm_responses")
    res_file = responses_dir / "test_step.json"
    res_data = {"test_out": "agent_success"}

    def mock_sleep(secs):
        res_file.parent.mkdir(parents=True, exist_ok=True)
        res_file.write_text(json.dumps(res_data), encoding="utf-8")

    with patch("time.sleep", side_effect=mock_sleep):
        output = present_llm_step(
            _write_handoff(project_dir), inputs, "test_step",
            full_auto="agent", llm_timeout=5)

    assert output == res_data
