"""PERF-008: the key a cached analysis result is filed under, and the store.

The punch list asks for two things this module owns:

* "Cache successful results by source identity, model, configuration, and
  analysis version."
* "Reuse previous evidence without treating stale analysis as current."

So a cached measurement is reusable only when all FOUR of those are the
same, and :class:`AnalysisKey` names all four.  An entry whose key does
not match the one the caller wants is STALE: it is never served as
current, the difference is named, and the caller measures again.  A cache
that serves a stale entry is worse than no cache, because the reader
cannot tell that the evidence is old.

**The store is per SOURCE IDENTITY.**  The file lives beside the other
measurements of the same content digest in the source memory
(:mod:`library.tools.source_memory`), so a camera file measured once is
measured once for the project that owns it.  The key is the digest of the
source's own bytes, so a re-recorded file never serves another file's
measurements, and the store follows the same project scoping every other
measurement follows.

**The unit of reuse is the FRAME.**  A detection pass samples the source
at a rate, and every sampled frame is one measured thing: the boxes the
detector answered on it, and the second of source it stands for.  A
request for a wider window than an earlier pass covered reuses the frames
the earlier pass measured and measures only the frames it missed - which
is the whole of "compute analysis only for spans a request needs".  The
frame indices are SOURCE frame numbers, so a frame is the same frame in
both passes or it is not the same frame at all.

Nothing here decides WHAT to measure.  That is the demand-driven planner's
job (:mod:`library.tools.query_directed_acquisition`); this module only
says whether a measurement that was already made may be used again.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from library.tools import cache_keys as _canonical

CACHE_FORMAT = "analysis-cache/1"
"""The store's own format, so a reader can refuse a file it does not know."""

CACHE_FILENAME = "analysis_cache.json"
"""The slot the store writes into, beside the source's other measurements."""

KEY_COMPONENTS = _canonical.KEY_COMPONENTS
"""The four things a cached result is reusable under, in the punch list's order."""


def configuration_digest(parts: dict[str, Any]) -> str:
    """A digest of a configuration, by VALUE.

    The configuration is everything about HOW a measurement was taken that
    is not the model or the source: the sample rate, the score threshold,
    the phrases asked about, the window size.  Two requests that differ in
    any of them ask different questions, and the digest is what says so.

    Constructed by :mod:`library.tools.cache_keys`, the one module that
    builds cache keys; this stays byte-identical so filed measurements
    do not move.
    """
    return _canonical.configuration_digest(parts)


@dataclass(frozen=True)
class AnalysisKey(_canonical.CacheKey):
    """The four things a cached measurement is reusable under.

    ``source_identity`` is the content digest of the media (the source's
    own bytes).  ``model`` names the producer or model that answered.
    ``configuration`` is the digest of how it was asked.  ``analysis_version``
    is the version of the analysis itself - the producer's own version
    string, which changes when the method changes.

    A subclass of :class:`library.tools.cache_keys.CacheKey`: the
    construction lives there, so every cache keys the same four things
    the same way.  The signatures are byte-identical.
    """

    def to_dict(self) -> dict[str, str]:
        return {name: getattr(self, name) for name in KEY_COMPONENTS}

    @classmethod
    def from_dict(cls, document: Any) -> AnalysisKey | None:
        """The key a stored document carries, or None when it carries none.

        A document with a missing or empty component is not a key: it is a
        file that cannot be trusted to say what it measured, and treating
        it as one would serve it as current.
        """
        parsed = _canonical.CacheKey.from_dict(document)
        if parsed is None:
            return None
        return cls(**parsed.to_dict())


def differing_components(cached: Any, wanted: AnalysisKey) -> list[str]:
    """Which of the four components of the key differ.

    An empty list means the entry is CURRENT for the wanted key.  A
    non-empty one names what moved, so a reader is told WHY the entry was
    not used instead of only that it was not.
    """
    return _canonical.differing_components(cached, wanted)


# ── the store ─────────────────────────────────────────────────────────────


def cache_path(source_digest: str, root: str | Path | None = None) -> Path:
    """Where one source's cached analyses live.

    The source memory owns the layout, so the cache sits beside the other
    measurements of the same content digest and follows the same project
    scoping they follow.
    """
    from library.tools import source_memory

    return source_memory.source_dir(source_digest, root) / CACHE_FILENAME


def read_store(source_digest: str, root: str | Path | None = None) -> dict:
    """The store for one source, or an empty one.

    A file that is not a store this module wrote is ignored rather than
    half-read: its entries carry no key, and an entry with no key cannot
    be reused as current.
    """
    from library.tools import source_memory

    document = source_memory.read_slot(cache_path(source_digest, root))
    if not isinstance(document, dict):
        return {}
    if document.get("format") != CACHE_FORMAT:
        return {}
    if document.get("content_digest") != source_digest:
        return {}
    entries = document.get("entries")
    return entries if isinstance(entries, dict) else {}


def write_store(source_digest: str, entries: dict, *,
                root: str | Path | None = None) -> None:
    """Write one source's store.

    The write is atomic (the source memory's writer), so a killed process
    never leaves a half-written store that reads as a cache hit.
    """
    from library.tools import source_memory

    source_memory.write_json(cache_path(source_digest, root), {
        "format": CACHE_FORMAT,
        "content_digest": source_digest,
        "entries": entries,
    })


def lookup_frames(source_digest: str, key: AnalysisKey,
                  frame_indices: list[int], *,
                  root: str | Path | None = None) -> dict[int, dict]:
    """The cached measurement of each named frame, for this key only.

    A frame is served only when the entry's key IS the wanted key: an
    entry measured by another model, another configuration or another
    analysis version is a DIFFERENT measurement, and serving it would
    answer a question nobody asked.
    """
    entries = read_store(source_digest, root)
    entry = entries.get(key.signature())
    if not isinstance(entry, dict):
        return {}
    if AnalysisKey.from_dict(entry.get("key")) != key:
        return {}
    frames = entry.get("frames")
    if not isinstance(frames, dict):
        return {}
    hits: dict[int, dict] = {}
    for index in frame_indices:
        row = frames.get(str(index))
        if isinstance(row, dict):
            hits[int(index)] = row
    return hits


def store_frames(source_digest: str, key: AnalysisKey, *,
                 producer: dict[str, Any], source_fps: float,
                 sample_rate_hz: float, frames: dict[int, dict],
                 root: str | Path | None = None) -> None:
    """File the frames one measurement produced, under this key.

    A frame already filed under this key is kept as it is: the same
    measurement taken twice is one measurement, and the second one says
    nothing the first did not.
    """
    entries = read_store(source_digest, root)
    entry = entries.get(key.signature())
    if not isinstance(entry, dict) or \
            AnalysisKey.from_dict(entry.get("key")) != key:
        entry = {
            "key": key.to_dict(),
            "signature": key.signature(),
            "producer": dict(producer or {}),
            "source_fps": float(source_fps),
            "sample_rate_hz": float(sample_rate_hz),
            "frames": {},
        }
    stored = entry.get("frames")
    if not isinstance(stored, dict):
        stored = {}
    for index, row in (frames or {}).items():
        stored[str(int(index))] = row
    entry["frames"] = stored
    entries[key.signature()] = entry
    write_store(source_digest, entries, root=root)


class FrameCache:
    """The cached frames of ONE key, for ONE source.

    The lane that measures asks the cache for the frames it needs and
    files the ones it measured; it never decides whether a request is
    worth measuring, and it never reads another key's frames.
    """

    def __init__(self, source_digest: str, key: AnalysisKey, *,
                 root: str | Path | None = None, enabled: bool = True):
        self.source_digest = source_digest
        self.key = key
        self.root = root
        self.enabled = bool(enabled)
        self.reused: list[int] = []
        self.measured: list[int] = []

    def lookup(self, frame_indices: list[int]) -> dict[int, dict]:
        """The frames this key already holds, and the ones it does not."""
        self.reused = []
        if not self.enabled:
            return {}
        hits = lookup_frames(self.source_digest, self.key, frame_indices,
                             root=self.root)
        self.reused = sorted(hits)
        return hits

    def store(self, frames: dict[int, dict], *, producer: dict[str, Any],
              source_fps: float, sample_rate_hz: float) -> None:
        """File the frames this measurement produced.

        The frames are counted whether or not the cache is on: a run with
        the cache off still MEASURED those frames, and a reader asking
        how many frames a pass cost is not answered by a cache
        bookkeeping detail.
        """
        self.measured = sorted(frames)
        if not self.enabled or not frames:
            return
        store_frames(self.source_digest, self.key, producer=producer,
                     source_fps=source_fps, sample_rate_hz=sample_rate_hz,
                     frames=frames, root=self.root)

    def report(self) -> dict[str, Any]:
        """What the cache did, for the run's own record.

        The counts are the measurement: how many frames were reused and
        how many were measured.  A run that reused everything says so
        here, and a reader is not left to infer it from a zero.
        """
        return {
            "signature": self.key.signature(),
            "key": self.key.to_dict(),
            "enabled": self.enabled,
            "reused_frames": sorted(self.reused),
            "measured_frames": sorted(self.measured),
            "hits": len(self.reused),
            "misses": len(self.measured),
        }


def cache_enabled() -> bool:
    """Whether the shared analysis cache is on.

    ``REN_ANALYSIS_CACHE=off`` turns it off, and each lane then measures
    everything it is asked for - the escape hatch a benchmark's
    independent arm needs.
    """
    raw = os.environ.get("REN_ANALYSIS_CACHE")
    if raw is None:
        return True
    return raw.strip().lower() not in {"0", "off", "no", "false"}
