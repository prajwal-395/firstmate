"""Shared fixtures for the computer-use tests: a mock driver binary and a
client connected to it.

The mock lives at `tests/mock_driver.py` (a test double, not a test file -
it sits at the tests root beside `promotion_test_helpers.py`, where the
layer rules keep non-test helpers). These fixtures point the client's
`$RESOLVE_CONTROL_BIN` at a generated copy.
"""

from __future__ import annotations

import stat
from pathlib import Path

import pytest

from library.tools.computer_use import client as client_module
from library.tools.computer_use.client import DriverClient

MOCK = Path(__file__).resolve().parents[2] / "mock_driver.py"


@pytest.fixture
def mock_bin(tmp_path: Path) -> Path:
    """A runnable copy of the mock driver, pointed at by the env."""
    binary = tmp_path / "mock-resolve-control"
    binary.write_text(
        "#!/usr/bin/env python3\n" + MOCK.read_text(encoding="utf-8"), encoding="utf-8"
    )
    binary.chmod(binary.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return binary


@pytest.fixture
def client(mock_bin: Path, monkeypatch) -> DriverClient:
    monkeypatch.setenv(client_module.ENV_BIN, str(mock_bin))
    with DriverClient() as instance:
        yield instance
