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

## The three files

| File | What it is |
|---|---|
| `reel26.timeline.json` | The serialized timeline `build_reels` wrote, trimmed to the keys `reel_hearing` reads |
| `reel26.transcript.json` | The timeline transcript's segments for the spans this reel plays, within five seconds either side |
| `reel26.heard.json` | The on-device transcriber's recorded output for the delivered mp4, so the test needs no transcriber and no render |

Source file paths are rewritten to `/footage/<name>` - only their
internal consistency matters, and the captain's directory layout does
not belong in the repository.

`reel26.heard.json` is a RECORDING, replayed through
`heard_speech.read_payload`. Regenerating it costs one `da voz` call on
the delivered mp4 and 3.5 seconds; the numbers the test pins were
measured from it on 2026-09-16.
