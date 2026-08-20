# Project 001, end to end: findings, evidence, proposals

Live record of the 2026-08-17 effort to produce a video from project 001
that the captain would publish. Written as it happens, so the findings are
in the order they were met rather than the order they matter.

Scope note: **audio is out of scope by the captain's ruling of 2026-08-15.**
Nothing here proposes automating the mix. Where a finding touches sound it
says so and stops.

Companion documents: `docs/PIPELINE_PLAN.md` is the plan spine, and every
proposal below cites the section it implements.

---

## 0. Where the run stands

| | |
| --- | --- |
| Project | `/Users/prajwal/Documents/content_stuff/post a day keeps the apple away/001`, referenced in place |
| Footage | 17 clips, 13.5 min, all 1920x1080; 7 carry `rotation=-90` (display portrait), 10 are true landscape |
| Every talking-head clip | true landscape - so framing is this project's central problem, not a detail |
| Baseline export | `exports/Pipeline_Edit.mp4`, 59.4s, 1080x1920 @30, rendered 2026-08-15 at an older head |
| Caches | vision profiles, transcription index, `pipeline_output/` and `pipeline_data.json` moved to `_archive_2026-08-17_pre_rerun/` so vision and transcription really execute. **Do not do this again** - it is what made the transcription cost forty minutes twice, and there is now a supported way: `--rerun edit` resets the creative work and is structurally incapable of discarding enrichment. See AGENTS.md section 3 and `library/tools/step_ledger.py` |
| Test suite | 1031 passed, 7 skipped on system `python3` (12.5s) |
| Interpreter | the run uses the worktree `.venv` (whisperx, librosa, cv2, mlx_vlm); the bare system `python3` has no whisperx, so `--project` must be invoked with the venv's python |

---

## 1. Six defects that made a correct run impossible, all fixed

These are mechanical - a hardcoded fixture, a broken import, an undeclared
input, a gate asserting a layout nothing reads. None is a creative choice,
so under the alignment contract they were fixed rather than proposed. They
are batched here because they share one shape: **something reported success
over data that was not there.** That is the failure mode
`docs/PIPELINE_PLAN.md` section 1 exists to name.

### 1.1 The runner carried a hardcoded answer for `speech_sequence`

`present_llm_step` contained, ahead of every backend branch:

```python
if node_id == "speech_sequence":
    mock_data = json.load(open("/Users/prajwal/.../001/pipeline_data.json.bak2_migrated"))[...]
    return mock_data
```

Landed in #99 as a proof harness. It fired for **every project and every
backend**. Under `--full-auto agy` the runner wrote the request file,
waited out the LLM, then discarded the answer and returned project 001's
stale narrative from a backup file that exists on one machine. No correct
run of any project was possible while it was there.

Guard: `tests/test_runner_no_fixture_shortcuts.py` fails on the removed
code two independent ways - an absolute path into a home directory, and an
`if node_id == "..."` branch inside `present_llm_step`. Both verified
against the parent revision.

### 1.2 `tools.paths` never imported, so the asset libraries never loaded

`run_pipeline.py` put one entry on `sys.path` - `library/tools` - then
imported two ways off it:

```python
from model_lifecycle import unload_all     # resolves
from tools.paths import sfx_library_path   # ImportError
```

All three `from tools.paths import ...` sites sat inside `except
ImportError` handlers, two of them `pass`. `PIPELINE_SFX_LIBRARY` and
`PIPELINE_MUSIC_LIBRARY` therefore reached no run, and `--slug` resolution
was dead for the same reason. Projects whose `pipeline_data.json` already
carried an `sfx_library` from an earlier era kept working, which is why it
survived; a genuinely fresh project cannot start.

### 1.3 An entry step could not receive the one input it requires

`validate_sfx_library` is an entry node - no incoming edges, so no
`data_mapping` can route anything to it. Its `step.py` reads
`data["sfx_library"]` and exits 1 without it. Its manifest declared
`interface.inputs: []`. Unrunnable by construction on any fresh project.

`gather_step_inputs` now supplies a process-level input to a step that
**declares** it, and only to that step. `tests/test_entry_step_inputs.py`
reads `step.py`'s own AST for the keys it pulls off the stdin payload and
asserts the manifest declares them.

### 1.4 The SFX gate failed the real library and passed an empty mock

Step 0.01 asserted `library_analysis.json` and `library_semantic.json`
inside `profiles/`. The only reader, `sfx_library.load_sfx_index`, reads
`<library>/sfx_index.json` first and lists both filenames in
`_NON_ENTRY_FILES` - it *skips* them.

Backwards in both directions, and measured:

| library | old gate | new gate |
| --- | --- | --- |
| `PIPELINE_SFX_LIBRARY` - 78 entries, 78 files on disk, 7 matchable types | **INVALID** | VALID |
| project-local `mock_sfx/` - one profile, no audio at all | **valid** | fails: "every SFX placement would be silent" |

So every run that reported SFX placement under `mock_sfx` placed sounds
that could not be heard, and the gate called the library fine. The gate now
checks what would actually break the sound: the index loads, at least one
entry points at a file that exists, at least one type is matchable.

**This one has a sound consequence to note, not to act on:** the run now
draws from a real 78-entry library where before it drew from nothing. SFX
becoming audible is the pipeline working as designed, not a mix decision,
and nothing here touches levels - `SetProperty("Volume", ...)` returning
False on Resolve 21 is unchanged and still unaddressed by ruling.

### 1.5 Every step was killed at 600 seconds

Both step runners carried `timeout=600`. That is, to the second, the
number the DAG carries as `semantic_analysis`'s
`estimated_duration_seconds` - **a planning estimate used as a deadline**.

Measured on this footage: 17 clips, 13.5 minutes, 45 to 90 minutes of
local vision. The step was killed four clips in, and then **retried**,
because `_is_transient` matches "timed out". `temporal_index`,
`render_subtitles` and `render` are all past ten minutes on real footage
too.

This is the direct answer to something `docs/PIPELINE_PLAN.md`'s Handover
records as a mystery - "no project on disk has any footage or a completed
run". It was not that nobody tried. It could not finish.

The ceiling that remains is four hours, exists to break a wedge rather
than to enforce an estimate, and is overridable
(`PIPELINE_STEP_TIMEOUT_SECONDS`, 0 for none). The per-clip analyser
ceiling moved too: it was also 600s, an overrunning clip is **skipped
rather than fatal**, and 001's longest clip is 188s - so the tight ceiling
was set to silently drop the longest and most important footage.

Step stderr is now streamed line by line instead of held behind
`capture_output=True`. A 90-minute vision pass that prints nothing until
it exits is indistinguishable from a hung one, which is exactly the state
this runner was in while being watched.

### 1.6 The vision cache never hit, so every run re-analysed everything

`vision_pipeline_v3` writes `clip_profile_<stem>_v3.json` and skips a clip
whose profile is already there - the stem is its cache key. Step 1.03's
"already analysed?" check compared the **catalog's synthetic clip ids**
(`clip_001`) against the names on disk (`IMG_1806_v3`). It never matched.
Every clip was re-analysed on every run.

The `_rename_profile` helper that was supposed to reconcile the two names
never fired either: it looked for `clip_profile_<stem>.json`, a name the
analyser stopped writing. Had it ever fired it would have broken the
analyser's own by-stem cache instead.

Its four tests all passed. They proved the helper was careful about
overwriting; they could not show that it ran. That is the same shape as
the `smart_reframe` reader in `docs/PIPELINE_PLAN.md` section 1 - coverage
for something that never executed - and it is worth recording because the
tests read as protection for the expensive path while the expensive path
had none.

Verified on the restarted run: `Semantic Analysis: 17 total clips, 4
already analyzed, 13 remaining`. Before the fix that line always read
`0 already analyzed`.

---

## 2. Framing: the decision this project turns on

Implements `docs/PIPELINE_PLAN.md` **Q1** and **Phase 1 / P1.2**.

Q1 was answered on 2026-08-16 - letterbox stays the default, the tracking
gets built - but that answer was given against synthetic frames. This is
the first time the question has been asked with **project 001's own
footage** in front of it, and the numbers are harsher than the abstract
version suggests.

Every talking-head clip in 001 is true 1920x1080 landscape. Filling a
1080x1920 frame from it needs a **3.16x zoom** relative to fit, which keeps
**31.6% of the source width** (608 of 1920 px). Letterboxing instead uses
**32% of the phone screen** and leaves 65% black.

Four options, all rendered from one real frame - IMG_1816 at t=40s, a clip
the edit actually uses. Open them side by side; they live beside the
project, at
`001/_archive_2026-08-17_pre_rerun/framing_options/` (`src_1816_t40.jpg` is
the ungraded source frame they are all cut from):

| | file | what it looks like |
| --- | --- | --- |
| a | `a_letterbox.jpg` | 1080x608 strip, black above and below. Today's default. |
| b | `b_fill_centre.jpg` | fills the frame; **the subject's face is cut off at the right edge** |
| c | `c_fill_tracked.jpg` | fills the frame, subject complete and centred - a normal vertical selfie shot, very tight |
| d | `d_blur_backdrop.jpg` | strip at native size over a blurred, darkened fill of the same frame |

Measured, not assumed: the Haar face centre on that frame is
`x = 0.626` of source width, which is why (b) loses the face and (c) keeps
it. The mechanism for (c) already exists and is tested -
`subject_framing.py` plus `_conform_fields`, delivered under P1.2 - and it
is off for this project only because `default_brand.yaml` declares no
`style.framing_intent`, so the legacy heuristic runs and chooses letterbox
whenever the subject is visible, which is every A-roll clip here.

**Proposal, for approval:** make 001 render (c) for talking-head A-roll -
subject-tracked fill - and keep letterbox for the scenery/B-roll clips
where nothing is cut off by filling. Concretely that is a
`style.framing_intent` on the template 001 uses, plus per-clip override
where the spine says the shot is scenery.

**Alternatives, briefly.** (a) is the safest and is what ships today; it is
also the "postage-stamp strip" the plan itself calls the loudest amateur
tell, and at 32% screen use it is hard to reconcile with "every frame
should look deliberate". (b) is never right - it is the beheading the
captain explicitly rejected. (d) fills the screen and keeps everything
visible, and is the option Q1 named that nobody has ever seen; it costs a
compositing route the renderer does not have today, so it is a real build
rather than a config change.

**Not decided here.** Whether the default across all future projects moves
is a separate question; this proposal is about project 001 and the template
it names.

### 2.1 The series document resolves the apparent conflict with the captain

Read after the four frames above, `1_through_the_4th_wall/branding_creative_direction.md`
settles something that looked like a contradiction. Its **Framing**
section says:

> **Close.** Medium-close or tight close-up. The viewer should feel like
> you're 3 feet away, sitting across from them. **Not a wide shot of you
> in a room - that creates distance.** The intimacy of the frame matches
> the intimacy of the words.

Option (a), the letterbox, is precisely a wide shot of a person in a room,
rendered into a third of a phone screen. Option (c) is a medium-close
shot. On this series' own stated terms the default the pipeline ships is
the wrong one, and the tracked fill is the right one.

That does **not** contradict the captain's standing instruction, which is
worth quoting exactly: they would "much rather have black bars and
everything visible" **than a crop that cuts the subject**. The rejected
thing is the beheading - option (b), which the judge also caught. Option
(c) cuts nothing; it is tracked precisely so the subject stays whole. So
(a)-over-(b) and (c)-over-(a) are consistent positions, and the instruction
is not a vote for letterbox in general - it is a veto on losing the
subject.

The measured cost of (c) stands and is not hidden by any of this: it
throws away 68% of the source width, and it can only be trusted where the
face signal is trustworthy. `subject_framing.py` returning None - "the
footage cannot support an answer" - must keep meaning letterbox, not a
guessed centre.

---

## 3. Two contradictions between the project and the captain's own bar

### 3.1 Duration: `project.yaml` says 120s, the planning doc says 60s max

`project.yaml` sets `target_duration_seconds: 120`.
`duration_targets.get_target_duration_zone` treats a project value as an
override of the brand default, so the target zone becomes 108-132s.
`step_2_02`'s handoff then instructs the selector to aim for ~75% of the
target, i.e. ~90s of speech.

The captain's planning doc is unambiguous
(`PLAN/series portfolio '26 planning/overall_branding_creative_direction.md:362`):

> **Every video is a maximum of 60 seconds.** Punch Card specifically
> targets 30 seconds.

and the series 001 belongs to, "Through the 4th Wall", is listed at
"Monologue (≤60 sec)". The 2026-08-15 baseline export came out at 59.4s.

**ANSWERED 2026-08-17: 60 seconds.** The planning doc is the creative
authority and is later and series-specific; the captain accepted the 59.4s
baseline. The earlier abstract "an explicit project target wins" precedent
was not a ruling about 001. Recorded for captain override.

`project.yaml` now reads `target_duration_seconds: 60`, with the reasoning
in a comment beside it so the next reader does not undo it.

### 3.2 Brand template: 001 names none, so it gets the fallback

`project.yaml` declares no brand template, so 001 resolves to
`default_brand.yaml`, whose own comments describe it as the fallback that
exists "to render something legible rather than to express a brand": a
deliberately accent-free neutral palette, `house_look: pmk_default`, no
`framing_intent`, no motion accents.

Of the four shipped templates, `shortform_energetic` is the one
`AGENTS.md` describes this pipeline as making, and it declares
`framing_intent: 1.0`, `house_look: electric_contrast` and
`motion_accents: true`. This is `docs/PIPELINE_PLAN.md` **Q9**, unanswered.

Reported, not proposed - the right answer may be a new template authored
from `PLAN/series portfolio '26 planning/1_through_the_4th_wall/`, which is
a design job rather than a config change. Flagging it because "which
template" silently decides the look, the framing and the captions, and 001
is currently getting the one that was written to express nothing.

**Followed up in section 5**, which traces the second half of the same
gap - the brief the template was never going to supply either - and
carries the ruling: the wiring is repaired, the new template is deferred
until the captain can judge it against a render.

---

## 3.5 The judge, run on real footage for the first time

Implements `docs/PIPELINE_PLAN.md` **Phase 4.5**, whose calibration step is
recorded there as **blocked on real footage** - the existing calibration
frames are synthetic test patterns the model reads as charts.

The four framing frames in section 2 are that real sample. gemma-4-12b via
`mlx_vlm`, asked the grounded closed questions in
`library/tools/perceptual_qa.py`, 7-21s per frame on this machine:

| frame | `black_bars` | `main_subject_fully_visible` | `what_is_wrong` |
| --- | --- | --- | --- |
| a letterbox | `top_and_bottom` | true | "large black bars at the top and bottom" |
| b fill, centre | `none` | **false** | "the main subject's head and body are cut off by the edges of the frame" |
| c fill, tracked | `none` | true | *(empty)* |
| d blur backdrop | `top_and_bottom` | true | "large black bars at the top and bottom, which are not part of the intended video content" |

**Three of four correct, and it agrees with the recommendation in section
2 without being told what to prefer.** It catches the letterbox, it
catches the beheading and names which edges, and it returns clean on the
subject-tracked frame - the two loudest defects in
`docs/PIPELINE_PLAN.md` section 2, neither of which any technical gate can
see.

**The fourth is the first measured false positive, and it matters to the
decision above.** There are no black bars in (d) - there is a blurred,
darkened backdrop. If option (d) were ever adopted, this gate would flag
every frame of every video. Worth knowing before choosing it, and worth
recording as the first real-footage evidence about the false-positive rate
that Phase 4.5 says the gate needs before it could ever become fatal. It
stays observation-only.

**And it found a hole in the gate itself.** On frame (a) the model
answered `main__subject_fully_visible` - two underscores. `parse_verdict`
skipped the unrecognised key and the dimension simply vanished; the frame
reported **clean** on a question nobody had answered. Fixed: unanswered
dimensions are collected, excluded from `clean`, and counted per dimension
in the summary.

That fix also settles a standing note. The plan keeps `text_legible` as
"an unverified dimension awaiting a real sample". The three captured
calibration replies never answered it **at all** - and all three still
reported clean. On the four real frames it answered `true` every time,
including on frames with no text on them. It is now a recorded test rather
than a sentence, but the dimension's fate should be decided on the render,
which does have captions.

---

## 3.6 Proposal: judge the export, not only the timeline

**Awaiting approval - not implemented.** Implements
`docs/PIPELINE_PLAN.md` Phase 4.5.

gemma-4-12b is already the perceptual gate's model:
`visual_qa_router.run_perceptual_observation` loads it through
`library/tools/vision_model.py`, and the plumbing works. So the design
call is not "which model" - it is **what it looks at**.

Today it grabs frames off Resolve's Deliver page during step 6.01, mid
build, behind `PIPELINE_PERCEPTUAL_QA`. That has one real advantage:
catching a framing problem before the export is paid for, and it can name
the clip a finding belongs to.

**Proposal: keep that, and add the same questions against the finished
mp4 in step 6.02.** Four reasons, in order of weight:

1. **It judges what ships.** 6.01 judges the timeline; 6.02 judges the
   file that goes to Instagram, after the export's own scaling and
   compression. Those are close but not the same picture, and the plan's
   own rule is to judge by what the reader produces.
2. **6.02 already probes the mp4.** `render_qa` measures resolution, fps,
   duration, LUFS and black frames on that exact file. The perceptual
   questions belong beside the technical ones, in the same report, on the
   same artifact.
3. **It runs without Resolve.** Any export can be re-judged at any time,
   including the ones already on disk, which is what makes calibrating it
   against the captain's eye practical rather than a rebuild each round.
4. **It is cheap.** 7-21s per frame measured above, bounded to the same
   6-frame even sample, all local.

Findings would carry a **timestamp** rather than only a frame index, so
"at 0:34 the subject is cut off" is checkable by scrubbing.

**It stays observation-only.** One false positive out of four frames is
the entire evidence base about its false-positive rate. Nothing here
should fail a render until the captain has looked at a set of frames
beside the verdicts and said whether they agree - which is the calibration
step Phase 4.5 names and which this run can finally supply.

**Alternative considered:** move the gate to 6.02 outright and drop the
6.01 hook. Rejected - catching a bad frame before paying for the export is
worth keeping, and the two are not redundant, they watch different
artifacts.

---

## 4. The run survived its own runtime, and what that proves

The agent runtime driving the run died on 2026-08-17. The pipeline did
not: `run_pipeline.py` was detached, and it kept analysing for the whole
interval, banking one profile per clip. Picked back up mid-pass with 10 of
17 clips already on disk and the cache hitting on every one of them.

That is the fix in section 1.5 and section 1.6 demonstrated rather than
argued. Before them, a run of this footage could not survive its first
step; this one survived losing the process that started it.

The `--full-auto agy` backend is a **file handshake**, not an API: the
runner writes `pipeline_output/llm_requests/<step>.json`, prints
`LLM_REQUEST_READY`, and polls for `llm_responses/<step>.json`
(`run_pipeline.py:679-742`). The agent IS the LLM. That is why the run
could be adopted by a different agent without restarting it, and it is
worth knowing before anyone treats a stalled hybrid step as a hang.

---

## 5. The creative inputs project 001 does not declare

Implements `docs/PIPELINE_PLAN.md` **Q9**, and the `creative_brief` path
that no section of the plan has yet claimed.

`001/project.yaml` declares `name`, `slug`, `client`, `status` and now
`target_duration_seconds`. Nothing else. Two consequences, both traced to
the line that applies them:

**1. No `brand_template`, so 001 gets `default_brand`**
(`run_pipeline.py:618`, `project_config.py:54`). That template's own
comments describe its job as "to render something legible rather than to
express a brand". It carries no accent colour by construction, and
declares `house_look: pmk_default`, `font: Helvetica`,
`energy_profile: high`, `sfx_density: dense`, and no `framing_intent`.

**2. No `creative_brief` - and setting one would not help, because the
path is inert.** Seven handoffs document the input -
`creative_direction`, `speech_sequence`, `music_selection`,
`select_broll`, `plan_transitions`, `plan_vfx`, `plan_sfx` - each with a
paragraph beginning "When a `creative_brief` is provided in the input, it
contains the captain's creative direction as a rich markdown document.
Read it in full before making any creative decisions."

No step ever receives one. Traced, and it is broken in **three
independent places**, any one of which is sufficient:

| # | Where | What is wrong |
| --- | --- | --- |
| 1 | `run_pipeline.py:396` | The loader fires only `if "creative_brief" in step_inputs`, and `step_inputs` is the step's own `manifest.json` `interface.inputs` (line 375). **No step manifest declares it** - `grep -l creative_brief library/steps/*/manifest.json` returns nothing. |
| 2 | `run_pipeline.py:351` | The non-routable globals whitelist is `project_folder`, `brand_template`, `project_config`. `creative_brief` is absent, and `PROCESS_LEVEL_INPUTS` (line 71) is only `sfx_library`/`music_library` - so the path could not reach `inputs` even for a step that did declare it. |
| 3 | `run_pipeline.py:269` | The process-manifest fallback names `creative_brief` in its tuple, but `processes/edit_video/manifest.json` declares only `project_folder`, `sfx_library`, `music_library`, `brand_template`. There is no entry to fall back to. |

So `ProjectConfig.creative_brief` (`project_config.py:55`) round-trips
through `pipeline_data.json` and stops there. Writing
`creative_brief: <path>` into any `project.yaml` today changes nothing at
all, silently. Seven handoffs instruct an LLM to read a document the
runner cannot hand it.

This is `docs/PIPELINE_PLAN.md` section 1's "inert: recorded, never read"
category, and it is the largest instance of it found so far: it is not one
unread key, it is the entire channel by which the captain's creative
direction was supposed to enter the pipeline. Every creative decision in
every project to date has been made without it.

It is absent, not missing. The direction exists, in detail, at
`PLAN/series portfolio '26 planning/1_through_the_4th_wall/`, and it is
specific enough to change the edit:

| The series says | `default_brand` says |
| --- | --- |
| "Minimal cuts. Let moments breathe." Cut on pauses, never mid-sentence | `energy_profile: high` |
| B-roll "sparingly, if ever - the default is: stay on you" | (no guidance; B-roll planned freely) |
| "Soft audio fades only. No hard cuts, no stingers, no whooshes" | `sfx_density: dense` |
| Captions in Nanum Pen Script, Warm Ivory `#FFF1DA`, Ice Blue `#00BFFF` for rare emphasis | `font: Helvetica`, no accent |
| Grade warm amber, "rich and alive - NOT desaturated" | `house_look: pmk_default` |

The register is not merely unset. On energy and SFX density it is set to
the opposite of what the series asks for.

**Half the mechanism exists.** `run_pipeline.py:396-405` does resolve the
path correctly once it is asked to - absolute paths are accepted, so the
read-only `PLAN/` tree can be cited in place and never copied. What is
missing is the wiring that asks: a declared input on the seven step
manifests, and a route for the path to reach them. That is a small,
mechanical change with an enormous creative consequence, which is why it
is proposed here rather than batched with the section 1 fixes.

And one of the four shipped looks, `warm_reflection`, is already authored
*from this series' own branding document* (`house_look.py:187-195`): warm
amber highlights, warm shadows that never go blue, medium-high contrast,
falloff into Deep Espresso. It is named today only by
`cinematic_narrative`, whose `target_duration_seconds` is 180-600 and
therefore wrong for a 60-second monologue.

So no shipped template fits 001: `default_brand` has the right duration
and no identity, `cinematic_narrative` has the right look and grade and
the wrong duration and framing, `shortform_energetic` is the opposite
register, `interview_professional` is a different format.

**Proposal - two parts, and the first is much cheaper than the second.**

> **RULED 2026-08-17: (a) approved and implemented in `61d17b9`. (b) NOT
> authorised** - it returns to the phase boundary with rendered evidence,
> because `warm_reflection` already exists and the captain should decide a
> look with pixels in front of them. The reasoning given for (a): the
> captain ruled the planning docs ARE the creative authority (Q6/Q8), and
> the handoffs already claim this input, so making it arrive is
> conformance to accepted intent rather than a new creative choice.
>
> The baseline export in progress stays **unaided** - 001 declares no
> brief yet - so there is a with/without comparison rather than an
> assertion.

*(a) Repair the `creative_brief` path, then point 001 at the captain's own
document.* Declare the input on the seven step manifests that already
document it, add `creative_brief` to the non-routable globals whitelist,
and set one key in `project.yaml` to the absolute path of
`1_through_the_4th_wall/branding_creative_direction.md`. No new asset, and
the `PLAN/` tree stays read-only and in place. Every creative step then
reads the direction it was always designed to read. This is the single
highest-leverage change available; it is reversible by deleting the key,
and a test asserting the brief actually reaches a step's prompt is what
stops it going inert a second time.

*(b) Author a `through_the_4th_wall` brand template* - `warm_reflection`
for the look, calm energy, 30-60s duration, sparse SFX, restrained
transitions, and the caption colours the series names. This is a design
job, not a config change, and it has one hard dependency: the series
specifies **Nanum Pen Script**, which is not bundled, and
`tests/test_bundled_fonts.py` fails a template naming a font that is
neither bundled nor an accepted system font. Nanum Pen Script is licensed
OFL 1.1, the same licence as the Montserrat already shipped under section
11 of `AGENTS.md`, so bundling it is permitted - but it is a separate
piece of work with its own licence file.

**Alternatives, briefly.** Leave 001 on `default_brand` and judge the run
as-is: honest about what the pipeline does unaided, but it measures the
pipeline against no brief when a brief exists, and the result cannot be
"a video the captain would publish" except by accident. Or point 001 at
`cinematic_narrative` for the warm grade alone: gets the look, but drags a
180-600s duration band and full letterbox into a 60-second vertical
monologue.

**Not proposed:** hand-feeding the brief into my own answers to the LLM
steps while leaving `project.yaml` empty. It would improve this one run
and leave the pipeline exactly as blind as it is now, and the next run
would not reproduce it. If the brief should be read, it should be wired.

---

## 6. The baseline export, and why it is not evidence about today's code

`exports/Pipeline_Edit.mp4` is the only rendered artifact that exists
right now: 59.41s, 1080x1920, h264 + aac 48kHz stereo, rendered
2026-08-15. Technically it passes everything except loudness (-18.29 LUFS
against a -14 target, and audio is out of scope by ruling).

Frames pulled at 2s, 20s, 30s, 50s and 57s show four things:

1. **Caption words run together.** "thepeoplebehind" at 2s, "verysmall"
   at 30s, "evenif i hate it," at 57s - the space is lost at the boundary
   of an emphasised word, while an unemphasised run ("just kind of enter",
   50s) spaces correctly.
2. **Four cyan corner brackets and a blue progress bar** on every frame
   carrying picture.
3. **Heavy grain**, loudest in flat sky areas.
4. **One frame with no subject and no discernible content at all** at 20s
   - an extreme-close blur that reads as a mistake, not a shot.

**Three of those four are already answered, and that is the point of
recording them.** The caption overlap was diagnosed and fixed in `6760f0d`
on **2026-08-16 - one day after this export was rendered**; the fix
replaced `transform: scale()` on an emphasised word (which grows a glyph
without reserving layout width) with `fontSize`, and the code now carries
the diagnosis as a comment. The corner brackets and progress bar became
template-declared under P3.1
(`step_4_06_render_motion_graphics/step.py:99-103`), and `default_brand`
declares no motion accents, so they should not be drawn at all on a run at
head.

**So this export is not a measurement of the current pipeline, and no
finding from it should be reported as one.** It is recorded because it is
the only picture anyone has today, because it gives the new render a
specific checklist - captions spaced, brackets absent, grain, the 20s
shot - and because the gap between "the defect is visible in the export on
disk" and "the defect is in the code" is exactly the confusion this
document exists to prevent.

The fourth item, the unusable 20s frame, is **not** known to be fixed and
is the one to watch: nothing in the pipeline judges whether a chosen shot
shows anything. That is what the perceptual gate is for, and it was not
running when this was made.

---

## 7. A measurement that cannot be taken, fed to the step that needs it

Read off this run's own profiles, all 12 banked so far:

```
speech_present: false      on every clip, including the 188.6s and
speech_coverage: 0.0       139.1s talking-head takes
camera_stability: "unknown"  while camera[].stability says "shaky"/"stable"
usable_ranges_method: "unmeasured"
```

`speech_present` is not measured from audio. It is
`bool(transcript and transcript.strip())`
(`vision_pipeline_v3.py:954`), and the profile's `transcript` is `""`.

It is empty for a structural reason, not a transient one. In
`dag.json`, `semantic_analysis` and `temporal_index` are **siblings** -
both depend only on `scan`, and neither depends on the other - and
WhisperX runs in `temporal_index`. Whichever way the topological sort
breaks the tie, the vision pass here runs first and no transcript exists
yet. So on this DAG `speech_present` is **always** false and
`speech_coverage` **always** 0.0, for every clip of every project.

That would be harmless if nothing read it.
`step_2_02_speech_sequence/manifest.json:104` declares
`semantic_analysis_documents.*.assessment.speech_coverage` in its
`context_fields`, so the step that chooses which passages become the video
is handed "0% speech coverage" for all 17 clips, including the ones that
are nothing but speech.

This is the failure mode `AGENTS.md` names under "One vision schema, two
views" - "an absent field warns in `context_projector`, a fabricated one
silently misleads the model" - and it is also the `fills_frame` discipline
from `perceptual_qa.py`: a field with one possible value ranks nothing.
Here it is worse than ranking nothing, because 0.0 is a plausible
measurement rather than an obvious absence.

**Not proposed yet, and deliberately so.** The fix could be an edge
(`temporal_index -> semantic_analysis`, paying a full serialisation of the
two slowest analysis steps), or dropping the two fields from the profile
and from 2.02's context, or computing coverage from a cheap VAD pass. Which
one is right depends on whether anything else wants a transcript at vision
time, and that is worth answering with the run's own numbers in hand rather
than ahead of them. Recorded here so the 0.0 in the next report is read as
this, and not as a fact about the footage.

---

## 8. Three more run-blocking defects, and where the run stopped

Work paused by the captain on 2026-08-17 partway through this section.
Everything below is measured; nothing below is a proposal.

Each of these three had the same shape as section 1's six, and each on
its own made a correct video impossible. They are recorded together
because the pattern is now unmistakable: **the pipeline's failures are
silent by default, and every one of them was found by watching a real
run rather than by reading the code.**

### 8.1 The step subprocess deadlocked on its own result - FIXED (`4ac62a8`)

`semantic_analysis` finished all 17 clips, printed "Collected 17 clip
profiles", and stopped dead for eight minutes. `sample` showed the child
in `_Py_write_impl -> write()` and BOTH parent threads in `read()`.

`_run_step_subprocess` echoed stderr from a pump thread while
`communicate()` ran on the same `Popen` - and `communicate()` reads
stdout AND stderr. Two readers on one pipe: the selector reports stderr
readable, the pump has already taken the bytes, the main thread parks in
a `read()` that never returns, and from then on nothing drains stdout. The
step blocks in `write()` the moment its result passes the 64KB pipe
buffer.

Introduced by `628b3e2` - the streaming-stderr fix in section 1.5, which
traded a timeout for a deadlock. Each pipe now has exactly one reader.

### 8.2 Transcription could not run at all - FIXED (`126306c`)

Every clip raised, inside `step_1_04`'s per-clip `try/except`:

```
TypeError: TranscriptionOptions.__init__() missing 2 required
positional arguments: 'multilingual' and 'hotwords'
```

and the step carried on, reporting **"0 regions, 0.0s speech, 0 words"
for all 17 clips**. No transcript means no speech selection, no spine and
no subtitles - the entire edit - presented as a successful step.

The root cause is the **interpreter**. On Python 3.14 there is no working
set: `ctranslate2 4.4.0` (which `whisperx 3.2.0` requires) has no `cp314`
wheel, so the resolver substitutes a `faster-whisper` that changed the
signature. Newer `whisperx` needs a `torchaudio` that has dropped
`list_audio_backends` and `AudioMetaData`, which `pyannote.audio` still
calls; older `faster-whisper` needs a PyAV with no 3.14 wheel. Rebuilt on
**3.12**, where a coherent set with a MATCHED `torch`/`torchaudio` 2.8.0
pair resolves unaided - the 3.14 env had torch 2.13 beside torchaudio
2.11.

Verified on the footage, not by importing the module: clip_006 went from
`0 words` to `2 regions, 3.0s speech, 13 words`, and the full pass
transcribed all 17 (clip_017: `5 regions, 80.1s speech, 219 words`).

**A second defect fell out of the rebuild.** `easyocr`/`sam2` pull
`opencv-python-headless`, which provides the same `cv2` module and
shadowed the correct `opencv-python 4.x`: `import cv2` gave **5.0.0 with
`hasattr(cv2, "CascadeClassifier")` False**, while the 17 cascade XMLs
sat in `cv2.data.haarcascades` looking healthy. The `<5` pin that
`AGENTS.md` documents constrained the package nobody imports. That is the
face signal `subject_framing` needs, so the framing work in section 2 was
one `pip install` away from losing its input with no error.

### 8.3 whisperx logs to stdout, and stdout is the step's result - OPEN

This is where the run stopped, and it is **not yet fixed**.

`temporal_index` did all of its work correctly:

```
Temporal Event Index Complete
  Indexed: 17 clips
  Failed:  0 clips
```

and then the runner rejected it:

```
✗ FAILED (RuntimeError): Step produced invalid JSON:
  stdout: 2026-08-17 11:54:57 - whisperx.asr - INFO - No language specified...
          2026-08-17 11:54:59 - whisperx.vads.pyannote - WARNING - No active speech found
```

The contract is JSON on stdout, logs on stderr. `whisperx` configures a
logger that writes to **stdout**, so its INFO and WARNING lines are
prepended to the step's result and the parse fails. Every clip's real
work - 17 indexes, ~40 minutes of CPU transcription - is already on disk
and is discarded by a logging default.

Note also that the step wrote to `<repo>/pipeline_output/temporal_index`
rather than into the project, because the path resolved against the
runner's cwd. The results are banked there and should not be deleted; the
location is its own small bug.

**The fix is mechanical and unwritten:** silence or redirect the whisperx
logger in `step_1_04` so only JSON reaches stdout. It is the same class
as the rest - nobody chose this, a dependency's default chose it - and it
is worth a general guard, because any dependency that logs to stdout
breaks any step the same way.

### Where the run stands at the pause

| | |
| --- | --- |
| Completed and banked | `validate_sfx_library`, `scan`, `catalog`, `semantic_analysis` (17 vision profiles) |
| Done but rejected | `temporal_index` - all 17 clips indexed and on disk, output discarded by 8.3 |
| `failed_steps` | `["temporal_index"]` |
| Not yet run | everything from `prosody_analysis` onward, including the export |
| Never reached | the gemma-4-12b judgement of a new render, and the `verify_fusion_comps` verdict |

No export was produced in this session, and no creative change was made
to project 001: it still declares no brief and no template, so whatever
is rendered next is still the unaided baseline the with/without
comparison needs.

---

## 9. A complete render, and the five more defects it took

`exports/Pipeline_Edit.mp4`, rendered 2026-08-17 21:04: **54.87s, h264 +
aac, 26 of 26 steps run.** The first end-to-end run this project has
completed. `validate` fails on one metric only - loudness, -20.96 LUFS
against a -14 target - which is audio and out of scope by ruling.

Getting from step 23 to a file took five more defects, all of the same
family as sections 1 and 8 and all mechanical, so all fixed rather than
proposed:

| # | Defect | Commit |
| --- | --- | --- |
| 9.1 | `vision_model` printed "Loading gemma-4-12b..." to stdout, into the middle of `render_subtitles`' result, after all 8 segments had rendered. The stdout guard is now shared (`library/tools/step_stdout.py`) and `temporal_index` uses it too. | `13eb8cc` |
| 9.2 | The transition beat-snap **relocated** cuts instead of adjusting them: the cut planned for the end of the 2.4s hook was placed at **0.196s**, and one planned for 18.37s moved to **11.33s**, which would have dropped seven seconds of speech. `snap_delta_seconds` said `0.0` throughout. | `59af4fa` |
| 9.3 | A **0.004s** abutment - an eighth of a frame - failed the build as "uncovered range... these render as black frames". | `45e9eaf` |
| 9.4 | A **0.002s** float difference between abutting clips failed manifest validation as a clip overlap. | `5d2a9a3` |
| 9.5 | `.env` was opened with no encoding, so under the ASCII locale of Resolve's Fusion subprocess its box-drawing characters raised `UnicodeDecodeError` and killed `apply_fusion_comps` at import. | `e545fce` |

9.2 is the one to remember: it silently moved cuts, reported that it had
not, and only ever affected the transitions that get a Fusion comp - so
any run of pure hard cuts looked healthy. `compile_manifest` caught it,
one step before the render.

Two spine changes were mine, not the pipeline's, and are recorded so the
edit is reproducible: the `fade_to_black` into the outro was dropped (a
per-clip Fusion transition is a HEAD effect on the incoming clip, and the
last block has none), and the outro block itself was dropped (an outro
carries no clip, so it rendered as 1.5s of black that `compile_manifest`
correctly refused). The video now ends on the last word, which is what
the creative direction asked for.

---

## 10. `verify_fusion_comps` on a real render - the standing question, closed

This is the question the brief asks in item 4, and nobody had seen the
station's verdict on a real timeline. **It fired, and on the first build
it failed:**

```
✗ [fusion_comps] fusion_comps_all_missing: expected 7 clips carrying a Fusion comp, got 0
✗ [transitions] transition_defocus_tail_on_v1[0]: expected a Fusion comp on the clip, got no comp
  ERROR: 3 transitions were planned and not one clip on V1 carries a Fusion comp
```

**The station was right, and it earned its place.** Every planned Fusion
effect was absent - three transitions and seven per-clip zooms - because
`apply_fusion_comps` died at import on the `.env` decode (9.5). The
render still produced a perfectly playable mp4. Nothing else in the
pipeline noticed; the failure was one buried `WARNING`.

After the fix, on the same plan:

```
── QA Summary ──
  Station clip_placement: Passed
  Station fusion_comps:  Passed
  Station transitions:   Passed
  Station audio:         Passed
  Station color_grades:  Passed
  Station full_sweep:    Failed
```

So: **planned per-clip Fusion transitions do reach the timeline**, and
the station can tell the difference between a build where they do and one
where they do not. That is the first time either has been demonstrated.

Two cautions on reading it. A `Passed` is **vacuous when nothing is
planned** - the station returns early if `fusion_effects.per_clip` is
empty - so a pass only means something alongside a non-zero count, which
this run has (7). And `full_sweep`'s failure is a **false positive**:
`gap_before_clip_1: 90`, `clip_3: 75`, `clip_6: 74` frames are exactly
the 3.0s intro and the two 2.5s transition slots, which carry B-roll on
V2 by design. It measures V1 contiguity alone, while
`compile_manifest`'s coverage assertion - which considers V1+V2 - passed
on the same timeline.

---

## 11. The judge's verdict on what shipped

gemma-4-12b via `mlx_vlm`, the grounded closed questions in
`library/tools/perceptual_qa.py`, 8 frames evenly sampled across the
export. **0 of 8 frames clean, 0 parse failures, 0 unanswered.**

| t | finding |
| --- | --- |
| 3.43s | "The image is extremely blurry and out of focus." |
| 10.29s | "horizontally stretched and distorted, appearing wider than its original aspect ratio" |
| 17.15s | "in a horizontal aspect ratio, which will result in significant black bars or cropping when displayed in a vertical 1080x1920 format" |
| 24.01s | same |
| 30.86s | "heavily distorted with horizontal motion blur and digital artifacts" |
| 37.72s | `main_subject_fully_visible: false` - "The main subject's upper body is cut off by the top edge of the frame." |
| 44.58s | horizontal aspect ratio |
| 51.44s | horizontal aspect ratio |

**The model found the single biggest defect in the video unprompted, on
five separate frames.** It was never told what aspect ratio the file
was; the prompt says only that this is "one frame of a vertical
short-form video, 1080x1920", and it noticed the frame did not match. It
also caught a cropped subject at 37.72s. This is the calibration evidence
Phase 4.5 says the gate is blocked on, now from a real render rather than
synthetic patterns.

Two dimensions still do not discriminate on real footage: `black_bars`
scored a variance of 1 (always `none` - correct, a landscape frame has
none) and **`text_legible` scored 1 again, answering `true` on all 8
frames including ones with no text**. On `perceptual_qa`'s own rule -
"every dimension must discriminate" - `text_legible` has now failed to
rank anything across three separate samples and should move to
`DROPPED_DIMENSIONS` with its evidence.

The in-render gate at 6.01 did **not** run: `PIPELINE_PERCEPTUAL_QA` was
set and the router was reached, but `mlx_vlm` 0.3.9 in the rebuilt
environment answered `Model type gemma4_unified not supported`. Pinned to
0.6.13 and verified working; the judgement above is on the finished mp4,
which is proposal 3.6 in practice.

---

## 12. What the render needs, for approval

Everything in sections 9-11 was mechanical. **These are not**, and none
is implemented.

### 12.1 The delivery format comes from the footage. It should come from the product.

**This is the biggest defect in the video and the root of most of the
rest.** The export is **1920x1080 landscape**. The bar is 9:16,
1080x1920.

Traced: `step_1_02_catalog_footage/step.py:261` sets
`project_resolution` to the **modal source resolution** -
`max(res_counts)` over the footage. All 17 clips of 001 are 1920x1080, so
the delivery format is 1920x1080. `compile_manifest` reads it
(`step.py:848`) and the Resolve builder creates the timeline from it
(`resolve_build_timeline.py:399`, `✓ Created timeline: Pipeline_Edit
(1920x1080 @ 30fps)`).

The default in that line is `[1080, 1920]`, so this only bites when the
footage is landscape - which is exactly this project, and exactly the
case the whole framing question exists for.

Two consequences beyond the obvious:

1. **The framing work is a no-op right now.** Letterbox versus
   subject-tracked fill (section 2, Q1) is a decision about fitting a
   landscape source into a vertical frame. With target equal to source
   there is nothing to fit, so nothing reframes and the entire P1.2
   mechanism sits idle.
2. **The overlays are already vertical, and it shows.** The subtitle and
   motion-graphics overlays render at 1080x1920 and are composited onto
   the landscape timeline, producing a visible lighter band over the
   central 1080px of the picture - clearly visible at 10.3s, and what the
   judge called "horizontally stretched and distorted".

**Proposal:** delivery format is a property of the product, not of the
footage. Take it from the brand template (with a project override), keep
`[1080, 1920]` as the pipeline default, and reduce the catalog's
`project_resolution` to what it honestly is - a description of the
source, used for conform decisions, not a delivery target.

**Alternatives.** Set it per project only: smallest change, but every new
project can silently ship landscape again. Or infer vertical whenever the
template declares a `framing_intent`: implicit, and couples two unrelated
settings. Or leave it and crop in post: rejected - it discards the
pipeline's reason to exist.

### 12.2 Two hard-coded creative floors override the compass

`step_3_02_select_broll/post_bridge.py:726` rejects any plan with fewer
than 5 B-roll clips - "You MUST select 5-15 B-roll clips for a 60-second
video" - while checking `< 5 or > 20`, so its message and its rule
disagree. `step_4_04_plan_sfx` requires 5-10 SFX.

This run's creative direction, derived from the footage, says B-roll
should stay sparse because the piece is a person talking. Three
assignments were planned; two more were added **only** to clear the
floor, and their rationales say so in the manifest. The same happened
with the fifth SFX.

**Proposal:** make both floors template-declared with a floor of 0, so a
calm series can ask for near-zero B-roll and an energetic one can demand
density. At minimum, reconcile the B-roll message with its own check.

### 12.3 `music_selection` cannot hear the compass

The step's bridge picks the track deterministically and the LLM is handed
an **empty output schema** - there is no decision to make. For this run it
chose `Inspirational Motivational Music Video _ Work Background Music.wav`
(3914s long, `bpm: null`) for a piece whose creative direction says in
terms that it must not be scored as triumphant. It also picked from the
project-local `music/` folder rather than `PIPELINE_MUSIC_LIBRARY`, which
contains exactly one track - `Sickick - Infected`, the track the captain's
own 4th Wall document names.

**Proposal:** give the step's LLM the candidate list and the creative
direction and let it choose, with the bridge resolving the choice to a
file. Reported, not designed - the fix touches which music ships.

### 12.4 The defocus transitions read as damage

Two of eight judged frames are blur findings - "extremely blurry and out
of focus" at 3.43s, "heavily distorted with horizontal motion blur and
digital artifacts" at 30.86s - and both sit on planned `defocus`
transitions. They are now genuinely reaching the picture for the first
time (section 10), which is why this is visible only now.

**Proposal:** look at them before deciding. Either reduce the blur
strength and shorten the 15-frame duration, or withdraw `defocus` and
record the reason in `transition_vocabulary.py`, as the project does for
every other withdrawn type.

### 12.5 Deferred from earlier, unchanged

Section 2/2.1 framing (letterbox vs subject-tracked fill) and section 5's
option (b), a `through_the_4th_wall` template with Nanum Pen Script
bundled. Both were to be decided with rendered evidence; there is now a
render, but **12.1 has to land first** - until the timeline is vertical,
a framing decision cannot be seen.

---

## 13. Where this phase ends

All four items the brief asked for are now answered:

- **A full run to a real DaVinci Resolve export** - done, section 9. 26 of
  26 steps, 54.87s, the first complete run this project has had.
- **gemma-4-12b as the visual judge** - done, section 11. 0 of 8 frames
  clean, and it found the aspect-ratio defect unprompted on five of them.
- **The `verify_fusion_comps` verdict on a real render** - done, section
  10, and the standing question is closed in both directions: the station
  failed correctly when nothing was drawn, and passes now that comps
  reach the timeline.
- **Fix what the run and the judge surface** - the mechanical half is
  fixed and committed (sections 1, 8, 9: fourteen defects). The creative
  half is section 12 and waits on approval.

Waiting on the rulings in section 12, 12.1 first. The render is not one
the captain would publish - it is landscape - and no further creative
work is worth doing until the delivery format is settled, because framing
cannot be judged in a frame the same shape as its source.

---

# Phase 2, 2026-08-19: the delivery-format ruling, and what the render now shows

## 14. The ruling, implemented

The captain ruled on 12.1 as proposed: **the delivery format is a
property of the PRODUCT**, declared by the brand template, overridable
per project, defaulting to vertical 1080x1920. The modal source
resolution the catalog derives is a DESCRIPTION OF THE SOURCE and must
never be the render target again.

`library/tools/delivery_format.py` is the one enumeration, in the house
style of `house_look` and `transition_vocabulary`: four named formats, an
unknown name RAISES, and adding one means adding a row. A brand template
declares `delivery_format`; a project overrides with
`pipeline.delivery_format`; every shipped template now states its frame
explicitly, and `tests/test_delivery_format.py` fails on one that does
not.

The catalog's measurement survives as `source_resolution`. The old key is
retired and a test fails if it reappears anywhere in `library/`, `tests/`
or `scripts/`.

**Two holes found while wiring it, both mechanical, both fixed.**

`project_resolution` was **carried by no DAG edge at all**. Every
`.get("project_resolution", [1080, 1920])` in the tree - in 3.01, 3.02,
4.05 and 4.06 - silently read its own fallback. The readers looked wired
and were not; the only reason the overlays were vertical is that the
fallback happened to be. Consumers now call
`resolve_delivery_format(project_folder)` directly, so there is no key in
flight to lose. `project_fps` had the identical hole - five steps asked
for it, no edge carried it, each read its own `30.0`, and project 001 is
30fps so it never showed. The catalog now declares it and four edges
carry it.

---

## 15. The render, and the defect the format fix uncovered

`exports/Pipeline_Edit.mp4`, **1080x1920 @ 30fps, 54.87s**, and Resolve
logs `✓ Created timeline: Pipeline_Edit (1080x1920 @ 30fps)`.

The first vertical render still had the band down the middle - **scaled
with the clip**, which is what gave it away. Measured on the frame at
t=10.29s:

| | phase 1 (1920x1080) | phase 2, first render |
| --- | --- | --- |
| step at source col 419 | -14.8 levels, rows 96..1035 | -13.4 levels, inside the picture band |
| step at source col 1499 | +1.5 | +12.7 (at frame col 843 = 1499 x 0.5625) |

A 1080-wide, full-height rectangle centred in a **1920-wide source
frame**, scaling with the footage. So it was never the vertical overlays
compositing onto a landscape timeline - **12.1's second consequence was
misdiagnosed**. It is the per-clip Fusion comps.

Every Background node `build_effect_comp` draws - the vignette, the fade,
both halves of a transition - is a solid image merged over `MediaIn`, so
it has to be the size of the image Fusion sees: **the source clip's own
frame**. It defaulted to 1080x1920. Nothing warned, because a
wrong-sized Background is a perfectly valid comp.
`CompEngine.from_params` already carried a `source_res` for exactly this
reason, with a comment explaining it - but the renderer calls
`build_effect_comp`, which did not. **A correct mechanism on a path
nothing executes**, which is the failure mode this project keeps
re-finding, so the test covers the path the renderer actually takes
including that it passes the value.

After the fix, same frame, same columns: **0.4 and 0.1**. The largest
step anywhere in the picture band is 7.1, which is image content. The
regenerated comps carry `Width = 1920 / Height = 1080`.

**One more gate found dishonest.** `verify_resolution` defaults to
1080x1920 and `run_full_render_qa` called it with no argument, while step
6.02 computed `expected_resolution` from the manifest and dropped it on
the floor. The default happened to be right, which is why the gate
correctly failed project 001's landscape master - **by luck, not by
reading the plan**. A series that declares `horizontal_1920x1080` would
have failed its own correct render. It now checks the declared delivery
format.

---

## 16. The judge, against the phase-1 baseline

Same method, same model, same 8 timestamps (the duration is unchanged, so
the frames are directly comparable): gemma-4-12b via `mlx_vlm`, the
grounded closed questions in `library/tools/perceptual_qa.py`.

| t | phase 1 (1920x1080) | phase 2 (1080x1920) |
| --- | --- | --- |
| 3.43s | "extremely blurry and out of focus" | "extremely blurry and low in resolution" |
| 10.29s | "horizontally stretched and distorted, appearing wider than its original aspect ratio" | "large black bars at the top and bottom" |
| 17.15s | "horizontal aspect ratio... significant black bars or cropping when displayed in a vertical 1080x1920 format" | black bars top and bottom |
| 24.01s | same | black bars top and bottom |
| 30.86s | "heavily distorted with horizontal motion blur and digital artifacts" | black bars top and bottom |
| 37.72s | **`main_subject_fully_visible: false`** - "upper body is cut off by the top edge" | `true`; "cropped too tightly on the left side" |
| 44.58s | horizontal aspect ratio | black bars top and bottom |
| 51.44s | horizontal aspect ratio | black bars top and bottom |

**Against the stated baseline, item by item:**

- **0 of 8 frames clean, then and now.** 0 parse failures, 0 unanswered,
  both times. The verdict did not get "better" - it changed subject.
- **The aspect-ratio defect the model named unprompted on five frames is
  gone from all eight.** Not one frame mentions aspect ratio, stretching
  or distortion. It was never told the file's shape either time.
- **The cropped subject at 37.72s is gone.**
  `main_subject_fully_visible` is `true` on all 8 frames.
- **What replaced it is letterbox.** `black_bars: top_and_bottom` on 6 of
  8 - every A-roll frame. The two that answer `none` (3.43s, 37.72s) are
  the portrait B-roll cutaways, which fill the vertical frame. That is
  the correct answer to both, and it is **the captain's own Q1 default
  doing exactly what it was ruled to do**. See section 17.
- **`black_bars` now discriminates.** It scored a variance of 1 in phase
  1 (always `none` - correct, a landscape frame has none) and 2 now. The
  dimension the plan built for this defect has finally ranked something.
- **`text_legible` scored 1 for the fourth consecutive sample**, `true`
  on all 8 frames including ones with no text. On `perceptual_qa`'s own
  rule it should move to `DROPPED_DIMENSIONS` with its evidence. Left in
  place: dropping a dimension changes what the gate measures, and that is
  the captain's call, not a mechanical fix.

The technical gate agrees, and both reports are on disk
(`exports/qa_report.json`, `exports/qa_report_phase1_landscape.json`).
Run through the same `validate_output`:

```
phase1-landscape -> 2 issue(s) found
    [technical] Resolution is 1920x1080
    [audio_levels] LUFS: -20.96, True Peak: -1.06
phase2-final -> 1 issue(s) found
    [audio_levels] LUFS: -20.96, True Peak: -1.06
```

Note that section 9's "validation fails on one metric only - loudness"
was **not right**: `resolution` failed too, at severity `error`. It is
the only `error` remaining now, and it is loudness, which is audio and
out of scope by the ruling of 2026-08-15.

---

## 17. What the render is evidence FOR - all three still the captain's

Nothing in 12.2, 12.3 or 12.4 is implemented. This section is the
rendered evidence they were deferred for, and nothing more.

### 17.1 Framing (section 2/2.1, and Q1) - now judgeable, and loud

All 13 clips compile with `needs_conform: false`. On a 1080x1920 frame a
1920x1080 source fits to **1080x607**, so the picture occupies rows
656-1263 and **68% of every A-roll frame is black**. The judge names it
on all six A-roll frames unprompted.

This is not a defect. It is Q1's ruled default - letterbox stays the
default - reaching the screen for the first time, because until the
delivery format landed there was nothing to letterbox INTO. The captain
now has the two options side by side in one file: A-roll letterboxed
(10.29s, 17.15s, 24.01s, 30.86s, 44.58s, 51.44s) and B-roll filling the
frame (3.43s, 37.72s).

The mechanism for the other answer is built and idle: a template's
`style.framing_intent: 1.0` fills, a spine block overrides per clip, and
`subject_framing` pans the crop to follow the face. **Nothing needs
building to change this - it is one line in a brand template.**

### 17.2 The two creative floors (12.2) - unchanged in this render

`step_3_02_select_broll/post_bridge.py` still rejects a plan with fewer
than 5 B-roll clips while its message says 5-15 and its check says
5-20, and `plan_sfx` still requires 5-10. This run carries the same 5
B-roll assignments and the same 5 SFX as the judged phase-1 edit - the
two B-roll cuts and the one SFX added only to clear the floors are still
in the video, with their rationales still saying so in the manifest.

Deliberately unchanged: keeping the creative half of the edit identical
is what makes the two judgements comparable.

### 17.3 Music (12.3) - unchanged in this render

`music_selection`'s LLM still receives an empty output schema, and the
same 3914-second "Inspirational Motivational Music Video" is still
scoring a piece whose creative direction says in terms it must not be
scored as triumphant. `PIPELINE_MUSIC_LIBRARY` still holds exactly one
track - Sickick, "Infected" - and the bridge still does not look there.

### 17.4 The defocus transitions (12.4) - and a correction to 12.4's evidence

**12.4's premise does not survive the timestamps.** The manifest plans
three `defocus` tails of **10 frames** each (not 15): after V1 clip 0,
2 and 5, so 2.07-2.40s, 18.05-18.38s and 33.05-33.38s - **30 frames of
1646, 1.8% of the video**. Neither 3.43s nor 30.86s, the two frames
phase 1 called blur findings, lies inside any of them. 3.43s is inside
`broll_1` (2.40-5.40s), which is handheld and soft on its own; the
phase-2 judge says "extremely blurry and low in resolution" of the same
frame, and it is B-roll footage, not a transition.

**Judged on the frames that ARE transitions, the model does not call
them damage.** Nine extra frames, same prompt, three inside each tail
and matched controls outside:

| t | inside a tail? | verdict |
| --- | --- | --- |
| 1.80s | no | black bars |
| 2.20s | yes | black bars |
| 2.35s | yes | black bars |
| 17.90s | no | black bars |
| 18.20s | yes | black bars |
| 18.33s | yes | black bars |
| 32.90s | no | black bars |
| 33.20s | yes | black bars |
| 33.35s | no - the seek landed on the incoming B-roll (full frame, rows 0-1919) | **nothing wrong** - the only clean frame found anywhere |

Not one mentions blur, in or out. Seven of the nine are inside or
immediately beside a tail.

**The effect is genuinely reaching the picture** - this is not a case of
a transition that does nothing. Laplacian variance across the hook's
tail, measured on the picture band of each rendered frame:

```
frame 62 (2.067s)  357.2      <- tail starts
frame 65 (2.167s)  260.2
frame 67 (2.233s)  167.5
frame 70 (2.333s)  173.7
frame 71 (2.367s)   62.8
frame 72 (2.400s)   62.8      <- the cut
```

A 5.6x drop in sharpness over eight frames, ramping as designed. The
speech_3 tail is stronger still: 244 at 18.30s, 38 at 18.35s.

So the question for the captain is narrower than 12.4 stated, and better
posed: **the defocus works, and it is strong.** Whether a 5.6x defocus in
the third of a second before three cuts is the house transition is a
taste call. Reducing the strength, shortening the tail, and withdrawing
`defocus` in `transition_vocabulary.py` with its reason recorded are all
still live and all still need the captain. What is now off the table is
the claim that the judge is seeing them as damage - on the evidence
above, it is not seeing them at all.

### 17.5 The 4th Wall template (section 5b) - now cheap, still unasked

Project 001 declares no brief and no template by design, so this render
remains the unaided baseline for the with-and-without comparison.
~~`library/templates/fourth_wall.yaml` exists on main and now declares its
delivery format like every other. Running 001 under it is a one-line
change to `project.yaml` whenever the captain wants the comparison.~~
**Superseded:** `fourth_wall.yaml` was deleted on 2026-08-20 (captain's
ruling: the series template arrives later, whole, with authorisation).

---

## 18. Where this phase ends

`26 steps ran, 25 completed.` `validate` fails on **one** issue -
`[audio_levels] LUFS: -20.96` - which is audio and out of scope by the
captain's ruling of 2026-08-15. That is the same single out-of-scope
failure phase 1 ended on, minus the resolution error phase 1 also had.

The mechanical half of this phase is committed: the ruling, two
never-wired data paths, a Fusion comp built at the wrong frame, and a QA
gate that checked a constant instead of the plan. Suite: 1176 passed, 7
skipped.

The creative half is section 17 and waits on the captain. The render is
now one whose remaining complaints are all decisions somebody chose,
which is the first time that has been true.

---

## 19. The captain's four rulings, implemented and rendered

Rulings of 2026-08-20. Two required code, two required restraint.

### 19.1 Framing (Q1) - keep the letterbox. Nothing built.

`style.framing_intent` is untouched at its letterbox default, no per-clip
spine override was added, and `subject_framing` stays built and idle.
**The judge still reports `black_bars: top_and_bottom` on every A-roll
frame and that is the ruled behaviour, not a defect** - see 19.5, where
the count is stated with the bars named as ruled.

### 19.2 The two creative floors - removed entirely

`step_3_02_select_broll/post_bridge.py` rejected any plan under 5 B-roll
clips (message saying 5-15, check saying 5-20); `plan_sfx`'s post-bridge
rejected anything outside its own 3-15 while telling the model 5-10. Both
are gone outright - not reconciled, not per-template, not downgraded to a
warning, all three of which the captain declined.

**The prompts went with them.** A handoff that says "you MUST plan exactly
5-15 B-roll insertions" pads the edit exactly as effectively as a bridge
that rejects a sparse one, and it is where the shipped rationales came
from. `select_broll/handoff.md`, `plan_sfx/handoff.md` and both manifests
now say there is no required number.

`_assert_sfx_distributed` stays: it catches every SFX landing on one
frame, which is a collapse, not a sparse plan.

Re-planned, and confirmed from the compiled manifest:

| | phase 2 | phase 3 |
| --- | --- | --- |
| V2 B-roll clips | 5 | **3** |
| A3 SFX | 5 | **4** |
| rationales citing a floor | **2** | **0** |

The two that went were `clip_006` over block 6 ("ADDED TO SATISFY A HARD
FLOOR, not because the edit wants it") and `clip_014` over block 9
("ADDED TO SATISFY THE SAME HARD FLOOR"), plus the `tick` SFX ("Kept only
because step 4.04's post-bridge rejects fewer than five SFX"). Every
remaining cutaway now sits on a block with no A-roll under it - the three
non-speech blocks - which is what the creative direction asks for: "the
B-roll is incidental documentary texture, not illustration, and should
stay sparse; the piece is a person talking."

Guard: `tests/test_no_creative_floors.py`, verified failing against the
parent revision on both halves - the bridge checks and the prompts.

### 19.3 Music - the hybrid the captain actually asked for

"fix schema and let LLM choose from both library or outside."

**The schema was empty, and the reason is worth keeping.**
`present_llm_step` builds the injected schema from `interface.outputs`
minus anything the bridge already supplied. The bridge supplied
`music_selection` - the step's only declared output - so the subtraction
left nothing and the model was asked for nothing. The banked response
from the previous run is three bytes: `{}`.

**The bridge did not select, it sorted.** `sorted(project_folder/music/*)[0]`.
`PIPELINE_MUSIC_LIBRARY` was never opened.

Now the bridge catalogues both sources, measures each track and picks
none; `library/tools/music_selection_contract.py` is the verdict.
On project 001 it finds 7 tracks and flags 3:

```
library: 1 track(s) in .../assets i used .../music
project: 6 track(s) in .../001/music
7 local track(s) catalogued, 4 within duration sanity for a 60s edit.

Inspirational Motivational Music Video ... 3914.7s  TOO LONG: 65.2 min for a 60s edit
Uplifting Office Music MIX ...            11386.9s  TOO LONG: 189.8 min for a 60s edit
dummy                                         1.0s  TOO SHORT: cannot cover a 60s edit
```

**What it chose:** `Sickick- _Infected_ _Instrumental_.wav`, source
`project`, 198.6s, from a catalogue of 7. The library's single holding is
the same composition in its VOCAL cut, rejected because a lyric vocal
competes with a piece that is one person talking for 55 seconds; the
instrumental is the same musical decision without that cost. The
`_background music_ rise - uplifting piano _inspiring _ beautiful_ _ _
motivation` candidate is within duration sanity and was rejected on the
direction alone - its own filename names four forbidden registers. Going
outside the library was weighed and recorded as not taken, with the one
honest mark against the choice written down: Infected is a commercial
release, not royalty-free, which is a clearance question for the captain.

`forbidden_registers` for this piece came back as `["triumphant",
"motivational", "inspirational", "uplifting", "self-pitying"]`, each with
its own answer. The shipped failure now fails two independent ways - on
duration, and on a title naming a register the model itself recorded as
off limits.

`music_analysis` measured the new track: **BPM 89.1, 274 beats, 69
downbeats** - a real beat grid, where the previous one was measured off a
65-minute compilation.

### 19.4 The defocus transitions - closed, untouched

`plan_transitions`' creative output was supplied verbatim from the
previous run, so all three defocus tails are bit-identical. The soft
handheld B-roll at 3.43s is also untouched: the captain declined the
option that bundled it, and the judge still says "extremely blurry and
low in resolution" of that frame. Checked against the ruling's own cheap
test before writing this down - 3.43s is inside `broll_1` (2.40-5.40s)
and not inside any tail (2.07-2.40, 18.05-18.38, 33.05-33.38).

### 19.5 The judge, against the phase-2 baseline

Same model, same eight timestamps, same grounded prompt. The phase-2
export was re-judged in the SAME session as a control, so the comparison
is not against a quoted table.

| | phase 2 (control) | phase 3 |
| --- | --- | --- |
| frames examined | 8 | 8 |
| parse failures / unanswered | 0 / 0 | 0 / 0 |
| frames clean | 0 | 0 |
| **aspect-ratio mentions** | **0 of 8** | **0 of 8** |
| `black_bars: top_and_bottom` | 6 of 8 | **7 of 8** |
| `main_subject_fully_visible: true` | 8 of 8 | 8 of 8 |
| `text_legible: true` | 8 of 8 | 8 of 8 |

The control reproduced the documented baseline exactly, including which
two frames answered `none`.

**The bars are ruled, and the count moved for a reason that is not
framing.** Exactly one timestamp changed, 37.72s, and it changed because
of 19.2:

```
   37.72s  phase2 = none            phase3 = top_and_bottom
```

At 37.72s phase 2 was showing `broll_9` - `IMG_1819`, one of the two
cutaways that existed only to clear the B-roll floor, and portrait, so it
filled the frame. With the floor gone that cutaway is gone, and 37.72s is
now A-roll `speech_9` on landscape `IMG_1822`. So 7 of 8 sampled frames
are A-roll where 6 were, and every A-roll frame letterboxes exactly as
Q1 ruled. Nothing about the framing mechanism changed.

`text_legible` scored a variance of 1 for the **fifth** consecutive
sample. On `perceptual_qa`'s own rule that is a dimension that ranks
nothing; moving it to `DROPPED_DIMENSIONS` changes what the gate measures
and remains the captain's call.

### 19.6 Where the run ends, and what it still fails on

**25 of 26 steps complete.** The one failure is `validate`, and it is the
same class phase 2 ended on:

```
Validation failed: 1 issue(s) found
  - [audio_levels] LUFS: -17.47, True Peak: 1.85
```

Audio is out of scope by the captain's ruling of 2026-08-15, so this is
reported rather than fixed - but the NUMBERS moved and the reason is
ruling 19.3: phase 2 measured LUFS -20.96 / True Peak -1.06 off the
"Inspirational Motivational" bed, and Infected is a hotter master. **True
peak is now above 0 dBTP**, which is inter-sample clipping, and it is a
direct consequence of the track the fixed selection chose. It needs a mix
pass, which is out of scope, so it is recorded here for the captain
rather than acted on.

Export read off the file: `1080x1920`, `30/1`, `54.869333s`, h264 +
aac 48kHz stereo - the same frame and the same duration as phase 2, which
is why the eight timestamps compare directly.

`render`'s own `full_sweep` station reports `gap_before_clip_1: 90`,
`clip_3: 75`, `clip_6: 74`, byte-identical to phase 2. Those are the
three non-speech blocks carrying B-roll on V2; the station measures V1
contiguity only. Removing the two padding cutaways changed none of these
numbers, because both sat OVER speech blocks and never filled a V1 gap.

### 19.7 A gate found reading fiction, on the way through

`render_subtitles` blocked this render twice, and neither time was about
the subtitles.

`library/tools/qa/subtitle_qa.py` sampled two fixed instants - 0.5s and
1.5s into the first segment - and handed them to gemma-4-12b. A subtitle
overlay is transparent between captions, and on `sub_block_10` both land
in an ordinary pause: the first caption is the single word "i", ending
0.70s in, and the next arrives at 1.58s. Measured alpha: 1080 non-zero
pixels at 0.5s, **zero** at 1.5s.

The same model had PASSED that same file on the phase-2 run. So the gate
was a coin toss on a blank image in both directions.

Frames are now chosen by measuring the overlay's own alpha channel. That
exposed the second half: shown frames that demonstrably DO carry captions,
the model failed them with fabricated defects - "the text is cut off by
the bottom edge" on a caption whose alpha bbox is `(288, 1694, 776, 1770)`
in a 1080x1920 frame, 150 clear rows below the type, and "the letters are
overlapping and distorted" of a clean line of Montserrat. Three runs out
of three.

So the gate was split along the line CLAUDE.md already draws. The half
that reads real state decides: ink must exist somewhere, and what is
drawn must sit inside the frame with a margin and in the lower half.
`check_caption_geometry` checks the exact complaint the model kept
inventing, deterministically. The model's typography opinion is recorded
in the step output for a human and blocks nothing.

Guard: `tests/test_subtitle_qa_sampling.py`, which builds real overlays
with ffmpeg and asserts both directions - a gap is not sampled, and an
overlay that draws nothing anywhere still fails hard.
