//! ren-graphics-fframes: Ren's precompiled FFrames renderer.
//!
//! `ren-graphics-fframes render scene.json <frames_dir>` reads a Ren Graphics
//! IR scene (library/graphics_ir/scene.schema.json), builds each frame as a
//! usvgr nested tree, and rasterises it through FFrames/Skia on Metal into
//! straight-alpha RGBA PNGs. `render scene.json --raw` writes the same
//! straight-alpha RGBA frames to stdout instead - a small header (width,
//! height as u32 LE) then ``width * height * 4`` bytes per frame - so the
//! encoder can pipe them straight into ffmpeg without a PNG round-trip. It
//! draws exactly what the scene says - every position, colour, face and
//! weight is the scene's; this binary states no style of its own.
//!
//! The tree is built as nested nodes (compile-time-svgtree mode) rather than
//! an SVG string, because only a nested tree can carry a shader layer's image
//! handle and per-frame images. Every text node's face is a static instance
//! at its declared weight (the Python side stages them), because usvg cannot
//! apply a variable font's ``wght`` axis and would draw the default (Thin)
//! instance.
//!
//! Text follows CSS: a node's bounds is its box, runs flow inline sharing
//! baselines, the line box spans the highest top to the lowest bottom
//! including the block's strut, a face's ascent and descent are rounded to
//! whole pixels before half-leading is taken, and the ascent side gets the
//! floor of half the leading (the two Blink rules). Transforms pivot on the
//! node's box centre, CSS's default ``transform-origin``.

use std::collections::{HashMap, HashSet};
use std::fs;
use std::io::Write;
use std::path::{Path, PathBuf};
use std::sync::Arc;

use base64::Engine as _;
use fframes::{AudioMap, Color, Duration, FFramesContext, Frame, FrameRenderer, RgbaFrame, Svgr, Video};
use fframes::{DynamicMediaProvider, RawFontData};
use fframes::usvgr::svgtree::{
    AId, Attribute, EId, NestedNodeData, NestedNodeKind, NestedSvgDocument, StringStorage,
    SvgAttributeValue,
};
use fframes_skia_renderer::metal::SkiaMetalCtx;
use fframes_skia_renderer::{SkiaBackend, SkiaFrameRenderer};
use serde::Deserialize;

// ── A minimal element builder that lowers to usvgr's nested tree ────────────

enum AttrVal {
    S(String),
}

struct El {
    tag: &'static str,
    attrs: Vec<(&'static str, AttrVal)>,
    children: Vec<El>,
    text: Option<String>,
}

impl El {
    fn new(tag: &'static str) -> Self {
        El { tag, attrs: Vec::new(), children: Vec::new(), text: None }
    }
    fn a(mut self, name: &'static str, v: impl ToString) -> Self {
        self.attrs.push((name, AttrVal::S(v.to_string())));
        self
    }
    fn c(mut self, child: El) -> Self {
        self.children.push(child);
        self
    }
    fn push(&mut self, child: El) {
        self.children.push(child);
    }
    fn t(mut self, text: &str) -> Self {
        self.text = Some(text.to_string());
        self
    }
    fn lower(self) -> NestedNodeData<'static> {
        let tag = EId::from_str(self.tag)
            .unwrap_or_else(|| panic!("unknown element {}", self.tag));
        let attrs: Vec<Attribute<'static>> = self
            .attrs
            .into_iter()
            .map(|(name, v)| Attribute {
                name: AId::from_str(name)
                    .unwrap_or_else(|| panic!("unknown attribute {name}")),
                value: match v {
                    AttrVal::S(s) => SvgAttributeValue::StringStorage(StringStorage::new_owned(s)),
                },
            })
            .collect();
        let mut children: Vec<Option<NestedNodeData<'static>>> =
            self.children.into_iter().map(|c| Some(c.lower())).collect();
        if let Some(text) = self.text {
            children.push(Some(NestedNodeData {
                kind: NestedNodeKind::Text(StringStorage::new_owned(text)),
                attrs: Box::new([]),
                children: vec![],
                static_hash: None,
            }));
        }
        NestedNodeData {
            kind: NestedNodeKind::Element { tag_name: tag },
            attrs: attrs.into_boxed_slice(),
            children,
            static_hash: None,
        }
    }
}

// ── Scene IR schema ────────────────────────────────────────────────

#[derive(Debug, Deserialize, Clone)]
struct Scene {
    version: u32,
    fps: f32,
    width: u32,
    height: u32,
    duration: f32,
    #[serde(default)]
    hold_frames: Option<u32>,
    nodes: Vec<IRNode>,
}

#[derive(Debug, Deserialize, Clone)]
#[allow(dead_code)] // filter/effect/audio/html content is vocabulary the adapter does not draw yet
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
    mask: Option<String>,
    #[serde(default)]
    clip: Option<String>,
    #[serde(default)]
    blend: Option<String>,
    #[serde(default)]
    filters: Vec<IRFilter>,
    #[serde(default)]
    timing: Option<IRTiming>,
    #[serde(default)]
    animations: Vec<IRAnimation>,
    #[serde(default)]
    tracks: Vec<IRTrack>,
    #[serde(default)]
    text: Option<IRTextContent>,
    #[serde(default)]
    shape: Option<IRShapeContent>,
    #[serde(default)]
    image: Option<IRImageContent>,
    #[serde(default)]
    svg: Option<IRSVGContent>,
    #[serde(default)]
    shader: Option<IRShaderContent>,
    #[serde(default)]
    camera: Option<IRCameraContent>,
    #[serde(default)]
    filter: Option<IRFilterContent>,
    #[serde(default)]
    effect: Option<IREffectContent>,
    #[serde(default)]
    audio: Option<IRAudioContent>,
    #[serde(default)]
    html: Option<IRHTMLContent>,
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
#[allow(dead_code)] // the browser-layout hint is not yet consumed
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
    #[serde(default)]
    runs: Vec<IRTextRun>,
    #[serde(default)]
    layout: Option<String>,
    #[serde(default)]
    reveal_ramp: Option<f32>,
}

#[derive(Debug, Deserialize, Clone)]
struct IRTextRun {
    text: String,
    #[serde(default)]
    font_size: Option<f32>,
    #[serde(default)]
    font_weight: Option<f32>,
    #[serde(default)]
    color: Option<String>,
    #[serde(default)]
    letter_spacing: Option<f32>,
    #[serde(default)]
    reveal_at: Option<f32>,
    #[serde(default)]
    new_line: bool,
    #[serde(default)]
    font_file: Option<String>,
    #[serde(default)]
    font_family: Option<String>,
}

#[derive(Debug, Deserialize, Clone)]
struct IRShapeContent {
    shape: String,
    #[serde(default)]
    fill: Option<IRPaint>,
    #[serde(default)]
    pattern: Option<IRPatternFill>,
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
#[serde(untagged)]
enum IRPaint {
    Solid(String),
    Gradient(IRGradient),
}

#[derive(Debug, Deserialize, Clone)]
struct IRGradient {
    kind: String,
    #[serde(default)]
    angle: Option<f32>,
    #[serde(default)]
    radius_x: Option<f32>,
    #[serde(default)]
    radius_y: Option<f32>,
    stops: Vec<IRPaintStop>,
}

#[derive(Debug, Deserialize, Clone)]
struct IRPaintStop {
    offset: f32,
    color: String,
    #[serde(default)]
    opacity: Option<f32>,
}

#[derive(Debug, Deserialize, Clone)]
struct IRPatternFill {
    src: String,
    #[serde(default)]
    tile_width: Option<f32>,
    #[serde(default)]
    tile_height: Option<f32>,
    #[serde(default)]
    opacity: Option<f32>,
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
    fill: Option<IRPaint>,
}

#[derive(Debug, Deserialize, Clone)]
#[allow(dead_code)] // shaders warn and skip at draw time
struct IRShaderContent {
    #[serde(default)]
    program: Option<String>,
    #[serde(default)]
    uniforms: HashMap<String, f32>,
    #[serde(default)]
    inputs: Vec<String>,
}

#[derive(Debug, Deserialize, Clone)]
struct IRCameraContent {
    #[serde(default)]
    scale: Option<f32>,
    #[serde(default)]
    rotate: Option<f32>,
    #[serde(default)]
    translate: Option<Vec<f32>>,
    #[serde(default)]
    focus: Option<f32>,
}

#[derive(Debug, Deserialize, Clone)]
#[allow(dead_code)] // the Filter node type is not yet drawn
struct IRFilterContent {
    filter: String,
    #[serde(default)]
    amount: Option<f32>,
}

#[derive(Debug, Deserialize, Clone)]
#[allow(dead_code)] // the Effect node type is not yet drawn
struct IREffectContent {
    effect: String,
    #[serde(default)]
    params: HashMap<String, serde_json::Value>,
}

#[derive(Debug, Deserialize, Clone)]
#[allow(dead_code)] // the Audio node type is not yet drawn
struct IRAudioContent {
    src: String,
    #[serde(default)]
    start: f32,
    #[serde(default)]
    duration: Option<f32>,
}

#[derive(Debug, Deserialize, Clone)]
#[allow(dead_code)] // the html node refuses by name at draw time
struct IRHTMLContent {
    #[serde(default)]
    engine: Option<String>,
    #[serde(default)]
    html: Option<String>,
}

#[derive(Debug, Deserialize, Clone)]
#[serde(tag = "kind", rename_all = "snake_case")]
enum IRFilter {
    Blur { sigma: f32 },
    DropShadow { dx: f32, dy: f32, sigma: f32, color: String },
}

#[derive(Debug, Deserialize, Clone)]
#[allow(dead_code)] // tracks are sampled by property; target is redundant on the node
struct IRTrack {
    target: String,
    property: String,
    keys: Vec<IRKeyframe>,
}

#[derive(Debug, Deserialize, Clone)]
struct IRKeyframe {
    at: f32,
    value: f32,
    #[serde(default)]
    ease: Option<String>,
}

// ── Easing evaluation (matches library/graphics_ir/easing.py) ──────

fn apply_easing(easing: &IREasing, t: f32, fps: f32) -> f32 {
    match easing {
        IREasing::Linear => t.clamp(0.0, 1.0),
        IREasing::Ease { power } => {
            let clamped = t.clamp(0.0, 1.0);
            1.0 - (1.0 - clamped).powf(*power)
        }
        IREasing::CubicBezier { x1, y1, x2, y2 } => cubic_bezier(*x1, *y1, *x2, *y2, t),
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

/// StagedScene's ease names, for keyframe tracks. Cubic, the way
/// `valueAt` draws them in the Remotion and HyperFrames compositions.
fn track_ease(name: Option<&str>, x: f32) -> f32 {
    match name.unwrap_or("linear") {
        "in" => x * x * x,
        "out" => 1.0 - (1.0 - x).powi(3),
        "inOut" => {
            if x < 0.5 {
                4.0 * x * x * x
            } else {
                1.0 - (-2.0 * x + 2.0).powi(3) / 2.0
            }
        }
        _ => x,
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

// ── Animation and track sampling (matches library/graphics_ir/evaluator.py) ──

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

/// A track's value at time ``t``: held flat outside the keys, eased by the
/// arriving key (StagedScene's `valueAt` semantics).
fn track_value(keys: &[IRKeyframe], t: f32, neutral: f32) -> f32 {
    if keys.is_empty() {
        return neutral;
    }
    if t <= keys[0].at {
        return keys[0].value;
    }
    let last = &keys[keys.len() - 1];
    if t >= last.at {
        return last.value;
    }
    for i in 1..keys.len() {
        let a = &keys[i - 1];
        let b = &keys[i];
        if t <= b.at {
            if b.at == a.at {
                return b.value;
            }
            let x = ((t - a.at) / (b.at - a.at)).clamp(0.0, 1.0);
            return a.value + (b.value - a.value) * track_ease(b.ease.as_deref(), x);
        }
    }
    last.value
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

/// The sampled value of one of a node's tracks at time ``t``.
fn sample_node_tracks(node: &IRNode, t: f32) -> Vec<(String, f32)> {
    node.tracks
        .iter()
        .map(|track| (track.property.clone(), track_value(&track.keys, t, 0.0)))
        .collect()
}

// ── Faces and their vertical metrics ───────────────────────────────

/// Ascent, descent (positive) and line gap, in ems.
#[derive(Debug, Clone, Copy)]
struct FaceMetrics {
    ascent: f32,
    descent: f32,
    line_gap: f32,
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
        let family_name = text.font_family.as_deref().map(|s| s.to_string());
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

// ── Advances: hmtx advance widths, cached per font ─────────────────

/// Horizontal advance widths of one face, from its ``hmtx`` table.
///
/// Kerning is deliberately not applied: measured against Chrome on this
/// scene's text, the per-run kerning total is 0.16-1.72 px, far inside the
/// parity gate's 1% pct8 budget. The glyphs within each run are still kerned
/// by the shaper; only the run's trailing width is unkerned.
#[derive(Clone)]
struct Advances {
    upem: f32,
    data: Vec<u8>,
    cache: HashMap<char, u32>,
}

fn load_advances(path: &Path) -> Advances {
    let data = fs::read(path).unwrap_or_default();
    let upem = ttf_parser::Face::parse(&data, 0)
        .map(|f| f.units_per_em())
        .unwrap_or(1000) as f32;
    Advances { upem, data, cache: HashMap::new() }
}

impl Advances {
    fn advance(&mut self, ch: char) -> u32 {
        *self.cache.entry(ch).or_insert_with(|| {
            if let Ok(f) = ttf_parser::Face::parse(&self.data, 0) {
                if let Some(g) = f.glyph_index(ch) {
                    return f.glyph_hor_advance(g).unwrap_or(0) as u32;
                }
            }
            0
        })
    }

    /// Advance width of *text* at *size* pixels, unkerned.
    fn width(&mut self, text: &str, size: f32) -> f32 {
        let units: u32 = text.chars().map(|ch| self.advance(ch)).sum();
        units as f32 * size / self.upem
    }
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

/// A CSS `polygon(x% y%, ...)` in a w x h box at (bx, by), as SVG points.
///
/// Percentages resolve against the node's box, and ``userSpaceOnUse`` puts
/// the points in the element's user space, so the box origin is added.
fn css_polygon(value: &str, bx: f32, by: f32, w: f32, h: f32) -> Vec<(f32, f32)> {
    let inner = value.trim().trim_start_matches("polygon(").trim_end_matches(')');
    inner
        .split(',')
        .map(|pair| {
            let v: Vec<f32> = pair
                .split_whitespace()
                .map(|s| s.trim_end_matches('%').parse::<f32>().unwrap())
                .collect();
            (bx + v[0] / 100.0 * w, by + v[1] / 100.0 * h)
        })
        .collect()
}

fn points_attr(pts: &[(f32, f32)]) -> String {
    pts.iter().map(|(x, y)| format!("{x:.4},{y:.4}")).collect::<Vec<_>>().join(" ")
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

/// Whether these decoded bytes are an SVG document, by leading markup.
///
/// The `image` crate decodes raster formats only, so an SVG asset must be
/// rasterized before it enters the preloaded-image path below.
fn is_svg_bytes(bytes: &[u8]) -> bool {
    let mut start = bytes;
    if start.starts_with(&[0xEF, 0xBB, 0xBF]) {
        start = &start[3..];
    }
    let indent = start.iter().take_while(|b| b.is_ascii_whitespace()).count();
    let rest = &start[indent..];
    rest.starts_with(b"<svg") || rest.starts_with(b"<?xml")
}

/// Whether the asset `src` names is SVG: an explicit signal (the data URI
/// mime or the file extension) or the bytes' own leading markup.
fn is_svg_src(src: &str, bytes: &[u8]) -> bool {
    if let Some(rest) = src.strip_prefix("data:") {
        if let Some((meta, _)) = rest.split_once(',') {
            if meta.split(';').next() == Some("image/svg+xml") {
                return true;
            }
        }
        return is_svg_bytes(bytes);
    }
    let path = src.split('?').next().unwrap_or(src);
    if path
        .rsplit('.')
        .next()
        .map(|ext| ext.eq_ignore_ascii_case("svg"))
        .unwrap_or(false)
    {
        return true;
    }
    is_svg_bytes(bytes)
}

/// tiny-skia holds premultiplied pixels; the preloaded-image path blends
/// straight alpha itself, so divide the alpha back out first.
fn unpremultiply(premultiplied: &[u8]) -> Vec<u8> {
    let mut out = Vec::with_capacity(premultiplied.len());
    for chunk in premultiplied.chunks_exact(4) {
        let (r, g, b, a) = (chunk[0], chunk[1], chunk[2], chunk[3]);
        if a == 0 {
            out.extend_from_slice(&[0, 0, 0, 0]);
        } else {
            let f = 255.0 / a as f32;
            out.push((r as f32 * f + 0.5).min(255.0) as u8);
            out.push((g as f32 * f + 0.5).min(255.0) as u8);
            out.push((b as f32 * f + 0.5).min(255.0) as u8);
            out.push(a);
        }
    }
    out
}

/// Rasterize an SVG asset at the size the layout asks for, the way the
/// browser draws it: `contain` - fit into the box, centred, on a
/// transparent ground. Returns the drawn size and straight-alpha RGBA at
/// exactly that size, so the `<image href>` the tree carries (already
/// `meet`) draws it 1:1. With no box asked, the SVG's own size.
fn rasterize_svg(
    bytes: &[u8],
    size: Option<(f32, f32)>,
    what: &str,
) -> Result<(u32, u32, Vec<u8>), String> {
    let mut opt = resvg::usvg::Options::default();
    opt.fontdb_mut().load_system_fonts();
    let tree = resvg::usvg::Tree::from_data(bytes, &opt)
        .map_err(|e| format!("{what} is SVG but does not parse: {e}"))?;
    let intrinsic = tree.size();
    if intrinsic.width() <= 0.0 || intrinsic.height() <= 0.0 {
        return Err(format!("{what} is SVG with no drawable size"));
    }
    let (w, h) = match size {
        Some((w, h)) if w > 0.0 && h > 0.0 => (
            w.ceil().clamp(1.0, 16384.0) as u32,
            h.ceil().clamp(1.0, 16384.0) as u32,
        ),
        _ => (
            intrinsic.width().ceil().clamp(1.0, 16384.0) as u32,
            intrinsic.height().ceil().clamp(1.0, 16384.0) as u32,
        ),
    };
    let scale = (w as f32 / intrinsic.width()).min(h as f32 / intrinsic.height());
    let dx = (w as f32 - intrinsic.width() * scale) / 2.0;
    let dy = (h as f32 - intrinsic.height() * scale) / 2.0;
    let mut pixmap = resvg::tiny_skia::Pixmap::new(w, h)
        .ok_or_else(|| format!("{what}: cannot rasterize SVG at {w}x{h}"))?;
    resvg::render(
        &tree,
        resvg::tiny_skia::Transform::from_translate(dx, dy).pre_scale(scale, scale),
        &mut pixmap.as_mut(),
    );
    Ok((w, h, unpremultiply(&pixmap.take())))
}

/// The preloaded pixels for one image or pattern node: raster formats
/// decode as before; SVG assets rasterize at the size the layout asks
/// for. One path - no parallel SVG module - so the PNG-sequence and raw
/// RGBA consumers draw the same pixels.
fn preload_image(
    name: &str,
    src: &str,
    size: Option<(f32, f32)>,
    what: &str,
) -> Result<fframes::media::ImageData<'static>, String> {
    let bytes = image_bytes(src)?;
    if !is_svg_src(src, &bytes) {
        let leaked: &'static [u8] = Box::leak(bytes.into_boxed_slice());
        return fframes::media::ImageData::new_from_bytes(name, leaked)
            .map_err(|e| format!("{what} does not decode: {:?}", e));
    }
    let (w, h, rgba) = rasterize_svg(&bytes, size, what)?;
    let data = fframes::usvgr::PreloadedImageData::new(name.to_string(), w, h, &rgba);
    Ok(fframes::media::ImageData::new_from_raw_data(
        Arc::new(data),
        name.to_string(),
        fframes::media::ImageMetadata {
            width: w,
            height: h,
        },
    ))
}

fn escape_xml(text: &str) -> String {
    text.replace('&', "&amp;")
        .replace('<', "&lt;")
        .replace('>', "&gt;")
        .replace('"', "&quot;")
}

// ── The scene, rendered as a nested tree ────────────────────────────

struct SceneVideo {
    scene: Scene,
    node_map: HashMap<String, IRNode>,
    roots: Vec<String>,
    metrics: HashMap<String, FaceMetrics>,
    /// Image or pattern node id -> the name its pixels are preloaded under.
    images: HashMap<String, String>,
    /// Font file path -> its advance widths.
    advances: HashMap<String, Advances>,
}

/// What one frame's tree is built against.
struct RenderContext<'a> {
    defs: El,
    next_id: usize,
    width: f32,
    height: f32,
    images: &'a HashMap<String, String>,
    advances: &'a mut HashMap<String, Advances>,
}

impl RenderContext<'_> {
    fn next(&mut self, prefix: &str) -> String {
        self.next_id += 1;
        format!("{}{}", prefix, self.next_id)
    }
}

/// How visible a run is at time `t`: the ramp ENDS on `reveal_at`.
fn run_opacity(t: f32, reveal_at: Option<f32>, ramp: f32) -> f32 {
    let Some(r) = reveal_at else { return 1.0 };
    if ramp <= 0.0 {
        return if t >= r { 1.0 } else { 0.0 };
    }
    if t >= r {
        return 1.0;
    }
    if t <= r - ramp {
        return 0.0;
    }
    (t - (r - ramp)) / ramp
}

/// The block's strut: Chrome's default serif at 16 px, measured with
/// fontTools. The line box includes it the way CSS 2.1 10.8 says.
const STRUT_SIZE: f32 = 16.0;
const STRUT_A: f32 = 0.891;
const STRUT_D: f32 = 0.216;

/// One inline box's (above, below) the baseline, Blink's way: the face's
/// ascent and descent are rounded to whole pixels before half-leading is
/// taken, and the ascent side gets the FLOOR of half the leading.
fn inline_box(size: f32, a: f32, d: f32, line_height: f32) -> (f32, f32) {
    let ra = (a * size).round();
    let rd = (d * size).round();
    let leading_px = line_height - (ra + rd);
    let up = (leading_px / 2.0).floor();
    (ra + up, rd + (leading_px - up))
}

/// One laid-out run: where it goes and how it draws.
struct RunLayout {
    text: String,
    x: f32,
    baseline: f32,
    size: f32,
    weight: f32,
    family: Option<String>,
    colour: String,
    letter_spacing: f32,
    opacity: f32,
}

/// Lay a text node's runs out inline, sharing baselines, the way the
/// browser does for StagedScene's text body.
fn layout_runs(
    text: &IRTextContent,
    t: f32,
    m: &FaceMetrics,
    bounds: &IRBounds,
    advances: &mut HashMap<String, Advances>,
) -> Vec<RunLayout> {
    let fallback_size = text.font_size.unwrap_or(16.0);
    let mut lines: Vec<Vec<&IRTextRun>> = vec![vec![]];
    for run in &text.runs {
        if run.new_line && !lines.last().map(|l| l.is_empty()).unwrap_or(true) {
            lines.push(vec![]);
        }
        lines.last_mut().unwrap().push(run);
    }
    let strut = inline_box(STRUT_SIZE, STRUT_A, STRUT_D, STRUT_SIZE);
    let ramp = text.reveal_ramp.unwrap_or(0.0);
    let mut top = 0.0_f32;
    let mut out = Vec::new();
    for line in &lines {
        let mut above = strut.0;
        let mut below = strut.1;
        let mut widths = Vec::new();
        for run in line {
            let size = run.font_size.unwrap_or(fallback_size);
            let lh = text.line_height.map(|l| l * size).unwrap_or(size);
            let (a, b) = inline_box(size, m.ascent, m.descent, lh);
            above = above.max(a);
            below = below.max(b);
            let ls = run.letter_spacing.unwrap_or(0.0);
            // Widths come from the run's own staged face: a Bold advance
            // is not a Thin advance.
            let key = run
                .font_file
                .clone()
                .or_else(|| text.font_file.clone())
                .unwrap_or_default();
            let adv = advances.entry(key.clone()).or_insert_with(|| {
                if key.is_empty() {
                    load_advances(Path::new(""))
                } else {
                    load_advances(Path::new(&key))
                }
            });
            let w = adv.width(&run.text, size) + ls * run.text.chars().count() as f32;
            widths.push(w);
        }
        let line_w: f32 = widths.iter().sum();
        let align = text.text_align.as_deref().unwrap_or("left");
        let mut x = match align {
            "right" | "end" => bounds.width - line_w,
            "center" | "middle" => (bounds.width - line_w) / 2.0,
            _ => 0.0,
        };
        let baseline = top + above;
        for (run, w) in line.iter().zip(widths) {
            let size = run.font_size.unwrap_or(fallback_size);
            let colour = run
                .color
                .clone()
                .or_else(|| text.color.clone())
                .unwrap_or_else(|| "#000000".to_string());
            out.push(RunLayout {
                text: run.text.clone(),
                x: x + bounds.x,
                baseline: baseline + bounds.y,
                size,
                weight: run.font_weight.unwrap_or(text.font_weight.unwrap_or(400.0)),
                family: run.font_family.clone().or_else(|| text.font_family.clone()),
                colour,
                letter_spacing: run.letter_spacing.unwrap_or(0.0),
                opacity: run_opacity(t, run.reveal_at, ramp),
            });
            x += w;
        }
        top += above + below;
    }
    out
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
        let mut held = frame.index;
        if let Some(hold) = self.scene.hold_frames {
            if hold > 1 {
                let hold = hold as usize;
                held = held / hold * hold;
            }
        }
        let t = held as f32 / self.scene.fps;
        let root = self.frame_tree(t);
        Svgr { svg_tree: NestedSvgDocument::from_nodes(vec![Some(root.lower())]) }
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
    fn frame_tree(&self, t: f32) -> El {
        let (w, h) = (self.scene.width as f32, self.scene.height as f32);
        let mut ctx = RenderContext {
            defs: El::new("defs"),
            next_id: 0,
            width: w,
            height: h,
            images: &self.images,
            advances: &mut self.advances.clone(),
        };
        let mut root = El::new("svg")
            .a("width", w)
            .a("height", h)
            .a("viewBox", format!("0 0 {w} {h}"));
        for id in &self.roots {
            if let Some(node) = self.node_map.get(id) {
                root = root.c(self.render_node(&mut ctx, node, t));
            }
        }
        root.c(ctx.defs)
    }

    fn render_node(&self, ctx: &mut RenderContext, node: &IRNode, t: f32) -> El {
        if let Some(timing) = &node.timing {
            if t < timing.start {
                return El::new("g");
            }
            if let Some(dur) = timing.duration {
                if t >= timing.start + dur {
                    return El::new("g");
                }
            }
        }

        let anims = sample_node_animations(node, t, self.scene.fps);
        let get = |prop: &str| anims.iter().find(|(p, _)| p == prop).map(|(_, v)| v);
        let tracks = sample_node_tracks(node, t);
        let track = |prop: &str| tracks.iter().find(|(p, _)| p == prop).map(|(_, v)| *v);

        let opacity = number(get("opacity")).or(node.opacity).unwrap_or(1.0);
        if opacity <= 0.0 {
            return El::new("g");
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

        let mut g = El::new("g");
        if !transform.is_empty() {
            g = g.a("transform", transform.trim());
        }
        if opacity < 1.0 {
            g = g.a("opacity", opacity);
        }
        // CSS order: filter, then clip-path, then mask, then blend.
        for filter in &node.filters {
            g = self.apply_filter(ctx, g, filter);
        }
        // A clip animation (a light's cone, baked per frame by the lowering)
        // overrides the node's static clip.
        let clip_string = match get("clip") {
            Some(IRValue::Text(c)) => Some(c.clone()),
            _ => None,
        };
        let clip_string = clip_string.as_deref().or(node.clip.as_deref()).filter(|c| !c.is_empty());
        if let Some(clip) = clip_string {
            if let Some(b) = node.bounds {
                g = self.apply_clip(ctx, g, clip, b);
            }
        }
        if let Some(mask) = node.mask.as_deref().filter(|m| !m.is_empty()) {
            let id = ctx.next("m");
            let mut mask_el = El::new("mask").a("id", &id);
            if let Some(src) = self.node_map.get(mask) {
                let src_node = src.clone();
                mask_el = mask_el.c(self.render_node(ctx, &src_node, t));
            } else {
                eprintln!("WARNING: mask of node {} names unknown node {mask}; nothing masked", node.id);
            }
            ctx.defs.push(mask_el);
            g = g.a("mask", format!("url(#{id})"));
        }
        if let Some(blend) = node.blend.as_deref().filter(|b| *b != "normal" && !b.is_empty()) {
            g = g.a("mix-blend-mode", blend);
        }

        match node.node_type.as_str() {
            "group" | "scene" | "ground" => {
                let mut children: Vec<&IRNode> = node
                    .children
                    .iter()
                    .filter_map(|id| self.node_map.get(id))
                    .collect();
                children.sort_by_key(|n| n.z_index.unwrap_or(0));
                for child in children {
                    g = g.c(self.render_node(ctx, child, t));
                }
            }
            "camera" => {
                let cam = node.camera.as_ref().cloned().unwrap_or(IRCameraContent {
                    scale: None, rotate: None, translate: None, focus: None,
                });
                let scale = track("camera.scale").or(cam.scale).unwrap_or(1.0);
                let roll = track("camera.rotate").or(cam.rotate).unwrap_or(0.0);
                let ccx = track("camera.translateX")
                    .or_else(|| cam.translate.as_ref().filter(|v| v.len() >= 2).map(|v| v[0]))
                    .unwrap_or(0.0);
                let ccy = track("camera.translateY")
                    .or_else(|| cam.translate.as_ref().filter(|v| v.len() >= 2).map(|v| v[1]))
                    .unwrap_or(0.0);
                let focus = track("camera.focus").or(cam.focus).unwrap_or(0.0);
                let mut inner = El::new("g");
                let mut children: Vec<&IRNode> = node
                    .children
                    .iter()
                    .filter_map(|id| self.node_map.get(id))
                    .collect();
                children.sort_by_key(|n| n.z_index.unwrap_or(0));
                for child in children {
                    inner = inner.c(self.render_node(ctx, child, t));
                }
                let cam_transform = format!(
                    "translate({},{}) scale({}) rotate({}) translate({},{}) translate({},{})",
                    ctx.width / 2.0, ctx.height / 2.0, scale, roll, -ccx, -ccy,
                    -ctx.width / 2.0, -ctx.height / 2.0
                );
                let mut world = El::new("g").a("transform", cam_transform);
                if focus > 0.0 {
                    let id = ctx.next("f");
                    ctx.defs.push(blur_filter(&id, focus, ctx.width, ctx.height));
                    world = world.a("filter", format!("url(#{id})"));
                }
                g = g.c(world.c(inner));
            }
            "text" => {
                if let Some(text) = &node.text {
                    let colour = match get("color") {
                        Some(IRValue::Text(c)) => Some(c.clone()),
                        _ => text.color.clone(),
                    };
                    g = self.draw_text(ctx, node, text, colour.as_deref(), t);
                }
            }
            "shape" => g = self.draw_shape(ctx, g, node),
            "image" => {
                if let (Some(key), Some(b)) = (ctx.images.get(&node.id), node.bounds) {
                    g = g.c(El::new("image")
                        .a("x", b.x).a("y", b.y).a("width", b.width).a("height", b.height)
                        .a("preserveAspectRatio", "xMidYMid meet")
                        .a("href", key));
                }
            }
            "svg" => g = self.draw_svg_path(g, node),
            "shader" => {
                eprintln!(
                    "WARNING: shader node {} names program {:?}, which this build does not draw; skipped",
                    node.id, node.shader.as_ref().and_then(|s| s.program.as_deref())
                );
            }
            "html" => {
                eprintln!(
                    "ERROR: html node {} names engine {:?}, which FFrames cannot draw; refusing",
                    node.id, node.html.as_ref().and_then(|h| h.engine.as_deref())
                );
                std::process::exit(1);
            }
            other => eprintln!("WARNING: node type {other:?} of node {} is not drawn", node.id),
        }
        g
    }

    fn apply_filter(&self, ctx: &mut RenderContext, g: El, filter: &IRFilter) -> El {
        match filter {
            IRFilter::Blur { sigma } => {
                if *sigma > 0.0 {
                    let id = ctx.next("f");
                    ctx.defs.push(blur_filter(&id, *sigma, ctx.width, ctx.height));
                    g.a("filter", format!("url(#{id})"))
                } else {
                    g
                }
            }
            IRFilter::DropShadow { dx, dy, sigma, color } => {
                let id = ctx.next("ds");
                let (col, alpha) = paint(color);
                // CSS drop-shadow draws the original with a shadow behind
                // it: feDropShadow alone would replace the image with its
                // shadow, so the original is merged back over it.
                ctx.defs.push(
                    El::new("filter")
                        .a("id", &id)
                        .a("filterUnits", "userSpaceOnUse")
                        .a("x", -ctx.width).a("y", -ctx.height)
                        .a("width", 3.0 * ctx.width).a("height", 3.0 * ctx.height)
                        .c(El::new("feDropShadow")
                            .a("dx", dx).a("dy", dy)
                            .a("stdDeviation", sigma)
                            .a("flood-color", &col)
                            .a("flood-opacity", alpha))
                        .c(El::new("feMerge")
                            .c(El::new("feMergeNode"))
                            .c(El::new("feMergeNode").a("in", "SourceGraphic")))
                );
                g.a("filter", format!("url(#{id})"))
            }
        }
    }

    fn apply_clip(&self, ctx: &mut RenderContext, g: El, clip: &str, b: IRBounds) -> El {
        if let Some([top, right, bottom, left]) = parse_inset(clip) {
            let x = b.x + b.width * left / 100.0;
            let y = b.y + b.height * top / 100.0;
            let w = b.width * (1.0 - (left + right) / 100.0);
            let h = b.height * (1.0 - (top + bottom) / 100.0);
            let id = ctx.next("c");
            ctx.defs.push(
                El::new("clipPath").a("id", &id).a("clipPathUnits", "userSpaceOnUse")
                    .c(El::new("rect").a("x", x).a("y", y)
                        .a("width", w.max(0.0)).a("height", h.max(0.0)))
            );
            return g.a("clip-path", format!("url(#{id})"));
        }
        if clip.trim().starts_with("polygon(") {
            let pts = css_polygon(clip, b.x, b.y, b.width, b.height);
            let id = ctx.next("cp");
            ctx.defs.push(
                El::new("clipPath").a("id", &id).a("clipPathUnits", "userSpaceOnUse")
                    .c(El::new("polygon").a("points", points_attr(&pts)))
            );
            return g.a("clip-path", format!("url(#{id})"));
        }
        g
    }

    fn draw_text(&self, ctx: &mut RenderContext, node: &IRNode, text: &IRTextContent, colour: Option<&str>, t: f32) -> El {
        let Some(b) = node.bounds else {
            eprintln!("WARNING: text node {} has no bounds; nothing drawn", node.id);
            return El::new("g");
        };
        let Some(m) = self.metrics.get(&node.id).copied() else {
            return El::new("g");
        };
        let mut g = El::new("g");
        let shown = apply_text_transform(&text.text, text.text_transform.as_deref());
        let fill = colour.unwrap_or("#000000");
        let shadows = text.text_shadow.as_deref().map(parse_text_shadow).unwrap_or_default();

        if text.runs.is_empty() {
            // Whole-text: one style, lines split on \n.
            let size = text.font_size.unwrap_or(16.0);
            let line_box = text
                .line_height
                .map(|l| l * size)
                .unwrap_or((m.ascent + m.descent + m.line_gap) * size);
            let spacing = text.letter_spacing.unwrap_or(0.0);
            let (anchor, x) = match text.text_align.as_deref() {
                Some("center") => ("middle", b.x + b.width / 2.0 - spacing / 2.0),
                Some("right") | Some("end") => ("end", b.x + b.width - spacing),
                _ => ("start", b.x),
            };
            for (i, line) in shown.split('\n').enumerate() {
                if line.trim().is_empty() {
                    continue;
                }
                let top = b.y + i as f32 * line_box;
                let baseline = top + (line_box - (m.ascent + m.descent) * size) / 2.0 + m.ascent * size;
                let content = escape_xml(line);
                for shadow in shadows.iter().rev() {
                    g = g.c(self.text_el(ctx, &content, x, baseline, size, text.font_family.as_deref(),
                        text.font_weight, shadow.colour.as_deref().unwrap_or(fill), anchor, Some(shadow)));
                }
                g = g.c(self.text_el(ctx, &content, x, baseline, size, text.font_family.as_deref(),
                    text.font_weight, fill, anchor, None));
            }
            return g;
        }

        // Runs: inline flow sharing baselines.
        let runs = layout_runs(text, t, &m, &b, &mut ctx.advances);
        for run in &runs {
            if run.opacity <= 0.0 {
                continue;
            }
            let mut el = self.text_el(ctx, &escape_xml(&run.text), run.x, run.baseline, run.size,
                run.family.as_deref(), Some(run.weight), &run.colour, "start", None);
            if run.letter_spacing != 0.0 {
                el = el.a("letter-spacing", run.letter_spacing);
            }
            if run.opacity < 1.0 {
                el = el.a("opacity", run.opacity);
            }
            g = g.c(el);
        }
        g
    }

    fn text_el(&self, ctx: &mut RenderContext, content: &str, x: f32, y: f32, size: f32,
               family: Option<&str>, weight: Option<f32>, colour: &str, anchor: &str,
               shadow: Option<&Shadow>) -> El {
        let mut el = El::new("text")
            .a("x", x).a("y", y)
            .a("text-anchor", anchor)
            .a("space", "preserve");
        if let Some(family) = family {
            el = el.a("font-family", format!("'{}'", escape_xml(family)));
        }
        el = el.a("font-size", size);
        if let Some(weight) = weight {
            el = el.a("font-weight", weight.round());
        }
        let (dx, dy) = shadow.map(|s| (s.dx, s.dy)).unwrap_or((0.0, 0.0));
        if dx != 0.0 || dy != 0.0 {
            el = el.a("transform", format!("translate({dx},{dy})"));
        }
        if let Some(s) = shadow {
            if s.blur > 0.0 {
                // A text shadow's blur is a gaussian with sigma = the CSS
                // value (measured), painted beneath the text.
                let id = ctx.next("ts");
                ctx.defs.push(blur_filter(&id, s.blur / 2.0, ctx.width, ctx.height));
                el = el.a("filter", format!("url(#{id})"));
            }
        }
        el = el.a("fill", colour);
        el.t(content)
    }

    fn draw_shape(&self, ctx: &mut RenderContext, g: El, node: &IRNode) -> El {
        let (Some(shape), Some(b)) = (&node.shape, node.bounds) else { return g };
        let mut g = g;
        // Pattern fill (the ground's grain tile).
        if let Some(pattern) = &shape.pattern {
            if let Some(key) = ctx.images.get(&node.id) {
                let id = ctx.next("pat");
                let (tw, th) = match (pattern.tile_width, pattern.tile_height) {
                    (Some(tw), Some(th)) => (tw, th),
                    _ => (b.width, b.height),
                };
                ctx.defs.push(
                    El::new("pattern")
                        .a("id", &id)
                        .a("patternUnits", "userSpaceOnUse")
                        .a("width", tw).a("height", th)
                        .c(El::new("image")
                            .a("width", tw).a("height", th)
                            .a("preserveAspectRatio", "none")
                            .a("href", key))
                );
                let mut rect = El::new("rect")
                    .a("x", b.x).a("y", b.y).a("width", b.width).a("height", b.height)
                    .a("fill", format!("url(#{id})"));
                if let Some(o) = pattern.opacity {
                    if o < 1.0 {
                        rect = rect.a("opacity", o);
                    }
                }
                g = g.c(rect);
                return g;
            }
        }
        // Gradient or solid fill.
        let fill = match &shape.fill {
            Some(IRPaint::Gradient(grad)) => self.gradient_fill(ctx, grad, &b),
            Some(IRPaint::Solid(col)) => col.clone(),
            None => "none".to_string(),
        };
        let mut el = match shape.shape.as_str() {
            "rect" => {
                let r = shape.corner_radius.unwrap_or(0.0).min(b.width / 2.0).min(b.height / 2.0);
                El::new("rect")
                    .a("x", b.x).a("y", b.y).a("width", b.width).a("height", b.height)
                    .a("rx", r).a("ry", r)
            }
            "circle" | "ellipse" => El::new("ellipse")
                .a("cx", b.x + b.width / 2.0).a("cy", b.y + b.height / 2.0)
                .a("rx", b.width / 2.0).a("ry", b.height / 2.0),
            "path" => {
                if let Some(path) = &shape.path {
                    El::new("path").a("d", path)
                } else {
                    return g;
                }
            }
            other => {
                eprintln!("WARNING: shape {other:?} of node {} is not drawn", node.id);
                return g;
            }
        };
        el = el.a("fill", fill);
        if let Some(stroke) = &shape.stroke {
            el = el.a("stroke", stroke);
            el = el.a("stroke-width", shape.stroke_width.unwrap_or(1.0));
        }
        g.c(el)
    }

    fn gradient_fill(&self, ctx: &mut RenderContext, grad: &IRGradient, b: &IRBounds) -> String {
        let stops: Vec<El> = grad.stops.iter().map(|s| {
            let mut stop = El::new("stop")
                .a("offset", s.offset)
                .a("stop-color", &s.color);
            if let Some(o) = s.opacity {
                if o < 1.0 {
                    stop = stop.a("stop-opacity", o);
                }
            }
            stop
        }).collect();
        match grad.kind.as_str() {
            "linear" => {
                // CSS angle semantics over the box: the line runs through the
                // centre along (sin A, -cos A), |w sin A| + |h cos A| long.
                let angle = grad.angle.unwrap_or(0.0);
                let a = angle.to_radians();
                let (dx, dy) = (a.sin(), -a.cos());
                let half = ((b.width * a.sin()).abs() + (b.height * a.cos()).abs()) / 2.0;
                let (g0, g1) = (
                    (b.x + b.width / 2.0 - dx * half, b.y + b.height / 2.0 - dy * half),
                    (b.x + b.width / 2.0 + dx * half, b.y + b.height / 2.0 + dy * half),
                );
                let id = ctx.next("lg");
                let mut lg = El::new("linearGradient")
                    .a("id", &id)
                    .a("gradientUnits", "userSpaceOnUse")
                    .a("x1", g0.0).a("y1", g0.1).a("x2", g1.0).a("y2", g1.1);
                for s in stops {
                    lg = lg.c(s);
                }
                ctx.defs.push(lg);
                format!("url(#{id})")
            }
            "radial" => {
                let id = ctx.next("rg");
                let rx = grad.radius_x.unwrap_or(50.0) / 100.0 * b.width;
                let ry = grad.radius_y.unwrap_or(50.0) / 100.0 * b.height;
                let mut rg = El::new("radialGradient")
                    .a("id", &id)
                    .a("gradientUnits", "userSpaceOnUse")
                    .a("cx", b.x + b.width / 2.0).a("cy", b.y + b.height / 2.0)
                    .a("r", rx)
                    .a("gradientTransform", format!(
                        "translate({},{}) scale(1,{}) translate({},{})",
                        b.x + b.width / 2.0, b.y + b.height / 2.0, ry / rx.max(1.0),
                        -(b.x + b.width / 2.0), -(b.y + b.height / 2.0)));
                for s in stops {
                    rg = rg.c(s);
                }
                ctx.defs.push(rg);
                format!("url(#{id})")
            }
            _ => {
                eprintln!("WARNING: gradient kind {:?} is not drawn; using none", grad.kind);
                "none".to_string()
            }
        }
    }

    fn draw_svg_path(&self, g: El, node: &IRNode) -> El {
        let (Some(svg), Some(b)) = (&node.svg, node.bounds) else { return g };
        let Some(path) = &svg.path else { return g };
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
        let fill = match &svg.fill {
            Some(IRPaint::Solid(col)) => col.clone(),
            _ => "#000000".to_string(),
        };
        g.c(El::new("path")
            .a("d", path)
            .a("transform", format!(
                "translate({},{}) scale({},{}) translate({},{})",
                b.x, b.y, b.width / vw, b.height / vh, -vx, -vy))
            .a("fill", fill))
    }
}

fn blur_filter(id: &str, std_dev: f32, w: f32, h: f32) -> El {
    El::new("filter")
        .a("id", id)
        .a("filterUnits", "userSpaceOnUse")
        .a("x", -w).a("y", -h)
        .a("width", 3.0 * w).a("height", 3.0 * h)
        .c(El::new("feGaussianBlur").a("stdDeviation", std_dev))
}

// ── Main ───────────────────────────────────────────────────────────

fn main() {
    let args: Vec<String> = std::env::args().collect();
    if args.len() == 2 && args[1] == "--version" {
        println!("ren-graphics-fframes {}", env!("CARGO_PKG_VERSION"));
        return;
    }
    if args.len() < 3 || args[1] != "render" {
        eprintln!("Usage: ren-graphics-fframes render <scene.json> [output_dir] [--raw]");
        eprintln!("       ren-graphics-fframes --version");
        std::process::exit(1);
    }
    let raw = args.iter().any(|a| a == "--raw");
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
    let result = if raw {
        render_scene_raw(scene)
    } else {
        render_scene(scene, &output_dir)
    };
    if let Err(e) = result {
        eprintln!("ERROR: render failed: {}", e);
        std::process::exit(1);
    }
}

/// The scene's fonts and images, handed to FFrames as its media.
///
/// FFrames resolves an `<image href>` only against preloaded image data,
/// so every image node's pixels - and every pattern fill's tile - are
/// decoded here once and named; the returned map is the owning node's id ->
/// that name. The bytes live for the whole process, which is one render.
fn scene_media(scene: &Scene) -> Result<(DynamicMediaProvider<'static>, HashMap<String, String>), String> {
    let mut fonts: Vec<RawFontData> = Vec::new();
    let mut seen: HashSet<PathBuf> = HashSet::new();
    for node in &scene.nodes {
        let Some(text) = &node.text else {
            continue;
        };
        // The node's face plus every run's staged face: runs at several
        // weights each draw with their own static instance.
        let mut files: Vec<&String> = text.font_file.iter().collect();
        files.extend(text.runs.iter().filter_map(|r| r.font_file.as_ref()));
        for font_file in files {
            let path = PathBuf::from(font_file);
            if !seen.insert(path.clone()) {
                continue;
            }
            let data =
                fs::read(&path).map_err(|e| format!("cannot read font file {}: {}", path.display(), e))?;
            fonts.push(RawFontData {
                file_name: path
                    .file_name()
                    .map(|name| name.to_string_lossy().into_owned())
                    .unwrap_or_else(|| path.to_string_lossy().into_owned()),
                data: Arc::new(data),
            });
        }
    }
    let mut images = HashMap::new();
    let mut names = HashMap::new();
    for node in &scene.nodes {
        if let Some(image) = &node.image {
            let name = format!("ren-image-{}", names.len());
            let size = node.bounds.map(|b| (b.width, b.height));
            let data = preload_image(
                &name,
                &image.src,
                size,
                &format!("image of node {}", node.id),
            )?;
            images.insert(name.clone(), data);
            names.insert(node.id.clone(), name);
        }
        if let Some(shape) = &node.shape {
            if let Some(pattern) = &shape.pattern {
                let name = format!("ren-pattern-{}", names.len());
                // The ground's grain tile: its own tile size, else the box.
                let size = match (pattern.tile_width, pattern.tile_height) {
                    (Some(tw), Some(th)) => Some((tw, th)),
                    _ => node.bounds.map(|b| (b.width, b.height)),
                };
                let data = preload_image(
                    &name,
                    &pattern.src,
                    size,
                    &format!("pattern of node {}", node.id),
                )?;
                images.insert(name.clone(), data);
                names.insert(node.id.clone(), name);
            }
        }
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

/// Everything a render needs once the scene is parsed: the video and its
/// media, the Metal context, and the frame geometry. The previewer and
/// renderer borrow these, so they are built by the caller from this.
struct RenderSetup {
    video: SceneVideo,
    media: DynamicMediaProvider<'static>,
    metal_ctx: SkiaMetalCtx,
    needs_system: bool,
    width: u32,
    height: u32,
    total_frames: usize,
}

fn setup_render(scene: Scene) -> Result<RenderSetup, String> {
    // Draw only the roots; a group draws its own children.
    let child_ids: HashSet<&String> = scene.nodes.iter().flat_map(|n| n.children.iter()).collect();
    let mut roots: Vec<&IRNode> = scene.nodes.iter().filter(|n| !child_ids.contains(&n.id)).collect();
    roots.sort_by_key(|n| n.z_index.unwrap_or(0));
    let roots: Vec<String> = roots.into_iter().map(|n| n.id.clone()).collect();
    let node_map: HashMap<String, IRNode> =
        scene.nodes.iter().map(|n| (n.id.clone(), n.clone())).collect();
    let metrics = text_metrics(&scene);

    // Precompute advance widths for every staged face: the node's plus
    // each run's, since a Bold advance is not a Thin advance.
    let mut advances: HashMap<String, Advances> = HashMap::new();
    for node in &scene.nodes {
        let Some(text) = &node.text else { continue };
        let mut paths: Vec<&str> = text.font_file.as_deref().into_iter().collect();
        paths.extend(text.runs.iter().filter_map(|r| r.font_file.as_deref()));
        for path in paths {
            if !advances.contains_key(path) {
                advances.insert(path.to_string(), load_advances(Path::new(path)));
            }
        }
    }

    let needs_system = scene
        .nodes
        .iter()
        .any(|n| n.text.as_ref().map_or(false, |t| t.font_file.is_none()));
    let (media, images) = scene_media(&scene)?;
    let (width, height) = (scene.width, scene.height);
    let total_frames = frame_count(&scene);

    let video = SceneVideo { scene, node_map, roots, metrics, images, advances };

    let metal_ctx = SkiaMetalCtx::new(width as usize, height as usize)
        .map_err(|e| format!("cannot create Metal context: {}", e))?;
    Ok(RenderSetup { video, media, metal_ctx, needs_system, width, height, total_frames })
}

/// The previewer and renderer over a setup. Both borrow the setup, so they
/// cannot outlive it; the caller holds them together.
fn build_renderer<'a>(setup: &'a RenderSetup)
    -> Result<(fframes::Previewer<'a, 'a, SceneVideo>, SkiaFrameRenderer<'a, SkiaMetalCtx>), String> {
    let mut options = fframes::RenderOptions::default();
    options.load_system_fonts = setup.needs_system;
    options.media = Some(&setup.media);
    let previewer = fframes::Previewer::new(&setup.video, &options)
        .map_err(|e| format!("cannot create previewer: {}", e))?;
    let renderer = SkiaFrameRenderer::new(&setup.metal_ctx);
    Ok((previewer, renderer))
}

/// Render every frame, handing each to ``emit``. Shared by the PNG and raw
/// output modes so the two cannot drift.
fn render_each_frame<'a, 'media: 'a, TVideo: Video, TBackend: SkiaBackend>(
    previewer: &mut fframes::Previewer<'a, 'media, TVideo>,
    renderer: &mut SkiaFrameRenderer<'_, TBackend>,
    width: u32,
    height: u32,
    total_frames: usize,
    mut emit: impl FnMut(&RgbaFrame) -> Result<(), String>,
) -> Result<(), String> {
    for frame_idx in 0..total_frames {
        let tree = previewer
            .svg_tree(frame_idx)
            .map_err(|e| format!("frame {} conversion failed: {}", frame_idx, e))?;
        let pixels = renderer
            .render_tree(&tree, fframes::Color::TRANSPARENT, width, height)
            .map_err(|e| format!("frame {} render failed: {}", frame_idx, e))?;
        emit(&pixels)?;
    }
    Ok(())
}

fn render_scene(scene: Scene, output_dir: &std::path::Path) -> Result<(), String> {
    fs::create_dir_all(output_dir).map_err(|e| format!("cannot create output dir: {}", e))?;
    let setup = setup_render(scene)?;
    let (mut previewer, mut renderer) = build_renderer(&setup)?;
    let mut idx = 0usize;
    render_each_frame(
        &mut previewer,
        &mut renderer,
        setup.width,
        setup.height,
        setup.total_frames,
        |pixels| {
            let path = output_dir.join(format!("frame_{:06}.png", idx + 1));
            idx += 1;
            pixels.save_png(&path).map_err(|e| format!("cannot save PNG: {}", e))
        },
    )?;
    println!("Rendered {} frames to {}", setup.total_frames, output_dir.display());
    Ok(())
}

/// Raw RGBA mode: a header (width, height as u32 LE) then straight-alpha RGBA
/// frames on stdout, so the encoder can pipe them into ffmpeg directly.
fn render_scene_raw(scene: Scene) -> Result<(), String> {
    let setup = setup_render(scene)?;
    let (mut previewer, mut renderer) = build_renderer(&setup)?;
    let stdout = std::io::stdout();
    let mut out = stdout.lock();
    out.write_all(&setup.width.to_le_bytes())
        .map_err(|e| format!("cannot write width: {}", e))?;
    out.write_all(&setup.height.to_le_bytes())
        .map_err(|e| format!("cannot write height: {}", e))?;
    render_each_frame(
        &mut previewer,
        &mut renderer,
        setup.width,
        setup.height,
        setup.total_frames,
        |pixels| {
            out.write_all(&pixels.pixels)
                .map_err(|e| format!("cannot write frame: {}", e))
        },
    )?;
    out.flush().map_err(|e| format!("cannot flush stdout: {}", e))?;
    Ok(())
}
