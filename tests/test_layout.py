import io
import os
import subprocess
import time
from pathlib import Path

import numpy as np
import pytest
from fastapi.testclient import TestClient
from PIL import Image

from clippie import media
from clippie.api import app
from clippie.layout import BLUR_FILTER, Layout, vertical_filter

RECT = {"x": 0.05, "y": 0.05, "w": 0.25, "h": 0.25}      # the red "webcam" in the test video (32,18 160x90 of 640x360)


# ---------- model ----------
def test_layout_defaults_validation_and_merge():
    d = Layout()
    assert d.mode == "blur" and d.facecam is None and not d.uses_facecam
    lay = Layout.from_dict({"mode": "facecam", "facecam": RECT, "facecam_pct": 99, "focus_x": -3})
    assert lay.uses_facecam and lay.facecam_pct == 45.0 and lay.focus_x == 0.0
    merged = Layout.from_dict({"gameplay": "fill"}, base=lay)
    assert merged.gameplay == "fill" and merged.facecam == lay.facecam
    for bad in ({"mode": "wide"}, {"gameplay": "stretch"}, {"nonsense": 1}, {"facecam": {"x": 0, "y": 0}},
                {"facecam": "box"}, {"facecam_pct": "tall"}):
        with pytest.raises(ValueError):
            Layout.from_dict(bad)
    assert Layout.from_dict({"facecam": None}, base=lay).facecam is None


def test_facecam_box_is_clamped_inside_the_frame():
    r = Layout.from_dict({"facecam": {"x": 0.9, "y": 0.8, "w": 0.5, "h": 0.5}}).facecam
    assert r["x"] + r["w"] <= 1.0001 and r["y"] + r["h"] <= 1.0001
    with pytest.raises(ValueError):
        Layout.from_dict({"facecam": {"x": 1.0, "y": 0.5, "w": 0.3, "h": 0.3}})    # nothing left inside the frame


# ---------- filter graph ----------
def test_filter_graph_shapes():
    assert vertical_filter(Layout(), 1920, 1080) == BLUR_FILTER
    assert vertical_filter(Layout(mode="facecam"), 1920, 1080) == BLUR_FILTER          # no box drawn yet: safe fallback
    lay = Layout.from_dict({"mode": "facecam", "facecam": RECT, "facecam_pct": 30})
    fit = vertical_filter(lay, 1920, 1080)
    assert fit.endswith("format=yuv420p[v]") and "vstack=inputs=2" in fit
    assert "scale=1080:576" in fit and "crop=1080:1344" in fit                           # 30% of 1920, rest is gameplay
    assert "boxblur" in fit                                                              # fit mode keeps the whole frame on a blurred fill
    fill = vertical_filter(Layout.from_dict({"mode": "facecam", "facecam": RECT, "gameplay": "fill", "focus_x": 1.0}), 1920, 1080)
    assert "boxblur" not in fill and "scale=1080:1344[game]" in fill
    # every crop uses even sizes (libx264 / yuv420p need that)
    import re
    for n in re.findall(r"crop=(\d+):(\d+)", fit + fill):
        assert int(n[0]) % 2 == 0 and int(n[1]) % 2 == 0


def test_filter_graph_handles_odd_and_narrow_sources():
    lay = Layout.from_dict({"mode": "facecam", "facecam": {"x": 0.9, "y": 0.9, "w": 0.2, "h": 0.2}, "gameplay": "fill"})
    g = vertical_filter(lay, 1281, 721)                                                  # odd source size
    assert "crop=" in g and g.endswith("[v]")
    tall = vertical_filter(lay, 540, 960)                                                # already vertical source
    assert "vstack" in tall


# ---------- real renders, checked by pixels ----------
@pytest.fixture(scope="module")
def video(tmp_path_factory):
    out = tmp_path_factory.mktemp("lay") / "s.mp4"
    src = ("color=c=0x303030:s=640x360:r=30:d=8,drawbox=x=32:y=18:w=160:h=90:color=red:t=fill,"
           "drawbox=x=480:y=250:w=140:h=90:color=green:t=fill")
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", src, "-f", "lavfi", "-i", "sine=f=330:d=8",
                    "-shortest", "-pix_fmt", "yuv420p", str(out)], check=True)
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


def asset_of(client, aid):
    return next(a for a in client.get("/api/assets").json() if a["id"] == aid)


def make_clip(client, video):
    with open(video, "rb") as f:
        aid = client.post("/api/assets/upload", files={"file": ("s.mp4", f, "video/mp4")}).json()["asset_id"]
    until(lambda: asset_of(client, aid)["status"] == "ready")
    cid = client.post("/api/candidates", json={"asset_id": aid, "start": 0.0, "end": 5.0}).json()["id"]
    return aid, cid


def export_frame(client, cid, t=1.0):
    e = client.post(f"/api/candidates/{cid}/export", json={"kinds": ["vertical"]}).json()[0]
    until(lambda: client.get(f"/api/jobs/{e['job_id']}").json()["status"] in ("done", "failed"))
    assert client.get(f"/api/jobs/{e['job_id']}").json()["status"] == "done"
    path = Path(os.environ["CLIPPIE_DATA_DIR"]) / "media" / "exports" / e["export_id"] / "vertical.mp4"
    assert (media.probe(path)["width"], media.probe(path)["height"]) == (1080, 1920)
    raw = subprocess.run(["ffmpeg", "-v", "error", "-ss", str(t), "-i", str(path), "-frames:v", "1", "-f", "rawvideo",
                          "-pix_fmt", "rgb24", "-"], capture_output=True, check=True).stdout
    return np.frombuffer(raw, dtype=np.uint8).reshape(1920, 1080, 3).astype(int)


def frac(region, color):
    r, g, b = region[..., 0], region[..., 1], region[..., 2]
    if color == "red":
        m = (r > 170) & (g < 90) & (b < 90)
    elif color == "green":
        m = (g > 120) & (r < 90) & (b < 90)
    return float(m.mean())


def test_default_blur_layout_has_no_facecam_band(client, video):
    aid, cid = make_clip(client, video)
    f = export_frame(client, cid)
    assert frac(f[:576], "red") < 0.2          # the top is blurred background + gameplay, not a red-filled band


def test_facecam_layout_puts_the_box_on_top_and_gameplay_below(client, video):
    aid, cid = make_clip(client, video)
    r = client.patch(f"/api/assets/{aid}", json={"layout": {"mode": "facecam", "facecam": RECT, "facecam_pct": 30}})
    assert r.status_code == 200 and r.json()["layout"]["mode"] == "facecam"
    f = export_frame(client, cid)
    assert frac(f[:570], "red") > 0.9                        # the band (576 px tall) is the red facecam crop
    assert frac(f[:570], "green") < 0.01
    assert frac(f[576:], "green") > 0.005                    # "fit" shows the whole frame below, green corner included


def test_fill_gameplay_follows_the_focus(client, video):
    aid, cid = make_clip(client, video)
    base = {"mode": "facecam", "facecam": RECT, "gameplay": "fill"}
    client.patch(f"/api/assets/{aid}", json={"layout": {**base, "focus_x": 1.0}})
    right = export_frame(client, cid)
    client.patch(f"/api/assets/{aid}", json={"layout": {**base, "focus_x": 0.0}})
    left = export_frame(client, cid)
    assert frac(right[576:], "green") > 0.02 and frac(right[576:], "red") < 0.001      # right edge kept: green, no red
    assert frac(left[576:], "red") > 0.02 and frac(left[576:], "green") < 0.001        # left edge kept: red, no green


# ---------- API ----------
def test_layout_settings_validation_and_merge(client, video):
    aid, _ = make_clip(client, video)
    assert asset_of(client, aid)["layout"]["mode"] == "blur"
    ok = client.patch(f"/api/assets/{aid}", json={"layout": {"gameplay": "fill"}}).json()["layout"]
    assert ok["gameplay"] == "fill" and ok["mode"] == "blur"
    ok = client.patch(f"/api/assets/{aid}", json={"layout": {"facecam_pct": 40}}).json()["layout"]
    assert ok["gameplay"] == "fill" and ok["facecam_pct"] == 40                         # partial updates merge
    assert client.patch(f"/api/assets/{aid}", json={"layout": {"mode": "sideways"}}).status_code == 400


def test_frame_and_layout_preview_endpoints(client, video):
    aid, _ = make_clip(client, video)
    fr = client.get(f"/api/assets/{aid}/frame.jpg?t=1")
    assert fr.status_code == 200 and fr.headers["content-type"] == "image/jpeg"
    assert Image.open(io.BytesIO(fr.content)).size == (640, 360)                          # not upscaled
    assert client.get(f"/api/assets/{aid}/frame.jpg").status_code == 200                  # default time = middle of the video

    pv = client.post(f"/api/assets/{aid}/layout-preview", json={"layout": {"mode": "facecam", "facecam": RECT}, "t": 1.0})
    assert pv.status_code == 200 and pv.headers["content-type"] == "image/jpeg"
    img = Image.open(io.BytesIO(pv.content)).convert("RGB")
    assert img.size == (540, 960)
    top = np.asarray(img)[:280].astype(int)                                               # unsaved layout is previewed...
    assert ((top[..., 0] > 170) & (top[..., 1] < 90)).mean() > 0.85
    assert asset_of(client, aid)["layout"]["mode"] == "blur"                              # ...without being saved
    assert client.post(f"/api/assets/{aid}/layout-preview", json={"layout": {"mode": "x"}}).status_code == 400
    assert client.get("/api/assets/missing/frame.jpg").status_code == 404
