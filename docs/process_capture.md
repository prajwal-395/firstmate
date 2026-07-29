# Process Capture Document

## Metadata

- **Process Name:** Shortform Video Editing Pipeline
- **Capture Date:** 2026-05-05
- **Source:** Prajwal (process owner) — captured from interview, describing current practice
- **Capture Method:** Conversational interview with targeted follow-ups

---

## Boundary

**Trigger:** Raw footage from a filming session has been transferred to the computer and placed in a project folder.

**Input State:**
- Raw video footage files exist in a project folder on the local filesystem
- Separate, persistent asset folders exist on the computer containing:
  - SFX (sound effects)
  - VFX (visual effects — overlays, particle effects, light leaks, screen shake, etc.)
  - Transitions (video transitions)
- A general idea or topic for the video exists in the creator's mind (may be explicit/planned or implicit/emergent from the footage)
- Video editing capabilities are available (multi-track timeline composition, audio mixing)

**Output State:**
- A finished shortform video file exists on the local filesystem
- Video is vertical (9:16 aspect ratio)
- Video is 30–60 seconds in duration
- Video has the creator's consistent style applied (color grading, subtitles, transitions, SFX, etc.)

**Scope:**
- **IN:** Everything from "raw footage on computer" to "exported finished video file"
  - Media organization and import
  - Storyline construction from footage
  - Clip selection and sequencing
  - SFX, music, VFX, transitions, animations, motion graphics
  - Subtitle generation and styling
  - Color grading
  - Sound editing / audio cleanup
  - Final export
- **OUT:**
  - Filming and planning (pre-production)
  - Posting / distribution to social media platforms
  - Thumbnail creation
  - Writing scripts or storyboards (this happens before the trigger)

---

## Narrative

The process follows roughly six phases, described in the order they're performed:

### Phase 1: Media Organization & Import
Raw footage is gathered into a project folder. The creator opens DaVinci Resolve and imports the raw footage along with assets from the persistent asset folders (SFX, VFX, transitions). At this point all the materials needed for the edit are accessible within the editing environment.

### Phase 2: Storyline Construction
This is the most creative and time-intensive phase. The creator reviews the raw footage — watching clips, listening to audio, noting what was said and captured. They construct a storyline, drawing from two sources:

1. **Pre-filming concept:** The general idea or topic that motivated the filming session (e.g., "daily vlog about X," "tutorial on Y," "cinematic piece about Z")
2. **Post-filming analysis:** What the footage actually contains, which may diverge from the original concept

These two inputs sometimes align, sometimes conflict (artistic vision evolves post-filming), and sometimes only the second exists (e.g., turning unplanned daily footage into something cinematic and cohesive). The output of this phase is a mental model of the video's narrative arc — which clips to use, in what order, to tell what story.

### Phase 3: Clip Selection & Assembly
Based on the storyline from Phase 2, the creator cuts the raw footage into the specific clips they want. They arrange these clips on the timeline in the right sequence to create a compelling narrative. This produces the rough cut — the backbone of the video.

### Phase 4: Enhancement Layer
With the rough cut in place, the creator layers on enhancements:
- **SFX:** Sound effects added at key moments (whooshes, impacts, ambient textures, etc.)
- **Music:** Background music sourced typically by downloading audio from YouTube (no established catalog)
- **VFX:** Visual effects — overlays, particle effects, light leaks, screen shake, etc.
- **Transitions:** Audio and video transitions between clips
- **Animations & Motion Graphics:** Additional visual elements
- **Subtitles:** Text overlays of spoken audio (style not yet fully specified)

### Phase 5: Style & Polish
Final styling and quality passes:
- **Color Grading:** Applied to achieve the creator's consistent visual style/vibe. The specific parameters and LUTs are not yet fully codified.
- **Sound Editing:** Audio cleanup — ensuring voice is clear, levels are balanced, no unwanted noise.

### Phase 6: Export
The finished video is exported from DaVinci Resolve to the local filesystem. Export settings are not yet standardized but the format is vertical 9:16 for shortform content.

---

## Variations

1. **Planned vs. Impromptu shoots:** Planned shoots have a clearer pre-filming concept; impromptu shoots (e.g., daily vlogs) rely more heavily on post-filming storyline construction. The editing workflow is the same, but the storyline phase is harder and more creative for impromptu footage.

2. **Complexity spectrum:** The amount of VFX, transitions, and motion graphics varies by video. Some shorts are minimal (just cuts, subtitles, music), others are heavily produced. The pipeline is the same but the Enhancement Layer phase scales in effort.

3. **Footage volume:** The ratio of raw footage to final video varies. Some sessions produce 5 minutes of footage for a 45-second short; others produce 30+ minutes.

---

## Known Failure Modes

1. **Storyline doesn't come together:** Sometimes the footage doesn't naturally form a compelling narrative. The creator gets stuck iterating on clip arrangement. This is the primary time sink.

2. **Style inconsistency:** Without codified style parameters, the look and feel can drift between videos, requiring rework to match the intended aesthetic.

3. **Music/SFX mismatch:** Finding the right music that matches the mood of the edit is trial-and-error. No established catalog means searching each time.

4. **Audio issues in raw footage:** Background noise, inconsistent levels, or poor recording quality in the raw footage can bottleneck the sound editing phase.

5. **Export settings trial-and-error:** Without standardized export presets, the creator sometimes needs multiple export attempts to get the right quality/size balance.

---

## Tacit Knowledge

1. **"Compelling storyline" is intuitive.** The creator has an internalized sense of pacing, emotional arc, and visual rhythm that determines clip selection and ordering. This is currently not externalized or codified.

2. **Style is "I know it when I see it."** The color grading, transition choices, and overall vibe are driven by aesthetic intuition rather than documented parameters. The creator recognizes their style in the output but hasn't specified it as a reproducible recipe.

3. **Short-form editing conventions.** The creator implicitly applies short-form content conventions: fast pacing, hook in the first 1–3 seconds, visual variety, text overlays for engagement, vertical framing. These conventions are internalized, not documented.

4. **DaVinci Resolve proficiency.** The specific techniques, keyboard shortcuts, and workflow patterns within DaVinci Resolve are second nature to the creator. The process as described abstracts over significant tool-specific knowledge.

5. **Temporal coherence of A-roll.** *(Discovered in pilot)* A-roll shots should generally maintain the chronological order they were filmed in. For unscripted/vlog footage, the filming order carries implicit visual continuity — the subject moves through a location, lighting changes, clothing is consistent within a sequence. Jumping between non-adjacent source files makes the video feel "jumpy" and disorienting. Breaking chronological order is acceptable only when: (a) clips are talking-head shots in the same visual setting, or (b) there is a deliberate creative motivation (e.g., a flash-forward hook).

6. **Audio must form a cohesive monologue.** *(Discovered in pilot)* The stitched-together audio in the final video must sound like a continuous, natural speech — not random fragments. This means:
   - Never cut in the middle of a sentence or thought
   - Adjacent audio segments must logically follow each other (conclusion of one idea → start of next idea)
   - Maintain consistent audio environment (background noise, reverb) between adjacent speech clips
   - The final audio transcript, read end-to-end, must make sense as a standalone paragraph

7. **A-roll vs B-roll are functionally different.** *(Discovered in pilot)* A-roll (footage with the subject speaking to camera) drives the narrative — it carries the storyline through dialogue. B-roll (footage without speech, or with incidental audio) is visual support — it covers cuts, shows the environment, adds variety, and gives the viewer visual breathing room. A-roll dictates the audio timeline; B-roll is placed visually over or between A-roll segments. The two must not be conflated — selecting B-roll for its transcript or A-roll for its visuals alone produces incoherent results.

8. **Cut points must respect speech segment boundaries.** *(Discovered in pilot)* When cutting A-roll, the in/out points must align to natural speech boundaries — complete sentences or complete thoughts. Using arbitrary timestamps (even if "near" a sentence) produces audio that starts or ends mid-word. The speech-to-text segment timestamps are the minimum viable cut points; paragraph-level boundaries produce smoother results.

9. **Unscripted footage has inherent narrative structure.** *(Discovered in pilot)* Even without a script, vlog footage has an implicit structure: the subject films in sequence, building on ideas as they go. The first takes are often false starts or setup. The middle takes contain the core content. Later takes are wrapping up or adding afterthoughts. The editing process should discover and amplify this natural arc, not impose an artificial one.

10. **Creation timestamp metadata IS the timeline.** *(Discovered in pilot)* Video files contain creation timestamp metadata (e.g., `creation_time` from the container format, `com.apple.quicktime.creationdate` for iPhone MOV files) that gives the exact recording date/time. This metadata is the authoritative source of chronological filming order. Filenames are not reliable — different cameras use different naming schemes, files can be renamed, and naming conventions don't guarantee temporal ordering. Clips within the same source file are inherently contiguous in time.

11. **Audio environment continuity.** *(Discovered in pilot)* Adjacent clips in the final edit should have compatible background audio characteristics. Jumping from a windy outdoor shot to a quiet indoor shot (or vice versa) is jarring. When possible, group clips by audio environment, or use B-roll and music to bridge environment transitions.

12. **The editing process works on a multi-track timeline.** *(Captured for decomposition)* The edit is a layered composition where multiple video and audio tracks play simultaneously. Video tracks are stacked (higher tracks visually override lower tracks — e.g., B-roll on V2 covers A-roll on V1). Audio tracks are mixed (dialogue, SFX, and music play simultaneously at their respective volumes). Clips on the timeline are non-destructive references to source media with in/out points — the source files are never modified. Rendering collapses all tracks into a single output file as the final operation. Note: not every step in the process must happen within a single editing tool — some steps may use external tools or processes (e.g., transcription, conforming, analysis).

---

## Tools & Resources

| Resource | Category | Notes |
|---|---|---|
| DaVinci Resolve | Video editing software | Primary editing environment |
| Raw footage | Input material | Variable format, from phone or camera |
| SFX library | Audio assets | Persistent folder, organized |
| VFX library | Visual assets | Persistent folder, organized |
| Transitions library | Video assets | Persistent folder, organized |
| YouTube (audio) | Music source | No established catalog — ad-hoc search |
| Local filesystem | Storage | Project folders per video |

---

## Pain Points

1. **Time:** The full edit takes hours to days. This is the dominant pain point. The overall style is roughly the same across videos — only the raw footage changes — which strongly suggests automation potential.

2. **Style codification:** The creator's style exists as intuition, not specification. This creates inconsistency and prevents delegation (to humans or machines).

3. **Music sourcing:** No established catalog; searching for appropriate music is time-consuming and ad-hoc.

4. **Repetitive enhancement work:** Applying the same types of SFX, transitions, subtitle styling, and color grading to every video is repetitive labor that follows a pattern.

5. **Export settings:** No standardized preset means unnecessary trial-and-error.

---

## Open Questions

### Resolved

1. ~~**Style specification:**~~ **RESOLVED** — Full style specification created in [`style_specification.md`](./style_specification.md). Color grading node tree, subtitle styling (Poppins Bold, lowercase, white, subtle shadow), transition toolkit, SFX patterns, pacing rules, and export settings are all codified.

2. ~~**DaVinci Resolve automation capability:**~~ **RESEARCHED** — The Resolve Python/Lua API can: create projects, import media, create timelines, append clips, apply color grades from .drx files, access Fusion for VFX, and trigger renders. The API does NOT support granular node parameter editing — grades must be pre-built as PowerGrades (.drx) and applied programmatically. **DaVinci Resolve Studio** is required for external script execution. Headless mode (`-nogui`) is available.

3. ~~**Subtitle generation method:**~~ **RESOLVED** — Speech-to-text (e.g., Whisper) is acceptable. Style codified in style_specification.md: Poppins Bold, lowercase, white, subtle drop shadow, lower third, subtle fade-in animation.

4. ~~**Music selection criteria:**~~ **PARTIALLY RESOLVED** — Not genre-locked. Selection criteria: mood matching, energy curve alignment, doesn't compete with dialogue. Curated catalog is a future improvement. For now, music selection remains nondeterministic.

5. ~~**Storyline construction:**~~ **RESOLVED** — Can be broken into sub-steps: (a) transcribe audio, (b) identify key moments/soundbites, (c) sequence into narrative arc, (d) select supporting B-roll. The creative judgment is concentrated in steps (c) and (d); steps (a) and (b) are largely deterministic.

6. ~~**Export settings:**~~ **RESOLVED** — Codified in style_specification.md: MP4, H.264, 1080×1920 (9:16), 12–15k kbps, multi-pass, Rec.709 Gamma 2.4.

### Remaining Open Questions

7. **Reference videos:** No finished videos that match the target style exist yet. Validation will need to rely on the codified style specification. First pilot output becomes the reference.

8. **Asset folder structure:** The SFX/VFX/Transitions folders exist and are organized, but the exact structure has not been documented yet. Needed for the enhancement phase (not required for core editing scope).

9. ~~**DaVinci Resolve version:**~~ **RESOLVED** — Pipeline is tool-agnostic. The process is defined at the semantic operation level; implementation may use any toolchain that supports the required operations. DaVinci Resolve is the reference tool for manual editing.

---

## Assessment

| Factor | Rating | Notes |
|---|---|---|
| **Frequency** | Weekly+ | Creator makes shortform content regularly |
| **Pain** | **High** | Hours to days per video; dominant creative bottleneck |
| **Value** | **High** | Core to content creation pipeline |
| **Stability** | **Medium** | The rough phases are stable; style is now codified but untested |
| **Capture Confidence** | **High** | Style codified, storyline decomposable, export standardized. **Ready for Step 2.** |

---

## Process Classification Notes

This process is a hybrid:
- **Phases 1, 6** (import, export) are **fully deterministic** — scriptable, repeatable, no judgment
- **Phases 3, 4, 5** (assembly, enhancement, styling) are **partially deterministic** — the *operations* are deterministic (apply LUT, add subtitle, add transition) but the *parameters* are nondeterministic (which LUT, what subtitle text, which transition where)
- **Phase 2** (storyline) is **fully nondeterministic** — creative judgment, no algorithm

The automation strategy will likely need to:
1. **Codify the style** first (turn nondeterministic parameters into deterministic ones)
2. **Automate the deterministic operations** (scripted via DaVinci Resolve API or FFmpeg)
3. **Use LLM/AI for the nondeterministic phases** (storyline construction, clip selection, with human review)
