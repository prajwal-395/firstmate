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

export type SubtitleOverlayProps = {
  subtitles: SubtitleBlock[];
  fps: number;
  width: number;
  height: number;
  durationInFrames: number;
};

export const subtitleOverlaySchema = {} as any;

export const SubtitleOverlay: React.FC<SubtitleOverlayProps> = ({
  subtitles,
}) => {
  const frame = useCurrentFrame();

  return (
    <AbsoluteFill className="items-center justify-center pointer-events-none">
      <div className="flex flex-col items-center justify-end w-full h-[85%] pb-32">
        {subtitles.map((sub, index) => {
          const isActive = frame >= sub.startFrame && frame <= sub.endFrame;
          if (!isActive) return null;

          const hasWords = sub.words && sub.words.length > 0;

          return (
            <div
              key={index}
              className="px-12 py-6 rounded-2xl flex flex-wrap justify-center items-center gap-x-4 gap-y-2 max-w-[90%]"
              style={{
                fontFamily: "Outfit, sans-serif",
                fontSize: "72px",
                fontWeight: 800,
                lineHeight: "1.2",
                textAlign: "center",
                textShadow: "0px 8px 16px rgba(0,0,0,0.4)",
              }}
            >
              {hasWords
                ? sub.words!.map((w, i) => {
                    const isEmphasis = sub.emphasisWords?.includes(
                      w.word.replace(/[^\w]/g, "").toLowerCase()
                    );
                    return (
                      <AnimatedWord
                        key={i}
                        word={w.word}
                        startFrame={w.startFrame}
                        endFrame={w.endFrame}
                        isEmphasis={isEmphasis}
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
