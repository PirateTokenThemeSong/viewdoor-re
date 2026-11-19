#!/usr/bin/env python3
"""Make the Cancelletto loading poster from a real camera frame.

    python make_blur_poster.py frame.jpg cancelletto-poster.jpg

Grayscale + Gaussian blur + soft vignette, 1100x900 (11:9, same as the card).
Grab a fresh frame with:
    curl -s -o frame.jpg "http://127.0.0.1:1984/api/frame.jpeg?src=cancelletto"
"""
import sys

from PIL import Image, ImageDraw, ImageEnhance, ImageFilter


def main(src_path: str, dst_path: str, blur: float = 7.0) -> None:
    src = Image.open(src_path).convert("L")
    big = src.resize((1100, 900), Image.LANCZOS)
    img = big.filter(ImageFilter.GaussianBlur(radius=blur))
    img = ImageEnhance.Contrast(img).enhance(1.05)
    img = ImageEnhance.Brightness(img).enhance(0.9)

    mask = Image.new("L", big.size, 0)
    ImageDraw.Draw(mask).ellipse([-200, -160, 1300, 1060], fill=255)
    mask = mask.filter(ImageFilter.GaussianBlur(radius=160))
    dark = ImageEnhance.Brightness(img).enhance(0.7)
    out = Image.composite(img, dark, mask)

    out.convert("RGB").save(dst_path, quality=86, optimize=True)
    print(f"{dst_path}: {out.size[0]}x{out.size[1]}")


if __name__ == "__main__":
    if len(sys.argv) < 3:
        raise SystemExit(__doc__)
    main(sys.argv[1], sys.argv[2])
