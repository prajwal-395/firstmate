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
  cannot know they share a body. Where the catalog recorded who shot
  each source (`camera_make`/`camera_model`, joined onto the rows in
  step 5.01), each group carries its `recorded_identity` and groups
  sharing one body are stated together in `shared_identity` - so the
  colourist sees what one slope would fix. The arithmetic never
  regroups: each group keeps its own measured neutral.
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

The chroma half: saturation, skin, contrast
-------------------------------------------
Two angles can agree in neutral balance and still not match: the same
wall under the same light reads GREYER on one camera, a face reads a
different vector, one shot is flatter. The neutral slope cannot see any
of that - it is a ratio on one measured vector - so the match stopped
exactly where a multicam grade's hardest half begins. This carries the
other three measured readings (library/tools/shot_colour) beside the
slope, each as the same arithmetic the slope is:

* `saturation_factor` - reference saturation over this camera's, the
  CDL `saturation` term that lands this camera's chroma ratio on the
  reference's - the same ratio of two stated measurements the neutral
  slope is, reported to prove the arithmetic, not to assert the
  render. What the render does is read back in Resolve.
* `skin_slope` - one per-channel `reference / measured` triple on the
  skin vector, the `slope` term that lands this camera's skin on the
  reference's. Whether to apply it whole-frame or scoped to the face
  (`subject_grade`) is the colourist's; the triple is stated either
  way, the way the neutral slope is.
* `contrast_ratio` - reference p95-p5 spread over this camera's,
  STATED and not a term: the CDL has no contrast term
  (`color_correction.WITHHELD_TERMS` - a pivot contrast would need a
  per-clip Fusion comp, which is a question this change does not
  answer). The colourist reads the ratio and judges.

Each is ABSENT with its reason where a side did not measure - a ratio
off an unmeasured saturation is the same defect as a match off a
whole-frame mean wearing a neutral's name. A measured 0.0 (a grey
frame, a flat field) is a measurement and reads as the absence of a
ratio, never as a factor of zero.

The creative intent travels beside the match, never into it
------------------------------------------------------------
A measured gap between two angles has two readings - a MISMATCH the
grade should fix, or a DELIBERATE mood change the grade must leave
alone - and only the creative intent separates them. That intent is
the project's declared look (`series_look`), which exists before the
colourist answers. It is carried VERBATIM: the look's own words and
its declared slope, so the colourist reads "the look declares X"
beside "the cameras differ by Y" and decides. The engine does not
read the prose - a reading of "this shift is fine" would be the engine
inventing a judgement (AGENTS.md 10.5), and the proposal stands
whatever the look says.

What IS derived is the direction of the look's own slope beside the
direction of the measured gap: `look_direction` and `gap_direction`
("warms" when the slope boosts red over blue, "cools" when it boosts
blue, "neutral" when the two channels move together) and
`same_direction_as_look`. A description of two slope triples'
arithmetic, not a verdict - the colourist is told whether the
declared look already travels the way the cameras differ, and what to
do with that remains theirs. `gap_direction` is derived from the
measured slope alone and travels even where the project declares no
look; `look_direction` and `same_direction_as_look` are absent
(None) where it declares none.
"""

from __future__ import annotations

import math
import os

from library.tools.series_look import DeclaredLook

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


def _ratio(reference, measured) -> float | None:
    """`reference / measured`, the factor that lands one on the other.

    None where no ratio exists - either side unmeasured, or a zero
    denominator. A measured 0.0 is a measurement (a grey frame, a flat
    field), and the absence of a ratio is said rather than shipped as a
    factor of zero.
    """
    if reference is None or measured is None or not measured:
        return None
    return round(float(reference) / float(measured), 4)


def _ratio_absence(row: dict, ref: dict, key: str) -> str:
    """Why a two-sided ratio on one measured key could not be taken.

    Names the side(s) that did not measure, carrying the reason the
    measurement itself recorded. Empty where the ratio exists.
    """
    missing = []
    for label, side in (("this camera", row), ("the reference", ref)):
        if side.get(key) is None:
            because = side.get(f"{key}_unmeasured_because")
            missing.append(f"{label} unmeasured - {because}" if because
                           else f"{label} unmeasured")
    if missing:
        return "; ".join(missing)
    if not row.get(key):
        return f"this camera measured {key} 0.0; no ratio exists off zero"
    return ""


def _skin_slope(ref: dict, row: dict) -> list | None:
    """Per-channel `reference / measured` on the skin vector, or None.

    The same arithmetic as the neutral slope, on the second measured
    vector - absent where either side carries no skin, or a skin
    channel measured zero.
    """
    ref_skin, row_skin = ref.get("skin_rgb"), row.get("skin_rgb")
    if not ref_skin or not row_skin:
        return None
    slope = [_ratio(float(ref_skin[i]), float(row_skin[i]))
             for i in range(3)]
    if any(v is None for v in slope):
        return None
    return slope


def _skin_absence(row: dict, ref: dict) -> str:
    """Why the skin slope could not be taken, or "" where it could."""
    missing = []
    for label, side in (("this camera", row), ("the reference", ref)):
        if side.get("skin_rgb") is None:
            because = side.get("skin_unmeasured_because")
            missing.append(f"{label} unmeasured - {because}" if because
                           else f"{label} unmeasured")
    if missing:
        return "; ".join(missing)
    if any(not v for v in row.get("skin_rgb") or []):
        return "a skin channel measured zero; no ratio exists"
    return ""


def _slope_direction(slope: list) -> str:
    """Where one slope triple travels: "warms" boosting red over blue,
    "cools" boosting blue, "neutral" when the two move together."""
    if slope[0] > slope[2]:
        return "warms"
    if slope[2] > slope[0]:
        return "cools"
    return "neutral"


def group_key(row: dict) -> tuple:
    """The camera group one measured row belongs to, and what grouped it.

    Returns `(key, grouped_by)`: the cut's own `camera` label where one
    travels on the row, else the source file's stem, else the clip id.
    The recorded make/model does NOT regroup here: each group keeps its
    first row's neutral and regrouping would silently drop the others'
    measurements. Identity is REPORTED beside the groups
    (`recorded_identity`, `shared_identity`) so the colourist sees
    which stem-groups claim one body - the engine merges nothing.
    """
    camera = str(row.get("camera") or "").strip()
    if camera:
        return camera, "camera label on the row"
    source = str(row.get("source_file") or "")
    stem = os.path.splitext(os.path.basename(source))[0] if source else ""
    if stem:
        return stem, "source file stem"
    return str(row.get("clip_id") or "?"), "clip id (no source file)"


def identity_key(row: dict):
    """The recorded body one measured row claims, or None.

    Read off the row's `camera_identity` (joined from the catalog's
    `camera_make`/`camera_model` in step 5.01): a (make, model) pair.
    A make without a model, or the reverse, identifies nothing - one
    half would merge every body sharing it.
    """
    identity = row.get("camera_identity") or {}
    make = identity.get("make")
    model = identity.get("model")
    if make and model:
        return (make, model)
    return None


def _shared_identity(measured: list) -> list:
    """Stem-groups claiming one recorded body, stated for the colourist.

    Two files from one body group by stem and read as two angles; where
    both carry the same recorded make/model, one slope may serve both -
    or the two bodies may be two units of one model, which grades the
    same. Either way the match arithmetic is unchanged (each group
    keeps its own neutral): this is the observation that stops the
    colourist re-deriving what one slope would fix, or accepting a
    slope across bodies that never shared one. Empty when no two
    groups share a recorded identity.
    """
    by_identity: dict = {}
    for group in measured:
        identity = group.get("recorded_identity")
        if identity is None:
            continue
        key = (identity["make"], identity["model"])
        by_identity.setdefault(key, []).append(group["camera"])
    return [{"make": make, "model": model, "groups": cameras}
            for (make, model), cameras in sorted(by_identity.items())
            if len(cameras) > 1]


def apply_slope(neutral_rgb: list, slope: list) -> list:
    """The measured neutral through a slope triple. Exact, by rounds."""
    return [round(float(neutral_rgb[i]) * float(slope[i]), 2)
            for i in range(3)]


def derive_camera_match(rows: list,
                        look: DeclaredLook | None = None) -> dict:
    """Derive the per-camera match from measured shot-colour rows.

    Args:
        rows: shot-colour rows, each carrying `clip_id`, `source_file`
            (or `camera`), `neutral_rgb`, `neutral_rb`, `neutral_gb`
            and `neutral_fraction`. Rows whose neutral is None are
            skipped with the reason - never matched off whole-frame.
        look: the project's resolved `series_look` declaration, or
            None where it declares none. Carried verbatim beside every
            match row - the colourist reads the look's own words next
            to the measured gap and decides. Never read into the
            arithmetic.

    Returns:
        A record carrying `method`, `grouped_by`, `reference` (the
        camera the match lands on, or None), `matches` (one per
        non-reference group: before/after balances, the slope, the
        level half in stops, the chroma half - the saturation factor,
        the skin slope and the stated contrast ratio, each absent with
        its reason where a side did not measure - and the look's words
        and slope direction beside the gap's) and `skipped` (every
        group or row that did not reach the derivation, with why).
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
                           "clips": [], "row": row, "identities": set()}
            order.append(key)
        groups[key]["clips"].append(row.get("clip_id", "?"))
        claimed = identity_key(row)
        if claimed is not None:
            groups[key]["identities"].add(claimed)

    measured = [groups[key] for key in order]
    for group in measured:
        identities = sorted(group.pop("identities"))
        group["recorded_identity"] = (
            {"make": identities[0][0], "model": identities[0][1]}
            if len(identities) == 1 else None)
    shared = _shared_identity(measured)
    if len(measured) < 2:
        return {
            "method": ("per-channel slope reference/measured on the "
                       "shared neutral (library/tools/camera_match)"),
            "grouped_by": (measured[0]["grouped_by"] if measured
                            else "no camera groups"),
            "reference": None,
            "matches": [],
            "skipped": skipped,
            "shared_identity": shared,
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
        saturation_factor = _ratio(ref.get("saturation"),
                                    row.get("saturation"))
        skin_slope = _skin_slope(ref, row)
        contrast_ratio = _ratio(ref.get("contrast"), row.get("contrast"))
        gap_direction = _slope_direction(slope)
        look_direction = (_slope_direction(list(look.slope))
                          if look is not None and look.has_cdl else None)
        matches.append({
            "camera": group["camera"],
            "clips": list(group["clips"]),
            "recorded_identity": group["recorded_identity"],
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
            "saturation_before": row.get("saturation"),
            "reference_saturation": ref.get("saturation"),
            "saturation_factor": saturation_factor,
            "saturation_unmeasured_because": (
                _ratio_absence(row, ref, "saturation")
                if saturation_factor is None else ""),
            "skin_before": row.get("skin_rgb"),
            "reference_skin": ref.get("skin_rgb"),
            "skin_slope": skin_slope,
            "skin_unmeasured_because": (
                _skin_absence(row, ref) if skin_slope is None else ""),
            "contrast_before": row.get("contrast"),
            "reference_contrast": ref.get("contrast"),
            "contrast_ratio": contrast_ratio,
            "contrast_unmeasured_because": (
                _ratio_absence(row, ref, "contrast")
                if contrast_ratio is None else ""),
            "look_name": look.name if look is not None else None,
            "look_intent": look.intent if look is not None else "",
            "look_slope": ([round(float(v), 4) for v in look.slope]
                           if look is not None and look.has_cdl else None),
            "look_direction": look_direction,
            "gap_direction": gap_direction,
            "same_direction_as_look": (
                look_direction == gap_direction
                if look_direction is not None else None),
            "already_matched": (
                _neutral_distance(row, ref) < MATCH_TOLERANCE),
        })

    return {
        "method": ("per-channel slope reference/measured on the shared "
                   "neutral, plus the chroma half - saturation factor, "
                   "skin slope, stated contrast ratio "
                   "(library/tools/camera_match)"),
        "grouped_by": reference["grouped_by"],
        "reference": {
            "camera": reference["camera"],
            "clips": list(reference["clips"]),
            "recorded_identity": reference["recorded_identity"],
            "why": ("its neutral sits nearest true neutral "
                    f"(R/B {ref['neutral_rb']}, G/B {ref['neutral_gb']})"),
        },
        "matches": matches,
        "skipped": skipped,
        "shared_identity": shared,
        "note": ("a STATED, MEASURED proposal: accept by copying a slope, "
                 "a saturation factor or a skin slope into "
                 "color_correction with a why, override with your own - "
                 "the engine applies nothing on its own. The contrast "
                 "ratio is stated, not a term: the CDL has none. The "
                 "declared look's words and slope direction travel "
                 "beside the gap's, never into the arithmetic - a "
                 "deliberate mood change and a mismatch are the "
                 "colourist's to separate. Verify the angles share the "
                 "set before accepting."),
    }
