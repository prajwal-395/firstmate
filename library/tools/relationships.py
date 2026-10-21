"""Relationship detectors: M10 (`relationships.json`) of the per-source
footage memory.

Gap 3 + Gap 7 of the world-model gap map (`data/vep-gapmap-a-world-model/
report.md` in firstmate's home): entities tracked across time with
persistent identities, and relationships between them - the review's
world-model level `Relationships` ("person holding object, looking at
person, referring to visible object").

**What the person lanes carry, and what that decides.** Every detector
here is a geometric rule over lanes already built - M3 (`persons.json`:
face boxes with head pose, lip landmarks, 21 hand joints per hand at the
M2 cadence), M3b identity tracks and M3d person regions (`regions.json`:
an instance mask per person per M2 sample, each region track owned by
the face track inside it) - no new model, no new vision pass, no decode.
All three of the review's relationship types are measurable from that:

- `holding` - a confident joint of one hand within
  `HOLDING_MAX_JOINT_DISTANCE` face widths of another hand's, sustained.
  The review's form is "hand joints near OBJECT box"; no lane carries
  object boxes yet (objects are per-window VLM observations nothing
  tracks - MU-15 is that unit). What the lanes do carry is the other
  half of every hold: the two hands that meet - a clasp, a handshake, a
  handoff, a two-handed grip. That is what this measures.
- `pointing` - a hand's wrist->fingertip ray within `POINTING_MAX_ANGLE_DEG`
  of the direction to another tracked face's centre, the hand extended
  past `POINTING_MIN_HAND_EXTENSION` face widths. The review's form
  verbatim ("hand joint extended toward a direction"), the direction
  aimed at the nearest other tracked face in the frame.
- `looking_at` - a head-pose ray intersecting another person's region.
  The face's yaw (M3, from the face-rectangles request) says which way
  the head is turned; a head turned at least `LOOKING_MIN_YAW_RAD` off
  the camera casts a ray from the face centre along the image row,
  toward the side it is turned to, and the FIRST other person region
  the ray meets on the M3d mask grid is the target. The sign convention
  is measured, not assumed: on the MU-03 fixture (`tests/fixtures/mu03/
  turned-face.jpg`, the head turned toward image right) the helper reads
  yaw +0.715 rad, and -0.723 on its mirror - positive yaw faces +x.
  Pitch is not used: its sign has no measurement here, and a
  person region is tall enough that a level ray meets a seated or
  standing partner at the same height.

**Candidates, not answers.** All three are cheap geometric rules over
imprecise signals - a hand near a hand is not always a hold, an extended
hand aimed at a face is not always a point, a head turned toward a
person is not always looking at them. They are recorded as CANDIDATE
spans under the same standing decision as M7's `hand_near_mouth`: each
frame carries its measured distance, angle or yaw so the local VLM can
verify a span without re-reading the lanes. `holding` and `looking_at`
frames carry the `d` that M8's `span_verification.select_frames` ranks
by, so `span_verification.verify_spans` takes their spans as they are
(wiring relationship `--verify` into the query lane is that lane's work,
not this slot's).

**Who is who.** Faces take M3b ArcFace tracks by position
(`event_spans.assign_face_tracks`). A hand belongs to the person whose
REGION its wrist lies in - the region track's owning face track - not
to the nearest face: a hand whose owner is out of frame is its own
faceless region (the LCATL0013 t~60-90 case M3d measured), and the
nearest-face rule would hand it to whoever sits closest. A relationship
span names both participants' tracks. A hand in no owned region, a
target face no track claims, or a ray that meets an unowned region
first is left out of named relationships rather than guessed into one.

**The coordinate frame.** M3 is image-normalised, so a distance is
measured in PIXELS: x scales by the frame width, y by its height, and
the unit is the frame's mean face width (the isotropic rule
`person_measurements` documents - the coordinate mistake that read the
hand-over-mouth eval's false positives). A frame with no face carries no
scale, and a distance without a scale is not a measurement. A frame
whose M3d sample was not decoded (`instances: null`) carries no regions,
and is not measured either.

    python3 -m library.tools.relationships build <project>
    python3 -m library.tools.relationships status <project>
"""

from __future__ import annotations

import argparse
import datetime
import json
import math
import sys
from collections.abc import Sequence
from pathlib import Path

from library.tools import (
    event_spans,
    person_entity,
    person_measurements,
    source_memory,
)

STATUS_BUILT = "built"

CLAIM_TYPES = {
    "relationship": "relation.candidate_span",
}
"""The claims this producer writes, and the registry type each one is
filed under. The class of a claim is a consequence of that type
(``claim_provenance``): the producer says how the value was produced,
and no reader re-guesses it from the value's shape."""

RELATIONS = ("holding", "pointing", "looking_at")
"""The relationship types an M10 record carries, in record order."""

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

LOOKING_MIN_YAW_RAD = 0.35
"""A head turned less than this (radians, ~20 deg) off the camera casts
no looking-at ray: a near-frontal face is addressing the lens, and the
side its small yaw leans to says little about whom it faces. A turned
head in a two-shot reads well past it (the MU-03 fixture's three-quarter
turn reads 0.715). A candidate cutoff, not an answer - the VLM disposes
(M8)."""

LOOKING_AT_MIN_FRAMES = 2
"""The same sustain rule as holding and pointing: one 2 Hz sample of a
turned head is a glance past, not a look."""

M10_METHOD = "relationships-v2 over M3 pose + M3d regions"
"""The slot version `footage_analysis.slot_is_fresh` checks. v2 owns
hands by region instead of by nearest face and adds `looking_at`; a v1
record (no regions, looking-at refused) rebuilds."""

POINTING_TIP_SUFFIX = "TIP"
"""A fingertip joint by its Vision name suffix (indexTIP, middleTIP,
ringTIP, littleTIP, thumbTIP). Matched case-insensitively so an SDK
rename of the finger joints does not silently turn every hand into a
fist."""


# ── the geometry ─────────────────────────────────────────────────────


def _pixels(m3: dict) -> tuple[float, float]:
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


def _confident_joints(hand: dict) -> dict[str, list]:
    """A hand's joints at or above `HAND_JOINT_MIN_CONFIDENCE` - the
    floor every hand measurement so far has used (event_spans)."""
    return {name: joint for name, joint in hand["joints"].items()
            if joint[2] >= event_spans.HAND_JOINT_MIN_CONFIDENCE}


def _frame_scale(m3: dict, frame: dict) -> float | None:
    """The frame's mean face width in pixels - the unit every distance
    here is measured in. None with no face in the frame: no scale, no
    measurement."""
    width, _height = _pixels(m3)
    widths = [(face["box"][2] - face["box"][0]) * width
              for face in frame["faces"]]
    widths = [w for w in widths if w > 0]
    return sum(widths) / len(widths) if widths else None


class RegionFrames:
    """M3d's person regions per M3 frame, decoded one frame at a time.

    A whole source's masks decoded at once is ~1 GB for an hour at 2 Hz
    on the 384 x 216 grid; one frame's is a few hundred KB, so `at`
    decodes on call. Each instance comes back as `{mask, owner, face,
    track}`: `owner` is the region TRACK's face track (the measured
    majority link, `person_regions.track_owner`), `face` the index of
    the M3 face inside it at this sample, or None.
    """

    def __init__(self, m3d: dict, runs: list[list]):
        self.grid = (int(m3d["grid"][0]), int(m3d["grid"][1]))
        self.samples = m3d["samples"]
        self.runs = runs
        self.owners = {track["track_id"]: track.get("face_track")
                       for track in m3d.get("tracks") or []}

    def at(self, i: int) -> list[dict] | None:
        """Frame `i`'s instances, or None when the sample was not
        decoded - no regions, which is not 'nobody there'."""
        from library.tools.person_regions import decode_runs
        instances = self.samples[i]["instances"]
        if instances is None:
            return None
        return [{"mask": decode_runs(self.runs[inst["mask"]], self.grid),
                 "owner": self.owners.get(inst["track"]),
                 "face": inst.get("face"),
                 "track": inst["track"]}
                for inst in instances]


def read_regions(content_digest: str, m3: dict,
                 root: Path | None = None) -> RegionFrames:
    """The source's M3d as `RegionFrames` aligned to `m3`'s frames.
    Refuses (raises) without M3d, or with one measured on another
    sample - a frame index would then name a different instant."""
    from library.tools import person_regions
    m3d = person_regions.read_m3d(content_digest, root)
    if m3d is None:
        raise RuntimeError("no M3d (regions.json); run "
                           "`python3 -m library.tools.person_regions build`")
    if [s["t"] for s in m3d["samples"]] != [f["t"] for f in m3["frames"]]:
        raise RuntimeError("M3d was not measured on this M3's sample; "
                           "rebuild M3d")
    return RegionFrames(m3d, person_regions.read_masks(content_digest, m3d,
                                                       root))


def _cell(point: Sequence[float], grid: tuple[int, int]) -> tuple[int, int]:
    """`(row, col)` of the grid cell an image-normalised point is in."""
    width, height = grid
    col = min(width - 1, max(0, int(point[0] * width)))
    row = min(height - 1, max(0, int(point[1] * height)))
    return row, col


def _hand_owners(m3: dict, regions: RegionFrames
                 ) -> list[list[str | None]]:
    """Per frame, per hand: the face track owning the person region its
    wrist lies in, or None (no wrist, no region there, an unowned
    region, or an undecoded sample). Containment, never the nearest
    face: an off-frame owner's hand is its own faceless region."""
    out: list[list[str | None]] = []
    for i, frame in enumerate(m3["frames"]):
        instances = regions.at(i) if frame["hands"] else []
        owners: list[str | None] = []
        for hand in frame["hands"]:
            wrist = next((j for name, j in _confident_joints(hand).items()
                          if name.lower() == "wrist"), None)
            if wrist is None or instances is None:
                owners.append(None)
                continue
            row, col = _cell(wrist, regions.grid)
            owners.append(next((inst["owner"] for inst in instances
                                if inst["mask"][row, col]), None))
        out.append(owners)
    return out


def _spans_from_hits(key_frames: dict[tuple, dict[int, dict]],
                     times: Sequence[float], min_frames: int) -> list[dict]:
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


def holding_candidates(m3: dict, regions: RegionFrames) -> list[dict]:
    """Runs of frames where two hands' confident joints come within
    `HOLDING_MAX_JOINT_DISTANCE` face widths of each other.

    A hand is owned by the person region its wrist lies in
    (`_hand_owners`). A pair with an unowned hand is skipped, not guessed: a relationship
    a participant cannot be named for is not a measurement. The two
    hands may belong to the same person - a two-handed grip is the most
    common hold there is.
    """
    width, height = _pixels(m3)
    times = [f["t"] for f in m3["frames"]]
    owners = _hand_owners(m3, regions)
    key_frames: dict[tuple, dict[int, dict]] = {}
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


def _fingertips(joints: dict[str, list]) -> list[list]:
    """The hand's confident fingertip joints (any finger - a pointing
    hand extends whichever finger points)."""
    return [joint for name, joint in joints.items()
            if name.upper().endswith(POINTING_TIP_SUFFIX)]


def pointing_candidates(m3: dict,
                        assignment: list[list[str | None]],
                        regions: RegionFrames) -> list[dict]:
    """Runs of frames where a hand's extended wrist->fingertip ray is
    aimed at another tracked face's centre.

    The target is a DIFFERENT tracked face in the same frame - pointing
    at one's own face is the hand-at-mouth rule's business (M7), not a
    relationship. A fist does not point: the wrist->fingertip distance
    must clear `POINTING_MIN_HAND_EXTENSION` face widths. The pointer
    is the hand's region owner (`_hand_owners`); the target is a face,
    by its M3b track.
    """
    width, height = _pixels(m3)
    times = [f["t"] for f in m3["frames"]]
    owners = _hand_owners(m3, regions)
    key_frames: dict[tuple, dict[int, dict]] = {}
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


# ── looking at (head-pose ray meets another person's region) ─────────


def _ray_target(instances: list[dict], face_index: int, owner: str,
                row: int, start_col: int, step: int
                ) -> tuple[dict, int] | None:
    """The first instance the ray along grid row `row` meets, walking
    from `start_col` in direction `step`, with the column it met it at.
    The looker's own region (the one their face is inside, or any their
    track owns) is transparent; anything else stops the ray, owned or
    not - a head turned toward an unnamed person is looking at THAT
    person, not through them at whoever sits behind."""
    width = instances[0]["mask"].shape[1] if instances else 0
    col = start_col
    while 0 <= col < width:
        for inst in instances:
            if inst["face"] == face_index or inst["owner"] == owner:
                continue
            if inst["mask"][row, col]:
                return inst, col
        col += step
    return None


def looking_at_candidates(m3: dict,
                          assignment: list[list[str | None]],
                          regions: RegionFrames) -> list[dict]:
    """Runs of frames where a tracked face's head-pose ray meets another
    person's region.

    The ray leaves the face box's edge on the side the head is turned to
    (positive yaw faces +x, measured - module docstring), along the
    image row of the face centre. Its target is the first other region
    it meets; a target region no face track owns ends the ray without a
    named relationship. Each frame records the yaw, the `reach` to the
    target in face widths, and `d`: the head's angular offset from the
    straight-across direction (pi/2 - |yaw|), the bearing to a partner
    at the looker's own depth - the span-verification rank, smaller
    meaning more squarely turned toward them.
    """
    width, _height = _pixels(m3)
    times = [f["t"] for f in m3["frames"]]
    key_frames: dict[tuple, dict[int, dict]] = {}
    grid_w = regions.grid[0]
    for i, frame in enumerate(m3["frames"]):
        scale = _frame_scale(m3, frame)
        if scale is None or scale <= 0:
            continue
        lookers = [(f, face, track_id) for f, (face, track_id)
                   in enumerate(zip(frame["faces"], assignment[i]))
                   if track_id is not None
                   and ((face.get("pose") or {}).get("yaw") is not None)
                   and abs(face["pose"]["yaw"]) >= LOOKING_MIN_YAW_RAD]
        if not lookers:
            continue
        instances = regions.at(i)
        if not instances:
            continue
        for f, face, looker in lookers:
            yaw = face["pose"]["yaw"]
            step = 1 if yaw > 0 else -1
            x1, y1, x2, y2 = face["box"]
            cx, cy = (x1 + x2) / 2.0, (y1 + y2) / 2.0
            row, _ = _cell((cx, cy), regions.grid)
            _, start = _cell((x2 if step > 0 else x1, cy), regions.grid)
            hit = _ray_target(instances, f, looker, row, start, step)
            if hit is None:
                continue
            inst, col = hit
            target = inst["owner"]
            if target is None or target == looker:
                continue
            reach = abs((col + 0.5) / grid_w - cx) * width / scale
            key = (looker, target)
            held = key_frames.setdefault(key, {}).get(i)
            record = {"index": i, "t": times[i],
                      "yaw": round(yaw, 3),
                      "d": round(math.pi / 2.0 - abs(yaw), 3),
                      "reach": round(reach, 3),
                      "region": inst["track"]}
            if held is None or record["d"] < held["d"]:
                key_frames[key][i] = record
    return _spans_from_hits(key_frames, times, LOOKING_AT_MIN_FRAMES)


# ── building M10 ──────────────────────────────────────────────────────


def build_source_relationships(content_digest: str, source_file: str,
                               root: Path | None = None) -> dict:
    """M10 for one source from its M3 + M3b + M3d. Light: no decode of
    the media, no model. Refuses (raises) without any of the three - an
    unbuilt source would read as 'no relationships here'."""
    m3 = person_measurements.read_m3(content_digest, root)
    identity = person_entity.read_identity(content_digest, root)
    if m3 is None:
        raise RuntimeError("no M3 (persons.json); run "
                           "`python3 -m library.tools.person_measurements build`")
    if identity is None:
        raise RuntimeError("no M3b (identity.json); run "
                           "`python3 -m library.tools.person_entity build`")
    regions = read_regions(content_digest, m3, root)
    assignment = event_spans.assign_face_tracks(m3, identity)
    holding = holding_candidates(m3, regions)
    pointing = pointing_candidates(m3, assignment, regions)
    looking_at = looking_at_candidates(m3, assignment, regions)
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
                         f"another hand's, each hand owned by the person "
                         f"region its wrist lies in, for >= "
                         f"{HOLDING_MIN_FRAMES} frames; a geometric "
                         f"CANDIDATE for VLM verification, never an answer "
                         f"(the review's object-box form awaits object "
                         f"tracking - no lane carries object boxes)",
                "spans": holding},
            "pointing": {
                "basis": f"a hand's wrist->fingertip ray within "
                         f"{POINTING_MAX_ANGLE_DEG} deg of another "
                         f"tracked face's centre, the hand extended >= "
                         f"{POINTING_MIN_HAND_EXTENSION} face widths and "
                         f"owned by the person region its wrist lies in, "
                         f"for >= {POINTING_MIN_FRAMES} frames; a geometric "
                         f"CANDIDATE for VLM verification, never an answer",
                "spans": pointing},
            "looking_at": {
                "basis": f"a head turned >= {LOOKING_MIN_YAW_RAD} rad of "
                         f"yaw casts a ray along the face centre's image "
                         f"row toward the side it faces (positive yaw "
                         f"faces +x), and the first other person region it "
                         f"meets is the target, for >= "
                         f"{LOOKING_AT_MIN_FRAMES} frames; pitch unused; a "
                         f"geometric CANDIDATE for VLM verification, never "
                         f"an answer",
                "spans": looking_at},
        },
        "instrument": {
            "method": M10_METHOD,
            "reads": [source_memory.SLOT_PERSONS, source_memory.SLOT_IDENTITY,
                      source_memory.SLOT_REGIONS],
        },
        "built_at": datetime.datetime.now(datetime.UTC).isoformat(
            timespec="seconds"),
    }
    source_memory.write_json(
        source_memory.source_dir(content_digest, root)
        / source_memory.SLOT_RELATIONSHIPS, record)
    return {"content_digest": content_digest, "source_file": source_file,
            "holding_spans": len(holding),
            "pointing_spans": len(pointing),
            "looking_at_spans": len(looking_at)}


@source_memory._project_scoped
def build_project_relationships(project_folder: str,
                                clip_ids: Sequence[str] | None = None,
                                root: Path | None = None) -> dict:
    """M10 for every catalog source (or just `clip_ids`, as the
    `ren analyze` lane asks) that carries M3, M3b and M3d. Same
    reporting shape as `event_spans.build_project_events`."""
    wanted = set(clip_ids) if clip_ids is not None else None
    results, failed = [], []
    for clip_id, digest, path in event_spans.catalog_sources(project_folder):
        if wanted is not None and clip_id not in wanted:
            continue
        try:
            account = build_source_relationships(digest, path, root)
        # One source's refusal (an unbuilt lane) is that source's row; the
        # rest of the catalog is still measured.
        except Exception as exc:  # noqa: BLE001
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
        description="M10: relationship candidate spans (holding, pointing, "
                    "looking_at) from M3 + M3b + M3d. Light: no decode, "
                    "no model.")
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
                     "m10": STATUS_BUILT if doc else "missing",
                     **{name: (len(doc["relationships"][name]["spans"])
                               if doc and name in doc["relationships"] else 0)
                        for name in RELATIONS}})
    print(json.dumps({"clips": rows}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
