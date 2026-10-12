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
    effects: tuple = ()
    """What running this verb may touch: a subset of `EFFECTS`, worst case
    over its subcommands and flags. Every verb declares at least one
    (`tests/contracts/test_verb_catalog.py`)."""
    json: bool = False
    """Accepts `--json` (on at least one subcommand, where it has several)."""
    dry_run: bool = False
    """Accepts `--dry-run`: shows what it would change and changes nothing."""

    @property
    def help(self) -> str:
        return self.detail or self.summary

    @property
    def read_only(self) -> bool:
        """Safe to call at any time: it reads, and changes nothing anywhere."""
        return set(self.effects) <= READ_ONLY_EFFECTS

    def as_record(self) -> dict:
        """This verb's row of the catalog `ren --help --json` prints."""
        if self.subcommand:
            runs = f"manage_project.py {self.subcommand}"
        elif self.module_argv:
            runs = "-m " + " ".join(self.module_argv)
        else:
            runs = f"builtin {self.builtin}"
        return {"name": self.name, "group": self.group,
                "summary": self.summary, "help": self.help,
                "effects": list(self.effects), "read_only": self.read_only,
                "json": self.json, "dry_run": self.dry_run, "runs": runs}


EFFECTS = {
    "read": "reads local files: a project, Ren's config, ledgers or the footage",
    "project_write": "writes local files: a project's folder, or Ren's own "
                     "config, runtime or ledgers on this machine",
    "resolve_read": "asks DaVinci Resolve or its broker what it holds",
    "resolve_write": "changes something in DaVinci Resolve (a timeline, the "
                     "media pool, markers, a render job)",
    "render": "renders picture or sound to a file",
    "heavy_lock": "takes the machine's heavy-work lock (a model pass, a "
                  "build or a render), so it may queue behind other heavy work",
    "network": "may reach the network (a model download, a release, a remote "
               "model endpoint)",
}
"""The effect vocabulary a verb's `effects` is drawn from."""

READ_ONLY_EFFECTS = frozenset({"read", "resolve_read"})


_FOOTAGE_QUERY = "library.tools.analysis.footage_query"

# A new verb is registered a SECOND time in `native/ren-cli/src/commands.rs`
# (`VERBS`), and the two lists must stay in sync: the native front door
# refuses a verb its own list does not know before exec'ing this module.
VERBS = (
    Verb("doctor", "Setup", "Check what this Mac can do with Ren (--for <capability>); changes nothing",
         effects=("read", "resolve_read"), json=True,
         builtin="doctor",
         detail="Prints only FAIL/WARN rows plus one summary line by default; pass `--all` for every check or `--json` for the report as JSON."),
    Verb("setup", "Setup", "Install Ren's runtime and system prerequisites; optional verified packs with --with",
         effects=("read", "project_write", "network"),
         builtin="setup",
         detail="Install the Python 3.12 runtime and locked dependencies, Node renderer dependencies, ffmpeg, and the default project folders. Optional model packs: panns, mfa, ecapa, deepfilter. Example: `ren setup --with panns`."),
    Verb("uninstall", "Setup", "Remove Ren's runtime and cache; customer data is kept",
         effects=("read", "project_write"), dry_run=True,
         builtin="uninstall",
         detail="Remove Ren's app runtime and cache after confirmation. Model downloads are kept unless `--models` is passed. Projects, footage, exports, shared assets, and user configuration are never removed."),
    Verb("config", "Setup", "Show where each setting comes from; --init writes a starter file",
         effects=("read", "project_write"),
         builtin="config"),
    Verb("support-bundle", "Setup", "Collect a redacted support bundle (version, config, doctor, recent errors; never footage)",
         effects=("read", "project_write"),
         builtin="support-bundle"),
    Verb("version", "Setup", "Show the Ren build (version, commit, channel, engine root)",
         effects=("read",),
         builtin="version"),
    Verb("upgrade", "Setup", "Install and verify an engine tree, then activate it",
         effects=("read", "project_write"),
         builtin="upgrade",
         detail="Install a Ren checkout or built engine tree alongside the active version, "
                "verify it, then switch the current version. Usage: `ren upgrade <engine-source> "
                "[--channel CHANNEL]`"),
    Verb("rollback", "Setup", "Switch to the previously active Ren version",
         effects=("read", "project_write"),
         builtin="rollback",
         detail="Switch the current pointer back to the previously active engine version. "
                "This changes no project files."),
    Verb("update", "Setup", "Move this Ren install to a released version",
         effects=("read", "project_write", "network"),
         builtin="update",
         detail="Fetch the release artifact that .github/workflows/release.yml builds, "
                "verify its checksum, and activate it. Usage: `ren update [--url <artifact-url>] "
                "[--tag vX.Y.Z]`"),
    Verb("taste", "Setup", "Record an explicit creative preference shared across projects",
         effects=("read", "project_write"),
         module_argv=("library.tools.taste_profile",)),
    Verb("init", "Setup", "Create the projects root folder",
         effects=("read", "project_write"), subcommand="init-root"),
    Verb("setup-hooks", "Setup", "Install the marker-feedback hook for a host (plan by default)",
         effects=("read", "project_write"),
         subcommand="setup-hooks"),
    Verb("ci", "Setup", "This Mac's local CI service, merge queue, and upstream engine intake",
         effects=("read", "project_write", "heavy_lock", "network"),
         module_argv=("library.tools.local_ci",),
         detail="`ren ci status` shows the queue, results and upstream pins; `ren ci check <pr>` runs one PR's check; `ren ci ready <pr>` marks it for the queue; `ren ci upstream status` shows engine pins; `ren ci upstream expedite <engine> --reason <text>` requests the same qualification gate immediately; `ren ci install` installs the launchd agent (only after the delivering PR merges). SOP: docs/LOCAL_CI.md."),

    Verb("new", "Projects", "Create a project",
         effects=("read", "project_write"), subcommand="new"),
    Verb("projects", "Projects", "List projects",
         effects=("read",), subcommand="list"),
    Verb("series", "Projects", "Register a series' brief and episode database: list, show, add",
         effects=("read", "project_write"),
         module_argv=("library.tools.series_registry",),
         detail="The per-machine series registry (~/.config/ren/series.yaml): `ren series add <id> --dir <series folder>` registers a series' creative-direction document and episode database; `ren series show <id> --episode <n>` prints that episode's row, its status and what the interview would ask. Then `ren new <slug> --series <id> --episode <n>` makes the planning tree the brief. Ren never writes the episode database."),
    Verb("status", "Projects", "Show a project's pipeline status",
         effects=("read",), subcommand="status"),
    Verb("info", "Projects", "Show a project's configuration as JSON",
         effects=("read",), subcommand="info"),
    Verb("work-state", "Projects", "Read where a project left off",
         effects=("read",), json=True,
         module_argv=("library.tools.project_work_state",),
         detail="Read a short summary of settled decisions, latest changes, approvals and open work. No model call or Resolve access."),
    Verb("now", "Projects", "State plus next actions, in one call (read only)",
         effects=("read",), json=True,
         module_argv=("library.tools.project_work_state", "now"),
         detail="`ren now <project>` prints the summary line plus numbered next actions; `--json` gives the whole structure (pipeline, run, asks, reels, notes, config findings, next_actions). No model call or Resolve access."),
    Verb("asks", "Projects", "What the host model owes, each shared prompt once (read only)",
         effects=("read",), json=True,
         module_argv=("library.tools.pending_asks",),
         detail="`ren asks <project>` lists every pending handshake request (an `llm_requests/*.json` with no response), grouped by prompt: each shared text (prompt, constraints, shared context section, schema) is printed once, then each ask's own context and the response path to write. `--json` gives the same as a structure. No model call or Resolve access."),
    Verb("assignment", "Projects", "Read a request's assignment: state, history and the verb it waits on",
         effects=("read", "project_write"),
         module_argv=("library.tools.assignment_interaction",),
         detail="Print the assignment contract's envelope for the project's open request: its state (brief, draft, review, approved, delivered), the history of verbs that moved it and the resume point. `--authorize-replacement --id <id>` records the creator's yes to replacing that assignment's approved work. No model call or Resolve access."),
    Verb("task", "Projects", "Start or finish an isolated semantic task",
         effects=("read", "project_write"),
         subcommand="task",
         detail="`ren task <project> start <task>` allocates a ren/<task> "
                "worktree; `ren task <project> finish <task>` commits and "
                "merges semantic state, then reports whether a Resolve "
                "rebuild is needed. Use repeatable --claim paths for "
                "disjoint tasks. Resolve work happens after finish"),
    Verb("check", "Projects", "Run a project's readiness check",
         effects=("read",), subcommand="check"),
    Verb("edit", "Projects", "Run the editing pipeline on a project",
         effects=("read", "project_write", "resolve_read", "resolve_write", "render", "heavy_lock", "network"), json=True, dry_run=True, subcommand="run"),
    Verb("pause", "Projects", "Pause a project's pipeline run at the next step boundary",
         effects=("read", "project_write"), subcommand="pause",
         detail="Engage the handbrake: the current step finishes, then the run holds. "
                "Release it with `ren resume`. Usage: `ren pause <project> [--reason <text>] [--by <who>]`."),
    Verb("resume", "Projects", "Release a paused pipeline run",
         effects=("read", "project_write"), subcommand="resume",
         detail="Disengage the handbrake so the next run proceeds. "
                "Usage: `ren resume <project>`."),
    Verb("wait", "Projects", "Wait for the next thing a pipeline run needs (read only)",
         effects=("read",), json=True,
         module_argv=("library.tools.run_wait",),
         detail="`ren wait <project> [--timeout 100] [--json]` blocks until the run asks, "
                "a review gate pends, the handbrake engages, the run ends or a lock wait "
                "starts; it prints one JSON object with `next_command` and exits 0. "
                "The default timeout sits under the 120 s harness default. No model call or Resolve access."),
    Verb("ops", "Projects", "List the named pipeline operations and the scopes each runs at (read only)",
         effects=("read",), json=True,
         module_argv=("library.tools.operations", "--list"),
         detail="`ren ops` lists every operation the pipeline can be asked to run - its name, owning node, scopes and what it does; `--json` gives the same as a structure. Run one with `bin/vep -m library.tools.operations <name> --project <p>`. No model call or Resolve access."),
    Verb("trace", "Projects", "Regenerate the run traceback and artifact index",
         effects=("read", "project_write"), subcommand="trace"),
    Verb("profile", "Projects", "Show where a run spent its time, layer by layer (read only)",
         effects=("read",), json=True,
         module_argv=("library.tools.perf_ledger",),
         detail="Read a run back from pipeline_output/logs/perf_ledger.jsonl: Ren own-code versus "
                "external-engine wall time, per-capability split, measured call counts, and named "
                "layers. Changes nothing"),
    Verb("progress", "Projects", "Read plain-language progress and currently available capabilities",
         effects=("read", "resolve_read"),
         module_argv=("library.tools.progress",),
         detail="One read-only status call for a host: completed and active steps, a history-backed time estimate when available, partial capabilities ready now, and the Resolve broker's current state."),
    Verb("organize", "Projects", "Bring a project folder onto the standard layout",
         effects=("read", "project_write"), subcommand="organize"),
    Verb("archive", "Projects", "Archive a finished project",
         effects=("read", "project_write"), subcommand="archive"),
    Verb("spec", "Projects", "Translate, clarify and record a natural-language edit spec",
         effects=("read", "project_write"),
         module_argv=("library.tools.edit_spec",)),
    Verb("revise", "Projects", "Turn an editor's note into a verified change, in one verb",
         effects=("read", "project_write", "resolve_read", "resolve_write", "render", "heavy_lock"),
         module_argv=("library.tools.revise",),
         detail="`ren revise <project> \"<note>\"` (or `--note-id` for a pulled Resolve marker) translates the note, shows what would change, then patches the reel in place, touches it up, or rebuilds only what the note reaches - and records the outcome with a summary naming where the change landed. `ren revise <project> --at 0:12 [--reel N] \"<note>\"` anchors a chat note to that delivered-file time through the reel's `.deliver.json`, without Resolve. It stops to ask exactly one question when the note is ambiguous, and `ren revise <project>` continues the newest open revision (`--answer` answers its question, `--plan` stops before changing anything)."),
    Verb("reindex", "Projects", "Move a project's words onto Voz plus MFA (re-transcribe legacy indexes)",
         effects=("read", "project_write", "heavy_lock", "network"), subcommand="reindex",
         detail="Move a project's words onto Voz plus MFA: invalidate legacy temporal indexes, then re-transcribe them"),

    Verb("propose", "Reels", "Publish the chosen moments as the reel review file",
         effects=("read", "project_write"), subcommand="propose-reels"),
    Verb("approve", "Reels", "Approve proposed reels for building",
         effects=("read", "project_write"), subcommand="approve-reels",
         detail="`ren approve <project> 1 3` (or `--all`) rules on the moments in review/reel_proposals_v2.json, keeping every other ruling. `--note` records why; `--by` records who."),
    Verb("reject", "Reels", "Reject proposed reels, recording why",
         effects=("read", "project_write"), subcommand="reject-reels",
         detail="`ren reject <project> 2 --note \"why\"` (or `--all --note \"why\"`) rules on the moments in review/reel_proposals_v2.json, keeping every other ruling."),
    Verb("build", "Reels", "Build approved reels in Resolve",
         effects=("read", "project_write", "resolve_read", "resolve_write", "render", "heavy_lock"), json=True, subcommand="build-reels"),
    Verb("adopt", "Reels", "Adopt existing reel timelines as Ren-built by exact Resolve id",
         effects=("read", "project_write", "resolve_read"),
         module_argv=("library.tools.resolve_axi", "ownership"),
         detail="Record who adopted each existing final reel timeline by its exact Resolve unique id. Reads the open project and its current timeline, writes only the local ownership ledger, then verifies the live inventory stayed unchanged."),
    Verb("touch", "Reels", "Apply a small change to a built reel (a touch-up, not a rebuild)",
         effects=("read", "project_write", "resolve_read", "resolve_write"), subcommand="touch-reel",
         detail="Apply a structured change to a built reel's existing timeline through composed_edit, staged and verified"),
    Verb("still-overlays", "Reels", "Freeze static overlay videos into stills, in place (no rebuild)",
         effects=("read", "project_write", "resolve_read", "resolve_write", "render"), json=True, dry_run=True, subcommand="still-overlays",
         detail="Replace each reel's static overlay videos (post headers, TV frames) with frozen stills at identical transforms, extended to the reel end where cut short; animated overlays are left alone. Backup, inventory diff, refuse-and-restore per reel. Pass `--json` for the report as JSON."),
    Verb("dedupe-stills", "Reels", "Point identical stills at one pool item, in place (no rebuild)",
         effects=("read", "project_write", "resolve_read", "resolve_write"), json=True, dry_run=True, subcommand="dedupe-stills",
         detail="Collapse duplicate stills (the TV frame on every reel) onto one content-addressed pool item at identical spans, transforms and markers; superseded pool items stay pooled for `ren gc`. Backup, inventory diff, refuse-and-restore per reel. Pass `--json` for the report as JSON."),
    Verb("touch-master", "Reels", "Apply a small change to the master timeline (a touch-up, not a rebuild)",
         effects=("read", "project_write", "resolve_read", "resolve_write"), subcommand="touch-master",
          detail="Apply a structured change to the master timeline's existing timeline through composed_edit, staged and verified"),
    Verb("replay-ledger", "Reels", "Apply a recorded edit ledger to a timeline directly, without a build",
         effects=("read", "project_write", "resolve_read", "resolve_write"), subcommand="replay-ledger",
          detail="Read the project's edit ledger and apply each row to the target timeline through the edit algebra, journaled so `ren undo` can reverse it"),
    Verb("undo", "Reels", "Undo a touch-up (in place) or rebuild (by version)",
         effects=("read", "project_write", "resolve_read", "resolve_write"), subcommand="undo",
          detail="Reverse a Ren act: a touch-up in place from its journal, a rebuild by rolling back to the version before it. "
                 "--act addresses any recorded act by id, not just the newest"),
    Verb("redo", "Reels", "Re-apply a reversed touch-up or rebuild",
         effects=("read", "project_write", "resolve_read", "resolve_write"), subcommand="redo",
          detail="Re-apply a reversed act: a touch from its journal's spec, a rolled-back rebuild from the plan moment of the act that had been rolled back"),
    Verb("dry-run", "Reels", "Plan a touch-up against the live timeline and stop",
         effects=("read", "resolve_read"), subcommand="ren-dry-run",
         detail="Dry-run the composed edit path (plan, read live, print, stop - never executes)"),
    Verb("deliver", "Reels", "Render approved reels to files (all signed-off, or one named reel)",
         effects=("read", "project_write", "resolve_read", "resolve_write", "render", "heavy_lock"), subcommand="deliver-reel"),
    Verb("preview", "Reels", "Render a watchable low-cost preview of each built reel (cached by content)",
         effects=("read", "project_write", "resolve_read", "render", "heavy_lock"), json=True,
         module_argv=("library.tools.reel_preview",),
         detail="`ren preview <project> [--reel N ...]` renders a half-resolution proxy of every approved, built reel (or the named ones) and names the file to watch. Headless through FilmCraft by default, so Resolve is not touched; `--engine resolve` renders the live timeline instead. A reel whose content has not changed since its last preview is not rendered again (a cache hit, recorded). One preview runs at a time, never beside a Resolve render or a model pass. Not a delivery: `ren deliver` makes the file you ship."),
    Verb("round-page", "Reels", "Write this review round's page: previews, what changed, what waits on you, notes at a moment",
         effects=("read", "project_write"), json=True,
         module_argv=("library.tools.round_page",),
         detail="`ren round-page <project> [--open]` writes `pipeline_output/previews/round-<N>.html`: each reel's newest preview to play, the creative change summary per reel this round, the notes that opened the round and their outcomes, what is waiting on you (open revision questions, notes not yet revised, reels with no preview), and a \"note at this time\" control per reel. A note leaves the page as one `ren notes add` command - queued to the agent through Lavish with `--open` when lavish-axi is installed, else copied - so `ren notes` lists it and `ren revise --note-id` changes the reel. Renders nothing; `ren preview` rewrites it after every preview. Watching needs no Resolve."),
    Verb("watch", "Reels", "Have a model watch a delivered reel",
         effects=("read", "project_write"), subcommand="watch-reel",
         detail="Show a model the PICTURE of a delivered reel and ask what it sees. Renders nothing"),
    Verb("hear", "Reels", "Hear a delivered reel against its plan",
         effects=("read", "project_write"), subcommand="hear-reel",
         detail="Hear a delivered reel against its plan and report what diverged. Renders nothing, gates nothing"),
    Verb("drift", "Reels", "Compare each reel's build snapshot with the live timeline",
         effects=("read", "project_write", "resolve_read"), subcommand="drift",
         detail="Compare each reel's build snapshot against its live timeline and report the per-reel factor"),
    Verb("holds", "Reels", "Release a staging hold on a timeline awaiting promotion",
         effects=("read", "project_write"), subcommand="holds",
         detail="A staged timeline awaiting promotion is held against the cleanup sweep until it is promoted. "
                "Usage: `ren holds release <project> <staging timeline name>`. "
                "Promoting or discarding the staging releases its hold; this verb is the explicit release."),
    Verb("post-header", "Reels", "Put each reel's social-post header on its final, as a journaled touch",
         effects=("read", "project_write", "resolve_read", "resolve_write"), dry_run=True, subcommand="post-header"),
    Verb("shift-rows", "Reels", "Move named rows of each reel up or down by pixels, as a journaled touch",
         effects=("read", "project_write", "resolve_read", "resolve_write"), dry_run=True, subcommand="shift-rows"),
    Verb("scale-rows", "Reels", "Shrink or grow named rows of each reel about a point, as a journaled touch",
         effects=("read", "project_write", "resolve_read", "resolve_write"), dry_run=True, subcommand="scale-rows"),
    Verb("fit-picture", "Reels", "Put each reel's TV picture at a scale no phone crops, as a journaled touch",
         effects=("read", "project_write", "resolve_read", "resolve_write"), dry_run=True, subcommand="fit-picture"),
    Verb("caption-width", "Reels", "Narrow each reel's captions to the width the platforms leave clear, as a journaled touch",
         effects=("read", "project_write", "resolve_read", "resolve_write"), dry_run=True, subcommand="caption-width"),
    Verb("safe-zones", "Reels", "Put a platform safe-zone guide on reel timelines, switched off so it never renders",
         effects=("read", "project_write", "resolve_read", "resolve_write"), subcommand="safe-zones"),
    Verb("sign-off", "Reels", "Record the captain's sign-off on a built reel",
         effects=("read", "project_write"), subcommand="sign-off",
         detail="Sign off a BUILT reel, so promotion must declare before replacing it"),
    Verb("purge", "Reels", "Plan (default) or --apply the lean-retention purge",
         effects=("read", "project_write"), subcommand="purge",
         detail="Plan (default) or apply the lean-retention purge of superseded renders, quarantine, scratch and stale journals"),
    Verb("discharge", "Reels", "Discharge a dropped note a promotion filed",
         effects=("read", "project_write"), subcommand="discharge-uncarried",
         detail="Discharge a dropped captain's note a promotion filed, so its reel can be signed off"),
    Verb("variant", "Reels", "Two versions of one reel: new, build, list, diff, choose, merge",
         effects=("read", "project_write", "resolve_read", "resolve_write", "render", "heavy_lock"), subcommand="variant"),
    Verb("explore-variants", "Reels", "Fork one base commit into one isolated branch per direction: propose, plan, list, dispatch",
         effects=("read", "project_write"), subcommand="explore-variants"),
    Verb("merge-queue", "Reels", "Queue a branch's candidate against the master: queue, list, evaluate, merge",
         effects=("read", "project_write"), subcommand="merge-queue"),
    Verb("worktree", "Reels", "A task's own checkout of the project store: add, list, commit, merge, remove",
         effects=("read", "project_write"),
         subcommand="worktree",
         detail="Give a task its own checkout of the project's git store on branch ren/<task>, outside the project folder, and merge it back. Never moves the project checkout's branch; touches no Resolve"),
    Verb("rounds", "Reels", "What changed between two feedback rounds",
         effects=("read",), subcommand="round-diff"),
    Verb("revert", "Reels", "Revert a creative commit: record its inverse as a new commit",
         effects=("read", "project_write"), json=True,
         module_argv=("library.tools.creative_history", "revert"),
         detail="`ren revert <project> <commit>` records the commit's inverse standing on the timeline's head, by id or unique prefix. Refuses a commit later commits also changed, one the record cannot invert, or one outside its timeline's line; `--dest-tracks <file>` additionally proves every restored change against the destination's current track reads"),
    Verb("pr-body", "Reels", "Generate the PR-body enumeration for a ledger change (read only)",
         effects=("read",), subcommand="pr-body"),
    Verb("notes", "Reels", "Show which timeline note went to which step",
         effects=("read", "project_write", "resolve_read"), subcommand="notes",
         json=True,
         detail="`ren notes <project>` shows which timeline note went to which step, with each watched-file note's moment and the `ren revise --note-id` that changes it. "
                "`ren notes pull <project> [--all]` pulls new notes off Resolve first (needs Resolve open). "
                "`ren notes add <project> --at 0:12 [--reel N] [--file <preview or delivered file>] \"<note>\"` stores a note at a moment in a watched file without revising it yet (what the round's review page sends; `--json` prints it). "
                "`ren notes import <project> <export.csv|fiojson|edl> --reel N` ingests a Frame.io comment "
                "export into the same note store, mapping file time through the delivery record; "
                "unmappable comments are listed, never guessed."),
    Verb("outcomes", "Reels", "Show feedback notes and their recorded outcomes",
         effects=("read", "project_write"), json=True,
         module_argv=("library.tools.feedback_outcomes",),
         detail="`ren outcomes <project>` prints a budgeted summary (counts per action and per verification state, plus the 5 newest notes). Pass `--full` for the full per-note ledger, `--html` for a standalone report or `--backfill` to derive entries from existing records. Filter with `--from-reel N` / `--to-reel N`."),
    Verb("take-pick", "Reels", "Print one reel's takes, freshness, snap deltas and boundary words in one read",
         effects=("read",), json=True, subcommand="take-pick-preview"),
    Verb("reels", "Reels", "Read every reel's state in one call (read only)",
         effects=("read",), json=True,
         module_argv=("library.tools.reel_state",),
         detail="`ren reels <project> [--reel N] [--json]` reads approval, latest build summary, version, sign-off, last delivery and owed model answers per reel. Text default is one line per reel; `--json` gives everything."),
    Verb("commits", "Reels", "Show a project's creative commits: what each touch-up changed and why",
         effects=("read",), json=True,
         module_argv=("library.tools.creative_history", "commits"),
         detail="Read the commit graph touch-ups record: `ren commits <project>` lists every commit, `--timeline` one reel's chain, `--show` one commit by id or unique prefix. Read only"),
    Verb("branches", "Reels", "Show a project's creative branches: what each branch stands on",
         effects=("read",), json=True,
         module_argv=("library.tools.creative_history", "branches"),
         detail="Read the branches recorded over the commit graph: `ren branches <project>` lists every branch, `--show` one branch by name. Read only"),
    Verb("tag", "Reels", "Name a creative commit a version (or list the names given)",
         effects=("read", "project_write"), json=True,
         module_argv=("library.tools.creative_history", "tag"),
         detail="`ren tag <project> <name> <commit>` points a version name at a commit by id or unique prefix; `--list` shows the names. A name that already points somewhere is refused unless `--move` is passed"),
    Verb("cherry-pick", "Reels", "Replay a creative commit's changes as a new commit",
         effects=("read", "project_write"), json=True,
         module_argv=("library.tools.creative_history", "cherry-pick"),
         detail="`ren cherry-pick <project> <commit>` replays the commit's changes onto its timeline's head by id or unique prefix. Refuses a commit later commits also changed, a merge, or one outside its timeline's line; `--dest-tracks <file>` additionally proves every replayed change against the destination's current track reads"),

    Verb("pool-organize", "Resolve", "File a Resolve project's media pool",
         effects=("read", "resolve_read", "resolve_write"), subcommand="resolve-organize",
         detail="File a Resolve project's media pool: reels by plan state, assets under the reel that uses them"),
    Verb("pool-prune", "Resolve", "Remove media-pool items no timeline plays",
         effects=("read", "project_write", "resolve_read", "resolve_write"), subcommand="resolve-prune",
         detail="Remove media-pool items no timeline plays, and delete their files. IRREVERSIBLE; plans by default"),
    Verb("gc", "Resolve", "Collect Ren-generated orphans into trash (plan by default)",
         effects=("read", "project_write", "resolve_read", "resolve_write"), subcommand="asset-gc",
         detail="Move Ren-generated pool items no timeline uses out of the pool and their files into a trash folder with a manifest - moved, never deleted. Footage and captain-imported media are never collected. Plans by default"),
    Verb("mark-master", "Resolve", "Mark the master timeline where each reel was taken",
         effects=("read", "resolve_read", "resolve_write"), subcommand="resolve-mark-master",
         detail="Mark the master timeline with where each reel was taken from. Markers only, and reversible"),
    Verb("relink", "Resolve", "Relink offline media after a move",
         effects=("read", "resolve_read", "resolve_write"), subcommand="relink"),
    Verb("timeline", "Resolve", "Answer what a timeline holds from its recorded generations; touches no Resolve",
         effects=("read",),
         module_argv=("library.tools.timeline_shadow",),
         detail="log, show, clips, markers or diff a timeline's recorded generations (the shadow store). Never reads Resolve. Pass the Ren project folder with --reel N to read a reel's recorded exact final timeline (--staging for its staging sibling) instead of --project/--timeline"),
    Verb("resolved", "Resolve", "Run or ask the broker that schedules the one Resolve (serve|ensure|status|list|submit|result|stop|kpi)",
         effects=("read", "project_write", "resolve_read", "resolve_write"), json=True,
          module_argv=("library.tools.resolved",)),
    Verb("shadow-watch", "Resolve", "Watch the current timeline for hand edits on a schedule (standalone; the broker hosts it)",
         effects=("read", "project_write", "resolve_read"),
          module_argv=("library.tools.shadow_watcher",),
          detail="Observe Resolve's current timeline at a slow cadence, recording observed generations and setting a soft captain-may-be-editing flag. Uses the broker's lowest priority. Hosted by the broker: starts and stops with it, follows the open project."),
    Verb("qualification", "Resolve", "Build, qualify or run the full real-footage workflow in Ren Qualification (media|reset|qualify|workflow|pm-e2e)",
         effects=("read", "project_write", "resolve_read", "resolve_write", "render", "heavy_lock"),
         module_argv=("library.tools.qualification_project",)),
    Verb("project-model", "Resolve", "Plan a project's model onto a headless engine (plan by default; --execute runs it)",
         effects=("read", "project_write", "render"),
         module_argv=("library.tools.project_model_cli",),
         detail="`ren project-model project <project> --engine filmcraft|effectcraft --out <plan.json>` plans the project's canonical model onto the named engine, writes the plan, and prints a summary - without running anything. Pass `--execute` to run the plan on the pinned headless CLI. `--model <model.json>` projects an explicit model document instead of the project's store."),
    Verb("computer-use", "Resolve", "Drive Resolve's UI from a model: status, run a task, or run a scripted UI task (status|run|script)",
         effects=("read", "resolve_read", "resolve_write", "network"),
         module_argv=("library.tools.computer_use",)),
    Verb("effects", "Resolve", "Search the installed ResolveFX/OFX effects: route and verification state (read only)",
         effects=("read", "resolve_read"),
         module_argv=("library.tools.resolvefx_effects",)),

    Verb("search", "Footage", "Find where in a project's footage something happens",
         effects=("read", "heavy_lock", "network"), json=True,
         module_argv=(_FOOTAGE_QUERY, "search")),
    Verb("where", "Footage", "Where is this person in the footage (region tracks, or the region at --at)",
         effects=("read",), json=True,
         module_argv=(_FOOTAGE_QUERY, "where")),
    Verb("search-index", "Footage", "Build the footage search index (writes into the project's scratch)",
         effects=("read", "project_write", "network"),
         module_argv=(_FOOTAGE_QUERY, "build")),
    Verb("analyze", "Footage", "Analyse a folder of footage without editing: steps, per-source memory, indexes",
         effects=("read", "project_write", "heavy_lock", "network"), json=True,
         module_argv=("library.tools.footage_analysis",)),
    Verb("export-memory", "Footage", "Write the versioned, path-portable export of a project's footage memory",
         effects=("read", "project_write"),
         module_argv=("library.tools.memory_export",)),
    Verb("eval-search", "Footage", "Score footage search against a pre-registered, hand-marked query set",
         effects=("read", "network"),
         module_argv=("library.tools.retrieval_eval",)),

    Verb("eval", "Eval", "Run the standing request-following eval (report only; gates nothing)",
         effects=("read", "project_write", "resolve_read", "resolve_write", "render", "heavy_lock", "network"),
         module_argv=("library.tools.eval_harness",)),

    Verb("worker", "Fabric", "This machine's Ren Fabric worker: its manifest, and whether a task's requirements match it",
         effects=("read", "resolve_read"),
         module_argv=("library.tools.worker_manifest",),
         detail="`ren worker manifest` prints this worker's advertisement as JSON, "
                "assembled from the probes ren doctor and the broker already run. "
                "`--check` exits 1 when a required need is unmet."),
    Verb("schedule", "Fabric", "Run a Ren Fabric task graph on this machine",
         effects=("read", "project_write", "heavy_lock"),
         module_argv=("library.tools.fabric_scheduler",),
         detail="`ren schedule <graph.json> [--timeout SECS]` runs a task graph on this machine: "
                "places ready tasks whose capabilities this worker's manifest meets "
                "and whose leases are free, and resumes from the last checkpoint."),
    Verb("events", "Fabric", "Read a project's event-sourced operation log",
         effects=("read",),
         module_argv=("library.tools.event_log",),
         detail="Read the operations a project's workers performed, oldest first: "
                "`ren events <project>` lists every event, `--task` one worker's, "
                "`--since` only what came after a sequence number. Read only."),
)


def catalog() -> dict:
    """The self-describing verb catalog `ren --help --json` prints."""
    return {"effects": dict(EFFECTS),
            "verbs": [verb.as_record() for verb in VERBS]}


def manage_project_subcommands() -> tuple:
    """Every `manage_project.py` subcommand, in registry order."""
    return tuple(verb.subcommand for verb in VERBS if verb.subcommand)


def for_subcommand(subcommand: str) -> Verb:
    """The verb that execs `manage_project.py <subcommand>`; KeyError when unregistered."""
    return _BY_SUBCOMMAND[subcommand]


_BY_SUBCOMMAND = {verb.subcommand: verb for verb in VERBS if verb.subcommand}
