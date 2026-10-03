"""Append one durable record for every Resolve picture transform write.

The log shares ``pipeline_output/logs`` with the run's other records so
it can be compared with a later timeline read.  This module never asks
Resolve for metadata: callers supply identities they already have, and
the timeline id comes from the existing Resolve cursor guard.
"""

from __future__ import annotations

import contextvars
import inspect
import json
import os
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterator

from library.tools.project_layout import Area, ProjectLayout

TRANSFORM_PROPERTIES = frozenset({"Pan", "Tilt", "ZoomX", "ZoomY"})
LOG_FILENAME = "transform_writes.jsonl"
RUN_ENV = "REN_TRANSFORM_WRITE_RUN"

_REPO_ROOT = Path(__file__).resolve().parents[2]
_CONTEXT: contextvars.ContextVar[dict[str, Any]] = contextvars.ContextVar(
    "transform_write_context", default={})
_APPEND_LOCK = threading.Lock()
_PROCESS_RUN_ID = time.strftime("%Y%m%dT%H%M%S", time.gmtime()) + (
    f"-{os.getpid()}")
_MISSING = object()


class TransformWriteContextError(RuntimeError):
    """A transform write cannot be logged with complete identities."""


@contextmanager
def write_scope(**fields: Any) -> Iterator[None]:
    """Supply run and timeline metadata to setters below this call."""
    merged = {**_CONTEXT.get(),
              **{key: value for key, value in fields.items()
                 if value is not None}}
    token = _CONTEXT.set(merged)
    try:
        yield
    finally:
        _CONTEXT.reset(token)


def _project_folder(project_folder: Any, project: str) -> Path:
    in_test = "PYTEST_CURRENT_TEST" in os.environ
    if project_folder:
        candidate = Path(str(project_folder)).expanduser()
        if candidate.is_file() and candidate.name == "project.yaml":
            candidate = candidate.parent
        if candidate.is_dir():
            return candidate.resolve()

    ledger = os.environ.get("REN_PERF_LEDGER")
    if ledger and not in_test:
        candidate = Path(ledger).expanduser().parents[2]
        if (candidate / "project.yaml").is_file():
            return candidate.resolve()

    candidate = Path(project).expanduser() if project else None
    if candidate is not None and (candidate / "project.yaml").is_file():
        return candidate.resolve()

    if not in_test:
        for parent in (Path.cwd(), *Path.cwd().parents):
            if (parent / "project.yaml").is_file():
                return parent.resolve()

    if project and not in_test:
        found = _registered_project_folder(project)
        if found:
            return Path(found)

    raise TransformWriteContextError(
        f"cannot find the project folder for transform write {project!r}; "
        "the write was not sent to Resolve")


@lru_cache(maxsize=64)
def _registered_project_folder(project: str) -> str | None:
    """Resolve an exact Resolve project name to its local Ren project."""
    try:
        from library.tools.project_registry import scan_projects

        matches = [config for config in scan_projects()
                   if project in (config.resolve.project_name,
                                  config.name, config.slug)]
    except Exception:  # noqa: BLE001 - an unmapped project is refused below
        return None
    if len(matches) != 1:
        return None
    return str(matches[0].project_root)


def _project_name(project: str, project_folder: Path) -> str:
    if project:
        return str(project)
    config_path = project_folder / "project.yaml"
    if ("PYTEST_CURRENT_TEST" in os.environ
            and not config_path.is_file()):
        # Resolve doubles use explicit pytest temporary directories, not
        # Ren project configs. Keep their log path isolated while giving
        # the synthetic project a stable local name.
        return project_folder.name
    try:
        from library.schemas.project_config import load_project_config

        config = load_project_config(config_path)
        return str(config.resolve.project_name or config.name)
    except Exception as exc:  # noqa: BLE001 - do not invent a project name
        raise TransformWriteContextError(
            f"cannot read the Resolve project identity from "
            f"{project_folder / 'project.yaml'}") from exc


def _timeline_identity(timeline_id: Any, timeline_name: str) -> tuple[str, str]:
    if timeline_id and timeline_name:
        return str(timeline_id), str(timeline_name)
    in_test = "PYTEST_CURRENT_TEST" in os.environ
    if not timeline_id and timeline_name and in_test:
        # A fake timeline has no Resolve cursor generation. Its explicit
        # test name still distinguishes it inside the temporary project.
        timeline_id = f"pytest:{timeline_name}"
    if not timeline_id or not timeline_name:
        try:
            from library.tools.resolve_lock import last_timeline_identity

            known_id, known_name = last_timeline_identity()
        except Exception:  # noqa: BLE001 - missing context is refused below
            known_id, known_name = None, None
        timeline_id = timeline_id or known_id
        timeline_name = timeline_name or known_name
    if not timeline_id or not timeline_name:
        raise TransformWriteContextError(
            "transform write needs the timeline's name and unique id from "
            "the existing Resolve write context; the write was not sent")
    return str(timeline_id), str(timeline_name)


def _site(frame) -> dict[str, Any]:
    source = Path(frame.f_code.co_filename)
    try:
        filename = source.resolve().relative_to(_REPO_ROOT).as_posix()
    except (OSError, ValueError):
        filename = source.name
    return {"file": filename, "line": frame.f_lineno,
            "function": frame.f_code.co_name}


def _json_value(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    return str(value)


def _complete_context(item_identity: Any,
                      context: dict[str, Any]) -> tuple[dict[str, Any], Path]:
    project = str(context.get("project") or "")
    project_folder = _project_folder(context.get("project_folder"), project)
    project = _project_name(project, project_folder)
    timeline_id, timeline_name = _timeline_identity(
        context.get("timeline_id"), str(context.get("timeline_name") or ""))
    if item_identity is None or item_identity == "":
        raise TransformWriteContextError(
            "transform write needs a stable item identity; the write was "
            "not sent to Resolve")
    if not isinstance(item_identity, (dict, list, tuple, str, int)):
        raise TransformWriteContextError(
            "transform write item identity must be JSON data")
    complete = dict(context)
    complete.update({"project": project,
                     "project_folder": str(project_folder),
                     "timeline_id": timeline_id,
                     "timeline_name": timeline_name})
    return complete, project_folder


def validate_transform_write_context(*, item_identity: Any,
                                     **fields: Any) -> dict[str, Any]:
    """Resolve identities before an equivalent setter that reports after."""
    context = {**_CONTEXT.get(),
               **{key: value for key, value in fields.items()
                  if value is not None}}
    complete, _folder = _complete_context(item_identity, context)
    return complete


def _record(*, property_name: str, old_value: Any, new_value: Any,
            item_identity: Any, context: dict[str, Any], caller_site: dict | None,
            write_kind: str = "SetProperty") -> dict:
    context, project_folder = _complete_context(item_identity, context)
    project = context["project"]
    timeline_id = context["timeline_id"]
    timeline_name = context["timeline_name"]

    frame = inspect.currentframe()
    caller = frame.f_back.f_back if frame and frame.f_back else None
    try:
        site = caller_site or (_site(caller) if caller else {})
    finally:
        del frame
    run_id = (context.get("run_id") or os.environ.get("REN_PERF_RUN")
              or os.environ.get(RUN_ENV) or _PROCESS_RUN_ID)
    return {
        "timestamp_utc": datetime.now(timezone.utc).isoformat(
            timespec="microseconds"),
        "project": project,
        "timeline_name": timeline_name,
        "timeline_id": timeline_id,
        "item_identity": _json_value(item_identity),
        "property": property_name,
        "old_value": None if old_value is _MISSING else _json_value(old_value),
        "new_value": _json_value(new_value),
        "caller": site,
        "run_id": str(run_id),
        "write_kind": write_kind,
    }, project_folder


def _append(row: dict, project_folder: Path) -> None:
    path = ProjectLayout(project_folder).write_dir(Area.LOGS) / LOG_FILENAME
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = (json.dumps(row, ensure_ascii=False, sort_keys=True,
                          separators=(",", ":"), allow_nan=False) + "\n")
    with _APPEND_LOCK:
        fd = os.open(path, os.O_APPEND | os.O_CREAT | os.O_WRONLY, 0o600)
        try:
            remaining = memoryview(encoded.encode("utf-8"))
            while remaining:
                written = os.write(fd, remaining)
                if written <= 0:
                    raise OSError("transform log append made no progress")
                remaining = remaining[written:]
            os.fsync(fd)
        finally:
            os.close(fd)


def record_transform_write(property_name: str, new_value: Any, *,
                           item_identity: Any, old_value: Any = _MISSING,
                           caller_site: dict | None = None,
                           write_kind: str = "SetProperty",
                           **fields: Any) -> None:
    """Append one transform event without making a Resolve API call."""
    if property_name not in TRANSFORM_PROPERTIES:
        return
    context = {**_CONTEXT.get(),
               **{key: value for key, value in fields.items()
                  if value is not None}}
    row, project_folder = _record(
        property_name=property_name, old_value=old_value,
        new_value=new_value, item_identity=item_identity, context=context,
        caller_site=caller_site, write_kind=write_kind)
    _append(row, project_folder)


def set_property(item: Any, property_name: str, value: Any, *,
                 item_identity: Any = None, old_value: Any = _MISSING,
                 **fields: Any) -> Any:
    """Use the real Resolve setter, logging only the four transform keys.

    The log is written before the API call, so a filesystem error refuses
    the write instead of allowing an untraceable transform change.
    ``old_value`` is caller supplied only; this function never reads Resolve.
    """
    if property_name in TRANSFORM_PROPERTIES:
        context = {**_CONTEXT.get(),
                   **{key: value for key, value in fields.items()
                      if value is not None}}
        if old_value is _MISSING and "old_value" in context:
            old_value = context["old_value"]
        row, project_folder = _record(
            property_name=property_name, old_value=old_value, new_value=value,
            item_identity=item_identity, context=context, caller_site=None)
        _append(row, project_folder)
    return item.SetProperty(property_name, value)
