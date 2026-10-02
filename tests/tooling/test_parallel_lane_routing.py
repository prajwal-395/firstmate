"""Smoke checks for the gate's computed parallel/serial boundary."""

from __future__ import annotations

from pathlib import Path

from library.tools.lane_routing import PARALLEL, SERIAL, classify_file, route_suite

REPO_ROOT = Path(__file__).resolve().parents[2]
RESOLVE_DRIVING = {
    "tests/qualification/test_marker_capture_against_resolve.py",
    "tests/qualification/test_marker_feedback_against_resolve.py",
    "tests/qualification/test_resolve_qualification.py",
}


def _route(tmp_path: Path, source: str):
    path = tmp_path / "test_sample.py"
    path.write_text(source, encoding="utf-8")
    return classify_file(path)


def test_risky_sources_route_serial(tmp_path):
    cases = {
        "live provider": (
            "def test_live():\n    analyze_image('frame.jpg', 'prompt')\n",
            "live provider call",
        ),
        "weight load": (
            "def test_model():\n    SentenceTransformer('model')\n",
            "weight load",
        ),
        "fixed port": (
            "def test_server():\n    serve(port=8080)\n",
            "fixed port",
        ),
        "fixed path": (
            "def test_file():\n    open('/tmp/fixture.wav')\n",
            "fixed path",
        ),
        "live Resolve": (
            "def test_resolve(resolve_session):\n    assert resolve_session\n",
            "resolve_session",
        ),
        "declared opt-out": (
            "import pytest\n"
            "@pytest.mark.serial('shared lock')\n"
            "def test_shared():\n    pass\n",
            "mark.serial",
        ),
        "reasonless opt-out": (
            "import pytest\n"
            "@pytest.mark.serial\n"
            "def test_shared():\n    pass\n",
            "without a reason",
        ),
    }

    for label, (source, reason) in cases.items():
        route = _route(tmp_path, source)
        assert route.lane == SERIAL, f"{label}: {route.reason}"
        assert reason in route.reason, f"{label}: {route.reason}"


def test_stubbed_ephemeral_and_fixture_paths_stay_parallel(tmp_path):
    cases = (
        "from unittest.mock import patch\n"
        "def test_fake():\n"
        "    with patch('library.tools.vision_model.analyze_image'):\n"
        "        analyze_image('frame.jpg', 'prompt')\n",
        "def test_server():\n    serve(host='127.0.0.1', port=0)\n",
        "def test_file(tmp_path):\n"
        "    (tmp_path / 'fixture.wav').write_bytes(b'data')\n",
    )

    for source in cases:
        route = _route(tmp_path, source)
        assert route.lane == PARALLEL, route.reason


def test_unreadable_and_unparseable_files_fail_closed(tmp_path):
    malformed = tmp_path / "test_malformed.py"
    malformed.write_text("def broken(:\n", encoding="utf-8")

    assert "unparseable" in classify_file(malformed).reason
    assert classify_file(tmp_path / "test_missing.py").lane == SERIAL


def test_current_tree_routes_each_file_once_and_declares_its_serial_files():
    routes = route_suite(REPO_ROOT)
    files = [route.path for route in routes]
    assert len(files) == len(set(files))

    by_rel = {route.path.relative_to(REPO_ROOT).as_posix(): route for route in routes}
    for relative in RESOLVE_DRIVING | {"tests/qualification/test_ml_dependencies_real.py"}:
        assert relative in by_rel, f"{relative} is missing from the test tree"
        assert by_rel[relative].lane == SERIAL, (
            f"{relative}: {by_rel[relative].reason}"
        )
    for route in routes:
        if route.lane != SERIAL:
            continue
        relative = route.path.relative_to(REPO_ROOT).as_posix()
        assert (
            relative in RESOLVE_DRIVING
            or "real_model" in route.reason
            or "mark.serial" in route.reason
        ), f"{relative} has no serial declaration: {route.reason}"
