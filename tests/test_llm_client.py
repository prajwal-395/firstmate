import os
import pytest
from unittest.mock import patch, MagicMock
from library.tools.llm_client import LLMClient

def test_gemini_missing_api_key(capsys):
    with patch.dict(os.environ, {}, clear=True):
        client = LLMClient("gemini", "gemini-2.5-flash")
        result = client.generate("hello")
        assert result == "{}"
        captured = capsys.readouterr()
        assert "GEMINI_API_KEY missing" in captured.err

def test_openai_missing_api_key(capsys):
    with patch.dict(os.environ, {}, clear=True):
        client = LLMClient("openai", "gpt-4o")
        result = client.generate("hello")
        assert result == "{}"
        captured = capsys.readouterr()
        assert "OPENAI_API_KEY missing" in captured.err

def test_anthropic_missing_api_key(capsys):
    with patch.dict(os.environ, {}, clear=True):
        client = LLMClient("anthropic", "claude-3-5-sonnet")
        result = client.generate("hello")
        assert result == "{}"
        captured = capsys.readouterr()
        assert "ANTHROPIC_API_KEY missing" in captured.err

def test_unknown_provider(capsys):
    client = LLMClient("unknown", "model")
    result = client.generate("hello")
    assert result == "{}"
    captured = capsys.readouterr()
    assert "Unknown LLM provider" in captured.err

@patch("library.tools.llm_client.os.environ.get")
def test_gemini_import_error(mock_get, capsys):
    mock_get.return_value = "fake_key"
    client = LLMClient("gemini", "gemini-2.5-flash")
    with patch.dict("sys.modules", {"google.generativeai": None}):
        result = client.generate("hello")
        assert result == "{}"
        captured = capsys.readouterr()
        assert "google-generativeai not installed" in captured.err
