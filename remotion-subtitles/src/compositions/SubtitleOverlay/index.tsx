/* eslint-disable @typescript-eslint/no-explicit-any */
import React from "react";
import { AbsoluteFill, useCurrentFrame } from "remotion";
import { AnimatedWord } from "./AnimatedWord";
import { loadBundledFonts } from "../../fonts";

// Registered at module scope so the delayRender handle is taken before
// the first frame is rasterised, not during it.
loadBundledFonts();

export type WordTiming = {
  word: string;
  startFrame: number;
  endFrame: number;
};

export type SubtitleBlock = {
  text: string;
  startFrame: number;
  endFrame: number;
  emphasisWords?: string[];
  words?: WordTiming[];
  /**
   * How far THIS card shrinks so its widest word fits the safe area.
   * 1.0 (or absent) for almost every card. A single word wider than the
   * usable width is an unbreakable inline block that the frame clips at
   * both edges - "announcement" at 160px is 1303px in an 840px box -
   * and this is what stops that. Planned by step 4.01's CaptionFitter
   * against library/tools/safe_area.py; it never changes the style's
   * font size, only this one card's.
   */
  fitScale?: number;
};

export type SubtitleStyle = {
  fontFamily?: string;
  fontColor?: string;
  accentColor?: string;
  position?: "bottom" | "top" | "center";
  outlineColor?: string;
  outlineWidth?: number;
  fontSize?: number;
  /**
   * Read from the brand template's `style.typography.weight` via
   * library/tools/subtitle_style.py. The weight used to be hardcoded to
   * 800 below, so a template asking for "bold" got 800 regardless.
   */
  fontWeight?: number;
  /**
   * The platform's keep-clear insets in pixels, from
   * library/tools/safe_area.py via subtitle_style.SubtitleStyle.resolve.
   * `position` names the edge; this says how far from it. The distance
   * used to be a literal `200px`, which is 10.4% of a 1920-row frame and
   * inside the band TikTok paints its own caption and audio bar over.
   */
  safeArea?: { top: number; right: number; bottom: number; left: number };
  /**
   * The widest a caption box may be, from the same enumeration. A caption
   * is centred, so it runs into the nearer edge first and can only be
   * twice that distance wide. This used to be `maxWidth: 90%`, which a
   * long word overflows without any of the wrapping the percentage
   * implies.
   */
  captionMaxWidth?: number;
};

export type SubtitleOverlayProps = {
  subtitles: SubtitleBlock[];
  fps: number;
  width: number;
  height: number;
  durationInFrames: number;
  style?: SubtitleStyle;
};

export const subtitleOverlaySchema = {} as any;

/**
 * Whether a rendered word is one the plan marked for emphasis.
 *
 * step_4_01 computes emphasis words for every caption and
 * generate_remotion_props serialises them as `emphasisWords`, but nothing
 * ever read them: every word was styled identically. Matching is
 * case-insensitive and ignores surrounding punctuation, because the word
 * timings carry the spoken token ("post,") while the emphasis list
 * carries the bare word ("post").
 */
export const normaliseWord = (word: string): string =>
  word.toLowerCase().replace(/^[^\p{L}\p{N}']+|[^\p{L}\p{N}']+$/gu, "");

export const isEmphasisWord = (
  word: string,
  emphasisWords?: string[],
): boolean => {
  if (!emphasisWords || emphasisWords.length === 0) return false;
  const target = normaliseWord(word);
  if (!target) return false;
  return emphasisWords.some((e) => normaliseWord(e) === target);
};

export const SubtitleOverlay: React.FC<SubtitleOverlayProps> = ({
  subtitles,
  style,
}) => {
  const frame = useCurrentFrame();

  const fontFam = style?.fontFamily || "Montserrat";
  const baseFontSize = style?.fontSize ?? 58;
  const fSize = `${baseFontSize}px`;
  const outlineCol = style?.outlineColor || "#000000";
  const outlineW = style?.outlineWidth || 4;
  const fWeight = style?.fontWeight || 800;
  const pos = style?.position || "bottom";
  // No default. The insets come from library/tools/safe_area.py through
  // step 4.01's style, and inventing one here is precisely the "three
  // more hardcoded margins" the captain's ruling of 2026-08-25 forbade.
  const safeArea = style?.safeArea;
  const maxWidthPx = style?.captionMaxWidth;
  if (!safeArea || !maxWidthPx) {
    throw new Error(
      "SubtitleOverlay props carry no safeArea/captionMaxWidth. Step 4.01 " +
        "resolves both from library/tools/safe_area.py via " +
        "library/tools/subtitle_style.py. Refusing to substitute a margin: " +
        "a caption placed by a literal is a caption under the platform's " +
        "own UI with nothing to notice.",
    );
  }

  const shadow = `
    -${outlineW}px -${outlineW}px 0 ${outlineCol},
     0   -${outlineW}px 0 ${outlineCol},
     ${outlineW}px -${outlineW}px 0 ${outlineCol},
     ${outlineW}px  0   0 ${outlineCol},
     ${outlineW}px  ${outlineW}px 0 ${outlineCol},
     0    ${outlineW}px 0 ${outlineCol},
    -${outlineW}px  ${outlineW}px 0 ${outlineCol},
    -${outlineW}px  0   0 ${outlineCol},
    0 10px 20px rgba(0,0,0,0.8)
  `;
  
  let bottomStyle = `${safeArea.bottom}px`;
  if (pos === "top") bottomStyle = "auto";
  const topStyle = pos === "top" ? `${safeArea.top}px` : "auto";
  const centerStyle = pos === "center" ? "50%" : undefined;
  const transformStyle = pos === "center" ? "translateY(-50%)" : undefined;

  return (
    <AbsoluteFill style={{ alignItems: "center", justifyContent: "center", pointerEvents: "none" }}>
      <div 
        style={{ 
          position: "absolute", 
          bottom: centerStyle ? undefined : bottomStyle, 
          top: centerStyle || topStyle,
          transform: transformStyle,
          width: "100%", 
          display: "flex", 
          justifyContent: "center", 
          flexDirection: "column", 
          alignItems: "center" 
        }}
      >
        {subtitles.map((sub, index) => {
          const isActive = frame >= sub.startFrame && frame < sub.endFrame;
          if (!isActive) return null;

          const hasWords = sub.words && sub.words.length > 0;

          return (
            <div
              key={index}
              style={{
                display: "flex",
                flexWrap: "wrap",
                justifyContent: "center",
                alignItems: "center",
                fontFamily: fontFam,
                fontWeight: fWeight,
                lineHeight: "1.2",
                textAlign: "center",
                textShadow: shadow,
                // The safe area bounds the box, and the horizontal
                // padding is exactly the outline the text shadow paints
                // outside the glyph box - so the INK, not just the type,
                // stays inside the usable width.
                maxWidth: `${maxWidthPx}px`,
                boxSizing: "border-box",
                padding: `24px ${outlineW}px`,
                fontSize:
                  sub.fitScale && sub.fitScale < 1
                    ? `${Math.round(baseFontSize * sub.fitScale)}px`
                    : fSize,
              }}
            >
              {hasWords
                ? sub.words!.map((w, i) => {
                    return (
                      <AnimatedWord
                        key={i}
                        word={w.word}
                        startFrame={w.startFrame}
                        endFrame={w.endFrame}
                        isEmphasis={isEmphasisWord(w.word, sub.emphasisWords)}
                        style={style}
                      />
                    );
                  })
                : sub.text}
            </div>
          );
        })}
      </div>
    </AbsoluteFill>
  );
};
