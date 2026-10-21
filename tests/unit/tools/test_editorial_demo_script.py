"""The demonstration's record is PRODUCED, not typed.

Two defects, both of which made the demo's headline numbers unreadable:

- the DELIVERY section was copied verbatim out of a hand-written steps
  file, so it carried three checks nobody ran (the frame, the rate and
  the length) and a verdict nobody measured;
- the ASSESSMENT's counts were typed by hand, and they disagreed with
  the rows they claimed to count - `passed 3, failed 4` over rows that
  were three passes, three failures and four unmeasured, with a score
  of `None` where the engine's scorer computes 0.5.

The judge's own work - the per-criterion rows, the rationales and the
artifacts it looked at - is the run's, and this script keeps it. The
counts and the delivery measurements are the engine's, and this script
computes them.
"""

from __future__ import annotations

import importlib.util
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from library.tools.editorial_acceptance import briefs

ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "scripts" / "vep_bench_demo.py"
SPEC = importlib.util.spec_from_file_location("vep_bench_demo", SCRIPT)
assert SPEC and SPEC.loader
demo_script = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = demo_script
SPEC.loader.exec_module(demo_script)

FFMPEG = shutil.which("ffmpeg")


@pytest.fixture(scope="module")
def video(tmp_path_factory) -> str:
    if FFMPEG is None:
        pytest.skip("ffmpeg not on PATH")
    target = tmp_path_factory.mktemp("demo") / "out.mp4"
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
         "color=c=0x345678:s=320x180:r=24:d=2",
         "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000:d=2",
         "-map", "0:v:0", "-map", "1:a:0", "-c:v", "libx264",
         "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-c:a", "aac",
         "-shortest", str(target)],
        check=True, capture_output=True)
    return str(target)


def _public() -> dict:
    the_set = briefs.load_brief_set(None)
    by_id = briefs.briefs_by_id(the_set)
    return briefs.public_view(by_id["open-with-the-cost"])


def _steps(path: str, public: dict) -> dict:
    criteria = []
    for index, criterion in enumerate(public["acceptance_criteria"]):
        criteria.append({
            "id": criterion["id"],
            "status": "pass" if index == 0 else (
                "fail" if index == 1 else "not_measured"),
            "rationale": "read off the delivered file",
            "evidence": ([{"artifact": path,
                           "observation": "the delivered file"}]
                         if index < 2 else []),
        })
    return {
        "brief_id": "open-with-the-cost",
        "instruction": public["instruction"],
        "delivery": {
            "path": path,
            "timeline": {"fps": 24.0, "frames": 48},
        },
        "assessment": {
            "judge": "an independent editor",
            "criteria": criteria,
            # The counts a hand-typed assessment carried, and which must
            # NOT reach the record: the scorer owns them.
            "editorial": {"status": "pass", "passed": 99, "failed": 0,
                          "not_measured": 0, "score": 1.0},
        },
    }


def test_the_delivery_section_is_measured_from_the_file(video):
    """The defect: the delivery section was typed, so three checks could
    not run and the report said nothing about them - which reads as
    coverage."""
    public = _public()
    built = demo_script.build_record(_steps(video, public), public)
    section = built["delivery"]
    assert section["sha256"]
    assert section["technical_not_checked"] == []
    assert section["technical_verification"]["framerate"] is True
    assert section["technical_verification"]["duration"] is True
    assert "technical_verification" not in _steps(video, public)["delivery"]


def test_the_assessment_is_counted_by_the_scorer(video):
    """The defect: the counts were typed, and they disagreed with the
    rows they claimed to count."""
    public = _public()
    built = demo_script.build_record(_steps(video, public), public)
    editorial = built["assessment"]["editorial"]
    assert editorial["passed"] == 1
    assert editorial["failed"] == 1
    assert editorial["not_measured"] == 1
    assert editorial["score"] == 0.5
    assert editorial["status"] == "fail"
    assert built["assessment"]["judge"] == "an independent editor"


def test_a_delivery_that_cannot_be_read_is_reported_as_one(video, tmp_path):
    """The defect: an absent render is scored as a delivered one."""
    public = _public()
    steps = _steps(video, public)
    steps["delivery"]["path"] = str(tmp_path / "nothing.mp4")
    built = demo_script.build_record(steps, public)
    assert built["delivery"]["evidence_error"]
    assert not built["delivery"].get("sha256")
    assert built["assessment"]["editorial"]["status"] == "not_measured"
