# Building the nineteen reels end to end, on main

The captain's stop condition needs the pipeline to produce the reels, through
its own steps, with nothing standing beside them. This is the run that answers
it: the exact sequence, what has to exist first, what evidence it must produce,
and what would falsify each claim.

Written to be executable by somebody who was not here.

## Why it must run on `main`

Twice on 2026-09-05 the live reels turned out to have been built from a tree
`main` does not contain - first the creative-brief commit `89c61e6`, then the
whole twenty-eight-commit field-test lane. A run against unmerged branches
reproduces exactly the claim that had to be withdrawn both times. So the run
waits for the reel path to land, and then runs against `main`.

## Prerequisites, and how to confirm each

Confirmed on this machine 2026-09-05 unless noted.

| what | why the run needs it | check | state |
|---|---|---|---|
| ML venv | `manage_project.py run` launches steps with `sys.executable`, so the interpreter running the CLI must carry `whisperx`, `mlx_vlm`, `easyocr`, `torch` (`ML_DEPENDENT_COMMANDS`, AGENTS.md 9) | `<venv>/bin/python3 -c "import whisperx, mlx_vlm, easyocr, torch, librosa"` | present in a sibling worktree; **a fresh worktree has none** |
| `librosa` | `music_analysis` fails loudly without it | same import | OK |
| `ffmpeg` / `ffprobe` | 27 library files shell out to them; every audio and video measurement silently skips itself without them | `which ffmpeg ffprobe` | OK |
| `node` / `npx` | Remotion renders the caption and graphics overlays | `which node npx` | OK, node@22 |
| `remotion-subtitles/node_modules` | the render bundle | `ls remotion-subtitles/node_modules` | **absent in a fresh worktree** - `npm ci` in that directory first |
| Resolve open, project `Podcast (field test)` | placing clips and reading the built timelines. The EXACT string - the neighbouring `Podcast` is the captain's untouchable original (AGENTS.md 5) | project list read-back | must be arranged with the captain; he is at his desk |
| `PIPELINE_SFX_LIBRARY`, `PIPELINE_MUSIC_LIBRARY`, `PIPELINE_PROJECTS_ROOT` | declared absolute paths | `.env` | `.env` carries the paths |
| an LLM backend | `--full-auto api` needs a provider key; `--full-auto agy` is answered by an agent | `env | grep PIPELINE_LLM` | **no API key on this machine.** `agy` only |

The last row is the one that decides how the run is judged, and section
"Evidence" below says why.

## The project

    /Users/prajwal/Documents/content_stuff/video_projects/lucie/geo-podcast

Outside `PIPELINE_PROJECTS_ROOT`, so it is addressed by absolute path in place
of a slug (AGENTS.md 8). Music is opted out. The rough cut is the captain's own
Resolve timeline `GEO Podcast - Synced`, so the run starts from an external
input rather than from the top of the pipeline (AGENTS.md 3, `external_inputs`).

## The sequence

Steps 1 and 2 exist today. Steps 3 onward depend on the reel path landing.

    # 0. snapshot first - the LLM archive is last-write-wins per step and
    #    the next run destroys it
    python3 -m library.tools.replay_bench capture \
        /Users/prajwal/Documents/content_stuff/video_projects/lucie/geo-podcast \
        --id reels-before-<date>

    # 1. what the cut says, per speaker, rooted to source spans.
    #    Needs Resolve open on the exact project name.
    python3 -m library.tools.timeline_transcript ...

    # 2. reel selection - the model step
    python3 manage_project.py run \
        /Users/prajwal/Documents/content_stuff/video_projects/lucie/geo-podcast \
        --step select_reels --rerun select_reels \
        --full-auto agy --llm-timeout 3600

    # 3. the captain approves, through write_proposal, never a hand edit
    # 4. build the reel timelines
    # 5. captions, graphics and SFX per reel THROUGH THE DAG STEPS
    # 6. verify

Steps 4, 5 and 6 are written as operations, not as script invocations:

    python3 -m library.tools.operations --list
    python3 -m library.tools.operations <op> --scope ...

`--list` today reports twelve operations and **none of them is a reel
operation**; `subtitles.render` and `subtitles.render_segment` carry
`project,region` scopes and the rest are `project` only. That is the gap this
run is waiting on.

## Evidence the run must produce, and what falsifies each

A run that can only succeed proves nothing, so each claim is paired with the
observation that would kill it.

**Condition 1 - the scripts are good.**
Evidence: `docs/REEL_SCRIPT_VERDICTS.md` re-derived against the NEW plan, with
the per-reel table regenerated rather than copied.
Falsified by: seven reels still sharing one closer; reel 3 unchanged; any reel
opening on throat-clearing, which nothing currently measures.

**Condition 2 - built through the pipeline, not standalone scripts.**
Evidence: pasted `operations` invocations and their real output; `git grep
reel_subtitles` returning nothing; and at least one **refusal** - an operation
run with a prerequisite absent, naming what produces it.
Falsified by: any step of the build performed by a script rather than a DAG
node; or the refusal not firing, since a path that only ever succeeds is not
proven.

**Condition 3 - creative reasoning is involved.**
Evidence: the run's own archived prompt and response - a decision recorded in
`considered` or `undetermined` that no deterministic rule produces; the craft
role in the prompt's first line; `contradicts_direction` either populated or
honestly empty with the reason.
Falsified by: `considered` and `undetermined` empty or generic; per-reel
justifications that repeat verbatim across reels, which is what exposed the CTA
collapse; a `contradicts_direction` of `[]` with no explanation of why nothing
contradicted.

**Condition 4 - it ran end to end.**
Evidence: the run summary reporting `SUCCESS`, which it does only when the whole
DAG is complete and `failed_steps` is empty in the project ledger - not just the
steps this invocation touched (AGENTS.md 3).
Falsified by: a green step list beside a non-empty `failed_steps`; or a
conformance report whose caption side is `expected 0`.

### The one that needs saying about condition 3

With no API key the only backend is `agy`, and `agy` is answered by an agent.
If the agent answering it is the same one that wrote the prompt and knows which
defect it is hunting, the answer is not evidence about the pipeline - it is the
"worker supplying taste" failure this project has already been bitten by once,
where sixteen reels were chosen by a crewmate with hand-written reasons and no
model was ever reached.

So the answering agent must be a fresh one, given the prompt and a shell and
nothing else. That is how the before-and-after comparison in
`docs/REEL_SCRIPT_VERDICTS.md` was run, and it is how this one must be.

## Proving the fixes rather than assuming them

Three defects were fixed on 2026-09-05 and each has a specific re-measurement:

1. The CTA collapse - count the closer distribution in the new plan the same
   way it was counted in the old. Seven-on-one-passage was the defect.
2. Reel 3 - does it come out trimmed, re-spanned, or not selected?
3. The bad-take detector - `redundant_takes` on the new plan's spans.

A fix that did not change behaviour is a finding, and a more useful one than a
fix that appears to work.
