"""Process-local model residency, reuse accounting, and safe eviction.

A model is cached only while a caller holds a managed lease or an explicit
resident batch scope. This keeps reuse within a measured batch while avoiding
an unbounded idle cache beside Resolve. Loader calls are serialized per
process, managed callers share one instance, and eviction waits for active
callers to release it.
"""

from __future__ import annotations

import contextlib
import gc
import logging
import resource
import sys
import threading
import time
from collections import defaultdict
from typing import Any

_loaded_models: dict[str, Any] = {}
_resident_models: set[str] = set()
_resident_counts: dict[str, int] = defaultdict(int)
_active_users: dict[str, int] = defaultdict(int)
_pending_eviction: set[str] = set()
_stats: dict[str, dict[str, Any]] = {}
logger = logging.getLogger(__name__)

_lock = threading.RLock()
_model_locks_guard = threading.Lock()
_model_locks: dict[str, threading.Lock] = {}


def _record(name: str) -> dict[str, Any]:
    with _lock:
        return _stats.setdefault(
            name,
            {
                "residency_policy": "scoped",
                "loads": 0,
                "load_failures": 0,
                "cache_hits": 0,
                "managed_uses": 0,
                "evictions": 0,
                "load_seconds": 0.0,
                "peak_process_rss_mb": None,
                "last_used_at": None,
                "loaded_at": None,
            },
        )


def _peak_rss_mb() -> float | None:
    try:
        scale = 1 / (1024 * 1024) if sys.platform == "darwin" else 1 / 1024
        return round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * scale, 1)
    except (AttributeError, OSError):
        return None


def _torch():
    """Return torch only if an actual model loader imported it."""
    return sys.modules.get("torch")


def _empty_gpu_cache():
    torch = _torch()
    if torch is None:
        return
    try:
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
        elif hasattr(torch.backends, "mps") and torch.backends.mps.is_available():
            torch.mps.empty_cache()
    except (AttributeError, RuntimeError):
        # Cache clearing is best-effort; the model reference is already gone.
        pass


def _move_to_cpu(model):
    torch = _torch()
    if torch is None:
        return
    candidates = model if isinstance(model, tuple) else (model,)
    for item in candidates:
        if hasattr(item, "to"):
            try:
                item.to("cpu")
            except Exception:
                logger.debug("Could not move model component to CPU", exc_info=True)


def _model_lock(name: str) -> threading.Lock:
    """Serialize one model's loader without throttling unrelated models."""
    with _model_locks_guard:
        return _model_locks.setdefault(name, threading.Lock())


def _load_model(
    name: str,
    loader_fn,
    *,
    acquire: bool = False,
    pin: bool = False,
    require_pin: bool = False,
):
    stats = _record(name)
    with _model_lock(name):
        with _lock:
            if require_pin and name not in _resident_models:
                raise RuntimeError(
                    f"model {name!r} requires an active resident_model scope"
                )
            if name in _loaded_models:
                model = _loaded_models[name]
                stats["cache_hits"] += 1
                stats["last_used_at"] = time.time()
                if acquire:
                    _active_users[name] += 1
                    stats["managed_uses"] += 1
                if pin:
                    _resident_counts[name] += 1
                    _resident_models.add(name)
                from library.tools import perf_ledger

                with perf_ledger.span(
                    "model_cache", model=name, cache_hit=True, loads=stats["loads"]
                ):
                    return model

        print(f"[{name}] Loading model into memory...", file=sys.stderr)
        from library.tools import perf_ledger

        started = time.perf_counter()
        try:
            with perf_ledger.span(
                "model_load", model=name, cold_start=True, cache_hit=False
            ) as row:
                model = loader_fn()
                row["process_peak_rss_mb"] = _peak_rss_mb()
        except BaseException:
            with _lock:
                stats["load_failures"] += 1
            raise
        elapsed = time.perf_counter() - started
        with _lock:
            stats["loads"] += 1
            stats["load_seconds"] = round(stats["load_seconds"] + elapsed, 4)
            stats["peak_process_rss_mb"] = _peak_rss_mb()
            stats["loaded_at"] = time.time()
            stats["last_used_at"] = stats["loaded_at"]
            _loaded_models[name] = model
            if acquire:
                _active_users[name] += 1
                stats["managed_uses"] += 1
            if pin:
                _resident_counts[name] += 1
                _resident_models.add(name)
        return model


def load_model(name: str, loader_fn):
    """Read a model inside an explicit resident batch scope.

    Other call sites should use `managed_model`, which releases the model
    after its final active caller exits.
    """
    with _lock:
        if name not in _resident_models:
            raise RuntimeError(
                f"model {name!r} requires an active resident_model scope"
            )
        return _load_model(name, loader_fn)


def _evict_locked(name: str) -> bool:
    if name not in _loaded_models:
        _pending_eviction.discard(name)
        return False
    if _active_users.get(name, 0):
        _pending_eviction.add(name)
        return False
    model = _loaded_models.pop(name)
    _pending_eviction.discard(name)
    _record(name)["evictions"] += 1
    _move_to_cpu(model)
    del model
    gc.collect()
    _empty_gpu_cache()
    return True


def unload_model(name: str):
    """Request eviction; active managed callers finish before memory is freed."""
    with _lock:
        _resident_models.discard(name)
        _resident_counts.pop(name, None)
        if name in _loaded_models:
            print(f"[{name}] Unloading model from memory...", file=sys.stderr)
        return _evict_locked(name)


def unload_all():
    """Request eviction of all models, deferring any in active use."""
    with _lock:
        names = list(_loaded_models)
        _resident_models.clear()
        _resident_counts.clear()
        if names:
            print(f"Unloading all models: {', '.join(names)}", file=sys.stderr)
        for name in names:
            _evict_locked(name)


def resource_accounting() -> dict[str, dict[str, Any]]:
    """Return per-model counters, residency and a process RSS high-water mark.

    The RSS value is a process measurement at load time, not an isolated
    allocation measurement. The scheduler's grant history is authoritative
    for per-capability physical-footprint peaks.
    """
    with _lock:
        return {
            name: {
                **values,
                "resident": name in _loaded_models,
                "pinned": name in _resident_models,
                "active_users": _active_users.get(name, 0),
                "eviction_pending": name in _pending_eviction,
            }
            for name, values in _stats.items()
        }


@contextlib.contextmanager
def managed_model(name: str, loader_fn):
    """Lease one model instance and evict it when its final caller exits."""
    model = _load_model(name, loader_fn, acquire=True)
    try:
        yield model
    finally:
        with _lock:
            _active_users[name] -= 1
            _record(name)["last_used_at"] = time.time()
            if not _active_users[name] and (
                name in _pending_eviction or name not in _resident_models
            ):
                _evict_locked(name)


@contextlib.contextmanager
def resident_model(name: str, loader_fn):
    """Pin a model for a bounded batch; active managed calls share its load."""
    model = _load_model(name, loader_fn, pin=True)
    try:
        yield model
    finally:
        with _lock:
            _resident_counts[name] -= 1
            if _resident_counts[name] <= 0:
                _resident_counts.pop(name, None)
                _resident_models.discard(name)
                if not _active_users.get(name, 0):
                    _evict_locked(name)
