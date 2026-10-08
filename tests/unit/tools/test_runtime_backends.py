from library.tools import runtime_backends


def test_snapshot_reports_effective_native_backends(monkeypatch):
    from library.tools import edit_backend, graphics_renderer, native_refresh

    monkeypatch.setattr(edit_backend, "rust", lambda: object())
    monkeypatch.setattr(native_refresh, "status", lambda *_args: ("fresh", ""))
    monkeypatch.setattr(
        graphics_renderer, "resolve_engine", lambda _project: "fframes")
    monkeypatch.setenv("REN_RESOLVED_DAEMON", "rust")
    from library.tools.resolved import client
    monkeypatch.setattr(client, "_rust_daemon", lambda: object())

    assert runtime_backends.snapshot("/qualification") == {
        "edit_core": "rust",
        "resolve_broker": "rust",
        "resolve_ui_driver": "rust",
        "graphics_renderer": "fframes",
    }


def test_snapshot_reports_requested_edit_fallback(monkeypatch):
    from library.tools import edit_backend, graphics_renderer, native_refresh

    monkeypatch.setattr(edit_backend, "rust", lambda: None)
    monkeypatch.setattr(native_refresh, "status", lambda *_args: ("missing", ""))
    monkeypatch.setattr(
        graphics_renderer, "resolve_engine", lambda _project: "remotion")
    monkeypatch.setenv("REN_EDIT_BACKEND", "rust")
    monkeypatch.delenv("REN_RESOLVED_DAEMON", raising=False)

    backends = runtime_backends.snapshot()
    assert backends["edit_core"] == "python"
    assert backends["edit_core_requested"] == "rust (fell back to python)"
    assert backends["resolve_broker"] == "python"
    assert backends["resolve_ui_driver"] == "unavailable"
    assert backends["graphics_renderer"] == "remotion"


def test_snapshot_reports_default_rust_fallback(monkeypatch):
    from library.tools import edit_backend, graphics_renderer, native_refresh

    monkeypatch.setattr(edit_backend, "rust", lambda: None)
    monkeypatch.setattr(native_refresh, "status", lambda *_args: ("missing", ""))
    monkeypatch.setattr(
        graphics_renderer, "resolve_engine", lambda _project: "remotion")
    monkeypatch.delenv("REN_EDIT_BACKEND", raising=False)
    monkeypatch.delenv("REN_RESOLVED_DAEMON", raising=False)

    backends = runtime_backends.snapshot()
    assert backends["edit_core"] == "python"
    assert backends["edit_core_requested"] == "rust (fell back to python)"
