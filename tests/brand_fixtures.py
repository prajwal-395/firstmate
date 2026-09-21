"""Synthetic brand templates for tests.

The product ships no templates (captain, 2026-09-21): a project carries
its own `brand.json`, and `library/templates/` is gone.  A test must not
depend on one client's tagline or hexes, so every fixture here is
synthetic - invented names, invented copy, invented colours.

The four GENERIC shapes below mirror the parameter values the deleted
product templates carried, because tests pin BEHAVIOUR against them (a
0.0 framing intent letterboxes; a 1.0 one fills; a 0.3 VFX intensity
reaches the prompt as 0.3).  The values are parameter sets, not identity,
so keeping them keeps the coverage meaning what it meant.

The CLIENT shape carries synthetic copy throughout - no real headline,
tagline, URL or hex.
"""

import json

import yaml

SYNTHETIC_DEFAULT = {
    "series_id": "synthetic_default",
    "delivery_format": "vertical_1080x1920",
    "style": {
        "color_palette": ["#F5F5F5", "#9AA0A6", "#141414"],
        "typography": {"font": "Montserrat", "size": 160, "weight": 800},
        "energy_profile": "high",
    },
    "effect": {
        "transition_types": [
            "hard_cut", "jump_cut", "match_cut", "fade_to_black",
            "defocus", "flash", "zoom_blur",
        ],
        "transition_duration_ms": {"min": 200, "max": 500},
        "vfx_intensity": 0.5,
        "subtitle_style": "default_subtitles",
        "caption_case": "lowercase",
    },
    "content": {
        "series_title": "Synthetic Series",
        "channel_name": "Synthetic Channel",
        "target_duration_seconds": {"min": 30, "max": 60},
    },
}

SYNTHETIC_CINEMATIC = {
    "series_id": "synthetic_cinematic",
    "delivery_format": "vertical_1080x1920",
    "style": {
        "energy_profile": "calm",
        "framing_intent": 0.0,
        "color_palette": ["#223344", "#aabbcc", "#111111"],
    },
    "effect": {
        "transition_types": ["hard_cut", "match_cut", "fade_to_black", "defocus"],
        "vfx_intensity": 0.3,
        "subtitle_style": "minimal",
        "sfx_density": "moderate",
        "caption_case": "lowercase",
    },
    "content": {
        "music_genre": ["orchestral", "atmospheric"],
        "target_duration_seconds": {"min": 180, "max": 600},
    },
}

SYNTHETIC_INTERVIEW = {
    "series_id": "synthetic_interview",
    "delivery_format": "vertical_1080x1920",
    "style": {
        "energy_profile": "moderate",
        "color_palette": ["#f5f5f5", "#333333", "#ddab7e"],
    },
    "effect": {
        "transition_types": ["hard_cut", "jump_cut", "defocus"],
        "vfx_intensity": 0.1,
        "subtitle_style": "clean_standard",
        "motion_accents": True,
        "motion_progress_bar": False,
        "sfx_density": "sparse",
        "caption_case": "lowercase",
    },
    "content": {
        "music_genre": ["ambient", "acoustic"],
        "target_duration_seconds": {"min": 300, "max": 900},
    },
}

SYNTHETIC_SHORTFORM = {
    "series_id": "synthetic_shortform",
    "delivery_format": "vertical_1080x1920",
    "style": {
        "energy_profile": "high",
        "framing_intent": 1.0,
        "color_palette": ["#ff0055", "#00ffcc", "#ffffff", "#000000"],
    },
    "effect": {
        "transition_types": ["hard_cut", "jump_cut", "zoom_blur", "flash"],
        "vfx_intensity": 0.8,
        "subtitle_style": "bold_large",
        "motion_accents": True,
        "motion_progress_bar": True,
        "sfx_density": "dense",
        "caption_case": "lowercase",
    },
    "content": {
        "music_genre": ["electronic", "upbeat"],
        "target_duration_seconds": {"min": 30, "max": 60},
    },
}

SYNTHETIC_CLIENT = {
    "series_id": "synthetic_client",
    "delivery_format": "vertical_1080x1920",
    "style": {
        "color_palette": ["#E8A33D", "#F5F5F5", "#2E4057"],
        "typography": {"font": "Montserrat", "size": 42, "weight": "bold"},
        "energy_profile": "moderate",
    },
    "effect": {
        "transition_types": ["hard_cut", "fade_to_black", "defocus"],
        "transition_duration_ms": {"min": 200, "max": 500},
        "vfx_intensity": 0.2,
        "subtitle_style": "default_subtitles",
        "sfx_density": "sparse",
        "caption_case": "as_written",
    },
    "content": {
        "channel_name": "Synthetic Client",
        "bookends": {
            "intro": {
                "composition": "ExampleIntro",
                "source": "compositions/ExampleIntro.tsx",
                "duration_seconds": 3.0,
                "props": {"accentColor": "#E8A33D", "style": "full"},
            },
            "end_card": {
                "composition": "ExampleEndCard",
                "source": "compositions/ExampleEndCard.tsx",
                "duration_seconds": 5.0,
                "props": {
                    "headline": "Example headline for tests",
                    "tagline": "Example tagline for tests",
                    "websiteUrl": "example.com",
                    "accentColor": "#E8A33D",
                    "bgColor": "#2E4057",
                },
            },
        },
        "closing_lockup": {
            "lines": ["First synthetic line", "example.com"],
            "color": "#F5F5F5",
        },
        "music_genre": ["ambient", "corporate"],
        "target_duration_seconds": {"min": 30, "max": 90},
    },
}

ALL_SYNTHETIC = {
    "synthetic_default": SYNTHETIC_DEFAULT,
    "synthetic_cinematic": SYNTHETIC_CINEMATIC,
    "synthetic_interview": SYNTHETIC_INTERVIEW,
    "synthetic_shortform": SYNTHETIC_SHORTFORM,
    "synthetic_client": SYNTHETIC_CLIENT,
}


def write_templates_dir(path, names=None):
    """Write synthetic `<name>.yaml` files under `path`; return the dir.

    The injectable `templates_dir` half of the resolvers: a test that
    needs name resolution points the resolver here instead of at the
    (removed) product directory.
    """
    import os

    os.makedirs(path, exist_ok=True)
    for name in names or ALL_SYNTHETIC:
        with open(os.path.join(path, f"{name}.yaml"), "w",
                  encoding="utf-8") as handle:
            yaml.safe_dump(ALL_SYNTHETIC[name], handle)
    return str(path)


def write_brand_json(project_dir, template):
    """Write `template` (a dict, or a name in ALL_SYNTHETIC) as the
    project's own `brand.json`; return the file path."""
    import os

    data = ALL_SYNTHETIC[template] if isinstance(template, str) else template
    out = os.path.join(str(project_dir), "brand.json")
    with open(out, "w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=2, sort_keys=True)
        handle.write("\n")
    return out
