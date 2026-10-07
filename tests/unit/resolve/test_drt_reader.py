"""Offline parsing of the plain XML surface of a DRT archive."""

from zipfile import ZipFile

import pytest

from library.tools.drt_reader import read_drt_archive


def _write_archive(path, xml):
    with ZipFile(path, "w") as archive:
        archive.writestr("SeqContainer/sequence-1.xml", xml)


def test_reader_extracts_ranges_transforms_markers_and_caption_text(tmp_path):
    path = tmp_path / "qualification.drt"
    _write_archive(path, """<?xml version="1.0" encoding="UTF-8"?>
<Sequence>
  <VideoTrackVec>
    <Element><ListMgt::Sm2TiTrack DbId="track-v1">
      <Name>V1</Name><Items>
        <Element><ListMgt::Sm2TiVideoClip DbId="clip-1">
          <Name>shot.mov</Name><Start>100</Start><Duration>48</Duration>
          <In>12</In><Out>60</Out><Pan>-160.5</Pan><Tilt>80</Tilt>
          <ZoomX>1.25</ZoomX><ZoomY>1.5</ZoomY>
          <Markers><Element><ListMgt::Sm2TiMarker>
            <Name>locator</Name><Comment>subject enters</Comment>
            <Color>Blue</Color><Frame>104</Frame><Duration>1</Duration>
          </ListMgt::Sm2TiMarker></Element></Markers>
        </ListMgt::Sm2TiVideoClip></Element>
      </Items>
    </ListMgt::Sm2TiTrack></Element>
    <Element><ListMgt::Sm2TiTrack DbId="track-subtitles">
      <Name>Subtitles</Name><Items>
        <Element><ListMgt::Sm2TiVideoClip DbId="caption-1">
            <Name>Hello::there</Name><Start>102</Start><Duration>24</Duration>
          <In>0</In><Out>24</Out>
        </ListMgt::Sm2TiVideoClip></Element>
      </Items>
    </ListMgt::Sm2TiTrack></Element>
  </VideoTrackVec>
</Sequence>""")

    result = read_drt_archive(path)

    assert result["item_count"] == 2
    assert result["caption_count"] == 1
    assert result["marker_count"] == 1
    clips = [item for track in result["timelines"][0]["tracks"]
             for item in track["items"]]
    video = next(item for item in clips if item["uid"] == "clip-1")
    assert video["record_range_frames"] == {"start": 100, "end": 148}
    assert video["source_range_frames"] == {"start": 12, "end": 60}
    assert video["transform"] == {
        "Pan": -160.5, "Tilt": 80.0, "ZoomX": 1.25, "ZoomY": 1.5}
    assert video["markers"] == [{
        "name": "locator", "comment": "subject enters", "color": "Blue",
        "frame": 104, "duration_frames": 1, "custom_data": None,
    }]
    caption = next(item for item in clips if item["uid"] == "caption-1")
    assert caption["caption"] == {"text": "Hello::there"}
    assert result["missing_field_counts"] == {}


def test_reader_preserves_omitted_values_as_missing_instead_of_guessing(
        tmp_path):
    path = tmp_path / "sparse.drt"
    _write_archive(path, """<Sequence><VideoTrackVec><Element>
<ListMgt::Sm2TiTrack><Name>V1</Name><Items><Element>
<ListMgt::Sm2TiVideoClip><Name>clip.mov</Name><Start>10</Start>
<Duration>20</Duration><In>4</In></ListMgt::Sm2TiVideoClip>
</Element></Items></ListMgt::Sm2TiTrack></Element></VideoTrackVec></Sequence>""")

    result = read_drt_archive(path)
    item = result["timelines"][0]["tracks"][0]["items"][0]

    assert item["record_range_frames"] == {"start": 10, "end": 30}
    assert item["source_range_frames"] == {"start": 4, "end": None}
    assert item["transform"] == {
        "Pan": None, "Tilt": None, "ZoomX": None, "ZoomY": None}
    assert set(item["missing_fields"]) == {
        "source_end_frame", "Pan", "Tilt", "ZoomX", "ZoomY"}
    assert result["missing_field_counts"] == {
        "Pan": 1, "Tilt": 1, "ZoomX": 1, "ZoomY": 1,
        "source_end_frame": 1,
    }


@pytest.mark.parametrize("filename, payload, message", [
    ("wrong.zip", b"not a zip", "expected a .drt archive"),
    ("empty.drt", None, "no SeqContainer XML"),
])
def test_reader_refuses_wrong_or_unrecognized_archive(tmp_path, filename,
                                                      payload, message):
    path = tmp_path / filename
    if payload is None:
        with ZipFile(path, "w") as archive:
            archive.writestr("project.xml", "<Project/>")
    else:
        path.write_bytes(payload)

    with pytest.raises(ValueError, match=message):
        read_drt_archive(path)


def test_reader_names_malformed_sequence_xml(tmp_path):
    path = tmp_path / "broken.drt"
    _write_archive(path, "<broken>")

    with pytest.raises(ValueError, match="sequence-1.xml"):
        read_drt_archive(path)
