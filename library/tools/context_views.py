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


def _prosody(data: dict) -> dict:
    """The prosody that was MEASURED, and one line for what was not.

    `prosody_analysis.profiles` is a per-clip mapping and the failure
    path used to fill it with records that say only why nothing was
    measured.  Project 001 carried seventeen identical "parselmouth not
    installed" profiles into the creative-direction prompt, and one arm
    of the A/B said, unasked, that it had to ignore the whole section.

    A path allow-list cannot tell a measurement from a record of its
    absence - it selects by NAME - so this selects by `profile_defect`,
    the same predicate step 1.05 rejects a hollow profile with.  The
    absence is REPORTED rather than hidden: a model told plainly that
    prosody was not measured knows not to reason about it, which is what
    seventeen copies of an error message failed to say.
    """
    from library.tools.prosody_profile import profile_defect

    analysis = data.get("prosody_analysis")
    if not isinstance(analysis, dict):
        return {}
    profiles = analysis.get("profiles")
    # Step 1.05 writes a clip_id -> profile mapping; a LIST of profiles
    # each carrying its own clip_id is the other shape this key has been
    # written in, and reading only one of them would send nothing at all.
    if isinstance(profiles, list):
        profiles = {str(p.get("clip_id", i)): p
                    for i, p in enumerate(profiles) if isinstance(p, dict)}
    if not isinstance(profiles, dict):
        return {}

    measured, unmeasured = {}, {}
    for clip_id, profile in profiles.items():
        defect = profile_defect(profile)
        if defect:
            unmeasured[clip_id] = defect
        else:
            measured[clip_id] = profile

    view = {}
    if measured:
        view["measured"] = measured
        view["clips_measured"] = len(measured)
    if unmeasured:
        reasons = sorted(set(unmeasured.values()))
        view["not_measured"] = (
            f"{len(unmeasured)} of {len(profiles)} clip(s) have no prosody "
            f"measurement: " + "; ".join(reasons[:3])
            + ("; ..." if len(reasons) > 3 else "")
        )
    if not view:
        return {}
    return {"prosody": view}


def _catalog_clip_ids(data: dict) -> dict:
    """The document id -> catalog clip id map, from whatever names both.

    The documents are keyed by FILE STEM (`IMG_1816_v3`) and every table a
    planning step reasons over is keyed by the catalog's synthetic id
    (`clip_011`) - AGENTS.md 10.1.  A table the step cannot join to its own
    spine is the `topics_toon` defect: step 2.02 reported it as "the two
    tables cannot be joined without a mapping the context does not
    contain", and answered from a mapping it had derived at an earlier
    step instead.

    Returns {the document's own clip_id: the catalog clip_id}.  The join
    needs a list carrying both a clip_id and a path.  The catalog
    is the one that carries every clip; the A-roll and B-roll assignments
    carry the placed ones and are what a planning step is routed when it
    is not routed the catalog.  A document that joins to none of them
    keeps the id it came with.
    """
    from library.tools.semantic_index import build_semantic_lookup

    entries = []
    for key in ("clip_catalog", "a_roll_assignments", "b_roll_assignments"):
        value = data.get(key)
        if isinstance(value, dict):
            value = list(value.values())
        if not isinstance(value, list):
            continue
        for item in value:
            if not isinstance(item, dict):
                continue
            entries.append(item)
            # An A-roll assignment names its clips one level down.
            for seg in item.get("video_segments") or []:
                if isinstance(seg, dict):
                    entries.append(seg)
    if not entries:
        return {}

    docs = data.get("semantic_analysis_documents")
    if docs is None:
        docs = data.get("semantic_analysis")
    lookup = build_semantic_lookup(docs, entries)
    return {
        str(doc.get("clip_id")): cid
        for cid, doc in lookup.items()
        if isinstance(doc, dict) and doc.get("clip_id")
    }


def _picture(data: dict) -> dict:
    """What the footage SHOWS, across the WHOLE clip, one row per record.

    The creative director chose the story from `analysis.scene`, which is
    `scene[]` rendered as prose - and `scene[]` is one segment per clip on
    001, so `IMG_1816_v3` (188.6s, and the source of seven of the ten
    spoken lines in the cut) was described as "[0.0-18.9s] Outdoor urban
    area with a parking lot and construction site...".  That is 10% of the
    clip.  The step that decides what the video is about was deciding it
    from the first nineteen seconds.

    The material was already there and going to other steps: the vision
    pass writes one action window per ~10 seconds - 19 of them for that
    clip, 86 across 001's seventeen, covering 95% of the footage and
    reaching the last second of every one of the seventeen - and
    `vision_schema_adapter` renders them as `blocks`.

    **This is not a substitute for `scene[]`, and must not be read as
    one.**  `scene[]` says WHERE the clip is - location, type, lighting,
    notable features - and on 001 it says it for 46.4% of the footage.
    The action windows say WHAT HAPPENS, for all of it.  A step that needs
    the place still declares `analysis.scene`; this is the other axis, and
    it is the one that covers the whole clip.

    One row per record, not the raw records: `body_language` restates the
    same moment as posture and expression and costs 2.4x the bytes of
    `visual`, and `label` is `scene[]`'s location, which is the field that
    is degenerate in the first place.  `visual` is the reading that
    answers "what happens in this clip, and when".

    Rows are keyed by the CATALOG clip id wherever the routed inputs make
    that join possible (`_catalog_clip_ids`), because that is the id every
    other table in a planning step's context uses.

    A clip the vision pass described no action for is NAMED rather than
    silently absent - the same rule `_prosody` follows.
    """
    docs = data.get("semantic_analysis_documents")
    if docs is None:
        # `plan_transitions` is routed the whole step 1.03 output under
        # `semantic_analysis`; the documents are the same list.
        docs = data.get("semantic_analysis")
    if isinstance(docs, dict):
        if "semantic_analysis_documents" in docs:
            docs = docs["semantic_analysis_documents"]
        else:
            docs = list(docs.values())
    if not isinstance(docs, list):
        return {}

    joined = _catalog_clip_ids(data)

    rows, undescribed, unjoined = [], [], []
    for doc in docs:
        if not isinstance(doc, dict):
            continue
        own_id = doc.get("clip_id")
        clip_id = joined.get(str(own_id), own_id)
        if joined and own_id and str(own_id) not in joined:
            unjoined.append(str(own_id))
        described = False
        for block in doc.get("blocks") or []:
            if not isinstance(block, dict):
                continue
            visual = (block.get("visual") or "").strip()
            if not visual:
                continue
            described = True
            rows.append({
                "clip_id": clip_id,
                "start": _seconds(block.get("start")),
                "end": _seconds(block.get("end")),
                "visual": visual,
            })
        if not described and clip_id:
            undescribed.append(str(clip_id))

    if not rows:
        return {}

    view = {"observed": rows}
    if undescribed:
        view["not_described"] = (
            f"{len(undescribed)} clip(s) have no observed action to show: "
            + ", ".join(sorted(undescribed))
        )
    if unjoined:
        # A MIXED table is the dangerous one: some rows key to the clip
        # ids the rest of the context uses and some do not, and nothing
        # on the row says which.  Say it rather than let it be inferred.
        view["not_in_the_clip_list"] = (
            f"{len(unjoined)} clip(s) are named by the vision pass's own id "
            f"because no routed clip list names them: "
            + ", ".join(sorted(unjoined))
        )
    return {"picture": view}


def _seconds(value):
    """A time in seconds, or None when the record carries no time."""
    try:
        return round(float(value), 1)
    except (TypeError, ValueError):
        return None


# name -> builder(routed_inputs) -> a dict merged into the projection.
#
# A view's NAME is the key it writes.  That is what makes a second
# projection of an already-projected tree a no-op, which it has to be:
# an `llm_only` step is projected twice on every run - once by
# `gather_step_inputs` and again by `present_llm_step` - and the second
# pass sees a tree the first one already stripped the source out of.
CONTEXT_VIEWS = {
    "transcript": _transcript,
    "prosody": _prosody,
    "picture": _picture,
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
