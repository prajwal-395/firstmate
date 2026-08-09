import pytest
from library.tools.context_projector import project_fields

def test_top_level_key():
    data = {"a": 1, "b": 2}
    assert project_fields(data, ["a"]) == {"a": 1}

def test_nested_path():
    data = {"a": {"b": {"c": 1, "d": 2}}, "e": 3}
    assert project_fields(data, ["a.b.c"]) == {"a": {"b": {"c": 1}}}

def test_array_wildcard():
    data = {"clips": [{"id": 1, "dur": 5}, {"id": 2, "dur": 10}]}
    assert project_fields(data, ["clips.*.dur"]) == {"clips": [{"dur": 5}, {"dur": 10}]}

def test_multiple_paths_merge():
    data = {
        "clips": [{"id": 1, "dur": 5}, {"id": 2, "dur": 10}],
        "meta": {"name": "test", "date": "today"}
    }
    paths = ["clips.*.id", "meta.name"]
    expected = {
        "clips": [{"id": 1}, {"id": 2}],
        "meta": {"name": "test"}
    }
    assert project_fields(data, paths) == expected

def test_missing_keys_ignored():
    data = {"a": 1}
    assert project_fields(data, ["b.c"]) == {}

def test_missing_array_item_keys_aligned():
    data = {"clips": [{"id": 1, "dur": 5}, {"id": 2}]}
    assert project_fields(data, ["clips.*.dur"]) == {"clips": [{"dur": 5}, {}]}

def test_wildcard_on_non_array():
    data = {"clips": "not_an_array"}
    assert project_fields(data, ["clips.*.dur"]) == {}

def test_original_data_not_mutated():
    data = {"a": {"b": 1}}
    result = project_fields(data, ["a.b"])
    result["a"]["b"] = 2
    assert data["a"]["b"] == 1

def test_array_multiple_wildcard_fields():
    data = {"clips": [{"id": 1, "dur": 5, "x": 9}, {"id": 2, "dur": 10, "y": 8}]}
    assert project_fields(data, ["clips.*.id", "clips.*.dur"]) == {
        "clips": [{"id": 1, "dur": 5}, {"id": 2, "dur": 10}]
    }

def test_deep_wildcard():
    data = {"scenes": [{"shots": [{"id": 1}, {"id": 2}]}]}
    assert project_fields(data, ["scenes.*.shots.*.id"]) == {
        "scenes": [{"shots": [{"id": 1}, {"id": 2}]}]
    }
