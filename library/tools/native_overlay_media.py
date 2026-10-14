"""Prepare overlays for the pinned FilmCraft decoder.

FilmCraft composites ProRes 4444 with alpha but silently omits qtrle. Keep
the source render immutable and cache a ProRes carriage by source content.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import time
from pathlib import Path
from typing import Any

CACHE_SCHEMA = "filmcraft-overlay-prores4444-v3"
OVERLAY_ROLES = {"subtitle_overlay", "motion_graphic", "frame_overlay", "post_header"}


def _probe(path: Path) -> dict[str, str] | None:
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=codec_name,pix_fmt",
         "-of", "json", str(path)], check=True, capture_output=True,
        text=True, encoding="utf-8")
    streams = json.loads(result.stdout).get("streams", [])
    return streams[0] if streams else None


def prepare_projection(projection: dict[str, Any], media_dir: str | Path,
                       cache_dir: str | Path, *,
                       ticks_per_second: int | None = None,
                       frame_rate: float = 25.0
                       ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Prepare compositing media as validated, content-keyed ProRes files.

    qtrle movies are transcoded; post-header PNG stills are looped for their
    longest recorded run. `cost_seconds` includes hashing, cache lookup,
    transcode and verification. Cache hits report zero transcode seconds.
    """
    root, cache = Path(media_dir), Path(cache_dir)
    roles = {placement["asset_id"]: placement.get("role")
             for placement in projection["placements"]
             if placement.get("role") in OVERLAY_ROLES}
    imports = []
    records: list[dict[str, Any]] = []
    for item in projection["imports"]:
        updated = dict(item)
        asset_id = item["asset_id"]
        if roles.get(asset_id) is None:
            imports.append(updated)
            continue
        source = Path(item["uri"])
        if not source.is_absolute():
            source = root / source
        stream = _probe(source)
        is_post_header_still = (
            roles.get(asset_id) == "post_header"
            and source.suffix.lower() == ".png"
            and ticks_per_second is not None and ticks_per_second > 0)
        if not is_post_header_still and (
                stream is None or stream.get("codec_name") != "qtrle"):
            imports.append(updated)
            continue

        started = time.perf_counter()
        digest = hashlib.sha256()
        with source.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        spans = [int(row["timeline_end"]) - int(row["timeline_start"])
                 for row in projection["placements"]
                 if row.get("asset_id") == asset_id
                 and row.get("role") == "post_header"]
        span_key = max(spans, default=0) if is_post_header_still else 0
        rate_key = frame_rate if is_post_header_still else 0
        key = hashlib.sha256(
            f"{CACHE_SCHEMA}:{digest.hexdigest()}:{span_key}:"
            f"{rate_key:.12g}".encode()).hexdigest()
        cache.mkdir(parents=True, exist_ok=True)
        destination = cache / f"{key}.mov"
        cache_hit = destination.is_file() and _is_prores_alpha(destination)
        transcode_seconds = 0.0
        if not cache_hit:
            temporary = cache / f".{key}.{os.getpid()}.{time.time_ns()}.tmp.mov"
            try:
                encode_started = time.perf_counter()
                encode = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y"]
                if is_post_header_still:
                    if span_key <= 0:
                        raise ValueError(
                            f"post_header placement has no positive duration: {asset_id}")
                    encode += ["-loop", "1", "-framerate",
                               f"{frame_rate:.12g}", "-i", str(source), "-t",
                               f"{span_key / ticks_per_second:.9f}"]
                else:
                    encode += ["-i", str(source), "-map", "0:v:0"]
                encode += ["-an", "-sn", "-dn", "-c:v", "prores_ks",
                           "-profile:v", "4", "-pix_fmt", "yuva444p10le",
                           "-alpha_bits", "16", str(temporary)]
                subprocess.run(
                    encode,
                    check=True, capture_output=True, text=True, encoding="utf-8")
                transcode_seconds = time.perf_counter() - encode_started
                if not _is_prores_alpha(temporary):
                    raise RuntimeError(f"transcoded overlay failed ProRes alpha validation: {source}")
                os.replace(temporary, destination)
            finally:
                temporary.unlink(missing_ok=True)
        updated["uri"] = str(destination)
        imports.append(updated)
        records.append({
            "asset_id": asset_id, "source": str(source), "cache": str(destination),
            "source_sha256": digest.hexdigest(), "cache_hit": cache_hit,
            "still_looped": is_post_header_still,
            "transcode_seconds": round(transcode_seconds, 6),
            "preparation_seconds": round(time.perf_counter() - started, 6),
        })
    prepared = dict(projection, imports=imports)
    return prepared, records


def _is_prores_alpha(path: Path) -> bool:
    if not path.is_file() or path.stat().st_size == 0:
        return False
    try:
        stream = _probe(path)
    except (subprocess.CalledProcessError, json.JSONDecodeError):
        return False
    return bool(stream and stream.get("codec_name") == "prores"
                and "a" in stream.get("pix_fmt", ""))
