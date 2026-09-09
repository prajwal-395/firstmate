"""The catalog knows every audio stream, and the program mix is a decision.

Defect covered here:
  3. a non-program audio stream leaked onto the timeline because nothing
     stopped it at import - the catalog took the FIRST audio stream it
     found and discarded the rest, so there was never a choice to make.

Measured on the captain's real MXF (LC4930.MXF, 2026-09-09): four audio
streams, all pcm_s24le mono, no channel layout, no language, no title,
no disposition to tell them apart. A silent default to stream 0 is
exactly the failure being fixed, so `select_program_stream` refuses on
indistinguishable metadata rather than defaulting.
"""

import pytest

from library.steps.step_1_02_catalog_footage.step import (
    ProgramStreamRefused,
    describe_audio_streams,
    select_program_stream,
)


def _mxf_like_probe():
    """ffprobe output shaped like the captain's MXF: one video stream,
    four IDENTICAL mono audio streams, one data stream."""
    streams = [{
        "index": 0, "codec_name": "h264", "codec_type": "video",
        "width": 3840, "height": 2160, "r_frame_rate": "24000/1001",
        "pix_fmt": "yuv420p", "tags": {},
    }]
    for i in (1, 2, 3, 4):
        streams.append({
            "index": i, "codec_name": "pcm_s24le", "codec_type": "audio",
            "sample_rate": "48000", "channels": 1,
            "bits_per_sample": 24, "tags": {},
            "disposition": {"default": 0, "dub": 0, "original": 0,
                            "comment": 0, "lyrics": 0, "karaoke": 0,
                            "forced": 0, "hearing_impaired": 0,
                            "visual_impaired": 0, "clean_effects": 0,
                            "attached_pic": 0, "timed_thumbnails": 0},
        })
    streams.append({"index": 5, "codec_name": "smpte_436m_anc",
                    "codec_type": "data"})
    return {"streams": streams, "format": {"duration": "10.0", "tags": {}}}


def test_every_audio_stream_is_recorded_with_what_tells_them_apart():
    streams = describe_audio_streams(_mxf_like_probe())
    assert len(streams) == 4
    first = streams[0]
    assert first["index"] == 1
    assert first["channel"] == 1
    assert first["codec"] == "pcm_s24le"
    assert first["channels"] == 1
    assert first["sample_rate"] == 48000
    for key in ("channel_layout", "language", "title", "handler"):
        assert key in first
    assert [s["channel"] for s in streams] == [1, 2, 3, 4]


def test_indistinguishable_streams_refuse_rather_than_default():
    """Defect 3 at the catalog: no declaration, no metadata difference,
    no stream 0 default - a refusal naming the source and what was seen."""
    streams = describe_audio_streams(_mxf_like_probe())
    with pytest.raises(ProgramStreamRefused) as exc:
        select_program_stream(streams, declaration=None, source="LC4930.MXF")
    message = str(exc.value)
    assert "LC4930.MXF" in message
    assert "4 audio streams" in message


def test_a_declaration_selects_the_program_stream():
    """The project declares (CH1 here, matching the captain's own edit),
    and the engine obeys it instead of choosing."""
    streams = describe_audio_streams(_mxf_like_probe())
    chosen = select_program_stream(streams, declaration=1, source="LC4930.MXF")
    assert chosen["channel"] == 1
    assert chosen["index"] == 1
    assert chosen["basis"] == "declared"


def test_a_declaration_naming_no_stream_is_refused():
    streams = describe_audio_streams(_mxf_like_probe())
    with pytest.raises(ProgramStreamRefused):
        select_program_stream(streams, declaration=9, source="LC4930.MXF")


def test_a_single_stream_needs_no_declaration():
    probe = {"streams": [
        {"index": 0, "codec_name": "h264", "codec_type": "video",
         "width": 1920, "height": 1080, "r_frame_rate": "30/1",
         "pix_fmt": "yuv420p"},
        {"index": 1, "codec_name": "aac", "codec_type": "audio",
         "sample_rate": "44100", "channels": 2,
         "channel_layout": "stereo"},
    ], "format": {"duration": "5.0"}}
    streams = describe_audio_streams(probe)
    assert len(streams) == 1
    chosen = select_program_stream(streams, declaration=None, source="phone.MOV")
    assert chosen["channel"] == 1
    assert chosen["basis"] == "single"


def test_distinguishable_streams_are_still_refused_without_a_declaration():
    """Even when metadata could tell streams apart, the engine does not
    invent the choice: which stream is the program mix is a project
    declaration, never a heuristic."""
    probe = {"streams": [
        {"index": 0, "codec_name": "h264", "codec_type": "video",
         "width": 1920, "height": 1080, "r_frame_rate": "30/1",
         "pix_fmt": "yuv420p"},
        {"index": 1, "codec_name": "aac", "codec_type": "audio",
         "sample_rate": "48000", "channels": 2,
         "channel_layout": "stereo",
         "tags": {"language": "eng", "title": "Program Mix"}},
        {"index": 2, "codec_name": "aac", "codec_type": "audio",
         "sample_rate": "48000", "channels": 8,
         "channel_layout": "7.1",
         "tags": {"language": "eng", "title": "ISO feeds"}},
    ], "format": {"duration": "5.0"}}
    streams = describe_audio_streams(probe)
    with pytest.raises(ProgramStreamRefused):
        select_program_stream(streams, declaration=None, source="cam.MXF")
    chosen = select_program_stream(streams, declaration=1, source="cam.MXF")
    assert chosen["title"] == "Program Mix"
