"""The delivery verdict measures the FILE's loudness.

The defect: `verify_deliverable` checked duration, resolution, audio
presence, black frames and codec, and never measured loudness. A file
nine decibels under the delivery target came back `passed: True` - the
verdict said the deliverable was fine while it was nine decibels too
quiet. The master happened to be normalized upstream, which is exactly
why the silence went unnoticed: a route that skipped the master shipped
quietly, and the verdict said nothing.

The loudness leg is `deliverable_qc`'s own check, against the same
delivery spec, so the delivery verdict and the QC gate cannot grade the
same number two ways.
"""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import replace

import pytest

from library.tools import reel_deliver
from library.tools.delivery_spec import DEFAULT_DELIVERY_SPEC

FFMPEG = shutil.which("ffmpeg")


def _loudness_file(tmp_path, name: str, gain_db: float) -> str:
    """A file with a tone at a known gain, so its loudness is known."""
    target = tmp_path / f"{name}.mp4"
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
         "color=c=0x345678:s=320x180:r=24:d=2",
         "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000:d=2",
         "-af", f"volume={gain_db}dB",
         "-map", "0:v:0", "-map", "1:a:0", "-c:v", "libx264",
         "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-c:a", "aac",
         "-shortest", str(target)],
        check=True, capture_output=True)
    return str(target)


@pytest.fixture(scope="module")
def quiet_file(tmp_path_factory) -> str:
    """A file at the demo's own measured level: -23.2 LUFS against a
    -14.0 target, so nine decibels under."""
    if FFMPEG is None:
        pytest.skip("ffmpeg not on PATH")
    return _loudness_file(tmp_path_factory.mktemp("loud"), "quiet", -1.4)


@pytest.fixture(scope="module")
def loud_file(tmp_path_factory) -> str:
    if FFMPEG is None:
        pytest.skip("ffmpeg not on PATH")
    return _loudness_file(tmp_path_factory.mktemp("loud"), "loud", 8.0)


def test_a_file_nine_decibels_under_target_fails_the_deliverable(quiet_file):
    """The defect: the verdict said `passed` over a file the platform
    would play nine decibels too quiet."""
    verdict = reel_deliver.verify_deliverable(
        quiet_file, 2.0, 320, 180, "H264")
    assert verdict["loudness"]["passed"] is False
    assert verdict["passed"] is False


def test_the_verdict_carries_the_loudness_it_measured(loud_file):
    """The defect: a verdict that says nothing about the level cannot be
    audited afterwards."""
    verdict = reel_deliver.verify_deliverable(
        loud_file, 2.0, 320, 180, "H264")
    assert verdict["loudness"]["metric"] == "lufs"
    assert verdict["loudness"]["passed"] is True


def test_the_loudness_is_measured_against_the_spec_not_a_constant(
        quiet_file):
    """The defect: a verdict that grades loudness against a hardcoded
    target ships to the wrong platform's spec."""
    lenient = replace(DEFAULT_DELIVERY_SPEC, loudness_target_lufs=-30.0,
                      loudness_tolerance_lufs=12.0)
    verdict = reel_deliver.verify_deliverable(
        quiet_file, 2.0, 320, 180, "H264", lenient)
    assert verdict["loudness"]["passed"] is True
    strict = replace(DEFAULT_DELIVERY_SPEC, loudness_target_lufs=-14.0)
    verdict = reel_deliver.verify_deliverable(
        quiet_file, 2.0, 320, 180, "H264", strict)
    assert verdict["loudness"]["passed"] is False


def test_the_delivery_verdict_and_the_qc_gate_measure_the_same_number(
        quiet_file):
    """The defect: two loudness checks, two answers - and the one the
    verdict used was the one that could not fail."""
    from library.tools import deliverable_qc

    verdict = reel_deliver.verify_deliverable(
        quiet_file, 2.0, 320, 180, "H264")
    qc = deliverable_qc.measure_loudness(quiet_file, DEFAULT_DELIVERY_SPEC)
    assert verdict["loudness"]["passed"] == qc.passed
