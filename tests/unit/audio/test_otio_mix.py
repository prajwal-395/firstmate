"""The audio algebra ops apply through the OTIO mix path, with read-back.

Gap C-06: the algebra's `audio.gain`/`audio.pan`/`audio.fade`/
`audio.automation` ops were typed, reversible vocabulary with no
executor - applying one raised "no native apply yet", so an editor
instruction like "fade the music out over the last 5 seconds" could not
be expressed as a typed op. These tests drive the real
`otio_mix.apply_audio_ops` on synthetic OTIO dicts (no model, no
Resolve, no render):

- `audio_op_target` is the ONE translation: it turns an op into a mix
  target, so a later native-mix change (gap D4) swaps its target
  without touching the ops;
- `audio.fade` writes a silence->unity->silence envelope onto the clip's
  Fairlight `volume` parameter, and the read-back shows the faded
  window - the report's fidelity test;
- `audio.gain` writes a static dB level, `audio.automation` a keyframed
  lane, and both read back;
- `audio.pan` refuses by name: Resolve's OTIO carries no measured
  audio-pan parameter (the audio surface is volume only), and an op
  that cannot be written must say so, never be silently dropped.
"""
from library.tools import otio_mix

FPS = 30.0


def _otio_with_clip(*, source_file="music.mp3", start_frame=0,
                    duration=300):
    """A one-track, one-clip OTIO in Resolve's own export dialect.

    The clip carries the Fairlight volume effect with an EMPTY parameter
    list - the measured default, and the reason `apply_mix` inserts the
    parameter rather than patching it.
    """
    media_path = f"/tmp/{source_file}"
    return {
        "OTIO_SCHEMA": "Timeline.1", "name": "scratch",
        "tracks": {"OTIO_SCHEMA": "Stack.1", "name": "", "children": [{
            "OTIO_SCHEMA": "Track.1", "name": "A1", "kind": "Audio",
            "children": [{
                "OTIO_SCHEMA": "Clip.2", "name": source_file,
                "active_media_reference_key": "DEFAULT_MEDIA",
                "media_references": {"DEFAULT_MEDIA": {
                    "OTIO_SCHEMA": "ExternalReference.1",
                    "target_url": f"file://{media_path}"}},
                "source_range": {
                    "OTIO_SCHEMA": "TimeRange.1",
                    "start_time": {"OTIO_SCHEMA": "RationalTime.1",
                                   "rate": FPS, "value": 0.0},
                    "duration": {"OTIO_SCHEMA": "RationalTime.1",
                                 "rate": FPS, "value": float(duration)}},
                "effects": [{
                    "OTIO_SCHEMA": "Effect.1", "name": "",
                    "effect_name": "Resolve Effect",
                    "metadata": {"Resolve_OTIO": {
                        "Effect Name": otio_mix.VOLUME_EFFECT_NAME,
                        "Enabled": True, "Name": "Volume", "Type": 62,
                        "Parameters": []}}}],
            }],
        }]},
    }


def _clips(source_file="music.mp3", start_frame=0, duration=300):
    """The placed clips the ops' unique_ids address (the shadow's shape)."""
    return [{"unique_id": "clip_1", "source_file": f"/tmp/{source_file}",
             "record_in": start_frame, "duration": duration}]


def _volume_parameter_of(otio):
    """The volume parameter on the fixture's one clip, or None."""
    for _track, clip, _start, _frames in otio_mix.audio_clips(otio):
        return otio_mix._volume_parameter_of(clip)
    return None


def test_audio_fade_writes_the_fade_curve_and_reads_it_back():
    """Defect: an `audio.fade` op had no executor, so "fade the music out
    over the last 5 seconds" could not be expressed as a typed op - it
    was either an OTIO mix parameter (a planner output, re-run to change)
    or an ad-hoc script. The op now writes the envelope through the mix
    path and the read-back shows the faded window.
    """
    otio = _otio_with_clip(duration=300)
    ops = [{"op": "audio.fade",
            "params": {"unique_id": "clip_1", "fade_in": 0.5,
                       "fade_out": 0.5}}]

    result = otio_mix.apply_audio_ops(otio, ops, _clips(duration=300),
                                      fps=FPS)

    assert result["unmatched"] == []
    assert result["complaints"] == []
    parameter = _volume_parameter_of(otio)
    assert parameter is not None
    keys = {int(frame): float(entry["Value"])
            for frame, entry in parameter["Key Frames"].items()}
    # Silence at the head and tail, unity between: a 0.5 s fade each way
    # at 30 fps is 15 frames in and 15 frames out of a 300-frame clip.
    assert keys == {0: otio_mix.MIN_VOLUME_DB, 15: 0.0,
                    284: 0.0, 299: otio_mix.MIN_VOLUME_DB}
    # The read-back shows the faded window on the clip.
    levels = result["levels"]
    assert len(levels) == 1
    assert levels[0]["keyframes"] == keys


def test_audio_gain_writes_a_static_level_and_reads_it_back():
    """Defect: an `audio.gain` op had no executor, so "bring the bed down
    6 dB" could not be expressed as a typed op. It now writes a static dB
    level through the mix path and reads back.
    """
    otio = _otio_with_clip(duration=300)
    ops = [{"op": "audio.gain",
            "params": {"unique_id": "clip_1", "db": -6.0}}]

    result = otio_mix.apply_audio_ops(otio, ops, _clips(duration=300),
                                      fps=FPS)

    assert result["unmatched"] == []
    assert result["complaints"] == []
    parameter = _volume_parameter_of(otio)
    assert parameter is not None
    assert float(parameter["Parameter Value"]) == -6.0
    assert parameter["Key Frames"] == {}
    assert result["levels"][0]["level_db"] == -6.0


def test_audio_automation_writes_a_keyframed_lane_and_reads_it_back():
    """Defect: an `audio.automation` op had no executor, so a keyframed
    audio lane could not be expressed as a typed op. It now writes the
    keyframes through the mix path and reads back.
    """
    otio = _otio_with_clip(duration=300)
    ops = [{"op": "audio.automation",
            "params": {"unique_id": "clip_1", "param": "volume",
                       "keyframes": {0: -6.0, 150: -12.0}}}]

    result = otio_mix.apply_audio_ops(otio, ops, _clips(duration=300),
                                      fps=FPS)

    assert result["unmatched"] == []
    assert result["complaints"] == []
    parameter = _volume_parameter_of(otio)
    assert parameter is not None
    keys = {int(frame): float(entry["Value"])
            for frame, entry in parameter["Key Frames"].items()}
    assert keys == {0: -6.0, 150: -12.0}
    assert result["levels"][0]["keyframes"] == keys


def test_audio_pan_refuses_by_name_it_cannot_write():
    """Defect: an op that cannot be written must refuse by name, never be
    silently dropped. Resolve's OTIO carries no measured audio-pan
    parameter - the audio surface is volume only - so `audio.pan` says
    so instead of writing nothing and reporting success.
    """
    otio = _otio_with_clip(duration=300)
    ops = [{"op": "audio.pan",
            "params": {"unique_id": "clip_1", "pan": -0.5}}]

    result = otio_mix.apply_audio_ops(otio, ops, _clips(duration=300),
                                      fps=FPS)

    assert result["applied"] == []
    assert len(result["unmatched"]) == 1
    assert "no measured OTIO spelling" in result["unmatched"][0]["reason"]
    # Nothing was written: the clip still carries no volume parameter.
    assert _volume_parameter_of(otio) is None


def test_an_op_addressing_no_placed_clip_is_reported_not_dropped():
    """Defect: a level nobody applied is the defect this module exists to
    remove. An audio op whose unique_id matches no placed clip is
    reported by name, never dropped quietly.
    """
    otio = _otio_with_clip(duration=300)
    ops = [{"op": "audio.gain",
            "params": {"unique_id": "clip_404", "db": -6.0}}]

    result = otio_mix.apply_audio_ops(otio, ops, _clips(duration=300),
                                      fps=FPS)

    assert result["applied"] == []
    assert len(result["unmatched"]) == 1
    assert result["unmatched"][0]["unique_id"] == "clip_404"
    assert _volume_parameter_of(otio) is None
