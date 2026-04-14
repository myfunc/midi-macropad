"""Crop Chrome browser UI from a captured screenshot.

Usage: python _crop.py <input.png> <output.png> [top_px] [bottom_px]
"""
import sys
from PIL import Image

inp = sys.argv[1]
out = sys.argv[2]
top = int(sys.argv[3]) if len(sys.argv) > 3 else 188  # chrome tabs + url bar
bottom = int(sys.argv[4]) if len(sys.argv) > 4 else 0

img = Image.open(inp)
w, h = img.size
cropped = img.crop((0, top, w, h - bottom))
cropped.save(out)
print(f"{inp} {w}x{h} -> {out} {cropped.size[0]}x{cropped.size[1]}")
