import pytest
import os
import tempfile
import json
from pathlib import Path

# Since testing the full pipeline runner requires a lot of setup,
# we'll test the helper functions and logic added for recovery.
from library.processes.edit_video.run_pipeline import (
    _is_transient_error,
    PreBridgeError,
    LLMError,
    PostBridgeError
)

def test_is_transient_error():
    assert _is_transient_error(Exception("Connection timeout")) is True
    assert _is_transient_error(Exception("503 Service Unavailable")) is True
    assert _is_transient_error(Exception("Rate limit exceeded")) is True
    assert _is_transient_error(Exception("429 Too Many Requests")) is True
    assert _is_transient_error(Exception("Network error: socket closed")) is True
    
    assert _is_transient_error(Exception("SyntaxError: invalid syntax")) is False
    assert _is_transient_error(Exception("FileNotFoundError: missing clip")) is False
    assert _is_transient_error(Exception("KeyError: 'prompt'")) is False

def test_custom_errors():
    with pytest.raises(PreBridgeError):
        raise PreBridgeError("pre bridge failed")
        
    with pytest.raises(LLMError):
        raise LLMError("llm failed")
        
    with pytest.raises(PostBridgeError):
        raise PostBridgeError("post bridge failed")
