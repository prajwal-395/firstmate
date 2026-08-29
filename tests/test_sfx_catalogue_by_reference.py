"""The SFX catalogue reaches the prompt as a REFERENCE, not as a copy.

`#298` put the whole library in step 4.04's prompt, which was right - a
model that cannot see `avoid_when` cannot decline a sound the library
says to decline.  But as one TOON table it measured **44,397 B on the
captain's 78-entry library**, and once `#295` stopped copying the
creative brief that was **44.1% of this step's entire context** and 6.5%
of every byte the pipeline's twelve contexts send.  `#299` named it.

The mechanism already existed.  These tests hold that it was APPLIED and
not forked:

  * the bridge writes the catalogue into the step's OWN directory and
    puts `brief_reference`'s map in the prompt;
  * the map is a fraction of the document, measured in bytes;
  * **every sound is still reachable** - the map names each by its exact
    `sfx_id`, and following the path and the line range returns prose
    that was NOT in the prompt;
  * a harness that cannot follow a path gets the document whole, which
    is clause 5 of the same rule.

This test FOLLOWS the reference rather than asserting its shape - it
parses the path and the range out of the string the model reads, the way
`tests/test_brief_reference.py` does.
"""
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SFX_STEP = REPO / "library" / "steps" / "step_4_04_plan_sfx"

from library.tools.brief_reference import (  # noqa: E402
    REFERENCED_INPUTS,
    reference_path,
    restore_for_harness,
)
from library.tools.sfx_library import (  # noqa: E402
    CATALOG_DOCUMENT_NAME,
    catalog_document,
    load_sfx_catalog,
)

# Enough sounds that the difference between a map and a copy is real,
# and each carrying the prose that made #298 worth doing.
SOUNDS = [
    ("whoosh_impact.mp3", "Whoosh", 8.04, "punchy", "cold tense"),
    ("riser_2.mp3", "Risers", 5.317, "swelling", "cold tense"),
    ("camera soft click.wav", "Camera Shutter", 0.46, "fading",
     "neutral calm"),
    ("Alien_racecar.wav", "Risers", 5.69, "fading", "cold tense"),
    ("Paper_crinkle_04.wav", "not really sure how to use these", 2.62,
     "swelling", "neutral calm"),
]


@pytest.fixture
def library(tmp_path):
    lib = tmp_path / "sfx"
    lib.mkdir()
    index, semantic = [], []
    for name, category, duration, envelope, temperature in SOUNDS:
        audio = lib / name
        audio.write_bytes(b"RIFF....WAVEfmt ")
        index.append({
            "file": name,
            "path": str(audio),
            "folder_category": category,
            "technical": {
                "basic": {"duration": duration},
                "energy_profile": {"envelope_shape": envelope},
            },
            "transient_offset_sec": 0.1,
        })
        semantic.append({
            "file": name,
            "description": f"A {envelope} {category} sound, and it doesn't "
                           f"ring on. " + "Body prose. " * 12,
            "source_object": "a thing being moved",
            "evokes": ["speed", "weight"],
            "emotional_temperature": temperature,
            "works_when": "Use it on a hard cut. " + "Because. " * 10,
            "avoid_when": "Avoid it under a quiet line. " + "Because. " * 10,
        })
    (lib / "sfx_index.json").write_text(json.dumps(index), encoding="utf-8")
    (lib / "library_semantic.json").write_text(json.dumps(semantic),
                                               encoding="utf-8")
    return lib


@pytest.fixture
def project(tmp_path):
    folder = tmp_path / "project"
    folder.mkdir()
    return folder


def _run_bridge(library, project):
    env = dict(os.environ)
    env["PYTHONPATH"] = str(REPO) + os.pathsep + env.get("PYTHONPATH", "")
    env["PIPELINE_SFX_LIBRARY"] = str(library)
    proc = subprocess.run(
        [sys.executable, str(SFX_STEP / "bridge.py")],
        input=json.dumps({
            "project_folder": str(project),
            "timed_spine": {"structure": []},
            "temporal_event_indices": [],
            "transition_spec": [],
            "project_fps": 30.0,
        }),
        capture_output=True, text=True, encoding="utf-8",
        cwd=str(REPO), env=env,
    )
    return proc


def _document_path(project):
    return (project / "pipeline_output" / "steps" / "4_04_plan_sfx"
            / CATALOG_DOCUMENT_NAME)


# ── The bridge writes the document and points at it ───────────────────

def test_the_bridge_writes_the_catalogue_into_its_own_step_directory(
        library, project):
    proc = _run_bridge(library, project)
    assert proc.returncode == 0, proc.stdout + proc.stderr

    path = _document_path(project)
    assert path.exists(), sorted(project.rglob("*"))

    reference = json.loads(proc.stdout)["sfx_catalog_reference"]
    assert reference_path(reference) == str(path.resolve())


def test_the_bridge_refuses_when_there_is_nowhere_to_write_it(library):
    """No project folder means no path to point at, and no quiet copy."""
    env = dict(os.environ)
    env["PYTHONPATH"] = str(REPO) + os.pathsep + env.get("PYTHONPATH", "")
    env["PIPELINE_SFX_LIBRARY"] = str(library)
    proc = subprocess.run(
        [sys.executable, str(SFX_STEP / "bridge.py")],
        input=json.dumps({"timed_spine": {"structure": []}}),
        capture_output=True, text=True, encoding="utf-8",
        cwd=str(REPO), env=env,
    )
    assert proc.returncode == 1
    assert "project_folder" in json.loads(proc.stdout)["error"]


def test_the_map_entry_is_a_fraction_of_the_section_it_stands_for(
        library, project):
    """The property that scales: what a sound costs in the PROMPT.

    The header is a fixed cost paid once, so the saving is per sound and
    has to be measured per sound. On the captain's own 78-entry library
    the map is 12,960 B against 44,575 B copied - a 31,224 B
    reduction, 30.9% of that step's whole context (measured
    2026-08-29).
    """
    proc = _run_bridge(library, project)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    reference = json.loads(proc.stdout)["sfx_catalog_reference"]
    lines = _document_path(project).read_text(encoding="utf-8").split("\n")

    for name, span in _ranges(reference).items():
        first, last = (int(n) for n in span.split("-"))
        section = len("\n".join(lines[first - 1:last]).encode("utf-8"))
        entry = sum(len(line.encode("utf-8")) + 1
                    for line in _map_entry(reference, name))
        assert entry < section / 3, (
            f"{name} costs {entry} B in the map against {section} B in "
            f"the document - that is not a saving worth the indirection")

    assert (len(reference.encode("utf-8"))
            < len("\n".join(lines).encode("utf-8")))


def _ranges(reference: str) -> dict:
    return dict(re.findall(r"^## (.+?)  \[[\d,]+ B, lines (\d+-\d+)\]$",
                           reference, flags=re.M))


def _map_entry(reference: str, name: str) -> list:
    """The map's lines for one sound: its heading and its lede."""
    lines = reference.split("\n")
    start = next(i for i, line in enumerate(lines)
                 if line.startswith(f"## {name}  ["))
    end = start + 1
    while end < len(lines) and lines[end].strip():
        end += 1
    return lines[start:end]


# ── Every sound is still reachable ────────────────────────────────────

def test_the_map_names_every_sound_by_the_id_an_answer_must_use(
        library, project):
    """A reference that narrows the menu has made the problem worse."""
    proc = _run_bridge(library, project)
    reference = json.loads(proc.stdout)["sfx_catalog_reference"]

    catalog = load_sfx_catalog(str(library))
    assert len(catalog) == len(SOUNDS)
    for entry in catalog:
        assert entry["sfx_id"] in reference, (
            f"{entry['sfx_id']} is not in the map, so the model cannot "
            f"name it")


def test_the_map_carries_what_was_measured_about_every_sound(
        library, project):
    """Category, length, envelope and temperature, without a read."""
    proc = _run_bridge(library, project)
    reference = json.loads(proc.stdout)["sfx_catalog_reference"]
    for name, category, duration, envelope, temperature in SOUNDS:
        line = next(l for l in reference.splitlines()
                    if l.strip().startswith(f"category {category}")
                    and f"plays for {round(duration, 2)} s" in l)
        assert envelope in line and temperature in line


def test_following_the_range_returns_prose_the_prompt_did_not_carry(
        library, project):
    """The load-bearing affordance: a heading, a size and a LINE RANGE."""
    proc = _run_bridge(library, project)
    reference = json.loads(proc.stdout)["sfx_catalog_reference"]
    path = Path(reference_path(reference))
    lines = path.read_text(encoding="utf-8").split("\n")

    ranges = _ranges(reference)
    assert set(ranges) == {name for name, *_ in SOUNDS}

    for name, span in ranges.items():
        first, last = (int(n) for n in span.split("-"))
        section = "\n".join(lines[first - 1:last])
        assert section.startswith(f"## {name}")
        avoid = next(l for l in section.splitlines()
                     if l.startswith("- avoid when:"))
        assert avoid not in reference, (
            f"{name}'s avoid_when is in the prompt as well as behind the "
            f"reference, so the indirection bought nothing")


# ── Clause 5: a harness that cannot follow a path ─────────────────────

def test_a_harness_that_cannot_read_a_file_gets_the_whole_catalogue(
        library, project):
    proc = _run_bridge(library, project)
    inputs = {"sfx_catalog_reference":
              json.loads(proc.stdout)["sfx_catalog_reference"]}

    restored, keys = restore_for_harness(inputs, "api")
    assert keys == ["sfx_catalog_reference"]
    assert restored["sfx_catalog_reference"] == catalog_document(
        load_sfx_catalog(str(library)))

    unchanged, keys = restore_for_harness(inputs, "agy")
    assert keys == []
    assert unchanged is inputs


def test_the_catalogue_is_a_row_in_the_one_enumeration():
    """A second document costs a row, not a second mechanism."""
    assert "sfx_catalog_reference" in REFERENCED_INPUTS
    assert "creative_brief" in REFERENCED_INPUTS

    manifest = json.loads((SFX_STEP / "manifest.json").read_text(
        encoding="utf-8"))
    assert "sfx_catalog_reference" in manifest["context_fields"]
    assert "sfx_catalog_reference" in {
        o["name"] for o in manifest["interface"]["outputs"]}
