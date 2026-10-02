# The known-answer case for `library/tools/reel_hearing.py`

Reel 26 of the `geo-podcast` project, **as delivered**. The defect is in
an mp4 on the captain's disk and this is the trimmed evidence of it.

One WhisperX row carried twelve words in 920 milliseconds with
`words: []` and no confidence:

```json
{"text": "make sure that you're coming up with something that answers
          specific questions.",
 "source_start": 4291.222, "source_end": 4292.142, "words": []}
```

Three consequences reached the delivered file, and all three fall out of
the deterministic half of the hearing pass:

1. six words ship with **no caption at all** (`make sure that you're
   coming up`, reel 25.65-26.61s) - the row has no word timings, so no
   subtitle segment was generated for its span;
2. the caption card that does appear is **a full second late**, and the
   karaoke highlight with it - nine consecutive words at **-1.01s**;
3. the extra words the speaker actually said (`niche`, `qu-`) are heard
   in the render and are not in the plan.

## The four files

| File | What it is |
|---|---|
| `reel26.timeline.json` | The serialized timeline `build_reels` wrote, trimmed to the keys `reel_hearing` reads |
| `reel26.transcript.json` | The timeline transcript's segments for the spans this reel plays, within five seconds either side |
| `reel26.heard.json` | The on-device transcriber's recorded output for the delivered mp4 - words AND the sentences the hybrid windows by - so the test needs no transcriber and no render |
| `episode.transcript.json` | The SAME transcript, trimmed instead to the whole-episode question `library/tools/transcript_fit.py` asks |

Source file paths are rewritten to `/footage/<name>` - only their
internal consistency matters, and the captain's directory layout does
not belong in the repository.

`reel26.heard.json` is a RECORDING, replayed through
`heard_speech.read_payload`. Regenerating it costs one `da voz` call on
the delivered mp4 and 3.5 seconds; the numbers the test pins were
measured from it on 2026-09-16.

It carries the transcriber's `sentences` as well as its `words`, because
`library/tools/hybrid_transcription.py` groups words into alignment
windows by sentence and a fixture without them cannot exercise that.
They were added on 2026-09-16 from a re-run that reproduced `words`,
`text` and `durationSec` byte for byte. `input`, `files` and `captions`
are dropped as they always were - the first names a path on the
captain's disk.

## `episode.transcript.json`, and what it is a slice of

The shipped transcript has **940 rows**. Keeping all of them would put
1.5 MB of the captain's client's speech in this repository to pin four
counts, so the fixture keeps exactly the rows those counts are about:

- every row whose text did not all reach a word timing (**15**);
- every row carrying a word the aligner PLACED without aligning - the
  numerals, **19** of them across 12 rows;
- the ten fastest fully-timed multi-word rows, so the document's own
  reference rate is a real one.

Forty-one rows, and **every number `tests/unit/audio/test_transcript_fit.py` pins
is the number the full 940-row document produces**: 15 unfitted rows,
13 of them having lost every word and 2 part of one, 217 words with no
timing, 12 rows asserting a rate no fitted row reaches, 19 interpolated
numerals, and a fastest fitted row of 11.76 words per second. Only
`rows` differs, and it is the fixture's own count rather than a claim
about the episode.

One row genuinely carries `source_file: null` - the pipeline could not
bind it to a clip - and it is kept rather than dropped, because that is
the case a measurement is most likely to skip silently.

Source file paths are rewritten the same way as the three files above.
