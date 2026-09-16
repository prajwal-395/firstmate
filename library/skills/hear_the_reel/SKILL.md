# hear_the_reel

What a rendered reel actually SAYS, against what the plan says it says.

## What it is

The render's audio is transcribed on the machine, the plan's own words
are resolved out of the timeline transcript through the reel's placed
clips, and the two are aligned and diffed. It reports three things:

- **script divergence** - words the render says that the plan does not,
  and words the plan carries that the render does not say;
- **timing drift** - where the render says each word against where the
  plan puts it, and any RUN of consecutive words drifting the same way
  past the transcribers' own measured disagreement;
- **caption coverage** - speech in the delivered file with no caption
  card on screen while it is spoken.

Nothing else in this pipeline compares HEARD against PLANNED.
`verify_render` measures the file against itself, `verify_timeline`
measures the timeline against the SOP, `ask_the_footage` looks at the
picture. A caption card a second behind its own audio is invisible to
all three and obvious to this.

## WHEN to reach for it

- You are judging, revising or building on a **delivered reel**, and the
  question is whether what it says lines up with what was planned.
- A caption, a karaoke highlight, a take boundary or a cut looks wrong
  and you want to know whether the audio agrees with the plan before you
  change anything.
- You are about to tell the captain a reel is fine.

Do NOT reach for it to judge a plan on paper - it hears a FILE, and with
no rendered file it refuses rather than passes. Do not reach for it on a
project with no timeline transcript: there is then nothing that says
what the plan says, and it refuses for that too.

## What it costs

**Seconds.** Measured on this machine: **3.5 s wall clock** for a 45.9 s
reel, which is the whole reason this exists - the same comparison
through the pipeline's own transcriber is 33-47 minutes for an episode.
No Resolve, no GPU model load, no render, no model call at all. It reads
the mp4 directly; there is no ffmpeg pre-step.

It needs the on-device transcriber installed (`da`). Absent, it returns
`available: false` naming what is missing rather than reporting a clean
reel nobody listened to.

## What it returns, and what it means

An **observation**, never a verdict. `findings` carries one row per
check with its own `passed`, `severity` and the step that owns the
decision behind it; `divergences`, `drift_runs` and `caption_coverage`
carry the evidence under each.

**This one REPORTS. It gates nothing** - `library/tools/reel_hearing.py`
sets `GATES = False`, no build reads its record, and a `passed: false`
row here fails nothing. Say what it found and what you think about it;
the decision to act is yours and the decision to promote any of it to a
gate is the captain's.

Two things to read before blaming the edit:

- `transcriber_anomalies` - the transcriber's own structural failures (a
  word held across a silence, a degenerate interval). A divergence next
  to one of these is more likely the transcriber's than the edit's.
- `wordless_transcript_rows` - transcript rows this reel plays that
  carry text and NO word timings. One of these is the upstream cause of
  an uncaptioned passage and of everything after it drifting; it is a
  separately filed fix and not something to change here.

Divergences that are NOT defects, and are reported anyway: contractions
(`gonna` against `going to`), spelled-out numbers, compound splits and
filler words one transcriber catches and the other drops. Names this
project has filed a spelling correction for are already normalised on
both sides - `Lucie` never reads as a divergence.

## How to call it

You have a shell. Run it yourself - do not describe the render from
memory:

```
python3 -m library.skills.hear_the_reel.skill \
    --project-folder /path/to/project \
    --step-id <your-step-id> \
    --reel 26
```

`--reel` takes the reel number or its timeline name; omit it for the
most recently delivered reel. `--video PATH` hears a specific rendered
file instead, and `--timeline PATH` names the plan when the render has
no `.deliver.json` beside it. `--announce` raises each finding on the
project's own hook layer, which is how one reaches the captain's review
channel as an anchored note - nothing fires unless the project declares
a hook for it.

The run writes a receipt to
`<project>/pipeline_output/skill_runs/<step-id>/hear_the_reel.json` and
the full record, including both scripts word for word, beside the render
as `<render>.hearing.json`.

Hearing every delivered reel at once is the same core through the CLI:

```
python3 manage_project.py hear-reel <project> --all
```
