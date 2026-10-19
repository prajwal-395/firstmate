"""The per-source footage memory: what Ren retains about a source file.

Scout report `data/vep-long-footage-memory-scout/report.md` (in
firstmate's home, outside this checkout) measured that Ren remembers
the TIMELINE, not the footage: on the 3.03 h geo-podcast episode it
holds a transcript of 24.6% of the source and no picture measurement at
all. This module is build-plan items 1-3 of that report's section 5:
a per-source store keyed by content digest, the whole-source
transcription that fills it first, and a shared 2 Hz I-frame sample
every per-frame measurement rides instead of decoding on its own.

**The shape.** `<memory root>/<content_digest>/`, where the digest is
`footage_identity.fingerprint` - size plus sha256 of the first and last
mebibyte. Clip ids (`clip_001` ...) are project-local numbering; the
digest is content identity, so two projects cut from the same camera
file (geo-podcast and podcast-roughcut already share LC4932/LCATL0013)
read the same memory for free. Projects REFERENCE it and never copy
it. Nothing here goes into `pipeline_data.json` (rewritten after every
step; the memory is per source, not per run).

**The slots.** M0, M1, M2, M3, M3b, M3c, M3d, M3e, M3f, M1b, M4, M5, M6, M7, M9, M10, M11, VI-03 and VI-09 are
written now; every later lane writes into its named slot without
inventing its own shape:

======== ============================ ============================= ==========
slot     file                         what                          producer
======== ============================ ============================= ==========
M0       `source.json` +              digest, size, streams, live    `library/tools/
         `program.16k.wav`            audio tracks, program-track    source_primitives.py`
                                      selection and its WAV, GOP
                                      (nullable)
M1       `transcript.words.json`      whole-SOURCE word-timed       this module
                                      transcript (voz text through  (voz + MFA)
                                      MFA, the reel path's seam)
M1b      `speakers.json`              diarized turns + voice        `library/tools/
                                       embeddings                    person_entity.py`
M2       `frames/` +                  I-frame thumbnails, 384 px,   this module
         `frames.index.json`           at ~2 Hz
M3       `persons.json`               faces, lips, hands at the M2  `library/tools/
                                       cadence (Apple Vision)        person_measurements.py`
M3b      `identity.json`              face/voice identity tracks,   `library/tools/
                                       speech-face links (face-only  person_entity.py`
                                       cross-source identity - see
                                       that module's docstring for why)
M3d      `regions.json` +             person instance masks at the  `library/tools/
         `regions.masks.<sha>.json.gz` M2 times, region tracks,     person_regions.py`
                                       face-track links
M3e      `saliency.json`              attention-saliency boxes at   `library/tools/
                                       the M2 times                  attention_saliency.py`
M3f      `region_stats.json`          luma, colour, skin, sharpness `library/tools/
                                       inside each M3d region vs     region_stats.py`
                                       outside, at the M2 times
M4       `scenes.json`                scene observations (VLM) +    `library/tools/
                                       ffmpeg scene boundaries       analysis/
                                                                vision_pipeline_v3.py`
VI-09    `scene_cinematography.json`  scene/shot/sub-shot/sequence  `library/tools/
                                      intervals and measured          scene_cinematography.py`
                                      attributes, citing M4 and
                                      temporal measurements
M5       `sound.json`                 environmental sound events   `library/steps/
                                      and music interval evidence   step_1_04_temporal_index/
                                                                    + step 2.06`
M6       `clock.json`                 per-source multicam offset    conversation_clock.py
M7       `events.json`                DERIVED per-person spans      `library/tools/
                                       (speaking, on_screen)         event_spans.py`
M9       `world_model.json`           derived per-second lookup    `library/tools/
                                       (M0-M8 in, one model out)     world_model.py`
M10      `relationships.json`         relationship CANDIDATES       `library/tools/
                                       (holding, pointing,           relationships.py`
                                       looking_at; over M3 + M3b
                                       + M3d)
M11      `audio_quality.json`         per-channel LUFS/true-peak/    `library/tools/
                                       clipping/noise/SNR/silence/    audio_quality.py`
                                       spectral/dynamics + intervals
VI-03    `image_defects.json`          sampled temporal noise,       `library/tools/analysis/
                                       blockiness, frozen/black      image_defects.py`
                                       samples, flicker, sharpness
                                       proxy (not focus/blur cause)
======== ============================ ============================= ==========

A slot a lane has not written yet is ABSENT, never a default: an
absent M1 reads as "not transcribed", not as "silent". Readers check
presence; see `read_m1`.

**Staleness.** A record is fresh when the file on disk still
fingerprints to the `content_digest` + `size_bytes` the record was
built from - the same content-not-mtime reasoning as
`footage_identity.py`. A re-recorded or replaced file changes the
digest, so the old transcript is never served as the new file's. The
project-side half reuses the search index's own mechanism:
`memory_fingerprint` feeds `footage_segments.ingest_fingerprint`, so
`ren search-index` reports stale the moment a memory transcript lands
or a source changes under one.

**What this module refuses.** Transcribing a file whose every audio
track is silent records `untranscribed-silent` rather than an empty
transcript - an empty hearing and a silent file are the same object
otherwise. A source with no live track is never given a program track
by default; the selection says which track and on what basis
(`declared`, `single`, `loudest-live`) with the measured levels as
evidence, so the choice is auditable per file.

**M2's decode.** `-skip_frame nokey` on the decoder drops the other
11/12 frames of a 12-frame GOP before they are decoded, not after, so
a whole file samples at its I-frame cadence (measured at 2 Hz on both
geo-podcast cameras) in about 4.7 min per 82-minute file against 76
min for a software 5 Hz full-decode pass (scout report §2.2, §2.5).
`-hwaccel videotoolbox` is tried first on this machine; a decode that
fails with it retries in software, since the shape that matters is
"I-frames only", not the accelerator. Every per-frame measurement
(Vision, CLIP, entity) reads this one sample instead of decoding its
own - `library/tools/analysis/footage_frames.py` and
`library/tools/person_entity.py` are the wired consumers. `gop_frames`
in M0 is filled in from the measured spacing once M2 has sampled a
source.

**Scope.** Audio and the shared frame sample (M0/M1/M2) in this
module; M3b (face/voice identity) is written by
`library/tools/person_entity.py` into the same slot layout, reading M2
when a fresh sample exists rather than decoding its own. Speaker-turn
diarization into M1b and predicates are later lanes with reserved
slots above; their code is untouched here. Media is read-only: the
only writes are under the memory root and a temp dir for demuxed
audio or extracted frames.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import wave
from pathlib import Path
from typing import Dict, List, Optional, Tuple

try:
    from library.tools.shared_environment import vep_home
    from library.tools import footage_identity
    from library.tools.speech_measurements import nonverbal_vocalizations
except ImportError:  # imported as `tools.*` from inside library/
    from tools.shared_environment import vep_home
    from tools import footage_identity
    from tools.speech_measurements import nonverbal_vocalizations

MEMORY_DIRNAME = "source_memory"
"""The store directory name under `vep_home()` (AGENTS.md 9: one shared
dependency location per machine, outside every checkout)."""

MEMORY_ROOT_ENV = "PIPELINE_SOURCE_MEMORY_ROOT"
"""Names the memory root outright, for a machine whose layout differs."""

# ── The slots ──────────────────────────────────────────────────────
# A lane writes into its named file and no other. Every slot named below
# has a writer (the per-slot constants and the writer history under
# them); no slot is reserved.

SLOT_SOURCE = "source.json"                    # M0
SLOT_TRANSCRIPT = "transcript.words.json"      # M1
SLOT_SPEAKERS = "speakers.json"                # M1b - written by person_entity.py
SLOT_FRAMES_DIR = "frames"                     # M2
SLOT_FRAMES_INDEX = "frames.index.json"        # M2
SLOT_PERSONS = "persons.json"                  # M3 - written by person_measurements.py
SLOT_IDENTITY = "identity.json"                # M3b - written by person_entity.py
SLOT_EXPRESSIONS = "expressions.json"          # M3c - written by expression_classifier.py
SLOT_REGIONS = "regions.json"                  # M3d - written by person_regions.py
SLOT_SALIENCY = "saliency.json"                # M3e - written by attention_saliency.py
SLOT_REGION_STATS = "region_stats.json"        # M3f - written by region_stats.py
SLOT_DEPTH = "depth.json"                      # M3g - written by depth.py
SLOT_SCENES = "scenes.json"                    # M4 - written by vision_pipeline_v3.py
SLOT_SCENE_CINEMATOGRAPHY = "scene_cinematography.json"  # VI-09 deterministic intervals
SLOT_SOUND = "sound.json"                      # M5 - written by step_1_04
M4_SCENE_PASS_METHOD = "folded window call (PROMPT_WINDOW_ALL_COMPACT)"
M4_BOUNDARY_METHOD = "ffmpeg-select-scene-gt-0.3"
SLOT_CLOCK = "clock.json"                      # M6 - written by conversation_clock.py
SLOT_EVENTS = "events.json"                    # M7 - written by event_spans.py
SLOT_VERDICTS = "verdicts.json"                # M8 - written by span_verification.py
SLOT_WORLD_MODEL = "world_model.json"          # M9 - written by world_model.py
SLOT_RELATIONSHIPS = "relationships.json"       # M10 - written by relationships.py
SLOT_AUDIO_QUALITY = "audio_quality.json"       # M11 - written by audio_quality.py
SLOT_IMAGE_DEFECTS = "image_defects.json"        # VI-03 - image_defects.py

SLOT_BY_LANE = {
    "m0": SLOT_SOURCE, "m1": SLOT_TRANSCRIPT, "m1b": SLOT_SPEAKERS,
    "m2": SLOT_FRAMES_INDEX, "m3": SLOT_PERSONS, "m3b": SLOT_IDENTITY,
    "m3c": SLOT_EXPRESSIONS, "m3d": SLOT_REGIONS, "m3e": SLOT_SALIENCY,
    "m3f": SLOT_REGION_STATS, "m3g": SLOT_DEPTH, "m4": SLOT_SCENES,
    "vi-09": SLOT_SCENE_CINEMATOGRAPHY, "m5": SLOT_SOUND,
    "m6": SLOT_CLOCK, "m7": SLOT_EVENTS, "m8": SLOT_VERDICTS,
    "m9": SLOT_WORLD_MODEL, "m10": SLOT_RELATIONSHIPS,
    "m11": SLOT_AUDIO_QUALITY, "vi-03": SLOT_IMAGE_DEFECTS,
}

# No slot is reserved now. SLOT_CLOCK (M6) was the first to gain a writer
# (`library/tools/conversation_clock.py`); SLOT_FRAMES_DIR/SLOT_FRAMES_INDEX
# (M2) followed (`extract_iframes`/`build_frames` below), then SLOT_IDENTITY
# (M3b) and SLOT_SPEAKERS (M1b) (`library/tools/person_entity.py`),
# SLOT_PERSONS (M3) (`person_measurements.py`), SLOT_EVENTS (M7)
# (`event_spans.py`), SLOT_SCENES (M4) (`vision_pipeline_v3.py`),
# SLOT_SOUND (M5) (`step_1_04_temporal_index/step.py`),
# SLOT_EXPRESSIONS (M3c) (`expression_classifier.py`), SLOT_REGIONS (M3d)
# (`person_regions.py`, plus its content-addressed mask sidecar),
# SLOT_SALIENCY (M3e) (`attention_saliency.py`),
# SLOT_REGION_STATS (M3f) (`region_stats.py`), SLOT_DEPTH (M3g)
# (`depth.py`),
# SLOT_WORLD_MODEL (M9) (`world_model.py`), SLOT_RELATIONSHIPS (M10)
# (`relationships.py`) and SLOT_AUDIO_QUALITY (M11) (`audio_quality.py`).

SILENCE_DB = -60.0
"""Below this a track is room tone off, not a candidate for anything.

The same measured value as step 1.02's `MEASURE_SILENCE_DB`, restated
rather than imported: steps import tools, never the reverse, and the
captain's MXF carry an empty stream at about -69 dB, so -60 dB
excludes exactly the nothing while keeping any real ISO.
"""

TRANSCRIPT_RATE_HZ = 16000
"""Demux rate for transcription. The reel path's own rate: 1.04's
`extract_audio_16k` and the scout's whole-source measurement agree."""

M1_STATUS_TRANSCRIBED = "transcribed"

M2_WIDTH = 384
"""Thumbnail width, as measured (§2.2): 10.3 KB/frame JPEG at this size,
~225 MB for a 3.03 h project at 2 Hz. Shared by every per-frame reader -
CLIP resizes to its own small input anyway, so this loses nothing a
consumer needs."""

M2_RATE_HZ_NOMINAL = 2.0
"""The nominal cadence `-skip_frame nokey` lands at on a 12-frame GOP
(measured on both geo-podcast cameras, §2.2). The real cadence is
whatever the source's own keyframes give - this is a label, not a
target the sampler enforces."""

M2_STATUS_SAMPLED = "sampled"


# ── Where the memory lives ─────────────────────────────────────────


def memory_root() -> Path:
    """The per-machine memory root, outside every project and checkout."""
    explicit = os.environ.get(MEMORY_ROOT_ENV)
    if explicit:
        return Path(explicit).expanduser()
    return vep_home() / MEMORY_DIRNAME


def source_dir(content_digest: str, root: Optional[Path] = None) -> Path:
    """The directory holding one source's memory."""
    return (Path(root) if root is not None else memory_root()) / content_digest


# ── Reading the project side (catalog + recorded digests) ──────────
# The memory must not depend on footage search; the dependency
# runs the other way. So the two project reads it needs are local and
# small: the clip catalog and the runner's recorded fingerprints.


def _load_json(path: Path):
    from library.tools.cache_lifecycle import record_cache_metric

    is_source_slot = (len(path.parent.name) == 64
                      and all(char in "0123456789abcdef"
                              for char in path.parent.name))
    try:
        from library.tools.cache_lifecycle import cache_file_lease
        with cache_file_lease(path), open(path, encoding="utf-8") as handle:
            document = json.load(handle)
        if is_source_slot:
            artifact_id = _register_source_memory_cache(path, document)
            from library.tools.cache_lifecycle import cache_artifact_id_from_environment
            artifact_id = cache_artifact_id_from_environment(
                path, _source_memory_cache_kind(path)) or artifact_id
            record_cache_metric(task="source-memory",
                                kind=_source_memory_cache_kind(path), outcome="hit",
                                bytes_used=path.stat().st_size,
                                artifact_id=artifact_id)
        return document
    except (OSError, ValueError):
        if is_source_slot:
            from library.tools.cache_lifecycle import cache_artifact_id_from_environment
            artifact_id = cache_artifact_id_from_environment(
                path, _source_memory_cache_kind(path))
            record_cache_metric(task="source-memory",
                                kind=_source_memory_cache_kind(path), outcome="miss",
                                compatible=False, artifact_id=artifact_id)
        return None


def _source_memory_cache_kind(path: Path) -> str:
    return ("transcription_alignment" if path.name == SLOT_TRANSCRIPT
            else "semantic_measurement")


def _register_source_memory_cache(path: Path, document: dict) -> str:
    """Register a measured source slot when its pipeline run is identifiable."""
    if not isinstance(document, dict):
        return ""
    digest = path.parent.name
    envelope = document.get("evidence_envelope")
    if not isinstance(envelope, dict):
        return ""
    try:
        validate_evidence_envelope(envelope)
    except ValueError:
        return ""
    documented_digest = (document.get("content_digest")
                         or (envelope.get("content_digest")
                             if isinstance(envelope, dict) else None))
    if documented_digest != digest:
        return ""
    from library.tools import event_log
    from library.tools.cache_lifecycle import (
        cache_artifact_id_from_environment,
        register_cache_artifact_from_environment,
    )

    log_path = os.environ.get(event_log.EVENT_LOG_ENV)
    if not log_path:
        return ""
    project_folder = str(Path(log_path).resolve().parents[2])
    source_candidates = [document.get("source_file")]
    state = _load_json(Path(project_folder) / "pipeline_data.json") or {}
    fingerprints = state.get("source_fingerprints") or {}
    source_candidates.extend(
        source.get("path") for source in fingerprints.values()
        if source.get("content_digest") == digest)
    source_available = False
    for source_path in source_candidates:
        if not source_path:
            continue
        try:
            source_available = (
                footage_identity.fingerprint(source_path)["content_digest"] == digest)
        except OSError:
            continue
        if source_available:
            break
    kind = _source_memory_cache_kind(path)
    register_cache_artifact_from_environment(
        path, kind=kind, producer_key=path.resolve().as_posix(),
        regeneration_method=f"source-memory producer: {path.name}",
        source_available=source_available, compatible=True)
    return cache_artifact_id_from_environment(path, kind)


def _record_source_memory_regeneration(path: Path, elapsed_seconds: float) -> None:
    from library.tools.cache_lifecycle import (
        cache_artifact_id_from_environment,
        record_cache_metric,
    )

    kind = _source_memory_cache_kind(path)
    record_cache_metric(
        task="source-memory", kind=kind, outcome="regenerated",
        elapsed_seconds=elapsed_seconds, bytes_used=path.stat().st_size,
        artifact_id=cache_artifact_id_from_environment(path, kind))


def load_catalog(project_folder: str) -> list:
    """The clip catalog, read off `pipeline_data.json` (the record)."""
    state = _load_json(Path(project_folder) / "pipeline_data.json") or {}
    from library.tools import capability_outputs
    catalog = capability_outputs.value(state, "footage.catalog", "clip_catalog")
    return catalog or []


def load_recorded_fingerprints(project_folder: str) -> dict:
    """`{clip_id: {path, size_bytes, content_digest}}`, as the runner saw."""
    state = _load_json(Path(project_folder) / "pipeline_data.json") or {}
    recorded = state.get("source_fingerprints") or {}
    return recorded if isinstance(recorded, dict) else {}


def digest_for_clip(project_folder: str, clip: dict,
                    recorded: Optional[dict] = None) -> Tuple[Optional[str], str]:
    """A clip's content digest, and where it came from.

    The live file is authoritative: two mebibytes off disk is
    milliseconds, so a replaced file is noticed rather than trusted on
    the runner's record. When the media is offline the recorded digest
    still answers - search works without the footage present; building
    does not. Returns `(digest, basis)` with basis `live`, `recorded`
    or `absent`.
    """
    path = clip.get("source_file") or clip.get("path") or ""
    if path:
        try:
            return footage_identity.fingerprint(path)["content_digest"], "live"
        except OSError:
            pass
    if recorded is None:
        recorded = load_recorded_fingerprints(project_folder)
    entry = recorded.get(clip.get("clip_id", "")) or {}
    if entry.get("content_digest"):
        return entry["content_digest"], "recorded"
    return None, "absent"


# ── M0: probing the source ─────────────────────────────────────────


def probe_streams(source_file: str) -> dict:
    """Bounded source registry probe from ffprobe, or a refusal.

    Raises RuntimeError when ffprobe itself fails - a broken probe is
    not a streamless file. Stream metadata is complete, while packet and
    decoded-frame tables are capped to the first 64 packets. This keeps M0
    useful for technical inventory without turning ingestion into a full
    media decode.
    """
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "warning", "-show_streams", "-show_format",
             "-show_packets", "-show_frames", "-read_intervals", "0%+#64",
             "-of", "json", source_file],
            capture_output=True, encoding="utf-8", errors="replace",
            timeout=120, check=False,
        )
    except FileNotFoundError:
        raise RuntimeError("ffprobe not found. Install: brew install ffmpeg")
    if out.returncode != 0:
        raise RuntimeError(
            f"ffprobe failed for {source_file}: "
            f"{(out.stderr or '')[:300]}")
    try:
        probe = json.loads(out.stdout or "")
    except ValueError as unreadable:
        raise RuntimeError(
            f"ffprobe wrote something that is not JSON for "
            f"{source_file}: {unreadable}") from unreadable
    streams = []
    audio_ordinal = 0
    for stream in probe.get("streams") or []:
        entry = dict(stream)
        entry["codec"] = stream.get("codec_name", "unknown")
        tags = stream.get("tags") or {}
        side_data = stream.get("side_data_list") or []
        recorded_capture = {
            key: value for key, value in tags.items()
            if key.lower() in {"creation_time", "make", "model",
                               "encoder", "lens_model", "focal_length",
                               "aperture", "iso"}
        }
        recorded_capture["side_data"] = [
            dict(item) for item in side_data
            if any(k in item for k in (
                "rotation", "displaymatrix", "camera_make", "camera_model",
                "lens", "focal_length", "aperture", "iso"))
        ]
        recorded_capture = {key: value for key, value in recorded_capture.items()
                            if value not in ({}, [])}
        entry["capture_metadata"] = recorded_capture
        entry["capture_metadata_status"] = (
            "available" if recorded_capture else "unavailable")
        if recorded_capture:
            entry["capture_metadata_provenance"] = {
                "producer": "ffprobe", "source_digest": None,
                "basis": "recorded_container_metadata",
            }
        if stream.get("codec_type") == "audio":
            audio_ordinal += 1
            entry["channel"] = audio_ordinal
            try:
                entry["sample_rate"] = int(stream.get("sample_rate"))
            except (TypeError, ValueError):
                entry["sample_rate"] = None
        streams.append(entry)
    try:
        duration = float((probe.get("format") or {}).get("duration"))
    except (TypeError, ValueError):
        duration = None
    packets = probe.get("packets") or []
    frames = probe.get("frames") or []
    warnings = [line.strip() for line in (out.stderr or "").splitlines()
                if line.strip()]
    discontinuities = _timestamp_discontinuities(packets)
    return {
        "streams": streams,
        "duration_seconds": duration,
        "packet_table": packets[:64],
        "frame_table": frames[:64],
        "frame_timing": _frame_timing_summary(frames[:64]),
        "timestamp_discontinuities": discontinuities,
        "file_integrity": {
            "probe_status": ("warning" if warnings or discontinuities or any(
                "C" in str(packet.get("flags", ""))
                for packet in packets) else "clean"),
            "probe_warnings": warnings,
            "corrupt_packet_count": sum(
                ("corrupt" in str(packet.get("flags", ""))
                 or "C" in str(packet.get("flags", "")))
                for packet in packets),
            "sampled_packet_count": len(packets[:64]),
            "sampled_frame_count": len(frames[:64]),
            "sample_scope": "first_64_packets",
        },
    }


def _frame_timing_summary(frames: list) -> dict:
    """Describe cadence only across sampled decoded frame timestamps."""
    by_stream = {}
    for frame in frames[:64]:
        stream_index = frame.get("stream_index")
        value = frame.get("best_effort_timestamp_time", frame.get("pts_time"))
        try:
            timestamp = float(value)
        except (TypeError, ValueError):
            continue
        by_stream.setdefault(stream_index, []).append(timestamp)
    rows = []
    for stream_index, timestamps in sorted(by_stream.items(),
                                            key=lambda row: str(row[0])):
        deltas = [round(b - a, 9) for a, b in zip(timestamps, timestamps[1:])]
        positive = [delta for delta in deltas if delta > 0]
        rows.append({
            "stream_index": stream_index,
            "sampled_timestamps_seconds": timestamps,
            "sampled_deltas_seconds": deltas,
            "variable_frame_rate_observed": (
                len(set(positive)) > 1 if positive else None),
        })
    return {"status": "measured" if rows else "unavailable",
            "sample_scope": "first_64_frames", "streams": rows}


def _timestamp_discontinuities(packets: list) -> list:
    """Report missing PTS and non-monotonic decode timestamps in sample.

    Packet PTS can legitimately move backwards in decode order when a video
    uses B-frames, so ordering checks use DTS. Presentation timestamps remain
    intact in the packet and frame tables.
    """
    prior_dts = {}
    findings = []
    for packet in packets[:64]:
        stream_index = packet.get("stream_index")
        pts = packet.get("pts")
        if pts is None:
            findings.append({"stream_index": stream_index,
                             "kind": "missing_pts"})
        dts = packet.get("dts")
        try:
            dts = int(dts)
        except (TypeError, ValueError):
            findings.append({"stream_index": stream_index,
                             "kind": "missing_dts"})
            continue
        if stream_index in prior_dts and dts <= prior_dts[stream_index]:
            findings.append({"stream_index": stream_index,
                             "kind": "non_monotonic_dts",
                             "previous_dts": prior_dts[stream_index],
                             "dts": dts})
        prior_dts[stream_index] = dts
    return findings


def demux_audio_tracks(source_file: str, audio_channels: List[int],
                       out_dir: str) -> Dict[int, str]:
    """Every audio stream to 16 kHz mono WAV, in ONE ffmpeg pass.

    Returns `{channel_ordinal: wav_path}`. The scout measured 13.1 s
    for three 82-minute tracks in one pass; one pass per stream would
    pay the full-file decode per stream.
    """
    os.makedirs(out_dir, exist_ok=True)
    command = ["ffmpeg", "-hide_banner", "-i", source_file]
    paths = {}
    for ordinal in audio_channels:
        wav_path = os.path.join(out_dir, f"track_CH{ordinal}.wav")
        command += ["-map", f"0:a:{ordinal - 1}", "-ar",
                    str(TRANSCRIPT_RATE_HZ), "-ac", "1", "-acodec",
                    "pcm_s16le", "-y", wav_path]
        paths[ordinal] = wav_path
    try:
        out = subprocess.run(command, capture_output=True,
                             encoding="utf-8", errors="replace",
                             timeout=1800, check=False)
    except FileNotFoundError:
        raise RuntimeError("ffmpeg not found. Install: brew install ffmpeg")
    if out.returncode != 0:
        raise RuntimeError(
            f"ffmpeg audio demux failed for {source_file}: "
            f"{(out.stderr or '')[-500:]}")
    for ordinal, wav_path in paths.items():
        if (not os.path.isfile(wav_path)
                or os.path.getsize(wav_path) == 0):
            raise RuntimeError(
                f"ffmpeg wrote no audio for {source_file} CH{ordinal}")
    return paths


def track_level_db(wav_path: str) -> float:
    """Mean level of a 16-bit mono WAV in dBFS, or -inf when silent."""
    with wave.open(wav_path, "rb") as wav:
        frames = wav.readframes(wav.getnframes())
        width = wav.getsampwidth()
    if not frames or width != 2:
        return float("-inf")
    import numpy as np

    # Vectorized mean-square over native 16-bit samples: the previous
    # pure-Python generator spent ~0.9 s on a 214 s program track
    # (13.7M samples). float64 holds each square exactly; the summed
    # mean can differ from the exact-integer sum by ~1 ulp, far below
    # the 0.1 dB the callers keep.
    samples = np.frombuffer(frames, dtype=np.int16)
    if samples.size == 0:
        return float("-inf")
    mean_square = float(np.mean(samples.astype(np.float64) ** 2))
    if mean_square <= 0:
        return float("-inf")
    return 10.0 * math.log10(mean_square / (32768.0 ** 2))


def select_program_track(levels_db: Dict[int, float],
                         declaration: Optional[int] = None
                         ) -> Tuple[Optional[int], dict]:
    """Which live track carries the program, and on what basis.

    Pure: measured levels in, decision out, so a test can drive it
    with no ffmpeg. `declaration` is the project's
    `source.program_stream` and always wins when it names a live
    track; a declaration naming a dead or absent track is refused
    rather than defaulted past. With no declaration the single live
    track is the program; several live tracks read as one mixed
    conversation (the scout measured all three live LC4932 tracks
    carrying both voices), so the loudest is transcribed and the
    basis says so with the levels as evidence. This never refuses a
    transcribable file the way the catalog's program selection does:
    an M1 that refuses on a close race would leave whole conversations
    unheard, and the mixed-track case IS the close race.

    Returns `(channel_or_None, selection)`; None means no live track.
    """
    live = {ch: lv for ch, lv in levels_db.items() if lv >= SILENCE_DB}
    evidence = {f"CH{ch}": round(lv, 1) for ch, lv in
                sorted(levels_db.items())}
    if declaration is not None:
        if declaration in live:
            return declaration, {"channel": declaration,
                                 "basis": "declared",
                                 "measured_levels_db": evidence}
        raise ValueError(
            f"project declares program stream CH{declaration}, which "
            f"is not a live track ({evidence or 'no levels measured'}). "
            f"Declare the track that carries the mix.")
    if not live:
        return None, {"channel": None, "basis": "no-live-track",
                      "measured_levels_db": evidence}
    if len(live) == 1:
        only = next(iter(live))
        return only, {"channel": only, "basis": "single",
                      "measured_levels_db": evidence}
    loudest = max(sorted(live), key=lambda ch: live[ch])
    return loudest, {"channel": loudest, "basis": "loudest-live",
                     "measured_levels_db": evidence}


def build_source_record(source_file: str, content_digest: str,
                        size_bytes: int, probe: dict,
                        levels_db: Dict[int, float],
                        selection: dict) -> dict:
    """The M0 document: everything cheaply known about one source."""
    streams = []
    for stream in probe.get("streams", []):
        entry = dict(stream)
        capture_provenance = entry.get("capture_metadata_provenance")
        if capture_provenance:
            entry["capture_metadata_provenance"] = {
                **capture_provenance, "source_digest": content_digest}
        streams.append(entry)
    file_integrity = dict(probe.get("file_integrity") or {
        "probe_status": "unavailable", "sample_scope": "unavailable"})
    file_integrity.update({
        "content_digest": content_digest,
        "size_bytes": size_bytes,
        "digest_method": "sha256_size_plus_first_and_last_mib",
        "integrity_scope": "bounded_probe_sample_not_full_decode",
    })
    return {
        "content_digest": content_digest,
        "size_bytes": size_bytes,
        "observed_paths": [os.path.abspath(source_file)],
        "duration_seconds": probe.get("duration_seconds"),
        "streams": streams,
        "packet_table": probe.get("packet_table", []),
        "frame_table": probe.get("frame_table", []),
        "frame_timing": probe.get("frame_timing", {
            "status": "unavailable", "sample_scope": "unavailable",
            "streams": []}),
        "timestamp_discontinuities": probe.get(
            "timestamp_discontinuities", []),
        "file_integrity": file_integrity,
        "video_streams": [s for s in probe.get("streams", [])
                          if s.get("codec_type") == "video"],
        "audio_streams": [s for s in probe.get("streams", [])
                          if s.get("codec_type") == "audio"],
        "measured_levels_db": selection.get("measured_levels_db", {}),
        "program_track": selection,
        # Reserved for the sampler lane: GOP length off the keyframe
        # cadence. Null until measured; never defaulted.
        "gop_frames": None,
    }


# ── M1: the whole-source transcript ────────────────────────────────


def utterances_from_aligned(aligned: dict, method: str) -> list:
    """Aligned seam segments into the region shape downstream reads.

    The same keys step 1.04's `_regions_from_segments` publishes
    (`start`, `end`, `text`, `words`, `confidence`, `method`), so an
    M1 utterance and a 1.04 speech region are interchangeable where
    the search reads them. Text and words are lowercased the way
    1.04's are. What this deliberately does NOT do is 1.04's onset
    snapping: that needs an onset pass over the audio, and the
    boundaries here are MFA's own. `confidence` is 0.0 - no arm
    publishes a segment confidence, the same default an absent key
    always carried, never a measurement.
    """
    regions = []
    for segment in aligned.get("segments") or []:
        text = str(segment.get("text") or "").strip()
        if not text:
            continue
        words = []
        for word in segment.get("words") or []:
            if "start" not in word or "end" not in word:
                continue
            try:
                start = round(float(word["start"]), 3)
                end = round(float(word["end"]), 3)
            except (TypeError, ValueError):
                continue
            token = str(word.get("word") or "").strip().lower()
            if not token:
                continue
            words.append({"word": token, "start": start, "end": end})
        if not words:
            vocalizations = nonverbal_vocalizations(
                text, float(segment.get("start", 0.0)),
                float(segment.get("end", segment.get("start", 0.0))))
            if vocalizations:
                regions.append({
                    "start": vocalizations[0]["start"],
                    "end": vocalizations[0]["end"],
                    "text": text.lower().strip(), "words": [],
                    "vocalizations": vocalizations,
                    "confidence": 0.0, "method": method,
                })
            continue
        region = {
            "start": words[0]["start"],
            "end": words[-1]["end"],
            "text": text.lower().strip(),
            "words": words,
            "confidence": 0.0,
            "method": method,
        }
        # VI-08 records are optional for legacy aligners and are passed
        # through only when the forced aligner actually emitted them.
        phonemes = segment.get("phonemes")
        syllables = segment.get("syllables")
        if phonemes:
            region["phonemes"] = phonemes
        if syllables:
            region["syllables"] = syllables
        vocalizations = nonverbal_vocalizations(
            text, region["start"], region["end"])
        if vocalizations:
            region["vocalizations"] = vocalizations
        regions.append(region)
    return regions


def transcribe_track(wav_path: str, label: str = "",
                     root: Optional[Path] = None) -> tuple:
    """One demuxed track through the reel path's transcription seam.

    `timeline_transcript.transcribe_audio`: voz words through MFA -
    the same instruments that time the reels, so memory words and
    timeline words agree. Through the canonical measurement
    (`library/tools/transcript_measurement.py`): samples step 1.04
    already heard - in its batch, before this lane runs in
    `ren analyze` - are served, not heard a second time. Imported
    lazily: readers of a built memory (notably `ren search-index`)
    must never pay for the transcriber.
    """
    from library.tools import transcript_measurement

    return transcript_measurement.transcribe(
        wav_path, label=label or os.path.basename(wav_path), root=root)


def build_transcript_document(utterances: list, record: dict,
                              content_digest: str, source_file: str,
                              selection: dict) -> dict:
    """The M1 document: the words, and the account of how they were heard."""
    arm = (record or {}).get("arm")
    aligner = (record or {}).get("aligner")
    detected = (record or {}).get("language") or {}
    words = sum(len(u.get("words") or []) for u in utterances)
    speech_seconds = round(sum(u["end"] - u["start"] for u in utterances), 3)
    return {
        "content_digest": content_digest,
        "source_file": os.path.abspath(source_file),
        "status": M1_STATUS_TRANSCRIBED,
        "utterance_cut": "hybrid-windows",
        "program_track": selection,
        "utterances": utterances,
        "utterance_count": len(utterances),
        "word_count": words,
        "speech_seconds": speech_seconds,
        "instrument": {
            "arm": arm,
            "aligner": aligner,
            "detected_language": detected.get("language") if isinstance(
                detected, dict) else None,
            "transcriber": (record or {}).get("transcriber"),
            "alignment_window": (record or {}).get("alignment_window"),
        },
    }


def build_untranscribed_document(content_digest: str, source_file: str,
                                 selection: dict, reason: str,
                                 detail: str = "") -> dict:
    """The account a source nothing could transcribe carries.

    Written, not omitted: an absent M1 means "not yet built" and is
    rebuilt on the next run; a recorded refusal means "measured" and
    is served as the answer.
    """
    return {
        "content_digest": content_digest,
        "source_file": os.path.abspath(source_file),
        "status": reason,
        "status_detail": detail,
        "utterance_cut": None,
        "program_track": selection,
        "utterances": [],
        "utterance_count": 0,
        "word_count": 0,
        "speech_seconds": 0.0,
        "instrument": {"arm": "none", "aligner": None},
    }


# ── Writing and reading the store ──────────────────────────────────


def write_json(path: Path, payload: dict) -> None:
    """Atomic write: temp file plus rename, so a killed build never
    leaves a half-written slot that reads as fresh.

    The temp name is unique per call (`mkstemp`), not per process:
    two clips cut from one camera file share a digest slot, and the
    read-only lanes index them at once - a pid-keyed temp meant one
    thread's rename moved the shared file away while the other was
    still writing, so the other's rename raised `FileNotFoundError`
    (measured: 95 failures in 200 same-slot writes across 8 threads).
    """
    from library.tools.cache_lifecycle import cache_file_lease

    payload = _with_evidence_envelope(path, payload)
    validate_evidence_envelope(payload["evidence_envelope"])
    path.parent.mkdir(parents=True, exist_ok=True)
    with cache_file_lease(path, exclusive=True):
        fd, staged_name = tempfile.mkstemp(
            dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, indent=1)
            os.replace(staged_name, path)
        except BaseException:
            try:
                os.unlink(staged_name)
            except OSError:
                pass
            raise
    digest = path.parent.name
    if (len(digest) == 64 and all(char in "0123456789abcdef" for char in digest)):
        source_file = payload.get("source_file")
        source_available = False
        if source_file:
            try:
                source_available = (
                    footage_identity.fingerprint(source_file)["content_digest"]
                    == digest)
            except OSError:
                source_available = False
        kind = _source_memory_cache_kind(path)
        from library.tools.cache_lifecycle import register_cache_artifact_from_environment
        root = memory_root()
        try:
            producer_key = path.resolve().relative_to(root.resolve()).as_posix()
        except ValueError:
            producer_key = path.resolve().as_posix()
        register_cache_artifact_from_environment(
            path, kind=kind, producer_key=producer_key,
            regeneration_method=f"source-memory producer: {path.name}",
            source_available=source_available,
            compatible=payload.get("content_digest", digest) == digest)


def _coalesce_coverage_intervals(intervals: list) -> list:
    """Merge overlapping or near-adjacent coverage spans, fail-closed.

    Point samples harvested from timestamps arrive as 1 ms slivers; spans
    less than a second apart merge into the continuous pass that produced
    them. Sparse samples stay separate, and the tail beyond 1024 spans is
    dropped: truncation understates coverage, so an absence check abstains
    rather than reasoning from a partial basis.
    """
    def _well_formed(row: object) -> bool:
        return (isinstance(row, dict)
                and isinstance(row.get("start_s"), (int, float))
                and isinstance(row.get("end_s"), (int, float))
                and row["end_s"] > row["start_s"])
    spans = sorted(
        (float(row["start_s"]), float(row["end_s"]))
        for row in intervals if _well_formed(row))
    malformed = [row for row in intervals if not _well_formed(row)]
    merged: list = []
    for start, end in spans:
        if merged and start <= merged[-1]["end_s"] + 1.0:
            merged[-1]["end_s"] = max(merged[-1]["end_s"], end)
        else:
            merged.append({"start_s": start, "end_s": end})
    # Malformed rows pass through so the caller's validation still
    # refuses them; truncation only ever drops well-formed spans.
    return (merged[:1024] + malformed)


def _with_evidence_envelope(path: Path, payload: dict) -> dict:
    """Stamp every JSON slot write with an additive evidence basis.

    Legacy payload fields remain untouched. Sparse lanes default to sampled;
    callers must provide explicit complete coverage to authorize absence.
    """
    if "evidence_envelope" in payload:
        return payload
    digest = payload.get("content_digest") or path.parent.name
    slot = path.name
    instrument = payload.get("instrument")
    producer = payload.get("producer")
    if not producer and isinstance(instrument, dict):
        producer = instrument.get("producer") or instrument.get("method")
    producer = producer or payload.get("method") or f"source_memory:{slot}"
    producer_version = (payload.get("producer_version")
                        or (instrument.get("version") if isinstance(instrument, dict) else None)
                        or payload.get("version") or "1")
    sample_resolution = payload.get("sample_resolution")
    if sample_resolution is None:
        sample_resolution = payload.get("sample_size")
    if sample_resolution is None:
        resolution = payload.get("resolution")
        if (isinstance(resolution, list) and len(resolution) == 2
                and all(isinstance(value, int) for value in resolution)):
            sample_resolution = resolution
        width, height = payload.get("width"), payload.get("height")
        if isinstance(width, int) and isinstance(height, int):
            sample_resolution = [width, height]
    if sample_resolution is not None and (
            not isinstance(sample_resolution, list) or len(sample_resolution) != 2
            or any(not isinstance(value, int) or value <= 0
                   for value in sample_resolution)):
        raise ValueError("sample_resolution must be null or positive [width, height]")
    intervals = payload.get("coverage_intervals")
    if intervals is None:
        intervals = []
        for key in ("coverage", "scene_coverage", "sound_coverage",
                    "music_coverage", "window_coverage"):
            value = payload.get(key)
            candidates = value if isinstance(value, list) else [value]
            for row in candidates:
                if not isinstance(row, dict):
                    continue
                start = row.get("start", row.get("start_seconds", row.get("start_s")))
                end = row.get("end", row.get("end_seconds", row.get("end_s")))
                if isinstance(start, (int, float)) and isinstance(end, (int, float)) and end > start:
                    intervals.append({"start_s": float(start), "end_s": float(end)})
        if not intervals:
            pending = [payload]
            while pending and len(intervals) < 2048:
                item = pending.pop()
                if isinstance(item, dict):
                    if "start" in item and "end" in item:
                        start, end = item["start"], item["end"]
                        if (isinstance(start, (int, float))
                                and isinstance(end, (int, float)) and end > start):
                            intervals.append({"start_s": float(start),
                                              "end_s": float(end)})
                    else:
                        point = next((item[key] for key in
                                      ("t", "time", "timestamp", "time_seconds")
                                      if isinstance(item.get(key), (int, float))), None)
                        if point is not None and point >= 0:
                            intervals.append({"start_s": float(point),
                                              "end_s": float(point) + 0.001})
                    pending.extend(item.values())
                elif isinstance(item, list):
                    pending.extend(item)
        intervals = list({(round(row["start_s"], 6), round(row["end_s"], 6)): row
                          for row in intervals}.values())
    if isinstance(intervals, list):
        intervals = _coalesce_coverage_intervals(intervals)
    if not isinstance(intervals, list):
        raise ValueError("coverage_intervals must be a list")
    for interval in intervals:
        if (not isinstance(interval, dict)
                or not isinstance(interval.get("start_s"), (int, float))
                or not isinstance(interval.get("end_s"), (int, float))
                or interval["end_s"] <= interval["start_s"]):
            raise ValueError("coverage intervals require increasing start_s/end_s")
    status = str(payload.get("status") or "").lower()
    coverage_status = payload.get("coverage_status")
    if coverage_status is None:
        coverage_status = ("failed" if "fail" in status else
                           "unavailable" if any(word in status for word in
                                                ("unavailable", "unmeasured", "not_analyzed")) else
                           "complete" if intervals and payload.get("coverage_complete") is True else
                           "sampled" if intervals else "unknown")
    verified_status = payload.get("verified_status")
    if verified_status is None:
        verified_status = "verified" if payload.get("verified") is True else "unverified"
    stream_groups = []
    source_record = _load_json(path.parent / SLOT_SOURCE) if path.name != SLOT_SOURCE else None
    if isinstance(source_record, dict):
        if slot in {SLOT_TRANSCRIPT, SLOT_SPEAKERS, SLOT_SOUND, SLOT_AUDIO_QUALITY}:
            stream_groups = source_record.get("audio_streams") or []
        elif slot not in {SLOT_CLOCK}:
            stream_groups = source_record.get("video_streams") or []
        if not stream_groups:
            stream_groups = (source_record.get("video_streams") or
                             source_record.get("audio_streams") or [])
    stream_index = (stream_groups[0].get("index", 0)
                    if stream_groups and isinstance(stream_groups[0], dict) else 0)
    stream_id = payload.get("source_stream_id")
    if not stream_id:
        stream_id = (f"stream-{digest}" if stream_index == 0
                     else f"stream-{digest}-{stream_index}")
    default_methods = {
        SLOT_SOURCE: "ffprobe-stream-probe",
        SLOT_TRANSCRIPT: "whole-source-audio-transcription",
        SLOT_SPEAKERS: "voice-track-diarization",
        SLOT_FRAMES_INDEX: "decode-keyframes-skip-nonkey",
        SLOT_PERSONS: "apple-vision-frame-measurement",
        SLOT_IDENTITY: "face-voice-track-association",
        SLOT_SCENES: "vlm-scene-pass-and-ffmpeg-boundaries",
        SLOT_SOUND: "environmental-audio-event-classification",
        SLOT_CLOCK: "multicamera-clock-estimation",
        SLOT_EVENTS: "derived-event-spans",
        SLOT_WORLD_MODEL: "joined-source-memory-view",
        SLOT_RELATIONSHIPS: "relationship-candidate-detection",
        SLOT_AUDIO_QUALITY: "audio-quality-measurement",
        SLOT_IMAGE_DEFECTS: "sampled-image-defect-measurement",
    }
    envelope = {
        "record_version": 1,
        "source_digest": digest,
        "producer": str(producer),
        "producer_version": str(producer_version),
        "source_stream_id": stream_id,
        "sampling_method": str(payload.get("sampling_method") or payload.get("method") or
                                (instrument.get("method") if isinstance(instrument, dict) else None) or
                                default_methods.get(slot, f"source-memory:{slot}")),
        "sample_resolution": sample_resolution,
        "coverage_intervals": intervals,
        "coverage_status": coverage_status,
        "confidence_semantics": payload.get("confidence_semantics", "producer_defined"),
        "preprocessing": payload.get("preprocessing", {}),
        "sampling_policy": payload.get("sampling_policy", {}),
        "verified_status": verified_status,
        "inputs": payload.get("inputs", []),
    }
    return {**payload, "evidence_envelope": envelope}


def validate_evidence_envelope(envelope: dict) -> None:
    required = ("record_version", "source_digest", "producer", "producer_version",
                "source_stream_id", "sampling_method", "sample_resolution",
                "coverage_intervals", "coverage_status", "confidence_semantics",
                "verified_status", "inputs")
    missing = [key for key in required if key not in envelope]
    if missing:
        raise ValueError(f"Source Memory evidence envelope missing {missing}")
    if not all(isinstance(envelope[key], str) and envelope[key]
               for key in ("source_digest", "producer", "producer_version",
                           "source_stream_id", "sampling_method", "coverage_status",
                           "confidence_semantics", "verified_status")):
        raise ValueError("Source Memory evidence envelope string fields must be non-empty")
    if not isinstance(envelope["coverage_intervals"], list) or not isinstance(envelope["inputs"], list):
        raise ValueError("Source Memory coverage_intervals and inputs must be lists")
    for key in ("preprocessing", "sampling_policy"):
        if key in envelope and not isinstance(envelope[key], dict):
            raise ValueError(f"Source Memory {key} must be an object")
    if envelope["record_version"] != 1:
        raise ValueError("unsupported Source Memory evidence envelope version")
    resolution = envelope["sample_resolution"]
    if resolution is not None and (
            not isinstance(resolution, list) or len(resolution) != 2
            or any(not isinstance(value, int) or value <= 0 for value in resolution)):
        raise ValueError("sample_resolution must be null or positive [width, height]")
    if envelope["coverage_status"] not in {
            "complete", "sampled", "inconclusive", "not_yet_analysed",
            "unavailable", "failed", "unknown"}:
        raise ValueError("unknown Source Memory coverage_status")
    for interval in envelope["coverage_intervals"]:
        if (not isinstance(interval, dict)
                or not isinstance(interval.get("start_s"), (int, float))
                or not isinstance(interval.get("end_s"), (int, float))
                or interval["start_s"] < 0
                or interval["end_s"] <= interval["start_s"]):
            raise ValueError("coverage interval must have non-negative increasing seconds")


def read_evidence_envelope(content_digest: str, lane: str,
                           root: Optional[Path] = None) -> Optional[dict]:
    """Read a slot's envelope; absent envelopes remain legacy/unknown."""
    slot = SLOT_BY_LANE.get(lane)
    if slot is None:
        return None
    doc = _load_json(source_dir(content_digest, root) / slot)
    envelope = doc.get("evidence_envelope") if isinstance(doc, dict) else None
    return envelope if isinstance(envelope, dict) else None


def read_m0(content_digest: str,
            root: Optional[Path] = None) -> Optional[dict]:
    """The M0 record for a digest, or None when never built."""
    path = source_dir(content_digest, root) / SLOT_SOURCE
    doc = _load_json(path)
    return doc if isinstance(doc, dict) else None


def is_fresh(record: Optional[dict], source_file: str) -> bool:
    """Whether a record still describes the file on disk.

    Content, not mtime: the digest and size the record was built from
    against a live fingerprint now. A missing file reads as not fresh -
    freshness is a claim about bytes, and there are none to check.
    """
    if not isinstance(record, dict):
        return False
    try:
        live = footage_identity.fingerprint(source_file)
    except OSError:
        return False
    return (record.get("content_digest") == live["content_digest"]
            and record.get("size_bytes") == live["size_bytes"])


def read_m1(content_digest: str,
            root: Optional[Path] = None) -> Tuple[Optional[dict], str]:
    """A digest's M1 transcript, freshness-checked.

    Returns `(doc, status)` with status `fresh`, `stale`, `missing`
    or `untranscribed-<reason>`: a stale transcript is never served
    as current, and a recorded refusal is served as the answer it is
    rather than rebuilt endlessly.
    """
    path = source_dir(content_digest, root) / SLOT_TRANSCRIPT
    doc = _load_json(path)
    if not isinstance(doc, dict):
        return None, "missing"
    if doc.get("content_digest") != content_digest:
        return None, "stale"
    if doc.get("status") != M1_STATUS_TRANSCRIBED:
        return doc, str(doc.get("status") or "untranscribed")
    return doc, "fresh"


def read_m1_for_clip(project_folder: str, clip: dict,
                     recorded: Optional[dict] = None,
                     root: Optional[Path] = None) -> Tuple[Optional[dict], str]:
    """A catalog clip's M1 transcript, or None and why not.

    The digest resolves live off disk when the media is present, off
    the runner's record when it is not - so search works with the
    footage offline. A digest the disk disagrees with (live file
    replaced since the run) reads as stale rather than served.
    """
    digest, basis = digest_for_clip(project_folder, clip, recorded)
    if digest is None:
        return None, "absent"
    doc, status = read_m1(digest, root)
    if status == "fresh" and basis == "live":
        path = clip.get("source_file") or clip.get("path") or ""
        m0 = read_m0(digest, root)
        if not is_fresh(m0, path):
            return None, "stale"
    return doc, status


def read_m2(content_digest: str,
            root: Optional[Path] = None) -> Optional[dict]:
    """The M2 frame-sample index for a digest, or None when never built."""
    path = source_dir(content_digest, root) / SLOT_FRAMES_INDEX
    doc = _load_json(path)
    return doc if isinstance(doc, dict) else None


def read_m2_for_clip(project_folder: str, clip: dict,
                     recorded: Optional[dict] = None,
                     root: Optional[Path] = None) -> Tuple[Optional[dict], str]:
    """A catalog clip's M2 frame sample, or None and why not.

    Same freshness shape as `read_m1_for_clip`: a recorded digest
    serves a reader with the footage offline, but a live digest the
    sample disagrees with reads as stale rather than served.
    """
    digest, basis = digest_for_clip(project_folder, clip, recorded)
    if digest is None:
        return None, "absent"
    doc = read_m2(digest, root)
    if doc is None:
        return None, "missing"
    if not m2_frames_present(digest, doc, root):
        return None, "stale"
    if basis == "live":
        path = clip.get("source_file") or clip.get("path") or ""
        if not is_fresh(doc, path):
            return None, "stale"
    return doc, "fresh"


def frame_abspath(content_digest: str, frame: dict,
                  root: Optional[Path] = None) -> str:
    """An M2 frame record's thumbnail as an absolute path.

    `frame["file"]` is stored relative to the source's memory
    directory - the same shape the project-side consumer resolves
    against the per-source root, never against its own index dir
    (the memory is referenced, never copied, AGENTS.md's per-source
    memory contract).
    """
    return str(source_dir(content_digest, root) / frame["file"])


def m2_frames_present(content_digest: str, record: dict,
                      root: Optional[Path] = None) -> bool:
    """Whether an M2 index still has its shared thumbnail directory.

    The JSON index alone is not a complete M2 cache entry: its frame paths
    are consumed by both the frame index and the per-frame analysis lanes.
    A killed or partially cleaned run can leave the index behind after the
    shared ``frames/`` directory is gone, so that record must be rebuilt.
    """
    frames_dir = source_dir(content_digest, root) / SLOT_FRAMES_DIR
    if not frames_dir.is_dir():
        return False
    frames = record.get("frames")
    if not isinstance(frames, list):
        return False
    return all(
        isinstance(frame, dict)
        and isinstance(frame.get("file"), str)
        and Path(frame_abspath(content_digest, frame, root)).is_file()
        for frame in frames)


# ── M1b / M4 / M5: the reserved lanes, now written ──────────────────
# Each writer serializes a measurement its producer already made - no
# writer here measures anything. The producers are `person_entity.py`
# (M1b, from the same voice measurement M3b makes), `vision_pipeline_v3.py`
# (M4, from the VLM scene pass plus the temporal index's ffmpeg
# boundaries) and `step_1_04_temporal_index/step.py` (M5, from the PANNs
# events the temporal index already measures).


def write_speakers(content_digest: str, source_file: str, status: str,
                   speakers: list, instrument: Optional[dict] = None,
                   root: Optional[Path] = None) -> None:
    """M1b `speakers.json`: diarized turns and their voice embeddings.

    `speakers` is the voice-track list `person_entity.measure_voice_tracks`
    already returns - `[{track_id, embedding, spans}]` - written into the
    slot the diarization lane reserved. `instrument` is the voice half of
    the M3b instrument (which diarizer answered, and why not when not).
    """
    write_json(source_dir(content_digest, root) / SLOT_SPEAKERS, {
        "content_digest": content_digest,
        "source_file": os.path.abspath(source_file),
        "status": status,
        "speakers": speakers,
        "instrument": instrument or {},
    })


def read_speakers(content_digest: str,
                  root: Optional[Path] = None) -> Optional[dict]:
    """The M1b speaker lane for a digest, or None when never written."""
    doc = _load_json(source_dir(content_digest, root) / SLOT_SPEAKERS)
    return doc if isinstance(doc, dict) else None


def write_scenes(content_digest: str, source_file: str, status: str,
                 scenes: list, scene_boundaries: Optional[list] = None,
                 scene_coverage: Optional[dict] = None,
                 instrument: Optional[dict] = None,
                 root: Optional[Path] = None) -> None:
    """M4 `scenes.json`: the VLM scene pass plus ffmpeg scene boundaries.

    `scenes` is the profile's normalized scene segments
    (`[{start, end, location, type, lighting, notable_features}]`),
    `scene_boundaries` the temporal index's ffmpeg cut points - both
    already measured, serialized into the slot the scene lane reserved.
    """
    write_json(source_dir(content_digest, root) / SLOT_SCENES, {
        "content_digest": content_digest,
        "source_file": os.path.abspath(source_file),
        "status": status,
        "scenes": scenes,
        "scene_boundaries": scene_boundaries or [],
        "scene_coverage": scene_coverage,
        "instrument": instrument or {},
    })


def merge_scene_window(content_digest: str, source_file: str, *,
                       start_s: float, end_s: float, scenes: list,
                       producer_version: str, source_digest: str,
                       measured: bool,
                       model_provenance: dict | None = None,
                       producer_project_id: str | None = None,
                       producer_project_root: str | None = None,
                       root: Optional[Path] = None) -> None:
    """Merge one measured M4 window without replacing source-wide records."""
    path = source_dir(content_digest, root) / SLOT_SCENES
    existing = _load_json(path)
    if not isinstance(existing, dict) or existing.get("content_digest") != content_digest:
        existing = {"content_digest": content_digest,
                    "source_file": os.path.abspath(source_file),
                    "status": "measured", "scenes": [],
                    "scene_boundaries": [], "scene_coverage": None,
                    "instrument": {}}
    start_s, end_s = round(float(start_s), 3), round(float(end_s), 3)
    old_scenes = []
    for row in existing.get("scenes", []):
        if not isinstance(row, dict):
            continue
        try:
            old_start, old_end = float(row["start"]), float(row["end"])
        except (KeyError, TypeError, ValueError):
            old_scenes.append(row)
            continue
        if old_start < start_s or old_end > end_s:
            if old_start < start_s:
                old_scenes.append({**row, "end": min(old_end, start_s)})
            if old_end > end_s:
                old_scenes.append({**row, "start": max(old_start, end_s)})
    incoming = [dict(row) for row in scenes]
    existing["scenes"] = sorted(old_scenes + incoming,
                                key=lambda row: (float(row.get("start", 0)),
                                                 float(row.get("end", 0))))
    coverage = list(existing.get("window_coverage") or [])
    item = {"start_s": start_s, "end_s": end_s,
            "status": "complete" if measured else "incomplete",
            "producer_version": producer_version,
            "source_digest": source_digest}
    if model_provenance is not None:
        required_provenance = {
            "implementation_id", "checkpoint_revision", "license_context",
            "workload_purpose", "output_reuse", "generated_at",
        }
        if (model_provenance.get("format") !=
                "model_evidence_provenance/1"
                or not model_provenance.get("evidence_ids")
                or not required_provenance <= model_provenance.keys()):
            raise ValueError("M4 model provenance is missing required contract fields")
        evidence_id = (f"m4-window:{source_digest}:"
                       f"{round(start_s * 1000)}:{round(end_s * 1000)}")
        if evidence_id not in model_provenance["evidence_ids"]:
            raise ValueError("M4 model provenance does not bind this window")
        item["model_provenance"] = model_provenance
        if not producer_project_id or not producer_project_root:
            raise ValueError("model-derived M4 evidence needs its producing project identity")
        item["producer_project_id"] = producer_project_id
        item["producer_project_root"] = producer_project_root
    coverage = [row for row in coverage if not (
        row.get("start_s") == start_s and row.get("end_s") == end_s)]
    coverage.append(item)
    existing["window_coverage"] = sorted(
        coverage, key=lambda row: (row["start_s"], row["end_s"]))
    existing["status"] = "measured"
    instrument = dict(existing.get("instrument") or {})
    instrument["scene_pass"] = M4_SCENE_PASS_METHOD
    instrument["window_producer_version"] = producer_version
    existing["instrument"] = instrument
    write_json(path, existing)


def read_scenes(content_digest: str,
                root: Optional[Path] = None) -> Optional[dict]:
    """The M4 scene lane for a digest, or None when never written."""
    doc = _load_json(source_dir(content_digest, root) / SLOT_SCENES)
    if not isinstance(doc, dict):
        return None
    hierarchy = _load_json(source_dir(content_digest, root) / SLOT_SCENE_CINEMATOGRAPHY)
    if (isinstance(hierarchy, dict)
            and hierarchy.get("content_digest") == content_digest):
        doc["scene_cinematography"] = hierarchy["scene_cinematography"]
    return doc


def write_scene_cinematography(content_digest: str, hierarchy: dict,
                               root: Optional[Path] = None) -> None:
    """Write VI-09 beside M4 so parallel producers cannot overwrite each other."""
    write_json(source_dir(content_digest, root) / SLOT_SCENE_CINEMATOGRAPHY, {
        "content_digest": content_digest,
        "scene_cinematography": hierarchy,
    })


def write_sound(content_digest: str, source_file: str, status: str,
                sound_events: list, method: str,
                root: Optional[Path] = None,
                audio_classification: dict = None,
                duration_seconds: float = None) -> None:
    """M5 `sound.json`: measured PANNs sound-event labels.

    `sound_events` is the `[{label, start, end, confidence}]` list the
    temporal index already measures, `method` the producer's name or
    `unmeasured: <reason>`. An unmeasured record is written only when no
    measured record exists for the digest: two clips of one camera file
    share a memory, and a source whose events were measured must not be
    demoted to empty because a later clip ran without the checkpoint.

    `audio_classification` (Gap 12) is the beyond-PANNs measurement:
    absolute speech level (dBFS), music genre, ambience type. It is
    stored beside the PANNs events in the same slot.
    """
    path = source_dir(content_digest, root) / SLOT_SOUND
    measured = not str(method or "").startswith("unmeasured")
    if not measured:
        existing = _load_json(path)
        if (isinstance(existing, dict)
                and not str(existing.get("method") or "").startswith(
                    "unmeasured")):
            return
    existing = _load_json(path)
    record = {
        "content_digest": content_digest,
        "source_file": os.path.abspath(source_file),
        "status": status,
        "sound_events": sound_events,
        "method": method,
    }
    if audio_classification is not None:
        record["audio_classification"] = audio_classification
    elif isinstance(existing, dict) and "audio_classification" in existing:
        record["audio_classification"] = existing["audio_classification"]
    if isinstance(existing, dict):
        for key in ("music_interval_evidence", "music_coverage",
                    "music_method", "music_status"):
            if key in existing:
                record[key] = existing[key]
    intervals, coverage = _environmental_sound_intervals(
        sound_events, method, duration_seconds)
    record["sound_interval_evidence"] = intervals
    record["sound_coverage"] = coverage
    write_json(path, record)


def _environmental_sound_intervals(events: list, method: str,
                                   duration_seconds: float = None
                                   ) -> tuple[list, dict]:
    """M5 intervals retain the PANNs method, resolution and source coverage."""
    measured = bool(method and not method.startswith("unmeasured"))
    try:
        duration = (max(0.0, float(duration_seconds))
                    if duration_seconds is not None else None)
    except (TypeError, ValueError):
        duration = None
    coverage = {
        "kind": "complete" if measured and duration is not None else "unavailable",
        "start_seconds": 0.0 if measured and duration is not None else None,
        "end_seconds": duration,
        "duration_seconds": duration,
    }
    if not measured:
        return [], coverage
    intervals = []
    for event in events or []:
        if not isinstance(event, dict):
            continue
        try:
            start, end = float(event["start"]), float(event["end"])
            label = str(event["label"])
        except (KeyError, TypeError, ValueError):
            continue
        if start < 0 or end <= start or not label:
            continue
        if duration is not None and end > duration + 0.01:
            continue
        row = {
            "record_type": "interval",
            "domain": "environmental_audio",
            "kind": "environmental_sound_event",
            "label": label,
            "interval_semantics": "span",
            "start_seconds": round(start, 3),
            "end_seconds": round(end, 3),
            "evidence_type": "measurement",
            "certainty_state": "measured",
            "method": method,
            "uncertainty": {
                "kind": "resolution_bound",
                "boundary_seconds": 0.01,
            },
            "coverage": dict(coverage),
        }
        confidence = event.get("confidence")
        if isinstance(confidence, (int, float)):
            row["confidence"] = float(confidence)
        intervals.append(row)
    return intervals, coverage


def write_music_evidence(content_digest: str, source_file: str,
                         intervals: list, coverage: dict, method: str,
                         status: str = "measured",
                         root: Optional[Path] = None) -> None:
    """Add the step 2.06 interval lane to M5 without replacing sound data."""
    path = source_dir(content_digest, root) / SLOT_SOUND
    record = _load_json(path)
    if not isinstance(record, dict):
        record = {
            "content_digest": content_digest,
            "source_file": os.path.abspath(source_file),
            "status": status,
            "sound_events": [],
            "method": "unmeasured: environmental events not analyzed",
        }
    record["music_interval_evidence"] = intervals
    record["music_coverage"] = coverage
    record["music_method"] = method
    record["music_status"] = status
    write_json(path, record)


def read_sound(content_digest: str,
               root: Optional[Path] = None) -> Optional[dict]:
    """The M5 sound lane for a digest, or None when never written."""
    doc = _load_json(source_dir(content_digest, root) / SLOT_SOUND)
    return doc if isinstance(doc, dict) else None


def write_audio_quality(content_digest: str, record: dict,
                        root: Optional[Path] = None) -> None:
    """Write M11 deterministic audio measurements for one source."""
    if record["content_digest"] != content_digest:
        raise ValueError("audio quality digest does not match its slot")
    write_json(source_dir(content_digest, root) / SLOT_AUDIO_QUALITY, record)


def read_audio_quality(content_digest: str,
                       root: Optional[Path] = None) -> Optional[dict]:
    """M11 measurements, or None when audio quality has not been measured."""
    doc = _load_json(source_dir(content_digest, root) / SLOT_AUDIO_QUALITY)
    return doc if isinstance(doc, dict) else None


def write_image_defects(content_digest: str, record: dict,
                        root: Optional[Path] = None) -> None:
    """Write VI-03 sampled image defect measurements for one source."""
    if record["content_digest"] != content_digest:
        raise ValueError("image defects digest does not match its slot")
    write_json(source_dir(content_digest, root) / SLOT_IMAGE_DEFECTS, record)


def read_image_defects(content_digest: str,
                       root: Optional[Path] = None) -> Optional[dict]:
    """VI-03 measurements, or None when image quality was not measured."""
    doc = _load_json(source_dir(content_digest, root) / SLOT_IMAGE_DEFECTS)
    return doc if isinstance(doc, dict) else None


def write_world_model(content_digest: str, record: dict,
                      root: Optional[Path] = None) -> None:
    """M9 `world_model.json`: a derived per-second compatibility view.

    Source intervals and samples remain authoritative in their own slots
    and in the Project Model. `record` is the lookup view `world_model.py`
    joins from M0-M8 - bins do not define semantic boundaries. Written
    atomically like every other slot.
    """
    write_json(source_dir(content_digest, root) / SLOT_WORLD_MODEL, record)


def read_world_model(content_digest: str,
                     root: Optional[Path] = None) -> Optional[dict]:
    """The M9 world model for a digest, or None when never built."""
    doc = _load_json(source_dir(content_digest, root) / SLOT_WORLD_MODEL)
    return doc if isinstance(doc, dict) else None


def read_relationships(content_digest: str,
                       root: Optional[Path] = None) -> Optional[dict]:
    """The M10 relationship candidates for a digest, or None when never
    built."""
    doc = _load_json(source_dir(content_digest, root) / SLOT_RELATIONSHIPS)
    return doc if isinstance(doc, dict) else None


# ── The project-side fingerprint (for the search index) ────────────


def memory_fingerprint(project_folder: str,
                       root: Optional[Path] = None) -> dict:
    """What the project's sources remember, as a digest.

    Per clip in catalog order: the digest the memory is keyed by and
    the M1 status behind it. `footage_segments.ingest_fingerprint`
    mixes this in, so `ren search-index` reports stale the moment a
    transcript lands, a source is replaced, or a refusal is recorded -
    reusing the index's own staleness check rather than inventing one.
    Reads only: digests resolve without the media present.
    """
    recorded = load_recorded_fingerprints(project_folder)
    entries = []
    for clip in load_catalog(project_folder):
        digest, basis = digest_for_clip(project_folder, clip, recorded)
        status = "absent"
        if digest is not None:
            _, status = read_m1(digest, root)
        entries.append({"clip_id": clip.get("clip_id"),
                        "content_digest": digest,
                        "digest_basis": basis,
                        "m1": status})
    blob = json.dumps(entries, sort_keys=True).encode("utf-8")
    return {
        "digest": hashlib.sha256(blob).hexdigest(),
        "sources": len(entries),
        "transcribed": sum(1 for e in entries if e["m1"] == "fresh"),
        "entries": entries,
    }


# ── Building M0 + M1 ───────────────────────────────────────────────


def build_source(source_file: str, declaration: Optional[int] = None,
                 root: Optional[Path] = None,
                 scratch_parent: Optional[str] = None) -> dict:
    """M0 + M1 for one source file. Returns the per-file account.

    Heavy: a full-file demux, a voz hearing and an MFA alignment.
    Callers run this under the heavy-work lock; media is read-only.
    An already-fresh transcript is reused, not re-transcribed, and
    says so (`reused: true`).
    """
    started = time.perf_counter()
    timings: Dict[str, float] = {}
    digest = footage_identity.fingerprint(source_file)["content_digest"]
    target = source_dir(digest, root)

    existing, status = read_m1(digest, root)
    if status == "fresh" and is_fresh(read_m0(digest, root), source_file):
        return {
            "source_file": os.path.abspath(source_file),
            "content_digest": digest,
            "reused": True,
            "m1_status": status,
            "utterances": existing.get("utterance_count", 0),
            "words": existing.get("word_count", 0),
            "timings": {"total": round(time.perf_counter() - started, 1)},
        }

    # The probe, the per-track levels, the program decision and the
    # program track as 16 kHz PCM are source primitives: measured once
    # per content digest and shared with step 1.04 and the identity
    # lane (`library/tools/source_primitives.py`).
    from library.tools import hybrid_transcription, source_primitives

    primitives = source_primitives.ensure(source_file, declaration, root,
                                          scratch_parent)
    timings.update(primitives["timings"])
    timings.pop("total", None)
    selection = primitives["m0"]["program_track"]

    def untranscribed(status: str, detail: str) -> dict:
        transcript_path = target / SLOT_TRANSCRIPT
        write_json(transcript_path, build_untranscribed_document(
            digest, source_file, selection, status, detail))
        _record_source_memory_regeneration(
            transcript_path, time.perf_counter() - started)
        timings["total"] = round(time.perf_counter() - started, 1)
        return {"source_file": os.path.abspath(source_file),
                "content_digest": digest, "reused": False,
                "m1_status": status, "utterances": 0, "words": 0,
                "timings": timings}

    if primitives["program_wav"] is None:
        basis = selection.get("basis")
        if basis == source_primitives.NO_AUDIO:
            return untranscribed("untranscribed-no-audio",
                                 "ffprobe reports no audio stream on this file")
        if basis == source_primitives.NO_DECODABLE_AUDIO:
            codecs = ", ".join(
                f"CH{s['channel']} {s['codec']}" for s in
                primitives["m0"].get("undecodable_audio_streams") or [])
            return untranscribed("untranscribed-no-decodable-audio",
                                 f"ffmpeg here has no decoder for {codecs}")
        if basis == source_primitives.DECLARATION_REFUSED:
            return untranscribed("untranscribed-declaration-refused",
                                 selection.get("refusal", ""))
        return untranscribed("untranscribed-silent",
                             "every audio track measures below "
                             f"{SILENCE_DB} dB")

    channel = selection["channel"]
    t0 = time.perf_counter()
    try:
        aligned, record = transcribe_track(
            primitives["program_wav"],
            label=(f"{os.path.basename(source_file)}:CH{channel}"),
            root=root)
    except hybrid_transcription.FallbackRequired as fell_back:
        timings["transcribe"] = round(time.perf_counter() - t0, 1)
        reason = fell_back.reason
        if reason in (hybrid_transcription.TRANSCRIBER_REFUSED,
                      hybrid_transcription.HEARD_NOTHING):
            return untranscribed("untranscribed-silent", fell_back.detail)
        return untranscribed(f"untranscribed-{reason}", fell_back.detail)
    timings["transcribe"] = round(time.perf_counter() - t0, 1)

    method = f"hybrid-{(record or {}).get('aligner', 'mfa')}"
    utterances = utterances_from_aligned(aligned, method)
    m1 = build_transcript_document(utterances, record, digest,
                                   source_file, selection)
    transcript_path = target / SLOT_TRANSCRIPT
    write_json(transcript_path, m1)
    _record_source_memory_regeneration(
        transcript_path, time.perf_counter() - started)

    timings["total"] = round(time.perf_counter() - started, 1)
    return {"source_file": os.path.abspath(source_file),
            "content_digest": digest, "reused": False,
            "m1_status": M1_STATUS_TRANSCRIBED,
            "program_track": selection,
            "utterances": m1["utterance_count"], "words": m1["word_count"],
            "speech_seconds": m1["speech_seconds"],
            "timings": timings}


def _run_clip_tasks(label: str, units, run_one) -> dict:
    """Run per-clip units through the Fabric scheduler; rows by clip id.

    `units` is [(task_id, clip_id, digest)] with one entry per clip to
    attempt; `run_one(task_id)` does the clip's work and returns its
    report row (an exception inside it becomes a `failed` row, so one
    clip never stops the others). Returns the rows keyed by clip id.

    Each task holds an EXCLUSIVE lease on its content digest: per-clip
    work replaces per-digest directories, so two clips sharing one
    digest (duplicate files) serialize instead of one deleting the
    directory out from under the other. Concurrent units are bounded
    by the machine's own core count: a whole-file subprocess per clip
    with no bound is a fork bomb on a large catalog. No live
    manifest: per-clip tasks declare no worker requirements (a missing
    tool surfaces per clip, exactly as the serial loops reported it),
    so matching is vacuous and the probe cost is skipped - the
    scheduler contributes threads, leases and the checkpoint.
    """
    from library.tools.fabric_scheduler import Scheduler
    from library.tools.resource_leases import LeaseDeclaration, ResourceRef
    from library.tools.task_graph import Task, TaskGraph

    clip_of = {task_id: clip_id for task_id, clip_id, _ in units}
    rows: Dict[str, dict] = {}
    rows_lock = threading.Lock()
    unit_slots = threading.Semaphore(os.cpu_count() or 1)

    def runner(task) -> int:
        try:
            with unit_slots:
                row = run_one(task.id)
        except Exception as exc:
            row = {"failed": f"{type(exc).__name__}: {exc}"}
        with rows_lock:
            rows[task.id] = row
        return 0 if "failed" not in row else 1

    graph = TaskGraph(tasks={
        task_id: Task(
            id=task_id,
            name=f"{label} for {clip_id}",
            resources=LeaseDeclaration(
                exclusive=frozenset({ResourceRef("clip", digest)})),
        )
        for task_id, clip_id, digest in units
    })
    with tempfile.TemporaryDirectory(prefix="fabric-clips-") as scratch:
        Scheduler(graph, {}, Path(scratch) / "clips.state.json",
                  runner=runner).run()
    return {clip_of[task_id]: {"clip_id": clip_of[task_id],
                               **rows.get(task_id, {"failed": "not-run"})}
            for task_id in clip_of}


def build_project(project_folder: str,
                  clip_ids: Optional[List[str]] = None,
                  root: Optional[Path] = None) -> dict:
    """M0 + M1 for every catalog file with media present.

    Clips whose media is offline are reported, not failed: their
    recorded digest may still serve search, but nothing can be built
    without bytes. A clip that no live digest resolves for is
    reported the same way.

    The per-clip units run through the Ren Fabric single-machine
    scheduler - one task per clip, no dependencies, so a project's
    hearings and alignments run in parallel instead of one core
    burning while the rest idle. The report shape is unchanged: rows
    come back in catalog order whatever order the clips finish in.
    Each unit is one whole-file hear-and-align (Voz plus MFA in its
    own scratch directory), bounded by the machine's core count, and
    serialized per digest like the frame lane.
    """
    try:
        from library.tools.footage_identity import declared_program_stream
        declaration = declared_program_stream(project_folder)
    except Exception:
        declaration = None
    catalog = load_catalog(project_folder)
    recorded = load_recorded_fingerprints(project_folder)
    order: List[str] = []
    rows: Dict[str, dict] = {}
    units: List[tuple] = []
    args_by_task: Dict[str, tuple] = {}
    for clip in catalog:
        clip_id = clip.get("clip_id")
        if clip_ids and clip_id not in clip_ids:
            continue
        order.append(clip_id)
        path = clip.get("source_file") or clip.get("path") or ""
        if not path or not os.path.isfile(path):
            rows[clip_id] = {"clip_id": clip_id, "source_file": path,
                             "skipped": "media-offline"}
            continue
        digest, _ = digest_for_clip(project_folder, clip, recorded)
        # No "no content digest" skip here: unlike the frame lane, the
        # serial loop never pre-checked the digest - build_source
        # fingerprints the live file itself and reports the failure.
        # A clip with no digest leases on its own id so it still runs
        # alone; digest clips serialize per digest as everywhere else.
        task_id = f"m1:{clip_id}"
        units.append((task_id, clip_id, digest or f"clip-id:{clip_id}"))
        args_by_task[task_id] = (clip_id, path)

    def run_one(task_id: str) -> dict:
        clip_id, path = args_by_task[task_id]
        account = build_source(path, declaration, root)
        return {"clip_id": clip_id, **account}

    for clip_id, account in _run_clip_tasks(
            "M0+M1 transcript", units, run_one).items():
        rows[clip_id] = account
    failed = [clip_id for clip_id in order
              if "failed" in rows.get(clip_id, {})]
    measured = [rows[clip_id] for clip_id in order
                if rows.get(clip_id, {}).get("content_digest")]
    unique_digests = {row["content_digest"] for row in measured}
    return {"project_folder": os.path.abspath(project_folder),
            "memory_root": str(source_dir("x", root).parent),
            "clips": [rows[clip_id] for clip_id in order],
            "resource_counters": {
                "frame_unique_digests": len(unique_digests),
                "frame_decode_requests": sum(
                    not row.get("reused", False) for row in measured),
                "frame_duplicate_decodes_avoided": (
                    len(measured) - len(unique_digests)),
            },
            "failed": failed}


# ── M2: the shared frame sample ─────────────────────────────────────


def _parse_frame_rate(rate_str: Optional[str]) -> Optional[float]:
    """`"24000/1001"` style `avg_frame_rate` into a float fps, or None."""
    if not rate_str:
        return None
    try:
        num, den = str(rate_str).split("/")
        den_f = float(den)
        return float(num) / den_f if den_f else None
    except (ValueError, ZeroDivisionError):
        return None


def extract_iframes(source_file: str, out_dir: str,
                    width: int = M2_WIDTH,
                    use_hwaccel: bool = True) -> Tuple[list, bool]:
    """Decode only I-frames, scaled to `width` px. One pass, whole file.

    `-skip_frame nokey` drops the other 11/12 frames of a GOP before
    they are decoded, not after - the measured 4.7 min vs 76 min gap
    (§2.2, §2.5). `-hwaccel videotoolbox` is tried first; a decode
    that fails with it retries in software, since a machine without
    the accelerator should still get the I-frame-only shape rather
    than refuse outright. `-fps_mode passthrough` matters as much as
    `-skip_frame nokey` itself: an image2 mux otherwise pads the
    kept I-frames back out to the source's full frame count to hold a
    constant rate (measured: 24 kept frames became 287 written files
    without it). `showinfo` on the output side is the timestamp
    source: a frame's spacing is whatever the source's own keyframes
    give, so the pts it actually carries is read off the filter log
    rather than inferred from a fixed step.

    Returns `(pairs, hwaccel_used)` with `pairs` a list of
    `(thumbnail_path, pts_seconds)` in time order.
    """
    os.makedirs(out_dir, exist_ok=True)
    out_pattern = os.path.join(out_dir, "frame_%06d.jpg")

    def run(hwaccel: bool):
        for stale in Path(out_dir).glob("frame_*.jpg"):
            stale.unlink()
        cmd = ["ffmpeg", "-hide_banner", "-loglevel", "info", "-y"]
        if hwaccel:
            cmd += ["-hwaccel", "videotoolbox"]
        cmd += ["-skip_frame", "nokey", "-i", source_file,
                "-an", "-sn", "-vf", f"scale={width}:-2,showinfo",
                "-fps_mode", "passthrough", "-q:v", "3", out_pattern]
        return subprocess.run(cmd, capture_output=True, encoding="utf-8",
                              errors="replace", timeout=3600, check=False)

    proc = run(use_hwaccel)
    hwaccel_used = use_hwaccel
    if proc.returncode != 0 and use_hwaccel:
        proc = run(False)
        hwaccel_used = False
    if proc.returncode != 0:
        raise RuntimeError(
            f"ffmpeg I-frame sample failed for {source_file}: "
            f"{(proc.stderr or '')[-500:]}")
    times = [float(m) for m in re.findall(r"pts_time:([0-9.]+)",
                                          proc.stderr or "")]
    files = sorted(Path(out_dir).glob("frame_*.jpg"))
    if len(times) != len(files):
        raise RuntimeError(
            f"frame/timestamp count mismatch for {source_file}: "
            f"{len(files)} thumbnails, {len(times)} showinfo timestamps")
    return list(zip((str(f) for f in files), times)), hwaccel_used


def build_frames(source_file: str, content_digest: Optional[str] = None,
                 root: Optional[Path] = None, width: int = M2_WIDTH,
                 use_hwaccel: bool = True) -> dict:
    """M2 for one source: the shared 2 Hz I-frame sample.

    Heavy: one whole-file decode. Reuses a fresh sample rather than
    re-decoding (`reused: true`) - the per-frame lanes (Vision, CLIP,
    entity) and `footage_frames.py` all read this instead of decoding
    their own. Callers run this under the heavy-work lock; media is
    read-only.
    """
    started = time.perf_counter()
    fp = footage_identity.fingerprint(source_file)
    digest = content_digest or fp["content_digest"]
    size = fp["size_bytes"]
    target = source_dir(digest, root)
    m0 = read_m0(digest, root)
    streams = (m0 or {}).get("video_streams") or [{}]
    duration = (m0 or {}).get("duration_seconds")

    existing = read_m2(digest, root)
    if (is_fresh(existing, source_file)
            and m2_frames_present(digest, existing, root)):
        from library.tools import perf_ledger
        with perf_ledger.media_decode(
                source_file, purpose="source_memory.m2_iframe_sample",
                media_kind="video", access_pattern="sequential",
                calls=0, cache_hits=1, decoded_source_s=duration,
                backend="source_memory_cache",
                frame_count=existing["frame_count"]):
            pass
        return {"source_file": os.path.abspath(source_file),
                "content_digest": digest, "reused": True,
                "frame_count": existing.get("frame_count", 0),
                "timings": {"total": round(time.perf_counter() - started, 1)}}

    frames_dir = target / SLOT_FRAMES_DIR
    if frames_dir.exists():
        shutil.rmtree(frames_dir)
    frames_dir.mkdir(parents=True, exist_ok=True)

    from library.tools import perf_ledger
    with perf_ledger.media_decode(
            source_file, purpose="source_memory.m2_iframe_sample",
            media_kind="video", access_pattern="sequential",
            decoded_source_s=duration, subprocesses=1, backend="ffmpeg",
            requested_hwaccel=("videotoolbox" if use_hwaccel else None),
            target_width=width) as measurement:
        t0 = time.perf_counter()
        pairs, hwaccel_used = extract_iframes(source_file, str(frames_dir),
                                              width, use_hwaccel)
        measurement["bytes_written"] = sum(
            path.stat().st_size for path in frames_dir.glob("frame_*.jpg"))
        measurement["hwaccel_used"] = hwaccel_used
        measurement["frame_count"] = len(pairs)
    decode_seconds = round(time.perf_counter() - t0, 1)

    fps = _parse_frame_rate(
        streams[0].get("avg_frame_rate"))
    gop_frames = None
    if fps and len(pairs) >= 2:
        diffs = sorted(b[1] - a[1] for a, b in zip(pairs, pairs[1:]))
        median = diffs[len(diffs) // 2]
        if median > 0:
            gop_frames = round(median * fps)

    frames = [{"file": os.path.relpath(path, target), "t": round(t, 3)}
              for path, t in pairs]
    doc = {
        "content_digest": digest,
        "size_bytes": size,
        "source_file": os.path.abspath(source_file),
        "status": M2_STATUS_SAMPLED,
        "rate_hz_nominal": M2_RATE_HZ_NOMINAL,
        "width": width,
        "frame_count": len(frames),
        "frames": frames,
        "instrument": {"decoder": "iframe-skip",
                       "hwaccel": "videotoolbox" if hwaccel_used else None},
        "gop_frames": gop_frames,
    }
    index_path = target / SLOT_FRAMES_INDEX
    write_json(index_path, doc)
    _record_source_memory_regeneration(index_path, time.perf_counter() - started)

    # The sampler measures GOP directly from keyframe spacing; M0's own
    # field stays null until a sample has actually been taken (the
    # contract in `build_source_record`), so this is the one place that
    # fills it in.
    if gop_frames is not None and m0 is not None and m0.get("gop_frames") != gop_frames:
        updated_m0 = dict(m0)
        updated_m0["gop_frames"] = gop_frames
        write_json(target / SLOT_SOURCE, updated_m0)

    return {"source_file": os.path.abspath(source_file),
            "content_digest": digest, "reused": False,
            "frame_count": len(frames), "gop_frames": gop_frames,
            "timings": {"decode": decode_seconds,
                       "total": round(time.perf_counter() - started, 1)}}


def build_project_frames(project_folder: str,
                         clip_ids: Optional[List[str]] = None,
                         root: Optional[Path] = None,
                         width: int = M2_WIDTH,
                         use_hwaccel: bool = True) -> dict:
    """M2 for every catalog file with media present.

    Same reporting shape as `build_project`: offline or undigestable
    clips are reported, not failed.

    The per-clip units run through the Ren Fabric single-machine
    scheduler (`library/tools/fabric_scheduler.py`) - one task per
    clip, no dependencies, so a project's frame samples decode in
    parallel instead of one core burning while the rest idle. The
    report shape is unchanged: rows come back in catalog order
    whatever order the clips finish in. Each task holds an EXCLUSIVE
    lease on its content digest while it samples, because sampling
    replaces the digest's frame directory - two clips sharing one
    digest (duplicate files) serialize instead of one deleting the
    directory out from under the other. Concurrent decodes are
    bounded by the machine's own core count: a whole-file ffmpeg
    decode per clip with no bound is a fork bomb on a large catalog.

    The scheduler's checkpoint is the run's own ledger; resume
    across runs stays where it was, in the slot freshness the lane
    checks before building (`slot_is_fresh`).
    """
    catalog = load_catalog(project_folder)
    recorded = load_recorded_fingerprints(project_folder)
    order: List[str] = []
    rows: Dict[str, dict] = {}
    units: List[tuple] = []
    args_by_task: Dict[str, tuple] = {}
    for clip in catalog:
        clip_id = clip.get("clip_id")
        if clip_ids and clip_id not in clip_ids:
            continue
        order.append(clip_id)
        path = clip.get("source_file") or clip.get("path") or ""
        if not path or not os.path.isfile(path):
            rows[clip_id] = {"clip_id": clip_id, "source_file": path,
                             "skipped": "media-offline"}
            continue
        digest, _ = digest_for_clip(project_folder, clip, recorded)
        if digest is None:
            rows[clip_id] = {"clip_id": clip_id, "source_file": path,
                             "skipped": "no content digest"}
            continue
        task_id = f"m2:{clip_id}"
        units.append((task_id, clip_id, digest))
        args_by_task[task_id] = (clip_id, path, digest)

    def run_one(task_id: str) -> dict:
        clip_id, path, digest = args_by_task[task_id]
        account = build_frames(path, digest, root, width, use_hwaccel)
        return {"clip_id": clip_id, **account}

    for clip_id, account in _run_clip_tasks(
            "M2 frames", units, run_one).items():
        rows[clip_id] = account
    failed = [clip_id for clip_id in order
              if "failed" in rows.get(clip_id, {})]
    return {"project_folder": os.path.abspath(project_folder),
            "memory_root": str(source_dir("x", root).parent),
            "clips": [rows[clip_id] for clip_id in order],
            "failed": failed}


def status_report(project_folder: str,
                  root: Optional[Path] = None) -> dict:
    """Per-clip memory state: digest basis, M0 freshness, M1/M2 status."""
    recorded = load_recorded_fingerprints(project_folder)
    rows = []
    for clip in load_catalog(project_folder):
        digest, basis = digest_for_clip(project_folder, clip, recorded)
        path = clip.get("source_file") or clip.get("path") or ""
        m0_fresh = (is_fresh(read_m0(digest, root), path)
                    if digest else False)
        m1_status = "absent"
        utterances = words = 0
        if digest is not None:
            doc, m1_status = read_m1(digest, root)
            if doc is not None:
                utterances = doc.get("utterance_count", 0)
                words = doc.get("word_count", 0)
        m2_status = "absent"
        frame_count = 0
        if digest is not None:
            m2_doc = read_m2(digest, root)
            if m2_doc is None:
                m2_status = "missing"
            else:
                m2_status = "fresh" if (basis != "live"
                                        or is_fresh(m2_doc, path)) else "stale"
                frame_count = m2_doc.get("frame_count", 0)
        rows.append({"clip_id": clip.get("clip_id"),
                     "content_digest": digest, "digest_basis": basis,
                     "m0_fresh": m0_fresh, "m1": m1_status,
                     "utterances": utterances, "words": words,
                     "m2": m2_status, "frames": frame_count})
    return {"project_folder": os.path.abspath(project_folder),
            "memory_root": str(source_dir("x", root).parent),
            "clips": rows}


# ── CLI ────────────────────────────────────────────────────────────


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="source_memory",
        description="Per-source footage memory: probe (M0), "
                    "whole-source transcription (M1) and the shared "
                    "2 Hz I-frame sample (M2). Reads media, writes only "
                    "under the memory root. Heavy commands run under "
                    "the heavy-work lock.")
    sub = parser.add_subparsers(dest="command")

    p_build = sub.add_parser("build", help="M0 + M1 for a project's catalog")
    p_build.add_argument("project")
    p_build.add_argument("--clip", action="append", default=None,
                         help="one clip id (repeatable; default: all)")
    p_build.add_argument("--memory-root",
                         help="override the memory root for this run")

    p_frames = sub.add_parser(
        "frames", help="M2: shared 2 Hz I-frame sample for a project's catalog")
    p_frames.add_argument("project")
    p_frames.add_argument("--clip", action="append", default=None,
                          help="one clip id (repeatable; default: all)")
    p_frames.add_argument("--width", type=int, default=M2_WIDTH)
    p_frames.add_argument("--no-hwaccel", action="store_true",
                          help="software I-frame decode only")
    p_frames.add_argument("--memory-root",
                          help="override the memory root for this run")

    p_status = sub.add_parser("status", help="per-clip memory state")
    p_status.add_argument("project")
    p_status.add_argument("--memory-root",
                          help="override the memory root for this run")

    args = parser.parse_args(argv)
    if not args.command:
        parser.print_help()
        return 2
    root = Path(args.memory_root).expanduser() if args.memory_root else None
    if args.command == "build":
        report = build_project(args.project, clip_ids=args.clip, root=root)
        print(json.dumps(report, indent=2))
        return 1 if report["failed"] else 0
    if args.command == "frames":
        report = build_project_frames(
            args.project, clip_ids=args.clip, root=root, width=args.width,
            use_hwaccel=not args.no_hwaccel)
        print(json.dumps(report, indent=2))
        return 1 if report["failed"] else 0
    print(json.dumps(status_report(args.project, root=root), indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
