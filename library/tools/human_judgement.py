"""A human judgement, entering the world model as an INTERPRETED claim.

PR 3137 gave every world-model claim an epistemic class: `measured`
(an instrument read it), `inferred` (a model asserted it), `interpreted`
(a person judged it) and `unknown` (nobody declared how it was
produced). The `interpreted` class carried NO claim, because nothing a
person said ever entered the world model. The captain's corrections
lived in the project's learned context and on markers in Resolve; the
model's own reading of the same footage was filed as an inference.
Two readings of one span - one of them a person's - and the world
model could not say which was which (punchlist VI-012: *"separate
measurements from interpretations"*).

This module is the ONE way a human judgement enters, and it is a
reader of records that already exist. It writes no store of its own:
a judgement is READ from where the human put it, NORMALISED, and
carried into the world model as a claim of class `interpreted`.

**A judgement never edits the claim it disagrees with.** The captain
saying "this segment was placed here by mistake" does not delete the
model's account of that second; it stands beside it, in a different
class, with its own provenance. That is the whole point of the class:
a disagreement between a person and a model is a fact about the
footage's READING, and it is recorded as such rather than resolved by
whoever wrote last.

**A judgement that cannot be placed is NOT placed.** A correction that
names timeline seconds and no recorded timeline-to-source map stays
unattached, with the reason recorded. Attaching it to the nearest
source by guesswork is the exact defect the punchlist names
("preserve unknown values rather than replacing them with plausible
guesses"), so this module refuses to do it and says which judgements
could not be anchored.

The kinds are one enumeration: a `correction` is the human saying the
picture or the plan is wrong, a `verdict` is the human saying whether
it is accepted.

    python3 -m library.tools.human_judgement report <project>
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Any, Mapping, Optional

from library.tools import project_layout

#: The kinds of human judgement. One enumeration: a judgement is a
#: correction (the human says this is wrong) or a verdict (the human
#: says whether this is accepted).
JUDGEMENT_KINDS = ("correction", "verdict")

#: Who said it. A role, not a name: the name rides in `author_detail`.
ROLE_CAPTAIN = "captain"
ROLE_REVIEWER = "reviewer"
ROLES = (ROLE_CAPTAIN, ROLE_REVIEWER)

#: The claim key this producer writes, and the registry type it is
#: filed under. The class is a CONSEQUENCE of that type
#: (``evidence_contract``): a human's judgement is a creative
#: judgement, and a creative judgement reads as `interpreted`.
CLAIM_KEY = "judgement"
MEASUREMENT_TYPE = "creative.correction"

CLAIM_TYPES = {CLAIM_KEY: MEASUREMENT_TYPE}
"""The claims this producer writes, and the registry type each one is
filed under (the same contract every other producer declares)."""

REQUIRED_FIELDS = ("id", "kind", "author", "statement", "origin")
"""What a judgement MUST carry to be a judgement at all."""


class HumanJudgementError(ValueError):
    """A judgement that cannot be read as one."""


# ── The record ─────────────────────────────────────────────────────


def make_judgement(judgement_id: str, kind: str, author: str, statement: str,
                   origin: str, *, recorded_at: Optional[str] = None,
                   detail: Optional[Mapping[str, Any]] = None,
                   targets: Optional[list] = None,
                   unattached_reason: Optional[str] = None,
                   author_detail: Optional[str] = None) -> dict[str, Any]:
    """One judgement, in the shape the world model files it under.

    `recorded_at` is None when nobody recorded when it was said: that
    stays None. It is never filled in with the time the file was read.
    """
    if kind not in JUDGEMENT_KINDS:
        raise HumanJudgementError(
            f"a judgement is {JUDGEMENT_KINDS}, not {kind!r}")
    if author not in ROLES:
        raise HumanJudgementError(
            f"a judgement is spoken by {ROLES}, not {author!r}")
    if not str(statement or "").strip():
        raise HumanJudgementError("a judgement with no words is not one")
    record: dict[str, Any] = {
        "id": str(judgement_id),
        "kind": kind,
        "author": author,
        "author_detail": author_detail,
        "recorded_at": recorded_at,
        "statement": str(statement),
        "origin": str(origin),
        "detail": dict(detail or {}),
        "targets": list(targets or []),
        "unattached_reason": unattached_reason,
    }
    return record


def _identity(*parts: Any) -> str:
    """A stable identity for a judgement that has no id of its own."""
    raw = "|".join("" if part is None else str(part) for part in parts)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


# ── The captain's notes, read off the marker pulls ─────────────────


def _note_statement(note: Mapping[str, Any]) -> str:
    """The captain's own words, exactly as typed."""
    for key in ("note", "text"):
        value = note.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _clip_targets(note: Mapping[str, Any], pull: Mapping[str, Any],
                  sources: Mapping[str, Mapping[str, Any]],
                  ) -> tuple[list[dict[str, Any]], list[str]]:
    """The source spans a note bears on, and the clips it could not name.

    A note typed on a MOMENT is about every clip playing under it, and
    the captain did not always say which one he meant. A clip that is
    a catalog source contributes a span; a clip that is a project-built
    asset (an overlay, a title) contributes nothing, because it is not
    a source anybody's world model is about.
    """
    fps = float(pull.get("timeline_fps") or 0.0)
    frame = note.get("frame")
    if frame is None:
        frame = note.get("frame_in_timeline_space")
    targets: list[dict[str, Any]] = []
    skipped: list[str] = []
    seen: set[tuple] = set()
    clips = list(note.get("clips") or [])
    attached = note.get("attached_clip")
    if isinstance(attached, Mapping):
        clips.append(attached)
    for clip in clips:
        if not isinstance(clip, Mapping):
            continue
        source_file = clip.get("source_file") or ""
        source = sources.get(source_file)
        if source is None:
            skipped.append(os.path.basename(source_file or "unnamed clip"))
            continue
        if frame is None or not fps:
            skipped.append(os.path.basename(source_file))
            continue
        timeline_start = clip.get("timeline_start")
        source_start = clip.get("source_start")
        if timeline_start is None or source_start is None:
            skipped.append(os.path.basename(source_file))
            continue
        source_frame = float(source_start) + (float(frame)
                                              - float(timeline_start))
        # One source under a note is ONE span, however many tracks it
        # plays on: a note on a moment with the picture and the sound of
        # the same camera is one correction, not two.
        if (source_file, source_frame) in seen:
            continue
        seen.add((source_file, source_frame))
        rate = float(source.get("frame_rate") or fps)
        duration = float(note.get("duration_frames") or 0) or 1.0
        targets.append({
            "source_file": source_file,
            "source_start_frame": source_frame,
            "source_end_frame": source_frame + duration,
            "start": source_frame / rate,
            "end": (source_frame + duration) / rate,
            "frame_rate": rate,
        })
    return targets, skipped


def marker_judgements(project_folder: str) -> list[dict[str, Any]]:
    """Every note the captain typed on a timeline, as judgements.

    A note is a correction of the edit he was looking at: he typed it
    because something in it is wrong. The words are kept as typed; the
    note's own id (when it has one) is the identity, and a digest of
    (timeline, frame, words) is the fallback for a note that predates
    the id.
    """
    from library.tools import marker_feedback

    recorded = _catalog_sources(project_folder)
    judgements: dict[str, dict[str, Any]] = {}
    for path, pull in marker_feedback.pulled_files(project_folder):
        relative = path.name
        timeline = pull.get("timeline") or ""
        for index, note in enumerate(pull.get("notes") or []):
            if not isinstance(note, Mapping):
                continue
            statement = _note_statement(note)
            if not statement:
                continue
            frame = note.get("frame")
            if frame is None:
                frame = note.get("frame_in_timeline_space")
            note_id = str(note.get("note_id") or "")
            identity = note_id or _identity(timeline, frame, note.get("source"),
                                           note.get("name"), statement)
            targets, skipped = _clip_targets(note, pull, recorded)
            detail = {
                "timeline": timeline,
                "marker_name": note.get("name"),
                "marker_colour": note.get("color"),
                "timecode": note.get("timecode"),
                "frame": frame,
                "clips_not_sources": skipped,
            }
            judgement = make_judgement(
                f"marker:{identity}", "correction", ROLE_CAPTAIN, statement,
                f"marker_feedback/{relative}#{index}",
                recorded_at=note.get("read_at") or None,
                detail=detail,
                targets=targets,
                unattached_reason=(None if targets else
                                   "the note names no source clip of this "
                                   "project"),
            )
            # The same note is pulled again every round. It is ONE
            # judgement, and the FIRST pull is when the captain said it:
            # a later pull records when the engine looked again.
            existing = judgements.get(judgement["id"])
            if existing is None or _earlier(judgement, existing):
                judgements[judgement["id"]] = judgement
    return list(judgements.values())


def _earlier(candidate: Mapping[str, Any], existing: Mapping[str, Any]) -> bool:
    """Whether a judgement was said before the one already recorded."""
    first, second = candidate.get("recorded_at"), existing.get("recorded_at")
    if first is None:
        return False
    if second is None:
        return True
    return str(first) < str(second)


# ── The captain's corrections, read off the learned context ────────


def learning_judgements(project_folder: str) -> list[dict[str, Any]]:
    """Every recorded correction in the project's learned context.

    These carry the captain's words and a `source` of their own - a
    spelling, a struck range, a keep insistence - and they are read
    here rather than in the prompt half that already reads them. Their
    `source` names TIMELINE seconds; nothing in the project records
    the timeline-to-source map, so they stay unattached rather than
    being attached to the nearest source.
    """
    path = project_layout.ProjectLayout(project_folder).read_path(
        project_layout.Area.LEARNED_CONTEXT, "learnings.json")
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    if isinstance(document, Mapping):
        entries = document.get("learnings") or []
    elif isinstance(document, list):
        entries = document
    else:
        return []
    judgements: list[dict[str, Any]] = []
    for entry in entries:
        if not isinstance(entry, Mapping):
            continue
        if entry.get("kind") != "correction":
            continue
        statement = str(entry.get("statement") or "").strip()
        if not statement:
            continue
        learning_id = str(entry.get("id") or _identity(statement))
        source = entry.get("source")
        source = source if isinstance(source, Mapping) else {}
        judgements.append(make_judgement(
            f"learning:{learning_id}", "correction", ROLE_CAPTAIN, statement,
            f"learned_context/learnings.json#{learning_id}",
            recorded_at=entry.get("created_at") or None,
            detail={"learning_id": learning_id,
                    "correction_type": source.get("correction_type"),
                    "said_by": entry.get("said_by")},
            unattached_reason=(
                "the correction names timeline seconds and the project "
                "records no timeline-to-source map"),
        ))
    return judgements


# ── The project's judgements, and where each one lands ─────────────


def project_judgements(project_folder: str) -> dict[str, Any]:
    """Every recorded human judgement a project holds, and its anchors.

    `judgements` carries each judgement with its `targets` resolved to
    a content digest where the project's own record says which source
    it is about. A judgement with no recorded anchor keeps
    `unattached_reason` and lands in `unattached` instead: it is
    recorded, and it is not placed.
    """
    judgements = marker_judgements(project_folder) + \
        learning_judgements(project_folder)
    digests = _recorded_digests(project_folder)
    attached: list[dict[str, Any]] = []
    unattached: list[dict[str, Any]] = []
    for judgement in judgements:
        targets = []
        for target in judgement.get("targets") or []:
            digest = digests.get(target.get("source_file"))
            if digest is None:
                continue
            targets.append({**target, "content_digest": digest})
        if targets:
            attached.append({**judgement, "targets": targets})
        else:
            unattached.append(judgement)
    return {"judgements": attached, "unattached": unattached}


def judgements_by_digest(report: Mapping[str, Any]) -> dict[str, list[dict]]:
    """The judgements of a project, grouped by the source each is about.

    One judgement may bear on more than one source (a note typed on a
    moment with two cameras under it), so a judgement appears under
    every source it names - with the target that names it.
    """
    grouped: dict[str, list[dict]] = {}
    for judgement in report.get("judgements") or []:
        for target in judgement.get("targets") or []:
            digest = target.get("content_digest")
            if not digest:
                continue
            grouped.setdefault(digest, []).append({
                **{key: value for key, value in judgement.items()
                   if key != "targets"},
                "target": target,
            })
    return grouped


def _catalog_sources(project_folder: str) -> dict[str, dict[str, Any]]:
    """The project's catalogued sources, keyed by their recorded path."""
    from library.tools import source_memory

    sources: dict[str, dict[str, Any]] = {}
    for clip in source_memory.load_catalog(project_folder):
        path = clip.get("source_file") or clip.get("path") or ""
        if path:
            sources[path] = clip
    return sources


def _recorded_digests(project_folder: str) -> dict[str, str]:
    """`{source path: content digest}` as the project RECORDED it.

    The recorded digest, never a fresh fingerprint of the media: this
    reads the project, it does not touch the footage.
    """
    from library.tools import source_memory

    recorded = source_memory.load_recorded_fingerprints(project_folder)
    digests: dict[str, str] = {}
    for clip_id, entry in recorded.items():
        if not isinstance(entry, Mapping):
            continue
        path = entry.get("path")
        digest = entry.get("content_digest")
        if path and digest:
            digests[path] = digest
    return digests


# ── The report ─────────────────────────────────────────────────────


def render_report(report: Mapping[str, Any]) -> str:
    """The judgements of a project, as a table a reader can check."""
    attached = report.get("judgements") or []
    unattached = report.get("unattached") or []
    lines = [
        f"judgements recorded: {len(attached) + len(unattached)}",
        f"  attached to a source: {len(attached)}",
        f"  unattached: {len(unattached)}",
        "",
        "| judgement | kind | author | recorded at | source |",
        "|---|---|---|---|---|",
    ]
    for judgement in attached:
        targets = judgement.get("targets") or []
        sources = ", ".join(
            f"{target.get('content_digest', '')[:12]}"
            f"@{target.get('start')}-{target.get('end')}"
            for target in targets)
        lines.append(
            f"| {judgement.get('id')} | {judgement.get('kind')} | "
            f"{judgement.get('author')} | "
            f"{judgement.get('recorded_at') or 'unknown'} | {sources} |")
    if unattached:
        lines += ["", "| unattached judgement | reason |", "|---|---|"]
        for judgement in unattached:
            lines.append(f"| {judgement.get('id')} | "
                         f"{judgement.get('unattached_reason')} |")
    return "\n".join(lines)


def main(argv: Optional[list[str]] = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == "report":
        argv = argv[1:]
    parser = argparse.ArgumentParser(
        prog="human_judgement",
        description="The human judgements a project holds, and where "
                    "each one lands.")
    parser.add_argument("project")
    args = parser.parse_args(argv)
    report = project_judgements(args.project)
    print(render_report(report))
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI entry
    sys.exit(main())
