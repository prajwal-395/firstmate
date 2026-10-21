"""An optional model that cannot run degrades the capability.

Measured 2026-10-22 (PROD-006): on the captain's 24 GiB box the OCR
recognizer and the SAM 2 weights are both optional models, and both can
be unable to load - not installed, no weights, or no memory for them.
The capability then has nothing to measure, which is a MISSING
MEASUREMENT, not a broken run.
"""


def test_an_unloadable_recognizer_degrades_the_ocr_batch(tmp_path,
                                                           monkeypatch,
                                                           capsys):
    """The warm-up is outside the per-clip guard, so a recognizer that
    cannot load failed the whole step with a stack trace - every clip
    needs the same reader, so one failure covers the batch."""
    from library.capabilities.ocr import extract as ocr_extract

    def refuse(self):
        raise ImportError("easyocr is not installed, so on-screen text "
                          "cannot be measured.")

    monkeypatch.setattr(ocr_extract.OCRExtractor, "_get_reader", refuse)
    result = ocr_extract.extract_ocr(
        raw_footage_files=["/nowhere/clip.mov"],
        project_folder=str(tmp_path))

    assert result == {"ocr_extraction": {}}
    assert "OCR unavailable" in capsys.readouterr().err


def test_a_recognizer_that_loads_still_measures(tmp_path, monkeypatch):
    """The degradation is the LOAD that fails, not the step: a reader
    that loads leaves the step measuring."""
    from library.capabilities.ocr import extract as ocr_extract

    seen = {}

    class _Reader:
        def readtext(self, frame):
            return []

    class _Extractor:
        def _get_reader(self):
            seen["loaded"] = True
            return _Reader()

        def extract_text_from_clip(self, clip_path, sample_fps=1.0,
                                   scene_boundaries=None, backend=None):
            raise AssertionError("the clip path is not reached in this "
                                 "test")

    monkeypatch.setattr(ocr_extract, "OCRExtractor", lambda: _Extractor())
    result = ocr_extract.extract_ocr(
        raw_footage_files=["/nowhere/clip.mov"],
        project_folder=str(tmp_path))

    assert seen.get("loaded") is True
    assert "ocr_extraction" in result
