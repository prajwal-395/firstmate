/* eslint-disable @typescript-eslint/no-unused-vars */
import React from "react";
import { useCurrentFrame, interpolate, Easing } from "remotion";

type AnimatedWordProps = {
  word: string;
  startFrame: number;
  endFrame: number;
  isEmphasis?: boolean;
};

export const AnimatedWord: React.FC<AnimatedWordProps> = ({
  word,
  startFrame,
  endFrame,
  isEmphasis = false,
}) => {
  const frame = useCurrentFrame();
  const isActive = frame >= startFrame;
  const scale = isActive
    ? interpolate(frame, [startFrame, startFrame + 4], [0.95, 1], {
        extrapolateRight: "clamp",
        easing: Easing.out(Easing.ease),
      })
    : 0.95;
  const opacity = isActive
    ? interpolate(frame, [startFrame, startFrame + 3], [0, 1], {
        extrapolateRight: "clamp",
      })
    : 0;

  return (
    <span
      style={{
        display: "inline-block",
        transform: `scale(${scale})`,
        opacity,
        color: isEmphasis ? "#FFAA4D" : "#FFFFFF",
      }}
    >
      {word}
    </span>
  );
};
