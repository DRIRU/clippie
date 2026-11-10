"""GTA profile: WASTED, BUSTED and mission result banners, read from the centre of the screen.

Everything else that makes GTA clips fun (crashes, explosions, police chases, chaos) has no
text to read; the generic audio and motion detection already scores those and is fused in.
Wanted-level stars are not detected.

IMPORTANT: the screen region, wording and thresholds are best-guess defaults and have NOT been
calibrated on real GTA footage. Banner wording is for English game text. Detection quality is unmeasured.
"""
from __future__ import annotations

from ..detection.candidates import Event
from .banners import Rule, derive, phrase, word
from .base import GameProfile, Region

CENTER = Region(0.15, 0.25, 0.70, 0.50)   # WASTED / BUSTED / MISSION PASSED banners

SIGNIFICANCE = {"mission_passed": 0.75, "wasted": 0.65, "busted": 0.65, "mission_failed": 0.45}

RULES = [
    Rule("wasted", word("wasted")),
    Rule("busted", word("busted")),
    Rule("mission_passed", phrase("mission passed")),
    Rule("mission_failed", phrase("mission failed")),
]


def derive_events(frames: dict, cfg: dict) -> list[Event]:
    return derive(frames.get("banner", []), RULES, "ocr_banner")


GTA = GameProfile(
    id="gta", name="GTA (experimental)",
    regions={"banner": CENTER}, significance=SIGNIFICANCE, derive_events=derive_events, ocr_fps=1.0,
    note="Finds WASTED, BUSTED and mission banners (English); crashes and chases come from audio and motion. Regions are uncalibrated.",
)
