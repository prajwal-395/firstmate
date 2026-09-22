"""What a reel build needs in its interpreter is DECLARED, and checked first.

Four instances, all on 2026-09-10/11, each costing a lane a failed build:
a cv2 without Haar cascades answering None from the face probe, a cv2 5.0
refusing the punch-in aim, and a purpose-built cv2 4.12 venv missing
`jsonschema` on the very next attempt. (The Remotion half already has an
owner.) Every lane fixed it locally with its own venv, each missing
something different - nothing declared the set, so every attempt
rediscovered a different subset.

`library/tools/shared_environment.py` (the BUILD half) is the one owner;
`env.face_detector` and `env.reel_build_libraries` in
`library/tools/requirements.py` are the pre-build refusal both reel-build
lanes share. A missing detector, cascade file or library is ONE CLEAR
MESSAGE BEFORE THE BUILD STARTS, naming what is missing and what would
supply it.

Nothing here reaches Resolve, renders, or a real project. The cv2 in
each case is a fake in `sys.modules`, because the point is what the
declaration says about each machine shape - not what this machine's
own cv2 happens to be.
"""

import os
import sys
import types
from pathlib import Path

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from library.tools import requirements as R  # noqa: E402
from library.tools import shared_environment as se  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[1]


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

def test_absent_detector_fails_the_pre_build_check_by_name(monkeypatch):
    """The first surviving gap: present cv2, absent cascades.

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


def test_the_loader_and_the_requirement_agree(monkeypatch, tmp_path):
    """The verdict has one owner: the loader the build really calls.

    `face_detector_usable` asks `subject_framing.load_face_cascade`,
    never a re-derived answer - so a working detector passes and every
    broken shape above refuses, through the same call the punch-in
    aim makes.
    """
    from library.tools import subject_framing

    _cv2_with_classifier(monkeypatch, tmp_path, loads=True)
    assert subject_framing.load_face_cascade() is not None
    assert se.face_detector_usable() is True

    _cv2_with_classifier(monkeypatch, tmp_path, loads=False)
    assert subject_framing.load_face_cascade() is None
    assert se.face_detector_usable() is False


# ── gap 2: completeness of a purpose-built venv's libraries ──────────

def test_missing_library_fails_the_pre_build_check_by_name(monkeypatch):
    """The second surviving gap: a venv built for one thing, missing
    another. The row's own instance was `jsonschema` absent from a cv2
    4.12 venv on the very next build attempt."""
    monkeypatch.setattr(se, "REEL_BUILD_LIBRARIES", ("no_such_module_xyz",))

    assert se.missing_build_libraries() == ("no_such_module_xyz",)
    assert se.build_libraries_present() is False

    requirement = _requirement("env.reel_build_libraries")
    verdict = requirement.check(R.Context())
    assert verdict.is_unsatisfied
    assert verdict.missing == "env.reel_build_libraries"
    assert "no_such_module_xyz" in verdict.reason
    assert se.REQUIREMENTS_FILE in verdict.reason


def test_the_declared_libraries_are_the_ones_requirements_pins():
    """The declaration must match reality: every entry is pinned in
    `requirements.txt`, and every entry imports in an interpreter that
    built from it - which this test interpreter did, or half this
    suite could not even collect."""
    pinned = (REPO_ROOT / "requirements.txt").read_text(encoding="utf-8")
    assert "jsonschema" in pinned
    assert "pyyaml" in pinned
    assert "jsonschema" in se.REEL_BUILD_LIBRARIES, (
        "the row's own instance - a venv missing jsonschema - left the "
        "declared set")
    assert "yaml" in se.REEL_BUILD_LIBRARIES, (
        "read on every build through the project configuration and the "
        "reel look")
    assert se.missing_build_libraries() == ()


# ── the acceptance shape: one message, before the build ─────────────

def test_both_gaps_are_one_message_before_the_build(monkeypatch):
    """Missing detector AND missing library: one message, naming both
    halves and what supplies each - not a refusal three steps in and
    not a silent gap."""
    _no_cv2(monkeypatch)
    monkeypatch.setattr(se, "REEL_BUILD_LIBRARIES", ("no_such_module_xyz",))

    ready, _ = se.reel_build_environment_available()
    assert ready is False
    with pytest.raises(se.ReelBuildEnvironmentMissing) as raised:
        se.require_reel_build_environment()
    message = str(raised.value)
    assert sys.executable in message
    assert "cv2 is not installed" in message
    assert "no_such_module_xyz" in message
    assert se.FACE_DETECTOR_PIN in message
    assert se.REQUIREMENTS_FILE in message


def test_a_satisfied_environment_passes_silently(monkeypatch, tmp_path):
    """Both halves present: the check returns truthy and says nothing.

    Silence is the contract - a satisfied environment must not print,
    warn, or refuse; the build simply starts."""
    _cv2_with_classifier(monkeypatch, tmp_path, loads=True)
    monkeypatch.setattr(se, "REEL_BUILD_LIBRARIES", ())

    assert se.reel_build_environment_problems() == []
    ready, detail = se.reel_build_environment_available()
    assert ready is True
    assert detail.strip()
    assert se.require_reel_build_environment() is None

    for name in ("env.face_detector", "env.reel_build_libraries"):
        assert _requirement(name).check(R.Context()).is_satisfied


def test_the_build_lanes_consume_both_requirements_and_verify_consumes_neither():
    """The refusal fires before a build starts, never before a grading.

    `reel.verify` grades timelines already placed and aims nothing, so
    asking it for a detector would refuse a grading that needs none.
    """
    for name in ("env.face_detector", "env.reel_build_libraries"):
        requirement = _requirement(name)
        assert requirement.kind == R.KIND_ENVIRONMENT
        assert requirement.consumers == ("build_reels",), (
            f"{name} is consumed by {requirement.consumers}: a pipeline "
            f"step's requirements must not move with this row, and the "
            f"verify node must not ask for a detector it never aims")
