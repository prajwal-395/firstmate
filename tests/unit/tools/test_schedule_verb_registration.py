"""The fabric verbs are registered in the ONE command registry.

Defect caught: a verb missing from `ren/commands.VERBS` is a command
`ren` does not have - the module exists but nothing reaches it.
"""
from ren.commands import VERBS


def test_the_schedule_verb_is_registered():
    by_name = {verb.name: verb for verb in VERBS}
    assert "schedule" in by_name, "ren lost its 'schedule' verb"
    assert "fabric_scheduler" in " ".join(by_name["schedule"].module_argv), (
        "ren 'schedule' no longer reaches the fabric scheduler")
