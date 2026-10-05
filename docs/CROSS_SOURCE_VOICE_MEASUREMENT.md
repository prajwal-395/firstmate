# Cross-source voice matching: measurement specification

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

## 6. Status

Specified, not run. The measurement requires real-footage ECAPA passes
(model inference on real audio), which this task does not run. The code
(`measure_cross_source_voice_separation`, `match_voices_across_sources`)
is implemented and tested; the threshold is `None` until this measurement
is run on real footage.
