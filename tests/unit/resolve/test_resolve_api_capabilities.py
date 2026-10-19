from library.tools.resolve_api_capabilities import (
    build_capability_map,
    callable_method,
    has_documented_method,
    method_capability_records,
    observe_methods,
)


def test_capability_map_is_versioned_and_parses_api_and_changelog(tmp_path):
    (tmp_path / "DaVinciResolveScript.pyi").write_text(
        "class Resolve:\n"
        "\tdef GetVersionString(self) -> str:\n"
        '\t\t"""Returns the Resolve version."""\n'
        "\t\t...\n"
        "class TimelineItem:\n"
        "\tdef SetFades(self, fades: FadeInfo) -> bool:\n"
        '\t\t"""Sets audio and video fades."""\n'
        "\t\t...\n"
        "\tdef NewReleaseCall(self):\n\n",
        encoding="utf-8",
    )
    (tmp_path / "CHANGELOG.md").write_text(
        "## 21.1\n\nAdded:\n- TimelineItem.SetFades\n\n"
        "## 21.0\n\nAdded:\n- Older API\n",
        encoding="utf-8",
    )

    result = build_capability_map("21.1.2.3", tmp_path)

    assert result["resolve_version"] == "21.1.2.3"
    release = result["versions"]["21.1.2.3"]
    assert release["class_count"] == 2
    assert release["method_count"] == 3
    assert release["changelog"] == {"21.1": ["TimelineItem.SetFades"]}
    assert release["classes"]["TimelineItem"]["NewReleaseCall"]["signature"] == "(self)"
    assert has_documented_method(result, "TimelineItem", "SetFades")
    assert not has_documented_method(result, "TimelineItem", "SetProperty")
    assert len(release["api_reference_sha256"]) == 64
    assert release["classes"]["TimelineItem"]["SetFades"]["parameters"] == [
        {"name": "fades", "type": "FadeInfo", "required": True}]
    assert release["classes"]["TimelineItem"]["SetFades"]["documented"] is True
    assert release["classes"]["TimelineItem"]["SetFades"]["observed"] is None
    set_fades = next(row for row in method_capability_records(result)
                     if row["id"] == "native_api.TimelineItem.SetFades")
    assert set_fades["production_enabled"] is False
    assert set_fades["resolve_version"] == "21.1.2.3"


def test_callable_method_does_not_use_hasattr_and_handles_proxy_errors():
    class Proxy:
        def Read(self):
            return "read"

        def __getattr__(self, name):
            if name == "Broken":
                raise RuntimeError("proxy disconnected")
            return "not callable"

    proxy = Proxy()
    assert callable_method(proxy, "Read")() == "read"
    assert callable_method(proxy, "Missing") is None
    assert callable_method(proxy, "Broken") is None


def test_observe_methods_records_live_presence_without_claiming_unseen_handles(tmp_path):
    (tmp_path / "DaVinciResolveScript.pyi").write_text(
        "class Timeline:\n\tdef GetName(self) -> str: ...\n"
        "\tdef AddMarker(self, frame: int) -> bool: ...\n",
        encoding="utf-8")
    (tmp_path / "CHANGELOG.md").write_text("## 21.1\n", encoding="utf-8")
    cap_map = build_capability_map("21.1.0", tmp_path)

    class LiveTimeline:
        def GetName(self):
            return "Example"

    observed = observe_methods(cap_map, {"Timeline": LiveTimeline()})
    assert observed == {"Timeline": ["GetName"]}
    methods = cap_map["versions"]["21.1.0"]["classes"]["Timeline"]
    assert methods["GetName"]["observed"] is True
    assert methods["AddMarker"]["observed"] is False
    assert methods["GetName"]["documented"] is True
