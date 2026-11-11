"""ffprobe / ffmpeg helpers and safe URL import."""
from __future__ import annotations

import ipaddress
import json
import re
import shutil
import socket
import subprocess
from pathlib import Path
from urllib.parse import urlparse

ALLOWED_EXTS = {".mp4", ".mkv", ".mov", ".webm", ".avi", ".m4v", ".ts", ".flv"}


class MediaError(Exception):
    pass


def probe(path: Path) -> dict:
    """Summarise a media file with ffprobe. Raises MediaError if it has no video."""
    try:
        r = subprocess.run(
            ["ffprobe", "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path)],
            capture_output=True, text=True, timeout=120,
        )
    except FileNotFoundError as e:
        raise MediaError("ffprobe not found on PATH") from e
    if r.returncode != 0:
        raise MediaError(f"ffprobe failed: {r.stderr.strip()[:300]}")
    data = json.loads(r.stdout or "{}")
    streams = data.get("streams", [])
    v = next((s for s in streams if s.get("codec_type") == "video"), None)
    if v is None:
        raise MediaError("file has no video stream")
    a = [s for s in streams if s.get("codec_type") == "audio"]

    def _fps(s: str | None) -> float:
        try:
            n, d = (s or "0/1").split("/")
            return round(float(n) / float(d), 3) if float(d) else 0.0
        except ValueError:
            return 0.0

    fmt = data.get("format", {})
    duration = float(fmt.get("duration") or v.get("duration") or 0)
    if duration <= 0:
        raise MediaError("could not determine duration")
    return {
        "duration": duration,
        "width": int(v.get("width", 0)),
        "height": int(v.get("height", 0)),
        "fps": _fps(v.get("avg_frame_rate")),
        "video_codec": v.get("codec_name"),
        "audio_codec": a[0].get("codec_name") if a else None,
        "audio_streams": len(a),
        "has_audio": bool(a),
        "size_bytes": int(fmt.get("size") or 0),
        "container": fmt.get("format_name"),
    }


def thumbnail(src: Path, out: Path, at: float) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-ss", f"{at:.2f}", "-i", str(src),
         "-frames:v", "1", "-vf", "scale=480:-2", str(out)],
        check=True, capture_output=True, timeout=120,
    )


def assert_public_url(url: str) -> None:
    """Reject non-http(s) URLs and hosts resolving to private/loopback/link-local ranges (SSRF guard).

    Limitation: this checks the hostname at import time only; yt-dlp may follow
    redirects. Acceptable for a single-user prototype, tighten before multi-user.
    """
    p = urlparse(url)
    if p.scheme not in ("http", "https") or not p.hostname:
        raise MediaError("only http(s) URLs are allowed")
    try:
        infos = socket.getaddrinfo(p.hostname, None)
    except socket.gaierror as e:
        raise MediaError(f"cannot resolve host: {p.hostname}") from e
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if not ip.is_global:
            raise MediaError("URL resolves to a non-public address")


def _clean_dl_error(msg: str) -> str:
    msg = re.sub("" + r"\[[0-9;]*m", "", msg).replace("ERROR: ", "").strip()
    if "Sign in to confirm" in msg or "not a bot" in msg:
        msg += " (YouTube is asking for a login/bot check; try again later or download the video and upload it)"
    return msg[:500]


def download_url(url: str, outdir: Path, max_bytes: int, on_progress=None) -> tuple[Path, str | None]:
    """Download with yt-dlp (no cookies, no user-supplied options) into outdir; return the file."""
    import yt_dlp

    assert_public_url(url)
    outdir.mkdir(parents=True, exist_ok=True)

    def hook(d):
        if on_progress and d.get("status") == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
            if total:
                on_progress(min(1.0, d.get("downloaded_bytes", 0) / total))

    opts = {
        "outtmpl": str(outdir / "source.%(ext)s"),
        # Prefer H.264: it decodes about 3x faster than AV1 on typical hardware, which speeds up every later stage.
        "format": "bv*[height<=1080][vcodec^=avc1]+ba/bv*[height<=1080]+ba/b[height<=1080]/b",
        "merge_output_format": "mp4",
        "noplaylist": True,
        "max_filesize": max_bytes,
        "quiet": True,
        "no_warnings": True,
        "progress_hooks": [hook],
        "restrictfilenames": True,
    }
    # Current YouTube extraction needs a JS runtime (solver scripts ship in yt-dlp-ejs).
    for rt in ("deno", "node", "bun"):
        if shutil.which(rt):
            opts["js_runtimes"] = {rt: {}}
            break
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=True)
    except yt_dlp.utils.DownloadError as e:
        raise MediaError(_clean_dl_error(str(e))) from e
    files = [f for f in outdir.glob("source.*") if f.suffix != ".part"]
    if not files:
        raise MediaError("download produced no file")
    title = (info or {}).get("title")
    return max(files, key=lambda f: f.stat().st_size), title
