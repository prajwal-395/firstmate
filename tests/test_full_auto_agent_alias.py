"""The `--full-auto agy` mode is named `agent`: the mechanism (the pipeline
writes a request file, an agent answers it) rather than the vendor that
used to supply the agent.

`agy` stays working as a deprecated alias: scripts, briefs, saved commands
and muscle memory all pass it today.
"""
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from library.processes.edit_video.run_pipeline import (
    build_parser,
    present_llm_step,
    LLMError,
)


def test_runner_help_hides_compatibility_spellings_but_still_parses_them():
    parser = build_parser()
    help_text = parser.format_help()
    assert "agy" not in help_text
    assert "api" not in help_text
    assert "--full-auto BACKEND" in help_text

    agy = parser.parse_args(["--project", "/tmp/project", "--full-auto", "agy"])
    api = parser.parse_args(["--project", "/tmp/project", "--full-auto", "api"])
    assert agy.full_auto == "agy"
    assert api.full_auto == "api"


def _write_handoff(project_dir: Path) -> str:
    prompt_path = project_dir / "handoff.md"
    prompt_path.write_text("Test prompt")
    return str(prompt_path)


def test_full_auto_agy_alias_still_writes_request(tmp_path, capsys):
    """The deprecated alias reaches the same mechanism, and says so."""
    project_dir = tmp_path / "test_project"
    project_dir.mkdir()
    inputs = {"project_folder": str(project_dir)}

    with patch("library.tools.model_task._agent_sleep"), patch(
        "library.tools.model_task._agent_clock", side_effect=[0, 0, 0, 10, 10, 10, 10, 10]
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


def test_full_auto_api_is_refused_with_a_fix(tmp_path):
    """`--full-auto api` is gone: provider API calls are out of scope.
    Passing the removed backend refuses in the refusal shape - naming
    the fix - rather than calling anything."""
    from library.tools.ren_refusal import REFUSAL_EXIT_CODE, RenRefusal

    project_dir = tmp_path / "test_project"
    project_dir.mkdir()
    inputs = {"project_folder": str(project_dir), "some_data": 123}
    manifest = {"interface": {"outputs": [{"name": "test_out"}]}}
    with pytest.raises(RenRefusal) as refused:
        present_llm_step(_write_handoff(project_dir), inputs, "test_step",
                         manifest, full_auto="api")
    assert refused.value.fix == "re-run with --full-auto agent"
    assert REFUSAL_EXIT_CODE == 4
