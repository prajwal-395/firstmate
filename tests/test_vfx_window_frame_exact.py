"""Effect windows are frame-exact (finding 27): the legacy Fusion fallback
matches only a window WHOLLY inside one placed item. A planned comp that
reaches no timeline item fails naming the clip (finding 15). Plain fakes,
no Resolve writes. History: `docs/evidence/vfx_window_frame_exact.md`.
"""
from library.tools.execution.apply_fusion_comps import (  # noqa: E402
    legacy_vfx_effects,
    unmapped_comp_failures,
)


class _Item:
    def __init__(self, start, end):
        self._start = start
        self._end = end

    def GetStart(self):
        return self._start

    def GetEnd(self):
        return self._end


FPS = 30.0
# Finding 27's geometry: V1 block 3 ends at frame 768, the V2 block
# starts at 767, and the planned window is 25.576-30.576 s.
V1_ITEMS = [_Item(0, 768), _Item(768, 1500)]
V1_CLIPS = [{"label": "speech_3_seg0"}, {"label": "speech_4_seg0"}]
ORIG_TO_ITEM = {0: 0, 1: 1}


def _entry(start, end, effect="slow_zoom_in"):
    return {
        "effect_type": effect,
        "timeline_start": start,
        "timeline_end": end,
        "target_block_position": 4,
        "params": {"zoom_start": 1.0, "zoom_end": 1.04},
    }


def test_boundary_overlap_matches_no_neighbour():
    """The finding's exact window: 25.576 s is frame 767, inside V1:3
    ([0, 768)) by the start edge - but the window runs to frame 917,
    past its end. The old start-edge match drew the zoom on V1:3 as
    well as the b-roll; the frame-exact match draws it on neither
    V1 item (compile keys it to the b-roll by label)."""
    assert legacy_vfx_effects(
        [_entry(25.576, 30.576)], V1_CLIPS, V1_ITEMS, ORIG_TO_ITEM,
        FPS) == {}


def test_a_window_wholly_inside_one_item_still_matches():
    assert legacy_vfx_effects(
        [_entry(26.0, 28.0)], V1_CLIPS, V1_ITEMS, ORIG_TO_ITEM,
        FPS) == {"speech_4_seg0": {
            "_preset": "slow_zoom_in",
            "zoom_start": 1.0, "zoom_end": 1.04}}


def test_routed_entries_never_ride_the_legacy_path():
    """A speed ramp and a stabilization carry their own applicators;
    merging their params here built empty comps."""
    entries = [
        {"effect_type": "speed_ramp", "route": "native_resolve",
         "timeline_start": 26.0, "timeline_end": 28.0,
         "params": {"segments": []}},
        {"effect_type": "stabilize", "route": "neural_engine",
         "timeline_start": 26.0, "timeline_end": 28.0, "params": {}},
    ]
    assert legacy_vfx_effects(
        entries, V1_CLIPS, V1_ITEMS, ORIG_TO_ITEM, FPS) == {}


class _PoolItem:
    def __init__(self, path):
        self._path = path

    def GetClipProperty(self, name):
        return {"File Path": self._path}.get(name, "")


class _PlacedItem:
    def __init__(self, path):
        self._mpi = _PoolItem(path) if path is not None else None

    def GetMediaPoolItem(self):
        return self._mpi


def test_unmapped_planned_comp_fails_naming_the_clip():
    """Finding 15's FR3.3 shape: per_clip keys a comp onto
    speech_12_seg0, but the spec file matches no placed item - the
    failure names the clip, the spec file and what was placed,
    instead of the pass staying green."""
    comp_tracks = [(1, [{"label": "speech_12_seg0",
                        "source_file": "/footage/IMG_1812.MOV"}], True)]
    items_by_track = {1: [_PlacedItem("/other/IMG_1800.MOV")]}
    failures = unmapped_comp_failures(
        comp_tracks, items_by_track, {},
        {"speech_12_seg0": {"_preset": "slow_zoom_in",
                            "zoom_start": 1.0, "zoom_end": 1.1}})
    assert len(failures) == 1
    assert "speech_12_seg0" in failures[0]
    assert "IMG_1812.MOV" in failures[0]
    assert "no timeline item" in failures[0]


def test_mapped_comp_and_plain_clips_stay_quiet():
    """A spec whose file matches, and a plain clip carrying no comp
    intent, record nothing."""
    spec = {"label": "speech_12_seg0",
            "source_file": "/footage/IMG_1812.MOV"}
    plain = {"label": "speech_13_seg0",
             "source_file": "/footage/IMG_1813.MOV"}
    comp_tracks = [(1, [spec, plain], True)]
    items_by_track = {1: [_PlacedItem("/footage/IMG_1812.MOV"),
                          _PlacedItem("/footage/IMG_1813.MOV")]}
    assert unmapped_comp_failures(
        comp_tracks, items_by_track, {},
        {"speech_12_seg0": {"_preset": "slow_zoom_in"}}) == []
