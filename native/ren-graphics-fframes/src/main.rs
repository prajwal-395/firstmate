//! ren-graphics-fframes: Ren's precompiled FFrames renderer.
//!
//! `ren-graphics-fframes render scene.json <frames_dir>` reads a Ren Graphics
//! IR scene (library/graphics_ir/scene.schema.json), builds one SVG per frame,
//! and rasterises it through FFrames/Skia on Metal into straight-alpha RGBA
//! PNGs. It draws exactly what the scene says - every position, colour, face
//! and weight is the scene's; this binary states no style of its own.
//!
//! Text follows CSS: a node's bounds is its box, each `\n`-separated line is
//! one line box `line_height` ems tall (`normal` when absent), the baseline
//! sits half the leading below the line top plus the face's ascent (typo
//! metrics when the face asks for them, else hhea), and `text_shadow` is a CSS
//! `text-shadow` list painted beneath the text. Transforms pivot on the
//! node's box centre, CSS's default `transform-origin`.

use std::collections::{HashMap, HashSet};
use std::fs;
use std::path::PathBuf;
use std::sync::Arc;

use base64::Engine as _;
use fframes::{AudioMap, Color, Duration, FFramesContext, Frame, FrameRenderer, Svgr, Video};
use fframes::{DynamicMediaProvider, RawFontData};
use fframes_skia_renderer::metal::SkiaMetalCtx;
use fframes_skia_renderer::SkiaFrameRenderer;
use serde::Deserialize;

// ── Scene IR schema ────────────────────────────────────────────────

#[derive(Debug, Deserialize, Clone)]
struct Scene {
    version: u32,
    fps: f32,
    width: u32,
    height: u32,
    duration: f32,
    nodes: Vec<IRNode>,
}

#[derive(Debug, Deserialize, Clone)]
struct IRNode {
    id: String,
    #[serde(rename = "type")]
    node_type: String,
    #[serde(default)]
    transform: Option<IRTransform>,
    #[serde(default)]
    opacity: Option<f32>,
    #[serde(default)]
    bounds: Option<IRBounds>,
    #[serde(default)]
    z_index: Option<i32>,
    #[serde(default)]
    timing: Option<IRTiming>,
    #[serde(default)]
    animations: Vec<IRAnimation>,
    #[serde(default)]
    text: Option<IRTextContent>,
    #[serde(default)]
    shape: Option<IRShapeContent>,
    #[serde(default)]
    image: Option<IRImageContent>,
    #[serde(default)]
    svg: Option<IRSVGContent>,
    #[serde(default)]
    children: Vec<String>,
}

#[derive(Debug, Deserialize, Clone)]
struct IRTransform {
    #[serde(default)]
    translate: Option<Vec<f32>>,
    #[serde(default)]
    rotate: Option<f32>,
    #[serde(default)]
    scale: Option<Vec<f32>>,
}

#[derive(Debug, Deserialize, Clone, Copy)]
struct IRBounds {
    x: f32,
    y: f32,
    width: f32,
    height: f32,
}

#[derive(Debug, Deserialize, Clone)]
struct IRTiming {
    #[serde(default)]
    start: f32,
    #[serde(default)]
    duration: Option<f32>,
}

#[derive(Debug, Deserialize, Clone)]
struct IRAnimation {
    property: String,
    from_value: IRValue,
    to_value: IRValue,
    start: f32,
    duration: f32,
    easing: IREasing,
}

#[derive(Debug, Deserialize, Clone)]
#[serde(untagged)]
enum IRValue {
    Number(f32),
    List(Vec<f32>),
    Text(String),
}

#[derive(Debug, Deserialize, Clone)]
#[serde(tag = "kind")]
enum IREasing {
    #[serde(rename = "linear")]
    Linear,
    #[serde(rename = "ease")]
    Ease { #[serde(default = "default_power")] power: f32 },
    #[serde(rename = "cubic_bezier")]
    CubicBezier { x1: f32, y1: f32, x2: f32, y2: f32 },
    #[serde(rename = "spring")]
    Spring { stiffness: f32, damping: f32, mass: f32 },
    #[serde(rename = "steps")]
    Steps { steps: i32, #[serde(default = "default_step_direction")] direction: String },
}

fn default_power() -> f32 { 2.0 }
fn default_step_direction() -> String { "end".to_string() }

#[derive(Debug, Deserialize, Clone)]
struct IRTextContent {
    text: String,
    #[serde(default)]
    font_family: Option<String>,
    #[serde(default)]
    font_file: Option<String>,
    #[serde(default)]
    font_size: Option<f32>,
    #[serde(default)]
    font_weight: Option<f32>,
    #[serde(default)]
    color: Option<String>,
    #[serde(default)]
    text_align: Option<String>,
    #[serde(default)]
    line_height: Option<f32>,
    #[serde(default)]
    letter_spacing: Option<f32>,
    #[serde(default)]
    text_transform: Option<String>,
    #[serde(default)]
    text_shadow: Option<String>,
}

#[derive(Debug, Deserialize, Clone)]
struct IRShapeContent {
    shape: String,
    #[serde(default)]
    fill: Option<String>,
    #[serde(default)]
    stroke: Option<String>,
    #[serde(default)]
    stroke_width: Option<f32>,
    #[serde(default)]
    path: Option<String>,
    #[serde(default)]
    corner_radius: Option<f32>,
}

#[derive(Debug, Deserialize, Clone)]
struct IRImageContent {
    src: String,
}

#[derive(Debug, Deserialize, Clone)]
struct IRSVGContent {
    #[serde(default, rename = "viewBox")]
    view_box: Option<String>,
    #[serde(default)]
    path: Option<String>,
    #[serde(default)]
    fill: Option<String>,
}

// ── Easing evaluation (matches library/graphics_ir/easing.py) ──────

fn apply_easing(easing: &IREasing, t: f32, fps: f32) -> f32 {
    match easing {
        IREasing::Linear => t.clamp(0.0, 1.0),
        IREasing::Ease { power } => {
            let clamped = t.clamp(0.0, 1.0);
            1.0 - (1.0 - clamped).powf(*power)
        }
        IREasing::CubicBezier { x1, y1, x2, y2 } => {
            cubic_bezier(*x1, *y1, *x2, *y2, t)
        }
        IREasing::Spring { stiffness, damping, mass } => {
            spring_progress(t, *stiffness, *damping, *mass, fps)
        }
        IREasing::Steps { steps, direction } => {
            let clamped = t.clamp(0.0, 1.0);
            if *steps <= 0 {
                return clamped;
            }
            if direction == "end" {
                (clamped * *steps as f32).floor() / *steps as f32
            } else {
                (clamped * *steps as f32).ceil() / *steps as f32
            }
        }
    }
}

fn cubic_bezier(x1: f32, y1: f32, x2: f32, y2: f32, t: f32) -> f32 {
    let clamped = t.clamp(0.0, 1.0);
    if clamped <= 0.0 { return 0.0; }
    if clamped >= 1.0 { return 1.0; }

    let bezier = |u: f32, p1: f32, p2: f32| -> f32 {
        3.0 * (1.0 - u).powi(2) * u * p1 + 3.0 * (1.0 - u) * u.powi(2) * p2 + u.powi(3)
    };
    let bezier_derivative = |u: f32, p1: f32, p2: f32| -> f32 {
        3.0 * (1.0 - u).powi(2) * p1 + 6.0 * (1.0 - u) * u * (p2 - p1) + 3.0 * u.powi(2) * (1.0 - p2)
    };

    let mut u = clamped;
    for _ in 0..16 {
        let bx = bezier(u, x1, x2);
        if (bx - clamped).abs() < 1e-7 { break; }
        let dx = bezier_derivative(u, x1, x2);
        if dx.abs() < 1e-7 { break; }
        u = u - (bx - clamped) / dx;
        u = u.clamp(0.0, 1.0);
    }
    bezier(u, y1, y2)
}

fn spring_advance(
    to_value: f32, last_timestamp: f32, current: f32, velocity: f32,
    now: f32, damping: f32, mass: f32, stiffness: f32,
) -> (f32, f32, f32) {
    let delta_time = (now - last_timestamp).min(64.0);
    if damping <= 0.0 {
        return (current, velocity, now);
    }
    let (c, m, k) = (damping, mass, stiffness);
    let v0 = -velocity;
    let x0 = to_value - current;
    let zeta = c / (2.0 * (k * m).sqrt());
    let omega0 = (k / m).sqrt();
    let omega1 = omega0 * (1.0 - zeta * zeta).sqrt();
    let t = delta_time / 1000.0;
    let sin1 = (omega1 * t).sin();
    let cos1 = (omega1 * t).cos();
    let envelope = (-zeta * omega0 * t).exp();
    let frag1 = envelope * (sin1 * ((v0 + zeta * omega0 * x0) / omega1) + x0 * cos1);
    let (position, next_velocity) = if zeta < 1.0 {
        let pos = to_value - frag1;
        let vel = zeta * omega0 * frag1 - envelope * (
            cos1 * (v0 + zeta * omega0 * x0) - omega1 * x0 * sin1
        );
        (pos, vel)
    } else {
        let critically = (-omega0 * t).exp();
        let pos = to_value - critically * (x0 + (v0 + omega0 * x0) * t);
        let vel = critically * (v0 * (t * omega0 - 1.0) + t * x0 * omega0 * omega0);
        (pos, vel)
    };
    (position, next_velocity, now)
}

fn spring_progress(t: f32, stiffness: f32, damping: f32, mass: f32, fps: f32) -> f32 {
    if t <= 0.0 { return 0.0; }
    let frame = t * fps;
    let frame_clamped = frame.max(0.0);
    let uneven_rest = frame_clamped % 1.0;
    let last = frame_clamped.floor();
    let (mut current, mut velocity, mut last_timestamp) = (0.0_f32, 0.0_f32, 0.0_f32);
    let mut f = 0.0_f32;
    while f <= last {
        if f == last {
            f = f + uneven_rest;
        }
        let (pos, vel, ts) = spring_advance(
            1.0, last_timestamp, current, velocity, f,
            damping, mass, stiffness,
        );
        current = pos;
        velocity = vel;
        last_timestamp = ts;
        f += 1.0;
    }
    current
}

// ── Animation sampling (matches library/graphics_ir/evaluator.py) ──

fn sample_animation(anim: &IRAnimation, time_seconds: f32, fps: f32) -> IRValue {
    if time_seconds < anim.start {
        return anim.from_value.clone();
    }
    if time_seconds >= anim.start + anim.duration {
        return anim.to_value.clone();
    }
    let local_time = time_seconds - anim.start;
    let progress = apply_easing(&anim.easing, local_time / anim.duration, fps);
    interpolate(&anim.from_value, &anim.to_value, progress)
}

fn interpolate(from: &IRValue, to: &IRValue, progress: f32) -> IRValue {
    match (from, to) {
        (IRValue::Number(f), IRValue::Number(t)) => {
            IRValue::Number(f + (t - f) * progress)
        }
        (IRValue::List(f), IRValue::List(t)) => {
            IRValue::List(f.iter().zip(t.iter()).map(|(a, b)| a + (b - a) * progress).collect())
        }
        (IRValue::Text(f), IRValue::Text(t)) => {
            if progress >= 0.5 { IRValue::Text(t.clone()) } else { IRValue::Text(f.clone()) }
        }
        _ => from.clone(),
    }
}

fn sample_node_animations(node: &IRNode, time_seconds: f32, fps: f32) -> Vec<(String, IRValue)> {
    let mut by_property: Vec<(String, Vec<&IRAnimation>)> = Vec::new();
    for anim in &node.animations {
        if let Some(entry) = by_property.iter_mut().find(|(p, _)| p == &anim.property) {
            entry.1.push(anim);
        } else {
            by_property.push((anim.property.clone(), vec![anim]));
        }
    }
    let mut result = Vec::new();
    for (prop, anims) in by_property {
        let mut sorted = anims;
        sorted.sort_by(|a, b| a.start.partial_cmp(&b.start).unwrap());
        let latest = sorted.iter().rev().find(|a| a.start <= time_seconds);
        let value = match latest {
            Some(anim) => sample_animation(anim, time_seconds, fps),
            None => sorted.first().map(|a| a.from_value.clone()).unwrap_or(IRValue::Number(0.0)),
        };
        result.push((prop, value));
    }
    result
}

// ── Faces and their vertical metrics ───────────────────────────────

/// Ascent, descent (positive) and line gap, in ems.
#[derive(Debug, Clone, Copy)]
struct FaceMetrics {
    ascent: f32,
    descent: f32,
    line_gap: f32,
}

fn face_query_family(text: &IRTextContent) -> Option<&str> {
    text.font_family.as_deref()
}

/// The metrics of the face each text node draws with, keyed by node id.
///
/// The scene's own font files are loaded first; the system is consulted
/// only when a node names a family no scene file carries.
fn text_metrics(scene: &Scene) -> HashMap<String, FaceMetrics> {
    let mut db = fontdb::Database::new();
    let mut loaded: HashSet<String> = HashSet::new();
    for node in &scene.nodes {
        if let Some(path) = node.text.as_ref().and_then(|t| t.font_file.as_ref()) {
            if loaded.insert(path.clone()) {
                if let Err(e) = db.load_font_file(path) {
                    eprintln!("WARNING: cannot load font file {}: {}", path, e);
                }
            }
        }
    }
    let mut system_loaded = false;
    let mut out = HashMap::new();
    for node in &scene.nodes {
        let Some(text) = &node.text else { continue };
        let weight = text.font_weight.unwrap_or(400.0).round().clamp(1.0, 1000.0) as u16;
        let family_name = face_query_family(text).map(|s| s.to_string());
        let lookup = |db: &fontdb::Database| {
            let families = match &family_name {
                Some(name) => vec![fontdb::Family::Name(name), fontdb::Family::SansSerif],
                None => vec![fontdb::Family::SansSerif],
            };
            db.query(&fontdb::Query {
                families: &families,
                weight: fontdb::Weight(weight),
                ..Default::default()
            })
        };
        let mut id = lookup(&db);
        let exact = id.is_some()
            && family_name.as_ref().map_or(false, |name| {
                id.and_then(|i| db.face(i))
                    .map_or(false, |f| f.families.iter().any(|(n, _)| n == name))
            });
        if !exact && !system_loaded {
            db.load_system_fonts();
            system_loaded = true;
            id = lookup(&db);
        }
        let metrics = id.and_then(|id| {
            db.with_face_data(id, |data, index| {
                ttf_parser::Face::parse(data, index).ok().map(|face| {
                    let upem = face.units_per_em() as f32;
                    FaceMetrics {
                        ascent: face.ascender() as f32 / upem,
                        descent: -(face.descender() as f32) / upem,
                        line_gap: (face.line_gap() as f32).max(0.0) / upem,
                    }
                })
            })
            .flatten()
        });
        match metrics {
            Some(m) => {
                out.insert(node.id.clone(), m);
            }
            None => {
                eprintln!(
                    "ERROR: text node {} names a face no font file or system font provides",
                    node.id
                );
                std::process::exit(1);
            }
        }
    }
    out
}

// ── Paint helpers ──────────────────────────────────────────────────

/// A CSS colour as an SVG paint plus its opacity.
fn paint(colour: &str) -> (String, f32) {
    let c = colour.trim();
    let lower = c.to_ascii_lowercase();
    if let Some(inner) = lower.strip_prefix("rgba(").and_then(|s| s.strip_suffix(')')) {
        let parts: Vec<&str> = inner.split(',').map(|p| p.trim()).collect();
        if parts.len() == 4 {
            let alpha = parts[3].parse::<f32>().unwrap_or(1.0);
            return (format!("rgb({},{},{})", parts[0], parts[1], parts[2]), alpha);
        }
    }
    if lower.starts_with('#') && lower.len() == 9 {
        let alpha = u8::from_str_radix(&lower[7..9], 16).unwrap_or(255) as f32 / 255.0;
        return (lower[..7].to_string(), alpha);
    }
    (c.to_string(), 1.0)
}

fn paint_attrs(kind: &str, colour: &str) -> String {
    let (value, alpha) = paint(colour);
    if alpha < 1.0 {
        format!(r#" {kind}="{}" {kind}-opacity="{}""#, escape_xml(&value), alpha)
    } else {
        format!(r#" {kind}="{}""#, escape_xml(&value))
    }
}

/// One entry of a CSS `text-shadow` list.
#[derive(Debug, Clone)]
struct Shadow {
    dx: f32,
    dy: f32,
    blur: f32,
    colour: Option<String>,
}

/// Split on commas that are not inside parentheses.
fn split_top_level(list: &str, sep: char) -> Vec<String> {
    let mut out = Vec::new();
    let mut depth = 0;
    let mut current = String::new();
    for ch in list.chars() {
        match ch {
            '(' => depth += 1,
            ')' => depth -= 1,
            _ => {}
        }
        if ch == sep && depth == 0 {
            out.push(current.trim().to_string());
            current.clear();
        } else {
            current.push(ch);
        }
    }
    if !current.trim().is_empty() {
        out.push(current.trim().to_string());
    }
    out
}

fn parse_length(token: &str) -> Option<f32> {
    let t = token.trim();
    let number = t.strip_suffix("px").unwrap_or(t);
    number.parse::<f32>().ok()
}

fn parse_text_shadow(list: &str) -> Vec<Shadow> {
    if list.trim().eq_ignore_ascii_case("none") {
        return Vec::new();
    }
    split_top_level(list, ',')
        .into_iter()
        .filter_map(|entry| {
            let mut lengths = Vec::new();
            let mut colour = None;
            for token in split_top_level(&entry, ' ') {
                if token.is_empty() {
                    continue;
                }
                match parse_length(&token) {
                    Some(v) => lengths.push(v),
                    None => colour = Some(token),
                }
            }
            if lengths.len() < 2 {
                return None;
            }
            Some(Shadow {
                dx: lengths[0],
                dy: lengths[1],
                blur: lengths.get(2).copied().unwrap_or(0.0),
                colour,
            })
        })
        .collect()
}

fn apply_text_transform(text: &str, transform: Option<&str>) -> String {
    match transform {
        Some("uppercase") => text.to_uppercase(),
        Some("lowercase") => text.to_lowercase(),
        Some("capitalize") => text
            .split(' ')
            .map(|w| {
                let mut chars = w.chars();
                match chars.next() {
                    Some(first) => first.to_uppercase().collect::<String>() + chars.as_str(),
                    None => String::new(),
                }
            })
            .collect::<Vec<_>>()
            .join(" "),
        _ => text.to_string(),
    }
}

/// `inset(top right bottom left)` in percent of the box.
fn parse_inset(value: &str) -> Option<[f32; 4]> {
    let inner = value.trim().strip_prefix("inset(")?.strip_suffix(')')?;
    let parts: Vec<f32> = inner
        .split_whitespace()
        .map(|p| p.trim_end_matches('%').parse::<f32>().unwrap_or(0.0))
        .collect();
    match parts.len() {
        1 => Some([parts[0]; 4]),
        2 => Some([parts[0], parts[1], parts[0], parts[1]]),
        3 => Some([parts[0], parts[1], parts[2], parts[1]]),
        4 => Some([parts[0], parts[1], parts[2], parts[3]]),
        _ => None,
    }
}

/// The encoded bytes an image node names: a base64 data URI or a file.
fn image_bytes(src: &str) -> Result<Vec<u8>, String> {
    if let Some(rest) = src.strip_prefix("data:") {
        let (meta, payload) = rest.split_once(',').ok_or("data URI has no payload")?;
        if !meta.ends_with(";base64") {
            return Err(format!("data URI {meta:?} is not base64"));
        }
        return base64::engine::general_purpose::STANDARD
            .decode(payload.trim())
            .map_err(|e| format!("data URI does not decode: {e}"));
    }
    fs::read(src).map_err(|e| format!("cannot read image {src}: {e}"))
}

fn escape_xml(text: &str) -> String {
    text.replace('&', "&amp;")
        .replace('<', "&lt;")
        .replace('>', "&gt;")
        .replace('"', "&quot;")
}

// ── SVG building ───────────────────────────────────────────────────

/// One frame's SVG, with the defs its filters and clips need.
struct FrameSvg {
    defs: String,
    body: String,
    filters: HashMap<String, String>,
    next_id: usize,
    width: u32,
    height: u32,
}

impl FrameSvg {
    fn blur_filter(&mut self, std_deviation: f32) -> String {
        let key = format!("{:.3}", std_deviation);
        if let Some(id) = self.filters.get(&key) {
            return id.clone();
        }
        let id = format!("f{}", self.next_id);
        self.next_id += 1;
        let (w, h) = (self.width as f32, self.height as f32);
        self.defs.push_str(&format!(
            r#"<filter id="{id}" filterUnits="userSpaceOnUse" x="{}" y="{}" width="{}" height="{}"><feGaussianBlur stdDeviation="{}"/></filter>"#,
            -w, -h, 3.0 * w, 3.0 * h, std_deviation
        ));
        self.filters.insert(key, id.clone());
        id
    }

    fn clip_rect(&mut self, x: f32, y: f32, w: f32, h: f32) -> String {
        let id = format!("c{}", self.next_id);
        self.next_id += 1;
        self.defs.push_str(&format!(
            r#"<clipPath id="{id}" clipPathUnits="userSpaceOnUse"><rect x="{x}" y="{y}" width="{}" height="{}"/></clipPath>"#,
            w.max(0.0),
            h.max(0.0)
        ));
        id
    }
}

struct SceneVideo {
    scene: Scene,
    node_map: HashMap<String, IRNode>,
    roots: Vec<String>,
    metrics: HashMap<String, FaceMetrics>,
    /// Image node id -> the name its pixels are preloaded under.
    images: HashMap<String, String>,
}

impl Video for SceneVideo {
    const FPS: usize = 30;
    const WIDTH: usize = 1920;
    const HEIGHT: usize = 1080;
    const BACKGROUND_COLOR: Color = Color::TRANSPARENT;

    fn duration(&self) -> Duration<'_> {
        Duration::Frames(frame_count(&self.scene))
    }

    fn audio(&self) -> AudioMap<'_> {
        AudioMap::none()
    }

    fn render_frame(&self, frame: Frame, _ctx: &FFramesContext) -> Svgr<'_> {
        let time_seconds = frame.index as f32 / self.scene.fps;
        let mut out = FrameSvg {
            defs: String::new(),
            body: String::new(),
            filters: HashMap::new(),
            next_id: 0,
            width: self.scene.width,
            height: self.scene.height,
        };
        for id in &self.roots {
            if let Some(node) = self.node_map.get(id) {
                self.render_node(&mut out, node, time_seconds);
            }
        }
        Svgr::from(format!(
            r#"<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h}" width="{w}" height="{h}"><defs>{}</defs>{}</svg>"#,
            out.defs,
            out.body,
            w = self.scene.width,
            h = self.scene.height
        ))
    }
}

fn frame_count(scene: &Scene) -> usize {
    (scene.duration * scene.fps).round() as usize
}

fn number(value: Option<&IRValue>) -> Option<f32> {
    match value {
        Some(IRValue::Number(n)) => Some(*n),
        _ => None,
    }
}

fn pair(value: Option<&IRValue>) -> Option<(f32, f32)> {
    match value {
        Some(IRValue::List(v)) if v.len() >= 2 => Some((v[0], v[1])),
        Some(IRValue::Number(n)) => Some((*n, *n)),
        _ => None,
    }
}

impl SceneVideo {
    fn render_node(&self, out: &mut FrameSvg, node: &IRNode, t: f32) {
        if let Some(timing) = &node.timing {
            if t < timing.start {
                return;
            }
            if let Some(dur) = timing.duration {
                if t >= timing.start + dur {
                    return;
                }
            }
        }

        let anims = sample_node_animations(node, t, self.scene.fps);
        let get = |prop: &str| anims.iter().find(|(p, _)| p == prop).map(|(_, v)| v);

        let opacity = number(get("opacity")).or(node.opacity).unwrap_or(1.0);
        if opacity <= 0.0 {
            return;
        }

        // CSS/GSAP order: translate, then rotate and scale about the box centre.
        let static_t = node.transform.as_ref();
        let (mut tx, mut ty) = static_t
            .and_then(|s| s.translate.as_ref())
            .filter(|v| v.len() >= 2)
            .map(|v| (v[0], v[1]))
            .unwrap_or((0.0, 0.0));
        if let Some((ax, ay)) = pair(get("transform.translate")) {
            tx += ax;
            ty += ay;
        }
        let rotate = number(get("transform.rotate"))
            .or(static_t.and_then(|s| s.rotate))
            .unwrap_or(0.0);
        let (sx, sy) = pair(get("transform.scale"))
            .or(static_t
                .and_then(|s| s.scale.as_ref())
                .filter(|v| v.len() >= 2)
                .map(|v| (v[0], v[1])))
            .unwrap_or((1.0, 1.0));
        let (cx, cy) = node
            .bounds
            .map(|b| (b.x + b.width / 2.0, b.y + b.height / 2.0))
            .unwrap_or((0.0, 0.0));

        let mut transform = String::new();
        if tx != 0.0 || ty != 0.0 {
            transform.push_str(&format!("translate({tx},{ty}) "));
        }
        if rotate != 0.0 || sx != 1.0 || sy != 1.0 {
            transform.push_str(&format!(
                "translate({cx},{cy}) rotate({rotate}) scale({sx},{sy}) translate({},{})",
                -cx, -cy
            ));
        }

        let mut attrs = String::new();
        if !transform.is_empty() {
            attrs.push_str(&format!(r#" transform="{}""#, transform.trim()));
        }
        if opacity < 1.0 {
            attrs.push_str(&format!(r#" opacity="{opacity}""#));
        }
        if let Some(blur) = number(get("blur")) {
            if blur > 0.0 {
                let id = out.blur_filter(blur);
                attrs.push_str(&format!(r#" filter="url(#{id})""#));
            }
        }
        if let (Some(IRValue::Text(clip)), Some(b)) = (get("clip"), node.bounds) {
            if let Some([top, right, bottom, left]) = parse_inset(clip) {
                let x = b.x + b.width * left / 100.0;
                let y = b.y + b.height * top / 100.0;
                let w = b.width * (1.0 - (left + right) / 100.0);
                let h = b.height * (1.0 - (top + bottom) / 100.0);
                let id = out.clip_rect(x, y, w, h);
                attrs.push_str(&format!(r#" clip-path="url(#{id})""#));
            }
        }

        out.body.push_str(&format!("<g{attrs}>"));
        match node.node_type.as_str() {
            "group" | "scene" | "ground" | "camera" => {
                let mut children: Vec<&IRNode> = node
                    .children
                    .iter()
                    .filter_map(|id| self.node_map.get(id))
                    .collect();
                children.sort_by_key(|n| n.z_index.unwrap_or(0));
                for child in children {
                    self.render_node(out, child, t);
                }
            }
            "text" => {
                if let Some(text) = &node.text {
                    let colour = match get("color") {
                        Some(IRValue::Text(c)) => Some(c.clone()),
                        _ => text.color.clone(),
                    };
                    self.draw_text(out, node, text, colour.as_deref());
                }
            }
            "shape" => draw_shape(out, node),
            "image" => {
                if let (Some(key), Some(b)) = (self.images.get(&node.id), node.bounds) {
                    out.body.push_str(&format!(
                        r#"<image x="{}" y="{}" width="{}" height="{}" preserveAspectRatio="xMidYMid meet" href="{}"/>"#,
                        b.x, b.y, b.width, b.height, escape_xml(key)
                    ));
                }
            }
            "svg" => draw_svg_path(out, node),
            _ => {}
        }
        out.body.push_str("</g>");
    }

    fn draw_text(&self, out: &mut FrameSvg, node: &IRNode, text: &IRTextContent, colour: Option<&str>) {
        let Some(b) = node.bounds else {
            eprintln!("WARNING: text node {} has no bounds; nothing drawn", node.id);
            return;
        };
        let Some(m) = self.metrics.get(&node.id).copied() else { return };
        let size = text.font_size.unwrap_or(16.0);
        let line_box = text
            .line_height
            .map(|l| l * size)
            .unwrap_or((m.ascent + m.descent + m.line_gap) * size);
        // CSS counts letter spacing after the last glyph too, so a centred
        // or right-aligned line sits that much further left than SVG puts it.
        let spacing = text.letter_spacing.unwrap_or(0.0);
        let (anchor, x) = match text.text_align.as_deref() {
            Some("center") => ("middle", b.x + b.width / 2.0 - spacing / 2.0),
            Some("right") | Some("end") => ("end", b.x + b.width - spacing),
            _ => ("start", b.x),
        };
        let mut font = String::new();
        if let Some(family) = &text.font_family {
            font.push_str(&format!(r#" font-family="'{}'""#, escape_xml(family)));
        }
        font.push_str(&format!(r#" font-size="{size}""#));
        if let Some(weight) = text.font_weight {
            font.push_str(&format!(r#" font-weight="{}""#, weight.round()));
        }
        if let Some(spacing) = text.letter_spacing {
            if spacing != 0.0 {
                font.push_str(&format!(r#" letter-spacing="{spacing}""#));
            }
        }
        let fill = colour.unwrap_or("#000000");
        let shadows = text.text_shadow.as_deref().map(parse_text_shadow).unwrap_or_default();
        let shown = apply_text_transform(&text.text, text.text_transform.as_deref());

        for (i, line) in shown.split('\n').enumerate() {
            if line.trim().is_empty() {
                continue;
            }
            let top = b.y + i as f32 * line_box;
            let baseline = top + (line_box - (m.ascent + m.descent) * size) / 2.0 + m.ascent * size;
            let content = escape_xml(line);
            // CSS paints the first shadow on top, so the last goes down first.
            for shadow in shadows.iter().rev() {
                let mut extra = String::new();
                if shadow.blur > 0.0 {
                    let id = out.blur_filter(shadow.blur / 2.0);
                    extra.push_str(&format!(r#" filter="url(#{id})""#));
                }
                let shadow_paint = paint_attrs("fill", shadow.colour.as_deref().unwrap_or(fill));
                out.body.push_str(&format!(
                    r#"<g transform="translate({},{})"{extra}><text x="{x}" y="{baseline}" text-anchor="{anchor}" xml:space="preserve"{font}{shadow_paint}>{content}</text></g>"#,
                    shadow.dx, shadow.dy
                ));
            }
            out.body.push_str(&format!(
                r#"<text x="{x}" y="{baseline}" text-anchor="{anchor}" xml:space="preserve"{font}{}>{content}</text>"#,
                paint_attrs("fill", fill)
            ));
        }
    }
}

fn draw_shape(out: &mut FrameSvg, node: &IRNode) {
    let (Some(shape), Some(b)) = (&node.shape, node.bounds) else { return };
    let mut style = paint_attrs("fill", shape.fill.as_deref().unwrap_or("none"));
    if let Some(stroke) = &shape.stroke {
        style.push_str(&paint_attrs("stroke", stroke));
        style.push_str(&format!(r#" stroke-width="{}""#, shape.stroke_width.unwrap_or(1.0)));
    }
    match shape.shape.as_str() {
        "rect" => {
            let r = shape.corner_radius.unwrap_or(0.0).min(b.width / 2.0).min(b.height / 2.0);
            out.body.push_str(&format!(
                r#"<rect x="{}" y="{}" width="{}" height="{}" rx="{r}" ry="{r}"{style}/>"#,
                b.x, b.y, b.width, b.height
            ));
        }
        "circle" | "ellipse" => {
            out.body.push_str(&format!(
                r#"<ellipse cx="{}" cy="{}" rx="{}" ry="{}"{style}/>"#,
                b.x + b.width / 2.0,
                b.y + b.height / 2.0,
                b.width / 2.0,
                b.height / 2.0
            ));
        }
        "path" => {
            if let Some(path) = &shape.path {
                out.body.push_str(&format!(r#"<path d="{}"{style}/>"#, escape_xml(path)));
            }
        }
        other => eprintln!("WARNING: shape {other:?} of node {} is not drawn", node.id),
    }
}

fn draw_svg_path(out: &mut FrameSvg, node: &IRNode) {
    let (Some(svg), Some(b)) = (&node.svg, node.bounds) else { return };
    let Some(path) = &svg.path else { return };
    let view: Vec<f32> = svg
        .view_box
        .as_deref()
        .unwrap_or("")
        .split(|c: char| c == ' ' || c == ',')
        .filter_map(|p| p.parse::<f32>().ok())
        .collect();
    let (vx, vy, vw, vh) = if view.len() == 4 && view[2] > 0.0 && view[3] > 0.0 {
        (view[0], view[1], view[2], view[3])
    } else {
        (0.0, 0.0, b.width.max(1.0), b.height.max(1.0))
    };
    out.body.push_str(&format!(
        r#"<path d="{}" transform="translate({},{}) scale({},{}) translate({},{})"{}/>"#,
        escape_xml(path),
        b.x,
        b.y,
        b.width / vw,
        b.height / vh,
        -vx,
        -vy,
        paint_attrs("fill", svg.fill.as_deref().unwrap_or("#000000"))
    ));
}

// ── Main ───────────────────────────────────────────────────────────

fn main() {
    let args: Vec<String> = std::env::args().collect();
    if args.len() == 2 && args[1] == "--version" {
        println!("ren-graphics-fframes {}", env!("CARGO_PKG_VERSION"));
        return;
    }
    if args.len() < 3 || args[1] != "render" {
        eprintln!("Usage: ren-graphics-fframes render <scene.json> [output_dir]");
        eprintln!("       ren-graphics-fframes --version");
        std::process::exit(1);
    }
    let scene_path = PathBuf::from(&args[2]);
    let output_dir = args.get(3).map(PathBuf::from).unwrap_or_else(|| PathBuf::from("frames"));

    let scene_json = match fs::read_to_string(&scene_path) {
        Ok(s) => s,
        Err(e) => {
            eprintln!("ERROR: cannot read scene file {}: {}", scene_path.display(), e);
            std::process::exit(1);
        }
    };
    let scene: Scene = match serde_json::from_str(&scene_json) {
        Ok(s) => s,
        Err(e) => {
            eprintln!("ERROR: cannot parse scene JSON: {}", e);
            std::process::exit(1);
        }
    };
    if scene.version != 1 {
        eprintln!("ERROR: unsupported scene version: {}", scene.version);
        std::process::exit(1);
    }
    if let Err(e) = render_scene(scene, &output_dir) {
        eprintln!("ERROR: render failed: {}", e);
        std::process::exit(1);
    }
}

/// The scene's fonts and images, handed to FFrames as its media.
///
/// FFrames resolves an `<image href>` only against preloaded image data,
/// so every image node's pixels are decoded here once and named; the
/// returned map is node id -> that name. The bytes live for the whole
/// process, which is one render.
fn scene_media(scene: &Scene) -> Result<(DynamicMediaProvider<'static>, HashMap<String, String>), String> {
    let mut fonts: Vec<RawFontData> = Vec::new();
    let mut seen: HashSet<PathBuf> = HashSet::new();
    for node in &scene.nodes {
        let Some(font_file) = node.text.as_ref().and_then(|t| t.font_file.as_ref()) else {
            continue;
        };
        let path = PathBuf::from(font_file);
        if !seen.insert(path.clone()) {
            continue;
        }
        let data = fs::read(&path).map_err(|e| format!("cannot read font file {}: {}", path.display(), e))?;
        fonts.push(RawFontData {
            file_name: path
                .file_name()
                .map(|name| name.to_string_lossy().into_owned())
                .unwrap_or_else(|| path.to_string_lossy().into_owned()),
            data: Arc::new(data),
        });
    }
    let mut images = HashMap::new();
    let mut names = HashMap::new();
    for node in &scene.nodes {
        let Some(image) = &node.image else { continue };
        let bytes: &'static [u8] = Box::leak(image_bytes(&image.src)?.into_boxed_slice());
        let name = format!("ren-image-{}", names.len());
        let data = fframes::media::ImageData::new_from_bytes(&name, bytes)
            .map_err(|e| format!("image of node {} does not decode: {:?}", node.id, e))?;
        images.insert(name.clone(), data);
        names.insert(node.id.clone(), name);
    }
    let provider = DynamicMediaProvider::new(
        Default::default(),
        images,
        Default::default(),
        Default::default(),
        fonts,
    );
    Ok((provider, names))
}

fn render_scene(scene: Scene, output_dir: &std::path::Path) -> Result<(), String> {
    fs::create_dir_all(output_dir).map_err(|e| format!("cannot create output dir: {}", e))?;

    // Draw only the roots; a group draws its own children.
    let child_ids: HashSet<&String> = scene.nodes.iter().flat_map(|n| n.children.iter()).collect();
    let mut roots: Vec<&IRNode> = scene.nodes.iter().filter(|n| !child_ids.contains(&n.id)).collect();
    roots.sort_by_key(|n| n.z_index.unwrap_or(0));
    let roots: Vec<String> = roots.into_iter().map(|n| n.id.clone()).collect();
    let node_map: HashMap<String, IRNode> =
        scene.nodes.iter().map(|n| (n.id.clone(), n.clone())).collect();
    let metrics = text_metrics(&scene);

    let needs_system = scene
        .nodes
        .iter()
        .any(|n| n.text.as_ref().map_or(false, |t| t.font_file.is_none()));
    let (media, images) = scene_media(&scene)?;
    let (width, height) = (scene.width, scene.height);
    let total_frames = frame_count(&scene);

    let video = SceneVideo { scene, node_map, roots, metrics, images };
    let mut options = fframes::RenderOptions::default();
    options.load_system_fonts = needs_system;
    options.media = Some(&media);
    let mut previewer = fframes::Previewer::new(&video, &options)
        .map_err(|e| format!("cannot create previewer: {}", e))?;

    let metal_ctx = SkiaMetalCtx::new(width as usize, height as usize)
        .map_err(|e| format!("cannot create Metal context: {}", e))?;
    let mut renderer = SkiaFrameRenderer::new(&metal_ctx);

    for frame_idx in 0..total_frames {
        let tree = previewer
            .svg_tree(frame_idx)
            .map_err(|e| format!("frame {} conversion failed: {}", frame_idx, e))?;
        let pixels = renderer
            .render_tree(&tree, fframes::Color::TRANSPARENT, width, height)
            .map_err(|e| format!("frame {} render failed: {}", frame_idx, e))?;
        let path = output_dir.join(format!("frame_{:06}.png", frame_idx + 1));
        pixels.save_png(&path).map_err(|e| format!("cannot save PNG: {}", e))?;
    }

    println!("Rendered {} frames to {}", total_frames, output_dir.display());
    Ok(())
}
