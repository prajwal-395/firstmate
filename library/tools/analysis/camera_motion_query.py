"""Find B-roll intervals using measured camera and scene movement."""

from __future__ import annotations

from library.tools.analysis.footage_segments import (
    load_catalog,
    load_temporal_index,
)
from library.tools.camera_motion import (
    matches_motion_constraints,
    summarize_camera_motion,
)


def rank_static_intervals(catalog, temporal_indexes, *, min_duration=2.0,
                          max_static_score=0.05, max_shake=None,
                          max_subject_energy=None):
    """Return measured intervals satisfying numeric low-motion limits.

    ``min_duration`` is applied to the catalog clip duration. Missing camera
    movement measurements fail closed, so semantic stability words never
    manufacture a static result.
    """
    if min_duration < 0:
        raise ValueError("min_duration must be non-negative")
    found = []
    for clip in catalog:
        clip_id = clip["clip_id"]
        duration = clip.get("duration_seconds")
        if not isinstance(duration, (int, float)) or duration < min_duration:
            continue
        index = temporal_indexes.get(clip_id)
        if not isinstance(index, dict):
            continue
        summary = summarize_camera_motion(index, 0.0, float(duration))
        if not matches_motion_constraints(
                summary, max_static_score=max_static_score,
                max_shake=max_shake,
                max_subject_energy=max_subject_energy):
            continue
        found.append({"clip_id": clip_id,
                      "source_file": clip.get("source_file") or clip.get("path"),
                      **summary})
    return sorted(found, key=lambda row: row["static_score"])


def find_static_broll(project_folder, **constraints):
    """Search analyzed project footage for relatively static B-roll.

    Example: ``find_static_broll(project, max_static_score=0.04)``.
    Thresholds are explicit numeric movement limits in normalized
    proxy-frame units per 5 Hz sample, never inferred from model prose.
    """
    return rank_static_intervals(
        load_catalog(project_folder), load_temporal_index(project_folder),
        **constraints)
