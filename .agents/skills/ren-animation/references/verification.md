# Verification

A successful renderer exit is not verification. Check what the requested
change should affect, using the least expensive evidence that can prove
it. Do not claim a check ran unless you ran it.

## Deterministic render gate

Use `verify_render` for file-level checks such as black frames, freezes,
loudness, silence, resolution, frame rate and duration:

```bash
bin/vep -m library.skills.verify_render.skill \
    --video /path/to/master.mp4 \
    --project-folder /path/to/project \
    --step-id <step-id>
```

## Frame inspection

Use ffmpeg or the available renderer tooling to capture relevant frames.
Compare with the requested result, project declarations and measured
safe area. Inspect text fit at the intended output size, clipping,
contrast, transition boundaries and unintended blank frames. Do not
apply generic margins, word counts, timing thresholds or stylistic
rules as a substitute for the creative direction.

For a composition rendered by `ren-graphics-fframes`, the binary writes
frame PNGs or raw RGBA output. It does not provide standalone inspect,
contact-sheet, onion-skin, snapshot or audio-analysis subcommands.

## Motion and sequence

When the change affects motion, inspect frames across the affected
interval. An onion-skin view can help when available, but it is an
inspection method, not a shipped FFrames command. Use a human or the
`ask_the_footage` workflow for questions whose answer depends on motion
or sequence rather than a single frame.

## HyperFrames

The pinned CLI is used by `library/tools/hyperframes_render.py`:

```bash
npx --no-install hyperframes@<pin> lint
npx --no-install hyperframes@<pin> check
npx --no-install hyperframes@<pin> keyframes .
npx --no-install hyperframes@<pin> snapshot . --at <times>
```

## What to report

State which checks ran and their results. Name anything material that
was not inspected as unchecked. A plan or renderer exit code alone does
not establish that the animation is correct.
