/* eslint-disable @typescript-eslint/no-explicit-any */
import React from "react";
import { AbsoluteFill } from "remotion";

export type MotionGraphicsProps = {
  title: string;
  subtitle: string;
  accentColor: string;
  showUpperThird: boolean;
  showProgress: boolean;
  showAccents: boolean;
  timelineProgressStart: number;
  timelineProgressEnd: number;
  fps: number;
  width: number;
  height: number;
  durationInFrames: number;
};

export const motionGraphicsSchema = {} as any;

export const MotionGraphics: React.FC<MotionGraphicsProps> = () => {
  return <AbsoluteFill className="pointer-events-none" />;
};
