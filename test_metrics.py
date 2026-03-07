from PIL import ImageFont

for name, path in [("Arial", "arialbd.ttf"), ("Arial Black", "ariblk.ttf")]:
    font = ImageFont.truetype(f"C:/Windows/Fonts/{path}", 88)
    asc, desc = font.getmetrics()
    line_height = asc + desc
    em_ratio = line_height / 88
    print(f"{name}: line_height={line_height}, em_ratio={em_ratio:.3f}")
