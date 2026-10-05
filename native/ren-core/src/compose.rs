//! The conflict algebra, ported from `library/tools/patch_algebra.py`:
//! whether two prepared EditPatches can both land, and how a stale patch
//! moves onto the head. Two writes MEET when one's key is a prefix of
//! the other's; the semantics two meeting writes compose under is the
//! strictest of the two operations' own and the two capabilities'
//! declared ones.

use ren_protocol::edit::{EditOp, EditPatch};
use serde::Deserialize;
use serde_json::Value;

use crate::algebra::{algebra, key, meet, MergeSemantics, TemporalEffect};
use crate::conditions::judge_all;
use crate::patch::validate_patch;
use crate::snapshot::Snapshot;
use crate::verify::verify;

/// The composition verdicts, least to most constraining
/// (`patch_algebra.VERDICTS`).
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Verdict {
    Commute,
    Merge,
    Serialize,
    Rebase,
    Conflict,
}

impl Verdict {
    pub fn rank(&self) -> usize {
        match self {
            Verdict::Commute => 0,
            Verdict::Merge => 1,
            Verdict::Serialize => 2,
            Verdict::Rebase => 3,
            Verdict::Conflict => 4,
        }
    }

    pub fn as_str(&self) -> &'static str {
        match self {
            Verdict::Commute => "commute",
            Verdict::Merge => "merge",
            Verdict::Serialize => "serialize",
            Verdict::Rebase => "rebase",
            Verdict::Conflict => "conflict",
        }
    }
}

/// How `later` lands once `earlier` has; both planned on the same base.
#[derive(Clone, Debug)]
pub struct Composition {
    pub verdict: Verdict,
    pub reason: String,
}

/// A capability's declared patch semantics, as compose reads them.
#[derive(Deserialize)]
pub struct Semantics {
    pub temporal_effect: String,
    pub merge_semantics: String,
}

fn semantics_of(value: &Value) -> Option<Semantics> {
    serde_json::from_value(value.clone()).ok()
}

fn strictest(semantics: &[&str]) -> Option<&'static str> {
    semantics
        .iter()
        .filter_map(|s| MergeSemantics::parse(s))
        .max_by_key(|m| m.rank())
        .map(|m| m.as_str())
}

/// How `later` lands once `earlier` has; both planned on `base`.
/// `a_sem`/`b_sem` are the two capabilities' declared patch semantics.
pub fn compose(
    earlier: &EditPatch,
    later: &EditPatch,
    base: &Snapshot,
    a_sem: &Value,
    b_sem: &Value,
) -> Composition {
    if (earlier.project.as_str(), earlier.timeline.as_str())
        != (later.project.as_str(), later.timeline.as_str())
    {
        return Composition {
            verdict: Verdict::Commute,
            reason: "they address different timelines".into(),
        };
    }
    let (Some(a), Some(b)) = (semantics_of(a_sem), semantics_of(b_sem)) else {
        return Composition {
            verdict: Verdict::Commute,
            reason: "no write of one meets a write of the other".into(),
        };
    };
    let mut found = vec![Composition {
        verdict: Verdict::Commute,
        reason: "no write of one meets a write of the other".into(),
    }];

    let a_temporal = TemporalEffect::parse(&a.temporal_effect);
    let b_temporal = TemporalEffect::parse(&b.temporal_effect);
    let shared: Vec<&String> = earlier
        .conflict_domains
        .iter()
        .filter(|d| later.conflict_domains.contains(d))
        .collect();
    if !shared.is_empty()
        && matches!(
            (a_temporal, b_temporal),
            (Some(TemporalEffect::Global), _) | (_, Some(TemporalEffect::Global))
        )
    {
        let mut names: Vec<&String> = shared;
        names.sort();
        return Composition {
            verdict: Verdict::Conflict,
            reason: format!(
                "a global effect on {:?} meets the other patch wherever it acts",
                names
            ),
        };
    }

    if a_temporal == Some(TemporalEffect::Ripple) {
        let Some(point) = earlier.affected_spans.iter().map(|s| s.0).min() else {
            return Composition {
                verdict: Verdict::Commute,
                reason: "no write of one meets a write of the other".into(),
            };
        };
        for op in &later.operations {
            let Some(decl) = algebra().iter().find(|d| d.id == op.op_id()) else {
                continue;
            };
            let Some(span) = crate::algebra::span(op, base) else {
                return Composition {
                    verdict: Verdict::Conflict,
                    reason: format!(
                        "{} targets clip {:?}, which is not exactly once on the generation before {:?}",
                        op.op_id(),
                        crate::algebra::op_unique_id(op).unwrap_or_else(|| "None".into()),
                        earlier.id
                    ),
                };
            };
            if span.1 <= point {
                continue;
            }
            if !decl.item {
                return Composition {
                    verdict: Verdict::Conflict,
                    reason: format!(
                        "{} addresses frame {}, past where patch {:?} ripples from ({point}), and a frame does not move with the cut",
                        op.op_id(),
                        crate::algebra::op_frame(op).unwrap_or(-1),
                        earlier.id
                    ),
                };
            }
            found.push(Composition {
                verdict: Verdict::Rebase,
                reason: format!(
                    "patch {:?} ripples from frame {point}, past clip {:?}: its span is re-measured",
                    earlier.id,
                    crate::algebra::op_unique_id(op).unwrap_or_else(|| "None".into())
                ),
            });
        }
    }

    for mine in &earlier.operations {
        let Some(mine_decl) = algebra().iter().find(|d| d.id == mine.op_id()) else {
            continue;
        };
        for theirs in &later.operations {
            let Some(theirs_decl) = algebra().iter().find(|d| d.id == theirs.op_id()) else {
                continue;
            };
            let theirs_key = key(theirs);
            if !meet(&key(mine), &theirs_key) {
                continue;
            }
            let Some(semantics) = strictest(&[
                &a.merge_semantics,
                &b.merge_semantics,
                mine_decl.merge.as_str(),
                theirs_decl.merge.as_str(),
            ]) else {
                continue;
            };
            let what = format!(
                "{} and {} both write {:?} under {semantics:?}",
                mine.op_id(),
                theirs.op_id(),
                key(theirs)
                    .iter()
                    .map(|p| match p {
                        crate::algebra::KeyPart::Text(t) => t.clone(),
                        crate::algebra::KeyPart::Num(n) => n.to_string(),
                    })
                    .collect::<Vec<_>>()
            );
            if semantics == "exclusive" {
                return Composition {
                    verdict: Verdict::Conflict,
                    reason: what,
                };
            }
            if mine == theirs {
                found.push(Composition {
                    verdict: Verdict::Merge,
                    reason: format!("{what}, identically"),
                });
            } else if semantics == "ordered" {
                found.push(Composition {
                    verdict: Verdict::Serialize,
                    reason: what,
                });
            } else if semantics == "replace" {
                return Composition {
                    verdict: Verdict::Conflict,
                    reason: format!("{what}, with different values: a lost update"),
                };
            }
        }
    }

    found
        .into_iter()
        .max_by_key(|c| c.verdict.rank())
        .unwrap_or(Composition {
            verdict: Verdict::Commute,
            reason: "no write of one meets a write of the other".into(),
        })
}

/// One generation since the patch's base, as `carry` reads it.
#[derive(Deserialize)]
pub struct CarryRow {
    pub generation: i64,
    pub source: String,
    #[serde(default)]
    pub patch: Option<EditPatch>,
    pub snapshot: Snapshot,
    /// The generation's own capability's declared patch semantics. The
    /// Rust side reads no registry, so the caller supplies each
    /// generation's semantics beside its patch; `compose` judges the
    /// pair under what EACH capability declares.
    #[serde(default)]
    pub semantics: Option<Value>,
}

/// `patch` moved onto the head, or the reason it cannot.
///
/// Composes `patch` with every generation recorded since its base, in
/// order. An `observed` generation refuses: its change is unattributed.
/// The moved patch must then validate on the head and its preconditions
/// hold there. `semantics` is the capability's declared patch semantics.
pub fn carry(patch: &EditPatch, rows: &[CarryRow], semantics: &Value) -> Result<EditPatch, String> {
    let mut moved = patch.clone();
    let Some(first) = rows.first() else {
        return Ok(moved);
    };
    let mut base_snap = first.snapshot.clone();
    let mut head: Option<i64> = None;
    for row in &rows[1..] {
        head = Some(row.generation);
        if row.source == "observed" {
            return Err(format!(
                "generation {} is a change nobody recorded (a hand edit), so nothing can say what it meets",
                row.generation
            ));
        }
        let Some(earlier_patch) = &row.patch else {
            continue;
        };
        let earlier_semantics = row.semantics.clone().unwrap_or_else(|| semantics.clone());
        let found = compose(
            earlier_patch,
            &moved,
            &base_snap,
            &earlier_semantics,
            semantics,
        );
        if found.verdict == Verdict::Conflict {
            return Err(format!(
                "generation {} (patch {:?}): {}",
                row.generation, earlier_patch.id, found.reason
            ));
        }
        if found.verdict == Verdict::Rebase {
            moved = respan(&moved, earlier_patch, &base_snap, &row.snapshot);
        }
        moved.base_generation = ren_protocol::ids::Generation(row.generation as u64);
        base_snap = row.snapshot.clone();
    }
    if head.is_none() {
        return Ok(moved);
    }
    validate_patch(&moved, &base_snap, semantics)?;
    let failures = judge_all(&moved.preconditions, &base_snap, &base_snap);
    if !failures.is_empty() {
        return Err(failures.join("; "));
    }
    Ok(moved)
}

/// `patch` with every span past `earlier`'s ripple re-measured
/// (`patch_algebra._respan`).
pub fn respan(
    patch: &EditPatch,
    earlier: &EditPatch,
    base: &Snapshot,
    head: &Snapshot,
) -> EditPatch {
    let point = match earlier.affected_spans.iter().map(|s| s.0).min() {
        Some(point) => point,
        None => return patch.clone(),
    };
    let mut kept: Vec<(i64, i64)> = patch
        .affected_spans
        .iter()
        .filter(|s| s.1 <= point)
        .copied()
        .collect();
    for op in &patch.operations {
        let Some(span) = crate::algebra::span(op, base) else {
            continue;
        };
        if span.1 > point {
            if let Some(new_span) = crate::algebra::span(op, head) {
                kept.push(new_span);
            }
        }
    }
    let mut moved = patch.clone();
    moved.affected_spans = kept;
    moved
}

/// Every capability declaration its own operations break
/// (`patch_algebra.problems`). `declarations` maps a capability id to
/// its declared patch semantics; `capability_ids` are the registered
/// ones.
pub fn problems(declarations: &Value, capability_ids: &Value) -> Vec<String> {
    let Some(declarations) = declarations.as_object() else {
        return vec!["declarations must be an object".into()];
    };
    let capability_ids: Vec<String> = capability_ids
        .as_array()
        .map(|arr| {
            arr.iter()
                .filter_map(|v| v.as_str().map(String::from))
                .collect()
        })
        .unwrap_or_default();
    let mut out = Vec::new();
    for (cap_id, sem) in declarations {
        let name = format!("{cap_id}.execution.patch");
        if !capability_ids.contains(cap_id) {
            out.push(format!("{name} names no registered capability"));
        }
        let temporal_effect = sem
            .get("temporal_effect")
            .and_then(|v| v.as_str())
            .unwrap_or("");
        let merge_semantics = sem
            .get("merge_semantics")
            .and_then(|v| v.as_str())
            .unwrap_or("");
        if TemporalEffect::parse(temporal_effect).is_none() {
            out.push(format!(
                "{name}: temporal_effect '{temporal_effect}' is not one of ('local', 'ripple', 'global')"
            ));
            continue;
        }
        if MergeSemantics::parse(merge_semantics).is_none() {
            out.push(format!(
                "{name}: merge_semantics '{merge_semantics}' is not one of ('commutative', 'ordered', 'replace', 'exclusive')"
            ));
            continue;
        }
        let domains: Vec<String> = sem
            .get("conflict_domains")
            .and_then(|v| v.as_array())
            .map(|arr| {
                arr.iter()
                    .filter_map(|v| v.as_str().map(String::from))
                    .collect()
            })
            .unwrap_or_default();
        let unknown: Vec<&String> = domains
            .iter()
            .filter(|d| !crate::patch::CONFLICT_DOMAINS.contains(&d.as_str()))
            .collect();
        if !unknown.is_empty() {
            let mut names: Vec<&String> = unknown;
            names.sort();
            let quoted: Vec<String> = names.iter().map(|d| format!("'{d}'")).collect();
            out.push(format!(
                "{name}: conflict domains [{}] are not in edit_patch.CONFLICT_DOMAINS",
                quoted.join(", ")
            ));
        }
        let operations: Vec<String> = sem
            .get("operations")
            .and_then(|v| v.as_array())
            .map(|arr| {
                arr.iter()
                    .filter_map(|v| v.as_str().map(String::from))
                    .collect()
            })
            .unwrap_or_default();
        if operations.is_empty() {
            out.push(format!(
                "{name} declares no operations: a capability that authors no patch has no declaration"
            ));
        }
        for op_name in &operations {
            let Some(spec) = algebra().iter().find(|d| d.id == op_name.as_str()) else {
                out.push(format!(
                    "{name}: operation '{op_name}' is not in edit_patch.OPERATIONS"
                ));
                continue;
            };
            if !domains.contains(&spec.domain.to_string()) {
                out.push(format!(
                    "{name}: {op_name} writes '{}', which it does not declare",
                    spec.domain
                ));
            }
            if spec.temporal.rank() > TemporalEffect::parse(temporal_effect).unwrap().rank() {
                out.push(format!(
                    "{name}: declares '{temporal_effect}', and {op_name} is '{}'",
                    spec.temporal.as_str()
                ));
            }
            if spec.merge.rank() > MergeSemantics::parse(merge_semantics).unwrap().rank() {
                out.push(format!(
                    "{name}: declares '{merge_semantics}', and {op_name} is '{}' - a scheduler trusting the declaration would let a lost update through",
                    spec.merge.as_str()
                ));
            }
        }
    }
    out
}

/// The verify judgement, re-exported for the Python boundary.
pub fn verify_op(op: &EditOp, after: &Snapshot) -> Option<String> {
    verify(op, after)
}
