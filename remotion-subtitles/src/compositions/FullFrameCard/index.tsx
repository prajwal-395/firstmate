/**
 * FullFrameCard - the whole frame, for a bounded stretch, IN PLACE OF picture.
 *
 * Every other composition in this project is an OVERLAY: SubtitleOverlay,
 * MotionGraphics and TimedTextOverlay all render with `--transparent` and
 * are composited over footage that keeps playing underneath. This one is
 * not. It draws its own ground and is placed on the reel's own picture
 * track (V1) for its own stretch of reel time, so there is nothing
 * beneath it and nothing to composite over.
 *
 * That difference is the reason it is a separate composition rather than
 * a MotionGraphics element with a "full frame" footprint. An overlay at
 * full opacity would hide the picture and leave the SOUND playing - and
 * the reels path cannot turn that sound down, because Resolve's scripting
 * API exposes no audio level at all (AGENTS.md 5). See
 * `library/tools/full_frame_element.py`, which is the one enumeration.
 *
 * It states nothing of its own. The ground, the typeface, every colour,
 * every word, the hold and the motion character all arrive in props from
 * a declaration the project wrote. There is no default background, no
 * default colour and no default copy here, for the reason `house_look.py`
 * was emptied (AGENTS.md 10.5). The only value this file supplies is what
 * a named motion character LOOKS like, and it imports those from
 * MotionGraphics rather than respelling them - one drawing of `blur` in
 * this engine, not two.
 */
import React from "react";
import { AbsoluteFill, useCurrentFrame } from "remotion";
import { loadBundledFonts, loadProjectFont } from "../../fonts";
import {
  elementOpacity,
  entranceTransform,
  exitTransform,
  typewriterCursorOn,
  typewriterShown,
  typewriterSplit,
} from "../MotionGraphics";

export type CardRun = {
  text: string;
  /** `display` | `supporting` | `micro` - the vocabulary's own axis. */
  type_role: string;
  colour: string;
  /** Pixels of the delivery frame, when the declaration states one. */
  font_size?: number;
};

export type FullFrameCardProps = {
  runs: CardRun[];
  /** The card's own ground. Required: a full-frame element IS the picture. */
  background: string;
  entrance: string;
  exit: string;
  fontFamily: string;
  fontFile?: string;
  fps: number;
  width: number;
  height: number;
  durationInFrames: number;
  safeArea: { top: number; right: number; bottom: number; left: number };
  /**
   * Normalised vertical centre of the stack on the delivery frame.
   *
   * Absent means centred in the safe box, which is the ABSENCE of a
   * position rather than a chosen one: unlike a timed text moment there
   * is no picture underneath and no letterbox bar to clear, so there is
   * nothing for a position to be measured against.
   */
  y?: number;
};

export const fullFrameCardSchema = {} as any;

/** The pixel size of each typographic weight, when a run states none.
 *
 * The same three the overlay roster names, at the same sizes MotionGraphics
 * draws them - a card is read at the same distance as a title lockup. A
 * run that states its own `font_size` overrides this; the declaration owns
 * the magnitude and this is only what the WEIGHT means in pixels when it
 * does not.
 */
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

/** Gap between two stacked runs, in pixels.
 *
 * A gap rather than a row pitch, for the reason MotionGraphics' own
 * STACK_GAP_PX is: a fixed pitch draws one run through another the moment
 * a run wraps to two lines.
 */
const RUN_GAP_PX = 20;

export const FullFrameCard: React.FC<FullFrameCardProps> = ({
  runs,
  background,
  entrance,
  exit,
  fontFamily,
  fontFile,
  width,
  height,
  durationInFrames,
  safeArea,
  y,
}) => {
  const frame = useCurrentFrame();

  // No default, and no substitution. Same refusal as TimedTextOverlay:
  // library/tools/safe_area.py owns the insets and inventing one here is
  // the "three more hardcoded margins" the captain's ruling of 2026-08-25
  // forbade.
  if (!safeArea) {
    throw new Error(
      "FullFrameCard props carry no safeArea. " +
        "library/tools/full_frame_element.py resolves it from " +
        "library/tools/safe_area.py. Refusing to substitute a margin.",
    );
  }
  if (!background) {
    throw new Error(
      "FullFrameCard props carry no background. A full-frame element IS " +
        "the picture, so it carries its own ground; there is nothing " +
        "underneath for an alpha channel to reveal and this composition " +
        "declares no colour of its own.",
    );
  }

  // BLOCKING, and throwing on a miss - a substituted face is a valid
  // picture of the right size that nothing downstream can tell apart.
  loadBundledFonts();
  if (fontFile) {
    loadProjectFont(fontFamily, fontFile);
  }

  const opacity = elementOpacity(frame, durationInFrames, entrance, exit);

  // `typewriter` is not a transform, it is a reveal, so `entranceTransform`
  // returns nothing for it and the card would hold still while claiming a
  // typewriter entrance - a declared character that draws nothing, which
  // is the defect class this repository keeps removing (AGENTS.md 10.2).
  // The split is MotionGraphics' own, shared rather than respelled.
  //
  // `typewriterShown` and not `typewriterProgress`: the latter answers
  // the ENTRANCE only, so a card declaring `exit: "typewriter"` held
  // every glyph and faded, which is the same defect this comment
  // describes seen from the other end of the element.
  const reveal = typewriterShown(frame, durationInFrames, entrance, exit);
  const { shown, cursorRun } = typewriterSplit(
    runs.map((run) => run.text.length),
    reveal,
  );
  const cursorOn = typewriterCursorOn(reveal);
  const motion = {
    ...entranceTransform(frame, durationInFrames, entrance),
    ...exitTransform(frame, durationInFrames, exit),
  };

  const boxWidth = width - safeArea.left - safeArea.right;
  const boxTop = safeArea.top;
  const boxHeight = height - safeArea.top - safeArea.bottom;

  // Centred in the safe box unless the declaration states a `y`. When it
  // does, the stack's own centre sits on that fraction of the DELIVERY
  // frame - the same frame `timed_text_overlay` normalises against, so
  // one reading of `y` in this engine rather than two.
  const positioned: React.CSSProperties =
    y === undefined || y === null
      ? {
          top: boxTop,
          height: boxHeight,
          justifyContent: "center",
        }
      : {
          top: 0,
          height: height,
          justifyContent: "flex-start",
          paddingTop: Math.max(0, height * y),
          transform: "translateY(-50%)",
        };

  return (
    <AbsoluteFill style={{ backgroundColor: background, fontFamily }}>
      <div
        style={{
          position: "absolute",
          left: safeArea.left,
          width: boxWidth,
          display: "flex",
          flexDirection: "column",
          alignItems: "center",
          gap: `${RUN_GAP_PX}px`,
          opacity,
          ...positioned,
          ...motion,
        }}
      >
        {runs.map((run, index) => {
          const visible = run.text.slice(0, shown[index]);
          const showCursor = cursorRun === index;
          if (visible.length === 0 && !showCursor) return null;
          return (
            <div
              key={index}
              style={{
                color: run.colour,
                fontSize: `${run.font_size ?? TYPE_SIZE[run.type_role] ?? TYPE_SIZE.supporting}px`,
                fontWeight: TYPE_WEIGHT[run.type_role] ?? TYPE_WEIGHT.supporting,
                lineHeight: 1.18,
                textAlign: "center",
                width: "100%",
                // Wrapped against the safe box, never the frame: the outer
                // insets belong to the platform's interface.
                overflowWrap: "break-word",
              }}
            >
              {visible}
              {showCursor && (
                <span style={{ opacity: cursorOn ? 1 : 0 }}>|</span>
              )}
            </div>
          );
        })}
      </div>
    </AbsoluteFill>
  );
};
