"""OWLv2 releases per-image MPS allocations while retaining the loaded model."""

import sys
from contextlib import nullcontext
from types import SimpleNamespace

from library.tools.analysis import open_vocabulary_detect as ovd


def test_owlv2_processor_uses_memory_safe_image_size():
    processor = SimpleNamespace(image_processor=SimpleNamespace(
        size=SimpleNamespace(height=960, width=960)))

    ovd._set_owlv2_input_size(processor)

    assert processor.image_processor.size.height == 960
    assert processor.image_processor.size.width == 960


def test_owlv2_clears_per_image_mps_cache_after_each_detection(monkeypatch):
    class FakeMPS:
        cached_bytes = 0
        clear_calls = 0

        def empty_cache(self):
            self.cached_bytes = 0
            self.clear_calls += 1

    fake_torch = type("FakeTorch", (), {})()
    fake_torch.mps = FakeMPS()
    fake_torch.mps.synchronize = lambda: None
    monkeypatch.setitem(sys.modules, "torch", fake_torch)
    monkeypatch.setattr(ovd, "load_detector", lambda _name: {
        "processor": object(), "model": object(), "device": "mps",
    })

    calls = 0

    def fake_infer(*_args):
        nonlocal calls
        # Simulate a cached allocation per image. If the production finally
        # stops clearing it, this bound trips after a few iterations.
        assert fake_torch.mps.cached_bytes == 0
        fake_torch.mps.cached_bytes += 256 * 1024 * 1024
        calls += 1
        return [ovd.Detection("shoe", [0.1, 0.2, 0.8, 0.9], 0.8,
                              detector=ovd.OWLV2)]

    monkeypatch.setattr(ovd, "_owlv2_infer", fake_infer)
    for _ in range(60):
        rows = ovd._detect_owlv2("unused.jpg", ["shoe"], 0.1)
        assert rows[0].box == [0.1, 0.2, 0.8, 0.9]

    assert calls == 60
    assert fake_torch.mps.clear_calls == 60
    assert fake_torch.mps.cached_bytes == 0


def test_owlv2_bounds_text_query_batch_and_releases_between_batches(
        monkeypatch):
    class FakeMPS:
        cached_bytes = 0
        clear_calls = 0

        def empty_cache(self):
            self.cached_bytes = 0
            self.clear_calls += 1

    fake_torch = type("FakeTorch", (), {})()
    fake_torch.mps = FakeMPS()
    sync_calls = []
    fake_torch.mps.synchronize = lambda: sync_calls.append(True)
    fake_torch.no_grad = nullcontext
    monkeypatch.setitem(sys.modules, "torch", fake_torch)

    class FakeImage:
        size = (3840, 1898)

        def thumbnail(self, bounds):
            assert bounds == (960, 960)
            self.size = (960, 475)

    image = FakeImage()
    monkeypatch.setattr(ovd, "_read_image",
                        lambda _path: (image, (3840, 1898)))

    class FakeProcessor:
        def __init__(self):
            self.batches = []

        def __call__(self, *, text, padding, max_length, **_kwargs):
            phrases = text[0]
            assert len(phrases) == ovd.OWLV2_QUERY_BATCH_SIZE
            assert padding == "max_length"
            assert max_length == ovd.OWLV2_MAX_TEXT_LENGTH
            assert fake_torch.mps.cached_bytes == 0
            self.batches.append(phrases)
            return {"phrases": phrases,
                    "input_shape": (len(phrases), max_length)}

        def post_process_grounded_object_detection(self, *_args, **_kwargs):
            self.target_sizes = _kwargs["target_sizes"]
            phrases = self.batches[-1]
            if phrases[-1] == ovd.OWLV2_PADDING_QUERY:
                boxes = [FakeBox([1, 2, 10, 15]),
                         FakeBox([2, 3, 12, 16])]
                scores, labels = [0.9, 0.8], [0, 3]
                texts = [phrases[0], ovd.OWLV2_PADDING_QUERY]
            else:
                boxes = [FakeBox([1, 2, 10, 15])]
                scores, labels, texts = [0.9], [0], [phrases[0]]
            return [{"boxes": boxes, "scores": scores, "labels": labels,
                     "text_labels": texts}]

    class FakeBox:
        def __init__(self, values):
            self.values = values

        def detach(self):
            return self

        def cpu(self):
            return self

        def tolist(self):
            return self.values

    class FakeModel:
        def __call__(self, **kwargs):
            assert fake_torch.mps.cached_bytes == 0
            assert kwargs["interpolate_pos_encoding"] is False
            assert kwargs["input_shape"] == (4, 16)
            fake_torch.mps.cached_bytes = 128 * 1024 * 1024
            return object()

    processor = FakeProcessor()
    rows = ovd._owlv2_infer(processor, FakeModel(), "mps", "unused.jpg",
                            [f"query {i}" for i in range(10)], 0.1)

    assert [row.phrase for row in rows] == ["query 0", "query 4", "query 8"]
    assert [len(batch) for batch in processor.batches] == [4, 4, 4]
    assert processor.batches[-1][-2:] == [
        ovd.OWLV2_PADDING_QUERY, ovd.OWLV2_PADDING_QUERY]
    assert fake_torch.mps.clear_calls == 3
    assert len(sync_calls) == 3
    assert fake_torch.mps.cached_bytes == 0
    assert image.size == (960, 475)
    assert processor.target_sizes == [(1898, 3840)]
