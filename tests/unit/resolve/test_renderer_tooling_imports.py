"""The renderer's verification layer must load when it is run as a SCRIPT.

History: docs/evidence/resolve_test_history.md#test_renderer_tooling_imports.
"""
import json
import os
import subprocess
import sys

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
RENDER_DIR = os.path.join(REPO, "library", "steps", "step_6_01_render")

# Every name the renderer binds in those import groups, and what dies with
# it if the import falls through to the None branch.
REQUIRED_TOOLING = [
    ("apply_super_scale", "neural Super Scale directives"),
    ("apply_stabilization", "neural stabilisation directives"),
    # No fairlight rows: the per-item stub (`get_preset` /
    # `apply_fairlight_preset`) is removed - it returned True having
    # applied nothing - and the renderer no longer imports that module.
    ("verify_clip_placement", "the clip placement QA station"),
    ("verify_transitions", "the transition QA station"),
    ("verify_color_grades", "the colour grade QA station"),
    ("verify_audio", "the audio QA station"),
    ("verify_fusion_comps", "the Fusion comp QA station"),
    ("run_full_timeline_qa", "the final timeline QA sweep"),
    ("plan_qa_checks", "the visual QA router"),
]

_PROBE = r"""
import json, os, sys, importlib.util

# Reproduce script invocation exactly: sys.path[0] is the SCRIPT's own
# directory. Nothing else from the repo is on the path.
render_dir = sys.argv[1]
sys.path.insert(0, render_dir)

# DaVinci Resolve is not running under CI, and the module imports it
# lazily inside functions, so nothing needs stubbing for the import.
spec = importlib.util.spec_from_file_location(
    "rbt_probe", os.path.join(render_dir, "resolve_build_timeline.py"))
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

print(json.dumps({
    "bound": {n: getattr(mod, n, None) is not None for n in json.loads(sys.argv[2])},
    "errors": getattr(mod, "_TOOLING_IMPORT_ERRORS", None),
    "records_errors": hasattr(mod, "_TOOLING_IMPORT_ERRORS"),
}))
"""


def _probe():
    """Import the renderer the way the pipeline does, in a clean process."""
    names = [n for n, _ in REQUIRED_TOOLING]
    env = dict(os.environ)
    # A PYTHONPATH carrying the repo root would mask the bug.
    env.pop("PYTHONPATH", None)
    proc = subprocess.run(
        [sys.executable, "-c", _PROBE, RENDER_DIR, json.dumps(names)],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        cwd=os.path.dirname(REPO),  # not the repo root, so '' cannot help
        env=env, timeout=120,
    )
    if proc.returncode != 0:
        pytest.fail(
            f"Importing resolve_build_timeline as a script failed outright:\n"
            f"{proc.stderr[-3000:]}")
    return json.loads(proc.stdout.strip().splitlines()[-1])


def test_every_verification_tool_binds_in_a_scripted_run():
    result = _probe()
    missing = [
        f"{name} ({what})"
        for name, what in REQUIRED_TOOLING
        if not result["bound"].get(name)
    ]
    assert not missing, (
        "These renderer tools are None when the module is run as a script, "
        "so they silently do nothing in production:\n  - "
        + "\n  - ".join(missing)
        + f"\nRecorded import errors: {result['errors']}"
    )


