"""Disposable, reproducible media and setup recipes for AXI-14.

Resolve timeline/profile creation remains a live AXI fixture operation. This
module generates and measures the source assets, records the isolated
timeline/topology recipes, and owns fail-closed cleanup of its temporary tree.
It never touches a user project or Resolve instance.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import tempfile
import uuid
from pathlib import Path
from typing import Any

SCHEMA = "ren-axi14-fixture/1"
MARKER = ".ren-axi14-fixture.json"

TIMELINE_PROFILES = tuple(
    {"id": f"{width}x{height}-{rate.replace('/', '_')}",
     "resolution": [width, height], "frame_rate": rate}
    for width, height in ((1920, 1080), (3840, 2160), (1080, 1920))
    for rate in ("24", "25", "30000/1001", "60"))

ASSET_RECIPES = (
    {"id": "h264-landscape", "filename": "h264-landscape.mp4",
     "size": [1920, 1080], "rate": "24", "codec": "h264", "audio": True},
    {"id": "h264-4k", "filename": "h264-4k.mp4",
     "size": [3840, 2160], "rate": "25", "codec": "h264", "audio": True},
    {"id": "h264-portrait", "filename": "h264-portrait.mp4",
     "size": [1080, 1920], "rate": "60", "codec": "h264", "audio": True},
    {"id": "prores-hd", "filename": "prores-hd.mov",
     "size": [1920, 1080], "rate": "24", "codec": "prores", "audio": False},
    {"id": "linked-av", "filename": "linked-av.mp4",
     "size": [1920, 1080], "rate": "30000/1001", "codec": "h264", "audio": True},
    {"id": "wav-only", "filename": "wav-only.wav",
     "rate": "48000", "codec": "pcm_s16le", "audio": True},
)

TOPOLOGY_RECIPES: dict[str, dict[str, Any]] = {
    "renamed_tracks": {
        "picture_track": "Program Picture",
        "graphics_track": "Brand Graphics",
        "audio_track": "Dialogue and Sync",
        "verify_by_name_readback": True,
    },
    "complex_linked_av": {
        "groups": [{"video": "linked-av.mp4", "audio": "linked-av.mp4",
                    "link_required": True}],
        "verify_source_and_record_offsets": True,
    },
    "graded_project": {
        "method": "TimelineItem.SetCDL under the isolated fixture AXI lease",
        "cdl": {"NodeIndex": "1", "Slope": "1.08 1.0 0.94",
                "Offset": "0 0 0", "Power": "1 1 1",
                "Saturation": "1.05"},
        "readback": "fresh timeline item GetClipProperty Slope/Offset/Power/Saturation",
        "verify_grade_readback": True,
    },
    "fusion_composition": {
        "method": "TimelineItem.AddFusionComp under the isolated fixture AXI lease",
        "native_fusion_comp_required": True,
        "verify_comp_identity_and_tool_count": True,
    },
    "ambiguous_targets": {"same_display_name_count": 2,
                          "expected_result": "refuse_without_mutation"},
    "stale_snapshots": {"capture_snapshot_then_reorder": True,
                         "expected_result": "refuse_without_mutation"},
    "approved_revision": {"approved_edit_history_required": True},
    "staged_promotion": {"staged_revision_required": True,
                          "verify_promotion_and_history": True},
}


def _run(argv: list[str], *, timeout: int = 180) -> str:
    result = subprocess.run(argv, capture_output=True, text=True,
                           encoding="utf-8", timeout=timeout, check=False)
    if result.returncode:
        raise RuntimeError(
            f"command failed ({result.returncode}): {' '.join(argv)}\n"
            f"{result.stderr[-2000:]}")
    return result.stdout


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _ffmpeg_args(asset: dict[str, Any], output: Path,
                 ffmpeg: str) -> list[str]:
    args = [ffmpeg, "-hide_banner", "-loglevel", "error", "-y"]
    if asset["id"] == "wav-only":
        return args + ["-f", "lavfi", "-i",
                       "sine=frequency=440:sample_rate=48000:duration=2",
                       "-c:a", "pcm_s16le", str(output)]
    width, height = asset["size"]
    args += ["-f", "lavfi", "-i",
             f"color=c=0x345678:s={width}x{height}:r={asset['rate']}:d=2"]
    if asset["audio"]:
        args += ["-f", "lavfi", "-i",
                 "sine=frequency=440:sample_rate=48000:duration=2"]
    args += ["-map", "0:v:0"]
    if asset["audio"]:
        args += ["-map", "1:a:0", "-c:a", "aac", "-shortest"]
    if asset["codec"] == "prores":
        args += ["-c:v", "prores_ks", "-profile:v", "2",
                 "-pix_fmt", "yuv422p10le"]
    else:
        args += ["-c:v", "libx264", "-preset", "ultrafast",
                 "-crf", "38", "-pix_fmt", "yuv420p"]
    return args + [str(output)]


def _probe(path: Path, ffprobe: str) -> dict[str, Any]:
    raw = _run([ffprobe, "-v", "error", "-show_streams", "-show_format",
                "-of", "json", str(path)], timeout=30)
    return json.loads(raw)


def _validate_probe(recipe: dict[str, Any], probe: dict[str, Any]) -> None:
    streams = probe.get("streams")
    if not isinstance(streams, list):
        raise TypeError(f"ffprobe returned no streams for {recipe['filename']}")
    video = next((row for row in streams if row.get("codec_type") == "video"), None)
    audio = next((row for row in streams if row.get("codec_type") == "audio"), None)
    if "size" in recipe:
        if not video or [video.get("width"), video.get("height")] != recipe["size"]:
            raise ValueError(f"video geometry mismatch for {recipe['filename']}")
        if recipe["codec"] == "prores" and video.get("codec_name") != "prores":
            raise ValueError(f"ProRes codec missing from {recipe['filename']}")
        if recipe["codec"] == "h264" and video.get("codec_name") != "h264":
            raise ValueError(f"H.264 codec missing from {recipe['filename']}")
        if bool(audio) is not recipe["audio"]:
            raise ValueError(f"audio stream mismatch for {recipe['filename']}")
        rate = video.get("avg_frame_rate") or video.get("r_frame_rate")
        numerator, denominator = map(int, recipe["rate"].split("/")) \
            if "/" in recipe["rate"] else (int(recipe["rate"]), 1)
        observed_n, observed_d = map(int, rate.split("/"))
        if abs(observed_n / observed_d - numerator / denominator) > 0.001:
            raise ValueError(f"frame rate mismatch for {recipe['filename']}: {rate}")
    else:
        if not audio or video:
            raise ValueError("WAV-only fixture must have audio and no video stream")
        if audio.get("codec_name") != "pcm_s16le" or audio.get("sample_rate") != "48000":
            raise ValueError("WAV-only fixture must be 48 kHz PCM s16le")


def create_fixture(*, ffmpeg: str = "ffmpeg", ffprobe: str = "ffprobe") -> Path:
    """Create an owned temp fixture and return its manifest path."""
    root = Path(tempfile.mkdtemp(prefix="ren-axi14-fixture-")).resolve()
    fixture_id = uuid.uuid4().hex
    try:
        assets = []
        for recipe in ASSET_RECIPES:
            path = root / recipe["filename"]
            _run(_ffmpeg_args(recipe, path, ffmpeg))
            probed = _probe(path, ffprobe)
            _validate_probe(recipe, probed)
            assets.append({**recipe, "sha256": _sha256(path),
                           "bytes": path.stat().st_size,
                           "streams": [{"type": stream.get("codec_type"),
                                        "codec": stream.get("codec_name"),
                                        "width": stream.get("width"),
                                        "height": stream.get("height"),
                                        "frame_rate": stream.get("avg_frame_rate")}
                                       for stream in probed["streams"]]})
        manifest = {
            "schema": SCHEMA, "fixture_id": fixture_id,
            "owned_root": str(root), "asset_recipes": assets,
            "timeline_profiles": list(TIMELINE_PROFILES),
            "topology_recipes": TOPOLOGY_RECIPES,
            "native_resolve_setup": {
                "required_entry": "guarded AXI fixture setup under the Resolve lease",
                "must_use_isolated_project": True,
                "read_back_project_and_timeline_settings": True,
                "grade_and_fusion_require_independent_native_readback": True,
            },
        }
        manifest_path = root / MARKER
        manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True)
                                 + "\n", encoding="utf-8")
        return manifest_path
    except BaseException:
        shutil.rmtree(root, ignore_errors=True)
        raise


def inspect_fixture(manifest_path: Path | str) -> dict[str, Any]:
    """Verify fixture ownership, exact inventory, and every asset digest."""
    manifest_path = Path(manifest_path).expanduser().resolve(strict=True)
    root = manifest_path.parent
    if (manifest_path.name != MARKER
            or not root.name.startswith("ren-axi14-fixture-")
            or root.parent != Path(tempfile.gettempdir()).resolve()):
        raise ValueError("fixture cleanup is restricted to owned temp roots")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    recorded_root = manifest.get("owned_root")
    try:
        owns_root = Path(recorded_root).expanduser().resolve(strict=True) == root
    except (TypeError, OSError, RuntimeError):
        owns_root = False
    if manifest.get("schema") != SCHEMA or not owns_root:
        raise ValueError("fixture ownership marker does not match this root")
    expected = {MARKER} | {row["filename"] for row in manifest["asset_recipes"]}
    actual = {path.name for path in root.iterdir()}
    if actual != expected or any(path.is_symlink() for path in root.iterdir()):
        raise ValueError("fixture inventory differs from its ownership manifest")
    measured = []
    for row in manifest["asset_recipes"]:
        path = root / row["filename"]
        if _sha256(path) != row["sha256"] or path.stat().st_size != row["bytes"]:
            raise ValueError(f"fixture asset changed: {row['filename']}")
        measured.append(row["filename"])
    return {"fixture_id": manifest["fixture_id"], "root": str(root),
            "asset_count": len(measured), "assets_verified": measured,
            "cleanup_inventory_verified": True}


def cleanup_fixture(manifest_path: Path | str) -> dict[str, Any]:
    """Remove only a complete, intact fixture created by ``create_fixture``."""
    evidence = inspect_fixture(manifest_path)
    root = Path(evidence["root"])
    shutil.rmtree(root)
    if root.exists():
        raise RuntimeError(f"fixture cleanup did not remove {root}")
    return {**evidence, "cleanup_status": "verified_removed"}
