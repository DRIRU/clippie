"""FFmpeg clip rendering: original aspect ratio and vertical 9:16."""
from __future__ import annotations

import subprocess
from pathlib import Path

from .config import get_settings

VERTICAL_FILTER = (
    "[0:v]split=2[a][b];"
    "[a]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,boxblur=30:5[bg];"
    "[b]scale=1080:-2[fg];"
    "[bg][fg]overlay=(W-w)/2:(H-h)/2,format=yuv420p[v]"
)


def _video_args() -> list[str]:
    if get_settings().use_nvenc:
        return ["-c:v", "h264_nvenc", "-preset", "p5", "-cq", "21"]
    return ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20"]


def build_command(src: Path, out: Path, start: float, end: float, kind: str, has_audio: bool) -> list[str]:
    if end <= start:
        raise ValueError("end must be greater than start")
    cmd = ["ffmpeg", "-y", "-v", "error", "-ss", f"{start:.3f}", "-i", str(src), "-t", f"{end - start:.3f}"]
    if kind == "vertical":
        cmd += ["-filter_complex", VERTICAL_FILTER, "-map", "[v]"]
    elif kind == "original":
        cmd += ["-map", "0:v:0"]
    elif kind == "preview":  # small, fast, seekable clip for in-browser review
        cmd += ["-map", "0:v:0", "-vf", r"scale=-2:min(540\,ih),format=yuv420p"]
    else:
        raise ValueError(f"unknown export kind {kind!r}")
    if has_audio:
        cmd += ["-map", "0:a:0", "-c:a", "aac", "-b:a", "96k" if kind == "preview" else "160k"]
    cmd += (["-c:v", "libx264", "-preset", "veryfast", "-crf", "26"] if kind == "preview" else _video_args())
    cmd += ["-movflags", "+faststart", str(out)]
    return cmd


def render(src: Path, out: Path, start: float, end: float, kind: str, has_audio: bool) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    cmd = build_command(src, out, start, end, kind, has_audio)
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"ffmpeg failed: {r.stderr.strip()[-500:]}")
