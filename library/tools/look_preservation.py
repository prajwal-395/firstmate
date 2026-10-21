"""QA-005: does an approved shot's LOOK survive a revision?

The incident this closes
------------------------
Reel 26, 2026-10-20.  The closing shot's Fusion comp was authored on
the SOURCE canvas (3840x2160) while every other comp in the reel was
authored on the DELIVERY canvas (1080x1920), so its vignette and glow
landed on a different picture and the shot read flatter and cooler than
the shot beside it.  PR 3009 re-applied the declared look, the shot
moved, and PR 3079 had to restore the version the captain preferred.

Nothing in the engine could have seen it, and the reason is a
MEASUREMENT gap, not a rule gap:

* the recorded state of a shot carried its transform, its keyframes,
  its clip CDL and its comp NAMES - but not the CANVAS its comp was
  authored on, nor the comp's tools.  Two comps with the same names
  read EQUAL when one was drawn on the delivery canvas and the other on
  the source canvas, which is the whole defect;
* nothing compared the DELIVERED pixels of a shot before a revision
  with the same shot after it, so a framing change no recorded state
  explained went unseen;
* a shot's look was not compared under the shot's EDITORIAL identity
  (the material and the role), so a re-placed shot - which Resolve
  gives a new id - started with an empty look and nothing compared it
  with the shot it replaced.

What this module is
-------------------
ONE check, in three parts, over the snapshots the engine already takes:

1. THE STATE - `capture_look` reads each shot's look out of a recorded
   snapshot: the transform (Resolve's Edit representation), the grade
   (its Color representation), the Fusion comps and their canvas (its
   Fusion representation), and the keyframes.  `compare_look` compares
   two of them under the shot's editorial identity and names every
   difference, in the engine's own conflict domains, with whether the
   authorized scope permits it.
2. THE CANVAS - `canvas_report` names the shots whose comp was
   authored on a canvas that is not the delivery canvas, and the shots
   whose canvas differs from their neighbours': the signature of the
   reel that lost its look.
3. THE PIXELS - `compare_frames` compares the DELIVERED frames of two
   renders, shot by shot, and says which shots moved.  The frames come
   from the delivered file, because a source frame and a delivered
   frame are not the same picture once the canvases differ.

The verdict is `preserved`, `violated` or `unprovable` - the engine's
own vocabulary (`library/tools/preservation_invariant.py`) - and never
a partial pass.  A difference nobody authorized is a violation; a
difference nobody can judge is unprovable, which is not a pass.

READ-ONLY.  Nothing here writes to Resolve or to a timeline.
"""

from __future__ import annotations

import os
from collections.abc import Mapping, Sequence
from typing import Any

from library.tools.edit_state_preservation import PICTURE_PROPERTIES
from library.tools.preservation_invariant import AUDIO_KEYS

#: The parts of a shot's look, in the order they are reported.
LOOK_PARTS = ("transform", "grade", "fusion", "keyframes", "sound")

#: The conflict domain each part belongs to.  The vocabulary is the
#: engine's own (`edit_patch.CONFLICT_DOMAINS`), so a look finding and
#: a delta finding mean the same thing when they name the same domain.
PART_DOMAINS = {
    "transform": "picture_transform",
    "keyframes": "picture_transform",
    "grade": "color",
    "fusion": "fusion",
    "sound": "audio_mix",
}

#: Two numbers this close are the same number.  The transform write
#: rule (`transform_write_log.VALUE_TOLERANCE`), because a look that
#: moved by a float's representation noise has not moved.
VALUE_TOLERANCE = 1e-6

PRESERVED = "preserved"
VIOLATED = "violated"
UNPROVABLE = "unprovable"

#: The delivered-frame difference above which a shot is said to have
#: MOVED.  Two renders of the same timeline are not bit-identical - the
#: encoder is not deterministic to the bit - so the measure is a
#: tolerance, not an equality, and it is stated rather than implied.
FRAME_TOLERANCE = 0.02

#: The per-pixel difference above which two pixels count as different,
#: on a 0..255 scale.  Encoder noise lives below it; a moved picture
#: does not.
PIXEL_THRESHOLD = 16


class LookPreservationError(ValueError):
    """A look comparison could not be made honestly."""


# ── The state half ──────────────────────────────────────────────────────

def _row(clip: Mapping) -> str:
    """The row a clip sits on, as `V1`."""
    track_type = str(clip.get("track_type") or "video")
    prefix = {"video": "V", "audio": "A"}.get(track_type, track_type[:1].upper())
    try:
        index = int(clip.get("track_index") or 0)
    except (TypeError, ValueError):
        index = 0
    return f"{prefix}{index}"


def _identity(clip: Mapping) -> dict | None:
    """The clip's EDITORIAL identity, or None where it names no moment.

    The identity is the material and the role, so it survives a re-place
    and a rebuild - which is exactly what a look comparison needs: the
    shot that was replaced and the shot that replaced it are the same
    editorial moment, and their looks are comparable.
    """
    try:
        from library.tools import editorial_identity

        identity = editorial_identity.identity_of(dict(clip))
    except Exception:  # noqa: BLE001 - an unreadable clip names no moment
        return None
    if identity is None:
        return None
    return identity.as_dict()


def _transform(clip: Mapping) -> dict:
    """The shot's picture transform, the keys the engine compares."""
    stored = clip.get("transform") or {}
    if not isinstance(stored, Mapping):
        return {}
    return {key: stored[key] for key in PICTURE_PROPERTIES
            if key in stored}


def _grade(clip: Mapping) -> dict:
    """The shot's grade, in the Color page's own vocabulary.

    The keys are the ones the Color page answers with, and the ones a
    VERSION RECORD carries: a version that keeps the CDL and drops the
    nodes and the LUTs cannot say whether the grade moved, so the
    version record keeps them and this reads them (`VC-003`).
    """
    color = clip.get("color") or {}
    if not isinstance(color, Mapping):
        return {}
    grade = {}
    for key in ("cdl", "node_count", "luts", "nodes", "color_group",
                "color_group_name"):
        if key in color:
            grade[key] = color[key]
    return grade


def _fusion(clip: Mapping) -> dict:
    """The shot's Fusion comps, and the canvas each was authored on.

    A comp whose canvas or whose tools could not be READ carries
    neither: an unread field is absent evidence, not an empty one, and
    a comparison that treated it as one would report a change that was
    never observed (a comp that has just been imported reads its tool
    list lazily).
    """
    fusion = clip.get("fusion") or {}
    if not isinstance(fusion, Mapping):
        return {}
    kept = {}
    for key in ("comp_count", "comp_names"):
        if key in fusion:
            kept[key] = fusion[key]
    comps = []
    for comp in fusion.get("comps") or ():
        if not isinstance(comp, Mapping):
            continue
        entry = {"name": str(comp.get("name") or "")}
        canvas = comp.get("canvas")
        if isinstance(canvas, Mapping):
            entry["canvas"] = dict(canvas)
        tools = comp.get("tools")
        if tools is not None:
            entry["tools"] = list(tools)
        comps.append(entry)
    if "comps" in fusion:
        # The KEY is kept when the recording carried it, and left out
        # when it did not: a recording that never carried the comps is
        # absent evidence, while a recording that carried an empty list
        # is a shot with no comp - and the two must not read alike.
        kept["comps"] = comps
    return kept


def _keyframes(clip: Mapping) -> dict:
    """The shot's keyframes, as recorded."""
    keyframes = clip.get("keyframes")
    return dict(keyframes) if isinstance(keyframes, Mapping) else {}


def _sound(clip: Mapping) -> dict:
    """The shot's SOUND: its level, its fades and its linked audio.

    The sound of a shot is not its picture: an AUDIO row's own
    transform keys are its mix, and a PICTURE row's sound is the audio
    it is linked to - the partner's own level, fades and enablement,
    because that is the state a rebuild would lose silently.

    A field the recording does not carry is left out: an absent field
    is absent evidence, and a comparison that treated it as an empty
    one would report a change nobody observed.
    """
    sound: dict = {}
    transform = clip.get("transform")
    if isinstance(transform, Mapping):
        mix = {key: transform[key] for key in AUDIO_KEYS if key in transform}
        if mix:
            sound["mix"] = mix
    if "volume" in clip:
        sound["volume"] = clip["volume"]
    fades = clip.get("fades")
    if isinstance(fades, Mapping):
        sound["fades"] = dict(fades)
    linked = clip.get("linked_clips")
    if isinstance(linked, (list, tuple)):
        partners = []
        for partner in linked:
            if not isinstance(partner, Mapping):
                continue
            partners.append({
                "enabled": partner.get("enabled"),
                "fades": partner.get("fades"),
            })
        if partners:
            sound["linked"] = partners
    return sound


def shot_look(clip: Mapping) -> dict:
    """The look of ONE shot, from a recorded clip."""
    identity = _identity(clip)
    return {
        "shot_id": (identity or {}).get("token")
        or str(clip.get("unique_id") or ""),
        "unique_id": str(clip.get("unique_id") or ""),
        "identity": identity,
        "name": str(clip.get("name") or ""),
        "row": _row(clip),
        "record_in": clip.get("record_in"),
        "record_out": clip.get("record_out"),
        "transform": _transform(clip),
        "grade": _grade(clip),
        "fusion": _fusion(clip),
        "keyframes": _keyframes(clip),
        "sound": _sound(clip),
    }


def capture_look(snapshot: Mapping) -> dict:
    """Every PICTURE shot's look in a recorded snapshot.

    A snapshot is what `reel_read.read_reel` returns and what the
    timeline shadow stores: the timeline, its rows, and every clip on
    them.  It is ALSO what a VERSION RECORD holds - the committed
    `.timeline.json` the version comparison diffs - and that record
    keeps the same clip fields, so one comparison reads both.  The
    delivery canvas is read off the snapshot itself (or, for a version
    record, off its own `metadata.resolution`), so the canvas report
    compares a comp's canvas with the canvas the reel actually
    delivers.
    """
    if not isinstance(snapshot, Mapping):
        raise LookPreservationError(
            "a look can only be captured from a recorded snapshot")
    shots = []
    for track in snapshot.get("tracks") or ():
        if str(track.get("type") or "").lower() != "video":
            continue
        for clip in track.get("clips") or ():
            if not isinstance(clip, Mapping):
                continue
            shots.append(shot_look(clip))
    return {
        "timeline": str(snapshot.get("timeline") or "") or _name_of(snapshot),
        "delivery": _delivery_of(snapshot),
        "shots": shots,
    }


def _name_of(snapshot: Mapping) -> str:
    """The state's own name, in the shape it keeps it.

    A live snapshot names the timeline at the top level; a VERSION
    RECORD names it under `metadata`.  One reading serves both.
    """
    metadata = snapshot.get("metadata")
    if isinstance(metadata, Mapping):
        return str(metadata.get("name") or "")
    return ""


def _delivery_of(snapshot: Mapping) -> dict:
    """The canvas this state delivers, in the state's own words.

    A live snapshot carries `width`/`height`; a VERSION RECORD carries
    its resolution under `metadata`.  A state that names neither
    delivers an UNKNOWN canvas, and an unknown canvas is not a canvas:
    the canvas report then says nothing rather than guessing one.
    """
    width = snapshot.get("width")
    height = snapshot.get("height")
    if width is None or height is None:
        metadata = snapshot.get("metadata")
        resolution = (metadata or {}).get("resolution") if isinstance(
            metadata, Mapping) else None
        if isinstance(resolution, (list, tuple)) and len(resolution) == 2:
            width, height = resolution[0], resolution[1]
    return {"width": width, "height": height}


# ── Comparing two looks ────────────────────────────────────────────────

def _numbers_agree(wanted: Any, actual: Any) -> bool:
    if (isinstance(wanted, (int, float)) and not isinstance(wanted, bool)
            and isinstance(actual, (int, float))
            and not isinstance(actual, bool)):
        return abs(float(actual) - float(wanted)) <= VALUE_TOLERANCE
    return wanted == actual


def _differences(before: Any, after: Any, path: str = "") -> list:
    """Every leaf that differs between two values, as (path, before, after).

    A key ONE side has and the other does not is NOT a difference: it
    is absent evidence, and a field only one recording carries says
    nothing about whether the look moved.  (The same rule the version
    ledger reads two receipts under.)
    """
    if isinstance(before, Mapping) and isinstance(after, Mapping):
        rows = []
        for key in sorted(set(before) & set(after)):
            name = f"{path}.{key}" if path else str(key)
            rows.extend(_differences(before[key], after[key], name))
        return rows
    if isinstance(before, (list, tuple)) and isinstance(after, (list, tuple)):
        if len(before) != len(after):
            return [(path or "value", list(before), list(after))]
        rows = []
        for index, (old, new) in enumerate(zip(before, after)):
            name = f"{path}[{index}]" if path else f"[{index}]"
            rows.extend(_differences(old, new, name))
        return rows
    if _numbers_agree(before, after):
        return []
    return [(path or "value", before, after)]


def _shot_key(shot: Mapping) -> str:
    """The key two shots are matched under.

    The EDITORIAL identity when the shot has one - so a re-placed shot
    matches the shot it replaced - and the Resolve id otherwise.
    """
    identity = shot.get("identity") or {}
    token = identity.get("token")
    if token:
        return str(token)
    return str(shot.get("unique_id") or shot.get("shot_id") or "")


def _match_shots(before: Sequence[Mapping],
                 after: Sequence[Mapping]) -> tuple[list, list, list]:
    """`(pairs, only_before, only_after)` - shots matched by identity.

    A material played twice in one reel gives two shots the same
    identity token, so the shots are matched IN ORDER within a token:
    the first before-shot with a token matches the first after-shot
    with it.  A re-place keeps the order, so the moment that was
    replaced is the moment that is judged.
    """
    after_by_key: dict[str, list] = {}
    for shot in after:
        after_by_key.setdefault(_shot_key(shot), []).append(shot)
    pairs = []
    taken: set[int] = set()
    unmatched_before = []
    for shot in before:
        key = _shot_key(shot)
        bucket = after_by_key.get(key) or []
        partner = next((candidate for candidate in bucket
                        if id(candidate) not in taken), None)
        if partner is None:
            unmatched_before.append(shot)
            continue
        taken.add(id(partner))
        pairs.append((shot, partner))
    unmatched_after = [shot for shot in after if id(shot) not in taken]
    return pairs, unmatched_before, unmatched_after


def _authorized(scope, targets: Sequence[str], domain: str) -> bool | None:
    """Whether the authorized scope permits this domain on these targets.

    None where there is no scope to judge against: a difference nobody
    can judge is not a pass, and it is not a violation either.
    """
    if scope is None:
        return None
    allows = getattr(scope, "allows", None)
    if not callable(allows):
        return None
    return any(allows(target, domain) for target in targets)


def compare_look(before: Mapping, after: Mapping, *, scope=None,
                 approved: set | None = None) -> dict:
    """Every difference between two recorded looks, and what it means.

    `before` and `after` are the dicts `capture_look` returns.  `scope`
    is a `preservation_invariant.MutationScope`: the authorized delta
    the revision was allowed to make.  `approved` narrows the shots
    that are judged - a shot nobody approved is not flagged - and
    defaults to every shot the BEFORE state carried.

    The verdict is `preserved`, `violated` or `unprovable`.
    """
    before_shots = list(before.get("shots") or ())
    after_shots = list(after.get("shots") or ())
    pairs, only_before, only_after = _match_shots(before_shots, after_shots)
    findings: list[dict] = []
    compared_items = 0
    for old, new in pairs:
        old_id = str(old.get("shot_id") or "")
        if approved is not None and old_id not in approved:
            continue
        targets = [f"clip:{old.get('unique_id')}"] if old.get("unique_id") else []
        if new.get("unique_id"):
            target = f"clip:{new.get('unique_id')}"
            if target not in targets:
                targets.append(target)
        for part in LOOK_PARTS:
            domain = PART_DOMAINS[part]
            old_part = old.get(part)
            new_part = new.get(part)
            if old_part or new_part:
                # An accepted ITEM: one part of one shot that the
                # comparison reads.  The count is the DENOMINATOR the
                # report answers with - a comparison that found one
                # change out of forty accepted items is not the same
                # claim as one that found one out of two.
                compared_items += 1
            for field, was, now in _differences(old_part, new_part, part):
                findings.append({
                    "shot": old_id,
                    "row": old.get("row"),
                    "name": old.get("name"),
                    "part": part,
                    "field": field,
                    "domain": domain,
                    "before": was,
                    "after": now,
                    "authorized": _authorized(scope, targets, domain),
                })
    for shot in only_before:
        shot_id = str(shot.get("shot_id") or "")
        if approved is not None and shot_id not in approved:
            continue
        findings.append({
            "shot": shot_id,
            "row": shot.get("row"),
            "name": shot.get("name"),
            "part": "shot",
            "field": "shot_lost",
            "domain": "timeline_structure",
            "before": shot.get("shot_id"),
            "after": None,
            "authorized": _authorized(
                scope, [f"clip:{shot.get('unique_id')}"], "timeline_structure"),
        })
    judged = [row for row in findings if row.get("authorized") is not None]
    if any(row.get("authorized") is False for row in findings):
        status = VIOLATED
    elif findings and not judged:
        status = UNPROVABLE
    else:
        status = PRESERVED
    return {
        "status": status,
        "findings": findings,
        "shots_compared": len(pairs),
        "counts": {
            "shots": len(pairs),
            "accepted_items": compared_items,
            "changed": len(findings),
            "unauthorized": sum(1 for row in findings
                                if row.get("authorized") is False),
            "unjudged": sum(1 for row in findings
                            if row.get("authorized") is None),
        },
        "shots_only_before": [str(shot.get("shot_id") or "")
                              for shot in only_before],
        "shots_only_after": [str(shot.get("shot_id") or "")
                             for shot in only_after],
    }


# ── The canvas half ─────────────────────────────────────────────────────

def comp_canvases(shot: Mapping) -> list:
    """The canvases a shot's comps were authored on.

    A comp's canvas is the size of the solid image it draws - the
    Background node's Width and Height - which is how the engine's own
    comp builder states it (`library/tools/fusion/effects.py`).  A comp
    that draws no solid image has no canvas, and a comp whose canvas
    could not be read is NOT a comp without one: it is absent
    evidence, and it is left out.
    """
    canvases = []
    fusion = shot.get("fusion") or {}
    for comp in fusion.get("comps") or ():
        if not isinstance(comp, Mapping):
            continue
        canvas = comp.get("canvas")
        if not isinstance(canvas, Mapping):
            continue
        try:
            width = int(canvas.get("width"))
            height = int(canvas.get("height"))
        except (TypeError, ValueError):
            continue
        if width <= 0 or height <= 0:
            continue
        canvases.append({
            "comp": str(comp.get("name") or ""),
            "width": width,
            "height": height,
        })
    return canvases


def canvas_report(look: Mapping, *, delivery: Mapping | None = None) -> list:
    """The shots whose comp canvas does not match the reel's.

    Two reports, both of them findings rather than failures:

    - `canvas_differs_from_delivery` - the shot's comp was authored on
      a canvas that is not the canvas the reel delivers.  When the
      source and the delivery canvases differ, the comp's vignette and
      glow land on a different picture than they were drawn for.
    - `canvas_differs_from_neighbours` - the shot's comp was authored
      on a different canvas from the reel's other comps.  This is the
      Reel 26 signature: one shot in a reel of matching ones.
    """
    shots = list(look.get("shots") or ())
    canvas = delivery if delivery is not None else look.get("delivery") or {}
    delivery_size = None
    try:
        width = int(canvas.get("width"))
        height = int(canvas.get("height"))
        if width > 0 and height > 0:
            delivery_size = (width, height)
    except (TypeError, ValueError):
        delivery_size = None
    by_shot = []
    counts: dict[tuple[int, int], int] = {}
    for shot in shots:
        canvases = comp_canvases(shot)
        if not canvases:
            continue
        size = (canvases[0]["width"], canvases[0]["height"])
        by_shot.append((shot, size))
        counts[size] = counts.get(size, 0) + 1
    common = None
    if counts:
        common = max(sorted(counts), key=lambda size: counts[size])
    report = []
    for shot, size in by_shot:
        shot_id = str(shot.get("shot_id") or "")
        if delivery_size is not None and size != delivery_size:
            report.append({
                "kind": "canvas_differs_from_delivery",
                "shot": shot_id,
                "row": shot.get("row"),
                "name": shot.get("name"),
                "canvas": {"width": size[0], "height": size[1]},
                "delivery": {"width": delivery_size[0],
                             "height": delivery_size[1]},
                "detail": (f"this shot's comp was authored on "
                           f"{size[0]}x{size[1]} and the reel delivers "
                           f"{delivery_size[0]}x{delivery_size[1]}, so its "
                           "vignette and glow land on a different picture "
                           "than they were drawn for"),
            })
        if common is not None and size != common:
            report.append({
                "kind": "canvas_differs_from_neighbours",
                "shot": shot_id,
                "row": shot.get("row"),
                "name": shot.get("name"),
                "canvas": {"width": size[0], "height": size[1]},
                "neighbours": {"width": common[0], "height": common[1]},
                "detail": (f"this shot's comp was authored on "
                           f"{size[0]}x{size[1]} while the reel's other "
                           f"comps were authored on {common[0]}x"
                           f"{common[1]}"),
            })
    return report


# ── The pixel half ──────────────────────────────────────────────────────

def frame_difference(before_path: str, after_path: str, *,
                     tolerance: float = FRAME_TOLERANCE) -> dict:
    """How far two delivered frames of one shot differ.

    The frames are the DELIVERED picture, because a source frame and a
    delivered frame are not the same picture once the canvases differ.
    The measure is the mean absolute difference over the three channels,
    on a 0..1 scale, and a shot is said to have MOVED when it exceeds
    `tolerance` or when more than 2% of its pixels differ by more than
    `PIXEL_THRESHOLD`.

    Two frames of different sizes are a finding in themselves: the
    canvas moved.
    """
    try:
        from PIL import Image, ImageChops, ImageStat
    except ImportError as exc:  # pragma: no cover - the ML venv's business
        raise LookPreservationError(
            "the delivered frames cannot be compared without Pillow") \
            from exc
    try:
        with Image.open(before_path) as raw_before:
            left = raw_before.convert("RGB")
        with Image.open(after_path) as raw_after:
            right = raw_after.convert("RGB")
    except (OSError, ValueError) as exc:
        raise LookPreservationError(
            f"a delivered frame could not be read ({exc})") from exc
    if left.size != right.size:
        return {
            "difference": 1.0,
            "changed": True,
            "reason": "frame_size_changed",
            "before_size": list(left.size),
            "after_size": list(right.size),
        }
    difference = ImageChops.difference(left, right)
    mean = sum(ImageStat.Stat(difference).mean) / 3.0 / 255.0
    grey = difference.convert("L")
    histogram = grey.histogram()
    total = max(1, sum(histogram))
    differing = sum(histogram[PIXEL_THRESHOLD + 1:])
    fraction = differing / total
    return {
        "difference": round(float(mean), 6),
        "changed": bool(mean > tolerance or fraction > 0.02),
        "pixels_differing": round(float(fraction), 6),
        "before_size": list(left.size),
        "after_size": list(right.size),
    }


def compare_frames(before: Mapping, after: Mapping, *,
                   tolerance: float = FRAME_TOLERANCE) -> dict:
    """Which shots' DELIVERED frames moved between two renders.

    `before` and `after` map a shot id to the path of that shot's
    reference frame.  A shot present in one map and not the other is
    named, never quietly dropped: a frame that is not there cannot be
    compared, and that is a finding.
    """
    rows = {}
    changed = []
    missing = []
    for shot_id in sorted(set(before) | set(after)):
        old = before.get(shot_id)
        new = after.get(shot_id)
        if not old or not new:
            missing.append(shot_id)
            rows[shot_id] = {"changed": None,
                             "reason": "no_reference_frame",
                             "before": old, "after": new}
            continue
        measured = frame_difference(old, new, tolerance=tolerance)
        measured["before"] = old
        measured["after"] = new
        rows[shot_id] = measured
        if measured.get("changed"):
            changed.append(shot_id)
    return {
        "shots": rows,
        "changed": changed,
        "missing": missing,
        "status": VIOLATED if changed else PRESERVED,
    }


def shot_file_frames(look: Mapping, *, start_frame: int,
                     fps: float) -> dict:
    """Each shot's middle frame in the DELIVERED file.

    The delivered file's frame 0 is the timeline's `start_frame`, so a
    shot at timeline frame F is at file frame F - start_frame.  A shot
    whose middle frame falls outside the file is left out, because
    there is no frame of it to compare.
    """
    frames = {}
    for shot in look.get("shots") or ():
        try:
            start = int(shot.get("record_in"))
            end = int(shot.get("record_out"))
        except (TypeError, ValueError):
            continue
        if end <= start:
            continue
        middle = (start + end) // 2
        frames[str(shot.get("shot_id") or "")] = middle - int(start_frame)
    return frames


def reference_frames(video_path: str, look: Mapping, *, start_frame: int,
                     fps: float, dest_dir: str) -> dict:
    """Cut one reference frame per shot out of a DELIVERED file.

    The frames are read back out of the delivered file - never out of
    the timeline, and never out of a source clip - because the delivered
    picture is the one the captain watches.
    """
    from library.tools.delivered_caption_spec import extract_frames

    wanted = shot_file_frames(look, start_frame=start_frame, fps=fps)
    indices = sorted({index for index in wanted.values() if index >= 0})
    if not indices:
        return {}
    os.makedirs(dest_dir, exist_ok=True)
    extracted = extract_frames(video_path, indices, dest_dir)
    return {shot_id: extracted[index]
            for shot_id, index in wanted.items() if index in extracted}


# ── The verdict ─────────────────────────────────────────────────────────

def verify_look_preservation(*, before: Mapping, after: Mapping,
                              scope=None,
                              approved: set | None = None,
                              frames_before: Mapping | None = None,
                              frames_after: Mapping | None = None,
                              frame_tolerance: float = FRAME_TOLERANCE,
                              delivery: Mapping | None = None) -> dict:
    """Judge an approved shot's look across a revision.

    `before` and `after` are recorded snapshots (what `reel_read`
    returns).  `scope` is the authorized delta - a
    `preservation_invariant.MutationScope` - and without it a difference
    is UNPROVABLE rather than a pass.  `frames_before` and
    `frames_after` are the delivered reference frames, shot id to
    image path.

    The verdict is `verified` when nothing unauthorized moved and
    `failed` when something did.
    """
    before_look = capture_look(before)
    after_look = capture_look(after)
    if approved is None:
        approved = {str(shot.get("shot_id") or "")
                    for shot in before_look.get("shots") or ()}
    state = compare_look(before_look, after_look, scope=scope,
                          approved=approved)
    canvas = canvas_report(after_look, delivery=delivery)
    frames = None
    if frames_before is not None and frames_after is not None:
        frames = compare_frames(frames_before, frames_after,
                                tolerance=frame_tolerance)
    failures = []
    for row in state.get("findings") or ():
        if row.get("authorized") is False:
            failures.append(
                f"{row.get('row')} {row.get('name') or row.get('shot')}: "
                f"{row.get('field')} changed and no operation authorized "
                f"it")
    if frames is not None:
        for shot_id in frames.get("changed") or ():
            failures.append(
                f"{shot_id}: the delivered frame moved and no authorized "
                "delta explains it")
    return {
        "status": "verified" if not failures else "failed",
        "state": state,
        "canvas": canvas,
        "frames": frames,
        "failures": failures,
    }
