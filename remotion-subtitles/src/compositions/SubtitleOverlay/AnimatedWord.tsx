import React from "react";
import { useCurrentFrame, interpolate, Easing } from "remotion";
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
  const hasSpoken = frame >= endFrame;
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
  // Transform is still used for the entry animation, but only to grow
  // INTO place from 0.95 - it never exceeds 1, so it cannot overlap.
  const fontScale = isEmphasis ? EMPHASIS_SCALE : 1;

  const scale = isSpokenNow
    ? interpolate(frame, [startFrame, startFrame + 4], [0.95, 1], {
        extrapolateRight: "clamp",
        easing: Easing.out(Easing.ease),
      })
    : hasSpoken
      ? 1
      : 0.95;

  const opacity = 1;

  return (
    <span
      style={{
        display: "inline-block",
        transform: `scale(${scale})`,
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
