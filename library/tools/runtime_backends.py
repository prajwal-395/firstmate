"""Describe the native-capable implementations selected on this machine.

The result is diagnostic metadata: it reports the backend a run will use,
without building binaries, starting the broker, or touching Resolve.
"""

from __future__ import annotations

import os


def snapshot(project_folder=None) -> dict[str, str]:
    """Return the effective edit, broker, Resolve driver and graphics backends."""
    from library.tools import edit_backend, graphics_renderer, native_refresh
    from ren.engine_root import find_engine_root

    root = find_engine_root()
    backends = {}

    requested_edit = os.environ.get(edit_backend.BACKEND_ENV, edit_backend.PYTHON).strip().lower()
    try:
        active_edit = "rust" if edit_backend.rust() is not None else "python"
    except Exception:  # noqa: BLE001 - diagnostics must remain available
        active_edit = "python"
    backends["edit_core"] = active_edit
    if requested_edit == edit_backend.RUST and active_edit != "rust":
        backends["edit_core_requested"] = "rust (fell back to python)"

    daemon_choice = os.environ.get("REN_RESOLVED_DAEMON", "python").strip().lower()
    if daemon_choice == "rust":
        try:
            from library.tools.resolved.client import _rust_daemon
            backends["resolve_broker"] = (
                "rust" if _rust_daemon() is not None else "rust (unavailable)")
        except Exception:  # noqa: BLE001 - diagnostics must remain available
            backends["resolve_broker"] = "rust (unknown)"
    else:
        backends["resolve_broker"] = "python"

    try:
        state, _detail = native_refresh.status(root, "resolve-control") if root else ("missing", "")
        backends["resolve_ui_driver"] = "rust" if state == "fresh" else "unavailable"
    except Exception:  # noqa: BLE001 - diagnostics must remain available
        backends["resolve_ui_driver"] = "unknown"

    try:
        backends["graphics_renderer"] = graphics_renderer.resolve_engine(
            str(project_folder) if project_folder else None)
    except Exception as exc:  # noqa: BLE001 - retain a useful diagnostic
        backends["graphics_renderer"] = f"unavailable ({type(exc).__name__})"
    return backends


def doctor_line(project_folder=None) -> str:
    """Human-readable one-line summary for ``ren doctor``."""
    values = snapshot(project_folder)
    return "Runtime backends: " + ", ".join(
        f"{name}={value}" for name, value in values.items())
