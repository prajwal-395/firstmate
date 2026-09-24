"""The reel build CONSULTS the ending and the caption-timing owners.

A router nobody is forced to call is a document, not a mechanism
(`library/tools/edit_depth.py`). `tests/test_reel_ending.py` and
`tests/test_caption_timing.py` prove each owner does what it says;
this file proves the BUILD asks them, and fails the day a refactor
stops asking. The reel build path needs Resolve, a project and a
rendered caption set, so the consultation is asserted structurally -
on the call graph of `rebuild_reels_in_project` and on the seam each
call sits at - which is the same evidence
`tests/test_orphan_wiring.py::test_builder_threading_names_ranges`
accepts for the trims beside it.
"""

import ast
import inspect

import pytest

from library.tools import reel_build
from library.tools import reel_ending


SOURCE = inspect.getsource(reel_build)


def _function(name):
    tree = ast.parse(SOURCE)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise AssertionError(f"{name} is not in reel_build")


def _calls(node):
    """Every `a.b(...)` call spelled inside a function, as 'a.b'."""
    out = []
    for child in ast.walk(node):
        if not isinstance(child, ast.Call):
            continue
        func = child.func
        if isinstance(func, ast.Attribute) and isinstance(
                func.value, ast.Name):
            out.append(f"{func.value.id}.{func.attr}")
        elif isinstance(func, ast.Name):
            out.append(func.id)
    return out


# ── The ending owner ───────────────────────────────────────────────


def test_the_tail_element_decides_what_draws_over_the_tail():
    """The behavioural half of the wiring, run for real: a declared
    `none` draws nothing on the last clip, a declared switch-off draws
    it, and no declaration keeps the unconditional arm that shipped
    before this existed."""
    from library.tools import reel_look

    unconditional = reel_look.power_effects({}, "first", "last")
    assert unconditional["last"]["tv_power_tail"] is True
    declared = reel_look.power_effects(
        {}, "first", "last",
        ending={"reel": "R", "ends_on": {"anchor_phrase": "x"},
                "tail_element": "tv_power_tail", "reason": "r"})
    assert declared["last"]["tv_power_tail"] is True
    silent = reel_look.power_effects(
        {}, "first", "last",
        ending={"reel": "R", "ends_on": {"anchor_phrase": "x"},
                "tail_element": "none", "reason": "r"})
    assert "last" not in silent
    # The switch-ON is never the ending's business.
    for out in (unconditional, declared, silent):
        assert out["first"]["tv_power_head"] is True


def test_reel13s_defect_cannot_pass_the_check_that_now_runs():
    """The exact numbers off the captain's timeline: Craig's twelve
    frames could not carry the eighteen-frame switch-off, and the
    build now refuses instead of dropping it to stderr."""
    ending = {"reel": "Reel 13", "ends_on": {"anchor_phrase": "our bio"},
              "tail_element": "tv_power_tail", "reason": "captain"}
    fps = 24000 / 1001
    craig = [{"source_in": 0.0, "source_out": 12 / fps}]
    with pytest.raises(reel_ending.TailElementHasNoRoom):
        reel_ending.assert_tail_fits(craig, ending, fps)
    akshita = [{"source_in": 0.0, "source_out": 310 / fps}]
    assert reel_ending.assert_tail_fits(
        akshita, ending, fps)["shot_frames"] == 310


# ── The caption-timing owner ───────────────────────────────────────


# ── Promotion says what it is about to discard ─────────────────────


# ── The verifier reads the same owners the builder does ────────────


def test_a_declared_short_card_is_reported_and_an_authored_one_fails():
    """The captain trimmed his last closer card to three frames and
    recorded why. A gate that FAILS correct output is no more coverage
    than one that cannot fail - but only a RECORDED pin reaches the
    exemption."""
    from library.tools import reel_conformance_verifier as verifier

    fps = 24000 / 1001
    cards = [{"text": "the link's in our bio", "reel_start": 1900 / fps,
              "reel_end": 1903 / fps, "frames": 3},
             {"text": "a flash nobody chose", "reel_start": 500 / fps,
              "reel_end": 503 / fps, "frames": 3}]
    findings = verifier.check_short_captions(
        "Reel 13", cards, fps, declared_short=((1900, 3),))
    assert len(findings) == 2
    by_text = {f.detail["text"][:10]: f for f in findings}
    declared = by_text["the link's"]
    assert declared.severity == "warning" and declared.detail["declared"]
    assert "caption_timing.json" in declared.message
    authored = by_text["a flash no"]
    assert authored.severity == "error" and not authored.detail["declared"]
    # With no declaration at all, the floor is exactly as hard as it was.
    assert all(f.severity == "error"
               for f in verifier.check_short_captions("Reel 13", cards, fps))


# ── The freeze reaches the timeline, the comp and the plan ─────────


def test_the_tail_element_arms_on_the_hold_not_the_live_tail():
    """The join that decides whether the switch-off plays over her last
    words or after them."""
    from library.tools import reel_look

    ending = {"reel": "R", "ends_on": {"anchor_phrase": "x"},
              "tail_element": "tv_power_tail", "tail_hold": "freeze",
              "reason": "r"}
    live = {"clip": type("C", (), {"source_file": "/f/shot.mov",
                                   "track_index": 1, "speaker": "A",
                                   "track_type": "video"})(),
            "source_in": 0.0, "source_out": 310 / 24.0,
            "record": 0.0, "snapped_record": 0}
    freeze = reel_ending.plan_freeze([live], ending, 24.0)
    with_hold = reel_build._with_freeze([live], freeze, 24.0)
    assert len(with_hold) == 2 and with_hold[-1]["freeze"] is True
    manifest = reel_look.fusion_manifest(with_hold, {}, [], 24.0,
                                         ending=ending)
    per_clip = manifest["fusion_effects"]["per_clip"]
    # The HOLD is clip 01 and carries the switch-off; the live tail
    # (clip 00) carries only the switch-on, because it is also first.
    assert per_clip["reel_picture_01"]["tv_power_tail"] is True
    assert "tv_power_tail" not in per_clip["reel_picture_00"]
    assert per_clip["reel_picture_00"]["tv_power_head"] is True
    # Without the hold the element falls back onto the live tail, which
    # is what the captain saw across his closing line.
    alone = reel_look.fusion_manifest([live], {}, [], 24.0, ending=ending)
    assert alone["fusion_effects"]["per_clip"][
        "reel_picture_00"]["tv_power_tail"] is True


def test_no_declaration_adds_no_freeze_anywhere():
    assert reel_build._with_freeze([{"a": 1}], None, 24.0) == [{"a": 1}]


# ── The hold is found by its FILE, not by being last on the row ─────

class _FakeItem:
    """Enough of a Resolve timeline item for the freeze lookup."""

    def __init__(self, path, properties=None):
        self._path = path
        self._props = dict(properties or {})
        self.copied_onto = []

    def GetMediaPoolItem(self):
        return self

    def GetClipProperty(self, key):
        return self._path if key == "File Path" else ""

    def GetProperty(self, *args):
        return dict(self._props)

    def SetProperty(self, key, value):
        self._props[key] = value
        return True

    def CopyGrades(self, targets):
        self.copied_onto.extend(targets)
        return True


class _FakeTimeline:
    def __init__(self, items):
        self._items = list(items)

    def GetItemListInTrack(self, media, index):
        return list(self._items) if media == "video" and index == 1 else []


class _FakeFreeze:
    track_index = 1
    held_from = 1255
    duration_frames = 19

    def __init__(self, path):
        self.rendered_path = path
