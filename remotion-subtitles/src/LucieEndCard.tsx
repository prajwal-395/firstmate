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

export const lucieEndCardSchema = z.object({
  fps: z.number(),
  width: z.number(),
  height: z.number(),
  durationInFrames: z.number(),
  /** Main headline — from the Lucie Content website */
  headline: z
    .string()
    .default("We power what AI knows about you."),
  /** Tagline below the headline */
  tagline: z
    .string()
    .default(
      "Strategic storytelling built for human trust and AI visibility."
    ),
  /** Website URL displayed at the bottom */
  websiteUrl: z.string().default("luciecontent.com"),
  /** Lucie brand accent color (amber/gold) */
  accentColor: z.string().default("#FFAA4D"),
  /** Lucie brand dark navy background */
  bgColor: z.string().default("#253746"),
});

export type LucieEndCardProps = z.infer<typeof lucieEndCardSchema>;

// ═══════════════════════════════════════════════════════════════════════
//  Animated Logo
// ═══════════════════════════════════════════════════════════════════════

const AnimatedLogo: React.FC<{
  frame: number;
  fps: number;
  width: number;
  height: number;
  accentColor: string;
}> = ({ frame, fps, width, height, accentColor }) => {
  // Spring-based scale entrance
  const scaleSpring = spring({
    frame,
    fps,
    config: { damping: 12, stiffness: 80, mass: 0.8 },
  });

  // Subtle float
  const floatY = interpolate(
    Math.sin((frame / fps) * Math.PI * 2 * 0.3),
    [-1, 1],
    [-3, 3]
  );

  // Glow pulse
  const glowPulse = interpolate(
    Math.sin((frame / fps) * Math.PI * 2 * 0.5),
    [-1, 1],
    [0.3, 0.8]
  );

  const logoWidth = width * 0.35;

  return (
    <div
      style={{
        display: "flex",
        justifyContent: "center",
        alignItems: "center",
        transform: `scale(${scaleSpring}) translateY(${floatY}px)`,
        willChange: "transform",
        filter: `drop-shadow(0 0 ${20 * glowPulse}px ${accentColor}40)`,
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
  );
};

// ═══════════════════════════════════════════════════════════════════════
//  Animated Headline — word-by-word reveal
// ═══════════════════════════════════════════════════════════════════════

const AnimatedHeadline: React.FC<{
  text: string;
  frame: number;
  fps: number;
  width: number;
  height: number;
  accentColor: string;
  startFrame: number;
}> = ({ text, frame, fps, width, height, accentColor, startFrame }) => {
  const words = text.split(" ");
  const framesPerWord = 4;

  const fontSize = Math.round(height * 0.032);

  return (
    <div
      style={{
        display: "flex",
        flexWrap: "wrap",
        justifyContent: "center",
        gap: "8px 12px",
        maxWidth: width * 0.85,
        lineHeight: 1.3,
      }}
    >
      {words.map((word, i) => {
        const wordStart = startFrame + i * framesPerWord;
        const localFrame = frame - wordStart;

        const wordSpring = spring({
          frame: Math.max(0, localFrame),
          fps,
          config: { damping: 14, stiffness: 120, mass: 0.6 },
        });

        const opacity = interpolate(wordSpring, [0, 1], [0, 1], {
          extrapolateRight: "clamp",
        });

        const translateY = interpolate(wordSpring, [0, 1], [20, 0], {
          extrapolateRight: "clamp",
        });

        // Highlight "AI" in accent color
        const isAccent = word.replace(/[.,!?]/g, "").toUpperCase() === "AI";

        return (
          <span
            key={i}
            style={{
              fontFamily: "'Proxima Nova', 'Inter', sans-serif",
              fontWeight: 700,
              fontSize,
              color: isAccent ? accentColor : "#FFFFFF",
              opacity,
              transform: `translateY(${translateY}px)`,
              display: "inline-block",
              willChange: "transform, opacity",
              textShadow: isAccent
                ? `0 0 20px ${accentColor}60`
                : "0 2px 8px rgba(0,0,0,0.3)",
            }}
          >
            {word}
          </span>
        );
      })}
    </div>
  );
};

// ═══════════════════════════════════════════════════════════════════════
//  Animated Tagline — fade in as a block
// ═══════════════════════════════════════════════════════════════════════

const AnimatedTagline: React.FC<{
  text: string;
  frame: number;
  fps: number;
  width: number;
  height: number;
  accentColor: string;
  startFrame: number;
}> = ({ text, frame, fps, width, height, accentColor, startFrame }) => {
  const localFrame = frame - startFrame;

  const fadeIn = interpolate(localFrame, [0, 20], [0, 1], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
    easing: Easing.out(Easing.cubic),
  });

  const slideUp = interpolate(localFrame, [0, 20], [15, 0], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
    easing: Easing.out(Easing.cubic),
  });

  const fontSize = Math.round(height * 0.018);

  // Highlight "AI visibility." in accent color
  const parts = text.split(/(AI visibility\.)/);

  return (
    <div
      style={{
        fontFamily: "'Lato', 'Inter', sans-serif",
        fontWeight: 400,
        fontSize,
        color: "rgba(255, 255, 255, 0.7)",
        textAlign: "center",
        maxWidth: width * 0.75,
        lineHeight: 1.5,
        opacity: fadeIn,
        transform: `translateY(${slideUp}px)`,
        willChange: "transform, opacity",
      }}
    >
      {parts.map((part, i) =>
        part === "AI visibility." ? (
          <span key={i} style={{ color: accentColor, fontWeight: 600 }}>
            {part}
          </span>
        ) : (
          <span key={i}>{part}</span>
        )
      )}
    </div>
  );
};

// ═══════════════════════════════════════════════════════════════════════
//  Accent Line — shimmering divider
// ═══════════════════════════════════════════════════════════════════════

const AccentLine: React.FC<{
  frame: number;
  fps: number;
  width: number;
  accentColor: string;
  startFrame: number;
}> = ({ frame, fps, width, accentColor, startFrame }) => {
  const localFrame = frame - startFrame;

  const lineWidth = spring({
    frame: Math.max(0, localFrame),
    fps,
    config: { damping: 18, stiffness: 60, mass: 1 },
  });

  const shimmerOffset = interpolate(frame % 90, [0, 90], [-50, 150]);

  const maxWidth = width * 0.35;

  return (
    <div
      style={{
        width: maxWidth * lineWidth,
        height: 2,
        borderRadius: 1,
        background: `linear-gradient(90deg, transparent, ${accentColor}80, ${accentColor}, ${accentColor}80, transparent)`,
        position: "relative",
        overflow: "hidden",
      }}
    >
      <div
        style={{
          position: "absolute",
          top: 0,
          left: `${shimmerOffset}%`,
          width: "30%",
          height: "100%",
          background:
            "linear-gradient(90deg, transparent, rgba(255,255,255,0.6), transparent)",
        }}
      />
    </div>
  );
};

// ═══════════════════════════════════════════════════════════════════════
//  Website URL — fade in with underline
// ═══════════════════════════════════════════════════════════════════════

const WebsiteUrl: React.FC<{
  url: string;
  frame: number;
  fps: number;
  height: number;
  accentColor: string;
  startFrame: number;
}> = ({ url, frame, fps, height, accentColor, startFrame }) => {
  const localFrame = frame - startFrame;

  const fadeIn = interpolate(localFrame, [0, 25], [0, 1], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
    easing: Easing.out(Easing.cubic),
  });

  const slideUp = interpolate(localFrame, [0, 25], [20, 0], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
    easing: Easing.out(Easing.cubic),
  });

  const fontSize = Math.round(height * 0.022);

  // Subtle pulse on the URL
  const pulse = interpolate(
    Math.sin((frame / fps) * Math.PI * 2 * 0.6),
    [-1, 1],
    [0.85, 1]
  );

  return (
    <div
      style={{
        fontFamily: "'Lato', 'Inter', sans-serif",
        fontWeight: 600,
        fontSize,
        color: accentColor,
        letterSpacing: 2,
        textTransform: "lowercase",
        opacity: fadeIn * pulse,
        transform: `translateY(${slideUp}px)`,
        willChange: "transform, opacity",
        textShadow: `0 0 15px ${accentColor}40`,
      }}
    >
      {url}
    </div>
  );
};

// ═══════════════════════════════════════════════════════════════════════
//  Background Particles — floating ambient dots
// ═══════════════════════════════════════════════════════════════════════

const BackgroundParticles: React.FC<{
  frame: number;
  fps: number;
  width: number;
  height: number;
  accentColor: string;
}> = ({ frame, fps, width, height, accentColor }) => {
  // Use a deterministic set of particles
  const particles = Array.from({ length: 12 }, (_, i) => {
    const seed = (i * 137.508) % 360; // Golden angle distribution
    const x = ((seed / 360) * width * 0.8) + width * 0.1;
    const baseY = ((i * 73) % height);
    const size = 2 + (i % 3) * 1.5;
    const speed = 0.15 + (i % 5) * 0.08;
    const phase = (i * 2.39) % (Math.PI * 2);

    return { x, baseY, size, speed, phase };
  });

  return (
    <>
      {particles.map((p, i) => {
        const y = p.baseY + Math.sin((frame / fps) * Math.PI * 2 * p.speed + p.phase) * 15;
        const opacity = interpolate(
          Math.sin((frame / fps) * Math.PI * 2 * 0.3 + p.phase),
          [-1, 1],
          [0.1, 0.35]
        );

        // Fade in during first 30 frames
        const entranceOpacity = interpolate(frame, [0, 30], [0, 1], {
          extrapolateLeft: "clamp",
          extrapolateRight: "clamp",
        });

        return (
          <div
            key={i}
            style={{
              position: "absolute",
              left: p.x,
              top: y,
              width: p.size,
              height: p.size,
              borderRadius: "50%",
              backgroundColor: i % 3 === 0 ? accentColor : "rgba(255,255,255,0.5)",
              opacity: opacity * entranceOpacity,
              boxShadow:
                i % 3 === 0
                  ? `0 0 ${p.size * 3}px ${accentColor}30`
                  : "none",
            }}
          />
        );
      })}
    </>
  );
};

// ═══════════════════════════════════════════════════════════════════════
//  Corner Brackets — Lucie-style decorative frames
// ═══════════════════════════════════════════════════════════════════════

const CornerBrackets: React.FC<{
  frame: number;
  fps: number;
  width: number;
  height: number;
  accentColor: string;
}> = ({ frame, fps, width, height, accentColor }) => {
  const entranceSpring = spring({
    frame,
    fps,
    config: { damping: 14, stiffness: 80, mass: 1 },
    delay: 10,
  });

  const pulse = interpolate(
    Math.sin((frame / fps) * Math.PI * 2 * 0.5),
    [-1, 1],
    [0.3, 0.6]
  );

  const margin = width * 0.08;
  const size = 24;
  const borderWidth = 2;

  const corners = [
    { top: margin, left: margin, borderTop: true, borderLeft: true },
    { top: margin, right: margin, borderTop: true, borderRight: true },
    {
      bottom: margin,
      left: margin,
      borderBottom: true,
      borderLeft: true,
    },
    {
      bottom: margin,
      right: margin,
      borderBottom: true,
      borderRight: true,
    },
  ];

  return (
    <>
      {corners.map((c, i) => {
        const style: React.CSSProperties = {
          position: "absolute",
          width: size,
          height: size,
          opacity: pulse * entranceSpring,
          ...(c.top !== undefined && { top: c.top }),
          ...(c.left !== undefined && { left: c.left }),
          ...(c.bottom !== undefined && { bottom: c.bottom }),
          ...(c.right !== undefined && { right: c.right }),
          ...(c.borderTop && {
            borderTop: `${borderWidth}px solid ${accentColor}60`,
          }),
          ...(c.borderLeft && {
            borderLeft: `${borderWidth}px solid ${accentColor}60`,
          }),
          ...(c.borderBottom && {
            borderBottom: `${borderWidth}px solid ${accentColor}60`,
          }),
          ...(c.borderRight && {
            borderRight: `${borderWidth}px solid ${accentColor}60`,
          }),
        };

        return <div key={i} style={style} />;
      })}
    </>
  );
};

// ═══════════════════════════════════════════════════════════════════════
//  Main End Card Component
// ═══════════════════════════════════════════════════════════════════════

export const LucieEndCard: React.FC<LucieEndCardProps> = ({
  headline = "We power what AI knows about you.",
  tagline = "Strategic storytelling built for human trust and AI visibility.",
  websiteUrl = "luciecontent.com",
  accentColor = "#FFAA4D",
  bgColor = "#253746",
}) => {
  const frame = useCurrentFrame();
  const { durationInFrames, fps, width, height } = useVideoConfig();

  // Timing (at 30fps)
  const logoStart = 0;
  const headlineStart = 20;
  const lineStart = headlineStart + 35;
  const taglineStart = lineStart + 10;
  const urlStart = taglineStart + 20;

  // Exit fade (last 20 frames)
  const exitFade = interpolate(
    frame,
    [durationInFrames - 20, durationInFrames],
    [1, 0],
    {
      extrapolateLeft: "clamp",
      extrapolateRight: "clamp",
      easing: Easing.in(Easing.cubic),
    }
  );

  return (
    <AbsoluteFill
      style={{
        backgroundColor: bgColor,
        opacity: exitFade,
      }}
    >
      {/* Subtle radial gradient overlay for depth */}
      <div
        style={{
          position: "absolute",
          inset: 0,
          background: `radial-gradient(ellipse at 50% 40%, ${accentColor}08 0%, transparent 70%)`,
        }}
      />

      {/* Background particles */}
      <BackgroundParticles
        frame={frame}
        fps={fps}
        width={width}
        height={height}
        accentColor={accentColor}
      />

      {/* Corner brackets */}
      <CornerBrackets
        frame={frame}
        fps={fps}
        width={width}
        height={height}
        accentColor={accentColor}
      />

      {/* Main content — centered vertically */}
      <div
        style={{
          position: "absolute",
          inset: 0,
          display: "flex",
          flexDirection: "column",
          alignItems: "center",
          justifyContent: "center",
          gap: height * 0.03,
          padding: `0 ${width * 0.1}px`,
        }}
      >
        {/* Logo — appears first, at the top */}
        {frame >= logoStart && (
          <AnimatedLogo
            frame={frame - logoStart}
            fps={fps}
            width={width}
            height={height}
            accentColor={accentColor}
          />
        )}

        {/* Spacer between logo and headline */}
        <div style={{ height: height * 0.01 }} />

        {/* Headline — below the logo */}
        <AnimatedHeadline
          text={headline}
          frame={frame}
          fps={fps}
          width={width}
          height={height}
          accentColor={accentColor}
          startFrame={headlineStart}
        />

        {/* Accent line divider */}
        <AccentLine
          frame={frame}
          fps={fps}
          width={width}
          accentColor={accentColor}
          startFrame={lineStart}
        />

        {/* Tagline */}
        <AnimatedTagline
          text={tagline}
          frame={frame}
          fps={fps}
          width={width}
          height={height}
          accentColor={accentColor}
          startFrame={taglineStart}
        />

        {/* Spacer before URL */}
        <div style={{ height: height * 0.02 }} />

        {/* Website URL */}
        <WebsiteUrl
          url={websiteUrl}
          frame={frame}
          fps={fps}
          height={height}
          accentColor={accentColor}
          startFrame={urlStart}
        />
      </div>
    </AbsoluteFill>
  );
};

