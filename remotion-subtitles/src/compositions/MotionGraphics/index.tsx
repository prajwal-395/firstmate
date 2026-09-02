/* eslint-disable @typescript-eslint/no-explicit-any */
import React from "react";
import { AbsoluteFill, useCurrentFrame, interpolate } from "remotion";
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
 * `element` is a roster key. The four this composition draws are
 * `title_lockup`, `quote_card`, `progress_bar` and `frame_accents` -
 * the four the roster marks `reachable_now`. An entry naming any other
 * element never reaches these props: `motion_graphics_plan.resolve_plan`
 * drops it with `renderer_cannot_draw_it_yet` and records the drop.
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

const DrawnElement: React.FC<{
  element: PlannedElement;
  frame: number;
  safeArea: Insets;
}> = ({ element, frame, safeArea }) => {
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

  // `title_lockup` - the upper third, now carrying the copy the plan
  // wrote. It has drawn since the pipeline's first render and has never
  // held a word, because nothing was ever asked for one.
  if (element.element === "title_lockup") {
    return (
      <div
        style={{
          opacity,
          display: "flex",
          flexDirection: "column",
          gap: "12px",
        }}
      >
        <Runs element={element} scale={scale} />
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
          display: "flex",
          flexDirection: "column",
          gap: "16px",
          borderLeft: `${Math.round(8 * scale)}px solid ${element.color}`,
          paddingLeft: `${Math.round(28 * scale)}px`,
        }}
      >
        <Runs element={element} scale={scale} />
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
