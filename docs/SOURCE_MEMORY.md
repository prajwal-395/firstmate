# Per-source footage memory: directory and file contract

Owner: `library/tools/source_memory.py`. Scout basis:
`data/vep-long-footage-memory-scout/report.md` (§3.1, §5 items 1-3 -
in firstmate's home, outside this checkout).

## Root

`PIPELINE_SOURCE_MEMORY_ROOT`, else `vep_home()/source_memory`
(`~/.local/share/vep/source_memory` on this machine). Per machine,
outside every project and checkout. Projects reference it and never
copy it; nothing goes into `pipeline_data.json`.

## Layout

```
<root>/<content_digest>/          # footage_identity.fingerprint
  source.json                    # M0 - written (library/tools/source_primitives.py)
  program.16k.wav                # M0 - the program track, 16 kHz mono s16le
  transcript.words.json          # M1 - written
  speakers.json                  # M1b - diarization lane (RESERVED)
  frames/ + frames.index.json    # M2  - written
  persons.json                   # M3  - written (library/tools/person_measurements.py)
  identity.json                  # M3b - entity lane (library/tools/person_entity.py)
  expressions.json               # M3c - written (library/tools/expression_classifier.py)
  scenes.json                    # M4  - CLIP lane (RESERVED)
  sound.json                     # M5  - SoundAnalysis lane (RESERVED)
  clock.json                     # M6  - written
  events.json                    # M7  - written (library/tools/event_spans.py)
  verdicts.json                  # M8  - written (library/tools/span_verification.py)
  world_model.json               # M9  - written (library/tools/world_model.py)
  relationships.json             # M10 - written (library/tools/relationships.py)
<root>/transcripts/<pcm_sha256>.json   # the canonical transcript measurement
```

A slot no lane has written is ABSENT, never a default. An absent M1
is "not transcribed"; a recorded `untranscribed-*` status is measured
silence or refusal and is served as the answer.

## M0 `source.json` (written)

```json
{
  "content_digest": "sha256 hex",
  "size_bytes": 0,
  "observed_paths": ["<absolute path built from>"],
  "duration_seconds": 0.0,
  "video_streams": [{"index": 0, "codec": "h264", "width": 3840,
                     "height": 2160, "avg_frame_rate": "24000/1001"}],
  "audio_streams": [{"index": 1, "channel": 1, "codec": "pcm_s24le",
                     "channels": 1, "sample_rate": 48000}],
  "measured_levels_db": {"CH1": -20.1},
  "program_track": {"channel": 1, "basis": "declared|single|loudest-live",
                    "measured_levels_db": {"CH1": -20.1}},
  "gop_frames": null,
  "undecodable_audio_streams": [{"channel": 2, "codec": "apple_apac"}],
  "program_declaration": null,
  "program_audio": {"file": "program.16k.wav", "channel": 1,
                    "rate_hz": 16000, "size_bytes": 0,
                    "pcm_sha256": "sha256 hex"}
}
```

M0 and `program.16k.wav` are the source PRIMITIVES
(`library/tools/source_primitives.py`): probed, demuxed, level-measured
and decided once per content digest, then read by M1, M3b and step
1.04 (for a source whose only audio stream is the program) instead of
each decoding the file again. A stream this machine's ffmpeg cannot
decode is listed in `undecodable_audio_streams` and left out rather
than failing the file. A program decision made under a different
`source.program_stream` declaration is rebuilt, not served.

`channel` is the 1-based audio ordinal (`-map 0:a:<channel-1>`).
`program_track.channel` is null with basis `no-audio-streams`,
`no-decodable-audio-streams`, `no-live-track` or `declaration-refused`
when nothing is transcribable (`program_audio.file` is then null). `gop_frames` is null until M2 samples the source, then
filled in from the measured keyframe spacing.

## M1 `transcript.words.json` (written)

```json
{
  "content_digest": "sha256 hex",
  "source_file": "<absolute path>",
  "status": "transcribed",
  "utterance_cut": "hybrid-windows",
  "program_track": {"channel": 1, "basis": "loudest-live", "...": "..."},
  "utterances": [{"start": 0.8, "end": 3.14, "text": "words here",
                  "words": [{"word": "words", "start": 0.8, "end": 0.92}],
                  "confidence": 0.0, "method": "hybrid-mfa"}],
  "utterance_count": 0, "word_count": 0, "speech_seconds": 0.0,
  "instrument": {"arm": "hybrid", "aligner": "mfa",
                 "detected_language": "en", "transcriber": {...},
                 "alignment_window": {...}}
}
```

Utterances carry the same keys as step 1.04 speech regions, so the
search serves either without branching. Text and words are
lowercased; boundaries are MFA's (no onset snapping).
`confidence` is 0.0 - no arm publishes one.

M1 is a PROJECTION of the canonical transcript measurement
(`library/tools/transcript_measurement.py`), not a hearing of its own:
`<root>/transcripts/<pcm_sha256>.json` holds the aligned document and
the hearing's record, keyed by the sha256 of the 16 kHz mono samples.
Step 1.04 writes it from its batched run and the lane serves it, so
`ren analyze` hears each source once; when the program track and 1.04's
extraction are different samples, each is heard once under its own key.

## M2 `frames/` + `frames.index.json` (written)

One shared decode per source: `-skip_frame nokey` drops every frame
but the keyframes before they are decoded, landing at the source's own
GOP cadence (measured 2 Hz on both geo-podcast cameras - 4.7 min per
82-minute file against 76 min for a software 5 Hz full-decode pass,
scout report §2.2, §2.5). `-hwaccel videotoolbox` is tried first on
this machine; a decode that fails with it retries in software. Every
per-frame reader (Vision, CLIP, entity) rides this one sample instead
of decoding its own - `library/tools/analysis/footage_frames.py` is
the first wired consumer.

```json
{
  "content_digest": "sha256 hex",
  "size_bytes": 0,
  "source_file": "<absolute path>",
  "status": "sampled",
  "rate_hz_nominal": 2.0,
  "width": 384,
  "frame_count": 0,
  "frames": [{"file": "frames/frame_000001.jpg", "t": 0.521}],
  "instrument": {"decoder": "iframe-skip", "hwaccel": "videotoolbox|null"},
  "gop_frames": 12
}
```

`frames[].file` is relative to the digest's own memory directory
(`<root>/<content_digest>/`), never to a project - a reader resolves
it with `source_memory.frame_abspath`, the same reference-not-copy
contract M1 follows. `frames[].t` is read off `showinfo`'s own
`pts_time` for that output frame, not computed from a fixed step: the
real cadence is whatever the source's keyframes give.

## M6 `clock.json` (written)

Owner: `library/tools/conversation_clock.py`. One record per source
that landed in a measured multicam group - a source in no group gets
no file, never a zero offset. Groups are MEASURED, never declared:
two sources join a group when unique word 4-grams between their M1
transcripts agree on a near-constant offset for most of the matches
that exist (`MIN_NGRAM_MATCHES`, `MIN_AGREEMENT_FRACTION`).

```json
{
  "content_digest": "sha256 hex",
  "source_file": "<absolute path>",
  "group_id": "sha256 of the sorted member digests",
  "group_members": ["<digest>", "..."],
  "reference_digest": "<the group member with the most transcribed words>",
  "offset_to_reference_seconds": 3.80,
  "path_to_reference": ["<this digest>", "...", "<reference digest>"],
  "direct_measurement": {"ngram_size": 4, "total_candidate_matches": 7471,
                         "agreeing_matches": 7438,
                         "agreement_fraction": 0.9956,
                         "offset_seconds": 3.80, "p5_seconds": 3.76,
                         "p95_seconds": 3.85, "tolerance_seconds": 1.0},
  "resolve_cross_check": {"timeline": "GEO Podcast - Synced",
                          "boundary_count": 63, "offset_seconds": 3.875,
                          "median_absolute_deviation_seconds": 0.1,
                          "agrees_with_ngram_offset": true,
                          "difference_from_ngram_offset_seconds": 0.075},
  "ngram_size": 4, "built_at": "2026-10-01T00:00:00+00:00"
}
```

`this_source_time + offset_to_reference_seconds == reference_time`.
`direct_measurement` is null when the offset is transitive (more than
one hop in `path_to_reference` - e.g. two shorter takes of the same
camera that both overlap a longer reference take but not each other).
The reference member's own record carries `offset_to_reference_seconds:
0.0` and `direct_measurement: null`.

`resolve_cross_check` reads a SAVED `timeline_transcript` output only -
never Resolve, never a new build - and is null when the project has no
such output. It finds adjacent timeline segments from two different
sources whose timeline gap is tight (a real camera-switch cut, not a
coincidental pause) and reads the offset the cut implies; this is
noisier than the n-gram match (a segment boundary is the nearest WORD
to the cut, not the cut itself), so it is reported with its own spread
rather than asserted to match.

Building is read-only and light - no ffmpeg, no model inference, no
heavy-work lock - over whatever M1 transcripts are already fresh:

```sh
python3 -m library.tools.conversation_clock build <project>
python3 -m library.tools.conversation_clock status <project>
```

## M3 `persons.json` (written)

Owner: `library/tools/person_measurements.py`. Apple Vision faces (box,
inner/outer lips) and hands (21 joints) on every M2 frame, through step
1.04's own helper (`vision_measure.measure_frames`, persistence filter
unchanged) - step 1.04 itself is untouched. Measured 22-27 ms/frame on
geo-podcast: 21,771 frames in 9 min for the whole 3.03 h project.

```json
{
  "content_digest": "sha256 hex", "size_bytes": 0,
  "source_file": "<absolute path>", "status": "measured",
  "coordinates": "image-normalised, top-left origin",
  "frames": [{"t": 0.521,
              "faces": [{"box": [x1, y1, x2, y2], "confidence": 0.8,
                         "outer_lips": [[x, y]], "inner_lips": [[x, y]]}],
              "hands": [{"chirality": "right", "confidence": 1.0,
                         "joints": {"VNHLKTTIP": [x, y, confidence]}}]}],
  "instrument": {"method": "vision-helper-v1 over M2", "m2_width": 384,
                 "frame_pixels": [384, 216], "ms_per_frame": 22.6,
                 "faces_removed_by_persistence": 0, "persistence_iou": 0.3}
}
```

**Lips are in IMAGE space, mapped out of their landmark box at write
time.** Vision reports landmark points relative to the face box and hand
joints relative to the image; comparing the two raw is what put the
Vision lane's hand-over-mouth false positives at 99/265 (23/265 mapped).
A pixel distance multiplies x by `frame_pixels[0]` and y by
`frame_pixels[1]` first.

```sh
bin/vep -m library.tools.heavy_work_lock run \
  --owner <lane> -- python3 -m library.tools.person_measurements build <project>
```

## M3b `identity.json` (written)

Owner: `library/tools/person_entity.py`. Measured, not guessed: the
face-match threshold below is the false-accept study (labelled set at
`data/vep-person-entity-store/eval/` in firstmate's home), rerun on the
frames production decodes by `library/tools/face_identity_study.py`:
FAR=0/FRR=0 over 508 faces at 1408x792, 2 cameras, profile and
eyes-closed frames included. At 1280 px, two same-person pairs fall
below the production threshold; at 512 px, 157 do. **ArcFace never
reads the 384 px M2 thumbnails.** Each frame is decoded from source and
scaled to 1408 px before JPEG output, at the nearest M2 I-frame times
when a fresh M2 exists (so M7 can join it to M3), else at its own planned
times (`instrument.frame_source` says which;
`instrument.frame_pixels` is the size the boxes are drawn on).

```json
{
  "content_digest": "sha256 hex",
  "source_file": "<absolute path>",
  "status": "measured",
  "face_match_threshold": 0.25,
  "faces": [{"track_id": "face_001", "embedding": [512 floats],
             "spans": [{"start": 60.0, "end": 60.2, "box": [x1, y1, x2, y2],
                       "det_score": 0.9}]}],
  "voices": [{"track_id": "voice_001", "embedding": [192 floats],
              "spans": [[25.27, 27.36]]}],
  "speech_face_links": [{"t": 61.0, "face_track": "face_001",
                         "voice_track": "voice_001",
                         "basis": "co-occurrence: voice span + face span overlap"}],
  "instrument": {"face": "insightface buffalo_l (ArcFace, 512-d)",
                 "face_unavailable_reason": null,
                 "voice": "speechbrain ECAPA (192-d) via single_track_diarization.diarize_track",
                 "frame_source": "M2-times-source-decode|own-decode",
                 "frame_pixels": [1408, 792],
                 "sample_count": 12, "sample_interval_s": 10.0,
                 "max_samples": 120, "face_input_max_width": 1408,
                 "face_model_pack": "buffalo_l",
                 "face_cache_key": "sha256 hex"}
}
```

Faces and voices are tracks WITHIN this one source, clustered by cosine
similarity (face: `FACE_MATCH_THRESHOLD`; voice reuses the already-landed
diarization clusters outright, PR #1482). `status` is `measured`,
`no-video-stream`, `no-faces-detected` or `face-identity-unavailable`
(insightface unreachable) - never a default where nothing was measured.
The face tracks are reused when `face_cache_key` still matches the source,
M2 timestamps, threshold, sampling policy, input width and model pack.
Changing one of those inputs rebuilds faces. Voice diarization still runs
on each build.

**Cross-source person identity is face-only.** `person_entity.
resolve_person_tracks` unifies face tracks across every source digest a
project's catalog references, by the same measured threshold. Voice
embeddings are NOT matched across sources - only the diarization DER
(separating voices WITHIN one file) is proven; porting that into a
cross-file identity claim would be exactly the unmeasured leap the rigor
gate exists to catch. A source's voice spans travel with whichever
person their speech-face link (within that same source) attaches them
  to. `ren search --person <name-or-id>` and `filter --person` read this
  resolution; a project may declare `source.person_names: {person_001:
  "Craig"}` to give a measured cluster a human name (a label, never a
  substitute for the embedding match that assigned the id).

## M3c `expressions.json` (written)

Owner: `library/tools/expression_classifier.py`. A lightweight expression
classifier on M3 face crops. Reads M3 face bounding boxes and lip
landmarks, extracts face crops from source video at the M2 cadence,
classifies expressions, and writes expression labels to this slot.

The classifier uses geometric features from M3 lip landmarks (mouth
curvature, openness) and image features from the face crop (edge density
in eye/nose regions). It outputs one of seven expressions: neutral, happy,
sad, angry, surprised, fearful, disgusted.

```json
{
  "content_digest": "sha256 hex",
  "source_file": "<absolute path>",
  "status": "measured",
  "frame_count": 100,
  "faces_total": 500,
  "faces_classified": 480,
  "expression_counts": {"neutral": 300, "happy": 120, "surprised": 60},
  "frames": [{"t": 0.521,
               "faces": [{"box": [x1, y1, x2, y2],
                          "expression": "happy", "confidence": 0.85,
                          "features": {"mouth_curvature": 0.03,
                                       "mouth_openness": 0.1,
                                       "eye_edge_density": 0.05,
                                       "nose_edge_density": 0.02}}]}],
  "instrument": {"method": "expression-classifier-v1",
                 "expressions": ["neutral", "happy", "sad", "angry",
                                 "surprised", "fearful", "disgusted"],
                 "thresholds": {"smile_curvature": 0.015,
                                "frown_curvature": -0.015,
                                "surprise_openness": 0.35,
                                "anger_eye_density": 0.15,
                                "fear_eye_density": 0.20,
                                "disgust_nose_density": 0.18},
                 "crop_padding": 0.2, "crop_min_size": 32,
                 "ms_per_face": 1.2}
}
```

**Real-footage measurement.** The classifier is wired and tested on
fixtures. Running it on real footage requires: (1) a fresh M3
(`python3 -m library.tools.person_measurements build`), (2) the heavy-work
lock (`bin/vep -m library.tools.heavy_work_lock run --owner expressions`),
and (3) ffmpeg for face crop extraction. The measurement is per-frame at
the M2 cadence (~2 Hz), classifying every face in every sampled frame.

```sh
bin/vep -m library.tools.heavy_work_lock run \
  --owner expressions -- python3 -m library.tools.expression_classifier build <project>
python3 -m library.tools.expression_classifier status <project>
```

## M7 `events.json` (written)

Owner: `library/tools/event_spans.py`. Light: built from M3 + M3b, no
decode, no model, no lock.

```json
{
  "content_digest": "sha256 hex", "source_file": "<absolute path>",
  "status": "built", "faces_assigned": 8149, "faces_unassigned": 43,
  "predicates": {
    "on_screen": {"basis": "...",
                  "spans": [{"face_track": "face_001", "start": 0.0, "end": 12.5,
                             "frames": 25}]},
    "speaking":  {"basis": "...",
                  "voice_face_links": [{"voice_track": "voice_001",
                                        "face_track": "face_001",
                                        "evidence": [{"face_track": "face_001",
                                                      "motion_ratio": 2.1}]}],
                  "voice_unavailable_reason": null,
                  "spans": [{"voice_track": "voice_001", "face_track": "face_001",
                             "start": 25.27, "end": 27.36}]}
  },
  "candidates": {
    "hand_near_mouth": {"basis": "...",
                        "spans": [{"face_track": "face_001", "start": 553.6,
                                   "end": 557.4,
                                   "frames": [{"index": 1108, "t": 554.1,
                                               "d": 0.02, "box": [x1, y1, x2, y2]}]}]}
  }
}
```

- `on_screen`: M3 faces assigned to an M3b ArcFace track by position
  (the time-adjacent M3b observation whose face sits where this one
  does); a face nothing vouches for is unassigned, never guessed.
- `speaking`: an M3b voice turn belongs to the face whose LIPS move
  during that voice's turns and not outside them. M3b's own
  `speech_face_links` (co-occurrence) attach every voice to the only face
  on a one-person angle - the listener is on screen too. `face_track:
  null` is a voice no face on this angle speaks with (off-screen here).
- `hand_near_mouth` is NOT a predicate: as a rule it recalled 11/17
  events against a 0.80 bar (`data/vep-structured-footage-query/eval/
  results.md` in firstmate's home), and asked for alone it REFUSES. It
  is recorded under `candidates` instead: frames whose nearest hand
  joint is under `HAND_CANDIDATE_MAX_DISTANCE` (1.5) face widths from
  the lips, with each frame's distance `d` and face box - a looser cutoff
  that reaches 16/17 events at 257 spans on the Craig angles, for the
  VLM to verify (M8). Candidates are never an answer.
- `hand_raise`, `nod`, `object_pickup`, `person_enters` and
  `person_leaves` are candidates too, each a cheap geometric rule over
  M3 hand/face geometry or M3b face-track runs: a hand risen
  `HAND_RAISE_MIN_RISE` face heights within `HAND_RAISE_WINDOW` frames
  and ending above the face's centre; a face centre's vertical
  peak-to-peak displacement of `NOD_MIN_DISPLACEMENT` face heights that
  reverses direction; a hand risen `PICKUP_MIN_RISE` face heights from
  below the face's bottom to above it; a face track's first frames (with
  the frame before them) and last frames (with the frame after them).
  The thresholds are geometric priors calibrated on the Craig angles'
  geo-podcast M3 (`data/vep-structured-footage-query/eval/diag_720_m3.json`
  in firstmate's home), not a measured recall bar - the VLM disposes.
  Motion and boundary spans carry `d` relative to the transition so the
  verifier is shown the transition, not only its end state.

```sh
python3 -m library.tools.event_spans build <project>
python3 -m library.tools.event_spans names <project>   # derived names + evidence
ren search <project> --person Craig --predicate speaking [--json]
```

The query: person (person_id, declared `source.person_names`, or a name
DERIVED from the timeline transcript's per-speaker tracks that the
person's attributed speech overlaps, agreement recorded) -> that
person's M7 spans on every source -> the same moments on every other
angle of the multicam group (M6) -> the `timeline_transcript` items that
play them (constant source-to-timeline offset, inside the item's
RECORDED source extent). A span outside every recorded extent is
`unplaced` - the transcript records speech, and an item can run past
its last word.

## M8 `verdicts.json` (written)

Owner: `library/tools/span_verification.py`. The local VLM's verdicts on
candidate spans - never on a whole episode. One VLM call per span: up to
`MAX_FRAMES_PER_SPAN` (4) whole M2 frames (all of a short span, else the
four with the smallest candidate distance), a fixed prompt
(`PROMPT_VERSION`) stating the caller's statement about "the person".
The statement may describe a state (a hand near the mouth) or an event (a
person entering); v2 of the prompt asks about the sequence of frames
too, because an event is a transition no single frame contains.

```json
{"content_digest": "sha256 hex",
 "verdicts": {"<key>": {"answer": "yes|no|unparsed", "frame": 2,
                        "frame_t": 554.6, "reason": "...",
                        "frames": [554.1, 554.6], "statement": "covers his mouth with his hand",
                        "model": "mlx-community/gemma-4-12b-it-4bit",
                        "prompt_version": 1, "seconds": 4.1, "reply": "<raw>"}}}
```

The key hashes prompt version, model, statement (lowercased) and the
frame times shown, so asking the same question again costs nothing and
any change to what was asked is a new verdict. `unparsed` is a reply with
no JSON yes/no and counts as NOT verified. New verdicts take the heavy-work
lock; an answer made wholly of recorded verdicts takes none.

Measured (Craig angles, 17 labelled events, "covers his mouth with his
hand"): 14/17 events, span precision 14/15, 257 calls in 17.3 min for
1.51 h of footage (0.19x real time), identical verdicts on a rerun. A
person crop instead of the whole frame recalled 11/17. A span longer than
four frames is shown through its four closest frames and can miss the
moment. `data/vep-query-hit-verification/eval/` in firstmate's home.

```sh
ren search <project> --person Craig --predicate hand_near_mouth \
  --verify "covers his mouth with his hand" [--json]
```

Hits are the YES spans, placed on the timeline like any other (below),
each carrying its `verification` (answer, reason, frames, model); the
NO spans come back as `rejected` with their reasons. `--verify` with a
predicate that is not a candidate generator (on_screen, speaking)
REFUSES: those spans run the length of the takes.

## M10 `relationships.json` (written)

Owner: `library/tools/relationships.py`. Light: built from M3 + M3b, no
decode, no model, no lock. Gap 3 + Gap 7 of the world-model gap map
(`data/vep-gapmap-a-world-model/report.md` in firstmate's home): the
review's `Relationships` level - "person holding object, looking at
person, referring to visible object".

```json
{
  "content_digest": "sha256 hex", "source_file": "<absolute path>",
  "status": "built", "frame_count": 10873,
  "relationships": {
    "holding": {"basis": "...",
                "spans": [{"owners": ["face_001", "face_001"],
                           "start": 12.0, "end": 14.5,
                           "frames": [{"index": 24, "t": 12.5, "d": 0.12,
                                       "hands": ["right", "left"]}]}]},
    "pointing": {"basis": "...",
                 "spans": [{"owners": ["face_001", "face_002"],
                            "start": 40.25, "end": 41.75,
                            "frames": [{"index": 80, "t": 40.5,
                                        "angle": 4.2, "extension": 0.41,
                                        "hand": "right"}]}]}
  },
  "unmeasured": {"looking_at": "<the refusal, verbatim>"}
}
```

- `holding` - a confident joint of one hand within
  `HOLDING_MAX_JOINT_DISTANCE` (0.5) face widths of another hand's, for
  at least `HOLDING_MIN_FRAMES` (2) frames. The review's form is "hand
  joints near OBJECT box"; M3 carries no object boxes (nothing tracks
  objects yet), so the detector measures the other half of every hold:
  the two hands that meet - a clasp, a handshake, a handoff, a
  two-handed grip. The two hands may share an owner.
- `pointing` - a hand's wrist->fingertip ray within
  `POINTING_MAX_ANGLE_DEG` (25) of another tracked face's centre, the
  hand extended at least `POINTING_MIN_HAND_EXTENSION` (0.3) face
  widths past its wrist, for at least `POINTING_MIN_FRAMES` (2) frames.
  A fist does not point; pointing at one's own face is M7's
  hand-at-mouth rule, not a relationship.
- Both are geometric CANDIDATES for VLM verification, never answers -
  the same standing decision as M7's `hand_near_mouth`. Each frame
  carries its measured distance (`d`) or angle and extension so a
  verifier can judge the span without re-reading M3.
- `looking_at` is REFUSED BY NAME under `unmeasured`, with the reason:
  the review's detector is "face orientation (from landmarks) toward
  another face's centre", and M3 persists no orientation -
  `person_measurements.frame_record` writes only the lip regions of the
  helper's landmark observations (the helper measures
  leftEye/rightEye/nose/faceContour; they are dropped at persist time),
  and an axis-aligned face box carries no facing direction. The unlock
  is persisting the eye landmarks in M3.
- Who is who: faces take M3b ArcFace tracks by position
  (`event_spans.assign_face_tracks`); a hand belongs to the tracked
  face its wrist is nearest. A hand or target no track claims is left
  out of named relationships, not guessed into one.
- Distances are measured in pixels (x scales by the frame width, y by
  its height; the unit is the frame's mean face width) - the
  coordinate contract `person_measurements` documents. A frame with no
  face carries no scale, and a distance without a scale is not a
  measurement.

```sh
python3 -m library.tools.relationships build <project>
python3 -m library.tools.relationships status <project>
```

The build refuses (raises) without M3 or M3b, per source, and reports
the failure without aborting the project - an unbuilt source would
read as "no relationships here".

## Staleness

A record is fresh when the file on disk still fingerprints to the
record's `content_digest` + `size_bytes` (content, not mtime). The
index half reuses the search's own check: `memory_fingerprint` is
mixed into `footage_segments.ingest_fingerprint`, so `ren
search-index` / `ren search ... stale` report stale when a transcript
lands or a source changes.

## Building

```sh
# Heavy: full-file demux + voz + MFA. Under the heavy-work lock.
bin/vep -m library.tools.heavy_work_lock run \
  --owner <lane> -- python3 -m library.tools.source_memory build <project>

# Heavy: one whole-file I-frame decode per source. Under the heavy-work lock.
bin/vep -m library.tools.heavy_work_lock run \
  --owner <lane> -- python3 -m library.tools.source_memory frames <project>

python3 -m library.tools.source_memory status <project>   # light, reads only
python3 -m library.tools.source_memory build <project> --clip clip_003
python3 -m library.tools.source_memory frames <project> --clip clip_003
```

`build` reuses a fresh transcript (`reused: true`) and `frames` reuses
a fresh M2 sample the same way; both record per-file failures without
aborting the project. `PIPELINE_SOURCE_MEMORY_ROOT` overrides the root
(`--memory-root` per run, e.g. for tests).

M3b builds the same way, under its own module. Run `source_memory
frames` first (or let `person_entity build` fall back to its own
decode) so the entity lane reads the shared M2 sample instead of paying
for a second decode:

```sh
# Heavy: buffalo_l + ECAPA, reading M2's frames when fresh. Under the lock.
bin/vep -m library.tools.heavy_work_lock run \
  --owner <lane> -- python3 -m library.tools.person_entity build <project>

python3 -m library.tools.person_entity roster <project>   # resolved persons
python3 -m library.tools.person_entity find <project> "Craig"
```

## Reading (search)

`footage_segments.build_segments` serves fresh M1 utterances ahead of
the 1.04 regions for the same clip - never both. Digests resolve live
off disk when media is present, off the runner's `source_fingerprints`
record when it is not, so search works with footage offline.

`footage_frames.build_frame_index` reads M2 the same way: a clip with
no fresh sample is SKIPPED with the `frames` command to run, never
decoded on the spot - the frame-level CLIP index is a reader of the
memory, not a second producer of it.

`ren search --person <name-or-id>` / `ren search filter --person
<name-or-id>` read M3b through `person_entity.resolve_person_tracks` the
same way, restricting results to that person's measured face or voice
spans. `ren search <project> --person <name> --predicate <p>` is the
structured query over M7 (above): no text, no index, no model.

## The analysis-only run, its export and its eval

Owners: `library/tools/footage_analysis.py` (`ren analyze`),
`library/tools/memory_export.py` (`ren export-memory`),
`library/tools/retrieval_eval.py` (`ren eval-search`). Basis: the
separable-product report's recommendation (`data/vep-phase1-separable-product/report.md`
in firstmate's home); measured numbers in
`data/vep-footage-intelligence-proof-build/eval.md` there.

```sh
# Heavy. A bare folder becomes a collection project over it (footage read in place).
ren analyze <folder-of-footage | project> [--into DIR] [--memory-only] [--with CAP] [--skip CAP]
ren analyze <project> --status            # light: per-source lane freshness
ren export-memory <project>               # reads only
ren eval-search <project> --out-json F    # the pre-registered set by default
```

- **Two halves.** The analysis capabilities run through footage
  intelligence's own orchestration (`library/tools/footage_intelligence.py`),
  never the editing runner: `footage.scan`, `footage.catalog`,
  `semantics.analyse`, `temporal.index`, `prosody.analyse`, composed by what
  each requires - never object segmentation, OCR only with `--with
  ocr.extract`. Results land in `capability_outputs` and the preflight ledger, so
  `ren edit` continues with preflight done. Then the lanes in
  `footage_analysis.LANES` fill M0-M3b, M6, M7 and build the text and frame
  indexes; M9 (`world_model.json`) is built by `world_model.py` and
  M10 (`relationships.json`) by `relationships.py`, both light
  M3 + M3b, light, and is not a lane yet. `--memory-only` runs scan and
  catalog alone.
- **A fresh record is reused, never rebuilt**: a per-source lane runs only
  for sources whose slot is missing, names another digest, or is older than
  a slot it reads (an M3 measured off an M2 that was since re-sampled).
- **`analysis_run.json`** (`pipeline_output/footage_memory/`) records, per
  lane and per source, `built` / `reused` / `skipped` (why) / `failed` (the
  error), with seconds per built video minute.
- **The export** (`footage_memory.v1.json`, schema `ren.footage-memory`) keys
  every asset by `sha256:<content digest>` and carries each slot's fields,
  time ranges, provenance (writer module, instrument, model, built time),
  coverage, and per-asset `complete` / `partial` / `failed` with the run's
  reason. Absolute paths go ONLY to `footage_memory.local.json`;
  `assert_portable` refuses to write an export any string of which carries
  a local path. Embeddings are withheld unless `--include-embeddings`.
- **The eval set** (`library/tools/retrieval_eval_set.json`) was committed
  before any of its queries were scored; changing a query or a span is a new
  set, not an edit.
