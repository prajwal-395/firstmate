"""Cut a project's ingest output into the units a footage search retrieves.

PROTOTYPE - nothing in the DAG imports this.  See
`docs/FOOTAGE_INDEX_PROTOTYPE.md` for what it is for and what it measured.

The central design question a cross-clip footage index has to answer is
what the UNIT of retrieval is.  This module is that answer, isolated from
the embedding and the search so it can be read and tested on its own.

There is no single right unit, so this does not invent one.  Each ingest
source is cut at the finest boundary IT actually measured, and the kinds
sit side by side in one corpus:

======== ======================== ===================== =================
kind     boundary                 source                typical span
======== ======================== ===================== =================
speech   one WhisperX utterance   1.04 speech_regions   1 - 8 s
action   one vision action window 1.03 actions[]        ~10 s
scene    one scene observation    1.03 scene[]          clip or part
camera   one camera observation   1.03 camera[]         clip or part
object   one object appearance    1.03 objects[]        seconds to clip
======== ======================== ===================== =================

**Retrieve at the utterance, snap at the word.**  A speech segment keeps
its word timestamps, so a hit is reported at utterance granularity and
can then be trimmed to the word that matched.  Indexing single words
instead would destroy retrieval - a lone "the" embeds to nothing - while
still leaving the caller to reassemble a sentence.  The word boundary is
the pipeline's finest unit and this is how it survives.

The dense 30 Hz / 5 Hz curves from step 1.04 are NOT cut into segments.
They are REDUCED over each segment's span into facets (how much speech,
how much motion, how present the face is, how bright).  A curve is a
thing to filter by, never a thing to retrieve.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path

# Every kind this module emits.  A kind not in here does not exist, and
# asking to filter by one raises rather than quietly matching nothing.
SEGMENT_KINDS = ("speech", "action", "scene", "camera", "object")

# Facets reduced off the step 1.04 curves, and the key each is read from.
# The value stored is the MEAN over the segment's span.
_CURVE_FACETS = {
    "speech_ratio": ("speech_activity", "values"),
    "motion": ("motion_energy", "values"),
    "face_presence": ("face_presence", "values"),
    "brightness": ("color_curves", "brightness_values"),
    "saturation": ("color_curves", "saturation_values"),
}


@dataclass
class Segment:
    """One retrievable span of one clip."""

    segment_id: str
    clip_id: str
    filename: str
    source_file: str
    kind: str
    start: float
    end: float
    text: str
    facets: dict = field(default_factory=dict)
    words: list = field(default_factory=list)

    @property
    def duration(self) -> float:
        return round(self.end - self.start, 3)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["duration"] = self.duration
        return d


# ─── Reading the ingest ───────────────────────────────────────────


def _load_json(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def _steps_root(project_folder) -> Path:
    return Path(project_folder) / "pipeline_output" / "steps"


def load_catalog(project_folder) -> list:
    """The clip catalog, preferring pipeline_data.json over the export.

    §10.1: the per-step JSON files are a best-effort dashboard export and
    a missing one reads as ``{}``.  The state file is the record.
    """
    state = _load_json(Path(project_folder) / "pipeline_data.json") or {}
    catalog = (state.get("step_outputs", {}).get("catalog") or {}).get("clip_catalog")
    if catalog:
        return catalog
    exported = _load_json(_steps_root(project_folder) / "1_02_catalog_footage" / "output.json") or {}
    return exported.get("clip_catalog") or []


def load_temporal_index(project_folder) -> dict:
    """``{clip_id: per-clip 1.04 document}``, read off the per-clip files."""
    out = {}
    index_dir = _steps_root(project_folder) / "1_04_temporal_index" / "index"
    if not index_dir.is_dir():
        return out
    for path in sorted(index_dir.glob("clip_*.json")):
        doc = _load_json(path)
        if isinstance(doc, dict) and doc.get("clip_id"):
            out[doc["clip_id"]] = doc
    return out


def load_vision_profiles(project_folder, catalog: list) -> dict:
    """``{clip_id: v3 vision profile}``.

    Step 1.03 keys its documents by FILE STEM and the catalog by
    ``clip_XXX`` (§10.1), so the join goes through the file path.  A
    project that has been analysed more than once carries ``*__2.json``
    re-runs of the same clip; the newest file wins.
    """
    by_stem = {}
    for clip in catalog:
        path = clip.get("source_file") or clip.get("path") or ""
        stem = os.path.splitext(os.path.basename(path))[0].lower()
        if stem:
            by_stem[stem] = clip.get("clip_id")

    out, seen_mtime = {}, {}
    vision_dir = _steps_root(project_folder) / "1_03_semantic_analysis"
    if not vision_dir.is_dir():
        return out
    for path in sorted(vision_dir.glob("clip_profile_*.json")):
        doc = _load_json(path)
        if not isinstance(doc, dict):
            continue
        doc_path = doc.get("file_path") or ""
        stem = os.path.splitext(os.path.basename(doc_path))[0].lower()
        clip_id = by_stem.get(stem)
        if not clip_id:
            continue
        mtime = path.stat().st_mtime
        if clip_id in seen_mtime and seen_mtime[clip_id] >= mtime:
            continue
        seen_mtime[clip_id] = mtime
        out[clip_id] = doc
    return out


# ─── Reducing the curves ──────────────────────────────────────────


def _curve_mean(doc: dict, group: str, key: str, start: float, end: float):
    """Mean of one 1.04 curve over ``[start, end)``, or None.

    The curve carries its own ``sample_rate_hz``; reading it off the
    document rather than assuming 30 is what lets 5 Hz and 1 Hz curves
    share this code.
    """
    block = doc.get(group)
    if not isinstance(block, dict):
        return None
    values = block.get(key)
    rate = block.get("sample_rate_hz")
    if not values or not rate:
        return None
    lo = max(0, int(start * rate))
    hi = min(len(values), max(lo + 1, int(end * rate)))
    window = [v for v in values[lo:hi] if isinstance(v, (int, float))]
    if not window:
        return None
    return round(sum(window) / len(window), 4)


def curve_facets(temporal_doc, start: float, end: float) -> dict:
    """Every curve facet for one span.  Absent curves are simply absent.

    A facet that could not be measured is LEFT OUT rather than defaulted:
    "no face curve" and "no face" are different answers, and a filter
    must be able to tell them apart.
    """
    if not isinstance(temporal_doc, dict):
        return {}
    facets = {}
    for name, (group, key) in _CURVE_FACETS.items():
        value = _curve_mean(temporal_doc, group, key, start, end)
        if value is not None:
            facets[name] = value
    return facets


def _vision_facets(vision_doc) -> dict:
    """Clip-level vision facts every segment of that clip inherits.

    Framing, stability and camera mode describe the SHOT, so they belong
    on each of its segments even when they were observed once per clip.
    """
    if not isinstance(vision_doc, dict):
        return {}
    facets = {}
    camera = (vision_doc.get("camera") or [{}])[0]
    for src, dst in (("framing", "framing"), ("mode", "camera_mode"),
                     ("stability", "stability"), ("movement", "movement")):
        if camera.get(src):
            facets[dst] = camera[src]
    scene = (vision_doc.get("scene") or [{}])[0]
    for src, dst in (("type", "scene_type"), ("lighting", "lighting")):
        if scene.get(src):
            facets[dst] = scene[src]
    assessment = vision_doc.get("assessment") or {}
    if assessment.get("content_type"):
        facets["content_type"] = assessment["content_type"]
    return facets


# ─── Cutting each source ──────────────────────────────────────────


def _clean(value) -> str:
    return " ".join(str(value or "").split())


def _speech_segments(clip, temporal_doc, base_facets) -> list:
    out = []
    for i, region in enumerate(temporal_doc.get("speech_regions") or []):
        text = _clean(region.get("text"))
        if not text:
            continue
        start = float(region.get("start", 0.0))
        end = float(region.get("end", start))
        words = [
            {"word": w.get("word", ""), "start": w.get("start"), "end": w.get("end")}
            for w in region.get("words") or []
            if isinstance(w, dict)
        ]
        out.append(Segment(
            segment_id=f"{clip['clip_id']}#speech#{i:03d}",
            clip_id=clip["clip_id"],
            filename=clip.get("filename", ""),
            source_file=clip.get("source_file") or clip.get("path", ""),
            kind="speech",
            start=round(start, 3),
            end=round(end, 3),
            text=text,
            facets={**base_facets, "has_speech": True,
                    **curve_facets(temporal_doc, start, end)},
            words=words,
        ))
    return out


def _action_segments(clip, vision_doc, temporal_doc, base_facets) -> list:
    """One segment per action the analyser reported.

    An action window carries both what happened and the subject's body
    language, and both are embedded: body language is the only place in
    the whole ingest that records an expression or a gesture.
    """
    out, n = [], 0
    for window in vision_doc.get("actions") or []:
        for action in window.get("actions") or []:
            described = _clean(action.get("action"))
            body = _clean(action.get("body_language"))
            cue = _clean(action.get("speech_cue"))
            text = " ".join(p for p in (described, body, cue) if p and p != "None")
            if not text:
                continue
            span = window.get("window") or [action.get("start"), action.get("end")]
            start = float(action.get("start", span[0] or 0.0))
            end = float(action.get("end", span[1] or start))
            out.append(Segment(
                segment_id=f"{clip['clip_id']}#action#{n:03d}",
                clip_id=clip["clip_id"],
                filename=clip.get("filename", ""),
                source_file=clip.get("source_file") or clip.get("path", ""),
                kind="action",
                start=round(start, 3),
                end=round(end, 3),
                text=text,
                facets={**base_facets, **curve_facets(temporal_doc, start, end)},
            ))
            n += 1
    return out


def _scene_segments(clip, vision_doc, temporal_doc, base_facets) -> list:
    out = []
    for i, scene in enumerate(vision_doc.get("scene") or []):
        parts = [_clean(scene.get("location")), _clean(scene.get("type")),
                 _clean(scene.get("lighting"))]
        parts += [_clean(f) for f in scene.get("notable_features") or []]
        text = ". ".join(p for p in parts if p)
        if not text:
            continue
        start = float(scene.get("start", 0.0))
        end = float(scene.get("end", start))
        out.append(Segment(
            segment_id=f"{clip['clip_id']}#scene#{i:03d}",
            clip_id=clip["clip_id"],
            filename=clip.get("filename", ""),
            source_file=clip.get("source_file") or clip.get("path", ""),
            kind="scene",
            start=round(start, 3),
            end=round(end, 3),
            text=text,
            facets={**base_facets, **curve_facets(temporal_doc, start, end)},
        ))
    return out


def _camera_segments(clip, vision_doc, temporal_doc, base_facets) -> list:
    out = []
    for i, cam in enumerate(vision_doc.get("camera") or []):
        parts = [_clean(cam.get("framing")), _clean(cam.get("mode")),
                 _clean(cam.get("movement")), _clean(cam.get("stability"))]
        text = " ".join(p for p in parts if p)
        if not text:
            continue
        start = float(cam.get("start", 0.0))
        end = float(cam.get("end", start))
        out.append(Segment(
            segment_id=f"{clip['clip_id']}#camera#{i:03d}",
            clip_id=clip["clip_id"],
            filename=clip.get("filename", ""),
            source_file=clip.get("source_file") or clip.get("path", ""),
            kind="camera",
            start=round(start, 3),
            end=round(end, 3),
            text=f"{text} shot",
            facets={**base_facets, **curve_facets(temporal_doc, start, end)},
        ))
    return out


def _object_segments(clip, vision_doc, temporal_doc, base_facets) -> list:
    """One segment per APPEARANCE, not per object.

    An object seen twice in a clip is two answers to "where is the X",
    and collapsing them to one loses the second timecode.
    """
    out, n = [], 0
    for obj in vision_doc.get("objects") or []:
        label = _clean(obj.get("label"))
        if not label:
            continue
        parts = [label, _clean(obj.get("category")), _clean(obj.get("role")),
                 _clean(obj.get("readable_text"))]
        text = ". ".join(p for p in parts if p and p != "None")
        appearances = obj.get("appearances") or []
        if not appearances:
            appearances = [[0.0, float(clip.get("duration_seconds", 0.0))]]
        for span in appearances:
            try:
                start, end = float(span[0]), float(span[1])
            except (TypeError, ValueError, IndexError):
                continue
            out.append(Segment(
                segment_id=f"{clip['clip_id']}#object#{n:03d}",
                clip_id=clip["clip_id"],
                filename=clip.get("filename", ""),
                source_file=clip.get("source_file") or clip.get("path", ""),
                kind="object",
                start=round(start, 3),
                end=round(end, 3),
                text=text,
                facets={**base_facets, **curve_facets(temporal_doc, start, end)},
            ))
            n += 1
    return out


# ─── The whole project ────────────────────────────────────────────


def build_segments(project_folder, kinds=SEGMENT_KINDS) -> list:
    """Every retrievable segment of every clip, in clip then time order.

    Reads only.  Nothing under `project_folder` is written or modified.
    """
    unknown = [k for k in kinds if k not in SEGMENT_KINDS]
    if unknown:
        raise ValueError(
            f"Unknown segment kind(s): {unknown}. Known kinds: {list(SEGMENT_KINDS)}"
        )

    catalog = load_catalog(project_folder)
    temporal = load_temporal_index(project_folder)
    vision = load_vision_profiles(project_folder, catalog)

    cutters = {
        "speech": lambda c, v, t, b: _speech_segments(c, t, b),
        "action": _action_segments,
        "scene": _scene_segments,
        "camera": _camera_segments,
        "object": _object_segments,
    }

    segments = []
    for clip in catalog:
        clip_id = clip.get("clip_id")
        if not clip_id:
            continue
        temporal_doc = temporal.get(clip_id) or {}
        vision_doc = vision.get(clip_id) or {}
        base = _vision_facets(vision_doc)
        base["duration_seconds"] = clip.get("duration_seconds")
        for kind in kinds:
            if kind == "speech":
                segments.extend(cutters[kind](clip, None, temporal_doc, base))
            else:
                segments.extend(cutters[kind](clip, vision_doc, temporal_doc, base))
    return segments


def coverage_report(project_folder) -> dict:
    """What the ingest actually produced, per source, before any search.

    Answers "is there anything to index" honestly: a source that measured
    nothing is reported as zero rather than as an empty success.
    """
    catalog = load_catalog(project_folder)
    temporal = load_temporal_index(project_folder)
    vision = load_vision_profiles(project_folder, catalog)

    prosody_dir = _steps_root(project_folder) / "1_05_prosody_analysis"
    prosody_measured = 0
    for path in sorted(prosody_dir.glob("clip_*_prosody.json")) if prosody_dir.is_dir() else []:
        doc = _load_json(path) or {}
        if (doc.get("prosody") or {}).get("method"):
            prosody_measured += 1

    return {
        "clips_in_catalog": len(catalog),
        "clips_with_temporal_index": len(temporal),
        "clips_with_vision_profile": len(vision),
        "clips_with_measured_prosody": prosody_measured,
        "utterances": sum(len(d.get("speech_regions") or []) for d in temporal.values()),
        "words": sum(
            len(r.get("words") or [])
            for d in temporal.values()
            for r in d.get("speech_regions") or []
        ),
        "vision_action_windows": sum(len(d.get("actions") or []) for d in vision.values()),
        "vision_scene_observations": sum(len(d.get("scene") or []) for d in vision.values()),
        "vision_object_labels": sum(len(d.get("objects") or []) for d in vision.values()),
    }
