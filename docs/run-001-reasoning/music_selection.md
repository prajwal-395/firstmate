# music_selection (step 2.04) - reasoning

Written before the answer. Request: `pipeline_output/llm_requests/music_selection.json`
(prompt 8,258 chars; context 8,991 chars; constraints 0 chars).

## 1. What I actually read

- **`creative_direction`, in full.** It is my own output from 2.01, so I was reading it as
  its author. I re-read `target_mood`, `emotional_landscape` and `energy_arc` specifically
  to extract the forbidden registers the prompt asks for, and took them literally from the
  sentence "Anything triumphant, anthemic or hard-hitting will fight this footage."
- **`music_candidates`, all 7 rows.** Each row is `audio_path, duration_note, duration_ok,
  duration_seconds, source, title`. I read every field.
- **`music_candidates.target_duration_seconds` (60.0) and `max_track_duration_seconds`
  (600.0).** These bound the choice and I checked both against the survivor.

**Note on the constraints block: it is EMPTY this time** (`constraints: 0 chars`), where
2.01's carried the brand style and content rules including `music_genre: ["electronic",
"upbeat"]`. So the brand's genre hint, which the prompt explicitly tells me to use as a
secondary search hint (`brand_content.music_genre`), did not reach this call. I am not
treating that as a problem to route around - I had already argued in 2.01 why that hint is
a fallback default rather than a brand decision - but it is a gap between what the prompt
says it will give me and what arrived, and it belongs on the record.

**No `creative_brief` again.** Same as 2.01. The prompt's "Creative Brief" section says the
brief "takes priority over all other genre guidance"; there is none, so genre came entirely
from the direction.

## 2. What the candidate list actually offered

Seven rows, but the real choice was between two. Working through them:

| track | dur | verdict |
|---|---|---|
| `Inspirational Motivational Music Video _ Work Background Music.wav` | 3914.7s | out on duration (65.2 min compilation) - the bridge flagged it |
| `Uplifting Office Music MIX ... Lounge Music_ shop.wav` | 11386.9s | out on duration (189.8 min compilation) - the bridge flagged it |
| `dummy.wav` | 1.0s | out on duration - a 1-second test fixture |
| `Sickick - Infected (lyrics).mp3` (library) | 201.9s | out: has lyrics |
| `Sickick - Infected _lyrics_.wav` (project) | 201.9s | out: same track, same reason - it is a duplicate of the row above in a different folder and container |
| `Sickick- _Infected_ _Instrumental_.wav` | 198.6s | real contender |
| `_background music_ rise - uplifting piano ... motivation.wav` | 149.0s | real contender |

The three duration rejections I did not re-derive - the bridge measured them and its
`duration_note` reasoning is sound (a 65-minute file scoring a 60-second edit is a
compilation). I accepted them.

The two lyric versions are an easy rejection and I want to say it was easy: this piece is
a monologue that is sometimes near-whispered. A track with a vocal puts two voices in the
same band and the viewer has to choose. The prompt says the same thing ("we need music
without vocals"). No judgement required.

## 3. The real decision, and how I made it

I was left with a dark trap instrumental and a stock piano track, and **both of them sit in
registers my own direction forbids.** Sickick "Infected" is hard-hitting; the piano file's
own name says "uplifting", "inspiring", "motivation" - and 2.01 rejected "motivational"
explicitly. So neither is a clean fit and I had to decide which mismatch was real and which
was cosmetic.

**The context gave me nothing to decide it on.** Title and duration. No BPM, no genre tags,
no mood descriptors, no loudness. I am being asked to match an emotional landscape against
seven filenames. So I did what a music supervisor does at this point: **I auditioned them.**
That is knowledge from outside the context and I am declaring it. I did not listen - I have
no ears - I MEASURED, with ffmpeg/ebur128 and librosa:

| | rise (piano) | Sickick (instrumental) |
|---|---|---|
| integrated loudness | -13.9 LUFS | -15.1 LUFS |
| loudness range (LRA) | **6.0 LU** | **9.1 LU** |
| tempo | 107.7 BPM | 89.1 BPM |
| 60-250Hz / 250-4kHz energy | 0.369 | 0.456 |
| RMS spread across track | 5.7 dB | **29.9 dB** |
| envelope over the played window | flat | -43 dBFS -> -13 dBFS by 30s |

**That measurement decided it, and it decided it on a mechanical ground rather than a
tasteful one, which is why I trust it.** The Sickick instrumental swings 30 dB. It opens
near silence and slams to -13 dBFS around the 30-second mark - it is a drop-based track.
The bed under this piece has to sit beneath a voice that is hesitant and sometimes
whispering. With a 30 dB swing there is no single clip gain that works: set it so the quiet
opening is audible and the drop buries the speech; set it for the drop and the first
20 seconds are inaudible. The piano track's whole-file LRA is 6.0 LU and across the window
that actually plays it is flat.

Its higher low-band ratio (0.456 vs 0.369) is a second, smaller mechanical strike: more
energy in 60-250 Hz means more masking of the male speaking voice's fundamentals.

**Copyright, which I should have led with.** "Infected" is a commercially released track by
a named artist. The premise of this video is a man committing to post publicly every single
day for a hundred days. Selection principle 8 asks for royalty-free / no-copyright, and a
content claim on video one of a hundred is the single worst outcome available here. The
piano file's name reads as stock-library SEO ("uplifting piano inspiring beautiful
motivation"), which is what royalty-free production music is called. That is an inference
from a filename, not proof, and I flag it as such - but it points the safe way.

**On the piano track's "motivational" label - I am overruling the filename with the
measurement.** Every stock piano cue on every library is tagged inspiring/beautiful/
motivation; it is search-engine copy, not a description of the music. What matters is
whether it swells into an anthem, and that is measurable.

**I made an error here mid-decision and caught it, so it goes on the record.** My first
reading of the whole-file envelope was that the track DECLINES (-16 dB at the head, -22 dB
by 140s) and I was about to argue that this matched the direction's "resolves by getting
quieter, not louder". That argument was wrong. Music is placed at source_in 0 and runs to
the end of the timeline, so on a 60-second edit **only the first 60 seconds of the track
ever play** - and the decline lives at 110-149s, which never plays. I re-measured the
played window in 5-second buckets:

```
 0- 5s  -18.9      20-25s  -16.2      40-45s  -16.2
 5-10s  -16.3      25-30s  -16.7      45-50s  -16.4
10-15s  -16.5      30-35s  -16.2      50-55s  -17.0
15-20s  -16.3      35-40s  -16.9      55-60s  -16.3
```

Flat at -16.3 dBFS after a soft 5-second entry. So the honest claim is not "it declines to
match the arc" - it is "**it does nothing at all**, which is what a bed should do". No
drop, no swell, no build to fight the ending. One clip gain works across the whole piece.
That is a weaker and truer argument than the one I nearly made.

**What I rejected that was not on the list: going external.** The prompt legitimises it and
says nothing local is preferred by virtue of being on disk, so I considered it seriously -
a sparse ambient or lo-fi piano cue from a royalty-free source would be a better fit than
either survivor. Two reasons I did not: (1) the post-bridge FETCHES the URL and re-measures
it, so naming a URL I cannot verify puts a network download on the critical path of a run
that has ten more LLM calls behind it, and a dead link fails the step; (2) more importantly,
I would be naming a file I have not confirmed exists. Inventing an asset is worse than
choosing a mediocre real one. If the captain wants a better-fitting track this is the line
to pull on, and I would rather flag it than fake it.

## 4. What I was missing

- **Any musical description of any candidate.** Title and duration only. No BPM, key, genre,
  mood, instrumentation or energy in the context. A music supervisor asked to match an
  emotional landscape was handed a directory listing. Everything in section 3's table I had
  to generate myself by measuring the files; a model without shell access could not have
  made this decision on anything but the filenames, and on the filenames alone the "rise"
  track reads as forbidden and Sickick reads as neutral - i.e. **the context as supplied
  points at the wrong answer.** This is the single most actionable observation in this log.
- **The brand constraints block, which arrived empty** (see section 1) though the prompt
  names `brand_content.music_genre` as a secondary hint.
- **The key.** Not measurable reliably from my tooling and not in the context; I am
  returning null rather than guessing one.
- **Whether the track is actually any good.** Measurement gives me shape, level and density.
  It cannot tell me the cue is not saccharine. A flat, even, mid-tempo piano bed is exactly
  as consistent with "warm and unhurried" as it is with "lift-music". I genuinely do not
  know which this is, and no amount of further measuring will tell me.
- **Confirmation that the file is licensed for use.** I inferred royalty-free from the
  filename shape. Nothing in the context states a licence for any candidate.

## 5. Confidence

**High** on the rejections. The lyric tracks, the two compilations and the 1-second dummy
are not close calls. The Sickick instrumental rejection is also high confidence because it
rests on a measured 29.9 dB dynamic range, which is a fact about the file rather than an
opinion about the music.

**Medium** on the selection itself. I am confident "rise" is the *better of the two local
options* and much less confident it is *right*. It is a default-by-elimination as much as a
choice, and I would rather say that than dress it up.

**What would change my answer:** hearing either file. Or a `creative_brief` naming a sonic
reference. Or permission to spend a minute finding a real royalty-free ambient cue, which I
think would beat this. If the piano turns out to be sentimental in a way the measurements
cannot see, this is the wrong track and the fix is a different track, not a different mix.

**On BPM:** 107.7, from librosa onset/beat tracking on the first 160 seconds, reported as
measured rather than known. Step 2.06 (`music_analysis`) will derive the real beat grid from
the file downstream and that grid, not this number, is what cuts get snapped to - so an
error here is caught rather than shipped.
