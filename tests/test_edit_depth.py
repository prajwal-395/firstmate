"""The depth router refuses display-layer fixes for every class.

For each edit class: the owner is named, every display
layer refuses with the owner and the deep path in the message, the
owning layer passes, and an unknown class refuses too. `flag` is the
loud variant for contexts where raising would break a live run.
"""

import pytest

from library.tools import edit_depth


def test_every_class_is_routed():
    """Named for what it checks, not for today's count - a name carrying
    the number goes stale on the next class and fails for itself rather
    than for the thing it guards."""
    assert edit_depth.classes(), "edit_depth names no classes at all"
    for name in edit_depth.classes():
        owner = edit_depth.owner_of(name)
        assert owner["layer"] and owner["store"] and owner["module"]
        assert edit_depth.DEEP_PATH[name]
        assert edit_depth.DISPLAYS[name]


def test_every_display_layer_refuses_naming_owner_and_path():
    for name in edit_depth.classes():
        owner = edit_depth.owner_of(name)
        for layer in edit_depth.DISPLAYS[name]:
            with pytest.raises(edit_depth.EditDepthError) as excinfo:
                edit_depth.refuse_display_edit(
                    name, layer, detail="vetting probe")
            message = str(excinfo.value)
            assert owner["layer"] in message
            assert "Deep path" in message
            assert layer in message


def test_owning_layer_passes():
    for name in edit_depth.classes():
        owner = edit_depth.owner_of(name)
        assert edit_depth.classify(name, owner["layer"]) == "owning"
        assert edit_depth.refuse_display_edit(
            name, owner["layer"]) is None


def test_unknown_class_refuses():
    with pytest.raises(edit_depth.EditDepthError):
        edit_depth.owner_of("vibes")
    with pytest.raises(edit_depth.EditDepthError):
        edit_depth.refuse_display_edit("vibes", "timeline captions")


def test_flag_is_loud_not_fatal(capsys):
    message = edit_depth.flag_display_edit(
        "wording", "timeline captions", detail="vetting probe")
    assert "REFUSED" in message and "transcript root" in message
    assert "REFUSED" in capsys.readouterr().err
    assert edit_depth.flag_display_edit(
        "wording", "transcript root") == ""


def test_cli_route_lists_owner_and_deep_path():
    assert edit_depth.main(["list"]) == 0
    assert edit_depth.main(["route", "wording", "transcript root"]) == 0
    assert edit_depth.main(
        ["route", "wording", "timeline captions"]) == 2
    assert edit_depth.main(["route", "vibes", "x"]) == 1
