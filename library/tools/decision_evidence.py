"""The evidence an APPROVED project decision still cites.

The punch list's **VI-042**: "Prevent cache cleanup from deleting
evidence still referenced by approved project decisions."

The collectors already protect a lot: the Resolve timelines, the
pipeline's own step records and manifest, the project's declared
inputs, and the source-memory digests the catalog and the recorded
fingerprints name. What none of them protects is the evidence an
APPROVED decision rests on - and an approved decision is exactly the
thing nobody may quietly invalidate.

**What an approval is.** Three kinds, and one enumeration:

- a **proposal moment** the captain approved (``reel_proposals_v2.json``
  carries ``approval: "approved"``): it names the raw footage it plays,
  as ``source_spans`` - ``{source_file, source_start, source_end}``;
- a **Project Model edit** that is evidence-backed: it carries
  ``evidence_ids``, and the observations it names carry the asset whose
  bytes they were measured from;
- a **signed-off reel**: the captain approved that reel as it stands.
  Its source footage is the approved moments it was built from, so it
  is covered by them; the timeline it lives on is covered by the
  Resolve roots.

**An approval that cannot be read is a refusal, not an absence.** A
decision record that exists but cannot be parsed is reported as
unreadable, and the caller treats the evidence it might have cited as
still referenced. A collector that cannot read the approvals must not
collect the evidence they protect.

**Identity, not a name.** A cited source file is located by its PATH
(the decision says which file it played) and identified by its CONTENT
DIGEST. The digest is what a collector can match on, and a path that
cannot be fingerprinted is still a path that must survive.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

FORMAT = "decision_evidence/1"

APPROVED = "approved"


class DecisionEvidenceError(ValueError):
    """A decision record that cannot be read as asked."""


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as exc:
        raise DecisionEvidenceError(
            f"cannot read the decision record at {path}: {exc}") from exc


def _recorded_digests(project_folder: Path) -> dict[str, str]:
    """``{source path: content digest}`` from the runner's own record."""
    from library.tools import source_memory
    out: dict[str, str] = {}
    try:
        recorded = source_memory.load_recorded_fingerprints(str(project_folder))
    except (OSError, ValueError, TypeError):
        return out
    for entry in (recorded or {}).values():
        if not isinstance(entry, dict):
            continue
        path = entry.get("path")
        digest = entry.get("content_digest")
        if path and digest:
            out[str(path)] = str(digest)
    return out


def _approved_moments(project_folder: Path) -> list[dict[str, Any]]:
    """The proposal moments the captain approved.

    A proposal file that exists but cannot be read raises: a collector
    that cannot read the approvals must not collect the evidence they
    protect.
    """
    from library.tools import reel_proposal
    try:
        path = reel_proposal.proposal_path(str(project_folder))
    except (OSError, ValueError, TypeError):
        return []
    if not Path(path).is_file():
        return []
    try:
        moments = reel_proposal.read_proposal(Path(path))
    except Exception as exc:  # noqa: BLE001 - any failure refuses
        raise DecisionEvidenceError(
            f"cannot read the approved proposals at {path}: {exc}") from exc
    def _approval_of(moment: Any) -> str:
        # The approval is an ENUM, and its value is the word: reading
        # ``str(enum)`` would read "Approval.APPROVED" and match nothing.
        approval = getattr(moment, "approval", "")
        return str(getattr(approval, "value", approval)).lower()

    return [moment for moment in moments if _approval_of(moment) == APPROVED]


def _moment_spans(moment: Any) -> list[dict[str, Any]]:
    spans = getattr(moment, "source_spans", ()) or ()
    out = []
    for span in spans:
        if isinstance(span, dict) and span.get("source_file"):
            out.append(span)
    return out


def _evidence_backed_edits(project_folder: Path) -> list[dict[str, Any]]:
    """The Project Model's edits that rest on evidence, and what they cite."""
    from library.tools import project_model_store
    try:
        _revision, model = project_model_store.load_project_model(project_folder)
    except (FileNotFoundError, ValueError, KeyError, OSError):
        return []
    assets = {row.get("id"): row for row in model.get("assets", [])
              if isinstance(row, dict)}
    observations = {row.get("id"): row for row in model.get("observations", [])
                    if isinstance(row, dict)}
    out = []
    for edit in model.get("edits", []):
        if not isinstance(edit, dict):
            continue
        evidence_ids = edit.get("evidence_ids") or []
        if not evidence_ids:
            continue
        cited = []
        for observation_id in evidence_ids:
            observation = observations.get(observation_id)
            if observation is None:
                continue
            asset = assets.get(observation.get("asset_id")) or {}
            cited.append({"observation_id": observation_id,
                          "asset_id": observation.get("asset_id"),
                          "content_digest": asset.get("content_digest"),
                          "source_file": asset.get("uri")})
        if cited:
            out.append({"edit_id": edit.get("id"), "cited": cited})
    return out


def cited_evidence(project_folder: str | os.PathLike) -> dict[str, Any]:
    """What this project's APPROVED decisions still cite.

    ``{"digests": [...], "paths": [...], "observations": [...],
    "decisions": [...], "unreadable": [...]}``.

    A decision record that exists but cannot be read is named under
    ``unreadable``, and the caller must treat the evidence it might
    have cited as still referenced.
    """
    root = Path(project_folder)
    report: dict[str, Any] = {
        "format": FORMAT,
        "project": str(root),
        "digests": [],
        "paths": [],
        "observations": [],
        "decisions": [],
        "unreadable": [],
    }
    digests: set[str] = set()
    paths: set[str] = set()
    observations: set[str] = set()
    decisions: list[dict[str, Any]] = []
    recorded = _recorded_digests(root)

    try:
        moments = _approved_moments(root)
    except DecisionEvidenceError as exc:
        report["unreadable"].append(str(exc))
        moments = []

    for moment in moments:
        spans = _moment_spans(moment)
        for span in spans:
            source_file = str(span["source_file"])
            paths.add(source_file)
            digest = recorded.get(source_file)
            if digest:
                digests.add(digest)
            else:
                report["unreadable"].append(
                    "cannot resolve the approved source path to a recorded "
                    f"content digest: {source_file}")
        decisions.append({
            "kind": "proposal_moment",
            "id": getattr(moment, "moment_id", "") or
                   f"reel-{getattr(moment, 'number', '?')}",
            "cited_paths": sorted({str(span["source_file"]) for span in spans}),
        })

    try:
        edits = _evidence_backed_edits(root)
    except DecisionEvidenceError as exc:
        report["unreadable"].append(str(exc))
        edits = []
    for edit in edits:
        for row in edit["cited"]:
            observations.add(row["observation_id"])
            if row.get("content_digest"):
                digests.add(str(row["content_digest"]))
            if row.get("source_file"):
                paths.add(str(row["source_file"]))
                if not row.get("content_digest"):
                    report["unreadable"].append(
                        "cannot resolve evidence-backed decision source to a "
                        f"content digest: {row['source_file']}")
        decisions.append({"kind": "evidence_backed_edit",
                          "id": edit["edit_id"],
                          "cited_observations": [row["observation_id"]
                                                 for row in edit["cited"]]})

    report["digests"] = sorted(digests)
    report["paths"] = sorted(paths)
    report["observations"] = sorted(observations)
    report["decisions"] = decisions
    return report


def cited_digests(project_folder: str | os.PathLike) -> set[str]:
    """The content digests an approved decision still cites.

    The set a collector matches on. A decision record that cannot be
    read makes this RAISE rather than come back short: a collector that
    cannot read the approvals must not collect the evidence they
    protect.
    """
    report = cited_evidence(project_folder)
    if report["unreadable"]:
        raise DecisionEvidenceError(
            "the approved decisions could not be read: "
            + "; ".join(report["unreadable"]))
    return set(report["digests"])
