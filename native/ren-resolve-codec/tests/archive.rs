use std::io::{Cursor, Write};

use ren_resolve_codec::{CodecError, inspect, read_preserved};
use zip::{ZipWriter, write::SimpleFileOptions};

fn archive(project: &str, extra: &[(&str, &[u8])]) -> Vec<u8> {
    let mut out = Cursor::new(Vec::new());
    {
        let mut zip = ZipWriter::new(&mut out);
        zip.start_file("project.xml", SimpleFileOptions::default())
            .unwrap();
        zip.write_all(project.as_bytes()).unwrap();
        for (name, body) in extra {
            zip.start_file(*name, SimpleFileOptions::default()).unwrap();
            zip.write_all(body).unwrap();
        }
        zip.finish().unwrap();
    }
    out.into_inner()
}

const PROJECT: &str = r#"<Project DbAppVer="21.1.0.0014" DbPrjVer="17" DbId="p1"><Name>Test</Name><TimelineHandleVec><TimelineHandle id="01234567-89ab-cdef-0123-456789abcdef"/></TimelineHandleVec></Project>"#;

#[test]
fn inspection_reports_member_hashes_and_source_spans() {
    let sequence = br#"<Sequence><VideoTrackVec><Element><Sm2TiTrack><UserDefinedName>V1</UserDefinedName><Items><Element><Sm2TiVideoClip><Name>shot</Name><Start>0</Start><Duration>24</Duration><In>12</In><MediaRef>m1</MediaRef><MediaFilePath>/media/shot.mov</MediaFilePath><EffectFiltersBA>opaque</EffectFiltersBA></Sm2TiVideoClip></Element></Items></Sm2TiTrack></Element></VideoTrackVec></Sequence>"#;
    let bytes = archive(
        PROJECT,
        &[
            (
                "SeqContainer/01234567-89ab-cdef-0123-456789abcdef.xml",
                sequence,
            ),
            ("unknown.bin", b"opaque"),
        ],
    );
    let result = inspect(&bytes, ".drp").unwrap();
    assert_eq!(result.project_name.as_deref(), Some("Test"));
    assert_eq!(result.db_app_ver.as_deref(), Some("21.1.0.0014"));
    assert_eq!(result.timelines.len(), 1);
    assert_eq!(result.timelines[0].tracks.len(), 1);
    let item = &result.timelines[0].tracks[0].items[0];
    assert_eq!(item.name.as_deref(), Some("shot"));
    assert!(item.source_span.start < item.source_span.end);
    assert_eq!(
        &sequence[item.source_span.start..item.source_span.end],
        b"<Sm2TiVideoClip><Name>shot</Name><Start>0</Start><Duration>24</Duration><In>12</In><MediaRef>m1</MediaRef><MediaFilePath>/media/shot.mov</MediaFilePath><EffectFiltersBA>opaque</EffectFiltersBA></Sm2TiVideoClip>"
    );
    assert_eq!(result.opaque_blobs[0].content_len, 6);
    let item_xml_span = result
        .xml_spans
        .iter()
        .find(|span| span.element == "Sm2TiVideoClip")
        .unwrap();
    assert_eq!(
        &sequence[item_xml_span.source.start..item_xml_span.source.end],
        &sequence[item.source_span.start..item.source_span.end]
    );
    assert!(
        result
            .members
            .iter()
            .any(|member| member.name == "unknown.bin")
    );
}

#[test]
fn unchanged_round_trip_is_byte_exact_including_zip_metadata() {
    let bytes = archive(PROJECT, &[("unknown.bin", b"keep me")]);
    let retained = read_preserved(&bytes, "drt").unwrap();
    assert_eq!(retained.write_unchanged(), bytes);
    assert_eq!(retained.original_bytes(), bytes);
}

#[test]
fn malformed_and_unsupported_inputs_return_typed_errors() {
    assert!(matches!(
        inspect(b"not a zip", "drp"),
        Err(CodecError::InvalidZip(_))
    ));
    assert!(
        matches!(inspect(&archive("<Project DbAppVer=\"21.1.0.0014\" DbPrjVer=\"99\"/>", &[]), "drp"),
        Err(CodecError::UnsupportedProjectVersion(Some(v))) if v == "99")
    );
    assert!(matches!(
        inspect(&archive("<Project DbAppVer=\"99.0\" DbPrjVer=\"17\"/>", &[]), "drp"),
        Err(CodecError::UnsupportedAppVersion(Some(v))) if v == "99.0"
    ));
    assert!(matches!(
        inspect(
            &archive("<Project DbPrjVer=\"17\"><broken></Project>", &[]),
            "drp"
        ),
        Err(CodecError::InvalidXml { .. })
    ));
    assert!(
        inspect(
            &archive(
                "<Project DbAppVer=\"21.1.0.0014\" DbPrjVer=\"17\">\u{1}</Project>",
                &[]
            ),
            "drp"
        )
        .is_ok()
    );
    assert!(matches!(
        inspect(&archive(PROJECT, &[]), "drx"),
        Err(CodecError::UnsupportedFormat(_))
    ));
}

#[test]
fn qualified_names_are_normalized_for_observation_only() {
    let project = "<Project DbAppVer=\"21.1.0.0014\" DbPrjVer=\"17\"><ListMgt::LmVersion>2</ListMgt::LmVersion></Project>";
    assert!(inspect(&archive(project, &[]), "drp").is_ok());
}

#[test]
fn traversal_paths_are_rejected() {
    let bytes = archive(PROJECT, &[("../outside", b"bad")]);
    assert!(matches!(
        inspect(&bytes, "drp"),
        Err(CodecError::UnsafeMember(_))
    ));
}

#[test]
fn duplicate_paths_and_windows_absolute_paths_are_rejected() {
    let mut duplicate = archive(PROJECT, &[("unknown.bin", b"a"), ("another.bin", b"b")]);
    let mut replaced = 0;
    let mut search_from = 0;
    while let Some(found) = duplicate[search_from..]
        .windows(b"another.bin".len())
        .position(|window| window == b"another.bin")
    {
        let start = search_from + found;
        duplicate[start..start + b"another.bin".len()].copy_from_slice(b"unknown.bin");
        search_from = start + b"unknown.bin".len();
        replaced += 1;
    }
    assert_eq!(
        replaced, 2,
        "local and central directory names both patched"
    );
    assert!(matches!(
        inspect(&duplicate, "drp"),
        Err(CodecError::AmbiguousStructure(_))
    ));
    let windows_absolute = archive(PROJECT, &[("C:/outside", b"bad")]);
    assert!(matches!(
        inspect(&windows_absolute, "drp"),
        Err(CodecError::UnsafeMember(_))
    ));
}

#[test]
fn invalid_utf8_xml_is_a_typed_error() {
    let bytes = archive(
        PROJECT,
        &[("SeqContainer/bad.xml", b"<Sequence>\xff</Sequence>")],
    );
    assert!(matches!(
        inspect(&bytes, "drp"),
        Err(CodecError::InvalidXml { .. })
    ));
}

#[test]
fn declared_oversized_members_are_refused_before_decompression() {
    let mut bytes = archive(PROJECT, &[("oversized.bin", b"x")]);
    let mut offset = 0;
    let mut patched = false;
    while offset + 46 <= bytes.len() {
        if bytes[offset..offset + 4] == [0x50, 0x4b, 0x01, 0x02] {
            let name_len = u16::from_le_bytes([bytes[offset + 28], bytes[offset + 29]]) as usize;
            let extra_len = u16::from_le_bytes([bytes[offset + 30], bytes[offset + 31]]) as usize;
            let comment_len = u16::from_le_bytes([bytes[offset + 32], bytes[offset + 33]]) as usize;
            let name = &bytes[offset + 46..offset + 46 + name_len];
            if name == b"oversized.bin" {
                let size = (128 * 1024 * 1024 + 1u32).to_le_bytes();
                bytes[offset + 24..offset + 28].copy_from_slice(&size);
                patched = true;
                break;
            }
            offset += 46 + name_len + extra_len + comment_len;
        } else {
            offset += 1;
        }
    }
    assert!(patched);
    assert!(matches!(
        inspect(&bytes, "drp"),
        Err(CodecError::LimitExceeded("member uncompressed size"))
    ));
}

#[test]
fn deterministic_malformed_zip_corpus_never_panics() {
    for len in 0..256 {
        let bytes: Vec<u8> = (0..len)
            .map(|n| ((n * 73 + len * 19) & 0xff) as u8)
            .collect();
        let result = std::panic::catch_unwind(|| inspect(&bytes, "drp"));
        assert!(
            result.is_ok(),
            "panic for deterministic malformed input length {len}"
        );
    }
}
