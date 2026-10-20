from argparse import Namespace

from library.tools.resolve_axi_core_commands import cmd_capabilities


def test_capability_search_finds_overlay_resize_by_editorial_request(capsys):
    assert cmd_capabilities(Namespace(
        operation="", search="resize existing lower third", json=False,
    )) == 0
    output = capsys.readouterr().out
    assert "overlay.resize" in output
    assert "matches[" in output
    assert "algebra_operations" not in output


def test_capability_detail_reports_schema_backends_evidence_and_cost(capsys):
    assert cmd_capabilities(Namespace(
        operation="overlay.resize", search="", json=True,
    )) == 0
    import json

    payload = json.loads(capsys.readouterr().out)
    assert payload["schema"]["required"] == ["unique_id", "width", "height"]
    assert payload["eligible_backends"] == []
    assert any(row["id"] == "reel_touchup" and not row["eligible"]
               for row in payload["backends"])
    assert payload["recovery_limits"]["qualified_backends"] == []
    assert payload["measured_cost"]["status"] == "unmeasured"


def test_capability_list_is_concise_toon_and_json_is_structured(capsys):
    assert cmd_capabilities(Namespace(
        operation="", search="", json=False,
    )) == 0
    output = capsys.readouterr().out
    assert "operations[" in output
    assert "overlay.resize" in output
    assert "native_mcp" not in output

    assert cmd_capabilities(Namespace(
        operation="", search="", json=True,
    )) == 0
    import json

    payload = json.loads(capsys.readouterr().out)
    assert any(row["id"] == "overlay.resize" for row in payload["operations"])


def test_an_unknown_operation_comes_back_with_guidance_not_a_bare_error(capsys):
    """The defect: `capabilities <unknown>` printed "unknown capability
    'x'" and nothing else, so the caller had to guess at ids and read
    sentences to find out what existed.

    The answer now carries the nearest real ids and the operations this
    build may route in production.
    """
    import json

    from library.tools import capability_guidance

    assert cmd_capabilities(Namespace(
        operation="clip.buld", search="", json=True,
    )) == 1
    payload = json.loads(capsys.readouterr().out)
    guidance = payload["guidance"]
    assert guidance["status"] == capability_guidance.UNKNOWN
    assert guidance["asked"] == "clip.buld"
    assert guidance["alternatives"], "an unknown id came back with nothing to try"
    assert guidance["supported"], "no production-enabled operation was named"
    for field in ("status", "asked", "what", "why", "fix", "next_action",
                  "alternatives", "supported"):
        assert field in guidance, field


def test_an_operation_that_is_not_production_enabled_says_what_is(capsys):
    """An operation that is implemented but unqualified comes back with
    the gap named AND the operations that ARE production-enabled, so the
    answer is "use this one", not "no"."""
    import json

    from library.tools import capability_guidance, edit_algebra

    unqualified = [status.id for status in edit_algebra.OP_STATUS.values()
                   if not status.production_enabled]
    assert unqualified, "the registry holds no unqualified operation"
    operation = unqualified[0]
    assert cmd_capabilities(Namespace(
        operation=operation, search="", json=True,
    )) == 0
    payload = json.loads(capsys.readouterr().out)
    guidance = payload["guidance"]
    assert guidance is not None, "an unqualified operation came back bare"
    assert guidance["status"] in {
        capability_guidance.UNQUALIFIED,
        capability_guidance.NOT_PRODUCTION_ENABLED,
        capability_guidance.UNAVAILABLE,
    }
    assert guidance["fix"], "the guidance named no next step"


def test_a_production_enabled_operation_carries_no_guidance(capsys):
    """Nothing to guide about: an operation this build may route comes
    back bare, so a reader is not sent looking for a gap that is not
    there."""
    import json

    from library.tools import edit_algebra

    enabled = [status.id for status in edit_algebra.OP_STATUS.values()
               if status.production_enabled]
    assert enabled, "the registry holds no production-enabled operation"
    assert cmd_capabilities(Namespace(
        operation=enabled[0], search="", json=True,
    )) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload.get("guidance") is None


def test_the_support_band_is_the_one_the_operation_map_publishes(capsys):
    """ONE reading of how well an operation is supported.

    The operation map publishes four bands - production enabled, context
    qualified, implemented, unsupported - and this answer publishes the
    same four.  Two surfaces that answer "how well is this supported?"
    with two vocabularies is how a caller comes to believe an operation
    is routable when it is not.
    """
    import json

    from library.tools import edit_algebra, operation_map

    for status in list(edit_algebra.OP_STATUS.values())[:12]:
        assert cmd_capabilities(Namespace(
            operation=status.id, search="", json=True,
        )) == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["support_level"] == operation_map.support_level(status), \
            status.id
        assert payload["support_level"] in operation_map.SUPPORT_LEVELS
