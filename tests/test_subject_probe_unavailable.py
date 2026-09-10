"""A probe with no detector raises; it never answers None.

Reel 09, 2026-09-09: a full `build-reels --only-reel 9` failed the reel
gate on 6x F12, every item delivering the IDENTICAL rect
(0, 656, 1080, 1264) - the identity transform's fitted strip.  The aim
never ran: system python's cv2 5.0.0 ships no `CascadeClassifier`, so
`measure_subject_in_window` answered None for every shot without
decoding a frame, `punch_in_properties` refused every shot, and the
placer left all six items unpunched.  The gate named the symptom; the
cause was an incapacitated probe the build never mentioned.

None from this function means "frames were read and no face was
measured" - genuine absence, which leaves the shot uncropped by the
captain's refuse-rather-than-guess ruling.  A detector that could not
even be loaded is a different fact and raises `SubjectProbeUnavailable`
naming the interpreter's cv2, before any frame is decoded, so the
caller refuses the build with the cause instead of shipping staging
the gate deletes.  The same line `render_qa.measure_face_intact`
draws with its "No Haar cascade available" warning: a measurement
that could not be taken must say so.
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


def test_an_empty_window_is_still_none_not_unavailable(monkeypatch):
    """A zero-length window has nothing to measure - that is absence."""
    import library.tools.subject_framing as framing

    monkeypatch.setattr(framing, "load_face_cascade", lambda: None)
    assert measure_subject_in_window("/x/LC4932.MXF", 5.0, 5.0) is None


def test_undecodable_footage_is_still_none(tmp_path):
    """A detector that looked and found nothing is absence too.

    The file does not exist, so ffmpeg decodes nothing and no face is
    measured - None, exactly as before.  The raise is only for a probe
    that could not look at all.
    """
    missing = str(tmp_path / "absent.MXF")
    assert measure_subject_in_window(
        missing, 0.0, 5.0, cascade=object()) is None
