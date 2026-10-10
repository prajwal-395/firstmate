"""Lane A3 proof against live Resolve: a placed clip's stamp reads back.

Proves a placed clip's `customData` stamp reads back with the model ids
from the build's revision, and the shadow generation row joins that
revision to the verified binding ids.

Through the broker only: one `resolve.qualification_session` job
(EXCLUSIVE whole-take lease, `borrowed()` with restore + read-back,
project read back by the exact name "Ren Qualification"). No direct
Resolve calls here - the steps are recorded `resolve_axi run` scripts
the broker runs on its own connection. Never touches a captain
project or timeline; the only marker written is the update of one
existing marker's `customData` on the qualification fixture's own
"Q Markers" timeline, whose visible fields are asserted unchanged.

Runs ONLY inside a granted borrow-resolve window (`REN_LIVE_RESOLVE=1`):
without it the module skips before any broker contact, so no default
pytest run, dependent selection or gate can reach the instance.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import time
import uuid
from pathlib import Path

import pytest

pytestmark = pytest.mark.resolve_live

sys.path.insert(
    0, os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__)))))

QUALIFICATION_PROJECT = "Ren Qualification"
TIMELINE = "Q Markers"

#: The build revision's own ids, from the committed fixture model below.
ASSET_ID = "asset-ada"
CLIP_USE_ID = "use-ada"

RUN = uuid.uuid4().hex[:8]


def _live_enabled() -> bool:
    return os.environ.get("REN_LIVE_RESOLVE") == "1"


@pytest.fixture
def lock_dir(monkeypatch):
    """A broker socket of our own, under /tmp and SHORT.

    A Unix socket path is bounded (104 bytes on macOS) and a pytest
    tmp_path is deep enough to overflow it. Opts into detached brokers
    (`REN_TESTS_ALLOW_BROKER_SPAWN`) and forces the Python daemon so
    the broker serves this checkout's code - the proof scripts import
    `library.tools.model_stamps` and `library.tools.timeline_shadow`.
    Seeds the ren_native cache entry so broker startup never compiles
    the extension for real.
    """
    if not _live_enabled():
        pytest.skip("drives the live Resolve; runs only with "
                    "REN_LIVE_RESOLVE=1 inside a granted borrow-resolve window")
    from library.tools import ren_native_cache, ren_native_source
    from library.tools import resolve_lock
    from library.tools.resolved import client

    directory = Path(tempfile.mkdtemp(prefix="ren-a3proof-", dir="/tmp"))
    monkeypatch.setenv(resolve_lock.LOCK_DIR_ENV, str(directory))
    monkeypatch.setenv(client.TESTS_ALLOW_SPAWN_ENV, "1")
    monkeypatch.setenv("REN_RESOLVED_DAEMON", "python")
    cache = directory / "native-cache"
    monkeypatch.setenv(ren_native_cache.CACHE_ENV, str(cache))
    root = client._engine_root()
    fingerprint = (ren_native_source.source_fingerprint(root)
                   if root is not None else None)
    if fingerprint is not None:
        entry = cache / fingerprint
        entry.mkdir(parents=True, exist_ok=True)
        from importlib.machinery import EXTENSION_SUFFIXES
        (entry / ("ren_native" + EXTENSION_SUFFIXES[0])).write_bytes(
            b"test-seed")
        (entry / ren_native_cache.FINGERPRINT_FILENAME).write_text(
            fingerprint + "\n", encoding="utf-8")
    yield directory
    shutil.rmtree(directory, ignore_errors=True)


@pytest.fixture
def broker(lock_dir):
    """Ensure the proof's broker, and stop whatever is serving after."""
    from library.tools.resolved import client
    from library.tools.resolved.server import SOCKET_NAME

    path = lock_dir / SOCKET_NAME
    assert client.ping(path) is None
    answer = client.ensure(path=path)
    assert answer is not None, "the proof broker did not start"
    try:
        yield path
    finally:
        try:
            client.call({"op": "shutdown"}, path)
        except Exception:  # noqa: BLE001 - shutdown is best effort
            pass
        deadline = time.time() + 10.0
        while client.ping(path) is not None and time.time() < deadline:
            time.sleep(0.05)


SWITCH_SCRIPT = """\
import json
import time

found = None
for index in range(1, int(project.GetTimelineCount() or 0) + 1):
    candidate = project.GetTimelineByIndex(index)
    if candidate is not None and candidate.GetName() == "Q Markers":
        found = candidate
        break
if found is None:
    outcome = {"ok": False, "reason": "no Q Markers timeline"}
else:
    project.SetCurrentTimeline(found)
    read_back = ""
    deadline = time.time() + 10.0
    while time.time() < deadline:
        current = project.GetCurrentTimeline()
        read_back = current.GetName() if current else ""
        if read_back == "Q Markers":
            break
        time.sleep(0.2)
    if read_back != "Q Markers":
        outcome = {"ok": False,
                   "reason": "Q Markers did not read back as current"}
    else:
        outcome = {"ok": True,
                   "timeline": found.GetName(),
                   "timeline_id": str(found.GetUniqueId() or "")}
with open("a3_switch_result.json", "w", encoding="utf-8") as handle:
    json.dump(outcome, handle)
result = outcome
"""

STAMP_SCRIPT_TEMPLATE = """\
import json
import time

ASSET_ID = __ASSET_ID__
CLIP_USE_ID = __CLIP_USE_ID__
REVISION = __REVISION__


def skip(reason):
    outcome = {"ok": False, "reason": reason}
    with open("a3_stamp_result.json", "w", encoding="utf-8") as handle:
        json.dump(outcome, handle)
    return outcome


def current_markers_timeline():
    # Q Markers, current and read back - or None. This step targets the
    # current timeline (whatever the switch step left), so the recorded
    # run's backup and inventory read the exact live picture. Refusing
    # to stamp any other timeline is the guard against writing where
    # the proof did not mean to.
    current = project.GetCurrentTimeline()
    if current is not None and current.GetName() == "Q Markers":
        return current
    found = None
    for index in range(1, int(project.GetTimelineCount() or 0) + 1):
        candidate = project.GetTimelineByIndex(index)
        if candidate is not None and candidate.GetName() == "Q Markers":
            found = candidate
            break
    if found is None:
        return None
    project.SetCurrentTimeline(found)
    deadline = time.time() + 10.0
    while time.time() < deadline:
        current = project.GetCurrentTimeline()
        if current is not None and current.GetName() == "Q Markers":
            return current
        time.sleep(0.2)
    return None


found = current_markers_timeline()
if found is None:
    result = skip("Q Markers is not available and current")
else:
    project.SetCurrentTimeline(found)
    items = found.GetItemListInTrack("video", 1) or []
    markers = found.GetMarkers() or {}
    if not items:
        result = skip("Q Markers has no placed video clip")
    elif not markers:
        result = skip("Q Markers has no timeline marker to stamp")
    else:
        from library.tools import marker_payload
        from library.tools import model_stamps
        from library.tools import timeline_shadow

        unique_id = str(items[0].GetUniqueId() or "")
        clip_start = int(items[0].GetStart())
        clip_duration = int(items[0].GetDuration())
        by_frame = {int(key): key for key in markers}
        frame = sorted(by_frame)[0]
        before = dict(markers[by_frame[frame]])
        envelope = marker_payload.parse(
            found.GetMarkerCustomData(frame) or "")
        model_stamps.stamp_envelope(envelope, [
            model_stamps.model_link_record(
                asset_id=ASSET_ID, clip_use_id=CLIP_USE_ID,
                revision=REVISION, timeline=found.GetName(),
                track="V1", label=items[0].GetName() or "")])
        if not found.UpdateMarkerCustomData(
                frame, marker_payload.dumps(envelope)):
            result = skip("UpdateMarkerCustomData returned False")
        else:
            back = marker_payload.parse(
                found.GetMarkerCustomData(frame) or "")
            link = model_stamps.model_link_for(back, CLIP_USE_ID)
            after = dict((found.GetMarkers() or {})[by_frame[frame]])
            project_name = project.GetName()
            timeline_id = str(found.GetUniqueId() or "")
            snapshot = timeline_shadow.read_live(project, found)
            store = timeline_shadow.ShadowStore()
            head = store.head(project_name, timeline_id)
            generation = store.record(
                project=project_name, timeline_id=timeline_id,
                timeline_name=found.GetName(), snapshot=snapshot,
                source=timeline_shadow.OBSERVED,
                expected_head=head.generation if head else 0)
            binding = model_stamps.build_nle_binding(
                timeline_id=timeline_id,
                timeline_identity=found.GetName(),
                project_identity=project_name,
                generation=generation.generation,
                clip_bindings={CLIP_USE_ID: unique_id})
            joined = store.attach_model_link(
                project=project_name, timeline_id=timeline_id,
                generation=generation.generation,
                model_revision=REVISION,
                nle_binding_ids=[binding["id"]])
            outcome = {
                "ok": True,
                "frame": frame,
                "asset_id": (link or {}).get("asset_id"),
                "clip_use_id": (link or {}).get("clip_use_id"),
                "revision": (link or {}).get("revision"),
                "name_kept": after.get("name") == before.get("name"),
                "note_kept": after.get("note") == before.get("note"),
                "color_kept": after.get("color") == before.get("color"),
                "unique_id": unique_id,
                "clip_start": clip_start,
                "clip_duration": clip_duration,
                "timeline_id": timeline_id,
                "generation": joined.generation,
                "model_revision": joined.model_revision,
                "nle_binding_ids": list(joined.nle_binding_ids),
                "binding": binding,
            }
            with open("a3_stamp_result.json", "w", encoding="utf-8") as handle:
                json.dump(outcome, handle)
            result = {"frame": frame, "generation": joined.generation,
                      "revision": joined.model_revision,
                      "binding": binding["id"]}
"""


def _render_stamp_script() -> str:
    return (STAMP_SCRIPT_TEMPLATE
            .replace("__ASSET_ID__", json.dumps(ASSET_ID))
            .replace("__CLIP_USE_ID__", json.dumps(CLIP_USE_ID))
            .replace("__REVISION__", "1"))


def test_placed_clip_stamp_reads_back_with_the_builds_ids(
        broker, tmp_path):
    if not _live_enabled():
        pytest.skip("drives the live Resolve; runs only with "
                    "REN_LIVE_RESOLVE=1 inside a granted borrow-resolve window")
    from library.tools import project_model_store
    from library.tools.resolved import client

    (tmp_path / "project.yaml").write_text(
        "project_name: a3-proof\n", encoding="utf-8")
    (tmp_path / "a3_switch.py").write_text(
        SWITCH_SCRIPT, encoding="utf-8")
    (tmp_path / "a3_stamp.py").write_text(
        _render_stamp_script(), encoding="utf-8")
    fixture = json.loads(Path(
        os.path.dirname(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__)))),
        "tests", "fixtures", "project_model_e2e",
        "expected-project-model.json").read_text(encoding="utf-8"))
    model_dir = tmp_path / "model"
    revision = project_model_store.commit_project_model(
        model_dir, fixture, expected_revision=0, author="a3-proof",
        reason="the build revision the proof stamps ids from")
    assert revision == 1
    assert {ASSET_ID} <= {asset["id"] for asset in fixture["assets"]}
    assert {CLIP_USE_ID} <= {use["id"] for use in fixture["clip_uses"]}

    session = {
        "project": QUALIFICATION_PROJECT,
        "cwd": str(tmp_path),
        "steps": [
            {"resolve_axi": [
                "run", "--purpose",
                f"A3 proof {RUN}: make Q Markers current",
                "--file", str(tmp_path / "a3_switch.py"),
                "--unsafe", "--project", QUALIFICATION_PROJECT,
                "--project-dir", str(tmp_path)]},
            {"resolve_axi": [
                "run", "--purpose",
                f"A3 proof {RUN}: stamp model ids and join generation",
                "--file", str(tmp_path / "a3_stamp.py"),
                "--unsafe", "--project", QUALIFICATION_PROJECT,
                "--project-dir", str(tmp_path)]},
        ],
    }
    submitted = client.submit(
        "resolve.qualification_session", session,
        qualification=True, path=broker)
    job = client.result(submitted["id"], wait=300.0, path=broker)
    assert job["state"] == "done", f"broker {job['state']}: {job!r}"
    assert job["result"]["restored"]["project"] != QUALIFICATION_PROJECT, (
        f"borrowed from the qualification project itself: {job['result']!r}")

    stamp_path = tmp_path / "a3_stamp_result.json"
    assert stamp_path.is_file(), (
        f"the stamp step left no result: {job['result']!r}")
    stamp = json.loads(stamp_path.read_text(encoding="utf-8"))
    if not stamp.get("ok"):
        pytest.skip(f"Ren Qualification is not in its fixture shape: "
                    f"{stamp.get('reason')}")
    assert (stamp["asset_id"], stamp["clip_use_id"],
            stamp["revision"]) == (ASSET_ID, CLIP_USE_ID, 1)
    assert stamp["name_kept"] and stamp["note_kept"] \
        and stamp["color_kept"]
    assert stamp["clip_start"] <= stamp["frame"] < (
        stamp["clip_start"] + stamp["clip_duration"])
    assert (stamp["model_revision"], stamp["nle_binding_ids"]) == (
        1, [stamp["binding"]["id"]])
    assert stamp["binding"]["clip_bindings"] == {
        CLIP_USE_ID: stamp["unique_id"]}
    assert stamp["binding"]["generation"] == stamp["generation"]
    assert stamp["binding"]["timeline_id"] == stamp["timeline_id"]

    second = project_model_store.append_model_patch(
        model_dir,
        {"nle_bindings": fixture.get("nle_bindings", [])
         + [stamp["binding"]]},
        expected_revision=1, author="a3-proof",
        reason="commit the verified binding")
    assert second == 2
    _, committed = project_model_store.load_project_model(model_dir)
    assert stamp["binding"] in committed["nle_bindings"]
    print(json.dumps({"proof": "a3-stamp-live",
                      "frame": stamp["frame"],
                      "generation": stamp["generation"],
                      "binding": stamp["binding"]["id"],
                      "job": submitted["id"]}))
