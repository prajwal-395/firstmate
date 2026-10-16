# Multimodal agent label agreement

This is a calibration-only comparison for the stable-person and
visible-to-audible-speaker fields in the frozen multimodal evaluation set.
The two blind submissions are agent labels from different model families
(OpenAI/Codex and OpenCode/Muse Spark). They are not human labels or ground
truth. Held-out windows were excluded from this comparison and remain sealed
from tuning.

## Matching rule

Before scoring, annotator-specific identity IDs were ignored and matched only
within the same source window by the visible description and the interval
being asserted. Person-presence agreement is exact agreement on whether any
person is present. Visible-interval agreement requires the intersection over
union of the union of each annotator's visible intervals to be at least 0.8.
Speaker-association agreement requires exact agreement on the associated
entity versus `unknown`; confidence wording is not treated as a probability.
No cross-window identity match is inferred from IDs alone.

## Interim results

The calibration subset contains 11 source windows. Before tie-break, presence
agreed on 9/11 (81.8%) and visible-interval agreement at the stated threshold
was 6/11 (54.5%). The two podcast windows disagreed on exact speaker
association (0/2). A third independent agent (Anthropic Claude Sonnet 5.5)
reviewed only the six disputed calibration windows. On IMG_1806, IMG_1811,
IMG_1809, and IMG_1810, the tie-break supports no person, no person, an edge
hand, and a ringed hand at the dashboard, respectively; the two initial
full-window identity claims in these four windows are not retained. In
LCATL0012, the tie-break supports the man as visible throughout, plus
unidentified partial hands, and associates the audible speech with the man.
In LC4931, it supports the woman throughout plus a partial hand at the left
edge; early speech and “Correct” remain unknown, while the later speech is
associated with the woman. These are adjudicated agent labels, with the
tie-breaker's sampling and audio limitations recorded in its report.

## Existing podcast baseline packet (P1-P4)

The same blind pair also labelled the 74-record baseline packet (16 P1 prompt
and source pairs, 12 P2 stills, 40 P3 frames, and 6 P4 transcripts); a third
independent Claude Sonnet 5.5 pass supplied the tie-break records. For P1,
binary presence status agreed on 14/16 pairs. The tie-break resolved the two
disputes in favour of labeller B, yielding a unanimous adjudicated status on
all 16 pairs. This was visible prompt evidence on the two camera views, not a
general object-detection accuracy estimate.

P2 and P3 are sparse still/frame annotations with incompatible class
vocabularies. Using a
coarse, declared alias map (person, plant, laptop, table, camera, chair,
glasses, lamp, wall; unmatched labels retain their normalized text), the
pairwise per-record class-set Jaccard was 0.536 across 12 P2 stills and 0.521
across 40 P3 frames. As a separate localization diagnostic, each labeller-A
box was compared with the highest-IoU labeller-B box in the same coarse class
and frame: mean best IoU was 0.223 for P2 (12/55 boxes at IoU >= 0.5) and 0.223
for P3 (40/155 at IoU >= 0.5). This permissive one-way matching does not
penalize unmatched B boxes and is not an accuracy score. The third-pass
objects and links are retained as adjudication evidence; no held-out quality
claim is made from these figures.

P4 transcript text matched exactly on five of six 150-second sources. The
remaining source (LC4932) differed by one word in 419 words; aggregate
normalized edit rate is 1/3,789 words (0.026%). Word timing comes from the same
Voz transcript family and this text comparison does not validate timing or
audibility. Speaker labels did not agree as encoded: labeller A used one
speaker ID throughout, while B used two. The third pass used pitch-separated
speaker labels and explicitly flags pitch-only identity, quiet-track
transcription, and overlap limitations. Treat the diarization as uncertain
agent annotation, not verified identity.

These are inter-annotator agreement and adjudication results, not model
accuracy or editorial-quality measurements. Held-out labels and windows were
not included in this report and remain reserved for one final report after
calibration is frozen.

## Provenance

The source packets and submissions are retained outside the repository under
Firstmate task data for `vep-mu-harness`. The harness tie-break request contains
only the six disputed calibration source windows, task questions, and output
schema; it contains no prior labels, predictions, or Ren outputs. The request
is at
`/Users/prajwal/.treehouse/firstmate-8bf1b0/3/firstmate/data/vep-mu-harness/tiebreak-request.md`.
