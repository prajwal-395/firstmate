"""Pre-conform keeps the source-frame map exact while placing proxies.

The native FilmCraft path ingests 4K MXF at ~43x below ffmpeg speed, so
used ranges (plus handles) transcode once to edit-resolution ProRes and
the engine places those instead. What must never break:

1. The map back is EXACT: pre-conform frame N names an original source
   frame, out-of-range and fractional mappings refuse, and the swap-back
   record still names the camera original (the Project Model never learns
   the proxy).
2. The cache is CONTENT-ADDRESSED: the same fingerprinted range under the
   same spec reuses the file (a second call is a hit), and a short or
   changed file never passes as valid.
3. Ranges are HONEST: empty and out-of-bounds ranges refuse, overlapping
   uses of one source merge into one transcode, and a transcode whose
   frame count reads back wrong is deleted, not mapped.

Heavy engine exports are not here: the SSIM/PSNR gate of pre-conformed
vs direct export lives in `native/ren-filmcraft/tests/adapter_qualification.rs`
beside the pinned CLI. These tests need only ffmpeg/ffprobe on PATH and
the committed A/V fixture, read-only (AGENTS.md 8).
"""

import json
import os
import shutil
from pathlib import Path

import pytest

from library.tools.filmcraft_preconform import (
    PreconformError,
    PreconformRecord,
    PreconformSpec,
    apply_to_media_map,
    cache_path,
    conform_filter,
    measure_fidelity,
    merge_ranges,
    preconform_source,
    source_fingerprint,
    used_range_with_handles,
)

ROOT = Path(__file__).resolve().parents[3]
FIXTURE = ROOT / "native/ren-filmcraft/tests/fixtures/linked-av.mp4"

# The fixture: 160x90 h264 at 24 fps, 72 frames with audio.
SOURCE_FPS = (24, 1)
SOURCE_FRAMES = 72


def needs_ffmpeg():
    if shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None:
        pytest.skip("ffmpeg/ffprobe are not on PATH")


def spec(**overrides):
    kwargs = {"width": 160, "height": 90, "fps_num": 24, "fps_den": 1}
    kwargs.update(overrides)
    return PreconformSpec(**kwargs)


# ── Pure planning: ranges, merge, conform policy, fingerprints ─────────

def test_used_range_expands_by_handles_and_clamps_to_media():
    assert used_range_with_handles(10, 20, 24, SOURCE_FRAMES) == (0, 44)
    assert used_range_with_handles(0, SOURCE_FRAMES, 24, SOURCE_FRAMES) == (0, SOURCE_FRAMES)
    assert used_range_with_handles(60, 70, 0, SOURCE_FRAMES) == (60, 70)


def test_used_range_refuses_empty_and_out_of_bounds():
    with pytest.raises(PreconformError):
        used_range_with_handles(20, 20, 24, SOURCE_FRAMES)
    with pytest.raises(PreconformError):
        used_range_with_handles(-1, 20, 24, SOURCE_FRAMES)
    with pytest.raises(PreconformError):
        used_range_with_handles(10, SOURCE_FRAMES + 1, 24, SOURCE_FRAMES)


def test_merge_ranges_unions_overlap_and_keeps_gaps():
    assert merge_ranges([(0, 10), (5, 20), (30, 40)]) == [(0, 20), (30, 40)]
    assert merge_ranges([(0, 10), (10, 20)]) == [(0, 20)]
    with pytest.raises(PreconformError):
        merge_ranges([(5, 5)])


def test_conform_filter_matches_the_measured_engine_policy():
    # Matching dimensions need no filter at all.
    assert conform_filter(160, 90, spec()) is None
    # A larger source center-crops with no resample: the engine places
    # 4K pixels 1:1 into the sequence (measured 2026-10-11, SSIM 0.94
    # for the exact center window vs ~0.44 for every scaled conform).
    assert conform_filter(3840, 2160, spec(width=1080, height=1920)) == "crop=1080:1920"
    assert conform_filter(320, 180, spec()) == "crop=160:90"
    # A source smaller than the spec in some dimension refuses: the
    # engine's upscale policy there is unmeasured.
    with pytest.raises(PreconformError):
        conform_filter(90, 160, spec())
    with pytest.raises(PreconformError):
        conform_filter(2000, 1000, spec(width=1080, height=1920))
    with pytest.raises(PreconformError):
        conform_filter(0, 90, spec())


def test_spec_validation_refuses_nonsense():
    for bad in [
        {"width": 0},
        {"height": -1},
        {"fps_num": 0},
        {"fps_den": -1},
        {"prores_profile": 4},
        {"handle_frames": -1},
    ]:
        with pytest.raises(PreconformError):
            spec(**bad).validate()


def test_fingerprint_is_stable_and_content_addressed(tmp_path):
    first = source_fingerprint(FIXTURE)
    assert source_fingerprint(FIXTURE) == first
    altered = tmp_path / "altered.mp4"
    altered.write_bytes(FIXTURE.read_bytes() + b"\x00")
    assert source_fingerprint(altered) != first
    with pytest.raises(PreconformError):
        source_fingerprint(tmp_path / "absent.mp4")


# ── The exact map back to source frames ──────────────────────────────

def make_record(**overrides):
    kwargs = {
        "asset_id": "asset-av",
        "source_path": "/media/original.MXF",
        "fingerprint": "abc",
        "source_fps_num": 24,
        "source_fps_den": 1,
        "source_start_frame": 10,
        "source_end_frame": 82,
        "spec_key": "160x90_24x1_p2_a1",
        "spec_fps_num": 24,
        "spec_fps_den": 1,
        "path": "/cache/preconform.mov",
        "frames": 72,
        "audio": True,
        "cache_hit": False,
    }
    kwargs.update(overrides)
    return PreconformRecord(**kwargs)


def test_source_frame_map_is_exact_at_equal_rates():
    record = make_record()
    assert record.source_frame_exact(0) == 10
    assert record.source_frame_exact(71) == 81
    with pytest.raises(PreconformError):
        record.source_frame_exact(72)
    with pytest.raises(PreconformError):
        record.source_frame_exact(-1)


def test_source_frame_map_refuses_fractional_rates():
    record = make_record(source_fps_num=24000, source_fps_den=1001,
                         spec_fps_num=24, spec_fps_den=1)
    with pytest.raises(PreconformError):
        record.source_frame_exact(1)


def test_swapback_names_the_camera_original():
    record = make_record()
    swapped, swapback = apply_to_media_map(
        {"asset-av": Path("/media/original.MXF")}, [record]
    )
    assert swapped["asset-av"] == Path("/cache/preconform.mov")
    entry = swapback["preconformed"]["asset-av"]
    assert entry["original_path"] == "/media/original.MXF"
    assert entry["original_range_frames"] == [10, 82]
    with pytest.raises(PreconformError):
        apply_to_media_map({}, [record])


# ── Transcode, cache, fidelity (needs ffmpeg, not the engine) ─────────

def test_preconform_transcodes_once_then_hits_cache(tmp_path):
    needs_ffmpeg()
    cache = tmp_path / "preconform"
    first = preconform_source(
        FIXTURE,
        asset_id="asset-av",
        source_fps_num=24,
        source_fps_den=1,
        use_start_frame=0,
        use_end_frame=SOURCE_FRAMES,
        total_frames=SOURCE_FRAMES,
        spec=spec(handle_frames=0),
        cache_dir=cache,
    )
    assert first.frames == SOURCE_FRAMES
    assert first.cache_hit is False
    assert Path(first.path).is_file()
    assert Path(first.path).with_suffix(".json").is_file()
    # The whole-file use plus zero handles maps 1:1 onto the source.
    assert first.source_frame_exact(0) == 0
    assert first.source_frame_exact(SOURCE_FRAMES - 1) == SOURCE_FRAMES - 1
    second = preconform_source(
        FIXTURE,
        asset_id="asset-av",
        source_fps_num=24,
        source_fps_den=1,
        use_start_frame=0,
        use_end_frame=SOURCE_FRAMES,
        total_frames=SOURCE_FRAMES,
        spec=spec(handle_frames=0),
        cache_dir=cache,
    )
    assert second.cache_hit is True
    assert second.path == first.path


# ── Cheap revalidation: stat plus sampled content, exact check on doubt ──
#
# A warm hit must not redecode the proxy (tens of seconds on a reel
# range). The sidecar anchors size, mtime, inode and sampled windows;
# anything those reject meets the exact ffprobe count instead.


def _transcode(tmp_path, **overrides):
    kwargs = {
        "asset_id": "asset-av",
        "source_fps_num": 24,
        "source_fps_den": 1,
        "use_start_frame": 0,
        "use_end_frame": SOURCE_FRAMES,
        "total_frames": SOURCE_FRAMES,
        "spec": spec(handle_frames=0),
        "cache_dir": tmp_path / "preconform",
    }
    kwargs.update(overrides)
    return preconform_source(FIXTURE, **kwargs)


def test_preconform_warm_hit_skips_the_frame_decode(tmp_path, monkeypatch):
    """The second call for an untouched entry never runs ffprobe."""
    needs_ffmpeg()
    import library.tools.filmcraft_preconform as fcp

    first = _transcode(tmp_path)
    assert first.cache_hit is False
    monkeypatch.setattr(
        fcp, "_ffprobe_frames",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("a warm hit decodes nothing")))
    second = _transcode(tmp_path)
    assert second.cache_hit is True
    assert second.path == first.path


def test_preconform_truncated_entry_retranscodes(tmp_path):
    """A short file fails the stat gate, then the exact count, and is
    rebuilt rather than mapped."""
    needs_ffmpeg()
    first = _transcode(tmp_path)
    proxy = Path(first.path)
    whole = proxy.stat().st_size
    proxy.write_bytes(proxy.read_bytes()[: whole // 2])
    second = _transcode(tmp_path)
    assert second.cache_hit is False
    assert Path(second.path).stat().st_size == whole
    restored = _transcode(tmp_path)
    assert restored.cache_hit is True


def test_preconform_same_size_tamper_meets_the_exact_check(tmp_path, monkeypatch):
    """A byte flipped inside a sampled window with the mtime restored
    declines the fast gate: the exact check (here boomed) engages."""
    needs_ffmpeg()
    import library.tools.filmcraft_preconform as fcp

    first = _transcode(tmp_path)
    proxy = Path(first.path)
    before = proxy.stat()
    data = bytearray(proxy.read_bytes())
    data[10] ^= 0xFF
    proxy.write_bytes(bytes(data))
    os.utime(proxy, ns=(before.st_atime_ns, before.st_mtime_ns))
    monkeypatch.setattr(
        fcp, "_ffprobe_frames",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("the exact check engaged")))
    with pytest.raises(AssertionError, match="the exact check engaged"):
        _transcode(tmp_path)


def test_preconform_old_sidecar_upgrades_to_the_fast_gate(tmp_path, monkeypatch):
    """Entries written before the revalidation identity pay one last
    exact check, which upgrades the sidecar; the run after is fast."""
    needs_ffmpeg()
    import library.tools.filmcraft_preconform as fcp

    first = _transcode(tmp_path)
    sidecar = Path(first.path).with_suffix(".json")
    legacy = json.loads(sidecar.read_text(encoding="utf-8"))
    for key in ("size_bytes", "mtime_ns", "dev", "ino", "sample_digest"):
        legacy.pop(key, None)
    sidecar.write_text(json.dumps(legacy, indent=2) + "\n", encoding="utf-8")
    second = _transcode(tmp_path)
    assert second.cache_hit is True
    upgraded = json.loads(sidecar.read_text(encoding="utf-8"))
    assert "sample_digest" in upgraded
    monkeypatch.setattr(
        fcp, "_ffprobe_frames",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("an upgraded entry decodes nothing")))
    third = _transcode(tmp_path)
    assert third.cache_hit is True


def test_preconform_cache_path_is_deterministic(tmp_path):
    first = cache_path(tmp_path, "abcdef0123456789", 10, 82, spec())
    assert cache_path(tmp_path, "abcdef0123456789", 10, 82, spec()) == first
    assert cache_path(tmp_path, "abcdef0123456789", 10, 83, spec()) != first
    assert cache_path(tmp_path, "abcdef0123456789", 10, 82, spec(width=320)) != first


def test_preconform_refuses_out_of_bounds_use(tmp_path):
    needs_ffmpeg()
    with pytest.raises(PreconformError):
        preconform_source(
            FIXTURE,
            asset_id="asset-av",
            source_fps_num=24,
            source_fps_den=1,
            use_start_frame=0,
            use_end_frame=SOURCE_FRAMES + 1,
            total_frames=SOURCE_FRAMES,
            spec=spec(),
            cache_dir=tmp_path,
        )


def test_fidelity_of_identical_files_is_perfect(tmp_path):
    needs_ffmpeg()
    measured = measure_fidelity(FIXTURE, FIXTURE)
    assert measured["ssim_all"] == 1.0
    assert measured["psnr_average_db"] == float("inf")
    with pytest.raises(PreconformError):
        measure_fidelity(FIXTURE, tmp_path / "absent.mp4")


def test_preconformed_proxy_stays_close_to_its_source(tmp_path):
    needs_ffmpeg()
    cache = tmp_path / "preconform"
    record = preconform_source(
        FIXTURE,
        asset_id="asset-av",
        source_fps_num=24,
        source_fps_den=1,
        use_start_frame=0,
        use_end_frame=SOURCE_FRAMES,
        total_frames=SOURCE_FRAMES,
        spec=spec(handle_frames=0),
        cache_dir=cache,
    )
    measured = measure_fidelity(FIXTURE, Path(record.path))
    # ProRes standard from a tiny h264 is near-transparent; the engine
    # gate in adapter_qualification.rs carries the binding floor.
    assert measured["ssim_all"] >= 0.90
    assert measured["psnr_average_db"] >= 30.0
