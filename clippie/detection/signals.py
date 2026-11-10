"""Cheap signal extraction: audio loudness/flux and low-res motion, both at 2 Hz.

Decoding is streamed through ffmpeg pipes, so multi-hour files never sit in memory.
"""
from __future__ import annotations

import subprocess

import numpy as np

RATE = 2.0  # feature frames per second
SR = 16000
W, H = 160, 90


def audio_features(path, on_progress=None, duration: float | None = None) -> dict[str, np.ndarray]:
    """Return per-0.5s RMS (dBFS) and spectral flux. Empty/zero arrays if no audio."""
    win = int(SR / RATE)
    cmd = ["ffmpeg", "-v", "error", "-i", str(path), "-vn", "-ac", "1", "-ar", str(SR), "-f", "s16le", "-"]
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    rms, flux = [], []
    prev = None
    hann = np.hanning(win).astype(np.float32)
    try:
        while True:
            buf = p.stdout.read(win * 2)
            if len(buf) < win * 2:
                break
            x = np.frombuffer(buf, dtype=np.int16).astype(np.float32) / 32768.0
            rms.append(20 * np.log10(np.sqrt(np.mean(x * x)) + 1e-6))
            mag = np.abs(np.fft.rfft(x * hann))
            mag = mag / (mag.sum() + 1e-9)
            flux.append(float(np.maximum(mag - prev, 0).sum()) if prev is not None else 0.0)
            prev = mag
            if on_progress and duration and len(rms) % 600 == 0:
                on_progress(min(1.0, len(rms) / RATE / duration))
    finally:
        p.stdout.close()
        p.wait()
    return {"rms_db": np.array(rms, dtype=np.float32), "flux": np.array(flux, dtype=np.float32)}


def video_features(path, on_progress=None, duration: float | None = None) -> dict[str, np.ndarray]:
    """Return per-frame mean absolute difference (motion) at 2 fps on 160x90 grayscale."""
    cmd = ["ffmpeg", "-v", "error", "-i", str(path), "-an", "-vf", f"fps={RATE},scale={W}:{H},format=gray",
           "-f", "rawvideo", "-"]
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    n = W * H
    motion = []
    prev = None
    try:
        while True:
            buf = p.stdout.read(n)
            if len(buf) < n:
                break
            f = np.frombuffer(buf, dtype=np.uint8).astype(np.float32)
            motion.append(float(np.abs(f - prev).mean()) if prev is not None else 0.0)
            prev = f
            if on_progress and duration and len(motion) % 600 == 0:
                on_progress(min(1.0, len(motion) / RATE / duration))
    finally:
        p.stdout.close()
        p.wait()
    return {"motion": np.array(motion, dtype=np.float32)}
