import { registerRoot } from "remotion";
import { RemotionRoot } from "./Root";

registerRoot(RemotionRoot);

// Library exports for external consumers
export { RemotionRoot } from "./Root";
export { SubtitleOverlay } from "./compositions/SubtitleOverlay";
export { MotionGraphics } from "./compositions/MotionGraphics";
