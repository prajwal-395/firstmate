"""QA-005: an approved shot's look must survive a revision.

The defect these name is the one Reel 26 carried on 2026-10-20: the
closing shot's Fusion comp was authored on the SOURCE canvas
(3840x2160) while every other comp in the reel was authored on the
DELIVERY canvas (1080x1920), so its vignette and glow landed on a
different picture and the shot read flatter and cooler than the shot
beside it.  PR 3009 re-applied the declared look, the shot moved, and
PR 3079 had to restore the version the captain preferred.

Three things had to be true for the engine to be blind to it, and each
is a defect with a test here:

- the recorded state of a shot did not carry the CANVAS its comp was
  authored on, so two comps with the same names read EQUAL;
- the look of a shot was not compared under the shot's EDITORIAL
  identity, so a re-placed shot (a new Resolve id) started with an
  empty look and nothing compared it with the shot it replaced;
- nothing compared the DELIVERED pixels of a shot before a revision
  with the same shot after it.
"""

from __future__ import annotations

import pytest

from library.tools import look_preservation as look
from library.tools import preservation_invariant

# ── The recorded state, in the shape `reel_read` writes it ──────────────

def _clip(uid="uid-1", *, name="shot", media="media-1", row=1,
          record_in=0, record_out=48, source_in=0, source_out=48,
          transform=None, cdl=None, node_count=1, luts=None, nodes=None,
          canvas=(1080, 1920), comps=None, keyframes=None,
          track_type="video"):
    """One recorded clip, in the shape `reel_read.clip_detail` writes."""
    if comps is None:
        comps = [{
            "comp_index": 1,
            "name": "MediaIn1",
            "tools": ["Background", "EllipseMask", "Merge", "MediaIn1",
                      "MediaOut1"],
            "canvas": ({"width": canvas[0], "height": canvas[1]}
                       if canvas else None),
        }]
    return {
        "unique_id": uid,
        "name": name,
        "item_type": "video",
        "track_type": track_type,
        "track_index": row,
        "track_name": "V1" if track_type == "video" else "A1",
        "record_in": record_in,
        "record_out": record_out,
        "duration": record_out - record_in,
        "source_in_frame": source_in,
        "source_out_frame": source_out,
        "source_file": f"/footage/{name}.mov",
        "media_type": "video",
        "media_pool_item_id": media,
        "enabled": True,
        "transform": transform if transform is not None else {
            "Pan": 0.0, "Tilt": 0.25, "ZoomX": 1.0, "ZoomY": 1.0,
            "ZoomGang": 1.0, "RotationAngle": 0.0,
            "AnchorPointX": 0.5, "AnchorPointY": 0.5,
        },
        "keyframes": keyframes if keyframes is not None else {},
        "fusion": {
            "comp_count": 1,
            "comp_names": ["MediaIn1"],
            "comps": comps,
            "media_windows": [],
            "mattes": [],
        },
        "color": {
            "cdl": cdl if cdl is not None else {
                "NodeIndex": 1, "Slope": [1.0, 1.0, 1.0],
                "Offset": [0.0, 0.0, 0.0], "Power": [1.0, 1.0, 1.0],
                "Saturation": 1.0,
            },
            "color_group": "",
            "node_count": node_count,
            "luts": luts if luts is not None else [],
            "nodes": nodes if nodes is not None else [
                {"node": 1, "label": "Node 1", "group": None}],
        },
        "markers": [],
    }


def _snapshot(*clips, width=1080, height=1920):
    return {
        "timeline": "Reel 26 - write for the question your customer ask",
        "project": "Podcast",
        "width": width,
        "height": height,
        "tracks": [{"type": "video", "index": 1, "name": "V1",
                    "clips": list(clips)}],
        "markers": {"timeline": [], "notes": []},
    }


def _scope(*operations):
    return preservation_invariant.scope_for(list(operations))


# ── The state half ──────────────────────────────────────────────────────

def test_the_same_look_read_twice_is_preserved():
    """A look that did not move is preserved, and says nothing."""
    snapshot = _snapshot(_clip())

    verdict = look.compare_look(look.capture_look(snapshot),
                                 look.capture_look(snapshot))

    assert verdict["status"] == look.PRESERVED
    assert verdict["findings"] == []


def test_a_comp_authored_on_another_canvas_is_a_change():
    """The Reel 26 fingerprint: the same comp, drawn on another canvas.

    Two comps with the same names read EQUAL until the engine recorded
    the canvas each was authored on - and the canvas is what moved.
    """
    before = _snapshot(_clip(canvas=(1080, 1920)))
    after = _snapshot(_clip(canvas=(3840, 2160)))

    verdict = look.compare_look(
        look.capture_look(before), look.capture_look(after),
        scope=_scope({"op": "clip.trim", "unique_id": "uid-1"}))

    assert verdict["status"] == look.VIOLATED
    fields = [row["field"] for row in verdict["findings"]]
    assert any("canvas" in field for field in fields), fields
    assert all(row["domain"] == "fusion" for row in verdict["findings"])


def test_a_grade_that_moved_outside_the_authorized_delta_is_violated():
    """A grade that moved where nothing authorized it is a violation."""
    before = _snapshot(_clip(cdl={"NodeIndex": 1, "Slope": [1.0, 1.0, 1.0],
                                  "Offset": [0.0, 0.0, 0.0],
                                  "Power": [1.0, 1.0, 1.0],
                                  "Saturation": 1.0}))
    after = _snapshot(_clip(cdl={"NodeIndex": 1, "Slope": [1.2, 1.0, 1.0],
                                 "Offset": [0.0, 0.0, 0.0],
                                 "Power": [1.0, 1.0, 1.0],
                                 "Saturation": 1.0}))

    unauthorized = look.compare_look(
        look.capture_look(before), look.capture_look(after),
        scope=_scope({"op": "clip.trim", "unique_id": "uid-1"}))
    authorized = look.compare_look(
        look.capture_look(before), look.capture_look(after),
        scope=_scope({"op": "grade", "unique_id": "uid-1"}))

    assert unauthorized["status"] == look.VIOLATED
    assert [row["domain"] for row in unauthorized["findings"]] == ["color"]
    assert authorized["status"] == look.PRESERVED


def test_a_look_change_with_no_authorized_delta_is_unprovable():
    """A difference nobody can judge against is not a pass."""
    before = _snapshot(_clip())
    after = _snapshot(_clip(canvas=(3840, 2160)))

    verdict = look.compare_look(look.capture_look(before),
                                 look.capture_look(after))

    assert verdict["status"] == look.UNPROVABLE
    assert verdict["findings"]


def test_a_shot_that_was_replaced_is_compared_with_the_shot_it_replaced():
    """A re-place gives the shot a new Resolve id and keeps its moment.

    The look of the moment is what is compared, so a replacement that
    dropped the grade is a finding - not a new shot with an empty look
    that nothing compares.
    """
    before = _snapshot(_clip(uid="uid-old", media="media-7"))
    after = _snapshot(_clip(uid="uid-new", media="media-7",
                             cdl={"NodeIndex": 1,
                                  "Slope": [0.8, 0.8, 0.8],
                                  "Offset": [0.0, 0.0, 0.0],
                                  "Power": [1.0, 1.0, 1.0],
                                  "Saturation": 1.0}))

    verdict = look.compare_look(look.capture_look(before),
                                 look.capture_look(after))

    assert verdict["shots_compared"] == 1
    assert verdict["status"] == look.UNPROVABLE
    assert {row["domain"] for row in verdict["findings"]} == {"color"}
    assert all(row["field"].startswith("grade.cdl")
               for row in verdict["findings"])


def test_a_field_only_one_recording_carries_is_not_a_change():
    """A recording that does not carry a field says nothing about it.

    An older snapshot without the comp canvas must not read as "the
    canvas changed" - that is absent evidence, and a change that was
    never observed.
    """
    before_clip = _clip()
    before_clip["fusion"].pop("comps")
    before = _snapshot(before_clip)
    after = _snapshot(_clip())

    verdict = look.compare_look(look.capture_look(before),
                                 look.capture_look(after))

    assert verdict["findings"] == []
    assert verdict["status"] == look.PRESERVED


def test_a_keyframe_that_moved_is_a_change():
    """A keyframe is part of the look, and it moved."""
    before = _snapshot(_clip(keyframes={
        "Pan": [{"frame": 0, "value": 0.0, "interpolation": "linear"}]}))
    after = _snapshot(_clip(keyframes={
        "Pan": [{"frame": 0, "value": 12.0, "interpolation": "linear"}]}))

    verdict = look.compare_look(look.capture_look(before),
                                 look.capture_look(after))

    assert verdict["status"] == look.UNPROVABLE
    assert [row["domain"] for row in verdict["findings"]] == [
        "picture_transform"]


def test_a_transform_that_moved_is_a_change():
    """The Edit representation moves too."""
    before = _snapshot(_clip())
    after = _snapshot(_clip(transform={"Pan": 3.0, "Tilt": 0.25,
                                       "ZoomX": 1.0, "ZoomY": 1.0}))

    verdict = look.compare_look(look.capture_look(before),
                                 look.capture_look(after))

    assert [row["domain"] for row in verdict["findings"]] == [
        "picture_transform"]


def test_a_float_that_moved_by_its_representation_noise_is_not_a_change():
    """A look that moved by a float's noise has not moved."""
    before = _snapshot(_clip(transform={"Pan": -35.0, "Tilt": 0.25}))
    after = _snapshot(_clip(transform={"Pan": -35.000000000000036,
                                       "Tilt": 0.25}))

    verdict = look.compare_look(look.capture_look(before),
                                 look.capture_look(after))

    assert verdict["findings"] == []


# ── The canvas half ─────────────────────────────────────────────────────

def test_the_shot_whose_comp_is_on_another_canvas_is_named():
    """One shot in a reel of matching ones: the Reel 26 signature."""
    reel = look.capture_look(_snapshot(
        _clip(uid="uid-1", media="media-1", canvas=(1080, 1920)),
        _clip(uid="uid-2", media="media-2", canvas=(1080, 1920),
              record_in=48, record_out=96),
        _clip(uid="uid-3", media="media-3", canvas=(3840, 2160),
              record_in=96, record_out=144),
    ))

    report = look.canvas_report(reel)

    kinds = {row["kind"] for row in report}
    assert "canvas_differs_from_delivery" in kinds
    assert "canvas_differs_from_neighbours" in kinds
    named = [row for row in report
             if row["kind"] == "canvas_differs_from_neighbours"]
    assert [row["shot"] for row in named] == [
        look.shot_look(_clip(uid="uid-3", media="media-3",
                             canvas=(3840, 2160)))["shot_id"]]


def test_a_reel_whose_comps_share_a_canvas_is_not_reported():
    """A reel that agrees with itself is not a finding."""
    reel = look.capture_look(_snapshot(
        _clip(uid="uid-1", media="media-1", canvas=(1080, 1920)),
        _clip(uid="uid-2", media="media-2", canvas=(1080, 1920),
              record_in=48, record_out=96),
    ))

    assert look.canvas_report(reel) == []


def test_a_shot_with_no_readable_comp_carries_no_canvas():
    """A comp whose canvas could not be read is absent evidence."""
    reel = look.capture_look(_snapshot(_clip(canvas=None)))

    assert look.comp_canvases(reel["shots"][0]) == []
    assert look.canvas_report(reel) == []


# ── The pixel half ──────────────────────────────────────────────────────

def _png(path, colour, size=(64, 64)):
    from PIL import Image

    Image.new("RGB", size, colour).save(str(path))
    return str(path)


def test_the_delivered_frames_that_moved_are_named(tmp_path):
    """The delivered pixels are compared, shot by shot."""
    before = {"shot-a": _png(tmp_path / "a-before.png", (10, 10, 10))}
    after = {"shot-a": _png(tmp_path / "a-after.png", (200, 200, 200)),
             "shot-b": _png(tmp_path / "b-after.png", (0, 0, 0))}

    verdict = look.compare_frames(before, after)

    assert verdict["changed"] == ["shot-a"]
    assert verdict["missing"] == ["shot-b"]
    assert verdict["status"] == look.VIOLATED


def test_the_delivered_frames_that_did_not_move_pass(tmp_path):
    """Two renders of the same shot agree, and the check says so."""
    before = {"shot-a": _png(tmp_path / "a-before.png", (10, 10, 10))}
    after = {"shot-a": _png(tmp_path / "a-after.png", (10, 10, 10))}

    verdict = look.compare_frames(before, after)

    assert verdict["changed"] == []
    assert verdict["status"] == look.PRESERVED


def test_a_frame_that_changed_size_is_a_change(tmp_path):
    """A canvas that moved is a change, whatever the pixels say."""
    before = {"shot-a": _png(tmp_path / "a-before.png", (10, 10, 10))}
    after = {"shot-a": _png(tmp_path / "a-after.png", (10, 10, 10),
                            size=(32, 64))}

    verdict = look.compare_frames(before, after)

    assert verdict["changed"] == ["shot-a"]
    assert verdict["shots"]["shot-a"]["reason"] == "frame_size_changed"


# ── The gate ────────────────────────────────────────────────────────────

def test_the_gate_fails_on_an_unauthorized_look_change():
    """The check refuses what the authorized delta does not permit."""
    before = _snapshot(_clip())
    after = _snapshot(_clip(canvas=(3840, 2160)))

    verdict = look.verify_look_preservation(
        before=before, after=after,
        scope=_scope({"op": "clip.trim", "unique_id": "uid-1"}))

    assert verdict["status"] == "failed"
    assert verdict["failures"]


def test_the_look_coverage_is_read_out_of_the_code():
    """The coverage report names the paths that do not carry the gate.

    Read out of the CODE, the way the whole-timeline coverage is: a
    path that stops passing a baseline - or stops running the gate -
    is reported the moment it does.
    """
    from library.tools import mutation_verification

    coverage = mutation_verification.look_coverage()

    assert coverage["denominator"] == len(coverage["covered_routes"]) + \
        len(coverage["uncovered_routes"])
    assert coverage["covered"] == 3, coverage
    covered = {row["path"] for row in coverage["covered_routes"]}
    assert covered == {"multi_operation_revision", "multi_delete_batch",
                       "protected_compound_touchup"}, covered
    # A build has no state BEFORE it, so the look has nothing to
    # compare against: not applicable, not uncovered.
    not_applicable = {row["path"]
                      for row in coverage["not_applicable_routes"]}
    assert not_applicable == {"reel_build"}, not_applicable
    uncovered = {row["path"] for row in coverage["uncovered_routes"]}
    assert uncovered == {"reel_touchup"}, uncovered


def test_the_gate_passes_a_clean_revision():
    """A revision that changed nothing about the look passes."""
    snapshot = _snapshot(_clip())

    verdict = look.verify_look_preservation(
        before=snapshot, after=snapshot,
        scope=_scope({"op": "clip.trim", "unique_id": "uid-1"}))

    assert verdict["status"] == "verified"
    assert verdict["failures"] == []


def test_the_gate_names_the_delivered_frame_that_moved(tmp_path):
    """A framing change no state explains is still a finding."""
    before = _snapshot(_clip())
    after = _snapshot(_clip())

    verdict = look.verify_look_preservation(
        before=before, after=after,
        scope=_scope({"op": "clip.trim", "unique_id": "uid-1"}),
        frames_before={"shot": _png(tmp_path / "before.png", (0, 0, 0))},
        frames_after={"shot": _png(tmp_path / "after.png", (255, 255, 255))})

    assert verdict["status"] == "failed"
    assert "delivered frame moved" in verdict["failures"][0]


# ── The read carries what the comparison needs ──────────────────────────

class _Tool:
    def __init__(self, reg_id, inputs):
        self._reg_id = reg_id
        self._inputs = dict(inputs)

    def GetAttrs(self, key):
        return self._reg_id if key == "TOOLS_RegID" else None

    def GetInput(self, key, *_frame):
        return self._inputs.get(key)


class _Comp:
    def __init__(self, tools):
        self._tools = dict(tools)

    def GetToolList(self, _selected=False):
        return dict(self._tools)


class _Graph:
    def __init__(self, labels):
        self._labels = dict(labels)

    def GetNumNodes(self):
        return len(self._labels)

    def GetLUT(self, node):
        return ""

    def GetNodeLabel(self, node):
        return self._labels.get(node)

    def GetNodeColorGroup(self, node):
        return ""


class _Pool:
    def GetClipProperty(self, name):
        return {"File Path": "/footage/shot.mov", "Resolution": "3840x2160",
                "Frames": "500"}.get(name, "")

    def GetMediaId(self):
        return "media-1"


class _Item:
    def __init__(self):
        self._comp = _Comp({1: _Tool("Background", {"Width": 3840,
                                                     "Height": 2160}),
                            2: _Tool("MediaIn1", {})})

    def GetName(self):
        return "shot"

    def GetStart(self):
        return 0

    def GetEnd(self):
        return 48

    def GetDuration(self):
        return 48

    def GetUniqueId(self):
        return "uid-1"

    def GetType(self):
        return "video"

    def GetMediaPoolItem(self):
        return _Pool()

    def GetLinkedItems(self):
        return []

    def GetFusionCompCount(self):
        return 1

    def GetFusionCompNameList(self):
        return ["MediaIn1"]

    def GetFusionCompByIndex(self, index):
        return self._comp if index == 1 else None

    def GetCDL(self):
        return {"NodeIndex": 1}

    def GetColorGroup(self):
        return ""

    def GetNodeGraph(self):
        return _Graph({1: "Node 1", 2: "Node 2"})

    def GetProperty(self):
        return {"Pan": 0.0, "Tilt": 0.25}

    def GetClipColor(self):
        return ""

    def GetFlagList(self):
        return []

    def GetClipEnabled(self):
        return True

    def GetMarkers(self):
        return {}

    def GetSourceStartFrame(self):
        return 0

    def GetSourceEndFrame(self):
        return 48

    def GetLeftOffset(self):
        return 0

    def GetRightOffset(self):
        return 0


def test_the_read_carries_the_comp_canvas_and_the_grade_nodes():
    """The reader states the canvas a comp was authored on.

    Without it, the state comparison cannot see the defect this lane
    exists for: two comps with the same names read EQUAL.
    """
    from library.tools import reel_read

    detail = reel_read.clip_detail(_Item(), "video", 1, "V1")

    comps = detail["fusion"]["comps"]
    assert len(comps) == 1
    assert comps[0]["name"] == "MediaIn1"
    assert comps[0]["canvas"] == {"width": 3840, "height": 2160}
    assert "Background" in comps[0]["tools"]
    assert detail["color"]["nodes"] == [
        {"node": 1, "label": "Node 1", "group": None},
        {"node": 2, "label": "Node 2", "group": None},
    ]


def test_the_read_reports_an_unreadable_comp_as_absent_evidence():
    """A comp whose tools cannot be read carries no canvas."""
    from library.tools import reel_read

    class _Broken(_Item):
        def GetFusionCompByIndex(self, index):
            return None

    detail = reel_read.clip_detail(_Broken(), "video", 1, "V1")

    assert detail["fusion"]["comps"][0]["canvas"] is None
    assert detail["fusion"]["comps"][0]["tools"] is None


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__]))
