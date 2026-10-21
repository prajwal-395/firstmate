"""Host the timeline shadow watcher beside the Rust broker process."""

from __future__ import annotations

import time

from library.tools import shadow_watcher

_resolve = None

#: How long the watcher's read of the open project may take.
#:
#: The read runs under a SHARED instance lease, and a shared lease
#: blocks every exclusive job - a watcher read that hangs holds the one
#: Resolve against every writer until it is given up. The lease
#: acquisition is bounded (the timeout below); the BODY was not, so a
#: Resolve that stopped answering held the shared lease for as long as
#: it liked: measured 2026-10-21, four watcher leases ran for 1478 s to
#: 6982 s, and the longest of them was the longest hold in the whole
#: job table.
#:
#: The budget is shorter than the lease timeout, so the read gives up
#: while it still holds the lease rather than after it.
READ_BUDGET_S = 5.0

#: How long the watcher waits for the shared lease itself.
LEASE_TIMEOUT_S = 10.0


def _project_name() -> str | None:
    global _resolve
    from library.tools.marker_feedback import connect_resolve
    from library.tools.resolve_deadline import deadline_proxy
    from library.tools.resolve_lock import resolve_lease
    from library.tools.resolved import jobs

    try:
        with resolve_lease("shadow watcher: open project", exclusive=False,
                           honor_captain=False, timeout=LEASE_TIMEOUT_S):
            if _resolve is None:
                _resolve = connect_resolve()
            return jobs.open_project_name(deadline_proxy(
                _resolve, label="shadow watcher: open project",
                timeout_s=READ_BUDGET_S))
    except Exception:  # noqa: BLE001 - watcher fails closed to unknown
        return None


def _project_folder(name: str) -> str:
    if not name:
        return ""
    from library.tools.project_registry import (
        load_project_config, project_config_paths,
    )

    for yaml_path in project_config_paths():
        try:
            config = load_project_config(yaml_path, migrate_format=False)
        except (ValueError, FileNotFoundError):
            continue
        if config.resolve.project_name == name:
            return str(yaml_path.parent.absolute())
    return ""


def main() -> int:
    from library.tools.resolved import client

    watcher = shadow_watcher.ShadowWatcher(
        submit=lambda kind, params, owner="": client.submit(
            kind, params, owner=owner),
        result=lambda job_id, wait=0.0: client.result(job_id, wait=wait),
    )
    watcher.start(_project_name, _project_folder)
    try:
        while True:
            time.sleep(1.0)
    except KeyboardInterrupt:
        watcher.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
