"""Variant renders: naming, ledger keys, spec guards and the reframe seam.

Covers `library/tools/deliver_variants.py` - the path `ren deliver`
takes when the requested platform specs differ. The ffmpeg-backed
`render_variant` proofs live at the bottom of this module on a tiny
synthetic clip (the same precedent as `test_reframe.py`'s one expensive
test); everything above them is pure.
"""

from __future__ import annotations

import dataclasses
import os
import subprocess

import pytest


def _square_spec():
    from library.tools.delivery_spec import DeliverySpec

    return DeliverySpec(
        platform="square", width=240, height=240, fps=10.0,
        codec="H264", container="mp4",
        loudness_target_lufs=-14.0, loudness_tolerance_lufs=1.0,
        true_peak_ceiling_dbtp=-1.0, caption_format="burned_in",
        source="test")


# ── Naming and ledger keys ──────────────────────────────────────

def test_variant_file_name_gains_the_served_platforms():
    from library.tools import deliver_variants

    assert deliver_variants.variant_file_name(
        "Reel 01 - pricing", "{timeline}.{ext}", "mp4",
        ["master"]) == "Reel 01 - pricing.master.mp4"


def test_variant_file_name_never_collides_with_the_master():
    from library.tools import deliver_variants
    from library.tools.reel_deliver import render_file_name

    master = render_file_name(
        "Reel 01 - pricing", "{timeline}.{ext}", "mp4")
    variant = deliver_variants.variant_file_name(
        "Reel 01 - pricing", "{timeline}.{ext}", "mp4", ["master"])
    assert variant != master
    assert variant.startswith("Reel 01 - pricing.")
    assert variant.endswith(".mp4")


def test_variant_file_name_suffixes_a_literal_naming():
    from library.tools import deliver_variants

    assert deliver_variants.variant_file_name(
        "Reel 01 - pricing", "final.mp4", "mp4",
        ["master"]) == "final.master.mp4"


def test_variant_file_name_handles_a_name_with_no_extension():
    from library.tools import deliver_variants

    assert deliver_variants.variant_file_name(
        "Reel 01 - pricing", "final", "mp4",
        ["tiktok"]) == "final.tiktok"


def test_variant_ledger_key_carries_the_served_platforms():
    from library.tools import deliver_variants

    key = deliver_variants.variant_ledger_key(
        "Reel 01 - pricing", ["master"])
    assert key != "Reel 01 - pricing"
    assert "master" in key
    other = deliver_variants.variant_ledger_key(
        "Reel 01 - pricing", ["tiktok"])
    assert other != key


def test_variant_extension_follows_the_variant_container():
    from library.tools import deliver_variants

    assert deliver_variants.variant_extension(_square_spec()) == "mp4"
    assert deliver_variants.variant_extension(
        _square_spec(), fallback="mp4") == "mp4"


# ── Spec guards ─────────────────────────────────────────────────

def test_registry_rows_pass_the_variant_guard():
    from library.tools import deliver_variants
    from library.tools.delivery_spec import DELIVERY_SPECS

    for spec in DELIVERY_SPECS.values():
        deliver_variants.check_variant_spec(spec)


def test_non_h264_codec_is_refused_not_shipped_wrong():
    from library.tools import deliver_variants

    prores = dataclasses.replace(_square_spec(), codec="ProRes")
    with pytest.raises(ValueError, match="libx264"):
        deliver_variants.check_variant_spec(prores)


def test_sidecar_captions_are_refused_not_shipped_wrong():
    from library.tools import deliver_variants

    sidecar = dataclasses.replace(
        _square_spec(), caption_format="sidecar")
    with pytest.raises(ValueError, match="sidecar"):
        deliver_variants.check_variant_spec(sidecar)


def test_non_positive_dimensions_are_refused():
    from library.tools import deliver_variants

    flat = dataclasses.replace(_square_spec(), width=0)
    with pytest.raises(ValueError, match="dimensions"):
        deliver_variants.check_variant_spec(flat)


# ── Probes and rates ────────────────────────────────────────────

def test_probe_master_refuses_a_missing_file(tmp_path):
    from library.tools import deliver_variants

    with pytest.raises(RuntimeError, match="needs the rendered master"):
        deliver_variants.probe_master(str(tmp_path / "nope.mp4"))


def test_render_variant_refuses_a_missing_master(tmp_path):
    from library.tools import deliver_variants

    with pytest.raises(RuntimeError, match="needs the rendered master"):
        deliver_variants.render_variant(
            master_path=str(tmp_path / "nope.mp4"), spec=_square_spec(),
            output_path=str(tmp_path / "out.mp4"))


def test_render_variant_refuses_an_unsatisfiable_spec(tmp_path):
    from library.tools import deliver_variants

    prores = dataclasses.replace(_square_spec(), codec="ProRes")
    with pytest.raises(ValueError, match="libx264"):
        deliver_variants.render_variant(
            master_path=str(tmp_path / "master.mp4"), spec=prores,
            output_path=str(tmp_path / "out.mp4"))


def test_parse_rate_reads_ffprobe_ratios():
    from library.tools.deliver_variants import _parse_rate

    assert _parse_rate("30/1") == 30.0
    assert _parse_rate("30000/1001") == pytest.approx(29.97, abs=0.01)
    assert _parse_rate("25.0") == 25.0
    assert _parse_rate("0/0") is None
    assert _parse_rate("") is None
    assert _parse_rate(None) is None


def test_output_fps_prefers_override_then_spec_then_probe():
    from library.tools.deliver_variants import _output_fps

    assert _output_fps(_square_spec(), 24.0, 30.0) == (24.0,
                                                      "caller_override")
    assert _output_fps(_square_spec(), 0.0, 30.0) == (10.0, "variant_spec")


def test_centred_windows_cover_the_master_duration():
    from library.tools.deliver_variants import _centred_windows

    probed = {"width": 320, "height": 240, "fps": 10.0,
              "duration_seconds": 1.0}
    windows = _centred_windows(probed, 240, 240, 10.0)
    assert len(windows) == 10
    first = windows[0]
    assert (first.w, first.h) == (240.0, 240.0)
    assert all((w.x, w.y) == (first.x, first.y) for w in windows)


# ── The crop reaches the pixels: tiny synthetic clip ────────────

def _synthetic_master(path: str) -> None:
    subprocess.run([
        "ffmpeg", "-nostdin", "-y", "-f", "lavfi", "-i",
        "testsrc=duration=1:size=320x240:rate=10",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", path,
    ], capture_output=True, check=True)


def _probe_size(path: str) -> str:
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=width,height", "-of", "csv=p=0",
         path],
        capture_output=True, text=True, check=True)
    return probe.stdout.strip()


def test_probe_master_measures_the_synthetic_clip(tmp_path):
    from library.tools import deliver_variants

    src = str(tmp_path / "master.mp4")
    _synthetic_master(src)
    probed = deliver_variants.probe_master(src)
    assert (probed["width"], probed["height"]) == (320, 240)
    assert probed["fps"] == pytest.approx(10.0)
    assert probed["duration_seconds"] == pytest.approx(1.0, abs=0.1)


def test_render_variant_produces_a_centred_square(tmp_path):
    """No face track: the crop is centred, said plainly, at target size."""
    from library.tools import deliver_variants

    src = str(tmp_path / "master.mp4")
    _synthetic_master(src)
    out = str(tmp_path / "variant.mp4")
    result = deliver_variants.render_variant(
        master_path=src, spec=_square_spec(), output_path=out,
        work_dir=str(tmp_path / "scratch"))
    assert os.path.isfile(result["output_path"])
    assert (result["width"], result["height"]) == (240, 240)
    assert result["crop"] == "centred"
    assert "centred" in result["crop_reason"]
    assert _probe_size(out) == "240,240"


def test_render_variant_follows_a_supplied_face_track(tmp_path):
    """A supplied track reaches the subject path and says which crop ran."""
    from library.tools import deliver_variants

    src = str(tmp_path / "master.mp4")
    _synthetic_master(src)
    out = str(tmp_path / "variant.mp4")
    track = [0.3] * 5 + [0.7] * 5
    result = deliver_variants.render_variant(
        master_path=src, spec=_square_spec(), output_path=out,
        face_centers=track, sample_rate_hz=10.0,
        work_dir=str(tmp_path / "scratch"))
    assert os.path.isfile(result["output_path"])
    assert result["crop"] == "subject_followed"
    assert _probe_size(out) == "240,240"
