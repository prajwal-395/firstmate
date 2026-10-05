//! The CDL read-back comparison, ported from
//! `library/tools/cdl_readback.py` (`compare_cdl` and its helpers). Pure:
//! it compares a `GetCDL` answer against the four specified terms.

use serde_json::Value;

/// How far a read-back channel may sit from the specified one
/// (`cdl_readback.TRIPLE_TOLERANCE`).
const TRIPLE_TOLERANCE: f64 = 0.02;

/// `"1.0800 1.0800 1.0800"` -> [1.08, 1.08, 1.08], or None.
///
/// None means the read-back names no triple (empty answer, a scalar,
/// non numeric text) - the caller reports the grade unverified, never
/// parses a grade out of nothing. A lone scalar broadcasts to all three
/// channels.
fn parse_triple(raw: Option<&Value>) -> Option<Vec<f64>> {
    let raw = raw?;
    let text = raw.as_str()?;
    let parts: Result<Vec<f64>, _> = text.split_whitespace().map(|p| p.parse()).collect();
    let parts = parts.ok()?;
    if parts.len() == 1 {
        return Some(vec![parts[0]; 3]);
    }
    if parts.len() != 3 {
        return None;
    }
    Some(parts)
}

/// The four specified terms as floats, in `GetCDL` key spelling
/// (`cdl_readback.expected_terms`).
fn expected_terms(cdl_values: &Value) -> Vec<(&'static str, [f64; 3])> {
    let cdl = cdl_values.as_object();
    let get = |key: &str, default: f64| -> f64 {
        cdl.and_then(|m| m.get(key))
            .and_then(|v| v.as_f64())
            .unwrap_or(default)
    };
    vec![
        (
            "Slope",
            [
                get("slope_r", 1.0),
                get("slope_g", 1.0),
                get("slope_b", 1.0),
            ],
        ),
        (
            "Offset",
            [
                get("offset_r", 0.0),
                get("offset_g", 0.0),
                get("offset_b", 0.0),
            ],
        ),
        (
            "Power",
            [
                get("power_r", 1.0),
                get("power_g", 1.0),
                get("power_b", 1.0),
            ],
        ),
        ("Saturation", [get("saturation", 1.0); 3]),
    ]
}

/// The mismatch lines between a `GetCDL` answer and the plan. Empty when
/// the read-back IS the specified grade; one line per disagreeing
/// channel otherwise.
pub fn compare_cdl(actual: &Value, cdl_values: &Value) -> Vec<String> {
    let expected = expected_terms(cdl_values);
    let mut problems = Vec::new();
    for (key, want) in expected {
        let have = parse_triple(actual.get(key));
        let Some(have) = have else {
            let read_back = actual
                .get(key)
                .map(|v| format!("{v:?}"))
                .unwrap_or_else(|| "None".into());
            let mut expected_text = String::new();
            for (channel, value) in want.iter().enumerate() {
                expected_text.push_str(&format!("{value:.4}"));
                if channel < 2 {
                    expected_text.push(' ');
                }
            }
            problems.push(format!(
                "{key}: read back {read_back} - unverifiable, expected {expected_text}"
            ));
            continue;
        };
        for channel in 0..3usize {
            let h: f64 = have[channel];
            let w: f64 = want[channel];
            if (h - w).abs() > TRIPLE_TOLERANCE {
                problems.push(format!(
                    "{key}[{channel}]: reads {:.4}, specified {:.4}",
                    h, w
                ));
            }
        }
    }
    problems
}
