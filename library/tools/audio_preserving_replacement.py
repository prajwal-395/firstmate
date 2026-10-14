"""Compile evidence-backed picture substitutions while retaining dialogue.

Planning records are explicit: a proposal must name the original and
replacement spans, the evidence that established their synchronization,
the bad-picture evidence, and a continuity verdict.  This module does
not infer synchronization from overlapping timestamps or from a B-roll
selection.
"""

from __future__ import annotations

from copy import deepcopy
import math


class ReplacementPlanError(ValueError):
    """A proposed picture replacement cannot safely reach the manifest."""


def validate_replacement(proposal: dict, *, fps: float = 24.0) -> dict:
    """Validate one source-clock replacement and normalize its time spans."""
    if not isinstance(proposal, dict):
        raise ReplacementPlanError("replacement proposal must be an object")
    required = ("spine_block_position", "original", "replacement",
                "bad_picture_evidence_ids", "sync_evidence_ids",
                "continuity")
    missing = [key for key in required if key not in proposal]
    if missing:
        raise ReplacementPlanError(
            f"replacement proposal missing required fields: {missing}")

    original = proposal["original"]
    replacement = proposal["replacement"]
    if not isinstance(original, dict) or not isinstance(replacement, dict):
        raise ReplacementPlanError("original and replacement spans are required")
    if not original.get("source_file") or not replacement.get("source_file"):
        raise ReplacementPlanError("both source files must be identified")
    for span in (original, replacement):
        for key in ("source_start", "source_end", "timeline_start",
                    "timeline_end"):
            if key not in span:
                raise ReplacementPlanError(f"span missing {key}")
            span[key] = float(span[key])
            if not math.isfinite(span[key]):
                raise ReplacementPlanError(f"span {key} must be finite")
        if span["source_end"] <= span["source_start"]:
            raise ReplacementPlanError("source span must have positive duration")
        if span["timeline_end"] <= span["timeline_start"]:
            raise ReplacementPlanError("timeline span must have positive duration")
        source_duration = span["source_end"] - span["source_start"]
        timeline_duration = span["timeline_end"] - span["timeline_start"]
        if abs(source_duration - timeline_duration) > 1.0 / fps:
            raise ReplacementPlanError(
                "source and timeline spans must preserve normal-speed sync")
    if (original["timeline_start"] != replacement["timeline_start"]
            or original["timeline_end"] != replacement["timeline_end"]):
        raise ReplacementPlanError(
            "replacement picture must cover the exact dialogue timeline span")
    for key, description in (("bad_picture_evidence_ids", "bad-picture"),
                             ("sync_evidence_ids", "synchronization")):
        evidence_ids = proposal[key]
        if (not isinstance(evidence_ids, list) or not evidence_ids
                or any(not isinstance(value, str) or not value.strip()
                       for value in evidence_ids)):
            raise ReplacementPlanError(f"{description} evidence must be cited")
    continuity = proposal["continuity"]
    if not isinstance(continuity, dict) or continuity.get("verified") is not True:
        raise ReplacementPlanError("picture continuity is not verified")
    evidence_ids = continuity.get("evidence_ids")
    if (not isinstance(evidence_ids, list) or not evidence_ids
            or any(not isinstance(value, str) or not value.strip()
                   for value in evidence_ids)):
        raise ReplacementPlanError("continuity evidence must be cited")
    return proposal


def apply_replacements(v1_clips: list, proposals: list, *, fps: float) -> dict:
    """Swap V1 pictures and add unchanged source audio mirrors on A1.

    A proposal is applied only when its original span matches exactly one
    V1 clip and its speech audio mirror. The source audio clip is copied
    before changing the V1 picture, preserving its file and source times.
    """
    if not proposals:
        return {"replacements": [], "speech_audio_clips": []}
    if fps <= 0:
        raise ReplacementPlanError("project frame rate must be positive")
    records = []
    audio_additions = []
    claimed = set()
    for raw in proposals:
        proposal = validate_replacement(deepcopy(raw), fps=fps)
        pos = proposal["spine_block_position"]
        if pos in claimed:
            raise ReplacementPlanError(f"duplicate replacement for block {pos}")
        claimed.add(pos)
        original = proposal["original"]
        replacement = proposal["replacement"]
        candidates = [clip for clip in v1_clips
                      if clip.get("spine_position") == pos
                      and clip.get("source_file") == original["source_file"]
                      and float(clip.get("source_in", -1)) == original["source_start"]
                      and float(clip.get("source_out", -1)) == original["source_end"]
                      and float(clip.get("timeline_in", -1)) == original["timeline_start"]
                      and float(clip.get("timeline_out", -1)) == original["timeline_end"]]
        if len(candidates) != 1:
            raise ReplacementPlanError(
                f"block {pos}: original picture span does not match exactly one V1 clip")
        old = candidates[0]
        audio = deepcopy(old)
        audio["label"] = f"speech_original_{pos}"
        audio["replacement_audio"] = True
        audio_additions.append(audio)

        old["source_file"] = replacement["source_file"]
        old["source_in"] = replacement["source_start"]
        old["source_out"] = replacement["source_end"]
        old["link_group_id"] = None
        old["video_only"] = True
        old["audio_preserving_replacement"] = True
        old["replacement_evidence"] = {
            "bad_picture": list(proposal["bad_picture_evidence_ids"]),
            "synchronization": list(proposal["sync_evidence_ids"]),
            "continuity": list(proposal["continuity"]["evidence_ids"]),
        }
        old["replacement_source_span"] = {
            "source_file": original["source_file"],
            "source_start": original["source_start"],
            "source_end": original["source_end"],
        }
        records.append({
            "spine_block_position": pos,
            "timeline_start": original["timeline_start"],
            "timeline_end": original["timeline_end"],
            "picture_source": replacement["source_file"],
            "audio_source": original["source_file"],
            "audio_source_start": original["source_start"],
            "audio_source_end": original["source_end"],
            "evidence": deepcopy(old["replacement_evidence"]),
        })
    return {"replacements": records, "speech_audio_clips": audio_additions}
