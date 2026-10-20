---
name: verify_export
description: >
  The deterministic gate over a DELIVERED export, against the accepted edit
  plan: clip and track structure, exact timing and source ranges, media
  identity, linked audio, missing media, black and frozen frames, gaps and
  overlaps, format and encoding, caption timing and presence, graphic
  placement, loudness, clipping, channel configuration, and the export's
  identity and provenance. Use before approving, signing off or shipping
  any exported file. Free, and a criterion that cannot be checked is
  reported as unknown, never as a pass.
---

# verify_export

The final-output gate. Run it before you sign off on, or ship, an export.

## What it is

The captain's eight final-output acceptance criteria, as ONE verdict each:

| | Criterion |
|---|---|
| QA-001 | clip and track structure against the accepted edit plan |
| QA-002 | exact timing, source ranges, media identity, linked audio |
| QA-003 | missing media, black frames, frozen frames, gaps, overlaps |
| QA-004 | output format, frame rate, dimensions, duration, encoding |
| QA-005 | subtitle/caption timing and presence |
| QA-006 | graphical elements at the expected placement and scale |
| QA-007 | loudness, peaks, clipping, channel configuration |
| QA-008 | export identity and provenance against the project revision |

It is a COMPOSER, not a second set of measurements: every criterion calls
the checker that already answers it - `render_qa` for the file,
`manifest_validator` for the plan, `subtitle_qa` for the captions,
`overlay_verify` for the overlays, `plan_provenance` and the delivery
ledger for the identity - and records what that checker measured.

It invents no expectation. A criterion whose evidence is missing comes
back UNKNOWN, with the reason and what would resolve it.

## WHEN to reach for it

- A file has been delivered or exported and you are about to approve it,
  sign it off, or ship it. Run this FIRST, before writing any judgement
  of your own.
- You are the step that judges a final output.

Do NOT reach for it to judge a plan on paper, and do not reach for it
for a picture you have not exported yet: it measures a FILE, and with no
file it refuses rather than passes. For a render that has not been
delivered, `verify_render` is the gate.

## What it costs

Free. No model loads, no Resolve, no GPU, no render. Seconds of ffmpeg
and ffprobe on the file, plus the arithmetic that compares the plan, the
build's own committed record and the delivery's identity record - all
three of which are already on disk.

## What it returns, and what it means

A verdict that GATES: `passed` plus one row per criterion with its
`verdict`, its `detail`, its `evidence` and its `reader`.

- `pass` - the criterion was checked and holds.
- `fail` - the export is wrong. Say which criterion failed and what it
  measured, and do not ship it.
- `unknown` - nobody could tell. **This is not a pass.** The verdict
  names what was missing and what would resolve it, and `passed` is
  false while any criterion is unknown: "a verified export includes
  evidence for every required acceptance criterion", so an export nobody
  could check is an unchecked export.
- `not_applicable` - the criterion had nothing to judge (a project with
  no captions has no caption timing to verify).

## How to call it

You have a shell. Run it yourself - do not assert you checked:

```
bin/vep -m library.skills.verify_export.skill \
    --video /path/to/export.mp4 \
    --project-folder /path/to/project \
    --step-id <your-step-id>
```

Optional: `--timeline "Reel 09 (final)"` to name the build and delivery
the export is judged against (the delivery ledger names one when you do
not), and `--plan /path/to/assembly_manifest.json` to name the accepted
plan (the project's compiled manifest when you do not).

The run writes a receipt to
`<project>/pipeline_output/skill_runs/<step-id>/verify_export.json`.
The pipeline reads that receipt back to confirm the check ran: only an
actual invocation writes one, so saying you checked without invoking
fails the step. If you have no shell, say which criteria you need and
the pipeline runs it for you - the receipt is still written, and its
verdict still gates.
