"""Global picture plan: selects shots against the whole edit.

The gap this closes
-------------------
A-roll assignment (step 3.01) is deterministic: it maps each speech block
to the video from the same source file and timestamps as the speech audio.
B-roll selection (step 3.02) is per-block LLM: for each block, it selects
one B-roll clip from candidates. Neither reasons globally about picture:
which shot covers which moment, which angle to use when, how to maintain
continuity across the cut, and how B-roll illustrates what is said.

This module is the global picture plan. It takes the whole edit (spine
blocks, clip catalog, semantic analysis, temporal event indices) and selects
shots for each block considering the whole edit:

- Gesture coverage: does the clip cover a gesture happening at that moment?
- Continuity: does the cut jump the subject across the frame?
- Variety: is the same camera/location over-used?
- Illustration: does the B-roll illustrate what is said?

The per-block B-roll selection becomes a constraint the global plan
satisfies, not the decision itself.

The model
---------
For each block, the plan considers all candidate clips (not just the
spine's clip reference) and scores each on:

    gesture_coverage  does the clip cover a gesture happening at that moment?
    continuity        does the cut jump the subject across the frame?
    variety           is the same camera/location over-used in the edit?
    illustration      does the B-roll illustrate what is said?

The best-scoring clip is selected for each block. The plan is global:
it considers all blocks at once, so variety is optimized across the whole
edit, not per-block.

A speech block's A-roll is the picture OF the speech - step 3.01 assigns
"the video from the same source file and timestamps as the speech
audio", and the timeline SOP keeps "a-roll picture and its speech ...
travel together, linked".  So for a speech block the plan may override
the spine's clip only with another shot that carries the speech - an
angle of the speaker, which is what the override is for.  A clip the
semantic pass measured no speech in is not an angle of the speaker;
it is B-roll, and B-roll belongs on its own track (step 3.02), never
on the A-roll track over a speech block.

This module imports nothing from steps or processes: it reads state
through the same readers every consumer has.
"""
from __future__ import annotations

import copy

from library.tools.semantic_index import build_semantic_lookup, clip_observations
from library.tools.spine_contract import (
    PICTURE_BLOCK_TYPES,
    SPEECH_BLOCK_TYPES,
)
from library.tools.vision_schema_adapter import adapt_semantic_document

GESTURE_KEYWORDS = frozenset({
    "gesture", "gestures", "gesturing",
    "point", "points", "pointing",
    "wave", "waves", "waving",
    "raise", "raises", "raising",
    "hand", "hands",
    "arm", "arms",
    "motion", "movement",
    "lean", "leans", "leaning",
    "turn", "turns", "turning",
    "nod", "nods", "nodding",
    "shake", "shakes", "shaking",
    "express", "expresses", "expressing",
    "emotion", "emotional",
    "reaction", "reacts", "reacting",
    "laugh", "laughs", "laughing",
    "smile", "smiles", "smiling",
    "frown", "frowns", "frowning",
    "surprise", "surprised", "surprising",
    "shock", "shocked",
    "excite", "excited", "exciting",
    "calm", "calms", "calming",
    "tense", "tenses", "tensing",
    "relax", "relaxes", "relaxing",
})

CONTINUITY_KEYWORDS = frozenset({
    "continuous", "continuity", "seamless",
    "smooth", "smoothly",
    "consistent", "consistency",
    "stable", "stability",
    "steady", "steadily",
    "static", "stationary",
    "fixed", "locked",
    "tripod", "mounted",
})

VARIETY_KEYWORDS = frozenset({
    "wide", "close", "medium",
    "establishing", "detail",
    "interior", "exterior",
    "indoor", "outdoor",
    "landscape", "portrait",
    "group", "solo",
    "action", "reaction",
})

ILLUSTRATION_KEYWORDS = frozenset({
    "illustrate", "illustrates", "illustration",
    "demonstrate", "demonstrates", "demonstration",
    "show", "shows", "showing",
    "explain", "explains", "explaining",
    "describe", "describes", "describing",
    "present", "presents", "presenting",
    "display", "displays", "displaying",
    "reveal", "reveals", "revealing",
    "highlight", "highlights", "highlighting",
    "emphasize", "emphasizes", "emphasizing",
})


def _as_dict(value) -> dict:
    return value if isinstance(value, dict) else {}


def _as_list(value) -> list:
    return value if isinstance(value, list) else []


def _overlap(start_a, end_a, start_b, end_b) -> bool:
    """Whether two half-open spans share any instant."""
    return start_a < end_b and start_b < end_a


def _keywords_in_text(text: str, keywords: frozenset) -> int:
    """Count how many keywords appear in the text."""
    if not text:
        return 0
    words = set(text.lower().split())
    return len(words & keywords)


def _gesture_score(semantic_doc: dict, block_source_start: float,
                   block_source_end: float) -> float:
    """Score how well a clip covers a gesture in the block's source range.

    Looks at the clip's semantic analysis blocks (derived from actions)
    for gesture keywords in the body_language field, weighted by how much
    of the block overlaps the block's source range.
    """
    from library.tools.vision_schema_adapter import adapt_semantic_document

    adapted = adapt_semantic_document(semantic_doc)
    blocks = _as_list(adapted.get("blocks"))
    if not blocks:
        return 0.0

    score = 0.0
    for block in blocks:
        if not isinstance(block, dict):
            continue
        block_start = block.get("start")
        block_end = block.get("end")
        if block_start is None or block_end is None:
            continue
        block_start = float(block_start)
        block_end = float(block_end)
        if not _overlap(block_start, block_end,
                        block_source_start, block_source_end):
            continue
        body_language = block.get("body_language") or ""
        visual = block.get("visual") or ""
        text = f"{body_language} {visual}"
        keyword_count = _keywords_in_text(text, GESTURE_KEYWORDS)
        if keyword_count > 0:
            overlap_start = max(block_start, block_source_start)
            overlap_end = min(block_end, block_source_end)
            overlap_fraction = (overlap_end - overlap_start) / (
                block_end - block_start)
            score += keyword_count * overlap_fraction
    return score


def _continuity_score(clip_analysis: dict) -> float:
    """Score how continuous/stable the clip is.

    Looks at the clip's stability and camera movement for continuity
    keywords. A stable, static shot scores higher for continuity.
    """
    stability = clip_analysis.get("stability") or ""
    movement = clip_analysis.get("movement") or ""
    text = f"{stability} {movement}"
    return float(_keywords_in_text(text, CONTINUITY_KEYWORDS))


def _variety_score(clip_analysis: dict, used_clips: set) -> float:
    """Score how much variety a clip adds to the edit.

    A clip that has not been used yet scores higher. A clip that has
    been used many times scores lower.
    """
    clip_id = clip_analysis.get("clip_id") or ""
    if not clip_id:
        return 0.0
    use_count = used_clips.get(clip_id, 0)
    return 1.0 / (1.0 + use_count)


def _illustration_score(clip_analysis: dict, block_text: str) -> float:
    """Score how well a clip illustrates what is said in the block.

    Looks at the clip's description and content for illustration
    keywords that match the block's text.
    """
    description = clip_analysis.get("description") or ""
    content_type = clip_analysis.get("content_type") or ""
    text = f"{description} {content_type}"
    clip_keywords = _keywords_in_text(text, ILLUSTRATION_KEYWORDS)
    block_keywords = _keywords_in_text(block_text, ILLUSTRATION_KEYWORDS)
    if clip_keywords == 0 or block_keywords == 0:
        return 0.0
    return float(min(clip_keywords, block_keywords))


def _block_text(block: dict, semantic: dict) -> str:
    """The text content of a block, for illustration matching."""
    parts = []
    clip_id = block.get("clip_id") or ""
    if clip_id and clip_id in semantic:
        doc = semantic[clip_id]
        observations = clip_observations(doc)
        parts.append(observations.get("description") or "")
        parts.append(observations.get("content_type") or "")
    for key in ("text", "transcript", "words"):
        value = block.get(key)
        if isinstance(value, str):
            parts.append(value)
    return " ".join(parts)


def _carries_speech(semantic_doc: dict) -> bool:
    """True when the semantic pass measured speech in this clip.

    `speech_present` is True-or-None-never-False (see
    library/tools/analysis/vision_pipeline_v3.py): True only on
    evidence - speech regions or a transcript - and None when nothing
    measured it.  Eligibility requires the evidence, never the absence
    of it: an unmeasured clip is not a clip the speech is in.
    """
    adapted = adapt_semantic_document(semantic_doc)
    assessment = adapted.get("assessment") or {}
    return assessment.get("speech_present") is True


def _candidate_clips(clip_catalog: list, block: dict,
                     semantic: dict, skip: set | None = None) -> list:
    """All clips that could cover this block, scored and sorted.

    A clip is a candidate if it has a semantic description and its
    source range overlaps the block's source range. The spine's own
    clip is always a candidate.  A clip whose own duration cannot hold
    the block's source range is not a candidate: the assignment step
    refuses a range that runs past the clip's end, so selecting one
    here would plan a block that cannot be cut.

    For a speech block, a clip other than the spine's own is a
    candidate only when it carries the speech (see `_carries_speech`):
    the A-roll over a speech block is the speaker's picture, so the
    plan's override may choose among the speaker's angles, never a
    B-roll cutaway - that is step 3.02's job, on its own track.
    """
    block_clip_id = block.get("clip_id") or ""
    block_source_end = float(block.get("source_end") or 0.0)
    speech_block = block.get("block_type") in SPEECH_BLOCK_TYPES

    candidates = []
    for clip in clip_catalog:
        if not isinstance(clip, dict):
            continue
        clip_id = clip.get("clip_id") or ""
        if not clip_id:
            continue
        if skip and clip_id in skip:
            continue
        if clip_id not in semantic:
            continue
        is_spine_clip = clip_id == block_clip_id
        if speech_block and not is_spine_clip \
                and not _carries_speech(semantic[clip_id]):
            continue
        observations = clip_observations(semantic[clip_id])
        if not observations.get("description"):
            continue
        duration = float(clip.get("duration_seconds") or 0.0)
        if duration + 0.5 < block_source_end:
            continue
        candidates.append({
            "clip_id": clip_id,
            "source_file": clip.get("source_file") or clip.get("path") or "",
            "duration_seconds": duration,
            "analysis": observations,
            "semantic_doc": semantic[clip_id],
            "is_spine_clip": is_spine_clip,
        })
    return candidates


def _score_candidate(candidate: dict, block: dict, semantic: dict,
                     used_clips: set, block_text: str) -> dict:
    """Score a candidate clip for a block."""
    analysis = candidate["analysis"]
    semantic_doc = candidate.get("semantic_doc") or {}
    block_source_start = float(block.get("source_start") or 0.0)
    block_source_end = float(block.get("source_end") or 0.0)

    gesture = _gesture_score(semantic_doc, block_source_start, block_source_end)
    continuity = _continuity_score(analysis)
    variety = _variety_score(analysis, used_clips)
    illustration = _illustration_score(analysis, block_text)

    # The spine's own clip gets a small bonus: it is the default choice
    # unless another clip scores higher on gesture coverage.
    spine_bonus = 0.1 if candidate["is_spine_clip"] else 0.0

    total = gesture + continuity + variety + illustration + spine_bonus

    return {
        "clip_id": candidate["clip_id"],
        "source_file": candidate["source_file"],
        "duration_seconds": candidate["duration_seconds"],
        "scores": {
            "gesture_coverage": round(gesture, 3),
            "continuity": round(continuity, 3),
            "variety": round(variety, 3),
            "illustration": round(illustration, 3),
            "spine_bonus": round(spine_bonus, 3),
            "total": round(total, 3),
        },
        "is_spine_clip": candidate["is_spine_clip"],
    }


def build_picture_plan(timed_spine: dict, clip_catalog: list,
                        semantic_docs: list,
                        duplicate_groups: list | None = None) -> dict:
    """Build a global picture plan for the whole edit.

    For each block in the spine, selects the best clip considering
    the whole edit: gesture coverage, continuity, variety, and
    illustration of the spoken content.

    A duplicate group is one take under different names: only its first
    clip is a candidate, so the plan never places two clips of one
    duplicate group as different takes.

    Returns a dict with:
        blocks: per-block shot selections
        summary: aggregate statistics
    """
    semantic = build_semantic_lookup(semantic_docs, clip_catalog)
    skip = set()
    for group in duplicate_groups or []:
        skip.update(group[1:])
    blocks = _as_list(timed_spine.get("structure"))

    used_clips: dict = {}
    block_plans = []

    for block in blocks:
        if not isinstance(block, dict):
            continue
        block_type = block.get("block_type") or ""
        if block_type not in SPEECH_BLOCK_TYPES + PICTURE_BLOCK_TYPES:
            continue

        block_text = _block_text(block, semantic)
        candidates = _candidate_clips(clip_catalog, block, semantic, skip)

        if not candidates:
            block_plans.append({
                "spine_block_position": block.get("position"),
                "block_type": block_type,
                "selected_clip_id": block.get("clip_id"),
                "candidates": [],
                "reason": "no candidates",
            })
            continue

        scored = [
            _score_candidate(c, block, semantic, used_clips, block_text)
            for c in candidates
        ]
        scored.sort(key=lambda s: s["scores"]["total"], reverse=True)

        best = scored[0]
        selected_clip_id = best["clip_id"]

        # Update used clips for variety scoring
        used_clips[selected_clip_id] = used_clips.get(selected_clip_id, 0) + 1

        block_plans.append({
            "spine_block_position": block.get("position"),
            "block_type": block_type,
            "selected_clip_id": selected_clip_id,
            "source_file": best["source_file"],
            "duration_seconds": best["duration_seconds"],
            "is_spine_clip": best["is_spine_clip"],
            "candidates": scored,
            "reason": (
                f"gesture={best['scores']['gesture_coverage']}, "
                f"continuity={best['scores']['continuity']}, "
                f"variety={best['scores']['variety']}, "
                f"illustration={best['scores']['illustration']}"
            ),
        })

    # Summary statistics
    total_blocks = len(block_plans)
    gesture_matched = sum(
        1 for b in block_plans
        if b.get("candidates") and b["candidates"][0]["scores"]["gesture_coverage"] > 0
    )
    spine_clip_used = sum(1 for b in block_plans if b.get("is_spine_clip"))
    unique_clips = len(set(
        b.get("selected_clip_id") for b in block_plans if b.get("selected_clip_id")
    ))

    return {
        "blocks": block_plans,
        "summary": {
            "total_blocks": total_blocks,
            "gesture_matched_blocks": gesture_matched,
            "spine_clip_used": spine_clip_used,
            "unique_clips_used": unique_clips,
        },
    }


def select_shot_for_block(block: dict, clip_catalog: list,
                          semantic_docs: list,
                          used_clips: set = None) -> dict:
    """Select the best shot for a single block, given the edit so far.

    This is the per-block entry point. It scores all candidates for
    this block and returns the best one. `used_clips` tracks which
    clips have already been selected for other blocks, so variety is
    maintained across the edit.
    """
    if used_clips is None:
        used_clips = set()

    semantic = build_semantic_lookup(semantic_docs, clip_catalog)
    block_text = _block_text(block, semantic)
    candidates = _candidate_clips(clip_catalog, block, semantic)

    if not candidates:
        return {
            "spine_block_position": block.get("position"),
            "selected_clip_id": block.get("clip_id"),
            "candidates": [],
            "reason": "no candidates",
        }

    scored = [
        _score_candidate(c, block, semantic, used_clips, block_text)
        for c in candidates
    ]
    scored.sort(key=lambda s: s["scores"]["total"], reverse=True)

    best = scored[0]
    return {
        "spine_block_position": block.get("position"),
        "block_type": block.get("block_type"),
        "selected_clip_id": best["clip_id"],
        "source_file": best["source_file"],
        "duration_seconds": best["duration_seconds"],
        "is_spine_clip": best["is_spine_clip"],
        "candidates": scored,
        "reason": (
            f"gesture={best['scores']['gesture_coverage']}, "
            f"continuity={best['scores']['continuity']}, "
            f"variety={best['scores']['variety']}, "
            f"illustration={best['scores']['illustration']}"
        ),
    }
