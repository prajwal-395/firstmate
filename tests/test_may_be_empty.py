"""Tests for the may_be_empty manifest flag.

A step must be able to declare that an empty output is a legitimate
answer, and the pipeline must stop treating that as a defect. At the
same time, a step that has NOT declared the flag must still fail on
empty output - the check does real work everywhere else.
"""
import json
import os
import sys
import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from library.processes.edit_video.run_pipeline import validate_step_output


# ── Real manifests: vfx and sfx declare the flag, others do not ─────

STEPS_DIR = os.path.join(
    os.path.dirname(__file__), "..", "library", "steps"
)


def _load_manifest(step_dir_name: str) -> dict:
    path = os.path.join(STEPS_DIR, step_dir_name, "manifest.json")
    with open(path) as f:
        return json.load(f)


def test_plan_vfx_empty_list_passes_qa():
    """An empty vfx_creative passes the same validation the QA loop runs."""
    manifest = _load_manifest("step_4_03_plan_vfx")
    llm_manifest = dict(manifest)
    llm_manifest["interface"] = dict(manifest["interface"])
    llm_manifest["interface"]["outputs"] = manifest["interface"]["llm_outputs"]
    issues = validate_step_output("plan_vfx", {"vfx_creative": []}, llm_manifest)
    assert not issues, (
        f"An empty VFX plan is a correct creative answer and must not be "
        f"rejected as semantically empty: {issues}"
    )


# Steps that must NOT have the flag - verify the safe direction

_STEPS_WITHOUT_MAY_BE_EMPTY = [
    ("step_2_05_mesh_spine", "structure"),
]


@pytest.mark.parametrize("step_dir,key", _STEPS_WITHOUT_MAY_BE_EMPTY)
def test_steps_without_flag_still_reject_empty(step_dir, key):
    """Steps that must produce output still fail on an empty list."""
    manifest = _load_manifest(step_dir)
    llm_outputs = manifest["interface"]["llm_outputs"]
    spec = next(o for o in llm_outputs if o["name"] == key)
    assert not spec.get("may_be_empty"), (
        f"{step_dir}.{key} should NOT have may_be_empty"
    )
    # Build the same validation context the QA loop uses
    llm_manifest = dict(manifest)
    llm_manifest["interface"] = dict(manifest["interface"])
    llm_manifest["interface"]["outputs"] = llm_outputs
    issues = validate_step_output(step_dir, {key: []}, llm_manifest)
    assert any("semantically empty" in i for i in issues), (
        f"Expected {step_dir}.{key} to fail on empty output, got: {issues}"
    )
