import cv2
import numpy as np

for name in ["test_ass.png", "test_pil.png"]:
    img = cv2.imread(name)
    # text is white/yellow with black outline, background is blue
    # find non-blue pixels
    blue = np.array([255, 0, 0])
    diff = np.abs(img - blue).sum(axis=2)
    y, x = np.where(diff > 50)
    
    if len(x) > 0:
        print(f"{name}: width={x.max() - x.min()}, height={y.max() - y.min()}")
    else:
        print(f"{name}: no text found")
