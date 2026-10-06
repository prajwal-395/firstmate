"""The ONE command registry: every `ren` verb, and what it runs.

`ren --help` renders this table (`ren/cli.py`), and `manage_project.py`
- the engine underneath, not a front door - builds its subparsers from
it: a subcommand's usage and help are its row here (`for_subcommand`),
and its parser
refuses to start when a row and a registered subcommand disagree. So a
command is added in ONE place, and nothing parses another file's source
to keep two lists in step.

Standard library only: `ren --help` must work before the ML environment
exists, so nothing here imports the engine.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Verb:
    name: str
    group: str
    summary: str
    subcommand: str = ""
    """The `manage_project.py` subcommand this verb execs, or ""."""
    module_argv: tuple = ()
    """Or: `-m <module> <args...>` this verb execs, arguments appended."""
    builtin: str = ""
    """Or: a command implemented in this package (`doctor`, `config`)."""
    detail: str = ""
    """What `<verb> --help` says beyond `summary`, where there is more to say."""

    @property
    def help(self) -> str:
        return self.detail or self.summary


_FOOTAGE_QUERY = "library.tools.analysis.footage_query"

# A new verb is registered a SECOND time in `native/ren-cli/src/commands.rs`
# (`VERBS`), and the two lists must stay in sync: the native front door
# refuses a verb its own list does not know before exec'ing this module.
VERBS = (
    Verb("doctor", "Setup", "Check what this Mac can do with Ren (--for <capability>); changes nothing",
         builtin="doctor"),
    Verb("setup", "Setup", "Install Ren's runtime and system prerequisites; optional verified packs with --with",
         builtin="setup",
         detail="Install the Python 3.12 runtime and locked dependencies, Node renderer dependencies, ffmpeg, and the default project folders. Optional model packs: panns, mfa, ecapa, deepfilter. Example: `ren setup --with panns`."),
    Verb("uninstall", "Setup", "Remove Ren's runtime and cache; customer data is kept",
         builtin="uninstall",
         detail="Remove Ren's app runtime and cache after confirmation. Model downloads are kept unless `--models` is passed. Projects, footage, exports, shared assets, and user configuration are never removed."),
    Verb("config", "Setup", "Show where each setting comes from; --init writes a starter file",
         builtin="config"),
    Verb("support-bundle", "Setup", "Collect a redacted support bundle (version, config, doctor, recent errors; never footage)",
         builtin="support-bundle"),
    Verb("version", "Setup", "Show the Ren build (version, commit, channel, engine root)",
         builtin="version"),
    Verb("upgrade", "Setup", "Install and verify an engine tree, then activate it",
         builtin="upgrade",
         detail="Install a Ren checkout or built engine tree alongside the active version, "
                "verify it, then switch the current version. Usage: `ren upgrade <engine-source> "
                "[--channel CHANNEL]`"),
    Verb("rollback", "Setup", "Switch to the previously active Ren version",
         builtin="rollback",
         detail="Switch the current pointer back to the previously active engine version. "
                "This changes no project files."),
    Verb("update", "Setup", "Move this Ren install to a released version",
         builtin="update",
         detail="Fetch the release artifact that .github/workflows/release.yml builds, "
                "verify its checksum, and activate it. Usage: `ren update [--url <artifact-url>] "
                "[--tag vX.Y.Z]`"),
    Verb("taste", "Setup", "Record an explicit creative preference shared across projects",
         module_argv=("library.tools.taste_profile",)),
    Verb("init", "Setup", "Create the projects root folder", subcommand="init-root"),
    Verb("setup-hooks", "Setup", "Install the marker-feedback hook for a host (plan by default)",
         subcommand="setup-hooks"),

    Verb("new", "Projects", "Create a project", subcommand="new"),
    Verb("projects", "Projects", "List projects", subcommand="list"),
    Verb("status", "Projects", "Show a project's pipeline status", subcommand="status"),
    Verb("info", "Projects", "Show a project's configuration as JSON", subcommand="info"),
    Verb("task", "Projects", "Start or finish an isolated semantic task",
         subcommand="task",
         detail="`ren task <project> start <task>` allocates a ren/<task> "
                "worktree; `ren task <project> finish <task>` commits and "
                "merges semantic state, then reports whether a Resolve "
                "rebuild is needed. Use repeatable --claim paths for "
                "disjoint tasks. Resolve work happens after finish"),
    Verb("check", "Projects", "Run a project's readiness check", subcommand="check"),
    Verb("edit", "Projects", "Run the editing pipeline on a project", subcommand="run"),
    Verb("trace", "Projects", "Regenerate the run traceback and artifact index", subcommand="trace"),
    Verb("profile", "Projects", "Show where a run spent its time, layer by layer (read only)",
         module_argv=("library.tools.perf_ledger",),
         detail="Read a run back from pipeline_output/logs/perf_ledger.jsonl: each named layer's share "
                "of the run's wall (Gemma, MFA, Resolve render, Remotion, a host model), then what each "
                "capability spent outside them. Changes nothing"),
    Verb("organize", "Projects", "Bring a project folder onto the standard layout", subcommand="organize"),
    Verb("archive", "Projects", "Archive a finished project", subcommand="archive"),
    Verb("spec", "Projects", "Translate, clarify and record a natural-language edit spec",
         module_argv=("library.tools.edit_spec",)),
    Verb("reindex", "Projects", "Move a project's words onto Voz plus MFA (re-transcribe legacy indexes)", subcommand="reindex",
         detail="Move a project's words onto Voz plus MFA: invalidate legacy temporal indexes, then re-transcribe them"),

    Verb("propose", "Reels", "Publish the chosen moments as the reel review file", subcommand="propose-reels"),
    Verb("build", "Reels", "Build approved reels in Resolve", subcommand="build-reels"),
    Verb("adopt", "Reels", "Adopt existing reel timelines as Ren-built by exact Resolve id",
         module_argv=("library.tools.resolve_axi", "ownership"),
         detail="Record who adopted each existing final reel timeline by its exact Resolve unique id. Reads the open project and its current timeline, writes only the local ownership ledger, then verifies the live inventory stayed unchanged."),
    Verb("touch", "Reels", "Apply a small change to a built reel (a touch-up, not a rebuild)", subcommand="touch-reel",
          detail="Apply a structured change to a built reel's existing timeline through composed_edit, staged and verified"),
    Verb("touch-master", "Reels", "Apply a small change to the master timeline (a touch-up, not a rebuild)", subcommand="touch-master",
          detail="Apply a structured change to the master timeline's existing timeline through composed_edit, staged and verified"),
    Verb("replay-ledger", "Reels", "Apply a recorded edit ledger to a timeline directly, without a build", subcommand="replay-ledger",
          detail="Read the project's edit ledger and apply each row to the target timeline through the edit algebra, journaled so `ren undo` can reverse it"),
    Verb("undo", "Reels", "Undo a touch-up (in place) or rebuild (by version)", subcommand="undo",
          detail="Reverse a Ren act: a touch-up in place from its journal, a rebuild by rolling back to the version before it. "
                 "--act addresses any recorded act by id, not just the newest"),
    Verb("redo", "Reels", "Re-apply a reversed touch-up or rebuild", subcommand="redo",
          detail="Re-apply a reversed act: a touch from its journal's spec, a rolled-back rebuild from the plan moment of the act that had been rolled back"),
    Verb("dry-run", "Reels", "Plan a touch-up against the live timeline and stop", subcommand="ren-dry-run",
         detail="Dry-run the composed edit path (plan, read live, print, stop - never executes)"),
    Verb("deliver", "Reels", "Render one approved reel to a file", subcommand="deliver-reel"),
    Verb("watch", "Reels", "Have a model watch a delivered reel", subcommand="watch-reel",
         detail="Show a model the PICTURE of a delivered reel and ask what it sees. Renders nothing"),
    Verb("hear", "Reels", "Hear a delivered reel against its plan", subcommand="hear-reel",
         detail="Hear a delivered reel against its plan and report what diverged. Renders nothing, gates nothing"),
    Verb("drift", "Reels", "Compare each reel's build snapshot with the live timeline", subcommand="drift",
         detail="Compare each reel's build snapshot against its live timeline and report the per-reel factor"),
    Verb("post-header", "Reels", "Put each reel's social-post header on its final, as a journaled touch", subcommand="post-header"),
    Verb("shift-rows", "Reels", "Move named rows of each reel up or down by pixels, as a journaled touch", subcommand="shift-rows"),
    Verb("scale-rows", "Reels", "Shrink or grow named rows of each reel about a point, as a journaled touch", subcommand="scale-rows"),
    Verb("fit-picture", "Reels", "Put each reel's TV picture at a scale no phone crops, as a journaled touch", subcommand="fit-picture"),
    Verb("caption-width", "Reels", "Narrow each reel's captions to the width the platforms leave clear, as a journaled touch", subcommand="caption-width"),
    Verb("safe-zones", "Reels", "Put a platform safe-zone guide on reel timelines, switched off so it never renders", subcommand="safe-zones"),
    Verb("sign-off", "Reels", "Record the captain's sign-off on a built reel", subcommand="sign-off",
         detail="Sign off a BUILT reel, so promotion must declare before replacing it"),
    Verb("purge", "Reels", "Plan (default) or --apply the lean-retention purge", subcommand="purge",
         detail="Plan (default) or apply the lean-retention purge of superseded renders, quarantine, scratch and stale journals"),
    Verb("discharge", "Reels", "Discharge a dropped note a promotion filed", subcommand="discharge-uncarried",
         detail="Discharge a dropped captain's note a promotion filed, so its reel can be signed off"),
    Verb("variant", "Reels", "Two versions of one reel: new, build, list, diff, choose, merge", subcommand="variant"),
    Verb("explore-variants", "Reels", "Fork one base commit into one isolated branch per direction: propose, plan, list, dispatch", subcommand="explore-variants"),
    Verb("merge-queue", "Reels", "Queue a branch's candidate against the master: queue, list", subcommand="merge-queue"),
    Verb("worktree", "Reels", "A task's own checkout of the project store: add, list, commit, merge, remove",
         subcommand="worktree",
         detail="Give a task its own checkout of the project's git store on branch ren/<task>, outside the project folder, and merge it back. Never moves the project checkout's branch; touches no Resolve"),
    Verb("rounds", "Reels", "What changed between two feedback rounds", subcommand="round-diff"),
    Verb("revert", "Reels", "Revert a creative commit: record its inverse as a new commit",
         module_argv=("library.tools.creative_history", "revert"),
         detail="`ren revert <project> <commit>` records the commit's inverse standing on the timeline's head, by id or unique prefix. Refuses a commit later commits also changed, one the record cannot invert, or one outside its timeline's line"),
    Verb("pr-body", "Reels", "Generate the PR-body enumeration for a ledger change (read only)", subcommand="pr-body"),
    Verb("notes", "Reels", "Show which timeline note went to which step", subcommand="notes"),
    Verb("take-pick", "Reels", "Print one reel's takes, freshness, snap deltas and boundary words in one read", subcommand="take-pick-preview"),
    Verb("commits", "Reels", "Show a project's creative commits: what each touch-up changed and why",
         module_argv=("library.tools.creative_history", "commits"),
         detail="Read the commit graph touch-ups record: `ren commits <project>` lists every commit, `--timeline` one reel's chain, `--show` one commit by id or unique prefix. Read only"),
    Verb("branches", "Reels", "Show a project's creative branches: what each branch stands on",
         module_argv=("library.tools.creative_history", "branches"),
         detail="Read the branches recorded over the commit graph: `ren branches <project>` lists every branch, `--show` one branch by name. Read only"),
    Verb("tag", "Reels", "Name a creative commit a version (or list the names given)",
         module_argv=("library.tools.creative_history", "tag"),
         detail="`ren tag <project> <name> <commit>` points a version name at a commit by id or unique prefix; `--list` shows the names. A name that already points somewhere is refused unless `--move` is passed"),
    Verb("cherry-pick", "Reels", "Replay a creative commit's changes as a new commit",
         module_argv=("library.tools.creative_history", "cherry-pick"),
         detail="`ren cherry-pick <project> <commit>` replays the commit's changes onto its timeline's head by id or unique prefix. Refuses a commit later commits also changed, a merge, or one outside its timeline's line"),

    Verb("pool-organize", "Resolve", "File a Resolve project's media pool", subcommand="resolve-organize",
         detail="File a Resolve project's media pool: reels by plan state, assets under the reel that uses them"),
    Verb("pool-prune", "Resolve", "Remove media-pool items no timeline plays", subcommand="resolve-prune",
         detail="Remove media-pool items no timeline plays, and delete their files. IRREVERSIBLE; plans by default"),
    Verb("mark-master", "Resolve", "Mark the master timeline where each reel was taken", subcommand="resolve-mark-master",
         detail="Mark the master timeline with where each reel was taken from. Markers only, and reversible"),
    Verb("relink", "Resolve", "Relink offline media after a move", subcommand="relink"),
    Verb("timeline", "Resolve", "Answer what a timeline holds from its recorded generations; touches no Resolve",
         module_argv=("library.tools.timeline_shadow",),
         detail="log, show, clips, markers or diff a timeline's recorded generations (the shadow store). Never reads Resolve"),
    Verb("resolved", "Resolve", "Run or ask the broker that schedules the one Resolve (serve|ensure|status|list|submit|result|stop|kpi)",
          module_argv=("library.tools.resolved",)),
    Verb("shadow-watch", "Resolve", "Watch a project's reels for hand edits on a schedule (standalone; the broker hosts it)",
          module_argv=("library.tools.shadow_watcher",),
          detail="Observe every reel in the open project at a slow cadence, recording observed generations and setting a soft captain-may-be-editing flag. Uses the broker's lowest priority. Hosted by the broker: starts and stops with it, follows the open project."),
    Verb("qualification", "Resolve", "Build the Ren Qualification project live tests use, or qualify the broker on it (media|reset|qualify)",
         module_argv=("library.tools.qualification_project",)),
    Verb("computer-use", "Resolve", "Drive Resolve's UI from a model: status, run a task, or run a scripted UI task (status|run|script)",
         module_argv=("library.tools.computer_use",)),

    Verb("search", "Footage", "Find where in a project's footage something happens",
         module_argv=(_FOOTAGE_QUERY, "search")),
    Verb("search-index", "Footage", "Build the footage search index (writes into the project's scratch)",
         module_argv=(_FOOTAGE_QUERY, "build")),
    Verb("analyze", "Footage", "Analyse a folder of footage without editing: steps, per-source memory, indexes",
         module_argv=("library.tools.footage_analysis",)),
    Verb("export-memory", "Footage", "Write the versioned, path-portable export of a project's footage memory",
         module_argv=("library.tools.memory_export",)),
    Verb("eval-search", "Footage", "Score footage search against a pre-registered, hand-marked query set",
         module_argv=("library.tools.retrieval_eval",)),

    Verb("eval", "Eval", "Run the standing request-following eval (report only; gates nothing)",
         module_argv=("library.tools.eval_harness",)),

    Verb("worker", "Fabric", "This machine's Ren Fabric worker: its manifest, and whether a task's requirements match it",
         module_argv=("library.tools.worker_manifest",),
         detail="`ren worker manifest` prints this worker's advertisement as JSON, "
                "assembled from the probes ren doctor and the broker already run. "
                "`--check` exits 1 when a required need is unmet."),
    Verb("schedule", "Fabric", "Run a Ren Fabric task graph on this machine",
         module_argv=("library.tools.fabric_scheduler",),
         detail="`ren fabric schedule <graph.json>` runs a task graph on this machine: "
                "places ready tasks whose capabilities this worker's manifest meets "
                "and whose leases are free, and resumes from the last checkpoint."),
)


def manage_project_subcommands() -> tuple:
    """Every `manage_project.py` subcommand, in registry order."""
    return tuple(verb.subcommand for verb in VERBS if verb.subcommand)


def for_subcommand(subcommand: str) -> Verb:
    """The verb that execs `manage_project.py <subcommand>`; KeyError when unregistered."""
    return _BY_SUBCOMMAND[subcommand]


_BY_SUBCOMMAND = {verb.subcommand: verb for verb in VERBS if verb.subcommand}
