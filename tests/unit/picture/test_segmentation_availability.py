"""SAM 2 is an optional model: it can be unable to be resident.

Measured 2026-10-22 (PROD-006): on the captain's 24 GiB box the SAM 2
weights need 10.15 GB of physical footprint at their peak, and the
machine refused to host them while it was already paging. Every clip in
the batch needs the SAME weights, so the batch is refused once, with the
reason - not once per clip, and not as a stack trace.
"""
from library.tools import resource_scheduler


def _video(tmp_path):
    path = tmp_path / "clip.mov"
    path.write_bytes(b"not really a video")
    return str(path)


def test_a_model_that_cannot_be_resident_refuses_the_batch_once(
        tmp_path, monkeypatch, capsys):
    """The refusal names the reason and reaches the reader: a step that
    silently produced no mattes reads as a step that measured nothing
    wrong."""
    from library.capabilities.objects import segment as seg

    class _Segmenter:
        run_active = False

    def _refuse():
        raise resource_scheduler.MemoryUnavailable(
            "resource scheduler: 'SAM 2 model memory' requests 11 GB RAM, "
            "but this machine is already paging: 12.2 GB of 13.0 GB of "
            "swap in use (94%)")

    monkeypatch.setattr(seg, "get_segmenter", lambda: _Segmenter())
    monkeypatch.setattr(seg, "_sam_model_memory_session", _refuse)

    result = seg.run_step(
        raw_footage_files=[],
        clip_catalog=[{"clip_id": "clip-1", "path": _video(tmp_path)}],
        output_dir=str(tmp_path / "out"),
        color_grade_spec={"subject_grades": [{"clip_id": "clip-1"}]},
        face_boxes_by_clip={"clip-1": [0.1, 0.1, 0.5, 0.5]})

    assert result["object_segmentation"] == []
    refusal = result["matte_trigger"]["memory_refusal"]
    assert "already paging" in refusal
    assert capsys.readouterr().err.count("Refusing the SAM 2 batch") == 1
