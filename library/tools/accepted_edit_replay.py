"""Replay accepted reel touch-ups after a full staged rebuild.

The undo journal is the authoritative record of an accepted touch: it holds
the structured operation spec and before/after timeline reads. Replaying
those specs through ``reel_touchup`` keeps the normal qualification,
staging, conflict checks and read-back verification in force.
"""

from __future__ import annotations

from collections.abc import Mapping


def accepted_touch_entries(project_folder: str, timeline: str) -> list[dict]:
    """Return active accepted touch journals for ``timeline`` in order."""
    from library.tools import undo_journal
    from library.tools.versions import reel_versions

    versions = reel_versions.versions_of(project_folder, timeline)
    by_version = {int(item["version"]): item for item in versions}
    entries = []
    for entry in undo_journal.list_entries(project_folder):
        if entry.get("final") != timeline or entry.get("status") != \
                undo_journal.STATUS_APPLIED:
            continue
        if entry.get("accepted_replay_of"):
            continue
        try:
            version = by_version[int(entry["version"])]
        except (KeyError, TypeError, ValueError):
            continue
        if version.get("undone_by"):
            continue
        spec = entry.get("spec")
        if not isinstance(spec, Mapping) or not spec.get("edits"):
            continue
        if not isinstance(entry.get("accepted_edit"), Mapping):
            # Journals accepted before this feature already hold the exact
            # edit program and the before/after reads. Backfill their
            # semantic record from that evidence, never from the live
            # timeline or a guessed declaration.
            entry["accepted_edit"] = undo_journal.accepted_edit_record(
                project_folder, entry)
            undo_journal.write_entry(project_folder, entry)
        entries.append(entry)
    return sorted(entries, key=lambda item: (str(item.get("opened_at") or ""),
                                             str(item.get("id") or "")))


def replay_accepted_touches(project_folder: str, timelines) -> dict:
    """Replay active accepted touches on newly rebuilt timelines.

    Each touch is independently staged and verified by ``apply_touchup``.
    A conflict is raised with its journal id; it is never skipped or
    converted into a successful rebuild receipt.
    """
    from library.tools import reel_touchup

    report = {str(timeline): [] for timeline in timelines}
    for timeline in timelines:
        timeline = str(timeline)
        for entry in accepted_touch_entries(project_folder, timeline):
            spec = dict(entry["spec"])
            spec["accepted_replay_of"] = str(entry["id"])
            try:
                receipt = reel_touchup.apply_touchup(project_folder, spec)
            except Exception as conflict:
                raise RuntimeError(
                    f"full rebuild of {timeline!r} could not replay accepted "
                    f"touch {entry['id']!r}: {conflict}") from conflict
            report[timeline].append({
                "journal": entry["id"],
                "status": receipt.get("status"),
                "version": receipt.get("version"),
                "verified": (bool(receipt.get("verification_read"))
                             or receipt.get("status")
                             == "already_satisfied"),
            })
    return report
