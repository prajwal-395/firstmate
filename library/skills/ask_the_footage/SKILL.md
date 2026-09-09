# ask_the_footage

Eyes on the picture, on request. Ask this when you need to SEE the
footage rather than read about it.

## What it is

Two routes to the same thing - the captain's "natively if possible, or
through Gemma 4 12B by way of Q&A":

1. **Stills you look at yourself.** A frame is captured from the file
   and handed to you as a picture, the way the frame-grab path hands
   the orchestrating model a frame to judge natively.
2. **Gemma Q&A.** The captured stills are shown to the local Gemma 4
   12B model with your question, and its answer comes back as text -
   the segment path that runs deterministic ffmpeg checks plus Gemma
   vision and feeds the structured summary back as context.

Both run in one call, and both are reported: what the deterministic
checks measured, what the model thought, and how long each took.

## WHEN to reach for it

- You are judging a visual decision and the prose does not carry
  appearance: is the caption readable, did the transition land, does
  the grade match the intent, is anything cropped that should not be.
- A deterministic check failed and you want to know whether it is real
  or a measurement artifact before acting on it.
- `review_rough_cut` is your step: looking at the cut is the job.

Do NOT reach for it on every build, on every clip, or as a substitute
for `verify_render` - the model load is gigabytes and seconds, and the
captain ruled the Gemma path runs when a step asks for it, not on
every build. Do NOT reach for it when the question is answerable from
numbers already in front of you (loudness, duration, resolution).

## What it costs

The deterministic half is free (ffprobe-scale). The Gemma half loads a
~7.5GB 4-bit model on first use and spends roughly 6 seconds per
still. The receipt records both timings, so the cost of YOUR call is
visible on the run that paid it.

## What it returns, and what it means

An OBSERVATION, not a verdict:

- `deterministic_passed` - what ffmpeg measured (black frames, freeze
  frames). This half CAN fail, and when it does the footage is broken
  regardless of any opinion.
- `vision` - the model's recorded opinion with its confidence, or
  `available: false` with the reason when the model could not be
  reached. An unavailable model is a reported fact, not a failure:
  the deterministic half still answers.
- `stills` - the captured frames, on disk, for you to open yourself.

Nothing here gates anything. A caller that wants a gate uses
`verify_render`. Your opinion is recorded into the retry context; it
is never enforced.

## How to call it

You have a shell. Run it yourself:

```
python3 -m library.skills.ask_the_footage.skill \
    --video /path/to/segment.mp4 \
    --question "Is the caption readable against this background?" \
    --project-folder /path/to/project \
    --step-id <your-step-id>
```

Optional: `--timestamp SECONDS` to look at one moment (default: three
stills spread across the file), `--check-type` for the kind of
question (`general`, `subtitle`, `transition`, `vfx`, `color`).

The run writes a receipt to
`<project>/pipeline_output/skill_runs/<step-id>/ask_the_footage.json`.
If you have no shell, say what you need to see and the pipeline runs
it for you - the observation comes back through the retry context,
the same route a contract rejection travels.
