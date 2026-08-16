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
  const fSize = style?.fontSize ? `${style.fontSize}px` : "58px";
  const outlineCol = style?.outlineColor || "#000000";
  const outlineW = style?.outlineWidth || 4;
  const fWeight = style?.fontWeight || 800;
  const pos = style?.position || "bottom";

  const shadow = `
    -${outlineW}px -${outlineW}px 0 ${outlineCol},
     0   -${outlineW}px 0 ${outlineCol},
     ${outlineW}px -${outlineW}px 0 ${outlineCol},
     ${outlineW}px  0   0 ${outlineCol},
     ${outlineW}px  ${outlineW}px 0 ${outlineCol},
     0    ${outlineW}px 0 ${outlineCol},
    -${outlineW}px  ${outlineW}px 0 ${outlineCol},
    -${outlineW}px  0   0 ${outlineCol}
  `;
  
  let bottomStyle = "128px";
  if (pos === "top") bottomStyle = "auto";
  const topStyle = pos === "top" ? "128px" : "auto";
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
                fontSize: fSize,
                fontWeight: fWeight,
                lineHeight: "1.2",
                textAlign: "center",
                textShadow: shadow,
                maxWidth: "90%",
                padding: "24px 48px",
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
