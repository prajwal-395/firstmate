"""Which timeline tracks carry per-clip Fusion comps.

One enumeration, so the pass that BUILDS the comps and the check that
reports what it could not reach cannot disagree.

`compile_manifest` merges the house look - pivot contrast, glow, grain,
vignette - onto every V1 **and V2** clip, because those are the four
parts of the look a CDL has no term for and a Fusion comp is their only
route to the picture. The pass that draws them read `tracks['V1']` only,
so on project 001 eleven clips carried a merged look and eight got a
comp: the three cutaways sat at a different contrast, with no grain and
no vignette, beside the A-roll they were cut into. Commit 85634d5 added
the detection that names them and deliberately left the gap open.

**A V2 clip that is FOOTAGE carries its own picture; a TRANSPARENT V2 clip
carries none, and a comp reads only the clip it sits on.** The two are
opposite cases and the rule that fits one is wrong for the other:

- A B-roll cutaway is footage. Zoom, blur, shake and grade it exactly as a
  V1 clip - `compile_manifest._picture_label_at` places a planned VFX on the
  V1 OR V2 clip covering it, and the pass below builds the comp either way.
  Refusing a VFX on a cutaway killed a whole run (`vfx_carriers.py`).
- A transparent overlay clip - the reused `transparent_1080x1920_30fps.mov`
  placed at `trackIndex: 2` - carries no picture, so its comp has nothing to
  read: dip-to-black, colour washes, letterbox bars and particle effects
  only, never flash, blur or zoom, which need image content.
- **NEITHER kind reads V1.** An effect on a cutaway alters the cutaway, not
  the A-roll under it, and an Adjustment Clip cannot go on V2 at all -
  `InsertGeneratorIntoTimeline` always targets V1.

Transitions stay on V1. `fusion_effects.transitions` carries an
`after_clip` index into the V1 clip LIST, so replaying it against another
track's clips would draw a transition at an unrelated cut.

This module holds no Resolve import on purpose: the renderer's Fusion
pass cannot be imported without DaVinci's scripting module, and a rule
that can only be checked by grepping a source file is the rot this repo
keeps having to undo.
"""

# In build order. A track named here must have its clips placed before
# the Fusion pass runs, or its comps land on nothing.
FUSION_COMP_TRACKS = (1, 2)

# The one track whose clip indices `fusion_effects.transitions` means.
TRANSITION_TRACK = 1


def fusion_comp_tracks(manifest: dict) -> list[tuple[int, list, bool]]:
    """(track_index, clip specs, whether transitions apply) per track.

    Ordered as the renderer walks them. A track with no clips is still
    returned - the caller skips it naturally - so the list is a statement
    about the mechanism rather than about one manifest.
    """
    tracks = manifest.get("tracks", {}) or {}
    out = []
    for index in FUSION_COMP_TRACKS:
        clips = (tracks.get(f"V{index}", {}) or {}).get("clips", []) or []
        out.append((index, clips, index == TRANSITION_TRACK))
    return out


def reachable_effect_labels(manifest: dict) -> set[str]:
    """Every clip label the Fusion pass will visit, off the manifest.

    The renderer checks against the labels it really PLACED, which is the
    stronger question; this answers the same question about a manifest
    that has not been built yet, which is what a test and
    `compile_manifest` can ask.
    """
    labels = set()
    for _index, clips, _transitions in fusion_comp_tracks(manifest):
        for clip in clips:
            label = clip.get("label")
            if label:
                labels.add(label)
    return labels
