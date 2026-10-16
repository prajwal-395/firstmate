import React from "react";
import { useCurrentFrame, Easing } from "remotion";
import { SubtitleStyle } from "./index";

type AnimatedWordProps = {
  word: string;
  startFrame: number;
  endFrame: number;
  isEmphasis?: boolean;
  style?: SubtitleStyle;
};

/** Scale bump an emphasised word holds once it has been spoken. */
const EMPHASIS_SCALE = 1.14;
/** Karaoke scale bounds: grow into the measured box, never beyond it. */
const KARAOKE_SCALE_RANGE = [0.95, 1] as const;
const KARAOKE_REST_SCALE = KARAOKE_SCALE_RANGE[0];
const KARAOKE_SCALE = KARAOKE_SCALE_RANGE[1];
const KARAOKE_RAMP_FRAMES = 4;

/**
 * Grow a spoken word into its measured layout box, then return to its
 * resting scale as soon as its highlight window closes. The span's
 * inline-block width remains its font-measured width: CSS transforms affect
 * only the painted glyphs, so this animation cannot reflow the caption.
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
  return KARAOKE_REST_SCALE +
    (KARAOKE_SCALE - KARAOKE_REST_SCALE) * eased;
};

export const AnimatedWord: React.FC<AnimatedWordProps> = ({
  word,
  startFrame,
  endFrame,
  isEmphasis = false,
  style,
}) => {
  const frame = useCurrentFrame();

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

  const opacity = 1;

  return (
    <span
      style={{
        display: "inline-block",
        transform: `scale(${scale})`,
        transformOrigin: "center center",
        fontSize: `${fontScale}em`,
        opacity,
        color: color,
        marginRight: "0.24em",
      }}
    >
      {word}
    </span>
  );
};
