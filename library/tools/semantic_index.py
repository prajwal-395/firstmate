"""Join semantic analysis documents to the clip catalog.

Step 1.03 keys its documents by the media file's stem (``IMG_1811``) while
the catalog assigns synthetic ids (``clip_006``).  Every consumer that
looked a document up by catalog clip_id therefore got nothing back, which
is why the B-roll candidate table came out empty and the LLM had no
footage to choose from.

`build_semantic_lookup` performs the join once, by file path, and hands
back a dict keyed by the catalog clip_id that the rest of the pipeline
speaks.
"""

import os

from library.tools.vision_schema_adapter import (
    adapt_semantic_document,
    camera_prose,
    format_ranges,
    framing_summary,
    is_v3_profile,
    movement_summary,
    scene_prose,
    stability_summary,
    subject_summary,
)


def _normalize_docs(semantic_docs) -> list:
    """Accept the several shapes step 1.03 output has taken over time."""
    if isinstance(semantic_docs, dict):
        if "semantic_analysis_documents" in semantic_docs:
            return _normalize_docs(semantic_docs["semantic_analysis_documents"])
        if "semantic_analysis" in semantic_docs:
            return _normalize_docs(semantic_docs["semantic_analysis"])
        if "clips" in semantic_docs:
            return _normalize_docs(semantic_docs["clips"])
        # Mapping of id -> document
        return [
            {**doc, "clip_id": doc.get("clip_id", key)}
            for key, doc in semantic_docs.items()
            if isinstance(doc, dict)
        ]
    if isinstance(semantic_docs, list):
        return [d for d in semantic_docs if isinstance(d, dict)]
    return []


def _stem(path: str) -> str:
    return os.path.splitext(os.path.basename(path or ""))[0].lower()


def build_semantic_lookup(semantic_docs, clip_catalog: list) -> dict:
    """Return {catalog_clip_id: semantic_document}.

    Matches on the source file path first, then on the file stem, then on
    a literal clip_id match.  Documents that match nothing are dropped -
    they cannot be attributed to a clip in this project.
    """
    docs = _normalize_docs(semantic_docs)
    if not docs:
        return {}

    by_path = {}
    by_stem = {}
    by_id = {}
    for clip in clip_catalog or []:
        cid = clip.get("clip_id")
        if not cid:
            continue
        by_id[cid] = cid
        for key in ("path", "source_file", "file_path"):
            if clip.get(key):
                by_path[os.path.realpath(clip[key])] = cid
                by_stem[_stem(clip[key])] = cid

    lookup = {}
    for doc in docs:
        cid = None
        for key in ("file_path", "path", "source_file"):
            if doc.get(key):
                cid = by_path.get(os.path.realpath(doc[key]))
                if cid:
                    break
                cid = by_stem.get(_stem(doc[key]))
                if cid:
                    break
        if not cid:
            doc_id = doc.get("clip_id", "")
            cid = by_id.get(doc_id) or by_stem.get(str(doc_id).lower())
        if cid:
            lookup[cid] = doc
    return lookup


def describe_clip(doc: dict) -> str:
    """Best available prose description of what a clip shows.

    v3 profiles describe the clip as time-bounded ``scene[]`` segments;
    step 1.03's retired schema nested a single-still caption under
    ``analysis.scene``, and older outputs used ``visual_description`` or
    ``description`` at the top level.  A raw v3 profile is adapted here
    too, so a document that reaches a consumer without passing through
    step 1.03 still describes itself instead of returning "" and failing
    the B-roll bridge.  Returns "" when the document carries no usable
    description.
    """
    if not doc:
        return ""
    if is_v3_profile(doc):
        described = scene_prose(doc)
        if described:
            return described
    for key in ("visual_description", "description", "summary"):
        value = doc.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    analysis = doc.get("analysis") or {}
    for key in ("scene", "summary", "visual_description"):
        value = analysis.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def clip_observations(doc: dict) -> dict:
    """What the analyser measured about a clip, as flat display fields.

    Candidate tables used to carry 180 characters of a single-frame
    caption and nothing else, so the model could not tell a wide
    establishing shot from a close-up.  Framing, stability and usable
    ranges were measured all along - this is the accessor that reaches
    them.  Every value is "" when the document never measured it, never a
    guessed default.
    """
    if not doc:
        return {}
    adapted = adapt_semantic_document(doc)
    analysis = adapted.get("analysis") or {}
    assessment = adapted.get("assessment") or {}

    activity = camera_prose(adapted) if is_v3_profile(adapted) else ""
    if not activity:
        motion = analysis.get("motion")
        activity = motion.strip() if isinstance(motion, str) else ""

    usable = format_ranges(assessment.get("usable_ranges"))
    if not usable:
        portions = assessment.get("usable_portions")
        usable = portions.strip() if isinstance(portions, str) else ""

    subjects = subject_summary(adapted)
    if not subjects:
        objects = analysis.get("objects")
        if isinstance(objects, str):
            subjects = objects.strip()
        elif isinstance(objects, list):
            subjects = "; ".join(str(o) for o in objects if o)

    return {
        "description": describe_clip(adapted),
        "activity": activity,
        "framing": framing_summary(adapted),
        "stability": stability_summary(adapted),
        "movement": movement_summary(adapted),
        "content_type": str(assessment.get("content_type")
                            or assessment.get("clip_type") or ""),
        "usable_ranges": usable,
        "subjects": subjects,
    }


def clip_tags(doc: dict) -> set:
    """Tags/keywords attached to a semantic document, lowercased."""
    if not doc:
        return set()
    doc = adapt_semantic_document(doc)
    tags = list(doc.get("tags", []) or []) + list(doc.get("keywords", []) or [])
    analysis = doc.get("analysis") or {}
    tags += list(analysis.get("tags", []) or [])
    assessment = doc.get("assessment") or {}
    tags += list(assessment.get("keywords", []) or [])
    return {str(t).lower() for t in tags if t}
