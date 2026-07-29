# Style Specification — Shortform Video Editing

This document codifies the creator's editing style into concrete, reproducible parameters. It translates aesthetic intuition into actionable specifications that can be encoded as deterministic steps.

**Philosophy:** "The bass guitar principle" — subtle, underspoken edits that you don't consciously notice, but whose absence is immediately felt. Every edit should be tasteful, purposeful, and additive without being flashy or attention-seeking.

**Reference Inspirations:**
- **Casey Neistat** — raw authenticity, relentless audience captivation, music-driven pacing, jump cuts, in-camera transitions
- **Scott Yu-Jan** — cinematic precision, elegant composition, warm highlight / cool shadow contrast, seamless match cuts, intentional lighting and framing

---

## 1. Color Grading

### Overall Look
- **Warm, vibrant, cinematic** — golden warmth in highlights/midtones, slightly cooler shadows for depth
- **Bright and energetic** — not dark/moody; the grade should feel alive and inviting
- **Elegant and classy with an "out of touch" flair** — polished, almost aspirational
- **Film-like quality** — subtle grain, gentle glow, soft highlight rolloff

### Technical Parameters (DaVinci Resolve Node Tree)

```
Node 1: Color Space Transform (Input)
├── Convert camera footage to DaVinci Wide Gamut / Intermediate
└── Purpose: Establish a clean, neutral base

Node 2: Primary Correction
├── White balance: Slightly warm (add ~200K over neutral)
├── Exposure: Set to taste per clip
├── Contrast: Gentle S-curve
│   ├── Lift shadows slightly (+0.02) — avoid crushed blacks
│   └── Soft-roll highlights (-0.03) — retain detail
└── Saturation: +10-15% above neutral — vibrant but not oversaturated

Node 3: Warm Tone Shaping
├── Offset: Push slightly toward orange/gold (+0.01 red, +0.005 green)
├── Highlights: Warm golden tone
├── Shadows: Slightly cool (hint of teal/blue) for contrast
├── Midtones (Gamma): Neutral-warm
└── Skin tone protection: Qualify skin tones, soft-lock hue to vectorscope line

Node 4: Creative / Film Look
├── Glow: Subtle (composite: Screen, opacity 10-15%)
├── Film Grain: Fine grain, subtle (amount: 0.2-0.3)
├── Vignette: Subtle darkening at edges (amount: 0.15-0.20)
└── Halation: Optional — very subtle warm bloom around highlights

Node 5: Color Space Transform (Output)
├── Convert to Rec.709 Gamma 2.4
└── Purpose: Standard display output for social media
```

### Quick Reference
| Parameter | Value |
|---|---|
| Color temperature feel | Warm, golden |
| Shadows | Slightly lifted, hint of cool teal |
| Highlights | Warm, soft rolloff |
| Saturation | Vibrant (+10-15%) |
| Contrast | Medium — gentle S-curve |
| Film grain | Fine, subtle |
| Glow | Subtle screen composite |
| Overall mood | Bright, warm, cinematic, elegant |

---

## 2. Subtitles / Text

### Style
- **Case:** All lowercase — "lowkey and chill" feel
- **Weight:** Bold — present but not aggressive
- **Font family:** Sans-serif, geometric
- **Recommended fonts (in order of preference):**
  1. **Poppins Bold** — round, friendly, reads great in lowercase, modern
  2. **Montserrat Bold** — geometric, contemporary, crisp
  3. **Roboto Bold** — designed for screens, highly legible at small sizes
- **Color:** White (#FFFFFF) with subtle drop shadow for readability
- **Shadow:** Very soft, ~2px offset, 50% opacity black — just enough to separate text from background
- **Position:** Lower third of frame, centered
- **Size:** Large enough to read on mobile but not dominating — approximately 5-6% of frame height
- **Background:** None (no background box) — relies on shadow for contrast
- **Line length:** Maximum 2 lines, ~6-8 words per line

### Animation / Kinetic Typography
- **Entrance:** Subtle fade-in or soft pop (scale from 95% to 100% over 150ms with ease-out)
- **Exit:** Cut or subtle fade-out (100ms)
- **Word emphasis:** No color changes; use slight scale bump (102-105%) on key words
- **Overall feel:** "You don't notice it's animated until you see a version without animation"

### What to Avoid
- All caps
- Drop shadow that's too harsh or visible
- Background boxes or bars behind text
- Overly bouncy or attention-seeking entrance animations
- Color-coded keyword emphasis

---

## 3. Transitions

### Philosophy
Transitions should be **seamless and creative** but never gratuitous. The best transition is one the viewer doesn't consciously register — it just *flows*.

### Transition Toolkit (ranked by frequency of use)

| Transition | When to Use | Notes |
|---|---|---|
| **Hard cut / Jump cut** | Most cuts — default | Keeps energy high, compresses time. Cut on motion or audio beats |
| **Whip/Swish pan** | Scene/location changes | Motion blur hides the cut; match movement direction between shots |
| **Zoom transition** | Energy moments, reveals | Quick zoom into/out of subject to bridge shots |
| **Match cut** | Visually similar compositions | Object/shape/motion continuity between shots (Scott Yu-Jan specialty) |
| **J-cut / L-cut** | Dialogue, narrative flow | Audio from next clip starts before or extends after video cut |
| **Cross dissolve** | Time passage, mood shifts | Short duration (8-15 frames); use sparingly |
| **Light leak / flare** | Stylistic bridges | Overlay light leak VFX asset to bridge two shots |

### Transition Rules
1. **Default to hard cuts.** Only use creative transitions when they serve the story or energy.
2. **Cut on movement or beats.** Never cut during a static hold unless intentional.
3. **Match energy.** Transition style should match the energy of the surrounding content.
4. **Variety.** Don't use the same creative transition twice in a row.

---

## 4. Sound Design (SFX)

### Philosophy
Sound effects are the "bass guitar" of video editing. You should **feel** them more than **hear** them. They add texture, weight, and polish without drawing conscious attention.

### SFX Toolkit

| SFX Category | When to Use | Volume Level |
|---|---|---|
| **Whoosh / Swish** | On cuts, transitions, camera movements | Subtle — just below conscious awareness |
| **Bass / Sub impact** | Reveals, title drops, emphasis moments | Low, felt more than heard |
| **Riser / Tension build** | Before a reveal or punchline | Gradual, supportive |
| **Foley / Ambient** | Establishing scenes, adding texture | Bed layer, very quiet |
| **Click / Tick / UI sounds** | Text appearances, small visual elements | Very subtle, crisp |
| **Reverse cymbal / Swell** | Transitions between major sections | Blends with music |

### SFX Rules
1. **Less is more.** Not every cut needs a sound effect.
2. **Layer with purpose.** A whoosh + subtle bass hit on an important transition; just a whoosh on a routine one.
3. **Match the music.** SFX should complement the music's rhythm and energy, not fight it.
4. **Pan and space.** SFX should feel like they exist in the soundstage, not pasted on top.

---

## 5. Music

### Selection Criteria
- **Mood matching:** Music should match or enhance the emotional arc of the video
- **Energy curve:** Music energy should mirror the video's pacing — build, climax, resolve
- **Not genre-locked:** Can be lo-fi, trap, electronic, cinematic, chill, hip-hop — whatever serves the content
- **Source:** Currently ad-hoc (YouTube audio downloads)

### Music Rules
1. **Select or at least identify music early** (ideally before final assembly if possible) — cut to the rhythm when possible
2. **Volume:** Background level — present but not competing with dialogue. Duck under speech.
3. **Beatmatching:** Major cuts and transitions should land on musical beats or accents when possible
4. **Transitions:** Fade music gently at start and end; never hard-start or hard-stop music

### Future Improvement
Build a curated music catalog organized by mood/energy/tempo so music selection becomes a lookup rather than a search.

---

## 6. Pacing & Rhythm

### Shortform Pacing Standard
- **Clip duration:** 2-4 seconds typical; 1-2 seconds for B-roll cuts; up to 5-8 seconds for talking head (with movement)
- **Hook:** First 1-3 seconds MUST grab attention — strongest visual or most compelling moment
- **Total duration:** 30-60 seconds for shortform
- **Energy contour:** Start high (hook) → maintain with variety → build to moment → resolve

### Talking Head Technique
When a talking head shot runs longer than a few seconds, apply these subtle engagement techniques:
- **Slow zoom:** Gentle 5-10% zoom over the duration of the clip (almost imperceptible)
- **Cut-in / Cut-out:** Alternate between a wider and tighter framing of the same shot
- **B-roll interjections:** Insert 1-3 second B-roll clips to illustrate what's being said
- **Jump cut on dead space:** Cut out pauses, "um"s, repetitions — keep the speech tight

### Movement Principle
**The frame should always be moving** — either through camera motion, subject motion, zoom, or editorial techniques. Static holds kill engagement in shortform content.

---

## 7. Motion Graphics & Animations

### Philosophy
Like all other elements — tasteful, subtle, purposeful. Animations should enhance understanding or add visual interest without screaming for attention.

### Common Use Cases
- **Text animations:** Subtle entrance/exit for subtitles and callouts (see Section 2)
- **Zoom emphasis:** Quick 5% scale bump on a key word or visual element
- **Shake/impact:** Subtle screen shake (2-3px, 3-4 frames) for emphasis moments
- **Tracking text/graphics:** Text or graphics that follow a subject or object in frame
- **Lower thirds:** Clean, minimal lower third for context/labels (same font as subtitles)

### Animation Timing
- **Ease-out** for entrances (snap in, gentle settle)
- **Ease-in** for exits (gentle start, snap out)
- **Duration:** 100-200ms for most micro-animations; never longer than 500ms
- **Keyframe interpolation:** Bezier curves, not linear — everything should feel organic

---

## 8. Export Settings

### Standard Shortform Video Export Preset

| Setting | Value |
|---|---|
| **Format** | MP4 |
| **Codec** | H.264 |
| **Resolution** | 1080 × 1920 (9:16 vertical) |
| **Frame rate** | Match source (typically 24fps or 30fps) |
| **Bitrate mode** | Restrict to |
| **Bitrate** | 12,000–15,000 kbps |
| **Encoding profile** | High |
| **Multi-pass encode** | Enabled |
| **Data levels** | Full |
| **Force sizing to highest quality** | Yes |
| **Force debayer to highest quality** | Yes |
| **Color space** | Rec.709, Gamma 2.4 |

---

## Style Summary

> **In one sentence:** Warm, cinematic, vibrant shortform videos with elegant composition, all-lowercase subtitles, seamless transitions, subtle-but-impactful SFX, rhythm-driven pacing, and an overall "effortlessly polished" aesthetic that captivates without trying too hard.
