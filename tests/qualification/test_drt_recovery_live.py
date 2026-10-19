"""Restore a Resolve recovery archive in a disposable project via AXI."""

from __future__ import annotations

import os

import pytest

pytestmark = pytest.mark.resolve_live


def test_drt_recovery_imports_verifies_and_retires_scratch_project():
    if os.environ.get("REN_LIVE_RESOLVE") != "1":
        pytest.skip("runs only in the granted live-Resolve qualification lane")

    from library.tools.resolved import client

    if client.ping() is None:
        pytest.skip("no ren-resolved broker serving")

    submitted = client.submit(
        "timeline.drt_recovery_probe", {"project": "Ren Qualification"},
        qualification=True)
    job = client.result(submitted["id"], wait=600.0)
    assert job["state"] == "done", f"broker {job['state']}: {job!r}"
    result = job["result"]
    assert result["archive"].endswith(".drt"), result
    assert result["archive_sha256"], result
    assert result["imported"]["timeline_uid"], result
    assert result["saved_readback"] is True, result
    assert result["origin_restored"] is True, result
    assert result["scratch_retired"] is True, result
