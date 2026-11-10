"""Candidate verification with an existing OpenAI-compatible vision-language endpoint
(e.g. a vLLM-served Qwen2.5-VL). Only a handful of frames per candidate are sent,
never the whole video, and the model may answer "unknown".

The model never decides timestamps: detectors keep authority over clip boundaries.
"""
from __future__ import annotations

import base64
import io
import json
import re
import subprocess
import threading
from pathlib import Path

import httpx

from .config import get_settings

EVENTS = ["kill", "multi_kill", "ace", "clutch", "round_win", "victory", "explosion", "combat",
          "funny", "other", "none"]

_gate = threading.Semaphore(1)  # one VLM request at a time (matches the 1-concurrent-GPU-job plan)


def enabled() -> bool:
    return bool(get_settings().vlm_base_url)


class VlmError(Exception):
    pass


# ---------- frames ----------
def sample_times(start: float, end: float, peak: float, n: int) -> list[float]:
    """Evenly spaced timestamps, concentrated around the detected peak."""
    lo, hi = max(start, peak - 6.0), min(end, peak + 6.0)
    if hi - lo < 1.0:
        lo, hi = start, end
    if n <= 1:
        return [round((lo + hi) / 2, 2)]
    return [round(lo + (hi - lo) * i / (n - 1), 2) for i in range(n)]


def extract_frame(src: Path, t: float, out: Path, width: int = 768) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-ss", f"{t:.2f}", "-i", str(src), "-frames:v", "1",
                    "-vf", f"scale={width}:-2", "-q:v", "4", str(out)],
                   check=True, capture_output=True, timeout=120)


def contact_sheet(frames: list[bytes], cols: int = 3) -> bytes:
    """Tile frames into one JPEG for endpoints limited to one image per request."""
    from PIL import Image

    ims = [Image.open(io.BytesIO(b)).convert("RGB") for b in frames]
    w = max(i.width for i in ims)
    h = max(i.height for i in ims)
    rows = (len(ims) + cols - 1) // cols
    sheet = Image.new("RGB", (cols * w, rows * h))
    for k, im in enumerate(ims):
        sheet.paste(im, ((k % cols) * w, (k // cols) * h))
    sheet.thumbnail((2048, 2048))
    buf = io.BytesIO()
    sheet.save(buf, "JPEG", quality=85)
    return buf.getvalue()


# ---------- prompt / parsing ----------
def build_prompt(game_name: str, detected: list[str], n_frames: int, sheet: bool) -> str:
    given = (f"one image tiling {n_frames} frames (left to right, top to bottom)" if sheet
             else f"{n_frames} frames")
    return (
        f"You are reviewing a short gameplay clip from {game_name}. You are given {given} "
        "in chronological order.\n"
        f"Automatic detectors flagged this moment as: {', '.join(detected) or 'high intensity'}.\n"
        "Decide whether the frames show a meaningful highlight such as a kill, multi-kill, clutch, "
        "round or match victory, big explosion, or a notable funny moment. Judge only what is visible. "
        "If the evidence is insufficient, answer unknown.\n"
        'Reply with ONLY a JSON object: {"event": one of ' + json.dumps(EVENTS) + ', '
        '"is_highlight": true|false|null, "confidence": number 0..1, '
        '"evidence": "one short sentence describing what you see"}. Use null for is_highlight when unknown.'
    )


def parse_response(text: str) -> dict:
    """Tolerant JSON extraction -> {verdict: confirmed|rejected|unknown, event, confidence, evidence}."""
    m = re.search(r"\{.*\}", text or "", re.S)
    if not m:
        return {"verdict": "error", "event": None, "confidence": 0.0, "evidence": "unparseable model reply"}
    try:
        d = json.loads(m.group(0))
    except json.JSONDecodeError:
        return {"verdict": "error", "event": None, "confidence": 0.0, "evidence": "unparseable model reply"}
    try:
        conf = max(0.0, min(1.0, float(d.get("confidence", 0))))
    except (TypeError, ValueError):
        conf = 0.0
    hl = d.get("is_highlight")
    verdict = "confirmed" if hl is True else "rejected" if hl is False else "unknown"
    event = d.get("event") if d.get("event") in EVENTS else None
    return {"verdict": verdict, "event": event, "confidence": round(conf, 3),
            "evidence": str(d.get("evidence", ""))[:400]}


def fuse_score(base: float, v: dict) -> float:
    """Heuristic blend (unvalidated): confirmation nudges the score up, rejection halves it,
    unknown/error leaves the detector score untouched."""
    if v.get("verdict") == "confirmed":
        return round(min(1.0, 0.75 * base + 0.25 * v.get("confidence", 0.0)), 3)
    if v.get("verdict") == "rejected":
        return round(base * 0.5, 3)
    return round(base, 3)


# ---------- client ----------
class VlmClient:
    def __init__(self, base_url: str, model: str | None, api_key: str = "EMPTY", timeout: float = 120.0,
                 max_images: int = 4, transport: httpx.BaseTransport | None = None):
        self.base = base_url.rstrip("/")
        self.model = model
        self.max_images = max_images
        self.http = httpx.Client(timeout=timeout, transport=transport,
                                 headers={"Authorization": f"Bearer {api_key}"})

    def resolve_model(self) -> str:
        if not self.model:
            try:
                r = self.http.get(f"{self.base}/models")
                r.raise_for_status()
                self.model = r.json()["data"][0]["id"]
            except (httpx.HTTPError, KeyError, IndexError, ValueError) as e:
                raise VlmError(f"cannot read model list from {self.base}/models: {e}") from e
        return self.model

    def _chat(self, prompt: str, images: list[bytes]) -> str:
        content = [{"type": "text", "text": prompt}] + [
            {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + base64.b64encode(b).decode()}}
            for b in images]
        body = {"model": self.resolve_model(), "temperature": 0, "max_tokens": 300,
                "messages": [{"role": "user", "content": content}]}
        with _gate:
            try:
                r = self.http.post(f"{self.base}/chat/completions", json=body)
            except httpx.HTTPError as e:
                raise VlmError(f"VLM request failed: {e}") from e
        if r.status_code >= 400:
            raise VlmError(f"VLM returned {r.status_code}: {r.text[:300]}")
        try:
            return r.json()["choices"][0]["message"]["content"]
        except (KeyError, IndexError, ValueError) as e:
            raise VlmError("unexpected VLM response shape") from e

    def verify(self, game_name: str, detected: list[str], frames: list[bytes]) -> dict:
        sheet = self.max_images <= 1
        imgs = [contact_sheet(frames)] if sheet else frames[: self.max_images]
        try:
            text = self._chat(build_prompt(game_name, detected, len(frames), sheet), imgs)
        except VlmError as e:
            # Many vLLM deployments allow only 1 image per prompt: retry once as a contact sheet.
            if not sheet and re.search(r"image|limit|at most|multimodal", str(e), re.I) and len(frames) > 1:
                self.max_images = 1
                text = self._chat(build_prompt(game_name, detected, len(frames), True), [contact_sheet(frames)])
            else:
                raise
        out = parse_response(text)
        out["model"] = self.model
        out["frames"] = len(frames)
        return out


_client: VlmClient | None = None


def get_client() -> VlmClient:
    global _client
    cfg = get_settings()
    if not cfg.vlm_base_url:
        raise VlmError("VLM is not configured (set CLIPPIE_VLM_BASE_URL)")
    if _client is None:
        _client = VlmClient(cfg.vlm_base_url, cfg.vlm_model, cfg.vlm_api_key, cfg.vlm_timeout, cfg.vlm_max_images)
    return _client
