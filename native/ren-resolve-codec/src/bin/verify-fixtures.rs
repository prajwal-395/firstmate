//! Manually verify the externally held sample exports against the committed
//! hash and aggregate-count manifest. This is not a test and is never run as
//! part of a test suite; it reads only paths the operator supplies.

use std::{env, fs, path::Path};

use ren_resolve_codec::inspect;
use sha2::{Digest, Sha256};

fn main() {
    if let Err(error) = run() {
        eprintln!("fixture verification failed: {error}");
        std::process::exit(2);
    }
}

fn run() -> Result<(), Box<dyn std::error::Error>> {
    let args: Vec<_> = env::args_os().collect();
    if args.len() != 4 {
        return Err("usage: verify-fixtures <manifest.json> <project.drp> <timeline.drt>".into());
    }
    let manifest: serde_json::Value = serde_json::from_slice(&fs::read(&args[1])?)?;
    let fixtures = manifest["fixtures"]
        .as_array()
        .ok_or("manifest has no fixtures array")?;
    for (arg, expected_format) in [(&args[2], "drp"), (&args[3], "drt")] {
        let expected_extension = format!(".{expected_format}");
        let path = Path::new(arg);
        let bytes = fs::read(path)?;
        let digest = hex(&Sha256::digest(&bytes));
        let record = fixtures
            .iter()
            .find(|entry| entry["artifact_type"].as_str() == Some(expected_extension.as_str()))
            .ok_or("manifest has no matching artifact type")?;
        if record["sha256"].as_str() != Some(&digest) {
            return Err(format!("{} SHA-256 differs from manifest", path.display()).into());
        }
        if record["bytes"].as_u64() != Some(bytes.len() as u64) {
            return Err(format!("{} byte length differs from manifest", path.display()).into());
        }
        let observed = inspect(&bytes, expected_format)?;
        let track_count: usize = observed.timelines.iter().map(|t| t.tracks.len()).sum();
        let item_count: usize = observed
            .timelines
            .iter()
            .flat_map(|t| &t.tracks)
            .map(|t| t.items.len())
            .sum();
        for (key, actual) in [
            ("sequence_containers", observed.timelines.len()),
            ("tracks", track_count),
            ("items", item_count),
        ] {
            if record["counts"][key].as_u64() != Some(actual as u64) {
                return Err(format!("{} {key} count differs from manifest", path.display()).into());
            }
        }
        if record["db_app_ver"].as_str() != observed.db_app_ver.as_deref()
            || record["db_prj_ver"].as_str() != Some(observed.db_prj_ver.as_str())
        {
            return Err(
                format!("{} version identity differs from manifest", path.display()).into(),
            );
        }
        println!(
            "PASS {} sha256={} sequences={} tracks={} items={}",
            path.display(),
            digest,
            observed.timelines.len(),
            track_count,
            item_count
        );
    }
    Ok(())
}

fn hex(bytes: &[u8]) -> String {
    bytes.iter().map(|byte| format!("{byte:02x}")).collect()
}
