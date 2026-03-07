from PIL import ImageFont, ImageDraw, Image

font = ImageFont.truetype("C:/Windows/Fonts/ariblk.ttf", 21)
img = Image.new("RGB", (500, 500))
d = ImageDraw.Draw(img)
bbox = d.textbbox((0, 0), "loading phase required?", font=font)
print(f"PIL Arial Black size 21 width: {bbox[2] - bbox[0]}, height: {bbox[3] - bbox[1]}")

font88 = ImageFont.truetype("C:/Windows/Fonts/ariblk.ttf", 88)
bbox88 = d.textbbox((0, 0), "loading phase required?", font=font88)
print(f"PIL Arial Black size 88 width: {bbox88[2] - bbox88[0]}, height: {bbox88[3] - bbox88[1]}")
