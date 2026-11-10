import os
import time

import pytest
from fastapi.testclient import TestClient

from clippie.api import app
from clippie.profiles import registry
from clippie.profiles.among_us import derive_events as among
from clippie.profiles.gta import derive_events as gta

from .killfeed_video import render


def fr(t, *texts, conf=0.95):
    return {"t": t, "rows": [[[x, conf] for x in texts]]}


def kinds(events):
    return [e.event_type for e in events]


def banner(*frames):
    return {"banner": list(frames)}


# ---------- Among Us rules ----------
def test_among_us_core_events():
    ev = among(banner(fr(5, "DEAD BODY REPORTED"), fr(9, "EMERGENCY MEETING"),
                      fr(14, "Red was The Impostor."), fr(30, "Blue was not The Impostor."),
                      fr(50, "VICTORY"), fr(70, "DEFEAT")), {})
    assert kinds(ev) == ["body_reported", "emergency_meeting", "impostor_ejected", "crewmate_ejected", "victory", "defeat"]


def test_among_us_not_the_impostor_is_not_an_impostor_ejection():
    ev = among(banner(fr(5, "Green was not The Impostor.")), {})
    assert kinds(ev) == ["crewmate_ejected"]


def test_among_us_tolerates_ocr_jitter_and_split_boxes():
    ev = among(banner(fr(5, "DEAD B0DY REPORTEO"), fr(20, "Red", "was", "The", "Impostor")), {})
    assert kinds(ev) == ["body_reported", "impostor_ejected"]          # row boxes are joined


def test_among_us_ignores_ordinary_text():
    chatter = banner(fr(3, "I think Red is the impostor, vote them"), fr(4, "victory royale lobby is open now"),
                     fr(6, "Who Is The Impostor?"), fr(8, "Skip Vote"))
    assert among(chatter, {}) == []


def test_among_us_ejection_text_in_a_busy_meeting_screen_is_ignored():
    busy = {"t": 5, "rows": [[["Red was the impostor lol", 0.95]]] + [[[f"Player{i}", 0.95]] for i in range(8)]}
    assert among({"banner": [busy]}, {}) == []
    sparse = fr(5, "Red was The Impostor.")
    assert kinds(among({"banner": [sparse]}, {})) == ["impostor_ejected"]


def test_among_us_low_confidence_and_cooldown():
    ev = among(banner(fr(5, "DEAD BODY REPORTED", conf=0.3), fr(6, "DEAD BODY REPORTED"), fr(7, "DEAD BODY REPORTED"),
                      fr(30, "DEAD BODY REPORTED")), {})
    assert [e.timestamp for e in ev] == [6, 30]      # low confidence dropped; repeat within 10 s collapsed


def test_among_us_needs_no_player_name():
    assert registry.get("among_us").needs_player_name is False


# ---------- GTA rules ----------
def test_gta_core_events():
    ev = gta(banner(fr(4, "WASTED"), fr(20, "BUSTED"), fr(40, "mission passed"), fr(60, "MISSION FAILED")), {})
    assert kinds(ev) == ["wasted", "busted", "mission_passed", "mission_failed"]


def test_gta_wasted_only_as_a_short_banner():
    assert gta(banner(fr(4, "I got wasted last night at the club")), {}) == []
    assert kinds(gta(banner(fr(4, "WASTEO")), {})) == ["wasted"]       # OCR slip


def test_gta_needs_no_player_name_and_has_regions():
    p = registry.get("gta")
    assert p.needs_player_name is False and "banner" in p.regions


# ---------- real OCR on rendered banner videos ----------
@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


def wait(client, jid, timeout=240):
    t0 = time.time()
    while time.time() - t0 < timeout:
        j = client.get(f"/api/jobs/{jid}").json()
        if j["status"] in ("done", "failed"):
            return j
        time.sleep(0.5)
    raise TimeoutError


def run_profile(client, path, game):
    with open(path, "rb") as f:
        r = client.post("/api/assets/upload", files={"file": (path.name, f, "video/mp4")}).json()
    aid = r["asset_id"]
    assert wait(client, r["job_id"])["status"] == "done"
    assert client.patch(f"/api/assets/{aid}", json={"game": game}).status_code == 200
    j = wait(client, client.post(f"/api/assets/{aid}/analyze").json()["job_id"])
    assert j["status"] == "done", j
    return aid, client.get(f"/api/assets/{aid}/events").json()


def near(events, kind, t, tol=2.5):
    return any(e["event_type"] == kind and abs(e["timestamp"] - t) <= tol for e in events)


def test_among_us_end_to_end(client, tmp_path):
    v = render(tmp_path / "au.mp4", [], 32, banners=[(5, 8, "DEAD BODY REPORTED"), (12, 15, "Red was not The Impostor."),
                                                      (20, 23, "Blue was The Impostor."), (26, 29, "VICTORY")])
    aid, ev = run_profile(client, v, "among_us")
    got = [(e["event_type"], round(e["timestamp"])) for e in ev if e["source"] == "ocr_banner"]
    assert near(ev, "body_reported", 5) and near(ev, "crewmate_ejected", 12) \
        and near(ev, "impostor_ejected", 20) and near(ev, "victory", 26), got
    assert not any(e["event_type"] == "impostor_ejected" and abs(e["timestamp"] - 12) < 3 for e in ev), got
    cands = client.get(f"/api/assets/{aid}/candidates").json()
    assert any("impostor_ejected" in c["event_types"] for c in cands)
    assert all("game_event" in c["score_breakdown"] for c in cands if c["event_types"] != ["high_intensity"])


def test_gta_end_to_end(client, tmp_path):
    v = render(tmp_path / "gta.mp4", [], 24, banners=[(4, 8, "WASTED"), (14, 18, "MISSION PASSED")])
    aid, ev = run_profile(client, v, "gta")
    got = [(e["event_type"], round(e["timestamp"])) for e in ev if e["source"] == "ocr_banner"]
    assert near(ev, "wasted", 4) and near(ev, "mission_passed", 14), got
    cands = client.get(f"/api/assets/{aid}/candidates").json()
    assert any("wasted" in c["event_types"] or "mission_passed" in c["event_types"] for c in cands)
