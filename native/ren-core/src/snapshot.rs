//! The shadow snapshot, typed.
//!
//! Modeled from the clip dicts `reel_read` QUICK mode produces and the
//! readers in `library/tools/edit_algebra.py` (`_clip`, `_track`,
//! `_timeline_marker`, `_clip_marker`). The Python side passes snapshots
//! as plain dicts; this module is the typed reading of them, so a field
//! the algebra reads cannot be silently absent.

use serde::Deserialize;
use serde_json::{Map, Value};

/// How close a re-read float must be to what was written
/// (`edit_algebra.FLOAT_TOLERANCE`).
pub const FLOAT_TOLERANCE: f64 = 1e-6;

/// One timeline or clip marker, as the shadow holds it.
#[derive(Clone, Debug, Deserialize)]
pub struct Marker {
    pub frame: i64,
    #[serde(default)]
    pub color: String,
    #[serde(default)]
    pub name: String,
    #[serde(default)]
    pub note: String,
    #[serde(default)]
    pub duration: i64,
    #[serde(default)]
    pub custom_data: String,
}

/// The `fusion` sub-dict of a clip (`comp_names` is what the mask ops
/// verify against).
#[derive(Clone, Debug, Default, Deserialize)]
pub struct FusionInfo {
    #[serde(default)]
    pub comp_names: Vec<String>,
}

/// The `color` sub-dict of a clip (`cdl` is what `grade.node_set`
/// verifies against).
#[derive(Clone, Debug, Default, Deserialize)]
pub struct ColorInfo {
    #[serde(default)]
    pub cdl: Map<String, Value>,
}

/// One clip on a row, in the shadow's QUICK spelling.
#[derive(Clone, Debug, Deserialize)]
pub struct Clip {
    pub unique_id: String,
    #[serde(default)]
    pub name: String,
    #[serde(default)]
    pub track_type: String,
    #[serde(default)]
    pub track_index: i64,
    #[serde(default)]
    pub track_name: String,
    pub record_in: i64,
    pub record_out: i64,
    #[serde(default)]
    pub duration: i64,
    #[serde(default)]
    pub source_in_frame: Option<i64>,
    #[serde(default)]
    pub source_out_frame: Option<i64>,
    #[serde(default)]
    pub source_file: String,
    #[serde(default)]
    pub enabled: Option<bool>,
    #[serde(default)]
    pub speed: Option<f64>,
    #[serde(default)]
    pub transform: Map<String, Value>,
    #[serde(default)]
    pub keyframes: Map<String, Value>,
    #[serde(default)]
    pub fusion: FusionInfo,
    #[serde(default)]
    pub color: ColorInfo,
    #[serde(default)]
    pub markers: Vec<Marker>,
}

/// One row of the timeline. The shadow spells the row type `"type"`.
#[derive(Clone, Debug, Deserialize)]
pub struct Track {
    #[serde(rename = "type")]
    pub track_type: String,
    pub index: i64,
    #[serde(default)]
    pub name: String,
    #[serde(default)]
    pub clips: Vec<Clip>,
}

/// The `markers` sub-dict: timeline-level markers (clip markers live on
/// the clips).
#[derive(Clone, Debug, Default, Deserialize)]
pub struct Markers {
    #[serde(default)]
    pub timeline: Vec<Marker>,
}

/// A whole timeline snapshot, as `timeline_shadow` stores it.
#[derive(Clone, Debug, Deserialize)]
pub struct Snapshot {
    pub start_frame: i64,
    pub end_frame: i64,
    #[serde(default)]
    pub tracks: Vec<Track>,
    #[serde(default)]
    pub markers: Markers,
}

impl Snapshot {
    /// Every clip on every row, in track order.
    pub fn clips(&self) -> Vec<&Clip> {
        self.tracks
            .iter()
            .flat_map(|track| track.clips.iter())
            .collect()
    }

    /// The clip holding `unique_id`, only when it is present EXACTLY
    /// once (`edit_algebra._clip`): a duplicate id is not an address.
    pub fn clip(&self, unique_id: &str) -> Option<&Clip> {
        let found: Vec<&Clip> = self
            .clips()
            .into_iter()
            .filter(|clip| clip.unique_id == unique_id)
            .collect();
        if found.len() == 1 {
            found.into_iter().next()
        } else {
            None
        }
    }

    /// The row of `track_type` at `index`, or None.
    pub fn track(&self, track_type: &str, index: i64) -> Option<&Track> {
        self.tracks
            .iter()
            .find(|track| track.track_type == track_type && track.index == index)
    }

    /// The timeline marker at `frame`, or None.
    pub fn timeline_marker(&self, frame: i64) -> Option<&Marker> {
        self.markers
            .timeline
            .iter()
            .find(|marker| marker.frame == frame)
    }

    /// The clip marker at `frame` on the clip holding `unique_id`, or
    /// None.
    pub fn clip_marker(&self, unique_id: &str, frame: i64) -> Option<&Marker> {
        self.clip(unique_id)?
            .markers
            .iter()
            .find(|marker| marker.frame == frame)
    }
}

/// `_same_value`: numbers compare within `FLOAT_TOLERANCE`, everything
/// else by equality.
pub fn same_value(got: &Value, want: &Value) -> bool {
    match (got, want) {
        (Value::Number(g), Value::Number(w)) => {
            let g = g.as_f64().unwrap_or(f64::NAN);
            let w = w.as_f64().unwrap_or(f64::NAN);
            (g - w).abs() <= FLOAT_TOLERANCE
        }
        _ => got == want,
    }
}

/// `_matches`: every field `want` names must read back equal on the
/// marker (`edit_algebra._matches`).
pub fn matches_marker(marker: &Marker, want: &Value) -> bool {
    let Some(want) = want.as_object() else {
        return false;
    };
    for (key, want_value) in want {
        let got = match key.as_str() {
            "color" => Value::String(marker.color.clone()),
            "name" => Value::String(marker.name.clone()),
            "note" => Value::String(marker.note.clone()),
            "duration" => Value::from(marker.duration),
            "custom_data" => Value::String(marker.custom_data.clone()),
            _ => continue,
        };
        if !same_value(&got, want_value) {
            return false;
        }
    }
    true
}
