import pytest
from library.tools.toon_serializer import json_to_toon, toon_to_json

def test_basic_dict():
    data = {"a": 1, "b": "hello"}
    toon = json_to_toon(data)
    assert "a: 1" in toon
    assert "b: hello" in toon
    assert toon_to_json(toon) == data

def test_nested_dict():
    data = {"a": {"b": 2}}
    toon = json_to_toon(data)
    assert toon == "a:\n  b: 2"
    assert toon_to_json(toon) == data

def test_uniform_array_table():
    data = [{"id": 1, "val": "a"}, {"id": 2, "val": "b"}]
    toon = json_to_toon(data)
    assert "[2]{id,val}" in toon
    assert toon_to_json(toon) == data

def test_non_uniform_array_indexed():
    data = [{"id": 1}, {"val": "a"}]
    toon = json_to_toon(data)
    assert "[0]" in toon
    assert "id: 1" in toon
    assert toon_to_json(toon) == data

def test_array_of_scalars():
    data = [1, 2, 3]
    toon = json_to_toon(data)
    assert "[0] 1" in toon
    assert toon_to_json(toon) == data

def test_commas_in_string():
    data = [{"a": "hello, world"}, {"a": "test"}]
    toon = json_to_toon(data)
    # csv quoting uses single quote based on our setup
    assert "'hello, world'" in toon
    assert toon_to_json(toon) == data

def test_newlines_in_string():
    data = {"a": "hello\nworld"}
    toon = json_to_toon(data)
    assert "hello\\nworld" in toon
    assert toon_to_json(toon) == data

def test_empty_structures():
    data = {"a": [], "b": {}}
    toon = json_to_toon(data)
    assert "a:\n  []" in toon
    assert "b:\n  {}" in toon
    assert toon_to_json(toon) == data

def test_round_trip_complex():
    data = {
        "meta": {"name": "test"},
        "clips": [
            {"id": 1, "desc": "a test, with comma"},
            {"id": 2, "desc": "another"}
        ],
        "tags": ["a", "b", "c"]
    }
    toon = json_to_toon(data)
    parsed = toon_to_json(toon)
    assert parsed == data

def test_booleans_and_nulls():
    data = {"a": True, "b": False, "c": None}
    toon = json_to_toon(data)
    assert "a: true" in toon
    assert "b: false" in toon
    assert "c: " in toon
    assert toon_to_json(toon) == data

def test_table_with_nested_objects():
    data = [{"id": 1, "meta": {"x": 1}}, {"id": 2, "meta": {"x": 2}}]
    toon = json_to_toon(data)
    # Table should handle nested dict as a serialized string
    assert '{"x": 1}' in toon
    parsed = toon_to_json(toon)
    assert parsed == data
