/**
 * PostHeader - a social-post header drawn above a vertical reel's picture.
 *
 * The captain, 2026-09-25: "for all of the videos create a graphic that
 * makes it like a twitter post by adding something like this with the
 * profile picture, the blue check, and a text hook regarding the video in
 * the black space above the video."
 *
 * An OVERLAY, rendered with `--transparent` as one still and carried over
 * the reel's picture runs (`library/tools/reel_post_header.py`). It states
 * nothing of its own: the account (avatar, name, handle, verified), every
 * size, the typeface and the hook arrive in props from the project's
 * declaration and the reel's own hook. The one thing drawn here that no
 * prop names is the verified badge's SHAPE - a platform glyph, like a
 * play button, not a look.
 */
import React from "react";
import { AbsoluteFill, Img } from "remotion";

import { loadBundledFonts, loadProjectFont } from "../../fonts";

export type PostHeaderProps = {
  /** The avatar, as a URL `Img` can load (a data URI from the pipeline). */
  avatarSrc: string;
  /** `square` (rounded corners, as the captain's example) or `round`. */
  avatarShape: "square" | "round";
  /** The avatar tile's ground, drawn behind a transparent logo. */
  avatarBackground: string;
  displayName: string;
  /** Without the leading `@`; the composition adds it. */
  handle: string;
  verified: boolean;
  hook: string;
  fontFamily: string;
  fontFile?: string;
  /** Pixels of the delivery frame, all declared by the project. */
  nameSize: number;
  handleSize: number;
  hookSize: number;
  /** The box the header lays out in, in delivery-frame pixels. */
  box: { left: number; top: number; width: number };
  fps: number;
  width: number;
  height: number;
  durationInFrames: number;
};

export const postHeaderSchema = {} as any;

/** The platform's verified badge: a scalloped seal with a check. */
const VerifiedBadge: React.FC<{ size: number }> = ({ size }) => (
  <svg width={size} height={size} viewBox="0 0 24 24" aria-label="verified">
    <path
      fill="#1D9BF0"
      d="M22.25 12c0-1.43-.88-2.67-2.19-3.34.46-1.39.2-2.9-.81-3.91s-2.52-1.27-3.91-.81c-.66-1.31-1.91-2.19-3.34-2.19s-2.67.88-3.33 2.19c-1.4-.46-2.91-.2-3.92.81s-1.26 2.52-.8 3.91C2.63 9.33 1.75 10.57 1.75 12s.88 2.67 2.19 3.34c-.46 1.39-.2 2.9.81 3.91s2.52 1.26 3.91.81c.67 1.31 1.91 2.19 3.34 2.19s2.68-.88 3.34-2.19c1.39.46 2.9.2 3.91-.81s1.27-2.52.81-3.91c1.31-.67 2.19-1.91 2.19-3.34z"
    />
    <path
      fill="#FFFFFF"
      d="M10.54 16.2l-3.74-3.74 1.41-1.41 2.33 2.33 5.66-5.66 1.41 1.41z"
    />
  </svg>
);

export const PostHeader: React.FC<PostHeaderProps> = (props) => {
  const {
    avatarSrc, avatarShape, avatarBackground, displayName, handle,
    verified, hook, fontFamily, fontFile, nameSize, handleSize, hookSize,
    box,
  } = props;

  if (fontFile) {
    loadProjectFont(fontFamily, fontFile);
  } else {
    loadBundledFonts();
  }

  // The avatar spans the name and handle lines, as on the platform.
  const avatar = Math.round(nameSize * 1.3 + handleSize * 1.3);
  const gap = Math.round(nameSize * 0.55);

  return (
    <AbsoluteFill style={{ backgroundColor: "transparent" }}>
      <div
        style={{
          position: "absolute",
          left: box.left,
          top: box.top,
          width: box.width,
          fontFamily,
          color: "#FFFFFF",
        }}
      >
        <div style={{ display: "flex", alignItems: "center" }}>
          <div
            style={{
              width: avatar,
              height: avatar,
              flex: "none",
              borderRadius: avatarShape === "round" ? avatar / 2 : avatar * 0.08,
              backgroundColor: avatarBackground,
              overflow: "hidden",
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
            }}
          >
            <Img
              src={avatarSrc}
              style={{ width: "72%", height: "72%", objectFit: "contain" }}
            />
          </div>
          <div style={{ marginLeft: gap, minWidth: 0 }}>
            <div
              style={{
                display: "flex",
                alignItems: "center",
                fontSize: nameSize,
                fontWeight: 700,
                lineHeight: 1.2,
              }}
            >
              <span>{displayName}</span>
              {verified ? (
                <span style={{ marginLeft: nameSize * 0.25, display: "flex" }}>
                  <VerifiedBadge size={Math.round(nameSize * 1.05)} />
                </span>
              ) : null}
            </div>
            <div
              style={{
                fontSize: handleSize,
                fontWeight: 500,
                lineHeight: 1.25,
                color: "#8B98A5",
              }}
            >
              @{handle}
            </div>
          </div>
        </div>
        <div
          style={{
            marginTop: Math.round(hookSize * 0.6),
            fontSize: hookSize,
            fontWeight: 600,
            lineHeight: 1.3,
            whiteSpace: "pre-wrap",
          }}
        >
          {hook}
        </div>
      </div>
    </AbsoluteFill>
  );
};
