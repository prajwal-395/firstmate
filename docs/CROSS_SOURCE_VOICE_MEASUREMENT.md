# Cross-source voice matching: measurement and result

2026-10-05. The gap: voice diarization is within-source only
(`person_entity.py` measures ECAPA embeddings per-source and clusters
per-source). Cross-source person identity is face-only. The question:
can ECAPA embeddings be matched across sources to track a person's
voice across all cameras?

The within-source diarization proof (PR #1482, DER 0.017) measures
separating voices WITHIN one file. Whether that separation holds ACROSS
files - different microphones, different rooms, different camera
positions - is an open question. This document specifies the measurement
that answers it.

## 1. What is measured

Whether same-person ECAPA voice embeddings from different sources
separate from different-person embeddings. The measurement computes:

- **Same-person distribution**: cosine similarity between ECAPA
  embeddings of the same person's voice measured in different sources
- **Different-person distribution**: cosine similarity between ECAPA
  embeddings of different people's voices across sources

If the distributions separate (a threshold exists with precision >= 0.90
and recall >= 0.80), cross-source voice matching is viable. If they
overlap, the face-only cross-source identity is the answer.

## 2. What footage is needed

- At least 2 sources where the same person speaks in both (e.g., a
  two-camera interview where both cameras carry the same speaker's
  voice on their program tracks)
- At least 2 different people (to measure the different-person
  distribution)
- Each source's voice tracks must be diarized by the existing
  `measure_voice_tracks` (ECAPA via `single_track_diarization`)
- The cross-source voice pairs must be labelled: for each pair of voice
  tracks from different sources, record whether they are the same person

## 3. Measurement procedure

1. Run `measure_voice_tracks` on each source to get per-source voice
   tracks with 192-d ECAPA centroid embeddings
2. Label the cross-source pairs: for each pair of voice tracks from
   different sources, record `(embedding_a, embedding_b, same_person)`
3. Call `person_entity.measure_cross_source_voice_separation(labelled_pairs)`
4. The function returns:
   - `same_person_distances`: cosine similarities for same-person pairs
   - `different_person_distances`: cosine similarities for different-person pairs
   - `separates`: whether a threshold achieves precision >= 0.90 and recall >= 0.80
   - `threshold`: the measured threshold (if `separates` is True)
   - `precision_at_threshold` / `recall_at_threshold`: the measured values

## 4. Decision criteria

- If `separates` is True: set `VOICE_MATCH_THRESHOLD` to the measured
  threshold and integrate `match_voices_across_sources` into
  `resolve_person_tracks` so voice tracks merge across sources
- If `separates` is False: the face-only cross-source identity is the
  answer. Record the distributions and the best achievable precision/recall
  in this document as the measurement that ruled out cross-source voice
  matching

## 5. Why precision >= 0.90

Voice matching needs high precision because a false accept merges two
people's speech into one person's track - a silent corruption of the
person's whole span history. A false reject leaves two voice tracks for
one person, which is visible and recoverable in search results. The
asymmetry matches the face-identity study's bias toward FAR over FRR
(`FACE_MATCH_THRESHOLD` docstring).

## 6. Result (2026-10-12): it separates, and matching is ON

Run by `library/tools/voice_identity_study.py`, which takes its voice
tracks from M3b (the centroids `match_voices_across_sources` compares)
and its windows from production's own VAD and ECAPA. Reproduce:

    bin/vep -m library.tools.heavy_work_lock run --owner voice-study -- \
      bin/vep -m library.tools.voice_identity_study run manifest.json --out report.json

**Footage (12 sources, 3 people, labels from physical tracks):**

- geo-podcast, both cameras: `LC4930/31/32.MXF` (Akshita's camera, her
  lav on the program track) and `LCATL0011-14.MXF` (Craig's camera and
  lav). Labels come from the multitrack timeline transcript
  (`pipeline_output/scratch/timeline_transcript/transcript.json`), whose
  speakers are the ISO tracks. A segment labels its own camera, and the
  OTHER camera through the timeline's own sync: where the edit cuts
  between cameras, the two items' offsets differ by the sync delta
  (measured: LC4932 to LCATL0013 -3.795 s over 52 agreeing cuts,
  LC4931 to LCATL0012 -0.292 s, LC4930 to LCATL0011 -11.366 s, LC4932
  to LCATL0014 -4103.412 s). That is Craig heard through Akshita's lav -
  the cross-microphone case.
- The captain's phone takes `post a day keeps the apple away/001/raw/
  IMG_1816/17/18/20/22.MOV` (a different person, room and microphone),
  each labelled whole as the captain - the E5 third voice of the
  diarization eval, chosen by the same rule (>= 40 s, speech fraction
  >= 0.25 in `e5_probe.txt`).

Diarized on production's path: 10 podcast tracks label at purity
>= 0.995 (on Craig's camera Akshita's quieter bleed never forms its own
cluster; on hers, Craig does - `LC4932:voice_002`, 151 s). 8 tracks
under 3 s of labelled speech are excluded and named in the report.

**Track arm** (17 tracks, 43 same-person / 88 different-person
cross-source pairs): FAR is 0 at every threshold >= 0.30 (closest
different-person 0.2925, `LC4930:voice_001` Craig vs `IMG_1817` captain).
All 8 same-person rejects involve two diarizer fragments of 4 s and 6 s
(`IMG_1817:voice_002`, `IMG_1820:voice_003`).

**Tracks >= 21 s** (15 tracks, 34 / 68 pairs): FAR=0 and FRR=0 at every
threshold from 0.30 to 0.65. Weakest same-person 0.6954, closest
different-person 0.2925. Between the podcast's two people, the gap is
wider still. Craig on his own lav against Craig through Akshita's lav
scores >= 0.848, and Craig against Akshita scores <= 0.054.

**Chunk arm**, kept windows inside one person's labelled spans,
averaged in runs:

| chunk | items | same / diff pairs | closest diff | weakest same | FAR=0 from | FRR at 0.45 |
|---|---|---|---|---|---|---|
| 4 windows (~3 s) | 1056 | 48393 / 298480 | 0.3874 | -0.1002 | 0.40 | 0.276 |
| 10 windows (~6 s) | 418 | 7412 / 46422 | 0.3269 | 0.0402 | 0.35 | 0.117 |
| 40 windows (>= 21 s) | 98 | 303 / 2436 | 0.2369 | 0.5011 | 0.30 | 0 |

`measure_cross_source_voice_separation` over the track pairs:
`separates: True` (precision 0.90, recall 0.84 at 0.253).

**Decision**, made the way the face study made it (FAR over FRR: a false
merge corrupts a person's whole speech history, while a miss leaves a
voice unattributed, which is visible and recoverable):

- `VOICE_MATCH_THRESHOLD = 0.45`. Tracks of at least the minimum length
  pass FAR=0/FRR=0 anywhere in 0.30-0.65. 0.45 also keeps FAR at 0 for
  3 s chunks (closest 0.3874), so a short track that gets past the
  length gate still cannot false-merge.
- `VOICE_MATCH_MIN_SECONDS = 21.0`. Every miss was a fragment under
  6 s, and the 40-window arm (>= 21 s) is clean. A shorter track stays
  where its own source's speech-face link put it.
- `person_entity.resolve_person_tracks` calls `match_voices_across_sources`
  through `join_voices_across_sources`. A voice track that no face in
  its own source claims joins the person whose linked voice it matches.
  A match never merges two persons and never moves a linked voice: a
  group touching two persons joins nobody and is listed in
  `voice_match_conflicts`. The project model carries each join as a
  `cross_source_voice_match` identity link.

**Limits, stated rather than tuned around:**

- Three voices: two men and one woman in one room, plus one man on a
  phone. The different-person distribution has not seen two similar
  voices, such as same-sex siblings. Rerun the study when a project
  brings more voices, with the same manifest shape.
- The captain's takes carry whole-file labels, so the very low
  same-person chunk scores (-0.10 at 4 windows) are probably non-voice
  sound in those clips, not his voice. That label noise counts against
  FRR, never FAR.
- Same-person across rooms and microphones was measured for the
  captain's takes and for Craig across two lavs. It has not been
  measured for one person across two different rooms on lavs.
- A voice-only person (matched voices that no face claims) is NOT
  minted. That group has no person to join, so it stays out of the
  roster as before.
