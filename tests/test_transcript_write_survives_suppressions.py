"""A suppression in the correction report must not lose the transcript.

2026-09-25, geo-podcast: a full re-transcription of the master (two
speakers, 167 spans) finished and then raised `KeyError: 'replacements'`
while PRINTING its correction summary, because `applied` mixes respelling
entries (which carry `replacements`) with suppression entries (which carry
`suppressed`). The document was never returned, so it was never written,
and the whole transcription was lost.
"""

from types import SimpleNamespace

from library.tools import timeline_transcript as tt


def test_a_suppression_entry_does_not_crash_the_summary(tmp_path, monkeypatch):
    clip = SimpleNamespace(speaker="Akshita")
    snapshot = SimpleNamespace(picture_clips=lambda: [clip])
    monkeypatch.setattr(tt, "build_speaker_audio", lambda *a, **k: None)
    monkeypatch.setattr(tt, "transcribe_audio", lambda *a, **k: ({}, {}))
    monkeypatch.setattr(tt, "segments_for_speaker", lambda *a, **k: [])
    monkeypatch.setattr(tt, "transcript_document",
                        lambda *a, **k: {"segments": []})

    from library.tools import transcript_corrections

    monkeypatch.setattr(
        transcript_corrections, "apply_to_document",
        lambda document, folder: {
            "replacements": 1, "segments_touched": 1, "suppressed": 1,
            "applied": [
                {"id": "lc-1", "heard": "x", "correct": "y",
                 "replacements": 1},
                {"id": "lc-2", "heard": "um", "suppressed": 1},
            ]})
    document = tt.build_and_transcribe(str(tmp_path), snapshot)
    assert document == {"segments": []}
