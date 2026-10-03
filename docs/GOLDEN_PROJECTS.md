# Golden projects

Tiny, controlled Ren projects driven through the same entry points the
captain's runs use, with structural and semantic assertions. The harnesses are
`tests/scenarios/golden.py` (the reels path) and `tests/scenarios/golden_edit.py`
(the `edit_video` path); each shape is one `tests/scenarios/test_golden_*.py`,
and `tests/qualification/test_golden_conversation_live.py` drives the
conversation on real Resolve in a disposable scratch project.

## Which shapes Ren needs (verified 2026-10-02)

Read off the projects that exist under `PIPELINE_PROJECTS_ROOT` and the
captain's content tree, not off the test plan's list of five:

| Shape | Real project | Path through Ren | Golden |
|---|---|---|---|
| Multicam conversation | `lucie/geo-podcast` (two synced MXF angles, 23.976) | reels: transcript, select, propose, build, verify, touch, deliver | `test_golden_conversation.py` |
| Already-human-edited reel | the same reels after the captain's hand edits in Resolve (Reel 09) | rebuild with first-contact and editor-edit carry | the hand-edit stage of `test_golden_conversation.py` |
| Monologue with B-roll | `post a day keeps the apple away/001` (phone walk-and-talk, portrait and landscape B-roll, music bed) | `edit_video` to `rough_cut_subtitles`: 17 steps, scan to the Resolve build and render | `test_golden_monologue.py` |
| Speechless / music piece | none | - | not built: no project or plan needs it |

A B-roll-heavy piece is the monologue's shape, not a separate one: 001 is
eleven landscape B-roll placements around a portrait monologue.

## What is real and what is answered

Everything runs for real except these seams, each answered once:

- **The transcriber** (`timeline_transcript.transcribe_audio`). The media is
  test pattern and tone, so the recipe's lines are what each microphone
  "heard". Span extraction, binding, mic-bleed resolution and the document
  are the real code.
- **The caption renderer** (step 4.05's `_default_unit_engine`). Remotion is
  replaced by an ffmpeg card of the size and length the props ask for.
- **Resolve**. Offline, the canonical double, given two opt-in knobs: a
  media `probe` (what Resolve reads off a file it imports) and a
  `render_engine` (the render queue writing a file of the job's shape).
- **The edit's measurement models** (`golden_edit.install_seams`): the
  vision pass and the transcriber answer from the recipe; face, audio-event
  and beat models answer what test pattern and tone measure (nothing).
  Step children run in the test's process (`golden_edit.in_process_steps`)
  so these seams reach them, and every model answer goes through the file
  handshake from `golden_monologue_answers.json`.

The model's judgement is the recipe's recorded answer, passed through the
step's own post-bridge, so the checks a real answer meets are the real ones.

## What the golden projects found

Found by the conversation shape on its first run. Fixed ones name the PR.

1. **`ren deliver` after `ren build` rendered a timeline that no longer
   existed.** The `reel.build` record names the staging containers, which
   `verify_reels` renames onto their finals. The captain's geo-podcast record
   carries four such names. Fixed in `reel_deliver._built_names_for`.
2. **A touch that places nothing called `AppendToTimeline([])`** and took
   `len()` of whatever came back. Fixed in `composed_edit.place_all`.
3. **The reels path is 23.976-only.** `reel_build` plans captions and cards
   at a literal `24000 / 1001` whatever the master's rate, so a 24 fps master
   fails F2 (captions placed a frame off). Open.
4. **Mic-bleed suppression leaves sub-floor audio slivers.** When a reel
   boundary sits in a silence under 0.5s from the other speaker's words (a
   body end the captain moves by hand), `suppress_mic_bleed_audio` keeps a
   2-7 frame piece of the silent mic and F7 refuses the whole reel. Open.
5. **A replace-guard refusal cannot be followed.** It prints
   `build-reels --allow-drop ...`, but its staging stays held and the next
   build refuses it as debris "from an interrupted run", so the suggested
   command never works until the staging is deleted by hand. The refusal is
   also a `ReelBuildError`, so the CLI shows a traceback and exits 1 rather
   than the refusal contract's 4. Open.
6. **The closing breath can play the next speaker's first word.** It is
   bounded by the next word's start in seconds, and frame placement crosses
   the bound by a fraction of a frame (F25 `played_not_captioned`). Open.
7. **A Ren touch-up does not survive a plan-change rebuild.** Hand edits made
   in Resolve are carried, but a `ren touch` property write on the same reel
   is silently lost when the reel is rebuilt. Open, and a product decision.

Found by the monologue:

8. **Step 4.05 refused every `edit_video` run's captions.** Its QA judged a
   tight caption canvas (the default carriage since 2026-09-10) as if it
   were the whole frame, so every caption read "upper half". Fixed: a tight
   segment records its canvas origin and frame, and the position is judged
   in the frame (`subtitle_qa.check_caption_geometry`).
9. **The double's `GetEndFrame` was inclusive; Resolve's is exclusive**
   (`qualification_project.verify`, live on 21.1). Fixed in the double, with
   a contract check run on both backends.

The live conversation passed on Resolve Studio 21.1 on its first run
(2026-10-02, 28.6s), in a scratch project deleted afterwards.
