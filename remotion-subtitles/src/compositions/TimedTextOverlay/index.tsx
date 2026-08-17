/* eslint-disable @typescript-eslint/no-explicit-any */
import React from "react";
import { AbsoluteFill, useCurrentFrame, interpolate } from "remotion";

export type TextMoment = {
  /** The text to display. */
  text: string;
  /** CSS colour. */
  color: string;
  /** Font size in pixels. */
  fontSize: number;
  /** Frame at which this moment appears. */
  startFrame: number;
  /** How many frames this moment is visible. */
  durationFrames: number;
  /** Horizontal position 0-1 (0.5 = center). */
  x: number;
  /** Vertical position 0-1 (0 = top, 1 = bottom). */
  y: number;
  /** Frames to fade in from transparent. */
  fadeInFrames: number;
  /** Frames to fade out to transparent. */
  fadeOutFrames: number;
  /** CSS font-weight (default 400). */
  fontWeight?: number;
  /** CSS text-align (default "center"). */
  textAlign?: "left" | "center" | "right";
  /** CSS text-shadow (default drop shadow). */
  textShadow?: string;
};

export type TimedTextOverlayProps = {
  moments: TextMoment[];
  fontFamily: string;
  fps: number;
  width: number;
  height: number;
  durationInFrames: number;
};

export const timedTextOverlaySchema = {} as any;

export const TimedTextOverlay: React.FC<TimedTextOverlayProps> = ({
  moments,
  fontFamily,
  width,
  height,
}) => {
  const frame = useCurrentFrame();

  return (
    <AbsoluteFill
      className="pointer-events-none"
      style={{ fontFamily }}
    >
      {moments.map((moment, i) => {
        const {
          text,
          color,
          fontSize,
          startFrame,
          durationFrames,
          x,
          y,
          fadeInFrames,
          fadeOutFrames,
          fontWeight,
          textAlign,
          textShadow,
        } = moment;
        const endFrame = startFrame + durationFrames;

        // Not yet visible or already past
        if (frame < startFrame || frame >= endFrame) return null;

        const opacity = interpolate(
          frame,
          [
            startFrame,
            startFrame + fadeInFrames,
            endFrame - fadeOutFrames,
            endFrame,
          ],
          [0, 1, 1, 0],
          { extrapolateLeft: "clamp", extrapolateRight: "clamp" },
        );

        return (
          <div
            key={i}
            style={{
              position: "absolute",
              left: x * width,
              top: y * height,
              transform: "translate(-50%, -50%)",
              fontSize,
              fontWeight: fontWeight ?? 400,
              color,
              textAlign: textAlign ?? "center",
              textShadow:
                textShadow ?? "0px 4px 12px rgba(0,0,0,0.6)",
              opacity,
              whiteSpace: "pre-line",
            }}
          >
            {text}
          </div>
        );
      })}
    </AbsoluteFill>
  );
};
