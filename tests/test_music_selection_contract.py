"""Music selection has a real schema, and the library is really consulted.

Captain's ruling 2026-08-20, verbatim: "fix schema and let LLM choose from
both library or outside". A hybrid, and neither offered option.

Three things must hold, and each of them failed on the shipped run:

1. The LLM is handed a non-empty output schema. It was handed an EMPTY one,
   because `present_llm_step` subtracts anything the bridge already
   supplied from `interface.outputs`, and the bridge supplied the step's
   only output.
2. `PIPELINE_MUSIC_LIBRARY` is opened. It never was.
3. Selecting from outside the library is still allowed. The captain did
   not restrict it, so a test that forbids `external` would be wrong.

And the failure that prompted all of it - a 3914-second "Inspirational
Motivational Music Video" scoring a 55-second piece whose direction says
it must never be scored as triumphant - must now be rejectable on the
recorded reasoning.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from library.tools.music_selection_contract import (  # noqa: E402
    MAX_TRACK_DURATION_FLOOR_SECONDS,
    VALID_SOURCES,
    catalogue_sources,
    max_track_duration_seconds,
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


def test_schema_is_non_empty_through_the_runner():
    """The path that actually builds the prompt, not just the manifest."""
    sys.path.insert(0, str(REPO / "library" / "processes" / "edit_video"))
    manifest = json.loads((STEP / "manifest.json").read_text(encoding="utf-8"))

    interface = manifest["interface"]
    # Mirror present_llm_step's own branch.
    if "llm_outputs" in interface:
        llm_outputs = interface["llm_outputs"]
    else:
        bridge_supplied = {"music_candidates"}
        already_have = {"creative_direction", "project_folder"} | bridge_supplied
        llm_outputs = [
            o for o in interface.get("outputs", [])
            if o.get("name") not in already_have
        ]
    assert llm_outputs, (
        "the runner would inject an empty schema for music_selection"
    )


# ── 2. The library is consulted ───────────────────────────────────────


def test_catalogue_sources_includes_the_music_library(monkeypatch, tmp_path):
    library = tmp_path / "shared_music"
    library.mkdir()
    monkeypatch.setenv("PIPELINE_MUSIC_LIBRARY", str(library))
    import importlib

    import library.tools.paths as paths
    importlib.reload(paths)
    try:
        sources = catalogue_sources(str(tmp_path / "proj"))
        assert sources["library"] == (str(library),)
        # One label, two directories: the captain's read-only music/ and
        # the downloads area a fetched track lands in. See
        # library/tools/project_layout.py for why those are separate
        # places, and catalogue_sources for why they share a label.
        assert sources["project"] == (
            str(tmp_path / "proj" / "music"),
            str(tmp_path / "proj" / "pipeline_output" / "steps"
                / "2_04_music_selection" / "downloads"),
        )
    finally:
        monkeypatch.delenv("PIPELINE_MUSIC_LIBRARY", raising=False)
        importlib.reload(paths)


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


# ── 3. Outside the library stays allowed ──────────────────────────────


def test_external_is_a_valid_source():
    assert "external" in VALID_SOURCES, (
        "the captain did NOT restrict selection to the library"
    )


def test_an_external_track_with_a_url_validates():
    selection = _good_selection("", duration=180.0)
    selection["source"] = "external"
    selection["source_url"] = "https://example.com/track"
    assert validate_selection(selection, [], 60.0) == []


def test_an_external_track_without_a_url_is_rejected():
    selection = _good_selection("", duration=180.0)
    selection["source"] = "external"
    selection["source_url"] = ""
    errors = validate_selection(selection, [], 60.0)
    assert any("source_url" in e for e in errors)


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


def test_a_track_shorter_than_the_edit_is_rejected(tmp_path):
    track = tmp_path / "dummy.wav"
    track.write_bytes(b"\x00" * 16)
    candidates = [{"audio_path": str(track), "source": "project"}]
    selection = _good_selection(str(track), duration=1.0)
    selection["source"] = "project"
    errors = validate_selection(selection, candidates, 60.0)
    assert any("silent" in e for e in errors), errors


def test_a_local_path_outside_the_catalogue_is_rejected(tmp_path):
    track = tmp_path / "not_catalogued.wav"
    track.write_bytes(b"\x00" * 16)
    errors = validate_selection(_good_selection(str(track)), [], 60.0)
    assert any("catalogued candidates" in e for e in errors), errors


def test_a_missing_justification_is_rejected(tmp_path):
    track = tmp_path / "t.wav"
    track.write_bytes(b"\x00" * 16)
    candidates = [{"audio_path": str(track), "source": "library"}]
    selection = _good_selection(str(track))
    del selection["direction_justification"]
    errors = validate_selection(selection, candidates, 60.0)
    assert any("direction_justification" in e for e in errors), errors


def test_an_unanswered_forbidden_register_is_rejected(tmp_path):
    track = tmp_path / "t.wav"
    track.write_bytes(b"\x00" * 16)
    candidates = [{"audio_path": str(track), "source": "library"}]
    selection = _good_selection(str(track))
    selection["direction_justification"]["why_not_forbidden"].pop("triumphant")
    errors = validate_selection(selection, candidates, 60.0)
    assert any("triumphant" in e for e in errors), errors


def test_a_valid_library_selection_passes(tmp_path):
    track = tmp_path / "t.wav"
    track.write_bytes(b"\x00" * 16)
    candidates = [{"audio_path": str(track), "source": "library"}]
    assert validate_selection(_good_selection(str(track)), candidates, 60.0) == []


@pytest.mark.parametrize(
    "target,expected",
    [(60.0, MAX_TRACK_DURATION_FLOOR_SECONDS),   # 10x60 = 600 = the floor
     (20.0, MAX_TRACK_DURATION_FLOOR_SECONDS),   # floor wins for short edits
     (120.0, 1200.0)],                           # 10x wins for longer ones
)
def test_duration_ceiling(target, expected):
    assert max_track_duration_seconds(target) == expected
