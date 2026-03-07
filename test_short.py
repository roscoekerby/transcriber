import subprocess

with open("test3.ass", "w") as f:
    f.write("""[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,Arial Black,88,&H00FFFFFF,&H0000FFFF,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,3,0,2,10,10,40,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
Dialogue: 0,0:00:00.00,0:00:05.00,Default,,0,0,0,,A""")

subprocess.run("ffmpeg -y -i test_base.mp4 -vf ass=test3.ass test_ass3.mp4", shell=True)
subprocess.run("ffmpeg -y -i test_ass3.mp4 -vframes 1 test_ass3.png", shell=True)

from PIL import Image, ImageDraw, ImageFont
img = Image.new("RGB", (1080, 1920), (0, 0, 255))
d = ImageDraw.Draw(img)
font = ImageFont.truetype("C:/Windows/Fonts/ariblk.ttf", 88)
d.text((540, 1920 - 40), "A", font=font, fill=(255, 255, 255), anchor="mb")
img.save("test_pil3.png")

import cv2
import numpy as np

for name in ["test_ass3.png", "test_pil3.png"]:
    im = cv2.imread(name)
    blue = np.array([255, 0, 0])
    diff = np.abs(im - blue).sum(axis=2)
    y, x = np.where(diff > 50)
    print(f"{name}: width={x.max() - x.min()}, height={y.max() - y.min()}")
