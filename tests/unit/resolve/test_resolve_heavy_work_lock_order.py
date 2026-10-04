"""Resolve waiters never reserve the machine-wide heavy-work lock."""

from __future__ import annotations

import ast
from contextlib import contextmanager
import os
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest

from library.tools import resolve_lock

REPO_ROOT = Path(__file__).resolve().parents[3]


def _wait_for_marker(path: Path, process: subprocess.Popen, timeout: float):
    deadline = time.monotonic() + timeout
    while not path.exists() and time.monotonic() < deadline:
        if process.poll() is not None:
            stdout, _ = process.communicate(timeout=1)
            pytest.fail(f"worker exited before {path.name}: {stdout}")
        time.sleep(0.02)
    assert path.exists(), f"worker did not create {path.name} within {timeout}s"


@pytest.mark.serial(
    reason="Spawns nested Python workers while exercising lock handoff; "
           "keep child startup out of xdist saturation."
)
@pytest.mark.parametrize(
    ("module_name", "function_name", "stub_name", "call"),
    [
        (
            "library.tools.execution.resolve_render",
            "render_timeline",
            "_connect",
            'entry(output_dir="unused")',
        ),
        (
            "library.steps.step_6_01_render.resolve_build_timeline",
            "build_timeline",
            "_connect_resolve",
            "entry(manifest={'project': {'name': 'Test', "
            "'resolution': [1920, 1080], 'frame_rate': 30}, "
            "'tracks': {'V1': {'clips': [{'source_file': '/missing.mov', "
            "'timeline_in_frame': 0}]}}})",
        ),
    ],
)
def test_resolve_waiter_does_not_hold_heavy_work_lock(
    tmp_path, monkeypatch, module_name, function_name, stub_name, call
):
    """Real temp-directory flocks prove the production decorators' order.

    The entry-point body is stubbed before it can connect to Resolve. While
    the Resolve flock is occupied, a waiting caller must leave the separate
    heavy-work lock directory absent.
    """
    resolve_dir = tmp_path / "resolve-locks"
    resolve_dir.mkdir()
    home = tmp_path / "home"
    home.mkdir()
    heavy_lock = home / ".local/share/vep/heavy-work.lock"
    ready = tmp_path / "worker-ready"
    start = tmp_path / "worker-start"
    waiting = tmp_path / "worker-waiting"
    entered = tmp_path / "worker-entered"
    monkeypatch.setenv(resolve_lock.LOCK_DIR_ENV, str(resolve_dir))
    monkeypatch.setattr(resolve_lock, "_sole_writer_reason", None)
    monkeypatch.setattr(resolve_lock, "captain_present", lambda: None)
    monkeypatch.setattr(resolve_lock, "_wait_for_captain", lambda *args: None)

    child_source = textwrap.dedent(
        f"""
        import importlib
        from pathlib import Path
        import sys
        import time
        from library.tools import resolve_lock

        resolve_lock.captain_present = lambda: None
        resolve_lock._wait_for_captain = lambda *args: None
        original_flock = resolve_lock._flock
        def observed_flock(*args, **kwargs):
            acquired = original_flock(*args, **kwargs)
            if not acquired:
                Path({str(waiting)!r}).touch()
            return acquired
        resolve_lock._flock = observed_flock

        sys.path.insert(0, {str(REPO_ROOT / "library/steps/step_6_01_render")!r})
        module = importlib.import_module({module_name!r})
        if {module_name!r} == (
                "library.steps.step_6_01_render.resolve_build_timeline"):
            module._preflight_check = lambda _manifest: []
        Path({str(ready)!r}).touch()
        while not Path({str(start)!r}).exists():
            time.sleep(0.01)
        def stop_before_resolve(*args, **kwargs):
            assert (Path.home() / ".local/share/vep/heavy-work.lock").exists()
            Path({str(entered)!r}).touch()
            raise RuntimeError("stubbed before Resolve")
        setattr(module, {stub_name!r}, stop_before_resolve)
        entry = getattr(module, {function_name!r})
        try:
            {call}
        except RuntimeError as error:
            assert str(error) == "stubbed before Resolve"
        else:
            raise AssertionError("entry point body was not stubbed")
        """
    )
    env = os.environ.copy()
    env["HOME"] = str(home)
    env["PIPELINE_RESOLVE_LEASE_TIMEOUT"] = "30"
    env.pop(resolve_lock.INHERIT_ENV, None)
    env.pop("VEP_HEAVY_WORK_OWNER", None)
    env.pop("VEP_HEAVY_WORK_LOCK_DIR", None)

    process = None
    try:
        process = subprocess.Popen(
            [sys.executable, "-c", child_source],
            cwd=REPO_ROOT,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
        )
        _wait_for_marker(ready, process, 120)
        with resolve_lock.resolve_lease(
            "test: hold Resolve while the entry point queues", timeout=5
        ):
            start.touch()
            _wait_for_marker(waiting, process, 20)
            assert not heavy_lock.exists(), (
                f"{function_name} acquired heavy work while queued for Resolve"
            )

        _wait_for_marker(entered, process, 10)
        stdout, _ = process.communicate(timeout=10)
        assert process.returncode == 0, stdout
        assert not heavy_lock.exists()
    finally:
        if process is not None and process.poll() is None:
            process.kill()
            process.communicate(timeout=5)


def _name(node: ast.AST) -> str:
    return ast.unparse(node)


def test_combined_resolve_and_heavy_lock_sites_use_the_global_order():
    """Every syntactically combined acquisition takes Resolve first."""
    paths = list((REPO_ROOT / "library").rglob("*.py"))
    violations = []
    for path in paths:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                names = [_name(decorator) for decorator in node.decorator_list]
                heavy = [i for i, name in enumerate(names)
                         if "heavy_work_locked" in name]
                resolve = [i for i, name in enumerate(names)
                           if "under_lease" in name]
                if heavy and resolve and min(resolve) > min(heavy):
                    violations.append(
                        f"{path.relative_to(REPO_ROOT)}:{node.lineno} decorators"
                    )
            if isinstance(node, (ast.With, ast.AsyncWith)):
                names = [_name(item.context_expr) for item in node.items]
                heavy = [i for i, name in enumerate(names)
                         if "heavy_work_lock" in name]
                resolve = [i for i, name in enumerate(names)
                           if "resolve_lease" in name]
                if heavy and resolve and min(resolve) > min(heavy):
                    violations.append(
                        f"{path.relative_to(REPO_ROOT)}:{node.lineno} with-items"
                    )

    assert not violations, (
        "combined locks must be acquired as Resolve lease, then heavy-work "
        "lock: " + ", ".join(violations)
    )


def test_build_prepares_before_resolve_and_connects_inside_both_locks(
    monkeypatch,
):
    """The offline plan runs before the lease; Resolve connects after both."""
    from library.steps.step_6_01_render import resolve_build_timeline as builder
    from library.tools import heavy_work_lock

    active = {"resolve": False, "heavy": False}
    events = []
    original_prepare = builder._prepare_timeline_build

    def prepare(*args, **kwargs):
        assert not active["resolve"]
        assert not active["heavy"]
        events.append("prepare")
        return original_prepare(*args, **kwargs)

    @contextmanager
    def resolve_scope(*args, **kwargs):
        assert not active["resolve"]
        active["resolve"] = True
        events.append("resolve-enter")
        try:
            yield
        finally:
            events.append("resolve-exit")
            active["resolve"] = False

    @contextmanager
    def heavy_scope(*args, **kwargs):
        assert active["resolve"]
        active["heavy"] = True
        events.append("heavy-enter")
        try:
            yield
        finally:
            events.append("heavy-exit")
            active["heavy"] = False

    def connect():
        assert active["resolve"]
        assert active["heavy"]
        events.append("connect")
        raise ConnectionError("offline test stopped before Resolve")

    monkeypatch.setattr(builder, "_prepare_timeline_build", prepare)
    monkeypatch.setattr(builder, "_connect_resolve", connect)
    monkeypatch.setattr(resolve_lock, "resolve_lease", resolve_scope)
    monkeypatch.setattr(heavy_work_lock, "heavy_work_lock", heavy_scope)

    manifest = {
        "project": {
            "name": "Lease boundary test",
            "resolution": [1920, 1080],
            "frame_rate": 30,
            "duration_seconds": 2,
        },
        "tracks": {
            "V1": {"clips": [{
                "source_file": "offline.mov",
                "timeline_in_frame": 0,
                "timeline_in": 0,
                "timeline_out": 2,
            }]},
        },
    }
    from tests.resolve_double import builder_paths_exist
    with builder_paths_exist(builder):
        result = builder.build_timeline(manifest)

    assert "offline test stopped before Resolve" in result["errors"][0]
    assert events == [
        "prepare", "resolve-enter", "heavy-enter", "connect",
        "heavy-exit", "resolve-exit",
    ]


def test_offline_and_post_render_wrappers_contain_no_resolve_calls():
    """Resolve methods stay in the functions whose decorators take the lease."""
    protected_names = {
        "_connect_resolve", "_connect", "scriptapp_preserving_locale",
        "GetProjectManager", "GetMediaPool", "GetRootFolder",
        "GetClipList", "GetClipProperty", "CreateEmptyTimeline",
        "SetCurrentTimeline", "AppendToTimeline", "GetRenderJobStatus",
        "AddRenderJob", "StartRendering", "DeleteRenderJob",
    }
    modules = (
        ("library/steps/step_6_01_render/resolve_build_timeline.py",
         {"_prepare_timeline_build", "build_timeline"}),
        ("library/tools/execution/resolve_render.py", {"render_timeline"}),
    )
    violations = []
    for filename, function_names in modules:
        tree = ast.parse((REPO_ROOT / filename).read_text(encoding="utf-8"))
        for node in tree.body:
            if not isinstance(node, ast.FunctionDef) or node.name not in function_names:
                continue
            referenced = {
                child.attr for child in ast.walk(node)
                if isinstance(child, ast.Attribute)
            }
            referenced.update(
                child.id for child in ast.walk(node)
                if isinstance(child, ast.Name)
            )
            escaped = sorted(protected_names & referenced)
            if escaped:
                violations.append(f"{filename}:{node.name}: {escaped}")
    assert not violations, "Resolve calls escaped the leased worker: " + "; ".join(violations)
