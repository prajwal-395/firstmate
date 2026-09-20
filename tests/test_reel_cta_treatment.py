"""One CTA animation across the fleet, declared once, applied at the source.

Captain's Reel 09 marker, 2026-09-20: the closing card's animation should
carry every CTA where the Lucie Visibility system is referred to. The
fleet measures six present variants plus an absent slot, because every
reel's closer graphic is model-planned per reel. `reel_cta_treatment`
normalises the closing card - the latest-anchored entry among the card
elements - to the declared treatment before `resolve_plan` times it.
Copy and timing stay the plan's own, so a re-render keeps its duration
and a swap cannot move a reel's length. Only reels whose own CTA names
the declared scope phrase qualify.

Every test below is synthetic under `tmp_path` (AGENTS.md 8); none
reaches Resolve or a real project.
"""

import json
import os
from types import SimpleNamespace

import pytest

from library.tools import reel_cta_treatment as cta_rx
from library.tools.reel_proposal import CallToAction

GOLD = "#FBF0B8"
SCOPE = "visibility system"

CTA_TEXT = ("we are calling the lucie visibility system "
            "we would love for you to check it out")
BODY_TEXT = "one is about position and the other is comprehension"


def _windows(words, start=60.0, step=0.5):
    out, cursor = [], start
    for word in words:
        out.append({"word": word, "start": round(cursor, 3),
                    "end": round(cursor + 0.4, 3)})
        cursor = round(cursor + step, 3)
    return out


def _speech():
    return _windows((BODY_TEXT + " then " + CTA_TEXT).split())


def _moment(text=CTA_TEXT):
    return SimpleNamespace(
        number=9, timeline_name="Reel 09 - x",
        call_to_action=CallToAction(timeline_start=100.0,
                                    timeline_end=110.0,
                                    text=text, speaker="craig"))


def _declare(root, treatment=None, scope=SCOPE, reason="captain marker",
             version=1, exclude=()):
    external = os.path.join(str(root), "external")
    os.makedirs(external, exist_ok=True)
    document = {"version": version,
                "scope_cta_contains": scope,
                "exclude_reels": list(exclude),
                "treatment": treatment if treatment is not None else {
                    "element": "title_lockup", "entrance": "fade",
                    "exit": "fade", "anchor": "centre", "color": GOLD},
                "reason": reason}
    with open(os.path.join(external, "reel_cta.json"), "w",
              encoding="utf-8") as handle:
        json.dump(document, handle)
    return str(root)


def _entry(element, phrase, **over):
    base = {"element": element, "anchor_phrase": phrase,
            "hold_seconds": 3.0, "anchor": "top_centre", "row": 0,
            "copy": {"display": phrase.upper()},
            "color": "#aabbcc", "why": "model reasons"}
    base.update(over)
    return base


# ── Absences pass through ────────────────────────────────────────────

def test_no_declaration_passes_the_answer_through_untouched(tmp_path):
    answer = [_entry("quote_card", "lucie visibility system")]
    moment = _moment()
    same, report = cta_rx.apply(answer, moment, _speech(), str(tmp_path))
    assert same is answer
    assert answer[0]["element"] == "quote_card"
    assert report == {"applied": [], "stale": [], "treatment": None}


def test_a_reel_with_no_call_to_action_is_untreated(tmp_path):
    root = _declare(tmp_path)
    moment = SimpleNamespace(number=10, timeline_name="Reel 10 - x",
                             call_to_action=None)
    answer = [_entry("title_lockup", "position and the other")]
    same, report = cta_rx.apply(answer, moment, _speech(), root)
    assert same is answer
    assert answer[0]["element"] == "title_lockup"
    assert report["applied"] == []
    assert len(report["stale"]) == 1
    assert "no call to action" in report["stale"][0]["reason"]


def test_a_cta_outside_the_scope_is_untreated(tmp_path):
    root = _declare(tmp_path)
    moment = _moment(
        text="search did not change the question changed and whoever")
    answer = [_entry("quote_card", "the question changed")]
    same, report = cta_rx.apply(answer, moment, _speech(), root)
    assert answer[0]["element"] == "quote_card"
    assert report["applied"] == []
    assert len(report["stale"]) == 1
    assert "out of the declared scope" in report["stale"][0]["reason"]


def test_scope_matches_either_spelling_of_the_name(tmp_path):
    """The episode spells it both ways; the declaration carries the
    scope and the CTA text carries the spelling, so both must match."""
    root = _declare(tmp_path)
    for text in (
            "we have been building the lucy visibility system for months",
            "we are calling the lucie visibility system today"):
        assert cta_rx.in_scope(_moment(text=text), ["visibility",
                                                    "system"])


# ── The treatment ────────────────────────────────────────────────────

def test_the_latest_anchored_card_is_treated_and_earlier_is_not(tmp_path):
    """The closing card is positional: the punchline card earlier in
    the reel keeps its treatment, the closer takes the declared one."""
    root = _declare(tmp_path)
    answer = [_entry("title_lockup", "one is about position"),
              _entry("title_lockup", "lucie visibility system",
                     colour_role="text")]
    moment = _moment()
    same, report = cta_rx.apply(answer, moment, _speech(), root)
    assert same is answer
    earlier, treated = answer
    assert earlier["element"] == "title_lockup"
    assert earlier["anchor"] == "top_centre"
    assert treated["element"] == "title_lockup"
    assert treated["entrance"] == "fade"
    assert treated["exit"] == "fade"
    assert treated["anchor"] == "centre"
    assert treated["color"] == GOLD
    assert "colour_role" not in treated
    # The plan's own copy, timing, row and reasoning survive.
    assert treated["copy"] == {"display": "LUCIE VISIBILITY SYSTEM"}
    assert treated["hold_seconds"] == 3.0
    assert treated["row"] == 0
    assert treated["why"] == "model reasons"
    assert len(report["applied"]) == 1
    assert report["applied"][0]["index"] == 1
    assert report["applied"][0]["was"]["anchor"] == "top_centre"


def test_a_closer_in_another_card_element_is_converted(tmp_path):
    """quote_card, stat_callout and list_build closers all take the
    title_lockup treatment; their copy is kept, not re-tiered."""
    root = _declare(tmp_path)
    for element in ("quote_card", "stat_callout", "list_build"):
        answer = [_entry(element, "lucie visibility system")]
        _, report = cta_rx.apply(answer, _moment(), _speech(), root)
        assert answer[0]["element"] == "title_lockup", element
        assert answer[0]["copy"] == {"display": "LUCIE VISIBILITY SYSTEM"}
        assert len(report["applied"]) == 1


def test_an_emblem_anchored_later_than_the_closer_is_not_promoted(tmp_path):
    """A subject emblem labelling something past the closing card keeps
    its element: emblems have never closed a reel, and promoting one
    would destroy the thing it was."""
    root = _declare(tmp_path)
    speech = _windows((BODY_TEXT + " then " + CTA_TEXT
                       + " then a very specific question").split())
    answer = [_entry("title_lockup", "lucie visibility system"),
              _entry("subject_emblem", "a very specific question")]
    _, report = cta_rx.apply(answer, _moment(), speech, root)
    assert answer[0]["element"] == "title_lockup"
    assert answer[0]["anchor"] == "centre"
    assert answer[1]["element"] == "subject_emblem"
    assert len(report["applied"]) == 1
    assert report["applied"][0]["index"] == 0


def test_a_declared_seconds_entry_is_never_attributed_by_time(tmp_path):
    """A beat accent on a cut names no words; it cannot be the closing
    card whatever frame it sits on."""
    root = _declare(tmp_path)
    answer = [_entry("beat_accent", "", start_seconds=62.0,
                     duration_seconds=0.4)]
    del answer[0]["anchor_phrase"]
    _, report = cta_rx.apply(answer, _moment(), _speech(), root)
    assert answer[0]["element"] == "beat_accent"
    assert report["applied"] == []
    assert len(report["stale"]) == 1


def test_tied_entries_are_both_treated(tmp_path):
    """Two card entries on the same words stack rather than collide,
    so a tie treats both."""
    root = _declare(tmp_path)
    answer = [_entry("title_lockup", "lucie visibility system"),
              _entry("quote_card", "lucie visibility system")]
    _, report = cta_rx.apply(answer, _moment(), _speech(), root)
    assert answer[0]["element"] == "title_lockup"
    assert answer[1]["element"] == "title_lockup"
    assert len(report["applied"]) == 2


def test_non_dict_entries_pass_through(tmp_path):
    root = _declare(tmp_path)
    answer = ["title_lockup",
              _entry("quote_card", "lucie visibility system")]
    _, report = cta_rx.apply(answer, _moment(), _speech(), root)
    assert answer[0] == "title_lockup"
    assert len(report["applied"]) == 1
    assert report["applied"][0]["index"] == 1


# ── Explicit exclusions ──────────────────────────────────────────────

def test_an_excluded_reel_is_untouched_even_in_scope(tmp_path):
    """Reel 08's class: in scope, but unifying its early card would
    restyle a non-closer, so the declaration spares it by name."""
    root = _declare(tmp_path, exclude=[
        "Reel 08 - top-three-on-google-hallucinated-by-ai"])
    moment = SimpleNamespace(
        number=8, timeline_name="Reel 08 - top-three-on-google-hallucinated-by-ai",
        call_to_action=CallToAction(timeline_start=100.0,
                                    timeline_end=110.0, text=CTA_TEXT,
                                    speaker="craig"))
    answer = [_entry("title_lockup", "lucie visibility system")]
    same, report = cta_rx.apply(answer, moment, _speech(), root)
    assert same is answer
    assert answer[0]["anchor"] == "top_centre"
    assert report["applied"] == []
    assert len(report["stale"]) == 1
    assert "explicitly excluded" in report["stale"][0]["reason"]


def test_an_exclusion_covers_the_staging_suffix(tmp_path):
    root = _declare(tmp_path, exclude=[
        "Reel 08 - top-three-on-google-hallucinated-by-ai"])
    moment = SimpleNamespace(
        number=8,
        timeline_name=("Reel 08 - top-three-on-google-hallucinated-by-ai "
                       "(rebuild staging)"),
        call_to_action=CallToAction(timeline_start=100.0,
                                    timeline_end=110.0, text=CTA_TEXT,
                                    speaker="craig"))
    answer = [_entry("title_lockup", "lucie visibility system")]
    _, report = cta_rx.apply(answer, moment, _speech(), root)
    assert answer[0]["anchor"] == "top_centre"
    assert report["applied"] == []


def test_a_malformed_exclusion_refuses(tmp_path):
    root = str(tmp_path)
    external = os.path.join(root, "external")
    os.makedirs(external, exist_ok=True)
    path = os.path.join(external, "reel_cta.json")
    good_treatment = {"element": "title_lockup", "entrance": "fade",
                      "exit": "fade", "anchor": "centre", "color": GOLD}
    for bad_exclude in ["Reel 08", ["  "], [42], ""]:
        document = {"version": 1, "scope_cta_contains": SCOPE,
                    "exclude_reels": bad_exclude,
                    "treatment": good_treatment, "reason": "x"}
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(document, handle)
        with pytest.raises(cta_rx.ReelCtaTreatmentError):
            cta_rx.load_treatment(root)


# ── The declaration is checked, never asserted ───────────────────────

def test_a_malformed_declaration_refuses(tmp_path):
    root = str(tmp_path)
    external = os.path.join(root, "external")
    os.makedirs(external, exist_ok=True)
    path = os.path.join(external, "reel_cta.json")
    good_treatment = {"element": "title_lockup", "entrance": "fade",
                      "exit": "fade", "anchor": "centre", "color": GOLD}
    bad_documents = [
        {"version": 1, "scope_cta_contains": SCOPE,
         "treatment": dict(good_treatment, element="ticker_tape"),
         "reason": "x"},
        {"version": 1, "scope_cta_contains": SCOPE,
         "treatment": dict(good_treatment, element="subject_emblem"),
         "reason": "x"},
        {"version": 1, "scope_cta_contains": SCOPE,
         "treatment": dict(good_treatment, entrance="wipe"),
         "reason": "x"},
        {"version": 1, "scope_cta_contains": SCOPE,
         "treatment": dict(good_treatment, anchor="everywhere"),
         "reason": "x"},
        {"version": 1, "scope_cta_contains": SCOPE,
         "treatment": dict(good_treatment, color="gold"),
         "reason": "x"},
        {"version": 1, "scope_cta_contains": "  ",
         "treatment": good_treatment, "reason": "x"},
        {"version": 1, "scope_cta_contains": SCOPE,
         "treatment": good_treatment, "reason": "  "},
        {"version": 99, "scope_cta_contains": SCOPE,
         "treatment": good_treatment, "reason": "x"},
    ]
    for document in bad_documents:
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(document, handle)
        with pytest.raises(cta_rx.ReelCtaTreatmentError):
            cta_rx.load_treatment(root)


def test_an_unreadable_declaration_refuses(tmp_path):
    root = str(tmp_path)
    external = os.path.join(root, "external")
    os.makedirs(external, exist_ok=True)
    with open(os.path.join(external, "reel_cta.json"), "w",
              encoding="utf-8") as handle:
        handle.write("{not json")
    with pytest.raises(cta_rx.ReelCtaTreatmentError):
        cta_rx.load_treatment(root)


def test_case_insensitive_treatment_values_are_accepted(tmp_path):
    root = _declare(tmp_path, treatment={
        "element": "Title_Lockup", "entrance": "FADE", "exit": "Fade",
        "anchor": "Centre", "color": "#fbf0b8"})
    loaded = cta_rx.load_treatment(root)
    assert loaded["treatment"] == {
        "element": "title_lockup", "entrance": "fade", "exit": "fade",
        "anchor": "centre", "color": "#fbf0b8"}
