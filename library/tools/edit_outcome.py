"""PROD-008: did the captain take the edit, or did he change it - and why.

The punch list's **PROD-008**: *"Add product-level feedback and outcome
tracking. Capture whether a user accepted or modified a generated edit.
Record correction type, time saved, and amount of manual repair.
Distinguish objective system failures from stylistic disagreement. Keep
media and project-level learning opt-in and appropriately isolated."*

What the engine already had, and what was missing
-------------------------------------------------
Every part of a hand correction existed, and none of it was joined into
an OUTCOME:

- `reel_edit_history` (the unified account of one reel's changes)
  knows an edit was the CAPTAIN's (`actor`, `act`), when it happened
  and what it changed;
- `reel_replace_guard` / `plan_provenance` recorded the DETECTED
  difference - `before_snapshot`, `after_snapshot` and the per-item
  `changes` with the fields that moved;
- `reel_adoption` (VC-004) appends the captain's hand-edited generation
  to the reel's version ledger as an ADOPTED version;
- `reel_acceptance` records that a delivered reel was TAKEN;
- `manual_edit_intent` measures the one objective defect a hand edit
  can carry - a crop that cuts the subject's face.

So the facts were all there, and the PRODUCT question - did he accept it
or change it, how much, and was the change a fault in the output or a
matter of taste - was never asked of them. This module asks it, and
answers it only from what was recorded.

THE OUTCOME
-----------
One of three, per reel, and never a guess:

``accepted``
    The captain took the generated edit as it stood: the reel carries a
    delivery acceptance (`reel_acceptance`) and no hand correction was
    recorded after the build it was delivered from.
``modified``
    A hand correction is recorded after the Ren edit that produced the
    reel. The corrections are listed, each with what it changed.
``unmeasured``
    Nothing recorded decides it - no acceptance and no correction. This
    is not "accepted" and it is not a pass: an absent measurement is
    never a default (AGENTS.md 10.3).

A reel that was accepted AND then corrected is `modified`: the captain
took a delivery and then changed the edit, and the correction is the
later fact. The acceptance is still carried in the record.

THE CLASSIFICATION, AND WHY IT IS NOT THE ENGINE'S TO MAKE
-----------------------------------------------------------
The punch list asks to tell an OBJECTIVE SYSTEM FAILURE from a
STYLISTIC DISAGREEMENT. The engine may not decide that on its own:
AGENTS.md 10.5 says the pipeline never invents a creative judgement on
the model's behalf, and a disagreement about taste has no measurement
that settles it. So a correction is classified only when something
decides it, and otherwise it stays `unclassified` - counted on its own,
never folded into either column:

``objective_failure``
    The captain said so (his own words, filed against the edit - the
    review channel already prompts for them), or the engine's own
    measurement found the output broken at that place (the subject
    framing a hand edit broke, or a measurement the caller supplies).
``stylistic_disagreement``
    The captain said so, or the change is of the LOOK - the framing,
    the grade, the composition - which is his call and which the engine
    measures as a rule only where it cuts the subject.
``unclassified``
    Nothing measured it and nobody said. It is NOT counted as taste:
    a disagreement the engine cannot read is not a disagreement about
    taste.

THE AMOUNT, AND THE TIME
------------------------
The amount is measured in frames, because frames are what the engine
measures: the corrections, the rows they touched, the items they
changed, and the frames they moved.

The time is two different things, and they are never added together:

``elapsed_to_correction_seconds``
    The wall-clock window between the Ren build and the captain's hand
    correction. It is an UPPER BOUND on the time he spent repairing:
    the window also holds the time he watched the reel. It is reported
    with its basis, and it is `unmeasured` when either timestamp is
    absent.
``time_saved_seconds``
    The program seconds that reached the captain without a hand
    correction - the reel's program length minus the program the
    corrections touched. It is a statement about the PROGRAM Ren
    delivered, not an estimate of a person's hours, and it carries its
    denominator.

LEARNING IS OPT-IN, AND IT IS ISOLATED
---------------------------------------
A correction is the most interesting thing in the project, and it is
also the most private. So:

- nothing is learned unless the project says so. `edit_outcome_policy.json`
  in the project root declares it, and its ABSENCE means no learning
  is recorded at all - the default is off, not on;
- a PROJECT-level learning stays in the project. It goes into
  `learned_context` (the store that already exists for what a run
  learned), it names the step that reads it, and it never leaves;
- a MEDIA-level learning - a fact about a piece of footage rather than
  about this project - carries NO FREE TEXT: the captain's words, the
  project name and the reel name are stripped, because a shareable
  fact about media must not carry the client's words with it;
- a media learning reaches another project only when BOTH projects
  grant each other, by the engine's existing cross-project policy
  (`model_evidence_provenance._mutual_project_share`). The opt-in is
  checked BEFORE the source is read, so a project that has not granted
  access is never opened.

`tests/unit/reels/test_edit_outcome.py`.
"""

from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path

from library.tools.reel_edit_history import (
    CLASSIFICATION_OBJECTIVE as CLASS_OBJECTIVE_FAILURE,
)
from library.tools.reel_edit_history import (
    CLASSIFICATION_STYLISTIC as CLASS_STYLISTIC_DISAGREEMENT,
)

FORMAT = "edit_outcomes/1"
POLICY_FORMAT = "edit_outcome_policy/1"
POLICY_FILENAME = "edit_outcome_policy.json"

# ── the outcome ───────────────────────────────────────────────────────────

OUTCOME_ACCEPTED = "accepted"
OUTCOME_MODIFIED = "modified"
OUTCOME_UNMEASURED = "unmeasured"
OUTCOMES: tuple[str, ...] = (OUTCOME_ACCEPTED, OUTCOME_MODIFIED,
                              OUTCOME_UNMEASURED)

# ── the classification ────────────────────────────────────────────────────

CLASS_UNCLASSIFIED = "unclassified"
CLASSIFICATIONS: tuple[str, ...] = (CLASS_OBJECTIVE_FAILURE,
                                    CLASS_STYLISTIC_DISAGREEMENT,
                                    CLASS_UNCLASSIFIED)

#: Where a class came from. A class with no source is not a class.
SOURCE_CAPTAIN = "captain"
SOURCE_MEASUREMENT = "measurement"
SOURCE_AXIS = "axis"
SOURCE_NONE = "none"

# ── the axes a correction touched ────────────────────────────────────────

AXIS_LOOK = "look"
AXIS_SELECTION = "selection"
AXIS_PLACEMENT = "placement"
AXIS_MARKER = "marker"
AXIS_UNKNOWN = "unknown"

#: The fields of an item that carry the LOOK. A change here is the
#: captain's call; the engine's one objective claim about a look is
#: that it must not cut the subject (`manual_edit_intent`).
_LOOK_FIELDS = frozenset({"transform", "color", "clip_color", "fusion",
                          "composite", "flags", "keyframes"})

#: The fields that carry WHICH passage plays.
_SELECTION_FIELDS = frozenset({"source_identity", "source_in_frame",
                               "source_out_frame", "enabled", "name"})

#: The fields that carry WHERE it plays.
_PLACEMENT_FIELDS = frozenset({"record_in", "record_out", "duration",
                               "track_type", "track_index", "track_name"})


class EditOutcomeError(RuntimeError):
    """An outcome cannot be recorded or read as asked."""


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


# ── where the record lives ───────────────────────────────────────────────

def review_dir(project_folder: str) -> str:
    return os.path.join(str(project_folder), "pipeline_output", "review")


def outcomes_path(project_folder: str) -> Path:
    """The outcome store for one project."""
    from library.tools.project_layout import Area, ProjectLayout

    return ProjectLayout(project_folder).write_path(
        Area.EDIT_OUTCOMES, "edit_outcomes.json")


def _atomic_json(path: Path, document: Mapping) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, staged = tempfile.mkstemp(
        dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as out:
            json.dump(dict(document), out, indent=2, ensure_ascii=False)
            out.write("\n")
        os.replace(staged, path)
    except BaseException:
        try:
            os.unlink(staged)
        except OSError:
            pass
        raise


def read_outcomes(project_folder: str) -> dict:
    """Every recorded outcome, or an empty document when none was written."""
    path = outcomes_path(project_folder)
    if not path.exists():
        return {"format": FORMAT, "outcomes": {}}
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as unreadable:
        raise EditOutcomeError(
            f"{path} exists but could not be read ({unreadable}). An "
            f"unreadable outcome record must REFUSE: an unreadable "
            f"record reads exactly like no outcome.") from unreadable
    if (not isinstance(document, dict)
            or document.get("format") != FORMAT
            or not isinstance(document.get("outcomes"), dict)):
        raise EditOutcomeError(
            f"{path} is not a {FORMAT} document.")
    return document


def write_outcome(project_folder: str, record: Mapping) -> dict:
    """Record one reel's outcome, replacing the earlier one for that reel."""
    record = dict(record)
    record.setdefault("format", FORMAT)
    document = read_outcomes(project_folder)
    document["outcomes"][str(record.get("reel") or "")] = record
    document["format"] = FORMAT
    document["updated_at"] = _now()
    _atomic_json(outcomes_path(project_folder), document)
    return record


def outcome_for(project_folder: str, reel: str) -> dict | None:
    """The recorded outcome for one reel, or None when none was recorded."""
    return (read_outcomes(project_folder).get("outcomes") or {}).get(
        str(reel))


# ── the policy: what this project opted into ─────────────────────────────

def policy(project_folder: str) -> dict:
    """What this project opted into. Absent means NOTHING is opted in.

    The default is off for every switch: a project that says nothing
    learns nothing, and nothing is shared. A malformed declaration
    refuses - a policy nobody can read is not a policy of "no".
    """
    path = Path(project_folder) / POLICY_FILENAME
    declared = {
        "format": POLICY_FORMAT,
        "project_learning": False,
        "media_learning": False,
        "read_by": [],
    }
    if not path.exists():
        return declared
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as unreadable:
        raise EditOutcomeError(
            f"{path} exists but could not be read ({unreadable}). A "
            f"policy nobody can read is not a policy of 'no'.") from unreadable
    if not isinstance(document, Mapping):
        raise EditOutcomeError(f"{path} is not a policy object.")
    if str(document.get("format") or POLICY_FORMAT) != POLICY_FORMAT:
        raise EditOutcomeError(
            f"{path} declares format {document.get('format')!r}; this "
            f"module reads {POLICY_FORMAT}.")
    readers = document.get("read_by")
    if readers is not None and not isinstance(readers, (list, tuple)):
        raise EditOutcomeError(
            f"{path} declares read_by as {type(readers).__name__}; it "
            f"must be a list of step ids or absent.")
    declared.update({
        "project_learning": bool(document.get("project_learning")),
        "media_learning": bool(document.get("media_learning")),
        "read_by": [str(row) for row in (readers or ())],
    })
    return declared


# ── reading the recorded corrections ──────────────────────────────────────

def _axis_of(changed_fields: Sequence[str]) -> str:
    """The axis a correction touched, from the fields that moved.

    One axis per correction: the LOOK wins over the others, because a
    change of the look is a change of the look whatever else moved
    with it. A change of no known field is `unknown`, and an unknown
    axis is classified by nothing.
    """
    fields = {str(name) for name in (changed_fields or ())}
    if fields & _LOOK_FIELDS:
        return AXIS_LOOK
    if fields & _SELECTION_FIELDS:
        return AXIS_SELECTION
    if fields & _PLACEMENT_FIELDS:
        return AXIS_PLACEMENT
    if "markers" in fields:
        return AXIS_MARKER
    return AXIS_UNKNOWN


def _row_of(item: Mapping) -> str:
    kind = str(item.get("track_type") or "video")
    name = str(item.get("track_name") or "")
    return f"{kind}:{name}" if name else kind


def _span_of(item: Mapping) -> tuple[int, int] | None:
    start = item.get("record_in")
    end = item.get("record_out")
    if start is None or end is None:
        return None
    try:
        return int(start), int(end)
    except (TypeError, ValueError):
        return None


def _corrections_of(entry: Mapping,
                    change_record: Mapping | None) -> list[dict]:
    """The corrections one manual-edit entry carries.

    The DETECTED change is read off the editor-change record - the
    `changes` list the replace guard recorded - so the fields that
    moved are measured, not guessed. A manual edit with no such record
    is still a correction: it is recorded with what is known (the
    captain's summary) and its axis is `unknown`.
    """
    corrections: list[dict] = []
    changes = list((change_record or {}).get("changes") or ())
    if not changes:
        corrections.append({
            "id": str(entry.get("id") or ""),
            "kind": "unattributed",
            "row": "",
            "item": "",
            "changed_fields": [],
            "axis": AXIS_UNKNOWN,
            "before": None,
            "after": None,
            "span_before": None,
            "span_after": None,
            "frames_before": 0,
            "frames_after": 0,
            "detail": str(entry.get("summary") or ""),
        })
        return corrections
    for index, change in enumerate(changes):
        if not isinstance(change, Mapping):
            continue
        before = change.get("before") or {}
        after = change.get("after") or {}
        changed = sorted(str(name)
                         for name in (change.get("changed") or {}))
        before_span = _span_of(before)
        after_span = _span_of(after)
        item = after or before
        corrections.append({
            "id": f"{entry.get('id') or ''}#{index}",
            "kind": str(change.get("kind") or "item_changed"),
            "row": _row_of(item),
            "item": str(item.get("name") or change.get("identity") or ""),
            "changed_fields": changed,
            "axis": _axis_of(changed),
            "before": dict(before) if before else None,
            "after": dict(after) if after else None,
            "span_before": list(before_span) if before_span else None,
            "span_after": list(after_span) if after_span else None,
            "frames_before": (before_span[1] - before_span[0]
                              if before_span else 0),
            "frames_after": (after_span[1] - after_span[0]
                             if after_span else 0),
            "detail": _describe_change(change),
        })
    return corrections


def _describe_change(change: Mapping) -> str:
    """One line naming what a change did."""
    kind = str(change.get("kind") or "item_changed")
    changed = sorted(str(name) for name in (change.get("changed") or {}))
    if kind == "item_added":
        return "the captain added an item"
    if kind == "item_removed":
        return "the captain removed an item"
    if changed:
        return ("the captain changed " + ", ".join(changed))
    return "the captain changed this item"


def _editor_change_records(review: str, reel: str) -> dict:
    """The detected changes for one reel, by id."""
    from library.tools import plan_provenance

    try:
        document = plan_provenance.read_provenance(review) or {}
    except Exception:  # noqa: BLE001 - an unreadable record reads as absent
        return {}
    table = document.get("unattributed_editor_changes") or {}
    rows = table.get(str(reel)) or []
    return {str(row.get("id")): row for row in rows
            if isinstance(row, Mapping) and row.get("id")}


# ── the classification ───────────────────────────────────────────────────

def classify_correction(correction: Mapping, *,
                         classification: str = "",
                         measurement: str = "",
                         evidence: Sequence[str] = ()) -> dict:
    """Classify one correction, and say what decided it.

    The order the questions are asked:

    1. the captain said which it is - his words, filed against the
       edit - and that decides it;
    2. the engine's own measurement found the output broken at that
       place, and that decides it;
    3. the change is of the LOOK, which is the captain's call, and the
       engine's one objective claim about a look (that it must not cut
       the subject) found nothing - so it is a stylistic
       disagreement;
    4. otherwise it is `unclassified`, which is NOT taste: nothing
       measured it and nobody said.
    """
    stated = str(classification or "")
    if stated and stated not in CLASSIFICATIONS:
        raise EditOutcomeError(
            f"{stated!r} is not a classification. The classes are: "
            + ", ".join(CLASSIFICATIONS))
    reasons = [str(row) for row in (evidence or ()) if str(row)]
    if stated in (CLASS_OBJECTIVE_FAILURE, CLASS_STYLISTIC_DISAGREEMENT):
        return {"classification": stated, "source": SOURCE_CAPTAIN,
                "evidence": reasons}
    if measurement:
        return {"classification": CLASS_OBJECTIVE_FAILURE,
                "source": SOURCE_MEASUREMENT, "evidence": reasons}
    if str(correction.get("axis") or "") == AXIS_LOOK:
        return {
            "classification": CLASS_STYLISTIC_DISAGREEMENT,
            "source": SOURCE_AXIS,
            "evidence": reasons + [
                ("the correction changed the look, which is the "
                 "captain's call, and no measurement of this place "
                 "failed")],
        }
    return {
        "classification": CLASS_UNCLASSIFIED,
        "source": SOURCE_NONE,
        "evidence": reasons + [
            ("nothing measured this correction and nobody said what it "
             "was, so it is not counted as taste")],
    }


# ── the derivation ───────────────────────────────────────────────────────

def _corrections(project_folder: str, reel: str, review: str,
                 entries: Sequence[Mapping],
                 changes: Mapping) -> list[dict]:
    """Every recorded correction for one reel, with its classification."""
    from library.tools import reel_edit_history

    corrections = []
    for entry in entries:
        if str(entry.get("actor") or "") != reel_edit_history.ACTOR_CAPTAIN:
            continue
        if str(entry.get("act") or "") != reel_edit_history.ACT_MANUAL_EDIT:
            continue
        refs = entry.get("refs") or {}
        change_id = str(refs.get("editor_change_id") or "")
        record = changes.get(change_id)
        reason = reel_edit_history.reason_for(entries,
                                              str(entry.get("id") or ""))
        stated = reel_edit_history.classification_for(
            entries, str(entry.get("id") or ""))
        for correction in _corrections_of(entry, record):
            measured = _face_crop_measurement(entry, record)
            verdict = classify_correction(
                correction, classification=stated,
                measurement=measured[0],
                evidence=([reason] if reason else []) + measured[1])
            correction["reason"] = reason or ""
            correction["classification"] = verdict["classification"]
            correction["classification_source"] = verdict["source"]
            correction["evidence"] = verdict["evidence"]
            corrections.append(correction)
    return corrections


def _face_crop_measurement(entry: Mapping,
                           record: Mapping | None) -> tuple[str, list[str]]:
    """The engine's own measurement of a hand edit that crops the subject.

    `manual_edit_intent` already measures this - a hand edit that zooms
    in far enough to cut the face is a defect in the OUTPUT, not a
    matter of taste. Its finding is the citation.
    """
    if not record:
        return "", []
    from library.tools import manual_edit_intent, reel_replace_guard

    before = record.get("before_snapshot") or {}
    after = record.get("after_snapshot") or {}
    try:
        changes = reel_replace_guard.snapshot_diff(before, after)
    except Exception:  # noqa: BLE001 - an unreadable diff measures nothing
        return "", []
    for change in changes:
        if str(change.get("kind") or "") != "item_changed":
            continue
        regression = manual_edit_intent.face_crop_regression(
            change.get("before") or {}, change.get("after") or {})
        if regression:
            return ("the captain's own zoom crop cuts the subject's face",
                    [regression])
    return "", []


def _repaired_program(corrections: Sequence[Mapping]) -> int:
    """The program the corrections RE-CUT or RE-TIMED, in frames.

    A correction of the LOOK changes no program: the same frames play
    in the same place, restyled. So the frames it governs are not
    repaired program, and a reel whose captain restyled it and nothing
    else saved the whole program. The spans are unioned per row, so two
    corrections over the same frames are counted once.
    """
    spans: dict[str, list[tuple[int, int]]] = {}
    for correction in corrections:
        if str(correction.get("axis") or "") not in (AXIS_SELECTION,
                                                     AXIS_PLACEMENT):
            continue
        span = correction.get("span_after") or correction.get("span_before")
        if not span:
            continue
        row = str(correction.get("row") or "")
        spans.setdefault(row, []).append((int(span[0]), int(span[1])))
    total = 0
    for intervals in spans.values():
        merged: list[list[int]] = []
        for start, end in sorted(intervals):
            if merged and start <= merged[-1][1]:
                merged[-1][1] = max(merged[-1][1], end)
            else:
                merged.append([start, end])
        total += sum(end - start for start, end in merged)
    return total


def _program_frames(rows: Mapping | None) -> int:
    """The program's length in frames, from a recorded rows snapshot."""
    total = 0
    for row in (rows or {}).values():
        try:
            total += int(row.get("frames") or 0)
        except (TypeError, ValueError):
            continue
    return total


def _fps(project_folder: str) -> float | None:
    """The project's frame rate, or None when nobody declared one."""
    path = Path(project_folder) / "project.yaml"
    if not path.exists():
        return None
    try:
        import yaml

        with open(path, encoding="utf-8") as handle:
            config = yaml.safe_load(handle) or {}
        source = config.get("source") or {}
        fps = float(source.get("fps") or 0)
    except (OSError, ValueError, TypeError):
        return None
    return fps if fps > 0 else None


def _seconds(frames: int, fps: float | None) -> float | None:
    if not fps:
        return None
    return round(float(frames) / float(fps), 3)


def _elapsed_seconds(start: str, end: str) -> float | None:
    """The seconds between two ISO stamps, or None when either is absent."""
    def parse(value):
        try:
            parsed = datetime.fromisoformat(str(value))
        except (TypeError, ValueError):
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        return parsed
    first, second = parse(start), parse(end)
    if not first or not second:
        return None
    return round((second - first).total_seconds(), 3)


def derive_outcome(project_folder: str, reel: str, *,
                    measurements: Sequence[Mapping] = ()) -> dict:
    """Derive one reel's product outcome from what was recorded.

    Nothing here writes to Resolve and nothing here guesses: the
    outcome is read off the version ledger, the edit history, the
    detected changes, the delivery acceptance and the project policy.
    """
    from library.tools import reel_acceptance, reel_edit_history
    from library.tools.versions import reel_versions

    reel = str(reel)
    review = review_dir(project_folder)
    entries = reel_edit_history.history_for(review, reel)
    changes = _editor_change_records(review, reel)
    corrections = _corrections(project_folder, reel, review, entries, changes)

    versions = reel_versions.versions_of(project_folder, reel)
    builds = [row for row in versions
              if str(row.get("kind") or "") in reel_versions.ACTS]
    adopts = [row for row in versions
              if str(row.get("kind") or "") == reel_versions.KIND_ADOPT]
    build = builds[-1] if builds else None
    adopt = adopts[-1] if adopts else None

    acceptance = reel_acceptance.acceptance_for(project_folder, reel)

    rows = None
    for candidate in (build, adopt):
        if candidate and candidate.get("rows"):
            rows = candidate.get("rows")
            break
    program_frames = _program_frames(rows)

    repaired_frames = _repaired_program(corrections)

    fps = _fps(project_folder)
    program_seconds = _seconds(program_frames, fps)
    repaired_seconds = _seconds(repaired_frames, fps)
    saved_seconds = None
    if program_seconds is not None and repaired_seconds is not None:
        saved_seconds = round(program_seconds - repaired_seconds, 3)

    build_at = str((build or {}).get("at") or "")
    correction_at = ""
    for entry in entries:
        if str(entry.get("act") or "") == reel_edit_history.ACT_MANUAL_EDIT:
            correction_at = str(entry.get("at") or "")
            break
    elapsed = _elapsed_seconds(build_at, correction_at)
    if elapsed is not None and elapsed < 0:
        elapsed = None
        elapsed_basis = ("unmeasured: the correction carries a timestamp "
                         "before the build it follows, so no window "
                         "between them was measured")
    else:
        elapsed_basis = (
            "the wall-clock window between the Ren build and the "
            "captain's hand correction; an upper bound on the time "
            "he spent repairing, because the window also holds the "
            "time he watched the reel"
            if elapsed is not None else
            "unmeasured: the build or the correction carries no "
            "timestamp")

    if corrections:
        outcome = OUTCOME_MODIFIED
        reason = (f"{len(corrections)} hand correction(s) were recorded "
                  f"after the build this reel came from")
    elif acceptance:
        outcome = OUTCOME_ACCEPTED
        reason = (f"the reel was delivered and accepted by "
                  f"{acceptance.get('by', 'the captain')} and no hand "
                  f"correction was recorded after it")
    else:
        outcome = OUTCOME_UNMEASURED
        reason = ("no delivery acceptance and no hand correction were "
                  "recorded, so nothing established what the captain did "
                  "with this reel")

    if outcome == OUTCOME_UNMEASURED:
        # Nothing established what the captain did with this reel, so
        # nothing is claimed about the time it saved. An absent
        # measurement is not a full one (AGENTS.md 10.3).
        saved_seconds = None
        saved_basis = ("unmeasured: nothing established what the captain "
                       "did with this reel, so nothing is claimed about "
                       "the time it saved")
    else:
        saved_basis = (
            "the program seconds that reached the captain without "
            "being re-cut or re-timed by a hand correction (the "
            "reel's program length minus the program the corrections "
            "governed); a statement about the program delivered, not "
            "an estimate of a person's hours"
            if saved_seconds is not None else
            "unmeasured: the program length or the frame rate is not "
            "known")

    counts = {
        "corrections": len(corrections),
        "objective_failures": sum(1 for row in corrections
                                   if row["classification"]
                                   == CLASS_OBJECTIVE_FAILURE),
        "stylistic_disagreements": sum(
            1 for row in corrections
            if row["classification"] == CLASS_STYLISTIC_DISAGREEMENT),
        "unclassified": sum(1 for row in corrections
                            if row["classification"] == CLASS_UNCLASSIFIED),
        "rows_touched": len({row["row"] for row in corrections
                             if row.get("row")}),
        "items_changed": len(corrections),
        "repaired_frames": repaired_frames,
        "program_frames": program_frames,
    }
    counts["repaired_frame_share"] = (
        round(repaired_frames / program_frames, 4)
        if program_frames else None)

    return {
        "format": FORMAT,
        "reel": reel,
        "outcome": outcome,
        "reason": reason,
        "at": _now(),
        "versions": {
            "build": ({"version": build.get("version"), "at": build_at,
                       "kind": build.get("kind")}
                      if build else None),
            "adopt": ({"version": adopt.get("version"),
                       "at": adopt.get("at"), "kind": adopt.get("kind")}
                      if adopt else None),
        },
        "acceptance": ({"accepted_at": acceptance.get("accepted_at"),
                        "by": acceptance.get("by"),
                        "delivered_file": acceptance.get("delivered_file")}
                       if acceptance else None),
        "corrections": corrections,
        "amount": counts,
        "time": {
            "elapsed_to_correction_seconds": elapsed,
            "elapsed_basis": elapsed_basis,
            "time_saved_seconds": saved_seconds,
            "time_saved_basis": saved_basis,
            "program_seconds": program_seconds,
            "repaired_seconds": repaired_seconds,
            "fps": fps,
        },
        "measurements": [dict(row) for row in (measurements or ())],
    }


def record_outcome(project_folder: str, reel: str, *,
                   measurements: Sequence[Mapping] = ()) -> dict:
    """Derive one reel's outcome and write it down."""
    record = derive_outcome(project_folder, reel,
                             measurements=measurements)
    record["learning"] = record_learnings(project_folder, record)
    return write_outcome(project_folder, record)


# ── learning: opt-in, project-local, and isolated ─────────────────────────

def learning_rows(record: Mapping) -> list[dict]:
    """The learnings one outcome would record, if the project opted in.

    A learning names the correction it came from and what the captain
    said about it. A correction with no words and no measurement
    teaches nothing, so it produces no row.
    """
    rows = []
    for correction in record.get("corrections") or ():
        statement = str(correction.get("reason") or "").strip()
        if not statement:
            continue
        rows.append({
            "statement": statement,
            "kind": "correction",
            "source": {
                "reel": str(record.get("reel") or ""),
                "correction_id": str(correction.get("id") or ""),
                "classification": str(correction.get("classification") or ""),
                "outcome": str(record.get("outcome") or ""),
            },
        })
    return rows


def record_learnings(project_folder: str, record: Mapping) -> dict:
    """Record the outcome's learnings, if this project opted in.

    Absent policy means NO learning is recorded, and the record says
    so - a learning nobody asked for is a copy of the captain's words
    in a place he did not choose.
    """
    declared = policy(project_folder)
    rows = learning_rows(record)
    if not declared["project_learning"]:
        return {"project": "not_opted_in", "media": "not_opted_in",
                "recorded": 0, "learnings": [],
                "reason": ("the project did not opt into project-level "
                           "learning, so nothing was learned")}
    if not rows:
        return {"project": "opted_in", "media":
                ("opted_in" if declared["media_learning"]
                 else "not_opted_in"),
                "recorded": 0, "learnings": [],
                "reason": "no correction carried the captain's own words"}
    if not declared["read_by"]:
        return {"project": "opted_in", "media":
                ("opted_in" if declared["media_learning"]
                 else "not_opted_in"),
                "recorded": 0, "learnings": [],
                "reason": ("the policy names no step to read the learning, "
                           "and a learning nothing consumes is the defect "
                           "AGENTS.md 10.4 names")}
    from library.tools import learned_context

    written = []
    for row in rows:
        written.append(learned_context.record(
            project_folder, row["kind"], row["statement"],
            read_by=list(declared["read_by"]), source=row["source"]))
    return {"project": "recorded", "recorded": len(written),
            "learnings": [row["id"] for row in written],
            "media": ("opted_in" if declared["media_learning"]
                      else "not_opted_in"),
            "reason": ""}


def media_learning_rows(record: Mapping) -> list[dict]:
    """The MEDIA-level rows one outcome could share, free text stripped.

    A shareable fact about a piece of footage carries what was
    MEASURED - the axis, the kind, the frames - and never the
    captain's words, the reel name or the project name. A media
    learning that carried the client's words would carry the client
    with it.
    """
    rows = []
    for correction in record.get("corrections") or ():
        rows.append({
            "axis": str(correction.get("axis") or AXIS_UNKNOWN),
            "kind": str(correction.get("kind") or ""),
            "classification": str(correction.get("classification")
                                  or CLASS_UNCLASSIFIED),
            "frames": int(correction.get("frames_after") or 0),
            "row": str(correction.get("row") or ""),
        })
    return rows


def share_media_learnings(source_project: str, target_project: str,
                           record: Mapping) -> dict:
    """Share one outcome's media-level rows with another project.

    The opt-in is checked BEFORE the source is read: a project that has
    not granted access is never opened, and the rows are never read.
    The grant is the engine's existing mutual cross-project policy -
    the same check the evidence gate and the graph query use, so there
    is one answer and not two.
    """
    from library.tools import model_evidence_provenance
    from library.tools.project_graph.cross_project import _policy

    def policy_id(folder):
        try:
            declared = _policy(Path(folder).expanduser().resolve())
        except (OSError, ValueError, TypeError):
            return ""
        return str((declared or {}).get("project_id") or "")

    source_id = policy_id(source_project)
    target_id = policy_id(target_project)
    if not source_id or not target_id:
        return {"shared": False, "rows": [],
                "reason": ("a project with no stable id cannot grant "
                           "anything, so there is nothing to share")}
    allowed = model_evidence_provenance._mutual_project_share(
        source_project, target_project, source_id, target_id)
    if not allowed:
        return {"shared": False, "rows": [],
                "reason": ("the two projects do not grant each other "
                           "access, so nothing was read and nothing was "
                           "shared")}
    return {"shared": True, "rows": media_learning_rows(record),
            "reason": "the two projects grant each other access"}


# ── a reader ──────────────────────────────────────────────────────────────

def describe(record: Mapping) -> str:
    """One reader's paragraph about a reel's outcome."""
    amount = record.get("amount") or {}
    timing = record.get("time") or {}
    headline = (f"{record.get('reel', '?')}: "
                f"{str(record.get('outcome') or '').upper()}")
    lines = [f"{headline} - {record.get('reason', '')}"]
    if amount.get("corrections"):
        lines.append(
            f"  {amount.get('corrections')} correction(s) over "
            f"{amount.get('rows_touched')} row(s); "
            f"{amount.get('objective_failures')} objective failure(s), "
            f"{amount.get('stylistic_disagreements')} stylistic "
            f"disagreement(s), {amount.get('unclassified')} unclassified")
    saved = timing.get("time_saved_seconds")
    if saved is None:
        lines.append("  time saved: unmeasured")
    else:
        lines.append(
            f"  time saved: {saved}s of "
            f"{timing.get('program_seconds')}s of program")
    return "\n".join(lines)


def main(argv=None) -> int:
    """Print one reel's outcome, or every outcome the project holds."""
    import argparse

    parser = argparse.ArgumentParser(
        prog="python3 -m library.tools.edit_outcome",
        description="Read the product outcome of a generated edit.")
    parser.add_argument("project", help="project folder")
    parser.add_argument("--reel", default="",
                        help="one reel; without it, every recorded outcome")
    parser.add_argument("--json", action="store_true",
                        help="print the outcome as JSON")
    args = parser.parse_args(argv)
    document = read_outcomes(args.project)
    if args.reel:
        record = (document.get("outcomes") or {}).get(args.reel)
        if record is None:
            print(f"edit_outcome: no outcome is recorded for {args.reel!r}",
                  flush=True)
            return 2
        records = [record]
    else:
        records = [document["outcomes"][name]
                   for name in sorted(document.get("outcomes") or {})]
    if args.json:
        print(json.dumps(records if not args.reel else records[0],
                         indent=2, ensure_ascii=False))
        return 0
    for record in records:
        print(describe(record))
    return 0


__all__ = [
    "CLASSIFICATIONS",
    "CLASS_OBJECTIVE_FAILURE",
    "CLASS_STYLISTIC_DISAGREEMENT",
    "CLASS_UNCLASSIFIED",
    "OUTCOMES",
    "OUTCOME_ACCEPTED",
    "OUTCOME_MODIFIED",
    "OUTCOME_UNMEASURED",
    "EditOutcomeError",
    "classify_correction",
    "derive_outcome",
    "describe",
    "learning_rows",
    "media_learning_rows",
    "outcome_for",
    "policy",
    "read_outcomes",
    "record_learnings",
    "record_outcome",
    "share_media_learnings",
    "write_outcome",
]


if __name__ == "__main__":
    raise SystemExit(main())
