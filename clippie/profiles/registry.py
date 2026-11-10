from __future__ import annotations

from .base import GameProfile
from .among_us import AMONG_US
from .gta import GTA
from .valorant import VALORANT

GENERIC = GameProfile(id="generic", name="Generic (audio + motion)")

_PROFILES: dict[str, GameProfile] = {p.id: p for p in (GENERIC, VALORANT, AMONG_US, GTA)}


def get(game_id: str) -> GameProfile:
    try:
        return _PROFILES[game_id]
    except KeyError:
        raise KeyError(f"unknown game profile {game_id!r}") from None


def all_profiles() -> list[GameProfile]:
    return list(_PROFILES.values())
