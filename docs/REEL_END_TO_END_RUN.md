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
| an LLM backend | `--full-auto api` needs a provider key; `--full-auto agent` is answered by an agent | `env | grep PIPELINE_LLM` | **no API key on this machine.** `agent` only |

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
        --full-auto agent --llm-timeout 3600

    # 3. the captain approves, through write_proposal, never a hand edit
    # 4. build the reel timelines
    # 5. captions, graphics and SFX per reel THROUGH THE DAG STEPS
    # 6. verify

Steps 4, 5 and 6 are written as operations, not as script invocations:

    python3 -m library.tools.operations --list
    python3 -m library.tools.operations <op> --scope ...

**UPDATED 2026-09-06.** `--list` reported twelve operations when this was
written and **none of them was a reel operation**. It now reports seventeen,
two of which are: `reel.candidates` and `reel.select`, both owned by
`select_reels`, and their contract REFUSES a project with no timeline
transcript, naming the command that writes one. Step 3 above is that.

Steps 4 and 5 - the timeline BUILD and the per-reel caption/graphics pass - are
still not operations, and that is a finding rather than a gap left open:
`docs/REEL_BUILD_HAS_NO_OWNING_NODE.md` says which node was checked and why
registering the build under it would produce a contract that refuses for a
reason that is not true. `subtitles.render` and `subtitles.render_segment`
carry `project,region` scopes and the rest are `project` only.

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

With no API key the only backend is `agent`, and `agent` is answered by an agent.
If the agent answering it is the same one that wrote the prompt and knows which
defect it is hunting, the answer is not evidence about the pipeline - it is the
"worker supplying taste" failure this project has already been bitten by once,
where sixteen reels were chosen by a crewmate with hand-written reasons and no
model was ever reached.

So the answering agent must be a fresh one, given the prompt and a shell and
nothing else. That is how the before-and-after comparison in
`docs/REEL_SCRIPT_VERDICTS.md` was run, and it is how this one must be.

## What the runs-now half actually produced, 2026-09-05

Executed before Resolve was available, against an isolated 1.8 MB copy of
the project in scratch - `pipeline_data.json`, `project.yaml`,
`creative_brief.md`, the cached transcript and the approved plan, with
`project_folder` repointed. Nothing was written to the captain's state.

    PYTHONPATH=<repo root> python3 library/processes/edit_video/run_pipeline.py \
        --project <copy> --step select_reels --rerun select_reels \
        --full-auto agent --llm-timeout 3600

The `PYTHONPATH` is not optional: `run_pipeline.py` does
`from library.tools...` at module scope and dies with `ModuleNotFoundError`
without it. And `--step X --rerun X` reported "scan: re-runs (step code
changed), catalog: re-runs (step code changed)" and invalidated their
cached preflight output even though only `select_reels` was selected -
worth knowing before this is pointed at a real project.

The agent request was answered by a FRESH agent, given the request file and
a shell and nothing else. It completed in 909.8s.

### What the run proved, and what it honestly did not

The step's own input line settles two questions at once:

    Inputs: ['project_folder', 'creative_brief', 'timeline_transcript']

`timeline_transcript` is satisfied from the disk cache by the runner - no
Resolve was opened at any point - and `creative_brief` is there, which on
the pre-batch-2 tree it was not.

**The summary reported `PARTIAL`, not `SUCCESS`**, with `Ledgers:
preflight 0/6, edit 2/22` and 25 steps never completed. That is the rule
working: `SUCCESS` is reserved for a complete DAG with an empty
`failed_steps` in the project ledger, and a single-step invocation cannot
earn it. **Do not read a green step beside a PARTIAL summary as an
end-to-end run.**

It also recorded, in the summary itself:

    What the steps could not determine:
      select_reels: Exactly where Craig's audio ends inside the 61
      transcript rows that straddle a cut.
    Where a step's measurements contradicted the direction:
      measured nothing that contradicted: select_reels

An honestly empty contradiction rather than a silent one.

### The plan-side conformance checks

Run against the approved nineteen with the expected caption cards derived
THROUGH THE PIPELINE - `reel_ranges` to `reel_spine.spine_for_reel` to
`operations.get("subtitles.plan")`:

| | |
|---|---|
| caption cards derived | **832** |
| reels refused for want of a reference | **0** |
| F6 overlapping caption cards | **1** |
| F7 caption cards under 0.5s | **39** |

Every one of those numbers was unreachable this morning. The plan side
was `()`, so F2, F5, F6 and F7 were disabled and the verifier reported
"captions expected 0, actual 762" beside a pass. The 39 short cards are
real findings on reels the captain has already approved.

### Where it genuinely stops

Everything above needs no Resolve. What remains is the timeline BUILD
(`reel_build.py`, 5 Resolve call sites) and the timeline-side half of the
verifier (3 sites) - reading the built reels back. Those wait on the
captain's session.

One further limit, stated because it would be easy to overclaim: the
registry now carries fifteen operations and the reel path is reachable
through named operations, but an operation still RESOLVES its entry point
rather than gathering its own inputs. This run therefore went through the
DAG RUNNER. "Built through the pipeline rather than a standalone script"
is proven; "driven by naming an operation" is not yet.

**2026-09-06, on that last sentence.** An operation now gathers its own inputs
and checks its own contract - `Operation.execute` - and `select_reels` is
addressable as `reel.candidates` / `reel.select`. What "driven by naming an
operation" still does NOT cover for a reel is the two stages after selection,
for the reason in `docs/REEL_BUILD_HAS_NO_OWNING_NODE.md`. A second limit,
measured: an operation whose step body takes the whole input as one `data`
parameter - four already did, and both reel operations join them - cannot be
driven through `execute` at all, because `Operation.gather` returns the step's
inputs and a post-bridge's `data` is those PLUS the pre-bridge output PLUS the
model's answer. Those are reached through `.run(...)`, as
`reel_build.reel_subtitle_segments` already reaches `subtitles.plan`.

## Proving the fixes rather than assuming them

Three defects were fixed on 2026-09-05 and each has a specific re-measurement:

1. The CTA collapse - count the closer distribution in the new plan the same
   way it was counted in the old. Seven-on-one-passage was the defect.
2. Reel 3 - does it come out trimmed, re-spanned, or not selected?
3. The bad-take detector - `redundant_takes` on the new plan's spans.

A fix that did not change behaviour is a finding, and a more useful one than a
fix that appears to work.
