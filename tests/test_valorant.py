import os
import tempfile
import time

import pytest

os.environ.setdefault("CLIPPIE_DATA_DIR", tempfile.mkdtemp(prefix="ff-test-val-"))

from fastapi.testclient import TestClient  # noqa: E402

from clippie.api import app  # noqa: E402
from clippie.profiles import registry  # noqa: E402
from clippie.profiles.valorant import derive_events  # noqa: E402

from .killfeed_video import render  # noqa: E402


def fr(t, *rows):
    return {"t": t, "rows": [[[x, c] for x, c in r] for r in rows]}


def kinds(events):
    return [e.event_type for e in events]


# ---------- pure event derivation (no video) ----------
def test_requires_player_name():
    with pytest.raises(ValueError):
        derive_events({}, {})


def test_own_kill_only_and_jitter_dedupe():
    frames = {"killfeed": [
        fr(5, [("Phoenix", .95), ("Jett", .9)]),
        fr(6, [("Phoenix", .95), ("Jett", .9)]),          # same row still visible
        fr(7, [("Ph0enix", .9), ("Jet", .8)]),             # OCR jitter on the same row
        fr(20, [("Omen", .9), ("Cypher", .9)]),            # someone else's kill
    ]}
    ev = derive_events(frames, {"player_name": "Phoenix"})
    assert kinds(ev) == ["kill"] and ev[0].timestamp == 5


def test_multi_kill_and_ace_heuristic():
    victims = ["Jett", "Sova", "Reyna", "Omen", "Cypher"]
    frames = {"killfeed": [fr(10 + 3 * i, [("Phoenix", .9), (v, .9)]) for i, v in enumerate(victims)]}
    ev = derive_events(frames, {"player_name": "phoenix"})
    k = kinds(ev)
    assert k.count("kill") == 5 and "multi_kill" in k and "ace" in k


def test_separated_kills_are_not_a_multi_kill():
    frames = {"killfeed": [fr(10, [("Phoenix", .9), ("Jett", .9)]), fr(40, [("Phoenix", .9), ("Sova", .9)])]}
    assert "multi_kill" not in kinds(derive_events(frames, {"player_name": "Phoenix"}))


def test_merged_box_prefix_match_and_banner():
    frames = {"killfeed": [fr(3, [("PhoenixJett", .9)])],
              "banner": [fr(8, [("ACE", .9)]), fr(9, [("ACE", .9)]), fr(30, [("CLUTCH", .8)]), fr(40, [("ACEROLA", .9)])]}
    k = kinds(derive_events(frames, {"player_name": "Phoenix"}))
    assert k.count("kill") == 1 and k.count("ace") == 1 and k.count("clutch") == 1


# ---------- real OCR on a synthetic kill-feed video ----------
@pytest.fixture(scope="module")
def feed_video(tmp_path_factory):
    out = tmp_path_factory.mktemp("kf") / "feed.mp4"
    feed = [(6, 11, "Phoenix", "Jett"), (9, 14, "Phoenix", "Sova"), (11, 16, "Phoenix", "Reyna"),
            (22, 27, "Omen", "Cypher")]
    return render(out, feed, 34, banners=[(18, 20, "CLUTCH")])


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


def test_registry_and_override_api(client):
    ids = [p["id"] for p in client.get("/api/game-profiles").json()]
    assert ids == ["generic", "valorant"]
    with pytest.raises(KeyError):
        registry.get("nope")


def test_valorant_end_to_end(client, feed_video):
    with open(feed_video, "rb") as f:
        r = client.post("/api/assets/upload", files={"file": ("feed.mp4", f, "video/mp4")})
    aid = r.json()["asset_id"]
    assert wait(client, r.json()["job_id"])["status"] == "done"

    assert client.patch(f"/api/assets/{aid}", json={"game": "bogus"}).status_code == 400

    # profile selected but no player name -> clear failure, not silent nonsense
    client.patch(f"/api/assets/{aid}", json={"game": "valorant"})
    j = wait(client, client.post(f"/api/assets/{aid}/analyze").json()["job_id"])
    assert j["status"] == "failed" and "player" in j["error"].lower()

    client.patch(f"/api/assets/{aid}", json={"player_name": "Phoenix"})
    j = wait(client, client.post(f"/api/assets/{aid}/analyze").json()["job_id"])
    assert j["status"] == "done", j

    ev = client.get(f"/api/assets/{aid}/events").json()
    kills = [e for e in ev if e["event_type"] == "kill"]
    assert len(kills) == 3, [(e["event_type"], e["timestamp"]) for e in ev]      # Omen's kill excluded
    assert [round(k["timestamp"]) for k in kills] == [6, 9, 11] or all(abs(k["timestamp"] - t) <= 2 for k, t in zip(kills, (6, 9, 11)))
    assert any(e["event_type"] == "multi_kill" for e in ev)
    assert any(e["event_type"] == "clutch" and 17 <= e["timestamp"] <= 21 for e in ev)

    cands = client.get(f"/api/assets/{aid}/candidates").json()
    multi = [c for c in cands if "multi_kill" in c["event_types"]]
    assert multi and multi[0]["start"] <= 6 and multi[0]["end"] >= 11
    assert "game_event" in multi[0]["score_breakdown"]
    # candidate with confirmed events outranks pure noise
    assert multi[0]["score"] >= max(c["score"] for c in cands) - 1e-6


def test_import_url_validation(client):
    u = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
    assert client.post("/api/assets/import-url", json={"url": u, "game": "bogus"}).status_code == 400
    r = client.post("/api/assets/import-url", json={"url": u, "game": "valorant"})  # auto-analyze needs a name
    assert r.status_code == 400 and "name" in r.json()["detail"]
