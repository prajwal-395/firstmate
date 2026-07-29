#!/usr/bin/env python3
"""
sfx_placer.py v4 — Semantic-aware SFX placement for DaVinci Resolve.

Changes from v3:
- Semantic profiles: Every SFX file gets a semantic profile (description,
  source object, emotional temperature, abstractness, recognition risk,
  style tags, works_when/avoid_when) loaded from library_semantic.json.
- 5-dimensional scoring: Each candidate SFX is scored against the placement
  context across recognition risk, abstractness, style match, emotional
  temperature, and evocation relevance.
- pick_best_sfx replaces pick_from_pool: Selection considers both acoustic
  fit AND semantic fit, filtering out inappropriate sounds (e.g. minecraft
  eating in a vulnerability confession).

Usage:
    python3 sfx_placer.py --analyze     # Dry run: show plan
    python3 sfx_placer.py --place       # Analyze + place on timeline
    python3 sfx_placer.py --clear       # Remove all auto SFX
"""

import sys
import os
import json
import argparse
import math
import random

import librosa
import numpy as np

RESOLVE_MODULES_PATH = "/Library/Application Support/Blackmagic Design/DaVinci Resolve/Developer/Scripting/Modules/"
if RESOLVE_MODULES_PATH not in sys.path:
    sys.path.append(RESOLVE_MODULES_PATH)

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SFX_LIBRARY = "/Users/prajwal/Documents/content_stuff/assets i used (just copied here for convenience)/sfx library"
SUBTITLES_PATH = os.path.join(PROJECT_ROOT, "pipeline_output", "subtitles.json")
ANALYSIS_CACHE = os.path.join(SFX_LIBRARY, "library_analysis.json")
SEMANTIC_CACHE = os.path.join(SFX_LIBRARY, "library_semantic.json")
MUSIC_ANALYSIS = os.path.join(PROJECT_ROOT, "pipeline_output", "music", "music_analysis.json")
PIPELINE_DATA = os.path.join(PROJECT_ROOT, "pipeline_data.json")

SFX_TRACK_NAMES = {
    "hit": "SFX Hits",
    "whoosh": "SFX Whoosh",
    "riser": "SFX Riser",
}

# Recognition risk numeric scores for semantic scoring
RECOGNITION_RISK_SCORES = {
    "very_low": 0, "low": 1, "medium": 2, "high": 3, "very_high": 4,
}

# Video style keyword mapping — used to infer style tags from creative direction
VIDEO_STYLE_KEYWORDS = {
    "raw":       ["raw", "indie", "lo-fi", "analog", "authentic", "vlog", "handheld"],
    "polished":  ["modern", "clean", "cinematic", "corporate", "designed"],
    "retro":     ["retro", "analog", "vintage", "nostalgic", "film", "tape"],
    "energetic": ["energetic", "dynamic", "punchy", "action", "bold"],
}

# ═══════════════════════════════════════════════════════════════════════════════
# 1. ACOUSTIC ANALYSIS & ROLE CLASSIFICATION
# ═══════════════════════════════════════════════════════════════════════════════

# A single SFX can have multiple roles. The rules engine picks from role pools.
ROLE_CRITERIA = {
    "punch": {
        "attack": ["sharp"],
        "duration_class": ["stab", "short"],
        "envelope": ["front", "sustained"],
    },
    "whoosh": {
        "duration_class": ["short", "medium"],
        "texture": ["noisy"],
    },
    "riser": {
        "envelope": ["rising"],
        "duration_class": ["medium", "long"],
    },
    "accent": {
        "attack": ["sharp", "medium"],
        "duration_class": ["stab", "short"],
    },
    "texture": {
        "duration_class": ["long"],
    },
    "punctuation": {
        "attack": ["sharp"],
        "duration_class": ["stab"],
        "freq_band": ["high", "air"],
    },
}


def compute_roles(traits):
    """Determine which acoustic roles an SFX qualifies for."""
    roles = []
    for role_name, criteria in ROLE_CRITERIA.items():
        match = True
        for trait_key, allowed_values in criteria.items():
            if traits.get(trait_key) not in allowed_values:
                match = False
                break
        if match:
            roles.append(role_name)
    return roles


def analyze_sfx_file(sfx_path):
    """Analyze an SFX file's acoustic properties and compute roles."""
    y, sr = librosa.load(sfx_path, sr=None)
    duration = len(y) / sr

    onset_frames = librosa.onset.onset_detect(y=y, sr=sr, backtrack=True)
    onset_s = float(librosa.frames_to_time(onset_frames[0], sr=sr)) if len(onset_frames) > 0 else 0.0

    peak_sample = int(np.argmax(np.abs(y)))
    peak_s = peak_sample / sr

    centroid = float(np.mean(librosa.feature.spectral_centroid(y=y, sr=sr)))

    # Spectral flatness → tonal vs noisy
    spectral_flatness = float(np.mean(librosa.feature.spectral_flatness(y=y)))

    # Acoustic trait classification
    attack_s = max(0, peak_s - onset_s)
    attack_class = "sharp" if attack_s < 0.1 else "medium" if attack_s < 0.5 else "slow"

    peak_position = peak_s / duration if duration > 0 else 0
    envelope = "rising" if peak_position > 0.6 else "front" if peak_position < 0.3 else "sustained"

    duration_class = "stab" if duration < 0.3 else "short" if duration < 1.0 else "medium" if duration < 3.0 else "long"

    texture = "noisy" if spectral_flatness > 0.05 else "tonal"

    freq_band = (
        "sub" if centroid < 200 else
        "low" if centroid < 500 else
        "mid" if centroid < 2500 else
        "high" if centroid < 6000 else
        "air"
    )

    traits = {
        "attack": attack_class,
        "envelope": envelope,
        "duration_class": duration_class,
        "texture": texture,
        "freq_band": freq_band,
    }

    roles = compute_roles(traits)

    return {
        "file": os.path.basename(sfx_path),
        "path": sfx_path,
        "sample_rate": sr,
        "duration_s": round(duration, 3),
        "onset_s": round(onset_s, 4),
        "peak_s": round(float(peak_s), 4),
        "centroid_hz": round(centroid),
        "spectral_flatness": round(spectral_flatness, 4),
        # Acoustic traits
        "attack": attack_class,
        "envelope": envelope,
        "duration_class": duration_class,
        "texture": texture,
        "freq_band": freq_band,
        # Roles
        "roles": roles,
    }


def analyze_library(sfx_dir, cache_path=None):
    """Analyze all SFX files in a library directory."""
    if cache_path and os.path.exists(cache_path):
        with open(cache_path) as f:
            cached = json.load(f)
        # Validate cache has new fields
        if cached and "roles" in cached[0]:
            cached_paths = {item["path"] for item in cached}
            current_paths = set()
            for root, _, files in os.walk(sfx_dir):
                for f in files:
                    if f.lower().endswith(('.wav', '.mp3', '.flac', '.aif', '.aiff')) and not f.startswith('.'):
                        current_paths.add(os.path.join(root, f))
            if current_paths == cached_paths:
                return cached

    results = []
    for root, _, files in os.walk(sfx_dir):
        for fn in files:
            if not fn.lower().endswith(('.wav', '.mp3', '.flac', '.aif', '.aiff')) or fn.startswith('.'):
                continue
            path = os.path.join(root, fn)
            rel = os.path.relpath(path, sfx_dir)
            parts = rel.split(os.sep)
            category = parts[0] if len(parts) > 1 else "Root"
            subcategory = parts[1] if len(parts) > 2 else ""

            try:
                analysis = analyze_sfx_file(path)
                analysis["category"] = category
                analysis["subcategory"] = subcategory
                analysis["rel"] = rel
                results.append(analysis)
            except Exception as e:
                print(f"  SKIP {fn}: {e}", file=sys.stderr)

    # Note: Generated SFX (bass_impact.wav, whoosh.wav) now live in
    # the SFX library under Generated/ — no separate local scan needed.

    if cache_path:
        os.makedirs(os.path.dirname(cache_path), exist_ok=True)
        with open(cache_path, "w") as f:
            json.dump(results, f, indent=2)

    return results


def build_role_pools(library):
    """Organize library into pools by acoustic role (not by label)."""
    pools = {role: [] for role in ROLE_CRITERIA}
    uncategorized = []

    for sfx in library:
        roles = sfx.get("roles", [])
        if not roles:
            uncategorized.append(sfx)
            continue
        for role in roles:
            if role in pools:
                pools[role].append(sfx)

    return pools, uncategorized


# ═══════════════════════════════════════════════════════════════════════════════
# 1b. SEMANTIC PROFILING & SCORING
# ═══════════════════════════════════════════════════════════════════════════════

def load_semantic_profiles(path=SEMANTIC_CACHE):
    """Load LLM-generated semantic profiles from cache."""
    if not os.path.exists(path):
        print(f"  ⚠ No semantic profiles at {path} — running without semantic scoring")
        return {}
    with open(path) as f:
        profiles = json.load(f)
    return {p["file"]: p for p in profiles}


def enrich_library_with_semantics(library, semantic_profiles):
    """Merge semantic profile data into each library entry."""
    enriched = 0
    for sfx in library:
        profile = semantic_profiles.get(sfx["file"])
        if profile:
            sfx["description"] = profile.get("description", "")
            sfx["source_object"] = profile.get("source_object", "unknown")
            sfx["evokes"] = profile.get("evokes", [])
            sfx["emotional_temperature"] = profile.get("emotional_temperature", "neutral-calm")
            sfx["abstractness"] = profile.get("abstractness", 0.5)
            sfx["recognition_risk"] = profile.get("recognition_risk", "low")
            sfx["style_tags"] = profile.get("style_tags", [])
            sfx["works_when"] = profile.get("works_when", "")
            sfx["avoid_when"] = profile.get("avoid_when", "")
            enriched += 1
        else:
            # Defaults for files without semantic profiles
            sfx["abstractness"] = 0.5
            sfx["recognition_risk"] = "low"
            sfx["style_tags"] = []
            sfx["evokes"] = []
            sfx["description"] = ""
    return enriched


def infer_video_style_tags(creative_direction):
    """Infer style tags from creative direction metadata."""
    if not creative_direction:
        return ["modern", "clean"]
    text = " ".join([
        str(creative_direction.get("target_mood", "")),
        str(creative_direction.get("energy_arc", "")),
        str(creative_direction.get("rationale", "")),
    ]).lower()
    tags = set()
    for style, keywords in VIDEO_STYLE_KEYWORDS.items():
        for kw in keywords:
            if kw in text:
                tags.update(VIDEO_STYLE_KEYWORDS[style])
                break
    return list(tags) if tags else ["modern", "clean"]


def score_sfx_for_context(sfx, context, video_style_tags):
    """
    Score an SFX candidate for a placement context across 5 dimensions:
    1. Recognition risk (penalty for recognizable sounds in wrong context)
    2. Abstractness (bonus for abstract, penalty for concrete without justification)
    3. Style match (overlap between SFX style tags and video style)
    4. Emotional temperature match
    5. Evocation relevance (semantic match to narrative/intent)
    
    Returns (score: float 0-1, reasons: list[str])
    """
    score = 0.5
    reasons = []

    # ── 1. Recognition risk ──
    risk = RECOGNITION_RISK_SCORES.get(sfx.get("recognition_risk", "low"), 1)
    is_humor = any(w in context.get("emotion", "").lower()
                   for w in ["humor", "funny", "comedic", "playful", "self-deprecating"])
    if risk >= 4:  # very_high
        if is_humor:
            score += 0.05
            reasons.append("Very high recognition — humor context allows it")
        else:
            score -= 0.5
            reasons.append(f"⛔ VERY HIGH recognition risk in non-humor context")
    elif risk >= 3:  # high
        score -= 0.25
        reasons.append(f"⚠ High recognition risk ({sfx.get('recognition_risk')})")
    elif risk <= 1:
        score += 0.1
        reasons.append("✓ Low recognition risk")

    # ── 2. Abstractness ──
    abstractness = sfx.get("abstractness", 0.5)
    if abstractness >= 0.7:
        score += 0.1
        reasons.append("✓ Abstract (versatile)")
    elif abstractness <= 0.3:
        # Concrete sounds need semantic justification from the context
        context_text = f"{context.get('emotion', '')} {context.get('narrative', '')} {context.get('sfx_intent', '')}".lower()
        evokes = sfx.get("evokes", [])
        matching_evocations = [ev for ev in evokes if ev.lower() in context_text]
        if matching_evocations:
            score += 0.2
            reasons.append(f"✓ Concrete with semantic match: {matching_evocations}")
        else:
            score -= 0.15
            reasons.append("✗ Concrete sound without semantic justification")

    # ── 3. Style match ──
    sfx_style = set(sfx.get("style_tags", []))
    video_style = set(video_style_tags)
    style_overlap = sfx_style & video_style
    if style_overlap:
        bonus = min(0.2, len(style_overlap) * 0.05)
        score += bonus
        reasons.append(f"✓ Style match: {style_overlap}")
    elif sfx_style and video_style:
        score -= 0.1
        reasons.append(f"✗ Style mismatch: sfx={sfx_style} vs video={video_style}")

    # ── 4. Emotional temperature match ──
    sfx_temp = sfx.get("emotional_temperature", "neutral-calm")
    ctx_emotion = context.get("emotion", "").lower()
    sfx_warm = "warm" in sfx_temp
    sfx_cold = "cold" in sfx_temp
    sfx_tense = "tense" in sfx_temp or "aggressive" in sfx_temp
    ctx_warm = any(w in ctx_emotion for w in ["nostalgic", "warm", "earnest", "hopeful"])
    ctx_vulnerable = any(w in ctx_emotion for w in ["vulnerable", "raw", "honest"])
    ctx_intense = any(w in ctx_emotion for w in ["assertive", "defiant", "determined"])

    if sfx_warm and ctx_warm:
        score += 0.1
        reasons.append("✓ Warm SFX + warm context")
    elif sfx_cold and ctx_warm:
        score -= 0.1
        reasons.append("✗ Cold SFX in warm context")
    if sfx_tense and ctx_intense:
        score += 0.1
        reasons.append("✓ Intense SFX + intense context")
    elif sfx_tense and ctx_vulnerable:
        score -= 0.05
        reasons.append("✗ Tense SFX in vulnerable context")

    # ── 5. Evocation relevance ──
    evokes = sfx.get("evokes", [])
    context_keywords = f"{context.get('narrative', '')} {context.get('sfx_intent', '')}".lower()
    relevant = [ev for ev in evokes if ev.lower() in context_keywords]
    if relevant:
        score += 0.15
        reasons.append(f"✓ Evocation: {relevant}")

    return round(min(1.0, max(0.0, score)), 2), reasons


def pick_best_sfx(pool, used_recently, context, video_style_tags,
                  max_duration=None, prefer_short=False, min_score=0.25):
    """
    Semantically-aware SFX selection.
    
    1. Pre-filter by duration
    2. Score all candidates semantically against the placement context
    3. Filter out low-scoring candidates
    4. Pick from top candidates (with randomness for variety)
    
    Returns (sfx_entry, semantic_reasons) or (None, [])
    """
    candidates = list(pool)

    # Duration filter
    if max_duration:
        candidates = [s for s in candidates if s["duration_s"] <= max_duration]
    if prefer_short:
        candidates.sort(key=lambda s: s["duration_s"])
        candidates = candidates[:max(5, len(candidates) // 2)]
    if not candidates:
        return None, []

    # Avoid recent repeats (but keep them as fallback)
    fresh = [s for s in candidates if s["path"] not in used_recently]
    if fresh:
        candidates = fresh

    # Score semantically
    scored = []
    for sfx in candidates:
        sc, reasons = score_sfx_for_context(sfx, context, video_style_tags)
        scored.append({"sfx": sfx, "score": sc, "reasons": reasons})

    # Filter by minimum score
    viable = [s for s in scored if s["score"] >= min_score]
    if not viable:
        # Fallback: take the least-bad option
        scored.sort(key=lambda s: s["score"], reverse=True)
        if scored:
            choice = scored[0]
            used_recently.add(choice["sfx"]["path"])
            return choice["sfx"], choice["reasons"] + ["(fallback: no candidate met min score)"]
        return None, []

    # Sort by score, pick from top 3 (with randomness)
    viable.sort(key=lambda s: s["score"], reverse=True)
    top_n = viable[:min(3, len(viable))]
    choice = random.choice(top_n)
    used_recently.add(choice["sfx"]["path"])
    return choice["sfx"], choice["reasons"]


# ═══════════════════════════════════════════════════════════════════════════════
# 2. TIMELINE ANALYSIS
# ═══════════════════════════════════════════════════════════════════════════════

def connect_resolve():
    import DaVinciResolveScript as dvr_script
    resolve = dvr_script.scriptapp("Resolve")
    if not resolve:
        print("ERROR: Cannot connect to Resolve.", file=sys.stderr)
        sys.exit(1)
    project = resolve.GetProjectManager().GetCurrentProject()
    timeline = project.GetCurrentTimeline()
    if not timeline:
        print("ERROR: No timeline open.", file=sys.stderr)
        sys.exit(1)
    return resolve, project, project.GetMediaPool(), timeline


def get_timeline_info(timeline):
    fps = float(timeline.GetSetting("timelineFrameRate") or 30)
    start_frame = timeline.GetStartFrame()
    end_frame = timeline.GetEndFrame()
    total_duration = (end_frame - start_frame) / fps

    v1_items = timeline.GetItemListInTrack("video", 1) or []
    clips = []
    transitions = []

    for item in v1_items:
        src_start = item.GetSourceStartFrame()
        is_transition = src_start is None
        mpi = item.GetMediaPoolItem()
        entry = {
            "name": item.GetName(),
            "start_frame": item.GetStart(),
            "end_frame": item.GetEnd(),
            "start_s": round((item.GetStart() - start_frame) / fps, 3),
            "end_s": round((item.GetEnd() - start_frame) / fps, 3),
            "is_transition": is_transition,
            "file_path": mpi.GetClipProperty("File Path") if mpi else None,
        }
        if is_transition:
            transitions.append(entry)
        else:
            clips.append(entry)

    cuts = []
    for i in range(1, len(clips)):
        has_transition = any(
            abs(t["start_s"] - clips[i]["start_s"]) < 1.0
            for t in transitions
        )
        source_change = clips[i].get("file_path") != clips[i - 1].get("file_path")
        cuts.append({
            "index": i,
            "time_s": clips[i]["start_s"],
            "frame": clips[i]["start_frame"],
            "from_clip": clips[i - 1]["name"],
            "to_clip": clips[i]["name"],
            "transition_type": "dissolve" if has_transition else "hard_cut",
            "source_change": source_change,
            "is_first": i == 1,
            "is_last": i == len(clips) - 1,
        })

    broll_events = []
    v2_count = timeline.GetTrackCount("video")
    for track_i in range(2, min(v2_count + 1, 4)):
        v2_items = timeline.GetItemListInTrack("video", track_i) or []
        for item in v2_items:
            src_start = item.GetSourceStartFrame()
            if src_start is None:
                continue
            mpi = item.GetMediaPoolItem()
            broll_events.append({
                "name": item.GetName(),
                "track": track_i,
                "start_s": round((item.GetStart() - start_frame) / fps, 3),
                "end_s": round((item.GetEnd() - start_frame) / fps, 3),
                "duration_s": round((item.GetEnd() - item.GetStart()) / fps, 3),
                "file_path": mpi.GetClipProperty("File Path") if mpi else None,
            })

    return {
        "fps": fps,
        "start_frame": start_frame,
        "end_frame": end_frame,
        "duration_s": round(total_duration, 3),
        "clips": clips,
        "cuts": cuts,
        "transitions": transitions,
        "broll": broll_events,
    }


# ═══════════════════════════════════════════════════════════════════════════════
# 3. SPEECH, MUSIC & CREATIVE CONTEXT
# ═══════════════════════════════════════════════════════════════════════════════

def load_subtitles(path):
    if not os.path.exists(path):
        return []
    with open(path) as f:
        return json.load(f)


def is_speech_active(time_s, subs, margin=0.15):
    for sub in subs:
        if (sub["timeline_start"] - margin) <= time_s <= (sub["timeline_end"] + margin):
            return True, sub["text"]
    return False, None


def load_music_analysis(path):
    if not os.path.exists(path):
        return None
    with open(path) as f:
        return json.load(f)


def snap_to_beat(time_s, beat_times, tolerance_s=0.1):
    if not beat_times:
        return time_s, False
    diffs = [abs(b - time_s) for b in beat_times]
    nearest_idx = min(range(len(diffs)), key=lambda i: diffs[i])
    if diffs[nearest_idx] <= tolerance_s:
        return beat_times[nearest_idx], True
    return time_s, False


def load_creative_context(path):
    """Load creative direction and semantic analysis from pipeline_data.json."""
    if not os.path.exists(path):
        return None
    with open(path) as f:
        data = json.load(f)

    ctx = {}

    # Creative direction
    cd = data.get("creative_direction", {})
    if isinstance(cd, dict) and "creative_direction" in cd:
        cd = cd["creative_direction"]
    ctx["creative_direction"] = cd

    # Semantic analysis
    sa = data.get("semantic_analysis", {})
    ctx["semantic_docs"] = sa.get("semantic_analysis_documents", [])

    # SFX spec (pipeline-generated suggestions)
    ctx["sfx_spec"] = data.get("sfx_spec", {})
    if isinstance(ctx["sfx_spec"], dict):
        ctx["sfx_spec"] = ctx["sfx_spec"].get("sfx_spec", [])

    # Spine blocks
    ctx["spine_blocks"] = data.get("timed_spine", {}).get("audio_spine", [])
    if not ctx["spine_blocks"]:
        ctx["spine_blocks"] = data.get("_spine_blocks", [])

    return ctx


# ═══════════════════════════════════════════════════════════════════════════════
# 4. SEGMENT BRIEFS — MAPPING EMOTION TO SFX
# ═══════════════════════════════════════════════════════════════════════════════

# Maps emotional/energy keywords → SFX parameters
EMOTION_SFX_MAP = {
    # High energy / intense
    "assertive":     {"density": "moderate", "energy": "punchy",  "prefer": ["punch", "whoosh", "accent"], "avoid": []},
    "determined":    {"density": "moderate", "energy": "punchy",  "prefer": ["punch", "whoosh", "accent"], "avoid": []},
    "high":          {"density": "moderate", "energy": "punchy",  "prefer": ["punch", "whoosh"], "avoid": []},
    "authoritative": {"density": "moderate", "energy": "punchy",  "prefer": ["punch", "whoosh", "accent"], "avoid": []},
    "defiant":       {"density": "moderate", "energy": "strong",  "prefer": ["punch", "whoosh", "riser"],  "avoid": []},
    # Medium energy / building
    "earnest":       {"density": "sparse",   "energy": "subtle",  "prefer": ["accent", "riser"], "avoid": ["punch"]},
    "nostalgic":     {"density": "sparse",   "energy": "warm",    "prefer": ["texture", "riser"], "avoid": ["punch"]},
    "building":      {"density": "moderate", "energy": "rising",  "prefer": ["riser", "whoosh", "accent"], "avoid": []},
    "focused":       {"density": "sparse",   "energy": "subtle",  "prefer": ["accent"],          "avoid": ["texture", "punch"]},
    "anticipatory":  {"density": "sparse",   "energy": "rising",  "prefer": ["riser"],            "avoid": ["punch"]},
    # Low energy / vulnerable
    "vulnerable":    {"density": "sparse",   "energy": "subtle",  "prefer": ["whoosh", "accent", "punctuation"], "avoid": []},
    "raw":           {"density": "sparse",   "energy": "textured","prefer": ["whoosh", "accent"], "avoid": []},
    "contemplative": {"density": "minimal",  "energy": "subtle",  "prefer": ["whoosh"],          "avoid": ["punch"]},
    "nervous":       {"density": "sparse",   "energy": "subtle",  "prefer": ["whoosh", "accent", "punctuation"], "avoid": ["punch"]},
    "self-conscious":{"density": "minimal",  "energy": "subtle",  "prefer": ["punctuation"],      "avoid": ["punch", "whoosh"]},
    "defeated":      {"density": "minimal",  "energy": "subtle",  "prefer": ["texture"],          "avoid": ["punch", "whoosh", "riser"]},
    # Humor / disruption
    "frustrated":    {"density": "sparse",   "energy": "punchy",  "prefer": ["accent", "punctuation"], "avoid": ["texture"]},
    "panicked":      {"density": "moderate", "energy": "sharp",   "prefer": ["accent", "punctuation"], "avoid": ["texture"]},
    "embarrassed":   {"density": "minimal",  "energy": "subtle",  "prefer": ["punctuation"],      "avoid": ["punch", "whoosh"]},
}

DENSITY_BUDGET = {
    "minimal": 0,    # No SFX on cuts in this segment
    "sparse": 1,     # At most 1 SFX event
    "moderate": 2,   # Up to 2 SFX events (e.g. whoosh + accent)
    "dense": 3,      # Full layering (riser + whoosh + impact)
}


def match_emotion_to_sfx_params(emotion_text):
    """
    Parse an emotion string and find the best-matching SFX parameters.
    Returns the SFX params dict for the strongest emotional match.
    """
    if not emotion_text:
        return {"density": "sparse", "energy": "subtle", "prefer": ["whoosh"], "avoid": []}

    emotion_lower = emotion_text.lower()
    best_match = None
    best_score = 0

    for keyword, params in EMOTION_SFX_MAP.items():
        if keyword in emotion_lower:
            # Longer keyword matches = more specific = higher priority
            score = len(keyword)
            if score > best_score:
                best_score = score
                best_match = params

    if best_match:
        return best_match

    # Fallback: infer from general tone
    if any(w in emotion_lower for w in ["high", "strong", "intense", "peak"]):
        return {"density": "moderate", "energy": "punchy", "prefer": ["punch", "whoosh"], "avoid": []}
    elif any(w in emotion_lower for w in ["low", "quiet", "soft", "calm"]):
        return {"density": "minimal", "energy": "subtle", "prefer": ["texture"], "avoid": ["punch"]}

    return {"density": "sparse", "energy": "subtle", "prefer": ["whoosh", "accent"], "avoid": []}


def build_segment_briefs(tl_info, subtitles, creative_ctx):
    """
    Build per-segment SFX briefs from creative direction + semantic analysis.
    Each brief tells the rules engine what kind of SFX to place in that segment.
    """
    clips = tl_info["clips"]
    cuts = tl_info["cuts"]
    broll = tl_info["broll"]

    # Parse creative direction
    cd = creative_ctx.get("creative_direction", {}) if creative_ctx else {}
    target_mood = cd.get("target_mood", "")
    energy_arc = cd.get("energy_arc", "")
    key_moments = cd.get("key_moments", [])

    # Parse semantic analysis — build a map of block labels + emotions
    semantic_map = {}  # clip_id → list of blocks with emotion/energy
    for doc in (creative_ctx.get("semantic_docs", []) if creative_ctx else []):
        clip_id = doc.get("clip_id", "")
        for block in doc.get("blocks", []):
            key = f"{clip_id}/{block.get('label', '')}"
            semantic_map[key] = {
                "emotion": block.get("emotion", ""),
                "energy": block.get("energy", ""),
                "subtext": block.get("subtext", ""),
                "transcript": block.get("transcript", ""),
            }

    # Parse sfx_spec (pipeline-generated SFX suggestions)
    sfx_spec = creative_ctx.get("sfx_spec", []) if creative_ctx else []

    # Build briefs for each segment (between cuts)
    segments = []
    cut_times = [0.0] + [c["time_s"] for c in cuts] + [tl_info["duration_s"]]

    for i in range(len(cut_times) - 1):
        seg_start = cut_times[i]
        seg_end = cut_times[i + 1]
        seg_duration = seg_end - seg_start

        # Get subtitle text in this segment
        seg_subs = [s for s in subtitles
                    if s["timeline_end"] > seg_start and s["timeline_start"] < seg_end]
        seg_text = " ".join(s["text"] for s in seg_subs)

        # Position in energy arc (0.0 = start, 1.0 = end)
        position = (seg_start + seg_end) / 2 / tl_info["duration_s"]

        # Determine segment type
        if i == 0:
            seg_type = "hook"
        elif i == len(cut_times) - 2:
            seg_type = "endcard"
        else:
            seg_type = "body"

        # Find B-roll in this segment
        seg_broll = [br for br in broll
                     if br["end_s"] > seg_start and br["start_s"] < seg_end]

        # Find sfx_spec entries in this segment
        seg_sfx_spec = [s for s in sfx_spec
                        if seg_start <= s.get("timeline_start", -1) < seg_end]

        # Infer emotion from transcript keywords
        emotion = ""
        energy_level = ""

        # Check for key emotional keywords in transcript
        text_lower = seg_text.lower()
        if any(w in text_lower for w in ["paralysis", "quit", "hate", "pressure", "don't do"]):
            emotion = "vulnerable, determined"
            energy_level = "building from low"
        elif any(w in text_lower for w in ["announce", "post every", "single day", "commitment"]):
            emotion = "assertive, earnest"
            energy_level = "medium-high"
        elif any(w in text_lower for w in ["casey", "neistat", "vlog", "inspire"]):
            emotion = "nostalgic, admiring"
            energy_level = "steady"
        elif any(w in text_lower for w in ["last shot", "only reason", "recording today"]):
            emotion = "defiant, vulnerable"
            energy_level = "building to peak"
        elif any(w in text_lower for w in ["happy", "recorded", "almost didn't"]):
            emotion = "raw, relieved"
            energy_level = "quiet determination"

        # Map emotion to SFX parameters
        sfx_params = match_emotion_to_sfx_params(emotion)

        # Override for structural segments
        if seg_type == "hook":
            sfx_params["density"] = "moderate"
            sfx_params["prefer"] = ["punch", "accent"]
        elif seg_type == "endcard":
            sfx_params["density"] = "dense"
            sfx_params["prefer"] = ["riser", "whoosh", "punch"]
            sfx_params["avoid"] = []  # Clear any conflicts

        # Get the cut info (the cut that STARTS this segment)
        entering_cut = cuts[i - 1] if i > 0 else None

        segments.append({
            "start": seg_start,
            "end": seg_end,
            "duration": round(seg_duration, 3),
            "type": seg_type,
            "position": round(position, 2),
            "emotion": emotion,
            "energy": energy_level,
            "sfx_params": sfx_params,
            "transcript_preview": seg_text[:100],
            "broll": seg_broll,
            "sfx_spec": seg_sfx_spec,
            "entering_cut": entering_cut,
        })

    return segments


# ═══════════════════════════════════════════════════════════════════════════════
# 5. SFX PLANNING — CONTEXT-DRIVEN RULES ENGINE
# ═══════════════════════════════════════════════════════════════════════════════

def pick_from_pool(pool, used_recently, prefer_short=False, max_duration=None,
                   prefer_freq=None):
    """Pick a random SFX from a pool, avoiding recent repeats."""
    candidates = list(pool)
    if max_duration:
        candidates = [s for s in candidates if s["duration_s"] <= max_duration]
    if prefer_short:
        candidates.sort(key=lambda s: s["duration_s"])
        candidates = candidates[:max(3, len(candidates) // 2)]
    if prefer_freq:
        # Prefer SFX in a specific frequency band
        freq_matches = [s for s in candidates if s.get("freq_band") == prefer_freq]
        if freq_matches:
            candidates = freq_matches
    if len(candidates) > 1:
        candidates = [s for s in candidates if s["path"] not in used_recently] or candidates
    if not candidates:
        return None
    choice = random.choice(candidates)
    used_recently.add(choice["path"])
    return choice


def plan_sfx_placement(pools, tl_info, subtitles, music, segment_briefs,
                       video_style_tags=None):
    """
    Context-driven, semantically-aware SFX planning.
    
    Each segment brief dictates:
    - What roles to prefer (punch, whoosh, riser, accent, texture, punctuation)
    - What density (how many SFX events)
    - What energy level (affects volume recommendation)
    
    The video_style_tags parameter enables semantic scoring — each pick considers
    recognition risk, style fit, emotional temperature, and evocation relevance.
    """
    fps = tl_info["fps"]
    cuts = tl_info["cuts"]
    beat_times = music["beat_times"] if music else []
    plan = []
    used = set()
    vstyle = video_style_tags or ["modern", "clean"]

    # ── Process each segment ──
    for seg in segment_briefs:
        params = seg["sfx_params"]
        preferred_roles = params.get("prefer", ["whoosh"])
        avoided_roles = params.get("avoid", [])
        density = params.get("density", "sparse")
        budget = DENSITY_BUDGET.get(density, 1)
        energy = params.get("energy", "subtle")
        entering_cut = seg.get("entering_cut")
        placed_in_segment = 0

        # Volume based on energy
        vol_map = {
            "punchy": "-10 dB",
            "strong": "-8 dB",
            "subtle": "-22 dB",
            "textured": "-20 dB",
            "warm": "-20 dB",
            "rising": "-18 dB",
            "sharp": "-14 dB",
        }
        base_volume = vol_map.get(energy, "-18 dB")

        # ── Hook: always gets a punch at time 0 ──
        if seg["type"] == "hook":
            # Pick a punch — prefer textured if mood is "raw"
            prefer_texture = "raw" in seg.get("emotion", "").lower()
            punch_pool = pools.get("punch", [])
            if prefer_texture:
                noisy_punches = [s for s in punch_pool if s.get("texture") == "noisy"]
                if noisy_punches:
                    punch_pool = noisy_punches

            hook_ctx = {
                "emotion": seg.get("emotion", ""),
                "narrative": f"Hook opening — {seg.get('transcript_preview', '')[:80]}",
                "sfx_intent": "Strong opening impact to grab attention",
            }
            punch, sem_reasons = pick_best_sfx(
                punch_pool, used, hook_ctx, vstyle, max_duration=0.8
            )
            if punch:
                plan.append({
                    "sfx": punch,
                    "sfx_type": "hit",
                    "target_time_s": 0.0,
                    "placement_time_s": 0.0,
                    "placement_frame": 0,
                    "reason": f"Hook punch — {seg.get('emotion', 'hook')}",
                    "semantic": sem_reasons,
                    "volume_note": "-8 dB" if punch.get("freq_band") in ["sub", "low"] else "-12 dB",
                    "segment": seg["type"],
                    "emotion": seg.get("emotion", ""),
                })
                placed_in_segment += 1

        # ── Process the cut entering this segment ──
        if entering_cut and budget > 0:
            cut_time = entering_cut["time_s"]
            speech_on, _ = is_speech_active(cut_time, subtitles)
            snapped_time, was_snapped = snap_to_beat(cut_time, beat_times, tolerance_s=0.1)
            snap_note = f" [beat-snapped]" if was_snapped else ""

            # Build context for this cut point
            cut_ctx = {
                "emotion": seg.get("emotion", ""),
                "narrative": f"Cut at {cut_time:.1f}s — {seg.get('transcript_preview', '')[:80]}",
                "sfx_intent": f"Transition ({entering_cut['transition_type']}) into {seg['type']} segment",
            }

            # Riser if preferred and budget allows
            if "riser" in preferred_roles and "riser" not in avoided_roles and budget >= 2:
                riser_ctx = {**cut_ctx, "sfx_intent": f"Build tension before {entering_cut['transition_type']} at {cut_time:.1f}s"}
                riser, sem_reasons = pick_best_sfx(
                    pools.get("riser", []), used, riser_ctx, vstyle,
                    max_duration=min(4.0, seg["start"]),
                )
                if riser:
                    riser_start = snapped_time - riser["duration_s"]
                    if riser_start >= 0:
                        plan.append({
                            "sfx": riser,
                            "sfx_type": "riser",
                            "target_time_s": round(snapped_time, 3),
                            "placement_time_s": round(riser_start, 4),
                            "placement_frame": max(0, int(round(riser_start * fps))),
                            "reason": f"Riser → cut at {cut_time:.1f}s ({seg.get('emotion', '')}){snap_note}",
                            "semantic": sem_reasons,
                            "volume_note": "-20 dB (build under speech)" if speech_on else "-16 dB",
                            "segment": seg["type"],
                            "emotion": seg.get("emotion", ""),
                        })
                        placed_in_segment += 1

            # Whoosh on the cut
            if "whoosh" in preferred_roles and "whoosh" not in avoided_roles:
                whoosh_ctx = {**cut_ctx, "sfx_intent": f"Whoosh on {entering_cut['transition_type']} transition"}
                whoosh, sem_reasons = pick_best_sfx(
                    pools.get("whoosh", []), used, whoosh_ctx, vstyle,
                    prefer_short=True, max_duration=2.0,
                )
                if not whoosh:
                    # Reset used pool if exhausted
                    used_whoosh = {s["path"] for s in pools.get("whoosh", [])}
                    used -= used_whoosh
                    whoosh, sem_reasons = pick_best_sfx(
                        pools.get("whoosh", []), used, whoosh_ctx, vstyle,
                        prefer_short=True, max_duration=2.0,
                    )
                if whoosh:
                    pre_roll = whoosh["peak_s"]
                    placement = snapped_time - pre_roll
                    plan.append({
                        "sfx": whoosh,
                        "sfx_type": "whoosh",
                        "target_time_s": round(snapped_time, 3),
                        "placement_time_s": round(max(0, placement), 4),
                        "placement_frame": max(0, int(round(placement * fps))),
                        "reason": f"Whoosh → {entering_cut['transition_type']} at {cut_time:.1f}s ({seg.get('emotion', '')}){snap_note}",
                        "semantic": sem_reasons,
                        "volume_note": f"{base_volume} (ducked)" if speech_on else base_volume,
                        "segment": seg["type"],
                        "emotion": seg.get("emotion", ""),
                    })
                    placed_in_segment += 1

            # Punch/impact on the cut if preferred (typically endcard or high-energy)
            if "punch" in preferred_roles and "punch" not in avoided_roles and placed_in_segment < budget:
                if seg["type"] == "endcard" or density == "dense":
                    impact_ctx = {**cut_ctx, "sfx_intent": f"Impact punctuation at {seg['type']}"}
                    impact, sem_reasons = pick_best_sfx(
                        pools.get("punch", []), used, impact_ctx, vstyle,
                        max_duration=0.8,
                    )
                    if impact:
                        plan.append({
                            "sfx": impact,
                            "sfx_type": "hit",
                            "target_time_s": round(snapped_time, 3),
                            "placement_time_s": round(snapped_time, 4),
                            "placement_frame": int(round(snapped_time * fps)),
                            "reason": f"Impact at {seg['type']} ({cut_time:.1f}s){snap_note}",
                            "semantic": sem_reasons,
                            "volume_note": "-8 dB (punctuation)",
                            "segment": seg["type"],
                            "emotion": seg.get("emotion", ""),
                        })
                        placed_in_segment += 1

        # ── B-roll accents ──
        for br in seg.get("broll", []):
            if placed_in_segment >= budget + 1:  # B-roll gets +1 allowance
                break

            br_start = br["start_s"]
            speech_on, _ = is_speech_active(br_start, subtitles)
            snapped_br, was_snapped = snap_to_beat(br_start, beat_times, tolerance_s=0.1)
            snap_note = f" [beat-snapped]" if was_snapped else ""

            # Pick accent or punctuation for b-roll entries
            broll_ctx = {
                "emotion": seg.get("emotion", ""),
                "narrative": f"B-roll entry: {br['name']} at {br_start:.1f}s — {seg.get('transcript_preview', '')[:60]}",
                "sfx_intent": f"Subtle accent on B-roll entry ({br['name']})",
            }
            accent_roles = [r for r in ["accent", "punctuation"] if r not in avoided_roles]
            sfx = None
            sem_reasons = []
            for role in accent_roles:
                sfx, sem_reasons = pick_best_sfx(
                    pools.get(role, []), used, broll_ctx, vstyle,
                    max_duration=1.5, prefer_short=True,
                )
                if sfx:
                    break

            if sfx:
                placement = snapped_br - sfx["peak_s"]
                plan.append({
                    "sfx": sfx,
                    "sfx_type": "whoosh",  # accents go on whoosh track
                    "target_time_s": round(snapped_br, 3),
                    "placement_time_s": round(max(0, placement), 4),
                    "placement_frame": max(0, int(round(placement * fps))),
                    "reason": f"B-roll accent ({br['name']}) at {br_start:.1f}s ({seg.get('emotion', '')}){snap_note}",
                    "semantic": sem_reasons,
                    "volume_note": "-22 dB (subtle accent)",
                    "segment": seg["type"],
                    "emotion": seg.get("emotion", ""),
                })
                placed_in_segment += 1

        # ── SFX spec entries (pipeline suggestions) ──
        for spec in seg.get("sfx_spec", []):
            spec_time = spec.get("timeline_start", seg["start"])
            spec_effect = spec.get("effect", "")
            spec_note = spec.get("note", "")

            # Map spec effect to a role
            role = "whoosh"
            if "bass" in spec_effect or "drop" in spec_effect:
                role = "punch"
            elif "cymbal" in spec_effect or "riser" in spec_effect:
                role = "riser"
            elif "whoosh" in spec_effect:
                role = "whoosh"

            max_dur = 1.0 if role == "punch" else 5.0 if role == "riser" else 3.0
            spec_ctx = {
                "emotion": seg.get("emotion", ""),
                "narrative": f"Pipeline spec: {spec_note}",
                "sfx_intent": f"{spec_effect} — {spec_note}",
            }
            sfx, sem_reasons = pick_best_sfx(
                pools.get(role, []), used, spec_ctx, vstyle,
                max_duration=max_dur,
            )
            if sfx:
                pre_roll = sfx["peak_s"] if role in ["whoosh", "punch"] else 0
                placement = spec_time - pre_roll
                plan.append({
                    "sfx": sfx,
                    "sfx_type": "hit" if role == "punch" else "riser" if role == "riser" else "whoosh",
                    "target_time_s": round(spec_time, 3),
                    "placement_time_s": round(max(0, placement), 4),
                    "placement_frame": max(0, int(round(placement * fps))),
                    "reason": f"Pipeline spec: {spec_note} ({spec_effect})",
                    "semantic": sem_reasons,
                    "volume_note": f"{spec.get('volume_db', -14)} dB",
                    "segment": seg["type"],
                    "emotion": seg.get("emotion", ""),
                })

    # Sort by placement time
    plan.sort(key=lambda p: p["placement_time_s"])
    return plan


# ═══════════════════════════════════════════════════════════════════════════════
# 6. RESOLVE PLACEMENT
# ═══════════════════════════════════════════════════════════════════════════════

def find_or_create_sfx_tracks(timeline):
    track_map = {}
    audio_count = timeline.GetTrackCount("audio")
    for sfx_type, name in SFX_TRACK_NAMES.items():
        found = None
        for i in range(1, audio_count + 1):
            if timeline.GetTrackName("audio", i) == name:
                found = i
                break
        if found:
            track_map[sfx_type] = found
        else:
            timeline.AddTrack("audio", "stereo")
            new_idx = timeline.GetTrackCount("audio")
            timeline.SetTrackName("audio", new_idx, name)
            track_map[sfx_type] = new_idx
            audio_count = new_idx
    return track_map


def clear_all_sfx_tracks(timeline):
    total_deleted = 0
    audio_count = timeline.GetTrackCount("audio")
    for i in range(1, audio_count + 1):
        name = timeline.GetTrackName("audio", i)
        if name in SFX_TRACK_NAMES.values():
            items = timeline.GetItemListInTrack("audio", i) or []
            if items:
                timeline.DeleteClips(items)
                total_deleted += len(items)
                print(f"  Cleared {len(items)} clips from '{name}'", file=sys.stderr)
    return total_deleted


def _parse_volume_db(volume_note: str) -> float | None:
    """Parse a volume note like '-8 dB' or '-22 dB (subtle accent)' into a float dB value."""
    if not volume_note:
        return None
    import re
    match = re.search(r'(-?\d+(?:\.\d+)?)\s*dB', volume_note)
    if match:
        return float(match.group(1))
    return None


def _db_to_gain(db: float) -> float:
    """Convert dB to linear gain (0.0 to 1.0+).
    
    0 dB = 1.0, -6 dB ≈ 0.5, -20 dB ≈ 0.1, -inf dB = 0.0
    """
    if db <= -100:
        return 0.0
    return 10 ** (db / 20.0)


def _apply_volume_to_placed_clip(timeline, track_idx, record_frame, volume_note):
    """Find the just-placed clip and apply volume.
    
    DaVinci Resolve's scripting API requires finding the clip on the
    timeline track and setting its properties. We look for the clip
    whose start frame matches record_frame on the given track.
    
    Returns True if volume was successfully applied.
    """
    db_val = _parse_volume_db(volume_note)
    if db_val is None:
        return False
    
    try:
        # Get all items on this audio track
        items = timeline.GetItemListInTrack("audio", track_idx) or []
        
        target_clip = None
        for item in items:
            if abs(item.GetStart() - record_frame) <= 2:  # ±2 frame tolerance
                target_clip = item
                break
        
        if not target_clip:
            print(f"    ⚠ Could not find placed clip at frame {record_frame} on track {track_idx}",
                  file=sys.stderr)
            return False
        
        # Convert dB to Resolve's volume scale
        # Resolve uses a linear scale where 0.0 = -∞ dB, 1.0 = 0 dB
        gain = _db_to_gain(db_val)
        
        # Try SetProperty first (works for some Resolve versions)
        success = target_clip.SetProperty("Volume", gain)
        if not success:
            # Fallback: try SetClipProperty via string
            success = target_clip.SetProperty("volume", str(gain))
        
        if success:
            print(f"    ↳ Volume set: {db_val} dB (gain={gain:.3f})", file=sys.stderr)
            return True
        else:
            # Another approach: use Fairlight audio properties
            # In some Resolve versions, volume is set on the Fairlight page
            try:
                # Try using the clip's own properties dict
                props = target_clip.GetProperty()
                if isinstance(props, dict):
                    target_clip.SetProperty("Volume", gain)
                    return True
            except Exception:
                pass
            
            print(f"    ⚠ Volume API failed for {db_val} dB — clip found but SetProperty returned False",
                  file=sys.stderr)
            return False
            
    except Exception as e:
        print(f"    ⚠ Volume error: {e}", file=sys.stderr)
        return False


def place_sfx_on_timeline(media_pool, timeline, sfx_plan, track_map):
    fps = float(timeline.GetSetting("timelineFrameRate") or 30)
    start_frame = timeline.GetStartFrame()

    root = media_pool.GetRootFolder()
    sfx_bin = None
    for sub in root.GetSubFolderList():
        if sub.GetName() == "SFX_Auto":
            sfx_bin = sub
            break
    if not sfx_bin:
        sfx_bin = media_pool.AddSubFolder(root, "SFX_Auto")
    media_pool.SetCurrentFolder(sfx_bin)

    unique_files = list(set(item["sfx"]["path"] for item in sfx_plan))
    imported = {}
    existing_clips = sfx_bin.GetClipList() or []
    for fpath in unique_files:
        found = None
        for clip in existing_clips:
            if clip.GetClipProperty("File Path") == fpath:
                found = clip
                break
        if found:
            imported[fpath] = found
        else:
            clips = media_pool.ImportMedia([fpath])
            if clips and len(clips) > 0:
                imported[fpath] = clips[0]
                print(f"  Imported: {os.path.basename(fpath)}", file=sys.stderr)

    placed = 0
    for item in sfx_plan:
        sfx = item["sfx"]
        mpi = imported.get(sfx["path"])
        if not mpi:
            print(f"  SKIP: {sfx['file']}", file=sys.stderr)
            continue

        sfx_type = item.get("sfx_type", "whoosh")
        track_idx = track_map.get(sfx_type, track_map.get("whoosh", 3))
        track_name = SFX_TRACK_NAMES.get(sfx_type, "SFX")

        record_frame = start_frame + item["placement_frame"]
        clip_frames = int(math.ceil(sfx["duration_s"] * fps))

        clip_info = {
            "mediaPoolItem": mpi,
            "startFrame": 0,
            "endFrame": clip_frames,
            "trackIndex": track_idx,
            "recordFrame": record_frame,
            "mediaType": 2,
        }

        result = media_pool.AppendToTimeline([clip_info])
        if result:
            placed += 1
            emotion = item.get("emotion", "")
            emotion_tag = f" [{emotion}]" if emotion else ""

            # ── Apply volume ducking ──
            volume_note = item.get("volume_note", "")
            volume_applied = _apply_volume_to_placed_clip(
                timeline, track_idx, record_frame, volume_note
            )
            vol_tag = f" vol={volume_note}" if volume_applied else ""

            print(
                f"  ✓ [{track_name:12s}] {sfx['file']:40s} at {item['target_time_s']:6.1f}s  "
                f"{item['reason']}{emotion_tag}{vol_tag}",
                file=sys.stderr,
            )
        else:
            print(
                f"  ✗ [{track_name:12s}] FAILED: {sfx['file']} at {item['target_time_s']:.1f}s",
                file=sys.stderr,
            )

    return placed


# ═══════════════════════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description="SFX Placer v3 — Acoustic-first, context-driven")
    parser.add_argument("--analyze", action="store_true", help="Dry run")
    parser.add_argument("--place", action="store_true", help="Analyze + place")
    parser.add_argument("--clear", action="store_true", help="Clear all SFX tracks")
    parser.add_argument("--sfx-lib", default=SFX_LIBRARY, help="SFX library path")
    parser.add_argument("--subtitles", default=SUBTITLES_PATH, help="Subtitles JSON")
    args = parser.parse_args()

    if not any([args.analyze, args.place, args.clear]):
        parser.print_help()
        sys.exit(1)

    # ── Clear ──
    if args.clear:
        _, _, _, timeline = connect_resolve()
        deleted = clear_all_sfx_tracks(timeline)
        print(f"Cleared {deleted} total SFX clips")
        return

    # ── Library analysis + semantic enrichment ──
    print("\n═══ SFX LIBRARY (Acoustic Roles) ═══", file=sys.stderr)
    library = analyze_library(args.sfx_lib, ANALYSIS_CACHE)
    print(f"  {len(library)} files analyzed", file=sys.stderr)

    # Load and merge semantic profiles
    semantic_profiles = load_semantic_profiles()
    enriched_count = enrich_library_with_semantics(library, semantic_profiles)
    print(f"  {enriched_count} semantic profiles loaded", file=sys.stderr)

    pools, uncategorized = build_role_pools(library)
    for role, items in pools.items():
        if items:
            names = [s["file"] for s in items[:4]]
            print(f"  {role:14s}: {len(items):2d} files — {', '.join(names)}{'...' if len(items) > 4 else ''}", file=sys.stderr)
    if uncategorized:
        print(f"  {'uncategorized':14s}: {len(uncategorized):2d} files (no matching role)", file=sys.stderr)

    # Show recognition risk summary
    risk_counts = {}
    for sfx in library:
        r = sfx.get("recognition_risk", "unknown")
        risk_counts[r] = risk_counts.get(r, 0) + 1
    print(f"  Recognition risk: {risk_counts}", file=sys.stderr)

    # ── Timeline ──
    print("\n═══ TIMELINE ═══", file=sys.stderr)
    resolve, project, media_pool, timeline = connect_resolve()
    tl_info = get_timeline_info(timeline)
    print(
        f"  {timeline.GetName()}  {tl_info['duration_s']:.1f}s  {tl_info['fps']}fps  "
        f"{len(tl_info['clips'])} clips  {len(tl_info['cuts'])} cuts  {len(tl_info['broll'])} b-roll",
        file=sys.stderr,
    )

    # ── Speech + Music ──
    subtitles = load_subtitles(args.subtitles)
    music = load_music_analysis(MUSIC_ANALYSIS)
    print(f"  {len(subtitles)} subtitles", file=sys.stderr)
    if music:
        print(f"  Music: {music['tempo']:.0f} BPM, {len(music['beat_times'])} beats", file=sys.stderr)

    # ── Creative context ──
    print("\n═══ CREATIVE CONTEXT ═══", file=sys.stderr)
    creative_ctx = load_creative_context(PIPELINE_DATA)
    if creative_ctx:
        cd = creative_ctx.get("creative_direction", {})
        print(f"  Mood: {cd.get('target_mood', 'unknown')}", file=sys.stderr)
        print(f"  Energy: {cd.get('target_energy', 'unknown')}", file=sys.stderr)
        sfx_specs = creative_ctx.get("sfx_spec", [])
        print(f"  Pipeline SFX suggestions: {len(sfx_specs)}", file=sys.stderr)
    else:
        print("  No creative context found — using defaults", file=sys.stderr)

    # ── Segment briefs ──
    print("\n═══ SEGMENT BRIEFS ═══", file=sys.stderr)
    briefs = build_segment_briefs(tl_info, subtitles, creative_ctx)
    for b in briefs:
        params = b["sfx_params"]
        density = params.get("density", "?")
        budget = DENSITY_BUDGET.get(density, 0)
        print(
            f"  {b['start']:5.1f}s - {b['end']:5.1f}s  [{b['type']:8s}]  "
            f"emotion=\"{b['emotion']}\"  density={density}({budget})  "
            f"prefer={params.get('prefer', [])}  avoid={params.get('avoid', [])}",
            file=sys.stderr,
        )
        if b.get("broll"):
            for br in b["broll"]:
                print(f"    ↳ B-roll: {br['name']} at {br['start_s']:.1f}s", file=sys.stderr)
        if b.get("sfx_spec"):
            for spec in b["sfx_spec"]:
                print(f"    ↳ Spec: {spec.get('effect')} at {spec.get('timeline_start')}s — {spec.get('note')}", file=sys.stderr)

    # ── Plan ──
    print("\n═══ PLACEMENT PLAN ═══", file=sys.stderr)
    # Infer video style tags from creative direction
    cd_for_style = creative_ctx.get("creative_direction", {}) if creative_ctx else {}
    vstyle = infer_video_style_tags(cd_for_style)
    print(f"  Video style tags: {vstyle}", file=sys.stderr)
    plan = plan_sfx_placement(pools, tl_info, subtitles, music, briefs,
                              video_style_tags=vstyle)

    for i, item in enumerate(plan):
        sfx = item["sfx"]
        emotion = item.get("emotion", "")
        emotion_tag = f" [{emotion}]" if emotion else ""
        sem = item.get("semantic", [])
        sem_short = "; ".join(r for r in sem if r.startswith("✓") or r.startswith("⛔") or r.startswith("⚠"))[:80]
        print(
            f"  {i + 1:2d}. [{item.get('sfx_type', '?'):6s}] {sfx['file']:40s}  "
            f"@{item['placement_time_s']:6.2f}s → {item['target_time_s']:6.1f}s  "
            f"{item['reason']}{emotion_tag}",
            file=sys.stderr,
        )
        if sem_short:
            print(f"      SEM: {sem_short}", file=sys.stderr)

    # JSON plan
    plan_json = []
    for item in plan:
        p = dict(item)
        p["sfx_file"] = p["sfx"]["path"]
        p["sfx_name"] = p["sfx"]["file"]
        p["sfx_duration_s"] = p["sfx"]["duration_s"]
        p["sfx_roles"] = p["sfx"].get("roles", [])
        del p["sfx"]
        plan_json.append(p)
    print(json.dumps(plan_json, indent=2, default=str))

    # ── Place ──
    if args.place:
        print("\n═══ PLACING SFX ═══", file=sys.stderr)
        track_map = find_or_create_sfx_tracks(timeline)
        for sfx_type, idx in track_map.items():
            print(f"  Track {idx}: {SFX_TRACK_NAMES[sfx_type]}", file=sys.stderr)
        placed = place_sfx_on_timeline(media_pool, timeline, plan, track_map)
        print(f"\n  ✅ Placed {placed}/{len(plan)} SFX clips on timeline.", file=sys.stderr)

        print("\n  ⚠️  Volume levels (manual adjustment recommended):", file=sys.stderr)
        for item in plan_json:
            print(
                f"    {item['sfx_name']:40s} at {item['target_time_s']:5.1f}s → {item['volume_note']}",
                file=sys.stderr,
            )


if __name__ == "__main__":
    main()
