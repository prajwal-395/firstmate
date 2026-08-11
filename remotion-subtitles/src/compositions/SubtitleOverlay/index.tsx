/* eslint-disable @typescript-eslint/no-explicit-any */
import React from "react";
import { AbsoluteFill, useCurrentFrame } from "remotion";
import { AnimatedWord } from "./AnimatedWord";

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

export const SubtitleOverlay: React.FC<SubtitleOverlayProps> = ({
  subtitles,
  style,
}) => {
  const frame = useCurrentFrame();

  const fontFam = style?.fontFamily || "Montserrat";
  const fSize = style?.fontSize ? `${style.fontSize}px` : "58px";
  const outlineCol = style?.outlineColor || "#000000";
  const outlineW = style?.outlineWidth || 4;
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
  let topStyle = pos === "top" ? "128px" : "auto";
  let centerStyle = pos === "center" ? "50%" : undefined;
  let transformStyle = pos === "center" ? "translateY(-50%)" : undefined;

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
                fontWeight: 800,
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
