"""The planner is told what an envelope buys, and what the library holds.

Project 001 shipped two camera shutters - 0.46s and 0.34s - layering
nothing, from a planner that judged the library *"built for a different
kind of edit"*. Every capability it needed existed; the two facts that
would have informed the judgement were written nowhere it could read.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from library.tools import sfx_envelope as se  # noqa: E402

SFX = REPO / "library" / "steps" / "step_4_04_plan_sfx"


@pytest.fixture
def sfx_library(tmp_path):
    """A one-entry library under tmp_path - never the captain's."""
    library = tmp_path / "sfx"
    library.mkdir()
    sound = library / "test_riser.wav"
    sound.write_bytes(b"RIFF....WAVEfmt ")
    (library / "sfx_index.json").write_text(json.dumps([{
        "file": "test_riser.wav",
        "path": str(sound),
        "folder_category": "Action",
        "description": "a long ascending build",
        "technical": {
            "basic": {"duration": 5.0},
            "energy_profile": {"envelope_shape": "swelling"},
        },
        "transient_offset_sec": 4.8,
    }]))
    project = tmp_path / "proj"
    project.mkdir()
    return {"PIPELINE_SFX_LIBRARY": str(library),
            "_project_folder": str(project)}


def test_the_placement_code_and_the_prompt_read_the_same_table():
    """`_describe_placement` used to be a private dict in the post-bridge,
    so the sentence the manifest recorded and the sentence the planner
    needed could never have been checked against each other."""
    from library.steps.step_4_04_plan_sfx.post_bridge import (
        _describe_placement,
    )
    for envelope in se.ENVELOPES:
        assert _describe_placement(envelope.shape) == envelope.engine_does
        assert envelope.engine_does in se.envelope_legend()[envelope.shape]
    # An unmeasured shape is an absence, not a fifth row.
    assert _describe_placement("") == se.UNMEASURED_PLACEMENT
    assert se.UNMEASURED not in se.ENVELOPES_BY_SHAPE


def test_the_library_shape_counts_what_is_there():
    shape = se.library_shape([
        {"sfx_id": "a", "envelope": "swelling", "duration_seconds": 5.0},
        {"sfx_id": "b", "envelope": "swelling", "duration_seconds": 0.3},
        {"sfx_id": "c", "envelope": "punchy", "duration_seconds": 0.5},
        # No measured length: counted in its envelope, in no length range.
        {"sfx_id": "d", "envelope": "swelling"},
        # Unprofiled: `unmeasured`, never folded into a real shape.
        {"sfx_id": "e", "envelope": "", "duration_seconds": 1.0},
    ])
    assert shape["total_sounds"] == 5
    swelling = shape["by_envelope"]["swelling"]
    assert swelling["sounds"] == 2 and swelling["at_least_2s"] == 1
    assert swelling["shortest_seconds"] == 0.3
    assert swelling["longest_seconds"] == 5.0
    assert "1 sound(s) carry no measured length" in shape["length_unmeasured"]
    assert se.UNMEASURED in shape["by_envelope"]


def test_nothing_here_recommends_or_ranks():
    """It describes a mechanism and counts a library. Whether a moment
    earns a sound is the sound editor's (AGENTS.md 10.5)."""
    text = json.dumps(se.envelope_legend()).lower() + json.dumps(
        se.library_shape([{"sfx_id": "a", "envelope": "swelling",
                           "duration_seconds": 5.0}])).lower()
    # Affirmative forms only: "it recommends nothing" is the disclaimer.
    for word in ("you should", "prefer ", "is best", "recommended",
                 "at least one", "must use", "ideal for"):
        assert word not in text, f"the envelope material states {word!r}"


def test_the_bridge_emits_both_and_the_manifest_declares_them(sfx_library):
    """Built, then projected away, with the prompt still describing it -
    the regression `context_fields` exists for."""
    manifest = json.loads((SFX / "manifest.json").read_text())
    declared = {o["name"] for o in manifest["interface"]["outputs"]}
    assert {"sfx_envelope_legend", "sfx_library_shape"} <= declared
    assert {"sfx_envelope_legend", "sfx_library_shape"} <= set(
        manifest["context_fields"])

    env = dict(os.environ)
    env["PYTHONPATH"] = str(REPO) + os.pathsep + env.get("PYTHONPATH", "")
    env.update(sfx_library)
    proc = subprocess.run(
        [sys.executable, str(SFX / "bridge.py")],
        input=json.dumps({"project_folder": sfx_library["_project_folder"],
                          "timed_spine": {"structure": []}}),
        capture_output=True, text=True, encoding="utf-8", cwd=str(REPO),
        env={k: v for k, v in env.items() if not k.startswith("_")},
        check=False)
    assert proc.returncode == 0, proc.stderr
    out = json.loads(proc.stdout)
    assert "swelling" in out["sfx_envelope_legend"]
    assert out["sfx_library_shape"]["total_sounds"] == 1
