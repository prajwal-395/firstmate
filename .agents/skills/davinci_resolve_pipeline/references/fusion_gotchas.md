# Fusion .comp Format — Known Gotchas

## CRASH-CAUSING BUGS

### ApplyMode on Merge → SIGSEGV
```lua
-- ❌ CRASHES RESOLVE on ImportFusionComp
ApplyMode = Input { Value = 5, },  -- Multiply mode
```
Fusion's `MergeInputs::NotifyChanged` crashes during comp deserialization.
Do NOT set ApplyMode. Use Normal mode (default) at reduced Blend opacity.

### Path + Merge → Black Output
```lua
-- ❌ Produces all-black frames when ANY Merge node is downstream
Transform1Center = Path {
    KeyFrames = {
        [0] = { 0.5, 0.5, Flags = { Linear = true } },
        [89] = { 0.5, 0.49, Flags = { Linear = true } },
    }
}
```
Use a static Center value instead:
```lua
-- ✅ Safe
Center = Input { Value = { 0.5, 0.49 }, },
```
Note: Path is safe when NO Merge exists in the comp (e.g., slide transitions).

## SILENT FAILURE BUGS

### BlendClone (Wrong Name)
```lua
-- ❌ Silently ignored — no error, vignette just doesn't work
BlendClone = Input { Value = 0.25, },

-- ✅ Correct
Blend = Input { Value = 0.25, },
```

### Missing GlobalOut on Background
```lua
-- ❌ Background may stop rendering mid-clip
Background1 = Background {
    Inputs = { Width = ..., Height = ... },
}

-- ✅ Always include GlobalOut
Background1 = Background {
    Inputs = {
        GlobalOut = Input { Value = 89, },  -- match clip duration
        Width = ..., Height = ...,
    },
}
```

### EllipseMask Missing Inverted
```lua
-- ❌ Darkens the CENTER (wrong)
Ellipse1 = EllipseMask {
    Inputs = { SoftEdge = ..., Width = ..., Height = ... },
}

-- ✅ Darkens the EDGES (vignette)
Ellipse1 = EllipseMask {
    Inputs = {
        SoftEdge = Input { Value = 0.35, },
        MaskWidth = Input { Value = 320, },
        MaskHeight = Input { Value = 240, },
        PixelAspect = Input { Value = { 1, 1 }, },
        Inverted = Input { Value = 1, },  -- REQUIRED
        Width = Input { Value = 1.8, },
        Height = Input { Value = 1.8, },
    },
}
```

### ordered() Wrapper
```lua
-- ❌ Non-standard
Tools = ordered() {

-- ✅ Standard
Tools = {
```

## INTENSITY GUIDELINES

| Effect | Subtle | Moderate | Too Strong |
|--------|--------|----------|------------|
| DirectionalBlur Length | 2-3 | 5 | 10+ |
| Transform zoom | 1.02 | 1.04 | 1.08+ |
| Defocus XDefocusSize | 1-2 | 3 | 5+ |
| Flash BC Gain | 1.5 | 2.0 | 3.0+ |
| SoftGlow Gain | 0.05 | 0.08 | 0.15+ |
| Vignette Blend | 0.15 | 0.25 | 0.4+ |

## WORKING NODE TYPES
✅ Transform, BrightnessContrast, ColorCorrector, ColorCurves, ColorGain,
   Blur, DirectionalBlur, SoftGlow, Glow, DVE, Background, FastNoise,
   BSpline, FilmGrain, Merge, ChannelBoolean, MatteControl, Dissolve
❌ Rectangle, HueSaturation, Ellipse (use EllipseMask instead)
