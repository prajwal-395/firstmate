"""The Fusion bank must hand back THIS build's comp, never a sibling's.

Measured on the captain's `Reel 09 - your-website-is-only-20-percent
(final)`, built 2026-09-10 11:45.  Its last picture clip carried the
repaired old-TV switch-off (`PowerBand1` - a masked black `Background`);
its FIRST picture clip carried the pre-repair switch-on (`PowerCrop1`, a
`Crop` driven through `CropTop`/`CropBottom`, names `Crop` does not have,
so the tool fell to its 1920x1080 registry defaults at offset (0, 0) and
cut a 3840x2160 source down to its bottom-left quadrant).  One timeline,
two builders.

Nothing was stale on disk about the manifest, the plan or the checkout.
The bank was keyed over the INPUTS a comp was built from and not over the
builder, so the head clip - whose inputs had not moved across the repair -
hit a comp banked at 22:07 the previous evening and imported it verbatim.
The tail's `source_in` had shifted by 2.25 s, so the tail alone missed
the bank and got the repaired recipe.

These tests pin the property that ends it: what reaches the timeline is
the comp `build_effect_comp` emits on this run, whatever the bank holds.
"""
import hashlib
import json
import pathlib

import pytest

from library.tools.custom_asset_bank import (
    bank_comp,
    comp_asset_key,
    get_asset_bank_dir,
    save_custom_asset,
)
from library.tools.fusion.comp_builder import build_effect_comp

STALE = "Composition { Version = \"a sibling build left this here\" }\n"


def _manifest():
    """One picture clip with a drift - the shape the reels ship."""
    return {
        "tracks": {"V1": {"clips": [
            {"source_file": "a_roll.mov", "label": "clip_0",
             "source_in": 12.0, "source_out": 15.0},
        ]}},
        "fusion_effects": {"per_clip": {
            "clip_0": {"_preset": "slow_zoom_in",
                       "zoom_start": 1.0, "zoom_end": 1.03},
        }},
    }


def _mock_resolve(monkeypatch, imported):
    """Resolve, reduced to the one call this asks about: what was imported."""
    import library.tools.execution.apply_fusion_comps as afc

    class MockClip:
        def GetStart(self): return 0
        def GetEnd(self): return 72
        def GetDuration(self): return 72
        def GetMediaPoolItem(self): return MockPool()
        def GetFusionCompNameList(self): return []
        def DeleteFusionCompByName(self, name): pass

        def ImportFusionComp(self, path):
            # Read it here, as Resolve does: what reached the timeline
            # is the bytes at import time, not the path afterwards.
            imported.append(
                pathlib.Path(path).read_text(encoding="utf-8"))
            return True

    class MockPool:
        def GetClipProperty(self, prop):
            if prop == "File Path":
                return "a_roll.mov"
            if prop == "Frames":
                return "600"
            return None

    class MockTimeline:
        def GetSetting(self, name): return "30"
        def GetItemListInTrack(self, track_type, index):
            return [MockClip()] if index == 1 else []

    class MockProject:
        def GetCurrentTimeline(self): return MockTimeline()

    class MockPM:
        def GetCurrentProject(self): return MockProject()

    class MockResolve:
        def GetProjectManager(self): return MockPM()

    monkeypatch.setattr(afc.dvr, "scriptapp", lambda x: MockResolve())
    return afc


def _legacy_key(label, effects, clip_dur, source_res, played_frames):
    """The key the bank used until 2026-09-10, restated here.

    It is restated rather than imported because the point of this file is
    that it is gone: a test that called the live function would pass
    again the moment somebody reintroduced it.
    """
    fingerprint = json.dumps(
        {"effects": effects, "clip_dur": clip_dur,
         "source_res": list(source_res) if source_res else None,
         "played_frames": played_frames},
        sort_keys=True, default=str,
    )
    return f"{label.lower()}_{hashlib.sha1(fingerprint.encode()).hexdigest()[:12]}"


def test_a_comp_banked_under_the_old_key_never_reaches_the_timeline(
        monkeypatch, tmp_path):
    """Reel 09's defect, on the path that shipped it.

    A stale comp sits in the bank under every name the previous scheme
    could have chosen for this clip.  The build must still import what it
    generated.
    """
    imported = []
    afc = _mock_resolve(monkeypatch, imported)

    effects = dict(_manifest()["fusion_effects"]["per_clip"]["clip_0"])
    # The applier normalises before keying, and the old key was taken
    # after that; plant every plausible spelling rather than guess which.
    for res in (None, (1920, 1080), (3840, 2160)):
        for played in (None, 72):
            save_custom_asset(
                str(tmp_path),
                _legacy_key("clip_0", effects, 600, res, played),
                STALE)
    planted = list(pathlib.Path(get_asset_bank_dir(str(tmp_path))).glob("*.comp"))
    assert planted, "the stale comps must actually be on disk"

    assert afc.apply_fusion_comps(_manifest(), str(tmp_path),
                                  step_id="build_reels") is True

    assert len(imported) == 1
    body = imported[0]
    assert STALE not in body
    assert "Transform1 = Transform {" in body, (
        "the drift this build declared has to be in what was imported"
    )


def test_the_imported_comp_is_byte_for_byte_what_this_build_generated(
        monkeypatch, tmp_path):
    """Not "equivalent to" - the same bytes `build_effect_comp` emitted."""
    imported = []
    afc = _mock_resolve(monkeypatch, imported)
    assert afc.apply_fusion_comps(_manifest(), str(tmp_path),
                                  step_id="build_reels") is True

    banked = sorted(pathlib.Path(get_asset_bank_dir(str(tmp_path))).glob("*.comp"))
    assert len(banked) == 1
    body = imported[0]
    assert body == banked[0].read_text(encoding="utf-8")
    assert banked[0].name == comp_asset_key("clip_0", body) + ".comp"
    # And it carries the drift this manifest declared, out to 1.03.
    assert "Transform1Size = BezierSpline {" in body
    assert "1.03" in body


def test_bank_comp_refuses_to_hand_back_bytes_it_was_not_given(tmp_path):
    """A file squatting on the key is overwritten, not trusted.

    Only a hash collision or a corrupted bank puts different bytes at a
    content-addressed name, and neither is a reason to import them.
    """
    fresh = build_effect_comp({"vignette": True, "vignette_blend": 0.25,
                            "vignette_soft": 0.35}, 120, (1920, 1080))
    key = comp_asset_key("clip_0", fresh)
    save_custom_asset(str(tmp_path), key, STALE)

    path, reused = bank_comp(str(tmp_path), "clip_0", fresh)
    assert reused is False
    assert pathlib.Path(path).read_text(encoding="utf-8") == fresh


def test_a_second_build_of_the_same_comp_reuses_the_banked_file(tmp_path):
    """The bank still dedupes - it just cannot lie about what it holds."""
    fresh = build_effect_comp({"vignette": True, "vignette_blend": 0.25,
                            "vignette_soft": 0.35}, 120, (1920, 1080))
    first, reused_first = bank_comp(str(tmp_path), "clip_0", fresh)
    second, reused_second = bank_comp(str(tmp_path), "clip_0", fresh)
    assert (reused_first, reused_second) == (False, True)
    assert first == second


def test_build_effect_comp_is_deterministic():
    """Content addressing is only a key if the same request is the same
    bytes; a timestamp or an unseeded random in the builder would make
    every run a miss and the bank an ever-growing pile."""
    args = ({"_preset": "slow_zoom_in", "zoom_start": 1.0, "zoom_end": 1.03},
            600, (3840, 2160))
    assert build_effect_comp(dict(args[0]), args[1], args[2]) == \
        build_effect_comp(dict(args[0]), args[1], args[2])


def test_the_old_input_only_key_is_gone():
    """`clip_asset_key` fingerprinted the request, not the answer.

    It was repaired twice by adding a field (`source_res`,
    `played_frames`) and both repairs left the trap armed, because the
    builder is not a field.  Reintroducing it under any name that keys a
    bank lookup reopens Reel 09.
    """
    import library.tools.custom_asset_bank as bank

    assert not hasattr(bank, "clip_asset_key")
