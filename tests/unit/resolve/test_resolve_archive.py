from __future__ import annotations

import zipfile

import pytest

from library.tools.resolve_archive import ArchiveError, read_archive, semantic_diff


def _archive(path, *, version="17", duration="100", item_name="Shot A"):
    project = f'''<!--DbAppVer="21.1.0.0014" DbPrjVer="{version}"-->
    <Project>
      <Name>Demo</Name><TimelineHandleVec><TimelineHandle>timeline-0001</TimelineHandle></TimelineHandleVec>
      <ListMgt::LmVersion>opaque</ListMgt::LmVersion>
    </Project>'''.encode()
    # Simulate Resolve's invalid control bytes inside an opaque byte-array value.
    sequence = f'''<Sequence><Track><TrackType>Video</TrackType><UserDefinedName>V1</UserDefinedName>
      <VideoClip><Name>{item_name}</Name><Start>0</Start><Duration>{duration}</Duration><In>12</In>
      <MediaRef>media-1</MediaRef><MediaFilePath>/media/a.mov</MediaFilePath><FieldsBlob>ab\x01cd</FieldsBlob>
      </VideoClip></Track><Track><TrackType>Audio</TrackType></Track></Sequence>'''.encode()
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("project.xml", project)
        archive.writestr("SeqContainer/timeline-0001.xml", sequence)


def test_reads_project_timeline_tracks_and_items_with_control_bytes(tmp_path):
    path = tmp_path / "synthetic.drp"
    _archive(path)

    facts = read_archive(path)

    assert facts["project"] == "Demo"
    assert facts["db_prj_ver"] == "17"
    assert facts["timeline_ids"] == ["timeline-0001"]
    video, audio = facts["timelines"][0]["tracks"]
    assert (video["order"], video["type"], video["name"]) == (0, "Video", "V1")
    item = video["items"][0]
    assert {key: item[key] for key in (
        "kind", "name", "start", "duration", "source_in", "media_ref", "media_path"
    )} == {
        "kind": "VideoClip", "name": "Shot A", "start": "0", "duration": "100",
        "source_in": "12", "media_ref": "media-1", "media_path": "/media/a.mov",
    }
    assert "FieldsBlob" in video["items"][0]["opaque_byte_arrays"]
    assert audio["items"] == []


def test_diff_reports_changed_item_values_and_identity(tmp_path):
    before, after = tmp_path / "a.drp", tmp_path / "b.drp"
    _archive(before)
    _archive(after, duration="101", item_name="Shot B")

    result = semantic_diff(read_archive(before), read_archive(after))

    assert result["changed"] is True
    assert result["changes"] == [
        {"kind": "item", "timeline_id": "timeline-0001", "track_order": 0,
         "order": 0, "field": "name", "before": "Shot A", "after": "Shot B"},
        {"kind": "item", "timeline_id": "timeline-0001", "track_order": 0,
         "order": 0, "field": "duration", "before": "100", "after": "101"},
    ]


def test_unknown_project_schema_is_refused(tmp_path):
    path = tmp_path / "unknown.drt"
    _archive(path, version="999")

    with pytest.raises(ArchiveError, match="unsupported DbPrjVer '999'.*Refusing to guess"):
        read_archive(path)
