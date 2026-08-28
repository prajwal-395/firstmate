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


# ── The flag exempts a declared output from the emptiness check ─────

def test_may_be_empty_skips_emptiness_check():
    """An output with may_be_empty: true passes validation even when empty."""
    manifest = {
        "interface": {
            "outputs": [
                {
                    "name": "vfx_creative",
                    "type": "list",
                    "required": True,
                    "may_be_empty": True,
                }
            ]
        }
    }
    issues = validate_step_output("plan_vfx", {"vfx_creative": []}, manifest)
    assert not issues, f"Expected no issues for may_be_empty output, got: {issues}"


def test_may_be_empty_still_checks_type():
    """may_be_empty exempts the emptiness check, not the type check."""
    manifest = {
        "interface": {
            "outputs": [
                {
                    "name": "vfx_creative",
                    "type": "list",
                    "required": True,
                    "may_be_empty": True,
                }
            ]
        }
    }
    # Wrong type should still fail.
    issues = validate_step_output("plan_vfx", {"vfx_creative": "not a list"}, manifest)
    assert any("expected type" in i for i in issues), (
        f"Type check should still fire with may_be_empty, got: {issues}"
    )


def test_may_be_empty_still_checks_missing():
    """may_be_empty does not excuse a missing key."""
    manifest = {
        "interface": {
            "outputs": [
                {
                    "name": "vfx_creative",
                    "type": "list",
                    "required": True,
                    "may_be_empty": True,
                }
            ]
        }
    }
    issues = validate_step_output("plan_vfx", {}, manifest)
    assert any("missing required key" in i for i in issues)


# ── A step that has NOT declared the flag still fails on empty ──────

def test_without_flag_empty_list_fails():
    """The emptiness check fires on a required list without may_be_empty."""
    manifest = {
        "interface": {
            "outputs": [
                {"name": "some_list", "type": "list", "required": True}
            ]
        }
    }
    issues = validate_step_output("test_step", {"some_list": []}, manifest)
    assert any("semantically empty" in i for i in issues), (
        f"Expected 'semantically empty' for a step without may_be_empty, got: {issues}"
    )


def test_without_flag_empty_dict_fails():
    """The emptiness check fires on a required dict without may_be_empty."""
    manifest = {
        "interface": {
            "outputs": [
                {"name": "some_dict", "type": "dict", "required": True}
            ]
        }
    }
    issues = validate_step_output("test_step", {"some_dict": {}}, manifest)
    assert any("semantically empty" in i for i in issues)


def test_without_flag_empty_string_fails():
    """The emptiness check fires on a required string without may_be_empty."""
    manifest = {
        "interface": {
            "outputs": [
                {"name": "some_str", "type": "string", "required": True}
            ]
        }
    }
    issues = validate_step_output("test_step", {"some_str": ""}, manifest)
    assert any("semantically empty" in i for i in issues)


# ── Real manifests: vfx and sfx declare the flag, others do not ─────

STEPS_DIR = os.path.join(
    os.path.dirname(__file__), "..", "library", "steps"
)


def _load_manifest(step_dir_name: str) -> dict:
    path = os.path.join(STEPS_DIR, step_dir_name, "manifest.json")
    with open(path) as f:
        return json.load(f)


def test_plan_vfx_declares_may_be_empty():
    """step_4_03_plan_vfx declares may_be_empty on vfx_creative."""
    manifest = _load_manifest("step_4_03_plan_vfx")
    llm_outputs = manifest["interface"]["llm_outputs"]
    vfx_spec = next(o for o in llm_outputs if o["name"] == "vfx_creative")
    assert vfx_spec.get("may_be_empty") is True


def test_plan_sfx_declares_may_be_empty():
    """step_4_04_plan_sfx declares may_be_empty on sfx_creative."""
    manifest = _load_manifest("step_4_04_plan_sfx")
    llm_outputs = manifest["interface"]["llm_outputs"]
    sfx_spec = next(o for o in llm_outputs if o["name"] == "sfx_creative")
    assert sfx_spec.get("may_be_empty") is True


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


def test_plan_sfx_empty_list_passes_qa():
    """An empty sfx_creative passes the same validation the QA loop runs."""
    manifest = _load_manifest("step_4_04_plan_sfx")
    llm_manifest = dict(manifest)
    llm_manifest["interface"] = dict(manifest["interface"])
    llm_manifest["interface"]["outputs"] = manifest["interface"]["llm_outputs"]
    issues = validate_step_output("plan_sfx", {"sfx_creative": []}, llm_manifest)
    assert not issues


# Steps that must NOT have the flag - verify the safe direction

_STEPS_WITHOUT_MAY_BE_EMPTY = [
    ("step_2_05_mesh_spine", "structure"),
    ("step_3_02_select_broll", "broll_creative"),
    ("step_4_02_plan_transitions", "transition_creative"),
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
