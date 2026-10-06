"""M3 person builds run one lane per source on the bounded FREE lanes.

Digest dedup is decided serially before the lanes (a shared `seen`
set cannot decide across lanes), the Vision helper compiles once
serially (concurrent first-use compiles would race on the binary),
and the assembly restores catalog order. A wiring that silently
serializes must fail the overlap assertion here, not in production.
"""
import os
import threading
import time

from library.tools import person_measurements as pm
from library.tools import source_memory
from library.tools.concurrency_routing import lane_width


def _catalog(tmp_path):
    paths = {}
    for i, name in enumerate(("A", "B", "C", "D"), start=1):
        media = tmp_path / f"{name}.MOV"
        media.write_bytes(b"bytes-" + name.encode())
        paths[f"clip_{i:03d}"] = str(media)
    return paths


class TestPersonLanes:
    def test_rows_match_serial_contract_and_lanes_overlap(
            self, tmp_path, monkeypatch):
        paths = _catalog(tmp_path)
        catalog = ([{"clip_id": cid, "path": path}
                    for cid, path in paths.items()]
                   + [{"clip_id": "clip_005", "path": paths["clip_002"]},
                      {"clip_id": "clip_006",
                       "path": "/nonexistent/B.MOV"}])
        monkeypatch.setattr(source_memory, "load_catalog",
                            lambda project_folder: catalog)
        monkeypatch.setattr(source_memory, "load_recorded_fingerprints",
                            lambda project_folder: {})
        monkeypatch.setattr(
            source_memory, "digest_for_clip",
            lambda project_folder, clip, recorded: (
                "digest-" + os.path.basename(clip["path"]), "test"))

        warmed = []

        class StubVision:
            def ensure_helper(self):
                warmed.append(True)
                return ("stub-helper", None)

        monkeypatch.setattr(pm, "_vision", lambda: StubVision())

        spans = []
        spans_lock = threading.Lock()
        measured = []

        def fake_build(path, digest, root=None):
            if path == paths["clip_002"]:
                raise RuntimeError("no fresh M2")
            with spans_lock:
                measured.append(path)
            start = time.monotonic()
            time.sleep(0.2)
            with spans_lock:
                spans.append((start, time.monotonic()))
            return {"content_digest": digest, "frames": 1}

        monkeypatch.setattr(pm, "build_source_persons", fake_build)

        report = pm.build_project_persons(str(tmp_path))

        assert warmed == [True], "helper compile was not warmed once"
        rows = report["clips"]
        assert [r["clip_id"] for r in rows] == [
            "clip_001", "clip_002", "clip_003",
            "clip_004", "clip_005", "clip_006"]
        assert report["failed"] == ["clip_002"]
        assert rows[1]["failed"] == "RuntimeError: no fresh M2"
        # Same digest as clip_002's file: decided reused up front,
        # never measured - even though clip_002 itself failed.
        assert rows[4] == {"clip_id": "clip_005",
                           "source_file": paths["clip_002"],
                           "skipped": "reused"}
        assert rows[5]["skipped"] == "media-offline"
        assert sorted(measured) == sorted(
            [paths["clip_001"],
             paths["clip_003"], paths["clip_004"]])

        width = lane_width()
        peak, depth, events = 0, 0, sorted(
            [(s, 1) for s, _ in spans] + [(e, -1) for _, e in spans])
        for _, delta in events:
            depth += delta
            peak = max(peak, depth)
        assert peak >= min(2, width), (
            f"lanes never overlapped (peak {peak}, width {width})")
