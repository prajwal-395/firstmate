/**
 * StagedScene - the whole frame, as a WORLD with a CAMERA over it.
 *
 * Every other composition in this project draws either decoration or a
 * card.  `SubtitleOverlay`, `MotionGraphics`, `TimedTextOverlay` and
 * `BrandMotion` are overlays composited over footage that keeps playing
 * underneath - `MotionGraphics` says so itself: "every element this
 * composition draws is decoration at an edge, so every one of them is
 * positioned from here [the safe-area insets]".  `FullFrameCard` does
 * own the frame, but what it owns is a card: one flat ground, a stack of
 * runs, one optional centred still.
 *
 * None of them can express the thing an animation-first reel is made of,
 * which is that the picture IS the animation rather than a layer on top
 * of one.  `docs/ANIMATION_FIRST_REFERENCE.md` is the frame-by-frame
 * measurement of the reference the captain sent on 2026-09-08 and the
 * eight gaps it names; this composition answers six of them:
 *
 *   1. a CAMERA - continuous zoom, pan, roll and focus, keyframed, so a
 *      shot change can be a camera state rather than a cut;
 *   2. a GROUND with depth - a surface colour, an optional project
 *      texture, and a vignette that belongs to the lens rather than to
 *      the world, so it holds still while the world moves;
 *   3. whole-frame FOCUS, so a transition can happen through a defocus;
 *   4. a WORLD - layers at surface coordinates sharing one camera and
 *      one shadow direction, rather than nine anchors and a row index;
 *   5. a shape that both DRAWS and CLIPS - a light form rendered as a
 *      gradient and used as the mask for the runs inside it;
 *   6. a reveal that COMPLETES on the spoken word instead of starting
 *      there.
 *
 * The seventh gap, a frame quantiser, is `holdFrames` below.  The
 * eighth - one object splitting into parts that move independently -
 * needs no feature: it is two `image` layers with the same `src`,
 * complementary `clipPath`s and diverging `x` tracks.
 *
 * WHAT THIS FILE STATES, AND WHAT IT REFUSES TO STATE
 *
 * It states nothing about how a scene should look.  There is no ground
 * colour, no vignette, no texture, no palette, no typeface, no size, no
 * duration, no easing magnitude and no camera move anywhere in it.
 * Every one of those arrives in props from a declaration someone wrote,
 * for the reason `house_look.py` was emptied (AGENTS.md 10.5), and the
 * captain restated it for this work on 2026-09-08: "i want no hardcoded
 * values.  there are no house glow looks, there are no settled house
 * grain or anything."
 *
 * Three things it does supply, and why each is the ABSENCE of a choice
 * rather than a choice:
 *
 *   - The NEUTRAL camera: zoom 1, x 0, y 0, roll 0, focus 0.  That is
 *     "the camera does not move", which is no move rather than a chosen
 *     one - the same reading `transition_vocabulary.CUT_TYPES` and
 *     `house_look.NEUTRAL_CDL` get in AGENTS.md 10.5.
 *   - `holdFrames` absent: every frame is drawn.  No stylisation.
 *   - What the four EASE NAMES look like.  `in`, `out` and `inOut` are
 *     cubic here.  That is what those words MEAN in this renderer, the
 *     way `RAMP_FRAMES.fade` is what "fade" means in MotionGraphics; a
 *     declaration chooses the character and never these curves.
 *
 * UNITS
 *
 * World coordinates are SHARES OF THE DELIVERY WIDTH.  The camera at
 * zoom 1 shows exactly 1.0 unit across the frame, and the world origin
 * is the frame centre, so `x: 0.25` is a quarter of a frame-width right
 * of centre at rest and a layer of `width: 0.4` covers 40% of the frame
 * at rest.  A share means the same thing at any delivery format, which
 * is the reasoning `safe_area.py` already applies to element widths.
 * Track times are SECONDS on the segment's own clock, rebased to zero -
 * the same timebase `MotionGraphics` gives its planned elements.
 */
import React from "react";
import {
  AbsoluteFill,
  Easing,
  Img,
  continueRender,
  delayRender,
  interpolate,
  staticFile,
  useCurrentFrame,
} from "remotion";
import { loadBundledFonts, loadProjectFont } from "../../fonts";

// Same reason as SubtitleOverlay and MotionGraphics: a scene may set a
// bundled family and must not race the font load.
loadBundledFonts();

/** One keyframe: a value, the second it holds, and how it got there.
 *
 * `ease` describes the segment ENDING at this keyframe, so the first
 * keyframe's `ease` is never read.  Absent means linear, which is the
 * absence of shaping rather than a chosen curve.
 */
export type Keyframe = {
  at: number;
  value: number;
  ease?: "linear" | "in" | "out" | "inOut";
};

/** A property that is either constant or keyframed. */
export type Animatable = number | Keyframe[];

/** What each ease NAME looks like in this renderer.
 *
 * Cubic, and cubic is not a magnitude a declaration chose - it is the
 * drawing of the word, exactly as `RAMP_FRAMES` is the drawing of
 * "fade" in MotionGraphics.  A scene names the character; it never
 * names a curve.
 */
const EASES = {
  linear: Easing.linear,
  in: Easing.in(Easing.cubic),
  out: Easing.out(Easing.cubic),
  inOut: Easing.inOut(Easing.cubic),
} as const;

/**
 * The value of an animatable property at time `t` seconds.
 *
 * Held flat before the first keyframe and after the last, so a track
 * never extrapolates a value nobody declared.  Exported because the
 * scene's behaviour over time is the thing worth asserting in a test
 * without rendering a frame.
 */
export const valueAt = (prop: Animatable | undefined, t: number, neutral: number): number => {
  if (prop === undefined) return neutral;
  if (typeof prop === "number") return prop;
  if (prop.length === 0) return neutral;
  const keys = prop;
  if (t <= keys[0].at) return keys[0].value;
  const last = keys[keys.length - 1];
  if (t >= last.at) return last.value;
  for (let i = 1; i < keys.length; i += 1) {
    const a = keys[i - 1];
    const b = keys[i];
    if (t <= b.at) {
      if (b.at === a.at) return b.value;
      return interpolate(t, [a.at, b.at], [a.value, b.value], {
        easing: EASES[b.ease ?? "linear"],
        extrapolateLeft: "clamp",
        extrapolateRight: "clamp",
      });
    }
  }
  return last.value;
};

/**
 * The frame a quantised scene draws.
 *
 * `holdFrames` of 2 renders every second frame twice - motion on twos,
 * which is 51% of the reference's frame pairs (§5 of the read).  Absent
 * or 1 draws every frame.  Exported for the same reason `valueAt` is.
 */
export const heldFrame = (frame: number, holdFrames?: number): number =>
  holdFrames && holdFrames > 1 ? Math.floor(frame / holdFrames) * holdFrames : frame;

/**
 * How visible a run is at time `t`.
 *
 * `revealAt` is the second the run is FULLY visible, and the ramp ENDS
 * there rather than starting there.  That is the rule the reference
 * follows on every run it sets - measured leads of 33-100 ms with the
 * fade completing on the syllable (§1 of the read) - and it is the
 * opposite of showing a cue once its start frame has passed, which is
 * what `FullFrameCard.wordCues` does.  The declaration states the
 * moment and the ramp length; this function states only that the ramp
 * lands on the moment.
 *
 * A run with no `revealAt` is visible for the whole scene: no reveal is
 * the absence of one.
 */
export const runOpacity = (t: number, revealAt?: number, revealRamp?: number): number => {
  if (revealAt === undefined) return 1;
  const ramp = revealRamp ?? 0;
  if (ramp <= 0) return t >= revealAt ? 1 : 0;
  if (t >= revealAt) return 1;
  if (t <= revealAt - ramp) return 0;
  return (t - (revealAt - ramp)) / ramp;
};

/** A drop shadow, stated in world units so it scales with the camera. */
export type Shadow = {
  dx: number;
  dy: number;
  blur: number;
  colour: string;
};

export type Placement = {
  /** World centre of the layer. */
  x?: Animatable;
  y?: Animatable;
  /** Width in world units. Height follows the source's aspect. */
  width: Animatable;
  /** Degrees, clockwise. */
  rotate?: Animatable;
  opacity?: Animatable;
  /**
   * A CSS `clip-path` applied to the layer, verbatim from the
   * declaration.  This is how a subject is cut out of its frame, and
   * how one subject becomes two: the same `src` twice with
   * complementary paths and diverging `x` tracks.
   */
  clipPath?: string;
};

export type SceneRun = {
  text: string;
  /** Font size in world units, so type scales with the camera. */
  size: number;
  weight: number;
  colour: string;
  letterSpacing?: number;
  /** The second this run is FULLY visible. See `runOpacity`. */
  revealAt?: number;
  /**
   * Start a fresh line before this run.
   *
   * Runs otherwise flow INLINE and share a baseline, which is what lets
   * a small lead-in and a large payload word interlock the way the
   * reference's do - its big word's `i` dot rises into the lead-in's row
   * (§7 of the read).  This is structure, not a look: the declaration
   * says where the line breaks and nothing here says how far apart the
   * lines sit unless `leading` states it.
   */
  newLine?: boolean;
};

export type ImageLayer = {
  kind: "image";
  id?: string;
  /** `frame` places this on the lens instead of the surface. */
  space?: "world" | "frame";
  /** A project-supplied still under Remotion's `public/`, `brand/<name>`.
   *
   * The engine ships no artwork and states none (AGENTS.md 14), so this
   * is a path the project staged or the layer was refused before it
   * reached these props - never a placeholder.
   */
  src: string;
  placement: Placement;
  shadow?: Shadow;
  /** Names the `light` layer whose shape clips this one, if any. */
  inLight?: string;
};

export type TextLayer = {
  kind: "text";
  id?: string;
  /** `frame` places this on the lens instead of the surface. */
  space?: "world" | "frame";
  runs: SceneRun[];
  placement: Placement;
  /** Seconds each run's reveal ramp takes, ending on its `revealAt`. */
  revealRamp?: number;
  /** `left` | `centre` | `right`, verbatim from the declaration. */
  align?: string;
  /** World units between the lines' baselines; absent packs them tight. */
  leading?: number;
  shadow?: Shadow;
  inLight?: string;
};

/**
 * A cone of light on the surface, drawn AND used as a mask.
 *
 * The apex sits at the layer's world position, the cone opens along
 * `angle` degrees (0 points up the frame), reaches `length` world units
 * and subtends `spreadDeg`.  Any layer whose `inLight` names this one
 * renders inside it and is clipped by its edges, so the light leaving a
 * word is what removes the word - which is what the reference does from
 * 8.5 s, slicing "space" down to "spac" to "sp" as the cone rotates
 * away (§7 of the read).
 */
export type LightLayer = {
  kind: "light";
  id: string;
  x?: Animatable;
  y?: Animatable;
  angle?: Animatable;
  length: Animatable;
  spreadDeg: Animatable;
  opacity?: Animatable;
  colour: string;
  /** 0..1 share of the cone's length over which it fades out at the far end. */
  falloff?: number;
};

/** A disc of light lying on the surface. Draws only; it clips nothing. */
export type DiscLayer = {
  kind: "disc";
  id?: string;
  /** `frame` places this on the lens instead of the surface. */
  space?: "world" | "frame";
  x?: Animatable;
  y?: Animatable;
  /** Diameter in world units. */
  size: Animatable;
  opacity?: Animatable;
  colour: string;
  blur?: number;
};

/** A straight rule on the surface - the persistent graphic of §3.3. */
export type RuleLayer = {
  kind: "rule";
  id?: string;
  /** `frame` places this on the lens instead of the surface. */
  space?: "world" | "frame";
  x?: Animatable;
  y?: Animatable;
  length: Animatable;
  angle?: Animatable;
  /** Thickness in world units. */
  thickness: number;
  opacity?: Animatable;
  colour: string;
};

export type SceneLayer = ImageLayer | TextLayer | LightLayer | DiscLayer | RuleLayer;

/**
 * Which space a layer is placed in.
 *
 * `world` (the absence of a declaration) puts the layer on the surface:
 * the camera moves it, scales it and defocuses it with everything else.
 * `frame` puts it on the LENS instead - it holds still and holds its
 * size while the world moves under it, the way the vignette does.
 *
 * Both are needed and neither is a look.  A staged picture whose type
 * could only live in the world cannot hold a line legible while the
 * camera crosses five frame-widths, and one whose type could only live
 * on the lens cannot be clipped by a light lying on the surface - the
 * reference does the second (§7 of the read) and needs the first.
 */
export const layerSpace = (layer: SceneLayer): "world" | "frame" =>
  (layer as { space?: string }).space === "frame" ? "frame" : "world";

export type Ground = {
  /** Required: a staged scene IS the picture, so it has a ground. */
  colour: string;
  /** A project-supplied tile under `public/`, as `brand/<name>`. */
  texture?: string;
  /** Required when `texture` is present. */
  textureOpacity?: number;
  /** World units the tile repeats over. */
  textureScale?: number;
  /**
   * The lens's vignette, not the world's: it is applied OUTSIDE the
   * camera, so it holds still while the world moves - which is what the
   * reference's does (§3.2).  All three numbers are declared together
   * or the vignette is absent.
   */
  vignette?: { strength: number; radiusX: number; radiusY: number };
};

export type Camera = {
  zoom?: Animatable;
  x?: Animatable;
  y?: Animatable;
  roll?: Animatable;
  /** Blur radius in world units. 0 is sharp. */
  focus?: Animatable;
};

export type StagedSceneProps = {
  ground: Ground;
  camera?: Camera;
  layers: SceneLayer[];
  fontFamily: string;
  fontFile?: string;
  /** Render every Nth frame twice. Absent draws every frame. */
  holdFrames?: number;
  fps: number;
  width: number;
  height: number;
  durationInFrames: number;
};

// eslint-disable-next-line @typescript-eslint/no-explicit-any
export const stagedSceneSchema = {} as any;

/** The polygon a cone subtends, as a CSS `clip-path` on the frame box.
 *
 * Percentages of the frame, computed from the apex in pixels so the
 * shape and the mask are one definition rather than two that can drift.
 */
export const conePolygon = (
  apexX: number,
  apexY: number,
  angleDeg: number,
  lengthPx: number,
  spreadDeg: number,
  w: number,
  h: number,
): string => {
  // 0 degrees points up the frame; CSS y grows downward.
  const dir = (angleDeg - 90) * (Math.PI / 180);
  const half = (spreadDeg / 2) * (Math.PI / 180);
  const pts = [
    [apexX, apexY],
    [apexX + Math.cos(dir - half) * lengthPx, apexY + Math.sin(dir - half) * lengthPx],
    [apexX + Math.cos(dir + half) * lengthPx, apexY + Math.sin(dir + half) * lengthPx],
  ];
  return `polygon(${pts.map(([px, py]) => `${(px / w) * 100}% ${(py / h) * 100}%`).join(", ")})`;
};

const shadowCss = (shadow: Shadow | undefined, unit: number): string | undefined =>
  shadow
    ? `drop-shadow(${shadow.dx * unit}px ${shadow.dy * unit}px ${shadow.blur * unit}px ${shadow.colour})`
    : undefined;

const Runs: React.FC<{
  layer: TextLayer;
  t: number;
  unit: number;
  fontFamily: string;
}> = ({ layer, t, unit, fontFamily }) => (
  <>
    {layer.runs.map((run, i) => (
      <React.Fragment key={i}>
        {run.newLine && i > 0 ? <br /> : null}
        <span
          style={{
            fontFamily,
            fontSize: `${run.size * unit}px`,
            fontWeight: run.weight,
            color: run.colour,
            opacity: runOpacity(t, run.revealAt, layer.revealRamp),
            letterSpacing:
              run.letterSpacing === undefined ? undefined : `${run.letterSpacing * unit}px`,
            whiteSpace: "pre",
          }}
        >
          {run.text}
        </span>
      </React.Fragment>
    ))}
  </>
);

/** One layer, placed in the world. */
const Layer: React.FC<{
  layer: SceneLayer;
  t: number;
  unit: number;
  width: number;
  height: number;
  fontFamily: string;
  lights: Record<string, LightLayer>;
}> = ({ layer, t, unit, width, height, fontFamily, lights }) => {
  if (layer.kind === "light") {
    const apexX = width / 2 + valueAt(layer.x, t, 0) * unit;
    const apexY = height / 2 + valueAt(layer.y, t, 0) * unit;
    const clip = conePolygon(
      apexX,
      apexY,
      valueAt(layer.angle, t, 0),
      valueAt(layer.length, t, 0) * unit,
      valueAt(layer.spreadDeg, t, 0),
      width,
      height,
    );
    const angle = valueAt(layer.angle, t, 0);
    const falloff = layer.falloff ?? 0;
    return (
      <AbsoluteFill
        style={{
          clipPath: clip,
          opacity: valueAt(layer.opacity, t, 1),
          // The cone is lit at its apex and fades along its own axis, so
          // the gradient runs down the same direction the polygon opens.
          background:
            falloff > 0
              ? `linear-gradient(${angle + 180}deg, ${layer.colour} ${(1 - falloff) * 100}%, transparent 100%)`
              : layer.colour,
        }}
      />
    );
  }

  if (layer.kind === "disc") {
    const size = valueAt(layer.size, t, 0) * unit;
    return (
      <div
        style={{
          position: "absolute",
          left: width / 2 + valueAt(layer.x, t, 0) * unit - size / 2,
          top: height / 2 + valueAt(layer.y, t, 0) * unit - size / 2,
          width: size,
          height: size,
          borderRadius: "50%",
          background: layer.colour,
          opacity: valueAt(layer.opacity, t, 1),
          filter: layer.blur === undefined ? undefined : `blur(${layer.blur * unit}px)`,
        }}
      />
    );
  }

  if (layer.kind === "rule") {
    const len = valueAt(layer.length, t, 0) * unit;
    return (
      <div
        style={{
          position: "absolute",
          left: width / 2 + valueAt(layer.x, t, 0) * unit - len / 2,
          top: height / 2 + valueAt(layer.y, t, 0) * unit,
          width: len,
          height: Math.max(1, layer.thickness * unit),
          background: layer.colour,
          opacity: valueAt(layer.opacity, t, 1),
          transform: `rotate(${valueAt(layer.angle, t, 0)}deg)`,
          transformOrigin: "50% 50%",
        }}
      />
    );
  }

  const p = layer.placement;
  const w = valueAt(p.width, t, 0) * unit;
  const body =
    layer.kind === "image" ? (
      <Img src={staticFile(layer.src)} style={{ width: w, display: "block" }} />
    ) : (
      <div
        style={{
          textAlign:
            layer.align === "left" ? "left" : layer.align === "right" ? "right" : "center",
          lineHeight: layer.leading === undefined ? 1 : `${layer.leading * unit}px`,
        }}
      >
        <Runs layer={layer} t={t} unit={unit} fontFamily={fontFamily} />
      </div>
    );

  const placed = (
    <div
      style={{
        position: "absolute",
        left: width / 2 + valueAt(p.x, t, 0) * unit,
        top: height / 2 + valueAt(p.y, t, 0) * unit,
        // A text layer's `width` is the box its runs align inside, so
        // `align` means something and the -50% shift is measured against
        // a width the declaration stated rather than the glyphs' own.
        width: w,
        transform: `translate(-50%, -50%) rotate(${valueAt(p.rotate, t, 0)}deg)`,
        opacity: valueAt(p.opacity, t, 1),
        clipPath: p.clipPath,
        filter: shadowCss(layer.shadow, unit),
      }}
    >
      {body}
    </div>
  );

  // A layer inside a light is clipped by that light's cone, so the light
  // leaving it is what removes it.
  const light = layer.inLight ? lights[layer.inLight] : undefined;
  if (!light) return placed;
  const apexX = width / 2 + valueAt(light.x, t, 0) * unit;
  const apexY = height / 2 + valueAt(light.y, t, 0) * unit;
  return (
    <AbsoluteFill
      style={{
        clipPath: conePolygon(
          apexX,
          apexY,
          valueAt(light.angle, t, 0),
          valueAt(light.length, t, 0) * unit,
          valueAt(light.spreadDeg, t, 0),
          width,
          height,
        ),
      }}
    >
      {placed}
    </AbsoluteFill>
  );
};

/**
 * The ground's texture tile, as a repeating background.
 *
 * A `background-image` is the only way to REPEAT a tile in CSS, and
 * Remotion's own lint rule forbids it unmodified for a real reason: the
 * browser fetches it lazily, so a render can capture a frame before the
 * tile has decoded and ship a ground with no grain on it.  The fix is
 * this component: `delayRender` holds the frame until the tile has
 * actually loaded, and only then is it painted.  Same shape as
 * `loadProjectFont`'s handle in `fonts.ts`.
 *
 * `onerror` clears the same handle, because a tile that cannot be
 * fetched must not hang the render for its whole timeout - the ground
 * is still drawn, without grain.  `tests/test_staged_scene.py` renders
 * exactly that case and fails in 18s when this line is removed.
 */
const GroundTexture: React.FC<{
  src: string;
  opacity?: number;
  sizePx?: number;
}> = ({ src, opacity, sizePx }) => {
  const url = staticFile(src);
  const [ready, setReady] = React.useState(false);
  React.useEffect(() => {
    const handle = delayRender(`Loading ground texture ${src}`);
    const image = new Image();
    const done = () => {
      setReady(true);
      continueRender(handle);
    };
    image.onload = done;
    image.onerror = done;
    image.src = url;
    return () => continueRender(handle);
  }, [url, src]);
  if (!ready) return null;
  return (
    <AbsoluteFill
      style={{
        // eslint-disable-next-line @remotion/no-background-image
        backgroundImage: `url(${url})`,
        backgroundRepeat: "repeat",
        backgroundSize: sizePx === undefined ? undefined : `${sizePx}px`,
        opacity,
      }}
    />
  );
};

export const StagedScene: React.FC<StagedSceneProps> = ({
  ground,
  camera,
  layers,
  fontFamily,
  fontFile,
  holdFrames,
  fps,
  width,
  height,
}) => {
  if (fontFile) loadProjectFont(fontFamily, fontFile);
  const frame = heldFrame(useCurrentFrame(), holdFrames);
  const t = frame / fps;

  // One world unit is one delivery width at zoom 1.
  const unit = width;
  const zoom = valueAt(camera?.zoom, t, 1);
  const camX = valueAt(camera?.x, t, 0);
  const camY = valueAt(camera?.y, t, 0);
  const roll = valueAt(camera?.roll, t, 0);
  const focus = valueAt(camera?.focus, t, 0);

  const lights: Record<string, LightLayer> = {};
  for (const l of layers) if (l.kind === "light") lights[l.id] = l;

  const v = ground.vignette;

  return (
    <AbsoluteFill style={{ backgroundColor: ground.colour, overflow: "hidden" }}>
      {/* The world, seen through the camera. Everything inside this
          element moves together; the vignette below does not. */}
      <AbsoluteFill
        style={{
          transform: `scale(${zoom}) rotate(${roll}deg) translate(${-camX * unit}px, ${-camY * unit}px)`,
          transformOrigin: "50% 50%",
          filter: focus > 0 ? `blur(${focus * unit}px)` : undefined,
        }}
      >
        {ground.texture ? (
          <GroundTexture
            src={ground.texture}
            opacity={ground.textureOpacity}
            sizePx={
              ground.textureScale === undefined
                ? undefined
                : ground.textureScale * unit
            }
          />
        ) : null}
        {layers.map((layer, i) =>
          layerSpace(layer) === "world" ? (
            <Layer
              key={i}
              layer={layer}
              t={t}
              unit={unit}
              width={width}
              height={height}
              fontFamily={fontFamily}
              lights={lights}
            />
          ) : null,
        )}
      </AbsoluteFill>
      {/* The lens. Everything from here down holds still while the world
          moves: frame-space layers first, then the vignette. */}
      {layers.map((layer, i) =>
        layerSpace(layer) === "frame" ? (
          <Layer
            key={i}
            layer={layer}
            t={t}
            unit={unit}
            width={width}
            height={height}
            fontFamily={fontFamily}
            lights={lights}
          />
        ) : null,
      )}
      {v ? (
        <AbsoluteFill
          style={{
            background: `radial-gradient(ellipse ${v.radiusX * 100}% ${v.radiusY * 100}% at 50% 50%, rgba(0,0,0,0) 0%, rgba(0,0,0,${v.strength}) 100%)`,
          }}
        />
      ) : null}
    </AbsoluteFill>
  );
};
