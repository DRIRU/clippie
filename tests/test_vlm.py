import json
import os
import subprocess
import tempfile
import time

import httpx
import pytest

os.environ.setdefault("CLIPPIE_DATA_DIR", tempfile.mkdtemp(prefix="clippie-test-vlm-"))

from fastapi.testclient import TestClient  # noqa: E402

from clippie import vlm  # noqa: E402
from clippie.api import app  # noqa: E402


# ---------- pure functions ----------
def test_parse_response_variants():
    ok = vlm.parse_response('```json\n{"event":"multi_kill","is_highlight":true,"confidence":0.9,"evidence":"three kills"}\n```')
    assert ok["verdict"] == "confirmed" and ok["event"] == "multi_kill" and ok["confidence"] == 0.9
    assert vlm.parse_response('Sure! {"event":"none","is_highlight":false,"confidence":0.7,"evidence":"menu"} done')["verdict"] == "rejected"
    assert vlm.parse_response('{"event":"other","is_highlight":null,"confidence":0.2}')["verdict"] == "unknown"
    assert vlm.parse_response("I cannot tell")["verdict"] == "error"
    assert vlm.parse_response('{"is_highlight":true,"confidence":"high"}')["confidence"] == 0.0   # bad number tolerated
    assert vlm.parse_response('{"event":"bogus","is_highlight":true,"confidence":5}')["event"] is None
    assert vlm.parse_response('{"is_highlight":true,"confidence":5}')["confidence"] == 1.0


def test_fuse_score():
    assert vlm.fuse_score(0.6, {"verdict": "confirmed", "confidence": 1.0}) == pytest.approx(0.7)
    assert vlm.fuse_score(0.6, {"verdict": "rejected", "confidence": 0.9}) == pytest.approx(0.3)
    assert vlm.fuse_score(0.6, {"verdict": "unknown"}) == 0.6
    assert vlm.fuse_score(0.6, {"verdict": "error"}) == 0.6


def test_sample_times():
    t = vlm.sample_times(0, 60, 30, 6)
    assert len(t) == 6 and t[0] == 24 and t[-1] == 36 and t == sorted(t)
    assert vlm.sample_times(10, 10.5, 10.2, 3)[0] >= 10          # tiny window falls back to full range
    assert len(vlm.sample_times(0, 20, 5, 1)) == 1


def test_prompt_mentions_unknown_and_events():
    p = vlm.build_prompt("Valorant", ["kill", "multi_kill"], 6, sheet=False)
    assert "unknown" in p and "kill, multi_kill" in p and "6 frames" in p
    assert "tiling" in vlm.build_prompt("x", [], 6, sheet=True)


# ---------- client against a mocked OpenAI-compatible server ----------
def _jpeg():
    from PIL import Image
    import io
    b = io.BytesIO()
    Image.new("RGB", (64, 36), (10, 20, 30)).save(b, "JPEG")
    return b.getvalue()


REPLY = {"choices": [{"message": {"content": '{"event":"kill","is_highlight":true,"confidence":0.8,"evidence":"enemy eliminated"}'}}]}


def test_client_discovers_model_and_sends_images():
    seen = []

    def handler(req: httpx.Request):
        if req.url.path.endswith("/models"):
            return httpx.Response(200, json={"data": [{"id": "Qwen/Qwen2.5-VL-7B-Instruct-AWQ"}]})
        seen.append(json.loads(req.content))
        return httpx.Response(200, json=REPLY)

    c = vlm.VlmClient("http://vlm.test/v1", None, max_images=4, transport=httpx.MockTransport(handler))
    out = c.verify("Valorant", ["kill"], [_jpeg()] * 6)
    body = seen[0]
    imgs = [p for p in body["messages"][0]["content"] if p["type"] == "image_url"]
    assert body["model"] == "Qwen/Qwen2.5-VL-7B-Instruct-AWQ" and len(imgs) == 4
    assert imgs[0]["image_url"]["url"].startswith("data:image/jpeg;base64,")
    assert out["verdict"] == "confirmed" and out["model"] == body["model"]


def test_client_falls_back_to_contact_sheet_on_image_limit():
    counts = []

    def handler(req: httpx.Request):
        if req.url.path.endswith("/models"):
            return httpx.Response(200, json={"data": [{"id": "m"}]})
        n = sum(p["type"] == "image_url" for p in json.loads(req.content)["messages"][0]["content"])
        counts.append(n)
        if n > 1:
            return httpx.Response(400, text="At most 1 image(s) may be provided in one prompt.")
        return httpx.Response(200, json=REPLY)

    c = vlm.VlmClient("http://vlm.test/v1", "m", transport=httpx.MockTransport(handler))
    assert c.verify("g", [], [_jpeg()] * 5)["verdict"] == "confirmed"
    assert counts == [4, 1]


def test_client_errors_are_clear():
    def down(req):
        raise httpx.ConnectError("refused")
    with pytest.raises(vlm.VlmError, match="request failed"):
        vlm.VlmClient("http://x/v1", "m", transport=httpx.MockTransport(down)).verify("g", [], [_jpeg()])
    with pytest.raises(vlm.VlmError, match="500"):
        vlm.VlmClient("http://x/v1", "m", transport=httpx.MockTransport(lambda r: httpx.Response(500, text="boom"))).verify("g", [], [_jpeg()])


# ---------- full flow through the API ----------
@pytest.fixture(scope="module")
def burst_video(tmp_path_factory):
    out = tmp_path_factory.mktemp("v") / "burst.mp4"
    fc = ("color=c=gray:s=640x360:r=30:d=25[v1];testsrc2=s=640x360:r=30:d=6[v2];color=c=gray:s=640x360:r=30:d=25[v3];"
          "[v1][v2][v3]concat=n=3:v=1:a=0[v];sine=f=220:d=25,volume=0.02[a1];anoisesrc=d=6:a=0.8:c=white[a2];"
          "sine=f=220:d=25,volume=0.02[a3];[a1][a2][a3]concat=n=3:v=0:a=1[a]")
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-filter_complex", fc, "-map", "[v]", "-map", "[a]",
                    "-ar", "44100", "-pix_fmt", "yuv420p", str(out)], check=True)
    return out


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


def wait_job(client, jid, timeout=120):
    t0 = time.time()
    while time.time() - t0 < timeout:
        j = client.get(f"/api/jobs/{jid}").json()
        if j["status"] in ("done", "failed"):
            return j
        time.sleep(0.3)
    raise TimeoutError


def wait_until(fn, timeout=60):
    t0 = time.time()
    while time.time() - t0 < timeout:
        v = fn()
        if v:
            return v
        time.sleep(0.4)
    raise TimeoutError


def upload(client, path):
    with open(path, "rb") as f:
        r = client.post("/api/assets/upload", files={"file": ("burst.mp4", f, "video/mp4")}).json()
    assert wait_job(client, r["job_id"])["status"] == "done"
    return r["asset_id"]


def mock_vlm(monkeypatch, handler):
    c = vlm.VlmClient("http://vlm.test/v1", "qwen-test", transport=httpx.MockTransport(handler))
    monkeypatch.setattr(vlm, "enabled", lambda: True)
    monkeypatch.setattr(vlm, "get_client", lambda: c)


def test_verify_disabled_returns_409(client, burst_video):
    aid = upload(client, burst_video)
    r = client.post(f"/api/assets/{aid}/verify")
    assert r.status_code == 409 and "CLIPPIE_VLM_BASE_URL" in r.json()["detail"]
    assert client.get("/api/health").json()["vlm_enabled"] is False


def test_auto_verify_fuses_score_and_trim_resets(client, burst_video, monkeypatch):
    calls = []

    def handler(req):
        calls.append(req)
        return httpx.Response(200, json=REPLY)

    mock_vlm(monkeypatch, handler)
    assert client.get("/api/health").json()["vlm_enabled"] is True
    aid = upload(client, burst_video)
    assert wait_job(client, client.post(f"/api/assets/{aid}/analyze").json()["job_id"])["status"] == "done"

    # analysis chains into verification automatically
    cands = wait_until(lambda: (lambda cs: cs if cs and all(c["verification"] for c in cs) else None)(
        client.get(f"/api/assets/{aid}/candidates").json()))
    c = cands[0]
    base = c["score_breakdown"]["detector_score"]
    assert c["verification"]["verdict"] == "confirmed" and c["verification"]["model"] == "qwen-test"
    assert c["score"] == pytest.approx(min(1.0, 0.75 * base + 0.25 * 0.8), abs=1e-3)
    assert len(calls) == len(cands)

    # frames were stored for the candidate
    assert os.path.isdir(os.path.join(os.environ["CLIPPIE_DATA_DIR"], "media", "frames", c["id"]))

    # re-running verify without force skips already-verified candidates (resume behaviour)
    n = len(calls)
    assert wait_job(client, client.post(f"/api/assets/{aid}/verify", json={"force": False}).json()["job_id"])["status"] == "done"
    assert len(calls) == n

    # trimming invalidates the verdict and restores the detector score
    r = client.patch(f"/api/candidates/{c['id']}", json={"start": c["start"] + 1, "end": c["end"]}).json()
    assert r["verification"] is None and r["score"] == pytest.approx(base, abs=1e-3)


def test_rejected_and_unparseable_replies(client, burst_video, monkeypatch):
    replies = iter(['{"event":"none","is_highlight":false,"confidence":0.9,"evidence":"loading screen"}'])

    def handler(req):
        txt = next(replies, "no idea")
        return httpx.Response(200, json={"choices": [{"message": {"content": txt}}]})

    mock_vlm(monkeypatch, handler)
    from clippie.config import get_settings
    monkeypatch.setattr(get_settings(), "auto_verify", False)
    aid = upload(client, burst_video)
    wait_job(client, client.post(f"/api/assets/{aid}/analyze").json()["job_id"])
    cand = client.get(f"/api/assets/{aid}/candidates").json()[0]
    assert wait_job(client, client.post(f"/api/assets/{aid}/verify", json={"force": True}).json()["job_id"])["status"] == "done"
    v = client.get(f"/api/assets/{aid}/candidates").json()[0]
    base = v["score_breakdown"]["detector_score"]
    assert v["verification"]["verdict"] == "rejected" and v["score"] == pytest.approx(base * 0.5, abs=1e-3)

    # unparseable reply is recorded as an error and leaves the detector score alone
    assert wait_job(client, client.post(f"/api/assets/{aid}/verify", json={"force": True}).json()["job_id"])["status"] == "done"
    v = client.get(f"/api/assets/{aid}/candidates").json()[0]
    assert v["verification"]["verdict"] == "error" and v["score"] == pytest.approx(base, abs=1e-3)
    assert v["id"] == cand["id"]


def test_unreachable_vlm_fails_job_clearly(client, burst_video, monkeypatch):
    def down(req):
        raise httpx.ConnectError("connection refused")

    mock_vlm(monkeypatch, down)
    aid = upload(client, burst_video)
    wait_job(client, client.post(f"/api/assets/{aid}/analyze").json()["job_id"])
    jid = client.post(f"/api/assets/{aid}/verify", json={"force": True}).json()["job_id"]
    j = wait_job(client, jid)
    assert j["status"] == "failed" and "VLM request failed" in j["error"]
