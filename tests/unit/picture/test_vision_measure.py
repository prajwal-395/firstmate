"""Apple Vision measurement in step 1.04: fallback and phantom filter.

Covers `library/steps/step_1_04_temporal_index/vision_measure.py` - the
wrapper, not the Swift helper (which the eval measured directly). Each
test names the defect it would catch; there are no count, existence or
snapshot tests here.
"""
import json
import os
import shutil
import stat
import sys

import pytest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from library.steps.step_1_04_temporal_index import vision_measure


def _vision_only_env(monkeypatch, tmp_path):
    """A machine with no swiftc and an empty helper cache.

    `ensure_helper` consults the temp cache first: without redirecting
    it, a developer machine that once compiled the helper would never
    take the fallback path under test.
    """
    monkeypatch.setattr(shutil, "which", lambda *_, **__: None)
    monkeypatch.setattr(vision_measure.tempfile, "gettempdir",
                        lambda: str(tmp_path))


def test_failed_helper_is_a_fallback_with_all_keys_not_an_exception(
        monkeypatch, tmp_path):
    """A Vision outage must not break the step or its document shape.

    Would catch: `measure_clip_vision` raising (or returning a partial
    dict) when swiftc is missing, so a whole clip fails - or worse,
    downstream `KeyError` on `vision_faces` - on any machine without
    the helper. The fallback carries every owned key empty with the
    reason stamped, the same contract the face_presence tests pin.
    """
    _vision_only_env(monkeypatch, tmp_path)
    doc = vision_measure.measure_clip_vision("no-such-file.mp4", 854, 480)
    for key in vision_measure.VISION_KEYS:
        assert key in doc, f"fallback missing {key}"
    method = doc["vision_method"]
    assert method["engine"] == "none"
    assert str(method["fallback"]).startswith("unavailable:")


def test_working_helper_returns_measurements_not_fallback(tmp_path):
    """A runnable helper must be measured, never fallback-stamped.

    Would catch: the availability/parse path claiming `unavailable`
    despite a working helper (bad `ensure_helper` check, wrong argv,
    JSON shape drift) - the silent form of the fallback defect, where
    the step serves empty Vision documents on a capable machine. The
    fake helper below speaks the real `--list` protocol and returns one
    faced doc per input line; landmarks must survive pairing.
    """
    helper = tmp_path / "vision_helper"
    helper.write_text(
        "#!/usr/bin/env python3\n"
        "import json, sys\n"
        "paths = [l for l in open(sys.argv[sys.argv.index('--list') + 1])"
        ".read().splitlines() if l]\n"
        "docs = [{\"faces\": [{\"box\": [0.4, 0.2, 0.6, 0.5], "
        "\"confidence\": 0.9}], "
        "\"landmarks\": [{\"box\": [0.4, 0.2, 0.6, 0.5], "
        "\"outerLips\": [[0.48, 0.4], [0.52, 0.4]]}], "
        "\"bodies\": [], \"hands\": [], \"segmentation\": {}} "
        "for _ in paths]\n"
        "sys.stdout.write(json.dumps(docs) + \"\\n\")\n"
    )
    helper.chmod(helper.stat().st_mode | stat.S_IEXEC)
    docs = vision_measure.measure_frames(["a.jpg", "b.jpg"], str(helper))
    assert len(docs) == 2
    doc = vision_measure._assemble(docs, 5, "testsha", "854x480")
    assert doc["vision_method"]["engine"] == vision_measure.VISION_METHOD
    assert doc["vision_method"]["fallback"] is None
    assert len(doc["vision_faces"]["samples"]) == 2
    assert (doc["vision_faces"]["samples"][0][0]["landmarks"]
            ["outerLips"] == [[0.48, 0.4], [0.52, 0.4]])


def test_persistence_keeps_tracks_drops_only_isolated():
    """The phantom filter must not eat sequence ends or short tracks.

    Would catch: an off-by-one that consults only the previous sample
    (a face visible on the last two samples dies on the last one), or a
    threshold applied to confidence instead of temporal support (the
    eval proved confidence cannot separate phantoms). Synthetic boxes:
    track A spans samples 0-1, track B is samples 2-3 adjacent to A but
    disjoint, sample 4 holds an isolated phantom.
    """
    A = [0.10, 0.10, 0.30, 0.40]
    A2 = [0.11, 0.10, 0.31, 0.40]
    B = [0.60, 0.60, 0.80, 0.90]
    B2 = [0.61, 0.60, 0.81, 0.90]
    phantom = [0.40, 0.70, 0.50, 0.85]
    kept, removed = vision_measure.apply_persistence(
        [[A], [A2], [B], [B2], [phantom]])
    assert kept == [[True], [True], [True], [True], [False]]
    assert removed == [0, 0, 0, 0, 1]
    # A lone first sample with support only ahead still survives.
    kept, _ = vision_measure.apply_persistence([[A], [A2]])
    assert kept == [[True], [True]]
