# render (step 6.01) - reasoning

Written before the answer. Request: `pipeline_output/llm_requests/render.json`
(prompt 3,217 chars; context 38,164 chars; schema 100 chars).

## 1. What this step actually asks of me

Almost nothing, and it is worth being precise about that. By the time this call is made the
render has already happened - `resolve_build_timeline.py` assembled the timeline and
`resolve_render.py` drove the Deliver page, and the log already says
`✓ Rendered .../Pipeline_Edit.mp4 (106.8 MB)`. The only output declared is
`render_review`, an **optional** array of "visual QA verdicts". Nothing I write changes the
file.

So the useful thing to do is not to narrate the render but to **check it**, and the prompt
hands me a checklist to check it against (its "Verification" section and its "Export
Parameters" table). This step is also one of the two AGENTS.md 10.1 names as deliberately
unprojected, so I received the whole context - and I did not need most of it.

**I measured the file rather than reporting what the log said about it.** Declaring that as
outside-the-context work: everything in section 3 comes from `ffprobe`, `ffmpeg -af ebur128`,
`blackdetect`, and reading the compiled manifest back out of `pipeline_data.json`.

## 2. The captain's ruling, and what this render is missing because of it

The first attempt at this step **failed**, and it failed in exactly the way AGENTS.md 5
warns about. The whole build succeeded - 11 V1 clips, 7 V2, 11 subtitle segments, 3 audio
tracks, 17 Fusion comps, 2 transitions, 2 SFX, the OTIO mix - and then the step died on the
**first clip of the stabilization pass**:

```
── Neural Engine: 13 clips ──
  ✗ [V10] hook_hook: Stabilization
✗ FAILED (RuntimeError): Step failed (exit 1)
```

`neural_engine_directives` runs after everything else is placed, so a build that dies inside
it loses all of it - which is the documented reason that section calls stabilization "the
memory ceiling of the whole pipeline".

**The captain ruled: "skip stabilization and move on."** The route taken is the one AGENTS.md
5 prescribes for exactly this - pop `neural_engine_directives` off the **in-memory** manifest
before `build_timeline` and leave the file on disk carrying it. I added a single opt-in
env var (`PIPELINE_SKIP_STABILIZATION`) in `step_6_01_render/step.py`, off by default. The
manifest in `pipeline_data.json` and in
`pipeline_output/steps/5_04_compile_manifest/output.json` still carries all **13** directives:
the plan is unchanged and only this run declined to execute it.

**So this render is un-stabilized, and that is a real and visible gap, not a formality.**
Nine of the eleven A-roll placements come from handheld selfie footage that the vision
analysis labels `shaky`. Steadying was planned for 13 clips and none of it happened. Anyone
judging this render's picture should judge it knowing that.

## 3. What I measured

**The prompt's Verification list, item by item:**

| check | result |
|---|---|
| exists, non-zero | 106,830,811 bytes |
| playable, valid MP4 | yes - `mov,mp4,m4a` container, h264 + aac read cleanly |
| resolution matches 1080x1920 | **1080x1920** |
| duration within +/-0.5s of the spine | spine 59.437s, file **59.4987s**, delta **0.062s** |
| frame rate matches | **30/1**, 1783 frames (= 59.433s at 30fps, the spine exactly) |
| file size reasonable | 14.364 Mbps x 59.5s = 106.8 MB - consistent to the byte |

**The prompt's Export Parameters table:**

| parameter | asked | delivered |
|---|---|---|
| Format | MP4 | MP4 |
| Codec | H.264 | **h264** |
| Resolution | 1080x1920 | 1080x1920 |
| Bitrate | 12,000-15,000 kbps | **14,056 kbps** (video) |

*(The prompt contradicts itself on codec: the Export Parameters table says H.264 and the
Implementation Notes say "Render uses H.265". The file is H.264, which matches the table.)*

**Audio - checked because AGENTS.md 5 says renders are silent by default:** the file carries
an **AAC 48 kHz stereo stream at 320 kbps**. Not silent.

**Two things I checked that the prompt does not ask for:**

- **`blackdetect` found no black stretches at all** across all 1783 frames. That is the
  render-side confirmation of `_assert_timeline_fully_covered` - the plan-side gate said every
  frame has a clip, and the picture agrees.
- **True peak -1.6 dBFS.** Not clipping, which is the half of the LUFS check that actually
  gates at 6.02.

**Integrated loudness is -20.9 LUFS**, roughly 7 LU below the -14 LUFS a social platform
normalises to. This is the known LUFS finding that fails 6.02 and it is explicitly not mine
to fix. Recording the number rather than the verdict.

## 4. Did my decisions actually reach the file?

This is the part I care about most, because a plan that does not reach the picture is the
failure mode AGENTS.md 10.2 exists for. Checked each:

- **The silent climax** (my one real audio decision, at 2.05). The compiled manifest's
  `_spine_blocks` carries `48.065-56.731 behavior=silent`, and the build log reports the mix
  went through OTIO as `background_music: 24 keyframes, -96..-6dB`. A -96 dB floor is
  silence. **It reached the mix.**
- **The two SFX** (4.4). `SFX: 2 clips across tracks` - `sfx_001: TL 1038-1046 → A3` and
  `sfx_002: TL 1384-1474 → A3`. Frame 1038 is 34.6s and frame 1384 is 46.13s: blocks 8 and
  13, exactly where I placed them after the re-plan. **Both landed.**
- **The two defocus transitions** (4.2, second version). `Fusion .comp: 17 VFX, 2
  transitions`, with `speech_7_seg0: 10 tools (tail=defocus)` / `speech_9_seg0: 10 tools
  (head=defocus)` and the same pair on `speech_12` / `speech_14`. **Both drew**, as a tail on
  the outgoing V1 clip and a head on the incoming one.
- **The empty VFX plan** (4.3). The "17 VFX" in that log line is the per-clip conform and
  house-look comps that every clip gets, **not** planned effects - I planned zero and zero
  were added. Saying so explicitly because that log line reads like my VFX plan did
  something, and it did not.

**One thing I could NOT verify and want to be honest about.** The per-block loudness I
measured (hook -26.8, block 2 -25.9, sunset slot -23.9, clip_012 triple -20.5, climax -19.0,
resolution -21.7 LUFS) is the **mixed** signal, so it cannot separate music from speech. The
climax measuring loudest is his voice being loudest there, not evidence about the music. The
evidence that the music is actually silent under it is the manifest and the OTIO keyframe
range, not the file. To prove it from the file I would need the stems.

## 5. What I was missing

- **Any sight of the picture.** I have measured that 1783 frames exist, that none is black,
  and that the geometry is right. I have not looked at one of them. Everything about whether
  the conform framing works, whether the blurred backdrop reads, whether the captions sit
  well - all of it is unchecked here, and the prompt calls this field "visual QA verdicts".
  Frame extraction is available (AGENTS.md 5: under 2KB means a broken or black frame) and I
  did not do it, because `blackdetect` over all 1783 frames is a strictly stronger version of
  that same check and I judged sampling stills would add nothing a human eye is not needed
  for anyway.
- **Stems**, per section 4.
- **The stabilization this render should have had**, per section 2.

## 6. Confidence

**High** on every line of section 3 - they are measurements of the delivered file with the
commands named, not readings of the build log.

**High** that the four planning decisions reached the timeline, with the one stated
exception about proving the silence from the file rather than from the manifest.

**Not applicable** to the question the field is named for. `render_review` says "visual QA
verdicts", and I have delivered a technical QA verdict. I have not seen the video. What I can
say is that the container, geometry, duration, frame count, bitrate, audio stream, peak level
and black-frame count are all correct, and that the plan is present in the file; what a
person will say about whether it is any good is a different question that this step cannot
answer.
