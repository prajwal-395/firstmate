# Verification

How Ren verifies motion itself. A render that exited 0 is not a
verified animation. This skill describes the inspection loop: contact
sheets, onion skin, frame stills viewed by the driving LLM's own
vision, and whole-video checks by Gemma.

## The loop

After every change to an animation:

1. **Static diagnostics** - check the timeline structure, missing
   fonts, text leaving the canvas, invalid SVG, broken transforms.
   No pixels rendered. Fast.
2. **Contact sheet** - evenly spaced frames in one labelled image.
   The fastest way to judge layout, rhythm and motion.
3. **Frame stills** - full-size PNGs of key moments (typography,
   alignment, contrast, transition boundaries).
4. **Onion skin** - blended frames showing the path and spacing of a
   movement: easing, overshoot, stagger.
5. **Whole-video** - a model watches the rendered file and reports
   what it sees.

## Commands that exist today

### Ren's own verification

```bash
# Deterministic gate over a rendered file (black, freeze, loudness,
# silence, resolution, frame rate, duration)
python3 -m library.skills.verify_render.skill \
    --video /path/to/master.mp4 \
    --project-folder /path/to/project \
    --step-id <step-id>

# Stills + Gemma vision (ask_the_footage skill)
# Capture a frame and look at it yourself, or ask Gemma a question
# about it. The skill runs deterministic ffmpeg checks plus Gemma
# vision and feeds the structured summary back as context.

# ffmpeg frame extraction (always available)
ffmpeg -ss 3.2 -i master.mp4 -frames:v 1 frame.png
ffmpeg -ss 3.2 -i master.mp4 -vf "select=eq(n\,96)" -frames:v 1 frame.png
```

### HyperFrames CLI (pinned, used by library/tools/hyperframes_render.py)

```bash
npx --no-install hyperframes@<pin> lint
npx --no-install hyperframes@<pin> check
npx --no-install hyperframes@<pin> keyframes .
npx --no-install hyperframes@<pin> keyframes . --selector "<selector>" --shot "<file>" --samples <n>
npx --no-install hyperframes@<pin> snapshot . --at <times>
```

The `--no-install` flag and the version pin match the invocation form
in `library/tools/hyperframes_render.py`, which resolves the pinned
HyperFrames version Ren already ships.

## PLANNED: ren-graphics-fframes inspection commands

The `ren-graphics-fframes` runtime (being built in a parallel lane,
see `renderers/fframes.md`) is expected to expose FFrames' inspection
primitives as subcommands of the Ren-owned binary. These do NOT exist
today. When they ship:

```bash
ren-graphics-fframes inspect [RANGE]        # static diagnostics
ren-graphics-fframes strip <scene> -n 12    # contact sheet
ren-graphics-fframes frame <times>          # full-size PNGs
ren-graphics-fframes onion <range> -n 6     # onion skin
ren-graphics-fframes svg <time>             # a frame's final SVG
ren-graphics-fframes snapshot <times>       # frame diff
```

Until the runtime ships, use Ren's own verification loop above or the
FFrames CLI directly (if installed on the machine).

## Contact sheet inspection

A contact sheet is evenly spaced frames in one labelled image. Open it
and check. The thresholds below are measured starting points, not
defaults - a project's own declared values win, and the safe-area
insets come from `library/tools/safe_area.py`, never a fixed margin:

- [ ] Nothing important inside the safe-area insets (from
      `library/tools/safe_area.py`; ~8-10% of the frame edge for
      landscape, tighter for portrait).
- [ ] Text fits its box and the canvas; no line longer than ~8 words;
      nothing below 28 px at 1080p.
- [ ] Every text is on screen long enough to read (3 words/s + 1 s
      after it settles).
- [ ] Only one thing draws the eye at a time; entrances are
      staggered, not simultaneous.
- [ ] Consistent margins, alignment and colours across scenes.
- [ ] Contrast is sufficient on every background (including gradients
      behind text).
- [ ] Transitions have no empty or flashing frame.
- [ ] The first frame is not blank for social formats.
- [ ] Static diagnostics are clean.

## Onion skin inspection

An onion skin blends frames into one image. It shows:

- **Easing** - even spacing means constant speed; clustered ghosts
  mean slow-in or settle; large gaps mean fast travel.
- **Overshoot** - the subject passes its final position and returns.
- **Stagger** - each item's ghost trail starts at a different time.

If the onion skin shows a movement you did not intend, the easing
parameters are wrong, not the inspection.

## Frame still inspection

Full-size PNGs of key moments. Look at:

- Typography: is the text readable, is the weight right, is the
  letter-spacing working?
- Alignment: do elements share edges? Is the grid consistent?
- Contrast: is the text readable against its background at full size?
- Transition boundaries: is the last frame of one scene and the
  first frame of the next both correct?

## Whole-video inspection

Most LLMs do not process video natively, so whole-video
understanding stays on Gemma. The `ask_the_footage` skill captures
stills and asks Gemma questions about them. Use it when:

- A deterministic check failed and you want to know whether it is
  real or a measurement artifact.
- You are judging a visual decision the prose does not carry (is
  the caption readable, did the transition land, is anything
  cropped).

Do NOT use it on every build or as a substitute for `verify_render`.
The model load is gigabytes and seconds.

## Snapshot diff

Compare rendered frames against approved PNGs. A `.diff.png` marks
changes. Use it when:

- A revision should not have changed the picture.
- You want to prove a change was intentional.

## What "verified" means

An animation is verified when:

1. Static diagnostics pass (or the remaining warnings are understood
   entrances).
2. A contact sheet of every scene looks right.
3. Key frames are checked at full size.
4. Onion skin of every movement shows the intended path.
5. The deterministic gate (`verify_render`) passes on the rendered
   file.
6. A whole-video check (Gemma or human) confirms the animation reads.

A render that has not been through this loop is not verified,
regardless of what the plan says.
