/* eslint-disable @typescript-eslint/no-explicit-any */
import React from "react";
import { AbsoluteFill, useCurrentFrame, interpolate, spring, staticFile, useVideoConfig } from "remotion";
import { loadBundledFonts } from "../../fonts";

// Same reason as SubtitleOverlay: this composition sets
// fontFamily "Montserrat" and must not race the font load.
loadBundledFonts();

/**
 * One planned graphic, with ITS OWN timing.
 *
 * `startFrame` and `durationFrames` are LOCAL to the rendered segment,
 * rebased by `motion_graphics_plan.plan_segments`. Nothing here is
 * derived from a spine block: the layer carries its own timebase, which
 * is what the captain asked for on 2026-09-02 ("the motion graphics can
 * be seperate and on their own timescale if they need to be").
 *
 * `anchor` is one of the vocabulary's nine grid positions and `row` is
 * which line within that anchor the element occupies, so several
 * graphics can be on screen at once without colliding ("they are
 * allowed to use multiple rows in order to have various motion
 * graphics"). See library/tools/motion_graphics_vocabulary.py.
 *
 * `element` is a roster key. The fourteen this composition draws are
 * `title_lockup`, `quote_card`, `progress_bar`, `frame_accents`,
 * `lower_third`, `context_stamp`, `stat_callout`, `beat_accent`,
 * `pointer_annotation`, `counter_roll`, `digit_counter`, `list_build`,
 * `comparison_bars` and `step_counter` - the fourteen the roster marks
 * `reachable_now`. An entry naming any other element never reaches
 * these props: `motion_graphics_plan.resolve_plan` drops it with
 * `renderer_cannot_draw_it_yet` and records the drop.
 */
export type PlannedElement = {
  element: string;
  anchor: string;
  row: number;
  runs: { text: string; type_role: string }[];
  color: string;
  entrance: string;
  exit: string;
  startFrame: number;
  durationFrames: number;
  timelineProgressStart: number;
  timelineProgressEnd: number;
  /** The plan's own magnitudes. Null when the plan stated none. */
  footprint: number | null;
  emphasis: number | null;
  /**
   * A project-supplied file this element draws, as a path under
   * Remotion's `public/` - `brand/<name>`, staged there from the
   * project's own `brand_assets/`. `staticFile` turns it into the URL
   * the render loads; Python states the path and never the URL, because
   * how `public/` is served is Remotion's business and not the
   * pipeline's. Absent for every element that draws no asset.
   *
   * The engine ships no artwork and states none (AGENTS.md 14), so this
   * is a path the PROJECT supplied or the element was dropped before it
   * reached these props - never a placeholder.
   */
  asset?: string;
  data?: any;
};

export type MotionGraphicsProps = {
  elements: PlannedElement[];
  fps: number;
  width: number;
  height: number;
  /**
   * The platform's keep-clear insets in pixels, from
   * library/tools/safe_area.py via generate_motion_props. Every element
   * this composition draws is decoration at an edge, so every one of
   * them is positioned from here. The corner accents used to sit at a
   * literal 60px on all four sides - 5.6% of a 1080px width, inside the
   * like/comment/share rail.
   */
  safeArea: { top: number; right: number; bottom: number; left: number };
  durationInFrames: number;
};

export const motionGraphicsSchema = {} as any;

/** How many frames an entrance or an exit takes.
 *
 * A character, not a magnitude: `cut` is no ramp at all and the others
 * are the composition's own drawing of the vocabulary's entrance/exit
 * axis. The PLAN chooses the character; it does not choose these
 * numbers, and neither does any brand template - they are what "fade"
 * means in this renderer, the way a 12px bar is what "a bar" means.
 */
const RAMP_FRAMES: Record<string, number> = {
  cut: 0,
  fade: 8,
  slide: 8,
  scale: 8,
  mask: 8,
  draw: 12,
  blur: 10,
  typewriter: 20,
  glitch: 10,
};

/** The pixel size of each typographic weight the vocabulary names. */
const TYPE_SIZE: Record<string, number> = {
  display: 56,
  supporting: 36,
  micro: 24,
};

const TYPE_WEIGHT: Record<string, number> = {
  display: 900,
  supporting: 700,
  micro: 600,
};

/** Vertical gap between two stacked rows at one anchor.
 *
 * A gap, not a row PITCH. It used to be a fixed 96px offset per row,
 * which put row 1 on top of row 0 the moment row 0 was taller than
 * 96px - a title lockup with two runs is about 115px, so the very first
 * two-row plan drew one graphic through another. Rows are laid out by a
 * flex column over the elements that are actually on screen, so a row
 * clears whatever is really above it however tall that turns out to be.
 */
const STACK_GAP_PX = 24;

/** How many frames the named entrance or exit character ramps over.
 *
 * Exported so a SECOND composition can draw the same character without
 * respelling the numbers. `FullFrameCard` is that second composition -
 * it is not an overlay, so it cannot reuse this file's element renderer,
 * but the character `blur` means the same thing on both surfaces and
 * there must be one drawing of it in this engine, not two.
 */
export const rampFrames = (character: string): number =>
  RAMP_FRAMES[character] ?? 0;

/** How far a `typewriter` entrance has revealed at `localFrame`, 0..1.
 *
 * 1 for any other character: a caller asking "how much is revealed"
 * about a non-typewriter entrance is asking about text that is simply
 * all there.
 *
 * **This answers the ENTRANCE only.** `typewriterShown` is the one to
 * call when the element has an exit as well - see the defect recorded
 * there.
 */
export const typewriterProgress = (
  localFrame: number,
  entrance: string,
): number => {
  if (entrance !== "typewriter") return 1;
  const inFrames = rampFrames(entrance);
  if (inFrames <= 0) return 1;
  return Math.min(1, Math.max(0, localFrame / inFrames));
};

/** How much text is on screen at `localFrame`, reading BOTH ramps, 0..1.
 *
 * `typewriter` is the only character whose animation is a reveal rather
 * than a transform, and a reveal has two ends: characters arrive across
 * the entrance ramp and leave across the exit ramp, the trailing ones
 * first.
 *
 * The exit half did not exist. `exitTransform` carried
 * `case "typewriter": return {};` under a comment saying characters
 * "disappear in reverse", and every caller computed its reveal from the
 * ENTRANCE field alone - so `exit: "typewriter"` was a plain opacity
 * fade over the typewriter ramp's length. Rendered mid-exit, the ink sat
 * at x 235..845 against 232..847 held: the same glyphs, dimmer. Reading
 * one field and letting it stand for the other is what made a declared
 * character draw nothing, so this function takes both and neither
 * caller derives one from the other.
 */
export const typewriterShown = (
  localFrame: number,
  durationFrames: number,
  entrance: string,
  exit: string,
): number => {
  // Falls to 0 across the exit ramp, so the trailing characters go first.
  const outFrames = exit === "typewriter" ? rampFrames(exit) : 0;
  if (outFrames > 0) {
    const fromEnd = durationFrames - localFrame;
    const concealing = Math.min(1, Math.max(0, fromEnd / outFrames));
    // Whichever ramp is active. They can only both be when the element is
    // shorter than its two ramps together, and there the exit wins - the
    // same precedence `extTransform` takes by being spread last.
    if (concealing < 1) return concealing;
  }
  return typewriterProgress(localFrame, entrance);
};

/** Whether this element's text is mid-reveal at all, either end. */
export const isTypewriting = (
  localFrame: number,
  durationFrames: number,
  entrance: string,
  exit: string,
): boolean =>
  (entrance === "typewriter" || exit === "typewriter")
  && typewriterShown(localFrame, durationFrames, entrance, exit) < 1;

/** Split a typewriter reveal across runs: how many characters of each show.
 *
 * The one implementation, shared by `TypewriterRuns` here and by
 * `FullFrameCard`. `cursorRun` is the index of the run the caret sits
 * after, or -1 when the reveal is complete.
 */
export const typewriterSplit = (
  lengths: number[],
  progress: number,
): { shown: number[]; cursorRun: number } => {
  const total = lengths.reduce((sum, n) => sum + n, 0);
  let remaining = Math.round(total * Math.min(1, Math.max(0, progress)));
  const shown: number[] = [];
  let cursorRun = -1;
  for (let i = 0; i < lengths.length; i += 1) {
    const take = Math.min(lengths[i], Math.max(0, remaining));
    shown.push(take);
    remaining -= take;
    if (cursorRun === -1 && progress < 1 && take < lengths[i]) {
      cursorRun = i;
    }
  }
  return { shown, cursorRun };
};

/** Whether the caret is drawn on this frame of a reveal (~15Hz blink). */
export const typewriterCursorOn = (progress: number): boolean =>
  Math.floor(progress * 30) % 2 === 0;

export const elementOpacity = (
  localFrame: number,
  durationFrames: number,
  entrance: string,
  exit: string,
): number => {
  // Hand-written rather than one `interpolate`, for the reason
  // TimedTextOverlay.momentOpacity is: `interpolate` throws on a
  // non-monotonic range, and a `cut` entrance beside a `cut` exit is
  // exactly that.
  const inFrames = RAMP_FRAMES[entrance] ?? 0;
  const outFrames = RAMP_FRAMES[exit] ?? 0;
  let opacity = 1;
  if (inFrames > 0 && localFrame < inFrames) {
    opacity = Math.min(opacity, localFrame / inFrames);
  }
  const fromEnd = durationFrames - localFrame;
  if (outFrames > 0 && fromEnd < outFrames) {
    opacity = Math.min(opacity, Math.max(0, fromEnd / outFrames));
  }
  return Math.max(0, Math.min(1, opacity));
};

/**
 * The CSS transform an entrance or exit character applies.
 *
 * `fade` is opacity-only (handled by elementOpacity). `slide` moves
 * vertically, `scale` zooms, `mask` clips, and `draw` does a longer
 * scale-and-fade. These are the composition's own drawing of the
 * vocabulary's entrance/exit axis - the PLAN chooses the character, and
 * these functions define what that character looks like on screen.
 */
/** The chromatic split `glitch` draws, as a `drop-shadow` chain.
 *
 * One definition, read by the entrance and the exit, so the two cannot
 * drift into meaning different things by the same name. The three
 * offsets and their colours are what `glitch` has always declared; only
 * the CSS property carrying them changed.
 */
const chromaticSplit = (aberration: number): string =>
  [
    `drop-shadow(${aberration}px 0 rgba(255,0,0,0.7))`,
    `drop-shadow(${-aberration}px 0 rgba(0,255,255,0.7))`,
    `drop-shadow(0 ${aberration * 0.5}px rgba(0,255,0,0.5))`,
  ].join(" ");

export const entranceTransform = (
  localFrame: number,
  durationFrames: number,
  entrance: string,
): React.CSSProperties => {
  const inFrames = RAMP_FRAMES[entrance] ?? 0;
  if (inFrames <= 0 || localFrame >= inFrames) return {};
  const progress = Math.min(1, localFrame / inFrames);

  switch (entrance) {
    case "slide": {
      const y = interpolate(progress, [0, 1], [40, 0], {
        extrapolateLeft: "clamp",
        extrapolateRight: "clamp",
      });
      return { transform: `translateY(${y}px)` };
    }
    case "scale": {
      const s = interpolate(progress, [0, 1], [0.7, 1], {
        extrapolateLeft: "clamp",
        extrapolateRight: "clamp",
      });
      return { transform: `scale(${s})` };
    }
    case "mask": {
      const clip = interpolate(progress, [0, 1], [100, 0], {
        extrapolateLeft: "clamp",
        extrapolateRight: "clamp",
      });
      return { clipPath: `inset(0 ${clip}% 0 0)` };
    }
    case "draw": {
      const s = interpolate(progress, [0, 1], [0.85, 1], {
        extrapolateLeft: "clamp",
        extrapolateRight: "clamp",
      });
      return { transform: `scale(${s})`, filter: `blur(${(1 - progress) * 4}px)` };
    }
    // `blur` - inspired by Remotion Bits BlurIn (MIT, github.com/av/remotion-bits).
    // A heavier blur than draw (8px vs 4px) with a slight upward drift,
    // designed for text defocusing from nothing to sharp.
    case "blur": {
      const blurAmount = interpolate(progress, [0, 1], [8, 0], {
        extrapolateLeft: "clamp",
        extrapolateRight: "clamp",
      });
      const y = interpolate(progress, [0, 1], [12, 0], {
        extrapolateLeft: "clamp",
        extrapolateRight: "clamp",
      });
      return {
        filter: `blur(${blurAmount}px)`,
        transform: `translateY(${y}px)`,
      };
    }
    // `typewriter` - character reveal handled by TypewriterRuns at the
    // text level. No spatial transform: the container fades normally via
    // elementOpacity while the text itself reveals character by character.
    // Inspired by Remotion Bits BasicTypewriter (MIT, github.com/av/remotion-bits).
    case "typewriter":
      return {};
    // `glitch` - chromatic-aberration entrance inspired by Remotion Bits
    // GlitchIn (MIT, github.com/av/remotion-bits). Shifts RGB channels
    // apart and jitters position, both easing to zero.
    //
    // The split is a `filter: drop-shadow` chain and NOT `textShadow`.
    // It was `textShadow` on this container, and `Runs` sets its own
    // `textShadow` on every run it draws, which overrides an inherited
    // one: rendered at mid-ramp, `max|R-B|` over every visible pixel was
    // 0 while the same measurement on a red element returns 255. The
    // aberration this case advertised had never reached a frame.
    // `drop-shadow` composites the rendered subtree instead of being a
    // text property, so it survives a child restating its own shadow,
    // and it draws the split on the elements that carry no text at all -
    // the bars, the brackets and the rules - which `textShadow` never
    // could. The magnitudes are unchanged.
    case "glitch": {
      const aberration = interpolate(progress, [0, 1], [6, 0], {
        extrapolateLeft: "clamp",
        extrapolateRight: "clamp",
      });
      const jitterX = Math.sin(progress * Math.PI * 8) * aberration * 0.5;
      const jitterY = Math.cos(progress * Math.PI * 6) * aberration * 0.3;
      return {
        transform: `translate(${jitterX}px, ${jitterY}px)`,
        filter: chromaticSplit(aberration),
      };
    }
    default:
      return {};
  }
};

export const exitTransform = (
  localFrame: number,
  durationFrames: number,
  exit: string,
): React.CSSProperties => {
  const outFrames = RAMP_FRAMES[exit] ?? 0;
  if (outFrames <= 0) return {};
  const fromEnd = durationFrames - localFrame;
  if (fromEnd >= outFrames) return {};
  const progress = Math.max(0, fromEnd / outFrames);

  switch (exit) {
    case "slide": {
      const y = interpolate(progress, [0, 1], [40, 0], {
        extrapolateLeft: "clamp",
        extrapolateRight: "clamp",
      });
      return { transform: `translateY(${-y}px)` };
    }
    case "scale": {
      const s = interpolate(progress, [0, 1], [0.7, 1], {
        extrapolateLeft: "clamp",
        extrapolateRight: "clamp",
      });
      return { transform: `scale(${s})` };
    }
    case "mask": {
      const clip = interpolate(progress, [0, 1], [100, 0], {
        extrapolateLeft: "clamp",
        extrapolateRight: "clamp",
      });
      return { clipPath: `inset(0 0 0 ${clip}%)` };
    }
    case "draw": {
      const s = interpolate(progress, [0, 1], [0.85, 1], {
        extrapolateLeft: "clamp",
        extrapolateRight: "clamp",
      });
      return { transform: `scale(${s})`, filter: `blur(${(1 - progress) * 4}px)` };
    }
    case "blur": {
      const blurAmount = interpolate(progress, [0, 1], [8, 0], {
        extrapolateLeft: "clamp",
        extrapolateRight: "clamp",
      });
      const y = interpolate(progress, [0, 1], [-12, 0], {
        extrapolateLeft: "clamp",
        extrapolateRight: "clamp",
      });
      return {
        filter: `blur(${blurAmount}px)`,
        transform: `translateY(${y}px)`,
      };
    }
    // Typewriter exit: characters disappear in reverse, at the text
    // level, driven by `element.exit === "typewriter"` in DrawnElement.
    // There is no spatial transform, and this arm returns nothing on
    // purpose - which is exactly why the reveal has to be read from the
    // exit field and not from this switch. It used to carry this comment
    // and NO reveal anywhere: rendered mid-exit the ink sat at x 235..845
    // against 232..847 held, the same glyphs at lower opacity. The
    // capability was a fade wearing another name.
    case "typewriter":
      return {};
    // Glitch exit: chromatic aberration increases as the element leaves.
    // Same `drop-shadow` reasoning as the entrance.
    case "glitch": {
      const aberration = interpolate(progress, [0, 1], [6, 0], {
        extrapolateLeft: "clamp",
        extrapolateRight: "clamp",
      });
      const jitterX = Math.sin((1 - progress) * Math.PI * 8) * aberration * 0.5;
      const jitterY = Math.cos((1 - progress) * Math.PI * 6) * aberration * 0.3;
      return {
        transform: `translate(${jitterX}px, ${jitterY}px)`,
        filter: chromaticSplit(aberration),
      };
    }
    default:
      return {};
  }
};

type Insets = { top: number; right: number; bottom: number; left: number };

/**
 * Where an anchor puts an element, in CSS, from the safe area alone.
 *
 * Never `bottom: 0` and never `width: 100%` - on a 1080x1920 delivery
 * the bottom inset is 320px and anything below it is covered by the
 * platform's own caption and like/comment/share rail. See
 * library/tools/safe_area.py.
 */
export const anchorStyle = (
  anchor: string,
  safeArea: Insets,
): React.CSSProperties => {
  const style: React.CSSProperties = { position: "absolute" };
  const [vertical, horizontal] = (() => {
    switch (anchor) {
      case "top_left":
        return ["top", "left"];
      case "top_centre":
        return ["top", "centre"];
      case "top_right":
        return ["top", "right"];
      case "middle_left":
        return ["middle", "left"];
      case "middle_right":
        return ["middle", "right"];
      case "bottom_left":
        return ["bottom", "left"];
      case "bottom_centre":
        return ["bottom", "centre"];
      case "bottom_right":
        return ["bottom", "right"];
      // `centre` is a DECLARED position, so it has its own arm. It used
      // to be served by `default`, which is also what an unrecognised
      // anchor falls into - the declared value and the fallback were
      // indistinguishable in the source, and a capability index that
      // reads this file could not tell that `centre` was implemented.
      case "centre":
        return ["middle", "centre"];
      default:
        return ["middle", "centre"];
    }
  })();

  if (vertical === "top") {
    style.top = safeArea.top;
    style.flexDirection = "column";
  } else if (vertical === "bottom") {
    // Rows stack UPWARD from a bottom anchor, so row 0 is the one
    // nearest the edge and adding a row never pushes one off-frame.
    style.bottom = safeArea.bottom;
    style.flexDirection = "column-reverse";
  } else {
    style.top = "50%";
    style.transform = "translateY(-50%)";
    style.flexDirection = "column";
  }
  style.display = "flex";
  style.gap = `${STACK_GAP_PX}px`;

  if (horizontal === "left") {
    style.left = safeArea.left;
  } else if (horizontal === "right") {
    style.right = safeArea.right;
    style.textAlign = "right";
  } else {
    style.left = safeArea.left;
    style.right = safeArea.right;
    style.textAlign = "center";
  }
  return style;
};

/** The usable width between the safe-area insets, in pixels.
 *
 * The same number `SafeAreaInsets.centered_usable_width` reports on the
 * Python side. An element sized as a SHARE of the frame is sized against
 * this rather than against the delivery width, so a share means the same
 * thing at any delivery format and nothing lands under the platform's
 * own rail (library/tools/safe_area.py).
 */
const safeUsableWidth = (safeArea: Insets, frameWidth: number): number =>
  Math.max(0, frameWidth - safeArea.left - safeArea.right);

const Runs: React.FC<{ element: PlannedElement; scale: number }> = ({
  element,
  scale,
}) => (
  <>
    {element.runs.map((run, i) => (
      <div
        key={i}
        style={{
          fontSize: `${(TYPE_SIZE[run.type_role] ?? TYPE_SIZE.supporting) * scale}px`,
          fontWeight: TYPE_WEIGHT[run.type_role] ?? TYPE_WEIGHT.supporting,
          color: element.color,
          textShadow: "0px 4px 12px rgba(0,0,0,0.6)",
          lineHeight: 1.1,
          letterSpacing: run.type_role === "display" ? "3px" : "0px",
          textTransform: run.type_role === "display" ? "uppercase" : "none",
        }}
      >
        {run.text}
      </div>
    ))}
  </>
);

/**
 * Typewriter text reveal: characters appear one at a time with a blinking
 * cursor at the reveal edge. Same styling as Runs, but clips each run's
 * text to a character count that advances with `revealProgress` (0-1).
 *
 * Inspired by Remotion Bits BasicTypewriter (MIT, github.com/av/remotion-bits).
 * The original is a standalone component; this integrates with the existing
 * run/type_role/colour system so a typewriter entrance on any text-carrying
 * element uses the plan's own copy and styling.
 */
const TypewriterRuns: React.FC<{
  element: PlannedElement;
  scale: number;
  /** 0 = nothing revealed, 1 = all text revealed */
  revealProgress: number;
}> = ({ element, scale, revealProgress }) => {
  // The reveal split is `typewriterSplit`, shared with FullFrameCard, so
  // the caret lands in the same place on both surfaces.
  const { shown, cursorRun } = typewriterSplit(
    element.runs.map((run) => run.text.length),
    revealProgress,
  );
  const cursorBlink = typewriterCursorOn(revealProgress);

  return (
    <>
      {element.runs.map((run, i) => {
        const runChars = shown[i];
        const visibleText = run.text.slice(0, runChars);
        // Only show cursor after the last visible character of the
        // last partially-revealed run.
        const showCursor = cursorRun === i;

        if (runChars === 0 && !showCursor) return null;

        return (
          <div
            key={i}
            style={{
              fontSize: `${(TYPE_SIZE[run.type_role] ?? TYPE_SIZE.supporting) * scale}px`,
              fontWeight: TYPE_WEIGHT[run.type_role] ?? TYPE_WEIGHT.supporting,
              color: element.color,
              textShadow: "0px 4px 12px rgba(0,0,0,0.6)",
              lineHeight: 1.1,
              letterSpacing: run.type_role === "display" ? "3px" : "0px",
              textTransform: run.type_role === "display" ? "uppercase" : "none",
            }}
          >
            {visibleText}
            {showCursor && (
              <span style={{ opacity: cursorBlink ? 1 : 0 }}>|</span>
            )}
          </div>
        );
      })}
    </>
  );
};

/**
 * Per-digit spring counter: each digit of the target number rolls
 * independently using spring physics, creating a mechanical odometer
 * effect. Each digit gets its own spring with a staggered delay, so
 * the least significant digits settle first.
 *
 * Inspired by Remotion Bits AnimatedCounter (MIT, github.com/av/remotion-bits).
 * The original is a standalone composition; this integrates with the
 * existing counter_roll element's data contract and colour system.
 */
const DigitRoll: React.FC<{
  value: string;
  fontSize: number;
  color: string;
  localFrame: number;
  fps: number;
  durationFrames: number;
}> = ({ value, fontSize, color, localFrame, fps, durationFrames }) => {
  const digits = value.split("");
  const digitHeight = fontSize * 1.2;

  return (
    <div
      style={{
        display: "flex",
        alignItems: "center",
        overflow: "hidden",
        height: `${digitHeight}px`,
      }}
    >
      {digits.map((char, i) => {
        // Non-digit characters (commas, dots, prefixes, suffixes) render static.
        const digitVal = parseInt(char, 10);
        if (isNaN(digitVal)) {
          return (
            <span
              key={i}
              style={{
                fontSize: `${fontSize}px`,
                fontWeight: 900,
                color,
                lineHeight: 1.2,
                fontVariantNumeric: "tabular-nums",
              }}
            >
              {char}
            </span>
          );
        }

        // Each digit springs at a stagger: the last digit (ones place)
        // starts first, giving the odometer look of the least significant
        // digits settling before the most significant ones.
        const reverseIndex = digits.length - 1 - i;
        const staggerDelay = Math.round(reverseIndex * (fps * 0.06));
        const delayedFrame = Math.max(0, localFrame - staggerDelay);

        // Spring from 0 to the target digit. The strip is 10 digits
        // (0-9) tall, and we translate to show the right one.
        const springVal = spring({
          frame: delayedFrame,
          fps,
          config: {
            damping: 15,
            stiffness: 80,
            mass: 0.8,
          },
          durationInFrames: Math.max(1, durationFrames - staggerDelay),
        });
        const yOffset = -digitVal * digitHeight * springVal;

        return (
          <div
            key={i}
            style={{
              width: `${fontSize * 0.65}px`,
              height: `${digitHeight}px`,
              overflow: "hidden",
              position: "relative",
            }}
          >
            <div
              style={{
                position: "absolute",
                top: 0,
                left: 0,
                transform: `translateY(${yOffset}px)`,
              }}
            >
              {Array.from({ length: 10 }, (_, d) => (
                <div
                  key={d}
                  style={{
                    fontSize: `${fontSize}px`,
                    fontWeight: 900,
                    color,
                    lineHeight: 1.2,
                    height: `${digitHeight}px`,
                    display: "flex",
                    alignItems: "center",
                    justifyContent: "center",
                    fontVariantNumeric: "tabular-nums",
                  }}
                >
                  {d}
                </div>
              ))}
            </div>
          </div>
        );
      })}
    </div>
  );
};

const DrawnElement: React.FC<{
  element: PlannedElement;
  frame: number;
  safeArea: Insets;
}> = ({ element, frame, safeArea }) => {
  const { fps, width: frameWidth } = useVideoConfig();
  const localFrame = frame - element.startFrame;
  if (localFrame < 0 || localFrame >= element.durationFrames) {
    return null;
  }
  const opacity = elementOpacity(
    localFrame,
    element.durationFrames,
    element.entrance,
    element.exit,
  );
  // `footprint` is the plan's share-of-the-frame number when it stated
  // one. Absent, the composition's own drawing stands - there is no
  // default footprint to substitute, and 1 is "as drawn", not a chosen
  // size.
  const scale = element.footprint && element.footprint > 0 ? element.footprint : 1;

  // Entrance and exit transforms: slide, scale, mask, and draw each
  // produce their own spatial animation. Fade is opacity-only.
  const entTransform = entranceTransform(
    localFrame, element.durationFrames, element.entrance);
  const extTransform = exitTransform(
    localFrame, element.durationFrames, element.exit);

  // When the entrance is `typewriter`, characters appear one at a time.
  // When the EXIT is, they leave the same way in reverse. Both are read
  // from their own field: reading the entrance and letting it stand for
  // the exit is what left `exit: typewriter` a plain fade under a
  // comment describing a reveal it never performed.
  // Both ramps, from the one shared helper. FullFrameCard reads the same
  // one, so a reveal means the same thing in both compositions.
  const shown = typewriterShown(
    localFrame, element.durationFrames, element.entrance, element.exit);
  const typewriting = isTypewriting(
    localFrame, element.durationFrames, element.entrance, element.exit);

  // TextContent: unified text renderer that uses TypewriterRuns for
  // typewriter entrances and exits, and Runs for everything else.
  const TextContent: React.FC<{ scl?: number }> = ({ scl }) => {
    const s = scl ?? scale;
    if (typewriting) {
      return <TypewriterRuns element={element} scale={s} revealProgress={shown} />;
    }
    return <Runs element={element} scale={s} />;
  };

  // `title_lockup` - the upper third, now carrying the copy the plan
  // wrote. It has drawn since the pipeline's first render and has never
  // held a word, because nothing was ever asked for one.
  if (element.element === "title_lockup") {
    return (
      <div
        style={{
          opacity,
          ...entTransform,
          ...extTransform,
          display: "flex",
          flexDirection: "column",
          gap: "12px",
        }}
      >
        <TextContent />
      </div>
    );
  }

  // `quote_card` - a run of copy set as a quotation, with a rule in the
  // element's own colour rather than a panel, so the picture stays
  // visible behind it.
  if (element.element === "quote_card") {
    return (
      <div
        style={{
          opacity,
          ...entTransform,
          ...extTransform,
          display: "flex",
          flexDirection: "column",
          gap: "16px",
          borderLeft: `${Math.round(8 * scale)}px solid ${element.color}`,
          paddingLeft: `${Math.round(28 * scale)}px`,
        }}
      >
        <TextContent />
      </div>
    );
  }

  // `progress_bar` - how far through the piece the viewer is. The two
  // fractions are computed against the WHOLE timeline in
  // motion_graphics_plan.resolve_plan, so the bar keeps meaning the
  // same thing however short the segment it is rendered in.
  if (element.element === "progress_bar") {
    const progressWidth = interpolate(
      localFrame,
      [0, Math.max(1, element.durationFrames - 1)],
      [element.timelineProgressStart * 100, element.timelineProgressEnd * 100],
      { extrapolateLeft: "clamp", extrapolateRight: "clamp" },
    );
    return (
      <div
        style={{
          position: "absolute",
          left: safeArea.left,
          right: safeArea.right,
          ...(String(element.anchor).startsWith("top")
            ? { top: safeArea.top }
            : { bottom: safeArea.bottom }),
          height: `${Math.round(12 * scale)}px`,
          backgroundColor: "rgba(255, 255, 255, 0.15)",
          opacity,
        }}
      >
        <div
          style={{
            width: `${progressWidth}%`,
            height: "100%",
            backgroundColor: element.color,
            boxShadow: `0 0 16px ${element.color}`,
          }}
        />
      </div>
    );
  }

  // `frame_accents` - four corner brackets. Chrome, declared as chrome:
  // it answers no question on its own and the roster says so.
  if (element.element === "frame_accents") {
    const size = Math.round(80 * scale);
    const stroke = Math.round(6 * scale);
    const corner = (
      key: string,
      style: React.CSSProperties,
    ): React.ReactElement => (
      <div
        key={key}
        style={{ position: "absolute", width: size, height: size, opacity, ...style }}
      />
    );
    return (
      <>
        {corner("tl", {
          top: safeArea.top,
          left: safeArea.left,
          borderTop: `${stroke}px solid ${element.color}`,
          borderLeft: `${stroke}px solid ${element.color}`,
        })}
        {corner("tr", {
          top: safeArea.top,
          right: safeArea.right,
          borderTop: `${stroke}px solid ${element.color}`,
          borderRight: `${stroke}px solid ${element.color}`,
        })}
        {corner("bl", {
          bottom: safeArea.bottom,
          left: safeArea.left,
          borderBottom: `${stroke}px solid ${element.color}`,
          borderLeft: `${stroke}px solid ${element.color}`,
        })}
        {corner("br", {
          bottom: safeArea.bottom,
          right: safeArea.right,
          borderBottom: `${stroke}px solid ${element.color}`,
          borderRight: `${stroke}px solid ${element.color}`,
        })}
      </>
    );
  }


  // `lower_third`
  if (element.element === "lower_third") {
    return (
      <div
        style={{
          opacity,
          ...entTransform,
          ...extTransform,
          display: "flex",
          flexDirection: "column",
          gap: "8px",
          backgroundColor: "rgba(0, 0, 0, 0.75)",
          padding: `${Math.round(24 * scale)}px ${Math.round(32 * scale)}px`,
          borderRadius: `${Math.round(16 * scale)}px`,
          borderLeft: `${Math.round(8 * scale)}px solid ${element.color}`,
          backdropFilter: "blur(8px)",
        }}
      >
        <TextContent />
      </div>
    );
  }

  // `channel_bug` - a project-supplied mark, held as chrome.
  //
  // The engine draws it and does not supply it: `asset` is a file out of
  // the project's own `brand_assets/`, staged verbatim into Remotion's
  // `public/brand/` by `remotion_brand_linker.link_brand_assets` and
  // resolved to this URL by `generate_motion_props`. An entry naming a
  // file that is not on disk never reaches here - `resolve_plan` drops
  // it as `asset_not_found_on_disk`, the same refusal
  // `compile_manifest` makes for an overlay segment the manifest names
  // and disk does not have.
  //
  // It carries no copy and no type role. `footprint` is its share of the
  // usable width and the element states its own; there is no default
  // size here, because a bug drawn at a size the engine chose is the
  // engine deciding how loud a client's mark is.
  if (element.element === "channel_bug") {
    if (!element.asset) return null;
    return (
      <img
        src={staticFile(element.asset)}
        alt=""
        style={{
          opacity,
          ...entTransform,
          ...extTransform,
          width: `${Math.round(safeUsableWidth(safeArea, frameWidth) * scale)}px`,
          height: "auto",
          objectFit: "contain",
          display: "block",
        }}
      />
    );
  }

  // `context_stamp`
  if (element.element === "context_stamp") {
    return (
      <div
        style={{
          opacity,
          ...entTransform,
          ...extTransform,
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          backgroundColor: element.color,
          padding: `${Math.round(8 * scale)}px ${Math.round(16 * scale)}px`,
          borderRadius: `${Math.round(4 * scale)}px`,
          boxShadow: `0 4px 12px rgba(0,0,0,0.4)`,
        }}
      >
        <div style={{ color: "#fff", textShadow: "none" }}>
          <TextContent scl={scale * 0.8} />
        </div>
      </div>
    );
  }

  // `stat_callout`
  if (element.element === "stat_callout") {
    return (
      <div
        style={{
          opacity,
          ...entTransform,
          ...extTransform,
          display: "flex",
          flexDirection: "column",
          gap: "12px",
          alignItems: "center",
        }}
      >
        <TextContent scl={scale * 1.5} />
      </div>
    );
  }

  // `beat_accent`
  if (element.element === "beat_accent") {
    const progress = localFrame / element.durationFrames;
    const size = Math.round(120 * scale * (0.5 + progress));
    return (
      <div
        style={{
          opacity: opacity * (1 - progress),
          width: `${size}px`,
          height: `${size}px`,
          borderRadius: "50%",
          backgroundColor: element.color,
          boxShadow: `0 0 24px ${element.color}`,
        }}
      />
    );
  }

  // `pointer_annotation`
  if (element.element === "pointer_annotation") {
    return (
      <div
        style={{
          opacity,
          width: `${Math.round(80 * scale)}px`,
          height: `${Math.round(80 * scale)}px`,
          borderRadius: "50%",
          border: `${Math.round(8 * scale)}px solid ${element.color}`,
          boxShadow: `0 0 16px ${element.color}, inset 0 0 16px ${element.color}`,
        }}
      />
    );
  }

  // `counter_roll` - a figure that animates from one value to another
  // across its life. The vocabulary says: "The CHANGE is the point -
  // growth, a countdown, an elapsed quantity - and a static figure would
  // show only its end." `data.start_value` and `data.end_value` drive
  // the roll; the copy runs carry the label. Cubic ease-out makes the
  // count decelerate into the final value.
  if (element.element === "counter_roll") {
    const data = element.data ?? {};
    const startVal = typeof data.start_value === "number" ? data.start_value : 0;
    const endVal = typeof data.end_value === "number" ? data.end_value : 100;
    const prefix = typeof data.prefix === "string" ? data.prefix : "";
    const suffix = typeof data.suffix === "string" ? data.suffix : "";
    const decimals = typeof data.decimals === "number" ? data.decimals : 0;

    // Ease into the target with an interpolation that starts fast and
    // settles, so the viewer reads the final value clearly.
    const rollProgress = interpolate(
      localFrame,
      [0, Math.max(1, element.durationFrames * 0.8)],
      [0, 1],
      { extrapolateLeft: "clamp", extrapolateRight: "clamp" },
    );
    // Cubic ease-out for a natural deceleration
    const eased = 1 - Math.pow(1 - rollProgress, 3);
    const currentValue = startVal + (endVal - startVal) * eased;
    const displayValue = decimals > 0
      ? currentValue.toFixed(decimals)
      : Math.round(currentValue).toLocaleString();
    const fontSize = (TYPE_SIZE.display ?? 56) * scale * 1.5;

    return (
      <div
        style={{
          opacity,
          ...entTransform,
          ...extTransform,
          display: "flex",
          flexDirection: "column",
          alignItems: "center",
          gap: `${Math.round(8 * scale)}px`,
        }}
      >
        <div
          style={{
            fontSize: `${fontSize}px`,
            fontWeight: 900,
            color: element.color,
            fontVariantNumeric: "tabular-nums",
            textShadow: `0 0 40px ${element.color}40, 0 4px 16px rgba(0,0,0,0.5)`,
            letterSpacing: "2px",
            lineHeight: 1.0,
          }}
        >
          {prefix}{displayValue}{suffix}
        </div>
        {element.runs.length > 0 && (
          <Runs element={element} scale={scale * 0.7} />
        )}
      </div>
    );
  }

  // `digit_counter` - per-digit spring counter: each digit of the target
  // number rolls independently using spring physics, creating a
  // mechanical odometer effect. Least-significant digits settle first.
  // Ships alongside counter_roll as a second option; the captain chooses.
  //
  // Inspired by Remotion Bits AnimatedCounter
  // (MIT, github.com/av/remotion-bits).
  if (element.element === "digit_counter") {
    const data = element.data ?? {};
    const endVal = typeof data.end_value === "number" ? data.end_value : 100;
    const prefix = typeof data.prefix === "string" ? data.prefix : "";
    const suffix = typeof data.suffix === "string" ? data.suffix : "";
    const decimals = typeof data.decimals === "number" ? data.decimals : 0;

    const displayValue = decimals > 0
      ? endVal.toFixed(decimals)
      : Math.round(endVal).toLocaleString();
    const fontSize = (TYPE_SIZE.display ?? 56) * scale * 1.5;

    return (
      <div
        style={{
          opacity,
          ...entTransform,
          ...extTransform,
          display: "flex",
          flexDirection: "column",
          alignItems: "center",
          gap: `${Math.round(8 * scale)}px`,
        }}
      >
        <div
          style={{
            display: "flex",
            alignItems: "center",
            textShadow: `0 0 40px ${element.color}40, 0 4px 16px rgba(0,0,0,0.5)`,
            letterSpacing: "2px",
          }}
        >
          {prefix && (
            <span
              style={{
                fontSize: `${fontSize}px`,
                fontWeight: 900,
                color: element.color,
                lineHeight: 1.0,
              }}
            >
              {prefix}
            </span>
          )}
          <DigitRoll
            value={displayValue}
            fontSize={fontSize}
            color={element.color}
            localFrame={localFrame}
            fps={fps}
            durationFrames={element.durationFrames}
          />
          {suffix && (
            <span
              style={{
                fontSize: `${fontSize}px`,
                fontWeight: 900,
                color: element.color,
                lineHeight: 1.0,
              }}
            >
              {suffix}
            </span>
          )}
        </div>
        {element.runs.length > 0 && (
          <Runs element={element} scale={scale * 0.7} />
        )}
      </div>
    );
  }

  // `list_build` - items appearing one at a time with staggered entrances.
  // Each run is one list item, revealed as its portion of the duration
  // arrives. Uses spring physics for a natural settle on each item.
  if (element.element === "list_build") {
    const items = element.runs;
    if (!items.length) return null;
    // Each item gets an equal share of the hold. The entrance happens
    // at the front of its share, so item N appears after item N-1 has
    // settled but before the element's own exit begins.
    const holdFrames = Math.max(1, element.durationFrames - (RAMP_FRAMES[element.exit] ?? 0));
    const staggerInterval = holdFrames / items.length;

    return (
      <div
        style={{
          opacity,
          ...entTransform,
          ...extTransform,
          display: "flex",
          flexDirection: "column",
          gap: `${Math.round(12 * scale)}px`,
        }}
      >
        {items.map((run, i) => {
          const itemStart = Math.round(i * staggerInterval);
          const itemProgress = Math.max(0, Math.min(1,
            (localFrame - itemStart) / Math.max(1, staggerInterval * 0.6),
          ));
          // Cubic ease-out for a natural deceleration per item
          const eased = 1 - Math.pow(1 - itemProgress, 3);
          const itemOpacity = eased;
          const itemY = interpolate(eased, [0, 1], [20, 0], {
            extrapolateLeft: "clamp",
            extrapolateRight: "clamp",
          });

          return (
            <div
              key={i}
              style={{
                opacity: itemOpacity,
                transform: `translateY(${itemY}px)`,
                display: "flex",
                alignItems: "flex-start",
                gap: `${Math.round(12 * scale)}px`,
              }}
            >
              <div
                style={{
                  width: `${Math.round(8 * scale)}px`,
                  height: `${Math.round(8 * scale)}px`,
                  borderRadius: "50%",
                  backgroundColor: element.color,
                  marginTop: `${Math.round(10 * scale)}px`,
                  flexShrink: 0,
                }}
              />
              <div
                style={{
                  fontSize: `${(TYPE_SIZE[run.type_role] ?? TYPE_SIZE.supporting) * scale}px`,
                  fontWeight: TYPE_WEIGHT[run.type_role] ?? TYPE_WEIGHT.supporting,
                  color: element.color,
                  textShadow: "0px 4px 12px rgba(0,0,0,0.6)",
                  lineHeight: 1.3,
                }}
              >
                {run.text}
              </div>
            </div>
          );
        })}
      </div>
    );
  }

  // `comparison_bars` - labelled horizontal bars at proportional lengths,
  // each growing from zero to its share of the maximum. The bars appear
  // in the order the runs are listed, which is the order spoken.
  if (element.element === "comparison_bars") {
    const data = element.data ?? {};
    const values: number[] = Array.isArray(data.values) ? data.values : [];
    const maxValue = Math.max(...values, 1);
    const items = element.runs;
    // Use whichever is shorter: runs or values. Extra of either is ignored.
    const count = Math.min(items.length, values.length);
    if (count === 0) return null;

    const holdFrames = Math.max(1, element.durationFrames - (RAMP_FRAMES[element.exit] ?? 0));
    const staggerInterval = holdFrames / count;

    return (
      <div
        style={{
          opacity,
          ...entTransform,
          ...extTransform,
          display: "flex",
          flexDirection: "column",
          gap: `${Math.round(16 * scale)}px`,
          width: "100%",
        }}
      >
        {Array.from({ length: count }).map((_, i) => {
          const itemStart = Math.round(i * staggerInterval);
          const growProgress = Math.max(0, Math.min(1,
            (localFrame - itemStart) / Math.max(1, staggerInterval * 0.7),
          ));
          const eased = 1 - Math.pow(1 - growProgress, 3);
          const barWidth = (values[i] / maxValue) * 100 * eased;

          return (
            <div
              key={i}
              style={{
                opacity: eased,
                display: "flex",
                flexDirection: "column",
                gap: `${Math.round(4 * scale)}px`,
              }}
            >
              <div
                style={{
                  fontSize: `${(TYPE_SIZE[items[i].type_role] ?? TYPE_SIZE.micro) * scale}px`,
                  fontWeight: TYPE_WEIGHT[items[i].type_role] ?? TYPE_WEIGHT.micro,
                  color: element.color,
                  textShadow: "0px 2px 8px rgba(0,0,0,0.5)",
                }}
              >
                {items[i].text}
              </div>
              <div
                style={{
                  width: "100%",
                  height: `${Math.round(12 * scale)}px`,
                  backgroundColor: "rgba(255, 255, 255, 0.1)",
                  borderRadius: `${Math.round(6 * scale)}px`,
                  overflow: "hidden",
                }}
              >
                <div
                  style={{
                    width: `${barWidth}%`,
                    height: "100%",
                    backgroundColor: element.color,
                    borderRadius: `${Math.round(6 * scale)}px`,
                    boxShadow: `0 0 12px ${element.color}60`,
                  }}
                />
              </div>
            </div>
          );
        })}
      </div>
    );
  }

  // `step_counter` - a positional marker saying "2 of 5". The position
  // and total come from data; an optional copy run labels the section.
  if (element.element === "step_counter") {
    const data = element.data ?? {};
    const position = typeof data.position === "number" ? data.position : 1;
    const total = typeof data.total === "number" ? data.total : 1;

    return (
      <div
        style={{
          opacity,
          ...entTransform,
          ...extTransform,
          display: "flex",
          flexDirection: "column",
          alignItems: "center",
          gap: `${Math.round(8 * scale)}px`,
        }}
      >
        <div
          style={{
            display: "flex",
            alignItems: "baseline",
            gap: `${Math.round(4 * scale)}px`,
          }}
        >
          <span
            style={{
              fontSize: `${(TYPE_SIZE.display ?? 56) * scale}px`,
              fontWeight: 900,
              color: element.color,
              fontVariantNumeric: "tabular-nums",
              textShadow: `0 0 24px ${element.color}40, 0 4px 12px rgba(0,0,0,0.5)`,
              lineHeight: 1.0,
            }}
          >
            {position}
          </span>
          <span
            style={{
              fontSize: `${(TYPE_SIZE.micro ?? 24) * scale}px`,
              fontWeight: TYPE_WEIGHT.micro ?? 600,
              color: element.color,
              opacity: 0.6,
              textShadow: "0px 2px 8px rgba(0,0,0,0.4)",
            }}
          >
            of {total}
          </span>
        </div>
        {element.runs.length > 0 && (
          <Runs element={element} scale={scale * 0.7} />
        )}
      </div>
    );
  }

  // Unreachable by construction: resolve_plan drops anything this
  // switch has no arm for, by name and with the reason recorded. Drawing
  // nothing here rather than throwing keeps a props file hand-edited in
  // the Remotion studio from taking the render down.
  return null;
};

/** Elements that lay themselves out across the whole frame.
 *
 * Chrome, not copy: a bar spans the safe box left to right and four
 * corner brackets are at four corners, so neither belongs in a stack
 * and neither can collide with one.
 */
const SELF_POSITIONING = new Set(["progress_bar", "frame_accents"]);

export const isLive = (element: PlannedElement, frame: number): boolean => {
  const local = frame - element.startFrame;
  return local >= 0 && local < element.durationFrames;
};

export const MotionGraphics: React.FC<MotionGraphicsProps> = ({
  elements,
  safeArea,
}) => {
  const frame = useCurrentFrame();

  // No default. The insets come from library/tools/safe_area.py, and
  // inventing one here is precisely the "three more hardcoded margins"
  // the captain's ruling of 2026-08-25 forbade.
  if (!safeArea) {
    throw new Error(
      "MotionGraphics props carry no safeArea. generate_motion_props " +
        "resolves it from library/tools/safe_area.py. Refusing to " +
        "substitute a margin: an accent drawn by a literal sits under " +
        "the platform's own interface with nothing to notice.",
    );
  }

  return (
    <AbsoluteFill
      className="pointer-events-none"
      style={{ fontFamily: "Montserrat, sans-serif" }}
    >
      {/*
        Chrome first: each of these positions itself across the whole
        safe box and cannot collide with a stack.
      */}
      {(elements ?? [])
        .filter((element) => SELF_POSITIONING.has(element.element))
        .map((element, i) => (
          <DrawnElement
            key={`chrome-${element.element}-${i}`}
            element={element}
            frame={frame}
            safeArea={safeArea}
          />
        ))}

      {/*
        Then the copy elements, GROUPED BY ANCHOR and stacked in row
        order inside one flex container per anchor. That is what makes a
        row clear the one above it: the browser lays them out against
        each other's real height, so a two-run title lockup pushes the
        row below it down by however tall it turned out to be. A fixed
        offset per row cannot do that, and the first two-row plan drew
        one graphic straight through another.

        Only the elements ON SCREEN are laid out, so a row whose
        neighbour has ended moves up - rows are an ordering, not
        reserved slots, and a permanently reserved lane would leave a
        hole in the frame for the whole segment.
      */}
      {Array.from(
        new Set(
          (elements ?? [])
            .filter((element) => !SELF_POSITIONING.has(element.element))
            .map((element) => element.anchor),
        ),
      ).map((anchor) => {
        const live = (elements ?? [])
          .filter(
            (element) =>
              !SELF_POSITIONING.has(element.element) &&
              element.anchor === anchor &&
              isLive(element, frame),
          )
          .sort((a, b) => (a.row ?? 0) - (b.row ?? 0));
        if (!live.length) {
          return null;
        }
        return (
          <div key={`stack-${anchor}`} style={anchorStyle(anchor, safeArea)}>
            {live.map((element, i) => (
              <DrawnElement
                key={`${element.element}-${element.row}-${i}`}
                element={element}
                frame={frame}
                safeArea={safeArea}
              />
            ))}
          </div>
        );
      })}
    </AbsoluteFill>
  );
};
