/* eslint-disable @typescript-eslint/no-explicit-any */
import React from "react";
import { AbsoluteFill } from "remotion";

export type LucieLogoAnimationProps = {
  accentColor: string;
  style: "full" | "minimal";
  fps: number;
  width: number;
  height: number;
  durationInFrames: number;
};

export const lucieLogoAnimationSchema = {} as any;

export const LucieLogoAnimation: React.FC<LucieLogoAnimationProps> = () => {
  return <AbsoluteFill className="pointer-events-none" />;
};
