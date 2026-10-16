# Adaptive source-frame sampling policy (2026-10-15)

## Existing baseline and scope

`library/tools/semantic_window_planner.py` already schedules activity-weighted
semantic windows. It preserves a uniform discovery floor, reports window
coverage and uncovered high-priority or unmeasured bins, and forbids inferring
events from unsampled time. PR 2616 is already on this base and carries the
evidence envelope's sampling basis and coverage. The existing window planner
does not produce a query-specific source-frame schedule or expose selected
frame counts and remaining source intervals.

This change adds `library/tools/adaptive_frame_sampling.py` as an opt-in policy
adapter. It consumes existing source-time measurements and retrieval candidates;
it does not decode media, run a model, or change the current semantic-window
default. Policy version, parameters, selected timestamps, selection reasons,
examined frame count, unexamined intervals, and sampled-only claim policy are
returned together for downstream provenance.

## Selection comparison

| Measurement | Existing semantic-window baseline | New source-frame policy |
|---|---|---|
| Unit selected | Fixed-duration windows | Source-frame timestamps |
| Activity input | Novelty, motion, scene, audio, speech, uncertainty | Existing activity bins plus explicit temporal boundaries |
| Query/refinement | Task profile only | Optional query and retrieval candidate intervals; bounded frame budget |
| Fine actions | Window priority | Higher temporal density around retrieval candidates for gesture/action queries |
| OCR/detail | No source-frame resolution request | Query marks higher-resolution analysis requested |
| Coverage report | Window union and unmeasured/activity candidates | Examined frame count and explicit unexamined source intervals |
| Event absence | No interpolation | Sampled evidence only; no exhaustive absence claim |
| Runtime / memory | Scheduling only | Scheduling only; no model load or decode |

The unit tests measure deterministic scheduling behavior on synthetic fixtures:
boundary inclusion, candidate concentration, budget compliance, stable activity
selection, gaps, and refusal of invalid rates. This measures information
prioritization per selected frame as a **synthetic scheduling proxy**, not event
recall or visual understanding on footage. No agent labels or footage claims are
used. No model was downloaded, and no model adoption or default change is made;
therefore cold/warm inference latency, peak memory, and end-to-end inference cost
are not applicable to this scheduling-only addition.

## Research notes

VideoITG-style selection is represented here by query-conditioned selection of
candidate intervals. Tempo-style allocation is represented by spending a fixed
frame budget more densely on informative candidate spans. This is an approach
study only: the module uses Ren's existing measurements and retrieval results,
not either project's weights or code. No external checkpoint is included.

## Limits

The current temporal-index producer provides scene, speech, optical-flow, and
camera-motion measurements. OCR changes and entity transitions are accepted
when supplied by producers but are not fabricated when absent. Camera and
subject motion are separated only when the decomposition explicitly marks its
geometry as reliable; the existing decomposition does not yet emit that
qualification, so the policy does not infer reliability from values alone.
`high_resolution_requested` is a request annotation, not an OCR execution or a
claim that pixels were analyzed at higher resolution. The selector is not yet
wired into the VLM decoder; it is an opt-in schedule for a consumer to execute.
