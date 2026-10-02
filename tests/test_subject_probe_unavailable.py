"""A probe with no detector raises; it never answers None.

None means "frames were read and no face was measured" (genuine
absence: the shot stays uncropped). A detector that could not be loaded
raises `SubjectProbeUnavailable` before decoding, so the build refuses
with the cause. Incident (Reel 09): `docs/evidence/subject_probe_unavailable.md`.
"""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from library.tools.subject_framing import measure_subject_in_window


def test_a_probe_with_no_detector_raises_rather_than_answering_none(
        monkeypatch):
    """Incapacity is not absence - the Reel 09 identical-rectangle cause.

    Before: this returned None when the cascade would not load, which
    reads as "no face in this shot".  Now it raises, before decoding.
    """
    import library.tools.subject_framing as framing

    monkeypatch.setattr(framing, "load_face_cascade", lambda: None)
    with pytest.raises(Exception, match="face detector"):
        measure_subject_in_window("/x/LC4932.MXF", 1421.571, 1426.656)


def test_genuine_absence_is_still_none_not_unavailable(monkeypatch, tmp_path):
    """A zero-length window has nothing to measure, and a detector that
    looked at undecodable footage found nothing: both are absence."""
    import library.tools.subject_framing as framing

    assert measure_subject_in_window(
        str(tmp_path / "absent.MXF"), 0.0, 5.0, cascade=object()) is None
    monkeypatch.setattr(framing, "load_face_cascade", lambda: None)
    assert measure_subject_in_window("/x/LC4932.MXF", 5.0, 5.0) is None

