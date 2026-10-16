"""Content-addressed incremental store for derived video representations.

TMP-041: every derived representation must be incrementally rebuildable.
A cache entry is keyed by what produced it - source identity, requested
interval, model identity, precision and the adapter code version - so a
missing or stale entry is rebuilt for exactly its own interval, never by
re-running the whole source. The store holds no footage and no creative
judgement, only measured tensors with their provenance.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np

ADAPTER_VERSION = "video-features-cache-v1"


class CacheMiss(Exception):
    """No usable cached representation exists for this exact request."""


def cache_key(*, source_path: str, byte_size: int, mtime_ns: int,
              start: float, end: float, model_id: str,
              precision: str) -> str:
    """Stable key for one interval's derived representation."""
    canonical = json.dumps({
        "adapter": ADAPTER_VERSION,
        "source_path": source_path,
        "byte_size": byte_size,
        "mtime_ns": mtime_ns,
        "start": round(float(start), 3),
        "end": round(float(end), 3),
        "model_id": model_id,
        "precision": precision,
    }, sort_keys=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def entry_path(cache_dir: Path, key: str) -> Path:
    return cache_dir / f"{key}.npz"


def store(cache_dir: Path, key: str, arrays: dict[str, np.ndarray],
          provenance: dict) -> Path:
    """Write one interval's tensors with provenance; returns the path."""
    cache_dir.mkdir(parents=True, exist_ok=True)
    path = entry_path(cache_dir, key)
    payload = dict(arrays)
    payload["__provenance__"] = np.array(
        json.dumps(provenance, sort_keys=True))
    np.savez_compressed(path, **payload)
    return path


def load(cache_dir: Path, key: str) -> tuple[dict[str, np.ndarray], dict]:
    """Read one entry; raises CacheMiss when absent or unreadable."""
    path = entry_path(cache_dir, key)
    if not path.is_file():
        raise CacheMiss(key)
    try:
        with np.load(path, allow_pickle=False) as stored:
            arrays = {name: stored[name] for name in stored.files
                      if name != "__provenance__"}
            provenance = json.loads(str(stored["__provenance__"]))
    except (OSError, ValueError, KeyError) as exc:
        raise CacheMiss(f"{key}: {exc}") from exc
    if not arrays:
        raise CacheMiss(f"{key}: entry holds no tensors")
    return arrays, provenance


def missing_keys(cache_dir: Path, keys: list[str]) -> list[str]:
    """Which of the requested keys still need a rebuild."""
    return [key for key in keys if not entry_path(cache_dir, key).is_file()]
