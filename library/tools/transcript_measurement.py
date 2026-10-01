"""The canonical transcript measurement: one hearing per audio signal.

`ren analyze` used to hear every source twice. Step 1.04 extracts the
16 kHz mono audio and transcribes it (voz + ONE batched MFA run for the
whole step), then the source-memory transcript lane
(`source_memory.build_source`, M1) demuxed the same audio again and
transcribed it again - per source, so it also paid MFA's ~35 s fixed
invocation cost per file that the batch exists to avoid.

**The measurement is keyed by the AUDIO, not by who asked.** The key is
the sha256 of the WAV's sample format and PCM frames (`pcm_key`), so the
header ffmpeg writes never splits two identical signals, and two
different signals - a source's ffmpeg-default stream and its
loudest-live program track, when those differ - never share an answer.
That is what makes reuse exact rather than approximate: a cache hit is
a measurement of byte-identical samples.

**What is stored is the measurement, not a projection of it.** The
aligned document (`segments` with word timings, the aligner's stamp)
and the hearing's record (`hybrid_transcription.hybrid_record`, or the
refusal record of a hearing that heard nothing). Each consumer projects
its own view off it: 1.04 snaps words to its onsets and applies its
boundary hygiene (`_regions_from_segments`), M1 keeps MFA's own
boundaries (`source_memory.utterances_from_aligned`). A refusal that is
not an answer (`FallbackRequired` other than silence) is never stored:
it is retried next run, the way it always was.

**Where it lives.** `<memory root>/transcripts/<pcm_key>.json`, beside
the per-source digests (`source_memory.memory_root`): per machine,
outside every project, referenced and never copied.
"""

from __future__ import annotations

import hashlib
import json
import os
import wave
from pathlib import Path
from typing import Optional, Tuple

STORE_DIRNAME = "transcripts"
STORE_VERSION = 1

HEARD_SINGLE = "single"
"""Heard alone: `timeline_transcript.transcribe_audio`, one aligner run."""
HEARD_BATCHED = "batched"
"""Heard in step 1.04's batch: one aligner run over many files' windows."""

_CHUNK_FRAMES = 1 << 20


def pcm_key(wav_path: str) -> str:
    """The content address of a WAV's samples: format plus PCM frames.

    The RIFF header is left out on purpose - two ffmpeg invocations
    that decode the same stream (`-vn` default selection against
    `-map 0:a:N`) write the same samples under headers that may
    differ.
    """
    digest = hashlib.sha256()
    with wave.open(str(wav_path), "rb") as wav:
        digest.update(json.dumps(
            [wav.getnchannels(), wav.getsampwidth(),
             wav.getframerate()]).encode("ascii"))
        while True:
            frames = wav.readframes(_CHUNK_FRAMES)
            if not frames:
                break
            digest.update(frames)
    return digest.hexdigest()


def audio_key(wav_path: str) -> Optional[str]:
    """`pcm_key`, or None for a file that is not a readable WAV.

    Such a file has no content address, so it is heard uncached - and
    the seam, not this module, says what is wrong with it.
    """
    try:
        return pcm_key(wav_path)
    except (OSError, EOFError, wave.Error):
        return None


def store_dir(root: Optional[Path] = None) -> Path:
    from library.tools import source_memory

    base = Path(root) if root is not None else source_memory.memory_root()
    return base / STORE_DIRNAME


def load(key: str, root: Optional[Path] = None
         ) -> Optional[Tuple[dict, dict]]:
    """`(aligned, record)` for an audio key, or None when never heard."""
    try:
        with open(store_dir(root) / f"{key}.json", encoding="utf-8") as fh:
            doc = json.load(fh)
    except (OSError, ValueError):
        return None
    if (not isinstance(doc, dict) or doc.get("pcm_sha256") != key
            or doc.get("version") != STORE_VERSION
            or not isinstance(doc.get("aligned"), dict)
            or not isinstance(doc.get("record"), dict)):
        return None
    return doc["aligned"], doc["record"]


def store(key: str, aligned: dict, record: dict, heard: str,
          label: str = "", root: Optional[Path] = None) -> None:
    """Record one hearing. Atomic, so a killed run never leaves half."""
    target = store_dir(root)
    target.mkdir(parents=True, exist_ok=True)
    path = target / f"{key}.json"
    tmp = target / f".{key}.{os.getpid()}.tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump({"version": STORE_VERSION, "pcm_sha256": key,
                   "heard": heard, "label": label,
                   "aligned": aligned, "record": record}, fh)
    os.replace(tmp, path)


def transcribe(wav_path: str, label: str = "",
               root: Optional[Path] = None, **seam_kwargs) -> tuple:
    """`timeline_transcript.transcribe_audio`, measured once per signal.

    Same return and same refusals as the seam. A stored hearing of the
    same samples is served instead of hearing again; a fresh hearing
    is stored before it is returned.
    """
    key = audio_key(wav_path)
    if key is not None:
        cached = load(key, root)
        if cached is not None:
            return cached
    from library.tools import timeline_transcript

    aligned, record = timeline_transcript.transcribe_audio(
        Path(wav_path), label=label or os.path.basename(str(wav_path)),
        **seam_kwargs)
    if key is not None:
        store(key, aligned, record, HEARD_SINGLE, label, root)
    return aligned, record
