import {
  AbsoluteFill,
  useCurrentFrame,
  useVideoConfig,
  interpolate,
  spring,
  Easing,
} from "remotion";
import { z } from "zod";

/**
 * Zod schema for the MotionGraphics composition props.
 *
 * timelineProgressStart / timelineProgressEnd represent this clip's
 * position within the full timeline (0–1).  The progress bar interpolates
 * between these two values so it tracks the *overall* video progress,
 * not just the current clip.
 */
export const motionGraphicsSchema = z.object({
  title: z.string().optional(),
  subtitle: z.string().optional(),
  accentColor: z.string().default("#00D4FF"),
  showUpperThird: z.boolean().default(true),
  showProgress: z.boolean().default(true),
  showAccents: z.boolean().default(true),
  // Timeline-level progress (0–1) for the global progress bar
  timelineProgressStart: z.number().default(0),
  timelineProgressEnd: z.number().default(1),
  fps: z.number(),
  width: z.number(),
  height: z.number(),
  durationInFrames: z.number(),
});

export type MotionGraphicsProps = z.infer<typeof motionGraphicsSchema>;

// ═══════════════════════════════════════════════════════════════════════
//  Upper Third — title card / topic display
// ═══════════════════════════════════════════════════════════════════════

const UpperThird: React.FC<{
  title?: string;
  subtitle?: string;
  accentColor: string;
  entranceProgress: number;
  exitProgress: number;
  width: number;
  height: number;
  fps: number;
  frame: number;
}> = ({
  title,
  subtitle,
  accentColor,
  entranceProgress,
  exitProgress,
  width,
  height,
  fps,
  frame,
}) => {
  // Slide in from right with spring
  const slideIn = spring({
    frame,
    fps,
    config: { damping: 14, stiffness: 100, mass: 0.8 },
  });

  const barWidth = width * 0.5;
  const translateX = interpolate(
    slideIn * (1 - exitProgress),
    [0, 1],
    [barWidth + 60, 0]
  );
  const opacity = interpolate(exitProgress, [0, 1], [1, 0], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });

  // Accent line shimmer
  const shimmerOffset = interpolate(frame % 120, [0, 120], [-100, 200]);

  const titleSize = Math.round(height * 0.02);
  const subtitleSize = Math.round(height * 0.015);

  return (
    <div
      style={{
        position: "absolute",
        top: height * 0.06,
        right: width * 0.06,
        opacity,
        transform: `translateX(${translateX}px)`,
        willChange: "transform, opacity",
        display: "flex",
        flexDirection: "column",
        alignItems: "flex-end",
      }}
    >
      {/* Title text */}
      {title && (
        <div
          style={{
            fontFamily: "'Inter', sans-serif",
            fontWeight: 700,
            fontSize: titleSize,
            color: "#FFFFFF",
            letterSpacing: 0.8,
            marginBottom: subtitle ? 6 : 4,
            textShadow: "0 2px 12px rgba(0,0,0,0.5)",
            textAlign: "right",
          }}
        >
          {title}
        </div>
      )}

      {/* Subtitle text */}
      {subtitle && (
        <div
          style={{
            fontFamily: "'Inter', sans-serif",
            fontWeight: 400,
            fontSize: subtitleSize,
            color: "rgba(255, 255, 255, 0.65)",
            letterSpacing: 0.4,
            marginBottom: 6,
            textAlign: "right",
          }}
        >
          {subtitle}
        </div>
      )}

      {/* Gradient accent line beneath text */}
      <div
        style={{
          width: barWidth * 0.6,
          height: 2,
          borderRadius: 1,
          background: `linear-gradient(90deg, transparent, ${accentColor}80, ${accentColor})`,
          position: "relative",
          overflow: "hidden",
        }}
      >
        {/* Shimmer sweep */}
        <div
          style={{
            position: "absolute",
            top: 0,
            left: `${shimmerOffset}%`,
            width: "25%",
            height: "100%",
            background:
              "linear-gradient(90deg, transparent, rgba(255,255,255,0.5), transparent)",
          }}
        />
      </div>
    </div>
  );
};

// ═══════════════════════════════════════════════════════════════════════
//  Accent Shapes — decorative elements in upper area + edges
// ═══════════════════════════════════════════════════════════════════════

const AccentShapes: React.FC<{
  accentColor: string;
  entranceProgress: number;
  exitProgress: number;
  width: number;
  height: number;
  frame: number;
  fps: number;
}> = ({ accentColor, entranceProgress, exitProgress, width, height, frame, fps }) => {
  const opacity = Math.min(entranceProgress, 1 - exitProgress);

  const dotSize = Math.max(5, Math.round(width * 0.004));
  const cornerOffset = width * 0.04;

  // Pulsing
  const pulse = interpolate(
    Math.sin((frame / fps) * Math.PI * 2 * 0.8),
    [-1, 1],
    [0.4, 1]
  );

  // Rotating accent line
  const rotation = interpolate(frame, [0, 180], [0, 360], {
    extrapolateRight: "extend",
  });

  // Floating circle
  const floatY = interpolate(
    Math.sin((frame / fps) * Math.PI * 2 * 0.4),
    [-1, 1],
    [-6, 6]
  );

  const smallCircleSize = Math.max(10, Math.round(width * 0.01));

  // Breathing scale for corner elements
  const breathe = interpolate(
    Math.sin((frame / fps) * Math.PI * 2 * 0.6),
    [-1, 1],
    [0.85, 1.15]
  );

  return (
    <div style={{ opacity, willChange: "opacity" }}>
      {/* Top-left corner bracket */}
      <div
        style={{
          position: "absolute",
          top: cornerOffset,
          left: cornerOffset,
          width: 16,
          height: 16,
          borderTop: `2px solid ${accentColor}60`,
          borderLeft: `2px solid ${accentColor}60`,
          opacity: pulse * 0.7,
        }}
      />

      {/* Top-right corner bracket */}
      <div
        style={{
          position: "absolute",
          top: cornerOffset,
          right: cornerOffset,
          width: 16,
          height: 16,
          borderTop: `2px solid ${accentColor}60`,
          borderRight: `2px solid ${accentColor}60`,
          opacity: pulse * 0.7,
        }}
      />

      {/* Top center — small floating dot */}
      <div
        style={{
          position: "absolute",
          top: height * 0.04,
          left: "50%",
          width: dotSize,
          height: dotSize,
          borderRadius: "50%",
          backgroundColor: accentColor,
          opacity: pulse * 0.5,
          transform: `translateX(-50%) translateY(${floatY * 0.5}px)`,
          boxShadow: `0 0 ${dotSize * 3}px ${accentColor}40`,
        }}
      />

      {/* Right edge — rotating line element (upper area) */}
      <div
        style={{
          position: "absolute",
          top: height * 0.15,
          right: cornerOffset,
          width: 2,
          height: Math.round(width * 0.035),
          backgroundColor: `${accentColor}50`,
          transform: `rotate(${rotation * 0.03}deg)`,
          transformOrigin: "top center",
          borderRadius: 1,
        }}
      />

      {/* Left edge — floating circle (upper area) */}
      <div
        style={{
          position: "absolute",
          top: height * 0.2,
          left: cornerOffset * 1.5,
          width: smallCircleSize,
          height: smallCircleSize,
          borderRadius: "50%",
          border: `1.5px solid ${accentColor}40`,
          transform: `translateY(${floatY}px) scale(${breathe})`,
        }}
      />

      {/* Small plus/cross — upper right area */}
      <div
        style={{
          position: "absolute",
          top: height * 0.12,
          right: cornerOffset * 2.5,
          width: smallCircleSize * 0.8,
          height: smallCircleSize * 0.8,
          opacity: pulse * 0.5,
        }}
      >
        {/* Horizontal */}
        <div
          style={{
            position: "absolute",
            top: "50%",
            left: 0,
            right: 0,
            height: 1.5,
            backgroundColor: `${accentColor}50`,
            transform: "translateY(-50%)",
            borderRadius: 1,
          }}
        />
        {/* Vertical */}
        <div
          style={{
            position: "absolute",
            left: "50%",
            top: 0,
            bottom: 0,
            width: 1.5,
            backgroundColor: `${accentColor}50`,
            transform: "translateX(-50%)",
            borderRadius: 1,
          }}
        />
      </div>

      {/* Left edge — small diamond shape */}
      <div
        style={{
          position: "absolute",
          top: height * 0.3,
          left: cornerOffset,
          width: dotSize * 1.5,
          height: dotSize * 1.5,
          backgroundColor: `${accentColor}30`,
          transform: `rotate(45deg) scale(${breathe})`,
          opacity: pulse * 0.6,
        }}
      />
    </div>
  );
};

// ═══════════════════════════════════════════════════════════════════════
//  Progress Indicator — global timeline progress at very bottom
// ═══════════════════════════════════════════════════════════════════════

const ProgressIndicator: React.FC<{
  accentColor: string;
  progress: number;
  entranceProgress: number;
  exitProgress: number;
  width: number;
  height: number;
}> = ({ accentColor, progress, entranceProgress, exitProgress, width, height }) => {
  const opacity = Math.min(entranceProgress, 1 - exitProgress);
  const barHeight = 3;
  // Place at very bottom of frame — below the subtitle zone
  const barY = height - Math.round(height * 0.025);
  const margin = width * 0.06;
  const trackWidth = width - margin * 2;

  return (
    <div
      style={{
        position: "absolute",
        top: barY,
        left: margin,
        width: trackWidth,
        height: barHeight,
        opacity,
      }}
    >
      {/* Track background */}
      <div
        style={{
          width: "100%",
          height: "100%",
          backgroundColor: "rgba(255, 255, 255, 0.12)",
          borderRadius: barHeight / 2,
          overflow: "hidden",
        }}
      >
        {/* Fill */}
        <div
          style={{
            width: `${progress * 100}%`,
            height: "100%",
            background: `linear-gradient(90deg, ${accentColor}88, ${accentColor})`,
            borderRadius: barHeight / 2,
            boxShadow: `0 0 6px ${accentColor}60`,
          }}
        />
      </div>
      {/* Leading edge dot */}
      <div
        style={{
          position: "absolute",
          top: -2.5,
          left: `${progress * 100}%`,
          width: barHeight + 5,
          height: barHeight + 5,
          borderRadius: "50%",
          backgroundColor: accentColor,
          transform: "translateX(-50%)",
          boxShadow: `0 0 4px ${accentColor}`,
        }}
      />
    </div>
  );
};

// ═══════════════════════════════════════════════════════════════════════
//  Main Component
// ═══════════════════════════════════════════════════════════════════════

/**
 * MotionGraphics — Renders decorative motion graphics overlays on a
 * transparent background. Designed for compositing above video footage
 * in DaVinci Resolve (ProRes 4444 alpha).
 *
 * Layout:
 *   - Upper third: title card, accent shapes, decorative elements
 *   - Middle: clear zone (subtitle text lives on separate track)
 *   - Bottom edge: global timeline progress bar
 *
 * The progress bar uses timelineProgressStart/End to show progress
 * across the entire timeline, not just the individual clip.
 */
export const MotionGraphics: React.FC<MotionGraphicsProps> = ({
  title,
  subtitle,
  accentColor = "#00D4FF",
  showUpperThird = true,
  showProgress = true,
  showAccents = true,
  timelineProgressStart = 0,
  timelineProgressEnd = 1,
}) => {
  const frame = useCurrentFrame();
  const { durationInFrames, fps, width, height } = useVideoConfig();

  const entranceFrames = 15;
  const exitFrames = 15;

  // Entrance (0 → 1 over first 15 frames)
  const entranceProgress = interpolate(
    frame,
    [0, entranceFrames],
    [0, 1],
    {
      extrapolateLeft: "clamp",
      extrapolateRight: "clamp",
      easing: Easing.out(Easing.cubic),
    }
  );

  // Exit (0 → 1 over last 15 frames)
  const exitProgress = interpolate(
    frame,
    [durationInFrames - exitFrames, durationInFrames],
    [0, 1],
    {
      extrapolateLeft: "clamp",
      extrapolateRight: "clamp",
      easing: Easing.in(Easing.cubic),
    }
  );

  // Global timeline progress: lerp between start and end based on clip progress
  const clipProgress = frame / Math.max(durationInFrames - 1, 1);
  const globalProgress =
    timelineProgressStart +
    clipProgress * (timelineProgressEnd - timelineProgressStart);

  return (
    <AbsoluteFill>
      {/* Accent shapes — upper area and edges */}
      {showAccents && (
        <AccentShapes
          accentColor={accentColor}
          entranceProgress={entranceProgress}
          exitProgress={exitProgress}
          width={width}
          height={height}
          frame={frame}
          fps={fps}
        />
      )}

      {/* Upper third — title / topic */}
      {showUpperThird && (title || subtitle) && (
        <UpperThird
          title={title}
          subtitle={subtitle}
          accentColor={accentColor}
          entranceProgress={entranceProgress}
          exitProgress={exitProgress}
          width={width}
          height={height}
          fps={fps}
          frame={frame}
        />
      )}

      {/* Global progress indicator at very bottom */}
      {showProgress && (
        <ProgressIndicator
          accentColor={accentColor}
          progress={globalProgress}
          entranceProgress={entranceProgress}
          exitProgress={exitProgress}
          width={width}
          height={height}
        />
      )}
    </AbsoluteFill>
  );
};
