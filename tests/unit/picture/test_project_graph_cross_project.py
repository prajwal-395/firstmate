import json

from library.tools.footage_identity import fingerprint
from library.tools.project_graph import core, cross_project


def _policy(path, project_id, share_with):
    path.mkdir(exist_ok=True)
    (path / cross_project.POLICY_FILE).write_text(json.dumps({
        "project_id": project_id,
        "share_with": share_with,
    }), encoding="utf-8")


def _graph(media):
    digest = fingerprint(str(media))["content_digest"]
    graph = core.new_graph()
    core.add_node(graph, {
        "node_id": "shot:clip_a", "node_type": core.SHOT, "label": "cafe clip",
        "payload": {"clip_id": "clip_a", "source_file": str(media), "content_digest": digest},
    })
    core.add_node(graph, {
        "node_id": "scene:one", "node_type": core.SCENE, "label": "cafe",
        "payload": {"content_digest": digest, "location": "cafe"},
    })
    core.add_node(graph, {
        "node_id": "person:alex", "node_type": core.PERSON, "label": "Alex",
        "payload": {"name": "Alex"},
    })
    core.add_edge(graph, {"from": "person:alex", "to": "shot:clip_a",
                          "type": core.APPEARS_IN, "payload": {}})
    core.add_node(graph, {
        "node_id": "edit:one", "node_type": core.EDIT, "label": "prior cut",
        "payload": {"edit_payload": {"clip_id": "clip_a", "timeline_start": 0, "timeline_end": 3}},
    })
    return graph


def test_cross_project_query_links_previous_use_and_requires_mutual_opt_in(tmp_path, monkeypatch):
    requester = tmp_path / "requester"
    source = tmp_path / "source"
    requester.mkdir()
    media = tmp_path / "source.mov"
    media.write_bytes(b"test media bytes")
    _policy(requester, "requester", ["source"])
    _policy(source, "source", ["requester"])
    graph = _graph(media)
    monkeypatch.setattr(cross_project, "load_or_build", lambda _path: graph)

    result = cross_project.query_cross_project_assets(
        requester, [source], "cafe", categories=("people", "scenes", "usage", "lineage"))

    assert result["error"] is None
    assert len(result["results"]) == 1
    asset = result["results"][0]
    assert asset["people"] == ["Alex"]
    assert asset["scenes"] == ["cafe"]
    assert asset["previous_usage"][0]["edit_id"] == "edit:one"
    assert asset["lineage"]["content_digest"] == asset["content_digest"]
    (source / cross_project.POLICY_FILE).write_text(json.dumps({
        "project_id": "source", "share_with": [],
    }), encoding="utf-8")
    assert cross_project.query_cross_project_assets(requester, [source], "cafe")["results"] == []


def test_cross_project_query_denies_stale_and_ambiguous_matches(tmp_path, monkeypatch):
    requester = tmp_path / "requester"
    source = tmp_path / "source"
    requester.mkdir()
    media = tmp_path / "source.mov"
    media.write_bytes(b"original")
    _policy(requester, "requester", ["source"])
    _policy(source, "source", ["requester"])
    graph = _graph(media)
    # The graph retained the former identity; live bytes no longer agree.
    media.write_bytes(b"replacement")
    monkeypatch.setattr(cross_project, "load_or_build", lambda _path: graph)
    assert cross_project.query_cross_project_assets(requester, [source], "cafe")["results"] == []

    media.write_bytes(b"original")
    duplicate = tmp_path / "duplicate.mov"
    duplicate.write_bytes(b"different source")
    ambiguous = _graph(media)
    second = _graph(duplicate)
    ambiguous["nodes"].extend(second["nodes"])
    ambiguous["node_count"] = len(ambiguous["nodes"])
    monkeypatch.setattr(cross_project, "load_or_build", lambda _path: ambiguous)
    result = cross_project.query_cross_project_assets(requester, [source], "cafe")
    assert result["abstained"] is True
    assert result["error"] == "ambiguous content match"
    assert result["results"] == []


def test_footage_query_project_graph_exposes_cross_project_search(tmp_path, monkeypatch):
    from library.tools.analysis.footage_query import FootageIndex

    requester = tmp_path / "requester"
    requester.mkdir()
    calls = []

    def fake_query(*args, **kwargs):
        calls.append((args, kwargs))
        return {"results": [], "error": None, "abstained": True}

    monkeypatch.setattr("library.tools.project_graph.query_cross_project_assets", fake_query)
    result = FootageIndex(str(requester)).project_graph(
        "cross_project_assets", projects=["/candidate"], search_text="Alex",
        categories=["people"])

    assert result["query"] == "cross_project_assets"
    assert result["mode"] == "project_graph"
    assert calls[0][0] == (str(requester), ["/candidate"], "Alex")
    assert calls[0][1] == {"categories": ("people",)}
