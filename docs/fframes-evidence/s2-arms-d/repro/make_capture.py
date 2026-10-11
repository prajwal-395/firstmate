"""Generate the fake website capture the website_panel cases composite.

A project-supplied still, the way brand_assets supplies one: the engine
never screenshots the web itself. Solid header, three text-ish bars and
a hero block - enough ink in known places that a misplaced or
mis-scaled shot fails the comparison, without pretending to be a real
page.
"""
import os
import sys

from PIL import Image, ImageDraw

out = sys.argv[1] if len(sys.argv) > 1 else os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "capture.png")

im = Image.new("RGB", (800, 600), (250, 250, 249))
d = ImageDraw.Draw(im)
d.rectangle((0, 0, 800, 96), fill=(29, 78, 216))
d.rectangle((48, 32, 288, 64), fill=(255, 255, 255))
d.rectangle((48, 160, 752, 208), fill=(32, 32, 32))
d.rectangle((48, 232, 560, 264), fill=(120, 120, 120))
d.rectangle((48, 288, 640, 320), fill=(120, 120, 120))
d.rectangle((48, 360, 304, 528), fill=(52, 211, 153))
d.rectangle((336, 360, 592, 528), fill=(245, 158, 11))
im.save(out)
print("wrote", out)
