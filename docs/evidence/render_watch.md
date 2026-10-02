# `library.tools.render_watch` - the history behind its contract

This is the module docstring of `library/tools/render_watch.py` as it stood on
2026-10-01, moved here verbatim when the module kept only its current
contract (punch list 17). The module's docstring is the contract; this
is the evidence and the story of how each rule was found. Where the two
disagree, the code and its docstring win.

```text
Frames of the FINISHED picture, in front of a model.

*"Nothing in this pipeline has ever seen the picture"*
(`docs/PICTURE_JUDGEMENT_INVENTORY.md`, 2026-09-06).  Re-measured
2026-09-13 and still true of the built product: the `reels` process is
`build_reels` then `verify_reels`, `reel_conformance_verifier` is 3,416
lines of format, item count, picture holes, audio holes, caption timing
and caption overlap - all STRUCTURAL - and `reel_quality_bar` judges
from the transcript.  `window_frames` does put a picture in front of a
model, and both steps that declare it are `edit_video` steps reachable
from no reel.

And `step_6_02_validate_output`'s handoff opened *"You are watching the
RENDERED video ... The ultimate test: Would I post this?"* while its
three declared inputs were `assembly_manifest`, `rendered_output` and
`project_folder`.  **It was asked "would I post this" and shown a
table.**  A gate whose prose claims eyes it does not have is worse than
an honest structural gate, because it reads as coverage (AGENTS.md
10.4).

This module is the eyes.  It draws a strip of the RENDERED FILE and
hands a model the strip plus a list of questions a still can answer.

Why the rendered file, and not the timeline or the source
---------------------------------------------------------
Every picture defect in the recorded corpus was in the COMPOSITE, not
in a source clip: subtitles at 160 px against an 85 px reference,
motion graphics and captions placed off the frame, a card composited on
the wrong row, a defocus held at full strength across a whole clip.
None of those exist in the source footage and none of them exist on
the plan - they exist only once everything is drawn over everything
else.  `window_frames` shows the SOURCE window a step is choosing;
this shows the OUTPUT that choosing produced.  They are different
surfaces and this is why both exist.

**So watching requires a render, and that is not designed around.**  A
render is the expensive thing the captain must ask for (standing
ruling 2026-09-09 / 2026-09-10, and `reel_deliver.py` is built on it).
This capability therefore inherits that constraint exactly:

* on the `edit_video` path the render already happened - step 6.01
  produced the file 6.02 is judging - so the watch is FREE there and
  runs on the render that ran;
* on the `reels` path a reel becomes a file only when the captain runs
  `deliver-reel`, so `manage_project.py watch-reel` reads the file that
  verb already wrote and NEVER renders one.  `tests/
  test_render_watch.py` asserts this module imports no render path.

Nothing here is a DAG node.  `library/processes/reels/dag.json` stays
two nodes and `tests/contracts/test_reel_deliver_is_explicit.py` holds it there.

What it costs
-------------
Measured 2026-09-13 on this machine against a synthetic 1080x1920 30 fps
h264 file of 35.0 s - the reel frame and a realistic reel length: **five
strips, 4.03 s of wall clock, 319 KB on disk, 0.05 s on a re-run with
the strips already there**.  The table is in
`docs/WATCHING_THE_BUILT_REEL.md` section 4.  One strip is ONE ffmpeg
call doing `MAX_FRAMES_PER_STRIP` fast seeks, which is why the decode is
seconds and the cheap/expensive split does not fall where it looks like
it should.

**The decode is not the cost.  The MODEL READING THE STRIPS is.**  Five
images per reel across eight reels is forty images per round, and that
is a real spend on the captain's machine and a real spend in tokens.
So, under the captain's 2026-09-09 ruling that a build checks itself
cheaply always and expensively on request:

* CHEAP, ALWAYS: `verify_reels` and `reel_conformance_verifier`,
  unchanged.  This module adds NOTHING to a build.  No `build-reels`,
  no `run`, no DAG and no operation reaches it.
* EXPENSIVE, ON REQUEST: the watch, over a file the captain asked for.

Report, not gate
----------------
The watch REPORTS.  It does not fail a build and it does not fail a
render.

A gate that refuses on a model's opinion is a new failure mode on a
pipeline whose gates have refused correct output three times this week
(AGENTS.md 10.4, `docs/RULE_EVIDENCE.md#gates-that-fail-correct-output`),
and the structural verifier that DOES gate is correct and stays.  What
a watcher adds is the class of defect no structure can reach, and every
one of those is a judgement: *is that text too big*, *is that graphic
clipped*, *is that shot of nothing*.  A judgement that blocks a build
is the pipeline inventing taste on the model's behalf, which 10.5
forbids in the other direction for the same reason.

**One thing here IS hard, and it is mechanical.**  A watch that was
ASKED FOR and drew no picture must not read as a watch that passed.
`NothingWasWatched` is that refusal, and it is about the instrument,
never about the answer - the same line `render_qa` draws when its
toolkit fails to run.

What a still can be asked, and what it cannot
---------------------------------------------
`WATCH_QUESTIONS` is the enumeration and `NOT_ANSWERABLE_FROM_STILLS`
is its boundary, written down because a question list that only says
what it covers teaches a reader to trust it for everything (the same
reason `motion_graphics_vocabulary` refuses an entry with no `never`).

Measured against the 15 recorded interventions the 2026-09-13 audit
classified - 13 typed asks and 6 corrections, of which 15 are distinct
picture-or-cut defects - this watcher would have caught **five** and
would have missed the rest.  The accounting is in
`docs/WATCHING_THE_BUILT_REEL.md` and it is deliberately unflattering:
the corpus is dominated by WHERE SPEECH IS CUT, which is audible and
textual and not in any frame.  Eyes are not the fix for that; they are
the fix for the five.
```
