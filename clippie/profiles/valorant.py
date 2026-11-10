"""Valorant profile: own kills from the kill feed, multi-kill / ace heuristics, and
ACE / CLUTCH banners.

IMPORTANT: region coordinates and thresholds are best-guess defaults and have NOT been
calibrated on real Valorant footage. Detection quality is unmeasured; calibrate the
regions (and check OCR output) on your own recordings before trusting results.
"""
from __future__ import annotations

import re

from rapidfuzz import fuzz

from ..detection.candidates import Event
from .base import GameProfile, Region

KILLFEED = Region(0.68, 0.06, 0.31, 0.32)   # top-right kill feed
BANNER = Region(0.30, 0.55, 0.40, 0.25)     # centre-lower ACE / CLUTCH banners

SIGNIFICANCE = {"kill": 0.3, "multi_kill": 0.65, "ace": 1.0, "clutch": 0.9}

TRACK_GAP_S = 8.0        # same victim seen again within this window = same kill row
MULTI_KILL_GAP_S = 6.0   # consecutive own kills closer than this chain into a multi-kill
ACE_KILLS, ACE_WINDOW_S = 5, 120.0  # heuristic: 5 own kills within 2 minutes
NAME_MATCH = 80          # rapidfuzz ratio (0-100) to accept OCR'd killer == player name
MIN_CONF = 0.5


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", s.lower())


def _killer_victim(row: list, player: str) -> tuple[bool, str, float] | None:
    """Return (is_player_kill, victim_norm, conf) for one kill-feed row, or None."""
    texts = [(t, c) for t, c in row if _norm(t)]
    if not texts:
        return None
    if len(texts) >= 2:
        (k, kc), (v, vc) = texts[0], texts[-1]
        kn, vn = _norm(k), _norm(v)
        if not kn or not vn:
            return None
        score = fuzz.ratio(kn, player)
        return score >= NAME_MATCH, vn, min(kc, vc) * (score / 100 if score >= NAME_MATCH else 1.0)
    # OCR merged killer + victim into one box: accept only a clear prefix match
    t, c = texts[0]
    n = _norm(t)
    if len(n) > len(player) + 1 and fuzz.ratio(n[: len(player)], player) >= NAME_MATCH:
        return True, n[len(player):], c * 0.8
    return None


def derive_events(frames: dict, cfg: dict) -> list[Event]:
    player = _norm(cfg.get("player_name", ""))
    if not player:
        raise ValueError("Valorant profile needs the player's in-game name")
    events: list[Event] = []

    # --- own kills from the kill feed, de-duplicated into tracks ---
    tracks: list[dict] = []
    for fr in sorted(frames.get("killfeed", []), key=lambda f: f["t"]):
        for row in fr["rows"]:
            r = _killer_victim(row, player)
            if not r or not r[0] or r[2] < MIN_CONF:
                continue
            _, victim, conf = r
            for tr in tracks:
                if fr["t"] - tr["last"] <= TRACK_GAP_S and fuzz.ratio(victim, tr["victim"]) >= 75:
                    tr["last"] = fr["t"]
                    tr["conf"] = max(tr["conf"], conf)
                    break
            else:
                tracks.append({"t": fr["t"], "last": fr["t"], "victim": victim, "conf": conf})
    for tr in tracks:
        events.append(Event("kill", tr["t"], round(tr["conf"], 3), "ocr_killfeed",
                            {"victim": tr["victim"], "player": player}))

    # --- multi-kill chains and ace heuristic ---
    times = [tr["t"] for tr in tracks]
    chain = [0]
    for i in range(1, len(times) + 1):
        if i < len(times) and times[i] - times[i - 1] <= MULTI_KILL_GAP_S:
            chain.append(i)
            continue
        if len(chain) >= 2:
            ts = [times[j] for j in chain]
            events.append(Event("multi_kill", ts[-1], 0.7, "ocr_killfeed",
                                {"kills": len(ts), "times": ts, "heuristic": True}))
        chain = [i]
    for i in range(len(times) - ACE_KILLS + 1):
        if times[i + ACE_KILLS - 1] - times[i] <= ACE_WINDOW_S:
            events.append(Event("ace", times[i + ACE_KILLS - 1], 0.6, "ocr_killfeed",
                                {"kills": ACE_KILLS, "heuristic": True}))
            break

    # --- ACE / CLUTCH banners ---
    last: dict[str, float] = {}
    for fr in sorted(frames.get("banner", []), key=lambda f: f["t"]):
        for row in fr["rows"]:
            for text, conf in row:
                n = _norm(text)
                kind = "ace" if n in ("ace", "teamace") else "clutch" if n == "clutch" else None
                if kind and conf >= MIN_CONF and fr["t"] - last.get(kind, -1e9) > 10:
                    last[kind] = fr["t"]
                    events.append(Event(kind, fr["t"], round(conf, 3), "ocr_banner", {"text": text}))
    return sorted(events, key=lambda e: e.timestamp)


VALORANT = GameProfile(
    id="valorant", name="Valorant (experimental)",
    regions={"killfeed": KILLFEED, "banner": BANNER},
    needs_player_name=True, significance=SIGNIFICANCE, derive_events=derive_events, ocr_fps=1.0,
    note="Needs your in-game name. Regions are uncalibrated defaults.",
)
