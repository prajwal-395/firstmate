"""Named references and explicit, dimension-scoped intent projection.

Measurements remain facts produced by :mod:`reference_style`; this module
records which measured dimensions a creator wants to use and how they feel
about each one. Missing media or missing measurements never become zeroes.
"""

from __future__ import annotations

from copy import deepcopy

VERSION = 1

# Stable product names mapped to the existing Style Representation fields.
DIMENSIONS = {
    "pacing": "pacing",
    "transitions": "transition_vocabulary",
    "palette": "palette",
    "contrast": "contrast",
    "text": "text",
    "camera_movement": "camera_movement",
    "graphic_density": "graphic_density",
    "music_sync": "music_sync",
    "framing": "framing",
    "sound_design": "sound_design",
}


class ReferenceCollectionError(ValueError):
    """A reference collection cannot be projected safely."""


def empty_collection():
    return {"version": VERSION, "references": []}


def reference(*, reference_id, name, media, measurements=None, digest="",
              selections=None, use_all=False):
    """Create a reference entry. ``use_all`` selects present dimensions."""
    if not all(isinstance(value, str) and value.strip()
               for value in (reference_id, name, media)):
        raise ReferenceCollectionError("reference id, name and media are required")
    measurements = deepcopy(measurements) if isinstance(measurements, dict) else None
    if use_all and selections:
        raise ReferenceCollectionError("use_all cannot be combined with selections")
    selected = (sorted(dim for dim, field in DIMENSIONS.items()
                       if measurements and field in measurements)
                if use_all else list(selections or []))
    if len(selected) != len(set(selected)) or any(dim not in DIMENSIONS for dim in selected):
        raise ReferenceCollectionError("unknown or duplicate reference dimension")
    return {
        "id": reference_id.strip(), "name": name.strip(), "media": media,
        "measurement_digest": digest or "", "measurements": measurements,
        "selection_mode": "all" if use_all else "selected",
        "dimensions": {dim: "like" for dim in selected},
    }


def set_preference(entry, dimension, preference):
    """Return a copy with an explicit per-dimension like/dislike selection."""
    if dimension not in DIMENSIONS:
        raise ReferenceCollectionError(f"unknown reference dimension {dimension!r}")
    if preference not in ("like", "dislike", None):
        raise ReferenceCollectionError("preference must be like, dislike, or None")
    result = deepcopy(entry)
    if preference is None:
        result["dimensions"].pop(dimension, None)
    else:
        result["dimensions"][dimension] = preference
    result["selection_mode"] = "selected"
    return result


def measure(entry, project_folder):
    """Attach the existing Style Representation, reusing a matching digest.

    Missing media is recorded explicitly. Measurements are cached on the
    reference entry, so repeated intent projection never decodes a video.
    """
    import os
    from library.tools import reference_style

    result = deepcopy(entry)
    path = result["media"]
    if not os.path.isabs(path):
        path = os.path.join(project_folder, path)
    digest = reference_style.reference_digest(path)
    if not digest:
        result.update(measurements=None, measurement_digest="",
                      measurement_status="missing_media")
        return result
    if (result.get("measurement_digest") == digest
            and isinstance(result.get("measurements"), dict)):
        result["measurement_status"] = "cached"
        return result
    try:
        result["measurements"] = reference_style.measure_reference_style(path)
    except (OSError, ValueError, RuntimeError) as exc:
        result.update(measurements=None, measurement_digest=digest,
                      measurement_status="measurement_error",
                      measurement_error=str(exc))
        return result
    result.update(measurement_digest=digest, measurement_status="measured")
    result.pop("measurement_error", None)
    return result


def add(collection, entry):
    """Return a validated collection containing the named reference."""
    result = deepcopy(collection or empty_collection())
    if result.get("version") != VERSION or not isinstance(result.get("references"), list):
        raise ReferenceCollectionError("unsupported reference collection")
    if any(row["id"] == entry["id"] for row in result["references"]):
        raise ReferenceCollectionError(f"duplicate reference id {entry['id']!r}")
    result["references"].append(deepcopy(entry))
    return result


def declarations(collection, *, project_id):
    """Make scoped-intent declarations from selected, available facts only."""
    result = []
    if not collection:
        return result
    if collection.get("version") != VERSION:
        raise ReferenceCollectionError("unsupported reference collection")
    for item in collection["references"]:
        measurements = item.get("measurements")
        if not isinstance(measurements, dict):
            continue  # missing media or unavailable/corrupt cached measurement
        selected = dict(item["dimensions"])
        if item.get("selection_mode") == "all":
            for dimension, field in DIMENSIONS.items():
                if field in measurements:
                    selected.setdefault(dimension, "like")
        for dimension, preference in sorted(selected.items()):
            if dimension not in DIMENSIONS or preference not in ("like", "dislike"):
                raise ReferenceCollectionError("invalid dimension preference")
            field = DIMENSIONS[dimension]
            if field not in measurements:
                continue  # selected but not measurable: do not invent a fact
            result.append({
                "key": f"reference.{dimension}",
                "value": {"preference": preference, "measurement": measurements[field]},
                "source": "creator", "scope": str(project_id),
                "provenance": (
                    f"reference:{item['id']} ({item['name']}); media={item['media']}; "
                    f"measurement_digest={item.get('measurement_digest', '')}; "
                    f"dimension={dimension}"
                ),
                "confidence": 1.0, "explicit": True,
            })
    return result
