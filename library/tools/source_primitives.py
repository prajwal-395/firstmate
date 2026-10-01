"""Source primitives: what every analysis of one source starts from, once.

Below every analysis lane sits the same handful of facts about a file:
its ffprobe stream metadata and timing, the level of each audio track,
which track carries the program, and that track as 16 kHz mono PCM.
Until this module, each consumer rediscovered them: step 1.04 extracted
its own audio, the source-memory transcript lane (M1) probed, demuxed
every track, measured levels and chose the program track, and the
identity lane (M3b) probed, demuxed, measured and chose AGAIN before
diarizing - a whole-file decode per consumer per source.

**Content-addressed.** Everything is keyed by `footage_identity`'s
content digest and lives in the source's memory directory
(`source_memory.source_dir`):

=====================  =================================================
`source.json` (M0)     probe (streams, duration, frame rate), measured
                       per-track levels, the program-track decision, the
                       streams no decoder here can read, and
                       `program_audio` - where the canonical WAV is and
                       the sha256 of its samples
`program.16k.wav`      the program track, 16 kHz mono s16le - the rate
                       step 1.04 and the transcriber hear at
=====================  =================================================

The shared I-frame sample (M2) is the frame primitive and is already
built once per source by `source_memory.build_frames`.

**Built once, served after.** `ensure` returns the stored primitives
when M0 still fingerprints to the file on disk, was decided under the
same program-stream declaration, and its WAV is present at the size it
was written; otherwise it measures and stores them. One ffmpeg pass
demuxes every decodable track (the scout measured 13.1 s for three
82-minute tracks in one pass); only the program track is kept.

**What it refuses to invent.** A file with no audio, no live track or a
declaration naming a dead track gets an M0 that says so and no WAV -
never a default track. A stream ffmpeg here has no decoder for (iPhone
spatial audio, `apple_apac`) is recorded as undecodable and left out of
the demux, instead of failing the whole file.

Consumers: `source_memory.build_source` (M1), `person_entity`'s identity
build (M3b), and step 1.04 for a source whose ONLY audio stream is the
program - there the WAV is the same samples 1.04's own extraction
writes, so its measurements cannot move (1.04's default-stream
extraction stays for multi-track sources, where the two differ).
"""

from __future__ import annotations

import functools
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Dict, Optional

from library.tools import footage_identity, source_memory

SLOT_PROGRAM_AUDIO = "program.16k.wav"

NO_AUDIO = "no-audio-streams"
NO_DECODABLE_AUDIO = "no-decodable-audio-streams"
DECLARATION_REFUSED = "declaration-refused"
NO_LIVE_TRACK = "no-live-track"


@functools.lru_cache(maxsize=1)
def available_decoders() -> frozenset:
    """Decoder names this machine's ffmpeg has (`ffmpeg -decoders`)."""
    try:
        out = subprocess.run(["ffmpeg", "-hide_banner", "-decoders"],
                             capture_output=True, encoding="utf-8",
                             errors="replace", timeout=60, check=False)
    except FileNotFoundError:
        raise RuntimeError("ffmpeg not found. Install: brew install ffmpeg")
    names = set()
    listing = False
    for line in (out.stdout or "").splitlines():
        if line.strip().startswith("------"):
            listing = True
            continue
        parts = line.split()
        if listing and len(parts) >= 2:
            names.add(parts[1])
    return frozenset(names)


def _program_audio_fresh(m0: dict, target: Path) -> bool:
    """Whether M0's recorded program WAV is the file on disk."""
    audio = m0.get("program_audio")
    if not isinstance(audio, dict):
        return False
    if audio.get("file") is None:
        # A recorded "no program track" is a measured answer.
        return "program_audio" in m0
    path = target / audio["file"]
    try:
        return path.stat().st_size == audio.get("size_bytes")
    except OSError:
        return False


def _declared_as(m0: dict, declaration: Optional[int]) -> bool:
    """Whether M0's program decision was made under `declaration`."""
    return m0.get("program_declaration") == declaration


_ANY_DECLARATION = object()


def read(source_file: str, declaration=None,
         root: Optional[Path] = None) -> Optional[dict]:
    """Stored primitives for a source, or None when they must be built.

    `declaration` is the project's program stream; `_ANY_DECLARATION`
    accepts whatever M0 was decided under.
    """
    try:
        fp = footage_identity.fingerprint(source_file)
    except OSError:
        return None
    target = source_memory.source_dir(fp["content_digest"], root)
    m0 = source_memory.read_m0(fp["content_digest"], root)
    if (m0 is None or not source_memory.is_fresh(m0, source_file)
            or (declaration is not _ANY_DECLARATION
                and not _declared_as(m0, declaration))
            or not _program_audio_fresh(m0, target)):
        return None
    return _account(m0, target, reused=True)


def _account(m0: dict, target: Path, reused: bool,
             timings: Optional[dict] = None) -> dict:
    audio = m0.get("program_audio") or {}
    wav = str(target / audio["file"]) if audio.get("file") else None
    return {"content_digest": m0["content_digest"], "m0": m0,
            "program_wav": wav,
            "program_pcm_sha256": audio.get("pcm_sha256"),
            "reused": reused, "timings": timings or {}}


def ensure(source_file: str, declaration: Optional[int] = None,
           root: Optional[Path] = None,
           scratch_parent: Optional[str] = None) -> dict:
    """The primitives for one source: stored ones, or measured now.

    Returns `{content_digest, m0, program_wav, program_pcm_sha256,
    reused, timings}`; `program_wav` is None exactly when M0's
    `program_track.basis` says why there is no program track.
    Heavy on a miss (one whole-file audio demux); callers run it under
    the heavy-work lock. Media is read-only.
    """
    stored = read(source_file, declaration, root)
    if stored is not None:
        return stored
    from library.tools import transcript_measurement

    started = time.perf_counter()
    timings: Dict[str, float] = {}
    fp = footage_identity.fingerprint(source_file)
    digest, size = fp["content_digest"], fp["size_bytes"]
    target = source_memory.source_dir(digest, root)

    t0 = time.perf_counter()
    probe = source_memory.probe_streams(source_file)
    timings["probe"] = round(time.perf_counter() - t0, 1)
    audio = [s for s in probe.get("streams", [])
             if s.get("codec_type") == "audio" and s.get("channel")]
    decoders = available_decoders() if audio else frozenset()
    undecodable = [{"channel": s["channel"], "codec": s.get("codec")}
                   for s in audio if s.get("codec") not in decoders]
    channels = [s["channel"] for s in audio if s.get("codec") in decoders]

    def finish(levels: Dict[int, float], selection: dict,
               program_audio: dict) -> dict:
        m0 = source_memory.build_source_record(
            source_file, digest, size, probe, levels, selection)
        previous = source_memory.read_m0(digest, root) or {}
        if previous.get("gop_frames") is not None:
            # M2 measured the GOP off the keyframes; the probe did not.
            m0["gop_frames"] = previous["gop_frames"]
        m0["undecodable_audio_streams"] = undecodable
        m0["program_declaration"] = declaration
        m0["program_audio"] = program_audio
        source_memory.write_json(target / source_memory.SLOT_SOURCE, m0)
        timings["total"] = round(time.perf_counter() - started, 1)
        return _account(m0, target, reused=False, timings=timings)

    none = {"file": None}
    if not audio:
        return finish({}, {"channel": None, "basis": NO_AUDIO,
                           "measured_levels_db": {}}, none)
    if not channels:
        return finish({}, {"channel": None, "basis": NO_DECODABLE_AUDIO,
                           "measured_levels_db": {}}, none)

    with tempfile.TemporaryDirectory(prefix="source-primitives-",
                                     dir=scratch_parent) as scratch:
        t0 = time.perf_counter()
        wavs = source_memory.demux_audio_tracks(source_file, channels,
                                                scratch)
        timings["demux"] = round(time.perf_counter() - t0, 1)
        t0 = time.perf_counter()
        levels = {ch: source_memory.track_level_db(wavs[ch])
                  for ch in channels}
        timings["levels"] = round(time.perf_counter() - t0, 1)
        try:
            channel, selection = source_memory.select_program_track(
                levels, declaration)
        except ValueError as refused:
            return finish(levels, {
                "channel": None, "basis": DECLARATION_REFUSED,
                "refusal": str(refused),
                "measured_levels_db": {
                    f"CH{ch}": round(lv, 1)
                    for ch, lv in sorted(levels.items())}}, none)
        if channel is None:
            return finish(levels, selection, none)
        target.mkdir(parents=True, exist_ok=True)
        landed = target / SLOT_PROGRAM_AUDIO
        shutil.move(wavs[channel], landed)
        t0 = time.perf_counter()
        key = transcript_measurement.pcm_key(str(landed))
        timings["hash"] = round(time.perf_counter() - t0, 1)
        return finish(levels, selection, {
            "file": SLOT_PROGRAM_AUDIO, "channel": channel,
            "rate_hz": source_memory.TRANSCRIPT_RATE_HZ,
            "size_bytes": landed.stat().st_size, "pcm_sha256": key})


def sole_program_wav(source_file: str,
                     root: Optional[Path] = None) -> Optional[str]:
    """The canonical WAV, for a source whose ONLY audio stream it is.

    Step 1.04's own extraction is ffmpeg's default audio stream; with a
    single decodable stream that is the program track and the samples
    are identical, so 1.04 can read the primitive instead of decoding
    the file again. With several streams the two may differ, and None
    sends 1.04 to its own extraction unchanged. A project's program
    declaration does not apply here: 1.04 never honoured one.
    """
    stored = read(source_file, _ANY_DECLARATION, root)
    if stored is not None:
        streams = stored["m0"].get("audio_streams") or []
        if len(streams) != 1:
            return None
        return stored["program_wav"]
    probe = source_memory.probe_streams(source_file)
    audio = [s for s in probe.get("streams", [])
             if s.get("codec_type") == "audio"]
    if len(audio) != 1:
        return None
    return ensure(source_file, None, root)["program_wav"]
