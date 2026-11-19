#!/usr/bin/env python3
"""
decode_qr.py - decode the doorbell's sticker QR and extract UID + auth code.

The Vimar/Qualvision QR carries the device's verification code:

  * V1 (plain) : "<apName> <uid> <authCode> <model>"
  * V2 (json)  : {"v":2,"u":"<uid>","c":"<authCode>","m":"<model>", ...}

Usage:
  python decode_qr.py label.jpg
  python decode_qr.py photo1.jpg photo2.jpg
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import cv2


def variants(img):
    yield img
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    yield gray
    yield cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)[1]
    for scale in (1.5, 2.0, 3.0):
        yield cv2.resize(gray, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
    yield cv2.GaussianBlur(gray, (3, 3), 0)


def decode(path: Path) -> list[str]:
    img = cv2.imread(str(path))
    if img is None:
        raise SystemExit(f"cannot read {path}")
    detector = cv2.QRCodeDetector()
    found: list[str] = []
    for candidate in variants(img):
        try:
            ok, texts, _, _ = detector.detectAndDecodeMulti(candidate)
        except cv2.error:
            ok, texts = False, []
        if ok:
            for t in texts:
                if t and t not in found:
                    found.append(t)
        if found:
            break
        try:
            text, _, _ = detector.detectAndDecode(candidate)
        except cv2.error:
            text = ""
        if text and text not in found:
            found.append(text)
            break
    return found


def parse(text: str) -> dict:
    text = text.strip()
    if text.startswith("{"):
        try:
            data = json.loads(text)
            return {
                "format": "v2",
                "uid": data.get("u"),
                "auth_code": data.get("c"),
                "model": data.get("m"),
                "ap_name": data.get("a"),
                "raw": data,
            }
        except json.JSONDecodeError:
            pass
    parts = [p for p in text.split(" ") if p]
    if len(parts) >= 4:
        return {
            "format": "v1",
            "ap_name": parts[0],
            "uid": parts[1],
            "auth_code": parts[2],
            "model": parts[3],
            "raw": text,
        }
    return {"format": "unknown", "raw": text}


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    rc = 1
    for arg in sys.argv[1:]:
        path = Path(arg)
        print(f"=== {path}")
        texts = decode(path)
        if not texts:
            print("  no QR code found - crop closer / better light / straight angle")
            continue
        for text in texts:
            print(f"  raw      : {text}")
            info = parse(text)
            for k in ("format", "uid", "auth_code", "model", "ap_name"):
                if info.get(k):
                    print(f"  {k:9}: {info[k]}")
            if info.get("auth_code"):
                rc = 0
                print()
                print("  next:")
                print(
                    f"    python tools/viewdoor.py probe --host 192.168.1.50 "
                    f"--code {info['auth_code']}"
                )
    return rc


if __name__ == "__main__":
    sys.exit(main())
