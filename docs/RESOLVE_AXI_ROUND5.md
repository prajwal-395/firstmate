# resolve-axi round 5: the cheap escape hatch + the real coverage answer

Date: 2026-09-20. Head: `c57081c` (PR 1245) plus this branch. No live
Resolve in this environment: every number below is measured on fakes or
by static inspection, and says so. Live verification is pending and
named where it matters.

## A. `resolve-axi run`: the escape hatch stops costing arbitrary tokens

The point was never to enumerate the scripting API. It is that an
arbitrary script stops costing arbitrary characters. Three levers, all
ours:

1. **Serialization.** The script leaves its answer in `result`; `run`
   renders it as TOON rows under the same truncation discipline as
   every other command (500-char cells, `--full` and `--json` escape).
   A list of dicts renders as a typed table (union of keys, first-seen
   order, capped at 12 columns with a `--json` pointer past that); a
   dict renders as a `kv` block; anything else renders as a value.
2. **Connection.** Dropped deliberately, not half-built. `run` uses the
   identical connect + lease path as every other command, so wall time
   is at parity by construction (CLI cold start measured 34 ms;
   round-1 measured connect at ~50-90 ms; exec + TOON render measured
   0.03 ms for 10 iterations on fakes). A warm reused connection would
   need a long-lived daemon holding Resolve state - cursor-adjacent to
   the build, a second writer-adjacent process to guard, all to save
   milliseconds when the prize is tokens and turns. Saying why and
   dropping it is the honest half of the lever.
3. **Preamble.** The script runs with exactly `RUN_SCOPE` in scope
   (`library/tools/resolve_axi.py` states the list, and a test asserts
   it is exact): `resolve`, `manager`, `project`, `project_name`,
   `timeline`, `timeline_name`, `is_current`, `timeline_names`,
   `by_index`, `read_notes`. A week-typical script collapses to its
   `result = ...` line.

**Safety, stated plainly.** `run` is a READ by default: the script is
AST-scanned for Resolve writers (any attribute starting with a mutator
prefix in `_RUN_WRITE_PREFIXES` - reads in this API are all `Get*`, so
a prefix rule covers the open-ended API without enumerating it) and
refused loudly when one appears. Mentioning a writer in a string or
comment is not a call and still runs. `--unsafe` is the declared write
path: it skips the refusal, holds the lease EXCLUSIVE, and reports the
cursor before and after (an arbitrary script owns its own cursor, so
there is nothing to assert it against - `restore --apply` and `reply
--apply` keep their assert-the-cursor shape). Nothing in the module
moves the cursor either way, and the AST test still holds.

**Measured per shape** (fakes at week-realistic sizes; response
compared against the MCP-escaped JSON `run_script` returns):

| Shape | Request raw | Request run | Response saving |
|---|---|---|---|
| Timeline enumeration (32 reels) | 478 chars | 139 chars | ~2.0x |
| Marker walk, both planes (5 notes, one 3 KB body) | 429 chars | 128 chars | ~4.3x |
| Caption-card listing (3 cards, long paths) | 563 chars | 127 chars | ~1.4x |

Requests run ~3.4-4.4x shorter (the ~330-char connect preamble
vanishes). Responses compress where truncation bites (long notes) and
barely where paths dominate (captions, 1.4x) - the same honesty as
round 1's caption finding, per shape rather than as an average.

## B. The real coverage question: one Resolve layer inside the pipeline

### Recount (checkable, not competing)

Three numbers exist; they differ by denominator, and each is stated
with its own so the next reader can choose:

- **63**: shipped code only (`library/`, `resolve_scripts/`,
  `resolve_workflow_integration/`, `bin/`), pattern
  `.(Get|Set|Create|Delete|Add|Import|Move|Update|Append|Duplicate|
  Load|Close|Open)[A-Za-z]*\(`. This is the inventory's denominator:
  it counts what can move the captain's session, which is what the
  consolidation answers.
- **113**: the same pattern repo-wide, adding `tests/` (fakes plus
  nine live `*_against_resolve` suites) and `docs/` (prose quoting
  calls). Useful for "where is this method mentioned", wrong for
  "what can break a build".
- **128** (round-3 audit): a wider sweep across the worktree that also
  caught tests and vendored trees. Same shape, broader net - the
  fragmentation claim holds under every denominator; the 63 is the
  better figure for scoping the fix because it excludes code that
  never touches a live session.

### Groups: shared layer exists-and-bypassed, partial, or genuinely none

| Group | Prod files | Shared layer | Verdict |
|---|---|---|---|
| Cursor establishment (`SetCurrentTimeline`, 15 sites) | 11 | `assert_current_timeline` + `cursor_fence`/`cursor_excursion` in `resolve_lock.py` | EXISTS AND BYPASSED: the two big builds assert heavily at placement (`reel_build` 5 sites, `resolve_build_timeline` ~12) but establish through 10 direct setters; only 3 callers use fence/excursion |
| Cursor reads (`GetCurrentTimeline`, 23 sites) | 14 | `current_timeline()` in `marker_feedback.py` | EXISTS, mostly used |
| Marker reads (`GetMarkers`) | 12 | `read_notes()` (5 users) | EXISTS, mostly used; direct readers remain (`timeline_serializer`, `marker_resolution`, `capture_timeline`, `timeline_decisions`, `mark_master`, `marker_carry`) |
| Marker writes (`AddMarker` calls) | 7 | `place_reply_marker` (reply), axi restore path | PARTIAL: reply/restore unified with read-back verify; build-time marking (`resolve_build_timeline` x2, `mark_master`, `marker_carry`, capture button) each owns its path |
| Placement (`AppendToTimeline`, 18 sites) | 7 | `composed_edit`, `overlay_placement` (per-path) | NO SINGLE LAYER for the end-bound convention |
| Timeline lifecycle (`CreateEmptyTimeline`, 5 sites) | 5 | none | GENUINELY NONE: each build path creates its own staging |
| Media pool (`ImportMedia` 7 files, `GetMediaPoolItem` ~19, `DeleteClips` 8, `MoveClips` 2) | ~20 | `organise_media_pool`, `resolve_organization`, orphan modules | PARTIAL: organise/orphan paths exist; `DeleteClips` blast radius unbounded per site |
| Fusion (`ImportFusionComp` 7 files) | 7 | `apply_fusion_comps` + process-isolation rule | EXISTS, and the isolation rule constrains HOW it consolidates (never share the import process) |
| Render (`AddRenderJob`/`StartRendering`) | 2 | `segment_renderer`, `resolve_render` | EXISTS (small set) |

### Trap rows: where the same trap is independently re-implemented

1. **End-bound convention** (inclusive `GetSourceEndFrame` vs exclusive
   `GetEnd`): recomputed at every `AppendToTimeline` site and every
   source-span reader. The lost-frame class lives here.
2. **Two marker planes**: fixed for READS (`read_notes`); still
   per-path for WRITES (restore covers the timeline plane only;
   clip/pool writes live in capture/carry/mark paths with no shared
   write-and-verify).
3. **Cursor establishment**: assertion points exist; establishment is
   unguarded in 10 files. The killed-Fusion-pass class lives here.
4. **Batch blast radius**: 8 `DeleteClips` sites with no shared bound
   (count + dry-run). The nine-destroyed-attempts class lives here.

### Proposal, in order of value

1. **Cursor establishment through `resolve_lock`** (smallest set,
   sharpest proven consequence). Every grandfathered setter migrates to
   `cursor_fence` (guarded sections) or `cursor_excursion`
   (move-and-return reads); the registry test below stops new ones.
   Needs live verification per migrated site: this PR does NOT migrate.
2. **One end-bound helper** (the lost-frame class): a single
   inclusive-to-exclusive conversion owned next to `composed_edit`,
   adopted site by site with the manifest validator watching.
3. **Build-time marking through write-and-verify** (the
   `place_reply_marker` shape: write + read-back + occupied-frame
   refusal), extended past reply/restore to `mark_master`,
   `marker_carry`, and the two `resolve_build_timeline` sites.
4. **A bounded `DeleteClips` helper** (count + dry-run diff in the
   restore shape) so no call site carries an unbounded blast radius.

Establishment, placement, and batch deletion are shared-LAYER work the
build calls: none of them becomes a CLI command (a second writer into
the captain's project stays refused). Cursor get/assert and
marker reply/restore are already surfaced; `run` covers the rest as
the escape hatch. That keeps one writer while removing the
fragmentation - the caution from the round-3 audit, kept.

### First slice shipped in this PR

- `resolve-axi run` (implementation + 14 tests, AST invariant holds).
- `tests/contracts/test_resolve_guard_wiring.py`: every `SetCurrentTimeline` site
  in shipped code registered with owner and migration state (1 shared
  + 2 probe + 11 grandfathered across 10 files); a new setter fails
  with routing instructions, a stale count fails as a lie about what
  is owed. Test-only: **no pipeline runtime behaviour changed**, and
  no already-wrong call site was quietly fixed - the registry records
  them for the migration, it does not absolve them.

### What this PR deliberately does not do

- Migrate any cursor setter (needs live Resolve per site).
- Touch `data/vep-resolve-axi/report.md` or
  `data/vep-field-test-feedback/PERFORMANCE-LEDGER.md`: both live in
  the firstmate repo, outside this worktree's isolation boundary. The
  PR body carries the exact ledger row and report-delta text for the
  supervisor to apply.

## C. Round-5b: the steer (bare positional, the rule, the echoed form)

Firstmate's live verification caught the round-2 defect recurring as
the pattern rather than the instance: `run` shipped without a bare
positional, so the obvious invocation failed at argparse. Fixed as the
class, not a third instance:

1. `run "result = ..."` works; `--script`/`--file` stay as the
   explicit forms. Live-verified against the captain's project:
   `run "result = timeline_names"` rendered `result[32]{value}` first
   try, cursor unmoved.
2. The lesson is now a RULE in the module docstring ("any command
   taking one obvious primary argument accepts it positionally") with
   `test_positional_primary_args` enumerating all five read commands
   plus `run` and asserting the shape - the next command built without
   a positional fails in tests instead of on first real use.
3. The writer refusal echoes the form the caller used (`--script
   "..."`, `--file <path>`, or the bare `"..."`), never suggesting a
   form they did not reach for.
