from library.tools.resolve_api_capabilities import (
    build_capability_map,
    callable_method,
    has_documented_method,
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
