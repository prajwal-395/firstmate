/* eslint-disable @typescript-eslint/no-unused-vars */
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
  const isYetToSpeak = frame < startFrame;
  
  let color = style?.fontColor || "#FFFFFF";
  if (isSpokenNow) {
    color = style?.accentColor || "#FBF0B8";
  }

  const scale = isSpokenNow
    ? interpolate(frame, [startFrame, startFrame + 4], [0.95, 1], {
        extrapolateRight: "clamp",
        easing: Easing.out(Easing.ease),
      })
    : (hasSpoken ? 1 : 0.95);

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
