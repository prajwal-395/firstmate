"""Step 0.01 must validate the library the pipeline actually reads.

It used to assert a layout no reader wants: `library_analysis.json` and
`library_semantic.json` inside `profiles/`. The only reader,
`library.tools.sfx_library.load_sfx_index`, reads `<library>/sfx_index.json`
first and lists those two filenames in `_NON_ENTRY_FILES` - it SKIPS them.

The consequence was backwards in both directions. The shipped library at
PIPELINE_SFX_LIBRARY - 78 entries, every file on disk - failed the gate.
A project-local `mock_sfx/` holding profiles and no audio at all passed it,
so every run under it placed SFX that could never make a sound and the gate
said the library was fine.
"""

import json
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
STEP = REPO_ROOT / "library" / "steps" / "step_0_01_validate_sfx_library" / "step.py"


def _run(payload: dict):
    proc = subprocess.run(
        [sys.executable, str(STEP)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    body = json.loads(proc.stdout)["sfx_library_status"]
    return proc.returncode, body


def _library(tmp_path: Path, *, with_audio: bool) -> Path:
    lib = tmp_path / "sfx library"
    (lib / "Accents").mkdir(parents=True)
    wav = lib / "Accents" / "whoosh_1.wav"
    if with_audio:
        wav.write_bytes(b"RIFF....WAVEfmt ")
    (lib / "sfx_index.json").write_text(json.dumps([
        {
            "file": "whoosh_1.wav",
            "path": str(wav),
            "folder_category": "Accents",
            "description": "a whoosh transition swish",
            "technical": {"basic": {"duration": 0.6}},
        }
    ]))
    return lib


def test_a_library_whose_files_exist_is_valid(tmp_path):
    code, body = _run({"sfx_library": str(_library(tmp_path, with_audio=True))})
    assert code == 0, body
    assert body["valid"] is True
    assert body["playable_entries"] == 1
    assert "whoosh" in body["available_types"]


def test_profiles_without_audio_fail(tmp_path):
    """The mock_sfx shape: an index, and not one file behind it."""
    code, body = _run({"sfx_library": str(_library(tmp_path, with_audio=False))})
    assert code == 1
    assert body["valid"] is False
    assert body["playable_entries"] == 0
    assert "silent" in body["error"]


def test_missing_index_files_do_not_fail_a_usable_library(tmp_path):
    """The regression proper.

    `library_analysis.json` / `library_semantic.json` are metadata the
    reader skips. Their absence must not fail a library whose entries load
    and whose files are on disk.
    """
    lib = _library(tmp_path, with_audio=True)
    assert not (lib / "profiles").exists()
    code, body = _run({"sfx_library": str(lib)})
    assert code == 0, body
    assert body["valid"] is True


def test_no_path_supplied_is_a_failure_with_a_fix(tmp_path):
    code, body = _run({})
    assert code == 1
    assert "PIPELINE_SFX_LIBRARY" in body["fix"]
