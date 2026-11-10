"""Region OCR over video: stream cropped, upscaled grayscale frames from ffmpeg and read
text rows. Backend is pluggable (RapidOCR today)."""
from __future__ import annotations

import subprocess
import threading
from statistics import median
from typing import Protocol

import numpy as np

# (text, conf, x_center, y_center, height)
Item = tuple[str, float, float, float, float]


class OcrBackend(Protocol):
    name: str

    def read(self, gray: np.ndarray) -> list[Item]: ...


class RapidOcrBackend:
    name = "rapidocr"

    def __init__(self):
        from rapidocr_onnxruntime import RapidOCR

        self._engine = RapidOCR()
        self._lock = threading.Lock()  # one inference at a time per engine

    def read(self, gray: np.ndarray) -> list[Item]:
        img = np.stack([gray] * 3, axis=-1)
        with self._lock:
            result, _ = self._engine(img)
        items: list[Item] = []
        for box, text, score in result or []:
            xs, ys = [p[0] for p in box], [p[1] for p in box]
            items.append((text, float(score), sum(xs) / 4, sum(ys) / 4, max(ys) - min(ys)))
        return items


_backend: OcrBackend | None = None


def get_backend() -> OcrBackend:
    global _backend
    if _backend is None:
        _backend = RapidOcrBackend()
    return _backend


def group_rows(items: list[Item]) -> list[list[list]]:
    """Cluster OCR items into text rows (top-to-bottom), each row ordered left-to-right."""
    if not items:
        return []
    h = median(i[4] for i in items) or 1.0
    rows: list[list[Item]] = []
    for it in sorted(items, key=lambda i: i[3]):
        if rows and abs(it[3] - rows[-1][-1][3]) <= 0.6 * h:
            rows[-1].append(it)
        else:
            rows.append([it])
    return [[[i[0], round(i[1], 3)] for i in sorted(r, key=lambda i: i[2])] for r in rows]


def _even(v: float) -> int:
    return max(2, int(v) // 2 * 2)


def scan_region(src, frame_w: int, frame_h: int, region, fps: float, backend: OcrBackend,
                upscale: int = 2, on_progress=None, duration: float | None = None) -> list[dict]:
    """OCR one HUD region at `fps`. Skips frames with no bright text-like pixels or that are
    near-identical to the last OCR'd frame. Returns [{"t", "rows"}] for frames with text."""
    x, y = _even(region.x * frame_w), _even(region.y * frame_h)
    w = min(_even(region.w * frame_w), frame_w - x)
    h = min(_even(region.h * frame_h), frame_h - y)
    ow, oh = w * upscale, h * upscale
    vf = f"fps={fps},crop={w}:{h}:{x}:{y},scale={ow}:{oh}:flags=lanczos,format=gray"
    p = subprocess.Popen(["ffmpeg", "-v", "error", "-i", str(src), "-an", "-vf", vf, "-f", "rawvideo", "-"],
                         stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    out: list[dict] = []
    last_ocr = None
    idx = 0
    try:
        while True:
            buf = p.stdout.read(ow * oh)
            if len(buf) < ow * oh:
                break
            t = idx / fps
            idx += 1
            f = np.frombuffer(buf, dtype=np.uint8).reshape(oh, ow)
            if on_progress and duration and idx % 20 == 0:
                on_progress(min(1.0, t / duration))
            if (f > 200).mean() < 0.0005:  # no bright pixels => no text to read
                continue
            if last_ocr is not None and np.abs(f.astype(np.int16) - last_ocr).mean() < 1.0:
                continue
            last_ocr = f.astype(np.int16)
            rows = group_rows(backend.read(f))
            if rows:
                out.append({"t": round(t, 2), "rows": rows})
    finally:
        p.stdout.close()
        p.wait()
    return out
