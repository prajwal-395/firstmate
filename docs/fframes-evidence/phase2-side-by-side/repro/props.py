"""Representative props for the five compositions, shared by both engines."""

W, H, FPS, D = 1080, 1920, 30, 60
SAFE = {"top": 220, "right": 60, "bottom": 320, "left": 60}
FRAMES = [3, 45]  # 0-based frames compared: mid-entrance, settled


def _words(text, start=0, per=8):
    out = []
    for i, w in enumerate(text.split()):
        out.append({"word": w, "startFrame": start + i * per,
                    "endFrame": start + (i + 1) * per})
    return out


def props(name, avatar_png=None):
    base = {"fps": FPS, "width": W, "height": H, "durationInFrames": D}
    if name == "SubtitleOverlay":
        text = "the quick brown fox jumps over the lazy dog"
        return dict(base, style={
            "fontFamily": "Montserrat", "fontSize": 64, "fontWeight": 800,
            "outlineColor": "#000000", "outlineWidth": 4,
            "fontColor": "#FFFFFF", "accentColor": "#FBF0B8",
            "position": "bottom", "safeArea": SAFE, "captionMaxWidth": 760,
        }, subtitles=[{"startFrame": 0, "endFrame": D, "text": text,
                       "words": _words(text, 0, 7),
                       "emphasisWords": ["fox"]}])
    if name == "TimedTextOverlay":
        return dict(base, fontFamily="Montserrat", safeArea=SAFE, moments=[{
            "text": "Day one\nof the build", "startFrame": 0,
            "durationFrames": D, "fadeInFrames": 6, "fadeOutFrames": 6,
            "x": 0.5, "y": 0.45, "fontSize": 72, "fontWeight": 700,
            "color": "#FFFFFF", "textAlign": "center"}])
    if name == "FullFrameCard":
        return dict(base, background="#1A1A2E", entrance="fade", exit="cut",
                    fontFamily="Montserrat", safeArea=SAFE, runs=[
                        {"text": "Three weeks later", "type_role": "display",
                         "colour": "#FFFFFF"},
                        {"text": "the prototype finally ran end to end",
                         "type_role": "supporting", "colour": "#F2C14E"}])
    if name == "MotionGraphics":
        return dict(base, safeArea=SAFE, elements=[{
            "element": "title_lockup", "anchor": "centre", "row": 0,
            "startFrame": 0, "durationFrames": D, "entrance": "fade",
            "exit": "cut", "color": "#FFFFFF", "runs": [
                {"text": "Title", "type_role": "display"},
                {"text": "a supporting line under it", "type_role": "supporting"}]}])
    if name == "PostHeader":
        return dict(base, fontFamily="Montserrat", box={"left": 64, "top": 120, "width": 900},
                    avatarSrc=avatar_png, avatarShape="round",
                    avatarBackground="#F5F5F5", displayName="Ren Studio",
                    handle="renstudio", verified=True,
                    hook="What happens when a renderer has to match a browser frame for frame",
                    nameSize=34, handleSize=26, hookSize=36)
    raise KeyError(name)


ALL = ["SubtitleOverlay", "TimedTextOverlay", "FullFrameCard",
       "MotionGraphics", "PostHeader"]
