"""Named views a manifest may put in the prompt instead of a raw dot path.

`context_fields` is an allow-list of dot paths into a step's routed
inputs, so what the model reads is always the SHAPE the pipeline stores.
That is right for most things and wrong for one: WhisperX's per-word
timings.

`temporal_index.*.speech_regions` carries, per clip, a list of regions
each holding its text AND every word in it with a start and an end.  On
project 001 that is 110 regions, 1,439 word records and 93,246 bytes -
82.5% of `creative_direction`'s entire prompt - to say 7,184 bytes worth
of English.  No creative model is asked anything a word boundary
answers; the code that cuts on them (`speech_sequence`'s post-bridge,
`spine_contract`, `plan_subtitles`, `bookends`) reads them from the
per-clip index files and from the unprojected inputs, never from the
prompt.

A view is the enumeration that lets a manifest ask for the READING of a
routed input rather than its storage shape.  One table, one builder per
name, and an unknown name raises - the same shape `beat_grid`,
`energy_reading`, `music_behavior`, `safe_area` and `house_look` use.

Declare one by putting `view:<name>` in `context_fields`.  A view whose
source input is not routed to the step contributes nothing, exactly as a
dot path that resolves to nothing does; the contract that a step
declaring a view also declares its source input is asserted on the
manifests, in `tests/test_transcript_view.py`.
"""

VIEW_PREFIX = "view:"


def _transcript(data: dict) -> dict:
    """What was said, in which clip, between which two seconds.

    Built from the `temporal_index` input, which the DAG maps from step
    1.04's `full_indices` - a LIST of per-clip dicts (AGENTS.md 10.3).
    The per-word `words` array under each region is what this view exists
    to leave behind.
    """
    clips = data.get("temporal_index")
    if not isinstance(clips, list):
        return {}

    lines = []
    for entry in clips:
        if not isinstance(entry, dict):
            continue
        clip_id = entry.get("clip_id")
        for region in entry.get("speech_regions") or []:
            text = (region.get("text") or "").strip()
            if not text:
                continue
            lines.append({
                "clip_id": clip_id,
                "start": round(float(region.get("start", 0.0)), 3),
                "end": round(float(region.get("end", 0.0)), 3),
                "text": text,
            })
    if not lines:
        return {}
    return {"transcript": lines}


# name -> builder(routed_inputs) -> a dict merged into the projection.
#
# A view's NAME is the key it writes.  That is what makes a second
# projection of an already-projected tree a no-op, which it has to be:
# an `llm_only` step is projected twice on every run - once by
# `gather_step_inputs` and again by `present_llm_step` - and the second
# pass sees a tree the first one already stripped the source out of.
CONTEXT_VIEWS = {
    "transcript": _transcript,
}


def is_view(path: str) -> bool:
    return path.startswith(VIEW_PREFIX)


def view_name(path: str) -> str:
    return path[len(VIEW_PREFIX):]


def build_view(name: str, data: dict) -> dict:
    """Build one named view. An unknown name raises rather than defaulting.

    Projecting an already-projected tree returns the view unchanged: the
    source input is gone by then, so the builder has nothing to read, and
    a second pass would otherwise DELETE the view the first one built.
    """
    if name not in CONTEXT_VIEWS:
        raise ValueError(
            f"Unknown context view {name!r}. "
            f"Known views: {sorted(CONTEXT_VIEWS)}"
        )
    built = CONTEXT_VIEWS[name](data)
    if not built and name in data:
        return {name: data[name]}
    return built
