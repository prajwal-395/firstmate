"""Behavioral capability resolution beyond the graph-wide contract audit."""

from dataclasses import replace

import pytest

from library.tools import dag_adapter, operations


def test_a_capability_without_a_legacy_node_refuses_node_keyed_questions():
    """A missing node must not read as a capability with no requirements."""
    scan = operations.get("footage.scan")
    nodeless = replace(scan, owning_dir="step_9_99_nodeless")
    with pytest.raises(dag_adapter.NoLegacyNode, match="footage.scan"):
        nodeless.requires  # noqa: B018


def test_gathering_demands_only_what_a_capability_consumes(tmp_path):
    """Gathering and its refusal agree with the capability's declared reads."""
    for capability in (
        "reel.touchup",
        "transcript.splice",
        "subtitles.render_segment",
    ):
        assert operations.get(capability).consumes == ()
        operations.get(capability).gather(str(tmp_path))
    with pytest.raises(RuntimeError, match="audio_spine"):
        operations.get("subtitles.plan").gather(str(tmp_path))
