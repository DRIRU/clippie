"""Turn raw signals into game-agnostic events and ranked clip candidates.

Weights are initial heuristics, not validated. Components are stored per candidate
so they can be re-weighted per game later.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from .signals import RATE

WEIGHTS = {"audio_excitement": 0.45, "visual_intensity": 0.35, "scene_change": 0.20}
# With a game profile (plan weights; player reaction 10% and context 5% are not implemented,
# so the implemented weights are renormalised to sum to 1).
WEIGHTS_GAME = {"game_event": 0.35, "event_significance": 0.20, "audio_excitement": 0.15,
                "visual_intensity": 0.15, "scene_change": 0.05}
MIN_PEAK_GAP_S = 10.0
SCENE_CUT_THRESHOLD = 40.0  # mean abs diff (0-255) treated as a hard cut


@dataclass
class Event:
    event_type: str
    timestamp: float
    confidence: float
    source: str
    evidence: dict = field(default_factory=dict)


@dataclass
class Candidate:
    start: float
    end: float
    peak: float
    score: float
    breakdown: dict
    event_types: list


def _robust01(x: np.ndarray, full_at: float = 6.0) -> np.ndarray:
    """Robust z-score (median/MAD) mapped to 0..1; flat signals map to 0."""
    if x.size == 0:
        return x
    med = np.median(x)
    mad = np.median(np.abs(x - med)) * 1.4826
    scale = max(mad, 1e-3 * (np.abs(x).max() + 1e-6), 1e-6)
    return np.clip((x - med) / scale, 0, full_at) / full_at


def _smooth(x: np.ndarray, seconds: float) -> np.ndarray:
    k = max(1, int(seconds * RATE))
    if x.size == 0 or k == 1:
        return x
    return np.convolve(x, np.ones(k) / k, mode="same")


def _align(n: int, a: np.ndarray) -> np.ndarray:
    out = np.zeros(n, dtype=np.float32)
    m = min(n, a.size)
    out[:m] = a[:m]
    return out


def _game_signals(n: int, game_events: list[Event], significance: dict) -> tuple[np.ndarray, np.ndarray]:
    conf = np.zeros(n, dtype=np.float32)
    sig = np.zeros(n, dtype=np.float32)
    for e in game_events:
        i = int(e.timestamp * RATE)
        lo, hi = max(0, i - 2), min(n, i + 3)  # +/- 2 s around the event
        if lo >= hi:
            continue
        conf[lo:hi] = np.maximum(conf[lo:hi], min(1.0, e.confidence))
        sig[lo:hi] = np.maximum(sig[lo:hi], significance.get(e.event_type, 0.3))
    return conf, sig


def build(audio: dict, video: dict, duration: float, *, pad_before: float, pad_after: float,
          min_score: float, max_per_hour: int, game_events: list[Event] | None = None,
          significance: dict | None = None) -> tuple[list[Event], list[Candidate]]:
    n = int(duration * RATE)
    if n < 4:
        return [], []
    rms = _align(n, audio.get("rms_db", np.zeros(0)))
    flux = _align(n, audio.get("flux", np.zeros(0)))
    motion = _align(n, video.get("motion", np.zeros(0)))

    audio_exc = np.maximum(_robust01(rms), _robust01(flux))
    visual = _robust01(motion)
    cuts = (motion > SCENE_CUT_THRESHOLD).astype(np.float32)
    scene = np.clip(_smooth(cuts, 4.0) * 4.0, 0, 1)

    comps = {"audio_excitement": audio_exc, "visual_intensity": visual, "scene_change": scene}
    weights = WEIGHTS
    smoothed = {k: _smooth(v, 2.0) for k, v in comps.items()}
    if game_events:
        gconf, gsig = _game_signals(n, game_events, significance or {})
        comps.update(game_event=gconf, event_significance=gsig)
        smoothed.update(game_event=gconf, event_significance=gsig)  # spiky by nature: not smoothed
        total = sum(WEIGHTS_GAME.values())
        weights = {k: v / total for k, v in WEIGHTS_GAME.items()}
    fused = np.asarray(sum(weights[k] * smoothed[k] for k in weights), dtype=np.float32)

    # Raw events (evidence for the review UI)
    events: list[Event] = []
    for i in np.where(audio_exc > 0.5)[0][:: max(1, int(RATE))]:
        events.append(Event("audio_spike", i / RATE, float(audio_exc[i]), "audio",
                            {"rms_db": float(rms[i]), "flux": float(flux[i])}))
    for i in np.where(cuts > 0)[0]:
        events.append(Event("scene_change", i / RATE, 1.0, "scene", {"motion": float(motion[i])}))

    # Peak picking: greedy highest-first with a minimum gap
    order = np.argsort(-fused)
    gap = int(MIN_PEAK_GAP_S * RATE)
    taken: list[int] = []
    for i in order:
        if fused[i] < min_score * 0.6:
            break
        if all(abs(i - j) >= gap for j in taken):
            taken.append(int(i))

    cands: list[Candidate] = []
    for i in sorted(taken):
        t = i / RATE
        bd = {k: round(float(v[max(0, i - 4): i + 5].max()), 3) for k, v in comps.items()}
        bd["weights"] = {k: round(v, 3) for k, v in weights.items()}
        score = round(float(fused[i]), 3)
        cands.append(Candidate(max(0.0, t - pad_before), min(duration, t + pad_after), t, score, bd,
                               ["high_intensity"]))
        events.append(Event("high_intensity", t, min(1.0, score), "fusion", {"score": score, **bd}))

    # Merge overlapping windows, keeping the best peak
    merged: list[Candidate] = []
    for c in cands:
        if merged and c.start <= merged[-1].end:
            m = merged[-1]
            if c.score > m.score:
                m.peak, m.score, m.breakdown = c.peak, c.score, c.breakdown
            m.end = max(m.end, c.end)
        else:
            merged.append(c)

    merged = [c for c in merged if c.score >= min_score]
    for c in merged:  # label with the game events that fall inside the window
        types = sorted({e.event_type for e in (game_events or []) if c.start <= e.timestamp <= c.end})
        c.event_types = types or ["high_intensity"]
    limit = max(1, int(max_per_hour * duration / 3600) or 1)
    merged = sorted(merged, key=lambda c: -c.score)[:limit]
    merged.sort(key=lambda c: c.start)
    return events, merged
