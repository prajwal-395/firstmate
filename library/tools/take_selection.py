"""Take selection: present alternate takes' delivery descriptions for comparison.

When the same content was recorded more than once, the editor chooses
a take. The spec's rule: "Don't use this objectively prettier take
because her delivery is weaker." Delivery is a judgment - whether one
performance is stronger than another - and this module does not make
it. It presents the measured delivery descriptions side by side so
the model (or the reviewer) can compare them and choose.

What it collects per alternate take:
- `speech_cue`: the vision pass's delivery description - pace, effort,
  pauses. This is the most direct delivery signal Ren measures.
- `body_language`: the vision pass's posture, gesture and facial
  expression description.
- `face_presence`: the temporal index's measured face visibility.
- `speech_regions`: the temporal index's measured speech spans.

No ranking, no recommendation, no invented ordering. The descriptions
are the fact; the judgment belongs to the model.

This module imports nothing from steps or processes: it reads the
measurements a state carries, the same reader every consumer has.
"""
from __future__ import annotations


def index_semantic_documents(raw: dict | list | None) -> dict:
    """Index semantic analysis output by clip_id.

    Step 1.03 emits `{"semantic_analysis_documents": [{"clip_id": ...,
    "blocks": [...]}, ...]}` - a list under a wrapper key, not a
    {clip_id: document} map. A caller that `.get(clip_id)` on the raw
    wrapper gets nothing on every real project, so every alternate
    reads as unmeasured. This indexes the list by clip_id; a map that
    is already indexed passes through unchanged.
    """
    if not raw:
        return {}
    if isinstance(raw, list):
        docs = raw
    elif isinstance(raw, dict):
        wrapped = raw.get("semantic_analysis_documents")
        if isinstance(wrapped, list):
            docs = wrapped
        elif isinstance(raw.get("semantic_analysis"), list):
            docs = raw["semantic_analysis"]
        elif "blocks" in raw and "clip_id" in raw:
            docs = [raw]
        else:
            return {k: v for k, v in raw.items()
                    if isinstance(v, dict)}
    else:
        return {}
    indexed = {}
    for doc in docs:
        if not isinstance(doc, dict):
            continue
        clip_id = doc.get("clip_id")
        if clip_id:
            indexed[str(clip_id)] = doc
    return indexed


def _delivery_description(semantic_doc: dict) -> dict:
    """The measured delivery descriptions from a semantic document.

    Returns the speech_cue and body_language for each block, or None
    for each field when the measurement is absent. An unmeasurable
    delivery is reported as absent, never as a default.
    """
    if not isinstance(semantic_doc, dict):
        return {"speech_cue": None, "body_language": None}
    blocks = semantic_doc.get("blocks") or []
    cues = []
    languages = []
    for block in blocks:
        if not isinstance(block, dict):
            continue
        cue = block.get("speech_cue")
        if cue:
            cues.append(str(cue))
        lang = block.get("body_language")
        if lang:
            languages.append(str(lang))
    return {
        "speech_cue": "; ".join(cues) if cues else None,
        "body_language": "; ".join(languages) if languages else None,
    }


def delivery_descriptions(raw: dict | list | None) -> dict:
    """The measured delivery descriptions per clip, keyed by clip_id.

    Accepts the raw step 1.03 shape or an already-indexed map (see
    `index_semantic_documents`). Every indexed clip gets an entry with
    `speech_cue` and `body_language`, each the measured description
    or None when unmeasured. A clip the vision pass never described
    is absent from the input, never defaulted here.
    """
    return {
        clip_id: _delivery_description(doc)
        for clip_id, doc in index_semantic_documents(raw).items()
    }


def _face_presence_mean(temporal_index: dict) -> float | None:
    """The clip's mean measured face presence, 0-1.

    None means no face was measured - an unmeasurable face, which is
    not a delivery signal. Never 0.0 as a stand-in: 0.0 would read as
    a measured "no face visible".
    """
    if not isinstance(temporal_index, dict):
        return None
    face = temporal_index.get("face_presence") or {}
    values = [v for v in (face.get("values") or [])
              if isinstance(v, (int, float))]
    if not values:
        return None
    return round(sum(values) / len(values), 4)


def _speech_spans(temporal_index: dict) -> list:
    """The clip's measured speech regions, as (start, end) pairs."""
    spans = []
    for region in temporal_index.get("speech_regions") or []:
        if not isinstance(region, dict):
            continue
        try:
            start = float(region.get("start", 0.0))
            end = float(region.get("end", 0.0))
        except (TypeError, ValueError):
            continue
        if end > start:
            spans.append((round(start, 3), round(end, 3)))
    return spans


def collect_take_alternates(
    clip_id: str,
    semantic_docs: dict,
    temporal_indices: dict,
    graph: dict,
) -> list:
    """Collect the measured delivery descriptions for alternate takes.

    Args:
        clip_id: the spine block's clip - the take already in the edit.
        semantic_docs: the project's measured per-clip vision analysis -
            either a {clip_id: semantic analysis document} map or the
            raw step 1.03 shape
            `{"semantic_analysis_documents": [...]}` (indexed here, so
            the caller never silently reads every alternate as
            unmeasured).
        temporal_indices: {clip_id: temporal index} - the project's
            measured per-clip face presence and speech regions.
        graph: the project graph, from project_graph.load_or_build.

    Returns a list of alternate take records, each with:
        - clip_id: the alternate take's clip
        - speech_cue: its measured delivery description, or None
        - body_language: its measured body language description, or None
        - face_presence: its measured face visibility, or None
        - speech_spans: its measured speech regions
    """
    from library.tools import project_graph

    alternates = project_graph.find_alternate_takes(graph, clip_id)
    semantic_by_clip = index_semantic_documents(semantic_docs)

    results = []
    for alt in alternates or []:
        if not isinstance(alt, dict):
            continue
        alt_id = str(alt.get("clip_id", ""))
        if not alt_id or alt_id == clip_id:
            continue
        sem = semantic_by_clip.get(alt_id, {})
        idx = (temporal_indices or {}).get(alt_id, {})
        desc = _delivery_description(sem)
        results.append({
            "clip_id": alt_id,
            "speech_cue": desc["speech_cue"],
            "body_language": desc["body_language"],
            "face_presence": _face_presence_mean(idx),
            "speech_spans": _speech_spans(idx),
        })

    return results
