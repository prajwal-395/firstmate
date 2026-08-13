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

    Step 1.03 nests its vision output under ``analysis.scene``; older
    outputs used ``visual_description`` or ``description`` at the top
    level.  Returns "" when the document carries no usable description.
    """
    if not doc:
        return ""
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


def clip_tags(doc: dict) -> set:
    """Tags/keywords attached to a semantic document, lowercased."""
    if not doc:
        return set()
    tags = list(doc.get("tags", []) or []) + list(doc.get("keywords", []) or [])
    analysis = doc.get("analysis") or {}
    tags += list(analysis.get("tags", []) or [])
    return {str(t).lower() for t in tags if t}
