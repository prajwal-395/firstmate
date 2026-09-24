"""Camera matching: the measured proposal that brings two angles together.

Step 5.01 could SEE that two shots differ in level (the `cut_adjacency`
gaps, in stops) but had no number for a DIFFERENCE IN CAST, so when two
angles covered the same set under different white balance the colourist
was shown no grounding for the CDL that fixes it. On geo-podcast the
shared dark neutral set reads camA R/B 1.003 against camB 0.953 - a
measured 0.05 gap on the wall itself, stable across the span and across
the neutral tolerance (fidelity rung 4c).

What this derives, from the shot-colour rows (library/tools/shot_colour):

* one group per camera: the `camera` label the cut carries when it has
  one, else the source file's stem (recorded as `grouped_by`, so a
  reader knows which). Two clips from one file are one angle; two files
  from one camera are two groups and say so - grouping by filename
  cannot know they share a body.
* the REFERENCE: the group whose neutral sits nearest true neutral
  (`|R/B - 1| + |G/B - 1|` smallest). Not the brightest, not the first:
  the match target is the angle with the least cast, stated on the row.
* per other group, one slope triple `reference / measured` per channel -
  the CDL slope that lands its neutral exactly onto the reference's.
  Slope-only, because gain is where a white-balance difference lives;
  the level it also carries is decomposed beside it as `level_stops`
  (`log2` of the neutral-mean ratio) so the colourist sees how much of
  the triple is exposure and how much is cast.
* the predicted AFTER: the measured neutral through the slope, which is
  the reference by construction - reported to prove the arithmetic, not
  to assert the render. What the render does is read back in Resolve.

What this NEVER is: a default. Every number is a ratio of two stated
measurements; a group with no measured neutral is SKIPPED with the
reason, never matched off its whole-frame mean; fewer than two measured
groups is a stated absence, not an identity proposal. The colourist
accepts by copying a slope into `color_correction` with a `why`, or
overrides with their own - the engine applies nothing on its own, and a
run that never reaches a colourist ships no match.

Two angles already together (neutral distance under `MATCH_TOLERANCE`)
are SAID, with a neutral slope: "these match" is the measurement's own
conclusion and leaving the row out would read as "not examined".
"""

from __future__ import annotations

import math
import os

#: Below this neutral distance two angles are reported as already
#: matched, with a neutral slope. A distance is |d(R/B)| + |d(G/B)| on
#: the shared neutral - the unit the whole table reasons in.
MATCH_TOLERANCE = 0.01


def _neutral_distance(first: dict, second: dict) -> float:
    return round(abs(first["neutral_rb"] - second["neutral_rb"])
                 + abs(first["neutral_gb"] - second["neutral_gb"]), 4)


def _cast_distance(neutral: dict) -> float:
    """How far one neutral sits from true neutral (1.0, 1.0)."""
    return abs(neutral["neutral_rb"] - 1.0) + abs(neutral["neutral_gb"] - 1.0)


def group_key(row: dict) -> tuple:
    """The camera group one measured row belongs to, and what grouped it.

    Returns `(key, grouped_by)`: the cut's own `camera` label where one
    travels on the row, else the source file's stem, else the clip id.
    """
    camera = str(row.get("camera") or "").strip()
    if camera:
        return camera, "camera label on the row"
    source = str(row.get("source_file") or "")
    stem = os.path.splitext(os.path.basename(source))[0] if source else ""
    if stem:
        return stem, "source file stem"
    return str(row.get("clip_id") or "?"), "clip id (no source file)"


def apply_slope(neutral_rgb: list, slope: list) -> list:
    """The measured neutral through a slope triple. Exact, by rounds."""
    return [round(float(neutral_rgb[i]) * float(slope[i]), 2)
            for i in range(3)]


def derive_camera_match(rows: list) -> dict:
    """Derive the per-camera match from measured shot-colour rows.

    Args:
        rows: shot-colour rows, each carrying `clip_id`, `source_file`
            (or `camera`), `neutral_rgb`, `neutral_rb`, `neutral_gb`
            and `neutral_fraction`. Rows whose neutral is None are
            skipped with the reason - never matched off whole-frame.

    Returns:
        A record carrying `method`, `grouped_by`, `reference` (the
        camera the match lands on, or None), `matches` (one per
        non-reference group: before/after balances, the slope, the
        level half in stops) and `skipped` (every group or row that
        did not reach the derivation, with why).
    """
    groups: dict = {}
    order: list = []
    skipped: list = []
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        neutral = row.get("neutral_rgb")
        if (not neutral
                or row.get("neutral_rb") is None
                or row.get("neutral_gb") is None):
            skipped.append({
                "clip_id": row.get("clip_id", "?"),
                "reason": ("no measured neutral - "
                           + str(row.get("colour_unmeasured_because")
                                 or "the neutral did not clear the floor")),
            })
            continue
        key, grouped_by = group_key(row)
        if key not in groups:
            groups[key] = {"camera": key, "grouped_by": grouped_by,
                           "clips": [], "row": row}
            order.append(key)
        groups[key]["clips"].append(row.get("clip_id", "?"))

    measured = [groups[key] for key in order]
    if len(measured) < 2:
        return {
            "method": ("per-channel slope reference/measured on the "
                       "shared neutral (library/tools/camera_match)"),
            "grouped_by": (measured[0]["grouped_by"] if measured
                            else "no camera groups"),
            "reference": None,
            "matches": [],
            "skipped": skipped,
            "note": ("fewer than two camera groups carry a measured "
                     "neutral, so there is nothing to match - this is a "
                     "stated absence, not an identity match"),
        }

    reference = min(measured,
                    key=lambda g: _cast_distance(g["row"]))
    ref = reference["row"]
    ref_mean = sum(ref["neutral_rgb"]) / 3.0

    matches = []
    for group in measured:
        if group is reference:
            continue
        row = group["row"]
        other = [float(v) for v in row["neutral_rgb"]]
        slope = [round(float(ref["neutral_rgb"][i]) / other[i], 4)
                 if other[i] else None for i in range(3)]
        if any(v is None for v in slope):
            skipped.append({
                "clip_id": row.get("clip_id", "?"),
                "reason": "a neutral channel measured zero; no ratio exists",
            })
            continue
        other_mean = sum(other) / 3.0
        level = (round(math.log2(ref_mean / other_mean), 3)
                 if other_mean > 0 and ref_mean > 0 else None)
        after = apply_slope(other, slope)
        blue = after[2]
        matches.append({
            "camera": group["camera"],
            "clips": list(group["clips"]),
            "reference_camera": reference["camera"],
            "reference_clips": list(reference["clips"]),
            "neutral_before": {
                "rgb": [round(v, 2) for v in other],
                "rb": row["neutral_rb"],
                "gb": row["neutral_gb"],
            },
            "reference_neutral": {
                "rgb": [round(float(v), 2)
                        for v in ref["neutral_rgb"]],
                "rb": ref["neutral_rb"],
                "gb": ref["neutral_gb"],
            },
            "neutral_distance_before": _neutral_distance(row, ref),
            "slope": slope,
            "level_stops": level,
            "neutral_after": {
                "rgb": after,
                "rb": (round(after[0] / blue, 4) if blue else None),
                "gb": (round(after[1] / blue, 4) if blue else None),
            },
            "already_matched": (
                _neutral_distance(row, ref) < MATCH_TOLERANCE),
        })

    return {
        "method": ("per-channel slope reference/measured on the shared "
                   "neutral; slope-only, level decomposed as level_stops "
                   "(library/tools/camera_match)"),
        "grouped_by": reference["grouped_by"],
        "reference": {
            "camera": reference["camera"],
            "clips": list(reference["clips"]),
            "why": ("its neutral sits nearest true neutral "
                    f"(R/B {ref['neutral_rb']}, G/B {ref['neutral_gb']})"),
        },
        "matches": matches,
        "skipped": skipped,
        "note": ("a STATED, MEASURED proposal: accept by copying a slope "
                 "into color_correction with a why, override with your "
                 "own - the engine applies nothing on its own. Verify "
                 "the angles share the set before accepting."),
    }
