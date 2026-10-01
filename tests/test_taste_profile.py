import json
import stat

import pytest

from library.tools import decided_value as dv
from library.tools import taste_profile as tp

SLOT = "mix.speech_above_bed_db"
MEASURED = {"bed_integrated_lufs": -13.9, "speech_lufs": -22.4}


def _user_profile_path(tmp_path, monkeypatch):
    config = tmp_path / "user-home" / ".config" / "ren" / "config.env"
    monkeypatch.setenv("REN_CONFIG", str(config))
    return config.parent / tp.PROFILE_FILENAME


def test_one_explicit_profile_preference_reaches_a_second_project(
        tmp_path, monkeypatch):
    profile = _user_profile_path(tmp_path, monkeypatch)
    first_project = tmp_path / "first-project"
    second_project = tmp_path / "second-project"
    first_project.mkdir()
    second_project.mkdir()

    written = tp.set_preference(
        SLOT, "background", 14, stated_by="Prajwal",
        reason="I want the voice clearly above the bed", path=profile)

    assert written == profile
    assert profile.is_file()
    assert first_project not in profile.parents
    assert second_project not in profile.parents
    data = json.loads(profile.read_text(encoding="utf-8"))
    assert list(data["preferences"]) == [SLOT]
    assert stat.S_IMODE(profile.stat().st_mode) == 0o600

    decision = dv.decide(
        SLOT, scope="background", project_folder=str(second_project),
        measurements=MEASURED,
        model_answer={"value": 6, "why": "the bed sounds soft here"})

    assert decision.basis == dv.STATED
    assert decision.answer == 14
    assert decision.value == pytest.approx((-22.4 - 14) - -13.9)
    assert str(profile) in decision.source
    assert "stated by Prajwal" in decision.source
    assert "clearly above the bed" in decision.source


def test_project_preference_overrides_the_user_profile(tmp_path, monkeypatch):
    profile = _user_profile_path(tmp_path, monkeypatch)
    tp.set_preference(
        SLOT, "background", 14, stated_by="Prajwal", reason="Across projects",
        path=profile)
    project = tmp_path / "project"
    project.mkdir()
    (project / "project.yaml").write_text(
        "pipeline:\n"
        "  creative_preferences:\n"
        "    mix:\n"
        "      speech_above_bed_db:\n"
        "        background: 10\n", encoding="utf-8")

    decision = dv.decide(
        SLOT, scope="background", project_folder=str(project),
        measurements=MEASURED)

    assert decision.basis == dv.STATED
    assert decision.answer == 10
    assert decision.source == (
        "pipeline.creative_preferences.mix.speech_above_bed_db.background")


def test_an_absent_profile_is_not_created_or_filled_with_a_value(
        tmp_path, monkeypatch):
    profile = _user_profile_path(tmp_path, monkeypatch)
    project = tmp_path / "project"
    project.mkdir()

    decision = dv.decide(
        SLOT, scope="background", project_folder=str(project),
        measurements=MEASURED,
        model_answer={"value": 9, "why": "the bed is mastered hot"})

    assert decision.basis == dv.REASONED
    assert not profile.exists()


def test_profile_cli_records_the_person_and_reason_in_user_config_dir(
        tmp_path, monkeypatch, capsys):
    profile = _user_profile_path(tmp_path, monkeypatch)

    assert tp.main([
        "set", SLOT, "--scope", "background", "--value", "12",
        "--stated-by", "Prajwal", "--reason", "A preference I stated",
    ]) == 0

    record = json.loads(profile.read_text(encoding="utf-8"))[
        "preferences"][SLOT]["background"]
    assert record["value"] == 12
    assert record["stated_by"] == "Prajwal"
    assert record["reason"] == "A preference I stated"
    assert "Recorded stated preference" in capsys.readouterr().out


def test_ren_has_a_front_door_for_recording_a_preference():
    from ren.commands import VERBS

    taste = next(verb for verb in VERBS if verb.name == "taste")
    assert taste.module_argv == ("library.tools.taste_profile",)


def test_profile_refuses_a_record_without_provenance(tmp_path):
    profile = tmp_path / "taste_profile.json"
    profile.write_text(json.dumps({
        "version": tp.PROFILE_VERSION,
        "preferences": {
            SLOT: {"background": {"value": 14, "stated_by": "Prajwal"}},
        },
    }), encoding="utf-8")

    with pytest.raises(tp.TasteProfileError, match="missing 'reason'"):
        tp.stated_preference(SLOT, "background", path=profile)
