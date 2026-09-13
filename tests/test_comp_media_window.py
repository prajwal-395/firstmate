"""A comp whose MediaIn does not cover its item's played frames REFUSES.

The measurement these tests pin is in `library/tools/comp_media_window.py`:
six of the captain's eight built reels carried a freeze tail whose comp
is rendered over comp frames 0..18 while its `MediaIn` begins at comp
frame 1, and every one of them FAILED a Deliver render at the hold's
first frame. Conforming the window to 0/18 rendered 19 of 19.

The numbers below are those reels' real numbers, not invented ones.
"""

import pathlib

import pytest

from library.tools import comp_media_window as window

#: What the captain's built reels carry on the freeze tail, and what a
#: freshly imported comp of the same file reads back.
DRIFTED = {"MediaSource": "Timeline", "MediaID": "",
           "AudioTrack": "Timeline Audio", "GlobalIn": 1.0, "GlobalOut": 19.0,
           "ClipTimeStart": 0.0, "ClipTimeEnd": 18.0}
CONFORMED = dict(DRIFTED, GlobalIn=0.0, GlobalOut=18.0)
FREEZE_FRAMES = 19


# ── The predicate ───────────────────────────────────────────────────


def test_the_conformed_freeze_window_covers_its_nineteen_frames():
    assert window.uncovered_reason(CONFORMED, FREEZE_FRAMES) is None
    assert window.covers(CONFORMED, FREEZE_FRAMES)


def test_the_shipped_freeze_window_does_not_cover_its_first_frame():
    reason = window.uncovered_reason(DRIFTED, FREEZE_FRAMES)
    assert reason is not None
    assert "GlobalIn 1" in reason
    assert not window.covers(DRIFTED, FREEZE_FRAMES)


def test_a_window_that_stops_short_names_the_frames_it_leaves_bare():
    short = dict(CONFORMED, GlobalOut=10.0)
    reason = window.uncovered_reason(short, FREEZE_FRAMES)
    assert reason is not None and "GlobalOut 10" in reason


def test_a_long_clip_whose_window_starts_far_negative_is_covered():
    """Reel 01's head clip: left offset 3151 of a 5400-frame source.

    `GlobalIn` is well before comp frame 0 and `GlobalOut` well past the
    479 frames it plays. This is the shape the predicate must NOT flag,
    or it fails every correct A-roll clip on every reel.
    """
    a_roll = dict(CONFORMED, GlobalIn=-3151.0, GlobalOut=2248.0,
                  ClipTimeStart=-3151.0, ClipTimeEnd=2248.0)
    assert window.uncovered_reason(a_roll, 479) is None


def test_an_unreadable_window_is_not_a_finding():
    """A handle that would not answer is an absence, not a defect.

    AGENTS.md 10.4: a gate that FAILS correct output is no more coverage
    than one that cannot fail. `conform_item` reports the absence
    instead.
    """
    assert window.uncovered_reason(None, FREEZE_FRAMES) is None
    assert window.uncovered_reason({"GlobalIn": None, "GlobalOut": None},
                                   FREEZE_FRAMES) is None


# ── The conform: read, compare, repair, verify ──────────────────────


class _Tool:
    def __init__(self, values):
        self.values = values

    def GetAttrs(self, key):
        return "MediaIn" if key == "TOOLS_RegID" else None

    def GetInput(self, key):
        return self.values.get(key)


class _Comp:
    def __init__(self, values):
        self.tool = _Tool(values)

    def GetToolList(self, selected):
        return {1: self.tool}


class _Item:
    """A timeline item whose comp window is whatever the last import left.

    `repairs` is the window each successive `ImportFusionComp` produces,
    so a test can model the real repair (a re-import conforms it) and the
    one that must refuse (a re-import that changes nothing).
    """

    def __init__(self, windows):
        self.windows = list(windows)
        self.imported = []
        self.deleted = []

    def GetFusionCompByIndex(self, index):
        return _Comp(self.windows[0]) if self.windows[0] else None

    def GetFusionCompCount(self):
        return 1

    def GetFusionCompNameList(self):
        return ["Fusion Composition 1"]

    def DeleteFusionCompByName(self, name):
        self.deleted.append(name)

    def ImportFusionComp(self, path):
        self.imported.append(path)
        if len(self.windows) > 1:
            self.windows.pop(0)
        return True


def test_a_covered_window_is_left_alone():
    item = _Item([CONFORMED])
    receipt = window.conform_item(item, FREEZE_FRAMES, "banked.comp",
                                  label="freeze")
    assert receipt["repaired"] is False
    assert item.imported == [], "nothing to repair, so nothing was written"


def test_a_drifted_window_is_repaired_by_re_importing_the_banked_comp():
    item = _Item([DRIFTED, CONFORMED])
    receipt = window.conform_item(item, FREEZE_FRAMES, "banked.comp",
                                  label="freeze")
    assert receipt["repaired"] is True
    assert item.imported == ["banked.comp"]
    assert receipt["before"]["GlobalIn"] == 1.0
    assert receipt["after"]["GlobalIn"] == 0.0
    assert receipt["reason_after"] is None


def test_a_repair_that_did_not_take_REFUSES_rather_than_shipping():
    """The verify is a re-read, and it is allowed to fail.

    A conform that trusted its own repair would promote exactly the reel
    this module exists to stop.
    """
    item = _Item([DRIFTED])
    with pytest.raises(window.CompWindowUncovered) as caught:
        window.conform_item(item, FREEZE_FRAMES, "banked.comp",
                            label="Reel 01 freeze")
    assert "Reel 01 freeze" in str(caught.value)
    assert "GlobalIn 1" in str(caught.value)


def test_an_unreadable_window_is_reported_and_not_repaired():
    item = _Item([None])
    receipt = window.conform_item(item, FREEZE_FRAMES, "banked.comp",
                                  label="freeze")
    assert receipt["unreadable"] is True
    assert receipt["repaired"] is False
    assert item.imported == []


# ── The diagnosis half: reading a built reel without rendering it ───


def test_reel_read_reports_the_uncovered_window_as_a_slice():
    """The reader owns the read (AGENTS.md 15); this module owns the law.

    `reel_read` already walks every clip on every track, so the
    diagnosis is a SLICE of that one read rather than a second walk -
    which is the rule `tests/test_reel_read.py` enforces.
    """
    from library.tools import reel_read

    result = {"tracks": [{"clips": [
        {"name": "LC4930.MXF", "track_type": "video", "track_index": 1,
         "record_in": 590, "record_out": 1069, "duration": 479,
         "fusion": {"comp_count": 1, "comp_names": ["c"], "media_windows": [
             {"comp_index": 1,
              "window": dict(CONFORMED, GlobalIn=-3151.0, GlobalOut=2248.0),
              "uncovered_reason": None}]}},
        {"name": "reel_freeze_6681969f12.mov", "track_type": "video",
         "track_index": 1, "record_in": 1255, "record_out": 1274,
         "duration": 19,
         "fusion": {"comp_count": 1, "comp_names": ["c"], "media_windows": [
             {"comp_index": 1, "window": DRIFTED,
              "uncovered_reason": window.uncovered_reason(
                  DRIFTED, FREEZE_FRAMES)}]}},
    ]}]}
    rows = reel_read.uncovered_comp_windows(result)
    assert len(rows) == 1
    assert rows[0]["clip"] == "reel_freeze_6681969f12.mov"
    assert rows[0]["record_in"] == 1255
    assert "GlobalIn 1" in rows[0]["uncovered_reason"]


# ── The wiring: the comp pass conforms what it imports ──────────────


def _manifest():
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


def _drive_comp_pass(monkeypatch, tmp_path, after_import):
    """Run the real comp pass against a clip whose window is what the LAST
    `ImportFusionComp` left behind - `after_import[0]` after the pass's own
    import, `after_import[1]` after the conform's repair."""
    import library.tools.execution.apply_fusion_comps as afc

    state = {"windows": [None], "pending": list(after_import), "imports": []}

    class MockClip:
        def GetStart(self): return 0
        def GetEnd(self): return 72
        def GetDuration(self): return 72
        def GetMediaPoolItem(self): return MockPool()
        def GetFusionCompNameList(self): return []
        def DeleteFusionCompByName(self, name): pass
        def GetFusionCompCount(self): return 1

        def GetFusionCompByIndex(self, index):
            return _Comp(state["windows"][0]) if state["windows"][0] else None

        def GetFusionCompByName(self, name):
            return None

        def ImportFusionComp(self, path):
            state["imports"].append(pathlib.Path(path).name)
            if state["pending"]:
                state["windows"][0] = state["pending"].pop(0)
            return True

    class MockPool:
        def GetClipProperty(self, prop):
            if prop == "Resolution":
                # A real MediaPoolItem states its stored frame; the
                # applier refuses a comp where Resolve will not state
                # one, so the mock states one like production does.
                return "1080x1920"
            return {"File Path": "a_roll.mov", "Frames": "600"}.get(prop)

    class MockTimeline:
        def GetSetting(self, name): return "30"
        def GetItemListInTrack(self, kind, index):
            return [MockClip()] if index == 1 else []

    class MockProject:
        def GetCurrentTimeline(self): return MockTimeline()

    class MockPM:
        def GetCurrentProject(self): return MockProject()

    class MockResolve:
        def GetProjectManager(self): return MockPM()
        def OpenPage(self, page): return True

    monkeypatch.setattr(afc.dvr, "scriptapp", lambda name: MockResolve())
    return afc, state


def test_the_comp_pass_conforms_a_drifted_window_it_just_imported(
        monkeypatch, tmp_path):
    """Remove the conform from `apply_fusion_comps` and this test fails.

    The clip plays 72 frames and its freshly imported comp comes back
    covering 1..72 - the shipped freeze tail's defect, on the pass that
    writes it. One re-import conforms it, and the pass must make it.
    """
    drifted = dict(DRIFTED, GlobalIn=1.0, GlobalOut=72.0)
    conformed = dict(DRIFTED, GlobalIn=0.0, GlobalOut=71.0)
    afc, state = _drive_comp_pass(monkeypatch, tmp_path,
                                  [drifted, conformed])

    assert afc.apply_fusion_comps(_manifest(), str(tmp_path),
                                  step_id="build_reels") is True
    assert len(state["imports"]) == 2, (
        "the pass imported once and never read the window back - a comp "
        "whose MediaIn misses the frames its item plays fails the whole "
        "render job")


def test_the_comp_pass_REFUSES_when_the_window_will_not_conform(
        monkeypatch, tmp_path):
    drifted = dict(DRIFTED, GlobalIn=1.0, GlobalOut=72.0)
    afc, _state = _drive_comp_pass(monkeypatch, tmp_path, [drifted])
    with pytest.raises(window.CompWindowUncovered):
        afc.apply_fusion_comps(_manifest(), str(tmp_path),
                               step_id="build_reels")
