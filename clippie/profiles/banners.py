"""Shared helper for games whose highlights show up as large on-screen text (splash banners,
result screens). A profile supplies rules; this turns raw OCR frames into events.

Matching is fuzzy so small OCR mistakes still match, and short single-word rules only match
short texts so ordinary chat or HUD text doesn't trigger them."""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable

from rapidfuzz import fuzz

from ..detection.candidates import Event

MIN_CONF = 0.5
FUZZY = 85            # partial_ratio needed for phrase rules
SHORT_FUZZY = 80      # ratio needed for single-word rules
SHORT_MAX_LEN = 12    # a single-word rule only fires on text this short
COOLDOWN_S = 10.0     # the same event type is reported at most once per window


def norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", s.lower())


def phrase(target: str) -> Callable[[str], float]:
    """Matches text containing `target` (all normalised). Returns a 0..1 strength."""
    t = norm(target)
    return lambda n: (fuzz.partial_ratio(t, n) / 100) if len(n) >= len(t) * 0.7 and fuzz.partial_ratio(t, n) >= FUZZY else 0.0


def word(target: str) -> Callable[[str], float]:
    """Matches a short text that is (almost) exactly `target`."""
    t = norm(target)
    return lambda n: (fuzz.ratio(t, n) / 100) if 0 < len(n) <= SHORT_MAX_LEN and fuzz.ratio(t, n) >= SHORT_FUZZY else 0.0


@dataclass(frozen=True)
class Rule:
    event_type: str
    match: Callable[[str], float]
    # optional veto: if it returns True the rule is skipped (e.g. "was not the impostor" vs "was the impostor")
    unless: Callable[[str], bool] | None = None
    # optional: only fire on sparse screens (at most this many text rows), so busy screens such as
    # a meeting full of chat and player names can't trigger a rule written for a splash screen
    max_rows: int | None = None


def derive(frames: list[dict], rules: list[Rule], source: str) -> list[Event]:
    events: list[Event] = []
    last: dict[str, float] = {}
    for fr in sorted(frames, key=lambda f: f["t"]):
        for row in fr["rows"]:
            cells = [(norm(t), c) for t, c in row if norm(t)]
            if not cells:
                continue
            joined = ("".join(n for n, _ in cells), min(c for _, c in cells))
            for text, conf in [joined] + cells:           # the whole row, then each box on its own
                if conf < MIN_CONF:
                    continue
                for rule in rules:
                    if rule.unless and rule.unless(text):
                        continue
                    if rule.max_rows is not None and len(fr["rows"]) > rule.max_rows:
                        continue
                    strength = rule.match(text)
                    if strength and fr["t"] - last.get(rule.event_type, -1e9) > COOLDOWN_S:
                        last[rule.event_type] = fr["t"]
                        events.append(Event(rule.event_type, fr["t"], round(conf * strength, 3), source,
                                            {"text": " ".join(t for t, _ in row)[:80]}))
    return sorted(events, key=lambda e: e.timestamp)
