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
  source.json                    # M0 - written
  transcript.words.json          # M1 - written
  speakers.json                  # M1b - diarization lane (RESERVED)
  frames/ + frames.index.json    # M2  - written
  persons.json                   # M3  - Vision lane (RESERVED)
  identity.json                  # M3b - entity lane (library/tools/person_entity.py)
  scenes.json                    # M4  - CLIP lane (RESERVED)
  sound.json                     # M5  - SoundAnalysis lane (RESERVED)
  clock.json                     # M6  - written
  events.json                    # M7  - predicate lane (RESERVED)
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
  "gop_frames": null
}
```

`channel` is the 1-based audio ordinal (`-map 0:a:<channel-1>`).
`program_track.channel` is null with basis `no-audio-streams`,
`no-live-track` or `declaration-refused` when nothing is
transcribable. `gop_frames` is null until M2 samples the source, then
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

## M3b `identity.json` (written)

Owner: `library/tools/person_entity.py`. Measured, not guessed: the
face-match threshold below is the false-accept study at
`data/vep-person-entity-store/eval/results.md` in firstmate's home
(FAR=0/FRR=0 across 83 real faces, 2 cameras, profile and eyes-closed
frames included). Reads the shared M2 sample for its frames when a
fresh one exists, falling back to its own sparse `ffmpeg -ss` decode
otherwise (`instrument.frame_source` below says which).

```json
{
  "content_digest": "sha256 hex",
  "source_file": "<absolute path>",
  "status": "measured",
  "face_match_threshold": 0.30,
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
                 "frame_source": "M2-shared-sample|own-decode",
                 "sample_count": 12, "sample_interval_s": 10.0}
}
```

Faces and voices are tracks WITHIN this one source, clustered by cosine
similarity (face: `FACE_MATCH_THRESHOLD`; voice reuses the already-landed
diarization clusters outright, PR #1482). `status` is `measured`,
`no-video-stream`, `no-faces-detected` or `face-identity-unavailable`
(insightface unreachable) - never a default where nothing was measured.

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
spans.
