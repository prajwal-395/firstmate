"""Resolve a reel's declared camera choices onto stable word anchors.

An angle plan is part of the edit ledger, but it has to run before the
timeline is placed: the Resolve API can switch an item that is already a
multicam clip, while reel builds use ordinary stacked picture rows. This
module turns the ledger rows into frame intervals and selects only the
declared camera's picture placements. Speech placements stay untouched.
"""

from __future__ import annotations

import math
from itertools import pairwise

from library.tools import reel_clock as _reel_clock


class AnglePlanError(ValueError):
    """A declared camera plan cannot be honoured by this reel."""


def _camera_key(camera: str, angles: list, row_name: str) -> str:
    wanted = camera.strip().casefold()
    matches = [angle for angle in angles
               if wanted in (str(angle["key"]).casefold(),
                             str(angle["label"]).strip().casefold())]
    if len(matches) != 1:
        choices = ", ".join(
            f"{angle['label']} (camera {angle['key']})"
            for angle in angles) or "no camera rows"
        if not matches:
            raise AnglePlanError(
                f"{row_name}: camera {camera!r} matches no master camera. "
                f"Available: {choices}.")
        raise AnglePlanError(
            f"{row_name}: camera {camera!r} is ambiguous. "
            f"Name a unique camera key or row label.")
    return str(matches[0]["key"])


def resolve(rows: list, angles: list, ranges: list, transcript: dict,
            fps: float, reel_name: str, lead_in_frames: int = 0) -> dict:
    """Resolve the reel's angle_plan rows to ordered picture intervals.

    A reel-anchored row names the opening camera. Every later row is
    anchored to words; its lead moves the picture cut earlier by the stated
    number of frames, while the speech remains at its planned position.
    `min_shot_seconds` is checked against each resulting interval. An
    ambiguous anchor, unknown camera, missing opening choice, or shot below
    its declared minimum refuses the build instead of quietly falling back
    to the master's stacked picture rows.
    """
    from library.tools import captain_edits
    from library.tools import edit_ledger

    plan_rows = [row for row in edit_ledger.rows_for_reel(rows, reel_name)
                 if row.get("op") == "angle_plan"]
    if not plan_rows:
        return {"declared": False, "shots": [], "camera_keys": []}
    try:
        edit_ledger.validate_rows(plan_rows)
    except edit_ledger.EditLedgerError as exc:
        raise AnglePlanError(f"{reel_name}: {exc}") from exc
    if fps <= 0 or not math.isfinite(fps):
        raise AnglePlanError(f"{reel_name}: frame rate {fps!r} is invalid.")

    total_frames = int(lead_in_frames)
    total_frames += _reel_clock.total_played_frames(ranges, fps)
    if total_frames <= lead_in_frames:
        raise AnglePlanError(
            f"{reel_name}: angle plan has no placeable reel picture.")

    decisions = []
    for row in plan_rows:
        anchor = row["anchor"]
        params = row["params"]
        name = edit_ledger._row_name(row)
        camera = params["camera"]
        camera_key = _camera_key(camera, angles, name)
        minimum = float(params["min_shot_seconds"])
        min_frames = int(math.ceil(minimum * fps - 1e-9))
        lead = int(params.get("lead_frames", 0))
        if anchor["kind"] == "reel":
            if lead:
                raise AnglePlanError(
                    f"{name}: the opening camera cannot lead before reel "
                    f"frame {lead}; set lead_frames to 0 on the first shot.")
            start_frame = int(lead_in_frames)
        else:
            try:
                reel_start, _, _ = captain_edits.anchor_reel_time(
                    ranges, transcript, anchor["phrase"],
                    lead_seconds=lead_in_frames / fps)
            except captain_edits.CaptainEditError as exc:
                raise AnglePlanError(f"{name}: {exc}") from exc
            start_frame = int(round(reel_start * fps)) - lead
            if start_frame < lead_in_frames:
                raise AnglePlanError(
                    f"{name}: a {lead}-frame lead reaches before the first "
                    f"picture frame {lead_in_frames}.")
        decisions.append({
            "start_frame": start_frame,
            "camera_key": camera_key,
            "camera": camera,
            "anchor": dict(anchor),
            "min_shot_frames": min_frames,
            "min_shot_seconds": minimum,
            "lead_frames": lead,
            "name": name,
        })

    decisions.sort(key=lambda item: item["start_frame"])
    if decisions[0]["start_frame"] != lead_in_frames:
        first = decisions[0]
        raise AnglePlanError(
            f"{first['name']}: the first angle-plan choice starts at frame "
            f"{first['start_frame']}, but the reel opens at frame "
            f"{lead_in_frames}. Add a reel-anchored opening camera.")
    for before, after in zip(decisions, decisions[1:]):
        if after["start_frame"] == before["start_frame"]:
            raise AnglePlanError(
                f"{after['name']}: two camera choices start at frame "
                f"{after['start_frame']}; the plan has no single picture "
                f"to show there.")
        duration = after["start_frame"] - before["start_frame"]
        if duration < before["min_shot_frames"]:
            raise AnglePlanError(
                f"{before['name']}: camera {before['camera']!r} would play "
                f"for {duration / fps:.3f}s before the next switch, below "
                f"its {before['min_shot_seconds']:.3f}s minimum. The "
                f"declared lead and minimum cannot both be honoured.")

    shots = []
    for index, decision in enumerate(decisions):
        end_frame = (decisions[index + 1]["start_frame"]
                     if index + 1 < len(decisions) else total_frames)
        duration = end_frame - decision["start_frame"]
        if duration <= 0 or duration < decision["min_shot_frames"]:
            raise AnglePlanError(
                f"{decision['name']}: the final camera shot lasts "
                f"{max(0, duration) / fps:.3f}s, below its "
                f"{decision['min_shot_seconds']:.3f}s minimum.")
        shots.append({**decision, "end_frame": end_frame})

    return {"declared": True, "shots": shots,
            "camera_keys": list(dict.fromkeys(
                shot["camera_key"] for shot in shots)),
            "total_frames": total_frames}


def _split(place: dict, start: int, end: int, fps: float) -> dict:
    """`place` cut down to reel frames [start, end), same camera."""
    from library.tools.reel_build import _placement_span_frames

    place_start, place_end = _placement_span_frames(place, fps)
    offset_start = (start - place_start) / fps
    offset_end = (place_end - end) / fps
    split = dict(place)
    split["snapped_record"] = start
    split["record"] = start / fps
    split["source_in"] = float(place["source_in"]) + offset_start
    split["source_out"] = float(place["source_out"]) - offset_end
    master_start, master_end = place["master"]
    split["master"] = (master_start + offset_start,
                       master_end - offset_end)
    return split


def _synced(place: dict, start: int, end: int, fps: float, shot: dict,
            donors: list, sync: dict) -> dict:
    """The declared camera's picture for frames [start, end), recorded
    with the other camera's `place` over the same frames.

    The master never placed this camera here (on a podcast master a
    camera is placed while its person speaks), so its frames come from
    the camera's own file through the offset the master's cuts measured
    (`camera_sync`). The split keeps the other placement's `master` range:
    the words heard are the same, only the picture changes. Refuses by
    name where no measured offset reaches any of the camera's files, or
    where it lands outside the file - never a guessed second.
    """
    import os

    other = place["clip"].source_file
    source_frame = (round(float(place["source_in"]) * fps)
                    + start - int(place["snapped_record"]))
    length = end - start
    tried = []
    for donor in donors:
        offset = sync.get("offsets", {}).get((other, donor.source_file))
        if offset is None:
            tried.append(f"{os.path.basename(donor.source_file)} "
                         f"(no agreed offset)")
            continue
        target = source_frame + offset
        limit = getattr(donor, "source_frames", None)
        if target < 0 or (limit is not None and target + length > limit):
            tried.append(f"{os.path.basename(donor.source_file)} "
                         f"(frame {target} is outside the file)")
            continue
        split = _split(place, start, end, fps)
        split["clip"] = donor
        split["source_in"] = target / fps
        split["source_out"] = (target + length) / fps
        split["track_index"] = donor.track_index
        split["speaker"] = getattr(donor, "speaker", None)
        split["synced_from"] = os.path.basename(other)
        return split
    raise AnglePlanError(
        f"{shot['name']}: camera {shot['camera']!r} has no placed picture "
        f"at reel frames {start}-{end}, and its footage cannot be reached "
        f"from {os.path.basename(other)}: "
        f"{'; '.join(dict.fromkeys(tried)) or 'the master places no clip of this camera'}. "
        f"The build would show picture from the wrong second, so it "
        f"refuses.")


def _source_limit(place: dict):
    """How many frames the placement's file holds, where the clip says."""
    return getattr(place["clip"], "source_frames", None)


def _floor_frames(fps: float) -> int:
    """The F7 readability floor in frames - no piece may be shorter."""
    from library.tools.manifest_validator import MIN_CAPTION_DISPLAY_SECONDS

    return math.ceil(MIN_CAPTION_DISPLAY_SECONDS * fps - 1e-9)


def _grow(place: dict, frames: int, earlier: bool, fps: float):
    """`place` played `frames` longer at its head (earlier) or tail, or
    None where its file does not hold those frames."""
    grown = dict(place)
    first = round(float(place["source_in"]) * fps)
    last = round(float(place["source_out"]) * fps)
    master_start, master_end = place["master"]
    if earlier:
        if first - frames < 0:
            return None
        grown["source_in"] = (first - frames) / fps
        grown["snapped_record"] = int(place["snapped_record"]) - frames
        grown["record"] = grown["snapped_record"] / fps
        grown["master"] = (master_start - frames / fps, master_end)
    else:
        limit = getattr(place["clip"], "source_frames", None)
        if limit is not None and last + frames > limit:
            return None
        grown["source_out"] = (last + frames) / fps
        grown["master"] = (master_start, master_end + frames / fps)
    return grown


def _as_handles(pieces: list, slivers: list, fps: float,
                shot: dict) -> list:
    """Play each sliver of a shot as a handle of the piece beside it.

    A planned cut is a word's reel time; a placement edge is a range
    edge. Where the two round a frame or two apart, the shot is left a
    sliver the F7 floor refuses (1 frame of the camera before its own
    clip, measured on scratch Reel 04, 2026-09-25). It is the SAME
    camera in the same shot, so a neighbouring piece plays it: its
    source extends by the sliver's frames, the camera's own placement
    first. `slivers` are uncovered (start, end) spans; a piece shorter
    than the floor is folded the same way. Where no neighbour's file
    holds the frames, the plan refuses by name.
    """
    from library.tools.reel_build import _placement_span_frames

    floor = _floor_frames(fps)
    pieces = sorted(pieces, key=lambda place: place["snapped_record"])
    todo = [tuple(span) for span in slivers]
    while True:
        spans = [_placement_span_frames(place, fps) for place in pieces]
        if not todo:
            short = [spans[i] for i in range(len(pieces))
                     if spans[i][1] - spans[i][0] < floor]
            if not short or len(pieces) == 1:
                return pieces
            index = spans.index(short[0])
            start, end = spans[index]
            del pieces[index]
            del spans[index]
        else:
            start, end = todo.pop(0)
        length = end - start
        candidates = []
        for index, (piece_start, piece_end) in enumerate(spans):
            if piece_end == start:
                candidates.append((index, False))
            elif piece_start == end:
                candidates.append((index, True))
        candidates.sort(key=lambda item: "synced_from" in pieces[item[0]])
        for index, earlier in candidates:
            grown = _grow(pieces[index], length, earlier, fps)
            if grown is not None:
                pieces[index] = grown
                break
        else:
            raise AnglePlanError(
                f"{shot['name']}: camera {shot['camera']!r} would play a "
                f"{length}-frame piece at reel frames {start}-{end}, under "
                f"the readability floor, and no neighbouring piece of "
                f"that shot has the frames to hold it. Move the anchor so "
                f"the cut lands on picture.")


def select_picture_placements(placements: list, plan: dict, fps: float,
                              master_clips=(), sync=None
                              ) -> tuple[list, dict]:
    """Split picture items on planned cuts and keep the selected camera.

    Each split retains the source and master ranges the original placement
    carried. Audio placements are returned unchanged. Where the declared
    camera has no placement of its own - the listener, a lead before the
    next speaker, a shot held past a speaker change - its picture comes
    from its own file, recorded with whichever camera IS placed over those
    frames (`_synced`, offset from `camera_sync.measure`). Coverage is
    checked per declared camera interval, so a plan cannot silently create
    a black hole where no camera has footage.
    """
    if not plan.get("declared"):
        return list(placements), {"declared": False, "shots": []}

    from library.tools.reel_build import _angle_key, _placement_span_frames

    sync = sync or {}
    pictures, audio = [], []
    for place in placements:
        if getattr(place["clip"], "track_type", "video") != "video":
            audio.append(place)
            continue
        pictures.append(place)

    selected = []
    for shot in plan["shots"]:
        shot_start = len(selected)
        own = []
        for place in pictures:
            if str(getattr(place["clip"], "track_index", "")) \
                    != shot["camera_key"]:
                continue
            place_start, place_end = _placement_span_frames(place, fps)
            start = max(place_start, shot["start_frame"])
            end = min(place_end, shot["end_frame"])
            if end > start:
                own.append((start, end, place))
        own.sort(key=lambda item: item[:2])
        for (_, first_end, _), (second_start, _, _) in pairwise(own):
            if second_start < first_end:
                raise AnglePlanError(
                    f"{shot['name']}: camera {shot['camera']!r} has two "
                    f"picture placements overlapping at frame "
                    f"{second_start}; Resolve would trim a clip on that "
                    f"picture row, so the angle plan cannot claim "
                    f"continuous coverage.")

        gaps, cursor = [], shot["start_frame"]
        for start, end, place in own:
            if start > cursor:
                gaps.append((cursor, start))
            selected.append(_split(place, start, end, fps))
            cursor = max(cursor, end)
        if cursor < shot["end_frame"]:
            gaps.append((cursor, shot["end_frame"]))

        donors = [clip for clip in master_clips or ()
                  if getattr(clip, "track_type", "video") == "video"
                  and _angle_key(clip) == shot["camera_key"]]
        donors = list({clip.source_file: clip for clip in donors}.values())
        slivers = []
        for gap_start, gap_end in gaps:
            if (gap_end - gap_start < _floor_frames(fps)
                    and selected[shot_start:]):
                slivers.append((gap_start, gap_end))
                continue
            covering = sorted(
                (max(_placement_span_frames(place, fps)[0], gap_start),
                 min(_placement_span_frames(place, fps)[1], gap_end),
                 index)
                for index, place in enumerate(pictures)
                if str(getattr(place["clip"], "track_index", ""))
                != shot["camera_key"])
            covering = [item for item in covering if item[1] > item[0]]
            cursor = gap_start
            for start, end, index in covering:
                if start > cursor:
                    break
                if end <= cursor:
                    continue
                selected.append(_synced(pictures[index], cursor, end, fps,
                                        shot, donors, sync))
                cursor = end
            if cursor < gap_end:
                raise AnglePlanError(
                    f"{shot['name']}: camera {shot['camera']!r} covers "
                    f"frames through {cursor}, but its declared shot ends "
                    f"at {shot['end_frame']}; no camera is placed there, so "
                    f"the build would show black instead of the planned "
                    f"camera.")
        selected[shot_start:] = _as_handles(selected[shot_start:],
                                            slivers, fps, shot)

    selected.sort(key=lambda place: place["snapped_record"])
    return audio + selected, {
        "declared": True,
        "shots": [{key: shot[key] for key in (
            "start_frame", "end_frame", "camera_key", "camera",
            "anchor", "min_shot_seconds", "lead_frames", "name")}
            for shot in plan["shots"]],
        "picture_placements": len(selected),
        "synced_placements": sum("synced_from" in place
                                 for place in selected),
        "sync_pairs": list((sync or {}).get("pairs", [])),
    }


def planned_picture(placements: list, rows: list, master_clips, ranges,
                    transcript: dict, fps: float, reel_name: str,
                    lead_in_frames: int = 0) -> tuple[list, dict]:
    """`placements` as the declared angle plan will place them.

    The ONE composition of `resolve` and `select_picture_placements` for
    a caller that plans against the reel's picture before it is built -
    the motion ask (`reel_build.write_visual_asks`) above all: a motion
    plan asked over the master's per-speaker shots describes shots the
    plan will not place, and the build's Fusion pass then refuses the
    comps (measured 2026-09-25, Reel 02 of a scratch geo-podcast). With
    no angle-plan row for this reel, `placements` come back unchanged.
    """
    from library.tools import camera_sync
    from library.tools.reel_build import reel_angles

    plan = resolve(rows, reel_angles(master_clips), list(ranges),
                   transcript, fps, reel_name,
                   lead_in_frames=lead_in_frames)
    return select_picture_placements(
        placements, plan, fps, master_clips=master_clips,
        sync=(camera_sync.measure(master_clips, fps)
              if plan["declared"] else None))
