import pytest
import json
import sys
from unittest.mock import patch, MagicMock

from library.processes.edit_video.run_pipeline import present_llm_step

@pytest.fixture
def mock_manifest():
    return {
        "interface": {
            "outputs": [{"name": "result", "type": "string"}]
        }
    }

@pytest.fixture
def mock_inputs():
    return {"project_folder": "/tmp/test", "some_input": "hello"}


@patch("library.processes.edit_video.run_pipeline.validate_step_output")
@patch("library.tools.template_loader.TemplateLoader")
@patch("library.tools.llm_client.LLMClient")
def test_failing_qa_triggers_retry(mock_llm_client_class, mock_template_loader, mock_validate, mock_inputs, mock_manifest, tmp_path):
    mock_client = MagicMock()
    mock_llm_client_class.return_value = mock_client
    mock_template_loader.return_value.get_brand_constraints.return_value = ""
    mock_template_loader.return_value.get_brand_constraints.return_value = ""
    
    # Two calls: first fails QA, second passes
    mock_client.generate.side_effect = [
        '```json\n{"result": "fail"}\n```',
        '```json\n{"result": "success"}\n```'
    ]
    
    # First call raises RuntimeError, second passes
    mock_validate.side_effect = [RuntimeError("Missing required key"), None]
    
    prompt_file = tmp_path / "prompt.txt"
    prompt_file.write_text("Test prompt")
    
    result = present_llm_step(str(prompt_file), mock_inputs, "test_node", manifest=mock_manifest, full_auto="api")
    
    assert result == {"result": "success"}
    assert mock_client.generate.call_count == 2
    assert mock_validate.call_count == 2
    
    # Check that feedback was appended to the second call's prompt
    second_call_prompt = mock_client.generate.call_args_list[1][0][0]
    assert "QA Feedback from previous attempt" in second_call_prompt
    assert "Missing required key" in second_call_prompt


@patch("library.processes.edit_video.run_pipeline.validate_step_output")
@patch("library.tools.template_loader.TemplateLoader")
@patch("library.tools.llm_client.LLMClient")
def test_retry_budget_respected(mock_llm_client_class, mock_template_loader, mock_validate, mock_inputs, mock_manifest, tmp_path):
    mock_client = MagicMock()
    mock_llm_client_class.return_value = mock_client
    mock_template_loader.return_value.get_brand_constraints.return_value = ""
    mock_template_loader.return_value.get_brand_constraints.return_value = ""
    
    # Always fail
    mock_client.generate.return_value = '```json\n{"result": "fail"}\n```'
    mock_validate.side_effect = RuntimeError("Always fails")
    
    prompt_file = tmp_path / "prompt.txt"
    prompt_file.write_text("Test prompt")
    
    result = present_llm_step(str(prompt_file), mock_inputs, "test_node", manifest=mock_manifest, full_auto="api")
    
    # Returns best attempt (the last parsed output)
    assert result == {"result": "fail"}
    # Max retries is 2, so 3 attempts total (initial + 2 retries)
    assert mock_client.generate.call_count == 3
    assert mock_validate.call_count == 3
