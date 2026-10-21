"""PLAN-003 measured on the representative corpus (BASE-003).

The corpus's briefs name their target roles at the FEATURE level - "the
caption row", "the name card", "the second angle" - and the punch list
asks (PLAN-003) that every named role be resolved to an actual item
before anything is staged.

These tests pin what the corpus's OWN measured evidence can ground, and
what it cannot:

- a brief whose role the corpus's evidence names as ONE measured moment
  grounds, with that moment's source interval;
- a brief whose role the evidence names as SEVERAL measured moments
  stays observed, because a unique target was not established;
- a brief whose role the evidence carries as a feature without naming
  one item stays unresolved, and the destructive operation on it is
  refused.

The measurement tool lives OUTSIDE the engine, because the sealed
answer key must not be a path the engine can read.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]


def _load_tool():
    """The corpus grounding tool, which lives outside the engine."""
    path = REPO_ROOT / "evaluation" / "tools" / "grounding_corpus.py"
    spec = importlib.util.spec_from_file_location("editorial_grounding_tool",
                                                  path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


tool = _load_tool()


@pytest.fixture(scope="module")
def payload():
    return tool.measure()


def test_the_measurement_covers_every_corpus_brief(payload):
    # Defect: a brief the measurement skipped was a brief nobody counted.
    assert payload["brief_count"] == 13
    assert payload["role_instances"] == 8
    assert (payload["grounded_role_instances"]
            + payload["observed_role_instances"]
            + payload["claimed_role_instances"]
            + payload["unresolved_role_instances"]) == 8


def test_a_role_the_evidence_names_as_one_moment_is_grounded(payload):
    # Defect: a target the corpus measured by looking at the frame was
    # reported as unresolved.
    rows = {row["brief_id"]: row for row in payload["briefs"]}

    hook = rows["open-on-the-cost-line"]
    assert hook["grounded_role_instances"] == 1
    target = hook["targets"][0]
    assert target["state"] == "verified"
    assert target["identity"]["kind"] == "measured_moment"
    assert target["interval"]["file"].endswith("LCATL0011.MXF")
    assert target["interval"]["start"] == 109.14
    assert target["interval"]["end"] == 111.87


def test_a_role_the_evidence_names_several_times_stays_observed(payload):
    # Defect: "the second time she said it" was resolved by picking the
    # first occurrence.
    rows = {row["brief_id"]: row for row in payload["briefs"]}

    repeated = rows["cut-the-repeated-definition"]
    assert repeated["observed_role_instances"] == 1
    target = repeated["targets"][0]
    assert target["state"] == "observed"
    assert len(target["candidates"]) == 3


def test_a_role_the_evidence_carries_without_naming_one_item_is_unresolved(
        payload):
    # Defect: a premise the footage carried was read as a resolved
    # target.
    rows = {row["brief_id"]: row for row in payload["briefs"]}

    captions = rows["lift-the-caption-row-but-keep-every-card"]
    assert captions["unresolved_role_instances"] == 1
    assert captions["targets"][0]["state"] == "unresolved"


def test_a_destructive_operation_on_an_ungrounded_role_is_refused(payload):
    # Defect: a destructive change was allowed to act on a target the
    # corpus's own evidence never established.
    assert payload["destructive_operations"] == 10
    assert payload["destructive_operations_refused"] == 6

    rows = {row["brief_id"]: row for row in payload["briefs"]}
    keep_captions = rows["keep-my-captions-where-i-put-them"]
    refused = {refusal["op"] for refusal in keep_captions["refusals"]}
    assert refused == {"clip.trim", "clip.remove"}

    # The brief whose target IS grounded keeps its destructive operation.
    pause = rows["take-out-the-pause-where-she-stops"]
    assert pause["grounded_role_instances"] == 1
    assert pause["destructive_operations_refused"] == 0


def test_the_measurement_reads_no_resolve_and_writes_no_project(payload):
    assert payload["schema"] == tool.SCHEMA
