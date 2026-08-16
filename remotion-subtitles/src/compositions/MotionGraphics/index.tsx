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
  durationInFrames,
}) => {
  const frame = useCurrentFrame();

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
            top: 150,
            left: 100,
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
              top: 60,
              left: 60,
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
              top: 60,
              right: 60,
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
              bottom: 60,
              left: 60,
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
              bottom: 60,
              right: 60,
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
