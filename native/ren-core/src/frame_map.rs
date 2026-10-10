//! Exact, fail-closed frame correspondence over the canonical Project Model.
//!
//! Rates are output ticks per input tick. Integer tick conversion is required:
//! a rational result between ticks is refused rather than rounded.
use ren_protocol::project_model::{FrameMap, FrameMapSegment, Interval, MappingKind, Rational};

#[derive(Clone, Debug, PartialEq, Eq)]
pub enum FrameMapError {
    ClockMismatch,
    InvalidRate,
    Overflow,
    InexactTick,
    Uncovered,
    Ambiguous,
    NonInvertible,
    Generated,
    InvalidRange,
}

/// The operation is recorded for audit/debugging; the compiler supplies only
/// newly measured segments inside the declared affected source interval.
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum EditKind {
    Cut,
    Split,
    Ripple,
    Retime,
    FrameRateChange,
    DurationChange,
}

#[derive(Clone, Debug, PartialEq)]
pub struct FrameMapEdit {
    pub kind: EditKind,
    pub affected: Interval,
    pub replacements: Vec<FrameMapSegment>,
}

/// Splice newly computed correspondence into a map, retaining the unaffected
/// prefix/suffix of existing segments exactly. No other segment is recomputed.
pub fn recompute_affected(map: &FrameMap, edit: &FrameMapEdit) -> Result<FrameMap, FrameMapError> {
    if edit.affected.clock_id != map.from_clock || edit.affected.start >= edit.affected.end {
        return Err(FrameMapError::InvalidRange);
    }
    if edit.replacements.iter().any(|s| {
        s.from.clock_id != map.from_clock
            || s.to.clock_id != map.to_clock
            || s.from.start < edit.affected.start
            || s.from.end > edit.affected.end
            || s.from.start >= s.from.end
            || s.to.start >= s.to.end
    }) {
        return Err(FrameMapError::InvalidRange);
    }
    let mut segments = Vec::new();
    for old in &map.segments {
        let (n, d) = rate(old)?;
        for (lo, hi) in [
            (old.from.start, old.from.end.min(edit.affected.start)),
            (old.from.start.max(edit.affected.end), old.from.end),
        ] {
            if lo < hi {
                let out_lo = affine(lo, &old.from, &old.to, n, d)?;
                let out_hi = affine(hi, &old.from, &old.to, n, d)?;
                let mut kept = old.clone();
                kept.from.start = lo;
                kept.from.end = hi;
                kept.to.start = out_lo;
                kept.to.end = out_hi;
                segments.push(kept);
            }
        }
    }
    segments.extend(edit.replacements.clone());
    segments.sort_by_key(|s| s.from.start);
    // Refuse overlapping input spans; callers must provide the complete new shape.
    if segments
        .windows(2)
        .any(|pair| pair[0].from.end > pair[1].from.start)
    {
        return Err(FrameMapError::Ambiguous);
    }
    let mut result = map.clone();
    result.segments = segments;
    Ok(result)
}

fn rate(segment: &FrameMapSegment) -> Result<(i128, i128), FrameMapError> {
    let (n, d) = match &segment.rate {
        Some(Rational {
            numerator,
            denominator,
        }) if *numerator > 0 && *denominator > 0 => (*numerator as i128, *denominator as i128),
        Some(_) => return Err(FrameMapError::InvalidRate),
        None => (
            i128::from(segment.to.end - segment.to.start),
            i128::from(segment.from.end - segment.from.start),
        ),
    };
    Ok((n, d))
}

fn affine(
    value: i64,
    from: &Interval,
    to: &Interval,
    n: i128,
    d: i128,
) -> Result<i64, FrameMapError> {
    let delta = i128::from(value) - i128::from(from.start);
    let product = delta.checked_mul(n).ok_or(FrameMapError::Overflow)?;
    if product % d != 0 {
        return Err(FrameMapError::InexactTick);
    }
    let mapped = i128::from(to.start)
        .checked_add(product / d)
        .ok_or(FrameMapError::Overflow)?;
    i64::try_from(mapped).map_err(|_| FrameMapError::Overflow)
}

fn usable(segment: &FrameMapSegment, inverse: bool) -> Result<(), FrameMapError> {
    match segment.mapping_kind {
        MappingKind::Generated | MappingKind::Gap => Err(FrameMapError::Generated),
        MappingKind::TransitionBlend => Err(FrameMapError::Ambiguous),
        MappingKind::Freeze | MappingKind::Repeat if inverse => Err(FrameMapError::NonInvertible),
        MappingKind::Freeze | MappingKind::Repeat => Err(FrameMapError::Ambiguous),
        _ => Ok(()),
    }
}

/// Map a tick. At a shared boundary the half-open segment beginning there owns it.
pub fn map_tick(map: &FrameMap, clock_id: &str, tick: i64) -> Result<i64, FrameMapError> {
    if map.from_clock.0 != clock_id {
        return Err(FrameMapError::ClockMismatch);
    }
    let matches: Vec<_> = map
        .segments
        .iter()
        .filter(|s| s.from.start <= tick && tick < s.from.end)
        .collect();
    if matches.is_empty() {
        return Err(FrameMapError::Uncovered);
    }
    if matches.len() != 1 {
        return Err(FrameMapError::Ambiguous);
    }
    let s = matches[0];
    usable(s, false)?;
    let (n, d) = rate(s)?;
    affine(tick, &s.from, &s.to, n, d)
}

pub fn inverse_tick(map: &FrameMap, clock_id: &str, tick: i64) -> Result<i64, FrameMapError> {
    if map.to_clock.0 != clock_id {
        return Err(FrameMapError::ClockMismatch);
    }
    let matches: Vec<_> = map
        .segments
        .iter()
        .filter(|s| s.to.start <= tick && tick < s.to.end)
        .collect();
    if matches.is_empty() {
        return Err(FrameMapError::Uncovered);
    }
    if matches.len() != 1 {
        return Err(FrameMapError::Ambiguous);
    }
    let s = matches[0];
    usable(s, true)?;
    let (n, d) = rate(s)?;
    affine(tick, &s.to, &s.from, d, n)
}

/// Translate a keyframe through its old render to a shared anchor and into the new render.
pub fn translate_keyframe(old: &FrameMap, new: &FrameMap, tick: i64) -> Result<i64, FrameMapError> {
    if old.from_clock != new.from_clock {
        return Err(FrameMapError::ClockMismatch);
    }
    let anchor = inverse_tick(old, &old.to_clock.0, tick)?;
    map_tick(new, &new.from_clock.0, anchor)
}

/// A range translates only when one exact segment covers the whole half-open range.
pub fn translate_range(
    old: &FrameMap,
    new: &FrameMap,
    range: &Interval,
) -> Result<Interval, FrameMapError> {
    if range.start >= range.end || range.clock_id != old.to_clock {
        return Err(FrameMapError::InvalidRange);
    }
    if old.from_clock != new.from_clock {
        return Err(FrameMapError::ClockMismatch);
    }
    let old_matches: Vec<_> = old
        .segments
        .iter()
        .filter(|s| s.to.start <= range.start && range.end <= s.to.end)
        .collect();
    if old_matches.len() != 1 {
        return Err(if old_matches.is_empty() {
            FrameMapError::Uncovered
        } else {
            FrameMapError::Ambiguous
        });
    }
    let old_segment = old_matches[0];
    usable(old_segment, true)?;
    let (old_n, old_d) = rate(old_segment)?;
    let anchor_start = affine(
        range.start,
        &old_segment.to,
        &old_segment.from,
        old_d,
        old_n,
    )?;
    let anchor_end = affine(range.end, &old_segment.to, &old_segment.from, old_d, old_n)?;
    let new_matches: Vec<_> = new
        .segments
        .iter()
        .filter(|s| s.from.start <= anchor_start && anchor_end <= s.from.end)
        .collect();
    if new_matches.len() != 1 {
        return Err(if new_matches.is_empty() {
            FrameMapError::Uncovered
        } else {
            FrameMapError::Ambiguous
        });
    }
    let new_segment = new_matches[0];
    usable(new_segment, false)?;
    let (new_n, new_d) = rate(new_segment)?;
    let a = affine(
        anchor_start,
        &new_segment.from,
        &new_segment.to,
        new_n,
        new_d,
    )?;
    let b = affine(anchor_end, &new_segment.from, &new_segment.to, new_n, new_d)?;
    if b <= a {
        return Err(FrameMapError::NonInvertible);
    }
    Ok(Interval {
        clock_id: new.to_clock.clone(),
        start: a,
        end: b,
    })
}

/// Compose A->B and B->C maps. Only exact, one-to-one/retimed overlap is emitted.
pub fn compose(first: &FrameMap, second: &FrameMap) -> Result<FrameMap, FrameMapError> {
    if first.to_clock != second.from_clock {
        return Err(FrameMapError::ClockMismatch);
    }
    let mut segments = Vec::new();
    for a in &first.segments {
        usable(a, false)?;
        let (an, ad) = rate(a)?;
        for b in &second.segments {
            usable(b, false)?;
            let (lo, hi) = (a.to.start.max(b.from.start), a.to.end.min(b.from.end));
            if lo >= hi {
                continue;
            }
            let from_lo = affine(lo, &a.to, &a.from, ad, an)?;
            let from_hi = affine(hi, &a.to, &a.from, ad, an)?;
            let to_lo = affine(lo, &b.from, &b.to, rate(b)?.0, rate(b)?.1)?;
            let to_hi = affine(hi, &b.from, &b.to, rate(b)?.0, rate(b)?.1)?;
            let bn_bd = rate(b)?;
            let mut n = an.checked_mul(bn_bd.0).ok_or(FrameMapError::Overflow)?;
            let mut d = ad.checked_mul(bn_bd.1).ok_or(FrameMapError::Overflow)?;
            let gcd = gcd(n, d);
            n /= gcd;
            d /= gcd;
            let mapping_kind = if matches!(a.mapping_kind, MappingKind::Retimed)
                || matches!(b.mapping_kind, MappingKind::Retimed)
            {
                MappingKind::Retimed
            } else {
                MappingKind::OneToOne
            };
            segments.push(FrameMapSegment {
                from: Interval {
                    clock_id: first.from_clock.clone(),
                    start: from_lo,
                    end: from_hi,
                },
                to: Interval {
                    clock_id: second.to_clock.clone(),
                    start: to_lo,
                    end: to_hi,
                },
                mapping_kind,
                rate: Some(Rational {
                    numerator: i64::try_from(n).map_err(|_| FrameMapError::Overflow)?,
                    denominator: u32::try_from(d).map_err(|_| FrameMapError::Overflow)?,
                }),
                source_clip_use: a
                    .source_clip_use
                    .clone()
                    .or_else(|| b.source_clip_use.clone()),
            });
        }
    }
    if segments.is_empty() {
        return Err(FrameMapError::Uncovered);
    }
    segments.sort_by_key(|s| s.from.start);
    Ok(FrameMap {
        id: first.id.clone(),
        from_clock: first.from_clock.clone(),
        to_clock: second.to_clock.clone(),
        segments,
    })
}

fn gcd(mut a: i128, mut b: i128) -> i128 {
    while b != 0 {
        let r = a % b;
        a = b;
        b = r;
    }
    a.max(1)
}

#[cfg(test)]
mod tests {
    use super::*;
    use ren_protocol::project_model::{ClockId, FrameMapId};
    fn segment(a: i64, b: i64, c: i64, d: i64, kind: MappingKind) -> FrameMapSegment {
        FrameMapSegment {
            from: Interval {
                clock_id: ClockId("source".into()),
                start: a,
                end: b,
            },
            to: Interval {
                clock_id: ClockId("render".into()),
                start: c,
                end: d,
            },
            mapping_kind: kind,
            rate: None,
            source_clip_use: None,
        }
    }
    fn map(segs: Vec<FrameMapSegment>, from: &str, to: &str) -> FrameMap {
        FrameMap {
            id: FrameMapId("m".into()),
            from_clock: ClockId(from.into()),
            to_clock: ClockId(to.into()),
            segments: segs,
        }
    }
    #[test]
    fn composition_and_inverse_hold_for_integer_affine_maps() {
        // Small exhaustive property grid exercises boundaries and inverse composition.
        for len in 1..40 {
            for rate_n in 1..5 {
                for rate_d in 1..5 {
                    // Inverse identity is required only for bijective maps.
                    if rate_n != rate_d || len * rate_n % rate_d != 0 {
                        continue;
                    }
                    let out = len * rate_n / rate_d;
                    let a = map(
                        vec![segment(10, 10 + len, 20, 20 + out, MappingKind::Retimed)],
                        "source",
                        "middle",
                    );
                    let b = map(
                        vec![FrameMapSegment {
                            from: Interval {
                                clock_id: ClockId("middle".into()),
                                start: 20,
                                end: 20 + out,
                            },
                            to: Interval {
                                clock_id: ClockId("render".into()),
                                start: 100,
                                end: 100 + out,
                            },
                            mapping_kind: MappingKind::OneToOne,
                            rate: None,
                            source_clip_use: None,
                        }],
                        "middle",
                        "render",
                    );
                    let c = compose(&a, &b).unwrap();
                    for t in 10..10 + len {
                        let mapped = map_tick(&c, "source", t).unwrap();
                        assert_eq!(inverse_tick(&c, "render", mapped).unwrap(), t);
                    }
                }
            }
        }
        // Rational rates greater than one are invertible on the representable
        // output ticks produced by the map (the inverse need not accept gaps).
        for len in 1..20 {
            for scale in 2..5 {
                let middle_len = len * scale;
                let a = map(
                    vec![FrameMapSegment {
                        from: Interval {
                            clock_id: ClockId("source".into()),
                            start: 0,
                            end: len,
                        },
                        to: Interval {
                            clock_id: ClockId("middle".into()),
                            start: 50,
                            end: 50 + middle_len,
                        },
                        mapping_kind: MappingKind::Retimed,
                        rate: Some(Rational {
                            numerator: scale,
                            denominator: 1,
                        }),
                        source_clip_use: None,
                    }],
                    "source",
                    "middle",
                );
                let b = map(
                    vec![FrameMapSegment {
                        from: Interval {
                            clock_id: ClockId("middle".into()),
                            start: 50,
                            end: 50 + middle_len,
                        },
                        to: Interval {
                            clock_id: ClockId("render".into()),
                            start: 100,
                            end: 100 + middle_len,
                        },
                        mapping_kind: MappingKind::OneToOne,
                        rate: None,
                        source_clip_use: None,
                    }],
                    "middle",
                    "render",
                );
                let c = compose(&a, &b).unwrap();
                for t in 0..len {
                    let mapped = map_tick(&c, "source", t).unwrap();
                    assert_eq!(inverse_tick(&c, "render", mapped).unwrap(), t);
                }
            }
        }
    }
    #[test]
    fn shorter_replacement_remaps_or_refuses_all_three_keys() {
        let prior = map(
            vec![segment(0, 412, 0, 412, MappingKind::OneToOne)],
            "anchor",
            "old-render",
        );
        let shorter = map(
            vec![segment(0, 200, 0, 200, MappingKind::OneToOne)],
            "anchor",
            "new-render",
        );
        assert_eq!(translate_keyframe(&prior, &shorter, 0), Ok(0));
        assert_eq!(
            translate_keyframe(&prior, &shorter, 205),
            Err(FrameMapError::Uncovered)
        );
        assert_eq!(
            translate_keyframe(&prior, &shorter, 411),
            Err(FrameMapError::Uncovered)
        );
        let range = Interval {
            clock_id: prior.to_clock.clone(),
            start: 205,
            end: 412,
        };
        assert_eq!(
            translate_range(&prior, &shorter, &range),
            Err(FrameMapError::Uncovered)
        );
    }
    #[test]
    fn edit_recomputes_only_affected_window() {
        let original = map(
            vec![segment(0, 100, 0, 100, MappingKind::OneToOne)],
            "a",
            "b",
        );
        let edited = recompute_affected(
            &original,
            &FrameMapEdit {
                kind: EditKind::Retime,
                affected: Interval {
                    clock_id: ClockId("a".into()),
                    start: 40,
                    end: 60,
                },
                replacements: vec![FrameMapSegment {
                    from: Interval {
                        clock_id: ClockId("a".into()),
                        start: 40,
                        end: 60,
                    },
                    to: Interval {
                        clock_id: ClockId("b".into()),
                        start: 45,
                        end: 65,
                    },
                    mapping_kind: MappingKind::Retimed,
                    rate: None,
                    source_clip_use: None,
                }],
            },
        )
        .unwrap();
        assert_eq!(edited.segments.len(), 3);
        assert_eq!(
            (
                edited.segments[0].from.start,
                edited.segments[0].from.end,
                edited.segments[0].to.start,
                edited.segments[0].to.end
            ),
            (0, 40, 0, 40)
        );
        assert_eq!(
            (
                edited.segments[2].from.start,
                edited.segments[2].from.end,
                edited.segments[2].to.start,
                edited.segments[2].to.end
            ),
            (60, 100, 60, 100)
        );
    }
    #[test]
    fn generated_and_overlap_are_never_guessed() {
        let generated = map(
            vec![segment(0, 10, 0, 10, MappingKind::Generated)],
            "a",
            "b",
        );
        assert_eq!(map_tick(&generated, "a", 0), Err(FrameMapError::Generated));
        let overlap = map(
            vec![
                segment(0, 10, 0, 10, MappingKind::OneToOne),
                segment(5, 15, 10, 20, MappingKind::OneToOne),
            ],
            "a",
            "b",
        );
        assert_eq!(map_tick(&overlap, "a", 6), Err(FrameMapError::Ambiguous));
    }

    #[test]
    fn rational_mapping_refuses_fractional_ticks() {
        let retimed = map(
            vec![FrameMapSegment {
                from: Interval {
                    clock_id: ClockId("a".into()),
                    start: 0,
                    end: 3,
                },
                to: Interval {
                    clock_id: ClockId("b".into()),
                    start: 0,
                    end: 2,
                },
                mapping_kind: MappingKind::Retimed,
                rate: Some(Rational {
                    numerator: 2,
                    denominator: 3,
                }),
                source_clip_use: None,
            }],
            "a",
            "b",
        );
        assert_eq!(map_tick(&retimed, "a", 1), Err(FrameMapError::InexactTick));
    }
}
