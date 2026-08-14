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
  const hasSpoken = frame >= endFrame;
  const isSpokenNow = frame >= startFrame && frame < endFrame;

  let color = style?.fontColor || "#FFFFFF";
  if (isSpokenNow) {
    color = style?.accentColor || "#FBF0B8";
  } else if (isEmphasis) {
    // An emphasis word keeps the accent colour after it is spoken, so the
    // key words of a caption still read as the key words.
    color = style?.accentColor || "#FBF0B8";
  }

  // The emphasis pass (step_4_01) exists to give these words a scale bump.
  // `isEmphasis` was declared here and never passed in, so every word was
  // rendered identically and the whole pass was invisible.
  const restScale = isEmphasis ? EMPHASIS_SCALE : 1;
  const enterScale = isEmphasis ? EMPHASIS_SCALE : 0.95;

  const scale = isSpokenNow
    ? interpolate(frame, [startFrame, startFrame + 4], [0.95, enterScale], {
        extrapolateRight: "clamp",
        easing: Easing.out(Easing.ease),
      })
    : hasSpoken
      ? restScale
      : 0.95;

  const opacity = 1;

  return (
    <span
      style={{
        display: "inline-block",
        transform: `scale(${scale})`,
        opacity,
        color: color,
        marginRight: "16px",
      }}
    >
      {word}
    </span>
  );
};
