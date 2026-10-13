"""VFX detection: the decision layer that knows WHEN to use stabilization,
screen replacement, subject isolation, retiming, object removal and
tracking - separate from the ability to operate them.

Each detection emits plan items with reason and evidence. A detection is
only a candidate; it never authorizes an edit. Stabilization still passes
the explicit-request gate in ``effect_authorization`` before compile/render.

1. **Stabilization needs** - shots where measured camera shake exceeds the
   instrument-derived threshold (``camera_stability.HANDHELD_BELOW``). The
   threshold is read off the block-search grid, not fitted to a project.
   The intent read (camera mode from semantic analysis) travels with the
   plan item: a handheld or selfie mode means the shake is deliberate, not
   a defect. An unreadable stability index is recorded as unmeasured with
   its reason - never silently skipped.

2. **Screen replacement candidates** - objects in semantic analysis that
   carry readable text and are not people or vehicles. A screen with text on
   it is a candidate for replacement; the text is the measured signal.

3. **Subject isolation needs** - clips where a subject grade or a
   behind-subject overlay requires a matte. The trigger is the same one
   step 1.06 uses, extended to emit plan items with reason and evidence.

4. **Retiming candidates** - clips whose measured motion energy is
   significantly above the project median.

5. **Object removal candidates** - objects with role ``passing`` in
   semantic analysis (briefly visible, measurable distraction).

6. **Tracking candidates** - clips whose camera mode is ``tracking`` (the
   camera is following a subject). A graphic placed in such a shot would
   need motion tracking to stay attached to the subject.

Each plan item names the existing operation that can execute it:
- ``stabilization_needed`` -> stabilization hold D5
- ``screen_replacement_candidate`` -> mask and track ops C-11
- ``subject_isolation_needed`` -> Magic Mask seed / SAM2 segmentation
- ``retiming_candidate`` -> native speed effects (speed_ramp, freeze_frame)
- ``object_removal_candidate`` -> mask and track ops C-11
- ``tracking_candidate`` -> mask and track ops C-11
"""

from __future__ import annotations

from library.tools.camera_stability import (
    HANDHELD_BELOW,
    read_camera_stability,
)
from library.tools.semantic_index import build_semantic_lookup

DETECTION_TYPES = {
    "stabilization_needed":
        "camera shake measured above the instrument-derived threshold",
    "screen_replacement_candidate":
        "object with readable text that is not a person or vehicle",
    "subject_isolation_needed":
        "subject grade or behind-subject overlay requires a matte",
    "retiming_candidate":
        "measured motion energy significantly above the project median",
    "object_removal_candidate":
        "object with role 'passing' (briefly visible, measurable distraction)",
    "tracking_candidate":
        "camera mode 'tracking' (camera is following a subject)",
}

EXECUTABLE_BY = {
    "stabilization_needed": "stabilization hold D5",
    "screen_replacement_candidate": "mask and track ops C-11",
    "subject_isolation_needed": "Magic Mask seed / SAM2 segmentation",
    "retiming_candidate": "native speed effects (speed_ramp, freeze_frame)",
    "object_removal_candidate": "mask and track ops C-11",
    "tracking_candidate": "mask and track ops C-11",
}


def _plan_item(detection_type: str, clip_id: str, reason: str,
               evidence: dict) -> dict:
    return {
        "detection_type": detection_type,
        "clip_id": clip_id,
        "reason": reason,
        "evidence": evidence,
        "executable_by": EXECUTABLE_BY[detection_type],
    }


def _camera_modes(doc: dict) -> list:
    """Distinct camera modes from a semantic analysis document.

    Returns the sorted list of ``camera[].mode`` values the VLM measured
    for the clip. Empty when the document carries no camera segments - an
    absent measurement is not evidence of intent.
    """
    if not isinstance(doc, dict):
        return []
    camera = doc.get("camera") or []
    if not isinstance(camera, list):
        return []
    modes = []
    for seg in camera:
        if not isinstance(seg, dict):
            continue
        mode = str(seg.get("mode") or "").strip().lower()
        if mode and mode not in modes:
            modes.append(mode)
    return sorted(modes)


def detect_stabilization_needs(temporal_index: dict,
                               clip_catalog: list = None,
                               semantic_docs: list = None,
                               unmeasured: list = None) -> list:
    """Detect shots needing stabilization from camera shake measurements.

    Reads ``camera_motion_decomposition.values[].residual`` from the
    temporal index and compares against ``HANDHELD_BELOW`` - the
    instrument-derived threshold from the 160-wide block search grid.

    Returns a list of plan items, one per clip whose mean residual exceeds
    the threshold. Clips with no measurement are not emitted - an absent
    measurement is not evidence of shake.

    When ``unmeasured`` is a list, clips whose stability index cannot be
    read (missing file, parse error) are appended there with the reason -
    an unreadable index is recorded, never silently skipped.

    When ``semantic_docs`` is provided, each plan item's evidence carries
    the clip's measured camera modes (the intent read): a ``handheld`` or
    ``selfie`` mode means the shake is deliberate, not a defect.
    """
    items = []
    lookup = {}
    if semantic_docs:
        lookup = build_semantic_lookup(semantic_docs, clip_catalog or [])
    indices = temporal_index
    if isinstance(temporal_index, dict):
        indices = temporal_index.get("temporal_event_indices",
                                     temporal_index.get("indices", []))
    for entry in indices or []:
        if not isinstance(entry, dict):
            continue
        clip_id = entry.get("clip_id", "")
        if not clip_id:
            continue
        index_path = entry.get("index_path", "")
        if index_path:
            import json
            import os
            if not os.path.exists(index_path):
                if isinstance(unmeasured, list):
                    unmeasured.append({
                        "clip_id": clip_id,
                        "reason": f"index file not found at {index_path}",
                    })
                continue
            try:
                with open(index_path, encoding="utf-8") as handle:
                    full_index = json.load(handle)
                label, method, mean_residual = read_camera_stability(
                    full_index)
            except (OSError, ValueError) as exc:
                if isinstance(unmeasured, list):
                    unmeasured.append({
                        "clip_id": clip_id,
                        "reason": f"index unreadable: "
                                  f"{type(exc).__name__}: {exc}",
                    })
                continue
        else:
            label, method, mean_residual = read_camera_stability(entry)

        if label in ("stable", "unknown") or mean_residual is None:
            continue
        if mean_residual < HANDHELD_BELOW:
            continue
        evidence = {
            "mean_residual": mean_residual,
            "label": label,
            "method": method,
            "threshold": HANDHELD_BELOW,
        }
        modes = _camera_modes(lookup.get(clip_id, {}))
        if modes:
            evidence["camera_modes"] = modes
        items.append(_plan_item(
            "stabilization_needed",
            clip_id,
            f"camera shake measured at {mean_residual:.4f} residual "
            f"({label}, method: {method})",
            evidence,
        ))
    return items


def detect_screen_replacement_candidates(semantic_docs: list,
                                         clip_catalog: list = None) -> list:
    """Detect screens that are candidates for replacement.

    A screen replacement candidate is an object in semantic analysis that:
    - has non-null ``readable_text`` (the VLM read text on it), and
    - is not a person or vehicle (category ``person`` or ``vehicle``).

    The readable text is the measured signal: a screen with text on it is
    a candidate for replacement. Objects without readable text are not
    emitted - no text means no measured content to replace.
    """
    items = []
    lookup = build_semantic_lookup(semantic_docs, clip_catalog or [])
    for clip_id, doc in lookup.items():
        if not isinstance(doc, dict):
            continue
        objects = doc.get("objects") or []
        if not isinstance(objects, list):
            continue
        for obj in objects:
            if not isinstance(obj, dict):
                continue
            category = str(obj.get("category") or "").lower()
            if category in ("person", "vehicle"):
                continue
            readable_text = obj.get("readable_text")
            if not readable_text:
                continue
            label = str(obj.get("label") or "unknown object")
            appearances = obj.get("appearances") or []
            items.append(_plan_item(
                "screen_replacement_candidate",
                clip_id,
                f"object '{label}' has readable text: {readable_text!r}",
                {
                    "object_label": label,
                    "category": category,
                    "readable_text": readable_text,
                    "appearances": appearances,
                },
            ))
    return items


def detect_subject_isolation_needs(color_grade_spec: dict = None,
                                   behind_subject_overlays: dict = None,
                                   a_roll_assignments: list = None,
                                   b_roll_assignments: list = None,
                                   b_roll_interjections: list = None) -> list:
    """Detect subjects that need isolation for a grade or a graphic.

    Uses the same trigger as step 1.06 (``matte_trigger``) but emits plan
    items with reason and evidence instead of just ``{clip_id: [reasons]}``.

    Two triggers:
    1. A ``subject_grades`` entry in ``color_grade_spec`` names a clip_id.
    2. A ``behind_subject_overlays`` segment plays over a timeline span a
       placed picture clip covers.
    """
    from library.steps.step_1_06_object_segmentation.step import matte_trigger

    items = []
    wanted = matte_trigger(
        color_grade_spec=color_grade_spec,
        behind_subject_overlays=behind_subject_overlays,
        a_roll_assignments=a_roll_assignments,
        b_roll_assignments=b_roll_assignments,
        b_roll_interjections=b_roll_interjections,
    )
    for clip_id, reasons in (wanted or {}).items():
        for reason in reasons:
            items.append(_plan_item(
                "subject_isolation_needed",
                clip_id,
                reason,
                {"trigger_reasons": reasons},
            ))
    return items


def _iter_temporal_entries(temporal_index):
    """Yield temporal index entries from either a dict or list container."""
    if isinstance(temporal_index, dict):
        indices = temporal_index.get("temporal_event_indices",
                                     temporal_index.get("indices", []))
    else:
        indices = temporal_index
    for entry in indices or []:
        if isinstance(entry, dict):
            yield entry


def _peak_motion(entry):
    """Read ``motion_energy.peak_mean_abs_diff`` from a temporal index entry.

    Returns None when the clip has no motion energy measurement - an absent
    measurement is not evidence of motion.
    """
    motion = entry.get("motion_energy")
    if not isinstance(motion, dict):
        return None
    peak = motion.get("peak_mean_abs_diff")
    if isinstance(peak, bool) or not isinstance(peak, (int, float)):
        return None
    return float(peak)


def detect_retiming_candidates(temporal_index: dict,
                               clip_catalog: list = None) -> list:
    """Detect shots where measured motion makes retiming a candidate.

    Reads ``motion_energy.peak_mean_abs_diff`` from the temporal index -
    the pre-normalization peak frame difference (0-1 grey scale). A clip
    whose peak is significantly above the project's own median is a
    retiming candidate: the motion is measured, not inferred from prose.

    The threshold is derived from the project's own distribution: twice the
    median peak across all measured clips. A clip with no motion energy
    measurement is not emitted - an absent measurement is not evidence of
    motion.
    """
    entries = list(_iter_temporal_entries(temporal_index))
    peaks = []
    for entry in entries:
        peak = _peak_motion(entry)
        if peak is not None:
            peaks.append(peak)
    if not peaks:
        return []
    peaks_sorted = sorted(peaks)
    n = len(peaks_sorted)
    if n % 2 == 1:
        median = peaks_sorted[n // 2]
    else:
        median = (peaks_sorted[n // 2 - 1] + peaks_sorted[n // 2]) / 2.0
    threshold = 2.0 * median
    items = []
    for entry in entries:
        clip_id = entry.get("clip_id", "")
        if not clip_id:
            continue
        peak = _peak_motion(entry)
        if peak is None or peak <= threshold:
            continue
        motion = entry.get("motion_energy") or {}
        high_motion_times = motion.get("high_motion_times") or []
        items.append(_plan_item(
            "retiming_candidate",
            clip_id,
            f"motion energy peak {peak:.4f} is above the project median "
            f"({median:.4f}) by more than 2x",
            {
                "peak_mean_abs_diff": peak,
                "project_median": median,
                "threshold": threshold,
                "threshold_source": "2x the project median peak_mean_abs_diff",
                "high_motion_times_count": len(high_motion_times),
            },
        ))
    return items


def detect_object_removal_candidates(semantic_docs: list,
                                     clip_catalog: list = None) -> list:
    """Detect objects that are candidates for removal.

    An object with role ``passing`` is briefly visible - a measurable
    distraction. The role is measured by the VLM in semantic analysis,
    not inferred from the label. Objects with no role or a different role
    are not emitted - only the measured ``passing`` role is evidence of a
    distraction.
    """
    items = []
    lookup = build_semantic_lookup(semantic_docs, clip_catalog or [])
    for clip_id, doc in lookup.items():
        if not isinstance(doc, dict):
            continue
        objects = doc.get("objects") or []
        if not isinstance(objects, list):
            continue
        for obj in objects:
            if not isinstance(obj, dict):
                continue
            role = str(obj.get("role") or "").lower()
            if role != "passing":
                continue
            label = str(obj.get("label") or "unknown object")
            appearances = obj.get("appearances") or []
            items.append(_plan_item(
                "object_removal_candidate",
                clip_id,
                f"object '{label}' has role 'passing' (briefly visible)",
                {
                    "object_label": label,
                    "category": str(obj.get("category") or ""),
                    "role": role,
                    "appearances": appearances,
                },
            ))
    return items


def detect_tracking_candidates(semantic_docs: list,
                               clip_catalog: list = None) -> list:
    """Detect shots where an overlay would need motion tracking.

    A tracking candidate is a clip whose measured camera mode is
    ``tracking`` - the camera is following a subject. A graphic placed in
    such a shot would need motion tracking to stay attached to the
    subject as the camera follows it.

    The camera mode is measured by the VLM in semantic analysis, not
    inferred from the label. Clips with a ``stationary``, ``mounted``,
    ``panning``, ``handheld`` or ``selfie`` mode are not emitted - those
    modes do not measure a subject being followed.
    """
    items = []
    lookup = build_semantic_lookup(semantic_docs, clip_catalog or [])
    for clip_id, doc in lookup.items():
        if not isinstance(doc, dict):
            continue
        modes = _camera_modes(doc)
        if "tracking" not in modes:
            continue
        items.append(_plan_item(
            "tracking_candidate",
            clip_id,
            f"camera mode 'tracking' (camera is following a subject)",
            {
                "camera_modes": modes,
            },
        ))
    return items


def detect_vfx_needs(temporal_index: dict = None,
                      semantic_docs: list = None,
                      clip_catalog: list = None,
                      color_grade_spec: dict = None,
                      behind_subject_overlays: dict = None,
                      a_roll_assignments: list = None,
                      b_roll_assignments: list = None,
                      b_roll_interjections: list = None) -> dict:
    """Run all detections and return a combined plan.

    Returns a dict with:
    - ``detections``: list of all plan items
    - ``by_type``: dict mapping detection_type to list of plan items
    - ``clips_touched``: sorted list of clip_ids that appear in any plan item
    - ``unmeasured_stabilization``: clips whose stability index could not
      be read, each with the reason - an absence with its cause, never a
      silent skip
    """
    unmeasured_stabilization = []
    stabilization = detect_stabilization_needs(
        temporal_index, clip_catalog,
        semantic_docs=semantic_docs,
        unmeasured=unmeasured_stabilization,
    )
    screens = detect_screen_replacement_candidates(semantic_docs, clip_catalog)
    isolation = detect_subject_isolation_needs(
        color_grade_spec=color_grade_spec,
        behind_subject_overlays=behind_subject_overlays,
        a_roll_assignments=a_roll_assignments,
        b_roll_assignments=b_roll_assignments,
        b_roll_interjections=b_roll_interjections,
    )
    retiming = detect_retiming_candidates(temporal_index, clip_catalog)
    removals = detect_object_removal_candidates(semantic_docs, clip_catalog)
    tracking = detect_tracking_candidates(semantic_docs, clip_catalog)
    all_items = (stabilization + screens + isolation + retiming
                 + removals + tracking)
    by_type = {
        "stabilization_needed": stabilization,
        "screen_replacement_candidate": screens,
        "subject_isolation_needed": isolation,
        "retiming_candidate": retiming,
        "object_removal_candidate": removals,
        "tracking_candidate": tracking,
    }
    clips_touched = sorted({item["clip_id"] for item in all_items})
    return {
        "detections": all_items,
        "by_type": by_type,
        "clips_touched": clips_touched,
        "unmeasured_stabilization": unmeasured_stabilization,
    }
