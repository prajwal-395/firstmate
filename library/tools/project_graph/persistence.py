"""Project-graph persistence: cache file, guard, fingerprint, load_or_build."""
from __future__ import annotations

import json
from pathlib import Path

try:
    from library.tools import (
        source_memory,
    )
except ImportError:
    from tools import (
        source_memory,
    )
from .builder import build_graph
from .colour import _build_colour
from .core import _load_state, _read_raw_bytes, new_graph
from .editorial import _assembly_manifest_path
from .footage import _read_identity
from .history import (
    _branch_records_digest,
    _commit_records_digest,
    _reel_versions_path,
    _tag_records_digest,
)
from .operations import _event_log_path
from .variants import _variant_explorations_digest

# ── Persistence ───────────────────────────────────────────────────

GRAPH_FILE_NAME = "project_graph.json"
"""The state file the graph is written to, beside `pipeline_data.json`."""


def _graph_path(project_folder: str) -> Path:
    return Path(project_folder) / GRAPH_FILE_NAME


def _refuse_engine_checkout(project_folder: str) -> None:
    """Refuse to read or write the graph at the engine checkout itself.

    The graph is project state: it is written beside the project's
    own `pipeline_data.json`. A test that passes a cwd-relative root
    (compile_manifest's `"dummy"` out_dir resolves its project to the
    empty string) lands the write in whatever directory the suite ran
    from - the audio suite left `project_graph.json` at the repository
    root. No stranger's project ever contains this package, so a root
    that does is the engine checkout, and the call is refused before
    any read or write. The check names the package by its layout
    markers - the single module or the split package directory - so it
    holds across the structural split.
    """
    try:
        from library.tools.ren_refusal import RenRefusal
    except ImportError:
        from tools.ren_refusal import RenRefusal
    resolved = Path(project_folder).expanduser().resolve()
    markers = (
        resolved / "library" / "tools" / "project_graph.py",
        resolved / "library" / "tools" / "project_graph" / "__init__.py",
    )
    if not any(marker.is_file() for marker in markers):
        return
    raise RenRefusal(
        f"project graph root {str(resolved)!r} is the engine checkout",
        "the graph is project state, written beside the project's own "
        "pipeline_data.json - a root that contains this package is a "
        "cwd-relative test root, never a project",
        "pass the test's tmp_path project (a test builds its project "
        "under tmp_path, never the repo)")


def _input_fingerprint(project_folder: str, root: Path | None = None) -> str:
    """A fingerprint of the inputs the graph is built from.

    The fingerprint covers the catalog, the world models, the person
    tracks and the edit graph state - every input that can change the
    graph. It is a SHA-256 of the canonical JSON of those inputs, so
    two runs over the same inputs produce the same fingerprint.
    """
    import hashlib
    catalog = source_memory.load_catalog(project_folder)
    recorded = source_memory.load_recorded_fingerprints(project_folder)
    state = _load_state(project_folder)

    inputs = {
        "catalog": catalog,
        "recorded": recorded,
        "state": state,
        # The colour track's output is a graph input like any other: a
        # re-grade changes the shot payloads, so the cache must rebuild.
        "colour": _build_colour(project_folder),
        # The assembly manifest carries the editorial findings and the
        # versions ledger carries the reel versions: a new compile or a
        # new version changes the graph, so the cache must rebuild.
        # Raw bytes, not parsed records - the fingerprint only asks
        # whether the input changed.
        "assembly_manifest": _read_raw_bytes(
            _assembly_manifest_path(project_folder)),
        "reel_versions": _read_raw_bytes(
            _reel_versions_path(project_folder)),
        # Variant explorations are a graph input like the versions
        # ledger: a new exploration changes the graph, so the cache
        # must rebuild. One digest over the exploration records -
        # sorted names plus bytes - so a new or changed exploration
        # invalidates and an untouched set reuses the cache.
        "variant_explorations": _variant_explorations_digest(
            project_folder),
        # Creative commits are a graph input like the explorations:
        # a new commit changes the graph, so the cache must rebuild.
        "creative_commits": _commit_records_digest(project_folder),
        # The event log is the operation history: a new lease, a new
        # checkpoint, a new sign-off changes what the graph says
        # happened, so the cache must rebuild. Raw bytes, not parsed
        # records - the fingerprint only asks whether the input
        # changed. A project with no log yet reads as absent, and a
        # run mid-write reads as whatever bytes are on disk.
        "event_log": _read_raw_bytes(
            _event_log_path(project_folder)),
        # Version names and branches are graph inputs like the
        # commits: a new or moved tag, or a new branch, changes the
        # graph, so the cache must rebuild.
        "creative_tags": _tag_records_digest(project_folder),
        "creative_branches": _branch_records_digest(project_folder),
    }
    for clip in catalog:
        digest, _ = source_memory.digest_for_clip(project_folder, clip, recorded)
        if digest is None:
            continue
        # Cross-source take groups derive from FI-13's fresh M1 transcript
        # and M6 clock, so either record changing invalidates the graph.
        source_dir = source_memory.source_dir(digest, root)
        inputs[f"m1:{digest}"] = _read_raw_bytes(
            source_dir / source_memory.SLOT_TRANSCRIPT)
        inputs[f"m6:{digest}"] = _read_raw_bytes(
            source_dir / source_memory.SLOT_CLOCK)
        model = source_memory.read_world_model(digest, root)
        if model:
            inputs[f"world_model:{digest}"] = model
        identity = _read_identity(digest, root)
        if identity:
            inputs[f"identity:{digest}"] = identity

    canonical = json.dumps(inputs, sort_keys=True, ensure_ascii=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def load_or_build(project_folder: str, root: Path | None = None) -> dict:
    """Load the cached graph, or rebuild when an input changed.

    The graph is persistent: it is written to `project_graph.json`
    beside `pipeline_data.json` with the fingerprint of the inputs it
    was built from. A query checks the fingerprint first - unchanged
    inputs reuse the cached graph, changed inputs rebuild and rewrite.
    """
    _refuse_engine_checkout(project_folder)
    path = _graph_path(project_folder)
    if path.is_file():
        try:
            cached = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            cached = None
        if cached and isinstance(cached, dict):
            fingerprint = _input_fingerprint(project_folder, root)
            if cached.get("input_fingerprint") == fingerprint:
                return cached.get("graph") or new_graph()

    graph = build_graph(project_folder, root)
    fingerprint = _input_fingerprint(project_folder, root)
    record = {
        "graph_id": "project_graph",
        "input_fingerprint": fingerprint,
        "graph": graph,
    }
    from library.tools.stable_json import write_stable
    write_stable(path, record)
    return graph
