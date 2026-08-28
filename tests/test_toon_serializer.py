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
    # A cell is quoted only when it needs to be, with a backtick.
    assert "`hello, world`" in toon
    assert "test" in toon and "`test`" not in toon
    assert toon_to_json(toon) == data


# ── What the model reads ──────────────────────────────────────────────
#
# The serializer is the last thing that touches a value before it becomes
# prompt text, and nothing downstream calls `toon_to_json` - the model
# reads the characters.  So "the round trip is lossless" is not enough on
# its own: the emitted FORM has to be the text too.
#
# It was not.  `'` was the quote character and `doublequote` was on, so
# every apostrophe in every transcript reached every prompt doubled:
# `we're` as `we''re`, 180 times in one 113 KB context on project 001,
# across nine of the ten archived requests.

CONTRACTION = "okay, we're here, we're here."


def test_an_apostrophe_is_not_doubled():
    data = [{"text": CONTRACTION}, {"text": "plain"}]
    toon = json_to_toon(data)
    assert CONTRACTION in toon, (
        f"the transcript is not in the prompt as written: {toon!r}"
    )
    assert "''" not in toon
    assert toon_to_json(toon) == data


def test_an_embedded_json_document_is_not_escaped_either():
    """The other content class these cells carry.

    A dict or list in a table cell falls back to `json.dumps`, so the cell
    is full of double quotes.  Swapping one quote character for the other
    would have moved the corruption rather than removed it.
    """
    boundaries = [{"time": 0.0, "score": 1.0, "type": "start"}]
    data = [{"scene_boundaries": boundaries, "id": 1},
            {"scene_boundaries": [], "id": 2}]
    toon = json_to_toon(data)
    import json as _json
    assert _json.dumps(boundaries) in toon, (
        f"the JSON cell is escaped rather than readable: {toon!r}"
    )
    assert "\\\"" not in toon
    assert toon_to_json(toon) == data


@pytest.mark.parametrize("value", [
    CONTRACTION,
    'he said "hi", ok',
    "a back\\slash, and a comma",
    "a backtick ` and, a comma",
    "  leading and trailing  ",
    "semi; colon, comma",
    "",
])
def test_the_round_trip_is_symmetric_for_any_cell(value):
    """Whatever the escaping does, reading it back must undo it exactly.

    The empty string is the one known asymmetry and it is deliberate:
    `_format_scalar(None)` and `_format_scalar("")` both emit nothing, so
    an empty cell reads back as None.  Pinned here rather than left to be
    rediscovered.
    """
    data = [{"v": value, "n": 1}, {"v": "other", "n": 2}]
    parsed = toon_to_json(json_to_toon(data))
    expected = value if value != "" else None
    assert parsed[0]["v"] == expected

def test_newlines_in_a_table_cell_are_escaped():
    """Inside a table a row IS a line, so there is nowhere else to put it."""
    data = [{"a": "hello\nworld"}, {"a": "plain"}]
    toon = json_to_toon(data)
    assert "hello\\nworld" in toon
    assert toon_to_json(toon) == data


def test_a_multi_line_value_under_a_key_is_a_block():
    """Under a key there is no such constraint, and the values are documents.

    The captain's creative brief is 47,903 bytes of markdown reaching two
    thirds of the LLM steps. Escaped, it arrived as ONE line carrying
    700-odd literal `\\n`, which is a document the model has to unescape
    before it can read it.
    """
    data = {"creative_brief": "# Title\n\nBody line.\n  indented\n",
            "other": 1}
    toon = json_to_toon(data)

    assert toon == (
        "creative_brief: |\n"
        "  # Title\n"
        "\n"
        "  Body line.\n"
        "    indented\n"
        "\n"
        "other: 1"
    ), repr(toon)
    assert "\\n" not in toon
    assert toon_to_json(toon) == data


@pytest.mark.parametrize("value", [
    "a\nb",
    "a\nb\n",
    "trailing\n\n\n",
    "\nleading",
    "# md\n\n- one\n- two\n",
    "  starts indented\nthen not",
])
def test_a_block_round_trips(value):
    data = {"doc": value, "after": 1}
    assert toon_to_json(json_to_toon(data)) == data


def test_a_value_that_is_the_block_marker_round_trips():
    """`k: |` has to mean one thing, so a value of `|` takes the block route."""
    data = {"a": "|", "b": 2}
    assert toon_to_json(json_to_toon(data)) == data


def test_a_block_nested_under_another_key_round_trips():
    data = {"outer": {"doc": "a\nb\n\n", "n": 2}, "z": 3}
    assert toon_to_json(json_to_toon(data)) == data

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


# ── The order the columns come out in ─────────────────────────────────
#
# The columns were sorted alphabetically, which put the END of a range
# before its START in five tables across four steps: the 110-row
# transcript reached `creative_direction` and `speech_sequence` as
# `clip_id,end,start,text`, so its first row read
# `clip_006,16.085,14.68,...` - a range that finishes before it begins,
# under the only reading a reader has.  The same sort led the 18-column
# spine table with `alignment_method` and put `content` - the line of
# dialogue - in column four.

def test_the_columns_are_the_order_the_data_declares():
    data = [{"type": "chorus", "start": 1.0, "end": 5.0, "energy": 0.8}]
    assert "[1]{type,start,end,energy}" in json_to_toon(data)


def test_a_range_never_ends_before_it_begins():
    """The shapes that were actually wrong, in the order they are stored."""
    for row in (
        {"clip_id": "clip_006", "start": 14.68, "end": 16.085, "text": "hi"},
        {"type": "verse", "start": 0.0, "end": 8.4, "duration": 8.4,
         "energy": 0.6, "relative_energy": 0.9},
        {"start": 14.0, "end": 23.0, "duration": 9.0, "intensity": 0.4},
    ):
        header = json_to_toon([row]).split("\n")[0]
        columns = header[header.index("{") + 1:header.index("}")].split(",")
        assert columns.index("start") < columns.index("end"), header


def test_a_key_a_later_row_introduces_lands_where_it_first_appears():
    data = [{"a": 1, "b": 2}, {"a": 3, "b": 4, "c": 5}]
    assert "[2]{a,b,c}" in json_to_toon(data)


def test_the_columns_are_not_sorted():
    """Named so the alphabetical default cannot come back unremarked."""
    data = [{"zebra": 1, "apple": 2}, {"zebra": 3, "apple": 4}]
    toon = json_to_toon(data)
    assert "[2]{zebra,apple}" in toon
    assert "[2]{apple,zebra}" not in toon
    assert toon_to_json(toon) == data
