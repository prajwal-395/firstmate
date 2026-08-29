# Where the music comes from, and why nothing goes looking for more

The captain, 2026-08-28:

> *"as far as the music choice, why is it that we are not like looking online for music or
> researching or anything and stuck choosing from 7 filenames?"*

Two questions in one. The **filenames** half is answered in code -
`library/tools/music_measurement.py` now measures every selectable candidate, and step 2.04
sees loudness, dynamic range and the envelope over the part that plays instead of a directory
listing. This document answers the **seven** half: where those seven come from, why there are
only seven, and what sourcing more would actually cost.

Nothing here was built. No network call, no download, no external service was added. Measured
on 2026-08-28 against project 001 and the machine's own library.

---

## 1. Where `music_candidates` comes from

`library/tools/music_selection_contract.catalogue_sources` is the whole enumeration. Three
directories, two source labels:

| source | directory | on 001, 2026-08-28 |
|---|---|---|
| `library` | `PIPELINE_MUSIC_LIBRARY` | **1 track** |
| `project` | `<project>/music/` - the captain's own files, an INPUT area the pipeline may not write to | **6 files** |
| `project` | `<project>/pipeline_output/steps/2_04_music_selection/downloads/` - where a fetched track lands | **0** (the directory has never been created) |

**Seven, and that is the whole world the step can see.** The step's own run of record recorded
`catalogue_size: 7`.

### The shared library is one file

    PIPELINE_MUSIC_LIBRARY = ".../assets i used (just copied here for convenience)/music"
    Sickick - Infected (lyrics).mp3          201.9s

That is it. The name of the directory says what it is: files copied there for convenience, not
a library anybody curated. Six of the seven candidates are 001's own `music/` folder, so the
"shared" library contributes **one** track to every project on this machine, and it is a
duplicate - 001 holds the same recording as a WAV, and the two measure identically to two
decimal places.

### What the seven actually are

| # | title | duration | selectable | note |
|---|---|---|---|---|
| 1 | Sickick - Infected (lyrics) `library` | 201.9s | yes | commercial single, has a vocal |
| 2 | Inspirational Motivational Music Video _ Work Background Music | 3,914.7s | **no** | 65 min - a compilation |
| 3 | Sickick - Infected _lyrics_ | 201.9s | yes | byte-for-byte the same record as #1 |
| 4 | Sickick- _Infected_ _Instrumental_ | 198.6s | yes | the instrumental cut of #1 and #3 |
| 5 | Uplifting Office Music MIX ... | 11,386.9s | **no** | 190 min - a compilation |
| 6 | _background music_ rise - uplifting piano ... motivation | 149.0s | yes | **the one 001 shipped** |
| 7 | dummy | 1.0s | **no** | a test file |

Four survive the duration check. Two of those four are the same recording. **So the real
choice, on the run that produced 001, was between three tracks: one commercial single, its own
instrumental, and one piano bed.**

---

## 2. The pipeline can already fetch. It has never been able to search.

This is the part that is easy to get wrong, so it is worth stating precisely.

**Fetching a NAMED track is wired and works.** The handoff tells the model that a track held
nowhere locally is a legitimate answer - `source: "external"` with a `source_url` - and
`post_bridge._fetch_external` calls `download_track.download_audio`, which shells out to
`yt-dlp`, converts to WAV into the project's downloads area, and then **re-measures the real
duration off the downloaded file** rather than trusting the number the model stated. The
validator judges the result like any other candidate.

**Searching for one is not wired at all.** `library/steps/step_2_04_music_selection/search_youtube.py`
exists, takes a query, and returns titles, URLs, durations and channels. **Nothing imports it** -
not the bridge, not the post-bridge, not the DAG, not a manifest, not a test. It has no caller
anywhere in the repository.

The consequence is exact: the model is invited to name a URL, with no search results in front
of it. It has to produce a working YouTube URL from memory. On 001 it did not try -
`source_url` is empty and `source` is `project`.

Two things would have to be true before that route is real, and neither is about writing code:

- **`yt-dlp` is not a declared dependency.** It is absent from `requirements.txt` and from the
  pipeline venv. The binary happens to be on this machine's PATH, so a fetch would probably
  work here today and would fail on a clean checkout with a `FileNotFoundError` forty minutes
  into a run.
- **Nothing records or checks a licence.** There is no licence field on a candidate, on a
  selection, or in the manifest. `music_measurement.DECLINED_MEASUREMENTS` says why the
  measurement layer cannot supply one: rights are not derivable from a waveform, and asserting
  one would be a claim rather than a measurement.

---

## 3. What sourcing would actually require

Ordered by what blocks what. Only the first item is a captain decision, and everything below it
is unbuildable until it is answered.

**1. A licensing decision. This is the whole blocker.**
The library's one track is *Sickick - Infected*, a commercially released single. 001's chosen
bed and its two over-long compilations are YouTube rips. None of this is licensed for
publication, and the channel brief's own posting cadence means every episode ships with the
same exposure. The handoff already says *"Prefer royalty-free / no-copyright tracks"* and
nothing checks it, because nothing can: the pipeline has no idea what any of these files are
licensed as.

The decision is not "should we search YouTube". It is **what is this channel licensed to score
its videos with** - a subscription (Epidemic Sound, Artlist, Musicbed, Soundstripe), a
free-tier catalogue with attribution rules (YouTube Audio Library, Free Music Archive, ccMixter),
or an accepted risk on Content ID. Each answer implies a different mechanism, and two of them
imply no scraping at all.

**2. Whatever mechanism that decision names.**
- A subscription is an authenticated catalogue API or a synced local folder. If it is a synced
  folder, **the pipeline needs no new code at all** - point `PIPELINE_MUSIC_LIBRARY` at it and
  the catalogue grows from 1 to hundreds, which is the cheapest possible version of this.
- A free-tier catalogue is a download plus an attribution obligation the pipeline would have to
  carry into the render's description, which nothing today does.
- A search over YouTube is `search_youtube.py`, wired, plus `yt-dlp` in `requirements.txt` - and
  it delivers unlicensed audio, so it is the option a licensing decision most likely removes.

**3. A licence field that travels with the track.**
Whatever the source, a candidate needs to carry what it is cleared for, and the selection needs
to record it. That is a contract change in `music_selection_contract.py`, not a measurement -
it is asserted by whoever put the file there, and `external_inputs.py`'s rule applies: a source
is recorded, never trusted.

**4. Only then, more candidates - and the measurements are what make more candidates useful.**
Handing the model 200 filenames instead of 7 is a worse prompt, not a better one. Handing it
200 rows of measured loudness, range and envelope is a table it can filter - and there is now
room for them: with #295 no longer copying the channel brief into the prompt, this step's
context on 001 is 16,747 B, of which the candidates are 28.0%. The cost is
measured: **18.8 s for 001's seven candidates**, of which four were opened. Per selectable
track it is ~4.5 s, dominated by the `loudnorm` pass at roughly 47x realtime. A catalogue of
200 three-minute tracks would be ~15 minutes, which is the point at which the per-file cache the
SFX library already keeps becomes worth building. Below about 50 tracks it is not.

---

## 4. What this leaves

The captain's question has a blunt answer: **we are not looking online because nobody has
decided what this channel is allowed to play**, and every mechanism that would find more music
is downstream of that. The seven filenames were not a design; they are what was in two folders.

The half that was an engineering problem - choosing among those seven with nothing but their
names - is fixed. See `library/tools/music_measurement.py` and AGENTS.md §10.5 on why what it
emits stops at numbers.
