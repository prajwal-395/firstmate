"""Project-graph history family: reel versions and creative commits."""
from __future__ import annotations

from pathlib import Path

from .core import BRANCH, COMMIT, TAG, VERSION, _record_dir_digest, add_node

def _reel_versions_path(project_folder: str) -> Path | None:
    try:
        from library.tools.versions import reel_versions
    except ImportError:
        from tools.versions import reel_versions
    try:
        return Path(reel_versions.path_for(project_folder))
    except Exception:
        return None


def _commit_records_digest(project_folder) -> str | None:
    """One digest over the creative commit records, or None.

    Sorted commit ids plus `commit.json` bytes - see
    `_record_dir_digest`. A project with no commits reads as None,
    and a new commit - a touch, a merge, a revert, a replay - changes
    the digest, so the cache rebuilds exactly when the creative
    history moves.
    """
    try:
        from library.tools import undo_journal
        root = Path(undo_journal.commit_root(project_folder))
    except (OSError, ValueError):
        return None
    return _record_dir_digest(root, undo_journal.COMMIT_FILENAME)


def _tag_records_digest(project_folder) -> str | None:
    """One digest over the version-name records, or None.

    See `_record_dir_digest`: a new or moved tag changes the digest,
    so the cache rebuilds exactly when the named versions move.
    """
    try:
        from library.tools import undo_journal
        root = Path(undo_journal.tag_root(project_folder))
    except (OSError, ValueError):
        return None
    return _record_dir_digest(root, undo_journal.TAG_FILENAME)


def _branch_records_digest(project_folder) -> str | None:
    """One digest over the branch records, or None.

    See `_record_dir_digest`: a new branch changes the digest, so the
    cache rebuilds exactly when the lines of development move.
    """
    try:
        from library.tools import undo_journal
        root = Path(undo_journal.branch_root(project_folder))
    except (OSError, ValueError):
        return None
    return _record_dir_digest(root, undo_journal.BRANCH_FILENAME)


def _derive_version_nodes(graph: dict, project_folder: str) -> None:
    """Version nodes from the reel versions ledger.

    Each version of each reel is a node, so the graph answers "what
    versions exist for this reel" and "what did version 3 change".
    """
    try:
        from library.tools.versions import reel_versions
        ledger = reel_versions.read(project_folder)
    except (OSError, ValueError, RuntimeError):
        return
    for final, versions in (ledger.get("reels") or {}).items():
        for entry in versions or []:
            version_num = entry.get("version")
            if version_num is None:
                continue
            add_node(graph, {
                "node_id": f"version:{final}:{version_num}",
                "node_type": VERSION,
                "label": f"version {version_num} of {final}",
                "payload": {
                    "final": final,
                    "version": version_num,
                    "kind": entry.get("kind"),
                    "at": entry.get("at"),
                    "rows_digest": entry.get("rows_digest"),
                },
            })


def _derive_commit_nodes(graph: dict, project_folder: str) -> None:
    """Commit nodes from the creative commit graph.

    Each recorded commit - touch, merge, revert, replay - is a node
    carrying its lineage (parent, and a merge's second parent), its
    timeline, agent, intent and change counts, so any lane reading
    only the graph answers "what changed on this reel and why"
    without re-reading the commit store. Like the version and variant
    nodes, these carry no edges: a commit links to nothing until a
    lane evaluates it. A project with no commits gains no nodes.
    """
    try:
        from library.tools import timeline_diff, undo_journal
        commits = undo_journal.list_commits(project_folder)
    except (OSError, ValueError, RuntimeError):
        return
    for commit in commits:
        commit_id = commit.get("id") or ""
        if not commit_id:
            continue
        payload = {
            "id": commit_id,
            "parent": commit.get("parent"),
            "timeline": commit.get("timeline"),
            "agent": commit.get("agent"),
            "intent": commit.get("intent"),
            "at": commit.get("recorded_at"),
            "sequence": commit.get("sequence") or 0,
            "change_counts": timeline_diff.summarize(
                commit.get("changes") or ()),
        }
        if commit.get("second_parent"):
            payload["second_parent"] = commit.get("second_parent")
        add_node(graph, {
            "node_id": f"commit:{commit_id}",
            "node_type": COMMIT,
            "label": f"commit {commit_id[:12]} on {commit.get('timeline')}",
            "payload": payload,
        })


def _derive_tag_nodes(graph: dict, project_folder: str) -> None:
    """Version-name nodes from the creative tag records.

    Each tag is a node carrying the name, the commit it points at and
    why it was kept, so any lane reading only the graph answers
    "what is v1" without re-reading the tag store. Like the commit
    nodes, these carry no edges: a tag is a pointer, and what it
    points at is named, not linked. A project with no tags gains no
    nodes.
    """
    try:
        from library.tools import undo_journal
        tags = undo_journal.list_tags(project_folder)
    except (OSError, ValueError, RuntimeError):
        return
    for tag in tags:
        name = tag.get("name") or ""
        if not name:
            continue
        add_node(graph, {
            "node_id": f"tag:{name}",
            "node_type": TAG,
            "label": f"tag {name} -> "
                     f"{str(tag.get('commit') or '')[:12]}",
            "payload": {
                "name": name,
                "commit": tag.get("commit"),
                "note": tag.get("note") or "",
                "at": tag.get("created_at"),
            },
        })


def _derive_branch_nodes(graph: dict, project_folder: str) -> None:
    """Branch nodes from the creative branch records.

    Each branch is a node carrying the base commit it stands on, the
    timeline it lives on and why it was cut, so any lane reading only
    the graph answers "what lines of development exist" without
    re-reading the branch store. Like the tag nodes, these carry no
    edges. A project with no branches gains no nodes.
    """
    try:
        from library.tools import undo_journal
        branches = undo_journal.list_branches(project_folder)
    except (OSError, ValueError, RuntimeError):
        return
    for branch in branches:
        name = branch.get("name") or ""
        if not name:
            continue
        add_node(graph, {
            "node_id": f"branch:{name}",
            "node_type": BRANCH,
            "label": f"branch {name} -> "
                     f"{str(branch.get('base') or '')[:12]}",
            "payload": {
                "name": name,
                "base": branch.get("base"),
                "timeline": branch.get("timeline"),
                "note": branch.get("note") or "",
                "at": branch.get("created_at"),
            },
        })
