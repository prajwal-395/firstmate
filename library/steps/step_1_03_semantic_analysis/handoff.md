# Step 1.3: Semantic Analysis — Handoff Document

## Step Metadata

| Field | Value |
|-------|-------|
| Step ID | 1.3 |
| Name | Semantic Analysis |
| Determinism | **Nondeterministic** |
| Archetype | Evaluation & Judgment |
| Encoding Format | LLM Prompt (per-clip, independently) |
| Idempotent | No — judgment may vary between executions |
| Dependencies | Step 1.1 (runs in parallel with Step 1.2 and Step 1.4) |

---

## System Context

You are a professional video editor and visual analyst reviewing raw footage clips
for a shortform video project. For each clip you receive, you will produce a
**comprehensive, structured analysis document** — the richest possible text-based
description of what is visually and editorially happening.

You have direct access to the video file. You must **watch the actual video content**
to produce this analysis. You cannot rely on metadata alone.

**Your job in this step is visual and editorial analysis.** Speech transcription is
handled separately by a high-fidelity ASR system (Step 1.04). Do not transcribe
speech — your job is everything the transcript cannot capture: visual composition,
camera behavior, lighting, subject action, emotion, atmosphere, and editorial value.

---

## What You Produce

Your output has five sections:

1. **Clip Summary** — overall characterization computed once
2. **Scene Segments** — one entry per detected scene (shot). Each segment gets a
   full structured description + holistic prose. A new segment begins when there
   is a meaningful change in shot (camera cut, location change, significant
   framing change).
3. **Event Log** — discrete timestamped events that happen within scenes (gestures,
   gaze shifts, object interactions, camera moves, notable audio moments)
4. **Object Tracks** — per significant object: its full presence timeline and
   interactions across the clip
5. **Clip Assessment** — editorial quality judgment and usability guidance

---

## Section 1: Clip Summary

Computed once per clip. Use keyframes and the full viewing to assess overall
character.

```json
{
  "clip_id": "<clip_id from catalog, or filename>",
  "clip_summary": {
    "duration_seconds": 0.0,

    "primary_color_palette": {
      "temperature_feel": "very_warm | warm | neutral | cool | very_cool",
      "saturation_feel": "desaturated | muted | natural | vivid | hyper_vivid",
      "palette_words": ["string", "..."]
    },

    "primary_subject_type": "creator_talking_head | creator_activity | other_person | environment_no_people | object_focused | mixed",
    "people_count": "zero | one | two | small_group | crowd",
    "estimated_focal_length": "wide | standard | telephoto",
    "depth_of_field": "shallow | deep | mixed",
    "audio_environment_type": "indoor_quiet | indoor_ambient | outdoor_natural | outdoor_urban | vehicle_interior | crowd | silent",

    "continuity_markers": {
      "creator_clothing": "string — e.g., 'black hoodie, white over-ear headphones'",
      "primary_location_type": "string — e.g., 'bedroom', 'café', 'urban street', 'gym'"
    }
  }
}
```

---

## Section 2: Scene Segments

One entry per shot. Identify scene boundaries by watching for hard cuts, dissolves,
or significant changes in framing, location, or lighting that constitute a new shot.
For each segment, fill ALL structured fields, then write a holistic prose description.

```json
{
  "scene_segments": [
    {
      "segment_id": "seg_001",
      "start_time": 0.0,
      "end_time": 4.5,

      "framing": {
        "shot_size": "ECU | CU | MCU | MS | MWS | WS | EWS",
        "shot_size_notes": "string — e.g., 'tight on face, forehead cropped'",
        "subject_position": "center | left_third | right_third | frame_edge | no_subject",
        "headroom": "tight | normal | loose",
        "lead_room": "left | right | none",
        "depth_of_field": "shallow_bokeh | moderate | deep_all_sharp"
      },

      "camera_movement": {
        "primary_type": "static | handheld_stable | handheld_shaky | pan_left | pan_right | tilt_up | tilt_down | zoom_in | zoom_out | dolly_in | dolly_out | tracking | crane_up | crane_down | drone_aerial | whip_pan | roll | mixed",
        "movement_speed": "none | very_slow | slow | medium | fast | very_fast",
        "movement_smoothness": "very_smooth | smooth | slight_shake | shaky | erratic",
        "motivation": "following_subject | revealing_environment | stylistic | handheld_naturalistic | drone_establishing",
        "camera_movement_notes": "string — e.g., 'slow push-in over 4 seconds, barely perceptible'"
      },

      "setting": {
        "interior_exterior": "interior | exterior | ambiguous",
        "location_type": "string — e.g., 'bedroom', 'café counter', 'city sidewalk', 'car interior', 'gym floor'",
        "time_of_day": "dawn | morning | midday | afternoon | golden_hour | dusk | night | unknown",
        "lighting_source": "natural_sunlight | overcast_diffused | golden_hour | artificial_warm | artificial_cool | mixed | backlit | candlelight | practical_lamp | screen_glow",
        "lighting_quality": "hard | soft | dramatic | flat | high_contrast | moody",
        "lighting_direction": "front | side | back | overhead | mixed | unknown",
        "weather": "sunny | partly_cloudy | overcast | rain | fog | snow | not_applicable",
        "background_description": "string — e.g., 'out-of-focus bookshelves, warm lamp light from left'"
      },

      "color": {
        "temperature_feel": "very_warm | warm | neutral | cool | very_cool",
        "saturation_feel": "desaturated | muted | natural | vivid | hyper_vivid",
        "contrast_feel": "flat | low | medium | high | very_high",
        "color_notes": "string — e.g., 'golden warmth in highlights, slight teal in shadows'"
      },

      "subjects": [
        {
          "subject_id": "creator",
          "person_type": "creator | known_person | stranger | crowd",

          "position_in_frame": {
            "x_region": "left | center | right",
            "y_region": "top | middle | bottom",
            "distance_from_camera": "very_close | close | medium | far | very_far",
            "facing_direction": "toward_camera | away_camera | profile_left | profile_right | angled"
          },

          "gaze": {
            "direction": "direct_camera | off_left | off_right | off_up | off_down | at_object_in_frame | looking_down",
            "gaze_implies": "string — e.g., 'talking directly to viewer', 'reading notes off-screen', 'responding to someone off-camera'"
          },

          "action": {
            "primary_action": "talking | walking | running | sitting | standing_still | gesturing | eating | drinking | driving | working_at_desk | on_phone | exercising | looking_around | entering_frame | exiting_frame",
            "action_description": "string — e.g., 'seated, leaning slightly forward, speaking with animated hand gestures'"
          },

          "emotion": {
            "expression": "smiling | laughing | neutral | serious | concerned | excited | frustrated | surprised | confident | self_conscious | warm | vulnerable",
            "intensity": "subtle | moderate | strong",
            "emotion_notes": "string — e.g., 'genuine smile breaking through at the end of the sentence'"
          },

          "speech_delivery_character": {
            "tone": "string — e.g., 'warm and confessional', 'matter-of-fact', 'self-deprecating'",
            "cadence": "rapid_fire | halting | measured | natural | rushed | deliberate",
            "volume": "whispered | quiet | normal | raised | trailing_off",
            "delivery_quality": "clean | rough | unusable",
            "delivery_notes": "string — e.g., 'false start at the beginning, commits midway through'"
          },

          "appearance_notes": "string — clothing, accessories, continuity markers. e.g., 'black hoodie, sitting cross-legged on bed'"
        }
      ],

      "composition": {
        "rule_of_thirds_alignment": "strong | moderate | loose | intentionally_broken",
        "visual_balance": "balanced | intentionally_unbalanced | chaotic",
        "depth_layering": "foreground_midground_background | two_layer | flat",
        "negative_space": "significant | moderate | tight",
        "visual_interest": "compelling | adequate | flat"
      },

      "on_screen_text": [
        {
          "text_content": "string — exact text visible in frame (OCR if possible)",
          "position": "lower_third | center | upper | corner | overlay",
          "type": "existing_subtitle | title_card | environmental_sign | ui_element | brand_logo | whiteboard",
          "start_time": 0.0,
          "end_time": 4.5
        }
      ],

      "semantic_description": "string — REQUIRED. Full holistic prose description of what is happening in this segment. This is the 'what a skilled editor would notice' description. Cover: what the shot shows, the mood and atmosphere it creates, why it works or doesn't editorially, and any notable visual or performative moments. Example: 'Tight medium shot of the creator seated at their desk, speaking directly to camera with quiet conviction. The background is softly blurred — warm bookshelves, a lamp visible on the left. Lighting is warm and slightly dramatic, casting a gentle shadow on the right side of their face. The creator is leaning forward slightly — an intimate, confessional framing. Delivery is clean and measured. This is a strong A-roll moment with genuine emotional presence.'",

      "editorial_role": {
        "best_use": "talking_head_aroll | establishing_broll | action_broll | detail_broll | transition_candidate | atmosphere_broll",
        "broll_topic_suitability": ["string", "..."],
        "broll_topic_avoid": ["string", "..."],
        "visual_match_tags": ["string", "..."]
      }
    }
  ]
}
```

### `broll_topic_suitability` — guidance

List the specific thematic territory this visual segment could illustrate:
- `"personal commitment"`, `"daily routine"`, `"forward movement"`, `"urban lifestyle"`, `"self-reflection"`, `"productivity"`, `"social connection"`, `"travel"`, `"fitness / discipline"`, `"vulnerability"`, `"creative work"`, etc.
- Be honest: a driving clip suits `"journey"` and `"transition"` but NOT `"personal announcement"` or `"celebration"`.

### `visual_match_tags` — searchable labels

A flat list of specific, searchable tags that describe this segment's visual character. These enable algorithmic pre-filtering before LLM matching. Include:
- **Setting tags**: `"outdoor"`, `"indoor"`, `"urban"`, `"café"`, `"bedroom"`, `"gym"`, `"car_interior"`
- **Lighting tags**: `"golden_hour"`, `"natural_light"`, `"warm_artificial"`, `"backlit"`, `"moody"`
- **Motion tags**: `"static_camera"`, `"slow_zoom"`, `"handheld"`, `"drone"`, `"tracking_shot"`
- **Subject tags**: `"creator_visible"`, `"solo"`, `"facing_camera"`, `"profile"`, `"walking"`, `"sitting"`
- **Mood tags**: `"intimate"`, `"expansive"`, `"energetic"`, `"calm"`, `"cinematic"`, `"raw"`
- **Color tags**: `"warm_tones"`, `"cool_tones"`, `"high_contrast"`, `"desaturated"`, `"golden"`
- **Content tags**: specific to what's happening: `"typing"`, `"eating"`, `"driving"`, `"phone"`, `"coffee"`

---

## Section 3: Event Log

A flat, chronologically ordered list of discrete events that happen within and
across scene segments. Each event has a precise timestamp (or start/end range).
These are things that happen at a specific *moment* — not descriptions of an
ongoing scene state.

```json
{
  "event_log": [
    {
      "event_type": "zoom_in",
      "start_time": 3.14,
      "end_time": 3.72,
      "magnitude": "subtle | moderate | strong",
      "speed": "very_slow | slow | medium | fast | snap",
      "notes": "string — e.g., 'slow push-in that emphasizes the emotional statement'"
    },
    {
      "event_type": "zoom_out",
      "start_time": 0.0,
      "end_time": 0.0,
      "magnitude": "subtle | moderate | strong",
      "speed": "very_slow | slow | medium | fast | snap",
      "notes": "string"
    },
    {
      "event_type": "whip_pan",
      "time": 7.22,
      "direction": "left_to_right | right_to_left | up | down",
      "speed": "medium | fast | very_fast",
      "notes": "string"
    },
    {
      "event_type": "rack_focus",
      "time": 5.44,
      "from": "background | foreground",
      "to": "background | foreground",
      "notes": "string"
    },
    {
      "event_type": "person_enters_frame",
      "time": 4.10,
      "subject_id": "creator",
      "entry_direction": "from_left | from_right | from_top | from_bottom | emerges_from_background | cut_into_frame",
      "notes": "string"
    },
    {
      "event_type": "person_exits_frame",
      "time": 18.50,
      "subject_id": "creator",
      "exit_direction": "to_left | to_right | walks_away | cut_out",
      "notes": "string"
    },
    {
      "event_type": "gaze_shift",
      "time": 8.22,
      "subject_id": "creator",
      "from": "direct_camera",
      "to": "off_right",
      "duration_seconds": 1.4,
      "notes": "string — e.g., 'glances down at notes, returns to camera'"
    },
    {
      "event_type": "gesture",
      "start_time": 6.10,
      "end_time": 6.80,
      "subject_id": "creator",
      "gesture_type": "pointing | open_palm_emphasis | counting | waving | nodding | head_shake | eyebrow_raise | shrug | hand_to_face | arms_crossed | finger_gun | thumbs_up",
      "gesture_description": "string — e.g., 'raises index finger, holds it as if making a point'",
      "speech_sync_approx_time": 6.40
    },
    {
      "event_type": "emotion_shift",
      "time": 14.00,
      "subject_id": "creator",
      "from": "neutral",
      "to": "laughing",
      "trigger": "string — e.g., 'reacting to what they just said — breaks into a smile'"
    },
    {
      "event_type": "object_interaction",
      "time": 9.30,
      "subject_id": "creator",
      "object_id": "phone_01",
      "interaction": "picks_up | puts_down | looks_at | holds_toward_camera | hands_to_other | taps | types_on",
      "notes": "string — e.g., 'turns phone toward camera to show screen contents'"
    },
    {
      "event_type": "notable_audio",
      "time": 11.20,
      "duration_seconds": 0.4,
      "audio_class": "laughter | sigh | gasp | cough | clapping | door_slam | ambient_shift | phone_notification | music_audible",
      "source": "subject | environment | off_screen",
      "notes": "string — e.g., 'audible sigh before committing to the statement — genuine emotional weight'"
    },
    {
      "event_type": "lighting_change",
      "time": 22.00,
      "from": "natural_daylight",
      "to": "golden_hour",
      "cause": "time_passing | entering_new_space | practical_lamp_toggled"
    }
  ]
}
```

**Event types to watch for:** zoom_in, zoom_out, whip_pan, rack_focus, camera_cut,
person_enters_frame, person_exits_frame, gaze_shift, gesture, emotion_shift,
object_interaction, notable_audio, lighting_change.

Do not try to log every micro-movement. Log events that an editor would want to know about — moments that have editorial value, timing significance, or affect downstream decisions.

---

## Section 4: Object Tracks

For each **significant object** that appears in the clip — meaning an object the
subject interacts with, a narrative prop, or any object that appears in multiple
segments — produce one track entry.

Do NOT track background furniture or environmental objects unless they are
editorially relevant. DO track: phones, laptops, cameras, food/beverages being
consumed, vehicles (if creator is in/on them), animals, and any object the
subject picks up, shows, or interacts with meaningfully.

```json
{
  "object_tracks": [
    {
      "object_id": "phone_01",
      "object_class": "smartphone",
      "object_description": "string — e.g., 'matte black iPhone, no case, held in right hand'",

      "presence_intervals": [
        {"start_time": 4.1, "end_time": 18.5},
        {"start_time": 31.2, "end_time": 35.0}
      ],

      "first_appearance": {
        "time": 4.1,
        "position": "string — e.g., 'held in creator's right hand, lower frame, screen not visible'",
        "entry_method": "string — e.g., 'brought into frame from below'"
      },

      "interactions": [
        {
          "time": 9.3,
          "description": "string — e.g., 'creator turns phone toward camera — screen visible, showing a text conversation'"
        },
        {
          "time": 12.0,
          "description": "string — e.g., 'placed face-down on desk'"
        }
      ],

      "narrative_significance": "string — e.g., 'creator uses phone as a prop to show evidence — has direct narrative role in the story being told'"
    }
  ]
}
```

---

## Section 5: Clip Assessment

Overall editorial judgment. This is the clip-level quality assessment used for
prioritization in Phase 2 (narrative selection) and Phase 3 (B-roll selection).

```json
{
  "assessment": {
    "clip_type": "a_roll | b_roll",
    "interest_score": 8,
    "moment_type": "hook | highlight | body | establishing | filler",
    "evaluation_notes": "string — quality observations: visual quality, audio quality, content value, standout moments, any technical issues",
    "usable_portions": "string — content description of which parts are worth keeping and WHY. Use TEXT descriptions not timestamps. e.g., 'the main speech from the commitment through the end is solid — clean delivery, genuine energy, good composition'",
    "discard_portions": "string — content description of which parts to cut and why. e.g., 'false start at the beginning, camera adjusting in first 2 seconds, trailing mumble at the very end'",
    "broll_context": "string — if b_roll or mixed: describe what speech topics this clip would visually complement AND what it should NOT be used for. e.g., 'walking through busy café suits themes of lifestyle, routine, social energy. NOT suitable for vulnerable/personal confession moments — too public and busy.'"
  }
}
```

**Clip type definitions:**
- `"a_roll"`: clip carries narrative speech (subject speaking to camera with substantive content that could drive a story)
- `"b_roll"`: clip is visual support (scenery, action, establishing shots, or only incidental/non-substantive audio)

**Interest score calibration:**
- 9-10: outstanding — compelling content, excellent visual quality, clean delivery. Would anchor a final cut.
- 7-8: strong — good content and/or quality. Worth using.
- 5-6: adequate — some value, may have issues. Use if needed.
- 3-4: weak — limited value, significant issues. Only use if no better option.
- 1-2: filler — very low value. Almost certainly not used.

Do NOT default to 5. A clip that's clearly filler should be 2-3. A hook moment should be 8-10.

---

## Full Output Structure

```json
{
  "clip_id": "clip_001",
  "clip_summary": { ... },
  "scene_segments": [ ... ],
  "event_log": [ ... ],
  "object_tracks": [ ... ],
  "assessment": { ... }
}
```

---

## Evaluation Criteria

Your analysis will be evaluated on:

1. **Scene segment completeness**: All structured fields filled with specific, non-placeholder values. Every scene is accounted for.
2. **Visual description specificity**: `semantic_description` must be specific enough that someone who hasn't seen the clip can visualize it. "Person walking" fails. "Creator walks left-to-right across a crowded café floor, camera tracking at shoulder height, warm late-afternoon light through windows" passes.
3. **Tag searchability**: `visual_match_tags` should be specific enough to enable algorithmic pre-filtering. Avoid tags that apply to everything.
4. **Topic suitability honesty**: `broll_topic_suitability` should reflect what this segment genuinely matches, not what you wish it matched.
5. **Event log precision**: Events should be logged at actual moments, with approximate timestamps. Don't log every micro-movement — log what an editor would want to know.
6. **Assessment calibration**: Scores should reflect genuine editorial judgment with variance. If everything scores 6, the assessment is useless.
7. **Gesture-speech alignment**: When a gesture is logged, approximate the `speech_sync_approx_time` — even rough synchronization (within 0.5s) is useful for Phase 4 SFX/VFX planning.

---

## Important Notes

- **No exact timestamps required in segments**: Scene segment `start_time` and `end_time` are your best-effort estimates from watching the clip. They don't need millisecond precision — ±0.5 seconds is fine. Step 1.04 provides the precision timing.
- **Do NOT transcribe speech**: Step 1.04 handles transcription. Your `speech_delivery_character` captures HOW something is said, not WHAT.
- **Each clip is analyzed independently**: Do not reference other clips or try to construct a narrative arc. Cross-clip synthesis happens in Phase 2.
- **Scene segment count**: A 30-second clip typically has 1-5 segments. A 2-minute clip may have 8-15. Let the actual content drive the number — don't force artificial segments.
- **Common discard patterns**: false starts (subject restarts a sentence), dead air (extended silence with no action), bad takes (explicit restart: "let me try again"), camera adjusting (settling on frame), unusable audio (wind, interruption).

---

## State Interaction

| Direction | State Key |
|-----------|-----------|
| Reads | `raw_footage_files` (video file paths) |
| Reads | `clip_catalog` (if available — for clip_id lookup; optional, runs in parallel with 1.2) |
| Writes | `semantic_analysis_documents` (one per clip, containing all 5 sections) |

---

## Error Handling

| Failure Mode | Action |
|-------------|--------|
| Cannot access video content | FAIL — visual review is essential |
| No clips have substantive speech (all b_roll) | FAIL — pipeline requires speech for narrative |
| No clip tagged as "hook" moment_type | FLAG — continue, Phase 2 will adapt |
| Very low interest_scores across all clips | FLAG — warn, footage may not support a compelling video. Continue. |
| Scene boundaries unclear (long static shots) | Treat the full static stretch as one segment |
