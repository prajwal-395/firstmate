"""Read-only semantic inventory and diff for Resolve .drp and .drt exports.

The export XML is private and versioned. This reader supports the observed
DbPrjVer 17 representation only; opaque byte arrays are fingerprinted, never
interpreted. It does not open Resolve or modify an archive.

Run with ``python3 -m library.tools.resolve_archive inspect FILE`` or
``python3 -m library.tools.resolve_archive diff BEFORE AFTER``.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import zipfile
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

SUPPORTED_DB_PRJ_VERSIONS = {"17"}
_INVALID_XML_CONTROLS = re.compile(rb"[\x00-\x08\x0b\x0c\x0e-\x1f]")
_XML_TAG = re.compile(rb"(<\/?)([A-Za-z_][A-Za-z0-9_.:-]*)")
_VERSION_COMMENT = re.compile(
    rb"<!--\s*DbAppVer=\"([^\"]+)\"\s+DbPrjVer=\"([^\"]+)\"\s*-->"
)
_ITEM_TAGS = {"VideoClip", "AudioClip", "Still", "FusionClip", "Generator", "Gap"}
_BYTE_ARRAY_SUFFIXES = ("BA", "Blob")


class ArchiveError(ValueError):
    """An archive could not be safely read as a supported Resolve export."""


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _text(node: ET.Element, *names: str) -> str | None:
    wanted = set(names)
    for child in node.iter():
        if _local(child.tag) in wanted:
            value = (child.text or "").strip()
            if value:
                return value
    for name in names:
        value = node.attrib.get(name)
        if value:
            return value
    return None


def _attribute(node: ET.Element, name: str) -> str | None:
    """Resolve metadata attributes whether placed on wrapper or project node."""
    for child in node.iter():
        value = child.attrib.get(name)
        if value:
            return value
    return None


def _parse_xml(data: bytes, member: str) -> ET.Element:
    # Resolve puts binary/control bytes inside otherwise useful XML values.
    # Strip XML-forbidden controls so the structural fields remain readable;
    # byte-array values are separately fingerprinted from the raw member.
    cleaned = _INVALID_XML_CONTROLS.sub(b"", data)
    # Resolve also emits C++-qualified names such as ``ListMgt::LmVersion``.
    # XML permits one namespace separator, not ``::``; normalize only tag
    # names so ElementTree can retain the surrounding document structure.
    cleaned = _XML_TAG.sub(
        lambda match: match.group(1) + match.group(2).replace(b"::", b"_"),
        cleaned,
    )
    try:
        return ET.fromstring(cleaned)
    except ET.ParseError as exc:
        raise ArchiveError(f"cannot parse Resolve XML member {member!r}: {exc}") from exc


def _byte_array_hashes(root: ET.Element) -> dict[str, str]:
    hashes: dict[str, str] = {}
    for node in root.iter():
        name = _local(node.tag)
        if name.endswith(_BYTE_ARRAY_SUFFIXES):
            raw = (node.text or "").encode("utf-8", errors="replace")
            hashes[name] = hashlib.sha256(raw).hexdigest()
    return hashes


def _tracks(root: ET.Element) -> list[dict[str, Any]]:
    # Resolve serializes tracks as typed vectors of Element/Sm2TiTrack, with
    # clips nested below each track's Items vector. Keep XML order across the
    # vectors; older/synthetic representations may use literal Track nodes.
    records: list[tuple[ET.Element, str | None]] = []
    for vector in root.iter():
        vector_name = _local(vector.tag)
        if vector_name.endswith("TrackVec"):
            vector_type = vector_name[:-len("TrackVec")]
            for entry in vector:
                candidate = entry[0] if _local(entry.tag) == "Element" and len(entry) else entry
                if _local(candidate.tag) == "Sm2TiTrack":
                    records.append((candidate, vector_type or None))
    if not records:
        records = [(node, None) for node in root.iter() if _local(node.tag) == "Track"]

    result = []
    for track, vector_type in records:
        kind = _text(track, "TrackType", "Type", "SubType") or vector_type
        items = []
        item_vector = next((node for node in track if _local(node.tag) == "Items"), None)
        candidates = []
        if item_vector is not None:
            for entry in item_vector:
                candidates.append(entry[0] if _local(entry.tag) == "Element" and len(entry) else entry)
        else:
            candidates = [node for node in track.iter() if _local(node.tag) in _ITEM_TAGS]
        for child in candidates:
            tag = _local(child.tag)
            item_kind = tag.removeprefix("Sm2Ti")
            if item_kind not in _ITEM_TAGS:
                continue
            items.append({
                "kind": item_kind,
                "name": _text(child, "Name"),
                "start": _text(child, "Start"),
                "duration": _text(child, "Duration"),
                "source_in": _text(child, "In"),
                "media_ref": _text(child, "MediaRef"),
                "media_path": _text(child, "MediaFilePath"),
                "opaque_byte_arrays": _byte_array_hashes(child),
            })
        result.append({"order": len(result), "type": kind,
                       "name": _text(track, "UserDefinedName", "Name"), "items": items})
    return result


def read_archive(path: str | Path) -> dict[str, Any]:
    """Return stable, JSON-ready facts from a .drp/.drt, without writing."""
    archive_path = Path(path)
    suffix = archive_path.suffix.lower()
    if suffix not in {".drp", ".drt"}:
        raise ArchiveError(f"expected a .drp or .drt file, got {archive_path.name!r}")
    try:
        with zipfile.ZipFile(archive_path) as archive:
            names = archive.namelist()
            project_name = next((n for n in names if n.lower() == "project.xml"), None)
            if project_name is None:
                raise ArchiveError("archive has no project.xml member")
            project_root = _parse_xml(archive.read(project_name), project_name)
            app_ver = _attribute(project_root, "DbAppVer")
            project_ver = _attribute(project_root, "DbPrjVer")
            version_comment = _VERSION_COMMENT.search(archive.read(project_name))
            if version_comment:
                app_ver = app_ver or version_comment.group(1).decode("ascii")
                project_ver = project_ver or version_comment.group(2).decode("ascii")
            if project_ver not in SUPPORTED_DB_PRJ_VERSIONS:
                raise ArchiveError(
                    f"unsupported DbPrjVer {project_ver!r}; supported values: "
                    f"{', '.join(sorted(SUPPORTED_DB_PRJ_VERSIONS))}. Refusing to guess."
                )
            timeline_ids = []
            for node in project_root.iter():
                if _local(node.tag) in {"TimelineHandle", "TimelineHandleVec", "SeqContainer"}:
                    candidates = [node.attrib.get(key) for key in ("id", "Id", "UUID", "ref")]
                    candidates += [(node.text or "").strip()]
                    for value in candidates:
                        if value and value not in timeline_ids and re.search(r"[0-9a-fA-F-]{8,}", value):
                            timeline_ids.append(value)
            sequences = []
            for member in sorted(n for n in names if re.fullmatch(r"SeqContainer/[^/]+\.xml", n)):
                root = _parse_xml(archive.read(member), member)
                timeline_id = Path(member).stem
                sequences.append({"id": timeline_id, "tracks": _tracks(root)})
                if timeline_id not in timeline_ids:
                    timeline_ids.append(timeline_id)
            return {
                "format": suffix[1:],
                "project": _text(project_root, "Name", "ProjectName"),
                "project_id": (_attribute(project_root, "DbId") or
                               _text(project_root, "ProjectId", "DbId")),
                "db_app_ver": app_ver,
                "db_prj_ver": project_ver,
                "timeline_ids": timeline_ids,
                "timelines": sequences,
                "members": len(names),
            }
    except zipfile.BadZipFile as exc:
        raise ArchiveError(f"{archive_path} is not a valid ZIP archive") from exc


def semantic_diff(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    """Describe changed archive identity, timeline, track and item facts."""
    changes: list[dict[str, Any]] = []
    for key in ("format", "project", "project_id", "db_app_ver", "db_prj_ver"):
        if before[key] != after[key]:
            changes.append({"kind": "project", "field": key, "before": before[key], "after": after[key]})
    old, new = {t["id"]: t for t in before["timelines"]}, {t["id"]: t for t in after["timelines"]}
    for timeline_id in sorted(old.keys() | new.keys()):
        if timeline_id not in old or timeline_id not in new:
            changes.append({"kind": "timeline", "id": timeline_id,
                            "change": "added" if timeline_id in new else "removed"})
            continue
        a_tracks, b_tracks = old[timeline_id]["tracks"], new[timeline_id]["tracks"]
        for index in range(max(len(a_tracks), len(b_tracks))):
            if index >= len(a_tracks) or index >= len(b_tracks):
                changes.append({"kind": "track", "timeline_id": timeline_id,
                                "order": index,
                                "change": "added" if index >= len(a_tracks) else "removed"})
                continue
            a_track, b_track = a_tracks[index], b_tracks[index]
            for field in ("type", "name"):
                if a_track[field] != b_track[field]:
                    changes.append({"kind": "track", "timeline_id": timeline_id,
                                    "order": index, "field": field,
                                    "before": a_track[field], "after": b_track[field]})
            a_items, b_items = a_track["items"], b_track["items"]
            for item_index in range(max(len(a_items), len(b_items))):
                if item_index >= len(a_items) or item_index >= len(b_items):
                    changes.append({"kind": "item", "timeline_id": timeline_id,
                                    "track_order": index, "order": item_index,
                                    "change": "added" if item_index >= len(a_items) else "removed"})
                    continue
                for field in ("kind", "name", "start", "duration", "source_in",
                              "media_ref", "media_path", "opaque_byte_arrays"):
                    if a_items[item_index][field] != b_items[item_index][field]:
                        changes.append({"kind": "item", "timeline_id": timeline_id,
                                        "track_order": index, "order": item_index,
                                        "field": field, "before": a_items[item_index][field],
                                        "after": b_items[item_index][field]})
    return {"changed": bool(changes), "changes": changes}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="resolve_archive", description=__doc__)
    modes = parser.add_subparsers(dest="mode", required=True)
    inspect = modes.add_parser("inspect", help="read one .drp or .drt")
    inspect.add_argument("archive")
    diff = modes.add_parser("diff", help="compare two .drp/.drt exports")
    diff.add_argument("before")
    diff.add_argument("after")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        result = (read_archive(args.archive) if args.mode == "inspect" else
                  semantic_diff(read_archive(args.before), read_archive(args.after)))
    except (ArchiveError, OSError) as exc:
        print(f"resolve_archive: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
