"""Per-test isolation for the shadow store.

The reel build's footage placement commits EditPatch generations keyed by
(project, timeline id, idempotency key). A fake timeline's id is derived
from its NAME, so every test that builds the same reel against a fresh
fake collides in the machine-shared store: the placement reads an
earlier test's receipt off a timeline that is empty and silently does
nothing, and the build reads back conformance-clean with no clips on it.
A per-test store keeps each build's generations to itself.
"""
from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _isolated_shadow_store(tmp_path, monkeypatch):
    monkeypatch.setenv("REN_SHADOW_DB", str(tmp_path / "shadow.db"))
