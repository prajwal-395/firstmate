import json
import os
import subprocess
import tempfile
import pytest

from library.tools.render_check import run_checks

# The frame these fixtures are built at, stated once. `run_checks` grades
# geometry against what the caller DECLARES, not against a constant, so a
# caller that declares nothing gets a "not checked" failure rather than a
# pass - which is what the two tests at the bottom of this file pin.
DECLARED_FRAME = (1080, 1920)

def build_fixture_video(path, width=1080, height=1920, duration=2.0, has_text=True, audio_levels=(-14, -14), black_hole=False, freeze=False):
    # We will build the video by constructing a filtergraph
    
    # Background
    bg = f"testsrc=s={width}x{height}:r=24:d={duration},scroll=h=0.01"
    
    # Text overlay: we simulate a caption by overlaying a white box.
    # To make it appear only from 0.5 to 1.5, we create a white color source and overlay it with enable
    overlay = ""
    if has_text:
        # Overlay a white box at bottom
        overlay = f",color=c=white:s=600x150:r=24:d={duration} [box]; [0:v][box] overlay=x=(W-600)/2:y=H-300:enable='between(t,0.5,1.5)' [v1]"
    else:
        overlay = "; [0:v] copy [v1]"
        
    black_filter = ""
    if black_hole:
        black_filter = f"; color=c=black:s={width}x{height}:r=24:d={duration} [blk]; [v1][blk] overlay=enable='between(t,1.5,1.6)' [v2]"
    else:
        black_filter = "; [v1] copy [v2]"
        
    freeze_filter = ""
    if freeze:
        freeze_filter = "; [v2] loop=loop=24:size=1:start=24 [vout]"
    else:
        freeze_filter = "; [v2] copy [vout]"

    vfilter = f"{bg} [0:v] {overlay} {black_filter} {freeze_filter}"

    a1_vol = 10 ** (audio_levels[0] / 20.0) if audio_levels[0] > -90 else 0
    a2_vol = 10 ** (audio_levels[1] / 20.0) if audio_levels[1] > -90 else 0
    audio_filter = f"aevalsrc=exprs='{a1_vol}*sin(440*2*PI*t)*lt(t,1) + {a2_vol}*sin(880*2*PI*t)*gt(t,1)':d={duration}"

    cmd = [
        'ffmpeg', '-y', '-f', 'lavfi', '-i', bg,
        '-f', 'lavfi', '-i', audio_filter,
        '-filter_complex', vfilter,
        '-map', '[vout]', '-map', '1:a',
        '-c:v', 'libx264', '-preset', 'ultrafast', '-crf', '0', '-c:a', 'aac', '-r', '24', path
    ]
    subprocess.run(cmd, capture_output=True, check=True)


@pytest.fixture
def plan_data(tmp_path):
    overlay_path = str(tmp_path / "overlay.mov")
    # Generate a dummy overlay with alpha (a white box in the bottom center)
    subprocess.run([
        'ffmpeg', '-y', '-f', 'lavfi', 
        '-i', 'color=c=black@0.0:s=1080x1920:r=24:d=2.0,format=rgba', 
        '-f', 'lavfi', 
        '-i', 'color=c=white@1.0:s=600x150:r=24:d=2.0,format=rgba',
        '-filter_complex', '[0:v][1:v]overlay=x=(W-600)/2:y=H-300:format=auto[v]',
        '-map', '[v]', '-c:v', 'prores_ks', '-profile:v', '4', '-pix_fmt', 'yuva444p10le', overlay_path
    ], capture_output=True, check=True)

    return {
        "plan_seconds": 2.0,
        "placements": [
            {
                "speaker": "SPEAKER_1",
                "record_seconds": 0.0,
                "source_in": 0.0,
                "source_out": 1.0,
                "source_file": "dummy1.mov"
            },
            {
                "speaker": "SPEAKER_2",
                "record_seconds": 1.0,
                "source_in": 0.0,
                "source_out": 1.0,
                "source_file": "dummy2.mov"
            }
        ],
        "subtitle_overlay": {
            "segments": [
                {
                    "overlay_path": overlay_path,
                    "timeline_start": 0.5,
                    "timeline_end": 1.5,
                    "source_in_frame": 0
                }
            ]
        },
        "master_holes": []
    }


def test_clean_case(tmp_path, plan_data):
    video_path = str(tmp_path / "clean.mp4")
    build_fixture_video(video_path, has_text=True, audio_levels=(-14, -14))
    findings = run_checks(video_path, plan_data, expected_frame=DECLARED_FRAME)
    failures = [f for f in findings if not f.passed]
    assert len(failures) == 0, f"Expected 0 failures, got: {failures}"

def test_geometry_defect(tmp_path, plan_data):
    video_path = str(tmp_path / "geom.mp4")
    build_fixture_video(video_path, width=1920, height=1080)
    findings = run_checks(video_path, plan_data, expected_frame=DECLARED_FRAME)
    failures = [f for f in findings if not f.passed]
    assert any(f.metric == 'geometry' for f in failures)

def test_duration_defect(tmp_path, plan_data):
    video_path = str(tmp_path / "dur.mp4")
    build_fixture_video(video_path, duration=1.5)
    findings = run_checks(video_path, plan_data, expected_frame=DECLARED_FRAME)
    failures = [f for f in findings if not f.passed]
    assert len(failures) == 1
    assert failures[0].metric == "duration"

def test_audio_missing_speaker(tmp_path, plan_data):
    video_path = str(tmp_path / "audio.mp4")
    build_fixture_video(video_path, audio_levels=(-14, -99))
    findings = run_checks(video_path, plan_data, expected_frame=DECLARED_FRAME)
    failures = [f for f in findings if not f.passed]
    assert len(failures) == 1
    assert failures[0].metric == "audio_speakers"
    assert "SPEAKER_2" in failures[0].message

def test_missing_caption(tmp_path, plan_data):
    video_path = str(tmp_path / "nocap.mp4")
    build_fixture_video(video_path, has_text=False)
    findings = run_checks(video_path, plan_data, expected_frame=DECLARED_FRAME)
    failures = [f for f in findings if not f.passed]
    assert len(failures) == 1
    assert failures[0].metric == "captions"
    assert "not visibly" in failures[0].message

def test_black_hole(tmp_path, plan_data):
    video_path = str(tmp_path / "black.mp4")
    build_fixture_video(video_path, black_hole=True)
    findings = run_checks(video_path, plan_data, expected_frame=DECLARED_FRAME)
    failures = [f for f in findings if not f.passed]
    assert len(failures) == 1
    assert failures[0].metric == "black_frames"
    assert "black hole" in failures[0].message

def test_freeze_frame(tmp_path, plan_data):
    video_path = str(tmp_path / "freeze.mp4")
    build_fixture_video(video_path, freeze=True)
    findings = run_checks(video_path, plan_data, expected_frame=DECLARED_FRAME)
    failures = [f for f in findings if not f.passed and f.metric == 'freeze_frames']
    assert len(failures) == 1
    assert failures[0].metric == "freeze_frames"
    
def test_inherited_black_hole(tmp_path, plan_data):
    video_path = str(tmp_path / "black_inherited.mp4")
    build_fixture_video(video_path, black_hole=True)
    plan_data["master_holes"] = [{"at_seconds": 1.5, "length": 2}]
    findings = run_checks(video_path, plan_data, expected_frame=DECLARED_FRAME)
    failures = [f for f in findings if not f.passed]
    # Inherited hole should not fail
    assert len(failures) == 0


def test_absent_caption_plan_reports_failure(tmp_path):
    from library.tools.render_check import check_captions
    video_path = str(tmp_path / "dummy.mp4")
    # Empty dictionary without caption fields
    findings = check_captions(video_path, {})
    assert len(findings) == 1
    assert findings[0].metric == "captions"
    assert findings[0].passed is False
    assert "absent" in findings[0].message


def test_explicit_zero_captions_plan_passes(tmp_path):
    from library.tools.render_check import check_captions
    video_path = str(tmp_path / "dummy.mp4")
    # Explicitly declared empty segments
    findings = check_captions(video_path, {"subtitle_overlay": {"segments": []}})
    assert len(findings) == 1
    assert findings[0].metric == "captions"
    assert findings[0].passed is True
    assert "Zero captions planned" in findings[0].message


def test_truly_blank_overlay_still_fails(tmp_path):
    """The span probes must keep their teeth: an overlay blank at EVERY
    probe across the span is still reported, not waved through."""
    from library.tools.render_check import check_captions

    width, height = 1080, 1920
    timeline_start, timeline_end = 0.5, 2.5
    dur = timeline_end - timeline_start
    overlay_path = str(tmp_path / "blank_overlay.mov")
    subprocess.run([
        'ffmpeg', '-y', '-f', 'lavfi',
        '-i', f'color=c=black@0.0:s={width}x{height}:r=24:d={dur},format=rgba',
        '-map', '0:v', '-c:v', 'prores_ks', '-profile:v', '4',
        '-pix_fmt', 'yuva444p10le', overlay_path,
    ], capture_output=True, check=True)
    video_path = str(tmp_path / "blank_caption.mp4")
    build_fixture_video(video_path, has_text=True)

    plan = {"subtitle_overlay": {"segments": [{
        "overlay_path": overlay_path,
        "timeline_start": timeline_start,
        "timeline_end": timeline_end,
        "source_in_frame": 0,
    }]}}
    findings = check_captions(video_path, plan)
    failures = [f for f in findings if not f.passed]
    assert len(failures) == 1
    assert "blank" in failures[0].message


def test_caption_blank_verdict_probes_the_span_not_one_instant(tmp_path):
    """The alpha blank-caption finding must not decide a span from one instant.

    D5's single-pick shape on another surface: the overlay file carries ink
    across the segment but is blank exactly at the one instant the check
    samples - a hard cut or fade inside the file, the way 001's caption
    cards cut mid-file. The caption is drawn, so no "overlay is blank"
    failure may be reported.
    """
    from library.tools.render_check import check_captions

    width, height = 1080, 1920
    timeline_start, timeline_end = 0.5, 2.5
    dur = timeline_end - timeline_start
    # The instant the single-pick code samples, derived from its own
    # expression - never a copied literal.
    sampled_offset = min(0.5, dur / 2)
    # A blank window around that instant: generous enough that seek slop
    # cannot hide it, narrow enough that the rest of the span keeps ink.
    wb0, wb1 = sampled_offset - 0.25, sampled_offset + 0.25

    overlay_path = str(tmp_path / "cut_overlay.mov")
    subprocess.run([
        'ffmpeg', '-y', '-f', 'lavfi',
        '-i', f'color=c=black@0.0:s={width}x{height}:r=24:d={dur},format=rgba',
        '-f', 'lavfi',
        '-i', f'color=c=white@1.0:s=600x150:r=24:d={dur},format=rgba',
        '-filter_complex', (
            f'[0:v][1:v]overlay=x=(W-600)/2:y=H-300:format=auto'
            f":enable='lt(t,{wb0})+gt(t,{wb1})'[v]"
        ),
        '-map', '[v]', '-c:v', 'prores_ks', '-profile:v', '4',
        '-pix_fmt', 'yuva444p10le', overlay_path,
    ], capture_output=True, check=True)

    # The render draws the same box where the overlay carries ink, so the
    # visibility half of the check has a matching frame to compare.
    ink_t = timeline_start + dur * 0.5
    box_on0, box_on1 = ink_t - 0.5, ink_t + 0.5
    video_path = str(tmp_path / "cut_caption.mp4")
    subprocess.run([
        'ffmpeg', '-y', '-f', 'lavfi',
        '-i', f'testsrc=s={width}x{height}:r=24:d={dur}',
        '-filter_complex', (
            f'color=c=white:s=600x150:r=24:d={dur}[box];'
            f'[0:v][box]overlay=x=(W-600)/2:y=H-300'
            f":enable='between(t,{box_on0},{box_on1})'[vout]"
        ),
        '-map', '[vout]',
        '-c:v', 'libx264', '-preset', 'ultrafast', '-crf', '0',
        '-r', '24', video_path,
    ], capture_output=True, check=True)

    plan = {"subtitle_overlay": {"segments": [{
        "overlay_path": overlay_path,
        "timeline_start": timeline_start,
        "timeline_end": timeline_end,
        "source_in_frame": 0,
    }]}}
    findings = check_captions(video_path, plan)
    failures = [f for f in findings if not f.passed]
    assert failures == [], f"caption drawn across the span reported as failed: {failures}"



# ── The shape is graded against a DECLARATION, not against vertical ───

def test_a_declared_horizontal_render_is_not_a_geometry_defect(tmp_path,
                                                               plan_data):
    """A 1920x1080 file is correct where 1920x1080 was declared.

    `check_geometry` read `width == 1080 and height == 1920`, so a
    project delivering 16:9 long-form had every correct render marked a
    defect - a gate that FAILS correct output (AGENTS.md 10.4).

    The input that breaks this: putting the literal comparison back, or
    defaulting `expected_frame` to (1080, 1920).
    """
    video_path = str(tmp_path / "wide.mp4")
    build_fixture_video(video_path, width=1920, height=1080)
    findings = run_checks(video_path, plan_data, expected_frame=(1920, 1080))
    geometry = [f for f in findings if f.metric == "geometry"]
    assert geometry and geometry[0].passed, geometry

    # ... and the same file IS a defect where vertical was declared.
    findings = run_checks(video_path, plan_data, expected_frame=(1080, 1920))
    geometry = [f for f in findings if f.metric == "geometry"]
    assert geometry and not geometry[0].passed


def test_an_undeclared_frame_is_reported_never_passed(tmp_path, plan_data):
    """No declared frame means the shape was NOT CHECKED, and says so.

    The input that breaks this: making the no-frame case pass. An
    unchecked shape reported as a pass is the exact incident this check
    exists for - a correct vertical timeline rendering out landscape
    with every other check green.
    """
    video_path = str(tmp_path / "clean.mp4")
    build_fixture_video(video_path)
    findings = run_checks(video_path, plan_data)
    geometry = [f for f in findings if f.metric == "geometry"]
    assert geometry and not geometry[0].passed
    assert "not checked" in geometry[0].message
