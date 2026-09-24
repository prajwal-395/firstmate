"""Has anything moved a built timeline's transforms SINCE the build?

The build already writes what it placed: `versions.store` puts a
`timeline_serializer` snapshot of every promoted reel under
`pipeline_output/review/<timeline>.timeline.json` seconds after the
build, carrying each clip's `transform`. Nothing ever read one back
against the live timeline, and that is the gap this module closes.

What it measured, 2026-09-11
----------------------------
The "4x" that cost three investigations is **not a build-time defect**,
and this is what says so. Every affected reel's own build snapshot
records the values the engine computed; the live timeline holds a clean
power of two times them:

| reel | timeline created | snapshot written | snapshot | live | factor |
|---|---|---|---|---|---|
| Reel 09 (final) | 09-10 16:01 | (capture 09-11 02:30) | Pan -35, Tilt 0.25 | -70, 0.5 | 2 |
| Reel 13 | 09-11 10:58 | 09-11 10:59:23 | Pan -12, Tilt 0.25 | -24, 0.5 | 2 |
| Reels 01/23/28/30/31 | 09-11 17:00-17:04 | 09-11 17:05:48-52 | Pan 1.493, Tilt 0.25 | 5.972, 1.0 | **4** |
| Reel 26 | 09-11 18:47 | 09-11 18:48:29 | Pan -31.644, Tilt 0.25 | unchanged | 1 |

The factor is uniform over every clip on a timeline - picture and
overlay, footage and freeze - and leaves `ZoomX`/`ZoomY`/`Scaling`
alone. So `apply_transform_overrides` writing the captain's `Pan -12`,
reading back `-12` and not raising was CORRECT: the divergence appeared
after the writing process had gone.

A drift is REPORTED, never repaired here. Whatever multiplies these
values is still unidentified, and a pass that silently divides by the
factor it just measured is how a project ends up two wrongs from where
it started.

What is ruled out
-----------------
Measured on Resolve Studio 21.1.0.14, on a scratch project deleted
afterwards, each read from a SEPARATE process (a read-back in the
session that set the value echoes the set value and proves nothing):

- re-running the reel builder's own three resolution calls
  (`useCustomSettings` then width then height) on an ALREADY-PLACED
  timeline - literal;
- `useCustomSettings` 0 then 1 - literal;
- changing the PROJECT resolution out and back under the timeline -
  literal;
- `marker_capture.grab_still` (GrabStill + ExportStills + DeleteStills)
  - literal;
- `color_page_grade.apply_power_grade` (`ApplyGradeFromDRX`) - literal.

Together with the resolution rescale being exactly symmetric
(`overlay_placement`'s module docstring), no round trip through a
resolution can leave a net factor, so the multiplier is a WRITE by
something, not a Resolve rescale.
"""

from __future__ import annotations

from typing import Any, Iterable, Mapping, Sequence

#: A held value within this of the built one counts as unmoved. Resolve
#: stores floats and its read-back carries representation noise (a
#: built -35.0 reads back -35.000000000000036); a drift of the kind
#: this module exists to find is a whole multiple away.
UNMOVED = 1e-3

#: How far two clips' factors may differ and still be called one
#: factor. The same reasoning: a shared multiplier is exact to
#: representation noise, and anything looser would let two different
#: causes read as one.
SAME_FACTOR = 1e-6

#: The properties a drift is judged on. `ZoomX`/`ZoomY`/`Scaling` are
#: deliberately NOT here: the measured drift leaves them untouched, and
#: a column that never moves reads as coverage it does not give.
AXES = ("Pan", "Tilt")


class TransformDriftError(ValueError):
    """A snapshot that cannot be read as a set of placed transforms."""


def built_transforms(document: Mapping[str, Any]) -> dict:
    """`{(track_index, record_in): {"Pan":…, "Tilt":…, "name":…}}`.

    Reads a `timeline_serializer` document - the shape
    `versions.store` writes under `pipeline_output/review/`.
    A clip carrying no `transform` is not a placement this can judge
    and is left out; a document with no `tracks` at all RAISES, because
    an empty reading of an unreadable file is the silent pass.
    """
    tracks = document.get("tracks")
    if not isinstance(tracks, list):
        raise TransformDriftError(
            "this is not a serialized timeline: it carries no `tracks` "
            "list, so there is nothing to compare a live reading "
            "against.")
    built: dict = {}
    for track in tracks:
        if not isinstance(track, Mapping) or track.get("type") != "video":
            continue
        try:
            index = int(track["index"])
        except (KeyError, TypeError, ValueError):
            continue
        for clip in track.get("clips") or ():
            transform = clip.get("transform") or {}
            if not all(axis in transform for axis in AXES):
                continue
            try:
                start = int(clip["record_in"])
            except (KeyError, TypeError, ValueError):
                continue
            row = {axis: float(transform[axis]) for axis in AXES}
            row["name"] = str(clip.get("name") or "")
            built[(index, start)] = row
    return built


def drift_rows(built: Mapping[tuple, Mapping[str, Any]],
               held: Mapping[tuple, Mapping[str, Any]]) -> list:
    """One row per placement the snapshot recorded, moved or not.

    `held` is keyed the same way and carries the live `Pan`/`Tilt` -
    whatever read it, so this stays testable without Resolve. A
    placement the live timeline no longer has is a row with
    `missing: True`, never a row quietly dropped: a clip that went away
    is a bigger finding than one that moved.
    """
    rows = []
    for key in sorted(built):
        before = built[key]
        now = held.get(key)
        row = {"track": key[0], "start": key[1],
               "name": before.get("name", ""),
               "built": {axis: before[axis] for axis in AXES},
               "missing": now is None, "moved": False, "factors": {}}
        if now is None:
            rows.append(row)
            continue
        row["held"] = {axis: float(now[axis]) for axis in AXES}
        for axis in AXES:
            was, is_now = before[axis], row["held"][axis]
            if abs(is_now - was) > UNMOVED:
                row["moved"] = True
            row["factors"][axis] = (is_now / was) if abs(was) > UNMOVED else None
        rows.append(row)
    return rows


def uniform_factor(rows: Iterable[Mapping[str, Any]]):
    """The ONE factor every moved placement shares, or `None`.

    `None` means two things and says so through `describe`: nothing
    moved, or what moved did not move by one factor. Both readings are
    worth more than a number that averages them - the measured drift is
    uniform per timeline, so a non-uniform one is a DIFFERENT fault and
    must not be reported as this one.
    """
    seen = [factor
            for row in rows if row.get("moved")
            for factor in row.get("factors", {}).values()
            if factor is not None]
    if not seen:
        return None
    first = seen[0]
    if any(abs(factor - first) > SAME_FACTOR for factor in seen):
        return None
    return first


def describe(name: str, rows: Sequence[Mapping[str, Any]]) -> str:
    """One line per timeline, in the words a report would use."""
    missing = [row for row in rows if row.get("missing")]
    moved = [row for row in rows if row.get("moved")]
    if not rows:
        return f"{name}: the snapshot records no placed transform"
    if not moved and not missing:
        return (f"{name}: {len(rows)} placement(s) hold exactly what the "
                f"build wrote")
    factor = uniform_factor(moved)
    if factor is None and moved:
        head = (f"{name}: {len(moved)} of {len(rows)} placement(s) moved "
                f"since the build, by NO single factor")
    elif moved:
        head = (f"{name}: {len(moved)} of {len(rows)} placement(s) moved "
                f"since the build, every one by x{factor:g}")
    else:
        head = f"{name}: nothing moved"
    if missing:
        head += f"; {len(missing)} the timeline no longer has"
    return head
