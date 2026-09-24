"""The per-user config beats the checkout's .env, and the environment beats both.

The defect this names: with the user file loaded AFTER .env (or with
both allowed to override), a stale per-checkout .env would silently win
over the machine's one config, and `ren config` would report the wrong
source.
"""

from library.tools.paths import load_configuration


def test_environment_then_user_file_then_dotenv(tmp_path):
    user = tmp_path / "config.env"
    user.write_text('PIPELINE_PROJECTS_ROOT="/from/user"\n'
                    "PIPELINE_SFX_LIBRARY=/from/user/sfx\n", encoding="utf-8")
    dotenv = tmp_path / ".env"
    dotenv.write_text("PIPELINE_PROJECTS_ROOT=/from/dotenv\n"
                      "PIPELINE_SFX_LIBRARY=/from/dotenv/sfx\n"
                      "PIPELINE_MUSIC_LIBRARY=/from/dotenv/music\n", encoding="utf-8")
    environ = {"PIPELINE_SFX_LIBRARY": "/from/environment"}

    sources = load_configuration((user, dotenv), environ)

    assert environ["PIPELINE_SFX_LIBRARY"] == "/from/environment"
    assert sources["PIPELINE_SFX_LIBRARY"] == "environment"
    assert environ["PIPELINE_PROJECTS_ROOT"] == "/from/user"
    assert sources["PIPELINE_PROJECTS_ROOT"] == str(user)
    assert environ["PIPELINE_MUSIC_LIBRARY"] == "/from/dotenv/music"
    assert sources["PIPELINE_MUSIC_LIBRARY"] == str(dotenv)
    assert "PIPELINE_SHARED_ASSETS" not in environ  # left to the code default
