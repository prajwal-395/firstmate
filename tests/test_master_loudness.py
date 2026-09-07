"""Mastering brings a quiet master to the delivery target, true-peak-safe.

The P4 gate (`render_qa.measure_lufs`) has failed every run since #155 with
the same finding - the master ~6 dB under -14 LUFS - and the finding is
real. These tests prove the mechanism in `master_loudness` against fixtures
built under `tmp_path` (never a real project), and pin the target to the
gate's own default so the two cannot drift apart silently.

Each gate-direction test calibrates first: the quiet fixture is MEASURED
failing before anything normalizes it. A fixture that ever measured
compliant would fail here loudly instead of letting the fix pass vacuously.

Needs real ffmpeg: CI installs it (AGENTS.md 9) and this lane's Mac carries
it at /opt/homebrew/bin/ffmpeg. Without it the suite skips by name rather
than passing hollow.
"""
import inspect
import shutil

import pytest

from library.tools import master_loudness as ml
from library.tools import render_qa

needs_ffmpeg = pytest.mark.skipif(
    shutil.which("ffmpeg") is None,
    reason="ffmpeg not installed - runs in CI (which installs ffmpeg per "
           "AGENTS.md 9) and on machines with ffmpeg on PATH")


def _quiet_master(path) -> None:
    """A 5-second master that fails the gate the way the real ones do."""
    import subprocess
    proc = subprocess.run(
        ["ffmpeg", "-nostdin", "-y",
         "-f", "lavfi", "-i", "color=c=black:s=320x240:d=5",
         "-f", "lavfi", "-i", "sine=frequency=440:duration=5",
         "-af", "volume=0.1",
         "-c:v", "libx264", "-pix_fmt", "yuv420p",
         "-c:a", "aac", "-b:a", "128k", "-shortest", str(path)],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=120, check=False,
    )
    assert proc.returncode == 0, f"fixture synth failed: {proc.stderr[-500:]}"


@needs_ffmpeg
def test_quiet_fixture_fails_then_passes(tmp_path):
    src = tmp_path / "quiet.mp4"
    out = tmp_path / "mastered.mp4"
    _quiet_master(src)

    # Calibration: the fixture really fails, or nothing below means anything.
    before = render_qa.measure_lufs(str(src))
    assert not before.passed, (
        f"fixture measures compliant ({before.detail}) - it no longer "
        f"reproduces the quiet-master finding this test exists for")

    result = ml.normalize_to_delivery(str(src), str(out))

    assert not result.already_compliant
    assert abs(result.output_i - ml.DELIVERY_LUFS_TARGET) <= 1.0
    assert result.output_tp <= -1.0

    # The gate itself, at its defaults, on the normalized file.
    after = render_qa.measure_lufs(str(out))
    assert after.passed, f"normalized master still fails: {after.detail}"

    # The input is evidence and is never modified in place.
    assert not render_qa.measure_lufs(str(src)).passed


@needs_ffmpeg
def test_already_compliant_master_is_copied_not_rebuilt(tmp_path):
    src = tmp_path / "quiet.mp4"
    mid = tmp_path / "mastered.mp4"
    again = tmp_path / "mastered_again.mp4"
    _quiet_master(src)
    ml.normalize_to_delivery(str(src), str(mid))

    result = ml.normalize_to_delivery(str(mid), str(again))

    assert result.already_compliant
    assert render_qa.measure_lufs(str(again)).passed


@needs_ffmpeg
def test_unmeasurable_or_self_targeted_input_is_refused(tmp_path):
    with pytest.raises(RuntimeError):
        ml.normalize_to_delivery(
            str(tmp_path / "missing.mp4"), str(tmp_path / "out.mp4"))
    src = tmp_path / "quiet.mp4"
    _quiet_master(src)
    with pytest.raises(RuntimeError):
        ml.normalize_to_delivery(str(src), str(src))


def test_mastering_target_is_the_gate_target():
    """The normalizer aims where the gate enforces, by construction.

    Runs everywhere (no ffmpeg): it reads the gate's own signature, so a
    change to either default fails here instead of letting mastering aim at
    a target the gate does not hold - or the reverse.
    """
    assert ml.DELIVERY_LUFS_TARGET == inspect.signature(
        render_qa.measure_lufs).parameters["target_lufs"].default
    assert ml.DELIVERY_LUFS_TARGET == inspect.signature(
        render_qa.run_full_render_qa).parameters["target_lufs"].default
