FAIRLIGHT_PRESETS = {
    "dialogue_enhancement": {
        "description": "Clean dialogue with noise reduction, de-ess, compression, and presence boost",
        "chain": [
            {"type": "noise_reduction", "threshold": -30, "reduction": 15},
            {"type": "de_esser", "frequency": 6500, "threshold": -20},
            {"type": "compressor", "threshold": -18, "ratio": 3.0, "attack_ms": 5, "release_ms": 100},
            {"type": "eq", "bands": [
                {"freq": 100, "type": "highpass", "q": 0.7},
                {"freq": 3000, "gain": 2.0, "q": 1.5, "type": "peak"},
                {"freq": 8000, "gain": 1.5, "q": 1.0, "type": "peak"}
            ]}
        ],
        "target_lufs": -16
    },
    "podcast_master": {
        "description": "Broadcast-ready podcast audio with heavy compression and limiting",
        "chain": [
            {"type": "noise_reduction", "threshold": -35, "reduction": 20},
            {"type": "compressor", "threshold": -20, "ratio": 4.0, "attack_ms": 3, "release_ms": 80},
            {"type": "eq", "bands": [
                {"freq": 80, "type": "highpass", "q": 0.7},
                {"freq": 2500, "gain": 3.0, "q": 1.2, "type": "peak"},
                {"freq": 10000, "gain": -2.0, "q": 0.8, "type": "shelf"}
            ]},
            {"type": "limiter", "threshold": -1.0, "release_ms": 50}
        ],
        "target_lufs": -14
    },
    "music_forward": {
        "description": "Music-priority mix with wider stereo and less compression",
        "chain": [
            {"type": "eq", "bands": [
                {"freq": 60, "type": "highpass", "q": 0.5},
                {"freq": 250, "gain": -2.0, "q": 1.0, "type": "peak"},
                {"freq": 12000, "gain": 2.0, "q": 0.7, "type": "shelf"}
            ]},
            {"type": "stereo_widener", "amount": 1.3},
            {"type": "compressor", "threshold": -12, "ratio": 2.0, "attack_ms": 10, "release_ms": 200},
            {"type": "limiter", "threshold": -0.5, "release_ms": 100}
        ],
        "target_lufs": -14
    },
    "ambient_bed": {
        "description": "Background ambient audio with low volume and soft rolloff",
        "chain": [
            {"type": "eq", "bands": [
                {"freq": 200, "type": "highpass", "q": 0.5},
                {"freq": 5000, "type": "lowpass", "q": 0.7}
            ]},
            {"type": "compressor", "threshold": -25, "ratio": 2.0, "attack_ms": 20, "release_ms": 300}
        ],
        "target_lufs": -24
    }
}

def get_preset(name: str) -> dict:
    return FAIRLIGHT_PRESETS.get(name, FAIRLIGHT_PRESETS["dialogue_enhancement"])

def select_preset_for_content(content_type: str, brand_audio: dict) -> str:
    """
    Choose preset based on content type (interview, vlog, cinematic) 
    and brand audio preferences.
    """
    if brand_audio and "preferred_preset" in brand_audio:
        preset_name = brand_audio["preferred_preset"]
        if preset_name in FAIRLIGHT_PRESETS:
            return preset_name

    content_type = content_type.lower() if content_type else "vlog"
    
    if content_type in ["podcast", "interview"]:
        return "podcast_master"
    elif content_type in ["cinematic", "music_video"]:
        return "music_forward"
    elif content_type in ["ambient", "b_roll"]:
        return "ambient_bed"
    else:
        return "dialogue_enhancement"


# There is no per-item `apply_fairlight_preset` here, and there must not
# be one again. It returned True having applied nothing: the Resolve
# scripting API exposes no per-parameter Fairlight EQ/compressor
# controls on a timeline item, so the stub "applied" the preset chain
# with `pass` and the build printed "Applied Fairlight preset" for an
# EQ that never happened. A fake success is worse than no function.
# The preset NAME still travels (4.04 selects it, the manifest records
# it under audio.fairlight_preset) and the one real application is the
# timeline-level `ApplyFairlightPresetToCurrentTimeline` call in
# resolve_build_timeline, which is judged by what Resolve returns.
