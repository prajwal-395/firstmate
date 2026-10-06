"""OCR extraction runs its per-clip loop on the bounded FREE lanes.

One shared extractor serves every lane (its reader is lane-safe for
inference - measured separately), each lane owns its clip's output
subdir, and a clip the extractor cannot read is omitted while the
others land. A wiring that silently serializes must fail here: the
overlap assertion pins that at least two clips measure at once.
"""
import json
import os
import threading
import time

from library.tools.concurrency_routing import lane_width
from library.steps.step_1_07_ocr_extraction import step as ocr_step


class TestOcrLanes:
    def _stub_extractor(self, monkeypatch, fail_paths=(), calls=None,
                        warmed=None):
        spans = []
        lock = threading.Lock()

        class StubResult:
            def __init__(self, clip_id):
                self.clip_id = clip_id

            def save(self, out_subdir):
                os.makedirs(out_subdir, exist_ok=True)
                with open(os.path.join(out_subdir,
                                       "ocr_result.json"), "w",
                          encoding="utf-8") as fh:
                    json.dump({"stub_clip": self.clip_id}, fh)

        class StubExtractor:
            def _get_reader(self):
                if warmed is not None:
                    warmed.append(True)

            def extract_text_from_clip(self, path, sample_fps=1.0,
                                       scene_boundaries=None):
                if path in fail_paths:
                    raise RuntimeError("unreadable frames")
                if calls is not None:
                    calls.append(path)
                start = time.monotonic()
                time.sleep(0.2)
                with lock:
                    spans.append((start, time.monotonic()))
                name = os.path.splitext(os.path.basename(path))[0]
                return StubResult(name)

        monkeypatch.setattr(ocr_step, "OCRExtractor", StubExtractor)
        return spans

    def _clips(self, tmp_path, n=4):
        return [{"path": str(tmp_path / f"C{i:03d}.mov"),
                 "clip_id": f"clip_{i:03d}"} for i in range(1, n + 1)]

    def test_rows_match_serial_contract_and_lanes_overlap(
            self, tmp_path, monkeypatch):
        from library.tools.project_layout import ProjectLayout
        ProjectLayout(str(tmp_path / "proj")).ensure()
        project = str(tmp_path / "proj")
        clips = self._clips(tmp_path)
        fail = clips[1]["path"]

        spans = self._stub_extractor(monkeypatch, fail_paths={fail})
        result = ocr_step.extract_ocr(clips, project)

        docs = result["ocr_extraction"]
        assert list(docs) == ["clip_001", "clip_003", "clip_004"]
        assert docs["clip_001"] == {"stub_clip": "C001"}

        width = lane_width()
        peak, depth, events = 0, 0, sorted(
            [(s, 1) for s, _ in spans] + [(e, -1) for _, e in spans])
        for _, delta in events:
            depth += delta
            peak = max(peak, depth)
        assert peak >= min(2, width), (
            f"lanes never overlapped (peak {peak}, width {width})")

    def test_fully_cached_run_measures_nothing(
            self, tmp_path, monkeypatch):
        from library.tools.project_layout import ProjectLayout
        ProjectLayout(str(tmp_path / "proj")).ensure()
        project = str(tmp_path / "proj")
        clips = self._clips(tmp_path, n=2)

        self._stub_extractor(monkeypatch)
        first = ocr_step.extract_ocr(clips, project)
        assert list(first["ocr_extraction"]) == ["clip_001", "clip_002"]

        calls, warmed = [], []
        self._stub_extractor(monkeypatch, calls=calls, warmed=warmed)
        second = ocr_step.extract_ocr(clips, project)
        assert second == first
        assert calls == [], "cached run re-measured clips"
        assert warmed == [], "cached run loaded the model"
