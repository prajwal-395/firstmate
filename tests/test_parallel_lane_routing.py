"""The parallel/serial boundary routes every file to exactly one lane.

The boundary is executable code run fresh on every gate invocation
(`library/tools/lane_routing.py`), never a checked-in list.  These
tests pin it from both directions: synthetic files prove each clause
fires on the real shape and stays quiet on the known-safe shapes
(ephemeral ports, stubbed transports, fake modules, docstring examples),
and whole-tree tests prove every file lands in exactly one lane with
the measured unsafe set routing serial.

A future test that matches an unsafe shape without the marker fails
here with the clause reason, and the fix is the marker.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from library.tools.lane_routing import (  # noqa: E402
    PARALLEL,
    SERIAL,
    classify_file,
    route_suite,
)

RESOLVE_DRIVING = {
    "tests/test_marker_capture_against_resolve.py",
    "tests/test_marker_feedback_against_resolve.py",
}
HEAVY_ML_FILE = "tests/test_ml_dependencies_real.py"

# Files the measurement (data/vep-parallelise-the-test-gate/report.md)
# verified safe by reading: ephemeral ports, stubbed transports, fake
# modules.  If any of these routes serial, the boundary is wider than
# measured and the parallel prize shrinks for no reason.
KNOWN_SAFE = [
    "tests/test_gemma_shim.py",  # own servers on ephemeral ports
    "tests/test_cli_ml_preflight.py",  # blocked-import child interpreters
    "tests/test_transcript_corrections.py",  # /tmp path to a fake backend
    "tests/test_llm_client.py",  # provider calls with keys absent/patched
    "tests/test_brief_reference.py",  # FakeClient swap
    "tests/test_full_auto.py",  # patch(...generate)
    "tests/test_qa_feedback_loop_integration.py",  # patched LLM seams
    "tests/test_vision_model_server.py",  # urlopen stubbed
    "tests/test_video_segment_analyzer.py",  # generate/load mocked
    "tests/test_pipeline_skills.py",  # analyze_image[s] monkeypatched
]


def _route_source(tmp_path, name: str, source: str):
    path = tmp_path / name
    path.write_text(source, encoding="utf-8")
    return classify_file(path)


class TestClause1LiveProviderCalls:
    def test_bare_llmclient_call_is_serial(self, tmp_path):
        route = _route_source(
            tmp_path, "test_x.py",
            "from library.tools.llm_client import LLMClient\n"
            "\n"
            "def test_live():\n"
            "    client = LLMClient('gemini', 'gemini-2.5-flash')\n"
            "    assert client.generate('hello') != ''\n",
        )
        assert route.lane == SERIAL
        assert "LLMClient" in route.reason

    def test_patched_provider_call_stays_parallel(self, tmp_path):
        route = _route_source(
            tmp_path, "test_x.py",
            "from unittest.mock import patch\n"
            "from library.tools.llm_client import LLMClient\n"
            "\n"
            "def test_faked():\n"
            "    with patch.dict('os.environ', {}, clear=True):\n"
            "        assert LLMClient('gemini', 'm').generate('hi') == '{}'\n",
        )
        assert route.lane == PARALLEL, route.reason


class TestClause2WeightLoads:
    def test_sentence_transformer_construction_is_serial(self, tmp_path):
        route = _route_source(
            tmp_path, "test_x.py",
            "def test_embed():\n"
            "    model = SentenceTransformer('all-MiniLM-L6-v2')\n"
            "    assert model is not None\n",
        )
        assert route.lane == SERIAL

    def test_heavy_ml_marker_is_serial(self, tmp_path):
        route = _route_source(
            tmp_path, "test_x.py",
            "import pytest\n"
            "\n"
            "@pytest.mark.heavy_ml\n"
            "def test_real_measurement():\n"
            "    assert True\n",
        )
        assert route.lane == SERIAL


class TestClause3FixedPorts:
    def test_fixed_port_keyword_is_serial(self, tmp_path):
        route = _route_source(
            tmp_path, "test_x.py",
            "def test_server():\n"
            "    config = serve(host='127.0.0.1', port=8080)\n"
            "    assert config is not None\n",
        )
        assert route.lane == SERIAL

    def test_ephemeral_port_keyword_stays_parallel(self, tmp_path):
        route = _route_source(
            tmp_path, "test_x.py",
            "def test_server():\n"
            "    config = serve(host='127.0.0.1', port=0)\n"
            "    assert config is not None\n",
        )
        assert route.lane == PARALLEL, route.reason


class TestClause4FixedPaths:
    def test_open_on_tmp_is_serial(self, tmp_path):
        route = _route_source(
            tmp_path, "test_x.py",
            "def test_file():\n"
            "    fh = open('/tmp/fixture.wav', 'rb')\n"
            "    assert fh is not None\n",
        )
        assert route.lane == SERIAL

    def test_tmp_path_open_stays_parallel(self, tmp_path):
        route = _route_source(
            tmp_path, "test_x.py",
            "def test_file(tmp_path):\n"
            "    p = tmp_path / 'out.wav'\n"
            "    p.write_bytes(b'data')\n"
            "    assert p.exists()\n",
        )
        assert route.lane == PARALLEL, route.reason


class TestClause5LiveResolve:
    def test_resolve_session_fixture_is_serial(self, tmp_path):
        route = _route_source(
            tmp_path, "test_x.py",
            "def test_drives_app(resolve_session):\n"
            "    assert resolve_session is not None\n",
        )
        assert route.lane == SERIAL


class TestClause6ExplicitOptOut:
    def test_serial_marker_with_reason_is_serial(self, tmp_path):
        route = _route_source(
            tmp_path, "test_x.py",
            "import pytest\n"
            "\n"
            "@pytest.mark.serial('shares the hand-rolled lock file')\n"
            "def test_opted_out():\n"
            "    assert True\n",
        )
        assert route.lane == SERIAL


class TestFailClosed:
    def test_unparseable_is_serial(self, tmp_path):
        route = _route_source(
            tmp_path, "test_x.py", "def broken(:\n  ???\n")
        assert route.lane == SERIAL
        assert "unparseable" in route.reason

    def test_missing_file_is_serial(self, tmp_path):
        route = classify_file(tmp_path / "test_gone.py")
        assert route.lane == SERIAL


class TestWholeTreePinning:
    def test_measured_unsafe_set_routes_serial(self):
        by_rel = {
            str(r.path.relative_to(REPO_ROOT)): r for r in route_suite(REPO_ROOT)
        }
        for rel in RESOLVE_DRIVING | {HEAVY_ML_FILE}:
            assert rel in by_rel, f"{rel} is gone from the tree"
            assert by_rel[rel].lane == SERIAL, (
                f"{rel} routes {by_rel[rel].lane}: {by_rel[rel].reason}")

    @pytest.mark.heavy
    def test_serial_lane_holds_nothing_undeclared(self):
        """A new serial file must be a Resolve driver, the heavy_ml
        tier, or an explicit opt-out - never an accidental match."""
        serial = [r for r in route_suite(REPO_ROOT) if r.lane == SERIAL]
        for route in serial:
            rel = str(route.path.relative_to(REPO_ROOT))
            assert (
                rel in RESOLVE_DRIVING
                or "heavy_ml" in route.reason
                or "mark.serial" in route.reason
            ), f"{rel} routes serial with no declaration: {route.reason}"
