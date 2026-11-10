"""Game profile framework. A profile declares where to look (HUD regions) and how to
turn raw OCR output into game-independent events. Profiles are pure data + a pure
function, so they can be unit-tested without video."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from ..detection.candidates import Event


@dataclass(frozen=True)
class Region:
    """Normalised screen rectangle (0..1) relative to the full frame."""
    x: float
    y: float
    w: float
    h: float


# frames = {region_name: [{"t": float, "rows": [[[text, conf], ...], ...]}, ...]}
DeriveFn = Callable[[dict, dict], list[Event]]


@dataclass(frozen=True)
class GameProfile:
    id: str
    name: str
    regions: dict[str, Region] = field(default_factory=dict)
    needs_player_name: bool = False
    significance: dict[str, float] = field(default_factory=dict)
    derive_events: DeriveFn | None = None
    ocr_fps: float = 1.0
    note: str = ""

    def public(self) -> dict:
        return {"id": self.id, "name": self.name, "needs_player_name": self.needs_player_name,
                "regions": {k: vars(v) for k, v in self.regions.items()}, "note": self.note}
