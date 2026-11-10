"""Vertical (9:16) layouts: the blurred-background default, and a facecam layout with the webcam in a
band on top and the gameplay below.

The facecam is whatever rectangle the user draws over a frame of their video, so nothing is guessed.
All filter graphs end in `format=yuv420p[v]` so the subtitle and preview code can hook in the same way.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass

OUT_W, OUT_H = 1080, 1920

BLUR_FILTER = (
    "[0:v]split=2[a][b];"
    "[a]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,boxblur=30:5[bg];"
    "[b]scale=1080:-2[fg];"
    "[bg][fg]overlay=(W-w)/2:(H-h)/2,format=yuv420p[v]"
)

MODES = ("blur", "facecam")
GAMEPLAY = ("fit", "fill")


@dataclass
class Layout:
    mode: str = "blur"                    # blur | facecam
    facecam: dict | None = None           # {"x","y","w","h"} as fractions of the source frame, or None
    facecam_pct: float = 30.0             # share of the 1920 px height used by the facecam band
    gameplay: str = "fit"                 # fit = whole frame on a blurred fill | fill = crop to fill the area
    focus_x: float = 0.5                  # with "fill": which part of the width to keep (0 = left, 1 = right)

    @classmethod
    def from_dict(cls, d: dict | None, base: "Layout | None" = None) -> "Layout":
        cur = asdict(base or cls())
        d = d or {}
        unknown = set(d) - set(cur)
        if unknown:
            raise ValueError(f"unknown layout setting(s): {', '.join(sorted(unknown))}")
        for k, v in d.items():
            if k == "mode":
                if v not in MODES:
                    raise ValueError("mode must be blur or facecam")
                cur[k] = v
            elif k == "gameplay":
                if v not in GAMEPLAY:
                    raise ValueError("gameplay must be fit or fill")
                cur[k] = v
            elif k == "facecam_pct":
                cur[k] = _num(v, 15.0, 45.0, k)
            elif k == "focus_x":
                cur[k] = _num(v, 0.0, 1.0, k)
            elif k == "facecam":
                cur[k] = None if v is None else _rect(v)
        return cls(**cur)

    def to_dict(self) -> dict:
        return asdict(self)

    @property
    def uses_facecam(self) -> bool:
        return self.mode == "facecam" and self.facecam is not None


def _num(v, lo, hi, name) -> float:
    try:
        v = float(v)
    except (TypeError, ValueError):
        raise ValueError(f"{name} must be a number") from None
    return max(lo, min(hi, v))


def _rect(v) -> dict:
    if not isinstance(v, dict) or set(v) != {"x", "y", "w", "h"}:
        raise ValueError("facecam must be an object with x, y, w and h")
    x, y = _num(v["x"], 0.0, 1.0, "x"), _num(v["y"], 0.0, 1.0, "y")
    w, h = _num(v["w"], 0.02, 1.0, "w"), _num(v["h"], 0.02, 1.0, "h")
    w, h = min(w, 1.0 - x), min(h, 1.0 - y)          # keep the box inside the frame
    if w < 0.02 or h < 0.02:
        raise ValueError("the facecam box is outside the frame")
    return {"x": round(x, 4), "y": round(y, 4), "w": round(w, 4), "h": round(h, 4)}


def _even(n: float) -> int:
    return max(2, int(n) // 2 * 2)


def vertical_filter(lay: Layout, src_w: int, src_h: int) -> str:
    """filter_complex for the 1080x1920 export of a src_w x src_h video (input 0)."""
    if not lay.uses_facecam:
        return BLUR_FILTER
    band = _even(OUT_H * lay.facecam_pct / 100)
    area = OUT_H - band

    r = lay.facecam
    fw = min(_even(r["w"] * src_w), _even(src_w))
    fh = min(_even(r["h"] * src_h), _even(src_h))
    fx = max(0, min(int(r["x"] * src_w), src_w - fw))
    fy = max(0, min(int(r["y"] * src_h), src_h - fh))
    face = (f"[a]crop={fw}:{fh}:{fx}:{fy},scale={OUT_W}:{band}:force_original_aspect_ratio=increase,"
            f"crop={OUT_W}:{band}[face]")

    if lay.gameplay == "fill":
        win_h = float(src_h)
        win_w = src_h * OUT_W / area
        if win_w > src_w:                       # source narrower than the area: crop the height instead
            win_w, win_h = float(src_w), src_w * area / OUT_W
        cw, ch = min(_even(win_w), _even(src_w)), min(_even(win_h), _even(src_h))
        cx = int(max(0.0, min(1.0, lay.focus_x)) * (src_w - cw))
        cy = (src_h - ch) // 2
        game = f"[b]crop={cw}:{ch}:{cx}:{cy},scale={OUT_W}:{area}[game]"
    else:
        game = (f"[b]split=2[b1][b2];"
                f"[b1]scale={OUT_W}:{area}:force_original_aspect_ratio=increase,crop={OUT_W}:{area},boxblur=30:5[gbg];"
                f"[b2]scale={OUT_W}:-2[gfg];"
                f"[gbg][gfg]overlay=(W-w)/2:(H-h)/2[game]")
    return f"[0:v]split=2[a][b];{face};{game};[face][game]vstack=inputs=2,format=yuv420p[v]"
