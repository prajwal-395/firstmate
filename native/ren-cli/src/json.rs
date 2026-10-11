//! A JSON value and serializer that matches Python's `json.dumps(indent=2)`
//! byte for byte, so a native command's output cannot drift from the Python
//! implementation it replaces.
//!
//! Three differences from `serde_json`'s default output are the reason this
//! exists rather than using it:
//!
//! * `ensure_ascii` - Python escapes every non-ASCII character as `\uXXXX`;
//!   `serde_json` writes raw UTF-8.
//! * float formatting - Python's `repr` keeps `.0` on an integral float
//!   (`30.0`); Rust's `Display` drops it (`30`).
//! * key order - Python dicts keep insertion order; `serde_json::Map` sorts
//!   unless `preserve_order` is set. This type keeps a `Vec` of pairs, so the
//!   order is whatever the builder chose, which is the whole point.

use std::fmt::Write as _;

/// A JSON value. `Object` keeps its pairs in insertion order.
#[derive(Debug, Clone, PartialEq)]
pub enum Json {
    Null,
    Bool(bool),
    Int(i64),
    Float(f64),
    Str(String),
    Array(Vec<Json>),
    Object(Vec<(String, Json)>),
}

impl Json {
    pub fn str(value: impl Into<String>) -> Json {
        Json::Str(value.into())
    }

    pub fn get(&self, key: &str) -> Option<&Json> {
        match self {
            Json::Object(pairs) => pairs.iter().find(|(k, _)| k == key).map(|(_, v)| v),
            _ => None,
        }
    }

    pub fn as_str(&self) -> Option<&str> {
        match self {
            Json::Str(s) => Some(s),
            _ => None,
        }
    }

    pub fn as_array(&self) -> Option<&[Json]> {
        match self {
            Json::Array(items) => Some(items),
            _ => None,
        }
    }

    /// The value Python's `json.dumps` would produce, with `indent=2`.
    pub fn to_python_json(&self) -> String {
        let mut out = String::new();
        self.write_pretty(&mut out, 0);
        out
    }

    fn write_pretty(&self, out: &mut String, depth: usize) {
        match self {
            Json::Null => out.push_str("null"),
            Json::Bool(true) => out.push_str("true"),
            Json::Bool(false) => out.push_str("false"),
            Json::Int(n) => {
                let _ = write!(out, "{n}");
            }
            Json::Float(f) => out.push_str(&format_float(*f)),
            Json::Str(s) => write_json_string(out, s),
            Json::Array(items) => {
                if items.is_empty() {
                    out.push_str("[]");
                    return;
                }
                out.push_str("[\n");
                for (index, item) in items.iter().enumerate() {
                    if index > 0 {
                        out.push_str(",\n");
                    }
                    push_indent(out, depth + 1);
                    item.write_pretty(out, depth + 1);
                }
                out.push('\n');
                push_indent(out, depth);
                out.push(']');
            }
            Json::Object(pairs) => {
                if pairs.is_empty() {
                    out.push_str("{}");
                    return;
                }
                out.push_str("{\n");
                for (index, (key, value)) in pairs.iter().enumerate() {
                    if index > 0 {
                        out.push_str(",\n");
                    }
                    push_indent(out, depth + 1);
                    write_json_string(out, key);
                    out.push_str(": ");
                    value.write_pretty(out, depth + 1);
                }
                out.push('\n');
                push_indent(out, depth);
                out.push('}');
            }
        }
    }
}

fn push_indent(out: &mut String, depth: usize) {
    for _ in 0..depth {
        out.push_str("  ");
    }
}

/// A float the way Python's `repr` prints it: shortest round-trip, with `.0`
/// kept on an integral value (`30.0`, not `30`).
pub fn format_float(value: f64) -> String {
    if value.is_finite() && value.fract() == 0.0 && value.abs() < 1e16 {
        let whole = value.trunc() as i64;
        return format!("{whole}.0");
    }
    let mut text = format!("{value}");
    if !text.contains('.') && !text.contains('e') && !text.contains('E') {
        text.push_str(".0");
    }
    text
}

/// A JSON string literal with Python's `ensure_ascii` escaping: every
/// character above `0x7E` becomes `\uXXXX` (a surrogate pair above the BMP),
/// and the control characters Python short-escapes use its shortcuts.
fn write_json_string(out: &mut String, value: &str) {
    out.push('"');
    for ch in value.chars() {
        match ch {
            '"' => out.push_str("\\\""),
            '\\' => out.push_str("\\\\"),
            '\n' => out.push_str("\\n"),
            '\r' => out.push_str("\\r"),
            '\t' => out.push_str("\\t"),
            '\u{08}' => out.push_str("\\b"),
            '\u{0c}' => out.push_str("\\f"),
            c if (c as u32) < 0x20 => {
                let _ = write!(out, "\\u{:04x}", c as u32);
            }
            c if (c as u32) < 0x7f => out.push(c),
            c => {
                let code = c as u32;
                if code > 0xffff {
                    let adjusted = code - 0x10000;
                    let high = 0xd800 + (adjusted >> 10);
                    let low = 0xdc00 + (adjusted & 0x3ff);
                    let _ = write!(out, "\\u{:04x}\\u{:04x}", high, low);
                } else {
                    let _ = write!(out, "\\u{:04x}", code);
                }
            }
        }
    }
    out.push('"');
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn an_empty_object_and_array_match_python() {
        assert_eq!(Json::Object(vec![]).to_python_json(), "{}");
        assert_eq!(Json::Array(vec![]).to_python_json(), "[]");
    }

    #[test]
    fn an_integral_float_keeps_its_decimal_point() {
        let value = Json::Object(vec![("fps".to_string(), Json::Float(30.0))]);
        assert_eq!(value.to_python_json(), "{\n  \"fps\": 30.0\n}");
    }

    #[test]
    fn a_fractional_float_prints_shortest() {
        let value = Json::Object(vec![("fps".to_string(), Json::Float(29.97))]);
        assert_eq!(value.to_python_json(), "{\n  \"fps\": 29.97\n}");
    }

    #[test]
    fn non_ascii_is_escaped_like_python() {
        let value = Json::str("Café");
        assert_eq!(value.to_python_json(), "\"Caf\\u00e9\"");
    }

    #[test]
    fn nested_structures_indent_by_two() {
        let value = Json::Object(vec![(
            "source".to_string(),
            Json::Object(vec![(
                "tags".to_string(),
                Json::Array(vec![Json::str("a"), Json::str("b")]),
            )]),
        )]);
        assert_eq!(
            value.to_python_json(),
            "{\n  \"source\": {\n    \"tags\": [\n      \"a\",\n      \"b\"\n    ]\n  }\n}"
        );
    }

    #[test]
    fn control_characters_use_pythons_short_escapes() {
        let value = Json::str("a\nb\tc");
        assert_eq!(value.to_python_json(), "\"a\\nb\\tc\"");
    }
}
