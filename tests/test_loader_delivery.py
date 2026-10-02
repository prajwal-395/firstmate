"""Locked Loader delivery: wiring and read-back refusal.

`TimelineItem.ImportFusionComp` turns Loader nodes into MediaIn
placeholders, so the delivery re-attaches the files under
comp.Lock(). Everything Resolve-shaped here is a fake with the same
method names; the logic under test is which calls are made, in which
order, and what read-back refuses.

No timeline path rides this today - the behind-subject composite it
was built for now reaches the timeline precomposited
(library/tools/behind_subject.py) - so the specs and wiring here are
synthetic. What stays covered is the generic executor, for a future
file-backed delivery that measures.
"""

import os
import sys

import pytest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from library.tools.execution import deliver_loaders as dl


SPECS = [
    {"role": "title", "first_frame": "/titles/t_00000.png",
     "trim_in": 12, "trim_out": 40},
    {"role": "matte", "first_frame": "/mattes/m_00000.png",
     "trim_in": 0, "trim_out": None},
]

WIRING = (
    ("TitleOver", "Foreground", "title", "Output"),
    ("SubjectOver", "EffectMask", "matte", "Mask"),
)

PLACEHOLDERS = ("BehindTitle", "SubjectMatte")


class _Input:
    def __init__(self, input_id, connected=True):
        self._attrs = {"INPS_ID": input_id, "INPB_Connected": connected}

    def GetAttrs(self):
        return dict(self._attrs)


class _Tool:
    def __init__(self, name, reg="Merge", attrs=None):
        self._name = name
        self._reg = reg
        # Both attribute tables, shaped like the measured loader:
        # the integer table parses the numbering (sane length, 0x0
        # dims until decode), the string table carries the decoded
        # file. A fake with only one table would pass code that
        # reads only that table and fail the build it ships to.
        self._attrs = {"TOOLS_Name": name, "TOOLS_RegID": reg,
                       "TOOLIT_Clip_Length": {1: 300},
                       "TOOLIT_Clip_Width": {1: 0},
                       "TOOLIT_Clip_Height": {1: 0},
                       "TOOLST_Clip_Length": {1: 300},
                       "TOOLST_Clip_Width": {1: 1920},
                       "TOOLST_Clip_Height": {1: 1080}}
        self._attrs.update(attrs or {})
        self.inputs = {}
        self.calls = []

    def GetAttrs(self):
        return dict(self._attrs)

    def SetMultiClip(self, path):
        self.calls.append(("SetMultiClip", path))

    def SetAttrs(self, values):
        self.calls.append(("SetAttrs", dict(values)))
        self._attrs.update(values)

    def ConnectInput(self, input_id, tool):
        self.calls.append(("ConnectInput", input_id, tool._name))
        return True

    def GetInputList(self):
        return {i: _Input(input_id, True)
                for i, input_id in enumerate(self.inputs)}

    def FindTool(self, name):
        raise AssertionError("wired through the comp, not the tool")

    def Delete(self):
        self.calls.append(("Delete",))


class _Comp:
    def __init__(self, tools):
        self.tools = dict(tools)
        self.calls = []
        self._locked = 0

    def Lock(self):
        self._locked += 1
        self.calls.append(("Lock",))

    def Unlock(self):
        self._locked -= 1
        self.calls.append(("Unlock",))

    def AddTool(self, reg, *_pos):
        tool = _Tool(f"Loader{len(self.tools)}", reg=reg)
        self.tools[tool._name] = tool
        self.calls.append(("AddTool", reg))
        return tool

    def FindTool(self, name):
        return self.tools.get(name)


def _wired_comp(**overrides):
    title_over = _Tool("TitleOver")
    title_over.inputs = {"Foreground": True}
    subject_over = _Tool("SubjectOver")
    subject_over.inputs = {"EffectMask": True}
    tools = {"TitleOver": title_over, "SubjectOver": subject_over}
    tools.update(overrides)
    return _Comp(tools)


def test_delivery_wires_both_loaders_under_lock():
    comp = _wired_comp()
    delivered = dl.deliver_loaders(comp, SPECS, WIRING, PLACEHOLDERS)
    assert sorted(delivered) == ["matte", "title"]
    assert comp.calls[0] == ("Lock",)
    assert comp.calls[-1] == ("Unlock",)
    assert comp._locked == 0
    title_over = comp.tools["TitleOver"]
    assert ("ConnectInput", "Foreground", delivered["title"]) in [
        (c[0], c[1], c[2]) for c in title_over.calls
        if c[0] == "ConnectInput"]


def test_a_loader_that_did_not_decode_or_resolve_refuses():
    comp = _wired_comp()
    real_add = comp.AddTool

    def _black_loader(reg, *_pos):
        tool = real_add(reg, *_pos)
        tool._attrs.update({"TOOLST_Clip_Width": {1: 0},
                            "TOOLST_Clip_Height": {1: 0}})
        return tool

    comp.AddTool = _black_loader
    with pytest.raises(dl.LoaderDeliveryRefused) as exc:
        dl.deliver_loaders(comp, SPECS, WIRING, PLACEHOLDERS)
    assert "did not decode" in str(exc.value)

    # An unreadable clip length refuses the same way.
    comp = _wired_comp()
    real_add_2 = comp.AddTool

    def _unreadable_loader(reg, *_pos):
        tool = real_add_2(reg, *_pos)
        tool._attrs.update({"TOOLST_Clip_Length": {1: 0xFFFFFFFF},
                            "TOOLIT_Clip_Length": {1: 0xFFFFFFFF}})
        return tool

    comp.AddTool = _unreadable_loader
    with pytest.raises(dl.LoaderDeliveryRefused) as exc:
        dl.deliver_loaders(comp, SPECS, WIRING, PLACEHOLDERS)
    assert "did not resolve" in str(exc.value)


def test_a_missing_merge_refuses_not_skips():
    comp = _Comp({})
    with pytest.raises(dl.LoaderDeliveryRefused) as exc:
        dl.deliver_loaders(comp, SPECS, WIRING, PLACEHOLDERS)
    assert "TitleOver" in str(exc.value)


def test_placeholders_are_removed_and_working_loaders_kept():
    placeholder = _Tool("BehindTitle", reg="MediaIn")
    comp = _wired_comp(BehindTitle=placeholder)
    dl.deliver_loaders(comp, SPECS, WIRING, PLACEHOLDERS)
    assert ("Delete",) in placeholder.calls


def test_trims_that_do_not_take_refuse():
    comp = _wired_comp()
    real_add = comp.AddTool

    def _stubborn_loader(reg, *_pos):
        tool = real_add(reg, *_pos)

        def _ignore(values):
            tool.calls.append(("SetAttrs", dict(values)))

        tool.SetAttrs = _ignore
        return tool

    comp.AddTool = _stubborn_loader
    with pytest.raises(dl.LoaderDeliveryRefused) as exc:
        dl.deliver_loaders(comp, SPECS, WIRING, PLACEHOLDERS)
    assert "wrong frames" in str(exc.value)
