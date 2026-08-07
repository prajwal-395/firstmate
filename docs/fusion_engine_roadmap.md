# Fusion Engine — Future Upgrades & Roadmap

This document outlines potential upgrades discussed during the engine refactor that are outside the current scope but would extend the pipeline's capabilities.

---

## 1. DRX PowerGrade Reverse Engineering

**Status:** Research complete, implementation deferred

**What we know:**
- `.drx` files are XML with human-readable node hierarchy + opaque `FieldsBlob` binary data
- The node structure (serial/parallel/layer connections) is visible in the XML tags
- The actual grade math (lift/gamma/gain, curves, qualifiers, power windows) is packed into `FieldsBlob` entries — likely Base64-encoded serialized data
- The scripting API provides `graph.ApplyGradeFromDRX(path, mode)` for applying pre-built grades

**Approach to crack the format:**
1. Export two nearly-identical grades from Resolve (e.g., one with only a Gain change)
2. Diff the `.drx` files to isolate which `FieldsBlob` bytes change
3. Decode the blob format (compare against Base64, Protobuf, or custom binary)
4. Build a Python generator once the format is understood
5. Validate by importing generated `.drx` files back into Resolve

**Value:** Would enable fully programmatic PowerGrade creation — composing color correction node trees in Python, just like we compose Fusion node trees now. Currently, PowerGrades must be created manually in the GUI, then applied via `ApplyGradeFromDRX()`.

---

## 2. Built-In Effect Extraction & Composable Blocks

**Status:** Parser can read them, integration pending

**What we have access to:**
Resolve bundles 417 `.setting` files inside `Templates.drfx`:

| Category | Count | Examples |
|----------|-------|---------|
| Edit/Transitions | ~67 | Cross Dissolve, Fold, Spin, Zoom In/Out, Camera Shake, Block Glitch, RGB Splitter |
| Edit/Titles | ~70 | Drop In, Rise Fade, Digital Glitch, Lower Thirds |
| Fusion/Tools | 3 | Advanced Camera Shake, Chromatic Aberration, Edge Control |
| Fusion/Particles | ~22 | Snow, Embers, Fireflies, Lava, Matrix, Bokeh |
| Fusion/Lens Flares | ~40 | 40+ variations |
| Fusion/Looks | ~6 | Posterize + LUT files (Bleach, Film Chrome) |

**Our parser already reads these.** The next step would be:
1. Extract interesting effects (Camera Shake, Chromatic Aberration, etc.)
2. Analyze their node graphs programmatically
3. Convert them into composable `fx.*()` blocks
4. e.g., `fx.camera_shake(magnitude=0.5)` wrapping the same Transform + Shake + Expression graph that Resolve's built-in effect uses

**Location of Templates.drfx:**
```
/Applications/DaVinci Resolve/DaVinci Resolve.app/Contents/Resources/Fusion/Templates/Templates.drfx
```

User-installed packages:
```
~/Library/Application Support/Blackmagic Design/DaVinci Resolve/Fusion/Templates/Edit/Effects/
├── mTransition_Luma.drfx
├── DaVinci Resolve ACIDBITE Bold Titles - Aquarium.drfx
└── mHello 3D.drfx
```

### Default Transition Parameter Values (Extracted from .setting files)

| Transition | Key Parameters | Values |
|-----------|---------------|--------|
| Brightness Flash | BrightnessContrast.Brightness, .Saturation | 0.67, 1.83 |
| Cross Dissolve | Dissolve.Mix | 0→1 (LUTLookup, Quart easing) |
| Crash Zoom | Transform.zoom scale/offset | 0.4, 0.6 (zoom range 0.6→1.0) |
| Glow | SoftGlow.Gain, .XGlowSize | 5.0, 100 |
| Camera Shake | ResolveFX Transform with expressions | Sine-based oscillation |

These values can be used directly in our `fx.*()` composable blocks to match DaVinci's look and feel.

---

## 3. V2 Overlay Track System (Validated)

**Status:** Validated and working — documented approach for V2 overlays

### What Works
- **Transparent MOV on V2**: Create a ProRes 4444 transparent clip, import to media pool, place on V2 via `AppendToTimeline` with `clipInfo` targeting `trackIndex=2`. One clip can be reused at multiple positions.
- **Fusion AddTool API**: Navigate to Fusion page, use `comp.AddTool()` to build node graphs live. Keyframes via `comp:BezierSpline()` Lua execute.
- **Track control**: `SetTrackEnable`, `SetTrackLock`, `SetProperty('Opacity')`, `SetProperty('CompositeMode')` all work programmatically.

### Key Limitations
- **V2 Fusion can't access V1 content**: Fusion on a V2 clip only has access to V2's own MediaIn. Effects like flash/defocus/zoom that process video content **must stay on V1** clips. V2 is only for **additive overlays** (backgrounds that generate their own pixels).
- **ImportFusionComp tool loading**: The `.comp` file nodes only load reliably for the most recently imported clip in a Python session. For bulk imports, use separate Python processes or use the live `AddTool` API instead.
- **Adjustment Clips can't go on V2**: `InsertGeneratorIntoTimeline("Adjustment Clip")` always inserts on V1 and has no track targeting. Generators have no MediaPoolItem so they can't be moved via `move_clips`. The transparent MOV approach is the workaround.

### V2 Overlay Use Cases (Validated)
| Effect | V2 Approach | How |
|--------|------------|-----|
| Dip to black | Background node with animated alpha | `TopLeftAlpha = BezierSpline(0→0.85→0)` |
| Color wash | Background node with color + animated alpha | Same approach, colored BG |
| Letterbox/bars | Background + mask with static geometry | No animation needed |
| Particle/snow | Particle emitter over transparent BG | Advanced Fusion |

### V2 Overlay Anti-Patterns (Don't Do These)
| Effect | Why Not V2 | Do This Instead |
|--------|-----------|----------------|
| Flash/brighten | V2 MediaIn is transparent — nothing to brighten | `fx.transition_tail/head` on V1 |
| Defocus/blur | V2 MediaIn has no content to blur | `fx.transition_tail/head` on V1 |
| Zoom punch | V2 MediaIn has no content to zoom | `fx.transition_tail/head` on V1 |

---

## 4. Automated PowerGrade Application Pipeline

**Status:** API available, integration pending

**Current capability:**
```python
# Apply a .drx PowerGrade to a clip (already supported by Resolve API)
graph = clip.GetNodeGraph()
success = graph.ApplyGradeFromDRX("/path/to/grade.drx", 0)
```

**Potential upgrade:**
- Integrate into step 5.01 (color grading) of the pipeline
- Auto-select PowerGrade based on segment type (HOOK → bold look, OUTRO → muted)
- Apply different grades to A-roll vs B-roll
- Stack grades: base CST → creative PowerGrade → per-segment adjustments

**Sourced free PowerGrades to test with:**

| Source | Best For | Link |
|--------|----------|------|
| PixelTools Free Film Emulation | Base grade, Kodak 2383 look | [pixeltoolspost.com/pages/free-powergrades](https://pixeltoolspost.com/pages/free-powergrades) |
| Denz Creates "Everyday Grade" | Talking head content | YouTube — search "Denz Creates Everyday Grade" |
| Vedro El Citra iPhone ProRes Log | iPhone footage specifically | YouTube — search "Vedro El Citra iPhone PowerGrade" |
| Grade Atlas | Browsing many options | [gradeatlas.com](https://gradeatlas.com) |
| Jamie Fenn | Cinematic film looks | [jamiefenn.com](https://jamiefenn.com) — check FREEBIES |
| Wego Film | Rec.709 presets | YouTube — search "Wego Film free PowerGrade" |

---

## 5. Round-Trip GUI Editing

**Status:** Parser complete, workflow validated

**What this enables:**
1. Design an effect in Fusion's visual node editor
2. Export/save the `.comp` file
3. Parse it into our Python object model: `comp = parse_comp_file("my_effect.comp")`
4. Inspect and modify programmatically
5. Re-serialize and apply to other clips

**Use cases:**
- Colorist designs a complex roto/mask in the GUI → export → apply to 50 clips
- Designer creates a title template → parse → parameterize → generate variants
- Debug a broken comp: parse → dump() → inspect node graph → fix → re-serialize

---

## 6. Expression Support in Effect Blocks

**Status:** Parser preserves expressions, engine doesn't generate them yet

The built-in Camera Shake effect uses Fusion Expressions:
```lua
PointExpressionX = Input { Value = "p1x*n1*n2+p2x", },
n4 = Input { Value = 2, Expression = "Xf_Shake_1.Input.Width*...", },
```

Adding expression support to our effect blocks would enable:
- Aspect-ratio-aware effects (expression reads Width/Height)
- Self-modulating animations (one parameter drives another)
- More complex camera moves without hard-coding values

---

## 7. Fusion .setting Export

**Status:** Not started

Currently we generate `.comp` files (per-clip compositions). Adding `.setting` export would let us:
- Package our effect blocks as installable Resolve macros
- Create reusable GroupOperator/MacroOperator templates
- Share effects between projects without the pipeline
- Potentially distribute as `.drfx` bundles (zip with `.setting` + icons)

---

## 8. Color Page API Deeper Integration

**Status:** Research complete, implementation pending

Beyond `ApplyGradeFromDRX()`, the Resolve Color Page API provides:
```python
# Node manipulation
node = clip.AddNode()
clip.SetNodeEnabled(node_index, True/False)

# Parameter control  
clip.SetCDL({"Slope": [1.1, 1.0, 0.9], ...})

# Gallery access
gallery = project.GetGallery()
albums = gallery.GetGalleryStillAlbums()
```

This could enable:
- Building color grades programmatically (without `.drx` files)
- Per-node parameter animation via CDL
- Automated still capture for A/B comparison
- Gallery management (organize stills by project/scene)

---

## Priority Order

| # | Upgrade | Effort | Impact |
|---|---------|--------|--------|
| 1 | Automated PowerGrade application | Low | High — immediate visual quality boost |
| 2 | Built-in effect extraction | Medium | High — access to 417 effects |
| 3 | V2 overlay system (dip-to-black, color wash) | Low | Medium — already validated |
| 4 | Color Page API integration | Medium | High — programmatic grading |
| 5 | Round-trip GUI editing | Low | Medium — workflow flexibility |
| 6 | Expression support | Medium | Medium — more complex effects |
| 7 | DRX reverse engineering | High | High — but risky |
| 8 | .setting export | Low | Low — nice to have |
