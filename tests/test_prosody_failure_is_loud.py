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
import re
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
    """The declared requirement and the requirements file must agree."""
    text = (REPO / "requirements.txt").read_text(encoding="utf-8")
    lines = [l.strip() for l in text.splitlines()
             if l.strip() and not l.strip().startswith("#")]
    assert any(l.startswith("praat-parselmouth") for l in lines), (
        "step 1.05 declares env.parselmouth as a requirement; "
        f"requirements.txt must install it. Got: {lines}")


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


    def test_the_shipped_001_record_is_rejected(self):
        """The literal seventeen records from project 001."""
        defect = profile_defect(
            {"clip_id": "clip_001",
             "prosody": {"method": None,
                         "error": "parselmouth not installed"}})
        assert "parselmouth not installed" in defect


    def test_a_profile_that_measured_nothing_is_rejected(self):
        assert profile_defect(
            {"prosody": {"method": "parselmouth-praat", "pitch_stats": {},
                         "intensity_contour_50ms": []}})


# ── The analyser raises rather than writing an error record ───────────

def _audio(tmp_path: Path, name: str = "clip_001.wav") -> Path:
    """A tiny valid WAV, written without any audio dependency."""
    import struct
    import wave
    tmp_path.mkdir(parents=True, exist_ok=True)
    path = tmp_path / name
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


# ── The step reports failure, and the runner sees it ──────────────────

def _run_step(tmp_path: Path, project: Path = None):
    project = project or (tmp_path / "project")
    project.mkdir(exist_ok=True)
    # The step now reads WAV files from the temporal index's audio cache
    # (Area.AUDIO_CACHE), not directly from raw_footage_files.  Place the
    # WAV there so the step finds it.
    from library.tools.project_layout import Area, ProjectLayout
    audio_cache = ProjectLayout(project).write_dir(
        Area.AUDIO_CACHE, step="temporal_index")
    wav = _audio(audio_cache, name="clip_001.wav")
    # raw_footage_files still needs a real path on disk (the step checks
    # os.path.exists), but it is NOT opened by Praat any more.
    source = tmp_path / "clip_001.MOV"
    source.write_bytes(b"\x00" * 64)  # a dummy - never read by Praat
    env = dict(os.environ)
    env["PYTHONPATH"] = str(REPO) + os.pathsep + env.get("PYTHONPATH", "")
    proc = subprocess.run(
        [sys.executable, str(STEP / "step.py")],
        input=json.dumps({
            "raw_footage_files": [{"path": str(source),
                                   "clip_id": "clip_001"}],
            "project_folder": str(project),
            "temporal_index": {},
        }),
        capture_output=True, text=True, encoding="utf-8", cwd=str(REPO),
        env=env,
    )
    return proc, json.loads(proc.stdout)["prosody_analysis"]


def _seed_stale_record(project: Path) -> Path:
    """Write the literal record project 001 shipped, where the step READS.

    The area is named, not composed. This test used to hand-write
    `pipeline_output/prosody` while the step resolves
    `Area.PROSODY` to `pipeline_output/steps/1_05_prosody_analysis`, so
    the step reported `0 cached, 1 to analyze` and never opened the record
    at all - the assertion below was passing on the missing dependency and
    not on the stale record. A test composes a project path no more freely
    than a step does; see AGENTS.md 8.
    """
    from library.tools.project_layout import Area, ProjectLayout

    prosody_dir = ProjectLayout(project).write_dir(
        Area.PROSODY, step="prosody_analysis")
    path = prosody_dir / "clip_001_prosody.json"
    path.write_text(json.dumps({
        "clip_id": "clip_001",
        "prosody": {"method": None, "error": "parselmouth not installed"},
        "analysis_time_s": 0.0,
    }), encoding="utf-8")
    return path


def _cached_count(stderr: str) -> int:
    """What the step said it was reusing off disk."""
    match = re.search(r"(\d+) cached", stderr)
    assert match, f"the step printed no cache line: {stderr[-400:]}"
    return int(match.group(1))


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


def test_a_stale_error_record_on_disk_is_rejected(tmp_path):
    """A profile left by an earlier broken run must not read as data.

    This runs whether or not parselmouth is installed, so it asserts the
    thing that is true either way: the record is never carried forward as
    a measurement. It used to assert `available is False`, which is not
    that - it is a statement about the ENVIRONMENT. With the dependency
    absent the step reports `available: false` for its own reasons, and
    with it present the clip is re-analysed and correctly reports
    `available: true`, so the assertion broke the moment somebody
    installed parselmouth (2026-08-28) despite nothing changing about the
    stale record or the step's treatment of it.
    """
    project = tmp_path / "project"
    project.mkdir()
    _seed_stale_record(project)

    _proc, out = _run_step(tmp_path, project=project)

    profiles = out.get("profiles") or {}
    for clip_id, profile in profiles.items():
        assert profile_defect(profile) == "", (
            f"{clip_id} was reported as a profile while measuring nothing: "
            f"{profile_defect(profile)}")
    assert "parselmouth not installed" not in json.dumps(profiles), (
        "the stale record reached the output as a measurement")

    measured = "clip_001" in profiles
    unmeasured = "clip_001" in (out.get("unmeasured_clips") or [])
    assert measured != unmeasured, (
        f"clip_001 must be reported as measured or as unmeasured, exactly "
        f"one: profiles={list(profiles)} unmeasured={out.get('unmeasured_clips')}")
    assert out["available"] is measured, (
        f"available={out['available']} disagrees with what was measured")


def test_a_stale_error_record_does_not_block_re_analysis(tmp_path):
    """The other half of "a profile file IS the cache".

    The record was rejected at collection - correctly - and then its mere
    existence counted as "already analysed", so it permanently prevented
    the measurement that would have replaced it. One run made without
    parselmouth poisoned every later run that had it. Measured 2026-08-28
    on a working parselmouth: `1 cached, 0 to analyze`, `available:
    false`. The cache check now runs the same `profile_defect` the
    collection half does.
    """
    project = tmp_path / "project"
    project.mkdir()
    stale = _seed_stale_record(project)

    proc, out = _run_step(tmp_path, project=project)

    assert _cached_count(proc.stderr) == 0, (
        f"a profile that measured nothing was reused as cached data: "
        f"{proc.stderr[-400:]}")

    if parselmouth_installed:
        assert out["available"] is True, (
            f"the dependency is installed and the clip was re-analysed, so "
            f"it must measure: {out.get('error')}")
        assert profile_defect(out["profiles"]["clip_001"]) == ""
        assert "parselmouth not installed" not in stale.read_text(
            encoding="utf-8"), "the stale record was not replaced on disk"
    else:
        assert out["available"] is False
        assert out["unmeasured_clips"] == ["clip_001"]


# ── Container files that Praat cannot read ────────────────────────────

def test_mov_container_uses_cached_audio_not_raw_file(tmp_path):
    """The step must read from the temporal index's audio cache, not the
    raw .MOV.  Praat raises ``PraatError: Not an audio file`` on a .MOV
    container, and this was the inner failure hidden behind the missing
    parselmouth dependency for weeks.

    The test creates a .MOV file that is NOT valid audio, places a valid
    WAV in the audio cache where the temporal index would have left it,
    and asserts the step uses the WAV - not the .MOV.
    """
    from library.tools.project_layout import Area, ProjectLayout

    project = tmp_path / "project"
    project.mkdir()

    # A non-audio file pretending to be a .MOV - Praat would reject this.
    source = tmp_path / "clip_001.MOV"
    source.write_bytes(b"\x00\x00\x00\x20ftypqt  " + b"\x00" * 56)

    # Place valid WAV in the audio cache (where the temporal index puts it).
    audio_cache = ProjectLayout(project).write_dir(
        Area.AUDIO_CACHE, step="temporal_index")
    _audio(audio_cache, name="clip_001.wav")

    env = dict(os.environ)
    env["PYTHONPATH"] = str(REPO) + os.pathsep + env.get("PYTHONPATH", "")
    proc = subprocess.run(
        [sys.executable, str(STEP / "step.py")],
        input=json.dumps({
            "raw_footage_files": [{"path": str(source),
                                   "clip_id": "clip_001"}],
            "project_folder": str(project),
            "temporal_index": {},
        }),
        capture_output=True, text=True, encoding="utf-8", cwd=str(REPO),
        env=env,
    )
    out = json.loads(proc.stdout)["prosody_analysis"]

    if parselmouth_installed:
        assert out["available"] is True, (
            f"with cached audio the step must succeed: {out.get('error')}")
        assert "clip_001" in out["profiles"]
        # The profile must reference the WAV, not the MOV.
        profile = out["profiles"]["clip_001"]
        audio_ref = profile.get("audio_file", "")
        assert audio_ref.endswith(".wav"), (
            f"the step opened the raw file instead of the cached WAV: "
            f"{audio_ref}")
    else:
        # Without parselmouth, the step correctly reports failure -
        # but NOT "Not an audio file", because the step never opened the
        # .MOV.  The error must be about parselmouth, not about the format.
        assert out["available"] is False
        assert "parselmouth" in (out.get("error") or "").lower(), (
            f"expected a parselmouth error, not a format error: "
            f"{out.get('error')}")


