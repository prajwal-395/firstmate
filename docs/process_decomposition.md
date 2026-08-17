# Process Decomposition: Shortform Video Editing Pipeline

## Metadata
- **Decomposed by:** Prajwal + Antigravity (collaborative, chunk-by-chunk)
- **Date:** 2026-05-06
- **Version:** 3.0.0
- **Granularity Target:** AI orchestrator with human review on nondeterministic steps
- **Source:** [Process Capture Document](./process_capture.md) + [Style Specification](./style_specification.md)
- **Scope:** Full pipeline — raw footage through finished, fully styled video

---

## Scope Definition

- **Input State:**
  - Raw video footage files exist in a project folder on the local filesystem
  - Footage is in a standard video format (MP4, MOV, etc.) with embedded audio and creation timestamp metadata
  - Separate, persistent asset folders exist on the computer containing:
    - SFX (sound effects library, organized)
    - VFX (visual effects — overlays, particle effects, light leaks, etc., organized)
    - Transitions (video transition assets, organized)
  - A general idea or topic for the video may exist (explicit/planned) or may be absent (narrative will be emergent from the footage)

- **Output State:**
  - A finished shortform video file exists on the local filesystem
  - Video is vertical format (9:16 aspect ratio), 1080×1920 resolution
  - Video is 30–60 seconds in duration
  - Video contains:
    - Selected A-roll clips in narrative order forming a coherent spoken monologue
    - B-roll clips placed for visual variety and coverage
    - Color grading applied per the style specification
    - Subtitles (all lowercase, styled per spec) over all speech
    - Transitions between clips (hard cuts as default, creative transitions where appropriate)
    - Sound effects layered at key moments
    - Background music matching the video's mood and energy
    - Sound editing applied (clean dialogue, balanced levels)
  - The video matches the creator's established style as codified in the style specification

- **In Scope:**
  - Media inventory and analysis (scanning, cataloging, transcription)
  - Narrative/storyline construction from footage
  - Clip selection and sequencing
  - Timeline assembly (multi-track composition)
  - Color grading
  - Subtitle generation and styling
  - Transition selection and placement
  - SFX selection and placement
  - Music selection and placement
  - VFX application
  - Motion graphics and animations
  - Sound editing / audio cleanup
  - Final export and output validation

- **Out of Scope:**
  - Filming and planning (pre-production)
  - Script writing or storyboarding (happens before the trigger)
  - Posting / distribution to social media platforms
  - Thumbnail creation
  - Building or curating the SFX/VFX/Transitions asset libraries (assumed to exist)
  - Building a curated music catalog (noted as future improvement in the PCD)

- **Assumptions:**
  - Raw footage is in a standard video format readable by common tools
  - Video files contain creation timestamp metadata for chronological ordering
  - Audio is embedded in the video files (not separate tracks)
  - The asset folders (SFX, VFX, Transitions) are organized and browsable
  - Internet access is available for cloud-based transcription (if used)
  - The style specification is the authoritative source for all styling parameters

---

## Resource Inventory

| Resource | Category | Required By |
|---|---|---|
| Video metadata extraction capability | Analysis | Footage cataloging |
| Semantic analysis capability | Analysis | Per-clip visual/editorial analysis (scene composition, emotion, B-roll suitability) |
| Temporal indexing capability | Audio/Video processing | Per-clip temporal index with word-level timestamps |
| Audio extraction capability | Audio processing | On-demand audio extraction for downstream processing |
| Judgment/creative capability | Creative | Narrative construction, clip evaluation, music selection |
| Multi-track timeline composition capability | Video processing | Assembly |
| Color grading capability | Video processing | Style application |
| Text/subtitle rendering capability | Video processing | Subtitle generation |
| Audio mixing capability | Audio processing | Sound editing, music/SFX layering |
| Video rendering capability | Video processing | Final export |
| SFX asset library | Audio assets | Enhancement |
| VFX asset library | Visual assets | Enhancement |
| Transition asset library | Visual assets | Enhancement |
| Music source | Audio assets | Enhancement |
| Local filesystem | Storage | All phases |

---

## Process Flow Overview

### Phase Structure

The following 6-phase structure was developed collaboratively from the PCD's original
description, restructured based on critical analysis of dependencies and the creator's
actual workflow.

Key architectural decisions:
- **Audio-first architecture:** The audio spine (speech + music) defines the video's
  pacing and narrative before any visual decisions are made
- **Music is structural, not decorative:** Music is part of the spine (Phase 3), not a
  post-assembly enhancement. Speech and music are two halves of the audio backbone.
- **Layered dependency chain:** Each phase builds on the locked output of the previous
  phase — audio → video → enhancements → refinement → export
- **Judgment and execution are always split:** Nondeterministic decisions produce plans;
  deterministic steps execute them

```
PHASE 1: Inventory & Analysis
  Scan footage, extract technical metadata, perform semantic analysis
  on each clip (visual/editorial assessment, speech delivery, emotional
  dynamics), and build temporal event indices (speech transcripts with
  word-level timestamps, scene boundaries, energy curves). Produces a
  fully analyzed clip catalog.
  Entry state: Raw footage files in a project folder
  Exit state:  Enriched clip catalog with per-clip semantic analysis documents

PHASE 2: Audio Spine Construction
  Build the two halves of the audio backbone:
    (a) Speech monologue — select passages (judgment), then resolve precise
        timestamps (execution). Judgment and execution are separate steps.
    (b) Music — select track(s)/splices guided by the creative direction
  Mesh the two halves: design hook structure, music fills, transitions between
  speech and music moments. Validate the complete audio spine.
  Entry state: Enriched clip catalog + per-clip semantic analysis documents
  Exit state:  Validated audio spine (speech sequence + music plan + timing)

PHASE 3: Visual Assembly
  With the audio spine locked, place video on top: A-roll video synced to its
  audio, B-roll at designated moments, establishing shots where planned.
  Verify the rough cut.
  Entry state: Audio spine + enriched clip catalog
  Exit state:  Rough cut with all video + audio placed on a multi-track timeline

PHASE 4: Enhancement
  Stack embellishments on top of the locked rough cut: transitions (video + audio),
  SFX, VFX (light leaks, screen shakes, zoom emphasis), subtitles, animations,
  and motion graphics. Enhancement point identification is partially rule-based
  (e.g., "a scene change needs a transition"); specific effect selection is
  creative judgment.
  Entry state: Rough cut timeline
  Exit state:  Fully enhanced timeline with all effects applied

PHASE 5: Refinement
  Global polish operations: color grading (per style spec node tree) and final
  audio mixing (leveling all layers, denoising dialogue, balancing SFX/music
  against speech).
  Entry state: Enhanced timeline
  Exit state:  Finished, polished timeline ready for export

PHASE 6: Export & Validation
  Render the final video file and validate the output against quality criteria.
  Entry state: Polished timeline
  Exit state:  Finished video file on the local filesystem
```

### Dependency Graph (Phase Level)

```
Phase 1 ──→ Phase 2 ──→ Phase 3 ──→ Phase 4 ──→ Phase 5 ──→ Phase 6
```

Strictly sequential at the phase level — each phase's exit state is the next
phase's entry state. No phase can begin until the previous phase is complete.

### Tacit Knowledge Registry (from PCD + collaborative discovery)

These items of tacit knowledge, discovered during the PCD capture and subsequent
analysis, inform the decomposition across all phases:

1. **A-roll vs B-roll are functionally different.** A-roll carries narrative (speech).
   B-roll is visual support. They serve different purposes and must not be conflated.
2. **Audio must form a cohesive monologue.** Stitched speech must sound natural and
   continuous. Never cut mid-sentence. Adjacent segments must logically follow.
3. **Cut points must respect natural speech boundaries.** In/out points align to
   natural speech boundaries — word endings, sentence completions, and pauses.
   These are identified precisely by on-demand precision tools when specific edit
   decisions need execution.
4. **Creation timestamp metadata IS the chronological order.** Not filenames.
5. **Temporal coherence of A-roll.** A-roll generally maintains filming order for
   visual continuity (lighting, location, clothing). Breaking order only for hooks
   or deliberate creative motivation.
6. **The video starts with a hook.** 1-3 second hook.
7. **Music and speech serve different structural roles.** Speech carries content. Music
   carries energy, fills non-speech moments, drives transitions. They mesh but don't
   compete.
8. **Enhancement decisions are two-part.** (a) Identify WHERE an effect is needed
    (partially rule-based), then (b) decide WHAT type of effect (creative judgment).
9. **Enhancements never cause structural backtracking.** Once the spine and video
    placement are locked, effects don't change clip placement.
10. **Semantic understanding and temporal precision are separate concerns.** The
    semantic analyzer identifies WHAT matters (correct text, meaning, quality);
    precision tools find WHERE it is in the timeline (frame-accurate timestamps).
    Neither works alone.
11. **Precision is on-demand.** Temporal precision tools (forced alignment, onset
    detection, pause detection) are queried when specific edit decisions need
    execution, not pre-computed for all content.
12. **Process only what you use.** Audio extraction and precision analysis happen
    only for clips selected for the edit, not for all raw input.

---

## Phase 1: Inventory & Analysis

**Goal:** Transform raw footage files into a fully analyzed clip catalog with
per-clip semantic analysis documents and temporal event indices. After this
phase, we know everything about the raw materials: what exists, what's
technically in each file, what's editorially in each file (visual scenes,
speech delivery, emotional dynamics, quality assessments), and what's
temporally in each file (word-level transcripts, scene boundaries, energy
curves).

**Entry State:** Raw footage files in a project folder on the local filesystem.

**Exit State:** An enriched clip catalog (technical metadata per file, ordered
chronologically) paired with per-clip semantic analysis documents and per-clip
temporal event indices. Analysis documents contain unified chronological blocks
covering visual, delivery, audio, emotional, and editorial dimensions plus
clip-level assessments. Temporal indices contain authoritative transcripts
with word-level timestamps, scene boundaries, and energy curves.

### Phase 1 Step Dependency Overview

```
1.1 Scan project folder
 │
 ├──────────────────────────┬──────────────────────────┐
 ▼                          ▼                          ▼
1.2 Catalog raw footage    1.3 Semantic analysis      1.4 Temporal event index
    (metadata + ordering)       (per clip)                 (per clip)
                                ⚡ NONDETERMINISTIC         DETERMINISTIC
```

Parallel opportunities:
- Steps 1.2, 1.3, and 1.4 can all execute in parallel (all depend only on 1.1).
  1.3 watches the video files directly, 1.4 processes audio/video signals
  deterministically. Neither needs the other's output.
  Their outputs are merged after all complete.

---

### Step 1.1: Scan Project Folder

```
STEP 1.1:
├── Action: Scan the designated project folder and enumerate all video files,
│          including files in subdirectories
├── Intent: Establish a complete inventory of all raw footage available for
│          this project. This is the entry point — everything downstream
│          depends on knowing what files exist.
├── Preconditions:
│   ├── State: A project folder path is provided and exists on the filesystem
│   ├── Resources: Filesystem access
│   └── Dependencies: None (entry step)
├── Input State: A project folder path (string)
├── Transformation: Recursive directory listing → filtered to video file
│   extensions → structured inventory
├── Output State: A list of video file entries:
│   [{
│     path: absolute path to the file,
│     filename: the file's name (e.g., "IMG_1234.MOV"),
│     extension: normalized lowercase (e.g., ".mov"),
│     size_bytes: file size in bytes
│   }, ...]
├── Verification:
│   ├── At least one video file is found in the folder
│   └── Every entry has a valid, accessible file path
├── Failure Modes:
│   ├── Folder does not exist → FAIL with clear error identifying the path
│   ├── Folder contains no video files → FAIL with "no footage found in [path]"
│   ├── Permission denied on folder → FAIL with permission error
│   └── Permission denied on individual file → Log warning, skip file, continue
├── State Interaction:
│   ├── Reads: [project_folder]
│   └── Writes: [raw_footage_files]
├── Parameters:
│   ├── Supported video extensions: [.mp4, .mov, .avi, .mkv, .mts, .m4v, .webm]
│   └── Recursive: true (scan subdirectories)
├── Idempotency: Yes — scanning is read-only, produces the same result on re-run
├── Classification: Deterministic / Information Retrieval
└── Notes:
    - Subdirectory scanning is important because footage may be organized in
      date-based or camera-based subfolders within the project folder.
    - The extension list should cover all common consumer/prosumer video formats.
    - File size is included for downstream reference (e.g., a 0-byte file
      indicates corruption).
```

### Step 1.2: Catalog Raw Footage

```
STEP 1.2:
├── Action: For each video file in the inventory, extract technical metadata:
│          duration, resolution, frame rate, codec, audio channels, file size,
│          creation timestamp, and rotation. Store the catalog in chronological
│          order by creation timestamp.
├── Intent: Know what we're working with. This metadata is needed for:
│          (a) chronological ordering (creation_time — the default narrative
│              order for unscripted content),
│          (b) timeline setup (resolution, frame_rate),
│          (c) audio processing decisions (audio channels, sample rate),
│          (d) conforming decisions (rotation, aspect ratio).
│          Without this, downstream steps operate blind.
│          Chronological ordering is established here because for unscripted
│          content, filming order IS the default narrative order — the subject
│          moves through a location, lighting changes, clothing is consistent
│          within a sequence. This temporal backbone is the starting point
│          from which narrative construction will work.
├── Preconditions:
│   ├── State: raw_footage_files list exists and is non-empty
│   ├── Resources: Video metadata extraction capability (e.g., a tool that
│   │   reads container format metadata and codec information)
│   └── Dependencies: [Step 1.1]
├── Input State: List of video file paths from Step 1.1
├── Transformation: For each file → extract technical metadata → produce
│   catalog entry. Sort all entries by creation_time ascending. Assign
│   sequential source_order integers (1, 2, 3, ...) reflecting chronological
│   filming sequence.
├── Output State: A structured footage catalog with metadata per file,
│   ordered chronologically:
│   [{
│     clip_id: string (generated unique identifier, e.g., "clip_001"),
│     source_order: integer (1-based, chronological position),
│     path: absolute path,
│     filename: file name,
│     duration_seconds: float (total duration),
│     width: integer (pixels),
│     height: integer (pixels),
│     frame_rate: float (fps),
│     video_codec: string (e.g., "h264", "hevc"),
│     audio_codec: string or null (e.g., "aac"),
│     audio_channels: integer or null,
│     audio_sample_rate: integer or null (Hz),
│     file_size_bytes: integer,
│     creation_time: ISO 8601 string (from container metadata),
│     rotation: integer (degrees, 0/90/180/270),
│     pixel_format: string (e.g., "yuv420p"),
│     has_audio: boolean (true if file has an audio track)
│   }, ...]
├── Verification:
│   ├── Every file in raw_footage_files has a catalog entry
│   ├── No catalog entry has null values for duration, width, height, or frame_rate
│   ├── creation_time is populated for every file
│   ├── Entries are ordered by creation_time ascending
│   ├── source_order values are unique and sequential (1 through N)
│   └── clip_id values are unique across all entries
├── Failure Modes:
│   ├── File is corrupt or unreadable → Log warning with filename, skip file,
│   │   continue. The file is excluded from all downstream processing.
│   ├── Metadata extraction capability unavailable → FAIL (cannot proceed)
│   ├── creation_time not found in metadata → FAIL with error identifying which
│   │   files lack timestamps. Chronological ordering requires this field for
│   │   every file.
│   └── Multiple clips have identical creation_time → Sort by filename as
│       tiebreaker (same-second recordings from the same device)
├── State Interaction:
│   ├── Reads: [raw_footage_files]
│   └── Writes: [clip_catalog]
├── Parameters: None
├── Idempotency: Yes — read-only extraction and deterministic sort
├── Classification: Deterministic / Information Retrieval
└── Notes:
    - creation_time must be extracted from container format metadata tags
      (e.g., format.tags.creation_time, com.apple.quicktime.creationdate for
      iPhone MOV files). This is the authoritative source of chronological
      filming order — NOT filenames.
    - Store duration as a float in seconds for arithmetic in downstream steps.
    - rotation metadata matters: phone footage is often recorded in portrait
      but stored as landscape with a rotation flag. Downstream conforming
      steps must account for this.
    - Files with no audio track are valid footage (B-roll candidates) — they
      are not errors. Mark has_audio as false.
    - The clip_catalog is the central data structure for the pipeline. It will
      be progressively enriched by Step 1.3 (semantic analysis) and subsequent
      phases.
```

### Step 1.3: Semantic Analysis ⚡ NONDETERMINISTIC

*This section is the original decomposition record for Step 1.3, kept as written.*
*The analysis document schema it describes below is superseded - the current authority is [Vision Pipeline v3 - Architecture](./architecture/vision_architecture.md).*

```
STEP 1.3:
├── Action: For each clip, perform a comprehensive visual, editorial, and
│          speech delivery analysis by reviewing the actual video content.
│          Produce a per-clip analysis document containing unified
│          chronological blocks (covering all editorial dimensions) and a
│          clip-level assessment.
├── Intent: Understand what is editorially IN every piece of footage. This is
│          the critical analysis step — the analyzer watches each clip and
│          captures everything an editor needs to make narrative, pacing, and
│          assembly decisions: visual scene descriptions, speech delivery
│          characteristics (tone, cadence, confidence, hesitation), audio
│          environment, emotional state, energy level, and subtext. It also
│          evaluates each clip's quality, tags its editorial role, and
│          identifies which portions are usable versus which should be
│          discarded.
│
│          This step focuses on dimensions that require watching the video —
│          visual composition, body language, facial expression, delivery
│          quality, and B-roll suitability. Verbatim speech transcription is
│          handled separately by signal-processing tools (Step 1.4).
│
│          Each clip is analyzed INDEPENDENTLY. The analyzer sees one clip at
│          a time. Cross-clip synthesis (finding the overall story arc,
│          identifying complementary moments across clips) happens in Phase 2,
│          not here.
├── Preconditions:
│   ├── State: raw_footage_files list exists (video file paths to analyze)
│   ├── Resources: Judgment capability (human or LLM) with the ability to
│   │   review the actual video content of each clip — including visuals,
│   │   audio, speech delivery, action, and environment
│   └── Dependencies: [Step 1.1] (runs in parallel with Steps 1.2 and 1.4)
├── Input State: List of video file paths. Video files accessible on the
│   filesystem.
├── Transformation: For each clip → watch/review the video → produce a
│   semantic analysis document containing chronological blocks and assessment
├── Output State: One semantic analysis document per clip, containing:
│
│   (A) UNIFIED CHRONOLOGICAL BLOCKS — one block per coherent moment in the
│       clip. Each block covers ALL editorial dimensions simultaneously:
│
│       blocks: [{
│         label: string — descriptive name for this moment
│                (e.g., "The Self-Conscious Start", "The Commitment")
│         visual: string — what is happening visually: framing (close-up,
│                medium, wide), camera movement (handheld, static, panning),
│                subject action (talking, walking, gesturing), environment
│                details (location, lighting, notable objects), scene
│                composition. Specific enough for B-roll matching downstream.
│         visual_suitability: "talking_head" | "establishing" | "action" |
│                            "detail" | "transition"
│                — what editorial role this visual could serve
│         audio_environment: string — ambient sounds, audio quality, notable
│                           non-speech sounds (e.g., traffic, wind, music,
│                           clapping, sighing)
│         speech_delivery: {
│           tone: string — warm, sarcastic, nervous, confident, etc.
│           cadence: string — rapid-fire, halting/hesitant, measured, rushed
│           volume: string — whispered, normal, raised, trailing off
│           notable_moments: string — voice cracks, sighs, laughing while
│                           talking, false starts, emphasis on specific words
│           delivery_quality: "clean" | "rough" | "unusable"
│                — clean: usable as-is; rough: usable with context;
│                  unusable: too hesitant/mumbled to edit around
│         } (empty object if no speech in this moment)
│         emotion: string — the subject's emotional state during this moment,
│                 including shifts and transitions
│         energy: string — pacing, intensity, dynamism. Whether the energy is
│                rising, falling, steady, or shifting.
│         subtext: string — meaning beyond the literal words. What is being
│                 communicated implicitly through tone, body language, word
│                 choice, or context.
│       }, ...]
│
│       Blocks are ordered chronologically within the clip. Together they
│       cover the full duration of the clip — no moment is unaccounted for.
│       The number of blocks depends on the clip's content: a clip with one
│       continuous thought may have 1-2 blocks; a clip with several distinct
│       moments may have 5-8 blocks.
│
│   (B) CLIP-LEVEL ASSESSMENT:
│
│       assessment: {
│         clip_type: "a_roll" | "b_roll"
│           — "a_roll": clip carries narrative speech (subject speaking to
│             camera with substantive content that could drive a story)
│           — "b_roll": clip is visual support (scenery, action, establishing
│             shots, or only incidental/non-substantive audio)
│         interest_score: integer (1-10) — how compelling is this clip's
│           usable content for the final video?
│         moment_type: "hook" | "highlight" | "body" | "establishing" | "filler"
│           — hook: attention-grabbing moment suitable for opening
│           — highlight: peak moment — emotional, funny, surprising, quotable
│           — body: solid content that carries the narrative forward
│           — establishing: sets the scene, shows location/context
│           — filler: low-value content, unlikely to be used
│         evaluation_notes: string — quality observations: visual quality,
│           audio quality, content value, anything notable
│         usable_portions: string — description of which portions of the clip
│           are worth keeping and why (e.g., "the main speech from 'And so my
│           very small announcement...' through '...every single day' is the
│           gold — clean delivery, genuine energy")
│         discard_portions: string — description of which portions to cut and
│           why (e.g., "false start before the subject commits to the
│           announcement — hesitant, restarts twice", "camera adjustment at
│           the beginning while finding framing")
│         broll_context: string — for b_roll clips, describes what speech
│           topics this visual would complement (e.g., "driving footage —
│           suitable for travel/journey metaphors")
│       }
│
│   The analysis document is stored alongside the clip catalog entry. Each
│   clip_id in the catalog maps to one analysis document.
│
├── Verification:
│   ├── Every clip in the catalog has a corresponding analysis document
│   ├── Each analysis document has at least one block
│   ├── Each block has a non-empty label
│   ├── Blocks together cover the full clip (no significant gaps)
│   ├── Every analysis document has a complete assessment section
│   ├── clip_type is either "a_roll" or "b_roll" for every clip
│   ├── interest_score is an integer from 1 to 10 for every clip
│   ├── moment_type is one of the five defined values for every clip
│   ├── At least one clip has clip_type "a_roll" (the video needs speech)
│   └── Score distribution has variance (not all clips rated the same)
├── Failure Modes:
│   ├── Analyzer cannot access video content → FAIL (video review is
│   │   essential — this step cannot work from metadata alone)
│   ├── No clips have substantive speech (all classified as b_roll) →
│   │   FAIL. The pipeline requires speech for narrative construction.
│   ├── No clip tagged as "hook" → Flag: no obvious hook moment found.
│   │   Phase 2 will need to manufacture one from the best available.
│   └── Very low scores across all clips → Flag: footage may not support
│       a compelling video. Continue but warn.
├── State Interaction:
│   ├── Reads: [raw_footage_files, video files]
│   └── Writes: [semantic_analysis_documents (one per clip)]
├── Parameters:
│   └── Analysis dimensions (all required per block):
│       - Visual scene (framing, action, environment, composition)
│       - Visual suitability (editorial role classification)
│       - Audio environment (ambient sounds, quality)
│       - Speech delivery (tone, cadence, volume, delivery quality)
│       - Emotion (subject's emotional state)
│       - Energy (pacing, intensity, dynamism)
│       - Subtext (meaning beyond the literal)
├── Idempotency: No — judgment may vary between executions
├── Classification: Nondeterministic / Evaluation & Judgment
└── Notes:
    - This is one of two nondeterministic steps in Phase 1 (alongside
      the judgment components of any manual review).
    - The analyzer must review the ACTUAL VIDEO content of each clip —
      not just metadata. Visual quality, composition, speech delivery,
      audio environment, and emotional dynamics can only be assessed by
      watching the footage.
    - No timestamps are produced by this step. The blocks describe WHAT
      happens in the clip, not precisely WHERE (in terms of frame-accurate
      timecodes).
    - The usable_portions and discard_portions fields contain CONTENT
      DESCRIPTIONS, not timestamp ranges. For example: "discard the false
      start before the subject commits" rather than "discard 0:00-0:05."
    - Audio extraction (producing standalone audio files from the video)
      is NOT a prerequisite for this step. The analyzer watches the video
      file directly.
    - Common discard reasons:
      - "false start" — subject begins speaking then restarts
      - "dead air" — extended silence with no action
      - "bad take" — subject flubs, loses train of thought, or explicitly
        restarts ("let me try that again")
      - "camera adjusting" — footage while setting up the shot
      - "unusable audio" — wind noise, interruption, inaudible speech
```

### Step 1.4: Temporal Event Index

```
STEP 1.4:
├── Action: For each clip, extract timestamped events using signal-processing
│          tools: detect scene boundaries, transcribe speech with word-level
│          timestamps, and compute audio energy curves.
├── Intent: Produce a per-clip temporal index that bridges the gap between
│          semantic analysis (which knows WHAT happens) and timeline assembly
│          (which needs to know WHEN things happen). This index provides the
│          authoritative verbatim transcript with frame-accurate word timing,
│          visual cut points, and energy dynamics.
│
│          Each clip is indexed INDEPENDENTLY. The temporal index is purely
│          signal-derived — no editorial judgment is involved.
├── Preconditions:
│   ├── State: raw_footage_files list exists (video file paths)
│   ├── Resources: Audio extraction capability, speech-to-text capability
│   │   with word-level timestamp output, scene detection capability,
│   │   audio energy analysis capability
│   └── Dependencies: [Step 1.1] (runs in parallel with Steps 1.2 and 1.3)
├── Input State: List of video file paths. Video files accessible on the
│   filesystem.
├── Transformation: For each clip →
│   extract audio (16kHz mono WAV) →
│   detect scene boundaries (visual cut/change points) →
│   transcribe speech with word-level timestamps →
│   compute per-second RMS audio energy curve →
│   produce structured temporal index document
├── Output State: One temporal index document per clip, containing:
│
│   {
│     clip_id: string,
│     source_file: string (path),
│     duration: float (seconds),
│     scene_boundaries: [{ time: float, score: float }, ...],
│     speech_regions: [{
│       start: float, end: float,
│       text: string — verbatim transcript of this speech region,
│       words: [{
│         word: string, start: float, end: float,
│         probability: float — ASR confidence for this word
│       }, ...],
│       confidence: float — average log probability for the region,
│       method: string — identifier of the ASR method used
│     }, ...],
│     energy_curve: {
│       sample_rate_hz: integer,
│       values: [float, ...] — normalized 0-1 RMS energy per sample,
│       peak_times: [float, ...] — times where energy exceeds threshold
│     }
│   }
│
├── Verification:
│   ├── Every clip has a corresponding temporal index document
│   ├── All speech regions have non-empty text and word arrays
│   ├── Word timestamps are monotonically increasing within each region
│   ├── All timestamps fall within [0, clip_duration]
│   ├── Energy curve length matches expected duration × sample_rate
│   └── At least one clip has speech regions (pipeline requires speech)
├── Failure Modes:
│   ├── Audio extraction fails → FAIL with error details
│   ├── ASR produces no output for a clip with audible speech → Flag:
│   │   may indicate audio quality issues. Continue with empty regions.
│   └── Scene detection produces no boundaries → Normal for single-shot
│       clips. Not an error.
├── State Interaction:
│   ├── Reads: [raw_footage_files, video files]
│   └── Writes: [temporal_event_indices (one per clip)]
├── Parameters: None (deterministic signal processing)
├── Idempotency: Yes — same input produces same output
├── Classification: Deterministic / Data Transformation
└── Notes:
    - This step produces the AUTHORITATIVE transcript for each clip.
      The transcript is derived from the audio signal and is verbatim.
    - Word-level timestamps enable downstream steps to look up precise
      source positions for any text passage without requiring a separate
      alignment step.
    - The energy curve enables downstream steps to identify high-energy
      moments, silence gaps, and pacing patterns for editorial decisions.
    - Scene boundaries are useful for identifying natural cut points and
      visual transitions within a single clip.
```

---

**Phase 1 is complete.** Steps 1.1–1.3 + 1.4 cover the full inventory and
analysis pipeline: scan → catalog + semantic analysis + temporal index (in parallel).

**Exit state:** An enriched clip catalog with per-clip semantic analysis
documents and per-clip temporal indices. Semantic analysis documents contain
visual/audio/emotional descriptions + quality assessment + moment tagging +
usable/discard guidance. Temporal indices contain authoritative transcripts
with word-level timestamps, scene boundaries, and energy curves. Together,
these provide the complete input for Phase 2 (Audio Spine Construction).

---

## Phase 2: Audio Spine Construction

**Goal:** Build the audio backbone of the video — the speech monologue and music
plan that together define the narrative, pacing, and energy of the entire video.
All visual decisions (Phase 3) and enhancement decisions (Phase 4) are downstream
of this spine. Get this wrong and nothing later can save it.

**Entry State:** Enriched clip catalog with per-clip semantic analysis documents
(correct transcripts, multi-dimensional editorial descriptions, clip assessments,
usable/discard guidance).

**Exit State:** A validated audio spine specification containing:
- An ordered sequence of speech segments forming a coherent narrative
- Selected music track(s) with specific splice points
- A structural plan defining how speech and music interplay, including the hook,
  intro structure, transition slots, and timing
- Estimated total duration validated against the 30-60 second target

### Phase 2 Step Dependency Overview

```
2.1 Define creative direction              ⚡ NONDETERMINISTIC
 │
 ├──────────────────────────┐
 ▼                          ▼
2.2 Construct speech        2.4 Select and prepare music
    sequence                    (PARALLEL)
    ⚡ NONDETERMINISTIC          ⚡ NONDETERMINISTIC
 │                          │
 └──────────┬───────────────┘
            ▼
2.5 Mesh and refine audio spine            ⚡ NONDETERMINISTIC
 │
 ▼
2.6 Calculate spine timing                 DETERMINISTIC
 │
 ▼
2.7 Validate audio spine                   ⚡ NONDETERMINISTIC
```

- Step 2.1 establishes the shared creative north star (target mood, emotion, vibe)
- Steps 2.2 and 2.4 execute in PARALLEL — both are guided by the creative direction,
  not by each other. Speech and music are co-dependent on the same vision, not
  sequentially dependent.
- Step 2.2 produces a text-based speech sequence with timestamp-resolved passages
  (timestamps are looked up from the temporal event indices produced in Phase 1).
- Step 2.5 is where the two halves come together. This is also where mismatches
  get resolved — a speech passage may be cut not because it's bad speech, but
  because it doesn't serve the shared mood (e.g., a joke interjection in an
  otherwise motivational flow). Similarly, a music splice may be swapped if it
  doesn't complement the speech energy.
- Steps 2.6 and 2.7 are timing computation and final validation.

If validation (2.7) fails, the failure mode specifies which earlier step to
revisit. This creates a conditional loop in the process DAG.

---

### Step 2.1: Define Creative Direction ⚡ NONDETERMINISTIC

```
STEP 2.1:
├── Action: Review the per-clip semantic analysis documents and clip catalog
│          to identify the video's target creative direction — the mood,
│          emotion, vibe, and narrative angle that will guide BOTH speech
│          selection and music selection
├── Intent: Establish the shared north star for the entire video before
│          either half of the spine is built. Speech and music are not
│          dependent on each other — they are both dependent on THIS
│          creative direction. Without it, speech and music are selected
│          in isolation and may not complement each other.
│
│          This step answers: "What is this video trying to make the
│          viewer FEEL?" The answer drives every selection downstream.
│
│          This is also where the per-clip analyses are SYNTHESIZED for
│          the first time. Phase 1 analyzed each clip independently; this
│          step reads across all clips to find the overall story arc,
│          emotional landscape, and strongest narrative thread. The output
│          must capture enough emotional and energy context that both the
│          speech selector (2.2) and the music selector (2.3) can work
│          from it without needing to re-read the raw analyses.
├── Preconditions:
│   ├── State: clip_catalog, semantic_analysis_documents (one per clip)
│   ├── Resources: Creative/judgment capability (human or LLM)
│   └── Dependencies: [Phase 1 complete (Steps 1.1-1.3)]
├── Input State:
│   - semantic_analysis_documents (per-clip unified blocks with correct
│     transcripts, visual/audio/emotional descriptions, assessments)
│   - clip_catalog (technical metadata, chronological source order)
│   - video_topic (if provided)
├── Transformation: Read all semantic analyses → synthesize across clips →
│   identify the strongest narrative thread → define the target mood,
│   emotion, energy, and emotional landscape
├── Output State: creative_direction:
│   {
│     narrative_theme: string (1-2 sentence description of what this
│       video is about — e.g., "Morning routine vlog with workout and
│       reflection on consistency"),
│     target_mood: string (e.g., "motivational", "reflective", "playful",
│       "cinematic and aspirational", "raw and vulnerable"),
│     target_energy: string ("low" | "medium" | "high" | "building" |
│       "dynamic" — describes the overall energy feel),
│     energy_arc: string (e.g., "start high with hook → sustain energy
│       → build to key moment → resolve", or "slow reflective start →
│       build gradually → peak → gentle close"),
│     emotional_landscape: string (description of the emotional territory
│       across the footage — e.g., "starts self-conscious and vulnerable,
│       builds to genuine excitement about the commitment, dips into
│       self-doubt about public perception, resolves with determined
│       acceptance". This gives the music selector the full emotional
│       contour to match against.),
│     audience_emotion: string (what should the viewer feel after watching
│       — e.g., "inspired to take action", "feeling calm and grounded",
│       "entertained and curious"),
│     key_moments: [string, ...] (the 2-3 strongest moments from the
│       footage that MUST appear in the final video — these anchor the
│       creative direction. Described by content, not timestamps.),
│     rationale: string (why this direction was chosen over alternatives)
│   }
├── Verification:
│   ├── All fields are populated
│   ├── target_mood and energy_arc are coherent (not contradictory)
│   ├── key_moments reference actual clips in the catalog
│   └── The direction is specific enough to guide selection decisions
│       (not generic like "make a good video")
├── Failure Modes:
│   └── Footage doesn't support a clear creative direction → Flag for
│       human input. The human may need to specify the angle they want,
│       or the footage may genuinely not support a cohesive video.
├── State Interaction:
│   ├── Reads: [clip_catalog, semantic_analysis_documents, video_topic]
│   └── Writes: [creative_direction]
├── Parameters: None (pure creative judgment)
├── Idempotency: No — creative judgment may vary
├── Classification: Nondeterministic / Creative Judgment
└── Notes:
    - This step is the ROOT of the creative tree. Steps 2.2 (speech) and
      2.4 (music) both branch from it in parallel.
    - The creative_direction is NOT a script or a shot list — it's a
      compass. It says "we're going for motivational energy" so that the
      speech selector knows to prioritize motivational moments and the
      music selector knows to find uplifting tracks.
    - key_moments are the non-negotiable anchors. These are the moments
      that inspired this creative direction. Everything else in the
      video exists to set up, support, and land these moments.
    - The narrative_theme is factored out of the old Step 2.1 (now 2.2).
      It used to be produced as part of speech construction, but it
      actually belongs here because it guides BOTH halves.
```

### Step 2.2: Construct Speech Sequence ⚡ NONDETERMINISTIC

```
STEP 2.2:
├── Action: Guided by the creative direction, select and sequence A-roll
│          speech passages into a coherent narrative monologue. This includes
│          identifying the hook (the opening attention-grabber pulled from
│          the most compelling speech moment) and determining the narrative
│          arc.
├── Intent: Build the first half of the audio spine — the spoken content
│          that carries the narrative. Guided by the creative direction's
│          target mood and energy, select the speech passages that best
│          serve that vision.
│
│          This step is PURE SELECTION — it produces a text-based plan
│          of which passages to use and in what order. No timestamps are
│          resolved here. The next step (2.3) takes this text-based plan
│          and resolves precise timestamps. This separation respects the
│          principle that judgment and execution are always split.
├── Preconditions:
│   ├── State: semantic_analysis_documents, clip_catalog, creative_direction
│   ├── Resources: Creative/judgment capability (human or LLM)
│   └── Dependencies: [Step 2.1]
├── Input State:
│   - creative_direction (target mood, energy, emotional landscape,
│     key moments — the compass)
│   - semantic_analysis_documents (per-clip unified blocks with correct
│     transcripts, assessments, usable/discard guidance)
│   - clip_catalog (technical metadata, chronological source order)
├── Transformation: Read semantic analyses → guided by creative direction →
│   select passages that serve the target mood/energy → determine
│   sequence order → identify hook → produce the ordered text-based
│   speech sequence
├── Output State: speech_sequence — an ordered list specifying every speech
│   passage in the final video's narrative (TEXT-BASED, no timestamps):
│   {
│     hook_segment: {
│       clip_id: string,
│       text: string (the hook text — may be a snippet of a longer passage),
│       rationale: string (why this is the hook — what makes it attention-
│         grabbing)
│     },
│     body_sequence: [
│       {
│         position: integer (1, 2, 3, ... — playback order),
│         clip_id: string,
│         text: string (the exact spoken words in this passage),
│         role: string ("opening" | "development" | "climax" | "resolution"),
│         flow_note: string (how this passage connects to the next — e.g.,
│           "sets up the contrast that the next passage resolves")
│       }, ...
│     ],
│     excluded_passages: [
│       {
│         clip_id: string,
│         text: string (first 50 chars),
│         reason_excluded: string (e.g., "redundant with passage at
│           position 3", "tangential to theme", "weak content",
│           "doesn't fit duration target")
│       }, ...
│     ]
│   }
├── Verification:
│   ├── A hook_segment is identified
│   ├── body_sequence contains at least 3 passages (enough for a narrative)
│   ├── body_sequence is in a logical order — reading the text field of
│   │   each passage in sequence should form a coherent monologue
│   ├── No passage appears more than once (except hook, which may be a
│   │   snippet of a body passage)
│   ├── Adjacent passages flow logically (the end of one leads naturally
│   │   to the start of the next — no jarring topic jumps without cause)
│   └── Every referenced clip_id exists in the clip_catalog
├── Failure Modes:
│   ├── No coherent narrative can be constructed from the footage → FAIL.
│   │   The footage may not support a shortform video. Surface this to
│   │   the human for a decision (different angle, different topic, abort).
│   └── No good hook found → Flag. Use the highest-scoring passage as
│       hook even if it's not ideal. The hook can also be manufactured
│       by extracting a compelling sub-portion of a longer passage.
├── State Interaction:
│   ├── Reads: [clip_catalog, semantic_analysis_documents, creative_direction]
│   └── Writes: [speech_sequence]
├── Parameters:
│   ├── Minimum body passages: 3
│   ├── Hook duration target: 1-3 seconds (per style spec)
│   └── Narrative guidance:
│       - Prefer chronological source_order as the default sequence
│         (maintains visual continuity — lighting, location, clothing)
│       - Break chronological order ONLY for the hook (pulling a later
│         moment to the front) or for deliberate narrative motivation
│         (e.g., contrast, callback, punchline)
│       - Prioritize passages from clips with higher interest_scores
│       - Prefer passages from clips tagged as "highlight" or "body" for
│         the main sequence; use "hook"-tagged clips for the opening
│       - Never select a partial sentence — passage boundaries should
│         align to complete thoughts
│       - Adjacent passages must logically follow (no non-sequiturs)
├── Idempotency: No — creative judgment may vary between executions
├── Classification: Nondeterministic / Creative Construction
└── Notes:
    - The hook passage CAN be a snippet of a passage that also appears
      in the body sequence. Example: the punchline of a sentence appears
      as the 2-second hook at the start, then the full sentence plays
      in context later in the body. This is a common shortform technique.
    - The "role" field in body_sequence is for the narrative builder's
      own tracking and for downstream understanding. It should follow
      a natural arc: opening → development → climax → resolution.
      Not every passage needs a distinct role — most will be "development."
    - The flow_note for the LAST passage in body_sequence should describe
      how the video ends (e.g., "closes with a forward-looking statement"
      or "ends on the punchline").
    - excluded_passages are documented for transparency — they show what
      was considered and why it was cut. This helps if the narrative
      needs to be revised later (e.g., if the video is too short and
      more content is needed).
    - This step does NOT produce timestamps or durations. It produces a
      TEXT-BASED PLAN — a sequence of passages identified by clip_id and
      verbatim text, with timestamps resolved from the temporal event
      indices produced in Phase 1.
```

### Step 2.4: Select and Prepare Music ⚡ NONDETERMINISTIC

```
STEP 2.4:
├── Action: Based on the creative direction's target mood, energy, and
│          emotional landscape, select one or more music tracks and
│          identify the specific sections/splices of each track to use
│          in the video
├── Intent: Build the second half of the audio spine — the musical backbone
│          that carries energy, fills non-speech moments, and drives the
│          emotional undercurrent of the video. Music and speech are
│          complementary: speech carries content, music carries feeling.
│          The right music elevates the narrative; the wrong music
│          undermines it.
├── Preconditions:
│   ├── State: creative_direction exists
│   ├── Resources: Music source (library, catalog, or ad-hoc source),
│   │   judgment capability
│   └── Dependencies: [Step 2.1]
├── Input State:
│   - creative_direction (target mood, energy, energy arc — the compass)
│   - style_specification (music rules: mood matching, energy curve,
│     beatmatching preference, no hard starts/stops)
├── Transformation: Analyze speech content's mood/energy → identify music
│   requirements → select track(s) → identify specific splices → document
│   selections with rationale
├── Output State: music_selections — the complete music plan:
│   {
│     overall_mood: string (e.g., "uplifting and energetic", "reflective
│       and chill", "motivational with cinematic feel"),
│     overall_energy: string ("low" | "medium" | "high" | "building"),
│     tracks: [
│       {
│         track_id: string (generated identifier, e.g., "track_01"),
│         track_source: string (path to file, URL, or description of
│           the track for identification),
│         track_name: string (title or working name),
│         genre: string (e.g., "lo-fi", "cinematic", "trap"),
│         bpm: integer (beats per minute — critical for beatmatching),
│         key: string (musical key, if known),
│         splices: [
│           {
│             splice_id: string (e.g., "track_01_splice_A"),
│             start_time: float (within the music track file),
│             end_time: float,
│             duration_seconds: float,
│             section_type: string ("intro" | "verse" | "chorus" |
│               "bridge" | "drop" | "buildup" | "outro" | "ambient"),
│             energy_level: string ("low" | "medium" | "high" | "peak"),
│             intended_use: string (how this splice will be used in the
│               video — e.g., "hook intro music", "background under speech",
│               "transition energy boost", "outro fade")
│           }, ...
│         ],
│         selection_rationale: string (why this track fits the narrative
│           and mood)
│       }, ...
│     ]
│   }
├── Verification:
│   ├── At least one track is selected
│   ├── Each track has at least one splice identified
│   ├── Splice timestamps are valid (within track duration, start < end)
│   ├── overall_mood and energy are coherent with the speech_sequence's
│   │   narrative_theme and role progression
│   ├── bpm is documented (needed for beatmatching in Phase 4)
│   └── intended_use is specified for each splice (no orphan splices
│       without a purpose)
├── Failure Modes:
│   ├── No suitable music found → Flag for human intervention. Music
│   │   selection may require browsing/searching beyond what's immediately
│   │   available. The human may need to source new music.
│   ├── Music source unavailable → FAIL (no music library accessible)
│   └── BPM unknown → Estimate from audio analysis or note as "unknown."
│       Beatmatching in Phase 4 will be approximate without BPM data.
├── State Interaction:
│   ├── Reads: [creative_direction, style_specification]
│   └── Writes: [music_selections]
├── Parameters:
│   ├── Music rules from style spec:
│   │   - Mood must match or enhance the emotional arc
│   │   - Energy should mirror the video's pacing
│   │   - Not genre-locked — genre serves the content
│   │   - Volume: background level, duck under speech
│   │   - Never hard-start or hard-stop music (fades required)
│   └── Splice selection guidance:
│       - Identify at least one "intro/hook" splice (for the video opening)
│       - Identify at least one "background" splice (for under speech)
│       - Identify transition splices if the track has distinct energy shifts
│       - Splices should be clean cut points — on beat boundaries when possible
├── Idempotency: No — creative judgment may vary
├── Classification: Nondeterministic / Creative Selection
└── Notes:
    - The music_selections output is a MENU of available splices, not
      the final placement plan. Step 2.5 (Mesh and Refine Audio Spine)
      determines exactly where each splice goes and how it interacts
      with speech. This step identifies what music material is available.
    - Multiple tracks may be selected if the video has distinct energy
      sections (e.g., a chill intro transitioning to a high-energy body).
      However, for 30-60 second videos, one track with multiple splices
      is more common than multiple tracks.
    - BPM is critical for Phase 4 beatmatching — the style spec says
      "major cuts and transitions should land on musical beats." Without
      BPM, the enhancement step can't compute beat positions.
    - The distinction between "select a track" and "identify splices"
      matters: a 3-minute track is never used in full. The creator
      picks specific 5-15 second sections that fit particular moments
      in the video. These are the splices.
```

### Step 2.5: Mesh and Refine Audio Spine ⚡ NONDETERMINISTIC

```
STEP 2.5:
├── Action: Combine the speech sequence and music selections into a complete
│          audio spine — an ordered structure defining every audio event in
│          the video and how speech and music interplay. This includes
│          designing the hook/intro structure, placing music splices relative
│          to speech blocks, defining transition slots (non-speech moments
│          where music and B-roll will fill), and specifying the overall
│          flow from start to finish.
├── Intent: This is where the two halves of the spine MESH — and where
│          mismatches get resolved. Speech and music were selected in
│          parallel, both guided by the creative direction. Now they must
│          be woven into one coherent timeline plan. This step also REFINES
│          both halves: a speech segment may be cut because it doesn't fit
│          the mood when heard alongside the music (e.g., a joke in an
│          otherwise motivational flow). A music splice may be swapped if
│          it doesn't complement the speech energy.
│
│          Key decisions made here:
│          - How does the video open? (hook → music intro → speech start)
│          - Where do music-driven moments go? (between speech blocks)
│          - How does the energy flow? (build, sustain, resolve)
│          - Where are the breathing points / transition slots?
│          - Do any speech segments need to be cut for mood coherence?
│          - Do any music splices need to be swapped?
├── Preconditions:
│   ├── State: speech_sequence, music_selections, and creative_direction
│   ├── Resources: Creative/judgment capability
│   └── Dependencies: [Step 2.2, Step 2.4]
├── Input State:
│   - speech_sequence (hook_segment, body_sequence with durations and roles)
│   - music_selections (tracks with splices and intended uses)
│   - creative_direction (the shared vision — used to resolve conflicts)
│   - style_specification (pacing rules, hook requirements, energy contour)
├── Transformation: Interleave speech and music into a sequential structure
│   → design the opening → place transition slots → assign music splices
│   to specific positions → produce the complete spine blueprint
├── Output State: audio_spine — the complete structural plan:
│   {
│     total_estimated_duration_seconds: float (rough total before exact calc),
│     structure: [
│       {
│         position: integer (1, 2, 3, ... — sequential playback order),
│         block_type: string (see block types below),
│         duration_seconds: float (estimated),
│         content: { ... block-type-specific fields ... },
│         music_behavior: string ("prominent" | "background" | "fade_in" |
│           "fade_out" | "silent"),
│         music_splice_id: string or null (which splice plays here),
│         visual_note: string (guidance for Phase 3 — what kind of visual
│           belongs here, e.g., "B-roll establishing shot", "A-roll talking
│           head", "transition moment")
│       }, ...
│     ]
│   }
│
│   BLOCK TYPES:
│   - "hook": The opening attention-grabber (speech snippet, 1-3 seconds).
│     content: { clip_id, start_time, end_time, text }
│   - "intro": Music + visual moment before speech begins (typically B-roll
│     or establishing shots with prominent music). No speech.
│     content: { purpose: string (e.g., "set the scene", "build energy") }
│   - "speech": A contiguous block of spoken A-roll audio.
│     content: { segments: [{clip_id, start_time, end_time,
│     text, duration_seconds}, ...] }
│     Multiple consecutive speech segments may be grouped into one speech
│     block if there's no transition/breathing room between them.
│   - "transition_slot": A non-speech moment between speech blocks where
│     music, B-roll, and effects carry the video forward. These create
│     pacing variety and prevent the video from being a monotone monologue.
│     Duration varies based on the type and intent of the transition
│     (e.g., a dramatic pause may be brief, a scene change may be longer,
│     a musical buildup may be several seconds).
│     content: { purpose: string (e.g., "energy shift", "scene change",
│     "breathing room", "visual emphasis", "dramatic pause"),
│     duration_seconds: float (determined by the transition's intent) }
│   - "outro": The closing section — may be speech trailing off, music
│     fading, or a final visual moment.
│     content: { purpose: string }
│
│   STRUCTURAL RULES:
│   - The spine MUST start with a "hook" block
│   - A "hook" is followed by an "intro" block (music + B-roll before
│     speech begins) — per tacit knowledge item 6
│   - Speech blocks should not exceed ~8-10 seconds without a transition
│     slot or B-roll interjection (per style spec: talking head shots
│     need variety after a few seconds)
│   - The spine MUST end (either with the last speech block resolving
│     naturally or with an explicit outro)
│   - music_behavior must be specified for every block — this tells
│     Phase 5 (Refinement) how to handle music levels
│   - MUSIC AND SPEECH CAN OVERLAP. Blocks define the PRIMARY content
│     driving the moment, not exclusive audio slots. A "speech" block
│     with music_behavior "background" means speech and music play
│     simultaneously — music provides an emotional bed under the speech.
│     The music_behavior field controls how music relates to the block:
│     "prominent" (music leads), "background" (music under speech),
│     "fade_in"/"fade_out" (transitioning), or "silent" (no music —
│     e.g., for dramatic effect, raw emotional moments, or emphasis).
├── Verification:
│   ├── structure is non-empty and starts with a "hook" block
│   ├── At least one "speech" block exists
│   ├── Every speech segment from body_sequence appears in exactly one
│   │   speech block (nothing lost, nothing duplicated)
│   ├── Music splice assignments are valid (referenced splice_ids exist
│   │   in music_selections)
│   ├── No speech block exceeds 10 seconds without a transition_slot
│   │   or B-roll visual_note
│   ├── music_behavior is specified for every block
│   └── The structure follows a coherent energy arc (not random)
├── Failure Modes:
│   ├── Speech and music don't mesh well → Revisit Step 2.4 (different
│   │   music selection) or Step 2.2 (adjust speech sequence)
│   └── Structure feels monotonous or lacks variety → Add more
│       transition_slots, vary music behavior, adjust pacing
├── State Interaction:
│   ├── Reads: [speech_sequence, music_selections, creative_direction,
│   │          style_specification]
│   └── Writes: [audio_spine]
├── Parameters:
│   ├── Max speech block duration before transition: 8-10 seconds
│   ├── Transition slot duration: varies by intent — determined by the
│   │   purpose of the transition (dramatic pause, scene change, energy
│   │   shift, musical buildup, etc.). No fixed minimum or maximum.
│   └── Energy contour from style spec: start high (hook) → maintain
│       with variety → build to moment → resolve
├── Idempotency: No — creative judgment in structure design
├── Classification: Nondeterministic / Creative Construction
└── Notes:
    - The visual_note field is guidance for Phase 3, NOT a binding
      assignment. It says "this is a good place for B-roll" or "this
      needs a talking head shot." Phase 3 makes the actual clip selection.
    - Transition slots are CRITICAL for pacing. A 30-60 second video
      that's all speech with no breathing room feels exhausting. These
      slots are where the music carries the energy and B-roll provides
      visual variety.
    - The hook_segment from speech_sequence may be a snippet of a
      segment that also appears in a speech block. This is intentional:
      the hook teases a moment, then the full context plays later.
    - music_behavior "background" means music plays quietly under speech.
      "prominent" means music is the primary audio (no competing speech).
      "fade_in" and "fade_out" are transitions between prominent and
      background states. These values inform the audio mixing in Phase 5.
```

### Step 2.6: Calculate Spine Timing

```
STEP 2.6:
├── Action: Walk through the audio spine structure and compute cumulative
│          timestamps for every block, producing exact start/end times
│          for each block in the final timeline
├── Intent: Transform the sequential structure into a timed plan. After
│          this step, every block has absolute timeline positions — we know
│          exactly when each speech segment, music moment, and transition
│          slot starts and ends. This is the data Phase 3 needs to place
│          clips at specific positions on the timeline.
├── Preconditions:
│   ├── State: audio_spine exists with duration_seconds per block
│   ├── Resources: None (arithmetic)
│   └── Dependencies: [Step 2.6]
├── Input State: Audio spine with estimated durations per block
├── Transformation: Walk the structure array in order → accumulate durations
│   → assign timeline_start and timeline_end to each block
├── Output State: Timed audio spine — each block gains:
│   - timeline_start: float (seconds from video start)
│   - timeline_end: float (seconds from video start)
│   And the spine gains:
│   - total_duration_seconds: float (timeline_end of the last block)
├── Verification:
│   ├── timeline_start of block N+1 equals timeline_end of block N (no gaps)
│   ├── All timeline values are non-negative
│   ├── total_duration_seconds equals sum of all block durations
│   └── total_duration_seconds is a real number (no NaN or infinity)
├── Failure Modes:
│   └── Any block has duration_seconds ≤ 0 → FAIL with error identifying
│       the block. Every block must have positive duration.
├── State Interaction:
│   ├── Reads: [audio_spine]
│   └── Writes: [audio_spine (enriched with timeline positions)]
├── Parameters: None
├── Idempotency: Yes — deterministic arithmetic
├── Classification: Deterministic / Data Transformation
└── Notes:
    - This is pure arithmetic — no judgment involved.
    - The durations from Step 2.5 for transition_slots and intro blocks
      are set by creative judgment (based on the intent and type of each
      transition). Speech block durations are exact (from the precision-
      resolved timestamps from temporal event indices). Music splice durations are exact
      (from the track analysis).
    - These timeline positions become the authoritative placement
      coordinates for Phase 3 (Visual Assembly).
```

### Step 2.7: Validate Audio Spine ⚡ NONDETERMINISTIC

```
STEP 2.7:
├── Action: Review the complete, timed audio spine and validate it against
│          quality criteria: duration target, narrative coherence, pacing
│          quality, and structural completeness
├── Intent: Catch problems before committing to visual assembly. Once the
│          spine is locked and Phase 3 begins, changing it means redoing
│          significant downstream work. This is the last checkpoint to
│          ensure the audio foundation is solid.
├── Preconditions:
│   ├── State: audio_spine with timeline positions and total_duration
│   ├── Resources: Judgment capability (for coherence and pacing assessment)
│   └── Dependencies: [Step 2.6]
├── Input State: Timed audio spine
├── Transformation: Evaluate spine against validation criteria → produce
│   validation result
├── Output State: validation_result:
│   {
│     status: "pass" | "fail",
│     total_duration_seconds: float,
│     duration_check: {
│       in_range: boolean (30-60 seconds),
│       assessment: string
│     },
│     coherence_check: {
│       pass: boolean,
│       issues: [string, ...] (e.g., "topic jump between blocks 3 and 4",
│         "hook doesn't relate to body content")
│     },
│     pacing_check: {
│       pass: boolean,
│       issues: [string, ...] (e.g., "too many speech blocks without
│         transitions", "intro too long", "ending feels abrupt")
│     },
│     energy_check: {
│       pass: boolean,
│       issues: [string, ...] (e.g., "energy is flat — no build",
│         "music and speech energy mismatch")
│     },
│     structural_check: {
│       pass: boolean,
│       issues: [string, ...] (e.g., "no outro — video ends abruptly",
│         "hook missing")
│     },
│     overall_notes: string (summary assessment),
│     recommended_action: string or null (if fail: which step to revisit
│       — e.g., "return to 2.1 to trim speech", "return to 2.2 to add
│       transition slots")
│   }
├── Verification:
│   ├── All check fields are populated
│   ├── status is "pass" only if ALL sub-checks pass
│   └── If status is "fail", recommended_action is non-null
├── Failure Modes:
│   ├── Duration too short (< 30 seconds) → Return to Step 2.2 to include
│   │   more speech segments or Step 2.5 to lengthen transition slots
│   ├── Duration too long (> 60 seconds) → Return to Step 2.2 to cut
│   │   speech segments or Step 2.5 to shorten transition slots
│   ├── Incoherent narrative → Return to Step 2.2 to restructure the
│   │   speech sequence
│   ├── Poor pacing → Return to Step 2.5 to adjust transition slots and
│   │   music placement
│   ├── Music-speech energy mismatch → Return to Step 2.4 to select
│   │   different music or Step 2.5 to adjust music behavior
│   └── Missing structural elements (no hook, no ending) → Return to
│       Step 2.5 to add the missing elements
├── State Interaction:
│   ├── Reads: [audio_spine]
│   └── Writes: [validation_result]
├── Parameters:
│   ├── Duration target: 30-60 seconds
│   ├── Maximum consecutive speech without transition: 10 seconds
│   └── Required structural elements: hook, at least one speech block,
│       defined ending (outro or final speech block)
├── Idempotency: No — judgment on coherence and pacing may vary
├── Classification: Nondeterministic / Evaluation & Judgment
└── Notes:
    - Duration check is deterministic (simple math). Coherence, pacing,
      and energy checks require judgment.
    - The loop-back mechanism: if validation fails, the recommended_action
      identifies which earlier step to revisit. This creates conditional
      loops in the process DAG:
        - Duration issues → loop to 2.1 (adjust speech content)
        - Pacing/structure issues → loop to 2.2 (adjust structure)
        - Music fit issues → loop to 2.2 (adjust music selection)
    - There should be a maximum iteration count (e.g., 3 loops) to
      prevent infinite revision. If the spine can't pass validation
      after 3 attempts, escalate to human review.
    - When the spine passes validation, it is LOCKED. No changes to the
      audio spine should occur in subsequent phases. Phases 3-6 build
      on top of this foundation.
```

---

**Phase 2 is complete.** Steps 2.1–2.7 cover the full audio spine construction:
define creative direction → construct speech sequence (text) → resolve timestamps
∥ select music → mesh and refine → calculate timing → validate.

**Exit state:** A validated, timed audio spine — the complete blueprint for the
video's audio layer. This is the locked foundation for Phase 3 (Visual Assembly).

**Key characteristics of this phase:**
- 7 steps: 5 nondeterministic (creative judgment), 2 deterministic (execution)
- Speech and music construction happen in PARALLEL, both guided by the creative direction
- Speech selection (judgment) and timestamp resolution (execution) are cleanly split
- The meshing step (2.5) resolves conflicts between the two halves
- Contains a conditional loop (validation failure → revisit earlier steps)
- The most consequential creative phase — the spine determines everything downstream

---

## Phase 3: Visual Assembly

**Goal:** Place video on top of the locked audio spine. Every speech block gets
its A-roll video, every transition slot and intro block gets B-roll, and the
complete visual layer is assembled into a shot list that can be mechanically
placed on a multi-track timeline.

**Entry State:** Validated, timed audio spine + enriched clip catalog.

**Exit State:** A verified shot list (edit decision list) mapping every spine
block to specific video clips with source references, in/out points, and
timeline positions. This is the complete blueprint for the rough cut — a
deterministic executor can build the timeline from this data alone.

### Phase 3 Step Dependency Overview

```
3.1 Assign A-roll video to speech blocks     DETERMINISTIC
 │
 ▼
3.2 Select and assign B-roll                 ⚡ NONDETERMINISTIC
 │
 ▼
3.3 Build shot list                          DETERMINISTIC
 │
 ▼
3.4 Verify rough cut                         ⚡ NONDETERMINISTIC
```

Sequential — each step builds on the previous.
- A-roll assignment (3.1) is deterministic because A-roll video is inherently
  linked to its audio (same source file, same timestamps)
- B-roll selection (3.2) requires judgment — which B-roll clip fits which moment?
- Shot list assembly (3.3) is deterministic data transformation
- Rough cut verification (3.4) is the final quality gate before enhancement

---

### Step 3.1: Assign A-Roll Video to Speech Blocks

```
STEP 3.1:
├── Action: For each speech block in the audio spine, assign the corresponding
│          A-roll video — the video from the same source file and timestamps
│          as the speech audio
├── Intent: A-roll audio and video come from the SAME source file — the
│          subject was speaking to camera, so the video is inherently linked
│          to the audio. This step simply makes that link explicit: "this
│          speech from clip_003 at 2.1s-8.4s → use the video from clip_003
│          at 2.1s-8.4s." No creative judgment is needed because the video
│          IS the audio's visual counterpart.
├── Preconditions:
│   ├── State: audio_spine (validated, timed) with speech blocks containing
│   │   clip_id and timestamp references, clip_catalog
│   ├── Resources: None (data mapping)
│   └── Dependencies: [Phase 2 complete — Step 2.7]
├── Input State: Audio spine with speech blocks, clip catalog with source
│   file paths
├── Transformation: For each speech block → for each segment within the
│   block → look up the source file in clip_catalog → create a video
│   assignment with matching timestamps
├── Output State: a_roll_assignments — video assignments for all speech blocks:
│   [{
│     spine_block_position: integer (which block in the spine),
│     block_type: "speech",
│     timeline_start: float (from spine),
│     timeline_end: float (from spine),
│     video_segments: [{
│       clip_id: string,
│       source_file: absolute path,
│       video_in: float (start time within source file),
│       video_out: float (end time within source file),
│       duration_seconds: float,
│       width: integer,
│       height: integer,
│       frame_rate: float,
│       rotation: integer,
│       needs_conform: boolean (true if resolution, frame rate, or
│         rotation differs from the target output — 1080x1920 9:16)
│     }, ...]
│   }, ...]
│
│   Also assigns the hook block's video (hook is a speech snippet):
│   hook_assignment: {
│     spine_block_position: 1 (hook is always position 1),
│     clip_id: string,
│     source_file: absolute path,
│     video_in: float,
│     video_out: float,
│     needs_conform: boolean
│   }
├── Verification:
│   ├── Every speech block has a video assignment
│   ├── The hook block has a video assignment
│   ├── Video in/out times match the audio segment in/out times exactly
│   ├── Every referenced source_file exists in the clip_catalog
│   └── needs_conform is correctly flagged (resolution != 1080x1920 or
│       rotation != 0 or frame_rate differs from target)
├── Failure Modes:
│   ├── Source file not found for a clip_id → FAIL (data integrity error —
│   │   the clip should exist in the catalog from Phase 1)
│   └── Segment timestamps exceed source file duration → FAIL (data
│       integrity error from upstream steps)
├── State Interaction:
│   ├── Reads: [audio_spine, clip_catalog]
│   └── Writes: [a_roll_assignments]
├── Parameters:
│   └── Target output specs: 1080x1920, 9:16 aspect ratio (from style spec)
├── Idempotency: Yes — deterministic mapping
├── Classification: Deterministic / Data Transformation
└── Notes:
    - This step is trivially deterministic because A-roll video and audio
      are the same source file. The timestamps are identical. There is
      no selection or judgment — just a data lookup.
    - The needs_conform flag is important for Phase 3's timeline setup:
      clips that aren't already in 1080x1920 portrait need to be scaled,
      cropped, or rotated. The actual conforming is a Phase 3 concern
      when placing clips on the timeline, but the FLAG is set here.
    - For the hook: if the hook is a snippet of a segment that also
      appears in the body, it uses the same source file but with
      truncated timestamps.
```

### Step 3.2: Select and Assign B-Roll ⚡ NONDETERMINISTIC

```
STEP 3.2:
├── Action: For each non-speech block in the audio spine (intro, transition
│          slots, outro), and for any B-roll interjection moments noted
│          within speech blocks (visual_note), select appropriate B-roll
│          clips from the catalog and assign them to specific timeline
│          positions
├── Intent: Fill the visual layer for every moment that isn't covered by
│          A-roll. B-roll provides visual variety, establishes scenes,
│          illustrates what's being said, and prevents the video from being
│          a continuous talking head. The selection must serve the creative
│          direction's mood and complement the adjacent speech content.
├── Preconditions:
│   ├── State: audio_spine with non-speech blocks identified, clip_catalog,
│   │   semantic_analysis_documents (for B-roll content descriptions),
│   │   a_roll_assignments (to know what's already covered), creative_direction
│   ├── Resources: Judgment capability (human or LLM) with ability to
│   │   review B-roll video content
│   └── Dependencies: [Step 3.1]
├── Input State:
│   - audio_spine (structure with block types and visual_notes)
│   - clip_catalog (B-roll clips with technical metadata)
│   - semantic_analysis_documents (B-roll content descriptions, moment
│     tags, quality assessments, visual/audio descriptions)
│   - a_roll_assignments (to avoid B-roll/A-roll overlap conflicts)
│   - creative_direction (mood, energy — guides B-roll selection)
├── Transformation: For each non-speech block and B-roll interjection →
│   review available B-roll clips → select the best match → assign with
│   in/out points
├── Output State: b_roll_assignments — video assignments for non-speech blocks:
│   [{
│     spine_block_position: integer,
│     block_type: string ("intro" | "transition_slot" | "outro"),
│     timeline_start: float,
│     timeline_end: float,
│     assigned_clip: {
│       clip_id: string,
│       source_file: absolute path,
│       video_in: float (start time within source file),
│       video_out: float (end time within source file),
│       duration_seconds: float,
│       needs_conform: boolean,
│       selection_rationale: string (why this clip fits this moment)
│     }
│   }, ...]
│
│   And B-roll interjections over speech blocks (overlays):
│   b_roll_interjections: [{
│     over_spine_block_position: integer (which speech block),
│     timeline_start: float,
│     timeline_end: float,
│     assigned_clip: { ... same fields as above ... },
│     purpose: string (e.g., "illustrate what's being said",
│       "break up long talking head", "show the location")
│   }, ...]
├── Verification:
│   ├── Every non-speech block has a B-roll assignment
│   ├── B-roll clip durations match or exceed the block durations they fill
│   ├── No B-roll clip is used more than once (avoid visual repetition)
│   │   unless the catalog has insufficient B-roll variety
│   ├── B-roll selections are visually appropriate for their context
│   │   (judged by the evaluator)
│   └── B-roll interjections are brief — typically 1-3 seconds but
│       varies by intent (a quick illustrative cut vs. a longer
│       establishing moment)
├── Failure Modes:
│   ├── Insufficient B-roll in catalog → Flag: some blocks may need
│   │   A-roll with visual treatment (zoom, crop) instead of true B-roll.
│   │   Proceed with available material but warn about limited variety.
│   └── No B-roll matches the mood of a block → Use the least-bad option
│       and note it for human review.
├── State Interaction:
│   ├── Reads: [audio_spine, clip_catalog, semantic_analysis_documents,
│   │          a_roll_assignments, creative_direction]
│   └── Writes: [b_roll_assignments]
├── Parameters:
│   ├── B-roll interjection duration: typically 1-3 seconds, varies by
│   │   intent (guideline, not hard constraint)
│   ├── Long speech block threshold: 5-8 seconds (speech blocks longer
│   │   than this should have at least one B-roll interjection)
│   └── Selection criteria:
│       - Visual relevance to the speech content or block purpose
│       - Mood/energy match with creative direction
│       - Visual quality and composition
│       - Variety (avoid repeating the same visual)
├── Idempotency: No — judgment may vary
├── Classification: Nondeterministic / Creative Selection
└── Notes:
    - B-roll interjections are placed OVER A-roll on a higher video track
      — the A-roll audio continues playing but the visual switches to
      B-roll briefly. This is a standard editing technique to maintain
      speech continuity while adding visual variety.
    - The style spec says talking head shots longer than a few seconds
      should use techniques like B-roll interjections, slow zoom, or
      cut-in/cut-out. B-roll interjections are handled here; zoom and
      cut-in effects are handled in Phase 4 (Enhancement).
    - B-roll selection should consider the semantic analysis documents'
      assessments — clips with higher quality scores, stronger moment
      tags, and more compelling content descriptions should be prioritized
      for prominent positions (intro, key transitions).
    - If a B-roll clip is longer than the block it's assigned to, use
      only a portion (specify video_in/video_out accordingly).
```

### Step 3.3: Build Shot List

```
STEP 3.3:
├── Action: Merge the audio spine, A-roll assignments, and B-roll assignments
│          into a single, ordered shot list — the complete edit decision list
│          (EDL) specifying every clip placement on the timeline
├── Intent: Produce the definitive assembly document. This is the data
│          structure that a deterministic executor (human or script) can
│          use to build the multi-track timeline without any further
│          creative decisions. Every clip, every track, every timestamp
│          is specified.
├── Preconditions:
│   ├── State: audio_spine, a_roll_assignments, b_roll_assignments
│   ├── Resources: None (data merging)
│   └── Dependencies: [Step 3.1, Step 3.2]
├── Input State: Spine + both assignment sets
├── Transformation: Merge all data into a unified timeline specification
│   with track assignments
├── Output State: shot_list — the complete edit decision list:
│   {
│     timeline_duration_seconds: float (from audio spine),
│     target_resolution: { width: 1080, height: 1920 },
│     target_frame_rate: float (from style spec — match source or 30fps),
│     tracks: {
│       V1: "A-roll video (primary video track)",
│       V2: "B-roll overlays and interjections",
│       V3: "Subtitles / text overlays (populated by Phase 4)",
│       V4: "Transition video elements (populated by Phase 4)",
│       A1: "A-roll speech audio",
│       A2: "Music",
│       A3: "SFX (populated by Phase 4)",
│       A4: "Transition audio (populated by Phase 4)"
│     },
│     entries: [
│       {
│         entry_id: string (e.g., "shot_001"),
│         timeline_start: float,
│         timeline_end: float,
│         duration_seconds: float,
│         track: string ("V1" | "V2" | "A1" | "A2"),
│         clip_id: string,
│         source_file: absolute path,
│         source_in: float (in-point within source file),
│         source_out: float (out-point within source file),
│         clip_type: string ("a_roll_video" | "b_roll_video" |
│           "a_roll_audio" | "music"),
│         needs_conform: boolean,
│         spine_block_position: integer (which spine block this serves),
│         notes: string (any special handling notes)
│       }, ...
│     ]
│   }
│
│   TRACK ASSIGNMENT RULES (base tracks — Phase 3):
│   - V1: A-roll video (primary video track)
│   - V2: B-roll overlays and interjections (covers V1 when present)
│   - A1: A-roll speech audio (primary audio)
│   - A2: Music (level controlled by music_behavior, applied in Phase 5)
│
│   Enhancement tracks (added by Phase 4):
│   - V3: Subtitles / text overlays
│   - V4: Transition video elements (light leaks, whip pan overlays, etc.)
│   - A3: SFX (sound effects)
│   - A4: Transition audio (whooshes, impacts paired with transitions)
│
│   Note: Transitions often have BOTH a video component (V4) and an audio
│   component (A4) — e.g., a whip pan overlay + its accompanying whoosh.
│   These tracks are defined here for structural clarity but POPULATED
│   by Phase 4's enhancement specification.
│
│   ENTRY GENERATION RULES:
│   - Each A-roll video segment → one V1 entry + one A1 entry (same
│     source file, same timestamps — video and audio stay linked)
│   - Each B-roll assignment → one V2 entry (video only — B-roll audio
│     is not used unless specifically noted)
│   - Each music splice → one A2 entry (from music_selections, placed
│     at the timeline positions defined by the spine)
│   - Hook → V1 + A1 entries at timeline position 0
├── Verification:
│   ├── No gaps on V1 — every second of the timeline has video coverage
│   │   (A-roll on V1, with B-roll on V2 filling non-speech blocks)
│   ├── A1 entries cover all speech blocks (no missing audio)
│   ├── A2 entries cover all blocks with music_behavior != "silent"
│   ├── Entry timestamps don't overlap within the same track
│   ├── All source_file references are valid paths
│   ├── timeline_duration matches the audio spine's total_duration
│   └── entry_id values are unique
├── Failure Modes:
│   ├── Gap found on V1 (timeline has uncovered video) → FAIL. Every
│   │   second needs video. May need additional B-roll assignments.
│   └── Overlapping entries on the same track → FAIL (data integrity
│       error in upstream assignments)
├── State Interaction:
│   ├── Reads: [audio_spine, a_roll_assignments, b_roll_assignments,
│   │          music_selections]
│   └── Writes: [shot_list]
├── Parameters: None
├── Idempotency: Yes — deterministic merge
├── Classification: Deterministic / Data Transformation
└── Notes:
    - The shot list is the HANDOFF document between creative decisions
      and mechanical execution. Everything before this point involves
      judgment. Everything after (within Phase 3) is mechanical placement.
    - Track V2 is an overlay — when V2 has content, it visually covers V1.
      When V2 is empty, V1 shows through. This means A-roll video plays
      continuously on V1, and B-roll "covers" it on V2 during interjections
      and non-speech blocks.
    - Music entries (A2) reference the music track source files and
      splice timestamps from music_selections. The volume/ducking is NOT
      applied here — that's Phase 5 (Refinement).
    - This shot list is tool-agnostic — it describes WHAT goes WHERE.
      The actual timeline construction (importing clips, setting in/out
      points, placing on tracks) is an encoding concern for the
      implementation layer.
```

### Step 3.4: Verify Rough Cut ⚡ NONDETERMINISTIC

```
STEP 3.4:
├── Action: Review the complete shot list and verify that it represents a
│          coherent, watchable rough cut — checking visual continuity, clip
│          coverage, pacing feel, and overall quality
├── Intent: Final quality gate before moving to enhancement. This is the
│          last chance to catch issues in the visual assembly before effects,
│          color grading, and other enhancements are layered on top. Changes
│          after this point are increasingly expensive.
├── Preconditions:
│   ├── State: shot_list exists
│   ├── Resources: Judgment capability, ability to visualize or review
│   │   the timeline assembly
│   └── Dependencies: [Step 3.3]
├── Input State: Complete shot list
├── Transformation: Review the shot list → assess quality → produce
│   verification result
├── Output State: rough_cut_verification:
│   {
│     status: "pass" | "fail",
│     total_shots: integer,
│     total_duration_seconds: float,
│     coverage_check: {
│       pass: boolean,
│       gaps: [{timeline_start, timeline_end, track}, ...] or []
│     },
│     visual_continuity_check: {
│       pass: boolean,
│       issues: [string, ...] (e.g., "jarring lighting change between
│         shots 3 and 4", "repeated B-roll clip used twice")
│     },
│     pacing_check: {
│       pass: boolean,
│       issues: [string, ...] (e.g., "too many short cuts in sequence",
│         "long talking head without B-roll relief")
│     },
│     overall_assessment: string,
│     recommended_action: string or null
│   }
├── Verification:
│   ├── All check fields are populated
│   └── status is "pass" only if all sub-checks pass
├── Failure Modes:
│   ├── Coverage gaps → Return to Step 3.2 (assign more B-roll)
│   ├── Visual continuity issues → Return to Step 3.2 (swap B-roll
│   │   selections) or Step 2.5 (adjust spine structure if the issue
│   │   is with A-roll sequencing)
│   ├── Pacing issues → Return to Step 3.2 (add/remove B-roll
│   │   interjections) or Step 2.5 (adjust transition slot durations)
│   └── Max 3 revision loops before escalating to human review
├── State Interaction:
│   ├── Reads: [shot_list]
│   └── Writes: [rough_cut_verification]
├── Parameters:
│   ├── Max talking head duration without B-roll: 5-8 seconds (guideline)
│   └── B-roll interjection duration: typically 1-3 seconds, varies by
│       intent (guideline, not hard constraint)
├── Idempotency: No — judgment may vary
├── Classification: Nondeterministic / Evaluation & Judgment
└── Notes:
    - When this step passes, the rough cut is LOCKED. Phase 4
      (Enhancement) builds on top of it without changing clip
      placement — per tacit knowledge item 9 (enhancements never
      cause structural backtracking).
    - The visual continuity check looks for jarring transitions between
      adjacent clips — lighting changes, location jumps, framing
      inconsistencies. Some of these are intentional (transitions will
      smooth them in Phase 4), but egregious ones should be caught here.
    - This verification can be done by reviewing the shot list data
      alone (checking timestamps, durations, clip reuse) or by actually
      building and watching the rough cut. The latter is more thorough
      but requires the timeline to be assembled first — which is an
      encoding concern.
```

---

**Phase 3 is complete.** Steps 3.1–3.4 cover the full visual assembly:
assign A-roll video → select and assign B-roll → build shot list → verify.

**Exit state:** A verified shot list (EDL) — the complete blueprint for the
rough cut. Every clip, every track, every timestamp is specified. A deterministic
executor can build the timeline from this data.

**Key characteristics of this phase:**
- Mostly deterministic — only B-roll selection (3.2) and verification (3.4)
  require judgment
- The shot list is the critical handoff document between creative decisions
  and mechanical execution
- The multi-track structure (V1/V2/A1/A2) enables B-roll overlays without
  disrupting A-roll audio continuity

---

## Phase 4: Enhancement

**Goal:** Layer all non-structural effects on top of the locked rough cut:
subtitles, transitions, visual effects (zoom, shake), and sound effects.
These enhancements add polish, energy, and production value WITHOUT changing
clip placement or the audio spine. The rough cut structure is immutable at
this point.

**Entry State:** Verified shot list (locked rough cut) + audio spine + creative direction.

**Exit State:** A complete enhancement specification listing every effect,
transition, subtitle, and SFX with exact timeline positions and parameters.
Combined with the shot list from Phase 3, this is the full edit — ready for
refinement (color/audio mix) and export.

**Critical constraint:** Enhancements NEVER cause structural backtracking.
If an enhancement doesn't work, the enhancement itself is changed — not the
clip placement.

### Phase 4 Step Dependency Overview

```
4.1 Generate  4.2 Plan         4.3 Plan visual  4.4 Plan
    subtitles     transitions      effects          SFX
                                                  (ALL PARALLEL)
 │               │                │               │
 └───────────────┴────────────────┴───────────────┘
                          ▼
              4.5 Build enhancement specification    DETERMINISTIC
                          │
                          ▼
              4.6 Verify enhanced cut               ⚡ NONDETERMINISTIC
```

- Steps 4.1, 4.2, 4.3, and 4.4 can ALL execute in PARALLEL — they each
  read from the shot list/spine and write to independent outputs
- SFX is NOT dependent on transitions — SFX serves its own purposes
  (defining movement, building tension, carrying emotion) in addition to
  sometimes pairing with transitions. The pairing is noted in both steps
  but doesn't create a dependency.
- Step 4.5 merges all enhancement data into one specification
- Step 4.6 validates the complete enhanced edit

---

### Step 4.1: Generate Subtitles

```
STEP 4.1:
├── Action: Convert the speech segments from the audio spine into styled,
│          timed subtitle entries — breaking speech into display groups
│          and assigning exact on/off times
├── Intent: Create the text overlay layer for the video. Subtitles are
│          essential for shortform content (most viewers watch with sound
│          off initially). The subtitles must be timed precisely to the
│          speech, styled per the style specification (all lowercase, bold,
│          sans-serif, animated pop), and grouped into readable chunks.
├── Preconditions:
│   ├── State: audio_spine with speech blocks containing text and timestamps
│   ├── Resources: Text processing capability
│   └── Dependencies: [Phase 3 complete — Step 3.4, but only reads audio_spine]
├── Input State:
│   - audio_spine (speech blocks with segment text and timestamps)
│   - style_specification (subtitle styling rules)
├── Transformation: For each speech segment → break text into display
│   groups (3-6 words per group) → lowercase → assign timing → produce
│   subtitle entries
├── Output State: subtitle_entries — timed subtitle data:
│   [{
│     entry_id: string (e.g., "sub_001"),
│     timeline_start: float,
│     timeline_end: float,
│     text: string (the display text — already lowercase),
│     emphasis_words: [string, ...] (keywords to receive scale bump),
│     spine_block_position: integer,
│     word_count: integer
│   }, ...]
│
│   GENERATION RULES:
│   - All text is lowercase (per style spec)
│   - Display groups: 3-6 words per group (readable at a glance on mobile)
│   - Maximum 2 lines per subtitle entry
│   - Timing: each group appears when its first word is spoken and
│     disappears when the next group appears (no gaps, no overlaps)
│   - Never split a sentence across groups in a way that breaks meaning
│   - Target track: V3 (subtitles/text overlay track)
├── Verification:
│   ├── Every speech block has corresponding subtitle entries
│   ├── Subtitle timing matches speech segment timing (no subtitle
│   │   appears before or after its speech)
│   ├── No subtitle exceeds 8 words or 2 lines
│   ├── All text is lowercase
│   ├── Emphasis words exist in the subtitle text
│   └── No overlapping subtitle entries
├── Failure Modes:
│   └── Speech text is inaudible or unintelligible in a segment →
│       Use the transcript text as-is (it was transcribed in Phase 1).
│       If transcript is wrong, flag for human correction.
├── State Interaction:
│   ├── Reads: [audio_spine, style_specification]
│   └── Writes: [subtitle_entries]
├── Parameters:
│   ├── Style from spec:
│   │   - Font: Poppins Bold (first preference)
│   │   - Color: White (#FFFFFF) with soft drop shadow
│   │   - Position: Lower third, centered
│   │   - Size: ~5-6% of frame height
│   │   - Animation: Pop from 95%→100% scale over 150ms (entrance),
│   │     cut or 100ms fade (exit)
│   └── Grouping: 3-6 words per display group
├── Idempotency: Yes — deterministic text processing
├── Classification: Deterministic / Data Transformation
└── Notes:
    - This step can run in PARALLEL with Steps 4.2, 4.3, and 4.4.
    - This is fully programmatic — the text is already known from the
      transcript, timestamps come from the speech segments, word grouping
      follows a fixed algorithm (split on word count / sentence boundaries).
    - The subtitle animation parameters (scale, duration, easing) are
      specified here but APPLIED during encoding. This step produces the
      data; the encoder applies the styling.
    - The hook block may also need subtitles if it contains speech.
```

### Step 4.2: Plan Transitions ⚡ NONDETERMINISTIC

```
STEP 4.2:
├── Action: For every cut point in the shot list (where one clip ends and
│          another begins), decide which transition type to use and specify
│          its parameters
├── Intent: Smooth the visual flow between clips. Transitions control how
│          one shot connects to the next — they manage the viewer's
│          attention, mask jarring changes, and add energy/polish. The
│          default is a hard cut; creative transitions are reserved for
│          moments that benefit from them.
├── Preconditions:
│   ├── State: shot_list with all clip placements, audio_spine with
│   │   music_behavior and BPM data
│   ├── Resources: Judgment capability, VFX asset availability (light
│   │   leaks, etc.)
│   └── Dependencies: [Phase 3 complete — Step 3.4]
├── Input State:
│   - shot_list (clip placements with timeline positions)
│   - audio_spine (music behavior, block types — informs transition energy)
│   - music_selections (BPM — for beatmatching cut points)
│   - creative_direction (mood — informs transition style)
│   - style_specification (transition rules and toolkit)
├── Transformation: For each cut point → assess context (energy, mood,
│   content change) → select transition type → compute beat-aligned timing
│   → produce transition entry
├── Output State: transition_plan — transition for every cut point:
│   [{
│     transition_id: string (e.g., "trans_001"),
│     cut_point_timeline: float (where the cut occurs),
│     from_entry_id: string (shot list entry ending),
│     to_entry_id: string (shot list entry beginning),
│     transition_type: string ("hard_cut" | "jump_cut" | "whip_pan" |
│       "zoom_transition" | "match_cut" | "j_cut" | "l_cut" |
│       "cross_dissolve" | "light_leak"),
│     duration_frames: integer (0 for hard/jump cuts; 8-15 for dissolves;
│       varies for others),
│     parameters: { ... transition-type-specific ... },
│     beat_aligned: boolean (is this cut on a musical beat?),
│     rationale: string (why this transition type was chosen)
│   }, ...]
│
│   TRANSITION PARAMETERS BY TYPE:
│   - hard_cut: {} (no parameters — instant cut)
│   - jump_cut: {} (no parameters — subset of hard cut, same subject)
│   - whip_pan: { direction: "left"|"right"|"up"|"down", speed: "fast"|"medium" }
│   - zoom_transition: { direction: "in"|"out", speed: "fast"|"medium" }
│   - match_cut: { match_element: string (what's being matched — shape,
│     motion, composition) }
│   - j_cut: { audio_overlap_seconds: float (how much audio precedes video) }
│   - l_cut: { audio_overlap_seconds: float (how much audio extends) }
│   - cross_dissolve: { duration_frames: integer (8-15) }
│   - light_leak: { asset_id: string (which VFX asset), opacity: float }
├── Verification:
│   ├── Every cut point in the shot list has a transition entry
│   ├── No two consecutive creative transitions are the same type
│   │   (per style spec: variety)
│   ├── Hard cuts dominate (creative transitions should be the minority)
│   ├── Beat alignment is attempted for major transitions (checked
│   │   against BPM data from music_selections)
│   └── J-cut/L-cut audio overlaps don't exceed 1 second
├── Failure Modes:
│   ├── BPM data unavailable → Beat alignment is skipped. Transitions
│   │   are placed at the existing cut points without musical sync.
│   └── VFX asset not available for light leak → Fall back to cross
│       dissolve or hard cut.
├── State Interaction:
│   ├── Reads: [shot_list, audio_spine, music_selections, creative_direction,
│   │          style_specification]
│   └── Writes: [transition_plan]
├── Parameters:
│   ├── Transition rules from style spec:
│   │   - Default to hard cuts
│   │   - Cut on movement or beats
│   │   - Match energy of surrounding content
│   │   - Variety — don't repeat creative transitions consecutively
│   └── Dissolve duration: 8-15 frames
├── Idempotency: No — transition selection involves judgment
├── Classification: Nondeterministic / Creative Selection
└── Notes:
    - This step can run in PARALLEL with Steps 4.1, 4.3, and 4.4.
    - Step 4.4 (SFX) MAY pair SFX with transitions — this is noted in
      both steps but doesn't create a dependency. SFX and transitions
      are planned independently and reconciled in Step 4.5.
    - Beat alignment: if the music is at 120 BPM, beats fall every 0.5s.
      A cut at 3.7s could be shifted to 3.5s or 4.0s to land on a beat.
      This is an enhancement, not a structural change — it shifts the
      cut by a few frames, not the clip placement.
    - J-cuts and L-cuts are the only transitions that affect AUDIO timing
      (overlapping audio from adjacent clips). These are subtle and
      create a more natural, flowing feel.
```

### Step 4.3: Plan Visual Effects ⚡ NONDETERMINISTIC

```
STEP 4.3:
├── Action: For each clip in the shot list, decide which visual effects
│          (if any) to apply — slow zoom on talking heads, screen shake
│          for emphasis, zoom emphasis on key visual moments, and
│          cut-in/cut-out framing variations
├── Intent: Keep the frame alive. Static holds kill engagement in shortform
│          content (per style spec: "the frame should always be moving").
│          These subtle effects add dynamism without changing the content
│          or clip placement. They're felt more than seen.
├── Preconditions:
│   ├── State: shot_list with clip types and durations
│   ├── Resources: Judgment capability
│   └── Dependencies: [Phase 3 complete — Step 3.4]
├── Input State:
│   - shot_list (which clips are talking head, which are B-roll, durations)
│   - subtitle_entries (useful for knowing where key words land — but
│     this is optional, VFX can be planned without subtitles)
│   - style_specification (visual effect rules)
├── Transformation: For each clip → assess if it needs visual effects →
│   select effect type and parameters → produce VFX entry
├── Output State: vfx_plan — visual effects for applicable clips:
│   [{
│     vfx_id: string (e.g., "vfx_001"),
│     target_entry_id: string (which shot list entry this applies to),
│     timeline_start: float,
│     timeline_end: float,
│     effect_type: string ("slow_zoom" | "screen_shake" | "zoom_emphasis"
│       | "cut_in"),
│     parameters: { ... effect-type-specific ... }
│   }, ...]
│
│   EFFECT PARAMETERS BY TYPE:
│   - slow_zoom: { direction: "in"|"out", zoom_percent: float (5-10%),
│     applied to entire clip duration }
│   - screen_shake: { intensity_px: integer (2-3), duration_frames: integer
│     (3-4), trigger_reason: string }
│   - zoom_emphasis: { zoom_percent: float (5%), trigger_time: float
│     (timeline position), duration_ms: integer (150-300) }
│   - cut_in: { scale_factor: float (1.2-1.4), applied at: float }
├── Verification:
│   ├── Every A-roll talking head clip longer than 3 seconds has at least
│   │   a slow_zoom effect (per style spec: the frame should always move)
│   ├── Screen shake is used sparingly (max 2-3 per video)
│   ├── Zoom emphasis is used only for genuinely important moments
│   ├── Effect parameters are within the ranges from the style spec
│   └── No effect changes the clip's in/out points or timeline position
├── Failure Modes:
│   └── No applicable clips for effects → Valid. Short B-roll clips or
│       already-dynamic footage may not need additional effects. Proceed
│       with an empty vfx_plan if appropriate.
├── State Interaction:
│   ├── Reads: [shot_list, style_specification]
│   └── Writes: [vfx_plan]
├── Parameters:
│   ├── Slow zoom: 5-10% over clip duration
│   ├── Screen shake: 2-3px intensity, 3-4 frames duration
│   ├── Animation timing: 100-200ms for micro-animations, never > 500ms
│   └── Easing: Bezier curves, not linear
├── Idempotency: No — effect selection involves judgment
├── Classification: Nondeterministic / Creative Selection
└── Notes:
    - This step can run in PARALLEL with Steps 4.1, 4.2, and 4.4.
    - Slow zoom is almost always applied to talking head clips — it's the
      most common and most subtle effect. It makes static shots feel alive
      without any conscious awareness from the viewer.
    - Cut-in/cut-out alternates between wider and tighter framings of the
      same shot. This is achieved by scaling/cropping the same source clip
      — no additional footage is needed. It simulates a multi-camera look
      from a single camera.
    - These effects are APPLIED to existing clips, not inserted between
      them. They modify how a clip is displayed, not when or where it sits
      on the timeline.
```

### Step 4.4: Plan Sound Effects ⚡ NONDETERMINISTIC

```
STEP 4.4:
├── Action: Select and place sound effects at appropriate moments in the
│          timeline — pairing SFX with transitions, emphasis moments, text
│          appearances, and energy shifts
├── Intent: Add texture, weight, and polish to the edit. SFX are the
│          "bass guitar" — felt more than heard. They complement transitions,
│          emphasize moments, and create a professional sound design layer.
│          Less is more: not every cut needs a sound effect.
├── Preconditions:
│   ├── State: shot_list, transition_plan, subtitle_entries (optional),
│   │   vfx_plan (optional)
│   ├── Resources: SFX library, judgment capability
│   └── Dependencies: [Phase 3 complete — Step 3.4]
├── Input State:
│   - shot_list (timeline positions, clip types)
│   - audio_spine (music behavior — SFX should complement, not fight music;
│     block types — intro/transition slots are natural SFX positions)
│   - subtitle_entries (text appearance times — click/tick SFX can pair
│     with subtitle pop-on)
│   - vfx_plan (screen shake moments — bass hit SFX can pair with shake)
│   - style_specification (SFX rules and toolkit)
├── Transformation: For each transition and emphasis moment → decide if SFX
│   is warranted → select SFX type → assign timeline position and volume →
│   produce SFX entry
├── Output State: sfx_plan — sound effect placements:
│   [{
│     sfx_id: string (e.g., "sfx_001"),
│     sfx_type: string ("whoosh" | "bass_impact" | "riser" | "foley" |
│       "click" | "reverse_cymbal"),
│     timeline_start: float,
│     timeline_end: float,
│     duration_seconds: float,
│     volume_level: string ("subtle" | "low" | "medium"),
│     paired_with: string or null (transition_id, vfx_id, or subtitle
│       entry_id that this SFX accompanies),
│     source_asset: string (path to the SFX audio file or asset ID),
│     rationale: string,
│     target_track: "A3" (SFX audio track)
│   }, ...]
│
│   SFX PLACEMENT GUIDELINES (from style spec):
│   - Whoosh/swish: On cuts, transitions, camera movements
│   - Bass/sub impact: On reveals, title drops, emphasis moments
│   - Riser/tension build: Before a reveal or punchline — can span
│     MULTIPLE clips to build anticipation over several seconds
│   - Foley/ambient: Establishing scenes, adding texture
│   - Click/tick: On subtitle appearances, small visual elements
│   - Reverse cymbal/swell: Between major sections
│
│   KEY: SFX is NOT just about transitions. Sound effects define movement,
│   carry emotion, and create tension. A riser can span 5-10 seconds across
│   multiple clips building toward a peak moment. A bass impact can
│   emphasize a key statement independent of any transition.
├── Verification:
│   ├── SFX are not overused (style spec: "less is more")
│   ├── Every creative transition (non-hard-cut) has at most one SFX
│   ├── SFX timing aligns with the events they accompany
│   ├── Volume levels are appropriate ("subtle" or "low" for most;
│   │   "medium" only for emphasis moments)
│   ├── SFX don't fight the music (check against music_behavior —
│   │   avoid loud SFX during prominent music moments)
│   └── No two SFX overlap at the same timeline position
├── Failure Modes:
│   ├── SFX asset not found → Skip the SFX and flag for human to
│   │   source the asset. Proceed without it.
│   └── Too many SFX placed → Reduce. A 30-60 second video should
│       have at most 5-10 SFX total. More than that is cluttered.
├── State Interaction:
│   ├── Reads: [transition_plan, shot_list, audio_spine, subtitle_entries,
│   │          vfx_plan, style_specification]
│   └── Writes: [sfx_plan]
├── Parameters:
│   ├── Max SFX count for 30-60s video: 5-10
│   ├── SFX rules from style spec:
│   │   - Less is more
│   │   - Layer with purpose (whoosh + bass hit for important transitions)
│   │   - Match the music rhythm and energy
│   │   - Pan and space — SFX should feel spatial, not pasted on
│   └── Volume: Never louder than the speech or music
├── Idempotency: No — SFX selection involves judgment
├── Classification: Nondeterministic / Creative Selection
└── Notes:
    - This step can run in PARALLEL with Steps 4.1, 4.2, and 4.3.
    - SFX serves MULTIPLE purposes — not just pairing with transitions:
      - Movement definition (whooshes to guide attention)
      - Emotional weight (bass impacts for emphasis)
      - Tension building (risers spanning multiple clips)
      - Texture (foley/ambient establishing mood)
      - Punctuation (clicks on text appearances)
    - When SFX and transitions DO coincide (e.g., whoosh on a whip pan),
      both the SFX (A3) and transition audio (A4) may play. The distinction:
      transition audio is inherent to the transition itself; SFX is an
      independent editorial choice layered on top.
    - The "layering with purpose" rule from the style spec: an important
      transition might get whoosh + subtle bass hit (two SFX). A routine
      cut gets nothing or just a subtle whoosh. Don't stack SFX without
      reason.
    - SFX go on A3 (SFX audio track), separate from transition audio
      (A4) and music (A2). This separation allows independent volume
      control in Phase 5 (Refinement).
```

### Step 4.5: Build Enhancement Specification

```
STEP 4.5:
├── Action: Merge subtitle entries, transition plan, VFX plan, and SFX plan
│          into a single, unified enhancement specification
├── Intent: Produce one document containing ALL enhancement data, organized
│          by timeline position. Combined with the shot list from Phase 3,
│          this gives a deterministic executor everything needed to build
│          the fully enhanced timeline.
├── Preconditions:
│   ├── State: subtitle_entries, transition_plan, vfx_plan, sfx_plan
│   ├── Resources: None (data merging)
│   └── Dependencies: [Steps 4.1, 4.2, 4.3, 4.4]
├── Input State: Four separate enhancement data structures
├── Transformation: Merge all enhancement data → sort by timeline position
│   → produce unified spec
├── Output State: enhancement_spec:
│   {
│     subtitle_count: integer,
│     transition_count: integer,
│     vfx_count: integer,
│     sfx_count: integer,
│     subtitles: [... subtitle_entries ...],
│     transitions: [... transition_plan ...],
│     visual_effects: [... vfx_plan ...],
│     sound_effects: [... sfx_plan ...],
│     timeline_summary: [{
│       timeline_position: float,
│       events: [string, ...] (e.g., ["trans_001: whip_pan",
│         "sfx_001: whoosh", "sub_003: starts"])
│     }, ...] (all events sorted chronologically for overview)
│   }
├── Verification:
│   ├── All four input data structures are present
│   ├── No enhancement conflicts (e.g., two transitions at the same cut
│   │   point, overlapping subtitles)
│   ├── timeline_summary is sorted chronologically
│   └── Counts match the actual array lengths
├── Failure Modes:
│   └── Enhancement data conflict → FAIL with details of the conflict.
│       Resolve by revisiting the conflicting step.
├── State Interaction:
│   ├── Reads: [subtitle_entries, transition_plan, vfx_plan, sfx_plan]
│   └── Writes: [enhancement_spec]
├── Parameters: None
├── Idempotency: Yes — deterministic merge
├── Classification: Deterministic / Data Transformation
└── Notes:
    - The timeline_summary provides a chronological view of ALL events
      at each position — useful for spotting overloaded moments (too
      many things happening at once).
    - This spec, combined with the shot_list from Phase 3, is the
      COMPLETE edit specification. Everything a deterministic executor
      needs to build the final enhanced timeline.
```

### Step 4.6: Verify Enhanced Cut ⚡ NONDETERMINISTIC

```
STEP 4.6:
├── Action: Review the complete enhancement specification in context with
│          the shot list and verify that enhancements add value without
│          being excessive, distracting, or conflicting
├── Intent: Final quality gate for the enhancement layer. Ensure the
│          enhancements follow the "bass guitar principle" — felt more
│          than seen/heard, purposeful, and polished.
├── Preconditions:
│   ├── State: enhancement_spec, shot_list
│   ├── Resources: Judgment capability
│   └── Dependencies: [Step 4.5]
├── Input State: Enhancement specification + shot list
├── Transformation: Review spec → assess quality → produce verification
├── Output State: enhancement_verification:
│   {
│     status: "pass" | "fail",
│     subtitle_check: { pass: boolean, issues: [string, ...] },
│     transition_check: { pass: boolean, issues: [string, ...] },
│     vfx_check: { pass: boolean, issues: [string, ...] },
│     sfx_check: { pass: boolean, issues: [string, ...] },
│     density_check: {
│       pass: boolean,
│       overloaded_moments: [{timeline_position, event_count}, ...]
│     },
│     overall_notes: string,
│     recommended_action: string or null
│   }
├── Verification:
│   ├── All check fields are populated
│   └── status is "pass" only if all sub-checks pass
├── Failure Modes:
│   ├── Too many SFX → Return to Step 4.4 to reduce
│   ├── Subtitle timing off → Return to Step 4.1 to adjust
│   ├── Transitions feel wrong → Return to Step 4.2 to adjust
│   ├── VFX overused → Return to Step 4.3 to reduce
│   ├── Overloaded moments (too many events at one position) → Simplify
│   │   by removing the least essential enhancement at that position
│   └── Max 2 revision loops before escalating to human review
├── State Interaction:
│   ├── Reads: [enhancement_spec, shot_list]
│   └── Writes: [enhancement_verification]
├── Parameters:
│   ├── Max events at a single timeline position: 3 (e.g., transition +
│   │   SFX + subtitle start is OK; adding a VFX on top is cluttered)
│   ├── SFX density: max 5-10 per 30-60s video
│   └── "Bass guitar" test: would removing this enhancement be noticeable?
│       If no → it's good (subtle). If a non-expert viewer actively
│       notices an enhancement → it might be too much.
├── Idempotency: No — judgment may vary
├── Classification: Nondeterministic / Evaluation & Judgment
└── Notes:
    - When this step passes, the enhancement layer is LOCKED. Phase 5
      (Refinement) handles color grading and audio mixing — it doesn't
      add or remove enhancements.
    - The density_check is particularly important: shortform videos can
      become over-produced if every cut has a whoosh, every word has
      emphasis, and every shot has zoom + shake. Restraint is key.
    - The "bass guitar" test from the style spec is the ultimate
      quality criterion: enhancements should be invisible in their
      presence but obvious in their absence.
```

---

**Phase 4 is complete.** Steps 4.1–4.6 cover the full enhancement pipeline:
generate subtitles ∥ plan transitions ∥ plan visual effects ∥ plan SFX →
build enhancement spec → verify.

**Exit state:** A verified enhancement specification + the shot list from Phase 3.
Together, these two documents describe the complete edit — all clip placements
AND all effects. Ready for Phase 5 (Refinement: color grading + audio mixing).

**Key characteristics of this phase:**
- Four parallel enhancement tracks (subtitles, transitions, VFX, SFX)
- Subtitles are the only deterministic step — the rest are creative judgment
- All enhancements are ADDITIVE — they layer on top without changing structure
- Guided by the "bass guitar principle" — subtle, purposeful, felt-not-seen

---

## Phase 5: Refinement

**Goal:** Apply the two final production polish layers: color grading (visual)
and audio mixing (audio). These operate on the complete, enhanced timeline —
they don't add or remove clips, effects, or enhancements. They adjust HOW
existing content looks and sounds.

**Entry State:** Shot list + enhancement specification (the complete edit blueprint).

**Exit State:** A color grading specification and an audio mix specification,
completing the full production data. Combined with the shot list and enhancement
spec, this is everything needed for the final render.

### Phase 5 Step Dependency Overview

```
5.1 Define color grading   5.2 Define audio mix
    specification              specification
    (PARALLEL)                 (PARALLEL)
 │                          │
 └────────────┬─────────────┘
              ▼
5.3 Verify refinement               ⚡ NONDETERMINISTIC
```

- Steps 5.1 and 5.2 are PARALLEL — color and audio are independent domains
- Step 5.3 validates both together

---

### Step 5.1: Define Color Grading Specification

```
STEP 5.1:
├── Action: Define the color grading parameters for the video — specifying
│          the node tree, per-clip adjustments, and overall look per the
│          style specification
├── Intent: Transform the raw camera footage into the warm, vibrant,
│          cinematic look defined in the style spec. Color grading is
│          applied uniformly across the video with per-clip exposure
│          adjustments as needed.
├── Preconditions:
│   ├── State: shot_list with source file references
│   ├── Resources: Color science knowledge (or a predefined LUT/grade)
│   └── Dependencies: [Phase 4 complete — Step 4.6]
├── Input State:
│   - shot_list (which clips are used, their source files)
│   - style_specification (color grading parameters: node tree, values)
├── Transformation: Define the grading pipeline (node tree) + identify
│   per-clip adjustments → produce grading specification
├── Output State: color_grade_spec:
│   {
│     grade_pipeline: {
│       node_1: { type: "color_space_transform", from: "camera",
│         to: "davinci_wide_gamut_intermediate" },
│       node_2: { type: "primary_correction", white_balance_offset: 200,
│         contrast_curve: "gentle_s", lift_shadows: 0.02,
│         roll_highlights: -0.03, saturation: "+10-15%" },
│       node_3: { type: "warm_tone_shaping", offset_red: 0.01,
│         offset_green: 0.005, highlights: "warm_golden",
│         shadows: "cool_teal_hint", skin_tone_protection: true },
│       node_4: { type: "creative_film_look", glow_opacity: "10-15%",
│         grain_amount: "0.2-0.3", vignette_amount: "0.15-0.20",
│         halation: "optional_subtle" },
│       node_5: { type: "color_space_transform", to: "rec709_gamma24" }
│     },
│     per_clip_adjustments: [{
│       entry_id: string (from shot list),
│       exposure_offset: float (stops — e.g., +0.5, -0.3),
│       white_balance_override: integer or null (kelvin),
│       notes: string (e.g., "clip is underexposed, lift by +0.7")
│     }, ...],
│     consistency_notes: string (any observations about matching clips
│       shot in different lighting conditions)
│   }
├── Verification:
│   ├── Grade pipeline matches the style spec's node tree structure
│   ├── Per-clip adjustments are specified for clips that need them
│   │   (different exposure, different white balance)
│   └── Output color space is Rec.709 Gamma 2.4 (per style spec)
├── Failure Modes:
│   ├── Clips shot in vastly different lighting → Per-clip adjustments
│   │   may not fully match. Flag for human colorist review.
│   └── Camera color science unknown → Use a generic camera-to-DWG
│       transform. Results may be approximate.
├── State Interaction:
│   ├── Reads: [shot_list, style_specification]
│   └── Writes: [color_grade_spec]
├── Parameters: All from style spec Section 1 (Color Grading)
├── Idempotency: Yes — deterministic parameter specification
├── Classification: Deterministic / Specification (parameters are fixed
│   by the style spec; per-clip adjustments are based on measured exposure)
└── Notes:
    - This step can run in PARALLEL with Step 5.2 (Audio Mix).
    - The grading pipeline is UNIFORM — the same node tree applies to
      every clip. Only per-clip exposure/WB adjustments vary.
    - This step SPECIFIES the grade; it doesn't APPLY it. Application
      is an encoding concern (DaVinci Resolve nodes, LUT application, etc.).
    - If a pre-built LUT or PowerGrade exists that matches the style spec,
      the specification can simply reference it rather than defining every
      parameter from scratch.
```

### Step 5.2: Define Audio Mix Specification

```
STEP 5.2:
├── Action: Define the audio mix parameters — volume levels, ducking rules,
│          and spatial positioning for each audio track (speech, music,
│          SFX, transition audio)
├── Intent: Balance all audio layers so they work together. Speech must be
│          clearly audible above everything else. Music must duck under
│          speech and swell during non-speech moments. SFX must be subtle.
│          The mix ensures nothing fights for attention.
├── Preconditions:
│   ├── State: audio_spine (music_behavior per block), enhancement_spec
│   │   (SFX placements)
│   ├── Resources: Audio mixing knowledge
│   └── Dependencies: [Phase 4 complete — Step 4.6]
├── Input State:
│   - audio_spine (music_behavior per block — "prominent", "background",
│     "fade_in", "fade_out", "silent")
│   - enhancement_spec (SFX placements with volume levels)
│   - style_specification (music rules, SFX rules)
├── Transformation: Translate music_behavior values and SFX volume levels
│   into concrete mix parameters → produce mix specification
├── Output State: audio_mix_spec:
│   {
│     track_levels: {
│       A1_speech: { base_level_db: 0 (reference), compressor: boolean,
│         notes: "Speech is the reference — everything else is relative" },
│       A2_music: {
│         prominent_level_db: float (e.g., -6),
│         background_level_db: float (e.g., -18),
│         fade_duration_seconds: float (e.g., 1.0),
│         ducking_trigger: "A1 activity" (when speech is present, duck)
│       },
│       A3_sfx: { base_level_db: float (e.g., -12),
│         notes: "Subtle — felt more than heard" },
│       A4_transition_audio: { base_level_db: float (e.g., -10),
│         notes: "Brief, paired with transition visuals" }
│     },
│     music_automation: [{
│       spine_block_position: integer,
│       timeline_start: float,
│       timeline_end: float,
│       music_behavior: string (from spine),
│       target_level_db: float (translated from behavior)
│     }, ...],
│     master_limiter: { threshold_db: float, enabled: boolean },
│     notes: string
│   }
├── Verification:
│   ├── Every spine block's music_behavior is translated to a dB level
│   ├── Speech (A1) is the loudest element at all times when present
│   ├── Music levels change smoothly (no hard jumps between blocks)
│   ├── SFX levels don't exceed speech levels
│   └── Fade durations are specified for all music_behavior transitions
├── Failure Modes:
│   └── Audio levels clip or distort → Adjust levels down. Use a limiter
│       on the master output.
├── State Interaction:
│   ├── Reads: [audio_spine, enhancement_spec, style_specification]
│   └── Writes: [audio_mix_spec]
├── Parameters:
│   ├── Speech reference level: 0 dB (everything else relative)
│   ├── Music prominent: -6 to -8 dB
│   ├── Music background (under speech): -16 to -20 dB
│   ├── Music fade duration: 0.5-1.5 seconds
│   ├── SFX: -10 to -15 dB
│   └── Style spec rule: music volume is background level, duck under speech
├── Idempotency: Yes — deterministic translation of behavior → levels
├── Classification: Deterministic / Specification
└── Notes:
    - This step can run in PARALLEL with Step 5.1 (Color Grading).
    - The music_automation array is the critical output — it tells the
      encoder exactly what volume level the music should be at every
      point in the timeline, derived from the spine's music_behavior.
    - Ducking: when speech is active (A1 has content), music (A2)
      automatically drops to background_level_db. When speech stops,
      music rises to prominent_level_db. The fade_duration controls
      how fast this transition happens.
    - This step SPECIFIES the mix; it doesn't APPLY it. Application
      is an encoding concern (automation curves, gain keyframes, etc.).
```

### Step 5.3: Verify Refinement ⚡ NONDETERMINISTIC

```
STEP 5.3:
├── Action: Review the color grading specification and audio mix
│          specification to verify they are complete and consistent
│          with the style specification
├── Intent: Final quality gate for the production polish layer. Ensure
│          the color and audio specs are complete before the video is
│          rendered.
├── Preconditions:
│   ├── State: color_grade_spec and audio_mix_spec exist
│   ├── Resources: Judgment capability
│   └── Dependencies: [Steps 5.1, 5.2]
├── Input State: Both refinement specifications
├── Transformation: Review specs → verify completeness and consistency
├── Output State: refinement_verification:
│   {
│     status: "pass" | "fail",
│     color_check: { pass: boolean, issues: [string, ...] },
│     audio_check: { pass: boolean, issues: [string, ...] },
│     overall_notes: string,
│     recommended_action: string or null
│   }
├── Verification:
│   ├── All check fields are populated
│   └── status is "pass" only if both checks pass
├── Failure Modes:
│   ├── Color spec incomplete → Return to Step 5.1
│   ├── Audio mix spec incomplete → Return to Step 5.2
│   └── Specs contradict style specification → Correct the spec
├── State Interaction:
│   ├── Reads: [color_grade_spec, audio_mix_spec, style_specification]
│   └── Writes: [refinement_verification]
├── Parameters: None
├── Idempotency: No — judgment may vary
├── Classification: Nondeterministic / Evaluation & Judgment
└── Notes:
    - When this passes, the COMPLETE production specification is locked:
      shot_list + enhancement_spec + color_grade_spec + audio_mix_spec.
      These four documents together describe the entire video.
    - Phase 6 (Export) uses all four to produce the final rendered file.
```

---

**Phase 5 is complete.** Steps 5.1–5.3 cover refinement:
define color grading ∥ define audio mix → verify.

**Exit state:** Complete production specification — shot list + enhancement spec +
color grade spec + audio mix spec. Everything needed to render the final video.

---

## Phase 6: Export & Validation

**Goal:** Render the final video file and validate it meets quality standards.
This is the mechanical execution of all specifications produced in Phases 1-5,
followed by a final human/AI review of the rendered output.

**Entry State:** Complete production specification (shot list + enhancement spec +
color grade spec + audio mix spec) + style specification (export settings).

**Exit State:** A rendered, validated video file ready for distribution.

### Phase 6 Step Dependency Overview

```
6.1 Render final video           DETERMINISTIC
 │
 ▼
6.2 Validate output              ⚡ NONDETERMINISTIC
```

---

### Step 6.1: Render Final Video

```
STEP 6.1:
├── Action: Execute the complete production specification — build the
│          timeline, apply all enhancements, apply color grading, apply
│          audio mix, and render to the final export format
├── Intent: Produce the deliverable video file. This is pure mechanical
│          execution — every creative and technical decision has already
│          been made in Phases 1-5. The encoder reads the specifications
│          and builds the timeline accordingly.
├── Preconditions:
│   ├── State: shot_list, enhancement_spec, color_grade_spec,
│   │   audio_mix_spec — all verified
│   ├── Resources: Video editing tool (DaVinci Resolve, Remotion, FFmpeg,
│   │   or equivalent), sufficient disk space for render
│   └── Dependencies: [Phase 5 complete — Step 5.3]
├── Input State: Complete production specification
├── Transformation: Build timeline from shot_list → apply enhancements
│   from enhancement_spec → apply color grading from color_grade_spec →
│   apply audio mix from audio_mix_spec → render to output format
├── Output State: rendered_video:
│   {
│     output_file: absolute path to the rendered video file,
│     format: "MP4",
│     codec: "H.264",
│     resolution: { width: 1080, height: 1920 },
│     frame_rate: float,
│     duration_seconds: float,
│     file_size_bytes: integer,
│     render_time_seconds: float,
│     render_settings: { ... from style spec Section 8 ... }
│   }
├── Verification:
│   ├── Output file exists and is non-zero bytes
│   ├── Output file is playable (valid MP4 container)
│   ├── Resolution matches target (1080x1920)
│   ├── Duration matches the audio spine's total_duration (±0.5 seconds)
│   ├── Frame rate matches target
│   └── File size is reasonable for the duration and bitrate
├── Failure Modes:
│   ├── Render fails → Check error logs, identify the failing step
│   │   (usually a missing source file, unsupported codec, or disk space)
│   ├── Output file is corrupt → Re-render
│   └── Duration mismatch → Investigate timeline assembly errors
├── State Interaction:
│   ├── Reads: [shot_list, enhancement_spec, color_grade_spec,
│   │          audio_mix_spec, style_specification]
│   └── Writes: [rendered_video]
├── Parameters: Export settings from style spec Section 8:
│   ├── Format: MP4
│   ├── Codec: H.264
│   ├── Resolution: 1080 × 1920 (9:16)
│   ├── Bitrate: 12,000–15,000 kbps (restrict to)
│   ├── Encoding profile: High
│   ├── Multi-pass: Enabled
│   ├── Data levels: Full
│   └── Color space: Rec.709, Gamma 2.4
├── Idempotency: Yes — deterministic render of deterministic inputs
├── Classification: Deterministic / Execution
└── Notes:
    - This step is ENTIRELY an encoding concern — it is where all the
      tool-agnostic specifications from Phases 1-5 get translated into
      tool-specific commands. For DaVinci Resolve, this means importing
      clips, building the timeline, applying Fusion effects, setting
      color nodes, writing automation, and delivering. For Remotion,
      this means generating a React component tree and rendering. For
      FFmpeg, this means constructing filter graphs.
    - The render itself is deterministic — same inputs produce the same
      output. But it is the most tool-dependent step in the entire
      pipeline. Everything else was tool-agnostic data; this is where
      the rubber meets the road.
    - Render time depends on the tool, hardware, and complexity. For a
      30-60 second shortform video, expect 1-10 minutes on typical hardware.
```

### Step 6.2: Validate Output ⚡ NONDETERMINISTIC

```
STEP 6.2:
├── Action: Watch the rendered video and validate it against quality
│          criteria — visual quality, audio quality, timing accuracy,
│          subtitle correctness, and overall production value
├── Intent: Final quality gate. A human or AI watches the actual rendered
│          video and confirms it meets the standard. This catches issues
│          that specification-level validation couldn't — rendering
│          artifacts, audio sync issues, visual glitches, subtitle overlap
│          with important visual content, etc.
├── Preconditions:
│   ├── State: rendered_video exists and is playable
│   ├── Resources: Judgment capability, ability to watch the video
│   └── Dependencies: [Step 6.1]
├── Input State: Rendered video file
├── Transformation: Watch the video → assess quality → produce validation
├── Output State: final_validation:
│   {
│     status: "pass" | "fail",
│     visual_quality: { pass: boolean, issues: [string, ...] },
│     audio_quality: { pass: boolean, issues: [string, ...] },
│     subtitle_accuracy: { pass: boolean, issues: [string, ...] },
│     timing_accuracy: { pass: boolean, issues: [string, ...] },
│     overall_impression: string,
│     distribution_ready: boolean,
│     recommended_action: string or null
│   }
├── Verification:
│   ├── All check fields are populated
│   ├── status is "pass" only if all checks pass
│   └── distribution_ready is true only if status is "pass"
├── Failure Modes:
│   ├── Visual glitch (artifact, wrong clip, bad conform) → Return to
│   │   Step 6.1 to fix the render or trace back to the specification
│   │   that caused the issue
│   ├── Audio sync issue → Return to Step 6.1 or trace to Phase 2/3
│   ├── Subtitle error → Return to Step 4.1 to fix subtitle data
│   ├── Overall quality doesn't meet standard → Identify the weakest
│   │   element and trace back to the appropriate phase
│   └── Max 2 re-renders before escalating to human review
├── State Interaction:
│   ├── Reads: [rendered_video]
│   └── Writes: [final_validation]
├── Parameters:
│   └── Quality criteria:
│       - Video plays smoothly (no stuttering, artifacts, or corruption)
│       - Audio is clear (speech audible, music balanced, no clipping)
│       - Subtitles are readable and correctly timed
│       - Color grading is consistent (no ungraded clips)
│       - Duration is within the 30-60 second target
│       - Overall "would I post this?" gut check
├── Idempotency: No — judgment may vary
├── Classification: Nondeterministic / Evaluation & Judgment
└── Notes:
    - This is the ONLY step in the pipeline that evaluates the RENDERED
      output — everything else operates on specifications and data.
    - The "would I post this?" test is the ultimate quality bar. If the
      answer is no, the specific reason determines which phase to revisit.
    - When this step passes with distribution_ready: true, the pipeline
      is COMPLETE. The video is ready for upload/distribution.
```

---

**Phase 6 is complete.** Steps 6.1–6.2 cover export and validation:
render → validate.

**Exit state:** A validated, distribution-ready video file.

---

## Pipeline Complete

The full shortform video editing pipeline is decomposed into 6 phases,
25 atomic steps:

| Phase | Steps | Focus |
|-------|-------|-------|
| 1. Inventory & Analysis | 1.1–1.3 (3 steps) | Raw footage → enriched catalog + per-clip semantic analysis |
| 2. Audio Spine Construction | 2.1–2.7 (7 steps) | Semantic analysis → validated audio spine |
| 3. Visual Assembly | 3.1–3.4 (4 steps) | Audio spine → verified shot list (EDL) |
| 4. Enhancement | 4.1–4.6 (6 steps) | Shot list → verified enhancement spec |
| 5. Refinement | 5.1–5.3 (3 steps) | Enhancement spec → color + audio specs |
| 6. Export & Validation | 6.1–6.2 (2 steps) | Specs → rendered, validated video |

**Total nondeterministic steps:** 14 of 25 (creative judgment required)
**Total deterministic steps:** 11 of 25 (mechanical execution)
