//! The id newtypes: one kind of id cannot be passed where another belongs.
//!
//! `Generation`, `TimelineId` and `SnapshotHash` are the captain's named
//! newtypes (captain-plan.md); `ClipId` is the edit algebra's item
//! identity. Each is a distinct type so a `Generation` cannot reach a
//! parameter that wants a `TimelineId`.

use schemars::JsonSchema;
use serde::{Deserialize, Deserializer, Serialize, Serializer};

/// One recorded generation of a timeline. `0` is never valid: a patch is
/// planned against a recorded generation (`edit_patch.validate`).
#[derive(Clone, Copy, Debug, PartialEq, Eq, PartialOrd, Ord, Hash)]
pub struct Generation(pub u64);

/// The SHA-256 of a snapshot's canonical JSON. The wire form is the
/// lowercase hex string `timeline_shadow.snapshot_hash` produces; the
/// type is the 32 bytes, so a hash cannot be confused with any other
/// string field.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash)]
pub struct SnapshotHash(pub [u8; 32]);

impl SnapshotHash {
    /// Parse the hex wire form. Accepts exactly 64 hex digits, the
    /// shape `timeline_shadow.snapshot_hash` writes.
    pub fn from_hex(text: &str) -> Result<Self, String> {
        let bytes = hex_decode(text)
            .ok_or_else(|| format!("a snapshot hash is 64 hex digits, got {text:?}"))?;
        let mut out = [0u8; 32];
        out.copy_from_slice(&bytes);
        Ok(SnapshotHash(out))
    }

    /// The hex wire form.
    pub fn to_hex(&self) -> String {
        hex_encode(&self.0)
    }
}

impl Serialize for SnapshotHash {
    fn serialize<S: Serializer>(&self, serializer: S) -> Result<S::Ok, S::Error> {
        serializer.serialize_str(&self.to_hex())
    }
}

impl<'de> Deserialize<'de> for SnapshotHash {
    fn deserialize<D: Deserializer<'de>>(deserializer: D) -> Result<Self, D::Error> {
        let text = String::deserialize(deserializer)?;
        SnapshotHash::from_hex(&text).map_err(serde::de::Error::custom)
    }
}

impl JsonSchema for SnapshotHash {
    fn schema_name() -> std::borrow::Cow<'static, str> {
        "SnapshotHash".into()
    }

    fn json_schema(generator: &mut schemars::SchemaGenerator) -> schemars::Schema {
        String::json_schema(generator)
    }

    fn inline_schema() -> bool {
        false
    }
}

pub(crate) fn hex_encode(bytes: &[u8]) -> String {
    const DIGITS: &[u8; 16] = b"0123456789abcdef";
    let mut out = String::with_capacity(bytes.len() * 2);
    for byte in bytes {
        out.push(DIGITS[(byte >> 4) as usize] as char);
        out.push(DIGITS[(byte & 0x0f) as usize] as char);
    }
    out
}

pub(crate) fn hex_decode(text: &str) -> Option<Vec<u8>> {
    if text.len() != 64 || !text.bytes().all(|b| b.is_ascii_hexdigit()) {
        return None;
    }
    let bytes = text.as_bytes();
    let mut out = Vec::with_capacity(32);
    for pair in bytes.chunks(2) {
        let hi = (pair[0] as char).to_digit(16)?;
        let lo = (pair[1] as char).to_digit(16)?;
        out.push((hi * 16 + lo) as u8);
    }
    Some(out)
}

macro_rules! string_newtype {
    ($name:ident, $doc:expr) => {
        #[doc = $doc]
        #[derive(Clone, Debug, PartialEq, Eq, Hash, Serialize, Deserialize, JsonSchema)]
        pub struct $name(pub String);
    };
}

string_newtype!(
    ClipId,
    "A Resolve clip's unique id (the edit algebra's `unique_id`)."
);
string_newtype!(
    TimelineId,
    "The shadow store's timeline identity, not its display name."
);

impl Serialize for Generation {
    fn serialize<S: Serializer>(&self, serializer: S) -> Result<S::Ok, S::Error> {
        serializer.serialize_u64(self.0)
    }
}

impl<'de> Deserialize<'de> for Generation {
    fn deserialize<D: Deserializer<'de>>(deserializer: D) -> Result<Self, D::Error> {
        Ok(Generation(u64::deserialize(deserializer)?))
    }
}

impl JsonSchema for Generation {
    fn schema_name() -> std::borrow::Cow<'static, str> {
        "Generation".into()
    }

    fn json_schema(generator: &mut schemars::SchemaGenerator) -> schemars::Schema {
        u64::json_schema(generator)
    }

    fn inline_schema() -> bool {
        false
    }
}
