"""Relationship detectors: M10 (`relationships.json`) of the per-source
footage memory.

Gap 3 + Gap 7 of the world-model gap map (`data/vep-gapmap-a-world-model/
report.md` in firstmate's home): entities tracked across time with
persistent identities, and relationships between them - the review's
world-model level `Relationships` ("person holding object, looking at
person, referring to visible object").

**What M3 carries, and what that decides.** Every detector here is a
geometric rule over the existing M3 lane (`persons.json`: face boxes, lip
landmarks, 21 hand joints per hand at the M2 cadence) joined to M3b
identity tracks - no new model, no new vision pass, no decode. Two of the
review's three relationship types are measurable from that data:

- `holding` - a confident joint of one hand within
  `HOLDING_MAX_JOINT_DISTANCE` face widths of another hand's, sustained.
  The review's form is "hand joints near OBJECT box"; M3 carries no
  object boxes (objects are per-window VLM observations nothing tracks
  yet - object tracking is its own unit of Gap 3). What M3 does carry is
  the other half of every hold: the two hands that meet - a clasp, a
  handshake, a handoff, a two-handed grip. That is what this measures.
- `pointing` - a hand's wrist->fingertip ray within `POINTING_MAX_ANGLE_DEG`
  of the direction to another tracked face's centre, the hand extended
  past `POINTING_MIN_HAND_EXTENSION` face widths. The review's form
  verbatim ("hand joint extended toward a direction"), the direction
  aimed at the nearest other tracked face in the frame.

`looking_at` is REFUSED BY NAME, never answered: the review's form is
"face orientation (from landmarks) toward another face's centre", and M3
persists no orientation - `person_measurements.frame_record` writes only
the lip regions of the helper's landmark observations (the helper
measures leftEye/rightEye/nose/faceContour and they are dropped at
persist time), and an axis-aligned face box carries no facing direction.
A gaze rule invented from box aspect would report a heuristic as a
measurement. The refusal (`LOOKING_AT_UNMEASURED`) names the unlock:
persist the eye landmarks in M3 and this detector is a few lines.

**Candidates, not answers.** Both detectors are cheap geometric rules
over imprecise signals - a hand near a hand is not always a hold, an
extended hand aimed at a face is not always a point. They are recorded
as CANDIDATE spans under the same standing decision as M7's
`hand_near_mouth`: each frame carries its measured distance or angle so
the local VLM can verify a span without re-reading M3 (M8's
`span_verification` is the verifier; wiring relationship `--verify` into
the query lane is that lane's work, not this slot's).

**Who is who.** Faces take M3b ArcFace tracks by position
(`event_spans.assign_face_tracks`); a hand belongs to the tracked face
its wrist is nearest - the same position-join rule, one hop further out.
A relationship span names both participants' tracks. A hand no tracked
face claims, or a target face none claims, is left out of named
relationships rather than guessed into one.

**The coordinate frame.** M3 is image-normalised, so a distance is
measured in PIXELS: x scales by the frame width, y by its height, and
the unit is the frame's mean face width (the isotropic rule
`person_measurements` documents - the coordinate mistake that read the
hand-over-mouth eval's false positives). A frame with no face carries no
scale, and a distance without a scale is not a measurement.

    python3 -m library.tools.relationships build <project>
    python3 -m library.tools.relationships status <project>
"""

from __future__ import annotations

import argparse
import datetime
import json
import math
import sys
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

from library.tools import (
    event_spans,
    person_entity,
    person_measurements,
    source_memory,
)

STATUS_BUILT = "built"

HOLDING_MAX_JOINT_DISTANCE = 0.5
"""Two hands are a holding CANDIDATE when a confident joint of one is
under this many face widths from a confident joint of the other. A clasp
brings joints to ~0; 0.5 face widths is loose enough to catch an
interlace and tight enough to exclude two people merely sitting side by
side. A candidate cutoff, not an answer - the VLM disposes (M8)."""

HOLDING_MIN_FRAMES = 2
"""A holding candidate needs this many hit frames: ~1 s at the M2
cadence before `span_bounds` adds half a frame either side - the
review's "> 1 s" for holding, at the sample rate M3 actually has."""

POINTING_MAX_ANGLE_DEG = 25.0
"""The wrist->fingertip ray may be this many degrees off the direction to
the target face's centre. A pointing arm is straight; 25 deg is the
slack a bent elbow and a 2 Hz sample leave."""

POINTING_MIN_HAND_EXTENSION = 0.3
"""Wrist->fingertip distance, in face widths, below which the hand is a
fist or a resting hand rather than a pointing one. A pointing hand is
extended; a fist's fingertips sit near the wrist."""

POINTING_MIN_FRAMES = 2

LOOKING_AT_UNMEASURED = (
    "measured and NOT shipped: the review's detector is 'face orientation "
    "(from landmarks) toward another face's centre', and M3 persists no "
    "orientation - person_measurements.frame_record writes only the lip "
    "regions of the helper's landmark observations (the helper measures "
    "leftEye/rightEye/nose/faceContour and they are dropped at persist "
    "time), and an axis-aligned face box carries no facing direction. "
    "Answering from box aspect would report a heuristic as a measurement. "
    "The unlock is persisting the eye landmarks in M3; until then "
    "looking-at spans are refused by name, never answered with an empty "
    "list that reads as 'never looks at anyone'.")
"""The looking-at refusal, recorded in every M10 so a reader can tell
'not measured' from 'never happens'."""

POINTING_TIP_SUFFIX = "TIP"
"""A fingertip joint by its Vision name suffix (indexTIP, middleTIP,
ringTIP, littleTIP, thumbTIP). Matched case-insensitively so an SDK
rename of the finger joints does not silently turn every hand into a
fist."""


# ── the geometry ─────────────────────────────────────────────────────


def _pixels(m3: dict) -> Tuple[float, float]:
    """Pixel size of the frames M3 measured on (the isotropic scale)."""
    width, height = (m3.get("instrument") or {})["frame_pixels"]
    return float(width), float(height)


def _dist(a: Sequence[float], b: Sequence[float],
          width: float, height: float) -> float:
    """Pixel distance between two image-normalised points."""
    return math.hypot((a[0] - b[0]) * width, (a[1] - b[1]) * height)


def _angle_deg(a: Sequence[float], vertex: Sequence[float],
               b: Sequence[float], width: float, height: float) -> float:
    """The angle at `vertex` between `a` and `b`, in degrees, measured in
    pixels (the normalized frame is anisotropic until it is scaled)."""
    v1 = ((a[0] - vertex[0]) * width, (a[1] - vertex[1]) * height)
    v2 = ((b[0] - vertex[0]) * width, (b[1] - vertex[1]) * height)
    n1, n2 = math.hypot(*v1), math.hypot(*v2)
    if n1 == 0.0 or n2 == 0.0:
        return 180.0
    cos = (v1[0] * v2[0] + v1[1] * v2[1]) / (n1 * n2)
    return math.degrees(math.acos(max(-1.0, min(1.0, cos))))


def _confident_joints(hand: dict) -> Dict[str, list]:
    """A hand's joints at or above `HAND_JOINT_MIN_CONFIDENCE` - the
    floor every hand measurement so far has used (event_spans)."""
    return {name: joint for name, joint in hand["joints"].items()
            if joint[2] >= event_spans.HAND_JOINT_MIN_CONFIDENCE}


def _frame_scale(m3: dict, frame: dict) -> Optional[float]:
    """The frame's mean face width in pixels - the unit every distance
    here is measured in. None with no face in the frame: no scale, no
    measurement."""
    width, height = _pixels(m3)
    widths = [(face["box"][2] - face["box"][0]) * width
              for face in frame["faces"]]
    widths = [w for w in widths if w > 0]
    return sum(widths) / len(widths) if widths else None


def _hand_owners(m3: dict, assignment: List[List[Optional[str]]]
                 ) -> List[List[Optional[str]]]:
    """Per frame, per hand: the M3b face track whose face centre is
    nearest the wrist, or None (no wrist, or no tracked face). A hand
    belongs to the person whose face it is with - the same position-join
    rule `assign_face_tracks` uses for faces, one hop further out."""
    width, height = _pixels(m3)
    out: List[List[Optional[str]]] = []
    for frame, row in zip(m3["frames"], assignment):
        owners: List[Optional[str]] = []
        for hand in frame["hands"]:
            joints = _confident_joints(hand)
            wrist = next((j for name, j in joints.items()
                          if name.lower() == "wrist"), None)
            if wrist is None:
                owners.append(None)
                continue
            best, best_d = None, None
            for face, track_id in zip(frame["faces"], row):
                if track_id is None:
                    continue
                cx = (face["box"][0] + face["box"][2]) / 2.0
                cy = (face["box"][1] + face["box"][3]) / 2.0
                d = _dist(wrist, (cx, cy), width, height)
                if best_d is None or d < best_d:
                    best, best_d = track_id, d
            owners.append(best)
        out.append(owners)
    return out


def _spans_from_hits(key_frames: Dict[tuple, Dict[int, dict]],
                     times: Sequence[float], min_frames: int) -> List[dict]:
    """Runs of hit frames per participant pair into candidate spans.

    `key_frames` maps a participant pair to its per-frame evidence
    (frame index -> record); a run bridges at most
    `event_spans.SPAN_GAP_FRAMES` missed frames and keeps at least
    `min_frames` hits. Each span names its participants and carries the
    per-frame evidence so a verifier can judge it without re-reading M3.
    """
    spans = []
    for key, frames in sorted(key_frames.items()):
        hits = [i in frames for i in range(len(times))]
        for first, last, _count in event_spans.runs(
                hits, min_hits=min_frames):
            start, end = event_spans.span_bounds(times, first, last)
            spans.append({"owners": list(key),
                          "start": start, "end": end,
                          "frames": [frames[i]
                                     for i in range(first, last + 1)
                                     if i in frames]})
    return spans


# ── holding (hand meets hand) ────────────────────────────────────────


def holding_candidates(m3: dict,
                       assignment: List[List[Optional[str]]]
                       ) -> List[dict]:
    """Runs of frames where two hands' confident joints come within
    `HOLDING_MAX_JOINT_DISTANCE` face widths of each other.

    A pair with an unowned hand is skipped, not guessed: a relationship
    a participant cannot be named for is not a measurement. The two
    hands may belong to the same person - a two-handed grip is the most
    common hold there is.
    """
    width, height = _pixels(m3)
    times = [f["t"] for f in m3["frames"]]
    owners = _hand_owners(m3, assignment)
    key_frames: Dict[tuple, Dict[int, dict]] = {}
    for i, frame in enumerate(m3["frames"]):
        scale = _frame_scale(m3, frame)
        if scale is None or scale <= 0:
            continue
        hands = frame["hands"]
        for a in range(len(hands)):
            for b in range(a + 1, len(hands)):
                pair = (owners[i][a], owners[i][b])
                if pair[0] is None or pair[1] is None:
                    continue
                joints_a = _confident_joints(hands[a])
                joints_b = _confident_joints(hands[b])
                best = None
                for ja in joints_a.values():
                    for jb in joints_b.values():
                        d = _dist(ja, jb, width, height)
                        if best is None or d < best:
                            best = d
                if best is None or best / scale >= HOLDING_MAX_JOINT_DISTANCE:
                    continue
                key = tuple(sorted(pair))
                held = key_frames.setdefault(key, {}).get(i)
                record = {"index": i, "t": times[i],
                          "d": round(best / scale, 3),
                          "hands": [hands[a].get("chirality", "unknown"),
                                    hands[b].get("chirality", "unknown")]}
                if held is None or record["d"] < held["d"]:
                    key_frames[key][i] = record
    return _spans_from_hits(key_frames, times, HOLDING_MIN_FRAMES)


# ── pointing (extended hand aimed at another face) ───────────────────


def _fingertips(joints: Dict[str, list]) -> List[list]:
    """The hand's confident fingertip joints (any finger - a pointing
    hand extends whichever finger points)."""
    return [joint for name, joint in joints.items()
            if name.upper().endswith(POINTING_TIP_SUFFIX)]


def pointing_candidates(m3: dict,
                        assignment: List[List[Optional[str]]]
                        ) -> List[dict]:
    """Runs of frames where a hand's extended wrist->fingertip ray is
    aimed at another tracked face's centre.

    The target is a DIFFERENT tracked face in the same frame - pointing
    at one's own face is the hand-at-mouth rule's business (M7), not a
    relationship. A fist does not point: the wrist->fingertip distance
    must clear `POINTING_MIN_HAND_EXTENSION` face widths.
    """
    width, height = _pixels(m3)
    times = [f["t"] for f in m3["frames"]]
    owners = _hand_owners(m3, assignment)
    key_frames: Dict[tuple, Dict[int, dict]] = {}
    for i, frame in enumerate(m3["frames"]):
        scale = _frame_scale(m3, frame)
        if scale is None or scale <= 0:
            continue
        for hand, owner in zip(frame["hands"], owners[i]):
            if owner is None:
                continue
            joints = _confident_joints(hand)
            wrist = next((j for name, j in joints.items()
                          if name.lower() == "wrist"), None)
            tips = _fingertips(joints)
            if wrist is None or not tips:
                continue
            tip_x = sum(t[0] for t in tips) / len(tips)
            tip_y = sum(t[1] for t in tips) / len(tips)
            extension = _dist(wrist, (tip_x, tip_y), width, height) / scale
            if extension < POINTING_MIN_HAND_EXTENSION:
                continue
            for face, track_id in zip(frame["faces"], assignment[i]):
                if track_id is None or track_id == owner:
                    continue
                cx = (face["box"][0] + face["box"][2]) / 2.0
                cy = (face["box"][1] + face["box"][3]) / 2.0
                angle = _angle_deg((tip_x, tip_y), wrist, (cx, cy),
                                   width, height)
                if angle > POINTING_MAX_ANGLE_DEG:
                    continue
                key = (owner, track_id)
                held = key_frames.setdefault(key, {}).get(i)
                record = {"index": i, "t": times[i],
                          "angle": round(angle, 1),
                          "extension": round(extension, 3),
                          "hand": hand.get("chirality", "unknown")}
                if held is None or record["angle"] < held["angle"]:
                    key_frames[key][i] = record
    return _spans_from_hits(key_frames, times, POINTING_MIN_FRAMES)


# ── building M10 ──────────────────────────────────────────────────────


def build_source_relationships(content_digest: str, source_file: str,
                               root: Optional[Path] = None) -> dict:
    """M10 for one source from its M3 + M3b. Light: no decode, no model.
    Refuses (raises) without either - an unbuilt source would read as
    'no relationships here'."""
    m3 = person_measurements.read_m3(content_digest, root)
    identity = person_entity.read_identity(content_digest, root)
    if m3 is None:
        raise RuntimeError("no M3 (persons.json); run "
                           "`python3 -m library.tools.person_measurements build`")
    if identity is None:
        raise RuntimeError("no M3b (identity.json); run "
                           "`python3 -m library.tools.person_entity build`")
    assignment = event_spans.assign_face_tracks(m3, identity)
    holding = holding_candidates(m3, assignment)
    pointing = pointing_candidates(m3, assignment)
    record = {
        "content_digest": content_digest,
        "size_bytes": m3.get("size_bytes"),
        "source_file": source_file,
        "status": STATUS_BUILT,
        "frame_count": len(m3["frames"]),
        "relationships": {
            "holding": {
                "basis": f"a confident joint of one hand within "
                         f"{HOLDING_MAX_JOINT_DISTANCE} face widths of "
                         f"another hand's, for >= {HOLDING_MIN_FRAMES} "
                         f"frames; a geometric CANDIDATE for VLM "
                         f"verification, never an answer (the review's "
                         f"object-box form awaits object tracking - M3 "
                         f"carries no object boxes)",
                "spans": holding},
            "pointing": {
                "basis": f"a hand's wrist->fingertip ray within "
                         f"{POINTING_MAX_ANGLE_DEG} deg of another "
                         f"tracked face's centre, the hand extended >= "
                         f"{POINTING_MIN_HAND_EXTENSION} face widths, for "
                         f">= {POINTING_MIN_FRAMES} frames; a geometric "
                         f"CANDIDATE for VLM verification, never an answer",
                "spans": pointing},
        },
        "unmeasured": {
            "looking_at": LOOKING_AT_UNMEASURED,
        },
        "built_at": datetime.datetime.now(datetime.timezone.utc).isoformat(
            timespec="seconds"),
    }
    source_memory.write_json(
        source_memory.source_dir(content_digest, root)
        / source_memory.SLOT_RELATIONSHIPS, record)
    return {"content_digest": content_digest, "source_file": source_file,
            "holding_spans": len(holding),
            "pointing_spans": len(pointing),
            "looking_at": "refused: no orientation landmarks in M3"}


def build_project_relationships(project_folder: str,
                                root: Optional[Path] = None) -> dict:
    """M10 for every catalog source that carries M3 and M3b. Same
    reporting shape as `event_spans.build_project_events`."""
    results, failed = [], []
    for clip_id, digest, path in event_spans.catalog_sources(project_folder):
        try:
            account = build_source_relationships(digest, path, root)
        except Exception as exc:
            failed.append(clip_id)
            results.append({"clip_id": clip_id, "content_digest": digest,
                            "failed": f"{type(exc).__name__}: {exc}"})
            continue
        results.append({"clip_id": clip_id, **account})
    return {"project_folder": str(Path(project_folder).resolve()),
            "clips": results, "failed": failed}


# ── CLI ──────────────────────────────────────────────────────────────


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="relationships",
        description="M10: relationship candidate spans (holding, pointing) "
                    "from M3 + M3b. Light: no decode, no model.")
    sub = parser.add_subparsers(dest="command")
    p_build = sub.add_parser("build", help="M10 for a project's catalog")
    p_build.add_argument("project")
    p_build.add_argument("--memory-root")
    p_status = sub.add_parser("status", help="which sources carry M10")
    p_status.add_argument("project")
    p_status.add_argument("--memory-root")
    args = parser.parse_args(argv)
    if not args.command:
        parser.print_help()
        return 2
    root = Path(args.memory_root).expanduser() if args.memory_root else None
    if args.command == "build":
        report = build_project_relationships(args.project, root=root)
        print(json.dumps(report, indent=2))
        return 1 if report["failed"] else 0
    recorded = source_memory.load_recorded_fingerprints(args.project)
    rows = []
    for clip in source_memory.load_catalog(args.project):
        digest, _ = source_memory.digest_for_clip(args.project, clip,
                                                  recorded)
        doc = (source_memory.read_relationships(digest, root)
               if digest else None)
        rows.append({"clip_id": clip.get("clip_id"),
                     "content_digest": digest,
                     "m9": STATUS_BUILT if doc else "missing",
                     "holding": (len(doc["relationships"]["holding"]["spans"])
                                 if doc else 0),
                     "pointing": (len(doc["relationships"]["pointing"]["spans"])
                                  if doc else 0)})
    print(json.dumps({"clips": rows}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
