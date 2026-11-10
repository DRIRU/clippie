"""Render a synthetic Valorant-like kill feed video (for tests and the OCR benchmark).

This is a *synthetic* stand-in: it proves the pipeline wiring and gives a smoke-level
OCR check, NOT real-game accuracy."""
from __future__ import annotations

import subprocess
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

W, H, FPS = 1280, 720, 10
FONT = ImageFont.load_default(size=22)
BIG = ImageFont.load_default(size=64)


def background(rng: np.random.Generator) -> Image.Image:
    base = np.linspace(40, 90, W, dtype=np.float32)[None, :, None].repeat(H, 0).repeat(3, 2)
    base += rng.normal(0, 6, base.shape)
    return Image.fromarray(np.clip(base, 0, 255).astype(np.uint8))


def render(out: Path, feed: list[tuple[float, float, str, str]], duration: float,
           banners: list[tuple[float, float, str]] = ()) -> Path:
    """feed: (t_start, t_end, killer, victim) rows; banners: (t_start, t_end, text)."""
    rng = np.random.default_rng(0)
    p = subprocess.Popen(
        ["ffmpeg", "-y", "-v", "error", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}", "-r", str(FPS),
         "-i", "-", "-an", "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", str(out)],
        stdin=subprocess.PIPE)
    for n in range(int(duration * FPS)):
        t = n / FPS
        img = background(rng)
        d = ImageDraw.Draw(img)
        active = [r for r in feed if r[0] <= t < r[1]]
        for i, (_, _, killer, victim) in enumerate(active):
            y = 50 + i * 40
            d.rectangle([880, y - 4, 1270, y + 30], fill=(15, 15, 20))
            d.text((890, y), killer, font=FONT, fill=(255, 255, 255))
            d.rectangle([1030, y + 6, 1060, y + 18], fill=(230, 230, 230))  # weapon icon stand-in
            d.text((1080, y), victim, font=FONT, fill=(255, 120, 120))
        for (a, b, text) in banners:
            if a <= t < b:
                d.text(((W - d.textlength(text, font=BIG)) / 2, 430), text, font=BIG, fill=(255, 255, 255))
        p.stdin.write(img.tobytes())
    p.stdin.close()
    p.wait()
    return out
