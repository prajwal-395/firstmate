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

from collections.abc import Mapping


def rank_take_instances(comparisons: list[dict], *, policy,
                        project_id: str, measurements_by_instance: Mapping,
                        provenance_id: str) -> list[dict]:
    """Rank preserved take instances under an explicit FI-22 policy.

    A take instance is identified by its coverage-group id and member span,
    not just its clip id: repeated recordings and duplicate media instances
    therefore remain separate candidates. Requirements remove failing
    candidates; preferences rank the survivors by count of passing rules.
    Ties remain ties. No delivery attribute (including pause count) gets an
    implicit preference.

    ``measurements_by_instance`` maps that instance id to stable
    ``measurement_id: EvidenceRecord`` entries. Missing policy or evidence
    fails closed through the FI-22 evaluator.
    """
    from library.tools import evaluation_policy

    if isinstance(policy, Mapping):
        policy = evaluation_policy.EvaluationPolicy.from_dict(policy)
    if policy is None:
        raise evaluation_policy.EvaluationPolicyError(
            "take ranking requires an explicit evaluation policy")
    if policy.project_id != project_id:
        raise evaluation_policy.EvaluationPolicyError(
            f"policy {policy.ref!r} is scoped to {policy.project_id!r}, "
            f"not {project_id!r}")

    candidates = []
    for comparison in comparisons:
        group_id = comparison["group_id"]
        for member_index, member in enumerate(comparison["members"]):
            if member["clip_id"] is None:
                continue
            start, end = member["source_start"], member["source_end"]
            instance_id = (f"{group_id}:{member_index}:"
                           f"{member['content_digest']}:{start}:{end}")
            measurements = measurements_by_instance.get(instance_id, {})
            if not isinstance(measurements, Mapping):
                raise evaluation_policy.EvaluationPolicyError(
                    f"measurements for {instance_id!r} must be a mapping")
            # All rows for one take must refer to its source asset/clock.
            records = list(measurements.values())
            if not records:
                raise evaluation_policy.EvaluationPolicyError(
                    f"take {instance_id!r} has no typed measurements")
            asset_id, clock_id = records[0].asset_id, records[0].clock_id
            evaluations = evaluation_policy.evaluate(
                policy, asset_id=asset_id, clock_id=clock_id,
                project_id=project_id,
                start_tick=round(float(start) * 1000),
                end_tick=round(float(end) * 1000),
                measurements=measurements, provenance_id=provenance_id)
            by_rule = {
                rule_id: value
                for result in evaluations
                for rule_id in result.basis_rule_ids
                for value in [result.value]
            }
            candidates.append({
                "instance_id": instance_id,
                "clip_id": member["clip_id"],
                "source_start": start,
                "source_end": end,
                "group_id": group_id,
                "policy_id": policy.ref,
                "rule_evaluations": by_rule,
                "eligible": all(by_rule[rule.id]
                                for rule in policy.requirements),
                "preference_score": sum(
                    by_rule[rule.id] for rule in policy.preferences),
                "evaluation_evidence": evaluations,
            })
    candidates.sort(key=lambda row: (
        not row["eligible"], -row["preference_score"],
        row["group_id"], row["source_start"], row["instance_id"]))
    return candidates


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

    comparisons = project_graph.find_take_comparisons(graph, clip_id)
    semantic_by_clip = index_semantic_documents(semantic_docs)

    results = []
    # FI-16 groups retain occurrences, including repeated instances of the
    # same clip. Keep the source spans and coverage relation attached to the
    # planning payload instead of collapsing it to a clip-level alternate.
    for comparison in comparisons:
        for member_index, member in enumerate(comparison["members"]):
            alt_id = member["clip_id"]
            if alt_id is None:
                continue
            sem = semantic_by_clip.get(str(alt_id), {})
            idx = (temporal_indices or {}).get(str(alt_id), {})
            desc = _delivery_description(sem)
            results.append({
                "clip_id": str(alt_id),
                "instance_id": (
                    f"{comparison['group_id']}:{member_index}:"
                    f"{member['content_digest']}:{member['source_start']}:"
                    f"{member['source_end']}"),
                "group_id": comparison["group_id"],
                "take_relation": comparison["kind"],
                "same_instant": comparison["same_instant"],
                "source_start": member["source_start"],
                "source_end": member["source_end"],
                "speech_evidence": member["speech_evidence"],
                "picture_evidence": member["picture_evidence"],
                "audio_evidence": member["audio_evidence"],
                "speech_cue": desc["speech_cue"],
                "body_language": desc["body_language"],
                "face_presence": _face_presence_mean(idx),
                "speech_spans": _speech_spans(idx),
            })
    if comparisons:
        return results

    alternates = project_graph.find_alternate_takes(graph, clip_id)
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
