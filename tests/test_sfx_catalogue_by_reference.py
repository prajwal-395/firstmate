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

    unchanged, keys = restore_for_harness(inputs, "agent")
    assert keys == []
    assert unchanged is inputs


