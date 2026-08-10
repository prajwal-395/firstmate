/* eslint-disable @typescript-eslint/no-explicit-any */
import React from "react";
import { AbsoluteFill } from "remotion";

export type LucieEndCardProps = {
  headline: string;
  tagline: string;
  websiteUrl: string;
  accentColor: string;
  bgColor: string;
  fps: number;
  width: number;
  height: number;
  durationInFrames: number;
};

export const lucieEndCardSchema = {} as any;

export const LucieEndCard: React.FC<LucieEndCardProps> = () => {
  return <AbsoluteFill className="pointer-events-none" />;
};
