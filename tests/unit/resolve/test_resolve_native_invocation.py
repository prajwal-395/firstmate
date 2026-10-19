import pytest

from library.tools.resolve_api_capabilities import build_capability_map
from library.tools.resolve_native_invocation import (
    NativeInvocationError,
    ObjectReference,
    invoke,
)


@pytest.fixture
def capability_map(tmp_path):
    (tmp_path / "DaVinciResolveScript.pyi").write_text(
        "from typing import Literal\n"
        "MarkerColor = Literal['Blue', 'Red']\n"
        "class Timeline:\n"
        "\tdef GetName(self) -> str: ...\n"
        "\tdef SetName(self, name: str) -> bool: ...\n"
        "\tdef ReadItem(self, item: TimelineItem) -> str: ...\n"
        "\tdef AddMarker(self, frameId: int, color: MarkerColor, name: str, "
        "note: str, duration: int, customData: str | None = None) -> bool: ...\n"
        "class Project:\n\tdef GetTimelineByName(self, name: str) -> Timeline: ...\n",
        encoding="utf-8")
    (tmp_path / "CHANGELOG.md").write_text("## 21.1\n", encoding="utf-8")
    return build_capability_map("21.1.0", tmp_path)


class Timeline:
    def __init__(self):
        self.calls = []

    def GetName(self):
        return "Example"

    def ReadItem(self, item):
        return "str"

    def AddMarker(self, **kwargs):
        self.calls.append(kwargs)
        return True


def test_read_invocation_validates_typed_args_and_returns_compact_result(capability_map):
    timeline = Timeline()
    result = invoke(
        resolve=object(), target=timeline, class_name="Timeline",
        method_name="GetName", arguments={}, capability_map=capability_map)
    assert result == {
        "method": "Timeline.GetName", "operation_id": None,
        "documented": True, "mutating": False,
        "returned": "Example", "verified": True,
    }


def test_mutation_requires_authorization_and_readback_before_call(capability_map):
    timeline = Timeline()
    kwargs = {
        "resolve": object(), "target": timeline, "class_name": "Timeline",
        "method_name": "AddMarker",
        "arguments": {"frameId": 10, "color": "Blue", "name": "x",
                      "note": "", "duration": 1},
        "capability_map": capability_map,
        "operation_id": "marker.add",
        "authorize": lambda _op, _method: True,
    }
    with pytest.raises(NativeInvocationError, match="read-back verifier"):
        invoke(**kwargs)
    assert timeline.calls == []

    result = invoke(**kwargs, verify=lambda returned: returned is True)
    assert result["verified"] is True
    assert timeline.calls[0]["frameId"] == 10


def test_mutation_refuses_unauthorized_or_bad_typed_arguments(capability_map):
    timeline = Timeline()
    args = {"frameId": 10, "color": "Blue", "name": "x",
            "note": "", "duration": 1}
    with pytest.raises(NativeInvocationError, match="requires AXI operation authorization"):
        invoke(resolve=object(), target=timeline, class_name="Timeline",
               method_name="AddMarker", arguments=args,
               capability_map=capability_map,
               operation_id="marker.add", authorize=lambda *_: False,
               verify=lambda _: True)
    args["frameId"] = "10"
    with pytest.raises(NativeInvocationError, match="requires int"):
        invoke(resolve=object(), target=timeline, class_name="Timeline",
               method_name="AddMarker", arguments=args,
               capability_map=capability_map,
               operation_id="marker.add", authorize=lambda *_: True,
               verify=lambda _: True)
    assert timeline.calls == []


def test_structured_timeline_reference_is_exact(capability_map):
    class Project:
        def GetTimelineByName(self, name):
            return Timeline() if name == "Example" else None

    class Resolve:
        def GetProjectManager(self):
            return self

        def GetCurrentProject(self):
            return Project()

    current_project = Project()
    ref = ObjectReference(kind="timeline_item", name="Example", stable_id="reel-1")
    # Stable references need AXI's identity resolver and cannot be resolved
    # from a name alone.
    with pytest.raises(NativeInvocationError, match="identity resolver"):
        invoke(resolve=Resolve(), target=Timeline(), class_name="Timeline",
               method_name="ReadItem", arguments={"item": ref},
               capability_map=capability_map, project=current_project)
    item = object()
    resolved = invoke(
        resolve=Resolve(), target=Timeline(), class_name="Timeline",
        method_name="ReadItem", arguments={"item": ref},
        capability_map=capability_map, project=current_project,
        resolve_object=lambda candidate: item if candidate == ref else None)
    assert resolved["returned"] == "str"

    timeline_ref = ObjectReference(kind="timeline", name="Example")
    by_reference = invoke(
        resolve=Resolve(), target=timeline_ref, class_name="Timeline",
        method_name="GetName", arguments={}, capability_map=capability_map,
        project=current_project)
    assert by_reference["returned"] == "Example"
