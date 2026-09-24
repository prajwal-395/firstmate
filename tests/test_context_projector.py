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








# ── Exclusion paths ────────────────────────────────────────────────────
#
# A `-` path removes what the paths above it selected.  It exists so a
# projection can say "the whole spine, without the per-word timings"
# instead of enumerating the other twenty keys - an enumeration that
# works once and then silently stops delivering the twenty-first.

def test_exclusion_removes_one_field_from_a_kept_subtree():
    data = {"spine": {"blocks": [{"text": "a", "words": [1, 2]},
                                 {"text": "b", "words": [3]}],
                      "duration": 9}}
    assert project_fields(data, ["spine", "-spine.blocks.*.words"]) == {
        "spine": {"blocks": [{"text": "a"}, {"text": "b"}], "duration": 9}
    }


def test_exclusion_reaches_a_nested_field():
    data = {"spine": {"blocks": [{"content": {"text": "a", "words": [1]}}]}}
    assert project_fields(
        data, ["spine", "-spine.blocks.*.content.words"]
    ) == {"spine": {"blocks": [{"content": {"text": "a"}}]}}


def test_exclusion_of_a_whole_key():
    data = {"music": {"bpm": 90, "curve": [1, 2, 3]}}
    assert project_fields(data, ["music", "-music.curve"]) == {"music": {"bpm": 90}}






