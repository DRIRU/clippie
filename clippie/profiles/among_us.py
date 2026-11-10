"""Among Us profile: meetings, ejections and results, read from the large centre-screen text.

IMPORTANT: the screen region, wording and thresholds are best-guess defaults and have NOT been
calibrated on real Among Us footage. Wording also differs between game versions and languages
(this profile expects English). Detection quality is unmeasured.
"""
from __future__ import annotations

from rapidfuzz import fuzz

from ..detection.candidates import Event
from .banners import Rule, derive, norm, phrase, word
from .base import GameProfile, Region

CENTER = Region(0.10, 0.25, 0.80, 0.50)   # splash text, ejection text and result screens

SIGNIFICANCE = {
    "impostor_ejected": 0.85, "victory": 0.8, "body_reported": 0.6,
    "crewmate_ejected": 0.55, "emergency_meeting": 0.5, "defeat": 0.5,
}

_EJECTION_TARGETS = [norm(t) for t in ("was the impostor", "was an impostor", "was not the impostor", "was not an impostor")]


def _ejection(negated: bool):
    """'X was The Impostor' vs 'X was not The Impostor' differ by three letters, so fuzzy matching alone
    can't tell them apart: first find an ejection line, then decide the negation explicitly."""
    def match(n: str) -> float:
        best = max(fuzz.partial_ratio(t, n) for t in _EJECTION_TARGETS)
        if best < 90 or len(n) < 12 or "was" not in n:     # chat like "I think Red is the impostor" must not match
            return 0.0
        is_negated = fuzz.partial_ratio("wasnot", n) >= 83
        return best / 100 if is_negated == negated else 0.0
    return match


RULES = [
    Rule("body_reported", phrase("dead body reported")),
    Rule("emergency_meeting", phrase("emergency meeting")),
    Rule("crewmate_ejected", _ejection(negated=True), max_rows=3),
    Rule("impostor_ejected", _ejection(negated=False), max_rows=3),
    Rule("victory", word("victory")),
    Rule("defeat", word("defeat")),
]


def derive_events(frames: dict, cfg: dict) -> list[Event]:
    return derive(frames.get("banner", []), RULES, "ocr_banner")


AMONG_US = GameProfile(
    id="among_us", name="Among Us (experimental)",
    regions={"banner": CENTER}, significance=SIGNIFICANCE, derive_events=derive_events, ocr_fps=2.0,
    note="Finds meetings, ejections and results from on-screen text (English). Regions are uncalibrated defaults.",
)
