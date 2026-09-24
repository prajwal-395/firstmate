"""step_1_04 must report WHERE the subject is, not only whether it is there.

The Haar cascade already returned `(x, y, w, h)` per face and the step kept
only `max(w*h)` as a scalar presence score. The position was measured and
discarded, and nothing else in the pipeline measures it: the v3 vision pass
emits shot size, identity and time ranges, and the two steps that do produce
boxes (`object_segmentation`, matte-triggered, and `ocr_extraction`, wired
and deselected by default) do not feed framing.

Also covered here: an OpenCV without Haar cascades. OpenCV 5 removed them,
`requirements.txt` allowed `>=4.8`, and the resulting AttributeError was
swallowed by the function's broad `except Exception` - so `face_presence`
returned EMPTY values rather than falling back to the variance heuristic.
Empty silently disabled the subject-absence usable-range rule in the vision
pass as well as subject-aware framing.
"""
import ast
import os
import sys

import pytest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

STEP_PATH = os.path.join(
    PROJECT_ROOT, "library", "steps", "step_1_04_temporal_index", "step.py")


def _face_presence_returns():
    """Every `return {...}` inside compute_face_presence, as key lists."""
    with open(STEP_PATH, encoding="utf-8") as f:
        tree = ast.parse(f.read())
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "compute_face_presence":
            out = []
            for sub in ast.walk(node):
                if isinstance(sub, ast.Return) and isinstance(sub.value, ast.Dict):
                    out.append([k.value for k in sub.value.keys
                                if isinstance(k, ast.Constant)])
            return out
    pytest.fail("compute_face_presence not found")


def test_every_return_path_carries_face_center_x():
    """Including the error paths.

    A consumer that indexes `face_center_x` must not have to guess whether
    the key is there. Five return sites exist - the happy path, two early
    ffmpeg failures and two exception handlers - and a key present on only
    some of them is the key-name mismatch this codebase is prone to.
    """
    returns = _face_presence_returns()
    assert returns, "no dict returns found"
    missing = [ks for ks in returns if "face_center_x" not in ks]
    assert not missing, f"return sites without face_center_x: {missing}"


def test_the_variance_fallback_reports_no_position():
    """It knows presence, never position.

    Emitting 0.5 ("centred") there would be a fabricated measurement that
    reads exactly like a real one, and it would move the picture.
    """
    with open(STEP_PATH, encoding="utf-8") as f:
        src = f.read()
    start = src.index("def compute_face_presence")
    body = src[start:src.index("# ── 11.", start)]
    fallback = body[body.index("Fallback: mid-frequency variance"):]
    assert "face_center_x.append(None)" in fallback


class TestCascadeAvailability:
    """`_load_face_cascade` must answer honestly on any OpenCV."""

    def test_returns_none_when_opencv_has_no_cascade_classifier(self, monkeypatch):
        """OpenCV 5: the attribute is simply gone."""
        from library.steps.step_1_04_temporal_index import step as s

        class FakeCv2:
            data = None

        monkeypatch.setitem(sys.modules, "cv2", FakeCv2())
        assert s._load_face_cascade() is None

    def test_returns_none_when_the_classifier_loaded_nothing(self, monkeypatch, tmp_path):
        """An empty CascadeClassifier detects nothing on every frame.

        That is indistinguishable from "no face in this video", which is
        the worst possible way for this to fail.
        """
        from library.steps.step_1_04_temporal_index import step as s

        xml = tmp_path / "haarcascade_frontalface_default.xml"
        xml.write_text("<opencv_storage/>")

        class Empty:
            def empty(self):
                return True

        class FakeData:
            haarcascades = str(tmp_path) + os.sep

        class FakeCv2:
            data = FakeData()

            @staticmethod
            def CascadeClassifier(path):
                return Empty()

        monkeypatch.setitem(sys.modules, "cv2", FakeCv2())
        assert s._load_face_cascade() is None

    def test_returns_the_classifier_when_everything_is_present(self, monkeypatch, tmp_path):
        from library.steps.step_1_04_temporal_index import step as s

        xml = tmp_path / "haarcascade_frontalface_default.xml"
        xml.write_text("<opencv_storage/>")
        sentinel = type("Loaded", (), {"empty": lambda self: False})()

        class FakeData:
            haarcascades = str(tmp_path) + os.sep

        class FakeCv2:
            data = FakeData()

            @staticmethod
            def CascadeClassifier(path):
                return sentinel

        monkeypatch.setitem(sys.modules, "cv2", FakeCv2())
        assert s._load_face_cascade() is sentinel
