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


def build_command(src: Path, out: Path, start: float, end: float, kind: str, has_audio: bool,
                  ass_name: str | None = None) -> list[str]:
    """ass_name: file name of an ASS subtitle file in the process's working directory (kept relative so no
    drive-letter or backslash escaping is needed inside the filter graph)."""
    if end <= start:
        raise ValueError("end must be greater than start")
    cmd = ["ffmpeg", "-y", "-v", "error", "-ss", f"{start:.3f}", "-i", str(src), "-t", f"{end - start:.3f}"]
    if kind == "vertical":
        fg = VERTICAL_FILTER
        if ass_name:
            fg = fg.replace("format=yuv420p[v]", f"ass={ass_name},format=yuv420p[v]")
        cmd += ["-filter_complex", fg, "-map", "[v]"]
    elif kind == "original":
        cmd += ["-map", "0:v:0"] + (["-vf", f"ass={ass_name},format=yuv420p"] if ass_name else [])
    elif kind == "preview":  # small, fast, seekable clip for in-browser review
        cmd += ["-map", "0:v:0", "-vf", r"scale=-2:min(540\,ih),format=yuv420p"]
    else:
        raise ValueError(f"unknown export kind {kind!r}")
    if has_audio:
        cmd += ["-map", "0:a:0", "-c:a", "aac", "-b:a", "96k" if kind == "preview" else "160k"]
    cmd += (["-c:v", "libx264", "-preset", "veryfast", "-crf", "26"] if kind == "preview" else _video_args())
    cmd += ["-movflags", "+faststart", str(out)]
    return cmd


def render(src: Path, out: Path, start: float, end: float, kind: str, has_audio: bool,
           ass_text: str | None = None) -> None:
    """ass_text: optional subtitle file content to burn into the picture (original and vertical only)."""
    out.parent.mkdir(parents=True, exist_ok=True)
    if ass_text is None:
        r = subprocess.run(build_command(src, out, start, end, kind, has_audio), capture_output=True, text=True)
    else:
        import tempfile
        with tempfile.TemporaryDirectory(prefix="clippie-ass-") as d:
            (Path(d) / "subs.ass").write_text(ass_text, encoding="utf-8")
            r = subprocess.run(build_command(src.resolve(), out.resolve(), start, end, kind, has_audio, "subs.ass"),
                               capture_output=True, text=True, cwd=d)
    if r.returncode != 0:
        raise RuntimeError(f"ffmpeg failed: {r.stderr.strip()[-500:]}")
