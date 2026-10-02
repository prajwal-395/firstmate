"""Music selection has a real schema, and the library is really consulted.

The LLM gets a non-empty output schema, `PIPELINE_MUSIC_LIBRARY` is
catalogued, outside tracks stay allowed, and the shipped 3914-second
compilation is rejected on the recorded reasoning. History:
docs/evidence/music_tests.md#music-selection-contract.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO))

from library.tools.music_selection_contract import (  # noqa: E402
    validate_selection,
)

STEP = REPO / "library" / "steps" / "step_2_04_music_selection"


def _good_selection(audio_path: str, duration: float = 200.0) -> dict:
    return {
        "title": "Infected (Instrumental)",
        "source": "library",
        "audio_path": audio_path,
        "source_url": "",
        "duration_seconds": duration,
        "bpm": 90,
        "key": "F#m",
        "direction_justification": {
            "direction_mood": "honest and self-deprecating, quietly determined",
            "why_it_fits": "sparse and unresolved; it sits under the voice",
            "forbidden_registers": ["triumphant", "motivational"],
            "why_not_forbidden": {
                "triumphant": "no lift, no swell, no resolution",
                "motivational": "no drive, no build to a payoff",
            },
        },
        "candidates_evaluated": [],
        "splices": [],
    }


# ── 1. The schema is not empty ────────────────────────────────────────


def test_manifest_declares_llm_outputs():
    """Without `llm_outputs` the injected schema is empty.

    `interface.outputs` minus what the bridge supplies was the whole
    schema, and the bridge supplied `music_selection` - the only output.
    An explicit `llm_outputs` is what makes the ask real.
    """
    manifest = json.loads((STEP / "manifest.json").read_text(encoding="utf-8"))
    llm_outputs = manifest["interface"].get("llm_outputs")
    assert llm_outputs, "step 2.04 declares no llm_outputs - schema is empty again"
    names = {o["name"] for o in llm_outputs}
    assert "music_selection" in names

    description = next(
        o["description"] for o in llm_outputs if o["name"] == "music_selection"
    )
    for required in (
        "source",
        "audio_path",
        "source_url",
        "duration_seconds",
        "direction_justification",
        "forbidden_registers",
    ):
        assert required in description, (
            f"the schema never asks for {required!r}, so nothing constrains it"
        )


# ── 2. The library is consulted ───────────────────────────────────────


def test_bridge_lists_the_library_and_picks_nothing(tmp_path, monkeypatch):
    """The bridge must catalogue both sources and select none of them."""
    library = tmp_path / "shared_music"
    library.mkdir()
    (library / "house_track.mp3").write_bytes(b"\x00" * 32)

    project = tmp_path / "proj"
    (project / "music").mkdir(parents=True)
    (project / "music" / "local_track.wav").write_bytes(b"\x00" * 32)
    (project / "project.yaml").write_text(
        "target_duration_seconds: 45\n", encoding="utf-8")

    env = dict(os.environ)
    env["PIPELINE_MUSIC_LIBRARY"] = str(library)
    env["PYTHONPATH"] = str(REPO) + os.pathsep + env.get("PYTHONPATH", "")

    proc = subprocess.run(
        [sys.executable, str(STEP / "bridge.py")],
        input=json.dumps({"project_folder": str(project),
                          "creative_direction": {}}),
        capture_output=True, text=True, encoding="utf-8", env=env,
    )
    assert proc.returncode == 0, proc.stderr
    out = json.loads(proc.stdout)

    assert "music_selection" not in out, (
        "the bridge chose a track. It must catalogue and hand over, not "
        "select - sorted(...)[0] is what put a 65-minute compilation in the "
        "shipped edit."
    )
    catalogue = out["music_candidates"]
    assert catalogue["target_duration_seconds"] == 45.0, (
        "target duration must come off the project's own project.yaml"
    )
    by_source = {c["source"] for c in catalogue["candidates"]}
    assert by_source == {"library", "project"}, (
        f"the library was not consulted: sources seen were {by_source}"
    )
    searched = {s["source"]: s for s in catalogue["searched"]}
    assert searched["library"]["directory"] == str(library)


# ── 3. Outside the library stays allowed, and a valid pick passes ────


def test_a_valid_library_or_external_selection_passes(tmp_path):
    """The captain did NOT restrict selection to the library."""
    track = tmp_path / "t.wav"
    track.write_bytes(b"\x00" * 16)
    candidates = [{"audio_path": str(track), "source": "library"}]
    assert validate_selection(_good_selection(str(track)), candidates,
                              60.0) == []

    external = _good_selection("", duration=180.0)
    external["source"] = "external"
    external["source_url"] = "https://example.com/track"
    assert validate_selection(external, [], 60.0) == []


# ── What the schema must make impossible ──────────────────────────────


def test_the_shipped_failure_is_now_rejected(tmp_path):
    """3914 seconds of "Inspirational Motivational" for a 60s edit.

    Two independent reasons, so removing either one still catches it.
    """
    track = tmp_path / "Inspirational Motivational Music Video.wav"
    track.write_bytes(b"\x00" * 16)
    candidates = [{
        "audio_path": str(track),
        "title": track.stem,
        "source": "project",
        "duration_seconds": 3914.652,
    }]
    selection = {
        "title": "Inspirational Motivational Music Video _ Work Background Music",
        "source": "project",
        "audio_path": str(track),
        "duration_seconds": 3914.652,
        "direction_justification": {
            "direction_mood": "honest and self-deprecating, quietly determined",
            "why_it_fits": "it is uplifting",
            "forbidden_registers": ["triumphant", "motivational"],
            "why_not_forbidden": {
                "triumphant": "it is only mildly so",
                "motivational": "it is background music",
            },
        },
    }
    errors = validate_selection(selection, candidates, 60.0)
    joined = " ".join(errors)
    assert "compilation" in joined, f"duration sanity did not fire: {errors}"
    assert "forbidden_registers" in joined, (
        f"the title/forbidden-register clash did not fire: {errors}"
    )


def test_each_unacceptable_selection_names_its_reason(tmp_path):
    track = tmp_path / "t.wav"
    track.write_bytes(b"\x00" * 16)
    uncatalogued = tmp_path / "not_catalogued.wav"
    uncatalogued.write_bytes(b"\x00" * 16)
    library = [{"audio_path": str(track), "source": "library"}]
    project = [{"audio_path": str(track), "source": "project"}]

    too_short = dict(_good_selection(str(track), duration=1.0),
                     source="project")
    no_url = dict(_good_selection("", duration=180.0),
                  source="external", source_url="")
    no_justification = _good_selection(str(track))
    del no_justification["direction_justification"]
    unanswered = _good_selection(str(track))
    unanswered["direction_justification"]["why_not_forbidden"].pop(
        "triumphant")

    rows = [
        (too_short, project, "silent"),
        (_good_selection(str(uncatalogued)), [], "catalogued candidates"),
        (no_url, [], "source_url"),
        (no_justification, library, "direction_justification"),
        (unanswered, library, "triumphant"),
    ]
    for selection, candidates, reason in rows:
        errors = validate_selection(selection, candidates, 60.0)
        assert any(reason in e for e in errors), (reason, errors)
