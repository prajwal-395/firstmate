//! The native verbs: `--version`, `--help`, `info`, `status`, `check`.
//!
//! Each mirrors the Python function it replaces, so its stdout, stderr and
//! exit code are identical. Where the Python reaches a part this crate does
//! not port (a format migration, the safe-zone layout engine), the verb
//! reports `Fallback` and the front door execs Python rather than drifting.

use std::env;
use std::fs;
use std::path::{Path, PathBuf};
use std::process::Command as ProcCommand;
use std::time::{Duration, Instant};

use crate::engine;
use crate::json::Json;
use crate::project::{
    self, Located, ProjectConfig, config_to_dict, project_status,
};

/// What a native verb did: finished with an exit code, or the project
/// needs a part only Python has, so the front door must exec Python.
pub enum Outcome {
    Exited(i32),
    Fallback,
}

const REFUSAL_EXIT_CODE: i32 = 4;

/// `ren.cli.version_line` - the build, and the engine it runs.
pub fn version_line(root: Option<&Path>) -> String {
    let _ = root;
    let version = version_string();
    let engine = match engine::find_engine_root() {
        Some(root) => root.to_string_lossy().to_string(),
        None => "not found".to_string(),
    };
    format!("ren {version} (engine {engine})")
}

/// `ren.version.version_string` - the SemVer plus build metadata, without
/// the engine the way `ren --help` prints it.
fn version_string() -> String {
    let root = engine::find_engine_root().unwrap_or_else(|| PathBuf::from("."));
    let version = read_python_assign(&root.join("ren").join("version.py"), "__version__")
        .unwrap_or_else(|| "0.1.0".to_string());
    let (sha, built_at, channel, edition) = build_info(&root);
    let mut metadata: Vec<String> = vec![];
    if !sha.is_empty() {
        metadata.push(sha);
    }
    if !channel.is_empty() {
        metadata.push(channel);
    }
    if !edition.is_empty() {
        metadata.push(edition);
    }
    if !built_at.is_empty() {
        metadata.push(built_at);
    }
    if metadata.is_empty() {
        return version;
    }
    format!("{version}+{}", metadata.join("."))
}

/// The packaged build record, or the checkout's live one - `ren.version`.
fn build_info(root: &Path) -> (String, String, String, String) {
    let build_py = root.join("ren").join("_build.py");
    if build_py.is_file() {
        let sha = read_python_assign(&build_py, "BUILD_SHA").unwrap_or_default();
        let built_at = read_python_assign(&build_py, "BUILD_DATE").unwrap_or_default();
        let channel = read_python_assign(&build_py, "BUILD_CHANNEL").unwrap_or_default();
        let edition = read_python_assign(&build_py, "BUILD_EDITION").unwrap_or_default();
        return (sha, built_at, channel, edition);
    }
    let sha = git_short_sha(&root.join("ren"));
    let edition = current_edition(root);
    (sha, String::new(), "dev".to_string(), edition)
}

/// `git rev-parse --short=12 HEAD` in the ren package dir, or "" when git
/// cannot answer.
fn git_short_sha(cwd: &Path) -> String {
    let output = ProcCommand::new("git")
        .arg("rev-parse")
        .arg("--short=12")
        .arg("HEAD")
        .current_dir(cwd)
        .output();
    match output {
        Ok(output) if output.status.success() => {
            String::from_utf8_lossy(&output.stdout).trim().to_string()
        }
        _ => String::new(),
    }
}

/// `ren.edition.current_edition` for a checkout: the build record's edition,
/// else `$REN_EDITION` (which must name a real edition), else `personal`.
fn current_edition(root: &Path) -> String {
    let build_py = root.join("ren").join("_build.py");
    if build_py.is_file() {
        if let Some(edition) = read_python_assign(&build_py, "BUILD_EDITION") {
            if edition == "public" || edition == "personal" {
                return edition;
            }
        }
        return "personal".to_string();
    }
    match env::var("REN_EDITION") {
        Ok(value) if value == "public" || value == "personal" => value,
        _ => "personal".to_string(),
    }
}

/// Read `NAME = "value"` off a generated Python file, the way the packaging
/// script writes the build record.
fn read_python_assign(path: &Path, name: &str) -> Option<String> {
    let text = fs::read_to_string(path).ok()?;
    for line in text.lines() {
        let line = line.trim();
        let Some(rest) = line.strip_prefix(name) else {
            continue;
        };
        let rest = rest.trim_start();
        let Some(rest) = rest.strip_prefix('=') else {
            continue;
        };
        let value = rest.trim();
        let Some(quote) = value.chars().next() else {
            continue;
        };
        if quote != '"' && quote != '\'' {
            continue;
        }
        let inner = &value[1..];
        if let Some(end) = inner.find(quote) {
            return Some(inner[..end].to_string());
        }
    }
    None
}

struct Verb {
    name: &'static str,
    group: &'static str,
    summary: &'static str,
}

/// `ren.commands.VERBS` - the one registry `ren --help` renders.
///
/// A new verb is registered a SECOND time here, and the two lists must
/// stay in sync: the native front door refuses a verb this list does not
/// know before exec'ing the Python implementation, so a verb missing
/// from here is unreachable even though Python knows it.
const VERBS: &[Verb] = &[
    Verb { name: "doctor", group: "Setup", summary: "Check what this Mac can do with Ren (--for <capability>); changes nothing" },
    Verb { name: "setup", group: "Setup", summary: "Install Ren's runtime and system prerequisites; optional verified packs with --with" },
    Verb { name: "uninstall", group: "Setup", summary: "Remove Ren's runtime and cache; customer data is kept" },
    Verb { name: "config", group: "Setup", summary: "Show where each setting comes from; --init writes a starter file" },
    Verb { name: "support-bundle", group: "Setup", summary: "Collect a redacted support bundle (version, config, doctor, recent errors; never footage)" },
    Verb { name: "version", group: "Setup", summary: "Show the Ren build (version, commit, channel, engine root)" },
    Verb { name: "upgrade", group: "Setup", summary: "Install and verify an engine tree, then activate it" },
    Verb { name: "rollback", group: "Setup", summary: "Switch to the previously active Ren version" },
    Verb { name: "update", group: "Setup", summary: "Move this Ren install to a released version" },
    Verb { name: "taste", group: "Setup", summary: "Record an explicit creative preference shared across projects" },
    Verb { name: "init", group: "Setup", summary: "Create the projects root folder" },
    Verb { name: "setup-hooks", group: "Setup", summary: "Install the marker-feedback hook for a host (plan by default)" },
    Verb { name: "new", group: "Projects", summary: "Create a project" },
    Verb { name: "projects", group: "Projects", summary: "List projects" },
    Verb { name: "status", group: "Projects", summary: "Show a project's pipeline status" },
    Verb { name: "info", group: "Projects", summary: "Show a project's configuration as JSON" },
    Verb { name: "task", group: "Projects", summary: "Start or finish an isolated semantic task" },
    Verb { name: "check", group: "Projects", summary: "Run a project's readiness check" },
    Verb { name: "edit", group: "Projects", summary: "Run the editing pipeline on a project" },
    Verb { name: "trace", group: "Projects", summary: "Regenerate the run traceback and artifact index" },
    Verb { name: "profile", group: "Projects", summary: "Show where a run spent its time, layer by layer (read only)" },
    Verb { name: "organize", group: "Projects", summary: "Bring a project folder onto the standard layout" },
    Verb { name: "archive", group: "Projects", summary: "Archive a finished project" },
    Verb { name: "spec", group: "Projects", summary: "Translate, clarify and record a natural-language edit spec" },
    Verb { name: "reindex", group: "Projects", summary: "Move a project's words onto Voz plus MFA (re-transcribe legacy indexes)" },
    Verb { name: "propose", group: "Reels", summary: "Publish the chosen moments as the reel review file" },
    Verb { name: "build", group: "Reels", summary: "Build approved reels in Resolve" },
    Verb { name: "adopt", group: "Reels", summary: "Adopt existing reel timelines as Ren-built by exact Resolve id" },
    Verb { name: "touch", group: "Reels", summary: "Apply a small change to a built reel (a touch-up, not a rebuild)" },
    Verb { name: "touch-master", group: "Reels", summary: "Apply a small change to the master timeline (a touch-up, not a rebuild)" },
    Verb { name: "replay-ledger", group: "Reels", summary: "Apply a recorded edit ledger to a timeline directly, without a build" },
    Verb { name: "undo", group: "Reels", summary: "Undo a touch-up (in place) or rebuild (by version)" },
    Verb { name: "redo", group: "Reels", summary: "Re-apply a reversed touch-up or rebuild" },
    Verb { name: "dry-run", group: "Reels", summary: "Plan a touch-up against the live timeline and stop" },
    Verb { name: "deliver", group: "Reels", summary: "Render one approved reel to a file" },
    Verb { name: "watch", group: "Reels", summary: "Have a model watch a delivered reel" },
    Verb { name: "hear", group: "Reels", summary: "Hear a delivered reel against its plan" },
    Verb { name: "drift", group: "Reels", summary: "Compare each reel's build snapshot with the live timeline" },
    Verb { name: "post-header", group: "Reels", summary: "Put each reel's social-post header on its final, as a journaled touch" },
    Verb { name: "shift-rows", group: "Reels", summary: "Move named rows of each reel up or down by pixels, as a journaled touch" },
    Verb { name: "scale-rows", group: "Reels", summary: "Shrink or grow named rows of each reel about a point, as a journaled touch" },
    Verb { name: "fit-picture", group: "Reels", summary: "Put each reel's TV picture at a scale no phone crops, as a journaled touch" },
    Verb { name: "caption-width", group: "Reels", summary: "Narrow each reel's captions to the width the platforms leave clear, as a journaled touch" },
    Verb { name: "safe-zones", group: "Reels", summary: "Put a platform safe-zone guide on reel timelines, switched off so it never renders" },
    Verb { name: "sign-off", group: "Reels", summary: "Record the captain's sign-off on a built reel" },
    Verb { name: "purge", group: "Reels", summary: "Plan (default) or --apply the lean-retention purge" },
    Verb { name: "discharge", group: "Reels", summary: "Discharge a dropped note a promotion filed" },
    Verb { name: "variant", group: "Reels", summary: "Two versions of one reel: new, build, list, diff, choose, merge" },
    Verb { name: "explore-variants", group: "Reels", summary: "Fork one base commit into one isolated branch per direction: propose, plan, list, dispatch" },
    Verb { name: "merge-queue", group: "Reels", summary: "Queue a branch's candidate against the master: queue, list" },
    Verb { name: "worktree", group: "Reels", summary: "A task's own checkout of the project store: add, list, commit, merge, remove" },
    Verb { name: "rounds", group: "Reels", summary: "What changed between two feedback rounds" },
    Verb { name: "revert", group: "Reels", summary: "Revert a creative commit: record its inverse as a new commit" },
    Verb { name: "pr-body", group: "Reels", summary: "Generate the PR-body enumeration for a ledger change (read only)" },
    Verb { name: "notes", group: "Reels", summary: "Show which timeline note went to which step" },
    Verb { name: "take-pick", group: "Reels", summary: "Print one reel's takes, freshness, snap deltas and boundary words in one read" },
    Verb { name: "commits", group: "Reels", summary: "Show a project's creative commits: what each touch-up changed and why" },
    Verb { name: "branches", group: "Reels", summary: "Show a project's creative branches: what each branch stands on" },
    Verb { name: "tag", group: "Reels", summary: "Name a creative commit a version (or list the names given)" },
    Verb { name: "pool-organize", group: "Resolve", summary: "File a Resolve project's media pool" },
    Verb { name: "pool-prune", group: "Resolve", summary: "Remove media-pool items no timeline plays" },
    Verb { name: "mark-master", group: "Resolve", summary: "Mark the master timeline where each reel was taken" },
    Verb { name: "relink", group: "Resolve", summary: "Relink offline media after a move" },
    Verb { name: "timeline", group: "Resolve", summary: "Answer what a timeline holds from its recorded generations; touches no Resolve" },
    Verb { name: "resolved", group: "Resolve", summary: "Run or ask the broker that schedules the one Resolve (serve|ensure|status|list|submit|result|stop|kpi)" },
    Verb { name: "shadow-watch", group: "Resolve", summary: "Watch a project's reels for hand edits on a schedule (standalone; the broker hosts it)" },
    Verb { name: "qualification", group: "Resolve", summary: "Build the Ren Qualification project live tests use, or qualify the broker on it (media|reset|qualify)" },
    Verb { name: "computer-use", group: "Resolve", summary: "Drive Resolve's UI from a model: status, run a task, or run a scripted UI task (status|run|script)" },
    Verb { name: "search", group: "Footage", summary: "Find where in a project's footage something happens" },
    Verb { name: "search-index", group: "Footage", summary: "Build the footage search index (writes into the project's scratch)" },
    Verb { name: "analyze", group: "Footage", summary: "Analyse a folder of footage without editing: steps, per-source memory, indexes" },
    Verb { name: "export-memory", group: "Footage", summary: "Write the versioned, path-portable export of a project's footage memory" },
    Verb { name: "eval-search", group: "Footage", summary: "Score footage search against a pre-registered, hand-marked query set" },
    Verb { name: "eval", group: "Eval", summary: "Run the standing request-following eval (report only; gates nothing)" },
    Verb { name: "worker", group: "Fabric", summary: "This machine's Ren Fabric worker: its manifest, and whether a task's requirements match it" },
    Verb { name: "schedule", group: "Fabric", summary: "Run a Ren Fabric task graph on this machine" },
];

/// `ren.cli.usage` - the verb table, the build, and the engine.
pub fn usage() -> String {
    let width = VERBS.iter().map(|verb| verb.name.len()).max().unwrap_or(0);
    let mut lines = vec![
        "usage: ren <verb> [args...]".to_string(),
        String::new(),
        "Ren is the front door to the video editing engine. `ren <verb> --help` shows a verb's own options.".to_string(),
        String::new(),
    ];
    let mut group = "";
    for verb in VERBS {
        if verb.group != group {
            group = verb.group;
            lines.push(format!("{group}:"));
        }
        lines.push(format!("  {}{}  {}", verb.name, " ".repeat(width - verb.name.len()), verb.summary));
    }
    lines.push(String::new());
    lines.push(format!("ren {}", version_string()));
    let engine = match engine::find_engine_root() {
        Some(found) => found.to_string_lossy().to_string(),
        None => "not found".to_string(),
    };
    lines.push(format!("Engine: {engine}"));
    lines.join("\n")
}

/// Whether `name` is a registered verb - the front door's unknown-verb check.
pub fn is_known_verb(name: &str) -> bool {
    VERBS.iter().any(|verb| verb.name == name)
}

/// A located project, or the reason the native verb cannot serve it.
#[allow(clippy::large_enum_variant)]
enum LocateResult {
    Found(ProjectConfig),
    /// The project needs a part only Python has (a format migration, the
    /// safe-zone layout engine): exec Python rather than drift.
    Fallback,
    NotFound(String),
    LoadError(String),
}

/// Locate and load a project, or report that Python must take it over.
fn locate_project(reference: &str, engine_root: &Path) -> LocateResult {
    match project::get_project(reference, engine_root) {
        Located::Found(config) => {
            let needs_python = !matches!(config.project_format_version, Json::Int(2))
                || config.pipeline.safe_zones.is_some();
            if needs_python {
                return LocateResult::Fallback;
            }
            LocateResult::Found(config)
        }
        Located::NotFound(message) => {
            LocateResult::NotFound(project::project_not_found_refusal(reference, &message))
        }
        Located::LoadError(message) => LocateResult::LoadError(message),
    }
}

/// Run a project verb: serve it natively, or turn the locate result into the
/// refusal, the fallback, or the load error the Python would have printed.
fn run_project_command(
    reference: &str,
    engine_root: &Path,
    serve: impl FnOnce(&ProjectConfig) -> Result<Outcome, String>,
) -> Outcome {
    match locate_project(reference, engine_root) {
        LocateResult::Found(config) => match serve(&config) {
            Ok(outcome) => outcome,
            Err(refusal) => {
                eprintln!("{refusal}");
                Outcome::Exited(REFUSAL_EXIT_CODE)
            }
        },
        LocateResult::Fallback => Outcome::Fallback,
        LocateResult::NotFound(refusal) => {
            eprintln!("{refusal}");
            Outcome::Exited(REFUSAL_EXIT_CODE)
        }
        LocateResult::LoadError(message) => {
            eprintln!("{message}");
            Outcome::Exited(1)
        }
    }
}

/// `ren info` - the project's configuration as JSON.
pub fn info(reference: &str, engine_root: &Path) -> Outcome {
    run_project_command(reference, engine_root, |config| {
        let dict = config_to_dict(config);
        println!("{}", dict.to_python_json());
        Ok(Outcome::Exited(0))
    })
}

/// `ren status` - the project's on-disk status.
pub fn status(reference: &str, engine_root: &Path) -> Outcome {
    run_project_command(reference, engine_root, |config| {
        let info = project_status(config);
        print_status(&info);
        Ok(Outcome::Exited(0))
    })
}

fn print_status(info: &Json) {
    let get = |key: &str| info.get(key).cloned().unwrap_or(Json::Null);
    println!();
    println!("  Project: {}", project::py_display_pub(&get("name")));
    println!("  Slug:    {}", project::py_display_pub(&get("slug")));
    println!("  Status:  {}", project::py_display_pub(&get("status")));
    let client = get("client");
    let client_text = project::py_display_pub(&client);
    println!("  Client:  {}", if client_text.is_empty() { "(none)".to_string() } else { client_text });
    println!("  Path:    {}", project::py_display_pub(&get("project_root")));
    println!();
    println!("  Raw footage:     {} files", get("raw_footage_count").to_python_json());
    let pipeline_data = if matches!(get("pipeline_data_exists"), Json::Bool(true)) { "yes" } else { "no" };
    println!("  Pipeline data:   {pipeline_data}");
    let steps = get("steps_completed");
    let steps_text = steps.as_array().map(|a| a.len()).unwrap_or(0);
    println!("  Steps completed: {steps_text}");
    if let Some(list) = steps.as_array() {
        for step in list {
            println!("    ✓ {}", step.as_str().unwrap_or(""));
        }
    }
    let exports = get("exports");
    let exports_text = exports.as_array().map(|a| a.len()).unwrap_or(0);
    println!("  Exports:         {exports_text} files");
    println!();
    println!("  Resolve project: {}", project::py_display_pub(&get("resolve_project")));
    let folder = get("resolve_folder");
    let folder_text = project::py_display_pub(&folder);
    println!("  Resolve folder:  {}", if folder_text.is_empty() { "(root)".to_string() } else { folder_text });
}

/// `ren check` - the project's readiness check.
pub fn check(reference: &str, engine_root: &Path) -> Outcome {
    run_project_command(reference, engine_root, |config| {
    let project_dir = config.project_root.clone();
    println!("Checking project: {} ({})", project::py_display_pub(&config.name), project_dir.display());

    println!("Checking brand template...");
    let template = match resolve_template_reference(&config.pipeline.brand_template, &project_dir) {
        Ok(template) => template,
        Err(message) => return Err(refusal(&message, "the check cannot verify a template it cannot read", &format!("fix pipeline.brand_template in project.yaml (or clear it for no template), then re-run `ren check {reference}`"))),
    };

    println!("Checking creative_brief...");
    if !config.pipeline.creative_brief.is_empty() {
        let cb_path = project_dir.join(&config.pipeline.creative_brief);
        if !cb_path.exists() {
            return Err(refusal(
                &format!("declared creative_brief path does not exist: {cb_path:?}"),
                "the check cannot verify a brief it cannot read",
                "fix pipeline.creative_brief in project.yaml (or clear it), then re-run `ren check`",
            ));
        }
    }

    println!("Checking environment paths...");
    let sfx = sfx_library(engine_root);
    if !sfx.exists() {
        return Err(refusal(
            &format!("PIPELINE_SFX_LIBRARY does not exist: {sfx:?}"),
            "the check cannot verify a library that is not there",
            "set PIPELINE_SFX_LIBRARY (`ren config --init`, then edit it), then re-run `ren check`",
        ));
    }
    let music = music_library(engine_root);
    if !music.exists() {
        return Err(refusal(
            &format!("PIPELINE_MUSIC_LIBRARY does not exist: {music:?}"),
            "the check cannot verify a library that is not there",
            "set PIPELINE_MUSIC_LIBRARY (`ren config --init`, then edit it), then re-run `ren check`",
        ));
    }
    let projects_root = project::projects_root(engine_root);
    if !projects_root.exists() {
        return Err(refusal(
            &format!("PIPELINE_PROJECTS_ROOT does not exist: {projects_root:?}"),
            "the check cannot verify a projects root that is not there",
            "create it (`ren init`), then re-run `ren check`",
        ));
    }

    println!("Checking footage files with ffprobe...");
    let footage = match enumerate_footage(&project_dir) {
        Ok(files) => files,
        Err(message) => return Err(refusal(&message, "the check cannot verify footage that is not there", &format!("copy raw footage to the project's raw footage dir, then re-run `ren check {reference}`"))),
    };
    if footage.is_empty() {
        return Err(refusal(
            "no footage files found in project",
            "the check cannot verify footage that is not there",
            &format!("copy raw footage to the project's raw footage dir, then re-run `ren check {reference}`"),
        ));
    }

    let mut fps_counts: Vec<(String, i64)> = vec![];
    let mut res_counts: Vec<(String, i64)> = vec![];
    for file in &footage {
        let path = PathBuf::from(&file.path);
        match probe_metadata(&path) {
            Ok(metadata) => {
                let missing = [
                    ("width", metadata.width.is_none()),
                    ("height", metadata.height.is_none()),
                    ("frame_rate", metadata.frame_rate.is_none()),
                ];
                for (field, is_missing) in missing {
                    if is_missing {
                        return Err(refusal(
                            &format!(
                                "missing required field '{field}' in file '{}'",
                                file.filename
                            ),
                            "the pipeline cannot cut a file it cannot measure",
                            &format!(
                                "re-encode the file so ffprobe reports every field, then re-run `ren check {reference}`"
                            ),
                        ));
                    }
                }
                let fps = metadata.frame_rate.map(crate::json::format_float).unwrap_or_default();
                if !fps.is_empty() {
                    increment(&mut fps_counts, &fps);
                }
                let res = format!("{}x{}", metadata.width.unwrap_or(0), metadata.height.unwrap_or(0));
                increment(&mut res_counts, &res);
            }
            Err(message) => {
                return Err(refusal(
                    &format!("ffprobe failed to read {}: {message}", file.path),
                    "the check cannot verify a file ffprobe cannot read",
                    &format!("fix or replace the file, then re-run `ren check {reference}`"),
                ));
            }
        }
    }

    println!("Checking project assets (bookends, fonts, timed text)...");
    let content = template.get("content").cloned().unwrap_or(Json::Object(vec![]));
    if let Err(message) = check_bookends(&content, &project_dir) {
        eprintln!("ren: refused - {message}");
        return Ok(Outcome::Exited(REFUSAL_EXIT_CODE));
    }
    let effect = template.get("effect").cloned().unwrap_or(Json::Object(vec![]));
    if let Some(overlay) = effect.get("timed_text_overlay") {
        if let Some(Json::Str(font_file)) = overlay.get("font_file") {
            let name = font_file.trim().strip_prefix('/').unwrap_or(font_file);
            if !Path::new(font_file).is_absolute() {
                let candidate = project_dir.join("brand_assets").join(Path::new(name).file_name().map(|f| f.to_string_lossy().to_string()).unwrap_or_default());
                if let Err(message) = resolve_project_asset(&candidate, &project_dir) {
                    eprintln!("ren: refused - {message}");
                    return Ok(Outcome::Exited(REFUSAL_EXIT_CODE));
                }
            } else if !Path::new(font_file).exists() {
                eprintln!("ren: refused - project asset '{font_file}' not found (checked absolute path)");
                return Ok(Outcome::Exited(REFUSAL_EXIT_CODE));
            }
        }
    }

    if fps_counts.len() > 1 {
        eprintln!("REPORT: Mixed frame rates detected: {}", python_dict(&fps_counts));
    }
    if res_counts.len() > 1 {
        eprintln!("REPORT: Mixed resolutions detected: {}", python_dict(&res_counts));
    }

    println!("Checking project folder for standalone scripts...");
    for script in find_standalone_scripts(&project_dir) {
        let relpath = pathdiff(Path::new(&script), &project_dir);
        eprintln!("REPORT: Standalone Python script in project folder: {relpath}");
    }

    println!();
    println!("Readiness check PASS.");
    Ok(Outcome::Exited(0))
    })
}

fn increment(counts: &mut Vec<(String, i64)>, key: &str) {
    for entry in counts.iter_mut() {
        if entry.0 == key {
            entry.1 += 1;
            return;
        }
    }
    counts.push((key.to_string(), 1));
}

/// A Python dict repr with insertion order: `{30.0: 1, 24.0: 1}`.
fn python_dict(counts: &[(String, i64)]) -> String {
    let entries: Vec<String> = counts
        .iter()
        .map(|(key, value)| format!("{key}: {value}"))
        .collect();
    format!("{{{}}}", entries.join(", "))
}

fn refusal(what: &str, why: &str, fix: &str) -> String {
    format!("ren: refused - {what}\n  why: {why}\n  fix: {fix}")
}

/// `library.tools.brand_registry.resolve_template_reference`.
fn resolve_template_reference(reference: &str, project_folder: &Path) -> Result<Json, String> {
    let reference = reference.trim();
    if !reference.is_empty() && looks_like_path(reference) {
        let path = PathBuf::from(reference);
        if !path.exists() {
            return Err(format!("Brand template path {reference:?} does not exist."));
        }
        return load_brand_template(&path);
    }
    let brand_json = project_folder.join("brand.json");
    if brand_json.exists() {
        return load_brand_template(&brand_json);
    }
    if reference.is_empty() {
        return Ok(no_brand_template());
    }
    let templates_dir = engine_root_templates();
    for ext in [".yaml", ".yml", ".json"] {
        let candidate = templates_dir.join(format!("{reference}{ext}"));
        if candidate.exists() {
            return load_brand_template(&candidate);
        }
    }
    let available: Vec<String> = if templates_dir.is_dir() {
        fs::read_dir(&templates_dir)
            .map(|entries| {
                let mut names: Vec<String> = entries
                    .flatten()
                    .filter_map(|entry| {
                        let path = entry.path();
                        path.file_stem().map(|s| s.to_string_lossy().to_string())
                    })
                    .collect();
                names.sort();
                names
            })
            .unwrap_or_default()
    } else {
        vec![]
    };
    Err(format!(
        "Brand template {reference:?} not found in {templates_dir:?}. Available: {available:?}. \
         The product ships no templates - carry the brand in the project's own brand.json."
    ))
}

fn engine_root_templates() -> PathBuf {
    engine::find_engine_root()
        .unwrap_or_else(|| PathBuf::from("."))
        .join("templates")
}

fn looks_like_path(reference: &str) -> bool {
    let lower = reference.to_lowercase();
    reference.contains('/')
        || lower.ends_with(".yaml")
        || lower.ends_with(".yml")
        || lower.ends_with(".json")
}

fn no_brand_template() -> Json {
    Json::Object(vec![
        ("series_id".to_string(), Json::str("")),
        ("delivery_format".to_string(), Json::str("")),
        ("style".to_string(), Json::Object(vec![])),
        ("effect".to_string(), Json::Object(vec![])),
        ("content".to_string(), Json::Object(vec![])),
    ])
}

/// `load_brand_template` - parse a template file into the slots `check`
/// reads, refusing an unknown slot key the way `Slots(**data)` does.
fn load_brand_template(path: &Path) -> Result<Json, String> {
    let text = fs::read_to_string(path)
        .map_err(|e| format!("cannot read {}: {e}", path.display()))?;
    let is_yaml = path
        .extension()
        .map(|e| {
            let ext = e.to_string_lossy().to_lowercase();
            ext == "yaml" || ext == "yml"
        })
        .unwrap_or(false);
    let data: Json = if is_yaml {
        serde_yaml::from_str::<serde_yaml::Value>(&text)
            .map_err(|e| format!("cannot parse {}: {e}", path.display()))
            .map(|yaml| yaml_to_json(&yaml))?
    } else {
        serde_json::from_str::<serde_json::Value>(&text)
            .map_err(|e| format!("cannot parse {}: {e}", path.display()))
            .map(|value| serde_json_to_json(&value))?
    };
    let style = data.get("style").cloned().unwrap_or(Json::Object(vec![]));
    let mut style_data = match style {
        Json::Object(pairs) => pairs,
        _ => vec![],
    };
    if let Some(legacy) = style_data.iter().find(|(k, _)| k == "house_look").map(|(_, v)| v.clone()) {
        if !style_data.iter().any(|(k, _)| k == "series_look") {
            style_data.push(("series_look".to_string(), legacy));
        }
    }
    let style_keys = ["color_palette", "series_look", "reference_look_image", "typography", "energy_profile", "framing_intent", "tv_frame"];
    validate_slots("StyleSlots", &style_data, &style_keys)?;
    let effect = data.get("effect").cloned().unwrap_or(Json::Object(vec![]));
    let effect_pairs = match effect {
        Json::Object(pairs) => pairs,
        _ => vec![],
    };
    let effect_keys = ["transition_types", "transition_duration_ms", "vfx_intensity", "subtitle_style", "sfx_density", "caption_case", "caption_words_per_card", "motion_accents", "motion_progress_bar", "timed_text_overlay"];
    validate_slots("EffectSlots", &effect_pairs, &effect_keys)?;
    let content = data.get("content").cloned().unwrap_or(Json::Object(vec![]));
    let content_pairs = match content {
        Json::Object(pairs) => pairs,
        _ => vec![],
    };
    let content_keys = ["series_title", "channel_name", "bookends", "closing_lockup", "watermark", "music_genre", "target_duration_seconds"];
    validate_slots("ContentSlots", &content_pairs, &content_keys)?;
    let delivery_format = data.get("delivery_format").cloned().unwrap_or(Json::Null);
    let delivery_format = if truthy(&delivery_format) { delivery_format } else { Json::str("") };
    Ok(Json::Object(vec![
        ("series_id".to_string(), data.get("series_id").cloned().unwrap_or(Json::str("default"))),
        ("delivery_format".to_string(), delivery_format),
        ("style".to_string(), Json::Object(style_data)),
        ("effect".to_string(), Json::Object(effect_pairs)),
        ("content".to_string(), Json::Object(content_pairs)),
    ]))
}

fn validate_slots(class: &str, pairs: &[(String, Json)], known: &[&str]) -> Result<(), String> {
    for (key, _) in pairs {
        if !known.contains(&key.as_str()) {
            return Err(format!(
                "{class}.__init__() got an unexpected keyword argument '{key}'"
            ));
        }
    }
    Ok(())
}

fn yaml_to_json(value: &serde_yaml::Value) -> Json {
    project::yaml_to_json_pub(value)
}

fn serde_json_to_json(value: &serde_json::Value) -> Json {
    match value {
        serde_json::Value::Null => Json::Null,
        serde_json::Value::Bool(b) => Json::Bool(*b),
        serde_json::Value::Number(n) => {
            if let Some(u) = n.as_u64() {
                Json::Int(u as i64)
            } else if let Some(i) = n.as_i64() {
                Json::Int(i)
            } else if let Some(f) = n.as_f64() {
                Json::Float(f)
            } else {
                Json::Null
            }
        }
        serde_json::Value::String(s) => Json::Str(s.clone()),
        serde_json::Value::Array(items) => {
            Json::Array(items.iter().map(serde_json_to_json).collect())
        }
        serde_json::Value::Object(map) => Json::Object(
            map.iter()
                .map(|(key, value)| (key.clone(), serde_json_to_json(value)))
                .collect(),
        ),
    }
}

fn truthy(value: &Json) -> bool {
    project::truthy_pub(value)
}

/// `bookends.declared_bookends` + `resolve_bookend` over a template's content.
fn check_bookends(content: &Json, project_dir: &Path) -> Result<(), String> {
    let declarations = match content.get("bookends") {
        Some(Json::Object(map)) => {
            let mut slots: Vec<(&String, &Json)> =
                map.iter().map(|(key, value)| (key, value)).collect();
            slots.sort_by_key(|(slot, _)| slot_order(slot));
            slots
        }
        Some(other) => {
            return Err(format!(
                "content.bookends must be a mapping of slot -> declaration, got {}",
                project::type_name_pub(other)
            ));
        }
        None => vec![],
    };
    for (slot, raw) in declarations {
        if !truthy(raw) {
            continue;
        }
        let declaration = match raw {
            Json::Object(pairs) => pairs.clone(),
            other => {
                return Err(format!(
                    "bookend '{slot}' must be a mapping, got {}",
                    project::type_name_pub(other)
                ));
            }
        };
        let get = |key: &str| declaration.iter().find(|(k, _)| k == key).map(|(_, v)| v.clone());
        let composition = get("composition");
        let asset = get("asset");
        let source = get("source");
        let composition_truthy = composition.as_ref().map(truthy).unwrap_or(false);
        let asset_truthy = asset.as_ref().map(truthy).unwrap_or(false);
        if composition_truthy == asset_truthy {
            return Err(format!(
                "bookend '{slot}' must name exactly one of 'composition' (a Remotion composition to render) or 'asset' (a clip that already exists); got composition={composition:?}, asset={asset:?}"
            ));
        }
        if asset_truthy && source.as_ref().map(truthy).unwrap_or(false) {
            return Err(format!(
                "bookend '{slot}' names both 'asset' and 'source'; an asset is already rendered, so there is nothing to build it from"
            ));
        }
        if composition_truthy
            && !source.as_ref().map(truthy).unwrap_or(false)
            && !ENGINE_BOOKEND_COMPOSITIONS.contains(
                &composition.as_ref().and_then(|v| v.as_str()).unwrap_or(""),
            )
        {
            return Err(format!(
                "bookend '{slot}' names composition {composition:?}, which the engine does not register. Either point 'source' at the project's own .tsx file, or name one of {ENGINE_BOOKEND_COMPOSITIONS:?}"
            ));
        }
        let duration = get("duration_seconds");
        let duration_number = match duration {
            Some(Json::Int(n)) => n as f64,
            Some(Json::Float(f)) => f,
            _ => {
                return Err(format!(
                    "bookend '{slot}' has no numeric duration_seconds; the spine needs it to place the block, and probing the file would make the plan depend on a render that has not happened yet"
                ));
            }
        };
        if duration_number <= 0.0 {
            return Err(format!(
                "bookend '{slot}' has duration_seconds={duration_number}, which puts nothing on screen"
            ));
        }
        if duration_number > 30.0 {
            return Err(format!(
                "bookend '{slot}' runs {duration_number}s, longer than the 30.0s a card may hold; a longer beat is content and belongs in the spine"
            ));
        }
        let mode = if asset_truthy { "asset" } else { "composition" };
        if mode == "asset" {
            let asset_path = asset.as_ref().and_then(|v| v.as_str()).unwrap_or("");
            resolve_project_asset_str(asset_path, project_dir)?;
        } else if source.as_ref().map(truthy).unwrap_or(false) {
            let source_path = source.as_ref().and_then(|v| v.as_str()).unwrap_or("");
            resolve_project_asset_str(source_path, project_dir)?;
        }
    }
    Ok(())
}

fn slot_order(slot: &str) -> u8 {
    match slot {
        "intro" => 0,
        "outro" => 1,
        "end_card" => 2,
        _ => 3,
    }
}

const ENGINE_BOOKEND_COMPOSITIONS: &[&str] = &["TimedTextOverlay"];

/// `project_asset.resolve_project_asset`.
fn resolve_project_asset(path: &Path, project_folder: &Path) -> Result<String, String> {
    resolve_project_asset_str(&path.to_string_lossy(), project_folder)
}

fn resolve_project_asset_str(declared_path: &str, project_folder: &Path) -> Result<String, String> {
    let base = if Path::new(declared_path).is_absolute() {
        PathBuf::from(declared_path)
    } else {
        project_folder.join(declared_path)
    };
    let base = normalize(&base);
    if base.is_file() {
        return Ok(base.to_string_lossy().to_string());
    }
    let stem = base.file_stem().map(|s| s.to_string_lossy().to_string()).unwrap_or_default();
    let ext = base.extension().map(|s| s.to_string_lossy().to_string()).unwrap_or_default();
    let index = base.with_file_name(format!("{stem}/index{ext}"));
    // Python: root, ext = splitext(base_path); if isdir(root): index = root/index{ext}
    let root = base.parent().unwrap_or(&base).to_path_buf();
    if root.is_dir() {
        let index_path = root.join(format!("index{ext}"));
        if index_path.is_file() {
            return Ok(index_path.to_string_lossy().to_string());
        }
    }
    let _ = index;
    Err(format!(
        "project asset '{declared_path}' not found (checked flat file and {}/index{})",
        Path::new(&stem).file_name().map(|f| f.to_string_lossy().to_string()).unwrap_or_default(),
        ext
    ))
}

fn normalize(path: &Path) -> PathBuf {
    let mut out = PathBuf::new();
    for component in path.components() {
        use std::path::Component;
        match component {
            Component::ParentDir => {
                out.pop();
            }
            Component::CurDir => {}
            other => out.push(other.as_os_str()),
        }
    }
    out
}

/// `library.tools.footage_identity.enumerate_footage` - every video file
/// under the project's footage root.
fn enumerate_footage(project_dir: &Path) -> Result<Vec<FootageFile>, String> {
    let config_path = project_dir.join("project.yaml");
    let footage_root = if config_path.is_file() {
        if let Ok(text) = fs::read_to_string(&config_path) {
            if let Ok(yaml) = serde_yaml::from_str::<serde_yaml::Value>(&text) {
                let data = yaml_to_json(&yaml);
                let declared = data
                    .get("source")
                    .and_then(|s| s.get("footage_root"))
                    .and_then(|v| v.as_str())
                    .unwrap_or("")
                    .trim()
                    .to_string();
                if !declared.is_empty() {
                    let expanded = project::expand_portable_pub(&declared, project_dir, &engine::find_engine_root().unwrap_or_else(|| PathBuf::from(".")));
                    let path = PathBuf::from(&expanded);
                    if path.is_absolute() {
                        path
                    } else {
                        project_dir.join(path)
                    }
                } else {
                    project_dir.join("raw")
                }
            } else {
                project_dir.join("raw")
            }
        } else {
            project_dir.join("raw")
        }
    } else {
        project_dir.join("raw")
    };
    if !footage_root.is_dir() {
        return Err(format!(
            "Missing 'raw' subdirectory in project folder: {}",
            project_dir.display()
        ));
    }
    let mut files = vec![];
    for (dirpath, names) in walk(&footage_root) {
        let mut sorted = names;
        sorted.sort();
        for name in sorted {
            let filepath = dirpath.join(&name);
            let ext = Path::new(&name)
                .extension()
                .map(|e| format!(".{}", e.to_string_lossy().to_lowercase()))
                .unwrap_or_default();
            if !SUPPORTED_VIDEO_EXTENSIONS.contains(&ext.as_str()) {
                continue;
            }
            if !filepath.is_file() {
                continue;
            }
            let size = match fs::metadata(&filepath) {
                Ok(metadata) => metadata.len(),
                Err(_) => continue,
            };
            if size == 0 {
                continue;
            }
            files.push(FootageFile {
                path: filepath.to_string_lossy().to_string(),
                filename: name,
            });
        }
    }
    files.sort_by(|a, b| a.path.cmp(&b.path));
    Ok(files)
}

struct FootageFile {
    path: String,
    filename: String,
}

const SUPPORTED_VIDEO_EXTENSIONS: &[&str] =
    &[".mp4", ".mov", ".avi", ".mkv", ".mts", ".m4v", ".webm", ".mxf"];

fn walk(root: &Path) -> Vec<(PathBuf, Vec<String>)> {
    let mut out = vec![];
    fn walk_dir(dir: &Path, out: &mut Vec<(PathBuf, Vec<String>)>) {
        let entries = match fs::read_dir(dir) {
            Ok(entries) => entries,
            Err(_) => return,
        };
        let mut names = vec![];
        for entry in entries.flatten() {
            let name = entry.file_name().to_string_lossy().to_string();
            if entry.path().is_dir() {
                walk_dir(&entry.path(), out);
            } else {
                names.push(name);
            }
        }
        out.push((dir.to_path_buf(), names));
    }
    walk_dir(root, &mut out);
    out
}

struct Metadata {
    frame_rate: Option<f64>,
    width: Option<i64>,
    height: Option<i64>,
}

/// `step_1_02_catalog_footage.extract_metadata` - the four fields `check`
/// reads, and the error paths that make it refuse.
fn probe_metadata(filepath: &Path) -> Result<Metadata, String> {
    let output = ffprobe(filepath)?;
    let parsed: serde_json::Value = serde_json::from_str(&output)
        .map_err(|_| "ffprobe returned invalid JSON".to_string())?;
    let probe = parsed;
    let mut video_stream: Option<&serde_json::Value> = None;
    let mut has_audio = false;
    if let Some(streams) = probe.get("streams").and_then(|s| s.as_array()) {
        for stream in streams {
            match stream.get("codec_type").and_then(|t| t.as_str()) {
                Some("video") => {
                    if video_stream.is_none() {
                        video_stream = Some(stream);
                    }
                }
                Some("audio") => has_audio = true,
                _ => {}
            }
        }
    }
    let video_stream = match video_stream {
        Some(stream) => stream,
        None => {
            if has_audio {
                return Ok(Metadata {
                    frame_rate: None,
                    width: None,
                    height: None,
                });
            }
            return Err("No video stream found".to_string());
        }
    };
    let frame_rate = match video_stream.get("r_frame_rate").and_then(|r| r.as_str()) {
        Some(fps_str) if !fps_str.is_empty() => {
            if let Some((num, den)) = fps_str.split_once('/') {
                let num: f64 = num.trim().parse().unwrap_or(0.0);
                let den: f64 = den.trim().parse().unwrap_or(0.0);
                if den > 0.0 {
                    Some(round3(num / den))
                } else {
                    None
                }
            } else {
                fps_str.parse::<f64>().ok().map(round3)
            }
        }
        _ => None,
    };
    let width = video_stream.get("width").and_then(|w| w.as_i64());
    let height = video_stream.get("height").and_then(|h| h.as_i64());
    Ok(Metadata {
        frame_rate,
        width,
        height,
    })
}

fn round3(value: f64) -> f64 {
    (value * 1000.0).round() / 1000.0
}

/// Run ffprobe the way `extract_metadata` does, with its 30s timeout. The
/// returned string is ffprobe's stdout; the Err is the `error` value the
/// metadata dict would carry.
fn ffprobe(filepath: &Path) -> Result<String, String> {
    let mut command = ProcCommand::new("ffprobe");
    command
        .arg("-v")
        .arg("quiet")
        .arg("-print_format")
        .arg("json")
        .arg("-show_format")
        .arg("-show_streams")
        .arg(filepath)
        .stdout(std::process::Stdio::piped())
        .stderr(std::process::Stdio::piped());
    let start = Instant::now();
    let mut child = command
        .spawn()
        .map_err(|_| "ffprobe not found. Install ffmpeg: brew install ffmpeg".to_string())?;
    loop {
        match child.try_wait() {
            Ok(Some(status)) => {
                let output = child.wait_with_output().map_err(|e| format!("{e}"))?;
                if !status.success() {
                    let stderr = String::from_utf8_lossy(&output.stderr);
                    let message = format!("ffprobe failed (exit {}): {}", status.code().unwrap_or(-1), &stderr[..stderr.len().min(200)]);
                    eprintln!("WARNING: {message} for {}", filepath.display());
                    return Err(message);
                }
                return Ok(String::from_utf8_lossy(&output.stdout).to_string());
            }
            Ok(None) => {
                if start.elapsed() > Duration::from_secs(30) {
                    let _ = child.kill();
                    let message = "ffprobe timed out after 30s".to_string();
                    eprintln!("WARNING: {message} for {}", filepath.display());
                    return Err(message);
                }
                std::thread::sleep(Duration::from_millis(10));
            }
            Err(e) => return Err(format!("{e}")),
        }
    }
}

/// `library.tools.project_scripts.find_standalone_scripts`.
fn find_standalone_scripts(project_folder: &Path) -> Vec<String> {
    let root = fs::canonicalize(project_folder).unwrap_or_else(|_| project_folder.to_path_buf());
    if !root.is_dir() {
        return vec![];
    }
    let excluded = root.join("pipeline_output");
    let excluded = fs::canonicalize(&excluded).unwrap_or(excluded);
    let mut found = vec![];
    walk_scripts(&root, &excluded, &mut found);
    found.sort();
    found
}

fn walk_scripts(dir: &Path, excluded: &Path, found: &mut Vec<String>) {
    let entries = match fs::read_dir(dir) {
        Ok(entries) => entries,
        Err(_) => return,
    };
    let mut sorted: Vec<_> = entries.flatten().collect();
    sorted.sort_by_key(|entry| entry.file_name());
    for entry in sorted {
        let path = entry.path();
        let real = fs::canonicalize(&path).unwrap_or(path.clone());
        if real == excluded {
            continue;
        }
        if path.is_dir() {
            walk_scripts(&path, excluded, found);
        } else if path
            .extension()
            .map(|e| e.to_string_lossy().to_lowercase() == "py")
            .unwrap_or(false)
        {
            found.push(real.to_string_lossy().to_string());
        }
    }
}

fn pathdiff(path: &Path, base: &Path) -> String {
    let path = fs::canonicalize(path).unwrap_or_else(|_| path.to_path_buf());
    let base = fs::canonicalize(base).unwrap_or_else(|_| base.to_path_buf());
    let path: Vec<_> = path.components().collect();
    let base: Vec<_> = base.components().collect();
    let mut index = 0;
    while index < path.len() && index < base.len() && path[index] == base[index] {
        index += 1;
    }
    let mut result = String::new();
    for _ in index..base.len() {
        result.push_str("../");
    }
    for component in &path[index..] {
        result.push_str(&component.as_os_str().to_string_lossy());
        result.push('/');
    }
    if result.ends_with('/') && result.len() > 1 {
        result.pop();
    }
    if result.is_empty() {
        result.push('.');
    }
    result
}

/// `library.tools.paths.SFX_LIBRARY`.
fn sfx_library(engine_root: &Path) -> PathBuf {
    let default = shared_assets(engine_root).join("sfx library");
    let value = crate::config::get("PIPELINE_SFX_LIBRARY", &default.to_string_lossy(), engine_root);
    PathBuf::from(value)
}

/// `library.tools.paths.MUSIC_LIBRARY`.
fn music_library(engine_root: &Path) -> PathBuf {
    let default = shared_assets(engine_root).join("music");
    let value = crate::config::get("PIPELINE_MUSIC_LIBRARY", &default.to_string_lossy(), engine_root);
    PathBuf::from(value)
}

fn shared_assets(engine_root: &Path) -> PathBuf {
    let default = engine::home_dir().join("Movies").join("Ren").join("assets");
    let value = crate::config::get("PIPELINE_SHARED_ASSETS", &default.to_string_lossy(), engine_root);
    PathBuf::from(value)
}
