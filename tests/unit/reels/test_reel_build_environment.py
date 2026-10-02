"""What a reel build needs in its interpreter is DECLARED, and checked first.

`library/tools/shared_environment.py` (the BUILD half) is the one owner;
`env.face_detector` and `env.reel_build_libraries` refuse BEFORE the build
starts, naming what is missing and what would supply it. The cv2 in each
case is a fake in `sys.modules`. History: docs/evidence/reel_build.md.
"""

import os
import sys
import types
from pathlib import Path

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..")))

from library.tools import requirements as R  # noqa: E402
from library.tools import shared_environment as se  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[3]


# ── fakes: every machine shape the row actually met ──────────────────

def _no_cv2(monkeypatch):
    """No cv2 at all: `import cv2` raises, as on a stock interpreter."""
    monkeypatch.setitem(sys.modules, "cv2", None)


def _cv2_without_classifier(monkeypatch, version="5.0.0"):
    """cv2 5.x: present, but Haar cascades are gone entirely."""
    fake = types.ModuleType("cv2")
    fake.__version__ = version
    monkeypatch.setitem(sys.modules, "cv2", fake)
    return fake


def _cv2_with_classifier(monkeypatch, tmp_path, *, loads=True,
                         version="4.14.0"):
    """A cv2 whose cascade file exists, loading or not on demand."""
    cascade_dir = tmp_path / "haarcascades"
    cascade_dir.mkdir(parents=True, exist_ok=True)
    (cascade_dir / se.FACE_CASCADE_FILENAME).write_text(
        "<opencv_storage/>", encoding="utf-8")

    class FakeCascade:
        def __init__(self, path):
            self.path = path

        def empty(self):
            return not loads

    fake = types.ModuleType("cv2")
    fake.__version__ = version
    fake.CascadeClassifier = FakeCascade
    fake.data = types.SimpleNamespace(haarcascades=str(cascade_dir))
    monkeypatch.setitem(sys.modules, "cv2", fake)
    return fake


def _requirement(name):
    return next(r for r in R.registry() if r.name == name)


# ── gap 1: the cv2 Haar cascades ─────────────────────────────────────

def test_an_absent_declaration_fails_the_pre_build_check_by_name(monkeypatch):
    """Both surviving gaps: absent detector, and a missing library.

    A declared requirement that is absent fails the pre-build check BY
    NAME - `missing` carries the requirement, not a downstream symptom.
    """
    _no_cv2(monkeypatch)

    assert se.face_detector_usable() is False
    with pytest.raises(se.FaceDetectorMissing) as raised:
        se.require_face_detector()
    assert se.FACE_DETECTOR_PIN in str(raised.value)

    requirement = _requirement("env.face_detector")
    verdict = requirement.check(R.Context())
    assert verdict.is_unsatisfied
    assert verdict.missing == "env.face_detector"

    # The second gap: a venv built for one thing, missing another (the
    # row's instance was `jsonschema` absent from a cv2 4.12 venv).
    monkeypatch.setattr(se, "REEL_BUILD_LIBRARIES", ("no_such_module_xyz",))
    assert se.missing_build_libraries() == ("no_such_module_xyz",)
    assert se.build_libraries_present() is False
    verdict = _requirement("env.reel_build_libraries").check(R.Context())
    assert verdict.is_unsatisfied
    assert verdict.missing == "env.reel_build_libraries"
    assert "no_such_module_xyz" in verdict.reason
    assert se.REQUIREMENTS_FILE in verdict.reason


def test_each_detector_shape_names_its_own_missing_half(
        monkeypatch, tmp_path):
    """"No face detector" is four different facts with four remedies.

    The message must say WHICH half is gone - no cv2, no classifier,
    no cascade directory, an unloadable file - because "install
    opencv" is the local fix that produced four different ad-hoc
    venvs.
    """
    _no_cv2(monkeypatch)
    assert "not installed" in se.face_detector_missing_message()

    _cv2_without_classifier(monkeypatch)
    message = se.face_detector_missing_message()
    assert "5.0.0" in message and "CascadeClassifier" in message

    _cv2_with_classifier(monkeypatch, tmp_path, loads=False)
    assert "fails to load" in se.face_detector_missing_message()

    for message in (
            se.face_detector_missing_message(),
            se.reel_build_environment_missing_message()):
        assert se.FACE_DETECTOR_PIN in message
        assert se.BUILD_VENV_DOC in message
