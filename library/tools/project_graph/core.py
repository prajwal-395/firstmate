"""Project-graph core: the node/edge vocabulary and the graph object."""
from __future__ import annotations

import json
from pathlib import Path

# ── Node types, one enumeration ────────────────────────────────────

PERSON = "person"
SCENE = "scene"
SHOT = "shot"
TRANSCRIPT_SEGMENT = "transcript_segment"
AUDIO_EVENT = "audio_event"
GRAPHIC = "graphic"
REFERENCE = "reference"
TIMELINE = "timeline"
EDIT = "edit"
COLOUR_READING = "colour_reading"
VFX_DETECTION = "vfx_detection"
ONSET_CLASS = "onset_class"
DEAD_SECTION = "dead_section"
VERSION = "version"
EDITORIAL_FINDING = "editorial_finding"
VARIANT = "variant"
COMMIT = "commit"
OPERATION = "operation"
PARTICIPANT = "participant"
PROPOSAL = "proposal"
TAG = "tag"
BRANCH = "branch"
TAKE_GROUP = "take_group"

NODE_TYPES = (
    PERSON,
    SCENE,
    SHOT,
    TRANSCRIPT_SEGMENT,
    AUDIO_EVENT,
    GRAPHIC,
    REFERENCE,
    TIMELINE,
    EDIT,
    COLOUR_READING,
    VFX_DETECTION,
    ONSET_CLASS,
    DEAD_SECTION,
    VERSION,
    EDITORIAL_FINDING,
    VARIANT,
    COMMIT,
    OPERATION,
    PARTICIPANT,
    PROPOSAL,
    TAG,
    BRANCH,
    TAKE_GROUP,
)


# ── Relationship types, one enumeration ───────────────────────────

SAYS = "says"
APPEARS_IN = "appears_in"
SPEAKS_IN = "speaks_in"
SHOWN_IN = "shown_in"
REACTS_TO = "reacts_to"
COVERS = "covers"
MATCHES = "matches"
MUSIC_CHANGES_AT = "music_changes_at"
CONTAINS = "contains"
SERVES = "serves"
TAKE_MEMBER = "take_member"

RELATIONSHIP_TYPES = (
    SAYS,
    APPEARS_IN,
    SPEAKS_IN,
    SHOWN_IN,
    REACTS_TO,
    COVERS,
    MATCHES,
    MUSIC_CHANGES_AT,
    CONTAINS,
    SERVES,
    TAKE_MEMBER,
)


# ── Editorial finding kinds ───────────────────────────────────────

FINDING_DEAD_SECTION = "dead_section"
FINDING_DEAD_TRIM = "dead_section_trim"
FINDING_JL_PLACEMENT = "jl_placement"
FINDING_JL_DECISION = "jl_decision"
FINDING_REACTION_CUTAWAY = "reaction_cutaway"

FINDING_KINDS = (
    FINDING_DEAD_SECTION,
    FINDING_DEAD_TRIM,
    FINDING_JL_PLACEMENT,
    FINDING_JL_DECISION,
    FINDING_REACTION_CUTAWAY,
)
"""The compile manifest's report-only findings that become graph nodes.

Each kind names the manifest key it is read from: `dead_sections`,
`dead_section_trims`, `jl_placements`, `jl_placement_results` (one node
per entry, carrying the entry's `status`), `reaction_cutaways`.
`screen_direction_issues` stays out: no lane references a direction
finding by node yet, and a node nothing reads is not shipped.
"""


# ── Small readers ─────────────────────────────────────────────────


def _as_dict(value) -> dict:
    return value if isinstance(value, dict) else {}


def _as_list(value) -> list:
    return value if isinstance(value, list) else []


def _overlap(start_a, end_a, start_b, end_b) -> bool:
    return start_a < end_b and start_b < end_a


# ── The graph object ───────────────────────────────────────────────


def new_graph() -> dict:
    """An empty graph, the shape `build_graph` returns."""
    return {
        "graph_id": "project_graph",
        "node_count": 0,
        "edge_count": 0,
        "nodes": [],
        "edges": [],
    }


def add_node(graph: dict, node: dict) -> None:
    graph["nodes"].append(node)
    graph["node_count"] = len(graph["nodes"])


def add_edge(graph: dict, edge: dict) -> None:
    graph["edges"].append(edge)
    graph["edge_count"] = len(graph["edges"])


def node(graph: dict, node_id: str) -> dict:
    for candidate in graph["nodes"]:
        if candidate["node_id"] == node_id:
            return candidate
    return {}


def edges_from(graph: dict, node_id: str) -> list:
    return [e for e in graph["edges"] if e["from"] == node_id]


def edges_to(graph: dict, node_id: str) -> list:
    return [e for e in graph["edges"] if e["to"] == node_id]


def edges_of_type(graph: dict, rel_type: str) -> list:
    return [e for e in graph["edges"] if e["type"] == rel_type]


def _read_raw_bytes(path: Path | None) -> str | None:
    """The hex digest of a file's bytes, or None when absent.

    For the input fingerprint: the cache asks whether an input
    changed, never what it says, so bytes are enough. An unreadable
    file reads as absent - the builder deriving from it already
    treats absence as "the run has not produced this yet".
    """
    if path is None:
        return None
    try:
        import hashlib
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return None


def _record_dir_digest(root, filename: str) -> str | None:
    """One digest over `<name>/<filename>` records under root, or None.

    The shared directory form of `_read_raw_bytes`: sorted record
    names plus bytes, hashed together. The fingerprint asks whether
    the input changed, never what it says. A missing, unreadable or
    empty directory reads as None, and a new or changed record changes
    the digest, so the cache rebuilds exactly when the record set
    moves.
    """
    import hashlib

    if root is None:
        return None
    root = Path(root)
    if not root.is_dir():
        return None
    try:
        names = sorted(
            entry.name for entry in root.iterdir()
            if entry.is_dir() and (entry / filename).is_file())
    except OSError:
        return None
    if not names:
        return None
    digest = hashlib.sha256()
    for name in names:
        try:
            digest.update(name.encode("utf-8"))
            digest.update((root / name / filename).read_bytes())
        except OSError:
            return None
    return digest.hexdigest()


def _load_state(project_folder: str) -> dict:
    path = Path(project_folder) / "pipeline_data.json"
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))
