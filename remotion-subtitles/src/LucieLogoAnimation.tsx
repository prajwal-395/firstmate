import {
  AbsoluteFill,
  useCurrentFrame,
  useVideoConfig,
  interpolate,
  spring,
  Easing,
  Img,
  staticFile,
} from "remotion";
import { z } from "zod";

// ═══════════════════════════════════════════════════════════════════════
//  Schema
// ═══════════════════════════════════════════════════════════════════════

export const lucieLogoAnimationSchema = z.object({
  fps: z.number(),
  width: z.number(),
  height: z.number(),
  durationInFrames: z.number(),
  /** Lucie brand accent color (amber/gold) */
  accentColor: z.string().default("#FFAA4D"),
  /** Style: "full" shows logo with glow and accents; "minimal" is just the logo */
  style: z.enum(["full", "minimal"]).default("full"),
});

export type LucieLogoAnimationProps = z.infer<
  typeof lucieLogoAnimationSchema
>;

// ═══════════════════════════════════════════════════════════════════════
//  Glow Ring — pulsing ring behind the logo
// ═══════════════════════════════════════════════════════════════════════

const GlowRing: React.FC<{
  frame: number;
  fps: number;
  accentColor: string;
  size: number;
  delay: number;
  opacity: number;
}> = ({ frame, fps, accentColor, size, delay, opacity: maxOpacity }) => {
  const entrance = spring({
    frame,
    fps,
    config: { damping: 15, stiffness: 60, mass: 1.2 },
    delay,
  });

  const pulse = interpolate(
    Math.sin((frame / fps) * Math.PI * 2 * 0.4),
    [-1, 1],
    [0.6, 1]
  );

  const breathe = interpolate(
    Math.sin((frame / fps) * Math.PI * 2 * 0.25),
    [-1, 1],
    [0.95, 1.05]
  );

  return (
    <div
      style={{
        position: "absolute",
        width: size,
        height: size,
        borderRadius: "50%",
        border: `1.5px solid ${accentColor}${Math.round(maxOpacity * pulse * 255 * 0.4)
          .toString(16)
          .padStart(2, "0")}`,
        opacity: entrance * maxOpacity * pulse,
        transform: `scale(${entrance * breathe})`,
        boxShadow: `0 0 ${size * 0.15}px ${accentColor}${Math.round(
          maxOpacity * 0.2 * 255
        )
          .toString(16)
          .padStart(2, "0")}`,
      }}
    />
  );
};

// ═══════════════════════════════════════════════════════════════════════
//  Accent Dots — small orbiting/floating dots around the logo
// ═══════════════════════════════════════════════════════════════════════

const AccentDots: React.FC<{
  frame: number;
  fps: number;
  accentColor: string;
  logoSize: number;
}> = ({ frame, fps, accentColor, logoSize }) => {
  const numDots = 6;
  const radius = logoSize * 0.7;

  return (
    <>
      {Array.from({ length: numDots }, (_, i) => {
        const baseAngle = (i / numDots) * Math.PI * 2;
        // Slow rotation
        const angle =
          baseAngle + (frame / fps) * Math.PI * 2 * 0.08;
        const x = Math.cos(angle) * radius;
        const y = Math.sin(angle) * radius;

        const dotSize = 3 + (i % 3);

        const entrance = spring({
          frame,
          fps,
          config: { damping: 12, stiffness: 80, mass: 0.8 },
          delay: 15 + i * 3,
        });

        const pulse = interpolate(
          Math.sin((frame / fps) * Math.PI * 2 * 0.5 + i * 1.2),
          [-1, 1],
          [0.3, 0.8]
        );

        return (
          <div
            key={i}
            style={{
              position: "absolute",
              width: dotSize,
              height: dotSize,
              borderRadius: "50%",
              backgroundColor:
                i % 2 === 0 ? accentColor : "rgba(255,255,255,0.6)",
              transform: `translate(${x}px, ${y}px)`,
              opacity: entrance * pulse,
              boxShadow:
                i % 2 === 0
                  ? `0 0 ${dotSize * 3}px ${accentColor}40`
                  : "none",
            }}
          />
        );
      })}
    </>
  );
};

// ═══════════════════════════════════════════════════════════════════════
//  Shimmer Line — horizontal accent sweep beneath the logo
// ═══════════════════════════════════════════════════════════════════════

const ShimmerLine: React.FC<{
  frame: number;
  fps: number;
  accentColor: string;
  width: number;
  startFrame: number;
}> = ({ frame, fps, accentColor, width: lineWidth, startFrame }) => {
  const localFrame = frame - startFrame;

  const lineEntrance = spring({
    frame: Math.max(0, localFrame),
    fps,
    config: { damping: 18, stiffness: 50, mass: 1.2 },
  });

  const shimmerOffset = interpolate(frame % 120, [0, 120], [-50, 150]);

  return (
    <div
      style={{
        width: lineWidth * lineEntrance,
        height: 2,
        borderRadius: 1,
        background: `linear-gradient(90deg, transparent, ${accentColor}60, ${accentColor}, ${accentColor}60, transparent)`,
        position: "relative",
        overflow: "hidden",
        marginTop: 12,
      }}
    >
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
  );
};

// ═══════════════════════════════════════════════════════════════════════
//  Main Component — Standalone Lucie Logo Animation
// ═══════════════════════════════════════════════════════════════════════

/**
 * LucieLogoAnimation — A reusable animated Lucie Content logo overlay.
 *
 * Renders on a transparent background (ProRes 4444 alpha) so it can be
 * placed anywhere on the timeline in DaVinci Resolve.
 *
 * Features:
 *   - Spring-based logo entrance with subtle float
 *   - Pulsing glow rings behind the logo
 *   - Orbiting accent dots
 *   - Shimmer accent line beneath
 *   - Smooth entrance and exit animations
 */
export const LucieLogoAnimation: React.FC<LucieLogoAnimationProps> = ({
  accentColor = "#FFAA4D",
  style: animStyle = "full",
}) => {
  const frame = useCurrentFrame();
  const { durationInFrames, fps, width, height } = useVideoConfig();

  // ── Logo entrance ──
  const logoScale = spring({
    frame,
    fps,
    config: { damping: 12, stiffness: 80, mass: 0.8 },
  });

  // Subtle continuous float
  const floatY = interpolate(
    Math.sin((frame / fps) * Math.PI * 2 * 0.3),
    [-1, 1],
    [-4, 4]
  );

  // Glow pulse
  const glowIntensity = interpolate(
    Math.sin((frame / fps) * Math.PI * 2 * 0.5),
    [-1, 1],
    [0.2, 0.7]
  );

  // Exit fade & scale (last 15 frames)
  const exitProgress = interpolate(
    frame,
    [durationInFrames - 15, durationInFrames],
    [0, 1],
    { extrapolateLeft: "clamp", extrapolateRight: "clamp" }
  );

  const exitScale = interpolate(exitProgress, [0, 1], [1, 0.85]);
  const exitOpacity = interpolate(exitProgress, [0, 1], [1, 0]);

  // Logo sizing — positioned in the upper portion of frame for lower-third style
  const logoWidth = width * 0.35;
  const logoContainerSize = logoWidth * 1.6; // Space for glow rings

  // Entrance opacity (first 5 frames)
  const entranceOpacity = interpolate(frame, [0, 5], [0, 1], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });

  return (
    <AbsoluteFill>
      {/* Center the logo assembly */}
      <div
        style={{
          position: "absolute",
          inset: 0,
          display: "flex",
          flexDirection: "column",
          alignItems: "center",
          justifyContent: "center",
          opacity: exitOpacity * entranceOpacity,
          transform: `scale(${exitScale})`,
          willChange: "transform, opacity",
        }}
      >
        {/* Logo container with relative positioning for rings/dots */}
        <div
          style={{
            position: "relative",
            width: logoContainerSize,
            height: logoContainerSize * 0.35,
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
          }}
        >
          {/* Glow rings (full style only) */}
          {animStyle === "full" && (
            <>
              <GlowRing
                frame={frame}
                fps={fps}
                accentColor={accentColor}
                size={logoContainerSize * 0.85}
                delay={5}
                opacity={0.3}
              />
              <GlowRing
                frame={frame}
                fps={fps}
                accentColor={accentColor}
                size={logoContainerSize * 1.05}
                delay={10}
                opacity={0.15}
              />
            </>
          )}

          {/* Accent dots (full style only) */}
          {animStyle === "full" && (
            <AccentDots
              frame={frame}
              fps={fps}
              accentColor={accentColor}
              logoSize={logoContainerSize * 0.4}
            />
          )}

          {/* The logo image */}
          <div
            style={{
              transform: `scale(${logoScale}) translateY(${floatY}px)`,
              willChange: "transform",
              filter: `drop-shadow(0 0 ${
                15 * glowIntensity
              }px ${accentColor}50)`,
              zIndex: 1,
            }}
          >
            <Img
              src={staticFile("lucie-logo.png")}
              style={{
                width: logoWidth,
                height: "auto",
              }}
            />
          </div>
        </div>

        {/* Shimmer line beneath logo (full style only) */}
        {animStyle === "full" && (
          <ShimmerLine
            frame={frame}
            fps={fps}
            accentColor={accentColor}
            width={logoWidth * 0.7}
            startFrame={20}
          />
        )}
      </div>
    </AbsoluteFill>
  );
};
