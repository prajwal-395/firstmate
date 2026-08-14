def get_target_duration_zone(data: dict) -> tuple[float, float, float]:
    """
    Returns (min_duration, target_duration, max_duration) in seconds.
    Precedence:
    1. Explicitly specified project target (project_config.target_duration_seconds) OVERRIDES the brand default.
       If project target is present, acceptable variance is +/- 10% (i.e. min = target * 0.9, max = target * 1.1).
    2. Otherwise, fall back to brand template's content.target_duration_seconds (min, max). Target is average.
    3. If neither is available, default to ~60s (min 54, max 66).
    """
    project_config = data.get("project_config", {})
    brand_template = data.get("brand_template", {})

    # 1. Project config override
    target_dur = project_config.get("target_duration_seconds") if isinstance(project_config, dict) else None
    if target_dur is not None and float(target_dur) > 0:
        target_f = float(target_dur)
        return target_f * 0.9, target_f, target_f * 1.1

    # 2. Brand template fallback
    if isinstance(brand_template, dict):
        content_slots = brand_template.get("content", {})
        brand_dur = content_slots.get("target_duration_seconds", {})
        if isinstance(brand_dur, dict) and "min" in brand_dur and "max" in brand_dur:
            min_dur = float(brand_dur["min"])
            max_dur = float(brand_dur["max"])
            target = (min_dur + max_dur) / 2.0
            return min_dur, target, max_dur

    # 3. Default
    return 54.0, 60.0, 66.0
