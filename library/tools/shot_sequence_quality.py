"""EDIT-021: the shot sequences that measure as weak, and what would break them.

The pipeline could tell that a shot was technically unsuitable - that is
`library/tools/shot_reject.py`, and it is a TECHNICAL verdict about soft
or unstable picture, never an editorial one.  What it could not say was
that a SEQUENCE of shots was weak: that three cuts in a row went to the
same camera for no measured reason, or that four shots in a row showed
the same thing, or that the run had nothing in it that changed.

A sequence is weak when the measurements say so, and the measurements
are three, none of them a taste:

- **no motivated cut** - none of the cuts INSIDE the run is motivated by
  anything measured: no scene change, no motion peak, no audio event, no
  emphasis beside it.  The cut is there because the cut is there.
- **same content** - every shot in the run shows the same measured
  scene, subject and action.
- **no variety** - every shot in the run carries the same measured
  framing or the same measured subject position, so the viewer's eye
  has nothing to follow.

**Two of the three make a run weak.**  One of them alone is an ordinary
cut.  A project may add a stricter limit of its own with
`shot.max_same_angle_run` in its editorial policy, and that limit makes
a long run weak on its own, whatever else it measures.

**The alternatives are proposals, not edits.**  Each one names what it
would change, what it would put there instead, and the measurements
that made it worth proposing:

- **replace the picture** - another camera's picture over the SAME
  audio range, so the words do not move.  The shape is the one
  `library/tools/audio_preserving_replacement.py` validates; this module
  does not build that proposal, because the synchronization and
  continuity evidence it needs belongs to the caller that has it.
- **insert a shot** - a shot from the material that shows something
  else, placed at a cut point inside the run that the run's own
  measurements offer as safe.
- **reorder two shots** - only two shots that carry no words, because
  swapping two spoken passages would break the chronology the
  documentary objective protects.

Nothing here is applied.  A weak sequence is a finding with a price, and
the price is a set of alternatives the editor can take or leave.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

#: A run needs at least this many shots to be a sequence at all.  A
#: single shot is not a weak SEQUENCE; it is one shot.
MIN_WEAK_RUN_SHOTS = 2

#: How many of the three measured weaknesses make a run weak.  One of
#: them alone is an ordinary cut.
WEAK_REASON_COUNT = 2

NO_MOTIVATED_CUT = "no_motivated_cut"
SAME_CONTENT = "same_content"
NO_VARIETY = "no_variety"
OVER_MAX_RUN = "over_max_run"

REASON_TEXT = {
    NO_MOTIVATED_CUT:
        "none of the cuts inside this run is motivated by a measured "
        "scene change, motion peak, audio event or emphasis",
    SAME_CONTENT:
        "every shot in this run shows the same measured scene, subject and "
        "action",
    NO_VARIETY:
        "every shot in this run carries the same measured framing and the "
        "same measured subject position",
    OVER_MAX_RUN:
        "this run is longer than the limit the project declared",
}


class ShotSequenceError(ValueError):
    """A shot sequence or its context could not be read."""


def _text(value: Any) -> str:
    return str(value or "").strip()


def _names(value: Any) -> set[str]:
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, (list, tuple, set)):
        return set()
    return {_text(row).lower() for row in value if _text(row)}


def _shot_key(shot: Mapping[str, Any]) -> tuple[str, str]:
    """What makes two shots the same camera.

    The camera identity when the material measured one, and the scene
    otherwise.  A run of one camera is the run; a run of one scene with
    no camera identity is the next best thing the measurements can say.
    """
    camera = _text(shot.get("camera_id"))
    if camera:
        return ("camera", camera)
    return ("scene", _text(shot.get("scene")))


def shot_runs(shots: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """The cut's shots, grouped into runs of one camera (or one scene)."""
    ordered = [shot for shot in shots or () if isinstance(shot, Mapping)]
    runs: list[dict[str, Any]] = []
    for shot in ordered:
        key = _shot_key(shot)
        if runs and runs[-1]["key"] == key:
            runs[-1]["shots"].append(shot)
            continue
        runs.append({"key": key, "kind": key[0], "name": key[1],
                     "shots": [shot]})
    return runs


def _motivated_cuts(run: Mapping[str, Any]) -> list[dict[str, Any]]:
    """The cuts inside a run that something measured motivated."""
    rows = []
    shots = list(run.get("shots") or [])
    for previous, current in zip(shots, shots[1:]):
        reasons = list(current.get("motivated_by") or [])
        if reasons:
            rows.append({
                "from": previous.get("position"),
                "to": current.get("position"),
                "reasons": reasons,
            })
    return rows


def _same_content(run: Mapping[str, Any]) -> bool:
    """Do all the shots in this run show the same thing?"""
    shots = list(run.get("shots") or [])
    if len(shots) < 2:
        return False
    scenes = {_text(shot.get("scene")) for shot in shots}
    subjects = [frozenset(_names(shot.get("subjects"))) for shot in shots]
    actions = [frozenset(_names(shot.get("actions"))) for shot in shots]
    known_scene = {scene for scene in scenes if scene}
    known_subject = [group for group in subjects if group]
    known_action = [group for group in actions if group]
    same_scene = bool(known_scene) and len(known_scene) == len(scenes)
    same_subject = bool(known_subject) and len(set(known_subject)) == 1
    same_action = bool(known_action) and len(set(known_action)) == 1
    # A room is a room: shots in the same SCENE are not the same content
    # on their own.  It takes the same subject or the same action as
    # well before the run counts as showing one thing.
    return bool((same_scene and (same_subject or same_action))
                or (same_subject and same_action))


def _no_variety(run: Mapping[str, Any]) -> bool:
    """Do all the shots in this run look the same?"""
    shots = list(run.get("shots") or [])
    if len(shots) < 2:
        return False
    framings = {_text(shot.get("framing")) for shot in shots}
    known_framings = {row for row in framings if row}
    if known_framings and len(known_framings) == len(framings):
        return True
    centres = [shot.get("subject_center_x") for shot in shots]
    known_centres = [row for row in centres if row is not None]
    if len(known_centres) == len(centres) and len(centres) >= 2:
        if max(known_centres) - min(known_centres) <= 0.0:
            return True
    return False


def weak_sequences(shots: Sequence[Mapping[str, Any]], *,
                   policy: Any = None) -> dict[str, Any]:
    """The runs that measure as weak, and why each one does.

    The measured reasons stand on their own.  A project may add a
    stricter limit through `shot.max_same_angle_run`; that limit makes a
    long run weak by itself, and an undeclared limit is simply absent
    rather than defaulted.
    """
    declared = None
    if policy is not None:
        try:
            declared = policy.slot("shot.max_same_angle_run")
        except Exception:  # noqa: BLE001 - an unreadable slot reads undeclared
            declared = None
    limit = int(declared) if declared is not None else None

    findings = []
    for run in shot_runs(shots):
        row = list(run.get("shots") or [])
        if len(row) < MIN_WEAK_RUN_SHOTS:
            continue
        measured = []
        motivated = _motivated_cuts(run)
        if not motivated:
            measured.append(NO_MOTIVATED_CUT)
        if _same_content(run):
            measured.append(SAME_CONTENT)
        if _no_variety(run):
            measured.append(NO_VARIETY)
        reasons = list(measured)
        if limit is not None and len(row) > limit:
            reasons.append(OVER_MAX_RUN)
        # A limit the project DECLARED is enough on its own: it said no
        # run this long, and the run is this long.  The three measured
        # weaknesses need two of them, because one of them alone is an
        # ordinary cut.
        if OVER_MAX_RUN not in reasons and len(measured) < WEAK_REASON_COUNT:
            continue
        findings.append({
            "kind": run["kind"],
            "name": run["name"],
            "shot_count": len(row),
            "positions": [shot.get("position") for shot in row],
            "reasons": reasons,
            "why": "; ".join(REASON_TEXT.get(reason, reason)
                             for reason in reasons),
            "motivated_cuts": motivated,
        })
    return {
        "runs": shot_runs(shots),
        "weak": findings,
        "max_same_angle_run": limit,
        "max_same_angle_run_source": "policy" if limit is not None else "undeclared",
    }


def _same_audio_range(candidate: Mapping[str, Any],
                      shot: Mapping[str, Any]) -> bool:
    try:
        return (abs(float(candidate["source_start"])
                    - float(shot["source_start"])) < 0.001
                and abs(float(candidate["source_end"])
                        - float(shot["source_end"])) < 0.001)
    except (KeyError, TypeError, ValueError):
        return False


def propose_alternatives(finding: Mapping[str, Any],
                         available_shots: Sequence[Mapping[str, Any]], *,
                         shots: Sequence[Mapping[str, Any]] = (),
                         evidence_ids: Sequence[str] = ()) -> list[dict[str, Any]]:
    """What could break this weak run, given the shots the material offers.

    Every alternative is a proposal.  None is applied, and each carries
    the measurements that made it worth proposing, so a reader can check
    it rather than trust it.
    """
    positions = list(finding.get("positions") or [])
    by_position = {shot.get("position"): shot
                   for shot in shots or () if isinstance(shot, Mapping)}
    available = [shot for shot in available_shots or ()
                 if isinstance(shot, Mapping)]
    alternatives: list[dict[str, Any]] = []

    for position in positions:
        current = by_position.get(position)
        if current is None:
            continue
        for candidate in available:
            if _text(candidate.get("camera_id")) == _text(current.get("camera_id")):
                continue
            if not _same_audio_range(candidate, current):
                continue
            alternatives.append({
                "kind": "replace_the_picture",
                "at_position": position,
                "with": {
                    "clip_id": _text(candidate.get("clip_id")),
                    "camera_id": _text(candidate.get("camera_id")),
                    "scene": _text(candidate.get("scene")),
                    "source_start": candidate.get("source_start"),
                    "source_end": candidate.get("source_end"),
                },
                "why": (
                    f"{_text(candidate.get('camera_id')) or candidate.get('clip_id')} "
                    f"shows the same stretch of the same material from another "
                    f"camera, so the words do not move"),
                "evidence_ids": list(evidence_ids),
                "requires": (
                    "a synchronization reading and a continuity reading "
                    "before this reaches the manifest; "
                    "library/tools/audio_preserving_replacement.py validates "
                    "both"),
            })

    run_shots = [by_position.get(position) for position in positions]
    run_shots = [shot for shot in run_shots if isinstance(shot, Mapping)]
    for candidate in available:
        if any(_text(candidate.get("camera_id")) == _text(shot.get("camera_id"))
               for shot in run_shots):
            continue
        if not _text(candidate.get("clip_id")):
            continue
        alternatives.append({
            "kind": "insert_a_shot",
            "at_position": positions[0] if positions else None,
            "with": {
                "clip_id": _text(candidate.get("clip_id")),
                "camera_id": _text(candidate.get("camera_id")),
                "scene": _text(candidate.get("scene")),
                "source_start": candidate.get("source_start"),
                "source_end": candidate.get("source_end"),
            },
            "why": (
                f"{_text(candidate.get('clip_id'))} shows "
                f"{_text(candidate.get('scene')) or 'something else'} and "
                f"breaks the run's camera"),
            "evidence_ids": list(evidence_ids),
            "requires": (
                "a cut point inside the run that the run's own "
                "measurements offer as safe"),
        })

    silent = [shot for shot in run_shots
              if not list(shot.get("word_timestamps") or [])]
    if len(silent) >= 2:
        first, second = silent[0], silent[1]
        alternatives.append({
            "kind": "reorder_two_shots",
            "at_position": first.get("position"),
            "with": {
                "swap_with": second.get("position"),
            },
            "why": (
                "both shots carry no words, so swapping them breaks the "
                "monotony without moving the chronology of what was said"),
            "evidence_ids": list(evidence_ids),
            "requires": "both shots carry no words",
        })
    return alternatives
