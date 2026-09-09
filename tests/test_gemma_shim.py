"""On-demand Gemma server: tiny shim on the fixed URL starts the real
model server at the first request and stops it after N idle minutes.

Only stopping the process returns the ~7 GB (POST /unload frees ~145 MB),
so idle here means the backend PROCESS is gone. The shim itself stays up
(~40 MB) so the captain's fixed provider URL never breaks.

These tests drive the shim against a FAKE backend (stdlib http.server
standing in for mlx_vlm) - no model is loaded.
"""

import json
import sys
import textwrap
import time
import urllib.error
import urllib.request

import pytest

from library.tools import gemma_shim
from library.tools.gemma_shim import GemmaShim, ShimConfig, server_scope

FAKE_BACKEND = textwrap.dedent(
    """
    import json, sys, time
    from http.server import BaseHTTPRequestHandler, HTTPServer

    port = int(sys.argv[1])
    delay = float(sys.argv[2]) if len(sys.argv) > 2 else 0.0
    time.sleep(delay)  # simulate model-load time before listening

    class H(BaseHTTPRequestHandler):
        def _send(self, code, payload):
            body = json.dumps(payload).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if self.path == "/health":
                self._send(200, {"status": "healthy", "fake": True})
            elif self.path == "/slow":
                time.sleep(4)
                self._send(200, {"slow": "done"})
            else:
                self._send(200, {"path": self.path})

        def do_POST(self):
            n = int(self.headers.get("Content-Length", 0))
            raw = self.rfile.read(n) if n else b""
            try:
                payload = json.loads(raw.decode())
            except ValueError:
                payload = {"raw": raw.decode(errors="replace")}
            if self.path == "/slow":
                time.sleep(4)
            self._send(200, {"echo": payload})

        def log_message(self, *a):
            pass

    HTTPServer(("127.0.0.1", port), H).serve_forever()
    """
)


@pytest.fixture()
def fake_backend_script(tmp_path):
    path = tmp_path / "fake_backend.py"
    path.write_text(FAKE_BACKEND, encoding="utf-8")
    return str(path)


def _free_port():
    import socket

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture()
def ports():
    return _free_port(), _free_port()


def _post(url, payload, timeout=30):
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.status, json.loads(resp.read().decode())


def _get(url, timeout=10):
    with urllib.request.urlopen(url, timeout=timeout) as resp:
        return resp.status, json.loads(resp.read().decode())


def test_cold_request_starts_backend_and_is_proxied(ports, fake_backend_script):
    """A request arriving with no backend running is ANSWERED, not failed."""
    shim_port, _ = ports
    cfg = ShimConfig(
        shim_host="127.0.0.1",
        shim_port=shim_port,
        backend_host="127.0.0.1",
        backend_port=ports[1],
        model="fake-model",
        idle_timeout_s=3600.0,
        startup_timeout_s=30.0,
        reap_interval_s=0.2,
        backend_cmd=[sys.executable, fake_backend_script, str(ports[1])],
    )
    shim = GemmaShim(cfg)
    shim.start()
    try:
        assert shim.backend_state() == "stopped"
        status, body = _post(
            f"http://127.0.0.1:{shim_port}/v1/chat/completions",
            {"model": "fake-model", "messages": "hi"},
        )
        assert status == 200
        assert body["echo"]["messages"] == "hi"
        assert shim.backend_state() == "ready"
    finally:
        shim.stop()


def test_idle_teardown_stops_backend_process(ports, fake_backend_script):
    """After the idle window with no work, the backend process is GONE."""
    shim_port, backend_port = ports
    cfg = ShimConfig(
        shim_host="127.0.0.1",
        shim_port=shim_port,
        backend_host="127.0.0.1",
        backend_port=backend_port,
        model="fake-model",
        idle_timeout_s=1.0,
        startup_timeout_s=30.0,
        reap_interval_s=0.2,
        backend_cmd=[sys.executable, fake_backend_script, str(backend_port)],
    )
    shim = GemmaShim(cfg)
    shim.start()
    try:
        _post(
            f"http://127.0.0.1:{shim_port}/v1/chat/completions",
            {"hello": "warm"},
        )
        proc = shim.backend_proc()
        assert proc is not None and proc.poll() is None
        deadline = time.time() + 15
        while proc.poll() is None and time.time() < deadline:
            time.sleep(0.2)
        assert proc.poll() is not None, "backend survived past the idle timeout"
        # The shim itself still answers on the fixed URL.
        status, body = _get(f"http://127.0.0.1:{shim_port}/_shim/status")
        assert status == 200
        assert body["backend"] == "stopped"
    finally:
        shim.stop()


def test_in_flight_request_blocks_teardown(ports, fake_backend_script):
    """Idle means no in-flight work AND no requests for the window."""
    shim_port, backend_port = ports
    cfg = ShimConfig(
        shim_host="127.0.0.1",
        shim_port=shim_port,
        backend_host="127.0.0.1",
        backend_port=backend_port,
        model="fake-model",
        idle_timeout_s=1.0,
        startup_timeout_s=30.0,
        reap_interval_s=0.2,
        backend_cmd=[sys.executable, fake_backend_script, str(backend_port)],
    )
    shim = GemmaShim(cfg)
    shim.start()
    try:
        _post(f"http://127.0.0.1:{shim_port}/v1/chat/completions", {"warm": 1})
        proc = shim.backend_proc()
        answers = []
        import threading

        def _slow():
            try:
                answers.append(
                    _post(
                        f"http://127.0.0.1:{shim_port}/slow",
                        {"slow": "go"},
                        timeout=30,
                    )
                )
            except Exception as exc:  # pragma: no cover - failure path
                answers.append(exc)

        t = threading.Thread(target=_slow, daemon=True)
        t.start()
        time.sleep(2.0)  # past the 1s idle timeout, request still running
        assert proc.poll() is None, "teardown killed a live request"
        t.join(timeout=30)
        assert answers and answers[0][0] == 200
        assert answers[0][1] == {"echo": {"slow": "go"}}
        # Once idle again, it is reaped.
        deadline = time.time() + 15
        while proc.poll() is None and time.time() < deadline:
            time.sleep(0.2)
        assert proc.poll() is not None
    finally:
        shim.stop()


def test_backend_failure_is_a_clear_503_not_a_hang(ports):
    """Model fails to load: clear message, bounded wait, never a hang."""
    shim_port, backend_port = ports
    cfg = ShimConfig(
        shim_host="127.0.0.1",
        shim_port=shim_port,
        backend_host="127.0.0.1",
        backend_port=backend_port,
        model="fake-model",
        idle_timeout_s=3600.0,
        startup_timeout_s=6.0,
        reap_interval_s=0.2,
        backend_cmd=[sys.executable, "-c", "import sys; sys.exit(3)"],
    )
    shim = GemmaShim(cfg)
    shim.start()
    try:
        t0 = time.time()
        with pytest.raises(urllib.error.HTTPError) as excinfo:
            _post(
                f"http://127.0.0.1:{shim_port}/v1/chat/completions",
                {"hi": 1},
                timeout=30,
            )
        elapsed = time.time() - t0
        assert excinfo.value.code == 503
        body = json.loads(excinfo.value.read().decode())
        assert "message" in body.get("error", {})
        assert elapsed < 20, f"failure took too long to surface: {elapsed:.1f}s"
    finally:
        shim.stop()


def test_hold_blocks_teardown_and_scope_releases(ports, fake_backend_script):
    """A pipeline burst holds the backend across gaps; scope releases it."""
    shim_port, backend_port = ports
    cfg = ShimConfig(
        shim_host="127.0.0.1",
        shim_port=shim_port,
        backend_host="127.0.0.1",
        backend_port=backend_port,
        model="fake-model",
        idle_timeout_s=1.0,
        startup_timeout_s=30.0,
        reap_interval_s=0.2,
        backend_cmd=[sys.executable, fake_backend_script, str(backend_port)],
    )
    shim = GemmaShim(cfg)
    shim.start()
    try:
        base = f"http://127.0.0.1:{shim_port}"
        with server_scope(base, timeout=10):
            _post(f"{base}/v1/chat/completions", {"warm": 1})
            proc = shim.backend_proc()
            time.sleep(2.0)  # past idle timeout, but held
            assert proc.poll() is None, "held backend was reaped"
        deadline = time.time() + 15
        while proc.poll() is None and time.time() < deadline:
            time.sleep(0.2)
        assert proc.poll() is not None, "released backend was never reaped"
    finally:
        shim.stop()


def test_stop_backend_refused_while_held(ports, fake_backend_script):
    """`gemma down` cannot kill work it did not start: 409 while held."""
    shim_port, backend_port = ports
    cfg = ShimConfig(
        shim_host="127.0.0.1",
        shim_port=shim_port,
        backend_host="127.0.0.1",
        backend_port=backend_port,
        model="fake-model",
        idle_timeout_s=3600.0,
        startup_timeout_s=30.0,
        reap_interval_s=0.2,
        backend_cmd=[sys.executable, fake_backend_script, str(backend_port)],
    )
    shim = GemmaShim(cfg)
    shim.start()
    try:
        base = f"http://127.0.0.1:{shim_port}"
        with server_scope(base, timeout=10):
            req = urllib.request.Request(f"{base}/_shim/stop-backend", data=b"{}")
            with pytest.raises(urllib.error.HTTPError) as excinfo:
                urllib.request.urlopen(req, timeout=10)
            assert excinfo.value.code == 409
    finally:
        shim.stop()


def test_health_reports_idle_without_starting_backend(ports, fake_backend_script):
    """GET /health on a cold shim answers WITHOUT loading the model."""
    shim_port, backend_port = ports
    cfg = ShimConfig(
        shim_host="127.0.0.1",
        shim_port=shim_port,
        backend_host="127.0.0.1",
        backend_port=backend_port,
        model="fake-model",
        idle_timeout_s=3600.0,
        startup_timeout_s=30.0,
        reap_interval_s=0.2,
        backend_cmd=[sys.executable, fake_backend_script, str(backend_port)],
    )
    shim = GemmaShim(cfg)
    shim.start()
    try:
        status, body = _get(f"http://127.0.0.1:{shim_port}/health")
        assert status == 200
        assert body["backend"] == "stopped"
        assert shim.backend_state() == "stopped"
    finally:
        shim.stop()
