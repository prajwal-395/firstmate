"""The last hop of the audio mix: planned dB onto a rendered timeline.

`mesh_spine` decides a `music_behavior` per block, `audio_mix` (5.02)
turns each word into a dB target through `library/tools/music_behavior.py`,
and `compile_manifest` carries the result to the renderer.  The plan was
always good.  What was missing was delivery: the renderer turned every
automation entry into a cyan timeline marker - a note asking a human to
set the level by hand - and set no level at all.  A block the spine
planned `silent` played music at full level in the finished video.

**The DaVinci scripting API cannot set an audio level, and that is a
complete enumeration rather than a failed search.**  An audio
`TimelineItem` has no property dictionary at all (`GetProperty()` returns
`{}`), so `SetProperty("Volume", ...)` returns False because the property
system on that object is empty, not because the name is wrong.  The whole
documented audio surface is `GetFairlightPresets`,
`ApplyFairlightPresetToCurrentTimeline` and
`InsertAudioToCurrentTrackAtPlayhead`, none of which is a fader.  Do not
re-probe it.

**OpenTimelineIO is the route.**  Resolve exports and imports OTIO, and
its OTIO carries Fairlight clip volume in plain JSON, in dB, with
keyframes - no fader law, no unit conversion.  This module owns that
vocabulary: the shape of the parameter, where a keyframe's frame number
is measured from, and how the per-block plan becomes a curve.

Three facts about the format, each of which was measured rather than
assumed, and each of which silently produces nothing if got wrong:

1. **The `volume` parameter is ABSENT from an untouched export.**
   Resolve writes `"Parameters": []` on the clip-volume effect whenever
   every value is at its default, so a patcher that looks for an existing
   `"Parameter ID": "volume"` finds nothing and changes nothing.  The
   parameter has to be INSERTED - `volume_parameter` below is its shape.

2. **A keyframe's frame number is measured from the CLIP'S START ON THE
   TIMELINE**, not from the start of the timeline and not from the
   source in-point.  Measured with two renders: a music clip placed at
   timeline frame 150 with source in-point 0, and again with source
   in-point 200; both times keys at 300..450 put the floor at 15.0-20.0s
   in the rendered file, which is timeline frames 450..600.  Under a
   timeline-absolute reading it would have been 10.0-15.0s; under a
   source-relative one, 8.3-13.3s.

3. **`ImportTimelineFromFile` returns None, with no diagnostic, if ANY
   referenced media file is missing.**  Not a warning, not a partial
   import - nothing.  `unresolvable_media` is the guard, so the run can
   say which file is missing instead of reporting that OTIO "did not
   work".

Resolve interpolates linearly between keys, so a PAIR of keys at the
same value is a plateau and the gap between two plateaus is a ramp.  That
is what `music_curve` builds, and it is why a planned silence is
measurable in the render: the plateau in the middle of the block is the
level the plan asked for, undiluted by the fade on either side.

What an OTIO import costs is documented in AGENTS.md section 5: the
import REBUILDS the timeline, and Fusion comps and CDL grades do not
survive it.  Placement, transform, markers and native transitions do.
The renderer therefore round-trips at PLACEMENT time, before the Fusion
pass and the grade, which is the order it already ran in.


Rules relocated from AGENTS.md 5
--------------------------------
These are the engine's rules for this module.  They lived in
AGENTS.md until it was split by subsystem; the wording is unchanged,
so each rule is findable by its own words, and AGENTS.md 5
keeps the headline and points here.

Every planned dB - the bed's per-block curve and each clip's `volume_db` - reaches Fairlight
by ONE route: `library/tools/otio_mix.py` writes it into an OpenTimelineIO export and
`library/tools/execution/deliver_audio_mix.py` imports the result back.
Resolve's OTIO carries clip volume in plain JSON, **in dB**, with keyframes.
[why - the measured renders, and the routes that were rejected](docs/RULE_EVIDENCE.md#the-mix-goes-through-otio)
- **The import REBUILDS the timeline.** Fusion comps and CDL grades do NOT survive it. The
  placement, transform (`_apply_conform`), timeline markers and native transitions do.
  `tests/scenarios/test_audio_mix_delivery.py` drives a whole build and asserts the comps are still there.
- **The `volume` parameter is ABSENT from an untouched export**: it must be INSERTED, not patched.
- **A keyframe's frame number is measured from the CLIP'S START ON THE TIMELINE**.
- **`ImportTimelineFromFile` answers None with no diagnostic** when a referenced media file is
- A cyan `UNAPPLIED target` marker is the FALLBACK, written only when the route declines and
  saying so.
"""

from __future__ import annotations

import json
import os

# ── The format ──────────────────────────────────────────────────────

VOLUME_EFFECT_NAME = "Fairlight Clip Volume and Fades"
"""Resolve's own name for the clip-volume effect, in `Resolve_OTIO`
metadata.  Every audio clip carries one, whether or not it carries a
level."""

VOLUME_PARAMETER_ID = "volume"

MIN_VOLUME_DB = -100.0
MAX_VOLUME_DB = 30.0
"""Resolve's own bounds, read off the parameter it writes when a level is
set.  `music_behavior.SILENT_LEVEL_DB` is -96 dB, which is
inside them - silence does not need a special case."""

RESOLVE_METADATA_KEY = "Resolve_OTIO"
CLIP_SCHEMA_PREFIX = "Clip"
AUDIO_TRACK_KIND = "Audio"

# How far a planned start frame may sit from the frame a clip really
# landed on before the two stop being the same clip.  Placement is exact
# in every run measured; this exists so a one-frame rounding difference
# between `round(seconds * fps)` in two places does not drop a level.
MATCH_TOLERANCE_FRAMES = 2
LEVEL_TOLERANCE_DB = 0.05


class OtioMixError(RuntimeError):
    """The mix could not be written onto the timeline."""


def volume_parameter(value_db: float, keyframes: dict | None = None) -> dict:
    """The `volume` parameter as Resolve writes it, ready to insert.

    `value_db` is dB directly.  `keyframes` maps a frame number measured
    from the clip's start on the timeline to a dB value; an empty or
    absent map is a static level.
    """
    return {
        "Parameter ID": VOLUME_PARAMETER_ID,
        "Parameter Value": _clamp_db(value_db),
        "Default Parameter Value": 0.0,
        "Variant Type": "Double",
        "minValue": MIN_VOLUME_DB,
        "maxValue": MAX_VOLUME_DB,
        "Key Frames": {
            str(int(frame)): {"Value": _clamp_db(db), "Variant Type": "Double"}
            for frame, db in sorted((keyframes or {}).items())
        },
    }


def _clamp_db(value) -> float:
    return float(min(MAX_VOLUME_DB, max(MIN_VOLUME_DB, float(value))))


# ── Reading an OTIO ─────────────────────────────────────────────────

def audio_clips(otio: dict):
    """Every audio clip in the timeline, with where it starts.

    Yields `(track_name, clip, start_frame, frame_count)`.  A clip's
    start is the sum of the durations before it on its own track, which
    is how OTIO expresses position - it has no absolute field.
    """
    for track in otio.get("tracks", {}).get("children", []):
        if track.get("kind") != AUDIO_TRACK_KIND:
            continue
        position = 0
        for child in track.get("children", []):
            span = child.get("source_range") or {}
            frames = int(round(float(
                (span.get("duration") or {}).get("value", 0) or 0)))
            if str(child.get("OTIO_SCHEMA", "")).startswith(CLIP_SCHEMA_PREFIX):
                yield track.get("name", ""), child, position, frames
            position += frames


def clip_media_path(clip: dict) -> str:
    """The file a clip plays, or "" when it names none."""
    key = clip.get("active_media_reference_key", "")
    reference = (clip.get("media_references") or {}).get(key) or {}
    url = reference.get("target_url", "") or ""
    return url[len("file://"):] if url.startswith("file://") else url


def unresolvable_media(otio: dict) -> list:
    """Referenced files that are not on disk, deduplicated, in order.

    `ImportTimelineFromFile` answers None for a whole timeline when one
    of these is missing, and says nothing about which.  Call this first
    so the run can name the file.
    """
    missing, seen = [], set()
    for track in otio.get("tracks", {}).get("children", []):
        for child in track.get("children", []):
            if not str(child.get("OTIO_SCHEMA", "")).startswith(CLIP_SCHEMA_PREFIX):
                continue
            path = clip_media_path(child)
            if path and path not in seen:
                seen.add(path)
                if not os.path.exists(path):
                    missing.append(path)
    return missing


def read_levels(otio: dict) -> list:
    """The level on every audio clip that carries one.

    The reader half of this module: what `apply_mix` wrote can be read
    back off a fresh export, which is how the renderer checks the mix
    reached the timeline rather than assuming the import obeyed.
    """
    found = []
    for track_name, clip, start, frames in audio_clips(otio):
        parameter = _volume_parameter_of(clip)
        if parameter is None:
            continue
        found.append({
            "track": track_name,
            "source_file": clip_media_path(clip),
            "start_frame": start,
            "frame_count": frames,
            "level_db": float(parameter.get("Parameter Value", 0.0)),
            "keyframes": {
                int(frame): float(entry.get("Value", 0.0))
                for frame, entry in (parameter.get("Key Frames") or {}).items()
            },
        })
    return found


def _volume_effect_of(clip: dict):
    for effect in clip.get("effects", []) or []:
        metadata = (effect.get("metadata") or {}).get(RESOLVE_METADATA_KEY) or {}
        if metadata.get("Effect Name") == VOLUME_EFFECT_NAME:
            return metadata
    return None


def _volume_parameter_of(clip: dict):
    effect = _volume_effect_of(clip)
    for parameter in (effect or {}).get("Parameters", []) or []:
        if parameter.get("Parameter ID") == VOLUME_PARAMETER_ID:
            return parameter
    return None


# ── Turning the plan into a curve ───────────────────────────────────

def music_curve(automation: list, *, fps: float, clip_start_frame: int,
                clip_frame_count: int, fade_seconds: float,
                crossfade_in_seconds: float = 0.0,
                crossfade_out_seconds: float = 0.0,
                fade_out_seconds: float = 0.0) -> dict:
    """The bed's level over one music clip, as clip-relative keyframes.

    `automation` is `audio_mix.music_automation` verbatim - one entry per
    spine block, carrying `timeline_start`, `timeline_end` and the
    `target_level_db` step 5.02 solved from the separation its mix
    engineer decided (`library/tools/decided_value.py`).  This
    function DELIVERS those numbers; it never revises one.

    Each run of blocks planning the same level becomes one plateau, and
    each boundary between two different levels becomes a ramp centred on
    it.  The ramp is `fade_seconds` long at most, and never eats more
    than a quarter of either neighbouring block, so every block keeps a
    plateau in its middle at exactly the level it was planned at.  That
    is what makes the plan measurable in the render rather than merely
    present on the timeline. When the plan carries word intervals, those
    levels apply under words and the declared recovery curve raises the
    bed through gaps.
    """
    if clip_frame_count <= 0:
        return {}

    last_frame = clip_frame_count - 1
    segments = _merge_runs(automation, fps=fps, origin=clip_start_frame,
                           limit_frame=last_frame)
    if not segments:
        return {}

    half_limit = max(1, int(round(fade_seconds * fps / 2.0)))

    keys = [(0, segments[0][2])]
    for index in range(len(segments) - 1):
        _, end, level = segments[index]
        next_start, next_end, next_level = segments[index + 1]
        half = min(half_limit,
                   max(1, (end - segments[index][0]) // 4),
                   max(1, (next_end - next_start) // 4))
        keys.append((end - half, level))
        keys.append((end + half, next_level))
    keys.append((last_frame, segments[-1][2]))

    # Word-level ducking is derived from the timed spine after the model
    # answer. The decided block level is what the bed plays under speech;
    # each declared gap recovers to that level plus the plan's duck depth.
    word_keys = {}
    word_blocks = []
    for entry in automation or []:
        intervals = entry.get("word_intervals") or []
        gap_level = entry.get("word_gap_level_db")
        speech_level = entry.get("target_level_db")
        if not intervals or gap_level is None or speech_level is None:
            continue
        block_start = max(0, min(
            last_frame, int(round(float(entry["timeline_start"]) * fps))
            - clip_start_frame))
        block_end = max(0, min(
            last_frame, int(round(float(entry["timeline_end"]) * fps))
            - clip_start_frame))
        release = max(1, int(round(
            float(entry["word_gap_release_ms"]) * fps / 1000.0)))
        frames = sorted((max(block_start, int(round(float(start) * fps))
                             - clip_start_frame),
                         min(block_end, int(round(float(end) * fps))
                             - clip_start_frame))
                        for start, end in intervals)
        frames = [(start, end) for start, end in frames if end > start]
        if not frames:
            continue

        word_blocks.append((block_start, block_end))
        groups = []
        for start, end in frames:
            if groups and start - groups[-1][1] < release:
                groups[-1] = (groups[-1][0], max(groups[-1][1], end))
            else:
                groups.append((start, end))
        # Resolve interpolates linearly between keys, so a recovered level
        # is HELD until one frame before the next word: without that key
        # the gap is a triangle that peaks at the gap level for one frame
        # and delivers about half the planned recovery (measured on the
        # K2 exports: 2-5 dB of a planned 10).  One frame is the shortest
        # duck a keyframe can express, not a chosen attack time.  A gap
        # after the block's last word holds to the block's end, where the
        # next block's own first key takes over.
        if groups[0][0] > block_start:
            word_keys[block_start] = float(gap_level)
            if groups[0][0] - 1 > block_start:
                word_keys[groups[0][0] - 1] = float(gap_level)
        for index, (start, end) in enumerate(groups):
            word_keys[start] = float(speech_level)
            word_keys[end] = float(speech_level)
            next_start = (groups[index + 1][0]
                          if index + 1 < len(groups) else block_end)
            if next_start - end >= release:
                recovery_frame = min(end + release, block_end)
                word_keys[recovery_frame] = float(gap_level)
                if next_start - 1 > recovery_frame:
                    word_keys[next_start - 1] = float(gap_level)

    if word_keys:
        # Inside a block whose words are keyed, the word keys ARE the
        # curve: a block-boundary ramp key a quarter-block in dragged a
        # recovered trailing gap back down to the ducked level (block 1
        # of the K2 MX3.1 export: -7.2 dB at frame 129, -17.2 by 146).
        merged_keys = {int(frame): float(level) for frame, level in keys
                       if not any(start <= frame <= end
                                  for start, end in word_blocks)}
        merged_keys.update(word_keys)
        keys = sorted(merged_keys.items())

    # THE SPLICE. A bed that is several pieces has a boundary between two
    # of them, and a boundary the plan declared a crossfade for is
    # delivered as a real OVERLAP: the outgoing segment plays on past the
    # boundary while the incoming one plays from it, and these are the two
    # halves of the fade across that overlap. A splice that declared NO
    # crossfade gets nothing here - a hard splice is the absence of
    # decoration, and no length is invented for it (AGENTS.md 10.5, and
    # library/tools/music_bed.py).
    if crossfade_in_seconds > 0:
        ramp = min(last_frame, max(1, int(round(crossfade_in_seconds * fps))))
        keys = ([(0, MIN_VOLUME_DB), (ramp, _level_at(keys, ramp))]
                + [(f, v) for f, v in keys if f > ramp])
    if crossfade_out_seconds > 0:
        ramp = min(last_frame, max(1, int(round(crossfade_out_seconds * fps))))
        hold = max(0, last_frame - ramp)
        keys = ([(f, v) for f, v in keys if f < hold]
                + [(hold, _level_at(keys, hold)), (last_frame, MIN_VOLUME_DB)])
    # Rung 7: the plan's stated fade-out - the ramp down over the
    # piece's last seconds where it ends into silence (SD3.2's "music
    # out with a 2-second fade"). Same gesture as a crossfade-out, and
    # refused beside one on the same segment (library/tools/music_bed),
    # so the two never compose here.
    if fade_out_seconds > 0:
        ramp = min(last_frame, max(1, int(round(fade_out_seconds * fps))))
        hold = max(0, last_frame - ramp)
        keys = ([(f, v) for f, v in keys if f < hold]
                + [(hold, _level_at(keys, hold)), (last_frame, MIN_VOLUME_DB)])

    return _tidy(sorted(keys), last_frame)


def _level_at(keys: list, frame: int) -> float:
    """The planned level at ``frame``, read off the keys already built.

    A crossfade ramps to whatever the block under it planned; it never
    substitutes a level of its own.
    """
    level = keys[0][1]
    for key_frame, key_level in keys:
        if key_frame <= frame:
            level = key_level
        else:
            break
    return level


def _merge_runs(automation: list, *, fps: float, origin: int,
                limit_frame: int | None = None) -> list:
    """`(start_frame, end_frame, level_db)` runs, clip-relative.

    Consecutive blocks planning the same level are one run: two
    `background` blocks in a row are one plateau, not two with a
    pointless ramp between them.
    """
    runs = []
    for entry in automation or []:
        level = float(entry["target_level_db"])
        start = int(round(float(entry.get("timeline_start", 0.0)) * fps)) - origin
        end = int(round(float(entry.get("timeline_end", 0.0)) * fps)) - origin
        # A bed SEGMENT covers part of the timeline, so the blocks outside
        # it are not this clip's to carry. Clamping rather than dropping
        # keeps the block that straddles the splice boundary on both
        # sides of it, at the level it was planned at.
        if limit_frame is not None:
            start = max(0, min(start, limit_frame))
            end = max(0, min(end, limit_frame))
        if end <= start:
            continue
        if runs and runs[-1][2] == level and runs[-1][1] == start:
            runs[-1] = (runs[-1][0], end, level)
        else:
            runs.append((start, end, level))
    return runs


def _tidy(keys: list, last_frame: int) -> dict:
    """Clamp into the clip, keep the order, and never lose a level.

    Two keys cannot share a frame, and a ramp shortened to nothing by
    clamping would silently drop the level it was carrying.  Pushing a
    collision one frame later keeps every planned level on the curve at
    the cost of a frame of ramp.
    """
    curve, cursor = {}, -1
    for frame, level in keys:
        frame = max(0, min(last_frame, int(frame)))
        if frame <= cursor:
            frame = cursor + 1
        if frame > last_frame:
            # No room left: the last level already written wins, which is
            # the one nearest the end of the clip.
            curve[last_frame] = _clamp_db(level)
            break
        curve[frame] = _clamp_db(level)
        cursor = frame
    return curve


# ── Dialogue-cleanup stems: cleaned audio onto speech clips ──────────

def stem_swaps(manifest: dict) -> list:
    """What the cleanup plan asks to swap, as clip-addressed stems.

    Reads `manifest.audio.dialogue_cleanup.stems` (staged at compile by
    `library/tools/dialogue_cleanup.stage_deepfilternet`, fidelity rung
    R5d). A swap names the ORIGINAL source file and the timeline frame
    the clip starts on - the pair that identifies a placed clip - plus
    the stem file, which IS that clip's played range. The swap itself
    is `dialogue_cleanup.rewrite_clip_media_to_stem`, applied in the
    same OTIO transaction as the levels below.
    """
    swaps = []
    cleanup = (manifest.get("audio") or {}).get("dialogue_cleanup") or {}
    for stem in cleanup.get("stems", []) or []:
        source = stem.get("source_file", "")
        stem_file = stem.get("stem_file", "")
        start = stem.get("timeline_in_frame")
        if not source or not stem_file or start is None:
            continue
        swaps.append({
            "source_file": source,
            "stem_file": stem_file,
            "start_frame": int(start),
            "label": stem.get("label", os.path.basename(stem_file)),
        })
    return swaps


def apply_stem_swaps(otio: dict, swaps: list) -> dict:
    """Point each named clip at its cleaned stem. Returns what happened.

    A swap that matches no clip, or whose stem is not on disk, is
    REPORTED, never dropped quietly - like `apply_mix`, a level (or a
    stem) nobody applied is the defect this module exists to remove.
    """
    from library.tools import dialogue_cleanup as _dc

    placed = list(audio_clips(otio))
    used, applied, unmatched = set(), [], []
    for swap in swaps or []:
        target = {"source_file": swap["source_file"],
                  "start_frame": swap["start_frame"]}
        index = _match(placed, used, target)
        if index is None:
            unmatched.append(dict(swap, reason="matched no clip"))
            continue
        used.add(index)
        _, clip, start, frames = placed[index]
        try:
            _dc.rewrite_clip_media_to_stem(clip, swap["stem_file"])
        except _dc.DialogueCleanupRefused as exc:
            unmatched.append(dict(swap, reason=str(exc.what)[:200]))
            continue
        applied.append(dict(swap, matched_start_frame=start,
                            matched_frame_count=frames))
    return {"applied": applied, "unmatched": unmatched}


def verify_stems(otio: dict, applied: list) -> list:
    """Stems that did not come back off a re-export, as complaints.

    The reader half of the swap: what `apply_stem_swaps` wrote is read
    back off a fresh export, the same way `verify` reads the levels.
    """
    on_timeline = {(clip_media_path(clip), start): True
                   for _, clip, start, _ in audio_clips(otio)}
    complaints = []
    for swap in applied or []:
        key = (os.path.abspath(swap["stem_file"]),
               swap.get("matched_start_frame", swap["start_frame"]))
        present = any(os.path.abspath(path) == key[0]
                      for (path, _s) in on_timeline)
        if not present:
            complaints.append(
                f"{swap['label']}: stem not on the imported timeline")
    return complaints


# ── Writing the plan into an OTIO ───────────────────────────────────

def mix_targets(manifest: dict, *, fps: float) -> list:
    """What the manifest asks to be set, as clip-addressed levels.

    A target names the file and the frame the clip starts on, because
    that pair identifies a placed clip exactly and survives the SFX
    allocator spreading one bucket across A3, A4 and A5.

    Two things are delivered.  The music bed carries the per-block curve
    `audio_mix.music_automation` planned.  Every other planned audio clip
    carries its static `volume_db` - which until now reached nothing,
    because `SetProperty("Volume", ...)` is one of the calls Resolve
    declines - plus, where step 4.04 cut a sound short, the one-frame
    de-click ramp `declick_curve` builds from its `fade_out_seconds`.
    """
    tracks = manifest.get("tracks", {}) or {}
    audio_mix = manifest.get("audio_mix", {}) or {}
    automation = audio_mix.get("music_automation", []) or []
    fade_seconds = float(
        ((audio_mix.get("track_levels") or {}).get("A2_music") or {})
        .get("fade_duration_seconds", 1.0))

    targets = []
    for clip in tracks.get("A2", {}).get("clips", []) or []:
        source = clip.get("source_file", "")
        if not source:
            continue
        start = _start_frame(clip, fps)
        frames = max(0, _end_frame(clip, fps) - start)
        targets.append({
            "role": "music",
            "source_file": source,
            "start_frame": start,
            "level_db": 0.0,
            "keyframes": music_curve(
                automation, fps=fps, clip_start_frame=start,
                clip_frame_count=frames, fade_seconds=fade_seconds,
                crossfade_in_seconds=float(
                    clip.get("crossfade_in_seconds") or 0.0),
                crossfade_out_seconds=float(
                    clip.get("crossfade_out_seconds") or 0.0),
                fade_out_seconds=float(
                    clip.get("fade_out_seconds") or 0.0)),
            "label": clip.get("label", os.path.basename(source)),
        })

    for name, track in sorted(tracks.items()):
        if not name.startswith("A") or name in ("A1", "A2"):
            continue
        for clip in track.get("clips", []) or []:
            source = clip.get("source_file", "")
            level = clip.get("volume_db")
            if not source or level is None:
                continue
            start = _start_frame(clip, fps)
            frames = max(0, _end_frame(clip, fps) - start)
            targets.append({
                "role": "sfx",
                "source_file": source,
                "start_frame": start,
                "level_db": float(level),
                "keyframes": declick_curve(
                    float(clip.get("fade_out_seconds") or 0.0),
                    fps=fps, clip_frame_count=frames, level_db=float(level),
                    fade_in_seconds=float(
                        clip.get("fade_in_seconds") or 0.0)),
                "label": clip.get("label", os.path.basename(source)),
            })
    return targets


def declick_curve(fade_seconds: float, *, fps: float, clip_frame_count: int,
                  level_db: float, fade_in_seconds: float = 0.0) -> dict:
    """The ramp at the out point of a sound the plan CUT SHORT.

    A sound played to its own end has nothing to ramp and gets `{}` - a
    static level, which is what every SFX carried before.  A sound the
    plan asked to stop early stops mid-waveform, and that step to silence
    is an audible click: measured on the run of record's own request,
    `whoosh_impact.mp3` from its 0.714 s transient for 0.25 s ends at
    16.5% of the slice's peak.

    How long the ramp is, is decided in
    `library/tools/sfx_duration.py` - one frame, because a keyframe is
    addressed by FRAME and one is the shortest this format can carry.
    Nothing is chosen here; this turns a measured number into the two
    keys that deliver it.

    `fade_in_seconds` (rung 7) is the plan's stated head ramp - silence
    to level over the first seconds. Absent it the curve starts at
    level, exactly as before.
    """
    if ((fade_seconds <= 0 and fade_in_seconds <= 0)
            or clip_frame_count <= 1):
        return {}
    last_frame = clip_frame_count - 1
    keys = []
    if fade_in_seconds > 0:
        ramp_in = max(1, int(round(fade_in_seconds * fps)))
        keys += [(0, MIN_VOLUME_DB),
                 (min(ramp_in, last_frame), level_db)]
    else:
        keys += [(0, level_db)]
    if fade_seconds > 0:
        ramp = max(1, int(round(fade_seconds * fps)))
        hold = last_frame - ramp
        if hold < 0:
            hold = 0
        if hold >= last_frame:
            hold = last_frame - 1
        keys += [(hold, level_db), (last_frame, MIN_VOLUME_DB)]
    else:
        keys += [(last_frame, level_db)]
    return _tidy(keys, last_frame)


def _start_frame(clip: dict, fps: float) -> int:
    if "timeline_in_frame" in clip:
        return int(clip["timeline_in_frame"])
    return int(round(float(clip.get("timeline_in", 0.0)) * fps))


def _end_frame(clip: dict, fps: float) -> int:
    if "timeline_out_frame" in clip:
        return int(clip["timeline_out_frame"])
    return int(round(float(clip.get("timeline_out", 0.0)) * fps))


def apply_mix(otio: dict, targets: list) -> dict:
    """Write every target onto its clip.  Returns what happened.

    A target that matches no clip is REPORTED, never dropped quietly: a
    level nobody applied is the defect this module exists to remove, and
    a silent miss would reproduce it one layer further down.
    """
    placed = list(audio_clips(otio))
    used, applied, unmatched = set(), [], []

    for target in targets:
        index = _match(placed, used, target)
        if index is None:
            unmatched.append(target)
            continue
        used.add(index)
        _, clip, start, frames = placed[index]
        effect = _volume_effect_of(clip)
        if effect is None:
            unmatched.append(dict(target, reason="clip carries no volume effect"))
            continue
        parameters = [p for p in (effect.get("Parameters") or [])
                      if p.get("Parameter ID") != VOLUME_PARAMETER_ID]
        parameters.append(volume_parameter(target["level_db"],
                                           target.get("keyframes")))
        effect["Parameters"] = parameters
        applied.append(dict(target, matched_start_frame=start,
                            matched_frame_count=frames))

    return {"applied": applied, "unmatched": unmatched}


def _match(placed: list, used: set, target: dict):
    """The clip a target names: same file, nearest start within tolerance."""
    best, best_delta = None, None
    for index, (_track, clip, start, _frames) in enumerate(placed):
        if index in used or clip_media_path(clip) != target["source_file"]:
            continue
        delta = abs(start - target["start_frame"])
        if delta > MATCH_TOLERANCE_FRAMES:
            continue
        if best_delta is None or delta < best_delta:
            best, best_delta = index, delta
    return best


# ── The file ────────────────────────────────────────────────────────

def load(path: str) -> dict:
    """Read an OTIO.  encoding named, per AGENTS.md section 9."""
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def save(otio: dict, path: str, *, timeline_name: str = "") -> str:
    """Write an OTIO, naming the timeline it will import as."""
    if timeline_name:
        otio["name"] = timeline_name
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(otio, handle, indent=1)
    return path


def verify(otio: dict, applied: list) -> list:
    """Levels that did not come back off a re-export, as complaints.

    The import is the only step here that is not this module's own code,
    and it answers None or a timeline with nothing in between.  Reading
    the result back is what turns "Resolve accepted the file" into "the
    level is on the clip".
    """
    on_timeline = {(entry["source_file"], entry["start_frame"]): entry
                   for entry in read_levels(otio)}
    complaints = []
    for target in applied:
        key = (target["source_file"], target.get("matched_start_frame",
                                                 target["start_frame"]))
        landed = on_timeline.get(key)
        if landed is None:
            complaints.append(
                f"{target['label']}: no level on the imported timeline")
            continue
        wanted = _clamp_db(target["level_db"])
        if abs(landed["level_db"] - wanted) > LEVEL_TOLERANCE_DB:
            complaints.append(
                f"{target['label']}: level {landed['level_db']}dB, "
                f"planned {wanted}dB")
        planned_keys = {int(f): _clamp_db(v)
                        for f, v in (target.get("keyframes") or {}).items()}
        actual_keys = landed["keyframes"]
        keyframes_match = (
            actual_keys.keys() == planned_keys.keys()
            and all(abs(actual_keys[frame] - value) <= LEVEL_TOLERANCE_DB
                    for frame, value in planned_keys.items())
        )
        if planned_keys and not keyframes_match:
            complaints.append(
                f"{target['label']}: {len(actual_keys)} keyframes on the "
                f"timeline differ from {len(planned_keys)} planned")
    return complaints
