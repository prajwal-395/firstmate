"""The editorial report: what the measurements say about this cut, in one place.

Five behaviours measure the same run's material and each answers one
question:

- `library/tools/silence_reduction.py` - EDIT-016, what a silence or a
  filler would cost the rhythm if it were removed;
- `library/tools/angle_evidence.py` - EDIT-020, how each camera angle
  measures against speaker identity, action visibility, eyeline and
  continuity;
- `library/tools/shot_sequence_quality.py` - EDIT-021, which sequences
  of shots measure as weak and what would break them;
- `library/tools/pacing_hold.py` - EDIT-022, which moments measure as
  deliberate and should be held;
- `library/tools/content_significance.py` - EDIT-023, what a reduction
  would take with it.

and one module checks the genre:

- `library/tools/editorial_objective.py` - EDIT-024, whether the cut
  still satisfies the objective the project declared.

**This module assembles them and reads nothing of its own.**  Every
number in the report is a number one of those modules measured, and
every verdict is one of theirs.  A section whose facts were never
measured says so, rather than reporting nothing as if nothing were
wrong.

**The command.**  `python3 -m library.tools.editorial_report <project>`
prints the report for a project; `--json` prints it as JSON and
`--out <path>` writes it.  The report is an ANALYSIS: it changes nothing
in the project and it gates nothing.  It is what the editor reads before
deciding, and what the model reads when it is handed the findings.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Mapping, Sequence
from typing import Any

from library.tools import (
    angle_evidence,
    content_significance,
    editorial_objective,
    editorial_policy,
    pacing_hold,
    shot_sequence_quality,
    silence_reduction,
)


def _text(value: Any) -> str:
    return str(value or "").strip()


def _number(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _spine_blocks(state: Mapping[str, Any]) -> list[dict[str, Any]]:
    for key in ("timed_spine", "audio_spine"):
        spine = state.get(key)
        if isinstance(spine, Mapping):
            structure = spine.get("structure")
            if isinstance(structure, list):
                return [row for row in structure if isinstance(row, Mapping)]
    return []


def _by_clip(rows: Sequence[Any]) -> dict[str, Mapping[str, Any]]:
    out: dict[str, Mapping[str, Any]] = {}
    for row in rows or ():
        if isinstance(row, Mapping) and _text(row.get("clip_id")):
            out[_text(row.get("clip_id"))] = row
    return out


def _policy_of(state: Mapping[str, Any],
               project_folder: str | None) -> editorial_policy.EditorialPolicy | None:
    declared = state.get("editorial_policy")
    if isinstance(declared, Mapping):
        try:
            return editorial_policy.policy_from_dict(declared)
        except editorial_policy.EditorialPolicyError:
            pass
    if project_folder:
        return editorial_policy.read_project_policy(
            project_folder, project_id=os.path.basename(
                os.path.normpath(project_folder)))
    return None


def _clip_content(state: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    """What the vision pass measured each clip to be."""
    out: dict[str, dict[str, Any]] = {}
    for doc in state.get("semantic_analysis_documents") or ():
        if not isinstance(doc, Mapping):
            continue
        clip_id = _text(doc.get("clip_id"))
        if not clip_id:
            continue
        assessment = doc.get("assessment") or {}
        scenes = []
        actions = []
        for block in doc.get("blocks") or ():
            if not isinstance(block, Mapping):
                continue
            label = _text(block.get("label"))
            if label and label not in scenes:
                scenes.append(label)
            visual = _text(block.get("visual"))
            if visual and visual not in actions:
                actions.append(visual)
        windows = []
        for block_row in doc.get("blocks") or ():
            if not isinstance(block_row, Mapping):
                continue
            try:
                start = float(block_row["start"])
                end = float(block_row["end"])
            except (KeyError, TypeError, ValueError):
                continue
            windows.append({
                "start_seconds": start,
                "end_seconds": end,
                "description": _text(block_row.get("visual")),
            })
        out[clip_id] = {
            "content_type": _text(assessment.get("content_type")),
            "scenes": scenes,
            "actions": actions,
            "action_windows": windows,
        }
    return out


def _speakers_by_clip(context) -> dict[str, list[dict[str, Any]]]:
    """Who speaks where, per clip, from the objective module's context.

    The speaker belongs to the TRANSCRIBER, and the objective module is
    the one place that joins the transcript onto the passages, so the
    report reads it from there rather than joining it a second time.
    """
    out: dict[str, list[dict[str, Any]]] = {}
    for row in context.source_passages:
        clip_id = row.source_clip_id
        if not clip_id:
            continue
        out.setdefault(clip_id, []).append({
            "start": row.source_start,
            "end": row.source_end,
            "speaker": row.speaker,
            "text": row.text,
        })
    return out


def _temporal_indices(state: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    """The per-clip temporal index, keyed by clip id.

    `library/tools/continuity.py` reads the measured subject centre out
    of it, and the eyeline criterion reads it through that reader.
    """
    out: dict[str, Mapping[str, Any]] = {}
    for row in state.get("temporal_event_indices") or ():
        if isinstance(row, Mapping) and _text(row.get("clip_id")):
            out[_text(row.get("clip_id"))] = row
    return out


def _face_centres(state: Mapping[str, Any]) -> dict[str, float]:
    """The measured subject centre per clip, through the one reader.

    `library/tools/subject_framing.subject_center_reading` keeps the
    difference between "measured and centred" and "nothing could be
    measured", and this reader uses it rather than indexing the track
    itself.
    """
    from library.tools.subject_framing import subject_center_reading

    centres: dict[str, float] = {}
    for row in state.get("temporal_event_indices") or ():
        if not isinstance(row, Mapping):
            continue
        clip_id = _text(row.get("clip_id"))
        if not clip_id:
            continue
        face = row.get("face_presence") or {}
        values = face.get("face_center_x") or []
        if not values:
            continue
        try:
            rate = float(face["sample_rate_hz"])
        except (KeyError, TypeError, ValueError):
            continue
        reading = subject_center_reading(face, 0.0, len(values) / rate)
        if reading.position is not None:
            centres[clip_id] = float(reading.position)
    return centres


def _action_windows(measured: Mapping[str, Any] | None) -> list[dict[str, Any]]:
    """The measured action windows of a clip, with their own bounds.

    The vision pass writes one window per observed action
    (`library/tools/vision_schema_adapter.py`), and the bounds are the
    measurement - the prose is only the description.
    """
    if not isinstance(measured, Mapping):
        return []
    windows = []
    for window in measured.get("action_windows") or []:
        if not isinstance(window, Mapping):
            continue
        try:
            start = float(window["start_seconds"])
            end = float(window["end_seconds"])
        except (KeyError, TypeError, ValueError):
            continue
        windows.append({
            "start_seconds": start,
            "end_seconds": end,
            "description": _text(window.get("description")),
        })
    return windows


def _angle_candidates(block: Mapping[str, Any],
                      state: Mapping[str, Any],
                      speakers: Mapping[str, list[dict[str, Any]]],
                      content: Mapping[str, Mapping[str, Any]],
                      centres: Mapping[str, float]) -> list[dict[str, Any]]:
    """The other cameras that cover this passage's moment.

    A candidate is a clip whose MEASURED speech regions overlap the
    passage's source range: the same moment, seen from somewhere else.
    Nothing here is a preference - the candidates are the other cameras
    the footage has, and the criteria decide between them.
    """
    clip_id = _text(block.get("clip_id"))
    start = _number(block.get("source_start"))
    end = _number(block.get("source_end"))
    if not clip_id or end <= start:
        return []
    candidates = []
    for row in state.get("temporal_event_indices") or ():
        if not isinstance(row, Mapping):
            continue
        other = _text(row.get("clip_id"))
        if not other or other == clip_id:
            continue
        regions = row.get("speech_regions") or []
        overlap = [region for region in regions
                   if isinstance(region, Mapping)
                   and _number(region.get("end")) > start
                   and _number(region.get("start")) < end]
        if not overlap:
            continue
        named = sorted({_text(entry.get("speaker"))
                        for entry in speakers.get(other, [])
                        if _text(entry.get("speaker"))
                        and _number(entry.get("end")) > start
                        and _number(entry.get("start")) < end})
        measured = content.get(other) or {}
        candidates.append({
            "angle_id": other,
            "clip_id": other,
            "camera_id": _text(row.get("camera_id")) or other,
            "label": _text(row.get("label")) or other,
            "scene": "; ".join(measured.get("scenes") or [])[:120],
            "source_start": start,
            "source_end": end,
            "speakers": named,
            "actions": list(measured.get("actions") or [])[:6],
            "subject_center_x": centres.get(other),
        })
    return candidates


#: The cut-edge flags that MOTIVATE a cut: something measured happens
#: there.  The rest of `library/tools/alignment_findings.py`'s flag
#: vocabulary describes what the edge landed on rather than why the cut
#: is there, so those do not count.
MOTIVATING_FLAGS = ("scene_at_cut", "motion_peak_near", "audio_event_near",
                    "prosody_peak_near")


def _edit_point_motivations(state: Mapping[str, Any]) -> dict[str, list[str]]:
    """Why each cut edge was placed there, from the measured edit points.

    `library/tools/alignment_findings.py` already measures the signals
    around every cut edge step 2.02 refined - a scene change there, a
    motion peak beside it, an audio event, a prosody peak.  Those are
    the MOTIVATIONS of a cut, and reading them here is what lets a shot
    sequence be measured as unmotivated rather than merely unmeasured.
    """
    speech = state.get("speech_sequence") or {}
    report = speech.get("edit_point_report")
    if not isinstance(report, list):
        return {}
    motivations: dict[str, list[str]] = {}
    for row in report:
        if not isinstance(row, Mapping):
            continue
        block = _text(row.get("block"))
        reasons = []
        for key in ("head_flags", "tail_flags"):
            for flag in row.get(key) or []:
                if flag in MOTIVATING_FLAGS and flag not in reasons:
                    reasons.append(flag)
        if block and reasons:
            motivations.setdefault(block, []).extend(reasons)
    return motivations


def _shot_rows(blocks: Sequence[Mapping[str, Any]],
               state: Mapping[str, Any],
               content: Mapping[str, Mapping[str, Any]],
               centres: Mapping[str, float],
               context,
               motivations: Mapping[str, Sequence[str]]) -> list[dict[str, Any]]:
    rows = []
    for block in blocks:
        clip_id = _text(block.get("clip_id"))
        measured = content.get(clip_id) or {}
        position = block.get("position")
        rows.append({
            "position": position,
            "clip_id": clip_id,
            "camera_id": _text(block.get("camera_id")) or clip_id,
            "scene": "; ".join(measured.get("scenes") or [])[:120],
            "source_start": block.get("source_start"),
            "source_end": block.get("source_end"),
            "timeline_start": block.get("timeline_start"),
            "timeline_end": block.get("timeline_end"),
            "framing": _text(block.get("framing")),
            "subject_center_x": centres.get(clip_id),
            "subjects": list(block.get("subjects") or []),
            "actions": list(measured.get("actions") or [])[:6],
            "word_timestamps": list(block.get("word_timestamps") or []),
            "serves_beats": list(block.get("serves_beats") or []),
            "motivated_by": list(motivations.get(_text(position)) or []),
        })
    return rows


def _blocks_with_beats(blocks: Sequence[Mapping[str, Any]],
                       context) -> list[dict[str, Any]]:
    """The spine's blocks, each carrying the beats its passage serves.

    A spine block names a passage by POSITION - `passage_ref` is the
    1-based index into the speech sequence's body, the only name a spine
    block may reference (library/capabilities/spine/mesh.py).  The BEATS
    belong to the passage, so they are read from the passage and joined
    onto the block.  Without the join the direction's own pacing curve
    reaches nothing.
    """
    by_position = {index: row
                   for index, row in enumerate(context.source_passages, 1)}
    by_id = {row.passage_id: row for row in context.source_passages}
    out = []
    for block in blocks:
        content = block.get("content") or {}
        ref = content.get("passage_ref")
        origin = None
        if isinstance(ref, int) and not isinstance(ref, bool):
            origin = by_position.get(ref)
        if origin is None:
            origin = by_id.get(_text(ref))
        row = dict(block)
        row["serves_beats"] = list(origin.serves_beats) if origin else []
        out.append(row)
    return out


def build_report(state: Mapping[str, Any], *,
                 project_folder: str | None = None,
                 policy: editorial_policy.EditorialPolicy | None = None
                 ) -> dict[str, Any]:
    """The whole editorial report for one run's state."""
    state = state or {}
    resolved = policy if policy is not None else _policy_of(state, project_folder)
    blocks = _spine_blocks(state)
    content = _clip_content(state)
    centres = _face_centres(state)

    words: list[dict[str, Any]] = []
    for block in blocks:
        for row in block.get("word_timestamps") or ():
            if isinstance(row, Mapping):
                words.append(dict(row))

    report: dict[str, Any] = {
        "policy": {
            "declared": resolved is not None,
            "objective": resolved.objective if resolved else None,
            "slots": dict(resolved.slots) if resolved else {},
            "description": editorial_policy.describe_policy(resolved),
        },
    }

    context = editorial_objective.context_from_state(state)
    speakers = _speakers_by_clip(context)
    report["objective"] = {
        "objective": resolved.objective if resolved else None,
        "constraints": list(resolved.constraints) if resolved else [],
        "findings": [finding.to_dict()
                     for finding in editorial_objective.check_constraints(
                         resolved, context)],
        "protected_passage_ids": list(
            editorial_objective.protected_passage_ids(resolved, context)),
    }

    report["silence"] = silence_reduction.propose_reductions(
        words, policy=resolved)

    report["weak_sequences"] = shot_sequence_quality.weak_sequences(
        _shot_rows(blocks, state, content, centres, context,
                   _edit_point_motivations(state)), policy=resolved)

    report["holds"] = pacing_hold.hold_candidates(
        _blocks_with_beats(blocks, context), policy=resolved,
        prosody_profiles=_prosody_profiles(state),
        intent_model=state.get("intent_model"))

    report["significance"] = content_significance.check_reduction(
        _source_passages(context), _cut_passages(context),
        claims=_claims(context), beats=_beats(context),
        clip_content=content, policy=resolved)

    report["angles"] = _angle_report(blocks, state, speakers, content, centres,
                                     resolved, _temporal_indices(state))
    return report


def _prosody_profiles(state: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    profiles = state.get("prosody_analysis")
    if isinstance(profiles, Mapping):
        return {key: value for key, value in profiles.items()
                if isinstance(value, Mapping)}
    return {}


def _source_passages(context) -> list[dict[str, Any]]:
    """The material's passages, in the shape the significance check reads."""
    return [{
        "passage_id": row.passage_id,
        "clip_id": row.source_clip_id,
        "text": row.text,
        "serves_beats": list(row.serves_beats),
    } for row in context.source_passages]


def _cut_passages(context) -> list[dict[str, Any]]:
    """The cut's passages, in the shape the significance check reads."""
    return [{
        "passage_id": row.passage_id,
        "clip_id": row.source_clip_id,
        "text": row.text,
        "serves_beats": list(row.serves_beats),
    } for row in context.cut_passages]


def _claims(context) -> list[dict[str, Any]]:
    return [{
        "claim_id": row.claim_id,
        "statement": row.statement,
        "passage_ids": list(row.passage_ids),
    } for row in context.claims]


def _beats(context) -> list[dict[str, Any]]:
    return [{
        "beat_id": row.beat_id,
        "purpose": row.purpose,
        "required": row.required,
        "passage_ids": list(row.passage_ids),
    } for row in context.beats]


def _angle_report(blocks, state, speakers, content, centres,
                  policy, temporal_indices=None) -> dict[str, Any]:
    rows = []
    for index, block in enumerate(blocks):
        if block.get("block_type") not in ("speech", "hook"):
            continue
        candidates = _angle_candidates(block, state, speakers, content, centres)
        if not candidates:
            continue
        previous = blocks[index - 1] if index else None
        content_block = block.get("content") or {}
        passage = {
            "source_start": block.get("source_start"),
            "source_end": block.get("source_end"),
        }
        speaking = _text(content_block.get("speaker"))
        if not speaking:
            for entry in speakers.get(_text(block.get("clip_id")), []):
                if (_number(entry.get("end")) > _number(block.get("source_start"))
                        and _number(entry.get("start")) < _number(block.get("source_end"))):
                    speaking = entry.get("speaker") or ""
                    break
        context = {
            "passage": passage,
            "speaking_speaker": speaking,
            "action_windows": _action_windows(
                content.get(_text(block.get("clip_id")))),
            "temporal_indices": temporal_indices,
            "previous_angle": ({
                "clip_id": _text(previous.get("clip_id")),
                "source_start": previous.get("source_start"),
                "source_end": previous.get("source_end"),
                "subject_center_x": centres.get(_text(previous.get("clip_id"))),
                "camera_id": _text(previous.get("clip_id")),
                "scene": "; ".join((content.get(_text(previous.get("clip_id")))
                                    or {}).get("scenes") or [])[:120],
            } if previous else None),
        }
        rows.append({
            "position": block.get("position"),
            "clip_id": _text(block.get("clip_id")),
            **angle_evidence.select_angle(candidates, context, policy=policy),
        })
    return {
        "passages": rows,
        "undetermined": (None if rows else
                         "no other camera covers this cut's passages, so "
                         "there is no angle to choose between"),
    }


def _load_state(project_folder: str) -> dict[str, Any]:
    from library.tools import project_layout
    path = project_layout.pipeline_data_path(project_folder)
    if not os.path.exists(path):
        raise FileNotFoundError(
            f"no pipeline state at {path}; run the pipeline first")
    with open(path, "r", encoding="utf-8") as handle:
        return json.load(handle)


def summarize(report: Mapping[str, Any]) -> list[str]:
    """The report as lines a person reads."""
    lines = [report["policy"]["description"], ""]
    objective = report.get("objective") or {}
    lines.append(f"Objective: {objective.get('objective') or 'none declared'}")
    for finding in objective.get("findings") or []:
        lines.append(f"  [{finding['status']}] {finding['constraint']}: "
                     f"{finding['detail']}")
    silence = report.get("silence") or {}
    lines.append("")
    lines.append(
        f"Silence: {len(silence.get('proposals') or [])} reduction(s) proposed, "
        f"{len(silence.get('reported') or [])} reported and not proposed "
        f"(threshold {silence.get('silence_threshold_seconds')}s, from the "
        f"{silence.get('silence_threshold_source')})")
    for row in (silence.get("reported") or [])[:10]:
        lines.append(f"  - {row.get('kind')} {row.get('word') or ''}: "
                     f"{row.get('why_not')}")
    angles = report.get("angles") or {}
    lines.append("")
    lines.append(f"Angles: {len(angles.get('passages') or [])} passage(s) "
                 f"with another camera to choose from")
    for row in (angles.get("passages") or [])[:10]:
        lines.append(f"  - {row.get('position')}: {row.get('why')}")
    weak = report.get("weak_sequences") or {}
    lines.append("")
    lines.append(f"Weak sequences: {len(weak.get('weak') or [])}")
    for row in (weak.get("weak") or [])[:10]:
        lines.append(f"  - {row.get('name')} ({row.get('shot_count')} shots): "
                     f"{row.get('why')}")
    holds = report.get("holds") or {}
    lines.append("")
    lines.append(f"Moments to hold: {len(holds.get('candidates') or [])}")
    for row in (holds.get("candidates") or [])[:10]:
        lines.append(f"  - {row.get('position')}: {row.get('why')}")
    significance = report.get("significance") or {}
    lines.append("")
    lines.append(f"Content after reduction: {significance.get('verdict')}")
    for finding in significance.get("findings") or []:
        lines.append(f"  - {finding.get('detail')}")
    return lines


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="The editorial report for a project's run.")
    parser.add_argument("project", help="the project folder")
    parser.add_argument("--json", action="store_true",
                        help="print the report as JSON")
    parser.add_argument("--out", default="",
                        help="write the report to this path as JSON")
    args = parser.parse_args(argv)

    state = _load_state(args.project)
    report = build_report(state, project_folder=args.project)
    if args.out:
        os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
        with open(args.out, "w", encoding="utf-8") as handle:
            json.dump(report, handle, indent=2)
        print(f"wrote {args.out}")
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print("\n".join(summarize(report)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
