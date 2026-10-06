"""The fabric verbs are registered in the ONE command registry.

Defect caught: a verb missing from `ren/commands.VERBS` is a command
`ren` does not have - the module exists but nothing reaches it.
"""
from pathlib import Path

import ren.commands as ren_commands
from ren.commands import VERBS

REPO = Path(__file__).resolve().parents[3]


def test_the_schedule_verb_is_registered():
    by_name = {verb.name: verb for verb in VERBS}
    assert "schedule" in by_name, "ren lost its 'schedule' verb"
    assert "fabric_scheduler" in " ".join(by_name["schedule"].module_argv), (
        "ren 'schedule' no longer reaches the fabric scheduler")


def test_the_events_verb_is_registered_in_both_front_doors():
    """`events` reaches the operation log past both verb lists.

    The defect the two registries' own comments warn about: a verb
    the Python list knows but the native list does not is
    unreachable - the native front door refuses it before exec'ing
    the implementation. Without this verb the event log is not
    readable through `ren`, so a worker whose only tool is the front
    door cannot read the operation history.
    """
    by_name = {verb.name: verb for verb in ren_commands.VERBS}
    assert by_name["events"].module_argv == ("library.tools.event_log",)
    native = (REPO / "native" / "ren-cli" / "src" / "commands.rs").read_text(
        encoding="utf-8")
    assert 'name: "events"' in native
