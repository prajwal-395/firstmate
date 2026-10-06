"""The stem plan lists only the stems a project actually has.

Covers library/tools/stem_plan.py and the shared track-role rule in
otio_mix: A1 dialogue, A2 music, A3+ SFX group by role, and an empty
role is absent rather than planned as silence.
"""
from __future__ import annotations

from library.tools.otio_mix import track_role
from library.tools.stem_plan import plan_stems


def _tracks(**roles):
    tracks = {}
    for role, files in roles.items():
        tracks[role] = {"clips": [
            {"source_file": f, "label": f} for f in files]}
    return tracks


def test_the_role_rule_is_a1_dialogue_a2_music_a3_up_sfx():
    """One owner for the rule: the mix targets and the stem plan read
    the same mapping, so a track cannot mean one thing at mix time and
    another at export time."""
    assert track_role("A1") == "dialogue"
    assert track_role("A2") == "music"
    assert track_role("A3") == "sfx"
    assert track_role("A5") == "sfx"
    assert track_role("V1") is None
    assert track_role("") is None
    assert track_role(None) is None


def test_each_populated_role_plans_one_stem():
    """Dialogue, music and SFX clips group by role, each stem naming
    its own source files."""
    plan = plan_stems(_tracks(A1=["vo.wav"], A2=["bed.mp3"],
                              A3=["whoosh.mp3"], A4=["ding.mp3"]))
    assert plan.roles == ["dialogue", "music", "sfx"]
    assert [c.source_file for c in plan.stems["dialogue"].clips] == \
        ["vo.wav"]
    assert [c.source_file for c in plan.stems["music"].clips] == \
        ["bed.mp3"]
    assert [c.source_file for c in plan.stems["sfx"].clips] == \
        ["whoosh.mp3", "ding.mp3"]
    assert plan.stems["sfx"].clip_count == 2


def test_an_empty_role_is_absent_never_silence():
    """The defect: a voiceover-only video shipping an empty music stem
    and an empty SFX stem. Roles with no clips are not planned - only
    the true stems are produced."""
    plan = plan_stems(_tracks(A1=["vo.wav"]))
    assert plan.roles == ["dialogue"]
    assert "music" not in plan.stems
    assert "sfx" not in plan.stems


def test_a_clip_without_a_source_names_nothing_to_export():
    """A clip entry with no file is skipped; a role left with nothing
    stays absent."""
    plan = plan_stems({"A3": {"clips": [{"label": "ghost"}]}})
    assert plan.roles == []
    assert plan.stems == {}


def test_non_mix_tracks_and_absent_tracks_plan_nothing():
    """Video tracks are not stems, and an absent mix is a fact that
    plans no stems rather than an error."""
    plan = plan_stems(_tracks(V1=["shot.mp4"]))
    assert plan.roles == []
    assert plan_stems({}).roles == []
    assert plan_stems(None).roles == []
