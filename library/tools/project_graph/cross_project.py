"""Explicitly scoped, freshness-checked retrieval across project graphs.

The Project Graph remains project-local. This read layer joins graphs only
when both projects explicitly grant each other cross-project intelligence
access, and joins assets only by their content digest.

An opt-in declaration is a project-root ``cross_project_policy.json`` with
``{"project_id": "stable-id", "share_with": ["other-stable-id"]}``.
Absent declarations mean private; malformed declarations refuse the query.
"""
from __future__ import annotations

import json
from pathlib import Path

from .core import APPEARS_IN, EDIT, SHOT
from .persistence import load_or_build

try:
    from library.tools.footage_identity import fingerprint
except ImportError:
    from tools.footage_identity import fingerprint

POLICY_FILE = "cross_project_policy.json"


def _policy(project: Path) -> dict:
    path = project / POLICY_FILE
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as exc:
        raise ValueError(f"invalid cross-project policy at {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise TypeError(f"cross-project policy at {path} must be an object")
    project_id = value.get("project_id")
    grants = value.get("share_with", [])
    if not isinstance(project_id, str) or not project_id.strip():
        raise ValueError(f"cross-project policy at {path} needs project_id")
    if (not isinstance(grants, list)
            or any(not isinstance(item, str) for item in grants)):
        raise ValueError(f"cross-project policy at {path} needs a string share_with list")
    return {"project_id": project_id, "share_with": set(grants)}


def _usage(graph: dict, digest: str) -> list[dict]:
    shots = {
        row["node_id"]: row["payload"]
        for row in graph["nodes"]
        if row["node_type"] == SHOT
        and row["payload"].get("content_digest") == digest
    }
    used = []
    for row in graph["nodes"]:
        if row["node_type"] != EDIT:
            continue
        payload = row["payload"].get("edit_payload") or {}
        if any(payload.get("clip_id") == shot.get("clip_id") for shot in shots.values()):
            used.append({"edit_id": row["node_id"], "payload": payload})
    return used


def query_cross_project_assets(
    requester: str | Path,
    projects: list[str | Path],
    query: str,
    *,
    categories: tuple[str, ...] = ("people", "scenes", "events", "usage", "lineage"),
) -> dict:
    """Find matching content-addressed assets in explicitly shared projects.

    ``requester`` and every candidate project must grant the other's stable
    project id in ``cross_project_policy.json``. Missing policy is private.
    Graphs are loaded through ``load_or_build`` so stale graph caches rebuild
    from their recorded inputs before they can be returned.
    """
    allowed = {"people", "scenes", "events", "usage", "lineage"}
    if not query.strip():
        return {"results": [], "error": "query must not be empty", "abstained": True}
    if not set(categories) <= allowed:
        return {"results": [], "error": "unknown query category", "abstained": True}
    requester_path = Path(requester).expanduser().resolve()
    requester_policy = _policy(requester_path)
    if not requester_policy:
        return {"results": [],
                "error": "requester has not opted into cross-project sharing",
                "abstained": True}

    terms = {part.casefold() for part in query.split() if part}
    matches: list[dict] = []
    seen_projects: set[str] = set()
    for value in projects:
        project = Path(value).expanduser().resolve()
        if project == requester_path:
            continue
        policy = _policy(project)
        if not policy:
            continue
        source_id = policy["project_id"]
        if source_id == requester_policy["project_id"]:
            return {"results": [], "error": "ambiguous project identity",
                    "abstained": True}
        if source_id in seen_projects:
            return {"results": [], "error": "ambiguous project identity",
                    "abstained": True}
        seen_projects.add(source_id)
        if (source_id not in requester_policy["share_with"]
                or requester_policy["project_id"] not in policy["share_with"]):
            continue
        graph = load_or_build(str(project))
        for shot in graph["nodes"]:
            if shot["node_type"] != SHOT:
                continue
            payload = shot["payload"]
            digest = payload.get("content_digest")
            # No digest means no trustworthy cross-project source identity.
            if not isinstance(digest, str) or not digest:
                continue
            # The graph cache may be current while media has changed since
            # its catalog was written. Verify the live source identity before
            # allowing its prior use or derived evidence to cross projects.
            source_file = payload.get("source_file")
            try:
                media_path = Path(source_file) if source_file else None
                if media_path is not None and not media_path.is_absolute():
                    media_path = project / media_path
                if (media_path is None or fingerprint(str(media_path))[
                        "content_digest"] != digest):
                    continue
            except OSError:
                continue
            linked = [row for row in graph["nodes"]
                      if row["payload"].get("content_digest") == digest]
            shot_node_id = shot["node_id"]
            person_ids = {edge["from"] for edge in graph["edges"]
                          if edge["type"] == APPEARS_IN and edge["to"] == shot_node_id}
            linked_people = [row for row in graph["nodes"]
                             if row["node_id"] in person_ids]
            use = _usage(graph, digest) if "usage" in categories else []
            searchable = " ".join(
                str(value) for row in linked
                for value in (row.get("label"), row["payload"].get("name"),
                              row["payload"].get("location"),
                              row["payload"].get("type"),
                              row["payload"].get("label"),
                              row["payload"].get("text"))
                if value is not None
            ) + " " + " ".join(str(row.get("label", "")) for row in linked_people)
            searchable += " " + digest
            searchable += " " + " ".join(
                str(value) for entry in use for value in entry.values())
            searchable = searchable.casefold()
            # Require all terms; lexical partials are not sufficient evidence.
            if not terms <= set(searchable.split()):
                continue
            people = [row["label"] for row in linked_people] if "people" in categories else []
            scenes = ([row["label"] for row in linked
                       if row["node_type"] == "scene"]
                      if "scenes" in categories else [])
            events = ([row["label"] for row in linked
                       if row["node_type"] in {"audio_event", "event"}]
                      if "events" in categories else [])
            matches.append({
                "project_id": source_id,
                "content_digest": digest,
                "clip_id": payload["clip_id"],
                "source_file": payload.get("source_file"),
                "people": people,
                "scenes": scenes,
                "events": events,
                "previous_usage": use,
                "lineage": ({"content_digest": digest,
                             "source_file": payload.get("source_file")}
                            if "lineage" in categories else None),
                "evidence": "project_graph",
                "freshness": "current",
            })

    unique = {(row["project_id"], row["content_digest"], row["clip_id"]): row
              for row in matches}
    results = list(unique.values())
    # Identical evidence resolving to multiple distinct sources is ambiguous;
    # do not rank one as if it were authoritative.
    digests = {row["content_digest"] for row in results}
    if len(digests) > 1:
        return {"results": [], "error": "ambiguous content match", "abstained": True}
    return {"results": results, "error": None, "abstained": not results}
