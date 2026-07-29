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
 * Zod schema for a single word with timing data.
 */
export const wordTimingSchema = z.object({
  word: z.string(),
  startFrame: z.number(),
  endFrame: z.number(),
});

/**
 * Zod schema for style configuration.
 */
export const subtitleStyleSchema = z.object({
  fontSize: z.number().optional(),
  fontColor: z.string().optional().default("#FFFFFF"),
  accentColor: z.string().optional().default("#00D4FF"),
  position: z.enum(["bottom", "center", "top"]).optional().default("bottom"),
  outlineColor: z.string().optional().default("#000000"),
  outlineWidth: z.number().optional().default(4),
});

/**
 * Zod schema for a single subtitle entry.
 */
export const subtitleEntrySchema = z.object({
  text: z.string(),
  startFrame: z.number(),
  endFrame: z.number(),
  emphasisWords: z.array(z.string()).optional(),
  words: z.array(wordTimingSchema).optional(),
});

/**
 * Zod schema for the SubtitleOverlay composition props.
 */
export const subtitleOverlaySchema = z.object({
  subtitles: z.array(subtitleEntrySchema),
  fps: z.number(),
  width: z.number(),
  height: z.number(),
  durationInFrames: z.number(),
  style: subtitleStyleSchema.optional(),
});

export type WordTiming = z.infer<typeof wordTimingSchema>;
export type SubtitleStyle = z.infer<typeof subtitleStyleSchema>;
export type SubtitleEntry = z.infer<typeof subtitleEntrySchema>;
export type SubtitleOverlayProps = z.infer<typeof subtitleOverlaySchema>;

/**
 * Generates 8-directional text-shadow for a crisp outline effect,
 * plus a soft ambient glow for readability.
 */
const buildOutlineShadow = (color: string, width: number): string => {
  const shadows: string[] = [];
  const steps = [
    [1, 0],
    [-1, 0],
    [0, 1],
    [0, -1],
    [1, 1],
    [-1, -1],
    [1, -1],
    [-1, 1],
  ];
  for (const [dx, dy] of steps) {
    shadows.push(`${dx * width}px ${dy * width}px 0px ${color}`);
  }
  // Ambient glow
  shadows.push(`0px 0px ${width * 3}px rgba(0,0,0,0.6)`);
  return shadows.join(", ");
};

/**
 * Renders a single animated word with karaoke-style reveal.
 */
const AnimatedWord: React.FC<{
  word: string;
  wordStartFrame: number;
  wordEndFrame: number;
  isActive: boolean;
  isSpoken: boolean;
  isEmphasis: boolean;
  globalFrame: number;
  fps: number;
  fontColor: string;
  accentColor: string;
}> = ({
  word,
  wordStartFrame,
  isActive,
  isSpoken,
  isEmphasis,
  globalFrame,
  fps,
  fontColor,
  accentColor,
}) => {
  // Word hasn't appeared yet
  if (!isSpoken && !isActive) {
    return (
      <span
        style={{
          opacity: 0,
          display: "inline-block",
        }}
      >
        {word}
      </span>
    );
  }

  // Spring-based pop-in when the word first appears
  const wordLocalFrame = globalFrame - wordStartFrame;
  const popIn = spring({
    frame: wordLocalFrame,
    fps,
    config: {
      damping: 12,
      stiffness: 200,
      mass: 0.4,
    },
  });

  // Active word: scale bump — ramps up then settles back
  const activeScale = isActive
    ? interpolate(wordLocalFrame, [0, 3, 8], [1.0, 1.08, 1.0], {
        extrapolateLeft: "clamp",
        extrapolateRight: "clamp",
        easing: Easing.out(Easing.cubic),
      })
    : 1.0;

  // Active word glow
  const glowIntensity = isActive
    ? interpolate(wordLocalFrame, [0, 4, 10], [0, 1, 0.6], {
        extrapolateLeft: "clamp",
        extrapolateRight: "clamp",
      })
    : 0;

  // Determine color
  let color = fontColor;
  if (isActive || isEmphasis) {
    color = accentColor;
  }

  // Build text shadow for active glow
  const glowShadow =
    glowIntensity > 0
      ? `, 0px 0px ${12 * glowIntensity}px ${accentColor}, 0px 0px ${24 * glowIntensity}px ${accentColor}40`
      : "";

  return (
    <span
      style={{
        display: "inline-block",
        color,
        opacity: popIn,
        transform: `scale(${popIn * activeScale}) translateY(${(1 - popIn) * 12}px)`,
        filter: glowIntensity > 0 ? `brightness(${1 + glowIntensity * 0.3})` : undefined,
        textShadow: glowShadow || undefined,
        transition: "color 0.08s ease",
        willChange: "transform, opacity",
      }}
    >
      {word}
    </span>
  );
};

/**
 * SubtitleOverlay - Renders premium animated subtitle text on a transparent
 * background, with word-by-word karaoke reveal, spring physics, gradient
 * active-word highlights, and smooth phrase enter/exit animations.
 *
 * Designed for compositing as an overlay in DaVinci Resolve (ProRes 4444 alpha).
 */
export const SubtitleOverlay: React.FC<SubtitleOverlayProps> = ({
  subtitles,
  style: styleProp,
}) => {
  const frame = useCurrentFrame();
  const { height, fps } = useVideoConfig();

  // Resolve style with defaults
  const s: Required<SubtitleStyle> = {
    fontSize: styleProp?.fontSize ?? Math.round(height * 0.035),
    fontColor: styleProp?.fontColor ?? "#FFFFFF",
    accentColor: styleProp?.accentColor ?? "#00D4FF",
    position: styleProp?.position ?? "bottom",
    outlineColor: styleProp?.outlineColor ?? "#000000",
    outlineWidth: styleProp?.outlineWidth ?? 4,
  };

  // Find the current subtitle based on the frame
  const currentSubtitle = subtitles.find(
    (sub) => frame >= sub.startFrame && frame < sub.endFrame
  );

  if (!currentSubtitle) {
    return <AbsoluteFill />;
  }

  const subtitleFrame = frame - currentSubtitle.startFrame;
  const subtitleDuration = currentSubtitle.endFrame - currentSubtitle.startFrame;

  // ----- Phrase entrance: spring from below -----
  const entranceSpring = spring({
    frame: subtitleFrame,
    fps,
    config: {
      damping: 14,
      stiffness: 160,
      mass: 0.6,
    },
  });

  const entranceY = interpolate(entranceSpring, [0, 1], [40, 0]);
  const entranceOpacity = entranceSpring;

  // ----- Phrase exit: fade out + upward drift in last 5 frames -----
  const exitFrames = 5;
  const exitProgress = interpolate(
    subtitleFrame,
    [subtitleDuration - exitFrames, subtitleDuration],
    [0, 1],
    {
      extrapolateLeft: "clamp",
      extrapolateRight: "clamp",
      easing: Easing.in(Easing.cubic),
    }
  );

  const exitOpacity = 1 - exitProgress;
  const exitY = exitProgress * -20;

  const combinedOpacity = Math.min(entranceOpacity, exitOpacity);
  const combinedY = entranceY + exitY;

  // ----- Build word list with timing -----
  const emphasisSet = new Set(
    (currentSubtitle.emphasisWords || []).map((w: string) =>
      w.toLowerCase().replace(/[.,!?;:'"]/g, "")
    )
  );

  // If per-word timing is available, use it; otherwise treat whole text as one unit
  const hasWordTimings =
    currentSubtitle.words && currentSubtitle.words.length > 0;

  const wordEntries: {
    word: string;
    startFrame: number;
    endFrame: number;
  }[] = hasWordTimings
    ? currentSubtitle.words!
    : [
        {
          word: currentSubtitle.text,
          startFrame: currentSubtitle.startFrame,
          endFrame: currentSubtitle.endFrame,
        },
      ];

  // ----- Position -----
  const positionStyle: React.CSSProperties = (() => {
    switch (s.position) {
      case "top":
        return { top: height * 0.12 };
      case "center":
        return { top: "50%", transform: "translateY(-50%)" };
      case "bottom":
      default:
        return { top: height * 0.75 };
    }
  })();

  const outlineShadow = buildOutlineShadow(s.outlineColor, s.outlineWidth);

  return (
    <AbsoluteFill>
      <div
        style={{
          position: "absolute",
          left: 0,
          right: 0,
          display: "flex",
          justifyContent: "center",
          alignItems: "center",
          padding: "0 5%",
          ...positionStyle,
        }}
      >
        <div
          style={{
            opacity: combinedOpacity,
            transform: `translateY(${combinedY}px)`,
            fontFamily: "'Inter', 'Helvetica Neue', Arial, sans-serif",
            fontSize: s.fontSize,
            fontWeight: 900,
            textAlign: "center",
            lineHeight: 1.3,
            textShadow: outlineShadow,
            color: s.fontColor,
            willChange: "transform, opacity",
            display: "flex",
            flexWrap: "wrap",
            justifyContent: "center",
            gap: `0px ${s.fontSize * 0.3}px`,
          }}
        >
          {wordEntries.map((wEntry, i) => {
            const isSpoken = frame >= wEntry.endFrame;
            const isActive =
              frame >= wEntry.startFrame && frame < wEntry.endFrame;
            const cleanWord = wEntry.word
              .toLowerCase()
              .replace(/[.,!?;:'"]/g, "");
            const isEmphasis = emphasisSet.has(cleanWord) && isSpoken;

            return (
              <AnimatedWord
                key={`${currentSubtitle.startFrame}-${i}`}
                word={wEntry.word}
                wordStartFrame={wEntry.startFrame}
                wordEndFrame={wEntry.endFrame}
                isActive={isActive}
                isSpoken={isSpoken}
                isEmphasis={isEmphasis}
                globalFrame={frame}
                fps={fps}
                fontColor={s.fontColor}
                accentColor={s.accentColor}
              />
            );
          })}
        </div>
      </div>
    </AbsoluteFill>
  );
};
