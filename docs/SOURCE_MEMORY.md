# Per-source footage memory: directory and file contract

Owner: `library/tools/source_memory.py`. Scout basis:
`data/vep-long-footage-memory-scout/report.md` (§3.1, §5 items 1-2 -
in firstmate's home, outside this checkout).

## Root

`PIPELINE_SOURCE_MEMORY_ROOT`, else `vep_home()/source_memory`
(`~/.local/share/vep/source_memory` on this machine). Per machine,
outside every project and checkout. Projects reference it and never
copy it; nothing goes into `pipeline_data.json`.

## Layout

```
<root>/<content_digest>/          # footage_identity.fingerprint
  source.json                    # M0 - this task
  transcript.words.json          # M1 - this task
  speakers.json                  # M1b - diarization lane (RESERVED)
  frames/ + frames.index.json    # M2  - sampler lane (RESERVED)
  persons.json                   # M3  - Vision lane (RESERVED)
  identity.json                  # M3b - entity lane (RESERVED)
  scenes.json                    # M4  - CLIP lane (RESERVED)
  sound.json                     # M5  - SoundAnalysis lane (RESERVED)
  clock.json                     # M6  - this task
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
transcribable. `gop_frames` is null until the sampler lane measures
it.

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

python3 -m library.tools.source_memory status <project>   # light, reads only
python3 -m library.tools.source_memory build <project> --clip clip_003
```

`build` reuses a fresh transcript (`reused: true`) and records
per-file failures without aborting the project. `PIPELINE_SOURCE_MEMORY_ROOT`
overrides the root (`--memory-root` per run, e.g. for tests).

## Reading (search)

`footage_segments.build_segments` serves fresh M1 utterances ahead of
the 1.04 regions for the same clip - never both. Digests resolve live
off disk when media is present, off the runner's `source_fingerprints`
record when it is not, so search works with footage offline.
