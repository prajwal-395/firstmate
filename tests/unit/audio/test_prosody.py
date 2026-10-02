"""Prosody that measured nothing must not report success.

The dependency is declared, a step that cannot measure reports
`available: false` (which `run_pipeline.check_output_is_real` reads as a
failed step), and an error record on disk is never carried forward as a
measurement or as cache. Incident (project 001's seventeen hollow
records): docs/evidence/prosody.md.
"""
import json
import os
import re
import subprocess
import sys
from pathlib import Path
import pytest


REPO = Path(__file__).resolve().parents[3]
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
    from library.tools.dependency_groups import declared_requirements
    lines = declared_requirements(REPO / "requirements.txt")
    assert any(l.startswith("praat-parselmouth") for l in lines), (
        "step 1.05 declares env.parselmouth as a requirement; "
        f"requirements.txt must install it. Got: {lines}")


# ── What counts as a profile ──────────────────────────────────────────

def test_a_profile_that_measured_nothing_is_rejected():
    """The literal record project 001 shipped, and a profile whose
    measurements are all empty, are both defects."""
    shipped = profile_defect(
        {"clip_id": "clip_001",
         "prosody": {"method": None, "error": "parselmouth not installed"}})
    assert "parselmouth not installed" in shipped
    assert profile_defect(
        {"prosody": {"method": "parselmouth-praat", "pitch_stats": {},
                     "intensity_contour_50ms": []}})
    assert profile_defect(
        {"clip_id": "clip_001",
         "prosody": {"method": "parselmouth-praat",
                     "pitch_stats": {"mean_f0_hz": 120.0},
                     "intensity_contour_50ms": [{"time": 0.0, "db": 60.0}]}}
    ) == ""


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
    """Write the literal record project 001 shipped, where the step READS
    (`Area.PROSODY`, never a hand-composed path - AGENTS.md 8)."""
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
def test_a_step_that_measured_nothing_fails_the_run(tmp_path):
    """`available: false` is the loudness: the reason reaches the
    operator, and check_output_is_real stops the run rather than carrying
    an error record into the next prompt."""
    import importlib.util
    proc, out = _run_step(tmp_path)
    assert out["available"] is False
    assert out["profiles"] == {}
    assert "parselmouth" in out["error"], out["error"]
    assert out["unmeasured_clips"] == ["clip_001"]

    spec = importlib.util.spec_from_file_location(
        "_run_pipeline_prosody_test",
        REPO / "library" / "processes" / "edit_video" / "run_pipeline.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    problems = module.check_output_is_real(
        "prosody_analysis", json.loads(proc.stdout))
    assert problems, "the run would have continued over a dead signal"
    assert "available=false" in problems[0]


def test_a_stale_error_record_is_neither_data_nor_cache(tmp_path):
    """A profile left by an earlier broken run must not read as data,
    and its mere existence must not count as "already analysed" (one run
    without parselmouth once poisoned every later run that had it).
    Asserts what is true with or without the dependency installed."""
    project = tmp_path / "project"
    project.mkdir()
    stale = _seed_stale_record(project)

    proc, out = _run_step(tmp_path, project=project)

    assert _cached_count(proc.stderr) == 0, (
        f"a profile that measured nothing was reused as cached data: "
        f"{proc.stderr[-400:]}")
    profiles = out.get("profiles") or {}
    for clip_id, profile in profiles.items():
        assert profile_defect(profile) == "", clip_id
    assert "parselmouth not installed" not in json.dumps(profiles), (
        "the stale record reached the output as a measurement")
    measured = "clip_001" in profiles
    unmeasured = "clip_001" in (out.get("unmeasured_clips") or [])
    assert measured != unmeasured
    assert out["available"] is measured
    assert measured is parselmouth_installed
    if parselmouth_installed:
        assert "parselmouth not installed" not in stale.read_text(
            encoding="utf-8"), "the stale record was not replaced on disk"


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


# --------------------------------------------------------------------------
# From test_prosody_view.py
#
# Seventeen copies of an error message are not seventeen measurements.
#
# `view:prosody` selects by `profile_defect` (the predicate step 1.05
# refuses a hollow profile with), states the absence once, and still
# carries every real measurement. History: docs/evidence/prosody.md.

if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from library.tools.context_views import build_view
from library.tools.toon_serializer import json_to_toon


HOLLOW = {
    f"clip_{i:03d}": {
        "clip_id": f"clip_{i:03d}",
        "audio_file": f"/nowhere/IMG_18{i:02d}.MOV",
        "prosody": {"method": None, "error": "parselmouth not installed"},
        "analysis_time_s": 0.0,
    }
    for i in range(1, 18)
}
REAL = {
    "clip_011": {
        "clip_id": "clip_011",
        "prosody": {"method": "praat",
                    "pitch_stats": {"mean_f0_hz": 118.4, "range_hz": 96.2}},
    }
}


# ── The absence is stated once, not seventeen times ───────────────────

def test_the_absence_is_stated_once_and_real_measurements_still_reach():
    view = build_view("prosody", {"prosody_analysis": {"profiles": HOLLOW}})
    context = json_to_toon(view)
    assert context.count("parselmouth not installed") == 1, context
    assert "17 of 17" in context
    assert "measured" not in view["prosody"]

    # A saving bought by blinding the step is not a saving.
    view = build_view(
        "prosody", {"prosody_analysis": {"profiles": {**HOLLOW, **REAL}}})
    assert view["prosody"]["clips_measured"] == 1
    assert view["prosody"]["measured"]["clip_011"]["prosody"][
        "pitch_stats"]["mean_f0_hz"] == 118.4
    assert "118.4" in json_to_toon(view)
    assert "16 of 17" in view["prosody"]["not_measured"]

    # A step with no prosody routed gets nothing rather than an error.
    assert build_view("prosody", {"clip_catalog": []}) == {}
    assert build_view("prosody", {"prosody_analysis": {}}) == {}


def test_contours_do_not_reach_the_prompt():
    """AGENTS.md 10.1: No raw value list reaches a prompt."""
    data = {
        "clip_001": {
            "clip_id": "clip_001",
            "prosody": {
                "method": "praat",
                "pitch_stats": {"mean_f0_hz": 118.4},
                "pitch_contour_10ms": [{"time": 0.01, "pitch": 100}],
                "intensity_contour_50ms": [{"time": 0.05, "intensity": 60}],
            }
        }
    }
    view = build_view("prosody", {"prosody_analysis": {"profiles": data}})
    assert view["prosody"]["clips_measured"] == 1
    assert "pitch_stats" in view["prosody"]["measured"]["clip_001"]["prosody"]
    assert "pitch_contour_10ms" not in view["prosody"]["measured"]["clip_001"]["prosody"]
    assert "intensity_contour_50ms" not in view["prosody"]["measured"]["clip_001"]["prosody"]
