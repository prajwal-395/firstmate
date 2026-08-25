"""Prosody that measured nothing must not report success.

`praat-parselmouth` is step 1.05's only measuring instrument and its
manifest has listed it as a precondition since the step was written -
while `requirements.txt` never carried it. On project 001 the step
reported SUCCESS in 0.1 seconds having written seventeen files that each
said `{"prosody": {"method": null, "error": "parselmouth not
installed"}}`, and 4.2 KB of those identical error records were
serialised into the creative-direction prompt as if they were
measurements.

Two halves, and the second is the worse one:

1. the dependency is declared in `requirements.txt` now;
2. a step that cannot do its job reports `available: false`, which
   `run_pipeline.check_output_is_real` reads as a failed step - and it
   writes no profile file, because a profile file IS the cache, so an
   error record on disk made the failure permanent as well as silent.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

STEP = REPO / "library" / "steps" / "step_1_05_prosody_analysis"
PIPELINE = REPO / "library" / "tools" / "analysis" / "speech_advanced_pipeline.py"

def profile_defect(profile):
    """Imported lazily so this module still COLLECTS against a tree
    without the fix - a collection error is not a test failure, and the
    point of these tests is to fail on the old behaviour."""
    from library.steps.step_1_05_prosody_analysis.step import (
        profile_defect as _impl,
    )
    return _impl(profile)


# ── The dependency is declared ────────────────────────────────────────

def test_requirements_carries_parselmouth():
    """The manifest precondition and the requirements file must agree."""
    text = (REPO / "requirements.txt").read_text(encoding="utf-8")
    lines = [l.strip() for l in text.splitlines()
             if l.strip() and not l.strip().startswith("#")]
    assert any(l.startswith("praat-parselmouth") for l in lines), (
        "step 1.05's manifest lists parselmouth as a precondition; "
        f"requirements.txt must install it. Got: {lines}")


def test_the_manifest_still_declares_the_precondition():
    manifest = json.loads((STEP / "manifest.json").read_text(encoding="utf-8"))
    preconditions = " ".join(manifest["interface"]["preconditions"]).lower()
    assert "parselmouth" in preconditions


# ── What counts as a profile ──────────────────────────────────────────

class TestProfileDefect:
    GOOD = {
        "clip_id": "clip_001",
        "prosody": {
            "method": "parselmouth-praat",
            "pitch_stats": {"mean_f0_hz": 120.0},
            "intensity_contour_50ms": [{"time": 0.0, "db": 60.0}],
        },
    }

    def test_a_real_profile_is_accepted(self):
        assert profile_defect(self.GOOD) == ""

    def test_the_shipped_001_record_is_rejected(self):
        """The literal seventeen records from project 001."""
        defect = profile_defect(
            {"clip_id": "clip_001",
             "prosody": {"method": None,
                         "error": "parselmouth not installed"}})
        assert "parselmouth not installed" in defect

    def test_a_profile_with_no_method_is_rejected(self):
        assert profile_defect({"prosody": {"method": None}})

    def test_a_profile_that_measured_nothing_is_rejected(self):
        assert profile_defect(
            {"prosody": {"method": "parselmouth-praat", "pitch_stats": {},
                         "intensity_contour_50ms": []}})

    def test_a_file_with_no_prosody_block_is_rejected(self):
        assert profile_defect({"clip_id": "clip_001"})


# ── The analyser raises rather than writing an error record ───────────

def _audio(tmp_path: Path) -> Path:
    """A tiny valid WAV, written without any audio dependency."""
    import struct
    import wave
    path = tmp_path / "clip_001.wav"
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(b"".join(struct.pack("<h", 0) for _ in range(1600)))
    return path


parselmouth_installed = True
try:  # pragma: no cover - depends on the environment under test
    import parselmouth  # noqa: F401
except ImportError:
    parselmouth_installed = False


@pytest.mark.skipif(parselmouth_installed,
                    reason="this is the missing-dependency failure mode")
def test_the_analyser_writes_no_profile_when_it_cannot_measure(tmp_path):
    out = tmp_path / "prosody"
    out.mkdir()
    proc = subprocess.run(
        [sys.executable, str(PIPELINE)],
        input=json.dumps({
            "audio_files": [{"path": str(_audio(tmp_path)),
                             "clip_id": "clip_001"}],
            "output_dir": str(out),
        }),
        capture_output=True, text=True, encoding="utf-8", cwd=str(REPO),
    )
    assert proc.returncode != 0, "a run that measured nothing exited 0"
    assert list(out.glob("*_prosody.json")) == [], (
        "an error record was written as a profile - it caches, so the "
        "next run skips the clip as already analysed")
    failures = json.loads(proc.stdout)["failures"]
    assert len(failures) == 1
    assert "parselmouth" in failures[0]["error"]


# ── The step reports failure, and the runner sees it ──────────────────

def _run_step(tmp_path: Path):
    project = tmp_path / "project"
    project.mkdir()
    env = dict(os.environ)
    env["PYTHONPATH"] = str(REPO) + os.pathsep + env.get("PYTHONPATH", "")
    proc = subprocess.run(
        [sys.executable, str(STEP / "step.py")],
        input=json.dumps({
            "raw_footage_files": [{"path": str(_audio(tmp_path)),
                                   "clip_id": "clip_001"}],
            "project_folder": str(project),
            "temporal_index": {},
        }),
        capture_output=True, text=True, encoding="utf-8", cwd=str(REPO),
        env=env,
    )
    return proc, json.loads(proc.stdout)["prosody_analysis"]


@pytest.mark.skipif(parselmouth_installed,
                    reason="this is the missing-dependency failure mode")
def test_the_step_does_not_report_success_with_nothing_measured(tmp_path):
    _proc, out = _run_step(tmp_path)
    assert out["available"] is False
    assert out["profiles"] == {}
    assert "parselmouth" in out["error"], (
        f"the reason must reach the operator, not a generic note: "
        f"{out['error']}")
    assert out["unmeasured_clips"] == ["clip_001"]


@pytest.mark.skipif(parselmouth_installed,
                    reason="this is the missing-dependency failure mode")
def test_the_runner_treats_it_as_a_failed_step(tmp_path):
    """`available: false` is the loudness: check_output_is_real stops the
    run rather than carrying an error record into the next prompt."""
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "_run_pipeline_prosody_test",
        REPO / "library" / "processes" / "edit_video" / "run_pipeline.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    proc, _out = _run_step(tmp_path)
    problems = module.check_output_is_real(
        "prosody_analysis", json.loads(proc.stdout))
    assert problems, "the run would have continued over a dead signal"
    assert "available=false" in problems[0]


@pytest.mark.skipif(not parselmouth_installed,
                    reason="parselmouth is present, so measurement succeeds")
def test_the_step_succeeds_when_the_dependency_is_installed(tmp_path):
    """The other direction: an installed parselmouth must still pass."""
    _proc, out = _run_step(tmp_path)
    assert out["available"] is True
    assert set(out["profiles"]) == {"clip_001"}
    assert out["error"] is None


def test_a_stale_error_record_on_disk_is_rejected(tmp_path):
    """A profile left by an earlier broken run must not read as data.

    This one runs whether or not parselmouth is installed: the cache is
    read before anything is analysed.
    """
    project = tmp_path / "project"
    prosody_dir = project / "pipeline_output" / "prosody"
    prosody_dir.mkdir(parents=True)
    (prosody_dir / "clip_001_prosody.json").write_text(json.dumps({
        "clip_id": "clip_001",
        "prosody": {"method": None, "error": "parselmouth not installed"},
        "analysis_time_s": 0.0,
    }), encoding="utf-8")

    env = dict(os.environ)
    env["PYTHONPATH"] = str(REPO) + os.pathsep + env.get("PYTHONPATH", "")
    proc = subprocess.run(
        [sys.executable, str(STEP / "step.py")],
        input=json.dumps({
            "raw_footage_files": [{"path": str(_audio(tmp_path)),
                                   "clip_id": "clip_001"}],
            "project_folder": str(project),
            "temporal_index": {},
        }),
        capture_output=True, text=True, encoding="utf-8", cwd=str(REPO),
        env=env,
    )
    out = json.loads(proc.stdout)["prosody_analysis"]
    assert out["available"] is False
    assert out["profiles"] == {}
    assert "clip_001" in out["unmeasured_clips"]
