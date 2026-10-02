"""The sound is chosen by the model, out of the library's own catalogue.

The catalogue carries what the library records about each on-disk sound
(all three index files); an id resolves exactly or not at all, and a plan
naming a sound the library has not got fails at plan time, in step 4.04.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
SFX_STEP = REPO / "library" / "steps" / "step_4_04_plan_sfx"

from library.tools.sfx_envelope import placement_of  # noqa: E402
from library.tools.sfx_library import (  # noqa: E402
    catalog_document,
    load_sfx_catalog,
    resolve_sfx_id,
)


# ── A library on tmp_path.  Never the captain's, never the shared one. ──

def _library(tmp_path, with_audio=True):
    lib = tmp_path / "sfx"
    lib.mkdir()
    (lib / "profiles").mkdir()

    present = lib / "present.wav"
    builder = lib / "builder.wav"
    if with_audio:
        present.write_bytes(b"RIFF....WAVEfmt ")
        builder.write_bytes(b"RIFF....WAVEfmt ")
    absent = lib / "gone.wav"

    (lib / "sfx_index.json").write_text(json.dumps([
        {
            "file": "present.wav",
            "path": str(present),
            "folder_category": "Accents",
            # EMPTY, the way 48 of the captain's 78 entries are. The
            # keyword matcher read this field and nothing else.
            "description": "",
            "technical": {
                "basic": {"duration": 0.4},
                "energy_profile": {"envelope_shape": "punchy"},
            },
            "transient_offset_sec": 0.05,
        },
        {
            "file": "builder.wav",
            "path": str(builder),
            "folder_category": "Risers",
            "description": "a build",
            "technical": {
                "basic": {"duration": 2.0},
                "energy_profile": {"envelope_shape": "swelling"},
            },
            # Its loudest moment is its climax, near the end.
            "transient_offset_sec": 1.7,
        },
        {
            "file": "gone.wav",
            "path": str(absent),
            "folder_category": "Accents",
            "description": "a sound whose file is not here",
            "technical": {"basic": {"duration": 1.0}},
        },
    ]), encoding="utf-8")

    (lib / "library_semantic.json").write_text(json.dumps([
        {
            "file": "present.wav",
            "description": "A dry, close snap, and it doesn't ring on.",
            "source_object": "a latch closing",
            "evokes": ["finality", "precision"],
            "emotional_temperature": "warm calm",
            "works_when": "Use this to punctuate a decision.",
            "avoid_when": "Avoid using this over speech.",
        },
    ]), encoding="utf-8")
    return lib


# ── What the model is offered ─────────────────────────────────────────

def test_the_catalogue_merges_all_three_index_files(tmp_path):
    """The semantic index describes what `sfx_index.json` left blank."""
    entry = load_sfx_catalog(str(_library(tmp_path)))[0]

    assert entry["sfx_id"] == "present.wav"
    assert entry["description"].startswith("A dry, close snap")
    assert entry["source_object"] == "a latch closing"
    assert entry["evokes"] == ["finality", "precision"]
    assert entry["works_when"] == "Use this to punctuate a decision."
    assert entry["avoid_when"] == "Avoid using this over speech."
    # and the measurements the index carries
    assert entry["duration_seconds"] == 0.4
    assert entry["envelope"] == "punchy"
    assert entry["transient_offset_sec"] == 0.05


def test_only_an_on_disk_sound_is_offered_and_an_id_resolves_exactly(
        tmp_path):
    """`gone.wav` is indexed but not on disk, so the model cannot name
    it; and a near miss never resolves - a nearest match is a chooser."""
    catalog = load_sfx_catalog(str(_library(tmp_path)))
    assert [e["sfx_id"] for e in catalog] == ["present.wav", "builder.wav"]
    assert resolve_sfx_id("present.wav", catalog)["path"].endswith(
        "present.wav")
    for near_miss in ("gone.wav", "present", "Present.wav", "present.mp3",
                      "", None):
        assert resolve_sfx_id(near_miss, catalog) is None, near_miss


def test_a_description_with_a_comma_and_an_apostrophe_needs_no_escaping(
        tmp_path):
    """Every field is its own line, so nothing is quoted at all.

    As a TOON table this had to be quoted with a backtick, because a
    tab-joined row would put "and it doesn't ring on" into the next
    column and the apostrophe would be doubled by any dialect quoting on
    `'` (AGENTS.md 10.1). A markdown line has no cell to break.
    """
    document = catalog_document(load_sfx_catalog(str(_library(tmp_path))))
    assert "doesn't" in document
    assert "doesn''t" not in document
    assert ("- description: A dry, close snap, and it doesn't ring on."
            in document)


# ── A plan naming an unplayable sound fails at PLAN time ──────────────

def _spine(n_blocks):
    return {"structure": [
        {"position": i + 1, "block_type": "speech", "clip_id": "clip_001",
         "source_start": i * 2.0, "source_end": i * 2.0 + 2.0,
         "timeline_start": i * 2.0, "timeline_end": i * 2.0 + 2.0,
         "word_timestamps": [], "alignment_method": "whisperx"}
        for i in range(n_blocks)]}


def _run_post_bridge(payload, library):
    env = dict(os.environ)
    env["PYTHONPATH"] = str(REPO) + os.pathsep + env.get("PYTHONPATH", "")
    env["PIPELINE_SFX_LIBRARY"] = str(library)
    return subprocess.run(
        [sys.executable, str(SFX_STEP / "post_bridge.py")],
        input=json.dumps(payload), capture_output=True, text=True,
        encoding="utf-8", cwd=str(REPO), env=env)


def _payload(entries):
    return {
        "sfx_creative": entries,
        "timed_spine": _spine(2),
        "temporal_event_indices": [],
        "music_analysis": {},
        "music_selection": {"audio_path": ""},
        "project_fps": 30.0,
        "creative_direction": {},
    }


def test_a_plan_naming_a_sound_the_library_has_not_got_fails_here(tmp_path):
    library = _library(tmp_path)
    proc = _run_post_bridge(_payload([
        {"spine_block_position": 1, "sfx_id": "present.wav",
         "volume_db": -18, "rationale": "punctuates the decision"},
        {"spine_block_position": 2, "sfx_id": "reverse_cymbal",
         "volume_db": -14, "rationale": "marks the section"},
    ]), library)

    assert proc.returncode == 1, proc.stdout + proc.stderr
    error = json.loads(proc.stdout)["error"]
    assert "reverse_cymbal" in error
    assert "1 of 2" in error
    # The good entry is not placed either: the plan is refused whole, so
    # nothing ships an edit that is quietly missing a planned sound.
    assert "sfx_spec" not in proc.stdout


def test_a_plan_naming_real_sounds_resolves_to_real_files(tmp_path):
    library = _library(tmp_path)
    proc = _run_post_bridge(_payload([
        {"spine_block_position": 1, "sfx_id": "present.wav",
         "volume_db": -18, "rationale": "punctuates the decision"},
    ]), library)

    assert proc.returncode == 0, proc.stdout + proc.stderr
    placed, = json.loads(proc.stdout)["sfx_spec"]["sfx_list"]
    assert placed["sfx_id"] == "present.wav"
    assert placed["source_file"] == str(library / "present.wav")
    assert os.path.exists(placed["source_file"])
    # Placement is keyed on the measured envelope. `punchy` means the
    # file's own transient lands on `timeline_in`, so playback starts
    # there and what plays is what is LEFT of the sound.
    assert placed["sfx_envelope"] == "punchy"
    assert placed["placement_method"] == placement_of("punchy")
    assert placed["source_in"] == 0.05
    assert placed["duration_seconds"] == pytest.approx(0.35)
    assert placed["timeline_out"] - placed["timeline_in"] == pytest.approx(0.35)
