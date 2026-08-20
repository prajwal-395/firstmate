/* eslint-disable @typescript-eslint/no-explicit-any */
import React from "react";
import { AbsoluteFill, useCurrentFrame } from "remotion";

import { loadBundledFonts, loadProjectFont } from "../../fonts";

/**
 * Opacity of one moment at a frame inside its own span.
 *
 * Written by hand rather than with `interpolate`, which requires a
 * strictly increasing input range and therefore threw
 * "inputRange must be strictly monotonically increasing but got
 * [0,0,30,30]" on any moment declared with no fade - a legitimate
 * declaration that crashed the whole render. Fades are clamped to fit
 * the moment so a long fade cannot invert the ramp either.
 */
export const momentOpacity = (
  localFrame: number,
  durationFrames: number,
  fadeInFrames: number,
  fadeOutFrames: number,
): number => {
  const fadeIn = Math.max(0, Math.min(fadeInFrames, durationFrames));
  const fadeOut = Math.max(0, Math.min(fadeOutFrames, durationFrames - fadeIn));
  let opacity = 1;
  if (fadeIn > 0 && localFrame < fadeIn) {
    opacity = localFrame / fadeIn;
  } else if (fadeOut > 0 && localFrame > durationFrames - fadeOut) {
    opacity = (durationFrames - localFrame) / fadeOut;
  }
  return Math.max(0, Math.min(1, opacity));
};

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
  /**
   * `staticFile()` path of the font file `fontFamily` is drawn from, when
   * the PROJECT carries its own typeface (`brand/<file>`, staged there by
   * prep_remotion). Absent means the family is one this repository
   * bundles or has accepted as a system font - see
   * library/tools/render_fonts.py.
   */
  fontFile?: string;
  fps: number;
  width: number;
  height: number;
  durationInFrames: number;
};

export const timedTextOverlaySchema = {} as any;

export const TimedTextOverlay: React.FC<TimedTextOverlayProps> = ({
  moments,
  fontFamily,
  fontFile,
  width,
  height,
}) => {
  const frame = useCurrentFrame();

  // BLOCKING, and throwing on a miss. Until this existed the composition
  // named a family and loaded nothing, so every card rendered in
  // Chromium's fallback sans - a valid picture of the right size, which
  // is the failure mode `src/fonts.ts` exists to end (P3.3).
  loadBundledFonts();
  if (fontFile) {
    loadProjectFont(fontFamily, fontFile);
  }

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

        const opacity = momentOpacity(
          frame - startFrame,
          durationFrames,
          fadeInFrames,
          fadeOutFrames,
        );

        return (
          <div
            key={i}
            style={{
              position: "absolute",
              left: x * width,
              top: y * height,
              transform: "translate(-50%, -50%)",
              // An absolutely positioned box with only `left` set is
              // available (containerWidth - left) px wide, so a moment at
              // x=0.5 wrapped at HALF the frame: "Through the 4th Wall"
              // broke onto two lines at 52px in a 1080px frame, with the
              // second line landing 60px lower than the declaration said.
              // Size the box to its content instead, and let the frame -
              // not the anchor point - be the only thing that can wrap it.
              width: "max-content",
              maxWidth: width,
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
