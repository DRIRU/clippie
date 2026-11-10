import os
import subprocess
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from clippie import media
from clippie.api import app
from clippie.progress import build_steps


# ---------- pipeline step derivation (pure) ----------
def job(kind, status, stage="", progress=0.0, error=None):
    return {"kind": kind, "status": status, "stage": stage, "progress": progress, "error": error}


def steps(jobs, *, status="ready", url=True, source=True, hud=False, vlm=False, cands=0, ver=0, prev=0):
    return build_steps(asset={"status": status, "has_source_url": url, "has_source": source}, jobs=jobs,
                       has_hud=hud, vlm_on=vlm, candidate_count=cands, verified_count=ver, previews_ready=prev)


def states(p):
    return {s["id"]: s["state"] for s in p["steps"]}


def test_download_in_progress():
    p = steps([job("import", "running", "downloading", 0.4)], status="pending", source=False)
    assert [s["id"] for s in p["steps"]][:2] == ["download", "prepare"]
    assert states(p)["download"] == "running" and p["steps"][0]["progress"] == 0.4
    assert p["state"] == "running" and p["label"].startswith("Step 1 of")


def test_upload_has_no_download_step():
    p = steps([job("ingest", "running", "probing", 0.1)], status="pending", url=False, source=True)
    assert "download" not in states(p) and states(p)["prepare"] == "running"


def test_motion_stage_running_marks_earlier_done_and_later_pending():
    p = steps([job("import", "done"), job("ingest", "done"), job("analyze", "running", "motion", 0.43)])
    st = states(p)
    assert st["download"] == st["prepare"] == st["audio"] == "done"
    assert st["motion"] == "running" and st["candidates"] == "pending" and st["clips"] == "pending"
    motion = next(s for s in p["steps"] if s["id"] == "motion")
    assert motion["progress"] == pytest.approx(0.52, abs=0.01)           # (0.43-0.30)/0.25
    assert "Motion analysis" in p["label"] and 0 < p["overall"] < 1


def test_hud_step_only_with_game_profile():
    assert "hud" not in states(steps([], hud=False))
    p = steps([job("analyze", "running", "ocr:killfeed", 0.7)], hud=True)
    assert states(p)["hud"] == "running" and states(p)["motion"] == "done"


def test_reanalysis_resets_downstream_steps():
    old = [job("ingest", "done"), job("analyze", "done"), job("verify", "done"), job("preview", "done"),
           job("analyze", "running", "audio", 0.1)]
    p = steps(old, vlm=True, cands=3, prev=3, ver=3)
    st = states(p)
    assert st["audio"] == "running" and st["ai"] == "pending" and st["clips"] == "pending"
    assert p["state"] == "running"


def test_failure_is_reported_with_message():
    p = steps([job("analyze", "failed", "motion", 0.4, error="ffmpeg exploded")])
    f = next(s for s in p["steps"] if s["state"] == "failed")
    assert f["id"] == "motion" and f["detail"] == "ffmpeg exploded"
    assert p["state"] == "failed" and "Motion analysis" in p["label"]


def test_everything_done_and_ai_step_only_when_enabled():
    done = [job("import", "done"), job("ingest", "done"), job("analyze", "done"), job("preview", "done")]
    p = steps(done, cands=3, prev=3)
    assert p["state"] == "done" and p["overall"] == 1.0 and "ai" not in states(p)
    p = steps(done + [job("verify", "running", "verifying", 0.5)], vlm=True, cands=3, prev=3, ver=1)
    assert states(p)["ai"] == "running" and p["state"] == "running"
    p = steps(done, vlm=True, cands=3, prev=3, ver=3)
    assert states(p)["ai"] == "done" and p["state"] == "done"


# ---------- real flow: trimmed preview clips ----------
@pytest.fixture(scope="module")
def video(tmp_path_factory):
    out = tmp_path_factory.mktemp("pp") / "s.mp4"
    fc = ("color=c=gray:s=320x180:r=30:d=25[v1];testsrc2=s=320x180:r=30:d=6[v2];color=c=gray:s=320x180:r=30:d=25[v3];"
          "[v1][v2][v3]concat=n=3:v=1:a=0[v];sine=f=220:d=25,volume=0.02[a1];anoisesrc=d=6:a=0.8:c=white[a2];"
          "sine=f=220:d=25,volume=0.02[a3];[a1][a2][a3]concat=n=3:v=0:a=1[a]")
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-filter_complex", fc, "-map", "[v]", "-map", "[a]",
                    "-pix_fmt", "yuv420p", str(out)], check=True)
    return out


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


def until(fn, timeout=60):
    t0 = time.time()
    while time.time() - t0 < timeout:
        v = fn()
        if v:
            return v
        time.sleep(0.3)
    raise TimeoutError


def media_path(*parts):
    return Path(os.environ["CLIPPIE_DATA_DIR"]) / "media" / Path(*parts)


def asset_of(client, aid):
    return next(a for a in client.get("/api/assets").json() if a["id"] == aid)


def test_previews_are_rendered_trimmed_and_pipeline_completes(client, video):
    with open(video, "rb") as f:
        r = client.post("/api/assets/upload", files={"file": ("s.mp4", f, "video/mp4")}).json()
    aid = r["asset_id"]
    until(lambda: asset_of(client, aid)["status"] == "ready")
    client.post(f"/api/assets/{aid}/analyze")

    # the whole pipeline finishes, including the clip-trimming step
    a = until(lambda: (lambda x: x if x["pipeline"]["state"] == "done" else None)(asset_of(client, aid)))
    assert [s["state"] for s in a["pipeline"]["steps"]] == ["done"] * len(a["pipeline"]["steps"])
    assert "clips" in [s["id"] for s in a["pipeline"]["steps"]]

    cands = client.get(f"/api/assets/{aid}/candidates").json()
    assert cands and all(c["preview_url"] for c in cands)
    c = cands[0]
    p = media.probe(media_path("previews", f"{c['id']}.mp4"))
    assert abs(p["duration"] - (c["end"] - c["start"])) < 0.6 and p["has_audio"] and p["height"] <= 540
    assert c["length"] == pytest.approx(c["end"] - c["start"], abs=0.1)

    # served with range support so the <video> element can seek
    rr = client.get(c["preview_url"], headers={"Range": "bytes=0-49"})
    assert rr.status_code == 206 and len(rr.content) == 50

    # trimming invalidates the preview and renders a new, shorter one
    new_end = c["start"] + 6.0
    t = client.patch(f"/api/candidates/{c['id']}", json={"start": c["start"], "end": new_end}).json()
    assert t["preview_url"] is None
    c2 = until(lambda: next((x for x in client.get(f"/api/assets/{aid}/candidates").json()
                             if x["id"] == c["id"] and x["preview_url"]), None))
    p2 = media.probe(media_path("previews", f"{c['id']}.mp4"))
    assert abs(p2["duration"] - 6.0) < 0.6 and c2["length"] == pytest.approx(6.0, abs=0.1)

    # re-analysis removes stale preview files; deleting the video removes the rest
    pending = [x["id"] for x in client.get(f"/api/assets/{aid}/candidates").json() if x["status"] == "pending"]
    client.post(f"/api/assets/{aid}/analyze", json={"force": False})
    until(lambda: asset_of(client, aid)["pipeline"]["state"] == "done")
    assert client.delete(f"/api/assets/{aid}").status_code == 200
    time.sleep(0.5)
    assert pending
    for cid in pending:
        assert not media_path("previews", f"{cid}.mp4").exists()
