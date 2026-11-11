"""Subtitles: local speech-to-text for a clip, caption styling, and ASS output for burn-in.

Transcription uses faster-whisper on just the clip's audio. Words are stored with times relative to
the clip start, so trimming re-transcribes and exports line up exactly. Styling is kept separate
from the text, so changing the look never needs a new transcription.

Sizes are percentages of the video height so the same style looks right on a 1080x1920 vertical
export and a 1280x720 original, and so the in-browser overlay matches the burned-in result.
"""
from __future__ import annotations

import os
import re
import subprocess
import tempfile
import threading
import wave
from dataclasses import asdict, dataclass
from pathlib import Path

from .config import get_settings

HEX = re.compile(r"^#[0-9a-fA-F]{6}$")
FONT = re.compile(r"^[A-Za-z0-9 \-]{1,40}$")
POSITIONS = {"bottom": 2, "middle": 5, "top": 8}     # ASS numpad alignment
FONTS = ["Arial", "Impact", "Verdana", "Tahoma", "Trebuchet MS", "Georgia", "Comic Sans MS", "Segoe UI"]
SIZES = {"Small": 4.5, "Medium": 6.0, "Large": 8.0, "Huge": 11.0}   # % of video height
MAX_GAP_S = 0.8


class CaptionError(Exception):
    pass


# ---------------------------------------------------------------- style
@dataclass
class CaptionStyle:
    enabled: bool = False
    font: str = "Arial"
    size_pct: float = 8.0           # % of video height
    bold: bool = True
    uppercase: bool = False
    color: str = "#FFFFFF"
    highlight: str = "#FFD400"      # colour of the word being spoken; "" turns word highlighting off
    outline_color: str = "#000000"
    outline_pct: float = 0.12       # outline width as a fraction of the font size
    box: bool = False               # opaque box behind the text instead of an outline
    box_color: str = "#000000"
    box_opacity: float = 0.6
    position: str = "bottom"        # bottom | middle | top
    margin_pct: float = 14.0        # distance from the top/bottom edge, % of video height
    max_words: int = 3              # words shown at a time

    @classmethod
    def from_dict(cls, d: dict | None, base: "CaptionStyle | None" = None) -> "CaptionStyle":
        """Validate user input; unknown keys are rejected, numbers are clamped, colours must be #RRGGBB."""
        s = base or cls()
        d = d or {}
        unknown = set(d) - set(asdict(s))
        if unknown:
            raise ValueError(f"unknown caption setting(s): {', '.join(sorted(unknown))}")
        out = asdict(s)
        for k, v in d.items():
            if k in ("enabled", "bold", "uppercase", "box"):
                out[k] = bool(v)
            elif k in ("color", "outline_color", "box_color"):
                if not isinstance(v, str) or not HEX.match(v):
                    raise ValueError(f"{k} must be a #RRGGBB colour")
                out[k] = v.upper()
            elif k == "highlight":
                if v not in ("", None) and not (isinstance(v, str) and HEX.match(v)):
                    raise ValueError("highlight must be a #RRGGBB colour or empty")
                out[k] = (v or "").upper()
            elif k == "font":
                if not isinstance(v, str) or not FONT.match(v):
                    raise ValueError("font may only contain letters, digits, spaces and dashes")
                out[k] = v
            elif k == "position":
                if v not in POSITIONS:
                    raise ValueError("position must be bottom, middle or top")
                out[k] = v
            elif k == "size_pct":
                out[k] = _clamp(v, 2.0, 18.0, k)
            elif k == "outline_pct":
                out[k] = _clamp(v, 0.0, 0.35, k)
            elif k == "box_opacity":
                out[k] = _clamp(v, 0.0, 1.0, k)
            elif k == "margin_pct":
                out[k] = _clamp(v, 0.0, 45.0, k)
            elif k == "max_words":
                out[k] = int(_clamp(v, 1, 8, k))
        return cls(**out)

    def to_dict(self) -> dict:
        return asdict(self)


def _clamp(v, lo, hi, name):
    try:
        v = float(v)
    except (TypeError, ValueError):
        raise ValueError(f"{name} must be a number") from None
    return max(lo, min(hi, v))


PRESETS = {
    "Bold": {"font": "Arial", "size_pct": 8.0, "bold": True, "uppercase": True, "color": "#FFFFFF", "highlight": "#FFD400",
             "outline_color": "#000000", "outline_pct": 0.14, "box": False, "position": "bottom", "margin_pct": 16, "max_words": 3},
    "Clean": {"font": "Arial", "size_pct": 5.5, "bold": False, "uppercase": False, "color": "#FFFFFF", "highlight": "",
              "outline_color": "#000000", "outline_pct": 0.08, "box": False, "position": "bottom", "margin_pct": 8, "max_words": 6},
    "Boxed": {"font": "Arial", "size_pct": 6.5, "bold": True, "uppercase": False, "color": "#FFFFFF", "highlight": "#7CF5A8",
              "outline_color": "#000000", "box": True, "box_color": "#000000", "box_opacity": 0.65, "position": "bottom",
              "margin_pct": 12, "max_words": 4},
    "Shorts": {"font": "Impact", "size_pct": 10.0, "bold": False, "uppercase": True, "color": "#FFFFFF", "highlight": "#FF4D4D",
               "outline_color": "#000000", "outline_pct": 0.16, "box": False, "position": "middle", "margin_pct": 10, "max_words": 2},
}


# ---------------------------------------------------------------- grouping
def group_words(words: list[dict], max_words: int = 3) -> list[list[dict]]:
    """Split words into on-screen chunks. A chunk ends at max_words, after sentence-ending
    punctuation, or before a pause longer than MAX_GAP_S. (The UI uses the same rule.)"""
    chunks: list[list[dict]] = []
    cur: list[dict] = []
    for w in words:
        if cur and w["s"] - cur[-1]["e"] > MAX_GAP_S:
            chunks.append(cur)
            cur = []
        cur.append(w)
        if len(cur) >= max_words or re.search(r"[.!?]$", w["w"]):
            chunks.append(cur)
            cur = []
    if cur:
        chunks.append(cur)
    return chunks


# ---------------------------------------------------------------- ASS
def _bgr(hex_color: str, alpha: int = 0) -> str:
    r, g, b = hex_color[1:3], hex_color[3:5], hex_color[5:7]
    return f"&H{alpha:02X}{b}{g}{r}".upper()


def _ts(t: float) -> str:
    t = max(0.0, t)
    cs = int(round(t * 100))
    h, rem = divmod(cs, 360000)
    m, rem = divmod(rem, 6000)
    s, c = divmod(rem, 100)
    return f"{h}:{m:02d}:{s:02d}.{c:02d}"


def _esc(text: str) -> str:
    return text.replace("\\", "").replace("{", "(").replace("}", ")").replace("\n", " ")


def build_ass(words: list[dict], style: CaptionStyle, width: int, height: int) -> str:
    """ASS subtitle file text for a clip whose output is width x height."""
    fs = round(style.size_pct / 100 * height, 1)
    align = POSITIONS[style.position]
    margin_v = round(style.margin_pct / 100 * height)
    margin_h = round(0.05 * width)
    if style.box:
        border_style, outline = 3, round(fs * 0.22, 1)
        outline_col = _bgr(style.box_color, int(round((1 - style.box_opacity) * 255)))
    else:
        border_style, outline = 1, round(fs * style.outline_pct, 1)
        outline_col = _bgr(style.outline_color)
    primary = _bgr(style.color)
    head = (
        "[Script Info]\nScriptType: v4.00+\n"
        f"PlayResX: {width}\nPlayResY: {height}\nWrapStyle: 0\nScaledBorderAndShadow: yes\n\n"
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, "
        "Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, "
        "MarginL, MarginR, MarginV, Encoding\n"
        f"Style: Default,{style.font},{fs},{primary},{primary},{outline_col},&H00000000,"
        f"{-1 if style.bold else 0},0,0,0,100,100,0,0,{border_style},{outline},0,{align},{margin_h},{margin_h},{margin_v},1\n\n"
        "[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
    )
    lines: list[str] = []
    chunks = group_words(words, style.max_words)
    for ci, chunk in enumerate(chunks):
        texts = [_esc(w["w"].upper() if style.uppercase else w["w"]) for w in chunk]
        c_start = chunk[0]["s"]
        next_start = chunks[ci + 1][0]["s"] if ci + 1 < len(chunks) else None
        c_end = chunk[-1]["e"] + 0.15
        if next_start is not None:
            c_end = min(c_end, next_start)
        if style.highlight:
            for i, w in enumerate(chunk):
                t0 = w["s"] if i else c_start
                t1 = chunk[i + 1]["s"] if i + 1 < len(chunk) else c_end
                if t1 <= t0:
                    continue
                parts = [f"{{\\c{_bgr(style.highlight)}&}}{t}{{\\c{primary}&}}" if j == i else t for j, t in enumerate(texts)]
                lines.append(f"Dialogue: 0,{_ts(t0)},{_ts(t1)},Default,,0,0,0,,{' '.join(parts)}")
        elif c_end > c_start:
            lines.append(f"Dialogue: 0,{_ts(c_start)},{_ts(c_end)},Default,,0,0,0,,{' '.join(texts)}")
    return head + "\n".join(lines) + "\n"


# ---------------------------------------------------------------- transcription
def available() -> bool:
    """Is faster-whisper installed? Checked without importing it (the import itself takes about a second)."""
    import importlib.util
    try:
        return importlib.util.find_spec("faster_whisper") is not None
    except (ImportError, ValueError):
        return False


_model = None
_model_lock = threading.Lock()
_run_lock = threading.Lock()      # one transcription at a time keeps CPU/GPU use predictable


def _read_wav(path: Path):
    """16 kHz mono 16-bit WAV (written by ffmpeg above) -> float32 samples. Handing faster-whisper the
    samples directly avoids its own audio decoder (PyAV), whose version requirements are fragile."""
    import numpy as np

    with wave.open(str(path), "rb") as w:
        if w.getsampwidth() != 2 or w.getnchannels() != 1:
            raise CaptionError("unexpected audio format from ffmpeg")
        raw = w.readframes(w.getnframes())
    return np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0


_model_device = "cpu"
_dlls_ready = False


def _prepare_cuda_dlls() -> None:
    """Windows: the pip packages nvidia-cublas-cu12 / nvidia-cudnn-cu12 keep their DLLs in
    site-packages/nvidia/*/bin, which Windows does not search by itself. Register those folders so
    CTranslate2 can find them. (On Linux, install the CUDA 12 / cuDNN 9 libraries system-wide.)"""
    global _dlls_ready
    if _dlls_ready or os.name != "nt":
        return
    _dlls_ready = True
    import site
    import sys
    roots = {Path(p) for p in site.getsitepackages() + [site.getusersitepackages()]} | {Path(sys.prefix) / "Lib" / "site-packages"}
    for root in roots:
        for bin_dir in (root / "nvidia").glob("*/bin"):
            try:
                os.add_dll_directory(str(bin_dir))
            except (OSError, AttributeError):
                pass
            os.environ["PATH"] = str(bin_dir) + os.pathsep + os.environ.get("PATH", "")


def _load_model(force_cpu: bool = False):
    global _model, _model_device
    with _model_lock:
        if _model is None:
            os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
            _prepare_cuda_dlls()
            from faster_whisper import WhisperModel

            cfg = get_settings()
            device, compute = ("cpu" if force_cpu else cfg.whisper_device), cfg.whisper_compute_type
            if device == "auto":
                try:
                    import ctranslate2
                    device = "cuda" if ctranslate2.get_cuda_device_count() > 0 else "cpu"
                except Exception:  # noqa: BLE001
                    device = "cpu"
            if compute == "auto" or device == "cpu" and compute == "float16":
                compute = "float16" if device == "cuda" else "int8"
            _model = WhisperModel(cfg.whisper_model, device=device, compute_type=compute)
            _model_device = device
        return _model


def _run(model, audio, language):
    segments, info = model.transcribe(audio, word_timestamps=True, vad_filter=True, language=language or None, beam_size=1)
    words = []
    for seg in segments:                      # lazy: GPU/library errors surface here
        for w in seg.words or []:
            t = w.word.strip()
            if t:
                words.append({"w": t, "s": round(float(w.start), 2), "e": round(float(w.end), 2)})
    return words, info


def transcribe(src: Path, start: float, end: float) -> dict:
    """Speech in src[start:end] as {"words": [{"w","s","e"}], "language", "text"}; times are relative to start."""
    global _model
    if not available():
        raise CaptionError("faster-whisper is not installed (pip install faster-whisper)")
    cfg = get_settings()
    with tempfile.TemporaryDirectory(prefix="clippie-asr-") as d:
        wav = Path(d) / "clip.wav"
        r = subprocess.run(["ffmpeg", "-y", "-v", "error", "-ss", f"{start:.3f}", "-i", str(src), "-t", f"{end - start:.3f}",
                            "-vn", "-ac", "1", "-ar", "16000", "-f", "wav", str(wav)], capture_output=True, text=True)
        if r.returncode != 0 or not wav.exists():
            raise CaptionError(f"could not extract audio: {r.stderr.strip()[-200:]}")
        audio = _read_wav(wav)
        model = _load_model()
        with _run_lock:
            try:
                words, info = _run(model, audio, cfg.whisper_language)
            except RuntimeError as e:
                # "auto" picked the GPU but its CUDA libraries (e.g. cublas) are missing: use the CPU instead
                if _model_device == "cuda" and cfg.whisper_device == "auto":
                    with _model_lock:
                        _model = None
                    words, info = _run(_load_model(force_cpu=True), audio, cfg.whisper_language)
                else:
                    raise CaptionError(f"speech recognition failed: {e}") from e
    return {"words": words, "language": info.language, "text": " ".join(w["w"] for w in words)}
