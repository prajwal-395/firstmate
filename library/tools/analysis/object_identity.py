"""Object identity: one object, one id, across cuts and angles.

WHAT THIS ANSWERS
-----------------
A request names an object ("the laptop"). The detector answers with
boxes, one per frame, and every answer arrives on its own: a box at
15 s and a box at 45 s of the same object are two answers, and a box
in one camera angle and a box in another are two answers. A reference
to the object therefore resolves to nothing, and the reference is the
thing a reader asks for.

This module is the identity half of the object lane. It takes the
per-frame answers the detector already produces and gives them ONE
identity per physical object, with the identity's VISIBILITY state
measured rather than assumed.

THE THREE DECISIONS, AND WHAT EACH ONE REFUSES
-----------------------------------------------
1. **Within a clip.** Detections of the same class in consecutive
   sampled frames are associated by box overlap (IoU), one to one. An
   association that two tracks explain about equally well is AMBIGUOUS:
   the detection starts a new identity rather than being merged into a
   track it might not belong to. A forced match is the failure this
   refuses.

2. **Across clips.** Two tracks of the same class in different clips
   are linked only when their appearances agree AND the agreement is
   unambiguous - the best candidate must beat the runner-up by a
   measured margin, and a candidate may take one partner only. A class
   whose identity another lane owns (a PERSON is established by the
   face and voice lanes, not by an object appearance descriptor) is
   refused for cross-clip linking and says so.

3. **Visibility.** A detection whose box runs out of frame does not
   bound the object, so the object is PARTIAL there and the lane
   abstains instead of claiming a box it cannot stand behind. A track
   that is alive and has no detection in an inspected frame reports
   ABSENT there. Absence is said, never left as silence.

SCOPE
-----
Identities are PROJECT-LOCAL: a link never crosses a scope boundary,
and the scope is the manifest's. Linking two clips that belong to
different measured conversations is allowed inside one scope and is
recorded as such; what is refused is a link whose evidence is not
there.

WHAT THIS DOES NOT DO
---------------------
It does not detect. The boxes come from
:mod:`library.tools.analysis.open_vocabulary_detect`, which is the
lane's detection producer, and this module never loads a model of its
own. It does not segment. It does not decide whether an object is the
SAME object across two angles when the footage does not say so - it
abstains, and the abstention is reported.

MEASUREMENT
-----------
The identity claims are measured against held-out human labels with
:mod:`library.tools.object_continuity_benchmark`, whose scorer is
independent of this producer. The measured report and the corpus are
in ``docs/benchmarks/``.

    # the identity lane over a frame manifest (heavy: it loads a detector)
    bin/vep -m library.tools.analysis.object_identity predict \\
        --manifest docs/benchmarks/2026-10-21-vi007b-object-identity-frames.json \\
        --out /tmp/vi007b-predictions.json

    # the incumbent behaviour, for the before/after (no identity at all:
    # every detection its own id, every cross-clip link abstained)
    bin/vep -m library.tools.analysis.object_identity predict \\
        --manifest <same> --mode per_frame --out /tmp/vi007b-before.json
"""

from __future__ import annotations

import argparse
import json
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

MANIFEST_SCHEMA = "ren_object_identity_frames/1"
"""The schema a frame manifest carries."""

PREDICTION_SCHEMA = "object-continuity-predictions-v1"
"""The schema this producer's prediction envelope carries.

The scorer in :mod:`library.tools.object_continuity_benchmark` reads
the envelope's `observations`, `producer` and `cost`; the schema name
is this producer's own, so a reader can tell whose predictions it is
scoring."""

# ── the vocabulary ────────────────────────────────────────────────────────

VISIBLE = "visible"
PARTIAL = "partial"
ABSENT = "absent"

LINKED = "linked"
ABSTAINED = "abstained"

OBSERVED = "observed"
ABSTAIN = "abstained"

PERSON_CLASSES = ("person", "human", "people", "man", "woman")
"""The classes whose cross-clip identity another lane owns.

A person's identity is established by the face and voice lanes
(:mod:`library.tools.person_entity`), which measure the face and the
voice. An object appearance descriptor cannot tell two people apart
and must not claim to: this lane refuses to link a person across clips
and says so, instead of guessing.

An object and a person may be the same physical thing seen twice; the
rule is about the CLASS, not the instance."""

# ── the measured settings ────────────────────────────────────────────────

DEFAULT_IOU_THRESHOLD = 0.30
"""The box overlap at which two detections are the same object.

Measured on the calibration corpus (the D-01 podcast frames plus
LCATL0011 at the same seconds, whose labels the tuning worktree may
read). The same object's box between two frames of one clip overlaps
at 0.826 to 1.000 (12 pairs, median 0.914); two DIFFERENT objects of
the same class in one frame overlap at 0.000. The threshold sits an
order of magnitude below the measured true-pair overlap - so a detector
box that moves a little still continues its track - and far above the
measured different-object overlap."""

DEFAULT_LINK_THRESHOLD = 0.62
"""The appearance agreement at which two tracks may be the same object.

Measured on the calibration corpus, over 96 cross-clip pairs of the
studio's two laptops:

- the SAME device seen from two clips: 0.821 to 0.883 (16 pairs);
- a DIFFERENT device of the same class: 0.346 to 0.506 (32 pairs);
- the same device seen EDGE-ON in one clip: 0.386 to 0.469 (16 pairs).

The threshold sits in the gap between the first two bands. The third
band falls BELOW the different-device band: a colour histogram of a
device seen edge-on cannot be told from a different device, so that
link is REFUSED and reported as an abstention. A refusal is the true
answer there, not a failure to be tuned away."""

DEFAULT_LINK_MARGIN = 0.08
"""How far the best candidate must beat the runner-up.

Measured on the calibration corpus: the smallest margin among the
calibration's TRUE pairs is 0.315 (0.821 - 0.506). The threshold sits
far below it, so the rule bites only when two candidates come close:
a candidate within 0.08 of the best refuses the link rather than
taking it."""

DEFAULT_AMBIGUITY_MARGIN = 0.05
"""How far the best track must beat the runner-up within a frame.

The same rule as the cross-clip one, applied to the within-clip
association: a detection that two tracks explain about equally well is
not merged into either."""

EDGE_TOUCH_FRACTION = 0.005
"""How close a box must come to a frame edge to count as running out
of frame.

The same tolerance the D-01 corpus uses for its PARTIAL stratum, so
the two measurements talk about the same geometry."""

MIN_VISIBLE_AREA_FRACTION = 0.25
"""How small a detection may be, relative to its own track's median
box, and still count as a usable view of the object.

A track's median box is the object's usual size in that clip. A
detection that collapses to a fraction of it is the object partly
hidden, not the object seen small."""


# ── the shapes ────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Frame:
    """One inspected frame of one source."""

    frame_id: str
    clip_id: str
    time_s: float
    image: str
    width: int = 0
    height: int = 0
    scope: str = ""
    angle_group: str = ""
    source_path: str = ""
    """The media this frame was cut from, read-only."""


@dataclass(frozen=True)
class ObservedDetection:
    """One detector answer: a class, a box, and where it was measured."""

    clip_id: str
    frame_id: str
    time_s: float
    phrase: str
    box: tuple[float, float, float, float]
    score: float
    image: str = ""


@dataclass
class Track:
    """One identity's observations, inside one clip."""

    entity_id: str
    phrase: str
    clip_id: str
    observations: list[dict[str, Any]] = field(default_factory=list)
    started_ambiguous: bool = False
    """True when the track began because the association was ambiguous.

    A track that starts ambiguous is a refusal to merge, recorded so
    the report can count it instead of hiding it in an id."""

    @property
    def first_time(self) -> float:
        return min(row["time_s"] for row in self.observations)

    @property
    def last_time(self) -> float:
        return max(row["time_s"] for row in self.observations)

    @property
    def last_box(self) -> tuple[float, float, float, float]:
        return self.observations[-1]["box"]

    @property
    def median_area(self) -> float:
        areas = sorted(
            (row["box"][2] - row["box"][0]) * (row["box"][3] - row["box"][1])
            for row in self.observations)
        return areas[len(areas) // 2]


# ── geometry ──────────────────────────────────────────────────────────────

def box_iou(a: Sequence[float], b: Sequence[float]) -> float:
    """Intersection over union of two normalized boxes."""
    ax0, ay0, ax1, ay1 = (float(v) for v in a)
    bx0, by0, bx1, by1 = (float(v) for v in b)
    ix0, iy0 = max(ax0, bx0), max(ay0, by0)
    ix1, iy1 = min(ax1, bx1), min(ay1, by1)
    inter = max(0.0, ix1 - ix0) * max(0.0, iy1 - iy0)
    area_a = max(0.0, ax1 - ax0) * max(0.0, ay1 - ay0)
    area_b = max(0.0, bx1 - bx0) * max(0.0, by1 - by0)
    union = area_a + area_b - inter
    return inter / union if union > 0 else 0.0


def box_touches_edge(box: Sequence[float], frame: Frame) -> bool:
    """Whether a box runs out of frame.

    A box within `EDGE_TOUCH_FRACTION` of an edge touches it, and an
    object that touches the frame edge is not bounded by the frame:
    the box describes the visible part, not the object. The same
    tolerance the D-01 corpus measures its PARTIAL stratum with.
    """
    if not frame.width or not frame.height:
        return False
    x0, y0, x1, y1 = (float(v) for v in box)
    tolerance_x = EDGE_TOUCH_FRACTION
    tolerance_y = EDGE_TOUCH_FRACTION
    return (x0 <= tolerance_x or y0 <= tolerance_y
            or x1 >= 1.0 - tolerance_x or y1 >= 1.0 - tolerance_y)


# ── the within-clip association ───────────────────────────────────────────

def associate_tracks(frames: Sequence[Frame],
                     detections: Sequence[ObservedDetection],
                     *, iou_threshold: float = DEFAULT_IOU_THRESHOLD,
                     ambiguity_margin: float = DEFAULT_AMBIGUITY_MARGIN,
                     ) -> list[Track]:
    """Give every detection the identity of the object it continues.

    Frames are visited in time order, clip by clip. A detection is
    associated with the live track of the same class it overlaps most,
    when that overlap clears the threshold and beats the runner-up by
    the ambiguity margin. A detection nothing explains starts a new
    track; a detection two tracks explain about equally well starts a
    new track too, and the track remembers that it began that way.
    """
    by_frame: dict[tuple[str, str], list[ObservedDetection]] = {}
    for detection in detections:
        by_frame.setdefault((detection.clip_id, detection.frame_id),
                             []).append(detection)
    tracks: list[Track] = []
    counter = 0

    def new_track(detection: ObservedDetection,
                  ambiguous: bool = False) -> Track:
        nonlocal counter
        counter += 1
        track = Track(entity_id=f"obj-{counter:04d}", phrase=detection.phrase,
                      clip_id=detection.clip_id, started_ambiguous=ambiguous)
        tracks.append(track)
        return track

    ordered = sorted(frames, key=lambda frame: (frame.clip_id, frame.time_s,
                                                 frame.frame_id))
    for frame in ordered:
        found = by_frame.get((frame.clip_id, frame.frame_id), [])
        if not found:
            continue
        live = [track for track in tracks if track.clip_id == frame.clip_id
                and track.observations]
        pairs: list[tuple[float, Track, ObservedDetection]] = []
        for detection in found:
            for track in live:
                if track.phrase != detection.phrase:
                    continue
                pairs.append((box_iou(track.last_box, detection.box), track,
                              detection))
        best_for_detection: dict[int, list[tuple[float, Track]]] = {}
        for index, detection in enumerate(found):
            best_for_detection[index] = sorted(
                ((score, track) for score, track, det in pairs
                 if det is detection), key=lambda row: row[0], reverse=True)
        ambiguous: set[int] = set()
        for index, ranked in best_for_detection.items():
            if len(ranked) < 2:
                continue
            if ranked[0][0] - ranked[1][0] < ambiguity_margin:
                ambiguous.add(index)
        pairs.sort(key=lambda row: row[0], reverse=True)
        taken_tracks: set[int] = set()
        taken_detections: set[int] = set()
        assigned: dict[int, Track] = {}
        for score, track, detection in pairs:
            if score < iou_threshold:
                break
            index = found.index(detection)
            if index in ambiguous or index in taken_detections:
                continue
            if id(track) in taken_tracks:
                continue
            assigned[index] = track
            taken_detections.add(index)
            taken_tracks.add(id(track))
        for index, detection in enumerate(found):
            track = assigned.get(index)
            if track is None:
                track = new_track(detection, ambiguous=index in ambiguous)
            track.observations.append({
                "frame_id": frame.frame_id,
                "time_s": frame.time_s,
                "box": tuple(float(v) for v in detection.box),
                "score": float(detection.score),
                "image": frame.image,
                "frame": frame,
            })
    return tracks


# ── the appearance ────────────────────────────────────────────────────────

def describe_track(track: Track) -> tuple[float, ...] | None:
    """The appearance descriptor of one track's tightest view.

    The descriptor is the person lane's model-free colour histogram
    (:mod:`library.tools.body_appearance`), reused here for an object
    crop: it is arithmetic over pixels, it carries no learned
    parameters, and it is already measured and already in the tree.

    The TIGHTEST view of the track is the one measured, and that is a
    measured decision rather than a taste. A detector's box is often
    padded with the studio's black table, and a crop padded with black
    measures the table: two different objects on that table then score
    the same. The calibration measured exactly that - the tablet seen
    face-on and seen edge-on score 0.386 to 0.469 on the labelled
    crops, but 0.847 on a padded crop against the strip, because both
    are mostly black. The tightest crop is the one that carries the
    object, and a track whose tightest view is a fragment is measured
    on that fragment, which is the truth about that view.
    """
    import cv2

    from library.tools import body_appearance

    if not track.observations:
        return None
    observation = min(
        track.observations,
        key=lambda row: ((row["box"][2] - row["box"][0])
                          * (row["box"][3] - row["box"][1])))
    path = observation.get("image") or ""
    if not path or not Path(path).is_file():
        return None
    frame_bgr = cv2.imread(path, cv2.IMREAD_COLOR)
    if frame_bgr is None:
        return None
    descriptors, skipped = body_appearance.describe_frame_boxes(
        frame_bgr, [observation["box"]])
    if skipped or not descriptors:
        return None
    return descriptors[0]


def appearance_similarity(a: Sequence[float],
                          b: Sequence[float]) -> float:
    """Cosine similarity of two appearance descriptors."""
    from library.tools import body_appearance

    return float(body_appearance.match(list(a), list(b)))


# ── the cross-clip link ───────────────────────────────────────────────────

@dataclass
class LinkDecision:
    """What the cross-clip pass decided about one track."""

    entity_id: str
    phrase: str
    clip_id: str
    status: str
    reason: str
    partner: str = ""
    similarity: float | None = None
    margin: float | None = None


def link_tracks(tracks: Sequence[Track],
                descriptors: dict[str, tuple[float, ...]],
                *, threshold: float = DEFAULT_LINK_THRESHOLD,
                margin: float = DEFAULT_LINK_MARGIN,
                scopes: dict[str, str] | None = None,
                ) -> tuple[dict[str, str], list[LinkDecision]]:
    """Decide which tracks in different clips are the same object.

    Returns the merged identity map (a track's entity id -> the id of
    the identity it belongs to) and one decision per track, so an
    abstention is reported rather than left as silence.

    A link needs four things, and refuses without all of them:
    the same class, the same scope, an appearance at or above the
    measured threshold, and a margin over the runner-up. A person is
    refused outright: that identity belongs to the face and voice
    lanes.
    """
    scopes = scopes or {}
    decisions: list[LinkDecision] = []
    partner_of: dict[str, str] = {}
    pairs: list[tuple[str, str]] = []
    for track in tracks:
        if track.phrase.strip().lower() in PERSON_CLASSES:
            decisions.append(LinkDecision(
                track.entity_id, track.phrase, track.clip_id, ABSTAINED,
                "person identity is established by the face and voice "
                "lanes, not by an object appearance descriptor"))
            continue
        mine = descriptors.get(track.entity_id)
        if mine is None:
            decisions.append(LinkDecision(
                track.entity_id, track.phrase, track.clip_id, ABSTAINED,
                "no appearance could be measured for this track"))
            continue
        candidates: list[tuple[float, Track]] = []
        for other in tracks:
            if other.entity_id == track.entity_id:
                continue
            if other.clip_id == track.clip_id:
                continue
            if other.phrase != track.phrase:
                continue
            if scopes.get(other.clip_id) != scopes.get(track.clip_id):
                continue
            theirs = descriptors.get(other.entity_id)
            if theirs is None:
                continue
            candidates.append((appearance_similarity(mine, theirs), other))
        if not candidates:
            decisions.append(LinkDecision(
                track.entity_id, track.phrase, track.clip_id, ABSTAINED,
                "no candidate of this class in another clip of the scope"))
            continue
        candidates.sort(key=lambda row: row[0], reverse=True)
        best_score, best = candidates[0]
        runner_up = candidates[1][0] if len(candidates) > 1 else 0.0
        difference = best_score - runner_up
        if best_score < threshold:
            decisions.append(LinkDecision(
                track.entity_id, track.phrase, track.clip_id, ABSTAINED,
                "appearance below the measured link threshold",
                similarity=round(best_score, 6),
                margin=round(difference, 6)))
            continue
        if difference < margin:
            decisions.append(LinkDecision(
                track.entity_id, track.phrase, track.clip_id, ABSTAINED,
                "two candidates agree about as well; the link is ambiguous",
                similarity=round(best_score, 6),
                margin=round(difference, 6)))
            continue
        if best.entity_id in partner_of or track.entity_id in partner_of:
            if partner_of.get(best.entity_id) == track.entity_id:
                # The same link, seen from its other side: the two tracks
                # name each other. It is ONE link, not two.
                decisions.append(LinkDecision(
                    track.entity_id, track.phrase, track.clip_id, LINKED,
                    "appearance and margin establish the same object",
                    partner=best.entity_id, similarity=round(best_score, 6),
                    margin=round(difference, 6)))
                continue
            decisions.append(LinkDecision(
                track.entity_id, track.phrase, track.clip_id, ABSTAINED,
                "one object takes one partner; this candidate already "
                "holds one", similarity=round(best_score, 6),
                margin=round(difference, 6)))
            continue
        partner_of[track.entity_id] = best.entity_id
        partner_of[best.entity_id] = track.entity_id
        pairs.append((track.entity_id, best.entity_id))
        decisions.append(LinkDecision(
            track.entity_id, track.phrase, track.clip_id, LINKED,
            "appearance and margin establish the same object",
            partner=best.entity_id, similarity=round(best_score, 6),
            margin=round(difference, 6)))
    identity = _merge_identities(tracks, pairs)
    return identity, decisions


def _merge_identities(tracks: Sequence[Track],
                      pairs: Sequence[tuple[str, str]]) -> dict[str, str]:
    """One id per linked component, whichever id the component has.

    A link is symmetric: two tracks that are the same object get ONE
    id, whichever of the two the reader sees first. The id is the
    smallest in the component, so the same detections always resolve to
    the same name.
    """
    parent: dict[str, str] = {track.entity_id: track.entity_id
                              for track in tracks}

    def find(node: str) -> str:
        while parent[node] != node:
            parent[node] = parent[parent[node]]
            node = parent[node]
        return node

    for left, right in pairs:
        left_root, right_root = find(left), find(right)
        if left_root == right_root:
            continue
        keep, drop = sorted((left_root, right_root))
        parent[drop] = keep
    return {track.entity_id: find(track.entity_id) for track in tracks}


# ── the resolution ────────────────────────────────────────────────────────

@dataclass
class Resolution:
    """What the lane resolved: identities, states, links and refusals."""

    tracks: list[Track]
    identity: dict[str, str]
    decisions: list[LinkDecision]
    observations: list[dict[str, Any]]
    report: dict[str, Any]


def resolve(frames: Sequence[Frame],
            detections: Sequence[ObservedDetection],
            *, iou_threshold: float = DEFAULT_IOU_THRESHOLD,
            link_threshold: float = DEFAULT_LINK_THRESHOLD,
            link_margin: float = DEFAULT_LINK_MARGIN,
            ambiguity_margin: float = DEFAULT_AMBIGUITY_MARGIN,
            measure_appearance: bool = True,
            scope: str = "") -> Resolution:
    """Resolve the detections into identities, states and links.

    The observations are the prediction rows: one per (frame, identity)
    pair the lane has an answer about, carrying the identity, the
    class, the visibility state and whether the identity's cross-clip
    link was established or refused.

    `measure_appearance` is False for a caller that has no second clip
    to match an appearance against - a single window, where the
    appearance cannot decide anything and the pixels are about to be
    deleted.
    """
    tracks = associate_tracks(
        frames, detections, iou_threshold=iou_threshold,
        ambiguity_margin=ambiguity_margin)
    scopes = {frame.clip_id: (frame.scope or scope) for frame in frames}
    descriptors: dict[str, tuple[float, ...]] = {}
    if measure_appearance:
        for track in tracks:
            descriptor = describe_track(track)
            if descriptor is not None:
                descriptors[track.entity_id] = descriptor
    identity, decisions = link_tracks(
        tracks, descriptors, threshold=link_threshold, margin=link_margin,
        scopes=scopes)

    frames_by_clip: dict[str, list[Frame]] = {}
    for frame in frames:
        frames_by_clip.setdefault(frame.clip_id, []).append(frame)
    for rows in frames_by_clip.values():
        rows.sort(key=lambda frame: (frame.time_s, frame.frame_id))

    # An identity that was linked across clips is one that spans them:
    # its rows say "linked", and the rows of an identity that was not
    # linked say "abstained" - an abstention is a refusal to claim a
    # cross-clip identity, never a claim that there is none.
    linked_ids = {identity.get(decision.entity_id, decision.entity_id)
                  for decision in decisions if decision.status == LINKED}
    observations: list[dict[str, Any]] = []
    for track in tracks:
        entity_id = identity.get(track.entity_id, track.entity_id)
        seen_frames = {row["frame_id"]: row for row in track.observations}
        first = min(row["time_s"] for row in track.observations)
        last = max(row["time_s"] for row in track.observations)
        for frame in frames_by_clip.get(track.clip_id, []):
            if not (first - 1e-9 <= frame.time_s <= last + 1e-9):
                continue
            observation = seen_frames.get(frame.frame_id)
            if observation is None:
                observations.append({
                    "clip_id": track.clip_id,
                    "frame_id": frame.frame_id,
                    "entity_id": entity_id,
                    "class": track.phrase,
                    "status": ABSTAIN,
                    "state": ABSENT,
                    "cross_clip_identity": (LINKED if entity_id in linked_ids
                                            else ABSTAINED),
                    "time_s": frame.time_s,
                })
                continue
            box = observation["box"]
            area = (box[2] - box[0]) * (box[3] - box[1])
            partial = box_touches_edge(box, frame)
            if area < MIN_VISIBLE_AREA_FRACTION * track.median_area:
                partial = True
            observations.append({
                "clip_id": track.clip_id,
                "frame_id": frame.frame_id,
                "entity_id": entity_id,
                "class": track.phrase,
                "status": OBSERVED,
                "box": [round(float(value), 6) for value in box],
                "score": round(float(observation.get("score") or 0.0), 6),
                "state": PARTIAL if partial else VISIBLE,
                "cross_clip_identity": (LINKED if entity_id in linked_ids
                                        else ABSTAINED),
                "time_s": frame.time_s,
            })
    observations.sort(key=lambda row: (row["clip_id"], row["frame_id"],
                                        row["entity_id"]))
    report = {
        "tracks": len(tracks),
        "identities": len({identity.get(track.entity_id, track.entity_id)
                           for track in tracks}),
        "links": sum(1 for decision in decisions if decision.status == LINKED),
        "abstentions": sum(1 for decision in decisions
                            if decision.status == ABSTAINED),
        "ambiguous_starts": sum(1 for track in tracks
                                if track.started_ambiguous),
        "observations": len(observations),
        "observed": sum(1 for row in observations
                        if row["status"] == OBSERVED),
        "abstained": sum(1 for row in observations
                         if row["status"] == ABSTAIN),
        "decisions": [
            {"entity_id": decision.entity_id,
             "class": decision.phrase,
             "clip_id": decision.clip_id,
             "status": decision.status,
             "reason": decision.reason,
             "partner": decision.partner,
             "similarity": decision.similarity,
             "margin": decision.margin}
            for decision in decisions],
    }
    return Resolution(tracks=tracks, identity=identity, decisions=decisions,
                      observations=observations, report=report)


# ── the incumbent behaviour, for the before/after ────────────────────────

def per_frame_observations(
        frames: Sequence[Frame],
        detections: Sequence[ObservedDetection]) -> list[dict[str, Any]]:
    """The incumbent's shape of answer: every detection on its own.

    A per-frame detector has no identity to carry: each answer is a new
    id, and nothing links two clips. This is the behaviour the identity
    lane is measured against - the same detections, the same frames,
    with no identity at all.
    """
    by_frame = {frame.frame_id: frame for frame in frames}
    rows: list[dict[str, Any]] = []
    for index, detection in enumerate(detections):
        frame = by_frame.get(detection.frame_id)
        rows.append({
            "clip_id": detection.clip_id,
            "frame_id": detection.frame_id,
            "entity_id": f"det-{index:04d}",
            "class": detection.phrase,
            "status": OBSERVED,
            "box": [round(float(value), 6) for value in detection.box],
            "score": round(float(detection.score), 6),
            "state": VISIBLE,
            "cross_clip_identity": ABSTAINED,
            "time_s": detection.time_s,
            "frame_width": frame.width if frame else 0,
            "frame_height": frame.height if frame else 0,
        })
    rows.sort(key=lambda row: (row["clip_id"], row["frame_id"],
                                row["entity_id"]))
    return rows


# ── the manifest ──────────────────────────────────────────────────────────

def load_manifest(path: str | Path) -> tuple[list[Frame], dict[str, Any]]:
    """Read a frame manifest: the frames to inspect and their scope."""
    document = json.loads(Path(path).read_text(encoding="utf-8"))
    if document.get("schema") != MANIFEST_SCHEMA:
        raise ValueError(f"the manifest schema is {MANIFEST_SCHEMA!r}, not "
                         f"{document.get('schema')!r}")
    frames: list[Frame] = []
    seen: set[str] = set()
    for row in document.get("frames") or []:
        frame_id = row.get("frame_id")
        if not frame_id or frame_id in seen:
            raise ValueError(f"a frame needs a unique frame_id: {frame_id!r}")
        seen.add(frame_id)
        source = row.get("source") or {}
        frames.append(Frame(
            frame_id=frame_id,
            clip_id=str(row.get("clip_id") or source.get("clip") or ""),
            time_s=float(row.get("time_s")
                         if row.get("time_s") is not None
                         else source.get("timestamp_s") or 0.0),
            image=str(row.get("image") or ""),
            width=int(row.get("width") or source.get("width") or 0),
            height=int(row.get("height") or source.get("height") or 0),
            scope=str(row.get("scope") or document.get("scope") or ""),
            angle_group=str(row.get("angle_group") or ""),
            source_path=str(source.get("path") or ""),
        ))
    if not frames:
        raise ValueError("the manifest holds no frames")
    return frames, document


def materialise_frames(frames: Sequence[Frame], *,
                        workdir: str | Path) -> list[Frame]:
    """Make sure every frame is on disk, cutting the ones that are not.

    A frame's pixels are derived from the source media and never written
    back to it: the stills live in scratch, and the manifest carries
    the clip and the second each was cut at so the cut can be repeated.
    """
    from library.tools.analysis import object_grounding

    out = Path(workdir)
    out.mkdir(parents=True, exist_ok=True)
    ready: list[Frame] = []
    for frame in frames:
        if frame.image and Path(frame.image).is_file():
            ready.append(frame)
            continue
        if not frame.source_path:
            raise ValueError("a frame without a still has no source to cut "
                             "it from")
        geometry = object_grounding.probe_source(frame.source_path)
        image = out / f"{frame.clip_id}-t{round(frame.time_s):06d}.jpg"
        command = [
            "ffmpeg", "-nostdin", "-v", "error", "-y",
            "-ss", f"{frame.time_s:.3f}", "-i", frame.source_path,
            "-frames:v", "1", "-q:v", "2", str(image),
        ]
        import subprocess

        result = subprocess.run(command, capture_output=True,
                                encoding="utf-8", check=False)
        if result.returncode != 0 or not image.is_file():
            raise ValueError(f"ffmpeg could not cut {frame.source_path} at "
                             f"{frame.time_s}s: "
                             f"{result.stderr.strip() or 'unknown error'}")
        ready.append(Frame(
            frame_id=frame.frame_id, clip_id=frame.clip_id,
            time_s=frame.time_s, image=str(image),
            width=geometry["width"], height=geometry["height"],
            scope=frame.scope, angle_group=frame.angle_group,
            source_path=frame.source_path))
    ready.sort(key=lambda frame: (frame.clip_id, frame.time_s,
                                   frame.frame_id))
    return ready


# ── the detection pass ────────────────────────────────────────────────────

def detect_frames(frames: Sequence[Frame], phrases: Sequence[str], *,
                  detector: str) -> tuple[list[ObservedDetection], list[str]]:
    """Ask the detector about the named objects on every frame.

    A detector that cannot run here refuses with the reason: "cannot
    run" and "ran and found nothing" are different answers, and the
    second is never reported as the first.
    """
    from library.tools.analysis import open_vocabulary_detect as ovd

    detections: list[ObservedDetection] = []
    failures: list[str] = []
    for frame in frames:
        try:
            found = ovd.detect_image(detector, frame.image, list(phrases))
        except ovd.DetectorNotAvailable as exc:
            failures.append(f"{frame.frame_id}: {exc}")
            continue
        except (FileNotFoundError, OSError) as exc:
            failures.append(f"{frame.frame_id}: {exc}")
            continue
        for detection in found:
            detections.append(ObservedDetection(
                clip_id=frame.clip_id,
                frame_id=frame.frame_id,
                time_s=frame.time_s,
                phrase=detection.phrase or detection.detector,
                box=tuple(float(v) for v in detection.box),
                score=float(detection.score),
                image=frame.image,
            ))
    return detections, failures


# ── the prediction envelope ───────────────────────────────────────────────

def prediction_envelope(*, producer_name: str, revision: str,
                        threshold: dict[str, Any], sampling_schedule: str,
                        observations: Sequence[dict[str, Any]],
                        cost: dict[str, Any]) -> dict[str, Any]:
    """The envelope the continuity scorer reads."""
    return {
        "schema": PREDICTION_SCHEMA,
        "producer": {
            "name": producer_name,
            "revision": revision,
            "threshold": threshold,
            "sampling_schedule": sampling_schedule,
        },
        "observations": list(observations),
        "cost": cost,
    }


def measure_cost(started: float, frames: Sequence[Frame],
                 failures: Sequence[str]) -> dict[str, Any]:
    """The cost record the envelope carries."""
    import resource
    import sys

    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    # Darwin reports bytes, Linux reports kilobytes.
    peak_rss_mb = (peak / (1024 * 1024) if sys.platform == "darwin"
                   else peak / 1024)
    return {
        "scope": f"{len(frames)} frames over "
                 f"{len({frame.clip_id for frame in frames})} clips",
        "wall_seconds": round(time.perf_counter() - started, 3),
        "peak_rss_mb": round(float(peak_rss_mb), 2),
        "frames_requested": len(frames),
        "frames_processed": len(frames) - len(failures),
        "failures": list(failures),
    }


# ── the command ───────────────────────────────────────────────────────────

def run_predict(manifest_path: str | Path, *, detector: str = "owlv2",
                phrases: Sequence[str] = ("laptop", "person", "plant"),
                mode: str = "tracked", workdir: str | Path | None = None,
                **options: Any) -> dict[str, Any]:
    """Measure the frames, resolve their identities, write the envelope."""
    frames, document = load_manifest(manifest_path)
    if workdir is None:
        workdir = Path.home() / ".cache" / "ren-vi007b" / "frames"
    frames = materialise_frames(frames, workdir=workdir)
    started = time.perf_counter()
    detections, failures = detect_frames(frames, phrases, detector=detector)
    if mode == "per_frame":
        observations = per_frame_observations(frames, detections)
        report = {"mode": "per_frame", "detections": len(detections)}
    else:
        resolution = resolve(frames, detections, **options)
        observations = resolution.observations
        report = resolution.report
    envelope = prediction_envelope(
        producer_name=("object-identity-lane" if mode == "tracked"
                       else "per-frame-detector"),
        revision=f"{PREDICTION_SCHEMA}/1",
        threshold={
            "iou": options.get("iou_threshold", DEFAULT_IOU_THRESHOLD),
            "link": options.get("link_threshold", DEFAULT_LINK_THRESHOLD),
            "link_margin": options.get("link_margin", DEFAULT_LINK_MARGIN),
            "ambiguity_margin": options.get("ambiguity_margin",
                                            DEFAULT_AMBIGUITY_MARGIN),
        },
        sampling_schedule=document.get("sampling_schedule") or
        f"{len(frames)} sampled stills, one independent pass each",
        observations=observations,
        cost=measure_cost(started, frames, failures),
    )
    envelope["resolution"] = report
    return envelope


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["predict"])
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--detector", default="owlv2")
    parser.add_argument("--phrases", default="laptop,person,plant")
    parser.add_argument("--mode", default="tracked",
                        choices=["tracked", "per_frame"])
    parser.add_argument("--iou-threshold", type=float,
                        default=DEFAULT_IOU_THRESHOLD)
    parser.add_argument("--link-threshold", type=float,
                        default=DEFAULT_LINK_THRESHOLD)
    parser.add_argument("--link-margin", type=float,
                        default=DEFAULT_LINK_MARGIN)
    parser.add_argument("--ambiguity-margin", type=float,
                        default=DEFAULT_AMBIGUITY_MARGIN)
    args = parser.parse_args(list(argv) if argv is not None else None)
    envelope = run_predict(
        args.manifest, detector=args.detector,
        phrases=[phrase.strip() for phrase in args.phrases.split(",")
                 if phrase.strip()],
        mode=args.mode,
        iou_threshold=args.iou_threshold,
        link_threshold=args.link_threshold,
        link_margin=args.link_margin,
        ambiguity_margin=args.ambiguity_margin,
    )
    Path(args.out).write_text(
        json.dumps(envelope, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8")
    print(json.dumps(envelope["resolution"], indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
