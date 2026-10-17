import React from "react";
import { useCurrentFrame, Easing } from "remotion";
import { SubtitleStyle } from "./index";

type AnimatedWordProps = {
  word: string;
  startFrame: number;
  endFrame: number;
  isEmphasis?: boolean;
  nextWord?: string;
  nextIsEmphasis?: boolean;
  nextScale?: number;
  fontFamily?: string;
  fontWeight?: number;
  baseFontSize?: number;
  style?: SubtitleStyle;
};

/** Scale bump an emphasised word holds once it has been spoken. */
export const EMPHASIS_SCALE = 1.14;
/** Karaoke scale bounds: grow into the measured box, never beyond it. */
const KARAOKE_SCALE_RANGE = [0.95, 1] as const;
const KARAOKE_REST_SCALE = KARAOKE_SCALE_RANGE[0];
const KARAOKE_SCALE = KARAOKE_SCALE_RANGE[1];
const KARAOKE_RAMP_FRAMES = 4;

/** Measured mean ink gap in delivered Reel 22, divided by its 58px type. */
export const DEFAULT_WORD_GAP_EM = 20.667 / 58;

/** Normalize a timed token before measuring or drawing it. */
export const normalizeWordSpacing = (word: string): string =>
  word.trim().replace(/\s+/g, " ");

/**
 * Grow a spoken word within its measured layout box, then return to its
 * resting scale as soon as its highlight window closes. Spacing is adjusted
 * from the font's measured ink bounds so the painted gap remains stable even
 * though transforms do not change the inline layout width.
 */
export const karaokeScaleAtFrame = (
  frame: number,
  startFrame: number,
  endFrame: number,
): number => {
  if (endFrame <= startFrame) {
    return 1;
  }
  if (frame < startFrame || frame >= endFrame) return KARAOKE_REST_SCALE;

  const duration = endFrame - startFrame;
  const ramp = Math.min(KARAOKE_RAMP_FRAMES, duration / 2);
  const elapsed = frame - startFrame;
  const remaining = endFrame - frame;
  const progress = Math.min(1, elapsed / ramp, remaining / ramp);
  const eased = Easing.inOut(Easing.ease)(progress);
  return KARAOKE_REST_SCALE + (KARAOKE_SCALE - KARAOKE_REST_SCALE) * eased;
};

type InkBounds = { advance: number; left: number; right: number };
const inkBoundsCache = new Map<string, InkBounds>();

const measureInk = (word: string, font: string): InkBounds => {
  const key = `${font}\u0000${word}`;
  const cached = inkBoundsCache.get(key);
  if (cached) return cached;
  const canvas = document.createElement("canvas");
  const context = canvas.getContext("2d");
  if (!context)
    throw new Error("Canvas 2D is required to measure subtitle ink");
  context.font = font;
  const metrics = context.measureText(word);
  const bounds = {
    advance: metrics.width,
    left: metrics.actualBoundingBoxLeft,
    right: metrics.actualBoundingBoxRight,
  };
  if (![bounds.advance, bounds.left, bounds.right].every(Number.isFinite)) {
    throw new Error(`Incomplete font ink metrics for subtitle word: ${word}`);
  }
  inkBoundsCache.set(key, bounds);
  return bounds;
};

const paintedLeft = ({ advance, left }: InkBounds, scale: number) =>
  (advance * (1 - scale)) / 2 - left * scale;
const paintedRight = ({ advance, right }: InkBounds, scale: number) =>
  (advance * (1 - scale)) / 2 + right * scale;

export const AnimatedWord: React.FC<AnimatedWordProps> = ({
  word,
  startFrame,
  endFrame,
  isEmphasis = false,
  nextWord,
  nextIsEmphasis = false,
  nextScale = 1,
  fontFamily = "Montserrat",
  fontWeight = 800,
  baseFontSize = 58,
  style,
}) => {
  const frame = useCurrentFrame();
  const renderedWord = normalizeWordSpacing(word);
  const renderedNextWord =
    nextWord === undefined ? undefined : normalizeWordSpacing(nextWord);

  // A highlight window with no width can never sweep: the accent phase
  // below (`endFrame > startFrame && frame >= startFrame && frame <
  // endFrame`) is unsatisfiable, so the sweep travels past the word
  // without ever accenting it. That is the pile-up Reel 05 shipped at
  // frame 241 - seven windows across three cards, from aligner stamps
  // the props builder clamps to the card. The word is still SPOKEN, so
  // it is still DRAWN: returning null here deleted "10-man" /
  // "50-person" / "hundred" from the captions entirely (this component
  // draws `words`, never the card `text`), which the captain read as
  // missing numbers. The absence of a sweep is honest either way: no
  // timing is invented here.
  const isSpokenNow =
    endFrame > startFrame && frame >= startFrame && frame < endFrame;

  let color = style?.fontColor || "#FFFFFF";
  if (isSpokenNow) {
    color = style?.accentColor || "#FBF0B8";
  }

  // The emphasis pass (step_4_01) exists to make these words bigger.
  // `isEmphasis` was declared here and never passed in, so every word was
  // rendered identically and the whole pass was invisible.
  //
  // The size comes from FONT SIZE, not from a transform. `transform:
  // scale()` grows the glyph without reserving any layout width, so an
  // emphasised word overflowed its own box by width*(scale-1)/2 on each
  // side and collided with its neighbours: "the brand template" rendered
  // as "thebrandtemplate". The larger a caption got the worse it read,
  // which the brand-template styles made obvious at 72px/900.
  //
  // Planned emphasis reserves its larger font size in layout. The karaoke
  // pulse grows only to the measured layout width, never beyond it, so it
  // cannot collide with the next word. The measured width and line breaks
  // remain unchanged throughout the pulse.
  const fontScale = isEmphasis ? EMPHASIS_SCALE : 1;
  const scale = karaokeScaleAtFrame(frame, startFrame, endFrame);
  const ownFontSize = baseFontSize * fontScale;
  const ownBounds = measureInk(
    renderedWord,
    `${fontWeight} ${ownFontSize}px ${JSON.stringify(fontFamily)}`,
  );
  let marginRight = 0;
  if (renderedNextWord !== undefined) {
    const nextFontSize = baseFontSize * (nextIsEmphasis ? EMPHASIS_SCALE : 1);
    const nextBounds = measureInk(
      renderedNextWord,
      `${fontWeight} ${nextFontSize}px ${JSON.stringify(fontFamily)}`,
    );
    const baseGap =
      (style?.wordGapEm ?? DEFAULT_WORD_GAP_EM) * baseFontSize;
    // Make the painted ink gap equal the common base gap. CSS transforms
    // scale around each slot's center, so compensate both glyph bearings as
    // well as the animated scale; this also prevents pairs with large natural
    // side bearings from opening wider than the rest of the line.
    marginRight =
      baseGap -
      ownBounds.advance -
      paintedLeft(nextBounds, nextScale) +
      paintedRight(ownBounds, scale);
  }

  const opacity = 1;

  return (
    <span
      style={{
        display: "inline-block",
        flexShrink: 0,
        transform: `scale(${scale})`,
        transformOrigin: "center center",
        fontSize: `${fontScale}em`,
        opacity,
        color: color,
        marginRight: `${marginRight}px`,
      }}
    >
      {renderedWord}
    </span>
  );
};
