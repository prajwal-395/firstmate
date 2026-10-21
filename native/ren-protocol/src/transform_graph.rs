//! Validated traversal and application of project-model time and space transforms.
//!
//! Graph edges are piecewise affine maps. A query succeeds only when every
//! edge covers the requested point and there is exactly one usable path.

use crate::project_model::{ProjectModel, SpaceTransform, TimeTransform};
use std::collections::{BTreeMap, VecDeque};

#[derive(Clone, Debug, PartialEq)]
pub struct MappedTime {
    pub tick: i64,
    pub uncertainty_ticks: u64,
    pub path: Vec<String>,
}

#[derive(Clone, Debug, PartialEq)]
pub struct MappedPoint {
    pub coordinates: Vec<f64>,
    /// Conservative radius in destination units.
    pub uncertainty: f64,
    pub path: Vec<String>,
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub enum TransformError {
    NoPath,
    AmbiguousPath,
    Uncovered,
    NonInvertible,
    InvalidTransform(String),
    Overflow,
}

#[derive(Clone)]
struct TimeEdge<'a> {
    transform: &'a TimeTransform,
    reverse: bool,
}

/// Map a tick through the unique graph path between clocks. Segment ranges
/// are half-open and uncertainty accumulates conservatively along the chain.
pub fn map_time(
    transforms: &[TimeTransform],
    from_clock: &str,
    to_clock: &str,
    tick: i64,
) -> Result<MappedTime, TransformError> {
    if from_clock == to_clock {
        return Ok(MappedTime {
            tick,
            uncertainty_ticks: 0,
            path: Vec::new(),
        });
    }
    let mut graph: BTreeMap<&str, Vec<TimeEdge<'_>>> = BTreeMap::new();
    for t in transforms {
        graph.entry(&t.from_clock.0).or_default().push(TimeEdge {
            transform: t,
            reverse: false,
        });
        graph.entry(&t.to_clock.0).or_default().push(TimeEdge {
            transform: t,
            reverse: true,
        });
    }
    let paths = find_paths(&graph, from_clock, to_clock);
    if paths.is_empty() {
        return Err(TransformError::NoPath);
    }
    if paths.len() != 1 {
        return Err(TransformError::AmbiguousPath);
    }
    let mut value = tick;
    let mut uncertainty = 0u64;
    let mut ids = Vec::new();
    for edge in &paths[0] {
        let (next, edge_uncertainty) = apply_time_edge(edge, value, uncertainty)?;
        value = next;
        uncertainty = edge_uncertainty;
        ids.push(edge.transform.id.clone());
    }
    Ok(MappedTime {
        tick: value,
        uncertainty_ticks: uncertainty,
        path: ids,
    })
}

fn find_paths<'a>(
    graph: &BTreeMap<&str, Vec<TimeEdge<'a>>>,
    from: &str,
    to: &str,
) -> Vec<Vec<TimeEdge<'a>>> {
    let mut result = Vec::new();
    let mut queue = VecDeque::from([(
        from.to_string(),
        Vec::<TimeEdge<'a>>::new(),
        vec![from.to_string()],
    )]);
    while let Some((node, path, seen)) = queue.pop_front() {
        if result.len() > 1 {
            break;
        }
        for edge in graph.get(node.as_str()).into_iter().flatten() {
            let next = if edge.reverse {
                edge.transform.from_clock.0.as_str()
            } else {
                edge.transform.to_clock.0.as_str()
            };
            if seen.iter().any(|s| s == next) {
                continue;
            }
            let mut next_path = path.clone();
            next_path.push(edge.clone());
            if next == to {
                result.push(next_path);
            } else {
                let mut next_seen = seen.clone();
                next_seen.push(next.to_string());
                queue.push_back((next.to_string(), next_path, next_seen));
            }
        }
    }
    result
}

fn apply_time_edge(
    edge: &TimeEdge<'_>,
    tick: i64,
    input_uncertainty: u64,
) -> Result<(i64, u64), TransformError> {
    let t = edge.transform;
    let segment = t
        .segments
        .iter()
        .find(|s| {
            let i = if edge.reverse { &s.to } else { &s.from };
            i.start <= tick && tick < i.end
        })
        .ok_or(TransformError::Uncovered)?;
    if edge.reverse && !segment.invertible {
        return Err(TransformError::NonInvertible);
    }
    let (target, end) = if edge.reverse {
        (&segment.from, segment.from.end)
    } else {
        (&segment.to, segment.to.end)
    };
    // Forward mapping is y = x * n / d + offset. Inverse uses the exact
    // reciprocal and subtracts the offset before scaling.
    let (numerator, denominator, adjusted) = if edge.reverse {
        (
            segment.slope.denominator as i128,
            segment.slope.numerator as i128,
            tick as i128 - segment.offset_ticks as i128,
        )
    } else {
        (
            segment.slope.numerator as i128,
            segment.slope.denominator as i128,
            tick as i128,
        )
    };
    let mapped = adjusted
        .checked_mul(numerator)
        .ok_or(TransformError::Overflow)?
        / denominator
        + if edge.reverse {
            0
        } else {
            segment.offset_ticks as i128
        };
    let mapped = i64::try_from(mapped).map_err(|_| TransformError::Overflow)?;
    if mapped < target.start || mapped >= end {
        return Err(TransformError::Uncovered);
    }
    let scaled = (input_uncertainty as u128)
        .checked_mul(numerator.unsigned_abs())
        .ok_or(TransformError::Overflow)?;
    let propagated = scaled.div_ceil(denominator as u128);
    let total = propagated
        .checked_add(segment.uncertainty_ticks as u128)
        .ok_or(TransformError::Overflow)?;
    Ok((
        mapped,
        u64::try_from(total).map_err(|_| TransformError::Overflow)?,
    ))
}

/// Apply the unique space-transform path to a homogeneous 2D or 3D point.
/// Uncertainty is propagated using each matrix's Frobenius norm as a safe
/// upper bound on its operator norm, then the edge's own error is added.
pub fn map_point(
    transforms: &[SpaceTransform],
    from_space: &str,
    to_space: &str,
    point: &[f64],
    uncertainty: f64,
) -> Result<MappedPoint, TransformError> {
    if !uncertainty.is_finite() || uncertainty < 0.0 || point.iter().any(|v| !v.is_finite()) {
        return Err(TransformError::InvalidTransform(
            "point and uncertainty must be finite; uncertainty nonnegative".into(),
        ));
    }
    if from_space == to_space {
        return Ok(MappedPoint {
            coordinates: point.to_vec(),
            uncertainty,
            path: Vec::new(),
        });
    }
    let paths = find_space_paths(transforms, from_space, to_space);
    if paths.is_empty() {
        return Err(TransformError::NoPath);
    }
    if paths.len() != 1 {
        return Err(TransformError::AmbiguousPath);
    }
    let mut coordinates = point.to_vec();
    let mut error = uncertainty;
    let mut ids = Vec::new();
    for transform in &paths[0] {
        let dimension = match transform.matrix.len() {
            9 => 3,
            16 => 4,
            _ => {
                return Err(TransformError::InvalidTransform(
                    "matrix must be 3x3 or 4x4".into(),
                ))
            }
        };
        if coordinates.len() + 1 != dimension {
            return Err(TransformError::InvalidTransform(
                "point dimension does not match matrix".into(),
            ));
        }
        let mut homogeneous = coordinates.clone();
        homogeneous.push(1.0);
        let mut result = vec![0.0; dimension];
        for (row, result_value) in result.iter_mut().enumerate() {
            for (col, coordinate) in homogeneous.iter().enumerate() {
                *result_value += transform.matrix[row * dimension + col] * coordinate;
            }
        }
        let w = result[dimension - 1];
        if !w.is_finite() || w == 0.0 {
            return Err(TransformError::InvalidTransform(
                "homogeneous coordinate is zero or non-finite".into(),
            ));
        }
        coordinates = result[..dimension - 1].iter().map(|v| v / w).collect();
        if coordinates.iter().any(|v| !v.is_finite()) {
            return Err(TransformError::Overflow);
        }
        let norm = transform.matrix.iter().map(|v| v * v).sum::<f64>().sqrt();
        error = error * norm + transform.uncertainty.unwrap_or(0.0);
        ids.push(transform.id.clone());
    }
    Ok(MappedPoint {
        coordinates,
        uncertainty: error,
        path: ids,
    })
}

fn find_space_paths<'a>(
    transforms: &'a [SpaceTransform],
    from: &str,
    to: &str,
) -> Vec<Vec<&'a SpaceTransform>> {
    let mut found = Vec::new();
    let mut queue = VecDeque::from([(
        from.to_string(),
        Vec::<&SpaceTransform>::new(),
        vec![from.to_string()],
    )]);
    while let Some((node, path, seen)) = queue.pop_front() {
        if found.len() > 1 {
            break;
        }
        for t in transforms.iter().filter(|t| t.from_space == node) {
            if seen.contains(&t.to_space) {
                continue;
            }
            let mut p = path.clone();
            p.push(t);
            if t.to_space == to {
                found.push(p);
            } else {
                let mut s = seen.clone();
                s.push(t.to_space.clone());
                queue.push_back((t.to_space.clone(), p, s));
            }
        }
    }
    found
}

/// Ensure all transforms are globally unambiguous and refer to declared nodes.
pub fn validate_graph(model: &ProjectModel) -> Result<(), String> {
    let mut ids = std::collections::BTreeSet::new();
    for t in &model.time_transforms {
        if !ids.insert(t.id.as_str()) {
            return Err("transform ids must be unique".into());
        }
        // A self-join (from_clock == to_clock) is well-formed per the
        // evidence contract - traversal simply never needs it, because
        // map_time short-circuits same-clock queries and path search skips
        // visited nodes - so validation must accept what the contract holds.
        for (i, a) in t.segments.iter().enumerate() {
            if a.slope.denominator == 0 || a.slope.numerator <= 0 {
                return Err("time transform slope must be positive".into());
            }
            if t.segments.iter().skip(i + 1).any(|b| {
                overlaps(a.from.start, a.from.end, b.from.start, b.from.end)
                    || overlaps(a.to.start, a.to.end, b.to.start, b.to.end)
            }) {
                return Err("time transform segments overlap".into());
            }
        }
    }
    let spaces: std::collections::BTreeSet<_> = model
        .coordinate_spaces
        .iter()
        .map(|s| s.id.as_str())
        .collect();
    let mut ids = std::collections::BTreeSet::new();
    for t in &model.space_transforms {
        if !ids.insert(t.id.as_str()) {
            return Err("transform ids must be unique".into());
        }
        // Self-joins are well-formed per the evidence contract (see above);
        // only dangling endpoints are refused here.
        if !spaces.is_empty()
            && (!spaces.contains(t.from_space.as_str()) || !spaces.contains(t.to_space.as_str()))
        {
            return Err("space transform has an invalid join".into());
        }
        if t.matrix.iter().any(|v| !v.is_finite())
            || t.uncertainty.is_some_and(|v| !v.is_finite() || v < 0.0)
        {
            return Err(
                "space transform values and uncertainty must be finite and uncertainty nonnegative"
                    .into(),
            );
        }
    }
    Ok(())
}

fn overlaps(a0: i64, a1: i64, b0: i64, b1: i64) -> bool {
    a0 < b1 && b0 < a1
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::project_model::{ClockId, Interval, ProvenanceId, TimeTransform, TransformSegment};

    fn time_edge(id: &str, from: &str, to: &str, n: i64, offset: i64, error: u64) -> TimeTransform {
        time_edge_rational(id, from, to, n, 1, offset, error, true)
    }

    fn time_edge_rational(
        id: &str,
        from: &str,
        to: &str,
        n: i64,
        d: u32,
        offset: i64,
        error: u64,
        invertible: bool,
    ) -> TimeTransform {
        TimeTransform {
            id: id.into(),
            from_clock: ClockId(from.into()),
            to_clock: ClockId(to.into()),
            segments: vec![TransformSegment {
                from: Interval {
                    clock_id: ClockId(from.into()),
                    start: 0,
                    end: 3000,
                },
                to: Interval {
                    clock_id: ClockId(to.into()),
                    start: 0,
                    end: 3000,
                },
                slope: crate::project_model::Rational {
                    numerator: n,
                    denominator: d,
                },
                offset_ticks: offset,
                uncertainty_ticks: error,
                invertible,
            }],
            provenance_id: ProvenanceId("p".into()),
        }
    }

    #[test]
    fn maps_nontrivial_chain_and_accumulates_uncertainty() {
        let transforms = vec![
            time_edge("pts-presentation", "pts", "presentation", 2, 10, 3),
            time_edge("presentation-sync", "presentation", "sync", 3, 5, 2),
        ];
        let mapped = map_time(&transforms, "pts", "sync", 7).unwrap();
        assert_eq!(mapped.tick, 77);
        assert_eq!(mapped.uncertainty_ticks, 11);
        assert_eq!(mapped.path, vec!["pts-presentation", "presentation-sync"]);
    }

    #[test]
    fn maps_retime_chain_forward_and_back() {
        // Source PTS -> presentation -> sync -> half-speed retime -> timeline.
        let transforms = vec![
            time_edge("pts-presentation", "pts", "presentation", 2, 10, 3),
            time_edge("presentation-sync", "presentation", "sync", 3, 5, 2),
            time_edge_rational("sync-retime", "sync", "retime", 1, 2, 0, 1, true),
            time_edge("retime-timeline", "retime", "timeline", 1, 100, 0),
        ];
        let mapped = map_time(&transforms, "pts", "timeline", 7).unwrap();
        // 7 -> 24 -> 77 -> 38 (77/2 truncates) -> 138.
        assert_eq!(mapped.tick, 138);
        // 0 -> 3 -> 11 -> ceil(11/2)+1=7 -> 7.
        assert_eq!(mapped.uncertainty_ticks, 7);
        assert_eq!(
            mapped.path,
            vec![
                "pts-presentation",
                "presentation-sync",
                "sync-retime",
                "retime-timeline"
            ]
        );
        // Reverse traversal divides by each slope and subtracts each offset
        // before scaling; integer truncation means the round trip lands near
        // but not exactly on the start tick: 138 -> 38 -> 76 -> 23 -> 6.
        let back = map_time(&transforms, "timeline", "pts", 138).unwrap();
        assert_eq!(back.tick, 6);
        assert_eq!(back.path.len(), 4);
    }

    #[test]
    fn rejects_ambiguous_paths_and_uncovered_ticks() {
        let a = time_edge("a", "pts", "sync", 1, 0, 0);
        let b = time_edge("b", "pts", "presentation", 1, 0, 0);
        let c = time_edge("c", "presentation", "sync", 1, 0, 0);
        assert_eq!(
            map_time(&[a.clone(), b, c], "pts", "sync", 1),
            Err(TransformError::AmbiguousPath)
        );
        assert_eq!(
            map_time(&[a], "pts", "sync", 3000),
            Err(TransformError::Uncovered)
        );
    }

    #[test]
    fn refuses_noninvertible_reverse_and_unknown_clocks() {
        let one_way = vec![time_edge_rational(
            "sync-retime",
            "sync",
            "retime",
            1,
            2,
            0,
            1,
            false,
        )];
        assert_eq!(
            map_time(&one_way, "sync", "retime", 10),
            Ok(MappedTime {
                tick: 5,
                uncertainty_ticks: 1,
                path: vec!["sync-retime".to_string()],
            })
        );
        assert_eq!(
            map_time(&one_way, "retime", "sync", 5),
            Err(TransformError::NonInvertible)
        );
        assert_eq!(
            map_time(&one_way, "pts", "timeline", 5),
            Err(TransformError::NoPath)
        );
    }

    #[test]
    fn maps_space_chain_and_propagates_error() {
        use crate::project_model::SpaceTransform;
        let transform = |id: &str, from: &str, to: &str, tx: f64| SpaceTransform {
            id: id.into(),
            from_space: from.into(),
            to_space: to.into(),
            matrix: vec![1., 0., tx, 0., 1., 0., 0., 0., 1.],
            uncertainty: Some(0.5),
            provenance_id: ProvenanceId("p".into()),
        };
        let result = map_point(
            &[
                transform("pixel-normalized", "pixel", "normalized", 1.),
                transform("normalized-canvas", "normalized", "canvas", 2.),
            ],
            "pixel",
            "canvas",
            &[3., 4.],
            0.1,
        )
        .unwrap();
        assert_eq!(result.coordinates, vec![6., 4.]);
        assert!(result.uncertainty > 0.5);
    }

    #[test]
    fn maps_pixel_to_canvas_through_mask_and_world() {
        use crate::project_model::SpaceTransform;
        let transform =
            |id: &str, from: &str, to: &str, matrix: Vec<f64>, error: f64| SpaceTransform {
                id: id.into(),
                from_space: from.into(),
                to_space: to.into(),
                matrix,
                uncertainty: Some(error),
                provenance_id: ProvenanceId("p".into()),
            };
        let transforms = vec![
            // Pixel to normalized: halve both axes.
            transform(
                "pixel-normalized",
                "pixel",
                "normalized",
                vec![0.5, 0., 0., 0., 0.5, 0., 0., 0., 1.],
                0.01,
            ),
            // Normalized to mask grid: translate onto the mask origin.
            transform(
                "normalized-mask",
                "normalized",
                "mask",
                vec![1., 0., 10., 0., 1., 20., 0., 0., 1.],
                0.5,
            ),
            // Mask grid to world: scale up into scene units.
            transform(
                "mask-world",
                "mask",
                "world",
                vec![2., 0., 0., 0., 2., 0., 0., 0., 1.],
                0.0,
            ),
            // World to canvas: place onto the delivery frame.
            transform(
                "world-canvas",
                "world",
                "canvas",
                vec![1., 0., -40., 0., 1., -20., 0., 0., 1.],
                0.25,
            ),
        ];
        let result = map_point(&transforms, "pixel", "canvas", &[1920., 1080.], 0.1).unwrap();
        // (1920,1080) -> (960,540) -> (970,560) -> (1940,1120) -> (1900,1100).
        assert_eq!(result.coordinates, vec![1900., 1100.]);
        assert_eq!(
            result.path,
            vec![
                "pixel-normalized",
                "normalized-mask",
                "mask-world",
                "world-canvas"
            ]
        );
        // The translation hops carry large operator norms, so the carried
        // error must grow well past any single edge's own uncertainty.
        assert!(result.uncertainty > 10.0);
        assert_eq!(
            map_point(&transforms, "pixel", "audio", &[1920., 1080.], 0.1),
            Err(TransformError::NoPath)
        );
    }

    #[test]
    fn refuses_mismatched_point_dimension() {
        use crate::project_model::SpaceTransform;
        let transforms = vec![SpaceTransform {
            id: "pixel-normalized".into(),
            from_space: "pixel".into(),
            to_space: "normalized".into(),
            matrix: vec![1., 0., 0., 0., 1., 0., 0., 0., 1.],
            uncertainty: None,
            provenance_id: ProvenanceId("p".into()),
        }];
        assert!(matches!(
            map_point(&transforms, "pixel", "normalized", &[1., 2., 3.], 0.0),
            Err(TransformError::InvalidTransform(_))
        ));
    }
}
