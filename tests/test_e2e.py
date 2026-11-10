import os
import subprocess
import tempfile
import time
from pathlib import Path

import pytest

DATA = tempfile.mkdtemp(prefix="ff-test-")
os.environ["CLIPPIE_DATA_DIR"] = DATA

from fastapi.testclient import TestClient  # noqa: E402

from clippie import media  # noqa: E402
from clippie.api import app  # noqa: E402
from clippie.detection import candidates as cand  # noqa: E402


@pytest.fixture(scope="session")
def video(tmp_path_factory) -> Path:
    """60s clip: calm, quiet gray video with an action burst (noise + motion) at 28-34s."""
    out = tmp_path_factory.mktemp("vid") / "sample.mp4"
    fc = (
        "color=c=gray:s=640x360:r=30:d=28[v1];"
        "testsrc2=s=640x360:r=30:d=6[v2];"
        "color=c=gray:s=640x360:r=30:d=26[v3];"
        "[v1][v2][v3]concat=n=3:v=1:a=0[v];"
        "sine=f=220:d=28,volume=0.02[a1];"
        "anoisesrc=d=6:a=0.8:c=white[a2];"
        "sine=f=220:d=26,volume=0.02[a3];"
        "[a1][a2][a3]concat=n=3:v=0:a=1[a]"
    )
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-filter_complex", fc, "-map", "[v]", "-map", "[a]",
                    "-ar", "44100", "-pix_fmt", "yuv420p", str(out)], check=True)
    return out


@pytest.fixture(scope="session")
def client():
    with TestClient(app) as c:
        yield c


def wait_job(client, job_id, timeout=120):
    t0 = time.time()
    while time.time() - t0 < timeout:
        j = client.get(f"/api/jobs/{job_id}").json()
        if j["status"] in ("done", "failed"):
            return j
        time.sleep(0.3)
    raise TimeoutError(job_id)


def test_probe(video):
    m = media.probe(video)
    assert 59 < m["duration"] < 61 and m["width"] == 640 and m["has_audio"]


def test_ssrf_guard():
    for bad in ("http://127.0.0.1/x", "http://localhost/x", "file:///etc/passwd", "ftp://example.com/x",
                "http://169.254.169.254/latest/meta-data"):
        with pytest.raises(media.MediaError):
            media.assert_public_url(bad)


def test_candidates_pure():
    import numpy as np
    n = 240  # 120 s at 2 Hz
    rms = np.full(n, -50.0, dtype=np.float32)
    rms[100:112] = -10.0
    ev, cs = cand.build({"rms_db": rms, "flux": np.zeros(n, dtype=np.float32)},
                        {"motion": np.zeros(n, dtype=np.float32)}, 120.0,
                        pad_before=8, pad_after=12, min_score=0.1, max_per_hour=100)
    assert cs and cs[0].start <= 50 <= cs[0].end
    flat, _ = cand.build({"rms_db": np.full(n, -50.0)}, {"motion": np.zeros(n)}, 120.0,
                         pad_before=8, pad_after=12, min_score=0.1, max_per_hour=100)
    assert flat == []


def test_full_flow(client, video):
    # upload -> ingest
    with open(video, "rb") as f:
        r = client.post("/api/assets/upload", files={"file": ("sample.mp4", f, "video/mp4")})
    assert r.status_code == 200, r.text
    asset_id = r.json()["asset_id"]
    assert wait_job(client, r.json()["job_id"])["status"] == "done"
    a = client.get(f"/api/assets/{asset_id}").json()
    assert a["status"] == "ready" and 59 < a["meta"]["duration"] < 61

    # analyze -> candidate around the 28-34s burst
    j = wait_job(client, client.post(f"/api/assets/{asset_id}/analyze").json()["job_id"])
    assert j["status"] == "done", j
    cands = client.get(f"/api/assets/{asset_id}/candidates").json()
    assert cands, "expected at least one candidate"
    best = max(cands, key=lambda c: c["score"])
    assert 24 <= best["peak"] <= 38, best
    assert client.get(f"/api/assets/{asset_id}/events").json()

    # trim + approve + export both formats
    cid = best["id"]
    assert client.patch(f"/api/candidates/{cid}", json={"start": 26, "end": 36}).status_code == 200
    assert client.patch(f"/api/candidates/{cid}", json={"start": 40, "end": 10}).status_code == 400
    assert client.post(f"/api/candidates/{cid}/approve").json()["status"] == "approved"
    exps = client.post(f"/api/candidates/{cid}/export").json()
    assert len(exps) == 2
    for e in exps:
        assert wait_job(client, e["job_id"])["status"] == "done"
    done = {e["kind"]: e for e in client.get(f"/api/candidates/{cid}/exports").json()}
    assert done["original"]["status"] == "done" and done["vertical"]["status"] == "done"

    orig = media.probe(Path(DATA) / "media" / f"exports/{done['original']['id']}/original.mp4")
    vert = media.probe(Path(DATA) / "media" / f"exports/{done['vertical']['id']}/vertical.mp4")
    assert (orig["width"], orig["height"]) == (640, 360)
    assert (vert["width"], vert["height"]) == (1080, 1920)
    assert 9.5 < orig["duration"] < 10.5 and vert["has_audio"]

    # served with Range support (needed for seeking in the player)
    r = client.get(done["vertical"]["url"], headers={"Range": "bytes=0-99"})
    assert r.status_code == 206 and len(r.content) == 100

    # re-analyze keeps approved candidate, resume path doesn't crash
    assert wait_job(client, client.post(f"/api/assets/{asset_id}/analyze").json()["job_id"])["status"] == "done"
    assert any(c["id"] == cid for c in client.get(f"/api/assets/{asset_id}/candidates").json())


def test_rejects_bad_upload(client):
    r = client.post("/api/assets/upload", files={"file": ("x.exe", b"MZ", "application/octet-stream")})
    assert r.status_code == 400
    r = client.post("/api/assets/upload", files={"file": ("fake.mp4", b"not a video", "video/mp4")})
    jid = r.json()["job_id"]
    assert wait_job(client, jid)["status"] == "failed"
    assert client.post("/api/assets/import-url", json={"url": "http://127.0.0.1/a.mp4"}).status_code == 400
