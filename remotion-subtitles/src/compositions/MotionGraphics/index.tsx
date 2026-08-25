/* eslint-disable @typescript-eslint/no-explicit-any */
import React from "react";
import { AbsoluteFill, useCurrentFrame, interpolate, spring } from "remotion";
import { loadBundledFonts } from "../../fonts";

// Same reason as SubtitleOverlay: this composition sets
// fontFamily "Montserrat" and must not race the font load.
loadBundledFonts();

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
  /**
   * The platform's keep-clear insets in pixels, from
   * library/tools/safe_area.py via generate_motion_props. Every element
   * this composition draws is decoration at an edge, so every one of
   * them is positioned from here. The corner accents used to sit at a
   * literal 60px on all four sides - 5.6% of a 1080px width, inside the
   * like/comment/share rail.
   */
  safeArea: { top: number; right: number; bottom: number; left: number };
  durationInFrames: number;
};

export const motionGraphicsSchema = {} as any;

export const MotionGraphics: React.FC<MotionGraphicsProps> = ({
  title,
  subtitle,
  accentColor,
  showUpperThird,
  showProgress,
  showAccents,
  timelineProgressStart,
  timelineProgressEnd,
  fps,
  safeArea,
  durationInFrames,
}) => {
  const frame = useCurrentFrame();

  // No default. The insets come from library/tools/safe_area.py, and
  // inventing one here is precisely the "three more hardcoded margins"
  // the captain's ruling of 2026-08-25 forbade.
  if (!safeArea) {
    throw new Error(
      "MotionGraphics props carry no safeArea. generate_motion_props " +
        "resolves it from library/tools/safe_area.py. Refusing to " +
        "substitute a margin: an accent drawn by a literal sits under " +
        "the platform's own interface with nothing to notice.",
    );
  }

  // Upper third animation: fade in early, stay, fade out
  const upperThirdOpacity = interpolate(
    frame,
    [0, 15, 45, 60],
    [0, 1, 1, 0],
    { extrapolateLeft: "clamp", extrapolateRight: "clamp" }
  );

  const upperThirdSlide = spring({
    frame,
    fps,
    config: { damping: 12 },
  });

  // Progress bar animation across the duration
  const progressWidth = interpolate(
    frame,
    [0, durationInFrames - 1],
    [timelineProgressStart * 100, timelineProgressEnd * 100],
    { extrapolateRight: "clamp" }
  );

  // Corner accents animation
  const accentsOpacity = interpolate(frame, [0, 20], [0, 1], {
    extrapolateRight: "clamp",
  });

  return (
    <AbsoluteFill className="pointer-events-none" style={{ fontFamily: "Montserrat, sans-serif" }}>
      {showUpperThird && (
        <div
          style={{
            position: "absolute",
            top: safeArea.top,
            left: safeArea.left,
            opacity: upperThirdOpacity,
            transform: `translateY(${interpolate(upperThirdSlide, [0, 1], [-30, 0])}px)`,
            display: "flex",
            flexDirection: "column",
            gap: "12px",
          }}
        >
          <div
            style={{
              fontSize: "56px",
              fontWeight: 900,
              color: "white",
              textShadow: "0px 4px 12px rgba(0,0,0,0.6)",
              textTransform: "uppercase",
              letterSpacing: "3px",
              lineHeight: 1.1,
            }}
          >
            {title}
          </div>
          <div
            style={{
              fontSize: "36px",
              fontWeight: 700,
              color: accentColor,
              textShadow: "0px 4px 12px rgba(0,0,0,0.6)",
              lineHeight: 1.1,
            }}
          >
            {subtitle}
          </div>
        </div>
      )}

      {showAccents && (
        <>
          <div
            style={{
              position: "absolute",
              top: safeArea.top,
              left: safeArea.left,
              width: 80,
              height: 80,
              borderTop: `6px solid ${accentColor}`,
              borderLeft: `6px solid ${accentColor}`,
              opacity: accentsOpacity,
            }}
          />
          <div
            style={{
              position: "absolute",
              top: safeArea.top,
              right: safeArea.right,
              width: 80,
              height: 80,
              borderTop: `6px solid ${accentColor}`,
              borderRight: `6px solid ${accentColor}`,
              opacity: accentsOpacity,
            }}
          />
          <div
            style={{
              position: "absolute",
              bottom: safeArea.bottom,
              left: safeArea.left,
              width: 80,
              height: 80,
              borderBottom: `6px solid ${accentColor}`,
              borderLeft: `6px solid ${accentColor}`,
              opacity: accentsOpacity,
            }}
          />
          <div
            style={{
              position: "absolute",
              bottom: safeArea.bottom,
              right: safeArea.right,
              width: 80,
              height: 80,
              borderBottom: `6px solid ${accentColor}`,
              borderRight: `6px solid ${accentColor}`,
              opacity: accentsOpacity,
            }}
          />
        </>
      )}

      {showProgress && (
        <div
          style={{
            position: "absolute",
            bottom: 0,
            left: 0,
            width: "100%",
            height: "12px",
            backgroundColor: "rgba(255, 255, 255, 0.15)",
          }}
        >
          <div
            style={{
              width: `${progressWidth}%`,
              height: "100%",
              backgroundColor: accentColor,
              boxShadow: `0 0 16px ${accentColor}`,
            }}
          />
        </div>
      )}
    </AbsoluteFill>
  );
};
