//! The project half of the native verbs: locating a project, loading its
//! `project.yaml` through the schema, and reading its on-disk status.
//!
//! Every function here mirrors one Python function the command used to
//! run, so the native verb's output is byte-identical to the Python
//! implementation's. Where the Python reaches a part this crate does not
//! port (a format migration, the safe-zone layout engine), the command
//! falls back to execing Python rather than drifting - see `commands.rs`.

use std::env;
use std::fs;
use std::path::{Path, PathBuf};

use serde_yaml::Value as Yaml;

use crate::engine;
use crate::json::Json;

const FORMAT_VERSION_KEY: &str = "project_format_version";
const PROJECT_FORMAT_VERSION: i64 = 2;
const MIN_SUPPORTED_FORMAT_VERSION: i64 = 0;

const STATUS_VALUES: &[&str] =
    &["draft", "in_progress", "review", "complete", "archived"];

const DELIVERY_FORMATS: &[&str] = &[
    "vertical_1080x1920",
    "vertical_2160x3840",
    "square_1080x1080",
    "horizontal_1920x1080",
];
const GEOMETRIES: &[&str] = &["full", "tight"];
const CONTAINERS: &[&str] = &["video", "frames"];
const ENGINES: &[&str] = &["remotion", "hyperframes"];
const TYPOGRAPHY_KEYS: &[&str] = &["font", "size", "weight"];
const DELIVER_PRESET_KEYS: &[&str] = &["format", "codec"];

/// One project's configuration, as `project.yaml` declares it.
pub struct ProjectConfig {
    pub name: Json,
    pub slug: Json,
    pub client: Json,
    pub created: Json,
    pub status: String,
    pub project_format_version: Json,
    pub tags: Json,
    pub description: Json,
    pub ren_version: Json,
    pub source: SourceConfig,
    pub pipeline: PipelineConfig,
    pub resolve: ResolveConfig,
    pub project_root: PathBuf,
}

pub struct SourceConfig {
    pub fps: Json,
    pub footage_root: String,
    pub program_stream: Option<Json>,
    pub measure_program_stream: Json,
    pub language: Json,
    pub shape: String,
    pub speakers: Option<Vec<Json>>,
}

pub struct PipelineConfig {
    pub brand_template: String,
    pub delivery_format: String,
    pub deliver_preset: Option<Json>,
    pub deliver_naming: String,
    pub framing_intent: Option<Json>,
    pub subtitle_typography: Option<Json>,
    pub subtitle_overlay_geometry: String,
    pub subtitle_overlay_container: String,
    pub motion_graphics_overlay_geometry: String,
    pub graphics_renderer: String,
    #[allow(dead_code)]
    /// Part of the schema; nothing in `info`/`status`/`check` reads it yet.
    pub tv_frame: Option<Json>,
    pub safe_zones: Option<Json>,
    pub creative_brief: String,
    pub attach_creative_brief: Option<Json>,
    pub creative_brief_inline: Vec<Json>,
    pub series: Json,
    pub creative_tasks: Vec<Json>,
    pub sfx_library: String,
    pub music_library: String,
}

pub struct ResolveConfig {
    pub project_name: String,
    pub database: String,
    pub folder: String,
    pub timeline_name: String,
}

/// Why a project could not be loaded: the Python exception's own message,
/// so the native refusal or exit matches.
#[derive(Debug)]
pub enum LoadError {
    /// `FileNotFoundError` - the project is not there.
    NotFound(String),
    /// `ValueError` - the config is malformed.
    Invalid(String),
}

/// A located project, or the reason it is not there.
#[allow(clippy::large_enum_variant)]
pub enum Located {
    Found(ProjectConfig),
    NotFound(String),
    LoadError(String),
}

fn yaml_to_json(value: &Yaml) -> Json {
    match value {
        Yaml::Null => Json::Null,
        Yaml::Bool(b) => Json::Bool(*b),
        Yaml::Number(n) => {
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
        Yaml::String(s) => Json::Str(s.clone()),
        Yaml::Sequence(seq) => Json::Array(seq.iter().map(yaml_to_json).collect()),
        Yaml::Mapping(map) => {
            let mut pairs = Vec::new();
            for (key, value) in map {
                let key = match key {
                    Yaml::String(s) => s.clone(),
                    Yaml::Number(n) => n.to_string(),
                    Yaml::Bool(b) => b.to_string(),
                    Yaml::Null => "null".to_string(),
                    other => serde_yaml::to_string(other)
                        .unwrap_or_default()
                        .trim()
                        .to_string(),
                };
                pairs.push((key, yaml_to_json(value)));
            }
            Json::Object(pairs)
        }
        Yaml::Tagged(tagged) => yaml_to_json(&tagged.value),
    }
}

fn truthy(value: &Json) -> bool {
    !matches!(
        value,
        Json::Null | Json::Bool(false) | Json::Int(0) | Json::Float(0.0)
    ) && !matches!(value, Json::Str(s) if s.is_empty())
        && !matches!(value, Json::Array(a) if a.is_empty())
        && !matches!(value, Json::Object(o) if o.is_empty())
}

fn as_string_or_empty(value: &Json) -> String {
    match value {
        Json::Str(s) => s.clone(),
        Json::Null => String::new(),
        other => py_display(other),
    }
}

/// `value or default` - the empty string for a falsy declaration, the value
/// otherwise. The `or ""` / `or "full"` half of the schema's defaults.
fn or_default(value: &Json, default: &str) -> String {
    if truthy(value) {
        as_string_or_empty(value)
    } else {
        default.to_string()
    }
}

/// Format a value the way Python's f-string (`str(value)`) would, for the
/// status command's output.
pub fn py_display_pub(value: &Json) -> String {
    py_display(value)
}

pub fn yaml_to_json_pub(value: &Yaml) -> Json {
    yaml_to_json(value)
}

pub fn truthy_pub(value: &Json) -> bool {
    truthy(value)
}

pub fn type_name_pub(value: &Json) -> String {
    type_name(value)
}

pub fn expand_portable_pub(value: &str, project_root: &Path, engine_root: &Path) -> String {
    expand_portable(value, project_root, engine_root)
}

fn py_display(value: &Json) -> String {
    match value {
        Json::Str(s) => s.clone(),
        Json::Int(n) => n.to_string(),
        Json::Float(f) => crate::json::format_float(*f),
        Json::Bool(b) => b.to_string(),
        Json::Null => "None".to_string(),
        other => other.to_python_json(),
    }
}

/// `library.tools.portable_paths.expand`: this machine's absolute for a
/// stored path declaration. A leading `$PROJECT`, `$HOME`, `$ENGINE` or
/// `~` resolves against the project folder, the current home and the
/// engine; a `$VAR` or `${VAR}` resolves against the environment; anything
/// else passes through untouched.
fn expand_portable(value: &str, project_root: &Path, engine_root: &Path) -> String {
    let text = value.trim();
    if text.is_empty() {
        return value.to_string();
    }
    let home = engine::home_dir();
    let roots = [
        ("$PROJECT", project_root.to_string_lossy().to_string()),
        ("$HOME", home.to_string_lossy().to_string()),
        ("$ENGINE", engine_root.to_string_lossy().to_string()),
    ];
    for (token, root) in roots {
        if root.is_empty() {
            continue;
        }
        if text == token {
            return root;
        }
        if let Some(rest) = text.strip_prefix(token) {
            return format!("{root}{rest}");
        }
    }
    if text == "~" {
        return home.to_string_lossy().to_string();
    }
    if let Some(rest) = text.strip_prefix('~') {
        return format!("{}{}", home.to_string_lossy(), rest);
    }
    if text.contains('$') {
        let expanded = expandvars(text);
        if expanded != text {
            return expanduser(&expanded);
        }
    }
    value.to_string()
}

fn expandvars(text: &str) -> String {
    let mut out = String::new();
    let mut chars = text.chars().peekable();
    while let Some(ch) = chars.next() {
        if ch != '$' {
            out.push(ch);
            continue;
        }
        match chars.peek() {
            Some('{') => {
                chars.next();
                let mut name = String::new();
                for c in chars.by_ref() {
                    if c == '}' {
                        break;
                    }
                    name.push(c);
                }
                if let Ok(value) = env::var(&name) {
                    out.push_str(&value);
                } else {
                    out.push_str("${");
                    out.push_str(&name);
                    out.push('}');
                }
            }
            Some(c) if c.is_ascii_alphabetic() || *c == '_' => {
                let mut name = String::new();
                name.push(*c);
                chars.next();
                while let Some(&c) = chars.peek() {
                    if c.is_ascii_alphanumeric() || c == '_' {
                        name.push(c);
                        chars.next();
                    } else {
                        break;
                    }
                }
                if let Ok(value) = env::var(&name) {
                    out.push_str(&value);
                } else {
                    out.push('$');
                    out.push_str(&name);
                }
            }
            _ => out.push('$'),
        }
    }
    out
}

fn expanduser(text: &str) -> String {
    let home = engine::home_dir();
    if text == "~" {
        return home.to_string_lossy().to_string();
    }
    if let Some(rest) = text.strip_prefix("~/") {
        return format!("{}{}", home.to_string_lossy(), rest);
    }
    text.to_string()
}

/// `_expand_path`: the empty string for a falsy declaration, else the
/// expanded portable path.
fn expand_path(raw: &Json, project_root: &Path, engine_root: &Path) -> String {
    if !truthy(raw) {
        return String::new();
    }
    match raw {
        Json::Str(s) => expand_portable(s, project_root, engine_root),
        _ => String::new(),
    }
}

fn parse_language(raw: &Json) -> Json {
    match raw {
        Json::Null => Json::str("en"),
        Json::Str(s) if !s.trim().is_empty() => Json::str(s.trim().to_lowercase()),
        other => other.clone(),
    }
}

fn parse_speakers(raw: &Json) -> Option<Vec<Json>> {
    match raw {
        Json::Null => None,
        Json::Array(items) => Some(items.clone()),
        other => Some(vec![other.clone()]),
    }
}

/// Load one `project.yaml` through the schema, WITHOUT the format gate -
/// the read-only load `scan_projects` performs and the one the native
/// verbs use before deciding whether Python must take over.
pub fn load_config(yaml_path: &Path, engine_root: &Path) -> Result<ProjectConfig, LoadError> {
    if !yaml_path.is_file() {
        return Err(LoadError::NotFound(format!(
            "Project config not found: {}",
            yaml_path.display()
        )));
    }
    let text = fs::read_to_string(yaml_path)
        .map_err(|e| LoadError::Invalid(format!("cannot read {}: {e}", yaml_path.display())))?;
    let yaml: Yaml = serde_yaml::from_str(&text)
        .map_err(|e| LoadError::Invalid(format!("cannot parse {}: {e}", yaml_path.display())))?;
    let data = yaml_to_json(&yaml);
    if !matches!(data, Json::Object(_)) {
        return Err(LoadError::Invalid(format!(
            "Project config is not a YAML mapping: {}",
            yaml_path.display()
        )));
    }
    let project_root = yaml_path.parent().unwrap_or(Path::new("")).to_path_buf();
    let config = extract_config(&data, &project_root, engine_root);
    let errors = validate(&config);
    if !errors.is_empty() {
        return Err(LoadError::Invalid(format!(
            "Invalid project config {}: {}",
            yaml_path.display(),
            errors.join("; ")
        )));
    }
    Ok(config)
}

fn extract_config(data: &Json, project_root: &Path, engine_root: &Path) -> ProjectConfig {
    let source_data = data.get("source").cloned().unwrap_or(Json::Object(vec![]));
    let pipeline_data = data.get("pipeline").cloned().unwrap_or(Json::Object(vec![]));
    let resolve_data = data.get("resolve").cloned().unwrap_or(Json::Object(vec![]));

    let source = SourceConfig {
        fps: source_data.get("fps").cloned().unwrap_or(Json::Int(30)),
        footage_root: expand_path(
            source_data.get("footage_root").unwrap_or(&Json::Null),
            project_root,
            engine_root,
        ),
        program_stream: source_data.get("program_stream").cloned(),
        measure_program_stream: source_data
            .get("measure_program_stream")
            .cloned()
            .unwrap_or(Json::Bool(false)),
        language: parse_language(source_data.get("language").unwrap_or(&Json::Null)),
        shape: as_string_or_empty(source_data.get("shape").unwrap_or(&Json::Null))
            .trim()
            .to_lowercase(),
        speakers: parse_speakers(source_data.get("speakers").unwrap_or(&Json::Null)),
    };

    let series_raw = match pipeline_data.get("series") {
        Some(value) if !matches!(value, Json::Null) => value.clone(),
        _ => data.get("series").cloned().unwrap_or(Json::Null),
    };
    let series = if truthy(&series_raw) {
        series_raw
    } else {
        Json::str("")
    };

    let pipeline = PipelineConfig {
        brand_template: as_string_or_empty(
            pipeline_data.get("brand_template").unwrap_or(&Json::Null),
        ),
        delivery_format: or_default(
            pipeline_data.get("delivery_format").unwrap_or(&Json::Null),
            "",
        ),
        deliver_preset: pipeline_data.get("deliver_preset").cloned(),
        deliver_naming: or_default(
            pipeline_data.get("deliver_naming").unwrap_or(&Json::Null),
            "",
        ),
        framing_intent: pipeline_data.get("framing_intent").cloned(),
        subtitle_typography: pipeline_data.get("subtitle_typography").cloned(),
        subtitle_overlay_geometry: or_default(
            pipeline_data
                .get("subtitle_overlay_geometry")
                .unwrap_or(&Json::Null),
            "full",
        ),
        subtitle_overlay_container: or_default(
            pipeline_data
                .get("subtitle_overlay_container")
                .unwrap_or(&Json::Null),
            "video",
        ),
        motion_graphics_overlay_geometry: or_default(
            pipeline_data
                .get("motion_graphics_overlay_geometry")
                .unwrap_or(&Json::Null),
            "full",
        ),
        graphics_renderer: or_default(
            pipeline_data.get("graphics_renderer").unwrap_or(&Json::Null),
            "remotion",
        ),
        tv_frame: pipeline_data.get("tv_frame").cloned(),
        safe_zones: pipeline_data.get("safe_zones").cloned(),
        creative_brief: expand_path(
            pipeline_data.get("creative_brief").unwrap_or(&Json::Null),
            project_root,
            engine_root,
        ),
        attach_creative_brief: pipeline_data.get("attach_creative_brief").cloned(),
        creative_brief_inline: match pipeline_data.get("creative_brief_inline") {
            Some(Json::Array(items)) => items.clone(),
            _ => vec![],
        },
        series,
        creative_tasks: match pipeline_data.get("creative_tasks") {
            Some(Json::Array(items)) => items.clone(),
            _ => vec![],
        },
        sfx_library: expand_path(
            pipeline_data.get("sfx_library").unwrap_or(&Json::Null),
            project_root,
            engine_root,
        ),
        music_library: expand_path(
            pipeline_data.get("music_library").unwrap_or(&Json::Null),
            project_root,
            engine_root,
        ),
    };

    let resolve = ResolveConfig {
        project_name: as_string_or_empty(
            resolve_data.get("project_name").unwrap_or(&Json::Null),
        ),
        database: as_string_or_empty(
            resolve_data.get("database").unwrap_or(&Json::str("local")),
        ),
        folder: as_string_or_empty(resolve_data.get("folder").unwrap_or(&Json::Null)),
        timeline_name: as_string_or_empty(
            resolve_data
                .get("timeline_name")
                .unwrap_or(&Json::str("Main Edit")),
        ),
    };

    let status_raw = data.get("status").cloned().unwrap_or(Json::str("draft"));
    let status = match &status_raw {
        Json::Str(s) if STATUS_VALUES.contains(&s.as_str()) => s.clone(),
        _ => "draft".to_string(),
    };

    let ren_version = data.get("ren_version").cloned().unwrap_or(Json::Null);
    let ren_version = if truthy(&ren_version) {
        ren_version
    } else {
        Json::str("")
    };

    ProjectConfig {
        name: data.get("name").cloned().unwrap_or(Json::str("")),
        slug: data.get("slug").cloned().unwrap_or(Json::str("")),
        client: data.get("client").cloned().unwrap_or(Json::str("")),
        created: data.get("created").cloned().unwrap_or(Json::str("")),
        status,
        project_format_version: data
            .get(FORMAT_VERSION_KEY)
            .cloned()
            .unwrap_or(Json::Int(0)),
        tags: data.get("tags").cloned().unwrap_or(Json::Array(vec![])),
        description: data.get("description").cloned().unwrap_or(Json::str("")),
        ren_version,
        source,
        pipeline,
        resolve,
        project_root: project_root.to_path_buf(),
    }
}

/// `ProjectConfig.validate` - every refusal, by name, with the Python
/// message. The safe-zone rule is absent: a project that declares one falls
/// back to Python (see `commands.rs`), because validating it needs the
/// platform layout engine this crate does not port.
fn validate(config: &ProjectConfig) -> Vec<String> {
    let mut errors = vec![];
    let version = &config.project_format_version;
    if matches!(version, Json::Bool(_))
        || !matches!(version, Json::Int(_))
        || matches!(version, Json::Int(n) if *n < MIN_SUPPORTED_FORMAT_VERSION)
        || matches!(version, Json::Int(n) if *n > PROJECT_FORMAT_VERSION)
    {
        errors.push(format!(
            "project_format_version must be an integer, got {version:?}."
        ));
    }
    if !truthy(&config.name) {
        errors.push("'name' is required".to_string());
    }
    if !truthy(&config.slug) {
        errors.push("'slug' is required".to_string());
    }
    if !matches!(&config.ren_version, Json::Str(_)) {
        errors.push(format!(
            "'ren_version' must be the Ren build string, got {}.",
            type_name(&config.ren_version)
        ));
    }
    let slug_text = as_string_or_empty(&config.slug);
    let slug_alphanumeric = slug_text
        .chars()
        .all(|c| c == '-' || c == '_' || c.is_alphanumeric());
    if !slug_alphanumeric || slug_text.is_empty() {
        errors.push(format!(
            "'slug' must be alphanumeric with dashes/underscores: {slug_text}"
        ));
    }
    if !STATUS_VALUES.contains(&config.status.as_str()) {
        errors.push(format!("Invalid status: {}", config.status));
    }
    if let Some(program_stream) = &config.source.program_stream {
        let valid = match program_stream {
            Json::Int(n) => *n >= 1,
            _ => false,
        };
        if !valid {
            errors.push(format!(
                "source.program_stream must be a 1-based audio channel ordinal \
                 (a positive integer), got {program_stream:?}."
            ));
        }
    }
    if !matches!(&config.source.measure_program_stream, Json::Bool(_)) {
        errors.push(format!(
            "source.measure_program_stream must be true or false, got {:?}.",
            config.source.measure_program_stream
        ));
    }
    errors.extend(validate_source(config));
    if !config.pipeline.delivery_format.is_empty()
        && !DELIVERY_FORMATS.contains(&config.pipeline.delivery_format.as_str())
    {
        errors.push(format!(
            "Invalid pipeline.delivery_format: {} must be one of {}",
            config.pipeline.delivery_format,
            sorted_list(DELIVERY_FORMATS)
        ));
    }
    if let Some(preset) = &config.pipeline.deliver_preset {
        match preset {
            Json::Object(pairs) => {
                let unknown: Vec<&String> =
                    pairs.iter().map(|(k, _)| k).filter(|k| !DELIVER_PRESET_KEYS.contains(&k.as_str())).collect();
                if !unknown.is_empty() {
                    let mut names: Vec<String> =
                        unknown.iter().map(|s| s.to_string()).collect();
                    names.sort();
                    errors.push(format!(
                        "pipeline.deliver_preset declares {names:?}, which nothing \
                         reads. It takes {DELIVER_PRESET_KEYS:?}."
                    ));
                }
                for (key, value) in pairs {
                    if !matches!(value, Json::Str(s) if !s.trim().is_empty()) {
                        errors.push(format!(
                            "pipeline.deliver_preset[{key:?}] must be a non-empty \
                             string, got {value:?}."
                        ));
                    }
                }
            }
            other => errors.push(format!(
                "pipeline.deliver_preset must be a mapping of format/codec, got {}.",
                type_name(other)
            )),
        }
    }
    if let Some(framing) = &config.pipeline.framing_intent {
        let valid = match framing {
            Json::Float(f) => (0.0..=1.0).contains(f),
            Json::Int(i) => (0.0..=1.0).contains(&(*i as f64)),
            _ => false,
        };
        if !valid {
            let (kind, value) = match framing {
                Json::Float(f) => ("float".to_string(), crate::json::format_float(*f)),
                Json::Int(i) => ("int".to_string(), i.to_string()),
                other => (type_name(other), other.to_python_json()),
            };
            if matches!(framing, Json::Float(_) | Json::Int(_)) {
                errors.push(format!(
                    "framing_intent in pipeline.framing_intent must be between 0.0 \
                     (letterbox) and 1.0 (fill), got {value}"
                ));
            } else {
                errors.push(format!(
                    "framing_intent in pipeline.framing_intent must be a number between \
                     0.0 (letterbox) and 1.0 (fill), got {kind}: {value}"
                ));
            }
        }
    }
    if let Some(attach) = &config.pipeline.attach_creative_brief {
        let is_bool = matches!(attach, Json::Bool(_));
        if !is_bool {
            errors.push(format!(
                "pipeline.attach_creative_brief must be true or false, got {}",
                type_name(attach)
            ));
        } else if matches!(attach, Json::Bool(true)) && config.pipeline.creative_brief.is_empty()
        {
            errors.push(
                "pipeline.attach_creative_brief is true and no pipeline.creative_brief \
                 path is declared. Declare the path, or set attach_creative_brief to false."
                    .to_string(),
            );
        }
    }
    if !matches!(&config.pipeline.series, Json::Str(_)) {
        errors.push(format!(
            "pipeline.series must be the name of the series this video belongs to, got {}.",
            type_name(&config.pipeline.series)
        ));
    }
    if !GEOMETRIES.contains(&config.pipeline.subtitle_overlay_geometry.as_str()) {
        errors.push(format!(
            "pipeline.subtitle_overlay_geometry must be one of {GEOMETRIES:?}, got {:?}.",
            config.pipeline.subtitle_overlay_geometry
        ));
    }
    if !CONTAINERS.contains(&config.pipeline.subtitle_overlay_container.as_str()) {
        errors.push(format!(
            "pipeline.subtitle_overlay_container must be one of {CONTAINERS:?}, got {:?}.",
            config.pipeline.subtitle_overlay_container
        ));
    }
    if !GEOMETRIES.contains(&config.pipeline.motion_graphics_overlay_geometry.as_str()) {
        errors.push(format!(
            "pipeline.motion_graphics_overlay_geometry must be one of {GEOMETRIES:?}, \
             got {:?}.",
            config.pipeline.motion_graphics_overlay_geometry
        ));
    }
    if !ENGINES.contains(&config.pipeline.graphics_renderer.as_str()) {
        errors.push(format!(
            "pipeline.graphics_renderer must be one of {ENGINES:?}, got {:?}.",
            config.pipeline.graphics_renderer
        ));
    }
    if let Some(typography) = &config.pipeline.subtitle_typography {
        match typography {
            Json::Object(pairs) => {
                let unknown: Vec<&String> =
                    pairs.iter().map(|(k, _)| k).filter(|k| !TYPOGRAPHY_KEYS.contains(&k.as_str())).collect();
                if !unknown.is_empty() {
                    let mut names: Vec<String> =
                        unknown.iter().map(|s| s.to_string()).collect();
                    names.sort();
                    errors.push(format!(
                        "pipeline.subtitle_typography declares {names:?}, which nothing \
                         reads. It takes {TYPOGRAPHY_KEYS:?}."
                    ));
                }
            }
            other => errors.push(format!(
                "pipeline.subtitle_typography must be a mapping of {TYPOGRAPHY_KEYS:?}, \
                 got {}.",
                type_name(other)
            )),
        }
    }
    if let Some(tasks) = config
        .pipeline
        .creative_tasks
        .first()
        .map(|_| &config.pipeline.creative_tasks)
    {
        for (index, entry) in tasks.iter().enumerate() {
            match entry {
                Json::Object(pairs) => {
                    let name = pairs
                        .iter()
                        .find(|(k, _)| k == "name")
                        .map(|(_, v)| v);
                    match name {
                        Some(Json::Str(s)) if !s.is_empty() => {}
                        _ => errors.push(format!(
                            "pipeline.creative_tasks[{index}] names no task: each entry \
                             needs a non-empty string `name`."
                        )),
                    }
                }
                other => errors.push(format!(
                    "pipeline.creative_tasks[{index}] must be a mapping, got {}.",
                    type_name(other)
                )),
            }
        }
    }
    errors
}

fn validate_source(config: &ProjectConfig) -> Vec<String> {
    let mut errors = vec![];
    let language = &config.source.language;
    let language_ok = match language {
        Json::Str(s) => is_language_code(s.trim()),
        _ => false,
    };
    if !language_ok {
        errors.push(format!(
            "source.language must be a language code like `en` or `es`, got {language:?}."
        ));
    }
    if !matches!(
        config.source.shape.as_str(),
        "" | "speech" | "music" | "both" | "picture-led"
    ) {
        errors.push(format!(
            "source.shape must be one of speech, music, both or picture-led, got {:?}.",
            config.source.shape
        ));
    }
    if let Some(speakers) = &config.source.speakers {
        for (index, entry) in speakers.iter().enumerate() {
            match entry {
                Json::Object(pairs) => {
                    let name = pairs.iter().find(|(k, _)| k == "name").map(|(_, v)| v);
                    match name {
                        Some(Json::Str(s)) if !s.trim().is_empty() => {}
                        _ => errors.push(format!(
                            "source.speakers[{index}] names no speaker: each entry needs \
                             a non-empty string `name`."
                        )),
                    }
                    let role = pairs.iter().find(|(k, _)| k == "role").map(|(_, v)| v);
                    if let Some(Json::Str(_)) = role {
                    } else if let Some(non_string) = role {
                        errors.push(format!(
                            "source.speakers[{index}] carries role {non_string:?}, which \
                             must be a string."
                        ));
                    }
                    let unknown: Vec<&String> = pairs
                        .iter()
                        .map(|(k, _)| k)
                        .filter(|k| k != &"name" && k != &"role")
                        .collect();
                    if !unknown.is_empty() {
                        let mut names: Vec<String> =
                            unknown.iter().map(|s| s.to_string()).collect();
                        names.sort();
                        errors.push(format!(
                            "source.speakers[{index}] declares {names:?}, which nothing \
                             reads. A speaker takes `name` and an optional `role`."
                        ));
                    }
                }
                other => errors.push(format!(
                    "source.speakers[{index}] must be a mapping with a `name`, got {}.",
                    type_name(other)
                )),
            }
        }
    }
    errors
}

/// `^[A-Za-z]{2,3}(-[A-Za-z]{2,4})?$`, the way `re.match` applies it.
fn is_language_code(text: &str) -> bool {
    let bytes = text.as_bytes();
    let mut index = 0;
    let mut letters = 0;
    while index < bytes.len() && bytes[index].is_ascii_alphabetic() && letters < 3 {
        index += 1;
        letters += 1;
    }
    if letters < 2 {
        return false;
    }
    if index == bytes.len() {
        return true;
    }
    if bytes[index] == b'-' {
        index += 1;
        let mut region = 0;
        while index < bytes.len() && bytes[index].is_ascii_alphabetic() && region < 4 {
            index += 1;
            region += 1;
        }
        if region < 2 {
            return false;
        }
    }
    index == bytes.len()
}

fn type_name(value: &Json) -> String {
    match value {
        Json::Null => "NoneType".to_string(),
        Json::Bool(_) => "bool".to_string(),
        Json::Int(_) => "int".to_string(),
        Json::Float(_) => "float".to_string(),
        Json::Str(_) => "str".to_string(),
        Json::Array(_) => "list".to_string(),
        Json::Object(_) => "dict".to_string(),
    }
}

fn sorted_list(values: &[&str]) -> String {
    let mut sorted: Vec<&str> = values.to_vec();
    sorted.sort();
    format!("{sorted:?}")
}

/// `project_config_to_dict` - the config as `ren info` prints it, every
/// conditional key included or omitted exactly as the Python decides.
pub fn config_to_dict(config: &ProjectConfig) -> Json {
    let mut source = vec![("fps".to_string(), config.source.fps.clone())];
    if !config.source.footage_root.is_empty() {
        source.push(("footage_root".to_string(), Json::str(&config.source.footage_root)));
    }
    let language_is_en = matches!(&config.source.language, Json::Str(s) if s == "en");
    if !language_is_en {
        source.push(("language".to_string(), config.source.language.clone()));
    }
    if !config.source.shape.is_empty() {
        source.push(("shape".to_string(), Json::str(&config.source.shape)));
    }
    if let Some(speakers) = &config.source.speakers {
        source.push((
            "speakers".to_string(),
            Json::Array(speakers.clone()),
        ));
    }
    if let Some(program_stream) = &config.source.program_stream {
        source.push(("program_stream".to_string(), program_stream.clone()));
    }
    if truthy(&config.source.measure_program_stream) {
        source.push((
            "measure_program_stream".to_string(),
            config.source.measure_program_stream.clone(),
        ));
    }

    let mut pipeline = vec![
        ("brand_template".to_string(), Json::str(&config.pipeline.brand_template)),
        ("delivery_format".to_string(), Json::str(&config.pipeline.delivery_format)),
    ];
    if let Some(preset) = &config.pipeline.deliver_preset {
        if truthy(preset) {
            pipeline.push(("deliver_preset".to_string(), preset.clone()));
        }
    }
    if !config.pipeline.deliver_naming.is_empty() {
        pipeline.push(("deliver_naming".to_string(), Json::str(&config.pipeline.deliver_naming)));
    }
    if let Some(framing) = &config.pipeline.framing_intent {
        pipeline.push(("framing_intent".to_string(), framing.clone()));
    }
    if let Some(typography) = &config.pipeline.subtitle_typography {
        pipeline.push(("subtitle_typography".to_string(), typography.clone()));
    }
    if config.pipeline.subtitle_overlay_geometry != "full" {
        pipeline.push((
            "subtitle_overlay_geometry".to_string(),
            Json::str(&config.pipeline.subtitle_overlay_geometry),
        ));
    }
    if config.pipeline.subtitle_overlay_container != "video" {
        pipeline.push((
            "subtitle_overlay_container".to_string(),
            Json::str(&config.pipeline.subtitle_overlay_container),
        ));
    }
    if config.pipeline.motion_graphics_overlay_geometry != "full" {
        pipeline.push((
            "motion_graphics_overlay_geometry".to_string(),
            Json::str(&config.pipeline.motion_graphics_overlay_geometry),
        ));
    }
    if config.pipeline.graphics_renderer != "remotion" {
        pipeline.push((
            "graphics_renderer".to_string(),
            Json::str(&config.pipeline.graphics_renderer),
        ));
    }
    if let Some(safe_zones) = &config.pipeline.safe_zones {
        pipeline.push(("safe_zones".to_string(), safe_zones.clone()));
    }
    pipeline.push(("creative_brief".to_string(), Json::str(&config.pipeline.creative_brief)));
    if let Some(attach) = &config.pipeline.attach_creative_brief {
        let attach_bool = matches!(attach, Json::Bool(true));
        pipeline.push(("attach_creative_brief".to_string(), Json::Bool(attach_bool)));
    }
    if !config.pipeline.creative_brief_inline.is_empty() {
        pipeline.push((
            "creative_brief_inline".to_string(),
            Json::Array(config.pipeline.creative_brief_inline.clone()),
        ));
    }
    if truthy(&config.pipeline.series) {
        pipeline.push(("series".to_string(), config.pipeline.series.clone()));
    }
    if !config.pipeline.creative_tasks.is_empty() {
        pipeline.push((
            "creative_tasks".to_string(),
            Json::Array(config.pipeline.creative_tasks.clone()),
        ));
    }
    pipeline.push(("sfx_library".to_string(), Json::str(&config.pipeline.sfx_library)));
    pipeline.push(("music_library".to_string(), Json::str(&config.pipeline.music_library)));

    let resolve = vec![
        ("project_name".to_string(), Json::str(&config.resolve.project_name)),
        ("database".to_string(), Json::str(&config.resolve.database)),
        ("folder".to_string(), Json::str(&config.resolve.folder)),
        ("timeline_name".to_string(), Json::str(&config.resolve.timeline_name)),
    ];

    let mut top = vec![
        (FORMAT_VERSION_KEY.to_string(), config.project_format_version.clone()),
        ("name".to_string(), config.name.clone()),
        ("slug".to_string(), config.slug.clone()),
        ("client".to_string(), config.client.clone()),
        ("created".to_string(), config.created.clone()),
        ("status".to_string(), Json::str(&config.status)),
    ];
    if truthy(&config.ren_version) {
        top.push(("ren_version".to_string(), config.ren_version.clone()));
    }
    top.push(("source".to_string(), Json::Object(source)));
    top.push(("pipeline".to_string(), Json::Object(pipeline)));
    top.push(("resolve".to_string(), Json::Object(resolve)));
    top.push(("tags".to_string(), config.tags.clone()));
    top.push(("description".to_string(), config.description.clone()));
    Json::Object(top)
}

/// `PipelineConfig.resolve_paths` - expand env vars and `~` in the two
/// library paths, falling back to the environment (through the config
/// layers) when undeclared.
fn resolve_paths(config: &mut ProjectConfig, engine_root: &Path) {
    if config.pipeline.sfx_library.is_empty() {
        let default = commands_default_sfx(engine_root);
        config.pipeline.sfx_library =
            crate::config::get("PIPELINE_SFX_LIBRARY", &default, engine_root);
    } else {
        config.pipeline.sfx_library =
            expandvars(&expanduser(&config.pipeline.sfx_library));
    }
    if config.pipeline.music_library.is_empty() {
        let default = commands_default_music(engine_root);
        config.pipeline.music_library =
            crate::config::get("PIPELINE_MUSIC_LIBRARY", &default, engine_root);
    } else {
        config.pipeline.music_library =
            expandvars(&expanduser(&config.pipeline.music_library));
    }
}

/// The `library.tools.paths` default for the SFX library, which the config
/// layers may override.
fn commands_default_sfx(engine_root: &Path) -> String {
    let shared = crate::config::get(
        "PIPELINE_SHARED_ASSETS",
        &engine::home_dir()
            .join("Movies")
            .join("Ren")
            .join("assets")
            .to_string_lossy(),
        engine_root,
    );
    format!("{shared}/sfx library")
}

fn commands_default_music(engine_root: &Path) -> String {
    let shared = crate::config::get(
        "PIPELINE_SHARED_ASSETS",
        &engine::home_dir()
            .join("Movies")
            .join("Ren")
            .join("assets")
            .to_string_lossy(),
        engine_root,
    );
    format!("{shared}/music")
}

/// Apply `resolve_paths` after extraction, as `load_project_config` does.
pub fn load_config_resolved(
    yaml_path: &Path,
    engine_root: &Path,
) -> Result<ProjectConfig, LoadError> {
    let mut config = load_config(yaml_path, engine_root)?;
    resolve_paths(&mut config, engine_root);
    Ok(config)
}

/// `library.tools.paths.PROJECTS_ROOT`: `$PIPELINE_PROJECTS_ROOT` (through
/// the config layers), else `~/Movies/Ren/projects`.
pub fn projects_root(engine_root: &Path) -> PathBuf {
    let value = crate::config::get("PIPELINE_PROJECTS_ROOT", "", engine_root);
    if value.is_empty() {
        engine::home_dir().join("Movies").join("Ren").join("projects")
    } else {
        PathBuf::from(value)
    }
}

/// `resolve_project_path`: a filesystem project reference to its
/// `project.yaml` path, or `None` when `ref` is a bare slug.
fn resolve_project_path(reference: &str) -> Option<PathBuf> {
    if reference.is_empty() {
        return None;
    }
    let has_separator = reference.contains('/') || reference.contains('\\');
    if !has_separator && !reference.starts_with('~') && !reference.starts_with('.') {
        return None;
    }
    let mut candidate = engine::expanduser(&PathBuf::from(reference));
    candidate = fs::canonicalize(&candidate).unwrap_or(candidate);
    if candidate.is_file() && candidate.file_name() == Some(std::ffi::OsStr::new("project.yaml")) {
        return Some(candidate);
    }
    if candidate.is_dir() {
        let yaml = candidate.join("project.yaml");
        if yaml.is_file() {
            return Some(yaml);
        }
    }
    None
}

/// `project_config_paths`: every `project.yaml` under the root, flat and
/// client-grouped, in sorted order.
fn project_config_paths(root: &Path) -> Vec<PathBuf> {
    if !root.is_dir() {
        return vec![];
    }
    let mut paths = vec![];
    let entries = match fs::read_dir(root) {
        Ok(entries) => entries,
        Err(_) => return paths,
    };
    let mut dirs: Vec<PathBuf> = entries
        .flatten()
        .map(|entry| entry.path())
        .filter(|path| {
            path.is_dir()
                && path
                    .file_name()
                    .and_then(|name| name.to_str())
                    .map(|name| !name.starts_with('.') && !name.starts_with('_'))
                    .unwrap_or(false)
        })
        .collect();
    dirs.sort();
    for entry in dirs {
        let yaml = entry.join("project.yaml");
        if yaml.exists() {
            paths.push(yaml);
            continue;
        }
        if let Ok(sub_entries) = fs::read_dir(&entry) {
            let mut sub_dirs: Vec<PathBuf> = sub_entries
                .flatten()
                .map(|entry| entry.path())
                .filter(|path| {
                    path.is_dir()
                        && path
                            .file_name()
                            .and_then(|name| name.to_str())
                            .map(|name| !name.starts_with('.') && !name.starts_with('_'))
                            .unwrap_or(false)
                })
                .collect();
            sub_dirs.sort();
            for sub_entry in sub_dirs {
                let sub_yaml = sub_entry.join("project.yaml");
                if sub_yaml.exists() {
                    paths.push(sub_yaml);
                }
            }
        }
    }
    paths
}

/// `get_project`: locate and load a project by slug or filesystem path.
/// The load is the read-only one (`migrate_format=False`); the caller
/// decides whether the format gate sends it to Python.
pub fn get_project(reference: &str, engine_root: &Path) -> Located {
    if let Some(direct) = resolve_project_path(reference) {
        return match load_config_resolved(&direct, engine_root) {
            Ok(config) => Located::Found(config),
            Err(LoadError::NotFound(message)) => Located::NotFound(message),
            Err(LoadError::Invalid(message)) => Located::LoadError(message),
        };
    }

    let root = projects_root(engine_root);
    let mut available: Vec<String> = vec![];
    for yaml_path in project_config_paths(&root) {
        match load_config_resolved(&yaml_path, engine_root) {
            Ok(config) => {
                available.push(as_string_or_empty(&config.slug));
                if as_string_or_empty(&config.slug) == reference {
                    return Located::Found(config);
                }
            }
            Err(LoadError::Invalid(message)) => {
                eprintln!("  Warning: skipping {}: {message}", yaml_path.display());
            }
            Err(LoadError::NotFound(message)) => {
                eprintln!("  Warning: skipping {}: {message}", yaml_path.display());
            }
        }
    }

    let direct = root.join(reference).join("project.yaml");
    if direct.is_file() {
        return match load_config_resolved(&direct, engine_root) {
            Ok(config) => Located::Found(config),
            Err(LoadError::NotFound(message)) => Located::NotFound(message),
            Err(LoadError::Invalid(message)) => Located::LoadError(message),
        };
    }

    if let Ok(entries) = fs::read_dir(&root) {
        let mut groups: Vec<PathBuf> = entries
            .flatten()
            .map(|entry| entry.path())
            .filter(|path| path.is_dir())
            .collect();
        groups.sort();
        for entry in groups {
            let grouped = entry.join(reference).join("project.yaml");
            if grouped.is_file() {
                return match load_config_resolved(&grouped, engine_root) {
                    Ok(config) => Located::Found(config),
                    Err(LoadError::NotFound(message)) => Located::NotFound(message),
                    Err(LoadError::Invalid(message)) => Located::LoadError(message),
                };
            }
        }
    }

    available.sort();
    let found = if available.is_empty() {
        "      (none)".to_string()
    } else {
        available
            .iter()
            .map(|slug| format!("      {slug}"))
            .collect::<Vec<_>>()
            .join("\n")
    };
    Located::NotFound(format!(
        "No project with slug '{reference}'.\n    Searched: {}\n    Found there:\n{found}\n    \
         A project kept outside that root is addressed by its path instead of its \
         slug, for example:\n      python3 manage_project.py info /path/to/{reference}",
        root.display()
    ))
}

/// The default `RenRefusal` first line (`library/tools/ren_refusal.py`
/// `USER_MESSAGE`): the plain-language consequence every refusal carries
/// first, before the technical `what`/`why`/`fix`. A refusal family may
/// override this on the Python side; every path this crate renders uses
/// the default, so the native verbs stay byte-identical to the Python
/// front door (`tests/native_commands.rs` fails on any drift).
pub const REFUSAL_USER_MESSAGE: &str = "I couldn't safely finish this step. The requested change has not been confirmed; resolve the issue below before continuing.";

/// `RenRefusal.render()`: the one refusal shape every `ren` verb prints.
pub fn render_refusal(what: &str, why: &str, fix: &str) -> String {
    format!("ren: refused - {REFUSAL_USER_MESSAGE}\n  what: {what}\n  why: {why}\n  fix: {fix}")
}

/// The `RenRefusal` a command raises when the project is not there, in the
/// one shape every `ren` verb prints.
pub fn project_not_found_refusal(slug: &str, message: &str) -> String {
    render_refusal(
        message,
        "no project with this slug is under PIPELINE_PROJECTS_ROOT",
        &format!(
            "run `ren new {slug}` to create it, `ren projects` to list them, or pass the project's absolute path instead of the slug"
        ),
    )
}

/// `project_status`: the on-disk status summary `ren status` prints.
pub fn project_status(config: &ProjectConfig) -> Json {
    let project_dir = &config.project_root;

    let mut raw_files: Vec<String> = vec![];
    let raw_dir = project_dir.join("raw");
    if raw_dir.is_dir() {
        if let Ok(entries) = fs::read_dir(&raw_dir) {
            for entry in entries.flatten() {
                let name = entry.file_name().to_string_lossy().to_string();
                let ext = Path::new(&name)
                    .extension()
                    .map(|e| e.to_string_lossy().to_lowercase())
                    .unwrap_or_default();
                if matches!(ext.as_str(), "mov" | "mp4" | "mxf" | "avi" | "mkv") {
                    raw_files.push(name);
                }
            }
        }
    }

    let pipeline_data_path = project_dir.join("pipeline_data.json");
    let pipeline_data_exists = pipeline_data_path.is_file();
    let mut steps_completed: Vec<String> = vec![];
    if pipeline_data_exists {
        if let Ok(text) = fs::read_to_string(&pipeline_data_path) {
            if let Ok(state) = serde_json::from_str::<serde_json::Value>(&text) {
                collect_steps(&state, &mut steps_completed);
            }
        }
    }

    let mut exports: Vec<String> = vec![];
    let exports_dir = project_dir.join("exports");
    if exports_dir.is_dir() {
        if let Ok(entries) = fs::read_dir(&exports_dir) {
            for entry in entries.flatten() {
                let name = entry.file_name().to_string_lossy().to_string();
                if !name.starts_with('.') {
                    exports.push(name);
                }
            }
        }
    }

    Json::Object(vec![
        ("slug".to_string(), config.slug.clone()),
        ("name".to_string(), config.name.clone()),
        ("status".to_string(), Json::str(&config.status)),
        ("client".to_string(), config.client.clone()),
        (
            "project_root".to_string(),
            Json::str(project_dir.to_string_lossy().to_string()),
        ),
        ("raw_footage_count".to_string(), Json::Int(raw_files.len() as i64)),
        (
            "pipeline_data_exists".to_string(),
            Json::Bool(pipeline_data_exists),
        ),
        (
            "steps_completed".to_string(),
            Json::Array(steps_completed.iter().map(Json::str).collect()),
        ),
        (
            "steps_completed_count".to_string(),
            Json::Int(steps_completed.len() as i64),
        ),
        (
            "exports".to_string(),
            Json::Array(exports.iter().map(Json::str).collect()),
        ),
        (
            "resolve_project".to_string(),
            Json::str(&config.resolve.project_name),
        ),
        (
            "resolve_folder".to_string(),
            Json::str(&config.resolve.folder),
        ),
    ])
}

/// `step_ledger.all_completed`: the merged read-only view of both ledgers,
/// legacy keys first, then each stage's, in the state file's own order.
fn collect_steps(state: &serde_json::Value, out: &mut Vec<String>) {
    let mut seen: Vec<String> = vec![];
    let push = |key: &str, state: &serde_json::Value, seen: &mut Vec<String>| {
        if let Some(object) = state.get(key).and_then(|v| v.as_object()) {
            for node in object.keys() {
                if !seen.contains(node) {
                    seen.push(node.clone());
                }
            }
        }
    };
    push("steps_completed", state, &mut seen);
    push("preflight_completed", state, &mut seen);
    push("edit_completed", state, &mut seen);
    out.extend(seen);
}

#[cfg(test)]
mod tests {
    use super::*;

    fn write_project(root: &Path, slug: &str, yaml: &str) -> PathBuf {
        let dir = root.join(slug);
        fs::create_dir_all(dir.join("raw")).unwrap();
        let path = dir.join("project.yaml");
        fs::write(&path, yaml).unwrap();
        path
    }

    const VALID_YAML: &str = "\
project_format_version: 2
name: Demo
slug: demo
status: in_progress
source:
  fps: 29.97
  language: en
  shape: speech
resolve:
  project_name: Demo
";

    #[test]
    fn a_valid_config_loads_and_round_trips() {
        let root = unique_temp("load");
        let path = write_project(&root, "demo", VALID_YAML);
        let config = load_config(&path, Path::new("/tmp/engine")).unwrap();
        assert_eq!(config.status, "in_progress");
        let dict = config_to_dict(&config);
        let text = dict.to_python_json();
        assert!(text.contains("\"fps\": 29.97"));
        assert!(text.contains("\"project_format_version\": 2"));
        let _ = fs::remove_dir_all(&root);
    }

    #[test]
    fn an_invalid_status_is_coerced_to_draft() {
        let root = unique_temp("status");
        let yaml = VALID_YAML.replace("in_progress", "bogus");
        let path = write_project(&root, "demo", &yaml);
        let config = load_config(&path, Path::new("/tmp/engine")).unwrap();
        assert_eq!(config.status, "draft");
        let _ = fs::remove_dir_all(&root);
    }

    #[test]
    fn a_missing_project_is_not_found() {
        let error = load_config(Path::new("/tmp/definitely-not-here/project.yaml"), Path::new("/tmp/engine"));
        assert!(matches!(error, Err(LoadError::NotFound(_))));
    }

    #[test]
    fn a_non_mapping_config_is_invalid() {
        let root = unique_temp("mapping");
        let dir = root.join("demo");
        fs::create_dir_all(&dir).unwrap();
        let path = dir.join("project.yaml");
        fs::write(&path, "- just\n- a\n- list\n").unwrap();
        let error = load_config(&path, Path::new("/tmp/engine"));
        assert!(matches!(error, Err(LoadError::Invalid(_))));
        let _ = fs::remove_dir_all(&root);
    }

    #[test]
    fn the_language_code_check_matches_the_python_regex() {
        assert!(is_language_code("en"));
        assert!(is_language_code("eng"));
        assert!(is_language_code("en-US"));
        assert!(is_language_code("en-USAB"));
        assert!(!is_language_code("e"));
        assert!(!is_language_code("english"));
        assert!(!is_language_code("en-U"));
        assert!(!is_language_code("en-USABC"));
    }

    #[test]
    fn portable_tokens_expand() {
        let project = Path::new("/tmp/proj");
        let engine = Path::new("/tmp/engine");
        assert_eq!(
            expand_portable("$PROJECT/raw/clip.mp4", project, engine),
            "/tmp/proj/raw/clip.mp4"
        );
        assert_eq!(
            expand_portable("$ENGINE/ren/version.py", project, engine),
            "/tmp/engine/ren/version.py"
        );
        assert_eq!(expand_portable("raw/clip.mp4", project, engine), "raw/clip.mp4");
        assert_eq!(expand_portable("/abs/clip.mp4", project, engine), "/abs/clip.mp4");
    }

    fn unique_temp(label: &str) -> PathBuf {
        std::env::temp_dir().join(format!("ren-project-{label}-{}", std::process::id()))
    }
}
