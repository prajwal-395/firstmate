"""The ResolveFX/OFX effects provider registry, as data the planner can query.

The registry has two layers:

  * Machine-independent data (schema, category rules, intent-word mapping)
    lives in the repo.
  * The installed effects list is built on the user's machine and cached
    per machine at ``~/.cache/ren/resolvefx_effects.json``. It is NOT
    committed to the repo, because each machine's installed plugins
    differ.

The installed list is read through Ren's own Resolve path: the
``fusion.registry_summary`` broker job (``library/tools/resolved/jobs.py``),
which runs a Ren-owned, read-only script on the broker's Resolve
connection and returns the data as the job result. Ren never starts a
broker itself - where none is serving, ``EffectsCacheUnavailable`` says
so and names the broker.

The cache is keyed by the Resolve version it was built against and is
rebuilt whenever the running version changes. A newly installed plugin
is picked up by the lookup itself: ``find_by_intent`` refreshes the
cache once per process when a query returns nothing, then retries.

Each entry records one effect: its id and name as Resolve shows it, its
category, how it is applied (api where the scripting API can, otherwise
ui), its parameters with type/range/default where known, whether it is
installed (present in the Fusion registry on this machine), whether it
is verified (an application of it was actually read back), and the
resolve_versions each was observed on.

The planner calls :func:`find_by_intent` with intent words (e.g. "blur",
"transition", "mask") and gets back matching entries with their
application method, parameters, and verification status.

Apply-by-search resolves through :func:`resolve_exact` instead: the
Effects Library route applies ONE named effect, so a fuzzy lookup
returning a near-miss first would apply the wrong effect. An exact
query that names nothing installed, or names more than one entry, is
refused rather than guessed at.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import List, Optional

from library.schemas.resolvefx_effect import (
    EffectEntry,
    SchemaError,
    validate_registry,
)
from library.tools.paths import PRESETS_ROOT

REGISTRY_PATH = PRESETS_ROOT / "resolvefx-effects" / "registry.json"
CACHE_PATH = Path(os.path.expanduser("~/.cache/ren/resolvefx_effects.json"))

_cached_entries: Optional[List[EffectEntry]] = None
_miss_refresh_done = False


def registry_path() -> Path:
    """The path to the machine-independent registry file."""
    return REGISTRY_PATH


def cache_path() -> Path:
    """The path to the per-machine installed effects cache."""
    return CACHE_PATH


def _category_for(name: str) -> str:
    n = name.lower()
    if any(w in n for w in ['blur', 'defocus', 'depth blur']):
        return 'blur'
    if any(w in n for w in ['grain', 'noise', 'damage', 'dirt', 'dust', 'scanline', 'flicker', 'dead pixel']):
        return 'damage'
    if any(w in n for w in ['glow', 'light', 'halation', 'lens flare', 'lens reflection']):
        return 'light'
    if any(w in n for w in ['sharpen', 'soften', 'edge detect', 'detail']):
        return 'sharpen'
    if any(w in n for w in ['color', 'white balance', 'hue', 'brightness', 'contrast', 'gamma', 'gamut', 'false color', 'pseudo color', 'split tone', 'highlight', 'hot spot', 'invert', 'replace color', 'recolor', 'match color', 'color space', 'aces', 'ocio', 'dctl', 'lut']):
        return 'color'
    if any(w in n for w in ['transform', 'crop', 'resize', 'letterbox', 'position', 'rotate', 'scale', 'mirror', 'flip']):
        return 'transform'
    if any(w in n for w in ['time', 'speed', 'retime', 'flow', 'smooth motion']):
        return 'time'
    if any(w in n for w in ['stabiliz', 'shake']):
        return 'stabilize'
    if any(w in n for w in ['vignette']):
        return 'vignette'
    if any(w in n for w in ['dve', 'picture in picture', 'drop shadow']):
        return 'composite'
    if any(w in n for w in ['keyer', 'key', 'matte', 'despill', 'cryptomatte']):
        return 'key'
    if any(w in n for w in ['fog', 'shadow', 'reflection']):
        return 'atmosphere'
    if any(w in n for w in ['ai ', 'face']):
        return 'ai'
    if any(w in n for w in ['lens']):
        return 'lens'
    if any(w in n for w in ['film']):
        return 'film'
    if any(w in n for w in ['object']):
        return 'object'
    if any(w in n for w in ['patch', 'repair', 'frame replace', 'clean', 'remove', 'deband', 'deflicker', 'dehaze', 'wire']):
        return 'restoration'
    if any(w in n for w in ['emboss', 'threshold', 'kaleidoscope', 'mosaic', 'prism', 'vari', 'watercolor', 'pencil', 'painterly', 'plastic', 'dent', 'drip', 'worm', 'vortex', 'spherize', 'smear', 'shear', 'shuffle', 'sort', 'stretch', 'bend', 'bevel', 'contour', 'displace', 'grid', 'ripple', 'waviness']):
        return 'stylize'
    if any(w in n for w in ['channel', 'alpha']):
        return 'channel'
    return 'effect'


def _slugify(name: str) -> str:
    import re
    s = name.lower()
    s = re.sub(r'[^a-z0-9]+', '_', s)
    s = s.strip('_')
    if s and s[0].isdigit():
        s = f"effect_{s}"
    return s


def _api_method_for(name: str) -> Optional[str]:
    api_effects = {
        'Magic Mask': 'TimelineItem.CreateMagicMask',
        'Spherical Stabilizer': 'TimelineItem.Stabilize',
        'Camera Shake': 'TimelineItem.Stabilize',
    }
    return api_effects.get(name)


class EffectsCacheUnavailable(RuntimeError):
    """The installed effects cache is missing and could not be built."""


class UnknownEffect(RuntimeError):
    """No installed effect has the requested name - refused, not guessed."""


class AmbiguousEffect(RuntimeError):
    """More than one installed effect shares the requested name.

    The same effect name ships in more than one bundle (OFX vendors
    reuse names), so the recipe cannot pick one - the caller names
    which entry it meant.
    """


def _normalize_name(name: str) -> str:
    """One effect name for comparison: case-insensitive, internal
    whitespace collapsed.

    ``"  Gaussian  BLUR "`` and ``"gaussian blur"`` name the same
    effect; ``"Gaussian Blur"`` and ``"Lens Blur"`` do not.
    """
    return " ".join(str(name).split()).casefold()


def _fetch_registry_summary() -> dict:
    """The running Resolve's version and installed effects, via the broker.

    Reaches Resolve through Ren's own path - the ``fusion.registry_summary``
    job on the serving broker, the same lease-holding read every other
    Ren read uses. Raises `EffectsCacheUnavailable` where no production
    broker is serving; Ren never starts one.
    """
    from library.tools.resolved import client
    if not client.serving():
        raise EffectsCacheUnavailable(
            "no ren-resolved broker is serving, so the installed effects "
            "cannot be read from Resolve; start one with `ren resolved "
            "serve` - Ren never starts a broker itself")
    submitted = client.submit("fusion.registry_summary", {})
    job = client.result(submitted["id"], wait=0.0)
    while job["state"] in ("queued", "running"):
        job = client.result(submitted["id"], wait=60.0)
    if job["state"] != "done":
        raise EffectsCacheUnavailable(
            f"the registry summary job ended {job['state']}: "
            f"{job.get('error') or 'no error recorded'}")
    return job["result"]


def _entries_from_summary(summary: dict) -> List[EffectEntry]:
    """Build the installed EffectEntry list from a registry summary.

    The entries are built here, on Ren's side, so the cache holds one
    format: the final EffectEntry list, not the raw registry rows.
    """
    version = summary.get("version") or ""
    entries = []
    seen_ids = set()
    for effect in summary.get("effects", []):
        name = effect["name"]
        effect_id = _slugify(name)
        if effect_id in seen_ids:
            effect_id = f"{effect_id}_{len(seen_ids)}"
        seen_ids.add(effect_id)

        api_method = _api_method_for(name)
        application = 'api' if api_method else 'ui'

        entry = EffectEntry(
            id=effect_id,
            name=name,
            category=_category_for(name),
            application=application,
            description=f'ResolveFX effect: {name}',
            api_method=api_method,
            parameters=[],
            installed=True,
            installed_evidence='Fusion.GetRegSummary(0) read back',
            installed_resolve_versions=[version] if version else [],
            verified=False,
            evidence=None,
            resolve_versions=[],
            intent_words=[w for w in name.lower().split() if len(w) > 2],
        )
        entries.append(entry)
    return entries


def _write_cache(entries: List[EffectEntry], version: str) -> None:
    """Write the cache in its one format: the version plus the entries."""
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "resolve_version": version,
        "entries": [e.to_dict() for e in entries],
    }
    with open(CACHE_PATH, 'w', encoding='utf-8') as f:
        json.dump(payload, f)


def _read_cache() -> Optional[dict]:
    if not CACHE_PATH.exists():
        return None
    with open(CACHE_PATH, 'r', encoding='utf-8') as f:
        return json.load(f)


def _load_cached_installed(force: bool = False) -> List[EffectEntry]:
    """Load the installed effects, rebuilding the cache when it is stale.

    The cache is keyed by the Resolve version it was built against: the
    running version is read through the broker on every load, and a
    version change rebuilds. A cache whose version matches is used as-is.
    A cache in any other shape (an older format) is rebuilt too.
    """
    summary = _fetch_registry_summary()
    version = summary.get("version") or ""
    cached = _read_cache()
    if (not force and isinstance(cached, dict)
            and cached.get("resolve_version") == version):
        return [EffectEntry.from_dict(e) for e in cached.get("entries", [])]
    entries = _entries_from_summary(summary)
    _write_cache(entries, version)
    return entries


def refresh_installed_cache() -> List[EffectEntry]:
    """Force a rebuild of the installed effects cache, and return it."""
    return _load_cached_installed(force=True)


def load_registry(path: Optional[Path] = None,
                  refresh: bool = False) -> List[EffectEntry]:
    """Load and validate the registry.

    Returns the list of EffectEntry objects. The installed effects list
    is loaded from the per-machine cache, or rebuilt from the running
    Resolve through the broker when the cache is missing, stale, or
    `refresh` asks for a rebuild.
    """
    global _cached_entries
    if _cached_entries is not None and path is None and not refresh:
        return _cached_entries

    if path is not None:
        with open(path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        entries = [EffectEntry.from_dict(e) for e in data.get('entries', [])]
        validate_registry(entries)
        return entries

    entries = _load_cached_installed(force=refresh)
    validate_registry(entries)
    _cached_entries = entries
    return entries


def _score_entries(entries: List[EffectEntry], query_tokens: set) -> list:
    scored: list = []
    for entry in entries:
        searchable = " ".join(
            entry.intent_words
            + [entry.name, entry.category, entry.description]
        ).lower()
        search_tokens = set(searchable.split())
        overlap = query_tokens & search_tokens
        if overlap:
            scored.append((len(overlap), entry))
    scored.sort(key=lambda x: x[0], reverse=True)
    return scored


def find_by_intent(
    words: str,
    entries: Optional[List[EffectEntry]] = None,
) -> List[EffectEntry]:
    """Find registry entries matching the given intent words.

    Matches against each entry's intent_words, name, category, and
    description. Returns entries sorted by relevance (most matches first).
    An empty query returns all entries.

    An exact-name query sorts its entry first: the scored order leaves
    equal scorers in registry order, so without this an apply step
    taking the top hit could apply whichever near-miss loaded first.

    A query that matches nothing refreshes the cache once per process
    and retries, so a newly installed plugin is picked up by the lookup
    that is looking for it.
    """
    global _miss_refresh_done
    own_entries = entries is None
    if entries is None:
        entries = load_registry()

    query = words.lower().strip()
    if not query:
        return list(entries)

    query_tokens = set(query.split())
    scored = _score_entries(entries, query_tokens)
    if own_entries and not scored and not _miss_refresh_done:
        _miss_refresh_done = True
        entries = load_registry(refresh=True)
        scored = _score_entries(entries, query_tokens)
    ranked = [entry for _, entry in scored]
    want = _normalize_name(query)
    exact = [entry for entry in ranked
             if _normalize_name(entry.name) == want]
    if exact:
        rest = [entry for entry in ranked
                if all(entry is not hit for hit in exact)]
        return exact + rest
    return ranked


def resolve_exact(
    name: str,
    entries: Optional[List[EffectEntry]] = None,
) -> EffectEntry:
    """Resolve one effect by its exact name as Resolve shows it.

    The apply half of apply-by-search: the recipe applies the entry
    this returns, so a near-miss is a refusal, never a guess. An empty
    name, a name nothing installed carries, or a name more than one
    installed entry shares raises - the caller (or the captain)
    disambiguates.
    """
    if entries is None:
        entries = load_registry()

    want = _normalize_name(name)
    if not want:
        raise UnknownEffect(
            "no effect name was given - name the effect as Resolve "
            "shows it in the Effects Library")
    matches = [entry for entry in entries
               if _normalize_name(entry.name) == want]
    if not matches:
        raise UnknownEffect(
            f"no installed effect is named {name!r} - search the "
            "installed list before applying")
    if len(matches) > 1:
        ids = ", ".join(entry.id for entry in matches)
        raise AmbiguousEffect(
            f"{len(matches)} installed effects are named {name!r} "
            f"({ids}) - name the entry id instead")
    return matches[0]


def get_by_id(
    effect_id: str,
    entries: Optional[List[EffectEntry]] = None,
) -> Optional[EffectEntry]:
    """Get a single registry entry by its id, or None if not found."""
    if entries is None:
        entries = load_registry()

    for entry in entries:
        if entry.id == effect_id:
            return entry
    return None


def verified_entries(
    entries: Optional[List[EffectEntry]] = None,
) -> List[EffectEntry]:
    """Return only the verified (application read back) entries."""
    if entries is None:
        entries = load_registry()
    return [e for e in entries if e.verified]


def installed_entries(
    entries: Optional[List[EffectEntry]] = None,
) -> List[EffectEntry]:
    """Return only the installed (present in registry) entries."""
    if entries is None:
        entries = load_registry()
    return [e for e in entries if e.installed]


def clear_cache() -> None:
    """Clear the cached registry (for testing)."""
    global _cached_entries, _miss_refresh_done
    _cached_entries = None
    _miss_refresh_done = False


def _describe(entry: EffectEntry) -> str:
    """One entry on one line: what the apply step needs to route it."""
    if entry.application == "api":
        route = f"api {entry.api_method}"
    else:
        route = "ui"
    if entry.verified:
        versions = ", ".join(entry.resolve_versions)
        state = f"verified on {versions}" if versions else "verified"
    else:
        state = "unverified"
    if entry.parameters:
        params = f"{len(entry.parameters)} parameter(s): " + ", ".join(
            p.name for p in entry.parameters)
    else:
        params = "no parameters"
    return f"{entry.name} [{route}] {state}, {params}"


def main(argv=None) -> int:
    """`ren effects [words]` - search the installed ResolveFX/OFX effects.

    The registry's reader: what #1813 populated is unreachable to an
    agent except by importing internals, so nothing - no planner, no
    recipe - could ask what is installed. An empty query lists every
    installed effect; words filter by intent. Each line carries the
    application route and the verification state the apply step
    decides on. Read only: where no broker serves, the refusal names
    it instead of tracing back.
    """
    import argparse

    parser = argparse.ArgumentParser(
        prog="ren effects",
        description="Search the installed ResolveFX/OFX effects.")
    parser.add_argument("words", nargs="*",
                        help="intent words; empty lists every effect")
    args = parser.parse_args(argv)
    try:
        found = find_by_intent(" ".join(args.words))
    except EffectsCacheUnavailable as exc:
        print(str(exc))
        return 1
    if not found:
        print(f"no installed effect matches {' '.join(args.words)!r} - "
              f"list what is installed with `ren effects`")
        return 1
    for entry in found:
        print(_describe(entry))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
