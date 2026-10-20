"""Cut a project's ingest output into the units a footage search retrieves.

Part of the footage-search capability (`footage_query.py`), not a pipeline
stage.  See `docs/FOOTAGE_INDEX_PROTOTYPE.md` for what it measured.

The central design question a cross-clip footage index has to answer is
what the UNIT of retrieval is.  This module is that answer, isolated from
the embedding and the search so it can be read and tested on its own.

There is no single right unit, so this does not invent one.  Each ingest
source is cut at the finest boundary IT actually measured, and the kinds
sit side by side in one corpus:

======== ======================== ===================== =================
kind     boundary                 source                typical span
======== ======================== ===================== =================
 speech   one whole-source         source memory M1 when   1 - 8 s
         utterance                fresh, else 1.04
                                  speech_regions
action   one vision action window 1.03 actions[]        ~10 s
scene    one scene observation    1.03 scene[]          clip or part
camera   one run of constant      1.04 optical_flow_    0.2 s - clip
         direction at 5 Hz        direction.values[],
                                  enriched by 1.03 camera[]
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

The camera movement CLASSIFICATION is the exception.  It is a per-sample
label, not a numeric curve, and its runs of constant direction are cut
into ``camera`` segments at the 5 Hz boundary the measurement actually
made - the deterministic measurement is the primary one, enriched by the
VLM's per-window description where they overlap (Gap 13).
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path

# The per-source memory (`library/tools/source_memory.py`) is a tools
# module, not a step: importing it here does not make search a pipeline
# stage any more than importing footage_identity would.
from library.tools import source_memory
from library.tools import source_quality
from library.tools import footage_model_link as model_link

# Every kind this module emits.  A kind not in here does not exist, and
# asking to filter by one raises rather than quietly matching nothing.
SEGMENT_KINDS = ("speech", "action", "scene", "camera", "object", "chunk")

# The unified-search chunk unit: fixed 8 s windows, one video embedding
# per window (slice-3 measurement: 8 s at 1 fps beats single frames and
# shorter prefixes on top-1 correctness; 2 fps is the opt-in for hard
# queries).  A chunk row joins the texts of the fine rows overlapping
# its window, so every span of footage is retrievable at a coarse
# granularity even where no fine row was cut.
CHUNK_S = 8.0

# Text for a chunk window no measurement overlaps.  It says what the
# row is - footage nothing described - rather than leaving the row
# textless: an empty string embeds to an arbitrary vector that could
# clear a floor for no reason.
UNMEASURED_CHUNK_TEXT = "unmeasured footage"

# Facets reduced off the step 1.04 curves, and the key each is read from.
# The value stored is the MEAN over the segment's span.
#
# The framing facets are NOT here: the subject's position and width are
# read by `library/tools/subject_framing.py`, which owns that measurement
# and decides which samples count. `library/tools/source_quality.py` is
# the one place both readings meet, so the index and the crop that
# follows it cannot disagree about where the subject is.
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
    # Lane B (cascade report 2.1): the segment's foreign keys into the
    # Project Model. `asset_id` + the source-clock interval locate the
    # span on the model's time; `obs_id` (speech rows from a fresh M1
    # transcript only) resolves to the utterance Observation the
    # migration mints. Absent when the digest or transcript is
    # unresolvable - honestly unlinked, never guessed.
    asset_id: str | None = None
    clock_id: str | None = None
    start_tick: int | None = None
    end_tick: int | None = None
    obs_id: str | None = None
    # The source-quality profile of this span: the one named aggregate
    # over the measured quality evidence for it. `library/tools/
    # source_quality.py` owns its shape and its dimensions; the flat
    # `facets` above are published from it, so the two cannot disagree.
    quality_profile: dict = field(default_factory=dict)

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
    from library.tools import capability_outputs
    catalog = capability_outputs.value(state, "footage.catalog", "clip_catalog")
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


def _deterministic_facets(temporal_doc) -> dict:
    """Clip-level deterministic camera facts, when step 1.04 measured them.

    ``dominant_motion`` is the clip-level optical-flow classification, in
    the same vocabulary the per-run ``movement`` facet uses at 5 Hz.  It is
    absent when unmeasured: "no flow" and "no dominant motion" are
    different answers, and a filter must be able to tell them apart.
    """
    if not isinstance(temporal_doc, dict):
        return {}
    flow = temporal_doc.get("optical_flow_direction") or {}
    dominant = flow.get("dominant_motion")
    if not dominant or dominant == "unknown":
        return {}
    return {"dominant_motion": dominant}


# ─── Cutting each source ──────────────────────────────────────────


def _clean(value) -> str:
    return " ".join(str(value or "").split())


def _linkage(start: float, end: float,
             digest: str | None) -> dict:
    """A segment's model FKs, or all-None when the digest is absent."""
    if digest is None:
        return {"asset_id": None, "clock_id": None,
                "start_tick": None, "end_tick": None}
    ticks = model_link.sec_to_ticks(start, end)
    return {"asset_id": model_link.asset_id_for_digest(digest),
            "clock_id": model_link.clock_id_for_digest(digest),
            "start_tick": ticks[0] if ticks else None,
            "end_tick": ticks[1] if ticks else None}


def _speech_segments(clip, temporal_doc, base_facets,
                     digest: str | None = None) -> list:
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
                    "transcript_source": "temporal-index",
                    **curve_facets(temporal_doc, start, end)},
            words=words,
            # 1.04 regions are not M1 utterances: no utterance
            # Observation exists for them, so no obs_id is minted.
            **_linkage(start, end, digest),
        ))
    return out


def _memory_speech_segments(clip, m1_doc, temporal_doc, base_facets,
                            digest: str | None = None) -> list:
    """One segment per whole-source memory utterance.

    The same id namespace and region keys as `_speech_segments` - an
    M1 utterance and a 1.04 region are interchangeable where the
    search reads them - with `transcript_source` saying which
    instrument's words these are. Curve facets still come off the
    1.04 document when one exists: they are measurements of the same
    clip, not of the transcript.
    """
    out = []
    for i, utterance in enumerate(m1_doc.get("utterances") or []):
        text = _clean(utterance.get("text"))
        if not text:
            continue
        try:
            start = float(utterance.get("start", 0.0))
            end = float(utterance.get("end", start))
        except (TypeError, ValueError):
            continue
        words = [
            {"word": w.get("word", ""), "start": w.get("start"), "end": w.get("end")}
            for w in utterance.get("words") or []
            if isinstance(w, dict)
        ]
        if not words:
            continue
        row = Segment(
            segment_id=f"{clip['clip_id']}#speech#{i:03d}",
            clip_id=clip["clip_id"],
            filename=clip.get("filename", ""),
            source_file=clip.get("source_file") or clip.get("path", ""),
            kind="speech",
            start=round(start, 3),
            end=round(end, 3),
            text=text,
            facets={**base_facets, "has_speech": True,
                    "transcript_source": "source-memory",
                    **curve_facets(temporal_doc, start, end)},
            words=words,
            **_linkage(start, end, digest),
        )
        if digest is not None:
            row.obs_id = model_link.utterance_obs_id(digest, i)
        out.append(row)
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


# Which signal produced a segment's ``movement`` facet.  A method travels
# with the number it qualifies, so a query can tell a deterministic span
# from a VLM-described one.
FLOW_MOVEMENT_METHOD = "optical_flow"
VLM_MOVEMENT_METHOD = "vision_vlm"

# Human-readable rendering of one per-sample direction, for the segment
# TEXT (lexical search).  The ``movement`` facet keeps the verbatim
# measurement; this only words it for a person reading the hit.
_DIRECTION_TEXT = {
    "right": "panning right",
    "up-right": "panning up-right",
    "up": "tilting up",
    "up-left": "panning up-left",
    "left": "panning left",
    "down-left": "panning down-left",
    "down": "tilting down",
    "down-right": "panning down-right",
    "static": "static",
    "mixed": "mixed motion",
}


def _flow_runs(temporal_doc) -> list:
    """Runs of constant direction in the 5 Hz optical-flow series.

    Step 1.04 writes one classified sample per frame pair.  A run is a
    maximal stretch of consecutive samples with the SAME direction and no
    time gap: an unmeasured pair leaves a gap, and a gap is not evidence
    the direction held across it.  Each run is the finest boundary the
    deterministic camera-movement measurement made, so each run is one
    segment's span.
    """
    flow = temporal_doc.get("optical_flow_direction") or {}
    values = flow.get("values") or []
    rate = flow.get("sample_rate_hz") or 5
    period = 1.0 / rate
    runs = []
    for sample in values:
        if not isinstance(sample, dict):
            continue
        direction = sample.get("direction")
        time = sample.get("time")
        if not direction or not isinstance(time, (int, float)):
            continue
        method = sample.get("method") or "unmeasured"
        if (runs and runs[-1]["direction"] == direction
                and abs(time - runs[-1]["last_time"] - period) < 1e-6):
            runs[-1]["last_time"] = time
            runs[-1]["methods"].add(method)
        else:
            runs.append({"direction": direction, "first_time": time,
                         "last_time": time, "methods": {method}})
    out = []
    for run in runs:
        methods = run["methods"]
        # The same name step 1.04 gives the clip-level method, so a run and
        # its clip read as one vocabulary.
        if methods == {"farneback"}:
            flow_method = "farneback"
        elif methods == {"block_match"}:
            flow_method = "block_match"
        else:
            flow_method = "farneback+block_match"
        out.append({
            "direction": run["direction"],
            # A sample at time t covers the interval ending at t, so the
            # run starts one period before its first sample.
            "start": round(max(0.0, run["first_time"] - period), 3),
            "end": round(run["last_time"], 3),
            "flow_method": flow_method,
        })
    return out


def _overlapping_vlm(windows, start: float, end: float):
    """The VLM camera window with the largest overlap of ``[start, end]``.

    The VLM describes the shot at 10 s window granularity; a deterministic
    run is enriched by the window it mostly falls in, never by merging
    windows the run only touches.
    """
    best, best_overlap = None, 0.0
    for window in windows:
        if not isinstance(window, dict):
            continue
        try:
            w_start = float(window.get("start", 0.0))
            w_end = float(window.get("end", w_start))
        except (TypeError, ValueError):
            continue
        overlap = min(end, w_end) - max(start, w_start)
        if overlap > best_overlap:
            best, best_overlap = window, overlap
    return best


def _camera_segments(clip, vision_doc, temporal_doc, base_facets) -> list:
    """Camera movement, primarily the deterministic 5 Hz measurement.

    Step 1.04 classifies camera movement per frame pair at 5 Hz
    (``optical_flow_direction.values[].direction``).  Its runs of
    constant direction are the finest boundary that measurement made, so
    they are the ``camera`` segments - the deterministic measurement is the
    primary one (Gap 13).  The VLM's per-window description enriches each
    run where they overlap.  A clip with no optical flow measurement falls
    back to the VLM windows alone, and says so via ``movement_method``.
    """
    runs = _flow_runs(temporal_doc)
    if runs:
        windows = [w for w in (vision_doc.get("camera") or [])
                   if isinstance(w, dict)]
        out = []
        for i, run in enumerate(runs):
            start, end = run["start"], run["end"]
            words = _DIRECTION_TEXT.get(run["direction"],
                                        run["direction"])
            vlm = _overlapping_vlm(windows, start, end)
            facets = {
                **base_facets,
                "movement": run["direction"],
                "movement_method": FLOW_MOVEMENT_METHOD,
                "flow_method": run["flow_method"],
                **curve_facets(temporal_doc, start, end),
            }
            text = f"{words} shot"
            if vlm:
                facets.update({
                    "vlm_mode": vlm.get("mode"),
                    "vlm_framing": vlm.get("framing"),
                    "vlm_stability": vlm.get("stability"),
                    "vlm_movement": vlm.get("movement"),
                })
                enrichment = " ".join(
                    _clean(vlm.get(k)) for k in ("framing", "mode",
                                                 "movement", "stability"))
                if enrichment:
                    text = f"{text} - {enrichment}"
            out.append(Segment(
                segment_id=f"{clip['clip_id']}#camera#{i:03d}",
                clip_id=clip["clip_id"],
                filename=clip.get("filename", ""),
                source_file=clip.get("source_file") or clip.get("path", ""),
                kind="camera",
                start=start,
                end=end,
                text=text,
                facets=facets,
            ))
        return out

    # No deterministic measurement: the VLM windows are all there is.
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
            facets={**base_facets, "movement_method": VLM_MOVEMENT_METHOD,
                    **curve_facets(temporal_doc, start, end)},
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


def cut_chunks(duration_s, chunk_s=CHUNK_S) -> list:
    """Fixed windows tiling ``[0, duration_s)``.

    The last window may be short; a missing or non-positive duration
    cuts nothing, said plainly as an empty list.  Pure: no ingest read,
    so the chunk grid is identical wherever it is cut from.
    """
    try:
        duration = float(duration_s)
    except (TypeError, ValueError):
        return []
    if duration <= 0 or chunk_s <= 0:
        return []
    windows = []
    start = 0.0
    while start < duration:
        windows.append((round(start, 3), round(min(start + chunk_s, duration), 3)))
        start += chunk_s
    return windows


def _overlaps(start: float, end: float, windows) -> list:
    """The entries of `windows` touching ``[start, end]``, in time order."""
    hits = []
    for window in windows or []:
        if not isinstance(window, dict):
            continue
        try:
            w_start = float(window.get("start", 0.0))
            w_end = float(window.get("end", w_start))
        except (TypeError, ValueError):
            continue
        if w_end <= start or w_start >= end:
            continue
        hits.append((w_start, window))
    return [w for _, w in sorted(hits, key=lambda h: h[0])]


def _action_windows(vision_doc) -> list:
    """Action windows as overlap-able entries.

    The analyser keys an action window by its ``window`` pair, not by
    top-level start/end - the same span rule `_action_segments` cuts
    by, so a chunk and the action rows agree on what overlaps what.
    """
    out = []
    for window in vision_doc.get("actions") or []:
        if not isinstance(window, dict):
            continue
        span = window.get("window") or []
        try:
            w_start = float(span[0])
            w_end = float(span[1])
        except (TypeError, ValueError, IndexError):
            actions = window.get("actions") or []
            bounds = [a for a in actions if isinstance(a, dict)]
            try:
                w_start = min(float(a.get("start", 0.0)) for a in bounds)
                w_end = max(float(a.get("end", w_start)) for a in bounds)
            except (TypeError, ValueError):
                continue
            if not bounds:
                continue
        entry = dict(window)
        entry["start"], entry["end"] = w_start, w_end
        out.append(entry)
    return out


def _chunk_segments(clip, temporal_doc, vision_doc, base_facets) -> list:
    """One row per 8 s window: the coarse guarantee beside fine rows.

    Text joins what the window overlaps - speech regions, scene
    observations, action windows, object labels - each read from the
    document that owns it, in time order.  A window nothing overlaps
    is still a row (`UNMEASURED_CHUNK_TEXT`): a silent unmeasured
    stretch is footage, and the index must be able to say so rather
    than holding nothing there at all.
    """
    try:
        duration = float(clip.get("duration_seconds"))
    except (TypeError, ValueError):
        return []
    out = []
    for n, (start, end) in enumerate(cut_chunks(duration)):
        parts = []
        for region in _overlaps(start, end, temporal_doc.get("speech_regions")):
            text = _clean(region.get("text"))
            if text:
                parts.append(text)
        for scene in _overlaps(start, end, vision_doc.get("scene")):
            bits = [_clean(scene.get("location")), _clean(scene.get("type")),
                    _clean(scene.get("lighting"))]
            bits += [_clean(f) for f in scene.get("notable_features") or []]
            text = ". ".join(p for p in bits if p)
            if text:
                parts.append(text)
        for window in _overlaps(start, end, _action_windows(vision_doc)):
            for action in window.get("actions") or []:
                bits = [_clean(action.get("action")),
                        _clean(action.get("body_language")),
                        _clean(action.get("speech_cue"))]
                text = " ".join(p for p in bits if p and p != "None")
                if text:
                    parts.append(text)
        for obj in vision_doc.get("objects") or []:
            label = _clean(obj.get("label"))
            if not label:
                continue
            appearances = obj.get("appearances") or [[0.0, duration]]
            spans = []
            for span in appearances:
                try:
                    spans.append((float(span[0]), float(span[1])))
                except (TypeError, ValueError, IndexError):
                    continue
            if any(s < end and e > start for s, e in spans):
                bits = [label, _clean(obj.get("category")),
                        _clean(obj.get("role")),
                        _clean(obj.get("readable_text"))]
                text = ". ".join(p for p in bits if p and p != "None")
                if text:
                    parts.append(text)
        speech_here = _overlaps(start, end, temporal_doc.get("speech_regions"))
        text = " ".join(parts) if parts else UNMEASURED_CHUNK_TEXT
        out.append(Segment(
            segment_id=f"{clip['clip_id']}#chunk#{n:03d}",
            clip_id=clip["clip_id"],
            filename=clip.get("filename", ""),
            source_file=clip.get("source_file") or clip.get("path", ""),
            kind="chunk",
            start=start,
            end=end,
            text=text,
            facets={**base_facets, "has_speech": bool(speech_here),
                    "measured": bool(parts),
                    **curve_facets(temporal_doc, start, end)},
        ))
    return out


# ─── The whole project ───


def build_segments(project_folder, kinds=SEGMENT_KINDS) -> list:
    """Every retrievable segment of every clip, in clip then time order.

    Reads only.  Nothing under `project_folder` is written or modified.
    Speech comes from the per-source memory (M1) wherever a fresh
    transcript exists, and from the 1.04 regions elsewhere - never
    both for one clip.
    """
    unknown = [k for k in kinds if k not in SEGMENT_KINDS]
    if unknown:
        raise ValueError(
            f"Unknown segment kind(s): {unknown}. Known kinds: {list(SEGMENT_KINDS)}"
        )

    catalog = load_catalog(project_folder)
    temporal = load_temporal_index(project_folder)
    vision = load_vision_profiles(project_folder, catalog)
    recorded = source_memory.load_recorded_fingerprints(project_folder)

    segments = []
    for clip in catalog:
        clip_id = clip.get("clip_id")
        if not clip_id:
            continue
        temporal_doc = temporal.get(clip_id) or {}
        vision_doc = vision.get(clip_id) or {}
        base = _vision_facets(vision_doc)
        base.update(_deterministic_facets(temporal_doc))
        base["duration_seconds"] = clip.get("duration_seconds")
        # Lane B (cascade report 2.1): the clip's content digest is what
        # a segment's foreign keys resolve through - `asset-{digest}`
        # and the source-clock interval. Computed once per clip and
        # passed to both speech paths; absent when the media is offline
        # and nothing was recorded, which reads as unlinked, never as
        # a guessed id.
        digest, _basis = source_memory.digest_for_clip(
            project_folder, clip, recorded)
        quality_doc = (source_memory.read_image_defects(digest)
                       if digest else None)
        audio_doc = (source_memory.read_audio_quality(digest)
                     if digest else None)
        first_segment = len(segments)
        cutters = {
            "speech": lambda c, v, t, b, digest=digest: _speech_segments(
                c, t, b, digest),
            "action": _action_segments,
            "scene": _scene_segments,
            "camera": _camera_segments,
            "object": _object_segments,
            "chunk": lambda c, v, t, b: _chunk_segments(c, t, v, b),
        }
        # A fresh whole-source transcript supersedes the 1.04 regions
        # for the same clip: both transcribe the whole file, so
        # serving both would index every word twice. The memory is
        # checked first and the 1.04 path stays for clips nothing has
        # transcribed yet.
        memory_doc, memory_status = source_memory.read_m1_for_clip(
            project_folder, clip, recorded)
        if memory_doc is not None and memory_status == "fresh":
            if "speech" in kinds:
                segments.extend(_memory_speech_segments(
                    clip, memory_doc, temporal_doc, base, digest))
            kinds_rest = [k for k in kinds if k != "speech"]
        else:
            kinds_rest = list(kinds)
        for kind in kinds_rest:
            if kind == "speech":
                segments.extend(cutters[kind](clip, None, temporal_doc, base))
            else:
                segments.extend(cutters[kind](clip, vision_doc, temporal_doc, base))
        for segment in segments[first_segment:]:
            # The source-quality profile is the ONE reading of this
            # clip's measured quality: VI-03's image signals, the 1.04
            # camera/subject decomposition, the face track's framing and
            # M11's intelligibility proxies, each with the lane and the
            # interval it came from. The flat facets the index filters
            # on are published from it, so a filter bound and the
            # profile a reader inspects cannot disagree.
            segment.quality_profile = source_quality.profile_over_span(
                image_defects=quality_doc,
                temporal_index=temporal_doc,
                audio_quality=audio_doc,
                start_seconds=segment.start,
                end_seconds=segment.end,
                asset_id=model_link.asset_id_for_digest(digest)
                if digest else None,
                content_digest=digest,
                source_file=segment.source_file or None,
                clock_id=model_link.clock_id_for_digest(digest)
                if digest else None)
            segment.facets.update(
                source_quality.facets_of(segment.quality_profile))
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

    # The per-source memory beside the project-side ingest: whole-source
    # transcripts that `build_segments` serves ahead of the 1.04
    # regions. Recorded digests resolve without the media present, so
    # these count what search can actually hear.
    recorded = source_memory.load_recorded_fingerprints(project_folder)
    memory_utterances = memory_words = memory_clips = 0
    per_clip = {}
    for clip in catalog:
        doc, status = source_memory.read_m1_for_clip(
            project_folder, clip, recorded)
        clip_id = clip.get("clip_id")
        vision_doc = vision.get(clip_id) or {}
        digest, _basis = source_memory.digest_for_clip(
            project_folder, clip, recorded)
        sound_doc = source_memory.read_sound(digest) if digest else None
        memory_transcribed = doc is not None and status == "fresh"
        if doc is not None and status == "fresh":
            memory_clips += 1
            memory_utterances += doc.get("utterance_count", 0)
            memory_words += doc.get("word_count", 0)

        # Keep modality coverage at the clip boundary.  This is a record
        # of measurements that exist, not a claim that they describe every
        # frame of the source.
        temporal_path = (_steps_root(project_folder) / "1_04_temporal_index"
                         / "index" / f"{clip_id}.json")
        ocr_path = (_steps_root(project_folder) / "1_07_ocr_extraction"
                    / clip_id / "ocr_result.json")
        expressions_path = (source_memory.source_dir(digest)
                            / source_memory.SLOT_EXPRESSIONS
                            if digest else None)
        per_clip[clip_id] = {
            "speech": bool(memory_transcribed or temporal_path.is_file()),
            "picture": bool(vision_doc),
            "scene": bool(vision_doc.get("scene")),
            "action": bool(vision_doc.get("actions")),
            "object": bool(vision_doc.get("objects")),
            "ocr": ocr_path.is_file(),
            "expressions": bool(expressions_path and expressions_path.is_file()),
            "sound": bool(sound_doc),
            "video_embeddings": False,
        }

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
        "memory_transcribed_clips": memory_clips,
        "memory_utterances": memory_utterances,
        "memory_words": memory_words,
        "per_clip": per_clip,
    }


# ─── Noticing that the ingest moved under the index ───────────────

# Every place this module reads, as a glob relative to the project folder.
# An index is built from exactly these files, so a change to any of them
# is what "the index is stale" means.  Keep this in step with the loaders
# above: a source read but not listed here is a source that can change
# without anybody noticing.
INGEST_SOURCES = (
    "pipeline_output/steps/1_02_catalog_footage/output.json",
    "pipeline_output/steps/1_03_semantic_analysis/clip_profile_*.json",
    "pipeline_output/steps/1_04_temporal_index/index/clip_*.json",
    "pipeline_output/steps/1_05_prosody_analysis/clip_*_prosody.json",
)

# `pipeline_data.json` is read too, but it is NOT fingerprinted whole:
# it carries every step's output and the runner's state writer rewrites
# it after every step, so a colour grade landing would report the ingest
# as stale.  Only the subtree `load_catalog` reads is measured.
STATE_FILE = "pipeline_data.json"
STATE_INGEST_KEYS = ("catalog",)


#: The files whose CONTENT decides what the segments in an index say.
#: A change to any of them changes the cut, so an index built by the old
#: code is serving rows the new code would not cut.
#:
#: The list is CURATED, not derived. Walking the AST closure of the cut
#: reaches the whole engine - the cut reads the source memory, which
#: reads the world - and an identity that takes minutes is not a
#: staleness check. A curated list over-invalidates a little: a change
#: to a source-memory reader that does not touch the cut still marks the
#: index stale. That direction is the safe one, because the cost is a
#: rebuild and the other direction is a silently wrong index.
SEGMENT_CUT_SOURCES = (
    "library/tools/analysis/footage_segments.py",
    "library/tools/source_quality.py",
    "library/tools/subject_framing.py",
    "library/tools/camera_motion.py",
    "library/tools/source_memory.py",
)

REPO_ROOT = Path(__file__).resolve().parents[3]


def segment_method_digest() -> str | None:
    """The identity of the code that CUTS the segments.

    An index is analysis as much as it is data: the rows in it were cut
    by a particular version of the code that cuts them, and a change to
    that code changes what the index would say. Content, not mtime: a
    file rewritten byte-for-byte identically has not changed.

    None when the identity cannot be computed. An index that cannot
    name its method is NOT declared stale on that ground, because
    "unknown" is not "changed".
    """
    digest = hashlib.sha256()
    for relative in SEGMENT_CUT_SOURCES:
        path = REPO_ROOT / relative
        try:
            blob = path.read_bytes()
        except OSError:
            return None
        digest.update(relative.encode("utf-8"))
        digest.update(blob)
    return digest.hexdigest()


def current_source_digests(project_folder) -> dict:
    """``{clip_id: content_digest}`` for the project's catalog, as it is now.

    The per-source half of the ingest fingerprint. The whole-set digest
    says THAT something moved; this says WHICH footage moved, and that
    is the difference between "rebuild the index" and "re-measure this
    clip".

    A clip whose media is offline answers from the runner's recorded
    fingerprint, so a project whose footage is not mounted still says
    what it was built from. A clip with neither is ABSENT from the
    answer, never given a placeholder digest.
    """
    recorded = source_memory.load_recorded_fingerprints(project_folder)
    out = {}
    for clip in load_catalog(project_folder):
        clip_id = clip.get("clip_id")
        if not clip_id:
            continue
        digest, _basis = source_memory.digest_for_clip(
            project_folder, clip, recorded)
        if digest:
            out[clip_id] = digest
    return out


def ingest_fingerprint(project_folder) -> dict:
    """A digest of the ingest an index would be built from.

    CONTENT, not mtime - the same reasoning as `footage_identity.py`
    (§3): a file rewritten byte-for-byte identically has not changed, and
    a rebuild it triggers is 4 s of work for nothing.  These are JSON
    documents rather than footage, so the whole file is read; on 001 that
    is 70 files and 15 ms, which is small beside the 4 s build it guards.

    The per-source memory is mixed in as well: `build_segments` serves
    fresh M1 transcripts ahead of the 1.04 regions, so a transcript
    landing (or a source changing under one) IS the ingest moving, and
    an index built before it must report stale. Memory entries resolve
    off recorded digests, so this stays readable with the media offline.

    Returns ``{"digest", "files", "bytes"}``.  A project with no ingest
    at all fingerprints to zero files, which is itself a stable answer.
    """
    root = Path(project_folder)
    digest = hashlib.sha256()
    count, total = 0, 0

    state_path = root / STATE_FILE
    if state_path.is_file():
        state = _load_json(state_path) or {}
        from library.tools import capability_outputs
        outputs = capability_outputs.node_outputs(state)
        subtree = json.dumps({k: outputs.get(k) for k in STATE_INGEST_KEYS},
                             sort_keys=True, default=str).encode("utf-8")
        digest.update(STATE_FILE.encode("utf-8"))
        digest.update(subtree)
        count += 1
        total += len(subtree)

    for pattern in INGEST_SOURCES:
        for path in sorted(root.glob(pattern)):
            if not path.is_file():
                continue
            try:
                blob = path.read_bytes()
            except OSError:
                continue
            digest.update(str(path.relative_to(root)).encode("utf-8"))
            digest.update(blob)
            count += 1
            total += len(blob)

    memory = source_memory.memory_fingerprint(str(project_folder))
    memory_blob = json.dumps(
        {"digest": memory["digest"], "entries": memory["entries"]},
        sort_keys=True).encode("utf-8")
    digest.update(b"source-memory-v1")
    digest.update(memory_blob)
    count += memory["sources"]
    total += len(memory_blob)
    return {"digest": digest.hexdigest(), "files": count, "bytes": total}
