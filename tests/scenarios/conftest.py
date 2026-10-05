"""Per-test isolation for the shadow store.

The reel build's placement commits EditPatch generations keyed by
(reel, source, record position). The scenario tests build the same reel
over and over against the same fake timeline, and a shared store makes a
later test's placement read an earlier test's receipt off a timeline that
is empty - the placement silently does nothing and the build reads back
conformance-clean with no clips on it. A per-test store keeps each
build's generations to itself.
"""
from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _isolated_shadow_store(tmp_path, monkeypatch):
    monkeypatch.setenv("REN_SHADOW_DB", str(tmp_path / "shadow.db"))
