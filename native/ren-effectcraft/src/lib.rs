//! A small, versioned boundary around EffectCraft's headless CLI.
//!
//! Ren owns these request and receipt types. EffectCraft project files are disposable engine
//! projections and never become Ren's project model.

use serde::{Deserialize, Serialize};
use serde_json::{Value, json};
use std::collections::{BTreeMap, HashMap};
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Output};
use std::sync::{Mutex, OnceLock};

/// Serializes pinned-CLI resolution inside one process. Cloning, checking out and building the
/// cached EffectCraft source share one working tree, so concurrent resolves race on the staging
/// directory and the checkout index lock; the CLI invocations themselves stay concurrent.
fn cli_build_lock() -> &'static Mutex<()> {
    static LOCK: OnceLock<Mutex<()>> = OnceLock::new();
    LOCK.get_or_init(|| Mutex::new(()))
}

/// Immutable upstream revision qualified by the EffectCraft capability scout.
pub const PINNED_EFFECTCRAFT_REV: &str = "813c7c4650f0d4c805da620c8d5837c9600ad2e3";
pub const REQUEST_VERSION: u32 = 1;
pub const RECEIPT_VERSION: u32 = 1;
const UPSTREAM: &str = "https://github.com/storytold/effectcraft.git";

#[derive(Debug)]
pub enum Error {
    Io(String),
    Cli(String),
    Version {
        expected: String,
        actual: String,
    },
    Invalid(String),
    UnknownEffect(String),
    UnknownParameter {
        effect: String,
        parameter: String,
    },
    ParameterType {
        effect: String,
        parameter: String,
        expected: String,
        actual: String,
    },
    Protocol(String),
    Revision {
        expected: String,
        actual: String,
    },
}

impl std::fmt::Display for Error {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::Io(e) | Self::Cli(e) | Self::Invalid(e) | Self::Protocol(e) => f.write_str(e),
            Self::Version { expected, actual } => write!(
                f,
                "EffectCraft version mismatch: expected {expected}, got {actual}"
            ),
            Self::Revision { expected, actual } => write!(
                f,
                "EffectCraft source revision mismatch: expected {expected}, got {actual}"
            ),
            Self::UnknownEffect(id) => {
                write!(f, "EffectCraft effect is not in the pinned registry: {id}")
            }
            Self::UnknownParameter { effect, parameter } => write!(
                f,
                "unknown parameter `{parameter}` for EffectCraft effect `{effect}`"
            ),
            Self::ParameterType {
                effect,
                parameter,
                expected,
                actual,
            } => write!(
                f,
                "invalid value type for `{parameter}` on EffectCraft effect `{effect}`: expected {expected}, got {actual}"
            ),
        }
    }
}

impl std::error::Error for Error {}

#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
#[serde(tag = "op", rename_all = "snake_case")]
pub enum Operation {
    NewComp {
        name: String,
        width: u32,
        height: u32,
        frame_rate: f64,
        duration: f64,
    },
    ImportFootage {
        path: PathBuf,
    },
    AddText {
        name: String,
        text: String,
        size: f64,
        fill: String,
        position: [f64; 2],
        font: Option<String>,
    },
    AddShape {
        name: String,
        kind: String,
        fill: String,
        size: [f64; 2],
        position: [f64; 2],
    },
    AddFootageLayer {
        footage: String,
    },
    Keyframe {
        layer: String,
        path: String,
        time: f64,
        value: Value,
    },
    AddMask {
        layer: String,
        shape: String,
        rect: [f64; 4],
    },
    ApplyEffect {
        layer: String,
        effect_id: String,
        params: BTreeMap<String, Value>,
    },
}

#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct RenderRequest {
    pub format: RenderFormat,
    pub comp: String,
    pub output: PathBuf,
    pub start_seconds: Option<f64>,
    pub end_seconds: Option<f64>,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum RenderFormat {
    RgbaPngSequence,
    Prores4444Alpha,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Request {
    pub version: u32,
    pub project_id: String,
    pub operations: Vec<Operation>,
    pub renders: Vec<RenderRequest>,
}

impl Request {
    pub fn new(project_id: impl Into<String>) -> Self {
        Self {
            version: REQUEST_VERSION,
            project_id: project_id.into(),
            operations: Vec::new(),
            renders: Vec::new(),
        }
    }
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct RenderReceipt {
    pub format: RenderFormat,
    pub path: PathBuf,
    pub width: u32,
    pub height: u32,
    pub frames: Option<u64>,
    pub has_alpha: bool,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Receipt {
    pub version: u32,
    pub project_id: String,
    pub upstream_revision: String,
    pub engine_version: String,
    pub project_file: PathBuf,
    pub renders: Vec<RenderReceipt>,
}

#[derive(Clone, Debug)]
pub struct EffectSpec {
    pub id: String,
    pub name: String,
    pub parameters: BTreeMap<String, ParameterSpec>,
}

#[derive(Clone, Debug)]
pub struct ParameterSpec {
    pub id: String,
    pub value_kind: &'static str,
}

/// Resolves a binary, verifies its engine version, and retrieves the effect registry from MCP.
pub struct Driver {
    cli: PathBuf,
    engine_version: String,
    effects: BTreeMap<String, EffectSpec>,
}

impl Driver {
    pub fn resolve() -> Result<Self, Error> {
        let cli = locate_or_build_cli()?;
        Self::from_cli(cli)
    }

    pub fn from_cli(cli: impl Into<PathBuf>) -> Result<Self, Error> {
        let cli = cli.into();
        verify_cli_revision(&cli)?;
        let info = run_json(&cli, &["info"])?;
        let version = info["version"]
            .as_str()
            .ok_or_else(|| Error::Protocol("CLI info omitted version".into()))?
            .to_owned();
        if version != "0.7.0" {
            return Err(Error::Version {
                expected: "0.7.0".into(),
                actual: version,
            });
        }
        let effects = fetch_effects(&cli)?;
        Ok(Self {
            cli,
            engine_version: version,
            effects,
        })
    }

    pub fn effects(&self) -> impl Iterator<Item = &EffectSpec> {
        self.effects.values()
    }

    pub fn execute(&self, request: &Request, work_dir: &Path) -> Result<Receipt, Error> {
        if request.version != REQUEST_VERSION {
            return Err(Error::Invalid(format!(
                "unsupported EffectCraft request version {}",
                request.version
            )));
        }
        if request.project_id.trim().is_empty() {
            return Err(Error::Invalid("project_id must not be empty".into()));
        }
        std::fs::create_dir_all(work_dir).map_err(io_err)?;
        let project_file = work_dir.join(format!("{}.ecproj", safe_name(&request.project_id)));
        if project_file.exists() {
            return Err(Error::Invalid(format!(
                "EffectCraft projection already exists; choose a fresh work directory: {}",
                project_file.display()
            )));
        }
        let mut commands: Vec<(String, Value)> = Vec::new();
        let mut effect_indices: HashMap<String, usize> = HashMap::new();
        for op in &request.operations {
            let index = if let Operation::ApplyEffect { layer, .. } = op {
                let next = effect_indices.entry(layer.clone()).or_insert(0);
                *next += 1;
                *next
            } else {
                1
            };
            commands.extend(self.compile_operation(op, index)?);
        }
        if commands.is_empty() {
            return Err(Error::Invalid("request has no operations".into()));
        }
        let mut args = vec!["run".to_owned()];
        for (id, params) in commands {
            args.push(id);
            args.push(params.to_string());
        }
        args.extend([
            "--empty".into(),
            "--save-as".into(),
            project_file.to_string_lossy().into_owned(),
            "--json".into(),
        ]);
        let out = invoke(&self.cli, &args)?;
        if !out.status.success() {
            return Err(Error::Cli(json_error(&out)));
        }

        let mut renders = Vec::new();
        for render in &request.renders {
            renders.push(self.render(&project_file, render)?);
        }
        Ok(Receipt {
            version: RECEIPT_VERSION,
            project_id: request.project_id.clone(),
            upstream_revision: PINNED_EFFECTCRAFT_REV.into(),
            engine_version: self.engine_version.clone(),
            project_file,
            renders,
        })
    }

    fn compile_operation(
        &self,
        op: &Operation,
        effect_index: usize,
    ) -> Result<Vec<(String, Value)>, Error> {
        let one = |id: &str, params: Value| vec![(id.to_owned(), params)];
        Ok(match op {
            Operation::NewComp {
                name,
                width,
                height,
                frame_rate,
                duration,
            } => one(
                "comp.new",
                json!({"name":name,"width":width,"height":height,"frameRate":frame_rate,"duration":duration}),
            ),
            Operation::ImportFootage { path } => one("file.import", json!({"paths":[path]})),
            Operation::AddText {
                name,
                text,
                size,
                fill,
                position,
                font,
            } => one(
                "layer.newText",
                json!({"name":name,"text":text,"size":size,"fill":fill,"position":position,"font":font}),
            ),
            Operation::AddShape {
                name,
                kind,
                fill,
                size,
                position,
            } => one(
                "layer.newShape",
                json!({"name":name,"kind":kind,"fill":fill,"size":size,"position":position}),
            ),
            Operation::AddFootageLayer { footage } => one("layer.addItem", json!({"item":footage})),
            Operation::Keyframe {
                layer,
                path,
                time,
                value,
            } => one(
                "prop.addKey",
                json!({"layer":layer,"path":path,"time":time,"value":value}),
            ),
            Operation::AddMask { layer, shape, rect } => one(
                "layer.addMask",
                json!({"layer":layer,"shape":shape,"rect":rect}),
            ),
            Operation::ApplyEffect {
                layer,
                effect_id,
                params,
            } => {
                let spec = self
                    .effects
                    .get(effect_id)
                    .ok_or_else(|| Error::UnknownEffect(effect_id.clone()))?;
                for key in params.keys() {
                    if !spec.parameters.contains_key(key) {
                        return Err(Error::UnknownParameter {
                            effect: spec.id.clone(),
                            parameter: key.clone(),
                        });
                    }
                }
                for (key, value) in params {
                    let parameter =
                        spec.parameters
                            .get(key)
                            .ok_or_else(|| Error::UnknownParameter {
                                effect: spec.id.clone(),
                                parameter: key.clone(),
                            })?;
                    let actual = json_value_kind(value);
                    if parameter.value_kind != actual {
                        return Err(Error::ParameterType {
                            effect: spec.id.clone(),
                            parameter: key.clone(),
                            expected: parameter.value_kind.into(),
                            actual: actual.into(),
                        });
                    }
                }
                let mut commands = one("effect.apply", json!({"layer":layer,"effect":spec.id}));
                for (parameter, value) in params {
                    commands.push(("prop.set".into(), json!({"layer":layer,"path":format!("effects/#{effect_index}/{parameter}"),"value":value})));
                }
                commands
            }
        })
    }

    pub fn validate_operation(&self, op: &Operation) -> Result<(), Error> {
        self.compile_operation(op, 1).map(|_| ())
    }

    fn render(&self, project: &Path, request: &RenderRequest) -> Result<RenderReceipt, Error> {
        if matches!(request.format, RenderFormat::RgbaPngSequence)
            && !request
                .output
                .file_name()
                .is_some_and(|n| n.to_string_lossy().contains('#'))
        {
            return Err(Error::Invalid(
                "PNG sequence output must include # frame-number placeholders".into(),
            ));
        }
        if let Some(parent) = request
            .output
            .parent()
            .filter(|p| !p.as_os_str().is_empty())
        {
            std::fs::create_dir_all(parent).map_err(io_err)?;
        }
        let (format, extra): (&str, Vec<String>) = match request.format {
            RenderFormat::RgbaPngSequence => ("png", vec!["--channels".into(), "rgba".into()]),
            RenderFormat::Prores4444Alpha => (
                "prores",
                vec![
                    "--channels".into(),
                    "rgba".into(),
                    "--prores".into(),
                    "4444".into(),
                ],
            ),
        };
        let mut args = vec![
            "render".into(),
            "--project".into(),
            project.to_string_lossy().into_owned(),
            "--comp".into(),
            request.comp.clone(),
            "--out".into(),
            request.output.to_string_lossy().into_owned(),
            "--format".into(),
            format.into(),
        ];
        if let Some(s) = request.start_seconds {
            args.extend(["--start".into(), s.to_string()]);
        }
        if let Some(e) = request.end_seconds {
            args.extend(["--end".into(), e.to_string()]);
        }
        args.extend(extra);
        args.push("--json".into());
        let out = invoke(&self.cli, &args)?;
        if !out.status.success() {
            return Err(Error::Cli(json_error(&out)));
        }
        let (probe_path, png) = match request.format {
            RenderFormat::RgbaPngSequence => (first_sequence_frame(&request.output), true),
            RenderFormat::Prores4444Alpha => (request.output.clone(), false),
        };
        let (width, height, mut frames, has_alpha) = if png {
            probe_png(&probe_path)?
        } else {
            probe_video(&probe_path)?
        };
        if png {
            frames = Some(count_sequence_frames(&request.output)?);
        }
        if !has_alpha {
            return Err(Error::Cli(format!(
                "EffectCraft render did not contain alpha: {}",
                probe_path.display()
            )));
        }
        Ok(RenderReceipt {
            format: request.format.clone(),
            path: request.output.clone(),
            width,
            height,
            frames,
            has_alpha,
        })
    }
}

fn safe_name(s: &str) -> String {
    s.chars()
        .map(|c| {
            if c.is_ascii_alphanumeric() || c == '-' || c == '_' {
                c
            } else {
                '_'
            }
        })
        .collect()
}
fn io_err(e: std::io::Error) -> Error {
    Error::Io(e.to_string())
}

fn first_sequence_frame(pattern: &Path) -> PathBuf {
    let name = pattern.file_name().unwrap_or_default().to_string_lossy();
    let replaced = if let Some(start) = name.find('#') {
        let count = name[start..].chars().take_while(|c| *c == '#').count();
        format!(
            "{}{:0width$}{}",
            &name[..start],
            0,
            &name[start + count..],
            width = count
        )
    } else {
        name.to_string()
    };
    pattern.with_file_name(replaced)
}

fn count_sequence_frames(pattern: &Path) -> Result<u64, Error> {
    let file = pattern.file_name().unwrap_or_default().to_string_lossy();
    let Some(start) = file.find('#') else {
        return Err(Error::Invalid(
            "PNG sequence output must include # frame-number placeholders".into(),
        ));
    };
    let count = file[start..].chars().take_while(|c| *c == '#').count();
    let prefix = &file[..start];
    let suffix = &file[start + count..];
    let parent = pattern.parent().unwrap_or_else(|| Path::new("."));
    let mut total = 0;
    for entry in std::fs::read_dir(parent).map_err(io_err)? {
        let entry = entry.map_err(io_err)?;
        let name = entry.file_name().to_string_lossy().into_owned();
        let Some(middle) = name
            .strip_prefix(prefix)
            .and_then(|n| n.strip_suffix(suffix))
        else {
            continue;
        };
        if middle.len() == count && middle.chars().all(|c| c.is_ascii_digit()) {
            total += 1;
        }
    }
    if total == 0 {
        return Err(Error::Protocol(format!(
            "EffectCraft produced no PNG sequence frames for {}",
            pattern.display()
        )));
    }
    Ok(total)
}

fn probe_png(path: &Path) -> Result<(u32, u32, Option<u64>, bool), Error> {
    use std::io::Read;
    let mut file = std::fs::File::open(path).map_err(|e| {
        Error::Io(format!(
            "cannot read EffectCraft PNG sequence frame {}: {e}",
            path.display()
        ))
    })?;
    let mut header = [0u8; 26];
    file.read_exact(&mut header).map_err(io_err)?;
    if &header[..8] != b"\x89PNG\r\n\x1a\n" || &header[12..16] != b"IHDR" {
        return Err(Error::Protocol("rendered PNG has an invalid header".into()));
    }
    let width = u32::from_be_bytes([header[16], header[17], header[18], header[19]]);
    let height = u32::from_be_bytes([header[20], header[21], header[22], header[23]]);
    let alpha = matches!(header[25], 4 | 6);
    Ok((width, height, None, alpha))
}

fn probe_video(path: &Path) -> Result<(u32, u32, Option<u64>, bool), Error> {
    let out = Command::new("ffprobe")
        .args([
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=width,height,nb_frames,pix_fmt",
            "-of",
            "json",
        ])
        .arg(path)
        .output()
        .map_err(io_err)?;
    if !out.status.success() {
        return Err(Error::Cli(format!(
            "ffprobe could not inspect {}: {}",
            path.display(),
            String::from_utf8_lossy(&out.stderr)
        )));
    }
    let value: Value = serde_json::from_slice(&out.stdout)
        .map_err(|e| Error::Protocol(format!("ffprobe returned invalid JSON: {e}")))?;
    let stream = value["streams"]
        .as_array()
        .and_then(|s| s.first())
        .ok_or_else(|| Error::Protocol("rendered ProRes has no video stream".into()))?;
    let width = stream["width"].as_u64().unwrap_or(0) as u32;
    let height = stream["height"].as_u64().unwrap_or(0) as u32;
    let frames = stream["nb_frames"].as_str().and_then(|x| x.parse().ok());
    let alpha = stream["pix_fmt"].as_str().is_some_and(|s| s.contains('a'));
    Ok((width, height, frames, alpha))
}

fn invoke(cli: &Path, args: &[String]) -> Result<Output, Error> {
    Command::new(cli).args(args).output().map_err(io_err)
}
fn run_json(cli: &Path, args: &[&str]) -> Result<Value, Error> {
    let out = Command::new(cli)
        .args(args)
        .arg("--json")
        .output()
        .map_err(io_err)?;
    if !out.status.success() {
        return Err(Error::Cli(json_error(&out)));
    }
    serde_json::from_slice(&out.stdout)
        .map_err(|e| Error::Protocol(format!("CLI returned invalid JSON: {e}")))
}
fn json_error(out: &Output) -> String {
    serde_json::from_slice::<Value>(&out.stdout)
        .ok()
        .and_then(|v| v["error"].as_str().map(str::to_owned))
        .unwrap_or_else(|| String::from_utf8_lossy(&out.stderr).trim().to_owned())
}

fn fetch_effects(cli: &Path) -> Result<BTreeMap<String, EffectSpec>, Error> {
    let child = Command::new(cli)
        .arg("mcp")
        .stdin(std::process::Stdio::piped())
        .stdout(std::process::Stdio::piped())
        .stderr(std::process::Stdio::null())
        .spawn()
        .map_err(io_err)?;
    let mut child = ChildGuard(child);
    let mut stdin = child
        .stdin
        .take()
        .ok_or_else(|| Error::Protocol("MCP stdin unavailable".into()))?;
    use std::io::{BufRead, BufReader, Write};
    writeln!(stdin, "{{\"jsonrpc\":\"2.0\",\"id\":1,\"method\":\"initialize\",\"params\":{{\"protocolVersion\":\"2025-03-26\",\"capabilities\":{{}},\"clientInfo\":{{\"name\":\"ren-effectcraft\",\"version\":\"0.1.0\"}}}}}}").map_err(io_err)?;
    let mut stdout = BufReader::new(
        child
            .stdout
            .take()
            .ok_or_else(|| Error::Protocol("MCP stdout unavailable".into()))?,
    );
    let mut line = String::new();
    stdout.read_line(&mut line).map_err(io_err)?;
    if !line.contains("\"id\":1") {
        return Err(Error::Protocol(format!(
            "unexpected MCP initialize reply: {line}"
        )));
    }
    writeln!(
        stdin,
        "{{\"jsonrpc\":\"2.0\",\"method\":\"notifications/initialized\"}}"
    )
    .map_err(io_err)?;
    writeln!(stdin, "{{\"jsonrpc\":\"2.0\",\"id\":2,\"method\":\"tools/call\",\"params\":{{\"name\":\"list_effects\",\"arguments\":{{}}}}}}").map_err(io_err)?;
    line.clear();
    stdout.read_line(&mut line).map_err(io_err)?;
    let response: Value = serde_json::from_str(&line)
        .map_err(|e| Error::Protocol(format!("invalid MCP registry response: {e}")))?;
    drop(stdin);
    let _ = child.wait();
    let result = &response["result"];
    let data = result
        .get("structuredContent")
        .cloned()
        .or_else(|| {
            result["content"]
                .as_array()
                .and_then(|a| a.first())
                .and_then(|c| c["text"].as_str())
                .and_then(|t| serde_json::from_str(t).ok())
        })
        .ok_or_else(|| {
            Error::Protocol("MCP list_effects returned no structured registry".into())
        })?;
    let list = data
        .as_array()
        .or_else(|| data["effects"].as_array())
        .ok_or_else(|| Error::Protocol("EffectCraft registry is not an array".into()))?;
    let mut effects = BTreeMap::new();
    for item in list {
        let id = item["id"]
            .as_str()
            .ok_or_else(|| Error::Protocol("effect registry item omitted id".into()))?
            .to_owned();
        let name = item["name"].as_str().unwrap_or(&id).to_owned();
        let parameters = item["params"]
            .as_array()
            .or_else(|| item["parameters"].as_array())
            .map(|p| {
                p.iter()
                    .filter_map(|x| {
                        let id = x["id"].as_str().or_else(|| x["name"].as_str())?.to_owned();
                        let value_kind = json_value_kind(&x["default"]);
                        Some((id.clone(), ParameterSpec { id, value_kind }))
                    })
                    .collect()
            })
            .unwrap_or_default();
        effects.insert(
            id.clone(),
            EffectSpec {
                id,
                name,
                parameters,
            },
        );
    }
    if effects.is_empty() {
        return Err(Error::Protocol(
            "EffectCraft returned an empty registry".into(),
        ));
    }
    Ok(effects)
}

struct ChildGuard(Child);

impl Drop for ChildGuard {
    fn drop(&mut self) {
        let _ = self.0.kill();
        let _ = self.0.wait();
    }
}

impl std::ops::Deref for ChildGuard {
    type Target = Child;
    fn deref(&self) -> &Self::Target {
        &self.0
    }
}

impl std::ops::DerefMut for ChildGuard {
    fn deref_mut(&mut self) -> &mut Self::Target {
        &mut self.0
    }
}

fn json_value_kind(value: &Value) -> &'static str {
    match value {
        Value::Null => "null",
        Value::Bool(_) => "boolean",
        Value::Number(_) => "number",
        Value::String(_) => "string",
        Value::Array(_) => "array",
        Value::Object(_) => "object",
    }
}

fn verify_cli_revision(cli: &Path) -> Result<(), Error> {
    let mut dir = cli
        .parent()
        .ok_or_else(|| Error::Invalid("EffectCraft CLI path has no parent".into()))?;
    let mut source_root = None;
    loop {
        if dir.join(".git").exists() {
            source_root = Some(dir);
            break;
        }
        let Some(parent) = dir.parent() else { break };
        dir = parent;
    }
    let revision = if let Some(root) = source_root {
        let output = Command::new("git")
            .current_dir(root)
            .args(["rev-parse", "HEAD"])
            .output()
            .map_err(io_err)?;
        if !output.status.success() {
            return Err(Error::Cli(
                "could not verify EffectCraft source revision".into(),
            ));
        }
        String::from_utf8_lossy(&output.stdout).trim().to_owned()
    } else {
        std::env::var("REN_EFFECTCRAFT_REV").unwrap_or_default()
    };
    if revision != PINNED_EFFECTCRAFT_REV {
        return Err(Error::Revision {
            expected: PINNED_EFFECTCRAFT_REV.into(),
            actual: if revision.is_empty() {
                "unverified binary (set REN_EFFECTCRAFT_REV)".into()
            } else {
                revision
            },
        });
    }
    Ok(())
}

fn locate_or_build_cli() -> Result<PathBuf, Error> {
    if let Some(p) = std::env::var_os("REN_EFFECTCRAFT_CLI") {
        let path = PathBuf::from(p);
        if path.is_file() {
            return Ok(path);
        }
    }
    let _guard = cli_build_lock()
        .lock()
        .unwrap_or_else(|poisoned| poisoned.into_inner());
    locate_or_build_cli_locked()
}

fn locate_or_build_cli_locked() -> Result<PathBuf, Error> {
    if let Some(p) = std::env::var_os("REN_EFFECTCRAFT_CLI") {
        let path = PathBuf::from(p);
        if path.is_file() {
            return Ok(path);
        }
    }
    let home = std::env::var_os("HOME").map(PathBuf::from).ok_or_else(|| {
        Error::Invalid("HOME is unset and REN_EFFECTCRAFT_CLI was not provided".into())
    })?;
    let root = home
        .join(".cache/ren/effectcraft")
        .join(PINNED_EFFECTCRAFT_REV);
    let source = root.join("source");
    let binary = source.join("target/release/effectcraft-cli");
    std::fs::create_dir_all(&root).map_err(io_err)?;
    if !source.join(".git").exists() {
        let staging = root.join(format!("source-{}", std::process::id()));
        if staging.exists() {
            std::fs::remove_dir_all(&staging).map_err(io_err)?;
        }
        let status = Command::new("git")
            .args([
                "clone",
                "--filter=blob:none",
                UPSTREAM,
                staging.to_string_lossy().as_ref(),
            ])
            .status()
            .map_err(io_err)?;
        if !status.success() {
            let _ = std::fs::remove_dir_all(&staging);
            if !source.join(".git").exists() {
                return Err(Error::Cli(
                    "failed to clone pinned EffectCraft source".into(),
                ));
            }
        } else {
            let status = Command::new("git")
                .current_dir(&staging)
                .args(["checkout", PINNED_EFFECTCRAFT_REV])
                .status()
                .map_err(io_err)?;
            if !status.success() {
                let _ = std::fs::remove_dir_all(&staging);
                return Err(Error::Cli(
                    "failed to check out pinned EffectCraft revision".into(),
                ));
            }
            if std::fs::rename(&staging, &source).is_err() {
                let _ = std::fs::remove_dir_all(&staging);
                if !source.join(".git").exists() {
                    return Err(Error::Cli(
                        "could not install pinned EffectCraft source".into(),
                    ));
                }
            }
        }
    }
    let status = Command::new("git")
        .current_dir(&source)
        .args(["checkout", PINNED_EFFECTCRAFT_REV])
        .status()
        .map_err(io_err)?;
    if !status.success() {
        return Err(Error::Cli(
            "failed to check out pinned EffectCraft revision".into(),
        ));
    }
    let cargo = std::env::var_os("CARGO")
        .map(PathBuf::from)
        .filter(|path| path.is_file())
        .or_else(|| {
            std::env::var_os("HOME")
                .map(PathBuf::from)
                .map(|home| home.join(".cargo/bin/cargo"))
                .filter(|path| path.is_file())
        })
        .unwrap_or_else(|| PathBuf::from("cargo"));
    let status = Command::new(cargo)
        .current_dir(&source)
        .args(["build", "--release", "-p", "effectcraft-cli", "-j", "4"])
        .status()
        .map_err(io_err)?;
    if !status.success() {
        return Err(Error::Cli("failed to build pinned EffectCraft CLI".into()));
    }
    if !binary.is_file() {
        return Err(Error::Cli(
            "EffectCraft build succeeded without its CLI binary".into(),
        ));
    }
    Ok(binary)
}
