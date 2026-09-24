"""Person and object profiles: stable cross-clip identity from v3 detections.

The vision pass describes each clip from scratch ("young man in black
baseball cap ..."), so no step can say WHO is on screen twice. These tests
pin the store that turns per-clip mentions into durable profiles:

- a recurring person links ACROSS clips into one profile;
- a distinct person, a background passerby and a body-part close-up stay
  separate;
- objects link only on distinctive evidence - a generic "white car" in two
  clips is two profiles, not one invented identity;
- names start unset (the captain's to give) and attach explicitly;
- ids are deterministic across runs.
"""
import os
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

from library.tools.person_object_profiles import (
    attach_name,
    build_profiles,
)


def _clip(clip_id, label, category="person", role="primary_subject",
          ranges=None, duration_s=10.0):
    return {
        "clip_id": clip_id,
        "duration_s": duration_s,
        "objects": [{
            "label": label,
            "category": category,
            "role": role,
            "appearances": ranges or [[0.0, duration_s]],
            "readable_text": None,
        }],
    }


def test_recurring_person_links_across_clips():
    clips = [
        _clip("clip_a", "young man in black baseball cap and light grey shirt"),
        _clip("clip_b", "young man in black baseball cap and silver chain"),
        _clip("clip_c", "young man in black baseball cap and grey shirt"),
    ]
    profiles = build_profiles(clips)
    persons = [p for p in profiles if p["kind"] == "person"]
    assert len(persons) == 1
    assert persons[0]["clip_count"] == 3
    assert persons[0]["profile_id"] == "person_01"
    assert {a["clip_id"] for a in persons[0]["appearances"]} == {
        "clip_a", "clip_b", "clip_c"}


def test_background_passerby_is_not_the_subject():
    clips = [
        _clip("clip_a", "young man in black baseball cap and grey shirt",
              role="primary_subject"),
        _clip("clip_a2", "person walking in background",
              role="background"),
    ]
    profiles = build_profiles(clips)
    persons = [p for p in profiles if p["kind"] == "person"]
    assert len(persons) == 2


def test_body_part_closeup_is_not_merged_into_the_person():
    clips = [
        _clip("clip_a", "young man in black baseball cap and grey shirt"),
        _clip("clip_b", "person's hand with silver chain and ring"),
    ]
    profiles = build_profiles(clips)
    persons = [p for p in profiles if p["kind"] == "person"]
    assert len(persons) == 2


def test_names_start_unset_and_attach_explicitly():
    profiles = build_profiles([
        _clip("clip_a", "young man in black baseball cap and grey shirt")])
    assert profiles[0]["name"] is None
    renamed = attach_name(profiles, profiles[0]["profile_id"], "PLACEHOLDER")
    assert renamed["name"] == "PLACEHOLDER"
    assert profiles[0]["name"] == "PLACEHOLDER"


def test_distinctive_object_links_but_generic_one_does_not():
    clips = [
        _clip("clip_a", "SCUFFLEWA Brewing Co. sign", category="object",
              role="background"),
        _clip("clip_b", "SCUFFLEWA Brewing Co. sign", category="object",
              role="background"),
        _clip("clip_a", "white car", category="vehicle",
              role="background", ranges=[[0.0, 5.0]]),
        _clip("clip_b", "white car", category="vehicle",
              role="background", ranges=[[0.0, 5.0]]),
    ]
    profiles = build_profiles(clips)
    sign = [p for p in profiles
            if "SCUFFLEWA" in p["display_label"]]
    cars = [p for p in profiles if p["display_label"] == "white car"]
    assert len(sign) == 1 and sign[0]["clip_count"] == 2
    assert len(cars) == 2
    assert all(c["clip_count"] == 1 for c in cars)


def test_same_clip_mentions_never_link():
    # The pass lists one entry per entity: a clip naming two cars holds
    # two cars. Measured on 001's IMG_1806, whose nine parking-lot cars
    # chained into one invented profile before this guard.
    clips = [{
        "clip_id": "clip_a",
        "duration_s": 3.5,
        "objects": [
            {"label": "dark grey hatchback parked in the middle row",
             "category": "vehicle", "role": "background",
             "appearances": [[0.0, 3.5]], "readable_text": None},
            {"label": "dark grey car parked in the front row",
             "category": "vehicle", "role": "background",
             "appearances": [[0.0, 3.5]], "readable_text": None},
            {"label": "white car parked in the back row",
             "category": "vehicle", "role": "background",
             "appearances": [[0.0, 3.5]], "readable_text": None},
        ],
    }]
    profiles = build_profiles(clips)
    assert len(profiles) == 3
    assert all(p["clip_count"] == 1 for p in profiles)


def test_subset_hub_label_does_not_merge_cars():
    # 001's IMG_1807 interior close-up ("dark gray car with chrome trim")
    # merged with two street cars through the shared hub "dark grey car".
    # A false identity is worse than a missed one: split, and say so.
    clips = [
        _clip("clip_a", "dark gray car with chrome trim", category="vehicle",
              role="background"),
        _clip("clip_b", "dark grey car", category="vehicle",
              role="background"),
        _clip("clip_c", "dark gray car parked on street", category="vehicle",
              role="background"),
    ]
    profiles = build_profiles(clips)
    assert len(profiles) == 3


def test_ids_are_deterministic():
    clips = [
        _clip("clip_b", "older woman in red dress holding a microphone",
              duration_s=50.0),
        _clip("clip_a", "young man in black baseball cap and grey shirt",
              duration_s=10.0),
    ]
    first = build_profiles(clips)
    second = build_profiles(list(reversed(clips)))
    assert [(p["profile_id"], p["display_label"]) for p in first] == [
        (p["profile_id"], p["display_label"]) for p in second]
    # Longest screen time orders first however the input arrives.
    assert first[0]["display_label"].startswith("older woman")
