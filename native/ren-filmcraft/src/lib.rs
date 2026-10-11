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
    /// The export render path the adapter actually requested. `Some` only
    /// for `export` receipts: `gpuRendering` was previously never sent, so
    /// the engine default (`Off`) always applied silently and a headless
    /// GPU fallback was invisible. Both fields are `Option` with a serde
    /// default so receipts written before this wiring still parse.
    #[serde(default)]
    pub hardware_encoding: Option<HardwareEncoding>,
    #[serde(default)]
    pub gpu_rendering: Option<GpuRendering>,
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
    /// Provision video/audio tracks beyond the three-plus-three a fresh
    /// project carries (`sequence.addTracks`). Planned by
    /// [`adapter::add_tracks_operation`] when a projection's overlay tracks
    /// need them; drive once per project after create, before place.
    AddTracks {
        project: PathBuf,
        video: u32,
        audio: u32,
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
    /// speed); `ripple` shifts followers exactly as ren-core's ripple does;
    /// `reverse` plays the source window backwards at that speed (measured:
    /// the flag persists on inspect and the render plays backwards).
    /// `clips` carries every engine clip of the retimed use (video plus
    /// linked audio) in one call: co-linked ids ripple once as a unit, while
    /// a razor severs the A/V link on later pieces, so those pieces are
    /// re-linked first (see `Operation::Link`). Answers `null`.
    Retime {
        project: PathBuf,
        clips: Vec<u64>,
        speed_percent: f64,
        ripple: bool,
        reverse: bool,
    },
    /// Re-link engine clips into one link group (`clip.link` toggle).
    /// A razor severs the A/V link on later pieces (measured distinct
    /// video/audio link ids), and a retime over severed ids ripples once per
    /// id instead of once. The toggle converges only through the measured
    /// sequence: mixed links clear to none (`linked:false`), unlinked clips
    /// gain a shared id (`linked:true`), and an intact pair unlinks - so a
    /// severed pair needs exactly two calls (clear, then link) while the
    /// intact first piece needs none. Answers `{"linked":bool}`.
    Link {
        project: PathBuf,
        clips: Vec<u64>,
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
    /// Source channel mapping (`clip.audioChannels`). `items` are engine
    /// source-item ids minted by the import receipt. With `format`/`clips`
    /// omitted this is a read-only query answering the current mapping as
    /// `{"items": [{"item": _, "format": _, "clips": _}]}`; with them set it regroups
    /// the source channels (measured: a 4x-mono MXF imports as stereo pairs
    /// `[[0,1],[2,3]]`, and `stereo [[0,0]]` maps the dialogue channel to
    /// both outputs for a dual-mono export matching Resolve).
    AudioChannels {
        project: PathBuf,
        items: Vec<u64>,
        format: Option<AudioChannelFormat>,
        clips: Option<Vec<Vec<u32>>>,
    },
    /// Per-clip gain in dB (`clip.audioGain`, mode `set`). The engine
    /// follows clip links (measured: gaining a video item writes the
    /// linked audio item's `gainDb`), so addressing the use's first
    /// reconciled id suffices; the receipt names the audio item that
    /// landed it. Mode is pinned to `set`: `adjust` is relative to
    /// whatever level the clip already carries, which no plan can name,
    /// and the normalize modes are unmeasured.
    ClipAudioGain {
        project: PathBuf,
        clips: Vec<u64>,
        gain_db: f64,
    },
    /// Grow the sequence's audio track count (`sequence.addTracks` with
    /// `video: 0`, so no stray video track lands). Fresh sequences carry
    /// three audio tracks (A1-A3); a stem plan addressing A4 or higher
    /// runs this first with the exact deficit. Read back the strips off
    /// `mixer.inspect`, not this receipt.
    AddAudioTracks {
        project: PathBuf,
        audio_tracks: u32,
    },
    /// Caption track creation (`captions.newTrack`). Answers `{"track": id}`.
    /// `format` is the engine vocabulary (`Subtitle`, `CEA-608`, ...).
    CreateCaptionTrack {
        project: PathBuf,
        format: String,
        name: Option<String>,
    },
    /// Caption placement (`captions.add`). `time_ticks`/`duration_ticks`
    /// are engine ticks on the sequence clock. Answers `{"caption": id}`.
    AddCaption {
        project: PathBuf,
        track: Option<String>,
        text: String,
        time_ticks: i64,
        duration_ticks: i64,
    },
    /// Caption text edit (`captions.setText`). Answers `null`.
    SetCaptionText {
        project: PathBuf,
        caption: u64,
        text: String,
        speaker: Option<String>,
    },
    /// Caption timing edit (`captions.setTimes`). Answers `null`; the
    /// receipt echoes the converted ticks as `{"start","end"}` through
    /// the raw engine response path (see `captions.list` for readback).
    SetCaptionTimes {
        project: PathBuf,
        caption: u64,
        start_ticks: i64,
        end_ticks: i64,
    },
    /// Read-only caption readback (`captions.list`). Answers
    /// `{"selection","tracks"}`; the adapter never mutates through it.
    ListCaptions {
        project: PathBuf,
        track: Option<String>,
    },
    /// Title overlay (`graphics.newText`), optionally bound to a clip.
    /// `time_ticks`/`duration_seconds` place it on the sequence clock;
    /// both store verbatim on a 24fps sequence (the qualification pins
    /// the rate with `SequenceSettings` first). Answers `{"clip","layer"}`.
    GraphicsNewText {
        project: PathBuf,
        text: String,
        clip: Option<u64>,
        time_ticks: Option<i64>,
        duration_seconds: Option<f64>,
    },
    /// LUT library import (`lut.import`). Answers `{"id","lut","name","ref"`;
    /// the `ref` (`lib:...` or builtin) is what `lumetri.setInputLut` takes.
    ImportLut {
        project: PathBuf,
        path: PathBuf,
        name: Option<String>,
    },
    /// LUT readback (`lut.list`). Answers `{"builtin","library"}`.
    ListLuts {
        project: PathBuf,
    },
    /// Lumetri input-LUT assignment (`lumetri.setInputLut`). Answers
    /// `{"clip","lut","name"}`.
    ApplyLut {
        project: PathBuf,
        clip: u64,
        lut: String,
    },
    /// Generic effect application (`effects.apply`). Answers `null`.
    /// Built-in `motion`/`opacity`/`lumetri` need no apply; `crop` does.
    ApplyEffect {
        project: PathBuf,
        clips: Vec<u64>,
        effect: String,
    },
    /// Enable per-parameter animation (`effects.toggleAnimation`).
    /// Answers `null`.
    ToggleEffectAnimation {
        project: PathBuf,
        clip: u64,
        effect: String,
        param: String,
    },
    /// Parameter write (`effects.setParam`), with optional timeline-tick
    /// `time` for a keyframe write. Answers `{"keyframes"}` with the
    /// count after the write. Out-of-span times
    /// are refused at planning time (see `keyframe_out_of_span`): the
    /// engine answers success while placing nothing.
    SetEffectParam {
        project: PathBuf,
        clip: u64,
        effect: String,
        param: String,
        value: serde_json::Value,
        time_ticks: Option<i64>,
    },
    /// Explicit keyframe add (`effects.addKeyframe`) at a timeline tick.
    /// Answers `null`. Same out-of-span refusal as `SetEffectParam`.
    AddEffectKeyframe {
        project: PathBuf,
        clip: u64,
        effect: String,
        param: String,
        time_ticks: i64,
    },
    /// Track volume keyframe (`mixer.setKeyframe`). Answers
    /// `{"time","value"}`.
    MixerSetKeyframe {
        project: PathBuf,
        strip: String,
        lane: String,
        time_ticks: i64,
        value: f64,
    },
    /// Track automation batch (`mixer.writeAutomation`). Answers
    /// `{"keyframes","points"}`.
    MixerWriteAutomation {
        project: PathBuf,
        strip: String,
        lane: String,
        points: Vec<(i64, f64)>,
    },
    /// Mixer readback (`mixer.inspect`). Answers the full mixer state
    /// including per-strip `lanes` automation.
    InspectMixer {
        project: PathBuf,
    },
    /// Sequence rate pin (`sequence.settings`). The model plans in engine
    /// ticks at the timeline rate; pinning the sequence to that rate
    /// (24.0 for the qualification clocks) keeps every tick readback
    /// verbatim instead of frame-floored onto an off-rate clock.
    /// Answers `{"scaledClips"}`.
    SequenceSettings {
        project: PathBuf,
        fps: f64,
    },
    Export {
        project: PathBuf,
        path: PathBuf,
        format: ExportFormat,
        hardware_encoder: HardwareEncoding,
        /// Requests serialized before the wiring name no compositor path;
        /// they parse as `Off`, the only executable headless value, so old
        /// exports keep planning while every new send names the path.
        #[serde(default)]
        gpu_rendering: GpuRendering,
        /// H264 video bitrate in kbps. `None` takes
        /// [`DEFAULT_H264_BITRATE_KBPS`]: Ren's delivery default, so an
        /// export that names no bitrate still ships at delivery size
        /// instead of the engine's hotter default (trial gap 4: 19 Mbps
        /// against Resolve's 10-11 Mbps at the same spec). Refused on
        /// non-H264 formats, where the engine silently ignores it (measured:
        /// a wav export accepts `bitrateKbps` and writes plain PCM).
        #[serde(default)]
        bitrate_kbps: Option<u32>,
        /// H264 rate control. `None` takes [`DEFAULT_BITRATE_MODE`].
        /// Refused on non-H264 formats, like `bitrate_kbps`.
        #[serde(default)]
        bitrate_mode: Option<BitrateMode>,
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
/// The engine compositor path (`GpuRendering` in the pinned engine's
/// `crates/export/src/settings.rs`). Headless `auto` never renders on a
/// GPU: the `export` subcommand falls back to the CPU reference path
/// silently (measured 2026-10-10: framehash-identical, zero fps gained),
/// while `exec file.exportMedia` (this adapter's path) panics the CLI
/// outright (wgpu, no backend feature - measured 2026-10-11). The adapter
/// sends this explicitly on every export and records it in the receipt
/// instead of letting the engine default apply unseen; `auto` refuses at
/// plan time until the exec path stops panicking.
#[derive(Clone, Copy, Debug, Default, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "lowercase")]
pub enum GpuRendering {
    #[default]
    Off,
    Auto,
}

/// Source channel grouping for `clip.audioChannels`, using the engine's own
/// spellings (`commands --json` on the pinned revision).
#[derive(Clone, Copy, Debug, Serialize, Deserialize, PartialEq, Eq)]
pub enum AudioChannelFormat {
    #[serde(rename = "mono")]
    Mono,
    #[serde(rename = "stereo")]
    Stereo,
    #[serde(rename = "5.1")]
    Surround51,
    #[serde(rename = "adaptive")]
    Adaptive,
}

/// H264 rate control, spelled exactly as the pinned engine's
/// `file.exportMedia` schema names it (`cbr|vbr1Pass|vbr2Pass`; measured via
/// `filmcraft-cli describe file.exportMedia`). Explicit renames rather than
/// `rename_all`, so an engine respelling fails the build instead of
/// silently sending a value the engine refuses.
#[derive(Clone, Copy, Debug, Serialize, Deserialize, PartialEq, Eq)]
pub enum BitrateMode {
    #[serde(rename = "cbr")]
    Cbr,
    #[serde(rename = "vbr1Pass")]
    Vbr1Pass,
    #[serde(rename = "vbr2Pass")]
    Vbr2Pass,
}

impl BitrateMode {
    fn as_engine_str(self) -> &'static str {
        match self {
            BitrateMode::Cbr => "cbr",
            BitrateMode::Vbr1Pass => "vbr1Pass",
            BitrateMode::Vbr2Pass => "vbr2Pass",
        }
    }
}

/// Ren's H264 delivery bitrate default, in kbps. Ren's delivery spec is
/// 1080x1920 H264 short-form (`DEFAULT_DELIVERY_SPEC` in
/// `library/tools/delivery_spec.py`), and the pinned engine's own preset
/// for that frame ("Social Vertical 1080x1920") targets 12 Mbps - beside
/// Resolve's measured 10-11 Mbps on the same frame (trial gap 4).
pub const DEFAULT_H264_BITRATE_KBPS: u32 = 12_000;
/// The engine's house H264 delivery mode (its 1080p/4K delivery presets are
/// VBR 1 pass).
pub const DEFAULT_BITRATE_MODE: BitrateMode = BitrateMode::Vbr1Pass;
/// Above the engine's 4K delivery presets (45-60 Mbps): refuses only the
/// absurd (a mistyped extra zero), never a real delivery target.
pub const MAX_H264_BITRATE_KBPS: u32 = 100_000;

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
        // Recorded on the receipt so a silently inert render path is
        // visible: which encoder AND which compositor the export asked for.
        let export_settings = match &request.operation {
            Operation::Export {
                hardware_encoder,
                gpu_rendering,
                ..
            } => Some((*hardware_encoder, *gpu_rendering)),
            _ => None,
        };
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
            hardware_encoding: export_settings.map(|(hardware, _)| hardware),
            gpu_rendering: export_settings.map(|(_, gpu)| gpu),
        })
    }
}

/// Describe one driver operation as the engine `exec` invocation that
/// runs it: the project it acts on, the receipt's operation name, the
/// engine command, its params, and whether the driver saves after it.
/// `execute` runs this mapping; the `ren project-model` verb plans
/// through it without executing, so the plan and the run cannot drift.
pub fn plan(op: Operation) -> Result<(Option<PathBuf>, String, &'static str, Value, bool)> {
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
        Operation::AddTracks {
            project,
            video,
            audio,
        } => {
            if video == 0 && audio == 0 {
                return Err(Error::Schema("track request adds no tracks".into()));
            }
            (
                Some(project),
                "add_tracks",
                "sequence.addTracks",
                json!({"video": video, "audio": audio}),
                true,
            )
        }
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
            // The engine names a sound's track once: a sound-only
            // placement whose strip equals its track carries no
            // `audioTrack` alongside `track` (measured refusal).
            if let Some(audio) = audio_track.filter(|strip| *strip != track) {
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
            reverse,
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
                json!({"clips":clips,"speed":speed_percent,"ripple":ripple,"reverse":reverse}),
                true,
            )
        }
        Operation::Link { project, clips } => {
            if clips.is_empty() {
                return Err(Error::Schema("link request names no clips".into()));
            }
            (
                Some(project),
                "link",
                "clip.link",
                json!({"clips":clips}),
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
        Operation::AudioChannels {
            project,
            items,
            format,
            clips,
        } => {
            if items.is_empty() {
                return Err(Error::Schema(
                    "audio channel mapping names no source items".into(),
                ));
            }
            if let Some(groups) = clips.as_ref() {
                if groups.is_empty() || groups.iter().any(Vec::is_empty) {
                    return Err(Error::Schema(
                        "audio channel mapping needs at least one group naming at least one channel (for example `[[0,0]]` for dual-mono)".into(),
                    ));
                }
            }
            let mut p = json!({"items": items});
            if let Some(format) = format {
                let name = match format {
                    AudioChannelFormat::Mono => "mono",
                    AudioChannelFormat::Stereo => "stereo",
                    AudioChannelFormat::Surround51 => "5.1",
                    AudioChannelFormat::Adaptive => "adaptive",
                };
                p["format"] = json!(name);
            }
            // The query form saves nothing: persisting a pure channel query
            // changes what a later `timeline.place` mints (measured: 3 clips
            // instead of 2 for a stereo-paired source), so only the regrouping
            // set form saves, like every other mutating operation.
            let saving = p.get("format").is_some() || clips.is_some();
            if let Some(groups) = clips {
                p["clips"] = json!(groups);
            }
            (
                Some(project),
                "audio_channels",
                "clip.audioChannels",
                p,
                saving,
            )
        }
        Operation::ClipAudioGain {
            project,
            clips,
            gain_db,
        } => {
            if clips.is_empty() {
                return Err(Error::Schema("clip gain request names no clips".into()));
            }
            if !gain_db.is_finite() {
                return Err(Error::Schema("clip gain must be a finite dB value".into()));
            }
            (
                Some(project),
                "clip_audio_gain",
                "clip.audioGain",
                json!({"clips":clips,"mode":"set","db":gain_db}),
                true,
            )
        }
        Operation::AddAudioTracks {
            project,
            audio_tracks,
        } => {
            if audio_tracks == 0 {
                return Err(Error::Schema(
                    "add-audio-tracks request names zero tracks".into(),
                ));
            }
            (
                Some(project),
                "add_audio_tracks",
                "sequence.addTracks",
                json!({"video":0,"audio":audio_tracks}),
                true,
            )
        }
        Operation::CreateCaptionTrack {
            project,
            format,
            name,
        } => {
            let mut p = json!({"format": format});
            if let Some(name) = name {
                p["name"] = json!(name);
            }
            (
                Some(project),
                "create_caption_track",
                "captions.newTrack",
                p,
                true,
            )
        }
        Operation::AddCaption {
            project,
            track,
            text,
            time_ticks,
            duration_ticks,
        } => {
            if time_ticks < 0 || duration_ticks <= 0 {
                return Err(Error::Schema(
                    "caption start must be at or after zero with positive duration".into(),
                ));
            }
            let mut p = json!({"text": text, "time": time_ticks, "durationSeconds": duration_ticks as f64 / adapter::ENGINE_TICKS_PER_SECOND as f64});
            if let Some(track) = track {
                p["track"] = json!(track);
            }
            (Some(project), "add_caption", "captions.add", p, true)
        }
        Operation::SetCaptionText {
            project,
            caption,
            text,
            speaker,
        } => {
            let mut p = json!({"caption": caption, "text": text});
            if let Some(speaker) = speaker {
                p["speaker"] = json!(speaker);
            }
            (
                Some(project),
                "set_caption_text",
                "captions.setText",
                p,
                true,
            )
        }
        Operation::SetCaptionTimes {
            project,
            caption,
            start_ticks,
            end_ticks,
        } => {
            if start_ticks < 0 || end_ticks <= start_ticks {
                return Err(Error::Schema(
                    "caption times must satisfy 0 <= start < end".into(),
                ));
            }
            (
                Some(project),
                "set_caption_times",
                "captions.setTimes",
                json!({"caption": caption, "startTime": start_ticks, "endTime": end_ticks}),
                true,
            )
        }
        Operation::ListCaptions { project, track } => {
            let mut p = json!({});
            if let Some(track) = track {
                p["track"] = json!(track);
            }
            (Some(project), "list_captions", "captions.list", p, false)
        }
        Operation::GraphicsNewText {
            project,
            text,
            clip,
            time_ticks,
            duration_seconds,
        } => {
            if time_ticks.is_some_and(|t| t < 0) {
                return Err(Error::Schema("title time must be at or after zero".into()));
            }
            let mut p = json!({"text": text});
            if let Some(clip) = clip {
                p["clip"] = json!(clip);
            }
            if let Some(t) = time_ticks {
                p["time"] = json!(t);
            }
            if let Some(seconds) = duration_seconds {
                if !seconds.is_finite() || seconds <= 0.0 {
                    return Err(Error::Schema(
                        "title duration must be positive seconds".into(),
                    ));
                }
                p["seconds"] = json!(seconds);
            }
            (
                Some(project),
                "graphics_new_text",
                "graphics.newText",
                p,
                true,
            )
        }
        Operation::ImportLut {
            project,
            path,
            name,
        } => {
            let mut p = json!({"path": path});
            if let Some(name) = name {
                p["name"] = json!(name);
            }
            (Some(project), "import_lut", "lut.import", p, true)
        }
        Operation::ListLuts { project } => {
            (Some(project), "list_luts", "lut.list", json!({}), false)
        }
        Operation::ApplyLut { project, clip, lut } => {
            if lut.trim().is_empty() {
                return Err(Error::Schema("LUT reference must not be empty".into()));
            }
            (
                Some(project),
                "apply_lut",
                "lumetri.setInputLut",
                json!({"clip": clip, "lut": lut}),
                true,
            )
        }
        Operation::ApplyEffect {
            project,
            clips,
            effect,
        } => {
            if clips.is_empty() {
                return Err(Error::Schema("effect application names no clips".into()));
            }
            if effect.trim().is_empty() {
                return Err(Error::Schema("effect name must not be empty".into()));
            }
            (
                Some(project),
                "apply_effect",
                "effects.apply",
                json!({"clips": clips, "effect": effect}),
                true,
            )
        }
        Operation::ToggleEffectAnimation {
            project,
            clip,
            effect,
            param,
        } => (
            Some(project),
            "toggle_effect_animation",
            "effects.toggleAnimation",
            json!({"clip": clip, "effect": effect, "param": param}),
            true,
        ),
        Operation::SetEffectParam {
            project,
            clip,
            effect,
            param,
            value,
            time_ticks,
        } => {
            if time_ticks.is_some_and(|t| t < 0) {
                return Err(Error::Schema(
                    "effect keyframe time must be at or after zero".into(),
                ));
            }
            let mut p = json!({"clip": clip, "effect": effect, "param": param, "value": value});
            if let Some(t) = time_ticks {
                p["time"] = json!(t);
            }
            (
                Some(project),
                "set_effect_param",
                "effects.setParam",
                p,
                true,
            )
        }
        Operation::AddEffectKeyframe {
            project,
            clip,
            effect,
            param,
            time_ticks,
        } => {
            if time_ticks < 0 {
                return Err(Error::Schema(
                    "effect keyframe time must be at or after zero".into(),
                ));
            }
            (
                Some(project),
                "add_effect_keyframe",
                "effects.addKeyframe",
                json!({"clip": clip, "effect": effect, "param": param, "time": time_ticks}),
                true,
            )
        }
        Operation::MixerSetKeyframe {
            project,
            strip,
            lane,
            time_ticks,
            value,
        } => {
            if time_ticks < 0 {
                return Err(Error::Schema(
                    "mixer keyframe time must be at or after zero".into(),
                ));
            }
            if !value.is_finite() {
                return Err(Error::Schema("mixer keyframe value must be finite".into()));
            }
            (
                Some(project),
                "mixer_set_keyframe",
                "mixer.setKeyframe",
                json!({"strip": strip, "lane": lane, "time": time_ticks, "value": value}),
                true,
            )
        }
        Operation::MixerWriteAutomation {
            project,
            strip,
            lane,
            points,
        } => {
            if points.is_empty() {
                return Err(Error::Schema("automation write names no points".into()));
            }
            for (ticks, value) in &points {
                if *ticks < 0 {
                    return Err(Error::Schema(
                        "automation point time must be at or after zero".into(),
                    ));
                }
                if !value.is_finite() {
                    return Err(Error::Schema(
                        "automation point value must be finite".into(),
                    ));
                }
            }
            let pairs: Vec<Value> = points
                .iter()
                .map(|(ticks, value)| json!([ticks, value]))
                .collect();
            (
                Some(project),
                "mixer_write_automation",
                "mixer.writeAutomation",
                json!({"strip": strip, "lane": lane, "points": pairs}),
                true,
            )
        }
        Operation::InspectMixer { project } => (
            Some(project),
            "inspect_mixer",
            "mixer.inspect",
            json!({}),
            false,
        ),
        Operation::SequenceSettings { project, fps } => {
            if !fps.is_finite() || fps <= 0.0 {
                return Err(Error::Schema("sequence fps must be a positive rate".into()));
            }
            (
                Some(project),
                "sequence_settings",
                "sequence.settings",
                json!({"fps": fps}),
                true,
            )
        }
        Operation::Export {
            project,
            path,
            format,
            hardware_encoder,
            gpu_rendering,
            bitrate_kbps,
            bitrate_mode,
        } => {
            // Measured 2026-10-11 on the pinned revision: `gpuRendering:auto`
            // through `file.exportMedia` (this path) panics the headless CLI
            // (`wgpu: No wgpu backend feature ... was enabled`), while the
            // `export` subcommand's `--gpu-rendering auto` falls back
            // silently. Refuse before any engine call rather than forward a
            // process-killing panic; `off` preserves the previous default,
            // now sent explicitly so the receipt shows the render path.
            if gpu_rendering == GpuRendering::Auto {
                return Err(Error::Schema(
                    "FilmCraft `gpuRendering:auto` panics the pinned headless CLI via `file.exportMedia` (wgpu has no backend feature in this build); the `export` subcommand falls back silently instead - request `off` until the exec path matches the subcommand"
                        .into(),
                ));
            }
            if !matches!(format, ExportFormat::H264)
                && (bitrate_kbps.is_some() || bitrate_mode.is_some())
            {
                return Err(Error::Schema(
                    "bitrate_kbps/bitrate_mode apply to H264 only: ProRes bitrate is profile-determined and Wav is PCM, and the engine silently ignores the parameters there".into(),
                ));
            }
            let mut params = json!({"path":path,"format":format_name(format),"hardwareEncoding":hardware_encoder,"gpuRendering":gpu_rendering,"wait":true});
            if matches!(format, ExportFormat::H264) {
                let kbps = bitrate_kbps.unwrap_or(DEFAULT_H264_BITRATE_KBPS);
                if kbps == 0 || kbps > MAX_H264_BITRATE_KBPS {
                    return Err(Error::Schema(format!(
                        "H264 bitrate must be 1..={} kbps, got {kbps}; the engine silently accepts 0 as its own default, so 0 refuses here",
                        MAX_H264_BITRATE_KBPS
                    )));
                }
                let mode = bitrate_mode.unwrap_or(DEFAULT_BITRATE_MODE);
                params["bitrateKbps"] = json!(kbps);
                params["bitrateMode"] = json!(mode.as_engine_str());
            }
            (Some(project), "export", "file.exportMedia", params, false)
        }
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
            "open_project",
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
    // Null-answering commands accept exactly null. `captions.setText`
    // answers null; `effects.apply`, `effects.toggleAnimation`,
    // `effects.addKeyframe` and `effects.setKeyframe` answer null as well.
    // `captions.setTimes` echoes its converted `{"start","end"}` ticks and
    // `effects.setParam` echoes its `{"keyframes"}` count instead.
    if matches!(
        command,
        "edit.clear"
            | "edit.rippleDelete"
            | "clip.speedDuration"
            | "captions.setText"
            | "effects.apply"
            | "effects.toggleAnimation"
            | "effects.addKeyframe"
            | "effects.setKeyframe"
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
    // Clip gain answers `{"clips":n,"gainDb":[{"clip","gainDb"}],"mode":"set"}`:
    // the receipt names the audio item that landed the level, which is how
    // the equivalence proof ties the plan back to the use (measured: gaining
    // a linked video item reports its audio item here).
    if command == "clip.audioGain" {
        let object = value.as_object().ok_or_else(|| Error::UnknownResponse {
            command: command.into(),
            detail: "expected JSON object".into(),
        })?;
        if object.get("clips").and_then(Value::as_u64).is_none() {
            return Err(Error::UnknownResponse {
                command: command.into(),
                detail: "missing integer `clips`".into(),
            });
        }
        if value["mode"].as_str() != Some("set") {
            return Err(Error::UnknownResponse {
                command: command.into(),
                detail: "expected `mode` \"set\"".into(),
            });
        }
        let landed = value["gainDb"]
            .as_array()
            .ok_or_else(|| Error::UnknownResponse {
                command: command.into(),
                detail: "missing `gainDb` array".into(),
            })?;
        if landed.iter().any(|entry| {
            entry.get("clip").and_then(Value::as_u64).is_none()
                || entry.get("gainDb").and_then(Value::as_f64).is_none()
        }) {
            return Err(Error::UnknownResponse {
                command: command.into(),
                detail: "`gainDb` entries must carry integer `clip` and numeric `gainDb`".into(),
            });
        }
        return Ok(());
    }
    // Track growth answers the minted track ids per family (`video` always;
    // `audio`/`submix` ride along when the call grew those families); the
    // strips themselves read back off `mixer.inspect`. The general tables
    // below govern: `video` required, unknown fields refused.
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
        "sequence.addTracks" => &["video", "audio", "submix"],
        "timeline.place" => &["clips"],
        "timeline.razor" => &["cuts"],
        "timeline.trim" => &["delta"],
        "timeline.move" => &["moved", "overwritten"],
        "clip.link" => &["linked"],
        "sequence.applyVideoTransition" => &["transition"],
        "mixer.setValue" => &["value"],
        "clip.audioChannels" => &["items"],
        "captions.newTrack" => &["track"],
        "captions.add" => &["caption"],
        "captions.setTimes" => &["start", "end"],
        "captions.list" => &["selection", "tracks"],
        "graphics.newText" => &["clip", "layer"],
        "lut.import" => &["id", "lut", "name", "ref"],
        "lut.list" => &["builtin", "library"],
        "lumetri.setInputLut" => &["clip", "lut", "name"],
        "effects.setParam" => &["keyframes"],
        "mixer.setKeyframe" => &["time", "value"],
        "mixer.writeAutomation" => &["keyframes", "points"],
        "mixer.inspect" => &[
            "automatchTime",
            "clipModes",
            "gestures",
            "latencySamples",
            "recording",
            "strips",
        ],
        "sequence.settings" => &["scaledClips"],
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
        "sequence.addTracks" => &["video"],
        "timeline.place" => &["clips"],
        "timeline.razor" => &["cuts"],
        "timeline.trim" => &["delta"],
        "timeline.move" => &["moved", "overwritten"],
        "clip.link" => &["linked"],
        "sequence.applyVideoTransition" => &["transition"],
        "mixer.setValue" => &["value"],
        "clip.audioChannels" => &["items"],
        "captions.newTrack" => &["track"],
        "captions.add" => &["caption"],
        "captions.setTimes" => &["start", "end"],
        "captions.list" => &["tracks"],
        "graphics.newText" => &["clip"],
        "lut.import" => &["ref"],
        "lut.list" => &["builtin", "library"],
        "lumetri.setInputLut" => &["clip", "lut"],
        "effects.setParam" => &["keyframes"],
        "mixer.setKeyframe" => &["time", "value"],
        "mixer.writeAutomation" => &["keyframes", "points"],
        "mixer.inspect" => &["strips"],
        "sequence.settings" => &["scaledClips"],
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
        "sequence.addTracks" => {
            value["video"].is_array()
                && value.get("audio").is_none_or(Value::is_array)
                && value.get("submix").is_none_or(Value::is_array)
        }
        "timeline.place" => value["clips"]
            .as_array()
            .is_some_and(|v| v.iter().all(Value::is_u64)),
        "timeline.razor" => value["cuts"].is_u64(),
        "timeline.trim" => value["delta"].is_i64(),
        "timeline.move" => value["moved"].is_array() && value["overwritten"].is_array(),
        "clip.link" => value["linked"].is_boolean(),
        "sequence.applyVideoTransition" => value["transition"].is_u64(),
        "mixer.setValue" => value["value"].is_number(),
        "clip.audioChannels" => value["items"].is_array(),
        "captions.newTrack" => value["track"].is_u64(),
        "captions.add" => value["caption"].is_u64(),
        "captions.setTimes" => value["start"].is_i64() && value["end"].is_i64(),
        "captions.list" => value["tracks"].is_array(),
        "graphics.newText" => value["clip"].is_u64(),
        "lut.import" => value["ref"].is_string(),
        "lut.list" => value["builtin"].is_array() && value["library"].is_array(),
        "lumetri.setInputLut" => value["clip"].is_u64() && value["lut"].is_string(),
        "effects.setParam" => value["keyframes"].is_u64(),
        "mixer.setKeyframe" => value["time"].is_i64() && value["value"].is_number(),
        "mixer.writeAutomation" => value["keyframes"].is_u64() && value["points"].is_u64(),
        "mixer.inspect" => value["strips"].is_array(),
        "sequence.settings" => value["scaledClips"].is_u64(),
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
    let overwritten = response["overwritten"]
        .as_array()
        .ok_or_else(|| Error::UnknownResponse {
            command: "timeline.move".into(),
            detail: "missing `overwritten` array".into(),
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
                reverse: false,
            },
            Operation::Link {
                project: "x.fcproj".into(),
                clips: vec![],
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
                reverse: false,
            };
            assert!(
                matches!(plan(operation), Err(Error::Schema(_))),
                "speed {speed} must refuse"
            );
        }
        // The null-answering commands accept exactly null.
        for command in [
            "edit.clear",
            "edit.rippleDelete",
            "clip.speedDuration",
            "captions.setText",
            "effects.apply",
            "effects.toggleAnimation",
            "effects.addKeyframe",
            "effects.setKeyframe",
        ] {
            assert!(validate_response_contract(command, &Value::Null).is_ok());
            assert!(validate_response_contract(command, &json!({})).is_err());
        }
        // `effects.setParam` echoes its post-write keyframe count.
        assert!(validate_response_contract("effects.setParam", &json!({"keyframes": 2})).is_ok());
        assert!(validate_response_contract("effects.setParam", &Value::Null).is_err());
        // The echo-answering commands accept their measured shapes.
        assert!(validate_response_contract("captions.newTrack", &json!({"track": 8})).is_ok());
        assert!(validate_response_contract("captions.add", &json!({"caption": 9})).is_ok());
        assert!(
            validate_response_contract("captions.setTimes", &json!({"start": 0, "end": 1})).is_ok()
        );
        assert!(validate_response_contract(
            "captions.list",
            &json!({"selection": [], "tracks": []})
        )
        .is_ok());
        assert!(
            validate_response_contract("graphics.newText", &json!({"clip": 13, "layer": 0}))
                .is_ok()
        );
        assert!(validate_response_contract(
            "lut.import",
            &json!({"id": "lut12", "lut": {}, "name": "x", "ref": "lib:lut12"})
        )
        .is_ok());
        assert!(
            validate_response_contract("lut.list", &json!({"builtin": [], "library": []})).is_ok()
        );
        assert!(validate_response_contract(
            "lumetri.setInputLut",
            &json!({"clip": 10, "lut": "builtin:x", "name": "X"})
        )
        .is_ok());
        assert!(validate_response_contract(
            "mixer.setKeyframe",
            &json!({"time": 0, "value": -3.0})
        )
        .is_ok());
        assert!(validate_response_contract(
            "mixer.writeAutomation",
            &json!({"keyframes": 2, "points": 2})
        )
        .is_ok());
        assert!(validate_response_contract("mixer.inspect", &json!({"strips": []})).is_ok());
        assert!(
            validate_response_contract("sequence.settings", &json!({"scaledClips": 0})).is_ok()
        );
        // The link toggle answers exactly `{"linked":bool}`.
        for linked in [true, false] {
            assert!(validate_response_contract("clip.link", &json!({"linked": linked})).is_ok());
        }
        assert!(validate_response_contract("clip.link", &Value::Null).is_err());
        assert!(validate_response_contract("clip.link", &json!({})).is_err());
        // A move that trims only moved clips (sub-frame normalization)
        // is clean; trimming an occupant outside the moved set is a loss.
        let clean = json!({"moved": [7], "overwritten": []});
        assert!(detect_move_loss(&clean).unwrap().is_empty());
        let normalized = json!({"moved": [10, 11], "overwritten": [{"clip": 10, "was": 762810048000u64, "now": 762048000000u64}]});
        assert!(detect_move_loss(&normalized).unwrap().is_empty());
        let dirty = json!({"moved": [13, 14], "overwritten": [{"clip": 10}]});
        let losses = detect_move_loss(&dirty).unwrap();
        assert_eq!(losses.len(), 1);
        assert_eq!(losses[0].code, "move_overwrote");
    }

    #[test]
    fn export_sends_gpu_rendering_explicitly_and_records_the_render_path() {
        // `gpuRendering` rides top-level beside `hardwareEncoding` (both
        // validated by the pinned engine as `off|auto`); the receipt keeps
        // both so a silently inert render path stays visible.
        for (hardware, gpu) in [
            (HardwareEncoding::Off, GpuRendering::Off),
            (HardwareEncoding::Auto, GpuRendering::Off),
        ] {
            let (_, operation, command, params, _) = plan(Operation::Export {
                project: "x.fcproj".into(),
                path: "out.mp4".into(),
                format: ExportFormat::H264,
                hardware_encoder: hardware,
                gpu_rendering: gpu,
                bitrate_kbps: None,
                bitrate_mode: None,
            })
            .unwrap();
            assert_eq!(operation, "export");
            assert_eq!(command, "file.exportMedia");
            let expected_hardware = match hardware {
                HardwareEncoding::Off => "off",
                HardwareEncoding::Auto => "auto",
            };
            let expected_gpu = match gpu {
                GpuRendering::Off => "off",
                GpuRendering::Auto => "auto",
            };
            assert_eq!(params["hardwareEncoding"].as_str(), Some(expected_hardware));
            assert_eq!(params["gpuRendering"].as_str(), Some(expected_gpu));
        }
        // Receipts written before this wiring carry neither field and must
        // still parse (both default to `None`).
        let legacy: Receipt = serde_json::from_str(
            r#"{"adapter_schema":1,"engine_revision":"x","project_schema":14,"operation":"export","engine_response":{"job":1,"path":"o"},"losses":[]}"#,
        )
        .unwrap();
        assert_eq!(legacy.hardware_encoding, None);
        assert_eq!(legacy.gpu_rendering, None);
        let current: Receipt = serde_json::from_str(
            r#"{"adapter_schema":1,"engine_revision":"x","project_schema":14,"operation":"export","engine_response":{"job":1,"path":"o"},"losses":[],"hardware_encoding":"auto","gpu_rendering":"off"}"#,
        )
        .unwrap();
        assert_eq!(current.hardware_encoding, Some(HardwareEncoding::Auto));
        assert_eq!(current.gpu_rendering, Some(GpuRendering::Off));
    }

    #[test]
    fn export_refuses_gpu_auto_before_the_engine() {
        // Measured 2026-10-11: `gpuRendering:auto` via `file.exportMedia`
        // panics the pinned headless CLI (wgpu, no backend feature), while
        // the `export` subcommand falls back silently. Refuse here, before
        // any engine call, rather than forward the panic.
        let err = plan(Operation::Export {
            project: "x.fcproj".into(),
            path: "out.mp4".into(),
            format: ExportFormat::H264,
            hardware_encoder: HardwareEncoding::Auto,
            gpu_rendering: GpuRendering::Auto,
            bitrate_kbps: None,
            bitrate_mode: None,
        })
        .unwrap_err();
        assert!(matches!(err, Error::Schema(_)), "got {err:?}");
    }

    #[test]
    fn caption_lut_keyframe_and_automation_validate_before_the_engine() {
        // Negative times, empty spans and empty points refuse.
        for operation in [
            Operation::AddCaption {
                project: "x.fcproj".into(),
                track: None,
                text: "hi".into(),
                time_ticks: -1,
                duration_ticks: 10,
            },
            Operation::AddCaption {
                project: "x.fcproj".into(),
                track: None,
                text: "hi".into(),
                time_ticks: 0,
                duration_ticks: 0,
            },
            Operation::SetCaptionTimes {
                project: "x.fcproj".into(),
                caption: 9,
                start_ticks: 10,
                end_ticks: 10,
            },
            Operation::SetEffectParam {
                project: "x.fcproj".into(),
                clip: 7,
                effect: "motion".into(),
                param: "position".into(),
                value: json!([0.0, 0.0]),
                time_ticks: Some(-1),
            },
            Operation::AddEffectKeyframe {
                project: "x.fcproj".into(),
                clip: 7,
                effect: "motion".into(),
                param: "position".into(),
                time_ticks: -1,
            },
            Operation::MixerSetKeyframe {
                project: "x.fcproj".into(),
                strip: "A1".into(),
                lane: "volume".into(),
                time_ticks: -1,
                value: -3.0,
            },
            Operation::MixerWriteAutomation {
                project: "x.fcproj".into(),
                strip: "A1".into(),
                lane: "volume".into(),
                points: vec![],
            },
            Operation::ApplyEffect {
                project: "x.fcproj".into(),
                clips: vec![],
                effect: "crop".into(),
            },
            Operation::ApplyLut {
                project: "x.fcproj".into(),
                clip: 7,
                lut: "  ".into(),
            },
            Operation::SequenceSettings {
                project: "x.fcproj".into(),
                fps: 0.0,
            },
        ] {
            assert!(matches!(plan(operation), Err(Error::Schema(_))));
        }
        // Non-finite automation values and title durations refuse.
        for operation in [
            Operation::MixerSetKeyframe {
                project: "x.fcproj".into(),
                strip: "A1".into(),
                lane: "volume".into(),
                time_ticks: 0,
                value: f64::INFINITY,
            },
            Operation::MixerWriteAutomation {
                project: "x.fcproj".into(),
                strip: "A1".into(),
                lane: "volume".into(),
                points: vec![(0, f64::NAN)],
            },
            Operation::GraphicsNewText {
                project: "x.fcproj".into(),
                text: "T".into(),
                clip: None,
                time_ticks: None,
                duration_seconds: Some(0.0),
            },
        ] {
            assert!(matches!(plan(operation), Err(Error::Schema(_))));
        }
    }

    #[test]
    fn clip_gain_and_track_growth_validate_before_the_engine() {
        // Empty clip lists and zero-track growth refuse without a CLI call.
        assert!(matches!(
            plan(Operation::ClipAudioGain {
                project: "x.fcproj".into(),
                clips: vec![],
                gain_db: -6.0,
            }),
            Err(Error::Schema(_))
        ));
        assert!(matches!(
            plan(Operation::AddAudioTracks {
                project: "x.fcproj".into(),
                audio_tracks: 0,
            }),
            Err(Error::Schema(_))
        ));
        // Non-finite gains refuse.
        for gain in [f64::NAN, f64::INFINITY, f64::NEG_INFINITY] {
            assert!(
                matches!(
                    plan(Operation::ClipAudioGain {
                        project: "x.fcproj".into(),
                        clips: vec![14],
                        gain_db: gain,
                    }),
                    Err(Error::Schema(_))
                ),
                "gain {gain} must refuse"
            );
        }
        // Clip gain pins mode `set`: the plan names an absolute level.
        let (_, operation, command, params, saving) = plan(Operation::ClipAudioGain {
            project: "x.fcproj".into(),
            clips: vec![14],
            gain_db: -12.0,
        })
        .unwrap();
        assert_eq!(operation, "clip_audio_gain");
        assert_eq!(command, "clip.audioGain");
        assert_eq!(params, json!({"clips": [14], "mode": "set", "db": -12.0}));
        assert!(saving);
        // Track growth adds audio only: a stray video track would shift
        // every V-row the projection planned.
        let (_, operation, command, params, _) = plan(Operation::AddAudioTracks {
            project: "x.fcproj".into(),
            audio_tracks: 2,
        })
        .unwrap();
        assert_eq!(operation, "add_audio_tracks");
        assert_eq!(command, "sequence.addTracks");
        assert_eq!(params, json!({"video": 0, "audio": 2}));
        // The pinned receipt shapes accept exactly what the engine answers.
        assert!(validate_response_contract(
            "clip.audioGain",
            &json!({"clips": 1, "gainDb": [{"clip": 14, "gainDb": -12.0}], "mode": "set"}),
        )
        .is_ok());
        for bad in [
            json!({"clips": 1, "gainDb": [], "mode": "adjust"}),
            json!({"clips": 1, "mode": "set"}),
            json!({"gainDb": [], "mode": "set"}),
            Value::Null,
        ] {
            assert!(
                validate_response_contract("clip.audioGain", &bad).is_err(),
                "gain receipt {bad} must refuse"
            );
        }
        assert!(validate_response_contract(
            "sequence.addTracks",
            &json!({"audio": [17, 18], "submix": [], "video": []}),
        )
        .is_ok());
        assert!(validate_response_contract(
            "sequence.addTracks",
            &json!({"audio": [], "submix": []}),
        )
        .is_err());
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
    fn export_bitrate_defaults_validate_and_scope_to_h264() {
        let h264 = |kbps: Option<u32>, mode: Option<BitrateMode>| Operation::Export {
            project: "x.fcproj".into(),
            path: "out.mp4".into(),
            format: ExportFormat::H264,
            hardware_encoder: HardwareEncoding::Auto,
            gpu_rendering: GpuRendering::Off,
            bitrate_kbps: kbps,
            bitrate_mode: mode,
        };
        // Omitted bitrate defaults to Ren's delivery spec, not the
        // engine's hotter default.
        let (_, _, command, params, _) = plan(h264(None, None)).unwrap();
        assert_eq!(command, "file.exportMedia");
        assert_eq!(params["bitrateKbps"], json!(DEFAULT_H264_BITRATE_KBPS));
        assert_eq!(params["bitrateMode"], json!("vbr1Pass"));
        // Explicit values pass through spelled as the engine names them.
        let (_, _, _, params, _) = plan(h264(Some(10_000), Some(BitrateMode::Cbr))).unwrap();
        assert_eq!(params["bitrateKbps"], json!(10_000));
        assert_eq!(params["bitrateMode"], json!("cbr"));
        let (_, _, _, params, _) = plan(h264(Some(15_000), Some(BitrateMode::Vbr2Pass))).unwrap();
        assert_eq!(params["bitrateMode"], json!("vbr2Pass"));
        // Zero refuses: the engine silently accepts it as its own default.
        assert!(matches!(plan(h264(Some(0), None)), Err(Error::Schema(_))));
        // The absurd refuses; the top of the real delivery range passes.
        assert!(matches!(
            plan(h264(Some(MAX_H264_BITRATE_KBPS + 1), None)),
            Err(Error::Schema(_))
        ));
        assert!(plan(h264(Some(MAX_H264_BITRATE_KBPS), None)).is_ok());
        // Bitrate on ProRes/Wav refuses: the engine silently ignores it.
        for format in [ExportFormat::ProRes, ExportFormat::Wav] {
            let operation = Operation::Export {
                project: "x.fcproj".into(),
                path: "out".into(),
                format,
                hardware_encoder: HardwareEncoding::Off,
                gpu_rendering: GpuRendering::Off,
                bitrate_kbps: Some(12_000),
                bitrate_mode: None,
            };
            assert!(matches!(plan(operation), Err(Error::Schema(_))));
            let operation = Operation::Export {
                project: "x.fcproj".into(),
                path: "out".into(),
                format,
                hardware_encoder: HardwareEncoding::Off,
                gpu_rendering: GpuRendering::Off,
                bitrate_kbps: None,
                bitrate_mode: Some(BitrateMode::Cbr),
            };
            assert!(matches!(plan(operation), Err(Error::Schema(_))));
        }
        // And the same formats without bitrate send no bitrate keys.
        for format in [ExportFormat::ProRes, ExportFormat::Wav] {
            let operation = Operation::Export {
                project: "x.fcproj".into(),
                path: "out".into(),
                format,
                hardware_encoder: HardwareEncoding::Off,
                gpu_rendering: GpuRendering::Off,
                bitrate_kbps: None,
                bitrate_mode: None,
            };
            let (_, _, _, params, _) = plan(operation).unwrap();
            assert!(params.get("bitrateKbps").is_none());
            assert!(params.get("bitrateMode").is_none());
        }
        // Previously serialized exports (no bitrate keys) still parse,
        // taking the delivery default.
        let legacy: Request<Operation> = serde_json::from_str(
            r#"{"adapter_schema":1,"operation":{"kind":"export","project":"x.fcproj","path":"o.mp4","format":"h264","hardware_encoder":"auto"}}"#,
        )
        .unwrap();
        assert!(matches!(
            legacy.operation,
            Operation::Export {
                bitrate_kbps: None,
                bitrate_mode: None,
                ..
            }
        ));
        // The mode enum spells exactly what the engine accepts.
        assert_eq!(
            serde_json::to_value(BitrateMode::Vbr1Pass).unwrap(),
            json!("vbr1Pass")
        );
        assert!(serde_json::from_str::<BitrateMode>(r#""bogus""#).is_err());
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

    #[test]
    fn add_tracks_validates_and_contracts_before_the_engine() {
        // An empty track request refuses without a CLI call.
        let operation = Operation::AddTracks {
            project: "x.fcproj".into(),
            video: 0,
            audio: 0,
        };
        assert!(matches!(plan(operation), Err(Error::Schema(_))));
        // A real request plans onto `sequence.addTracks`.
        let operation = Operation::AddTracks {
            project: "x.fcproj".into(),
            video: 2,
            audio: 0,
        };
        assert!(plan(operation).is_ok());
        // The pinned response shape (measured: `{"video":[9],...}`).
        assert!(validate_response_contract(
            "sequence.addTracks",
            &json!({"video": [9], "audio": [], "submix": []}),
        )
        .is_ok());
        assert!(validate_response_contract("sequence.addTracks", &json!({"video": [9]}),).is_ok());
        assert!(validate_response_contract("sequence.addTracks", &json!({"audio": []}),).is_err());
        assert!(validate_response_contract(
            "sequence.addTracks",
            &json!({"video": [9], "future": true}),
        )
        .is_err());
    }

    #[test]
    fn audio_channels_operations_validate_before_the_engine() {
        // Empty item lists and empty channel groups refuse without a CLI call.
        for operation in [
            Operation::AudioChannels {
                project: "x.fcproj".into(),
                items: vec![],
                format: Some(AudioChannelFormat::Stereo),
                clips: Some(vec![vec![0, 0]]),
            },
            Operation::AudioChannels {
                project: "x.fcproj".into(),
                items: vec![8],
                format: Some(AudioChannelFormat::Stereo),
                clips: Some(vec![]),
            },
            Operation::AudioChannels {
                project: "x.fcproj".into(),
                items: vec![8],
                format: Some(AudioChannelFormat::Mono),
                clips: Some(vec![vec![]]),
            },
        ] {
            assert!(matches!(plan(operation), Err(Error::Schema(_))));
        }
        // The query form (no format, no clips) plans: it reads the current
        // mapping back rather than regrouping channels.
        let (_, operation, command, params, saving) = plan(Operation::AudioChannels {
            project: "x.fcproj".into(),
            items: vec![8],
            format: None,
            clips: None,
        })
        .unwrap();
        assert_eq!(operation, "audio_channels");
        assert_eq!(command, "clip.audioChannels");
        assert_eq!(params, json!({"items": [8]}));
        assert!(!saving);
        // The dual-mono set form carries the engine's own spellings,
        // including `5.1` for surround.
        let (_, _, _, params, saving) = plan(Operation::AudioChannels {
            project: "x.fcproj".into(),
            items: vec![8],
            format: Some(AudioChannelFormat::Stereo),
            clips: Some(vec![vec![0, 0]]),
        })
        .unwrap();
        assert_eq!(
            params,
            json!({"items": [8], "format": "stereo", "clips": [[0, 0]]})
        );
        assert!(saving);
        let (_, _, _, params, _) = plan(Operation::AudioChannels {
            project: "x.fcproj".into(),
            items: vec![8],
            format: Some(AudioChannelFormat::Surround51),
            clips: None,
        })
        .unwrap();
        assert_eq!(params, json!({"items": [8], "format": "5.1"}));
        // The query/set answers carry exactly `{"items": [...]}`.
        assert!(validate_response_contract(
            "clip.audioChannels",
            &json!({"items": [{"item": 8, "format": "Stereo", "clips": [[0, 1], [2, 3]]}]}),
        )
        .is_ok());
        assert!(validate_response_contract("clip.audioChannels", &json!({})).is_err());
        assert!(validate_response_contract(
            "clip.audioChannels",
            &json!({"items": [], "extra": 1})
        )
        .is_err());
    }
}
