//! The scope: what an operation runs against.
//!
//! Modeled from `library/tools/scope.py` and `library/tools/region.py`.
//! The enum makes Python's `__post_init__` coherence rules
//! unrepresentable: a REGION scope carries a region, a CLIP scope a clip
//! id, a REEL scope its keep ranges, and nothing crosses.

use schemars::JsonSchema;
use serde::{Deserialize, Serialize};

use crate::capability::ScopeKind;

/// A scope that does not describe anything runnable.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct ScopeError(pub String);

impl std::fmt::Display for ScopeError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(f, "{}", self.0)
    }
}

impl std::error::Error for ScopeError {}

/// An interval of ONE named timeline, in seconds. Always TIMELINE
/// seconds - the type has one time domain and carries no flag saying
/// which, because a flag is a thing a caller can set wrongly.
///
/// `timeline` is `None` for the project's own master timeline
/// (`region.MASTER`), or the timeline's declared name. Half-open,
/// `[start, end)`.
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize, JsonSchema)]
pub struct Region {
    pub timeline: Option<String>,
    pub start: f64,
    pub end: f64,
}

impl Region {
    /// Build a region, refusing the incoherent ones (`Region.__post_init__`):
    /// a negative start, or an end preceding the start.
    pub fn new(timeline: Option<String>, start: f64, end: f64) -> Result<Region, ScopeError> {
        if start < 0.0 {
            return Err(ScopeError(format!(
                "region start {start} is negative. A region is timeline seconds measured from the first frame."
            )));
        }
        if end < start {
            return Err(ScopeError(format!(
                "region end {end} precedes start {start}. An interval is (start, end), both in timeline seconds."
            )));
        }
        Ok(Region {
            timeline,
            start,
            end,
        })
    }

    /// The region's duration in seconds.
    pub fn duration(&self) -> f64 {
        self.end - self.start
    }
}

/// The address an operation runs against: the whole project, one clip,
/// or a region.
///
/// Field-for-field the shape of `scope.Scope`: a kind and the payload
/// that kind carries. The constructors refuse the combinations
/// Python's `Scope.__post_init__` refuses - a REGION scope with no
/// region would read as "the whole project" and silently redo
/// everything.
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize, JsonSchema)]
pub struct Scope {
    pub kind: ScopeKind,
    #[serde(default)]
    pub region_span: Option<Region>,
    #[serde(default)]
    pub clip_id: Option<String>,
    #[serde(default)]
    pub reel_ranges: Option<Vec<Region>>,
}

impl Scope {
    /// The whole project - what every step does today.
    pub fn project() -> Scope {
        Scope {
            kind: ScopeKind::Project,
            region_span: None,
            clip_id: None,
            reel_ranges: None,
        }
    }

    /// One clip, by its catalog id.
    pub fn clip(clip_id: impl Into<String>) -> Scope {
        Scope {
            kind: ScopeKind::Clip,
            region_span: None,
            clip_id: Some(clip_id.into()),
            reel_ranges: None,
        }
    }

    /// An interval of the timeline.
    pub fn region(region_span: Region) -> Scope {
        Scope {
            kind: ScopeKind::Region,
            region_span: Some(region_span),
            clip_id: None,
            reel_ranges: None,
        }
    }

    /// One reel: its keep ranges, in play order, on the reel's timeline.
    ///
    /// Refuses what `Scope._check_reel` refuses: no ranges, ranges on
    /// more than one timeline, and overlapping ranges (one second would
    /// get two answers). Order is NOT checked: play order is the point.
    pub fn reel(ranges: Vec<Region>) -> Result<Scope, ScopeError> {
        if ranges.is_empty() {
            return Err(ScopeError(
                "a REEL scope must carry its keep ranges; without them it would read as the whole project and redo everything".to_string(),
            ));
        }
        let timelines: Vec<&Option<String>> = ranges.iter().map(|r| &r.timeline).collect();
        let first = timelines[0];
        if timelines.iter().any(|t| *t != first) {
            return Err(ScopeError(
                "a REEL's ranges must all be on ONE timeline, got more than one. A reel is one timeline built from one master; ranges from two of them laid end to end are not a reel.".to_string(),
            ));
        }
        let mut ordered = ranges.clone();
        ordered.sort_by(|a, b| {
            a.start
                .partial_cmp(&b.start)
                .unwrap_or(std::cmp::Ordering::Equal)
                .then(
                    a.end
                        .partial_cmp(&b.end)
                        .unwrap_or(std::cmp::Ordering::Equal),
                )
        });
        for pair in ordered.windows(2) {
            if pair[1].start < pair[0].end {
                return Err(ScopeError(format!(
                    "a REEL's ranges must not overlap: {:?} and {:?} share time, so reel_build.reel_time would give one master second two answers",
                    pair[0], pair[1]
                )));
            }
        }
        Ok(Scope {
            kind: ScopeKind::Reel,
            region_span: None,
            clip_id: None,
            reel_ranges: Some(ranges),
        })
    }

    /// Which timeline this scope addresses, or None where it says nothing
    /// (`Scope.timeline`).
    pub fn timeline(&self) -> Option<&str> {
        match self.kind {
            ScopeKind::Region => self
                .region_span
                .as_ref()
                .and_then(|r| r.timeline.as_deref()),
            ScopeKind::Reel => self
                .reel_ranges
                .as_ref()
                .and_then(|ranges| ranges.first())
                .and_then(|r| r.timeline.as_deref()),
            _ => None,
        }
    }
}
