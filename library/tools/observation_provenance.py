"""Where an observation came from: the source file, and the exact seconds.

The pieces of provenance already exist and each of them is load-bearing:
an observation names its asset and its interval on a named clock, an
asset names its URI and its content digest, a clock names its domain
(source, project, timeline, render), and a time transform maps one clock
onto another with an exact rational slope and an uncertainty bound.

What was missing was the ONE place that puts them together. A reader
that wants to know "which file, and which seconds" had to know the
model's four collections, the transform algebra in
``project_model_contract``, and the difference between a source clock
and a timeline one - and every reader that assembled that answer
differently assembled it differently.

This module is that place. :func:`resolve` answers for one
observation; :func:`resolve_all` answers for the project's evidence in
one pass.

**Absent is absent.** An observation whose clock has no path back to a
source answers with a reason and NO file and NO seconds. It never
answers with a zero, with the asset's whole duration, or with a
nearest-neighbour interval: a reader that cannot place an observation
must be able to see that it cannot.

**Exact, and then rounded once.** The mapping is `Fraction` arithmetic
throughout, and the seconds are rounded at the very end, so an interval
that starts at 1/3 of a frame does not drift by rounding on the way
there.

**The uncertainty travels.** A source interval reached through a
measured transform carries the transform's uncertainty bound, widened
when the chain is longer than one segment. A reader that wants to know
how sure the seconds are is told, rather than left to assume they are
exact.
"""

from __future__ import annotations

import json
import math
import os
from fractions import Fraction
from pathlib import Path
from typing import Any

from library.tools.project_model_contract import TimeMap, compose_time_maps

FORMAT = "observation_provenance/1"
"""The resolution's own format tag, so a reader can refuse an unknown one."""

UNRESOLVED = "unresolved"
RESOLVED = "resolved"


class ObservationProvenanceError(ValueError):
    """A provenance request that cannot be answered as asked."""


# ── The model's collections, indexed ─────────────────────────────────


def _index(model: dict[str, Any]) -> dict[str, Any]:
    """The model's rows by id, once, so a resolution does not re-scan."""
    return {
        "observations": {row["id"]: row for row in model.get("observations", [])
                         if isinstance(row, dict) and row.get("id")},
        "clocks": {row["id"]: row for row in model.get("clocks", [])
                   if isinstance(row, dict) and row.get("id")},
        "assets": {row["id"]: row for row in model.get("assets", [])
                   if isinstance(row, dict) and row.get("id")},
        "streams": {row["id"]: row for row in model.get("streams", [])
                    if isinstance(row, dict) and row.get("id")},
        "transforms": [row for row in model.get("time_transforms", [])
                       if isinstance(row, dict)],
    }


def _source_clocks(index: dict[str, Any]) -> set[str]:
    """The clocks that sit on a SOURCE, and therefore on a real file."""
    out = set()
    for clock_id, clock in index["clocks"].items():
        domain = clock.get("domain") or {}
        if domain.get("kind") == "source" and domain.get("asset_id"):
            out.add(clock_id)
    return out


def _segment_maps(transform: dict[str, Any]) -> list[TimeMap]:
    """The transform's segments as the contract's time maps.

    The algebra belongs to ``project_model_contract``: this module only
    reads the model's rows into it, so the slope, the offset and the
    uncertainty are interpreted the one way the contract interprets
    them.

    A segment the contract refuses - an empty range, a non-positive
    slope - is SKIPPED, and the refusal is not allowed to become a
    mapping that quietly means something else.
    """
    out = []
    for segment in transform.get("segments") or []:
        if not isinstance(segment, dict):
            continue
        source = segment.get("from") or {}
        target = segment.get("to") or {}
        slope = segment.get("slope") or {}
        try:
            out.append(TimeMap(
                source_clock=transform.get("from_clock"),
                target_clock=transform.get("to_clock"),
                source_start=int(source["start"]),
                source_end=int(source["end"]),
                target_start=int(target["start"]),
                target_end=int(target["end"]),
                slope=Fraction(int(slope["numerator"]), int(slope["denominator"])),
                uncertainty_ticks=int(segment.get("uncertainty_ticks") or 0),
                offset_ticks=Fraction(int(segment.get("offset_ticks") or 0)),
                invertible=bool(segment.get("invertible", True))))
        except (KeyError, TypeError, ValueError):
            continue
    return out


def _chain_to_source(index: dict[str, Any], clock_id: str) -> list[TimeMap] | None:
    """The maps from a SOURCE clock up to ``clock_id``, in that order.

    The chain is found by walking the transforms backwards from the
    clock the observation sits on, because that is the direction a
    reader has: they hold a timeline observation and want the source
    file. A cycle refuses rather than looping.
    """
    source_clocks = _source_clocks(index)
    if clock_id in source_clocks:
        return []
    # {target clock: [(transform, segment map)]} - the edges a reader can
    # walk backwards.
    incoming: dict[str, list[tuple[dict[str, Any], TimeMap]]] = {}
    for transform in index["transforms"]:
        for segment_map in _segment_maps(transform):
            incoming.setdefault(segment_map.target_clock, []).append(
                (transform, segment_map))
    # Breadth-first, nearest first, so the shortest chain wins.
    queue: list[tuple[str, list[TimeMap]]] = [(clock_id, [])]
    seen = {clock_id}
    while queue:
        current, chain = queue.pop(0)
        for transform, segment_map in incoming.get(current, []):
            forward = [segment_map] + chain
            if segment_map.source_clock in source_clocks:
                return forward
            if segment_map.source_clock in seen:
                continue
            seen.add(segment_map.source_clock)
            queue.append((segment_map.source_clock, forward))
    return None


def _compose(chain: list[TimeMap]) -> TimeMap | None:
    """One map from the source clock to the observation's clock."""
    if not chain:
        return None
    composed = chain[0]
    for nxt in chain[1:]:
        try:
            composed = compose_time_maps(composed, nxt)
        except ValueError:
            return None
    return composed


# ── The source file ─────────────────────────────────────────────────


def _catalog_by_digest(project_folder: Path) -> dict[str, str]:
    """``{content_digest: source path}`` from the project's own records.

    The catalog is the project's record of where its media is; the
    runner's recorded fingerprints are the same thing seen at ingest.
    A digest that neither knows is ABSENT, never a placeholder path.
    """
    out: dict[str, str] = {}
    try:
        from library.tools import source_memory
        recorded = source_memory.load_recorded_fingerprints(str(project_folder))
    except (ImportError, OSError, ValueError):
        recorded = {}
    for entry in (recorded or {}).values():
        if not isinstance(entry, dict):
            continue
        digest = entry.get("content_digest")
        path = entry.get("path")
        if digest and path:
            out.setdefault(digest, str(path))
    try:
        from library.tools.analysis.footage_segments import load_catalog
        catalog = load_catalog(str(project_folder))
    except (ImportError, OSError, ValueError):
        catalog = []
    for clip in catalog or []:
        if not isinstance(clip, dict):
            continue
        path = clip.get("source_file") or clip.get("path")
        if not path:
            continue
        try:
            from library.tools import footage_identity
            digest = footage_identity.fingerprint(str(path))["content_digest"]
        except (ImportError, OSError, ValueError):
            continue
        out.setdefault(digest, str(path))
    return out


def _source_file(project_folder: Path | None, asset: dict[str, Any] | None,
                 catalog: dict[str, str]) -> str | None:
    """The file an asset's bytes live in, or None.

    The asset's own URI wins: it is the model's record of the file. A
    relative URI is resolved against the project folder, because a
    model's URI is project-relative by construction; with no project
    folder the URI travels as the model recorded it. When the model
    names no file, the project's catalog is asked by content digest,
    which is identity rather than a name.
    """
    uri = (asset or {}).get("uri")
    if isinstance(uri, str) and uri:
        if os.path.isabs(uri) or project_folder is None:
            return uri
        return str((project_folder / uri).resolve())
    digest = (asset or {}).get("content_digest")
    if isinstance(digest, str) and digest:
        return catalog.get(digest)
    return None


# ── The resolution ──────────────────────────────────────────────────


def _unresolved(observation_id: str, reason: str, **extra: Any) -> dict[str, Any]:
    out = {"format": FORMAT, "observation_id": observation_id,
           "status": UNRESOLVED, "reason": reason,
           "source_file": None, "content_digest": None,
           "start_seconds": None, "end_seconds": None,
           "duration_seconds": None, "uncertainty_ticks": None}
    out.update(extra)
    return out


def _seconds(ticks: Fraction | int, ticks_per_second: int) -> float:
    return float(Fraction(ticks) / Fraction(int(ticks_per_second)))


def _resolve_one(project_folder: Path | None, observation: dict[str, Any],
                 index: dict[str, Any], catalog: dict[str, str]
                 ) -> dict[str, Any]:
    """One observation to its source file and exact source seconds."""
    observation_id = observation.get("id")
    interval = observation.get("interval") or {}
    clock_id = interval.get("clock_id")
    clock = index["clocks"].get(clock_id)
    if clock is None:
        return _unresolved(observation_id, "the observation names a clock "
                           "the model does not have")
    ticks_per_second = clock.get("ticks_per_second")
    if not isinstance(ticks_per_second, int) or ticks_per_second <= 0:
        return _unresolved(observation_id, "the clock names no tick rate")
    domain = clock.get("domain") or {}
    start = interval.get("start")
    end = interval.get("end")
    if not isinstance(start, int) or not isinstance(end, int) or end <= start:
        return _unresolved(observation_id, "the observation's interval is not "
                           "a non-empty half-open tick range")

    source_clock_id = None
    source_start = start
    source_end = end
    uncertainty = 0
    if domain.get("kind") == "source":
        source_clock_id = clock_id
    else:
        chain = _chain_to_source(index, clock_id)
        if chain is None:
            return _unresolved(
                observation_id,
                f"no measured transform carries the {clock_id} clock back to "
                "a source clock")
        composed = _compose(chain)
        if composed is None:
            return _unresolved(observation_id,
                               "the transforms do not compose into one chain")
        source_clock_id = composed.source_clock
        try:
            inverse = composed.inverse()
        except ValueError:
            return _unresolved(observation_id,
                               "the transform chain is not invertible")
        try:
            first = inverse.map_tick(start)
            last = inverse.map_tick(end - 1)
        except ValueError:
            return _unresolved(
                observation_id,
                "the observation falls outside the transform's coverage")
        source_start = math.floor(first)
        source_end = math.ceil(last) + 1
        uncertainty = int(inverse.uncertainty_ticks)

    source_clock = index["clocks"].get(source_clock_id) or {}
    asset_id = ((source_clock.get("domain") or {}).get("asset_id")
                or observation.get("asset_id"))
    asset = index["assets"].get(asset_id)
    source_file = _source_file(project_folder, asset, catalog)
    if source_file is None:
        return _unresolved(observation_id,
                           "the model names no file for this asset",
                           asset_id=asset_id, clock_id=clock_id)
    source_ticks_per_second = source_clock.get("ticks_per_second")
    if not isinstance(source_ticks_per_second, int) or source_ticks_per_second <= 0:
        source_ticks_per_second = ticks_per_second
    start_seconds = _seconds(source_start, source_ticks_per_second)
    end_seconds = _seconds(source_end, source_ticks_per_second)
    return {
        "format": FORMAT,
        "observation_id": observation_id,
        "status": RESOLVED,
        "reason": None,
        "modality": observation.get("modality"),
        "provenance_id": observation.get("provenance_id"),
        "asset_id": asset_id,
        "content_digest": (asset or {}).get("content_digest"),
        "source_file": source_file,
        "clock_id": clock_id,
        "source_clock_id": source_clock_id,
        "start_seconds": round(start_seconds, 6),
        "end_seconds": round(end_seconds, 6),
        "duration_seconds": round(end_seconds - start_seconds, 6),
        "uncertainty_ticks": uncertainty or None,
    }


def _load_model(project_folder: Path) -> dict[str, Any] | None:
    try:
        from library.tools import project_model_store
        _revision, model = project_model_store.load_project_model(project_folder)
    except (FileNotFoundError, ValueError, KeyError, OSError):
        return None
    return model


def resolve_in_model(model: dict[str, Any], observation: dict[str, Any], *,
                     project_folder: str | os.PathLike | None = None
                     ) -> dict[str, Any]:
    """Where one observation came from, read off a model already in hand.

    The model-only half: a caller that already holds the model (an
    evidence query answering inside a project) does not have to read it
    again. ``project_folder`` is what turns a project-relative URI into
    a path a reader can open.
    """
    if not isinstance(model, dict):
        raise ObservationProvenanceError("a project model is needed")
    if not isinstance(observation, dict) or not observation.get("id"):
        raise ObservationProvenanceError("an observation is needed")
    root = Path(project_folder) if project_folder is not None else None
    index = _index(model)
    catalog = _catalog_by_digest(root) if root is not None else {}
    return _resolve_one(root, observation, index, catalog)


def source_placements(model: dict[str, Any], observations: list[dict[str, Any]], *,
                      project_folder: str | os.PathLike | None = None
                      ) -> list[dict[str, Any]]:
    """Where several observations came from, in one pass.

    The reader that already holds the model and a list of observations -
    an evidence query answering inside a project - uses this, and the
    model's index is built once.
    """
    if not isinstance(model, dict):
        raise ObservationProvenanceError("a project model is needed")
    root = Path(project_folder) if project_folder is not None else None
    index = _index(model)
    catalog = _catalog_by_digest(root) if root is not None else {}
    out = []
    for observation in observations or []:
        if not isinstance(observation, dict) or not observation.get("id"):
            continue
        out.append(_resolve_one(root, observation, index, catalog))
    return out


def resolve(project_folder: str | os.PathLike, observation_id: str) -> dict[str, Any]:
    """Where one observation came from: the file, and the exact seconds.

    ``{"status": "resolved", "source_file": ..., "start_seconds": ...,
    "end_seconds": ...}`` when the observation can be placed, and
    ``{"status": "unresolved", "reason": ...}`` when it cannot - with
    no file and no seconds, so a reader cannot mistake an unanswered
    question for an answer.
    """
    if not observation_id or not isinstance(observation_id, str):
        raise ObservationProvenanceError(
            "an observation id is needed to resolve provenance")
    root = Path(project_folder)
    model = _load_model(root)
    if model is None:
        return _unresolved(observation_id,
                           "this project has no Project Model, so it has no "
                           "observations to place")
    index = _index(model)
    observation = index["observations"].get(observation_id)
    if observation is None:
        return _unresolved(observation_id,
                           "no observation with this id is in the model")
    return _resolve_one(root, observation, index, _catalog_by_digest(root))


def resolve_all(project_folder: str | os.PathLike,
                observation_ids: list[str] | None = None) -> dict[str, Any]:
    """Every observation's provenance, in one pass.

    ``{observation_id: resolution}``. An observation that cannot be
    placed is in the answer with its reason, so a caller that asks
    "where did everything come from" learns about the holes too.
    """
    root = Path(project_folder)
    model = _load_model(root)
    if model is None:
        return {"format": FORMAT, "project": str(root),
                "model_revision": None, "resolutions": {},
                "unresolved": []}
    index = _index(model)
    catalog = _catalog_by_digest(root)
    wanted = set(observation_ids) if observation_ids else None
    resolutions: dict[str, Any] = {}
    for observation_id, observation in index["observations"].items():
        if wanted is not None and observation_id not in wanted:
            continue
        resolutions[observation_id] = _resolve_one(
            root, observation, index, catalog)
    return {
        "format": FORMAT,
        "project": str(root),
        "resolutions": resolutions,
        "resolved": sorted(name for name, row in resolutions.items()
                           if row["status"] == RESOLVED),
        "unresolved": sorted(name for name, row in resolutions.items()
                             if row["status"] == UNRESOLVED),
    }


def main(argv=None) -> int:
    """``resolve <project> <observation_id>``: where one observation came from."""
    import argparse
    import sys

    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    one = sub.add_parser("resolve", help="Where one observation came from")
    one.add_argument("project")
    one.add_argument("observation_id")
    every = sub.add_parser("resolve-all",
                            help="Where every observation came from")
    every.add_argument("project")
    args = parser.parse_args(argv)
    if args.command == "resolve":
        print(json.dumps(resolve(args.project, args.observation_id),
                         indent=2, sort_keys=True))
        return 0
    if args.command == "resolve-all":
        print(json.dumps(resolve_all(args.project), indent=2, sort_keys=True))
        return 0
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
