import {
  AbsoluteFill,
  useCurrentFrame,
  useVideoConfig,
  interpolate,
  spring,
  Easing,
  Sequence,
} from "remotion";
import { z } from "zod";

/**
 * 4th Wall Motion Graphics — Night card + closing ritual text overlays.
 *
 * Designed for "Through the 4th Wall" series:
 *   - Night card: "Night 1 — Through the 4th Wall" in Faded Brass
 *   - Closing: "It's 2:16." in Ice Blue, then "Day 1. Attack the day tomorrow." in Faded Brass
 *   - "1 / 100" counter overlay during declaration section
 */

export const fourthWallOverlaySchema = z.object({
  nightCardText: z.string(),
  nightCardColor: z.string(),
  nightCardStartFrame: z.number(),
  nightCardDurationFrames: z.number(),
  closingLine1Text: z.string(),
  closingLine1Color: z.string(),
  closingLine1StartFrame: z.number(),
  closingLine2Text: z.string(),
  closingLine2Color: z.string(),
  closingLine2StartFrame: z.number(),
  counterText: z.string().optional(),
  counterColor: z.string().optional(),
  counterStartFrame: z.number().optional(),
  counterDurationFrames: z.number().optional(),
  fontFamily: z.string(),
  fps: z.number(),
  width: z.number(),
  height: z.number(),
  durationInFrames: z.number(),
});

export type FourthWallOverlayProps = z.infer<typeof fourthWallOverlaySchema>;

/**
 * Animated text with fade-in from below + optional scale pulse.
 */
const AnimatedText: React.FC<{
  text: string;
  color: string;
  fontSize: number;
  startFrame: number;
  durationFrames: number;
  fontFamily: string;
  letterSpacing?: number;
  centered?: boolean;
}> = ({ text, color, fontSize, startFrame, durationFrames, fontFamily, letterSpacing = 0, centered = true }) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();

  const localFrame = frame - startFrame;

  if (localFrame < 0 || localFrame >= durationFrames) {
    return null;
  }

  // Entrance: spring from below
  const entranceSpring = spring({
    frame: localFrame,
    fps,
    config: { damping: 16, stiffness: 120, mass: 0.5 },
  });

  const translateY = interpolate(entranceSpring, [0, 1], [30, 0]);
  const opacity = entranceSpring;

  // Exit: fade out in last 10 frames
  const exitFrames = 10;
  const exitOpacity = interpolate(
    localFrame,
    [durationFrames - exitFrames, durationFrames],
    [1, 0],
    { extrapolateLeft: "clamp", extrapolateRight: "clamp", easing: Easing.in(Easing.cubic) }
  );

  const finalOpacity = Math.min(opacity, exitOpacity);

  return (
    <div
      style={{
        position: "absolute",
        left: 0,
        right: 0,
        display: "flex",
        justifyContent: "center",
        alignItems: centered ? "center" : "flex-start",
        top: "50%",
        transform: `translateY(calc(-50% + ${translateY}px))`,
        opacity: finalOpacity,
        fontFamily,
        fontSize,
        fontWeight: 400,
        color,
        letterSpacing,
        textAlign: "center",
        padding: "0 10%",
        textShadow: `0px 0px 20px ${color}40, 0px 2px 4px rgba(0,0,0,0.5)`,
        willChange: "transform, opacity",
      }}
    >
      {text}
    </div>
  );
};

/**
 * Counter overlay ("1 / 100") — appears with a scale punch.
 */
const CounterOverlay: React.FC<{
  text: string;
  color: string;
  startFrame: number;
  durationFrames: number;
  fontFamily: string;
}> = ({ text, color, startFrame, durationFrames, fontFamily }) => {
  const frame = useCurrentFrame();
  const { fps, height } = useVideoConfig();

  const localFrame = frame - startFrame;

  if (localFrame < 0 || localFrame >= durationFrames) {
    return null;
  }

  // Punch-in spring
  const scaleSpring = spring({
    frame: localFrame,
    fps,
    config: { damping: 10, stiffness: 250, mass: 0.3 },
  });

  const scale = interpolate(scaleSpring, [0, 1], [0.5, 1]);
  const opacity = scaleSpring;

  // Exit fade
  const exitFrames = 8;
  const exitOpacity = interpolate(
    localFrame,
    [durationFrames - exitFrames, durationFrames],
    [1, 0],
    { extrapolateLeft: "clamp", extrapolateRight: "clamp" }
  );

  return (
    <div
      style={{
        position: "absolute",
        left: 0,
        right: 0,
        top: height * 0.35,
        display: "flex",
        justifyContent: "center",
        opacity: Math.min(opacity, exitOpacity),
        transform: `scale(${scale})`,
        fontFamily,
        fontSize: height * 0.06,
        fontWeight: 700,
        color,
        letterSpacing: 8,
        textShadow: `0px 0px 30px ${color}60, 0px 0px 60px ${color}20`,
        willChange: "transform, opacity",
      }}
    >
      {text}
    </div>
  );
};

/**
 * Closing ritual — two lines appearing sequentially.
 */
const ClosingRitual: React.FC<{
  line1Text: string;
  line1Color: string;
  line1StartFrame: number;
  line2Text: string;
  line2Color: string;
  line2StartFrame: number;
  fontFamily: string;
}> = ({ line1Text, line1Color, line1StartFrame, line2Text, line2Color, line2StartFrame, fontFamily }) => {
  const frame = useCurrentFrame();
  const { fps, height } = useVideoConfig();

  // Line 1
  const l1Local = frame - line1StartFrame;
  const l1Spring = l1Local >= 0 ? spring({ frame: l1Local, fps, config: { damping: 14, stiffness: 140, mass: 0.5 } }) : 0;
  const l1Y = interpolate(l1Spring, [0, 1], [20, 0]);

  // Line 2
  const l2Local = frame - line2StartFrame;
  const l2Spring = l2Local >= 0 ? spring({ frame: l2Local, fps, config: { damping: 14, stiffness: 140, mass: 0.5 } }) : 0;
  const l2Y = interpolate(l2Spring, [0, 1], [20, 0]);

  if (l1Local < 0) return null;

  return (
    <div
      style={{
        position: "absolute",
        left: 0,
        right: 0,
        top: "45%",
        display: "flex",
        flexDirection: "column",
        alignItems: "center",
        gap: height * 0.03,
        padding: "0 10%",
      }}
    >
      {l1Local >= 0 && (
        <div
          style={{
            opacity: l1Spring,
            transform: `translateY(${l1Y}px)`,
            fontFamily,
            fontSize: height * 0.045,
            fontWeight: 400,
            color: line1Color,
            textShadow: `0px 0px 20px ${line1Color}50`,
            willChange: "transform, opacity",
          }}
        >
          {line1Text}
        </div>
      )}
      {l2Local >= 0 && (
        <div
          style={{
            opacity: l2Spring,
            transform: `translateY(${l2Y}px)`,
            fontFamily,
            fontSize: height * 0.032,
            fontWeight: 400,
            color: line2Color,
            textShadow: `0px 0px 16px ${line2Color}40`,
            willChange: "transform, opacity",
          }}
        >
          {line2Text}
        </div>
      )}
    </div>
  );
};

/**
 * FourthWallOverlay — Renders all motion graphics for a Through the 4th Wall episode.
 * Transparent background, designed for ProRes 4444 compositing in Resolve.
 */
export const FourthWallOverlay: React.FC<FourthWallOverlayProps> = (props) => {
  return (
    <AbsoluteFill>
      {/* Night card */}
      <AnimatedText
        text={props.nightCardText}
        color={props.nightCardColor}
        fontSize={1920 * 0.035}
        startFrame={props.nightCardStartFrame}
        durationFrames={props.nightCardDurationFrames}
        fontFamily={props.fontFamily}
        letterSpacing={3}
      />

      {/* 1/100 counter */}
      {props.counterText && props.counterStartFrame !== undefined && (
        <CounterOverlay
          text={props.counterText}
          color={props.counterColor || "#00BFFF"}
          startFrame={props.counterStartFrame}
          durationFrames={props.counterDurationFrames || 45}
          fontFamily={props.fontFamily}
        />
      )}

      {/* Closing ritual */}
      <ClosingRitual
        line1Text={props.closingLine1Text}
        line1Color={props.closingLine1Color}
        line2Text={props.closingLine2Text}
        line2Color={props.closingLine2Color}
        line1StartFrame={props.closingLine1StartFrame}
        line2StartFrame={props.closingLine2StartFrame}
        fontFamily={props.fontFamily}
      />
    </AbsoluteFill>
  );
};
