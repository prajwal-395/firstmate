"""Project-graph colour family: the colour track's registration on shots."""
from __future__ import annotations
from .core import _as_list, _load_state

COLOUR_CAPABILITY = "color_grade.resolve"
"""The capability id the colour track's output is recorded under.

The grade step's post-bridge: its `color_grade_spec`, the bridge's
measured `clip_exposure` rows and the derived `camera_match` all land
in this one record (library/tools/capability_outputs.py partitions a
node's output; the undeclared keys belong to the run capability).
"""


def _build_colour(project_folder: str) -> dict:
    """The colour track's recorded output, or {} where the run produced none.

    Read through `capability_outputs`, the same reader
    `source_memory.load_catalog` uses - never a direct state key.
    """
    state = _load_state(project_folder)
    from library.tools import capability_outputs
    record = capability_outputs.read(state, COLOUR_CAPABILITY)
    return record if isinstance(record, dict) else {}


def _colour_for_clip(colour: dict, clip_id: str) -> dict | None:
    """The colour record for one clip: measurement, grade, match.

    Each half is the verbatim row the colour track produced - the
    measured `clip_exposure` row, the `per_clip_adjustments` entry
    carrying the delivered CDL, and the `camera_match` row whose
    camera group holds this clip. A half that was never produced is
    None, and a clip with no half at all is None: the graph does not
    invent a measurement it does not have.
    """
    measurement = None
    for row in _as_list(colour.get("clip_exposure")):
        if isinstance(row, dict) and row.get("clip_id") == clip_id:
            measurement = row
            break

    grade = None
    spec = colour.get("color_grade_spec")
    if isinstance(spec, dict):
        for adj in _as_list(spec.get("per_clip_adjustments")):
            if isinstance(adj, dict) and adj.get("clip_id") == clip_id:
                grade = adj
                break

    match = None
    match_record = colour.get("camera_match")
    if isinstance(match_record, dict):
        for row in _as_list(match_record.get("matches")):
            if isinstance(row, dict) and clip_id in _as_list(row.get("clips")):
                match = row
                break

    if measurement is None and grade is None and match is None:
        return None
    return {
        "measurement": measurement,
        "grade": grade,
        "match": match,
    }
