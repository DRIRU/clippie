import os
import subprocess
import time
from pathlib import Path

import numpy as np
import pytest
from fastapi.testclient import TestClient

from clippie import captions as cap
from clippie.api import app
from clippie.captions import CaptionStyle, build_ass, group_words


def W(text, s, e):
    return {"w": text, "s": s, "e": e}


WORDS = [W("Hello", 1.0, 1.4), W("this", 1.5, 1.9), W("is", 2.0, 2.3), W("clippie.", 2.4, 3.0), W("Nice", 3.2, 3.6)]


# ---------- style validation ----------
def test_style_defaults_and_partial_merge():
    base = CaptionStyle.from_dict({"size_pct": 11, "color": "#ff0000"})
    assert base.size_pct == 11 and base.color == "#FF0000" and base.bold is True
    merged = CaptionStyle.from_dict({"uppercase": True}, base=base)
    assert merged.uppercase and merged.size_pct == 11 and merged.color == "#FF0000"


def test_style_rejects_bad_input_and_clamps_numbers():
    for bad in ({"color": "red"}, {"color": "#12345"}, {"font": "Arial; rm -rf"}, {"position": "left"},
                {"nonsense": 1}, {"size_pct": "big"}, {"highlight": "yellow"}):
        with pytest.raises(ValueError):
            CaptionStyle.from_dict(bad)
    s = CaptionStyle.from_dict({"size_pct": 500, "outline_pct": -1, "box_opacity": 7, "margin_pct": 99, "max_words": 50})
    assert (s.size_pct, s.outline_pct, s.box_opacity, s.margin_pct, s.max_words) == (18.0, 0.0, 1.0, 45.0, 8)
    assert CaptionStyle.from_dict({"highlight": ""}).highlight == ""        # highlighting can be switched off


def test_all_presets_are_valid():
    for p in cap.PRESETS.values():
        CaptionStyle.from_dict(p)
    assert set(cap.SIZES) == {"Small", "Medium", "Large", "Huge"}


# ---------- grouping ----------
def test_group_words_rules():
    assert [[w["w"] for w in c] for c in group_words(WORDS, 3)] == [["Hello", "this", "is"], ["clippie."], ["Nice"]]
    assert [len(c) for c in group_words(WORDS, 1)] == [1] * 5
    slow = [W("a", 0, 0.2), W("b", 0.3, 0.5), W("c", 3.0, 3.2)]                  # a long pause splits the chunk
    assert [len(c) for c in group_words(slow, 5)] == [2, 1]
    assert group_words([], 3) == []


# ---------- ASS output ----------
def style_fields(ass):
    return next(l for l in ass.splitlines() if l.startswith("Style: Default")).split(",")


def test_ass_size_colour_position_scale_with_output_height():
    st = CaptionStyle.from_dict({"size_pct": 10, "color": "#FFD400", "position": "top", "margin_pct": 10,
                                 "font": "Impact", "bold": False})
    ass = build_ass(WORDS, st, 1080, 1920)
    assert "PlayResX: 1080" in ass and "PlayResY: 1920" in ass
    f = style_fields(ass)
    assert f[1] == "Impact" and float(f[2]) == 192.0          # 10% of 1920
    assert f[3] == "&H0000D4FF"                               # #FFD400 as ASS BGR
    assert f[7] == "0" and f[18] == "8" and f[21] == "192"    # not bold, top-aligned, 10% margin
    assert float(style_fields(build_ass(WORDS, st, 1280, 720))[2]) == 72.0


def test_ass_highlight_one_event_per_word_and_off_one_per_chunk():
    on = build_ass(WORDS, CaptionStyle.from_dict({"highlight": "#00FF00", "max_words": 3}), 1280, 720)
    off = build_ass(WORDS, CaptionStyle.from_dict({"highlight": "", "max_words": 3}), 1280, 720)
    assert sum(l.startswith("Dialogue") for l in on.splitlines()) == 5      # one per word
    assert sum(l.startswith("Dialogue") for l in off.splitlines()) == 3     # one per chunk
    assert "&H0000FF00&" in on                                              # the highlight colour is used


def test_ass_uppercase_box_and_escaping():
    st = CaptionStyle.from_dict({"uppercase": True, "box": True, "box_opacity": 0.5, "highlight": ""})
    ass = build_ass([W("hi{x}", 0, 1), W("a\\b", 1, 2)], st, 1280, 720)
    f = style_fields(ass)
    assert f[15] == "3" and f[5].startswith("&H80")               # opaque box with 50% alpha
    assert "HI(X) AB" in ass and "{x}" not in ass
    assert build_ass([], CaptionStyle(), 640, 360).count("Dialogue") == 0


# ---------- full flow with a mocked transcriber ----------
@pytest.fixture(scope="module")
def video(tmp_path_factory):
    out = tmp_path_factory.mktemp("cap") / "s.mp4"
    fc = ("color=c=gray:s=640x360:r=30:d=25[v1];testsrc2=s=640x360:r=30:d=6[v2];color=c=gray:s=640x360:r=30:d=25[v3];"
          "[v1][v2][v3]concat=n=3:v=1:a=0[v];sine=f=220:d=25,volume=0.02[a1];anoisesrc=d=6:a=0.8:c=white[a2];"
          "sine=f=220:d=25,volume=0.02[a3];[a1][a2][a3]concat=n=3:v=0:a=1[a]")
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-filter_complex", fc, "-map", "[v]", "-map", "[a]",
                    "-pix_fmt", "yuv420p", str(out)], check=True)
    return out


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture()
def fake_asr(monkeypatch):
    calls = []

    def fake(src, start, end):
        calls.append((round(start, 2), round(end, 2)))
        return {"words": [dict(w) for w in WORDS], "language": "en", "text": "Hello this is clippie. Nice"}

    monkeypatch.setattr(cap, "transcribe", fake)
    monkeypatch.setattr(cap, "_load_model", lambda: None)
    monkeypatch.setattr(cap, "available", lambda: True)
    return calls


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


def uploaded(client, video):
    with open(video, "rb") as f:
        aid = client.post("/api/assets/upload", files={"file": ("s.mp4", f, "video/mp4")}).json()["asset_id"]
    until(lambda: asset_of(client, aid)["status"] == "ready")
    return aid


def analyzed(client, video):
    aid = uploaded(client, video)
    client.post(f"/api/assets/{aid}/analyze")
    until(lambda: asset_of(client, aid)["pipeline"]["state"] == "done")
    return aid


def frame(path, t):
    r = subprocess.run(["ffmpeg", "-v", "error", "-ss", str(t), "-i", str(path), "-frames:v", "1", "-vf", "scale=320:180",
                        "-f", "rawvideo", "-pix_fmt", "gray", "-"], capture_output=True, check=True)
    return np.frombuffer(r.stdout, dtype=np.uint8).astype(np.int16)


def done(client, job_id):
    until(lambda: client.get(f"/api/jobs/{job_id}").json()["status"] in ("done", "failed"))
    j = client.get(f"/api/jobs/{job_id}").json()
    assert j["status"] == "done", j
    return j


def test_generate_style_export_and_trim(client, video, fake_asr):
    aid = analyzed(client, video)
    # no subtitles until asked for: the step is absent
    assert "captions" not in [s["id"] for s in asset_of(client, aid)["pipeline"]["steps"]]
    assert client.post(f"/api/assets/{aid}/captions").status_code == 200

    a = until(lambda: (lambda x: x if x["candidate_count"] and x["captions_ready"] == x["candidate_count"]
                       and x["pipeline"]["state"] == "done" else None)(asset_of(client, aid)))
    assert "captions" in [s["id"] for s in a["pipeline"]["steps"]]
    c = client.get(f"/api/assets/{aid}/candidates").json()[0]
    assert [w["w"] for w in c["captions"]["words"]][:2] == ["Hello", "this"]
    assert c["captions"]["start"] == c["start"] and c["captions"]["end"] == c["end"] and c["captions"]["language"] == "en"

    # style: partial updates merge, bad values are rejected
    r = client.patch(f"/api/assets/{aid}", json={"caption_style": {"enabled": True, "size_pct": 11, "color": "#ffd400"}})
    assert r.json()["caption_style"]["size_pct"] == 11 and r.json()["caption_style"]["bold"] is True
    assert client.patch(f"/api/assets/{aid}", json={"caption_style": {"color": "nope"}}).status_code == 400
    assert asset_of(client, aid)["caption_style"]["enabled"] is True

    # export with subtitles: the picture really differs from an export without them
    with_subs = client.post(f"/api/candidates/{c['id']}/export", json={"kinds": ["original"]}).json()[0]
    no_subs = client.post(f"/api/candidates/{c['id']}/export", json={"kinds": ["original"], "subtitles": False}).json()[0]
    done(client, with_subs["job_id"])
    done(client, no_subs["job_id"])
    root = Path(os.environ["CLIPPIE_DATA_DIR"]) / "media" / "exports"
    a_path, b_path = root / with_subs["export_id"] / "original.mp4", root / no_subs["export_id"] / "original.mp4"
    assert client.get(f"/api/exports/{with_subs['export_id']}").json()["subtitles"] is True
    assert np.abs(frame(a_path, 1.2) - frame(b_path, 1.2)).mean() > 0.4           # "Hello" is on screen at 1.2 s
    assert np.abs(frame(a_path, 0.2) - frame(b_path, 0.2)).mean() < 0.4           # nothing spoken yet: identical

    # the vertical export works too
    done(client, client.post(f"/api/candidates/{c['id']}/export", json={"kinds": ["vertical"]}).json()[0]["job_id"])

    # trimming clears the subtitles and re-transcribes the new span
    n_before = len(fake_asr)
    client.patch(f"/api/candidates/{c['id']}", json={"start": c["start"] + 1, "end": c["end"]})
    c2 = until(lambda: next((x for x in client.get(f"/api/assets/{aid}/candidates").json()
                             if x["id"] == c["id"] and x["captions"]), None))
    assert len(fake_asr) == n_before + 1 and c2["captions"]["start"] == pytest.approx(c["start"] + 1, abs=0.01)


def test_export_requires_subtitles_when_forced(client, video, fake_asr):
    aid = analyzed(client, video)
    cid = client.get(f"/api/assets/{aid}/candidates").json()[0]["id"]
    r = client.post(f"/api/candidates/{cid}/export", json={"kinds": ["original"], "subtitles": True})
    assert r.status_code == 409 and "no subtitles" in r.json()["detail"]
    ok = client.post(f"/api/candidates/{cid}/export", json={"kinds": ["original"]})      # style disabled: a normal export
    assert ok.status_code == 200
    assert client.get(f"/api/exports/{ok.json()[0]['export_id']}").json()["subtitles"] is False


def test_caption_options_and_unavailable(client, video, monkeypatch):
    o = client.get("/api/caption-options").json()
    assert set(o["presets"]) >= {"Bold", "Clean", "Boxed", "Shorts"} and o["sizes"]["Huge"] > o["sizes"]["Small"]
    assert o["defaults"]["size_pct"] == 8.0 and "Arial" in o["fonts"]
    aid = analyzed(client, video)
    monkeypatch.setattr(cap, "available", lambda: False)
    r = client.post(f"/api/assets/{aid}/captions")
    assert r.status_code == 409 and "faster-whisper" in r.json()["detail"]
    assert client.get("/api/health").json()["captions_available"] is False


def test_captions_before_analysis_is_refused(client, video, fake_asr):
    aid = uploaded(client, video)
    r = client.post(f"/api/assets/{aid}/captions")
    assert r.status_code == 409 and "analyze" in r.json()["detail"]
