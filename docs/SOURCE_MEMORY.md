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
  speakers.json                  # M1b - written (library/tools/person_entity.py,
                                 #   via the identity lane)
  frames/ + frames.index.json    # M2  - written
  persons.json                   # M3  - written (library/tools/person_measurements.py)
  identity.json                  # M3b - entity lane (library/tools/person_entity.py)
  expressions.json               # M3c - written (library/tools/expression_classifier.py)
  regions.json + regions.masks.<sha256>.json.gz
                                 # M3d - written (library/tools/person_regions.py)
  saliency.json                  # M3e - written (library/tools/attention_saliency.py)
  region_stats.json              # M3f - written (library/tools/region_stats.py)
  depth.json                     # M3g - written (library/tools/depth.py)
  scenes.json                    # M4  - written (step 1.03 via write_scene_memory in
                                 #   library/tools/analysis/vision_pipeline_v3.py)
  scene_cinematography.json      # VI-09 - measured scene/shot hierarchy, intervals and attributes
  sound.json                     # M5  - written (step 1.04 via write_sound_memory in
                                 #   library/steps/step_1_04_temporal_index/step.py)
  clock.json                     # M6  - written
  events.json                    # M7  - written (library/tools/event_spans.py)
  verdicts.json                  # M8  - written (library/tools/span_verification.py)
  world_model.json               # M9  - written (library/tools/world_model.py,
                                 #   via its own build command, not a ren analyze lane)
  relationships.json             # M10 - written (library/tools/relationships.py,
                                 #   the `relationships` lane of ren analyze)
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
  "frames": [{"file": "frames/frame_000001.jpg", "t": 0.521,
              "pts": 0.520833, "frame_index": 15}],
  "timebase": {"fps_num": 30, "fps_den": 1,
               "time_base_num": 1, "time_base_den": 15360,
               "start_time": 0.0, "total_frames": 1440,
               "rate_basis": "avg_frame_rate"},
  "frame_index_status": "measured",
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

**Every sampled frame carries the SOURCE FRAME INDEX of the original,
and the sample carries the source's own timebase.** `frames[].pts` is
the measured timestamp in full (six decimals - `t` stays at three for
the readers that compare it), `frames[].frame_index` is the frame of
the SOURCE that the sample holds, and `timebase` is the container's
exact rational rate, its time base, where frame 0 sits and how many
frames there are. The index is computed on the container's TICKS
(`library/tools/frame_exact`), never through a rounded second: at 30
fps, `t = 1.267` is frame 38 and a float would say 38.01.

`frame_index_status` says where the index came from. A source whose
rate the container never declared gets NO index - `timebase` is null,
`frame_index_status` names the reason, and no frame carries an index
that was invented from a default.

```sh
bin/vep -m library.tools.frame_exact refine <source> \
  --frame-index <coarse frame index> --half-window 0.5 --out <dir>
```

That is the zoom-in: the coarse sample says WHERE to look, and the
refinement says WHICH FRAME. See `docs/FRAME_EXACT_MEASURED.md`.

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
head-pose yaw/pitch/roll, face capture quality, inner/outer lips) and
hands (21 joints) on every M2 frame, through the shared Vision helper
(`library/tools/vision_measure.measure_frames`, persistence filter
included). This is the one per-frame face record: step 1.04 ran a second
5 Hz Vision pass of its own whose `vision_*` keys nothing read, and that
pass is gone (MU-12). Measured 22-27 ms/frame on
geo-podcast: 21,771 frames in 9 min for the whole 3.03 h project
(pre-quality; the quality request adds ~17-27 ms/frame warm).

```json
{
  "content_digest": "sha256 hex", "size_bytes": 0,
  "source_file": "<absolute path>", "status": "measured",
  "coordinates": "image-normalised, top-left origin",
  "frames": [{"t": 0.521, "frame_index": 15,
              "faces": [{"box": [x1, y1, x2, y2], "confidence": 0.8,
                         "pose": {"yaw": 0.25, "pitch": 0.21, "roll": 0.01},
                         "capture_quality": 0.082,
                         "outer_lips": [[x, y]], "inner_lips": [[x, y]]}],
              "hands": [{"chirality": "right", "confidence": 1.0,
                         "joints": {"VNHLKTTIP": [x, y, confidence]}}]}],
  "instrument": {"method": "vision-helper-v2 over M2", "m2_width": 384,
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

**Each M3 frame carries the source frame index of the M2 frame it
measured** (`frame_index`), so a reader of M3 can name the frame of the
original without converting the time again. It is null when the M2
sample that fed it carried no index - a source whose rate was never
measured - and never a number invented to fill the gap.

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

**Cross-source persons are FACES; a voice can only JOIN one.** `person_entity.
resolve_person_tracks` unifies face tracks across every source digest a
project's catalog references, by the same measured threshold. A source's
voice spans travel with whichever person their speech-face link (within
that same source) attaches them to. A voice track no face in its own
source claims joins a person when it matches, across sources, a voice
that person's face IS linked to - ECAPA cosine >= `VOICE_MATCH_THRESHOLD`
on tracks of >= `VOICE_MATCH_MIN_SECONDS`, both measured by
`library/tools/voice_identity_study.py`
([`CROSS_SOURCE_VOICE_MEASUREMENT.md`](CROSS_SOURCE_VOICE_MEASUREMENT.md)).
A voice match never merges two persons: a match group touching two is
listed in `voice_match_conflicts` and joins nobody.
`ren search --person <name-or-id>` and `filter --person` read this
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

## M3d `regions.json` + mask sidecar (written)

Owner: `library/tools/person_regions.py` (the module docstring holds
every design decision and its measurement). Apple Vision's
`VNGeneratePersonInstanceMaskRequest`, through
`library/tools/person_regions_helper.swift`, at every M2 sample time.
The model is fed a 1152 px I-frame decode of its own, not the 384 px
M2 thumbnail: at 384 px the masks swallow the glass table's reflection
and drop the subject on some samples. Masks are STORED on the M2 grid.

```json
{
  "content_digest": "sha256 hex",
  "status": "measured",
  "coordinates": "image-normalised, top-left origin",
  "grid": [384, 216],
  "sidecar": {"file": "regions.masks.<sha256>.json.gz", "sha256": "...",
              "codec": "runs-rowmajor-v1", "bytes": 1556905, "masks": 12007},
  "faces": {"with_face_track": 8145, "inside_exactly_one_instance": 8116,
            "inside_no_instance": 29, "inside_several_instances": 0,
            "fraction_inside_exactly_one": 0.9964},
  "tracks": [{"track_id": "region_001", "start": 0.083, "end": 59.6,
              "samples": 120, "face_track": "face_001",
              "face_track_fraction": 0.98},
             {"track_id": "region_002", "start": 60.1, "end": 61.1,
              "samples": 3, "face_track": null,
              "unassigned": "no_face_inside"}],
  "samples": [{"t": 0.083, "instances": [
      {"track": "region_001", "bbox": [x1, y1, x2, y2],
       "centroid": [x, y], "interior_point": [x, y], "area": 0.09,
       "mask": 0, "face": 0, "face_track": "face_001",
       "containment": 0.97}]}],
  "instrument": {"method": "person-instance-mask-v1", "decode_width": 1152,
                 "face_inside_min_fraction": 0.5,
                 "track_link_min_iou": 0.2,
                 "decode_seconds": 239.1, "vision_seconds": 220.5,
                 "ms_per_frame": 26.9, "wall_seconds": 489.4}
}
```

- `instances: null` on a sample means the decode did not reach it; `[]`
  means measured and nobody there.
- `mask` indexes the sidecar's `masks`; `read_mask` refuses bytes that
  do not hash to `sidecar.sha256`. Runs alternate background-first over
  the row-major grid.
- A region is a PERSON only through a face inside it (majority of the
  face box) whose M3b face track `event_spans.assign_face_tracks`
  resolved; otherwise `unassigned` names why (`UNASSIGNED_REASONS`). A
  limb whose owner is out of frame is segmented by Vision and stays
  unowned.
- Freshness: the lane (`footage_analysis.LANES`, `regions`) rebuilds a
  record whose `instrument.method` is not the current slot version or
  whose sidecar is missing.

```sh
bin/vep -m library.tools.heavy_work_lock run \
  --owner <lane> -- python3 -m library.tools.person_regions build <project>
python3 -m library.tools.person_regions status <project>
python3 -m library.tools.person_regions window <project> --clip clip_002 \
  --start 60 --end 90
```

Cost: `docs/benchmarks/2026-10-11-person-regions-m3d.md`.

## M3e `saliency.json` (written)

Owner: `library/tools/attention_saliency.py` (the module docstring holds
the input-resolution measurement). Apple Vision's
`VNGenerateAttentionBasedSaliencyImageRequest`, through
`library/tools/attention_saliency_helper.swift`, on the M2 thumbnails
themselves - no decode of its own. Only the salient-object boxes are
kept; the 68x68 heatmap has no reader.

```json
{
  "content_digest": "sha256 hex",
  "status": "measured",
  "coordinates": "image-normalised, top-left origin",
  "frame_count": 8191,
  "samples_measured": 8191,
  "samples": [{"t": 0.083, "boxes": [
      {"bbox": [x1, y1, x2, y2], "confidence": 0.52}]}],
  "instrument": {"method": "attention-saliency-v1-m2",
                 "input": "m2-thumbnail", "input_pixels": [384, 216],
                 "ms_per_frame": 3.4, "wall_seconds": ...}
}
```

`ms_per_frame` 3.4 is the warm median on the 30 frames the input was
chosen on; a whole-source wall time has not been measured yet.

- `boxes: null` on a sample means the helper could not read it; `[]`
  means measured and nothing salient.
- Reader: `library/tools/overlay_keep_clear.py`, which the reel build's
  overlay sweep runs - per placed overlay, its overlap with each M3d
  person region and each salient box under it, REPORTED and never moved.
- Freshness: the lane (`footage_analysis.LANES`, `saliency`) rebuilds a
  record whose `instrument.method` is not the current slot version.

```sh
python3 -m library.tools.attention_saliency build <project>
python3 -m library.tools.attention_saliency status <project>
```

## M3f `region_stats.json` (written)

Owner: `library/tools/region_stats.py` (the module docstring holds what
each reading is and is not). Arithmetic over the M2 thumbnails and the
M3d masks stored on their grid - no decode, no model. Per M3d sample,
one reading per person instance and one for OUTSIDE (every cell no
person instance holds): Rec.709 luma median, mean RGB, the
`shot_colour` skin vector, and the 4-neighbour Laplacian variance over
the cells whose whole stencil lies in the region.

```json
{
  "content_digest": "sha256 hex",
  "status": "measured",
  "grid": [384, 216],
  "outside": "cells no person instance holds",
  "frame_count": 8191,
  "samples_measured": 8191,
  "tracks": [{"track_id": "region_001", "face_track": "face_001"}],
  "samples": [{"t": 0.083,
    "whole_frame": {"cells": 82944, "luma_p50": 0.0, "rgb_mean": [r, g, b],
                    "sharpness": 0.0, "mask_sha256": "sha256 hex"},
    "outside": {"cells": 70000, "luma_p50": 0.0, "rgb_mean": [r, g, b],
                "skin_rgb": null, "skin_fraction": 0.0,
                "sharpness": 0.0, "sharpness_cells": 68000},
    "regions": [{"track": "region_001", "mask": 0,
                 "mask_sha256": "sha256 hex",
                 "inside": {"cells": 12000, "luma_p50": 0.0,
                            "mask_sha256": "sha256 hex", "...": "..."}}]}],
  "instrument": {"method": "region-stats-v2-m2",
                 "input": "m2-thumbnail + m3d masks", "luma": "rec709",
                 "m3d_sidecar_sha256": "sha256 hex", "wall_seconds": 0.0}
}
```

- `regions: null` on a sample means M3d did not measure it or its
  thumbnail is gone; `[]` means measured and nobody there. If M2 exists
  when M3d did not measure, `whole_frame` is still recorded and `outside`
  remains unavailable.
- Readers: step 5.01's bridge (`clip_region_stats`, over the source
  seconds each clip plays; it measures the samples it needs in memory
  when this slot was not measured off the current M3d sidecar), and
  `project_model_evidence`, which migrates every reading into
  compatibility `Observation(modality="region_stats")` rows and typed
  `image_measurement` evidence with resolution, sample coverage, method,
  and source/mask digests.
- Freshness: the lane (`footage_analysis.LANES`, `region_stats`)
  rebuilds a record whose `instrument.method` is not the current slot
  version or that is older than its M2 or M3d input.

```sh
python3 -m library.tools.region_stats build <project>
python3 -m library.tools.region_stats status <project>
```

## M3g `depth.json` (written)

Owner: `library/tools/depth.py` (the module docstring holds what each
reading is and is not). Per M3d sample, one median relative depth per
person region (brighter is nearer), on the source's pooled 2nd-98th
percentile normalisation; the maps themselves are estimated on demand
and never stored. Readers: `project_model_evidence`, which migrates
every median into `Observation(modality="depth")`, and
`behind_subject.apply_behind_object`, which layers a title under a
tracked foreground object.

```sh
python3 -m library.tools.depth build <project>
python3 -m library.tools.depth status <project>
```

## M7 `events.json` (written)

Owner: `library/tools/event_spans.py`. Light: built from M3, no decode,
no model, no lock. M3b is an ENRICHMENT, not a precondition: the
candidate rules are geometry over M3 faces and hands, so a source with
no identity record is still measured, on the geometry tracks of
`library/tools/face_tracks.py` (a face continuing across the picture,
never a person). The record says which basis it used, and the
person-scoped predicates it could not measure are recorded as
unavailable rather than as empty spans.

```json
{
  "content_digest": "sha256 hex", "source_file": "<absolute path>",
  "status": "built", "faces_assigned": 8149, "faces_unassigned": 43,
  "identity": {"basis": "m3b", "available": true,
               "unavailable_reason": null,
               "track_basis": "M3b ArcFace face tracks, assigned to M3 faces by position"},
  "predicates_unavailable": {},
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
  Without M3b the person-scoped predicates (`on_screen`, `speaking`)
  are NOT measured: `predicates` carries neither and
  `predicates_unavailable` names the reason, so a licence-blocked
  identity model never reads as "this person never spoke".
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

## M9 `world_model.json` (written)

Owner: `library/tools/world_model.py`. Light: a pure join over the
lanes, no decode, no model, no lock. `build_source_world_model` reads
M0, M1, M3, M3b (+M3c), M4, M5, M6, M7, M8 and M10 out of this same
memory and emits one joined entry per second of the source, so a query
at time T is one array index; a lane that was never built is ABSENT
from the record's `lanes` list, never defaulted.

```json
{"content_digest": "sha256 hex",
 "source_file": "<absolute path>",
 "status": "built",
 "duration_seconds": 0.0,
 "lanes": ["m0", "m1", "m3", "m3b", "m4", "m5", "m6", "m7", "m10"],
 "timeline": [{"t": 0,
               "speech": {"words": [], "speaker": null},
               "faces": [], "scene": null,
               "sound": {"events": []},
               "events": {"on_screen": [], "speaking": [],
                          "hand_near_mouth": []},
               "relationships": {"holding": [], "pointing": []}}],
 "entities": {"face_tracks": [], "voice_tracks": []},
 "multicam": null}
```

`multicam` is null when the source is in no M6 group, else the M6
`group_id`, `group_members`, `reference_digest` and
`offset_to_reference_seconds` (`world_model._build_multicam`).

It is built by the `world_model` lane of `ren analyze`
(`footage_analysis.LANES`) and by its own command:

```sh
python3 -m library.tools.world_model build <project> [--memory-root DIR]
python3 -m library.tools.world_model status <project>
python3 -m library.tools.world_model query <project> --t 12.5
```

Reader: `library/tools/project_graph/footage.py` (`_build_world_models`
via `source_memory.read_world_model`) joins each catalog source's model
into the footage graph's scenes and transcript segments.

## M10 `relationships.json` (written)

Owner: `library/tools/relationships.py`; the `relationships` lane of
`ren analyze` (`footage_analysis.LANES`), after M7. Light: built from
M3 + M3b + M3d, no decode, no model, no lock. Gap 3 + Gap 7 of the
world-model gap map (`data/vep-gapmap-a-world-model/report.md` in
firstmate's home): the review's `Relationships` level - "person holding
object, looking at person, referring to visible object".

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
                                        "hand": "right"}]}]},
    "looking_at": {"basis": "...",
                   "spans": [{"owners": ["face_001", "face_002"],
                              "start": 60.0, "end": 63.25,
                              "frames": [{"index": 120, "t": 60.0,
                                          "yaw": 0.9, "d": 0.671,
                                          "reach": 1.03,
                                          "region": "region_002"}]}]}
  },
  "instrument": {"method": "relationships-v2 over M3 pose + M3d regions",
                 "reads": ["persons.json", "identity.json", "regions.json"]}
}
```

- `holding` - a confident joint of one hand within
  `HOLDING_MAX_JOINT_DISTANCE` (0.5) face widths of another hand's, for
  at least `HOLDING_MIN_FRAMES` (2) frames. The review's form is "hand
  joints near OBJECT box"; no lane carries object boxes (nothing tracks
  objects yet), so the detector measures the other half of every hold:
  the two hands that meet - a clasp, a handshake, a handoff, a
  two-handed grip. The two hands may share an owner.
- `pointing` - a hand's wrist->fingertip ray within
  `POINTING_MAX_ANGLE_DEG` (25) of another tracked face's centre, the
  hand extended at least `POINTING_MIN_HAND_EXTENSION` (0.3) face
  widths past its wrist, for at least `POINTING_MIN_FRAMES` (2) frames.
  A fist does not point; pointing at one's own face is M7's
  hand-at-mouth rule, not a relationship.
- `looking_at` - a head-pose ray meeting another person's region. A
  face turned at least `LOOKING_MIN_YAW_RAD` (0.35) of M3 yaw off the
  camera casts a ray from its face box's edge along the face centre's
  image row, toward the side it faces; the first other M3d region the
  ray meets on the mask grid is the target, for at least
  `LOOKING_AT_MIN_FRAMES` (2) frames. Positive yaw faces image +x -
  measured on the MU-03 fixture (`tests/fixtures/mu03/turned-face.jpg`:
  +0.715, mirrored -0.723), not assumed. Pitch is unused: its sign has
  no measurement here. A ray stops at the first region whoever it is: a
  look meeting an unowned region names nobody, never the person behind.
  `d` is `pi/2 - |yaw|`, the head's offset from the straight-across
  bearing to a partner at the looker's own depth; `reach` the distance
  to the target in face widths.
- All three are geometric CANDIDATES for VLM verification, never
  answers - the same standing decision as M7's `hand_near_mouth`. Each
  frame carries its measured distance (`d`), angle and extension, or yaw
  so a verifier can judge the span without re-reading the lanes;
  `holding` and `looking_at` frames carry the `d`
  `span_verification.select_frames` ranks by.
- Who is who: faces take M3b ArcFace tracks by position
  (`event_spans.assign_face_tracks`). A hand belongs to the person
  REGION its wrist lies in (the region track's owning face track), not
  to the nearest face - an off-frame owner's hand is its own faceless
  region, which the nearest-face rule handed to whoever sat closest. A
  hand or target no track claims is left out of named relationships,
  not guessed into one.
- Distances are measured in pixels (x scales by the frame width, y by
  its height; the unit is the frame's mean face width) - the
  coordinate contract `person_measurements` documents. A frame with no
  face carries no scale, and a distance without a scale is not a
  measurement; a frame whose M3d sample was not decoded carries no
  regions and is not measured either.
- Stale when its `instrument.method` is not the current slot version
  (a v1 record owned hands by nearest face and refused looking-at), or
  when it is older than the M3, M3b or M3d it read.

```sh
python3 -m library.tools.relationships build <project>
python3 -m library.tools.relationships status <project>
```

The build refuses (raises) without M3, M3b or M3d, or with an M3d
measured on another sample than M3, per source, and reports the failure
without aborting the project - an unbuilt source would read as "no
relationships here".

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

`<project>` is a project FOLDER or a project SLUG under
`PIPELINE_PROJECTS_ROOT` (flat or client-grouped); the measurement CLIs
resolve it through `library/tools/project_ref.py` and REFUSE a
reference that names no project, rather than reporting an empty
catalog.

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
   `footage_analysis.LANES` fill M0-M3f, M6, M7, M9 and M10 and build the
   text and frame indexes (the `identity` lane writes M1b `speakers.json` beside
   M3b via `source_memory.write_speakers`); M9 (`world_model.json`) is
   also built by `world_model.build_project_world_model` through its own
   `build` command. `--memory-only` runs scan and catalog alone.
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
