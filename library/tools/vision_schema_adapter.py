"""Adapt a v3 vision profile into the document shape consumers read.

`library/tools/analysis/vision_pipeline_v3.py` emits ``scene[]``,
``camera[]``, ``actions[]``, ``objects[]`` and ``assessment{}``.  Every
consumer - the ``context_fields`` allow-lists, ``semantic_index`` and the
B-roll bridges - addresses the retired shape: ``analysis.scene`` (a prose
string), ``blocks`` (time-resolved description) and legacy ``assessment``
keys.  Nothing joined the two, so the pipeline's richest observation of
the footage reached nobody: framing, camera stability, usable ranges and
subject visibility are all measured per time range and were unreadable.
Worse, feeding a v3 profile to step 3.02 fails the step outright, because
``describe_clip`` returns "" for every clip.

This module is that join.  It derives ONLY what the v3 observations
actually contain - every derived field below is a rendering or a
regrouping of something the analyser measured.  Fields the retired schema
had and v3 does not measure (``analysis.mood``, ``analysis.energy``,
``assessment.interest_score``, ``assessment.moment_type``) are deliberately
NOT invented: an absent field warns in `context_projector`, a fabricated
one silently misleads the model.

Legacy documents pass through untouched, so a project whose stored state
predates v3 keeps working.
"""

import math

_V3_VERSION = "v3"

# Content types the analyser reports for footage whose subject is speaking
# to camera. Everything else is treated as coverage footage.
_A_ROLL_CONTENT_TYPES = ("person_talking_to_camera", "interview", "monologue")


def is_v3_profile(doc) -> bool:
    """True when `doc` came from the v3 analyser.

    v3 makes ``scene`` a list of time-bounded segments; the retired schema
    made it a prose string nested under ``analysis``.
    """
    if not isinstance(doc, dict):
        return False
    meta = doc.get("analysis_metadata") or {}
    if isinstance(meta, dict) and meta.get("pipeline_version") == _V3_VERSION:
        return True
    return isinstance(doc.get("scene"), list) and isinstance(doc.get("camera"), list)


def _fmt_time(value) -> str:
    try:
        return f"{float(value):.1f}"
    except (TypeError, ValueError):
        return "?"


def _fmt_clock(value) -> str:
    """Seconds as ``M:SS``, the format the retired `blocks` schema used."""
    try:
        total = int(float(value))
    except (TypeError, ValueError):
        return "?"
    return f"{total // 60}:{total % 60:02d}"


def _fmt_range(start, end) -> str:
    return f"{_fmt_time(start)}-{_fmt_time(end)}s"


def _fmt_range_inward(start, end) -> str:
    """A range rounded inwards, so the rendering never claims footage.

    `_fmt_time` rounds to nearest, which reads a 3.567s clip back as
    "3.6s" - a tenth of a second that does not exist.  Rounding the end
    down and the start up keeps the displayed range inside the measured
    one, so a consumer that cuts to the printed bounds stays in range.

    Tenths are rounded to six places before the floor/ceil so binary
    representation alone cannot shave a tenth off an exact value
    (``45.9 * 10`` is ``458.99999999999994``).
    """
    try:
        low = math.ceil(round(float(start) * 10, 6)) / 10
        high = math.floor(round(float(end) * 10, 6)) / 10
    except (TypeError, ValueError):
        return _fmt_range(start, end)
    if low > high:
        low = high
    return f"{low:.1f}-{high:.1f}s"


def scene_prose(doc: dict) -> str:
    """The ``scene[]`` segments rendered as one time-bounded description.

    This is what `analysis.scene` used to hold, except that it described a
    single representative still ("The image shows...") while this describes
    the clip across its whole length.
    """
    lines = []
    for seg in doc.get("scene") or []:
        if not isinstance(seg, dict):
            continue
        head = f"[{_fmt_range(seg.get('start'), seg.get('end'))}]"
        parts = [p for p in (
            seg.get("location"),
            seg.get("type"),
            seg.get("lighting"),
        ) if p]
        features = [f for f in (seg.get("notable_features") or []) if f]
        if features:
            parts.append("notable: " + "; ".join(str(f) for f in features))
        lines.append(f"{head} " + ". ".join(str(p) for p in parts))
    return " ".join(lines).strip()


def camera_prose(doc: dict) -> str:
    """The ``camera[]`` segments rendered as one time-bounded description.

    Framing and stability per time range are the fields the audit found
    measured-but-unreachable; this is the string form that reaches steps
    still reading `analysis.motion`.
    """
    lines = []
    for seg in doc.get("camera") or []:
        if not isinstance(seg, dict):
            continue
        parts = [p for p in (
            seg.get("framing") and f"{seg['framing']} framing",
            seg.get("mode"),
            seg.get("stability"),
            seg.get("movement"),
        ) if p]
        lines.append(
            f"[{_fmt_range(seg.get('start'), seg.get('end'))}] "
            + ", ".join(str(p) for p in parts)
        )
    return " ".join(lines).strip()


def _blocks_from_actions(doc: dict) -> list:
    """One `blocks` entry per observed action, with its own time bounds.

    The retired schema carried 1-3 blocks for clips up to 188s; the v3
    action windows are the same information at the resolution it was
    actually measured.
    """
    blocks = []
    for window in doc.get("actions") or []:
        if not isinstance(window, dict):
            continue
        for action in window.get("actions") or []:
            if not isinstance(action, dict):
                continue
            start = action.get("start", (window.get("window") or [None])[0])
            end = action.get("end")
            blocks.append({
                "timestamp_range": f"{_fmt_clock(start)}-{_fmt_clock(end)}",
                "start": start,
                "end": end,
                "label": _scene_location_at(doc, start) or "action",
                "visual": action.get("action") or "",
                "body_language": action.get("body_language") or "",
                "speech_cue": action.get("speech_cue"),
            })
    return blocks


def _scene_location_at(doc: dict, time_s):
    """Location of the scene segment covering `time_s`, if any."""
    if time_s is None:
        return None
    for seg in doc.get("scene") or []:
        if not isinstance(seg, dict):
            continue
        start, end = seg.get("start"), seg.get("end")
        if start is None or end is None:
            continue
        if start <= time_s <= end:
            return seg.get("location")
    return None


def derived_keywords(doc: dict) -> list:
    """Tags implied by the observations - not a new judgement.

    Drawn from scene types, object categories and roles, camera framings
    and the assessed content type. Object *labels* are deliberately left
    out: they are prose sentences, not tags.
    """
    keywords = set()
    for seg in doc.get("scene") or []:
        if isinstance(seg, dict) and seg.get("type"):
            keywords.add(str(seg["type"]).lower())
    for seg in doc.get("camera") or []:
        if isinstance(seg, dict):
            for key in ("framing", "mode", "movement"):
                if seg.get(key):
                    keywords.add(str(seg[key]).lower())
    for obj in doc.get("objects") or []:
        if isinstance(obj, dict):
            for key in ("category", "role"):
                if obj.get(key):
                    keywords.add(str(obj[key]).lower())
    assessment = doc.get("assessment") or {}
    if assessment.get("content_type"):
        keywords.add(str(assessment["content_type"]).lower())
    return sorted(keywords)


def framing_summary(doc: dict) -> str:
    """Every framing the clip uses, in the order it uses them."""
    seen = []
    for seg in doc.get("camera") or []:
        if isinstance(seg, dict) and seg.get("framing") and seg["framing"] not in seen:
            seen.append(seg["framing"])
    return " -> ".join(str(f) for f in seen)


def stability_summary(doc: dict) -> str:
    """Clip-level stability, preferring the assessment's own verdict."""
    assessment = doc.get("assessment") or {}
    if assessment.get("camera_stability"):
        return str(assessment["camera_stability"])
    seen = []
    for seg in doc.get("camera") or []:
        if isinstance(seg, dict) and seg.get("stability") and seg["stability"] not in seen:
            seen.append(seg["stability"])
    return " -> ".join(str(s) for s in seen)


def movement_summary(doc: dict) -> str:
    """Every camera movement the clip uses, in order."""
    seen = []
    for seg in doc.get("camera") or []:
        if isinstance(seg, dict) and seg.get("movement") and seg["movement"] not in seen:
            seen.append(seg["movement"])
    return " -> ".join(str(m) for m in seen)


def format_ranges(ranges) -> str:
    """``[[0, 45.9]]`` as ``0.0-45.9s``.

    Used for the usable/unusable ranges the assessment reports, which are
    bounds a consumer cuts against - so they are rounded inwards rather
    than to nearest.
    """
    if not isinstance(ranges, list):
        return ""
    out = []
    for entry in ranges:
        if isinstance(entry, (list, tuple)) and len(entry) >= 2:
            out.append(_fmt_range_inward(entry[0], entry[1]))
        elif isinstance(entry, dict) and "start" in entry and "end" in entry:
            out.append(_fmt_range_inward(entry["start"], entry["end"]))
    return ", ".join(out)


def subject_summary(doc: dict, limit: int = 4) -> str:
    """Who or what is in shot, primary subject first."""
    primary, other = [], []
    for obj in doc.get("objects") or []:
        if not isinstance(obj, dict) or not obj.get("label"):
            continue
        (primary if obj.get("role") == "primary_subject" else other).append(
            str(obj["label"]))
    picked = (primary + other)[:limit]
    return "; ".join(picked)


def _derived_clip_type(assessment: dict) -> str:
    """`a_roll` when the analyser saw someone speaking to camera.

    A rendering of `content_type`, not a new judgement - the retired
    schema's `clip_type` carried exactly this distinction.
    """
    content_type = str(assessment.get("content_type") or "").lower()
    if content_type in _A_ROLL_CONTENT_TYPES:
        return "a_roll"
    return "b_roll"


def adapt_semantic_document(doc: dict) -> dict:
    """Return `doc` in the shape every consumer reads.

    v3 fields are preserved verbatim and the retired-schema view is
    derived alongside them, so a step may address either. Anything that is
    not a v3 profile is returned unchanged.
    """
    if not is_v3_profile(doc):
        return doc

    adapted = dict(doc)
    assessment = dict(doc.get("assessment") or {})

    analysis = dict(doc.get("analysis") or {})
    analysis.setdefault("scene", scene_prose(doc))
    analysis.setdefault("motion", camera_prose(doc))
    adapted["analysis"] = analysis

    if not adapted.get("blocks"):
        adapted["blocks"] = _blocks_from_actions(doc)

    assessment.setdefault("clip_type", _derived_clip_type(assessment))
    assessment.setdefault("keywords", derived_keywords(doc))
    usable = format_ranges(assessment.get("usable_ranges"))
    if usable:
        assessment.setdefault("usable_portions", usable)
    adapted["assessment"] = assessment

    adapted["vision_schema_version"] = _V3_VERSION
    return adapted


def adapt_semantic_documents(docs):
    """Adapt a list of documents, leaving non-v3 entries alone."""
    if not isinstance(docs, list):
        return docs
    return [adapt_semantic_document(d) if isinstance(d, dict) else d for d in docs]
