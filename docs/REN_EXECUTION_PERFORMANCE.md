# Ren execution performance investigation

Measured against `33f50210aa3e6b49863ab7d2eac34259299d3ae5` on 2026-10-17.
[Machine-readable measurements](benchmarks/ren-execution-2026-10-17.json).

**The final lower-third quick edit passed in 3.178 seconds:** all 33
items matched the complete placement's file windows, record spans, rows,
transforms and linked groups after replacing only the two declared
lower-third paths. Python used 0.738 CPU seconds; 7,377 Resolve API calls
accounted for 2.329 seconds. The native hold, header and shared-row card
were retained. The measured code is `f2ff21054`.

The final complete placement also passed all 33 items in **11.433 seconds**
with no repair (`bdd11e4c3`). The earlier complete placement took 11.593
seconds, with 5.761 seconds across 10,326 Resolve API calls and 5.206 Python
CPU seconds (`2e5499315`).
The controlled main comparison took **149.567 seconds for one placement
patch**, including 146.203 CPU seconds and only 0.649 seconds / 519 calls in
Resolve. Its card matched the new build's card. This comparison mounted the
frozen main Python modules in the instrumented build process and stopped
immediately after the first successful patch. The current native extension
was common to both sides. It is not a completed before/after full build.

Measurements use a frozen Reel 26 fixture in an APFS project clone and a
separately imported `Ren Scratch - exec deep` project. Main cannot parse the
current lower-third construction/animation fields, so the fixture keeps the
supported declaration and supplies two existing rendered lower-third assets.
No approved timeline in the live project was written. Full-run timings include cProfile and API instrumentation; the final quick
edit uses API instrumentation only. They are not uninstrumented latency.

## What actually executes an edit

| Part | Implementation and owner |
| --- | --- |
| Front door | A checkout's `bin/ren` is a shell launcher; the packaged native front door is `native/ren-cli`. Build commands exec Python `ren.cli`, then `manage_project.py`; they are not native edit execution. |
| Orchestration and planning | `library/processes/reels/run_reels.py`, the operation registry and Python step bodies. A warm approved build derives plans and consumes stored visual answers. It does not inherently require another model-planning call. |
| Overlay rendering | Python subtitle/graphics planning dispatches to the existing render backends, including Rust FFrames and ffmpeg child processes. This is separate from rendering the finished timeline in Resolve. |
| Admission and transport | `native/ren-resolved` owns the Rust broker queue, socket protocol, watchdog and SQLite job store. It grants the build process its cursor lease. Its execution workers remain Python (`library.tools.resolved.worker`); changing the daemon language does not port their work. |
| Placement | Python `reel_build.build_reel_timeline`, `edit_patch.apply_live_patch` and `edit_algebra` issue Resolve API calls. The measured build's Python process executes this path under broker grants. Typed broker jobs and custom scripts execute in the broker's Python worker. |
| Editorial computation | `native/ren-core`, imported as `ren_native`, computes patch spans, validation, conditions and verification. The Python implementation remains the selectable fallback. It does not own Resolve's timeline or perform its API mutations. |
| Verification | Python timeline read-back, SOP conformance, speech/caption/stream checks and project ledgers. Native algebra checks are one component of this phase. |
| Promotion | Python staged-reel verification, replacement guards, retirement, marker carry and version recording. The ordinary build's staging remains separate from the approved timeline until promotion. |
| Finished-video render | Resolve performs it. `build-reels` is not a finished-video export; `deliver-reel` owns that path. |

This branch is rebased onto the sibling
[small-edit PR](https://github.com/prajwal-395/video_editing_pilot/pull/2685).
Its batched footage pass exposed the right seam; the final full-build path
records the entire reel and executes it without per-batch EditPatch rounds.
The initial live incremental attempt refused the native ending hold. The
follow-up now proves and retains a native hold and post header, preserves
cards sharing the motion-graphics row, and replaces only the changed
overlays without a Project Model transaction. Its deterministic test
compares source frames, speed, transforms and row items against a fresh
full rebuild. The initial live refusal remains recorded honestly below;
the final live timing is reported below.

## Initial real Reel 26 profile

The exact entry point was the normal `manage_project.py build-reels` runner,
with `--only-reel 26 --rebuild-all`, wrapped by cProfile and a 600-second limit.
The command used the dedicated interpreter through `bin/vep`. Project binding
and `pipeline_data.json.project_folder` pointed to the scratch copies.

The profile recorded **203.788 seconds** to refusal, including **408,240,305
function calls** (305,898,318 primitive). These are cumulative call times and
are nested; do not add them together:

| Function or phase | Calls | Cumulative seconds |
| --- | ---: | ---: |
| Operation input gathering | 1 | 35.816 |
| Marker routing, within input gathering | 1 | 33.926 |
| Project Model load, within routing | 1 | 33.592 |
| Caption preparation and rendering | 1 | 64.456 |
| Staging cleanup, including refusal cleanup | 2 | 41.951 |
| Pool inventories, within cleanup | 4 | 20.226 |
| Master timeline snapshots | 3 | 10.641 |
| Broker socket exchanges | 51 | 0.0479 |

The socket exchanges comprise 24 grants, 26 pings and one other call. The
performance ledger separately recorded 12 lease acquisitions: **2.587 seconds
waiting** and **63.483 seconds holding Resolve**. A lease hold contains Python
work as well as API time; it is not a measurement of Resolve execution alone.
The ledger recorded 22 ffmpeg encodes totalling 11.389 seconds. The remaining
caption work includes Python font/outline generation and graphics preparation.

This initial refusal did not measure placement. The subsequent live run
wrapped the fusionscript handles to count and time every API method separately;
its complete placement figures are below. No total-build speedup is inferred
from the initial refusal.

## Fixed costs

| Issue | Fix and non-timing regression |
| --- | --- |
| One full patch transaction per footage clip | Compile the complete reel with the existing placement recorder, append planned video as one API batch, then append audio in overlap-safe layers with targeted spill cleanup. No whole-timeline observe/judge/verify or model transaction occurs per batch. The final inventory checks the complete plan once. Fake-Resolve comparisons preserve item fields; call-count tests prohibit patch/model transactions and bound repairs. Shapes the recorder cannot carry retain direct native placement, with final process conformance. |
| Native span conversion once per inserted item | Pass inserted operations through one `spans` batch. A call-count assertion proves one span batch for 24 insert/property pairs. |
| Every insert searches the entire pool again | A patch-scoped, lazy pool-ID index. It cannot persist across another import or another patch. The 24-item test requires exactly one pool walk. |
| A batch re-lists the entire timeline after each insert | Retain the newly returned handle and refresh older handles only when an operation actually needs them. Structural mutations still invalidate earlier handles; the completed patch still gets full read-back verification. |
| Both idempotency checks decode every historical generation | Indexed `ShadowStore.patch_generation` lookup by project, timeline and patch ID. The regression forbids calling `history` while committing and retrying the batch; existing changed-key refusal tests still pass. |
| Empty pre/postconditions marshal whole snapshots into Rust | Return the empty judgement without invoking the backend. The regression makes any backend access fail. |
| Rust clip verification scans every clip for every operation | One lazy concrete clip-ID-to-location index per immutable snapshot. Duplicate IDs remain unaddressable. Confirmed property writes reuse the index; replacing the snapshot creates a new one. Rust tests and Python parity cover the verdicts. |
| Python fallback batch checks repeat the same clip scan | A scoped index for `spans` and `verifies`; its regression requires one snapshot enumeration and checks duplicate-ID refusal. |
| Repeated loads schema-validate an unchanged verified event head | A bounded cache of validation verdicts keyed by verified event hash. Actual model reads still verify the complete hash chain and return independently parsed mutable data. New heads, snapshot repair, malformed logs and tampering remain checked. |
| Patch prepare/apply load a large model just to read its revision | `project_model_revision` reuses a previously validated revision only under the store lock and only while inode, device, size, mtime and ctime still agree. A changed log takes the complete validated load path. Rewriting a log and restoring mtime is explicitly tested. No mutable model or large deep copy is cached. |
| The native extension is built in debug mode | `maturin build --release`. The builder script is now part of the source fingerprint, so an existing debug artifact cannot satisfy the release build's cache key. |
| A killed build leaves the broker reporting a stale lease JSON | Both Rust and Python broker status/handoff paths probe the OS flock rather than trusting the diagnostic file. They do not unlink a potentially newer writer's record. A real killed child, a replacement holder, and the native probe are tested. |
| Evidence take discovery classifies every cross-source utterance pair | Exact token/three-gram candidate indexes and bounded-memory longest-run DP preserve every emitted link and tie. Exhaustive/seeded parity and classifier-call-count tests cover the output and work. |
| Evidence links/signals repeatedly scan growing collections | Append-aware observation-ID lookup and one signal-ID set per projection; regressions bound inspected IDs and preserve duplicate/first-match behavior. |
| Every fragment repeats complete model validation | Internal fragment construction defers to the existing validated commit; the public standalone fragment boundary still validates. |
| Quick edits refuse a native hold or post header and confuse a shared-row card with an overlay | Prove retained items, isolate fixed card slots, re-inherit the held tail treatment, then check one final row inventory. Full/incremental parity includes source frames, speed and transforms. |
| Reused captions rewrite/fsync an unchanged complete ledger | Preserve the existing receipt when merged entries and metadata are unchanged; a regression forbids fsync on exact reuse while missing/changed records still write. |
| Empty scoped marker pulls load the large model | Load only when a note actually needs routing; a test forbids model access for empty and unrelated pulls. |
| Reclaimed staging files the whole pool before replacing it | Defer filing to promotion/refusal cleanup; the existing refusal-debris test remains, and the reclaim regression forbids the redundant organizer call. |
| Detached test brokers can connect to live Resolve outside conftest | The real fusionscript boundary now refuses inherited test subprocesses unless the explicit live-session opt-in is set. Fake scripting modules remain usable. The regression checks both load and handshake boundaries without connecting to Resolve. |

## Live complete placement

| Component | Instrumented seconds |
| --- | ---: |
| Complete placement and final inventory | 11.593 |
| Resolve API wall time within placement | 5.761 |
| Wall time outside Resolve API calls | 5.832 |
| Python CPU, including API marshaling | 5.206 |
| API wrapper CPU within that figure | 0.309 |
| Complete plan recording | 0.726 |
| Deferred overlay transforms | 2.758 |
| Grade | 0.204 |
| Subject punch-in | 0.626 |
| Recorded overrides and replay | 1.053 |
| Overlay sweep read-back | 0.099 |
| Linking | 0.125 |

The component profile is nested and rounded, so its rows are not an additive
accounting of the whole build. No broker socket exchange occurs inside this
already-granted placement pass. The broker grants/admission belong to the
outer build; the initial whole-run measurement found 51 socket exchanges in
0.048 seconds. Broker transport is not the stall.

The source-window check retains the raw source-frame reports for diagnostics.
Resolve's raw source-out report is not its played edge, as already documented
by `timeline_ingest`. The native append check uses the stored file offset and
the measured timeline duration on the source clock; a zero-speed hold uses
its held source frame. This does not change the source windows the renderer
plays. Duplicate items remain visible through multiset comparison.

The first native prototype exposed 95,333 `GetClipProperty` calls: an index
miss still walked the pool for each newly rendered asset. An authoritative
index is now used only inside the exclusive build pass, and its own imports
are registered immediately. The final pass made 3,867 such calls. Callers
outside that isolated pass retain live miss lookup for concurrent imports.

The live run also exposed two existing compatibility errors: a native hold
must append the span ending on the held frame, and a tight post header needs
its declared shadow padding and 480-pixel placement floor. The minimal hold
correction overlaps the lower-third branch's existing fix and must be kept
consistent when those branches land together.

Unchanged caption receipts now preserve their durable ledger bytes without
another whole-ledger serialization and fsync. Missing files and changed
placings still update the ledger under its existing lock. The full native
placement's online-media and empty-row checks reuse its final inventory;
property/link repairs reuse its handles and re-read only affected rows.

## Post-placement evidence migration

The combined profiled run was stopped at 576 seconds, before the ten-minute
limit. Placement had passed, but the CLI had not completed verification or
promotion. Its post-build Project Model maintenance took 330.743 seconds;
evidence migration accounted for 279.279 seconds of that nested figure.
Cross-source take discovery classified 2,688,997 pairs in 163.954 seconds,
source-signal materialization took 43.451 seconds, and speech/face linking
took 22.625 seconds. These are Ren CPU costs, not Resolve latency.

The follow-up fixes tokenize utterances once, use exact shared-three-token
and identical-text candidate indexes, compute the longest shared run with
bounded-memory dynamic programming, index identity anchors as observations
are appended, and use a signal-ID set during materialization. Exhaustive
small-corpus and seeded randomized comparisons preserve the original link
classification, ordering and tie breaks. A negative-corpus call-count test
reduces 40,401 potential pairs to one classifier call. Per-fragment model
validation is deferred to the existing validated commit boundary, removing
duplicate whole-document schema walks without caching mutable models.

Unsupported edit-ledger programs refuse before duplicating a timeline;
an inventory refusal after duplication deletes only that call's staging
and restores the final cursor. The final normal entry point took **293.023 seconds**, including cProfile,
API instrumentation, old-staging cleanup and a failed comparison-name
collision probe. Conformance passed with **0 errors / 4 warnings**.
Promotion then refused because the frozen fixture's two lower-third
assets could not carry editor changes recorded against different assets.
Nothing was renamed. This is a timing through a protected refusal, not a
successful whole-build latency claim.

The subsequent recorded broker job used a unique comparison name and
passed the 33-item quick-edit proof in 3.178 seconds. It duplicated and
removed only its own scratch comparison, restored all temporary Python
module bindings, and passed the recorded run's inventory guard. The MOV
variants changed container metadata while keeping decoded frames identical;
the reference substitutes only those two paths into the verified full
inventory. The fake-Resolve test separately compares a fresh full rebuild
against the incremental result, including source-frame reports and speed.

| Post-build cost | Earlier profile | Final profile |
| --- | ---: | ---: |
| Evidence migration | 279.279s | 58.263s |
| Whole model maintenance | 330.743s | 108.539s |
| Cross-source take links | 163.954s | 0.212s |
| Classified utterance pairs | 2,688,997 | 42,379 |
| Source-signal projection | 43.451s | 0.098s |
| Speech/face links | 22.625s | 0.043s |

These are nested measurements, not additive phases. The final profile still
spent 77.922 seconds on three distinct schema-validation boundaries and
53.448 seconds on four actual model reads. Empty scoped marker pulls had
unnecessarily loaded that model three times (48.852 seconds): the follow-up
now defers loading until a note actually needs routing. A call-count
regression forbids loading for empty/unrelated pulls. Reclaiming old staging
also no longer files the entire media pool before its replacement build;
filing stays with promotion or refusal cleanup. Those two final follow-ups
were tested deterministically and were not re-timed on Resolve.

The final restore read-back verified `Podcast (field test)` / Reel 26 on the
Edit page, and the broker reported no active holder.

## Isolated measurements

These compare identical inputs and verdicts; they are not a claim about total
Reel 26 build time. Rust measurements use the existing cached base extension
and the extensions built from this branch, in separate Python processes.

| `verify_many` input | Base debug | Indexed debug | Indexed release |
| --- | ---: | ---: | ---: |
| 1,000 items | 0.01251s | 0.00933s | 0.00240s |
| 10,000 items | 1.11508s | 0.04191s | 0.00722s |

All three returned exactly the same 1,000/10,000 successful verdicts. CPU time
for the 10,000-item case was 1.06217s, 0.04140s and 0.00722s respectively.
The previous 10x input increase cost 89x wall time; indexed release cost 3x.
The separate indexed-debug result distinguishes the algorithm change from
compiler optimization.

On the cloned real Project Model (revision 50, 115,963 observations), loading
the model to obtain its revision took 21.475s wall / 19.436s CPU. The two
subsequent validated revision-only reads took 0.000295s and 0.000098s. A cold
revision read still validates the model; this optimization removes repeated
unused loads, not the initial integrity check or the actual editorial read.
These were shared-machine measurements; CPU figures are included because
other workers can affect wall time.

## Acquisition planner: one acquisition per question set

The L5 acquisition planner (`library/tools/evidence_acquisition.py`) schedules
one step per (requirement, lane, window). A requirement graph routinely asks
several questions of the same footage - "a chart is visible" and "the camera
moves" both want the detector over the same ten seconds - and every question
scheduled its OWN copy of that producer call. The producer therefore ran N
times over one interval, the plan's cost estimate counted N times one
measurement, and `run_acquisition` attributed the result to the LAST
requirement that asked: the earlier one paid for the measurement and lost it.

The planner now coalesces steps by acquisition identity (catalog lane, lane,
interval, producer - the same identity the ledger key already carries) and
names every requirement each acquisition serves, in `requirement_ids`. The
measurement is unchanged; it is paid for once and answers every question that
asked for it.

Measured on the planner's own cost model, over a 120 s source, using the
checked-in per-window costs (`segmentation-vision-nominations`, 1.6 s/window):

| Plan | Before | After |
| --- | ---: | ---: |
| "scene description and camera movement" (3 requirements, 12 windows) | 36 steps, 57.6 s | 12 steps, 19.2 s |
| the same plan escalated to the OWLv2 tier (36.4 s/window) | 36 steps, 1310.4 s | 12 steps, 436.8 s |
| "footage with laughter and applause" (2 requirements, 1 window) | 2 steps | 1 step |

These are the planner's own cost model over measured per-window costs, not a
live run. A live run over the captain's footage is the live lane's evidence
(`vep-axc-live2`), and this lane names that as the live measurement it still
needs.

## Limits and remaining work

- A completed full-main versus full-new timing comparison is not available.
  The original main entry point refused the current creative declarations;
  the controlled comparison measured one real main patch. Do not extrapolate
  it into a measured full-build duration.
- The final quick-edit equality passed on the frozen fixture. The latest
  creative artwork and approved-tail revisions are outside that fixture; no
  claim about their visual appearance or promotion is made.
- Native transition elements and image-sequence captions use the existing
  direct append placers because their complete recorder spelling is not yet
  implemented. Their full-build footage/card writes also avoid per-batch
  model transactions; the process conformance gate remains mandatory.
- A missing picture whose grade/retime/recorded treatment cannot safely be
  reconstructed from the item table refuses promotion. Untreated missing
  items get one targeted append and row re-check; unexpected media is never
  deleted to manufacture a pass.
- Cold full-model validation, event-log hashing and actual model commits
  remain. This branch removes revision-only loads and redundant validation
  of an already verified head, without caching mutable project data.
- The initial broad dependent run collected 4,476 tests and was interrupted
  at firstmate's request after 266 passed / 43 skipped. It is not a passing
  gate. The real scripting boundary now guards inherited test subprocesses;
  focused deterministic checks are reported on the PR. Broad sweeps were
  stopped at the captain's direction.
