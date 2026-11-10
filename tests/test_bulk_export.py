import io
import os
import subprocess
import time
import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from clippie import captions as cap
from clippie import media
from clippie.api import app


@pytest.fixture(scope="module")
def video(tmp_path_factory):
    out = tmp_path_factory.mktemp("bulk") / "s.mp4"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "testsrc2=s=320x180:r=30:d=40",
                    "-f", "lavfi", "-i", "sine=f=330:d=40", "-shortest", "-pix_fmt", "yuv420p", str(out)], check=True)
    return out


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


def until(fn, timeout=90):
    t0 = time.time()
    while time.time() - t0 < timeout:
        v = fn()
        if v:
            return v
        time.sleep(0.3)
    raise TimeoutError


def asset_of(client, aid):
    return next(a for a in client.get("/api/assets").json() if a["id"] == aid)


def make_asset(client, video, n_clips=4):
    """A ready video with n manual clips (manual clips start approved) and their previews."""
    with open(video, "rb") as f:
        aid = client.post("/api/assets/upload", files={"file": ("s.mp4", f, "video/mp4")}).json()["asset_id"]
    until(lambda: asset_of(client, aid)["status"] == "ready")
    ids = []
    for i in range(n_clips):
        r = client.post("/api/candidates", json={"asset_id": aid, "start": i * 8.0, "end": i * 8.0 + 5.0})
        assert r.status_code == 200, r.text
        ids.append(r.json()["id"])
    until(lambda: asset_of(client, aid)["previews_ready"] == n_clips)
    return aid, ids


def exports_settled(client, aid, done):
    a = asset_of(client, aid)
    return a["exports_pending"] == 0 and a["exports"]["done"] >= done and a["exports"]["failed"] == 0


def test_bulk_defaults_to_approved_clips_in_vertical(client, video):
    aid, ids = make_asset(client, video)
    client.post(f"/api/candidates/{ids[3]}/reject")
    r = client.post(f"/api/assets/{aid}/export")
    assert r.status_code == 200
    body = r.json()
    assert body["count"] == 3 and {e["kind"] for e in body["exports"]} == {"vertical"}
    assert {e["candidate_id"] for e in body["exports"]} == set(ids[:3])          # the rejected clip is left out
    until(lambda: exports_settled(client, aid, 3))
    e = client.get(f"/api/assets/{aid}").json()
    assert e["exports"]["done"] == 3 and e["exports_pending"] == 0
    root = Path(os.environ["CLIPPIE_DATA_DIR"]) / "media" / "exports"
    for x in body["exports"]:
        m = media.probe(root / x["export_id"] / "vertical.mp4")
        assert (m["width"], m["height"]) == (1080, 1920) and m["has_audio"]


def test_bulk_selected_clips_and_single_format_choice(client, video):
    aid, ids = make_asset(client, video)
    r = client.post(f"/api/assets/{aid}/export", json={"candidate_ids": [ids[1], ids[2]], "kinds": ["original"]})
    body = r.json()
    assert body["count"] == 2 and {e["kind"] for e in body["exports"]} == {"original"}
    until(lambda: exports_settled(client, aid, 2))
    root = Path(os.environ["CLIPPIE_DATA_DIR"]) / "media" / "exports"
    m = media.probe(root / body["exports"][0]["export_id"] / "original.mp4")
    assert (m["width"], m["height"]) == (320, 180)
    both = client.post(f"/api/assets/{aid}/export", json={"candidate_ids": [ids[0]], "kinds": ["vertical", "original"]}).json()
    assert both["count"] == 2                                                   # asking for both is still possible


def test_bulk_validation(client, video):
    aid, ids = make_asset(client, video, n_clips=2)
    for i in ids:
        client.post(f"/api/candidates/{i}/reject")
    r = client.post(f"/api/assets/{aid}/export")                                  # nothing approved, nothing selected
    assert r.status_code == 409 and "approve" in r.json()["detail"]
    assert client.post(f"/api/assets/{aid}/export", json={"candidate_ids": ["nope"]}).status_code == 400
    assert client.post(f"/api/assets/{aid}/export", json={"candidate_ids": ids, "kinds": ["sideways"]}).status_code == 400
    assert client.post("/api/assets/missing/export").status_code == 404
    assert client.post(f"/api/assets/{aid}/export", json={"candidate_ids": ids}).status_code == 200   # rejected ones can be picked


def test_zip_download(client, video):
    aid, ids = make_asset(client, video)
    assert client.get(f"/api/assets/{aid}/exports.zip").status_code == 404        # nothing exported yet
    client.post(f"/api/assets/{aid}/export", json={"kinds": ["vertical", "original"]})
    until(lambda: exports_settled(client, aid, 8))
    client.post(f"/api/assets/{aid}/export", json={"candidate_ids": [ids[0]]})     # a newer duplicate of clip 1
    until(lambda: exports_settled(client, aid, 9))

    r = client.get(f"/api/assets/{aid}/exports.zip")
    assert r.status_code == 200 and r.headers["content-type"] == "application/zip"
    assert "clips.zip" in r.headers["content-disposition"]
    z = zipfile.ZipFile(io.BytesIO(r.content))
    names = z.namelist()
    assert names == [f"clip-0{n}-{k}.mp4" for n in (1, 2, 3, 4) for k in ("original", "vertical")]   # one per clip and format
    z.extractall(Path(os.environ["CLIPPIE_DATA_DIR"]) / "zipcheck" / aid)
    assert media.probe(Path(os.environ["CLIPPIE_DATA_DIR"]) / "zipcheck" / aid / "clip-02-vertical.mp4")["height"] == 1920

    only_v = zipfile.ZipFile(io.BytesIO(client.get(f"/api/assets/{aid}/exports.zip?kind=vertical").content)).namelist()
    assert only_v == [f"clip-0{n}-vertical.mp4" for n in (1, 2, 3, 4)]
    picked = zipfile.ZipFile(io.BytesIO(client.get(f"/api/assets/{aid}/exports.zip?kind=original&ids={ids[2]},{ids[3]}").content)).namelist()
    assert picked == ["clip-03-original.mp4", "clip-04-original.mp4"]
    assert client.get(f"/api/assets/{aid}/exports.zip?kind=sideways").status_code == 400
    assert client.get(f"/api/assets/{aid}/exports.zip?ids=does-not-exist").status_code == 404
    leftovers = list((Path(os.environ["CLIPPIE_DATA_DIR"]) / "tmp").glob("*.zip"))
    time.sleep(0.5)
    assert not [p for p in leftovers if p.exists()], "temporary zip files should be cleaned up"


def test_bulk_with_subtitles(client, video, monkeypatch):
    monkeypatch.setattr(cap, "available", lambda: True)
    monkeypatch.setattr(cap, "_load_model", lambda: None)
    monkeypatch.setattr(cap, "transcribe", lambda src, s, e: {
        "words": [{"w": "Hello", "s": 0.5, "e": 0.9}, {"w": "world.", "s": 1.0, "e": 1.5}], "language": "en", "text": "Hello world."})
    aid, ids = make_asset(client, video, n_clips=3)
    r = client.post(f"/api/assets/{aid}/captions")
    assert r.status_code == 200
    until(lambda: asset_of(client, aid)["captions_ready"] == 3)
    client.patch(f"/api/assets/{aid}", json={"caption_style": {"enabled": True}})
    late = client.post("/api/candidates", json={"asset_id": aid, "start": 30.0, "end": 35.0}).json()["id"]   # no subtitles for this one
    until(lambda: asset_of(client, aid)["previews_ready"] == 4)

    forced = client.post(f"/api/assets/{aid}/export", json={"subtitles": True})
    assert forced.status_code == 409 and "1 of the selected clips" in forced.json()["detail"]

    r = client.post(f"/api/assets/{aid}/export").json()                               # default: burn in where they exist
    assert r["count"] == 4 and r["without_subtitles"] == [late]
    until(lambda: exports_settled(client, aid, 4))
    flags = {e["candidate_id"]: client.get(f"/api/exports/{e['export_id']}").json()["subtitles"] for e in r["exports"]}
    assert flags[late] is False and all(flags[i] for i in ids)
