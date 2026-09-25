"""The duration zone a run is judged against, or the absence of one.

Four gates measure the cut against a target length: step 2.02's post
bridge, step 2.05's spine contract, step 3.03's rough-cut review and step
5.03's cohesion review.  Every one of them used to be handed
``(54.0, 60.0, 66.0)`` when nothing declared a target - and nothing ever
did, because ``project_config`` reached no step and no step that calls
this declares a ``brand_template`` input.  So on project 001 the captain's
own ``target_duration_seconds: 60`` in project.yaml governed nothing, and
four gates ran against a minute this file made up.

Both halves are fixed here and in `load_pipeline_state`:

* the PROJECT's declaration is read into state and broadcast, so the
  captain's number is the one the gates use;
* when neither the project nor a selected brand template declares a
  target, this returns **None** - "nothing declared a length" - and each
  caller says so rather than measuring against an invented one.
"""

from typing import Optional, Tuple

# How far either side of a declared target still counts as on-target.
# Not a creative value: the project declares a LENGTH, and a length with
# no tolerance would fail every cut that is not frame-exact.
PROJECT_TARGET_TOLERANCE = 0.1


def get_target_duration_zone(data: dict) -> Optional[Tuple[float, float, float]]:
    """Returns (min, target, max) in seconds, or None if nothing declared one.

    Precedence:

    1. ``project_config.target_duration_seconds`` - the project's own
       declaration, plus or minus ``PROJECT_TARGET_TOLERANCE``.
    2. the SELECTED brand template's ``content.target_duration_seconds``
       ``{min, max}``; the target is the average.  A project that names no
       brand template has no such declaration - see
       ``no_brand_template()`` in library/tools/brand_registry.py.
    3. **None.**  There used to be a ``54.0, 60.0, 66.0`` here.
    """
    project_config = data.get("project_config", {})
    brand_template = data.get("brand_template", {})

    target_dur = (project_config.get("target_duration_seconds")
                  if isinstance(project_config, dict) else None)
    if target_dur is not None:
        try:
            target_f = float(target_dur)
        except (TypeError, ValueError):
            target_f = 0.0
        if target_f > 0:
            return (target_f * (1 - PROJECT_TARGET_TOLERANCE),
                    target_f,
                    target_f * (1 + PROJECT_TARGET_TOLERANCE))

    if isinstance(brand_template, dict):
        content_slots = brand_template.get("content", {})
        brand_dur = content_slots.get("target_duration_seconds", {})
        if isinstance(brand_dur, dict) and "min" in brand_dur and "max" in brand_dur:
            min_dur = float(brand_dur["min"])
            max_dur = float(brand_dur["max"])
            return min_dur, (min_dur + max_dur) / 2.0, max_dur

    return None


def get_speech_duration_zone(data: dict) -> Optional[Tuple[float, float, float]]:
    """The band step 2.02 holds SPEECH alone to, or None if undeclared.

    `(zone_min, zone_target, zone_target)`: speech owns the LOWER half
    of the declared zone - it must reach the floor and must not pass
    the target. The band above the target, up to the zone ceiling, is
    the room the NON-SPEECH the spine adds extends into (intro/outro
    breaths, music and picture blocks - step 2.05's creative half),
    and step 2.05 holds the TOTAL to the full `(min, target, max)`.

    Why the ceiling is the target and not the max: 2.02 holding speech
    alone to the full zone, and 2.05 then holding the total to the
    same zone, left nothing for breaths, b-roll slots or beats - at a
    30 s target 30.3 s of speech passed 2.02 and left 2.7 s for
    everything else (findings 9 and 30, execution-frontier report
    2026-09-24). A speech sequence already at the target cannot take
    another breath without breaking the total, so it is refused HERE,
    where the model that chose the passages can still cut, rather than
    at 2.05, where nothing can. B-roll overlaps speech and adds no
    time; only what EXTENDS the total needs the upper band.
    """
    zone = get_target_duration_zone(data)
    if zone is None:
        return None
    min_dur, target_dur, _max_dur = zone
    return (min_dur, target_dur, target_dur)


# What the three resolved numbers ARE.  Shipped in the context alongside
# the numbers so a model judged against the zone can read what it is being
# judged against - the same route ``music_measurement.MEASUREMENT_LEGEND``
# takes for step 2.04.  Definitions only; what to conclude is the model's
# call.
DURATION_ZONE_LEGEND = {
    "minimum_seconds":
        "The shortest acceptable total spine duration, in seconds. "
        "A spine shorter than this fails the duration gate.",
    "target_seconds":
        "The declared target duration for the project, in seconds. "
        "The centre of the zone.",
    "maximum_seconds":
        "The longest acceptable total spine duration, in seconds. "
        "A spine longer than this fails the duration gate.",
}


NO_TARGET_DECLARED = (
    "no target length is declared: neither the project's "
    "`target_duration_seconds` nor a selected brand template's "
    "`content.target_duration_seconds` reached this step"
)
