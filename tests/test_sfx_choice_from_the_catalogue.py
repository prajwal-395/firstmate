"""The sound is chosen by the model, out of the library's own catalogue.

`library/tools/sfx_library.py` used to hold `TYPE_KEYWORDS`: eight
hand-written word lists, counted as substring hits over each entry's
description, folder and filename, highest count wins, ties to whichever
entry came first.  That was the chooser.  These tests hold the shape that
replaced it:

  * the catalogue carries what the library RECORDS about each sound, out
    of all three of its index files;
  * a sound that is not on disk is not in the catalogue;
  * an id the catalogue does not contain fails AT PLAN TIME, in step
    4.04, naming the id;
  * no word list, and none of the three undeliverable type names, is
    reachable from anything the planner can emit;
  * the transition plan reaches the step whose first named purpose is
    pairing sounds with transitions.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SFX_STEP = REPO / "library" / "steps" / "step_4_04_plan_sfx"

from library.tools import sfx_library  # noqa: E402
from library.tools.sfx_library import (  # noqa: E402
    CATALOG_COLUMNS,
    catalog_document,
    catalog_rows,
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


def test_a_sound_that_is_not_on_disk_is_not_offered(tmp_path):
    """The guarantee `sfx_library` exists for, at its earliest point.

    `gone.wav` is in the index with a description a word list would have
    matched. It is not in the catalogue, so the model cannot name it.
    """
    catalog = load_sfx_catalog(str(_library(tmp_path)))
    assert [e["sfx_id"] for e in catalog] == ["present.wav", "builder.wav"]
    assert resolve_sfx_id("gone.wav", catalog) is None


def test_an_id_resolves_exactly_or_not_at_all(tmp_path):
    catalog = load_sfx_catalog(str(_library(tmp_path)))
    assert resolve_sfx_id("present.wav", catalog)["path"].endswith("present.wav")
    for near_miss in ("present", "Present.wav", "present.mp3", "", None):
        assert resolve_sfx_id(near_miss, catalog) is None, (
            f"{near_miss!r} resolved - a nearest match is a chooser")


def test_the_catalogue_keeps_its_declared_column_order(tmp_path):
    catalog = load_sfx_catalog(str(_library(tmp_path)))
    assert list(catalog_rows(catalog)[0]) == list(CATALOG_COLUMNS)


def test_every_sound_gets_its_own_section_titled_with_its_id(tmp_path):
    """The heading IS the string an answer has to name.

    That is what lets `brief_reference`'s map name all 78 of the
    captain's sounds without the body being read.
    """
    catalog = load_sfx_catalog(str(_library(tmp_path)))
    document = catalog_document(catalog)
    for entry in catalog:
        assert f"\n## {entry['sfx_id']}\n" in document, entry["sfx_id"]
    assert document.count("\n## ") == len(catalog)


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


def test_an_empty_catalogue_says_so_rather_than_pretending(tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    assert load_sfx_catalog(str(empty)) == []
    document = catalog_document([])
    assert "0 playable sounds" in document
    assert "\n## " not in document


# ── Nothing maps a word to a sound any more ───────────────────────────

def test_the_keyword_chooser_is_gone(tmp_path):
    """Deleted, not unwired, and no second word list took its place."""
    for name in ("TYPE_KEYWORDS", "match_sfx_file", "available_sfx_types"):
        assert not hasattr(sfx_library, name), (
            f"{name} is back in library/tools/sfx_library.py. Which sound "
            f"plays is the model's decision, made from the catalogue.")


# `foley` and `ambient` are in neither the output schema nor the library;
# `reverse_cymbal` is in the library's index of nothing at all. All three
# are still named in step_4_04_plan_sfx/handoff.md's toolkit table, which
# is under a captain freeze - that file is the one place they survive,
# and after this change they name nothing the model can emit: the schema
# asks for an `sfx_id` out of `sfx_catalog_reference`.
UNDELIVERABLE_TYPES = ("foley", "ambient", "reverse_cymbal")
FROZEN_PROMPT = SFX_STEP / "handoff.md"


def test_the_undeliverable_types_are_gone_from_everything_but_the_prompt():
    reachable = [
        SFX_STEP / "manifest.json",
        SFX_STEP / "bridge.py",
        SFX_STEP / "post_bridge.py",
        REPO / "library" / "tools" / "sfx_library.py",
    ]
    for path in reachable:
        text = path.read_text(encoding="utf-8")
        for name in UNDELIVERABLE_TYPES:
            if path.suffix == ".py" and name in _docstrings(text):
                continue  # recorded history, not a reachable value
            assert name not in text, (
                f"{name!r} is still in {path.relative_to(REPO)} - it names "
                f"a sound the library cannot play")

    # The prompt is the captain's, and it still lists them. Recorded here
    # so nobody reads the assertion above as covering the whole step.
    assert any(name in FROZEN_PROMPT.read_text(encoding="utf-8")
               for name in UNDELIVERABLE_TYPES)


def _docstrings(source: str) -> str:
    import ast
    out = []
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef,
                             ast.ClassDef)):
            doc = ast.get_docstring(node)
            if doc:
                out.append(doc)
    return "\n".join(out)


def test_the_schema_asks_for_a_library_id_not_a_type():
    manifest = json.loads((SFX_STEP / "manifest.json").read_text(
        encoding="utf-8"))
    described = manifest["interface"]["llm_outputs"][0]["description"]
    assert "sfx_id" in described
    assert "sfx_catalog_reference" in described
    assert "sfx_type" not in described


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
         "volume_level": "subtle", "rationale": "punctuates the decision"},
        {"spine_block_position": 2, "sfx_id": "reverse_cymbal",
         "volume_level": "low", "rationale": "marks the section"},
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
         "volume_level": "subtle", "rationale": "punctuates the decision"},
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
    assert placed["placement_method"].startswith("onset-snap")
    assert placed["source_in"] == 0.05
    assert placed["duration_seconds"] == pytest.approx(0.35)
    assert placed["timeline_out"] - placed["timeline_in"] == pytest.approx(0.35)


def test_a_sound_that_builds_plays_from_its_own_beginning(tmp_path):
    """A `swelling` sound is END-aligned, so its head is not trimmed.

    Trimming to the transient would throw away the build that the
    placement is pointing at an energy peak, and leave 0.3s of a
    2.0s sound.
    """
    library = _library(tmp_path)
    proc = _run_post_bridge(_payload([
        {"spine_block_position": 1, "sfx_id": "builder.wav",
         "volume_level": "low", "rationale": "resolves at the cut"},
    ]), library)

    assert proc.returncode == 0, proc.stdout + proc.stderr
    placed, = json.loads(proc.stdout)["sfx_spec"]["sfx_list"]
    assert placed["sfx_envelope"] == "swelling"
    assert placed["placement_method"].startswith("end-at-energy-peak")
    assert placed["source_in"] == 0.0
    assert placed["duration_seconds"] == pytest.approx(2.0)


# ── The transition plan reaches the step that pairs sounds with cuts ──
#
# The edge and the `transitions_toon` table landed in #294, on its own
# branch, while this one was in flight. This holds it in place from the
# sound side: a sound cannot be paired with a transition the step cannot
# see, and the id the table is keyed by has to be the one `sfx_creative`
# names.

def test_the_transition_plan_is_routed_to_plan_sfx():
    dag = json.loads((REPO / "library" / "processes" / "edit_video"
                      / "dag.json").read_text(encoding="utf-8"))
    edge = [e for e in dag["edges"]
            if e["from"] == "plan_transitions" and e["to"] == "plan_sfx"]
    assert len(edge) == 1, (
        "plan_sfx's handoff names pairing sounds with transitions as its "
        "first purpose; it needs that edge exactly once")
    assert edge[0]["data_mapping"] == {"transition_spec": "transition_spec"}

    manifest = json.loads((SFX_STEP / "manifest.json").read_text(
        encoding="utf-8"))
    declared = {i["name"]: i for i in manifest["interface"]["inputs"]}
    assert "transition_spec" in declared

    # The pre-bridge reduces it to a table keyed by the spine block the
    # cut leads INTO - the same identifier `sfx_creative` names - so the
    # raw spec stays out of the prompt and the model needs no join.
    assert "transitions_toon" in manifest["context_fields"]
    assert "transition_spec" not in manifest["context_fields"]
    assert any(o["name"] == "transitions_toon"
               for o in manifest["interface"]["outputs"])


def test_a_sound_and_a_transition_are_named_by_the_same_identifier():
    """`spine_block_position` in both, or the pairing needs a join."""
    manifest = json.loads((SFX_STEP / "manifest.json").read_text(
        encoding="utf-8"))
    answer = manifest["interface"]["llm_outputs"][0]["description"]
    assert "spine_block_position" in answer

    bridge = (SFX_STEP / "bridge.py").read_text(encoding="utf-8")
    assert '"spine_block_position", "transition_type"' in bridge
