//! Strict adapter for the pinned FilmCraft headless CLI.
//!
//! Ren owns the request and receipt types here. FilmCraft project files remain an engine
//! projection and are checked against the exact format revision this driver qualifies.

use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use std::{
    path::{Path, PathBuf},
    process::Command,
};
use thiserror::Error;

pub const FILMCRAFT_REPOSITORY: &str = "https://github.com/storytold/filmcraft.git";
/// Replaced by the intake manifest when that lane lands. Keep this as the sole pin source.
pub const FILMCRAFT_REVISION: &str = "7bd76212629a067a9d1c5426d751de9faf4664b7";
pub const FILMCRAFT_CLI_VERSION: &str = "0.5.0";
pub const FILMCRAFT_PROJECT_FORMAT: &str = "filmcraft.project";
pub const FILMCRAFT_PROJECT_SCHEMA: u32 = 13;
pub const ADAPTER_SCHEMA: u32 = 1;

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
            "--package",
            "filmcraft-cli",
            "--root",
        ])
        .arg(&root)
        .arg("--locked")
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
        } => (
            Some(project),
            "transition",
            "sequence.applyVideoTransition",
            json!({"clip":clip,"edge":edge,"frames":frames,"effect":effect}),
            true,
        ),
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
            r#"{"format":"filmcraft.project","schema_version":12,"project":{}}"#,
        )
        .unwrap();
        assert!(matches!(check_project_schema(&path), Err(Error::Schema(_))));
        std::fs::write(
            &path,
            r#"{"format":"filmcraft.project","schema_version":13,"project":{}}"#,
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
            r#"{"adapter_schema":1,"engine_revision":"x","project_schema":13,"operation":"inspect","engine_response":{},"losses":[],"extra":true}"#,
        );
        assert!(unknown.is_err());
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
