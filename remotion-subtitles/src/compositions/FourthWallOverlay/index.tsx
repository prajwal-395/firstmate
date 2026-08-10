/* eslint-disable @typescript-eslint/no-explicit-any */
import React from "react";
import { AbsoluteFill } from "remotion";

export type FourthWallOverlayProps = {
  nightCardText: string;
  nightCardColor: string;
  nightCardStartFrame: number;
  nightCardDurationFrames: number;
  closingLine1Text: string;
  closingLine1Color: string;
  closingLine1StartFrame: number;
  closingLine2Text: string;
  closingLine2Color: string;
  closingLine2StartFrame: number;
  counterText: string;
  counterColor: string;
  counterStartFrame: number;
  counterDurationFrames: number;
  fontFamily: string;
  fps: number;
  width: number;
  height: number;
  durationInFrames: number;
};

export const fourthWallOverlaySchema = {} as any;

export const FourthWallOverlay: React.FC<FourthWallOverlayProps> = () => {
  return <AbsoluteFill className="pointer-events-none" />;
};
