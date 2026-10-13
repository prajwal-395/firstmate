//! Read-only inspection and byte-preserving round-trip for `.drp` and
//! `.drt` Resolve exports. The original archive is retained as the source of
//! truth; this crate does not rewrite XML or claim semantic mutation support.

use std::io::{Cursor, Read};

use sha2::{Digest, Sha256};
use zip::ZipArchive;

pub mod blob;
pub mod version;

const MAX_MEMBERS: usize = 20_000;
const MAX_ARCHIVE_SIZE: usize = 1024 * 1024 * 1024;
const MAX_XML_SPANS: usize = 2_000_000;
const MAX_MEMBER_SIZE: u64 = 128 * 1024 * 1024;
const MAX_TOTAL_SIZE: u64 = 1024 * 1024 * 1024;

#[derive(Debug)]
pub enum CodecError {
    UnsupportedFormat(String),
    InvalidZip(String),
    UnsafeMember(String),
    LimitExceeded(&'static str),
    MissingProjectXml,
    UnsupportedProjectVersion(Option<String>),
    UnsupportedAppVersion(Option<String>),
    InvalidXml { member: String, detail: String },
    AmbiguousStructure(String),
    Io(std::io::Error),
}

impl std::fmt::Display for CodecError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::UnsupportedFormat(s) => write!(f, "unsupported Resolve archive format: {s}"),
            Self::InvalidZip(s) => write!(f, "invalid ZIP archive: {s}"),
            Self::UnsafeMember(s) => write!(f, "unsafe ZIP member path: {s}"),
            Self::LimitExceeded(s) => write!(f, "archive safety limit exceeded: {s}"),
            Self::MissingProjectXml => f.write_str("archive has no project.xml member"),
            Self::UnsupportedProjectVersion(v) => write!(
                f,
                "unsupported DbPrjVer {v:?}; supported values: {}. Refusing to guess",
                version::SUPPORTED_DB_PROJECT_VERSIONS.join(", ")
            ),
            Self::UnsupportedAppVersion(v) => write!(
                f,
                "unsupported DbAppVer {v:?}; supported values: {}. Refusing to guess",
                version::SUPPORTED_DB_APP_VERSIONS.join(", ")
            ),
            Self::InvalidXml { member, detail } => {
                write!(f, "cannot inspect XML member {member:?}: {detail}")
            }
            Self::AmbiguousStructure(s) => write!(f, "ambiguous Resolve archive structure: {s}"),
            Self::Io(e) => e.fmt(f),
        }
    }
}

impl std::error::Error for CodecError {}

impl From<std::io::Error> for CodecError {
    fn from(value: std::io::Error) -> Self {
        Self::Io(value)
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct MemberInfo {
    pub name: String,
    pub compressed_size: u64,
    pub uncompressed_size: u64,
    pub crc32: u32,
    pub compression: String,
    pub last_modified: String,
    pub unix_mode: Option<u32>,
    pub sha256: String,
    pub is_directory: bool,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ItemObservation {
    pub kind: String,
    pub name: Option<String>,
    pub start: Option<String>,
    pub duration: Option<String>,
    pub source_in: Option<String>,
    pub media_ref: Option<String>,
    pub media_path: Option<String>,
    pub source_span: SourceSpan,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct TrackObservation {
    pub order: usize,
    pub kind: Option<String>,
    pub name: Option<String>,
    pub items: Vec<ItemObservation>,
    pub source_span: SourceSpan,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct SourceSpan {
    pub member: String,
    pub start: usize,
    pub end: usize,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct OpaqueBlobObservation {
    pub member: String,
    pub element: String,
    pub content_len: usize,
    pub sha256: String,
    pub source_span: SourceSpan,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct XmlElementSpan {
    pub element: String,
    pub source: SourceSpan,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct TimelineObservation {
    pub id: String,
    pub tracks: Vec<TrackObservation>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ArchiveInspection {
    pub format: String,
    pub project_name: Option<String>,
    pub project_id: Option<String>,
    pub db_app_ver: Option<String>,
    pub db_prj_ver: String,
    pub timeline_ids: Vec<String>,
    pub timelines: Vec<TimelineObservation>,
    pub opaque_blobs: Vec<OpaqueBlobObservation>,
    pub xml_spans: Vec<XmlElementSpan>,
    pub members: Vec<MemberInfo>,
}

/// A retained archive can only be written back verbatim. There is deliberately
/// no API that constructs an archive from the inspection model.
#[derive(Debug, Clone)]
pub struct PreservedArchive {
    original: Vec<u8>,
    pub inspection: ArchiveInspection,
}

impl PreservedArchive {
    pub fn original_bytes(&self) -> &[u8] {
        &self.original
    }

    /// Returns the exact original archive bytes, including ZIP metadata,
    /// compression streams, member order, comments and unknown entries.
    pub fn write_unchanged(&self) -> Vec<u8> {
        self.original.clone()
    }
}

pub fn inspect(bytes: &[u8], extension: &str) -> Result<ArchiveInspection, CodecError> {
    if bytes.len() > MAX_ARCHIVE_SIZE {
        return Err(CodecError::LimitExceeded("archive compressed size"));
    }
    let extension = extension.trim_start_matches('.').to_ascii_lowercase();
    if extension != "drp" && extension != "drt" {
        return Err(CodecError::UnsupportedFormat(extension));
    }
    let mut archive =
        ZipArchive::new(Cursor::new(bytes)).map_err(|e| CodecError::InvalidZip(e.to_string()))?;
    if archive.len() > MAX_MEMBERS {
        return Err(CodecError::LimitExceeded("member count"));
    }
    // The reader may silently collapse duplicate directory entries, so the
    // raw central directory is walked independently: a duplicate name or an
    // entry count the reader does not reproduce is archive ambiguity and
    // refuses instead of letting one entry shadow another.
    {
        let raw_names = central_directory_names(bytes)?;
        if raw_names.len() != archive.len() {
            return Err(CodecError::AmbiguousStructure(format!(
                "central directory holds {} entries but the reader inventories {}",
                raw_names.len(),
                archive.len()
            )));
        }
        let mut seen_raw = std::collections::HashSet::new();
        for name in &raw_names {
            if !seen_raw.insert(name) {
                return Err(CodecError::AmbiguousStructure(format!(
                    "duplicate member {:?}",
                    String::from_utf8_lossy(name)
                )));
            }
        }
    }

    let mut members = Vec::with_capacity(archive.len());
    let mut project_bytes = None;
    let mut sequence_documents = Vec::new();
    let mut opaque_blobs = Vec::new();
    let mut xml_spans = Vec::new();
    let mut seen = std::collections::HashSet::new();
    let mut total = 0u64;
    for index in 0..archive.len() {
        let mut file = archive
            .by_index(index)
            .map_err(|e| CodecError::InvalidZip(e.to_string()))?;
        let name = file.name().to_owned();
        validate_member_name(&name)?;
        if !seen.insert(name.clone()) {
            return Err(CodecError::AmbiguousStructure(format!(
                "duplicate member {name:?}"
            )));
        }
        let size = file.size();
        if size > MAX_MEMBER_SIZE {
            return Err(CodecError::LimitExceeded("member uncompressed size"));
        }
        total = total
            .checked_add(size)
            .ok_or(CodecError::LimitExceeded("total uncompressed size"))?;
        if total > MAX_TOTAL_SIZE {
            return Err(CodecError::LimitExceeded("total uncompressed size"));
        }
        let mut content = Vec::with_capacity(size as usize);
        file.by_ref()
            .take(MAX_MEMBER_SIZE + 1)
            .read_to_end(&mut content)
            .map_err(CodecError::Io)?;
        if content.len() as u64 > MAX_MEMBER_SIZE {
            return Err(CodecError::LimitExceeded("actual member decompressed size"));
        }
        if content.len() as u64 != size {
            return Err(CodecError::InvalidZip(format!("truncated member {name:?}")));
        }
        members.push(MemberInfo {
            name: name.clone(),
            compressed_size: file.compressed_size(),
            uncompressed_size: size,
            crc32: file.crc32(),
            compression: format!("{:?}", file.compression()),
            last_modified: format!("{:?}", file.last_modified()),
            unix_mode: file.unix_mode(),
            sha256: hex(&Sha256::digest(&content)),
            is_directory: file.is_dir(),
        });
        if name.eq_ignore_ascii_case("project.xml") {
            if project_bytes.replace(content).is_some() {
                return Err(CodecError::AmbiguousStructure(
                    "multiple project.xml members".into(),
                ));
            }
        } else if is_sequence_member(&name) {
            sequence_documents.push((name, content));
        }
    }
    let project_bytes = project_bytes.ok_or(CodecError::MissingProjectXml)?;
    let (project_text, project_offsets) =
        normalize_xml_for_observation(&project_bytes, "project.xml")?;
    let project_doc = parse_xml(&project_text, "project.xml")?;
    let project_root = project_doc.root_element();
    let app_ver = attribute_recursive(project_root, "DbAppVer");
    let project_ver = attribute_recursive(project_root, "DbPrjVer")
        .or_else(|| version_comment(&project_bytes).map(|(_, p)| p));
    let app_ver = app_ver.or_else(|| version_comment(&project_bytes).map(|(a, _)| a));
    if !app_ver
        .as_deref()
        .is_some_and(|v| version::SUPPORTED_DB_APP_VERSIONS.contains(&v))
    {
        return Err(CodecError::UnsupportedAppVersion(app_ver));
    }
    let Some(project_ver_value) = project_ver else {
        return Err(CodecError::UnsupportedProjectVersion(None));
    };
    if !version::SUPPORTED_DB_PROJECT_VERSIONS.contains(&project_ver_value.as_str()) {
        return Err(CodecError::UnsupportedProjectVersion(Some(
            project_ver_value,
        )));
    }
    let project_name = first_text(project_root, &["Name", "ProjectName"]);
    let project_id = attribute_recursive(project_root, "DbId")
        .or_else(|| first_text(project_root, &["ProjectId", "DbId"]));

    sequence_documents.sort_by(|a, b| a.0.cmp(&b.0));
    let mut timeline_ids = Vec::new();
    let mut timelines = Vec::new();
    for (member, xml) in sequence_documents {
        let id = member
            .trim_start_matches("SeqContainer/")
            .trim_end_matches(".xml")
            .to_owned();
        if timeline_ids.contains(&id) {
            return Err(CodecError::AmbiguousStructure(format!(
                "duplicate timeline ID {id}"
            )));
        }
        timeline_ids.push(id.clone());
        let (text, offsets) = normalize_xml_for_observation(&xml, &member)?;
        let doc = parse_xml(&text, &member)?;
        xml_spans.extend(collect_xml_spans(doc.root_element(), &member, &offsets)?);
        opaque_blobs.extend(blob_observations(
            doc.root_element(),
            &member,
            &xml,
            &offsets,
        ));
        timelines.push(TimelineObservation {
            id,
            tracks: tracks(doc.root_element(), &member, &offsets),
        });
    }
    for node in project_root.descendants() {
        if ["TimelineHandle", "TimelineHandleVec", "SeqContainer"].contains(&node.tag_name().name())
        {
            for key in ["id", "Id", "UUID", "ref"] {
                if let Some(value) = node.attribute(key).filter(|v| looks_like_id(v))
                    && !timeline_ids.iter().any(|id| id == value)
                {
                    timeline_ids.push(value.to_owned());
                }
            }
            if let Some(value) = node.text().map(str::trim).filter(|v| looks_like_id(v))
                && !timeline_ids.iter().any(|id| id == value)
            {
                timeline_ids.push(value.to_owned());
            }
        }
    }
    opaque_blobs.extend(blob_observations(
        project_root,
        "project.xml",
        &project_bytes,
        &project_offsets,
    ));
    xml_spans.extend(collect_xml_spans(
        project_root,
        "project.xml",
        &project_offsets,
    )?);
    Ok(ArchiveInspection {
        format: extension,
        project_name,
        project_id,
        db_app_ver: app_ver,
        db_prj_ver: project_ver_value,
        timeline_ids,
        timelines,
        opaque_blobs,
        xml_spans,
        members,
    })
}

pub fn read_preserved(bytes: &[u8], extension: &str) -> Result<PreservedArchive, CodecError> {
    let inspection = inspect(bytes, extension)?;
    Ok(PreservedArchive {
        original: bytes.to_vec(),
        inspection,
    })
}

/// Raw central-directory member names, walked without decompressing anything.
/// Compared against the reader inventory so a collapsed duplicate cannot
/// shadow another entry. Returns `InvalidZip` when the directory cannot be
/// located and walked exactly; every index is bounds-checked.
fn central_directory_names(bytes: &[u8]) -> Result<Vec<Vec<u8>>, CodecError> {
    fn u16le(bytes: &[u8], at: usize) -> Result<u16, CodecError> {
        bytes
            .get(at..at + 2)
            .and_then(|w| <[u8; 2]>::try_from(w).ok())
            .map(u16::from_le_bytes)
            .ok_or_else(|| CodecError::InvalidZip("truncated ZIP directory".into()))
    }
    fn u32le(bytes: &[u8], at: usize) -> Result<u32, CodecError> {
        bytes
            .get(at..at + 4)
            .and_then(|w| <[u8; 4]>::try_from(w).ok())
            .map(u32::from_le_bytes)
            .ok_or_else(|| CodecError::InvalidZip("truncated ZIP directory".into()))
    }
    fn u64le(bytes: &[u8], at: usize) -> Result<u64, CodecError> {
        bytes
            .get(at..at + 8)
            .and_then(|w| <[u8; 8]>::try_from(w).ok())
            .map(u64::from_le_bytes)
            .ok_or_else(|| CodecError::InvalidZip("truncated ZIP directory".into()))
    }
    // The end-of-central-directory record is the last structure in the file;
    // candidates are tried from the end so a comment carrying the same
    // signature does not win over the real record.
    let search_from = bytes.len().saturating_sub(65_535 + 22);
    let mut eocd = None;
    let mut pos = search_from;
    while pos + 22 <= bytes.len() {
        if bytes[pos..pos + 4] == [0x50, 0x4b, 0x05, 0x06] {
            let comment_len = u16le(bytes, pos + 20)? as usize;
            if pos + 22 + comment_len == bytes.len() {
                eocd = Some(pos);
                break;
            }
        }
        pos += 1;
    }
    let eocd =
        eocd.ok_or_else(|| CodecError::InvalidZip("missing end of central directory".into()))?;
    if u16le(bytes, eocd + 4)? != 0 || u16le(bytes, eocd + 6)? != 0 {
        return Err(CodecError::InvalidZip(
            "multi-disk archives are unsupported".into(),
        ));
    }
    let mut total = u16le(bytes, eocd + 10)? as u64;
    let mut cd_offset = u32le(bytes, eocd + 16)? as u64;
    if u16le(bytes, eocd + 8)? == 0xffff || total == 0xffff || cd_offset == 0xffff_ffff {
        let locator = eocd
            .checked_sub(20)
            .ok_or_else(|| CodecError::InvalidZip("truncated ZIP64 locator".into()))?;
        if bytes.get(locator..locator + 4) != Some(&[0x50, 0x4b, 0x06, 0x07][..]) {
            return Err(CodecError::InvalidZip(
                "missing ZIP64 directory locator".into(),
            ));
        }
        let zip64_eocd = u64le(bytes, locator + 8)? as usize;
        if bytes.get(zip64_eocd..zip64_eocd + 4) != Some(&[0x50, 0x4b, 0x06, 0x06][..]) {
            return Err(CodecError::InvalidZip(
                "missing ZIP64 directory record".into(),
            ));
        }
        total = u64le(bytes, zip64_eocd + 32)?;
        cd_offset = u64le(bytes, zip64_eocd + 48)?;
    }
    if total > MAX_MEMBERS as u64 {
        return Err(CodecError::LimitExceeded("member count"));
    }
    let mut cursor = cd_offset as usize;
    let mut names = Vec::with_capacity(total as usize);
    for _ in 0..total {
        if bytes.get(cursor..cursor + 4) != Some(&[0x50, 0x4b, 0x01, 0x02][..]) {
            return Err(CodecError::InvalidZip(
                "damaged central directory entry".into(),
            ));
        }
        let name_len = u16le(bytes, cursor + 28)? as usize;
        let extra_len = u16le(bytes, cursor + 30)? as usize;
        let comment_len = u16le(bytes, cursor + 32)? as usize;
        let name_start = cursor
            .checked_add(46)
            .ok_or_else(|| CodecError::InvalidZip("truncated ZIP directory".into()))?;
        let name_end = name_start
            .checked_add(name_len)
            .ok_or_else(|| CodecError::InvalidZip("truncated ZIP directory".into()))?;
        let next = name_end
            .checked_add(extra_len)
            .and_then(|v| v.checked_add(comment_len))
            .ok_or_else(|| CodecError::InvalidZip("truncated ZIP directory".into()))?;
        if name_end > bytes.len() || next > bytes.len() || next < cursor {
            return Err(CodecError::InvalidZip("truncated ZIP directory".into()));
        }
        names.push(bytes[name_start..name_end].to_vec());
        cursor = next;
    }
    Ok(names)
}

fn validate_member_name(name: &str) -> Result<(), CodecError> {
    if name.is_empty()
        || name.starts_with('/')
        || name.contains('\\')
        || name.contains('\0')
        || name.split('/').any(|part| part == "..")
        || name
            .split('/')
            .next()
            .is_some_and(|part| part.contains(':'))
    {
        return Err(CodecError::UnsafeMember(name.to_owned()));
    }
    Ok(())
}

fn is_sequence_member(name: &str) -> bool {
    name.starts_with("SeqContainer/")
        && name.ends_with(".xml")
        && name.matches('/').count() == 1
        && name.len() > "SeqContainer/.xml".len()
}

fn parse_xml<'a>(text: &'a str, member: &str) -> Result<roxmltree::Document<'a>, CodecError> {
    roxmltree::Document::parse(text).map_err(|e| CodecError::InvalidXml {
        member: member.into(),
        detail: e.to_string(),
    })
}

/// Apply the same narrow repairs as the read-only Python inventory while
/// retaining a byte-offset map back to the untouched source member.
fn normalize_xml_for_observation(
    bytes: &[u8],
    member: &str,
) -> Result<(String, Vec<usize>), CodecError> {
    let mut output = Vec::with_capacity(bytes.len());
    let mut offsets = Vec::with_capacity(bytes.len() + 1);
    let mut i = 0;
    while i < bytes.len() {
        let byte = bytes[i];
        if (0x00..=0x08).contains(&byte)
            || byte == 0x0b
            || byte == 0x0c
            || (0x0e..=0x1f).contains(&byte)
        {
            i += 1;
            continue;
        }
        if byte == b'<' {
            let mut name_start = i + 1;
            if bytes.get(name_start) == Some(&b'/') {
                name_start += 1;
            }
            if bytes
                .get(name_start)
                .is_some_and(|b| b.is_ascii_alphabetic() || *b == b'_')
            {
                let mut end = name_start;
                while bytes.get(end).is_some_and(|b| {
                    b.is_ascii_alphanumeric() || matches!(*b, b'_' | b'.' | b':' | b'-')
                }) {
                    end += 1;
                }
                output.push(byte);
                offsets.push(i);
                if name_start > i + 1 {
                    output.push(b'/');
                    offsets.push(i + 1);
                }
                let mut j = name_start;
                while j < end {
                    if bytes.get(j..j + 2) == Some(b"::") {
                        output.push(b'_');
                        offsets.push(j);
                        j += 2;
                    } else {
                        output.push(bytes[j]);
                        offsets.push(j);
                        j += 1;
                    }
                }
                i = end;
                continue;
            }
        }
        output.push(byte);
        offsets.push(i);
        i += 1;
    }
    offsets.push(bytes.len());
    let text = String::from_utf8(output).map_err(|e| CodecError::InvalidXml {
        member: member.into(),
        detail: e.to_string(),
    })?;
    Ok((text, offsets))
}

fn original_range(range: std::ops::Range<usize>, offsets: &[usize]) -> std::ops::Range<usize> {
    offsets[range.start]..offsets[range.end]
}

fn local<'a, 'input>(node: roxmltree::Node<'a, 'input>) -> &'input str {
    node.tag_name()
        .name()
        .rsplit('}')
        .next()
        .unwrap_or(node.tag_name().name())
}

fn first_text(node: roxmltree::Node<'_, '_>, names: &[&str]) -> Option<String> {
    node.descendants()
        .find(|n| n.is_element() && names.contains(&local(*n)))
        .and_then(|n| n.text())
        .map(str::trim)
        .filter(|v| !v.is_empty())
        .map(str::to_owned)
        .or_else(|| {
            names
                .iter()
                .find_map(|n| node.attribute(*n).map(str::to_owned))
        })
}

fn attribute_recursive(node: roxmltree::Node<'_, '_>, name: &str) -> Option<String> {
    node.descendants()
        .filter(|n| n.is_element())
        .find_map(|n| n.attribute(name).map(str::to_owned))
}

fn version_comment(xml: &[u8]) -> Option<(String, String)> {
    let s = std::str::from_utf8(xml).ok()?;
    let start = s.find("<!--")?;
    let end = s[start..].find("-->")? + start;
    let comment = &s[start..end];
    Some((
        quoted_attr(comment, "DbAppVer")?,
        quoted_attr(comment, "DbPrjVer")?,
    ))
}

fn quoted_attr(s: &str, key: &str) -> Option<String> {
    let rest = s.get(s.find(key)? + key.len()..)?.trim_start();
    let rest = rest.strip_prefix('=')?.trim_start();
    let quote = rest.chars().next()?;
    if quote != '"' && quote != '\'' {
        return None;
    }
    Some(rest[1..].split(quote).next()?.to_owned())
}

fn tracks(root: roxmltree::Node<'_, '_>, member: &str, offsets: &[usize]) -> Vec<TrackObservation> {
    let mut track_nodes = Vec::new();
    for vector in root
        .descendants()
        .filter(|n| n.is_element() && local(*n).ends_with("TrackVec"))
    {
        for entry in vector.children().filter(|n| n.is_element()) {
            let candidate = if local(entry) == "Element" {
                entry.children().find(|n| n.is_element()).unwrap_or(entry)
            } else {
                entry
            };
            if local(candidate) == "Sm2TiTrack" {
                track_nodes.push((
                    candidate,
                    Some(local(vector).trim_end_matches("TrackVec").to_owned()),
                ));
            }
        }
    }
    if track_nodes.is_empty() {
        track_nodes = root
            .descendants()
            .filter(|n| n.is_element() && local(*n) == "Track")
            .map(|n| (n, None))
            .collect();
    }
    track_nodes
        .into_iter()
        .enumerate()
        .map(|(order, (track, vector_type))| {
            let kind = first_text(track, &["TrackType", "Type", "SubType"]).or(vector_type);
            let item_vec = track
                .children()
                .find(|n| n.is_element() && local(*n) == "Items");
            let candidates: Vec<_> = if let Some(items) = item_vec {
                items
                    .children()
                    .filter(|n| n.is_element())
                    .map(|entry| {
                        if local(entry) == "Element" {
                            entry.children().find(|n| n.is_element()).unwrap_or(entry)
                        } else {
                            entry
                        }
                    })
                    .collect()
            } else {
                track
                    .descendants()
                    .filter(|n| n.is_element() && is_item_tag(local(*n)))
                    .collect()
            };
            let items = candidates
                .into_iter()
                .filter_map(|item| {
                    let tag = local(item);
                    if !is_item_tag(tag) {
                        return None;
                    }
                    Some(ItemObservation {
                        kind: tag.strip_prefix("Sm2Ti").unwrap_or(tag).to_owned(),
                        name: first_text(item, &["Name"]),
                        start: first_text(item, &["Start"]),
                        duration: first_text(item, &["Duration"]),
                        source_in: first_text(item, &["In"]),
                        media_ref: first_text(item, &["MediaRef"]),
                        media_path: first_text(item, &["MediaFilePath"]),
                        source_span: span(member, item, offsets),
                    })
                })
                .collect();
            TrackObservation {
                order,
                kind,
                name: first_text(track, &["UserDefinedName", "Name"]),
                items,
                source_span: span(member, track, offsets),
            }
        })
        .collect()
}

fn span(member: &str, node: roxmltree::Node<'_, '_>, offsets: &[usize]) -> SourceSpan {
    let range = original_range(node.range(), offsets);
    SourceSpan {
        member: member.to_owned(),
        start: range.start,
        end: range.end,
    }
}

fn blob_observations(
    root: roxmltree::Node<'_, '_>,
    member: &str,
    source: &[u8],
    offsets: &[usize],
) -> Vec<OpaqueBlobObservation> {
    root.descendants()
        .filter(|n| {
            n.is_element() && {
                let name = local(*n);
                name.ends_with("BA") || name.ends_with("Blob")
            }
        })
        .filter_map(|node| {
            let text_node = node.children().find(|child| child.is_text())?;
            let content_range = original_range(text_node.range(), offsets);
            let raw_content = source.get(content_range.clone())?;
            Some(OpaqueBlobObservation {
                member: member.to_owned(),
                element: local(node).to_owned(),
                content_len: raw_content.len(),
                sha256: hex(&Sha256::digest(raw_content)),
                source_span: span(member, node, offsets),
            })
        })
        .collect()
}

fn collect_xml_spans(
    root: roxmltree::Node<'_, '_>,
    member: &str,
    offsets: &[usize],
) -> Result<Vec<XmlElementSpan>, CodecError> {
    let mut spans = Vec::new();
    for node in root.descendants().filter(|node| node.is_element()) {
        let element = local(node);
        let observed = matches!(
            element,
            "Project"
                | "Sequence"
                | "Name"
                | "ProjectName"
                | "ProjectId"
                | "DbId"
                | "DbAppVer"
                | "DbPrjVer"
                | "TimelineHandle"
                | "TimelineHandleVec"
                | "SeqContainer"
                | "Start"
                | "Duration"
                | "In"
                | "MediaRef"
                | "MediaFilePath"
                | "UserDefinedName"
                | "TrackType"
                | "Type"
                | "SubType"
                | "Items"
                | "Track"
                | "Sm2TiTrack"
                | "VideoClip"
                | "AudioClip"
                | "Still"
                | "FusionClip"
                | "Generator"
                | "Gap"
                | "Sm2TiVideoClip"
                | "Sm2TiAudioClip"
                | "Sm2TiStill"
                | "Sm2TiFusionClip"
                | "Sm2TiGenerator"
                | "Sm2TiGap"
        ) || element.ends_with("BA")
            || element.ends_with("Blob");
        if observed {
            spans.push(XmlElementSpan {
                element: element.to_owned(),
                source: span(member, node, offsets),
            });
            if spans.len() > MAX_XML_SPANS {
                return Err(CodecError::LimitExceeded("XML source span count"));
            }
        }
    }
    Ok(spans)
}

fn is_item_tag(name: &str) -> bool {
    [
        "VideoClip",
        "AudioClip",
        "Still",
        "FusionClip",
        "Generator",
        "Gap",
    ]
    .iter()
    .any(|kind| name == *kind || name == format!("Sm2Ti{kind}"))
}

fn looks_like_id(value: &str) -> bool {
    value.len() >= 8 && value.chars().all(|c| c.is_ascii_hexdigit() || c == '-')
}
fn hex(bytes: &[u8]) -> String {
    bytes.iter().map(|b| format!("{b:02x}")).collect()
}
