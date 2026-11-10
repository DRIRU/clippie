import os
import subprocess
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from clippie.api import app
from clippie.db import session
from clippie.models import Job


@pytest.fixture(scope="module")
def video(tmp_path_factory):
    out = tmp_path_factory.mktemp("lib") / "s.mp4"
    fc = ("color=c=gray:s=320x180:r=30:d=12[v1];testsrc2=s=320x180:r=30:d=5[v2];color=c=gray:s=320x180:r=30:d=12[v3];"
          "[v1][v2][v3]concat=n=3:v=1:a=0[v];sine=f=220:d=12,volume=0.02[a1];anoisesrc=d=5:a=0.8:c=white[a2];"
          "sine=f=220:d=12,volume=0.02[a3];[a1][a2][a3]concat=n=3:v=0:a=1[a]")
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-filter_complex", fc, "-map", "[v]", "-map", "[a]",
                    "-pix_fmt", "yuv420p", str(out)], check=True)
    return out


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


def wait(client, jid, timeout=90):
    t0 = time.time()
    while time.time() - t0 < timeout:
        j = client.get(f"/api/jobs/{jid}").json()
        if j["status"] in ("done", "failed"):
            return j
        time.sleep(0.3)
    raise TimeoutError


def add(client, path):
    with open(path, "rb") as f:
        r = client.post("/api/assets/upload", files={"file": ("s.mp4", f, "video/mp4")}).json()
    assert wait(client, r["job_id"])["status"] == "done"
    return r["asset_id"]


def media(*parts):
    return Path(os.environ["CLIPPIE_DATA_DIR"]) / "media" / Path(*parts)


def test_delete_removes_everything_and_leaves_others(client, video):
    keep, doomed = add(client, video), add(client, video)
    assert wait(client, client.post(f"/api/assets/{doomed}/analyze").json()["job_id"])["status"] == "done"
    cid = client.get(f"/api/assets/{doomed}/candidates").json()[0]["id"]
    exp = client.post(f"/api/candidates/{cid}/export", json={"kinds": ["original"]}).json()[0]
    assert wait(client, exp["job_id"])["status"] == "done"
    assert media("exports", exp["export_id"]).exists() and media("sources", doomed).exists()

    r = client.delete(f"/api/assets/{doomed}")
    assert r.status_code == 200 and r.json()["exports"] == 1

    assert client.get(f"/api/assets/{doomed}").status_code == 404
    assert client.get(f"/api/exports/{exp['export_id']}").status_code == 404
    for p in (("sources", doomed), ("thumbs", f"{doomed}.jpg"), ("analysis", doomed), ("exports", exp["export_id"])):
        assert not media(*p).exists(), p
    # the other video is untouched
    assert client.get(f"/api/assets/{keep}").json()["status"] == "ready"
    assert media("sources", keep).exists() and media("thumbs", f"{keep}.jpg").exists()
    assert [a["id"] for a in client.get("/api/assets").json()].count(doomed) == 0


def test_delete_missing(client):
    assert client.delete("/api/assets/does-not-exist").status_code == 404


def test_delete_cancels_running_job_and_cleans_up(client, video):
    from clippie import jobs
    from clippie.storage import get_storage

    finished = []

    @jobs.handler("slowtest")
    def slow(job_id, asset_id, params, progress):
        st = get_storage()
        for i in range(400):                       # ~20 s if never cancelled
            progress("working", i / 400)
            st.new_path(f"sources/{asset_id}/late.part").write_bytes(b"x")   # keeps writing like a download
            time.sleep(0.05)
        finished.append(True)

    aid = add(client, video)
    jid = jobs.enqueue("slowtest", aid)
    t0 = time.time()
    while client.get(f"/api/jobs/{jid}").json()["status"] != "running":
        assert time.time() - t0 < 10
        time.sleep(0.1)
    assert client.delete(f"/api/assets/{aid}").status_code == 200

    # the job stops on its own (within ~1-2 s, not the 20 s it would take to finish)...
    t0 = time.time()
    while media("sources", aid).exists() and time.time() - t0 < 8:
        time.sleep(0.2)
    assert not finished, "job kept running after its video was deleted"
    # ...and nothing it wrote after the delete is left behind
    assert not media("sources", aid).exists() and not media("thumbs", f"{aid}.jpg").exists()
    assert client.get(f"/api/assets/{aid}").status_code == 404


def test_storage_delete_waits_for_a_locked_file(tmp_path):
    """A file still held open (e.g. by a browser streaming it) is removed once released."""
    import threading
    from clippie.storage import LocalStorage

    st = LocalStorage(tmp_path)
    p = st.new_path("sources/x/source.mp4")
    p.write_bytes(b"data")
    fh = open(p, "rb")
    threading.Timer(0.6, fh.close).start()
    st.delete("sources/x")                  # on Windows this needs the retry; elsewhere it succeeds at once
    assert not (tmp_path / "sources" / "x").exists()
