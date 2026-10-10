//! Strict adapter for the pinned FilmCraft headless CLI.
//!
//! Ren owns the request and receipt types here. FilmCraft project files remain an engine
//! projection and are checked against the exact format revision this driver qualifies.

pub mod adapter;

use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use std::{
    collections::BTreeSet,
    path::{Path, PathBuf},
    process::Command,
    sync::OnceLock,
};
use thiserror::Error;

pub const FILMCRAFT_REPOSITORY: &str = "https://github.com/storytold/filmcraft.git";
/// Mirrored by library/tools/upstream_engines.json; the upstream intake refuses
/// promotion when the manifest drifts from this constant. Keep this as the sole pin source.
pub const FILMCRAFT_REVISION: &str = "98ba9acbcedcbb630ba2a07b782176b66835edd2";
pub const FILMCRAFT_CLI_VERSION: &str = "0.5.0";
pub const FILMCRAFT_PROJECT_FORMAT: &str = "filmcraft.project";
pub const FILMCRAFT_PROJECT_SCHEMA: u32 = 14;
pub const ADAPTER_SCHEMA: u32 = 1;

/// Checked-in transition authority: verbatim `filmcraft-cli exec effects.list
/// '{"kind":"VideoTransition"}'` output measured from the pinned revision.
/// `plan_ren_edit` is pure (no I/O, no CLI), so plan-time validation reads
/// this file compiled in rather than asking the engine; `tests/pinned_cli.rs`
/// asserts the live CLI still reports exactly this id set, so a pin move
/// re-measures the file instead of silently trusting it.
const VIDEO_TRANSITIONS_JSON: &str = include_str!("video_transitions.json");

/// The engine's own spelling rule (measured against the pinned CLI):
/// surrounding whitespace is ignored, case is ignored, and one space equals
/// one underscore - `Cross Dissolve`, `cross_dissolve` and `CROSS_DISSOLVE`
/// all apply, while `cross-dissolve`, `cross__dissolve` and `Cross  Dissolve`
/// all refuse. Both the transition id and its display name are accepted.
pub fn normalize_transition_effect(raw: &str) -> String {
    raw.trim().to_lowercase().replace(' ', "_")
}

fn transition_effect_names() -> &'static BTreeSet<String> {
    static NAMES: OnceLock<BTreeSet<String>> = OnceLock::new();
    NAMES.get_or_init(|| {
        let effects: Vec<Value> = serde_json::from_str(VIDEO_TRANSITIONS_JSON)
            .expect("checked-in transition authority parses as a JSON array");
        let mut names = BTreeSet::new();
        for effect in &effects {
            if let Some(id) = effect.get("id").and_then(Value::as_str) {
                names.insert(normalize_transition_effect(id));
            }
            if let Some(name) = effect.get("name").and_then(Value::as_str) {
                names.insert(normalize_transition_effect(name));
            }
        }
        assert!(
            !names.is_empty(),
            "checked-in transition authority carries no names"
        );
        names
    })
}

/// True when the pinned engine would accept `name` as a
/// `sequence.applyVideoTransition` effect (id or display name, engine
/// spelling rule). Used by the adapter so model edits refuse unknown names
/// with a named code before any mutation.
pub fn transition_effect_known(name: &str) -> bool {
    transition_effect_names().contains(&normalize_transition_effect(name))
}

/// Refuse an unknown transition name before any mutation. `None` (the engine
/// default transition) stays valid; anything else must normalize to an id or
/// display name in the checked-in authority.
pub fn validate_transition_effect(effect: Option<&str>) -> Result<()> {
    match effect {
        None => Ok(()),
        Some(name) if transition_effect_known(name) => Ok(()),
        Some(name) => Err(Error::Schema(format!(
            "unknown FilmCraft transition effect `{name}`; it is not among the {} known names in the pinned authority (`src/video_transitions.json`, measured from revision {FILMCRAFT_REVISION}); omit `effect` for the engine default transition",
            transition_effect_names().len()
        ))),
    }
}

#[derive(Debug, Error)]
pub enum Error {
    #[error("FilmCraft CLI could not be located or built: {0}")]
    Cli(String),
    #[error("FilmCraft command {command} failed: {detail}")]
    Command { command: String, detail: String },
    #[error("FilmCraft returned an unrecognized {command} response: {detail}")]
    UnknownResponse { command: String, detail: String },
    #[error("unsupported FilmCraft project format/schema: {0}")]
    Schema(String),
    #[error("FilmCraft edit refused to hide known loss: {0}")]
    Loss(String),
    #[error(transparent)]
    Io(#[from] std::io::Error),
    #[error(transparent)]
    Json(#[from] serde_json::Error),
}

pub type Result<T> = std::result::Result<T, Error>;

#[derive(Clone, Debug, Serialize, Deserialize, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct Request<T> {
    pub adapter_schema: u32,
    pub operation: T,
}

#[derive(Clone, Debug, Serialize, Deserialize, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct Receipt {
    pub adapter_schema: u32,
    pub engine_revision: String,
    pub project_schema: u32,
    pub operation: String,
    pub engine_response: Value,
    pub losses: Vec<LossReport>,
}

#[derive(Clone, Debug, Serialize, Deserialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub struct LossReport {
    pub code: String,
    pub detail: String,
}

#[derive(Clone, Debug, Serialize, Deserialize, PartialEq)]
#[serde(tag = "kind", rename_all = "snake_case", deny_unknown_fields)]
pub enum Operation {
    CreateProject {
        path: PathBuf,
        name: String,
    },
    ImportMedia {
        project: PathBuf,
        paths: Vec<PathBuf>,
    },
    Place {
        project: PathBuf,
        item: u64,
        track: String,
        audio_track: Option<String>,
        frame: i64,
        source_in_ticks: i64,
        duration_ticks: i64,
    },
    Razor {
        project: PathBuf,
        track: String,
        frame: i64,
    },
    /// Lift: remove clips, leaving a gap. The engine follows linked audio,
    /// so one id per clip use suffices. Answers `null`.
    Delete {
        project: PathBuf,
        clips: Vec<u64>,
    },
    /// Extract: remove clips and close the gap by shifting followers.
    /// Answers `null`.
    RippleDelete {
        project: PathBuf,
        clips: Vec<u64>,
    },
    /// Constant-speed retime. `speed_percent` is Ren percent (200 = double
    /// speed); `ripple` shifts followers exactly as ren-core's ripple does.
    /// Answers `null`. Reverse is never sent: it refuses at planning time.
    Retime {
        project: PathBuf,
        clips: Vec<u64>,
        speed_percent: f64,
        ripple: bool,
    },
    Trim {
        project: PathBuf,
        clip: u64,
        edge: TrimEdge,
        mode: TrimMode,
        delta_frames: i64,
    },
    Move {
        project: PathBuf,
        clip: u64,
        track: String,
        timeline_ticks: i64,
        insert: bool,
    },
    Transition {
        project: PathBuf,
        clip: u64,
        edge: TrimEdge,
        frames: u32,
        effect: Option<String>,
    },
    AudioGain {
        project: PathBuf,
        track: String,
        gain_db: f64,
    },
    Export {
        project: PathBuf,
        path: PathBuf,
        format: ExportFormat,
        hardware_encoder: HardwareEncoding,
    },
    RenderFrame {
        project: PathBuf,
        frame: i64,
        path: PathBuf,
    },
    Inspect {
        project: PathBuf,
    },
    OpenProject {
        path: PathBuf,
    },
}

#[derive(Clone, Copy, Debug, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum TrimEdge {
    In,
    Out,
}
#[derive(Clone, Copy, Debug, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum TrimMode {
    Regular,
    Ripple,
}
#[derive(Clone, Copy, Debug, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum ExportFormat {
    H264,
    ProRes,
    Wav,
}
#[derive(Clone, Copy, Debug, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "lowercase")]
pub enum HardwareEncoding {
    Off,
    Auto,
}

/// Resolve the CLI without a human setup step. The explicit path is useful for managed installs;
/// otherwise Cargo builds the binary into a revision-addressed per-user cache with no UI feature.
pub fn locate_or_build_cli() -> Result<PathBuf> {
    if let Some(path) = std::env::var_os("REN_FILMCRAFT_CLI") {
        let path = PathBuf::from(path);
        if path.is_file() {
            return Ok(path);
        }
        return Err(Error::Cli(format!(
            "REN_FILMCRAFT_CLI does not name a file: {}",
            path.display()
        )));
    }
    if let Some(path) = std::env::var_os("FILMCRAFT_CLI") {
        let path = PathBuf::from(path);
        if path.is_file() {
            return Ok(path);
        }
    }
    let home = std::env::var_os("HOME").ok_or_else(|| Error::Cli("HOME is unset".into()))?;
    let root = PathBuf::from(&home)
        .join(".cache/ren/filmcraft")
        .join(FILMCRAFT_REVISION);
    let binary = root.join("bin").join(if cfg!(windows) {
        "filmcraft-cli.exe"
    } else {
        "filmcraft-cli"
    });
    if binary.is_file() {
        return Ok(binary);
    }
    std::fs::create_dir_all(&root)?;
    let cargo = std::env::var_os("CARGO")
        .map(PathBuf::from)
        .unwrap_or_else(|| {
            let home_cargo = PathBuf::from(&home).join(".cargo/bin/cargo");
            if home_cargo.is_file() {
                home_cargo
            } else {
                PathBuf::from("cargo")
            }
        });
    let output = Command::new(cargo)
        .args([
            "install",
            "--git",
            FILMCRAFT_REPOSITORY,
            "--rev",
            FILMCRAFT_REVISION,
            "--root",
        ])
        .arg(&root)
        // Current Cargo resolves the git package as a positional crate, not
        // `--package`, and the install stays within the lane's job budget.
        .args(["--locked", "-j", "4", "filmcraft-cli"])
        .output()?;
    if !output.status.success() {
        return Err(Error::Cli(format!(
            "pinned cargo install failed: {}",
            String::from_utf8_lossy(&output.stderr)
        )));
    }
    if !binary.is_file() {
        return Err(Error::Cli(format!(
            "cargo install succeeded but {} is absent",
            binary.display()
        )));
    }
    Ok(binary)
}

pub struct FilmCraft {
    cli: PathBuf,
}

impl FilmCraft {
    pub fn new() -> Result<Self> {
        let cli = locate_or_build_cli()?;
        verify_cli_version(&cli)?;
        Ok(Self { cli })
    }
    pub fn with_cli(cli: impl Into<PathBuf>) -> Self {
        Self { cli: cli.into() }
    }

    pub fn execute(&self, request: Request<Operation>) -> Result<Receipt> {
        if request.adapter_schema != ADAPTER_SCHEMA {
            return Err(Error::Schema(format!(
                "adapter schema {} is not supported",
                request.adapter_schema
            )));
        }
        let (project, operation, command, params, saving) = plan(request.operation)?;
        if command != "file.newSequence" {
            if let Some(project) = project.as_deref() {
                check_project_schema(project)?;
            }
        }
        let before = if matches!(command, "timeline.razor" | "timeline.trim") {
            project
                .as_deref()
                .map(|p| run_command(&self.cli, Some(p), false, "sequence.inspect", json!({})))
                .transpose()?
        } else {
            None
        };
        let response = if let Some((new_path, name)) = project.as_ref().and_then(|p| {
            if command == "file.newSequence" {
                Some((p.clone(), params["name"].as_str()?.to_string()))
            } else {
                None
            }
        }) {
            let mut cli = Command::new(&self.cli);
            cli.args(["exec", "file.newSequence"])
                .arg(json!({"name":name}).to_string())
                .arg("--compact")
                .arg("--save-as")
                .arg(&new_path);
            parse_cli_output("file.newSequence", cli.output()?)?
        } else {
            run_command(
                &self.cli,
                project.as_deref(),
                saving,
                command,
                params.clone(),
            )?
        };
        if let Some(project) = project.as_deref() {
            check_project_schema(project)?;
        }
        if command == "file.import"
            && response["errors"]
                .as_array()
                .is_some_and(|items| !items.is_empty())
        {
            return Err(Error::Loss(format!(
                "FilmCraft import reported errors: {}",
                response["errors"]
            )));
        }
        let after = if before.is_some() {
            Some(run_command(
                &self.cli,
                project.as_deref(),
                false,
                "sequence.inspect",
                json!({}),
            )?)
        } else {
            None
        };
        let losses = match command {
            "timeline.razor" => detect_razor_loss(before.as_ref(), after.as_ref())?,
            "timeline.trim" => detect_trim_loss(before.as_ref(), after.as_ref(), &params)?,
            "timeline.move" => detect_move_loss(&response)?,
            _ => vec![],
        };
        Ok(Receipt {
            adapter_schema: ADAPTER_SCHEMA,
            engine_revision: FILMCRAFT_REVISION.into(),
            project_schema: FILMCRAFT_PROJECT_SCHEMA,
            operation,
            engine_response: response,
            losses,
        })
    }
}

fn plan(op: Operation) -> Result<(Option<PathBuf>, String, &'static str, Value, bool)> {
    let tuple = match op {
        Operation::CreateProject { path, name } => {
            if path.extension().and_then(|s| s.to_str()) != Some("fcproj") {
                return Err(Error::Schema(
                    "FilmCraft project paths must use the `.fcproj` extension".into(),
                ));
            }
            if path.exists() {
                return Err(Error::Schema(format!(
                    "refusing to replace existing project {}",
                    path.display()
                )));
            }
            if path.file_stem().and_then(|s| s.to_str()) != Some(name.as_str()) {
                return Err(Error::Schema("project name must match the `.fcproj` file stem because FilmCraft Save As assigns that name".into()));
            }
            (
                Some(path),
                "create_project",
                "file.newSequence",
                json!({"name": name}),
                true,
            )
        }
        Operation::ImportMedia { project, paths } => (
            Some(project),
            "import_media",
            "file.import",
            json!({"paths": paths}),
            true,
        ),
        Operation::Place {
            project,
            item,
            track,
            audio_track,
            frame,
            source_in_ticks,
            duration_ticks,
        } => {
            if source_in_ticks < 0 || duration_ticks <= 0 {
                return Err(Error::Schema(
                    "placement source range must start at or after zero and have positive duration"
                        .into(),
                ));
            }
            let mut p = json!({"item":item,"track":track,"frame":frame,"sourceIn":source_in_ticks,"duration":duration_ticks});
            if let Some(audio) = audio_track {
                p["audioTrack"] = json!(audio);
            }
            (Some(project), "place", "timeline.place", p, true)
        }
        Operation::Razor {
            project,
            track,
            frame,
        } => (
            Some(project),
            "razor",
            "timeline.razor",
            json!({"track":track,"frame":frame}),
            true,
        ),
        Operation::Delete { project, clips } => {
            if clips.is_empty() {
                return Err(Error::Schema("delete request names no clips".into()));
            }
            (
                Some(project),
                "delete",
                "edit.clear",
                json!({"clips":clips}),
                true,
            )
        }
        Operation::RippleDelete { project, clips } => {
            if clips.is_empty() {
                return Err(Error::Schema("ripple delete request names no clips".into()));
            }
            (
                Some(project),
                "ripple_delete",
                "edit.rippleDelete",
                json!({"clips":clips}),
                true,
            )
        }
        Operation::Retime {
            project,
            clips,
            speed_percent,
            ripple,
        } => {
            if clips.is_empty() {
                return Err(Error::Schema("retime request names no clips".into()));
            }
            if !speed_percent.is_finite() || speed_percent <= 0.0 {
                return Err(Error::Schema(
                    "retime speed must be a finite positive percent".into(),
                ));
            }
            (
                Some(project),
                "retime",
                "clip.speedDuration",
                json!({"clips":clips,"speed":speed_percent,"ripple":ripple}),
                true,
            )
        }
        Operation::Trim {
            project,
            clip,
            edge,
            mode,
            delta_frames,
        } => (
            Some(project),
            "trim",
            "timeline.trim",
            json!({"clip":clip,"edge":edge,"mode":mode,"deltaFrames":delta_frames}),
            true,
        ),
        Operation::Move {
            project,
            clip,
            track,
            timeline_ticks,
            insert,
        } => (
            Some(project),
            "move",
            "timeline.move",
            json!({"moves":[{"clip":clip,"track":track,"time":timeline_ticks}],"insert":insert,"linked":true}),
            true,
        ),
        Operation::Transition {
            project,
            clip,
            edge,
            frames,
            effect,
        } => {
            // Unknown names refuse here, before any CLI call mutates the
            // project; the engine would fail them generically at execution.
            validate_transition_effect(effect.as_deref())?;
            (
                Some(project),
                "transition",
                "sequence.applyVideoTransition",
                json!({"clip":clip,"edge":edge,"frames":frames,"effect":effect}),
                true,
            )
        }
        Operation::AudioGain {
            project,
            track,
            gain_db,
        } => {
            if !gain_db.is_finite() {
                return Err(Error::Schema("audio gain must be a finite dB value".into()));
            }
            (
                Some(project),
                "audio_gain",
                "mixer.setValue",
                json!({"strip":track,"lane":"volume","value":gain_db}),
                true,
            )
        }
        Operation::Export {
            project,
            path,
            format,
            hardware_encoder,
        } => (
            Some(project),
            "export",
            "file.exportMedia",
            json!({"path":path,"format":format_name(format),"hardwareEncoding":hardware_encoder,"wait":true}),
            false,
        ),
        Operation::RenderFrame {
            project,
            frame,
            path,
        } => {
            if frame < 0 {
                return Err(Error::Schema("render frame must be nonnegative".into()));
            }
            (
                Some(project),
                "render_frame",
                "__render",
                json!({"frame":frame,"path":path}),
                false,
            )
        }
        Operation::Inspect { project } => (
            Some(project),
            "inspect",
            "sequence.inspect",
            json!({}),
            false,
        ),
        Operation::OpenProject { path } => (
            Some(path),
            "open_project".into(),
            "project.inspect",
            json!({}),
            false,
        ),
    };
    Ok((tuple.0, tuple.1.into(), tuple.2, tuple.3, tuple.4))
}

fn run_command(
    cli: &Path,
    project: Option<&Path>,
    save: bool,
    id: &str,
    params: Value,
) -> Result<Value> {
    if id == "__render" {
        let inspect = run_command(cli, project, false, "sequence.inspect", json!({}))?;
        let rate = &inspect["settings"]["frame_rate"];
        let numerator = rate["num"].as_f64().ok_or_else(|| Error::UnknownResponse {
            command: "sequence.inspect".into(),
            detail: "settings.frame_rate.num missing".into(),
        })?;
        let denominator = rate["den"].as_f64().ok_or_else(|| Error::UnknownResponse {
            command: "sequence.inspect".into(),
            detail: "settings.frame_rate.den missing".into(),
        })?;
        let seconds = params["frame"].as_i64().unwrap_or_default() as f64 * denominator / numerator;
        let mut cmd = Command::new(cli);
        if let Some(project) = project {
            cmd.arg("--project").arg(project);
        }
        let output_path = params["path"]
            .as_str()
            .ok_or_else(|| Error::Schema("render request missing path".into()))?;
        cmd.args(["render", "--seconds"])
            .arg(seconds.to_string())
            .arg("--out")
            .arg(output_path);
        let output = cmd.output()?;
        if !output.status.success() {
            return Err(Error::Command {
                command: "render".into(),
                detail: String::from_utf8_lossy(&output.stderr).into_owned(),
            });
        }
        let path = params["path"]
            .as_str()
            .ok_or_else(|| Error::Schema("render request missing path".into()))?;
        if !Path::new(path).is_file() {
            return Err(Error::UnknownResponse {
                command: "render".into(),
                detail: "CLI succeeded but did not create its requested image".into(),
            });
        }
        let result = json!({"path":path,"frame":params["frame"]});
        validate_response_contract("render", &result)?;
        return Ok(result);
    }
    let mut cmd = Command::new(cli);
    if let Some(project) = project {
        cmd.arg("--project").arg(project);
    }
    cmd.args(["exec", id])
        .arg(params.to_string())
        .arg("--compact");
    if save {
        cmd.arg("--save");
    }
    parse_cli_output(id, cmd.output()?)
}

fn parse_cli_output(id: &str, output: std::process::Output) -> Result<Value> {
    if !output.status.success() {
        return Err(Error::Command {
            command: id.into(),
            detail: String::from_utf8_lossy(&output.stderr).into_owned(),
        });
    }
    let text = std::str::from_utf8(&output.stdout).map_err(|e| Error::UnknownResponse {
        command: id.into(),
        detail: e.to_string(),
    })?;
    let value: Value = serde_json::from_str(text).map_err(|e| Error::UnknownResponse {
        command: id.into(),
        detail: e.to_string(),
    })?;
    validate_response_contract(id, &value)?;
    Ok(value)
}

pub fn validate_response_contract(command: &str, value: &Value) -> Result<()> {
    // Delete, ripple delete and retime answer `null`: the receipt carries
    // no ids, and the equivalence proof reads the timeline back instead.
    if matches!(
        command,
        "edit.clear" | "edit.rippleDelete" | "clip.speedDuration"
    ) {
        return if value.is_null() {
            Ok(())
        } else {
            Err(Error::UnknownResponse {
                command: command.into(),
                detail: "expected JSON null".into(),
            })
        };
    }
    let allowed: &[&str] = match command {
        "file.newSequence" => &["sequence"],
        "file.import" => &[
            "items",
            "sequences",
            "documents",
            "errors",
            "ingest",
            "imageSequences",
        ],
        "timeline.place" => &["clips"],
        "timeline.razor" => &["cuts"],
        "timeline.trim" => &["delta"],
        "timeline.move" => &["moved", "overwritten"],
        "sequence.applyVideoTransition" => &["transition"],
        "mixer.setValue" => &["value"],
        "file.exportMedia" => &["job", "path", "preset", "result"],
        "sequence.inspect" => &[
            "id",
            "name",
            "settings",
            "duration",
            "durationFrames",
            "playhead",
            "in",
            "out",
            "split",
            "transitionSelection",
            "markers",
            "video",
            "audio",
            "selection",
        ],
        "project.inspect" => &[
            "name",
            "path",
            "dirty",
            "revision",
            "root",
            "activeSequence",
            "sourceItem",
        ],
        "render" => &["path", "width", "height", "frame", "seconds"],
        other => {
            return Err(Error::UnknownResponse {
                command: command.into(),
                detail: format!("no response contract for `{other}`"),
            })
        }
    };
    let object = value.as_object().ok_or_else(|| Error::UnknownResponse {
        command: command.into(),
        detail: "expected JSON object".into(),
    })?;
    if let Some(key) = object.keys().find(|key| !allowed.contains(&key.as_str())) {
        return Err(Error::UnknownResponse {
            command: command.into(),
            detail: format!("unknown response field `{key}`"),
        });
    }
    let required: &[&str] = match command {
        "file.newSequence" => &["sequence"],
        "file.import" => &["items"],
        "timeline.place" => &["clips"],
        "timeline.razor" => &["cuts"],
        "timeline.trim" => &["delta"],
        "timeline.move" => &["moved", "overwritten"],
        "sequence.applyVideoTransition" => &["transition"],
        "mixer.setValue" => &["value"],
        "file.exportMedia" => &["job", "path"],
        "sequence.inspect" => &["id", "video", "audio"],
        "project.inspect" => &["name", "path", "dirty", "revision", "root"],
        "render" => &["path"],
        _ => &[],
    };
    if let Some(key) = required.iter().find(|key| !object.contains_key(**key)) {
        return Err(Error::UnknownResponse {
            command: command.into(),
            detail: format!("missing response field `{key}`"),
        });
    }
    let valid = match command {
        "file.newSequence" => value["sequence"].is_u64(),
        "file.import" => {
            value["items"].is_array() && value.get("errors").is_none_or(Value::is_array)
        }
        "timeline.place" => value["clips"]
            .as_array()
            .is_some_and(|v| v.iter().all(Value::is_u64)),
        "timeline.razor" => value["cuts"].is_u64(),
        "timeline.trim" => value["delta"].is_i64(),
        "timeline.move" => value["moved"].is_array() && value["overwritten"].is_array(),
        "sequence.applyVideoTransition" => value["transition"].is_u64(),
        "mixer.setValue" => value["value"].is_number(),
        "file.exportMedia" => value["job"].is_u64() && value["path"].is_string(),
        "sequence.inspect" => {
            value["id"].is_u64() && value["video"].is_array() && value["audio"].is_array()
        }
        "project.inspect" => value["name"].is_string() && value["root"].is_object(),
        "render" => value["path"].is_string(),
        _ => false,
    };
    if !valid {
        return Err(Error::UnknownResponse {
            command: command.into(),
            detail: "response fields have an unexpected type or shape".into(),
        });
    }
    Ok(())
}

fn format_name(format: ExportFormat) -> &'static str {
    match format {
        ExportFormat::H264 => "h264",
        ExportFormat::ProRes => "prores",
        ExportFormat::Wav => "wav",
    }
}

fn detect_razor_loss(before: Option<&Value>, after: Option<&Value>) -> Result<Vec<LossReport>> {
    let (Some(before), Some(after)) = (before, after) else {
        return Ok(vec![]);
    };
    // Sequence inspection is a version-pinned engine response. Absence of tracks is an unknown
    // response; do not treat it as evidence that the edit is safe.
    let video_delta = item_count(after, "video")?.saturating_sub(item_count(before, "video")?);
    let audio_delta = item_count(after, "audio")?.saturating_sub(item_count(before, "audio")?);
    if video_delta > audio_delta {
        Ok(vec![LossReport { code:"linked_audio_not_razored".into(), detail:format!("picture gained {video_delta} razor segment(s), audio gained {audio_delta}; linked audio may continue under a picture gap") }])
    } else {
        Ok(vec![])
    }
}

fn item_count(state: &Value, kind: &str) -> Result<usize> {
    state
        .get(kind)
        .and_then(Value::as_array)
        .ok_or_else(|| Error::UnknownResponse {
            command: "sequence.inspect".into(),
            detail: format!("missing `{kind}` track array"),
        })?
        .iter()
        .try_fold(0usize, |sum, track| {
            let items = track
                .get("items")
                .and_then(Value::as_array)
                .ok_or_else(|| Error::UnknownResponse {
                    command: "sequence.inspect".into(),
                    detail: format!("{kind} track missing `items` array"),
                })?;
            Ok(sum + items.len())
        })
}

fn detect_trim_loss(
    before: Option<&Value>,
    after: Option<&Value>,
    params: &Value,
) -> Result<Vec<LossReport>> {
    let (Some(before), Some(after)) = (before, after) else {
        return Ok(vec![]);
    };
    let clip = params["clip"]
        .as_u64()
        .ok_or_else(|| Error::Schema("trim request missing clip id".into()))?;
    let find = |state: &Value| -> Result<(i64, i64)> {
        for kind in ["video", "audio"] {
            let tracks = state[kind]
                .as_array()
                .ok_or_else(|| Error::UnknownResponse {
                    command: "sequence.inspect".into(),
                    detail: format!("missing `{kind}`"),
                })?;
            for track in tracks {
                for item in track["items"]
                    .as_array()
                    .ok_or_else(|| Error::UnknownResponse {
                        command: "sequence.inspect".into(),
                        detail: "missing `items`".into(),
                    })?
                {
                    if item["clip"].as_u64() == Some(clip) {
                        return Ok((
                            item["startFrame"]
                                .as_i64()
                                .ok_or_else(|| Error::UnknownResponse {
                                    command: "sequence.inspect".into(),
                                    detail: "clip missing startFrame".into(),
                                })?,
                            item["durationFrames"].as_i64().ok_or_else(|| {
                                Error::UnknownResponse {
                                    command: "sequence.inspect".into(),
                                    detail: "clip missing durationFrames".into(),
                                }
                            })?,
                        ));
                    }
                }
            }
        }
        Err(Error::UnknownResponse {
            command: "sequence.inspect".into(),
            detail: format!("trimmed clip {clip} missing"),
        })
    };
    let (old_start, old_duration) = find(before)?;
    let (new_start, new_duration) = find(after)?;
    let requested = params["deltaFrames"]
        .as_i64()
        .ok_or_else(|| Error::Schema("trim request missing deltaFrames".into()))?;
    let expected = match params["edge"].as_str() {
        Some("in") => (requested, -requested),
        _ => (0, requested),
    };
    let actual = (new_start - old_start, new_duration - old_duration);
    if actual != expected {
        Ok(vec![LossReport { code:"trim_delta_mismatch".into(), detail:format!("requested start/duration delta {:?} frames, FilmCraft applied {:?}; source duration was {old_duration} frames", expected, actual) }])
    } else {
        Ok(vec![])
    }
}

/// A move that trims an occupant reports it in `overwritten`: surface that
/// as an explicit loss rather than a silent trim. Only clips OUTSIDE the
/// moved set count: the engine also reports sub-frame tick normalization
/// of the moved clips themselves there (measured: a placed 72-frame span
/// reads back 762810048000 ticks and a later move reports trimming it to
/// the exact 762048000000, with frame readback unchanged at 72), and that
/// normalization is not data loss. Ripple translations pre-check
/// destinations and refuse before reaching the engine, so a clean ripple
/// flow reports no loss here; the frame-exact inspect comparison behind
/// every equivalence proof remains the backstop for moved-clip damage.
fn detect_move_loss(response: &Value) -> Result<Vec<LossReport>> {
    let moved: std::collections::BTreeSet<u64> = response["moved"]
        .as_array()
        .ok_or_else(|| Error::UnknownResponse {
            command: "timeline.move".into(),
            detail: "missing `moved` array".into(),
        })?
        .iter()
        .filter_map(|v| v.as_u64())
        .collect();
    let overwritten = response["overwritten"].as_array().ok_or_else(|| {
        Error::UnknownResponse {
            command: "timeline.move".into(),
            detail: "missing `overwritten` array".into(),
        }
    })?;
    let foreign: Vec<_> = overwritten
        .iter()
        .filter(|entry| {
            entry
                .get("clip")
                .and_then(|v| v.as_u64())
                .is_none_or(|clip| !moved.contains(&clip))
        })
        .collect();
    if foreign.is_empty() {
        return Ok(vec![]);
    }
    Ok(vec![LossReport {
        code: "move_overwrote".into(),
        detail: format!(
            "FilmCraft trimmed {} occupant(s) the move landed on outside the moved set: {foreign:?}; plan moves onto free spans instead",
            foreign.len()
        ),
    }])
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn refuses_unknown_adapter_schema() {
        let adapter = FilmCraft::with_cli("unused");
        let err = adapter
            .execute(Request {
                adapter_schema: 999,
                operation: Operation::Inspect {
                    project: "x.fcproj".into(),
                },
            })
            .unwrap_err();
        assert!(matches!(err, Error::Schema(_)));
    }

    #[test]
    fn project_schema_must_be_exactly_pinned() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("project.fcproj");
        std::fs::write(
            &path,
            r#"{"format":"filmcraft.project","schema_version":13,"project":{}}"#,
        )
        .unwrap();
        assert!(matches!(check_project_schema(&path), Err(Error::Schema(_))));
        std::fs::write(
            &path,
            r#"{"format":"filmcraft.project","schema_version":14,"project":{}}"#,
        )
        .unwrap();
        assert!(check_project_schema(&path).is_ok());
    }

    #[test]
    fn request_and_receipt_reject_unknown_fields() {
        let unknown: std::result::Result<Request<Operation>, _> = serde_json::from_str(
            r#"{"adapter_schema":1,"extra":true,"operation":{"kind":"inspect","project":"x.fcproj"}}"#,
        );
        assert!(unknown.is_err());
        let unknown: std::result::Result<Receipt, _> = serde_json::from_str(
            r#"{"adapter_schema":1,"engine_revision":"x","project_schema":14,"operation":"inspect","engine_response":{},"losses":[],"extra":true}"#,
        );
        assert!(unknown.is_err());
    }

    #[test]
    fn delete_ripple_delete_and_retime_validate_before_the_engine() {
        // Empty clip lists refuse without a CLI call.
        for operation in [
            Operation::Delete {
                project: "x.fcproj".into(),
                clips: vec![],
            },
            Operation::RippleDelete {
                project: "x.fcproj".into(),
                clips: vec![],
            },
            Operation::Retime {
                project: "x.fcproj".into(),
                clips: vec![],
                speed_percent: 200.0,
                ripple: true,
            },
        ] {
            assert!(matches!(plan(operation), Err(Error::Schema(_))));
        }
        // Non-positive and non-finite speeds refuse.
        for speed in [0.0, -50.0, f64::NAN, f64::INFINITY] {
            let operation = Operation::Retime {
                project: "x.fcproj".into(),
                clips: vec![7],
                speed_percent: speed,
                ripple: true,
            };
            assert!(
                matches!(plan(operation), Err(Error::Schema(_))),
                "speed {speed} must refuse"
            );
        }
        // The three null-answering commands accept exactly null.
        for command in ["edit.clear", "edit.rippleDelete", "clip.speedDuration"] {
            assert!(validate_response_contract(command, &Value::Null).is_ok());
            assert!(validate_response_contract(command, &json!({})).is_err());
        }
        // A move that trims only moved clips (sub-frame normalization)
        // is clean; trimming an occupant outside the moved set is a loss.
        let clean = json!({"moved": [7], "overwritten": []});
        assert!(detect_move_loss(&clean).unwrap().is_empty());
        let normalized =
            json!({"moved": [10, 11], "overwritten": [{"clip": 10, "was": 762810048000u64, "now": 762048000000u64}]});
        assert!(detect_move_loss(&normalized).unwrap().is_empty());
        let dirty = json!({"moved": [13, 14], "overwritten": [{"clip": 10}]});
        let losses = detect_move_loss(&dirty).unwrap();
        assert_eq!(losses.len(), 1);
        assert_eq!(losses[0].code, "move_overwrote");
    }

    #[test]
    fn transition_authority_pins_the_pinned_engine_surface() {
        // The authority shape: every entry is a named VideoTransition.
        let effects: Vec<Value> =
            serde_json::from_str(super::VIDEO_TRANSITIONS_JSON).expect("authority parses");
        assert_eq!(effects.len(), 112);
        assert!(effects.iter().all(|e| e["kind"] == "VideoTransition"
            && e["id"].is_string()
            && e["name"].is_string()));
        // Engine spelling rule: id, display name and case/space variants
        // are known; pipeline-native names the engine lacks are not.
        for known in [
            "cross_dissolve",
            "Cross Dissolve",
            "CROSS_DISSOLVE",
            "slide",
            " Slide ",
            "morph_cut",
            "Morph Cut",
        ] {
            assert!(
                super::transition_effect_known(known),
                "{known} must be known"
            );
            assert!(super::validate_transition_effect(Some(known)).is_ok());
        }
        for unknown in [
            "smooth_cut",
            "Smooth Cut",
            "spin",
            "Spin",
            "bogus_xyz_nope",
            "",
            "   ",
            "cross-dissolve",
        ] {
            assert!(
                !super::transition_effect_known(unknown),
                "{unknown:?} must be unknown"
            );
            assert!(super::validate_transition_effect(Some(unknown)).is_err());
        }
        // No effect names the engine default transition and stays valid.
        assert!(super::validate_transition_effect(None).is_ok());
    }

    #[test]
    fn transition_operations_validate_effect_before_the_engine() {
        let operation = Operation::Transition {
            project: "x.fcproj".into(),
            clip: 7,
            edge: TrimEdge::In,
            frames: 4,
            effect: Some("smooth_cut".into()),
        };
        assert!(matches!(plan(operation), Err(Error::Schema(_))));
        let operation = Operation::Transition {
            project: "x.fcproj".into(),
            clip: 7,
            edge: TrimEdge::In,
            frames: 4,
            effect: Some("cross_dissolve".into()),
        };
        assert!(plan(operation).is_ok());
    }
}

pub fn check_project_schema(path: &Path) -> Result<()> {
    let bytes = std::fs::read(path)?;
    let doc: Value = serde_json::from_slice(&bytes)?;
    if doc["format"].as_str() != Some(FILMCRAFT_PROJECT_FORMAT) {
        return Err(Error::Schema(
            "missing or unknown `format` identifier".into(),
        ));
    }
    let version = doc["schema_version"]
        .as_u64()
        .ok_or_else(|| Error::Schema("missing integer `schema_version`".into()))?;
    if version != u64::from(FILMCRAFT_PROJECT_SCHEMA) {
        return Err(Error::Schema(format!(
            "schema {version}; adapter supports exactly {FILMCRAFT_PROJECT_SCHEMA}"
        )));
    }
    Ok(())
}

fn verify_cli_version(cli: &Path) -> Result<()> {
    let output = Command::new(cli).arg("--version").output()?;
    if !output.status.success() {
        return Err(Error::Cli(format!(
            "pinned CLI --version exited {}",
            output.status
        )));
    }
    let version = std::str::from_utf8(&output.stdout)
        .map_err(|e| Error::Cli(e.to_string()))?
        .trim();
    if version != format!("filmcraft-cli {FILMCRAFT_CLI_VERSION}") {
        return Err(Error::Cli(format!("CLI version `{version}`; expected FilmCraft {FILMCRAFT_CLI_VERSION} from revision {FILMCRAFT_REVISION}")));
    }
    Ok(())
}
