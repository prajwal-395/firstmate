"""The cold start is a reading, not a guess.

Every test here names the defect it catches: the first inference after
a load pays for warming the model up, and a profile that cannot say
which inference was the cold one reports the cost of loading a model
as though it were free.
"""

from library.tools import model_lifecycle


def test_the_first_inference_after_a_load_is_cold_and_the_rest_are_warm():
    """The first inference after a load is the cold one.

    Defect caught: every inference charged as warm, so a session that
    pays to load a 12 GB model reads as if the loading were free.
    """
    model_lifecycle.unload_all()
    with model_lifecycle.managed_model("fixture-cold", lambda: object()):
        first = model_lifecycle.note_inference("fixture-cold")
        second = model_lifecycle.note_inference("fixture-cold")

    assert first["cold_start"] is True
    assert second["cold_start"] is False
    assert first["resident"] is True


def test_a_reload_makes_the_next_inference_cold_again():
    """A model loaded afresh is cold again, not warm for ever.

    Defect caught: the cold inference counted only once per process,
    so every later session of a long-lived worker reads as warm and
    the repeated cost of loading is invisible.
    """
    model_lifecycle.unload_all()
    with model_lifecycle.managed_model("fixture-reload", lambda: object()):
        model_lifecycle.note_inference("fixture-reload")
    model_lifecycle.unload_all()
    with model_lifecycle.managed_model("fixture-reload", lambda: object()):
        again = model_lifecycle.note_inference("fixture-reload")

    assert again["cold_start"] is True
    model_lifecycle.unload_all()
