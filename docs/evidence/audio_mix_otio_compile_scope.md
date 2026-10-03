# Audio mix and OTIO compilation: scope audit

Checked against `origin/main` at `1cbec703530c24bd68a8bd222e9944503910fdd6`, which contains PR 1606.

## Finding

The described second materialisation does not occur on the OTIO reel path in this revision.

The master timeline builder has the only production call to
`deliver_audio_mix.deliver_mix`, in
`library/steps/step_6_01_render/resolve_build_timeline.py`. After appending
the timeline's picture and audio, it exports that timeline, applies
`otio_mix` targets and stem swaps, writes a mixed OTIO file, imports a
replacement, verifies picture structure and reads the mix back. PR 1606
adds the picture-range guard to this round-trip. This is the master
builder's existing audio-delivery behavior.

The reel builder's `placement_mode="otio"` follows a different path:
`reel_build.build_reel_timeline` records placements,
`reel_otio_placement.compile_recorded` writes them through
`otio_compile`, and `import_recorded` calls `ImportTimelineFromFile` once.
No production call from this path reaches `deliver_mix`, `otio_mix`, or
`mix_intent`. The reel builder also receives no manifest or `audio_mix`
argument. `otio_compile.Placement` describes file, source and record
ranges, transforms, link groups, channels and enabled state; it has no
audio-level or automation field.

Consequently, `deliver_mix` does not export, modify and re-import an
OTIO-placed reel today. The `audio_mix` plan it consumes is the master
manifest's A2 music automation and static/de-clicked levels for other
audio tracks. Folding that plan into reel compilation would create a new
reel mixing behavior, and this path has no input carrying that plan.

## Materialisation cost and existing proof

The measured Reel 09 OTIO placement held Resolve for 1.26 s total: 0.40 s
for its one import and 0.83 s for restore and read-back
(`docs/OTIO_COMPILATION_MEASURED.md`). This path currently has no second
mix import, so its second-materialisation cost is zero. The repository
does not contain a measurement of a hypothetical `deliver_mix` pass on a
compiled reel; the 0.40 s import figure is not a measurement of that
different pass.

The existing `deliver_mix` scenario verifies its master-timeline round
trip: picture fingerprints match, static audio levels and music
keyframes read back, and an injected source-range drift is refused. It
does not compare a compiled-and-mixed reel to a two-pass reel result,
because that two-pass reel path is absent.

## Decision

No implementation was made. There is no current second reel
materialisation to remove, and no established reel audio-mix behavior to
preserve or compare against. A follow-up that intentionally adds mixing
to reels needs to define and supply the reel's audio-mix plan first.
