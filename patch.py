import json
import re

with open('library/steps/step_6_01_render/fcpxml_generator.py', 'r') as f:
    content = f.read()

# 1. Update docstring
content = content.replace('"""\nFCPXML 1.10 Generator', '"""\nFCPXML 1.10 Generator — Manifest to DaVinci Resolve native format.\n\nNow upgraded with Palmier Pro features:\n- Conform-fit position/scale math\n- Source timecodes\n- Speed/Retiming\n- Crop & Opacity support')

# 2. Add helpers
helpers = """
def _rational_time_str(num, den):
    \"\"\"Format a rational time as FCPXML string (e.g. '7/2s').\"\"\"
    if num == 0:
        return "0s"
    frac = Fraction(num, den)
    if frac.denominator == 1:
        return f"{frac.numerator}s"
    return f"{frac.numerator}/{frac.denominator}s"


def _read_source_timecode(filepath):
    \"\"\"Read embedded start timecode from a media file using ffprobe.
    Returns (numerator, denominator) for the start time, or (0, 1) if unavailable.
    Ported from Palmier Pro's SourceTimingReader.\"\"\"
    import subprocess
    try:
        result = subprocess.run(
            ['ffprobe', '-v', 'quiet', '-print_format', 'json',
             '-show_entries', 'format_tags=timecode:stream_tags=timecode',
             filepath],
            capture_output=True, text=True, timeout=5
        )
        if result.returncode != 0:
            return (0, 1)
        data = json.loads(result.stdout)
        # Try format tags first, then stream tags
        tc_str = None
        fmt_tags = data.get('format', {}).get('tags', {})
        tc_str = fmt_tags.get('timecode')
        if not tc_str:
            for stream in data.get('streams', []):
                tc_str = stream.get('tags', {}).get('timecode')
                if tc_str:
                    break
        if not tc_str:
            return (0, 1)
        # Parse HH:MM:SS:FF or HH:MM:SS;FF
        return _parse_timecode(tc_str)
    except (subprocess.TimeoutExpired, FileNotFoundError, json.JSONDecodeError):
        return (0, 1)


def _parse_timecode(tc_str, fps=30):
    \"\"\"Parse SMPTE timecode string to rational seconds (num, den).\"\"\"
    # Handle both : and ; (drop-frame) separators
    parts = tc_str.replace(';', ':').split(':')
    if len(parts) != 4:
        return (0, 1)
    try:
        h, m, s, f = int(parts[0]), int(parts[1]), int(parts[2]), int(parts[3])
    except ValueError:
        return (0, 1)
    total_frames = ((h * 3600 + m * 60 + s) * fps) + f
    frac = Fraction(total_frames, fps)
    return (frac.numerator, frac.denominator)


def _format_number(value):
    \"\"\"Format a float for FCPXML: integer when whole, up to 4 decimal places otherwise.
    Ported from Palmier Pro.\"\"\"
    rounded = round(value, 4)
    if rounded == int(rounded):
        return str(int(rounded))
    s = f"{rounded:.4f}".rstrip('0').rstrip('.')
    return s


def _fit_fractions(source_w, source_h, seq_w, seq_h):
    \"\"\"Per-axis conform-fit fractions. Resolve scales imported positions by
    these at render time, so we pre-divide to compensate.
    Ported from Palmier Pro's FCPXMLExporter.swift.\"\"\"
    if source_w <= 0 or source_h <= 0:
        return (1.0, 1.0)
    source_aspect = source_w / source_h
    frame_aspect = seq_w / seq_h
    if source_aspect >= frame_aspect:
        return (1.0, frame_aspect / source_aspect)
    else:
        return (source_aspect / frame_aspect, 1.0)


def _transform_attrs(clip, seq_w, seq_h, rotation=0.0, flip_h=False, flip_v=False):
    \"\"\"Build adjust-transform attributes with conform-fit compensation.
    Ported from Palmier Pro's FCPXMLExporter.swift.\"\"\"
    source_res = clip.get("source_resolution")  # [w, h] or None
    fit_w, fit_h = (1.0, 1.0)
    if source_res:
        fit_w, fit_h = _fit_fractions(source_res[0], source_res[1], seq_w, seq_h)
    
    # Scale: divide out the aspect-fit
    sx = clip.get("scale_x", 1.0) / fit_w
    sy = clip.get("scale_y", 1.0) / fit_h
    if flip_h: sx = -sx
    if flip_v: sy = -sy
    
    # Position: 1% of frame height units, center origin, +Y up
    center_x = clip.get("center_x", 0.5)
    center_y = clip.get("center_y", 0.5)
    unit = seq_h / 100.0
    pos_x = (center_x - 0.5) * seq_w / unit / fit_w
    pos_y = (0.5 - center_y) * seq_h / unit / fit_h
    
    # Rotation: FCPXML is counter-clockwise positive, negate
    rot = -rotation
    
    scale_str = f"{_format_number(sx)} {_format_number(sy)}"
    pos_str = f"{_format_number(pos_x)} {_format_number(pos_y)}"
    
    attrs = f'scale="{scale_str}"'
    if abs(rot) > 0.005:
        attrs += f' rotation="{_format_number(rot)}"'
    attrs += f' anchor="0 0" position="{pos_str}"'
    return attrs


def _rational_speed(speed):
    \"\"\"Approximate a speed value with an exact small fraction (p, q).
    Ported from Palmier Pro's FCPXMLExporter.swift.\"\"\"
    best_p, best_q, best_err = 1, 1, float('inf')
    for q in range(1, 1001):
        p = round(speed * q)
        if p <= 0:
            continue
        err = abs(speed - p / q)
        if err < best_err:
            best_p, best_q, best_err = p, q, err
            if err == 0:
                break
    return (best_p, best_q)


def _time_map_xml(speed, media_frames, fps, origin=(0, 1)):
    \"\"\"Build <timeMap> element for retimed clips.
    Ported from Palmier Pro's FCPXMLExporter.swift.\"\"\"
    if abs(speed - 1.0) < 0.001 or media_frames <= 0:
        return None
    p, q = _rational_speed(speed)
    origin_time = _rational_time_str(origin[0], origin[1])
    end_output = _rational_time_str(media_frames * q, fps * p)
    end_source_num = media_frames * origin[1] + origin[0] * fps
    end_source_den = fps * origin[1]
    end_source = _rational_time_str(end_source_num, end_source_den)
    return (
        '<timeMap frameSampling="floor">\\n'
        f'    <timept time="0s" value="{origin_time}" interp="linear"/>\\n'
        f'    <timept time="{end_output}" value="{end_source}" interp="linear"/>\\n'
        '</timeMap>'
    )


def _clip_start_retimed(trim_start_frame, speed, fps, origin=(0, 1)):
    \"\"\"Calculate clip start attribute for retimed clips.
    Ported from Palmier Pro's FCPXMLExporter.swift.\"\"\"
    if abs(speed - 1.0) < 0.001:
        return None
    p, q = _rational_speed(speed)
    return _rational_time_str(trim_start_frame * q, fps * p)


def _crop_xml(crop, source_res, seq_w, seq_h):
    \"\"\"Build <adjust-crop> element with Resolve-specific units.
    Ported from Palmier Pro's FCPXMLExporter.swift.\"\"\"
    if not crop:
        return None
    top = crop.get("top", 0.0)
    right = crop.get("right", 0.0)
    bottom = crop.get("bottom", 0.0)
    left = crop.get("left", 0.0)
    # Identity check
    if abs(top) < 0.0001 and abs(right) < 0.0001 and abs(bottom) < 0.0001 and abs(left) < 0.0001:
        return None
    # Resolve-specific units when source dimensions known
    lr_scale = 100.0
    tb_scale = 100.0
    if source_res:
        sw, sh = source_res
        if sw > 0 and sh > 0:
            fit = min(seq_w / sw, seq_h / sh)
            lr_scale = sw * 100.0 / seq_h
            tb_scale = 100.0 / fit
    return (
        '<adjust-crop mode="trim">\\n'
        f'    <trim-rect top="{_format_number(top * tb_scale)}" '
        f'right="{_format_number(right * lr_scale)}" '
        f'bottom="{_format_number(bottom * tb_scale)}" '
        f'left="{_format_number(left * lr_scale)}"/>\\n'
        '</adjust-crop>'
    )


def _blend_xml(opacity):
    \"\"\"Build <adjust-blend> element for opacity.
    Ported from Palmier Pro's FCPXMLExporter.swift.\"\"\"
    if opacity is None or opacity >= 0.9995:
        return None
    return f'<adjust-blend amount="{_format_number(opacity)}"/>'
"""

content = content.replace("def _file_url(path):", helpers + "\n\ndef _file_url(path):")


# 3. Update generate_fcpxml signature
content = content.replace(
    "def generate_fcpxml(manifest, subtitle_data=None, output_path=None,\n                    style=None):",
    "def generate_fcpxml(manifest, subtitle_data=None, output_path=None,\n                    style=None, **kwargs):"
)


# 4. Add timecode cache parsing
timecode_block = """
    # Read source timecodes (disable with timecodes=False for speed)
    read_timecodes = kwargs.get('timecodes', True)
    timecode_cache = {}
    if read_timecodes:
        all_sources = set()
        for track_clips in [v1_clips, v2_clips, a2_clips, a3_clips]:
            for c in track_clips:
                sf = c.get("source_file", "")
                if sf and os.path.isfile(sf):
                    all_sources.add(sf)
        for sf in all_sources:
            timecode_cache[sf] = _read_source_timecode(sf)

    # Default subtitle style
"""
content = content.replace("    # Default subtitle style\n", timecode_block)


# 5. Asset element updates (formats & timecodes)
# To handle custom formats and timecodes we'll do some regex replacements for the asset creation block
# The original code looks like this:
"""
    # ── Build <resources> ──
    resources_lines = []
    resources_lines.append(fmt_vertical)
    resources_lines.append(fmt_landscape)

    for (track, idx), asset_id in clip_asset_ids.items():
        if track == "V1":
...
"""
def replace_resources_block(match):
    return """    # ── Build <resources> ──
    custom_formats = {}
    resources_lines = []
    resources_lines.append(fmt_vertical)
    resources_lines.append(fmt_landscape)

    for (track, idx), asset_id in clip_asset_ids.items():
        if track == "V1":
            clip = v1_clips[idx]
        elif track == "V2":
            clip = v2_clips[idx]
        elif track == "A2":
            clip = a2_clips[idx]
        else:
            continue  # A3 handled separately below

        src = clip["source_file"]
        name = os.path.basename(src)
        src_dur = clip.get("source_duration",
                         clip.get("source_out", clip.get("duration", 300)) + 10)
        dur_rat = _to_rational(src_dur, fps)

        origin = timecode_cache.get(src, (0, 1))
        start_str = _rational_time_str(origin[0], origin[1])

        fmt_id = primary_format
        source_res = clip.get("source_resolution")
        if source_res and len(source_res) == 2 and (source_res[0] != w or source_res[1] != h):
            fmt_id = f"f_{source_res[0]}x{source_res[1]}"
            if fmt_id not in custom_formats:
                custom_formats[fmt_id] = (
                    f'<format name="FFVideoFormatUndefined" '
                    f'width="{source_res[0]}" height="{source_res[1]}" id="{fmt_id}" '
                    f'frameDuration="1/{int(fps)}s"/>'
                )
                resources_lines.append(custom_formats[fmt_id])

        if track == "A2":
            resources_lines.append(
                f'<asset audioChannels="2" hasAudio="1" audioSources="1" '
                f'start="{start_str}" name="{xml_escape(name)}" '
                f'duration="{dur_rat}" id="{asset_id}">'
            )
        else:
            resources_lines.append(
                f'<asset hasVideo="1" audioChannels="4" hasAudio="1" '
                f'audioSources="1" start="{start_str}" name="{xml_escape(name)}" '
                f'duration="{dur_rat}" id="{asset_id}" format="{fmt_id}">'
            )

        resources_lines.append(
            f'    <media-rep kind="original-media" '
            f'src="{_file_url(src)}"/>'
        )
        resources_lines.append('</asset>')

    # SFX assets (A3)
    for (track, idx), asset_id in clip_asset_ids.items():
        if track != "A3":
            continue
        clip = a3_clips[idx]
        src = clip["source_file"]
        name = os.path.basename(src)
        src_dur = clip.get("source_duration", clip.get("duration", 10))
        dur_rat = _to_rational(src_dur, fps)
        
        origin = timecode_cache.get(src, (0, 1))
        start_str = _rational_time_str(origin[0], origin[1])
        
        resources_lines.append(
            f'<asset audioChannels="2" hasAudio="1" audioSources="1" '
            f'start="{start_str}" name="{xml_escape(name)}" '
            f'duration="{dur_rat}" id="{asset_id}">'
        )
        resources_lines.append(
            f'    <media-rep kind="original-media" '
            f'src="{_file_url(src)}"/>'
        )
        resources_lines.append('</asset>')"""

import re
content = re.sub(
    r'    # ── Build <resources> ──\n.*?    # Subtitle overlay asset',
    replace_resources_block,
    content,
    flags=re.DOTALL
)


# 6. Update V1 clips
def replace_v1_block(match):
    return """        # Get source timecode origin for this file
        origin = timecode_cache.get(clip["source_file"], (0, 1))

        # Clip start = trim_start_frame added to origin
        speed = clip.get("speed", 1.0)
        if abs(speed - 1.0) > 0.001:
            start = _clip_start_retimed(src_in_f, speed, fps, origin=origin)
        else:
            if origin[0] != 0:
                start_num = src_in_f * origin[1] + origin[0] * fps
                start_den = fps * origin[1]
                start = _rational_time_str(start_num, start_den)
            else:
                start = _frames_to_rational(src_in_f, fps)

        duration = _frames_to_rational(clip_dur_f, fps)
        src_dur_rat = _to_rational(src_dur, fps)

        # ── Element Builder ──
        has_transform_data = any(k in clip for k in ["source_resolution", "scale_x", "scale_y", "center_x", "center_y", "rotation", "flip_horizontal", "flip_vertical"])
        if has_transform_data:
            base_attrs = _transform_attrs(clip, w, h, clip.get("rotation", 0.0), clip.get("flip_horizontal", False), clip.get("flip_vertical", False))
        else:
            base_attrs = 'anchor="0 0" scale="1 1" position="0 0"'

        vfx = vfx_map.get(ci)
        if vfx and "params" in vfx:
            p = vfx["params"]
            s_start = p.get("scale_start", 1.0)
            s_end = p.get("scale_end", 1.0)
            if s_start != s_end:
                source_res = clip.get("source_resolution")
                fit_w, fit_h = (1.0, 1.0)
                if source_res:
                    fit_w, fit_h = _fit_fractions(source_res[0], source_res[1], w, h)
                
                s_sx_start = s_start / fit_w
                s_sy_start = s_start / fit_h
                s_sx_end = s_end / fit_w
                s_sy_end = s_end / fit_h
                
                transform_xml = (
                    f'<adjust-transform {base_attrs}>\\n'
                    f'    <param value="{_format_number(s_sx_start)} {_format_number(s_sy_start)}" name="scale">\\n'
                    '        <keyframeAnimation>\\n'
                    f'            <keyframe time="0/1s" value="{_format_number(s_sx_start)} {_format_number(s_sy_start)}"/>\\n'
                    f'            <keyframe time="{duration}" value="{_format_number(s_sx_end)} {_format_number(s_sy_end)}"/>\\n'
                    '        </keyframeAnimation>\\n'
                    '    </param>\\n'
                    '</adjust-transform>'
                )
            else:
                transform_xml = f'<adjust-transform {base_attrs}/>'
        else:
            transform_xml = f'<adjust-transform {base_attrs}/>'

        # ── Build clip element ──
        cl = []
        cl.append(
            f'<clip enabled="1" tcFormat="NDF" start="{start}" '
            f'name="{xml_escape(os.path.basename(clip["source_file"]))}" '
            f'duration="{duration}" offset="{offset}" format="{primary_format}">'
        )
        
        tm_xml = _time_map_xml(speed, clip_dur_f, fps, origin=origin)
        if tm_xml:
            for line in tm_xml.split("\\n"):
                cl.append(f'    {line}')
                
        cr_xml = _crop_xml(clip.get("crop"), clip.get("source_resolution"), w, h)
        if cr_xml:
            for line in cr_xml.split("\\n"):
                cl.append(f'    {line}')

        # Conform (tells Resolve how to fit mismatched aspect ratios)
        cl.append('    <adjust-conform type="fit"/>')

        # Transform (with possible VFX keyframes)
        for line in transform_xml.split("\\n"):
            cl.append(f'    {line}')
            
        bl_xml = _blend_xml(clip.get("opacity"))
        if bl_xml:
            cl.append(f'    {bl_xml}')

        # Video + linked audio (lane="-1")"""

content = re.sub(
    r'        start = _frames_to_rational\(src_in_f, fps\).*?# Video \+ linked audio \(lane="-1"\)',
    replace_v1_block,
    content,
    flags=re.DOTALL
)

# 7. Update B-roll clips
def replace_broll_block(match):
    return """            # FCPXML connected clip timing:
            #   start = B-roll's own source_in time
            #   offset = parent_source_in + (broll_timeline - parent_timeline)
            br_rel_pos = br_tl_start - clip_start_f
            br_offset = _frames_to_rational(src_in_f + br_rel_pos, fps)
            
            br_speed = bclip.get("speed", 1.0)
            br_origin = timecode_cache.get(bclip["source_file"], (0, 1))
            if abs(br_speed - 1.0) > 0.001:
                br_start = _clip_start_retimed(br_src_in, br_speed, fps, origin=br_origin)
            else:
                if br_origin[0] != 0:
                    br_start_num = br_src_in * br_origin[1] + br_origin[0] * fps
                    br_start_den = fps * br_origin[1]
                    br_start = _rational_time_str(br_start_num, br_start_den)
                else:
                    br_start = _frames_to_rational(br_src_in, fps)
                    
            br_duration = _frames_to_rational(br_dur, fps)
            br_src_dur_rat = _to_rational(br_src_dur, fps)

            cl.append(
                f'    <clip enabled="1" tcFormat="NDF" '
                f'start="{br_start}" '
                f'lane="1" '
                f'name="{xml_escape(os.path.basename(bclip["source_file"]))}" '
                f'duration="{br_duration}" '
                f'offset="{br_offset}" format="{primary_format}">'
            )
            
            br_tm_xml = _time_map_xml(br_speed, br_dur, fps, origin=br_origin)
            if br_tm_xml:
                for line in br_tm_xml.split("\\n"):
                    cl.append(f'        {line}')
                    
            br_cr_xml = _crop_xml(bclip.get("crop"), bclip.get("source_resolution"), w, h)
            if br_cr_xml:
                for line in br_cr_xml.split("\\n"):
                    cl.append(f'        {line}')
                    
            cl.append('        <adjust-conform type="fit"/>')
                    
            br_has_transform_data = any(k in bclip for k in ["source_resolution", "scale_x", "scale_y", "center_x", "center_y", "rotation", "flip_horizontal", "flip_vertical"])
            if br_has_transform_data:
                br_attrs = _transform_attrs(bclip, w, h, bclip.get("rotation", 0.0), bclip.get("flip_horizontal", False), bclip.get("flip_vertical", False))
                cl.append(f'        <adjust-transform {br_attrs}/>')
            else:
                cl.append('        <adjust-transform anchor="0 0" scale="1 1" position="0 0"/>')
                
            br_bl_xml = _blend_xml(bclip.get("opacity"))
            if br_bl_xml:
                cl.append(f'        {br_bl_xml}')

            cl.append(
                f'        <video ref="{br_asset_id}" start="0/1s" '"""

content = re.sub(
    r'            # FCPXML connected clip timing:.*?f\'        <video ref="\{br_asset_id\}" start="0/1s" \'',
    replace_broll_block,
    content,
    flags=re.DOTALL
)

# 8. Update summary at the bottom
content = content.replace(
    '        print(f"  VFX: {len(vfx_list)} keyframed")',
    '        print(f"  VFX: {len(vfx_list)} keyframed")\n        print(f"  Upgrades: Uses advanced Palmier Math (speed, crop, fit)")'
)

with open('library/steps/step_6_01_render/fcpxml_generator.py', 'w') as f:
    f.write(content)
