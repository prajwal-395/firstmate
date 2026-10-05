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

**The slots.** M0, M1, M2, M3, M3b, M1b, M4, M5, M6 and M7 are written now;
every later lane writes into its named slot without inventing its own
shape:

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
M4       `scenes.json`                scene observations (VLM) +    `library/tools/
                                       ffmpeg scene boundaries       analysis/
                                                                vision_pipeline_v3.py`
M5       `sound.json`                 sound-event labels (PANNs)    `library/steps/
                                                                step_1_04_temporal_index/
                                                                step.py`
M6       `clock.json`                 per-source multicam offset    conversation_clock.py
M7       `events.json`                DERIVED per-person spans      `library/tools/
                                      (speaking, on_screen)         event_spans.py`
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
import time
import wave
from pathlib import Path
from typing import Dict, List, Optional, Tuple

try:
    from library.tools.shared_environment import vep_home
    from library.tools import footage_identity
except ImportError:  # imported as `tools.*` from inside library/
    from tools.shared_environment import vep_home
    from tools import footage_identity

MEMORY_DIRNAME = "source_memory"
"""The store directory name under `vep_home()` (AGENTS.md 9: one shared
dependency location per machine, outside every checkout)."""

MEMORY_ROOT_ENV = "PIPELINE_SOURCE_MEMORY_ROOT"
"""Names the memory root outright, for a machine whose layout differs."""

# ── The slots ──────────────────────────────────────────────────────
# A lane writes into its named file and no other. `source.json`,
# `transcript.words.json`, the `frames/`/`frames.index.json` pair,
# `identity.json` and `speakers.json` (by `library/tools/person_entity.py`),
# `scenes.json` (by `library/tools/analysis/vision_pipeline_v3.py`) and
# `sound.json` (by `library/steps/step_1_04_temporal_index/step.py`) are
# written now; the rest are RESERVED - named here so later lanes share
# the shapes instead of inventing their own, with no code behind them yet.

SLOT_SOURCE = "source.json"                    # M0
SLOT_TRANSCRIPT = "transcript.words.json"      # M1
SLOT_SPEAKERS = "speakers.json"                # M1b - written by person_entity.py
SLOT_FRAMES_DIR = "frames"                     # M2
SLOT_FRAMES_INDEX = "frames.index.json"        # M2
SLOT_PERSONS = "persons.json"                  # M3 - written by person_measurements.py
SLOT_IDENTITY = "identity.json"                # M3b - written by person_entity.py
SLOT_SCENES = "scenes.json"                    # M4 - written by vision_pipeline_v3.py
SLOT_SOUND = "sound.json"                      # M5 - written by step_1_04
SLOT_CLOCK = "clock.json"                      # M6 - written by conversation_clock.py
SLOT_EVENTS = "events.json"                    # M7 - written by event_spans.py
SLOT_VERDICTS = "verdicts.json"                # M8 - written by span_verification.py

# No slot is reserved now. SLOT_CLOCK (M6) was the first to gain a writer
# (`library/tools/conversation_clock.py`); SLOT_FRAMES_DIR/SLOT_FRAMES_INDEX
# (M2) followed (`extract_iframes`/`build_frames` below), then SLOT_IDENTITY
# (M3b) and SLOT_SPEAKERS (M1b) (`library/tools/person_entity.py`),
# SLOT_PERSONS (M3) (`person_measurements.py`), SLOT_EVENTS (M7)
# (`event_spans.py`), SLOT_SCENES (M4) (`vision_pipeline_v3.py`) and
# SLOT_SOUND (M5) (`step_1_04_temporal_index/step.py`).

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
    try:
        with open(path, encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, ValueError):
        return None


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
    """Video/audio streams and duration off ffprobe, or a refusal.

    Raises RuntimeError when ffprobe itself fails - a broken probe is
    not a streamless file.
    """
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries",
             "stream=index,codec_type,codec_name,width,height,"
             "avg_frame_rate,channels,sample_rate:"
             "format=duration,size",
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
        entry = {
            "index": stream.get("index"),
            "codec_type": stream.get("codec_type"),
            "codec": stream.get("codec_name", "unknown"),
        }
        if stream.get("codec_type") == "video":
            entry["width"] = stream.get("width")
            entry["height"] = stream.get("height")
            entry["avg_frame_rate"] = stream.get("avg_frame_rate")
        if stream.get("codec_type") == "audio":
            audio_ordinal += 1
            entry["channel"] = audio_ordinal
            entry["channels"] = stream.get("channels")
            try:
                entry["sample_rate"] = int(stream.get("sample_rate"))
            except (TypeError, ValueError):
                entry["sample_rate"] = None
        streams.append(entry)
    try:
        duration = float((probe.get("format") or {}).get("duration"))
    except (TypeError, ValueError):
        duration = None
    return {"streams": streams, "duration_seconds": duration}


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
    import array

    samples = array.array("h")
    samples.frombytes(frames)
    if not samples:
        return float("-inf")
    mean_square = sum(s * s for s in samples) / len(samples)
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
    return {
        "content_digest": content_digest,
        "size_bytes": size_bytes,
        "observed_paths": [os.path.abspath(source_file)],
        "duration_seconds": probe.get("duration_seconds"),
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
            continue
        regions.append({
            "start": words[0]["start"],
            "end": words[-1]["end"],
            "text": text.lower().strip(),
            "words": words,
            "confidence": 0.0,
            "method": method,
        })
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
    leaves a half-written slot that reads as fresh."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.parent / f".{path.name}.{os.getpid()}.tmp"
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=1)
    os.replace(tmp, path)


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


def read_scenes(content_digest: str,
                root: Optional[Path] = None) -> Optional[dict]:
    """The M4 scene lane for a digest, or None when never written."""
    doc = _load_json(source_dir(content_digest, root) / SLOT_SCENES)
    return doc if isinstance(doc, dict) else None


def write_sound(content_digest: str, source_file: str, status: str,
                sound_events: list, method: str,
                root: Optional[Path] = None) -> None:
    """M5 `sound.json`: measured PANNs sound-event labels.

    `sound_events` is the `[{label, start, end, confidence}]` list the
    temporal index already measures, `method` the producer's name or
    `unmeasured: <reason>`. An unmeasured record is written only when no
    measured record exists for the digest: two clips of one camera file
    share a memory, and a source whose events were measured must not be
    demoted to empty because a later clip ran without the checkpoint.
    """
    path = source_dir(content_digest, root) / SLOT_SOUND
    measured = not str(method or "").startswith("unmeasured")
    if not measured:
        existing = _load_json(path)
        if (isinstance(existing, dict)
                and not str(existing.get("method") or "").startswith(
                    "unmeasured")):
            return
    write_json(path, {
        "content_digest": content_digest,
        "source_file": os.path.abspath(source_file),
        "status": status,
        "sound_events": sound_events,
        "method": method,
    })


def read_sound(content_digest: str,
               root: Optional[Path] = None) -> Optional[dict]:
    """The M5 sound lane for a digest, or None when never written."""
    doc = _load_json(source_dir(content_digest, root) / SLOT_SOUND)
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
        write_json(target / SLOT_TRANSCRIPT, build_untranscribed_document(
            digest, source_file, selection, status, detail))
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
    write_json(target / SLOT_TRANSCRIPT, m1)

    timings["total"] = round(time.perf_counter() - started, 1)
    return {"source_file": os.path.abspath(source_file),
            "content_digest": digest, "reused": False,
            "m1_status": M1_STATUS_TRANSCRIBED,
            "program_track": selection,
            "utterances": m1["utterance_count"], "words": m1["word_count"],
            "speech_seconds": m1["speech_seconds"],
            "timings": timings}


def build_project(project_folder: str,
                  clip_ids: Optional[List[str]] = None,
                  root: Optional[Path] = None) -> dict:
    """M0 + M1 for every catalog file with media present.

    Clips whose media is offline are reported, not failed: their
    recorded digest may still serve search, but nothing can be built
    without bytes. A clip that no live digest resolves for is
    reported the same way.
    """
    try:
        from library.tools.footage_identity import declared_program_stream
        declaration = declared_program_stream(project_folder)
    except Exception:
        declaration = None
    catalog = load_catalog(project_folder)
    results, failed = [], []
    for clip in catalog:
        clip_id = clip.get("clip_id")
        if clip_ids and clip_id not in clip_ids:
            continue
        path = clip.get("source_file") or clip.get("path") or ""
        if not path or not os.path.isfile(path):
            results.append({"clip_id": clip_id, "source_file": path,
                            "skipped": "media-offline"})
            continue
        try:
            account = build_source(path, declaration, root)
        except Exception as exc:
            failed.append(clip_id)
            results.append({"clip_id": clip_id, "source_file": path,
                            "failed": f"{type(exc).__name__}: {exc}"})
            continue
        results.append({"clip_id": clip_id, **account})
    return {"project_folder": os.path.abspath(project_folder),
            "memory_root": str(source_dir("x", root).parent),
            "clips": results,
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

    existing = read_m2(digest, root)
    if is_fresh(existing, source_file):
        return {"source_file": os.path.abspath(source_file),
                "content_digest": digest, "reused": True,
                "frame_count": existing.get("frame_count", 0),
                "timings": {"total": round(time.perf_counter() - started, 1)}}

    frames_dir = target / SLOT_FRAMES_DIR
    if frames_dir.exists():
        shutil.rmtree(frames_dir)
    frames_dir.mkdir(parents=True, exist_ok=True)

    t0 = time.perf_counter()
    pairs, hwaccel_used = extract_iframes(source_file, str(frames_dir),
                                          width, use_hwaccel)
    decode_seconds = round(time.perf_counter() - t0, 1)

    m0 = read_m0(digest, root)
    fps = _parse_frame_rate(
        ((m0 or {}).get("video_streams") or [{}])[0].get("avg_frame_rate"))
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
    write_json(target / SLOT_FRAMES_INDEX, doc)

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
    """
    catalog = load_catalog(project_folder)
    recorded = load_recorded_fingerprints(project_folder)
    results, failed = [], []
    for clip in catalog:
        clip_id = clip.get("clip_id")
        if clip_ids and clip_id not in clip_ids:
            continue
        path = clip.get("source_file") or clip.get("path") or ""
        if not path or not os.path.isfile(path):
            results.append({"clip_id": clip_id, "source_file": path,
                            "skipped": "media-offline"})
            continue
        digest, _ = digest_for_clip(project_folder, clip, recorded)
        if digest is None:
            results.append({"clip_id": clip_id, "source_file": path,
                            "skipped": "no content digest"})
            continue
        try:
            account = build_frames(path, digest, root, width, use_hwaccel)
        except Exception as exc:
            failed.append(clip_id)
            results.append({"clip_id": clip_id, "source_file": path,
                            "failed": f"{type(exc).__name__}: {exc}"})
            continue
        results.append({"clip_id": clip_id, **account})
    return {"project_folder": os.path.abspath(project_folder),
            "memory_root": str(source_dir("x", root).parent),
            "clips": results,
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
